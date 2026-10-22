import asyncio
import base64
import logging
import os
import re
from io import BytesIO
from typing import Any, Dict, List, Optional

import httpx

from app.core import mem_release
from app.core.config import get_settings
from app.core.http_verify import resolve_httpx_verify
from app.core.memprobe import probe
from app.services.legacy_office import extract_doc_text, extract_ppt_text, real_office_ext
from app.services.ppt_binary import PptError, collect_ppt
from app.services.pptx_xml import collect_pptx, pptx_text

logger = logging.getLogger(__name__)

# Реэкспорт для тестов / monkeypatch (catppt fallback).
__all_legacy_reexports = (extract_doc_text, extract_ppt_text)

try:
    import docx

    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    import fitz  # PyMuPDF

    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    import openpyxl

    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

try:
    import xlrd

    XLRD_AVAILABLE = True
except ImportError:
    XLRD_AVAILABLE = False

try:
    from pdf2image import convert_from_bytes as pdf_convert_from_bytes

    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from pptx import Presentation  # noqa: F401

    PPTX_AVAILABLE = True
except ImportError:
    PPTX_AVAILABLE = False


def _create_confidence_info_for_text(
    text: str, confidence_per_word: float, file_type: str
) -> Dict[str, Any]:
    """Создаёт структуру confidence_info, совместимую с backend."""
    tokens = (text or "").split()
    words_with_confidence = [
        {"word": token, "confidence": float(confidence_per_word)} for token in tokens
    ]
    avg_confidence = confidence_per_word
    return {
        "confidence": avg_confidence,
        "text_length": len(text or ""),
        "file_type": file_type,
        "words": words_with_confidence,
    }


_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def parsed_file_type(parsed: Optional[Dict[str, Any]]) -> str:
    """Тип файла из результата parse_document.

    pdf/изображения кладут его на верхний уровень, остальные форматы — только в
    ``confidence_info["file_type"]``; берём из любого места (BUG-RAG-18).
    """
    if not isinstance(parsed, dict):
        return ""
    top = parsed.get("file_type")
    if top:
        return str(top)
    conf = parsed.get("confidence_info")
    if isinstance(conf, dict) and conf.get("file_type"):
        return str(conf["file_type"])
    return ""


def empty_document_index_error(
    parsed: Optional[Dict[str, Any]], filename: str = ""
) -> str:
    """Текст 422, когда парсер вернул пустой text (особенно для фото/OCR)."""
    conf: Dict[str, Any] = {}
    if isinstance(parsed, dict):
        raw = parsed.get("confidence_info")
        if isinstance(raw, dict):
            conf = raw
    err = conf.get("error")
    if err:
        return f"OCR изображения: {err}"
    ftype = ""
    if isinstance(parsed, dict):
        ftype = str(parsed.get("file_type") or conf.get("file_type") or "").lower()
    name = (filename or "").lower()
    is_image = ftype == "image" or any(name.endswith(ext) for ext in _IMAGE_EXTS)
    if is_image:
        return (
            "Не удалось распознать текст на изображении. "
            "Нужна vision-модель в OCR_VISION_MODEL_PATH "
            "или загрузите фото с читаемым текстом."
        )
    return "Документ пустой"


async def _call_ocr_service(
    image_bytes: bytes, filename: str, languages: str = "ru,en"
) -> Dict[str, Any]:
    """Вызов OCR (Surya) по URL из config.yml"""
    settings = get_settings()
    ocr_url = settings.ocr.url.rstrip("/")
    timeout = settings.ocr.timeout

    mime = "image/jpeg"
    lower = filename.lower()
    if lower.endswith(".png"):
        mime = "image/png"
    elif lower.endswith(".bmp"):
        mime = "image/bmp"
    elif lower.endswith(".webp"):
        mime = "image/webp"

    files = {"file": (filename, BytesIO(image_bytes), mime)}
    data = {"languages": languages}

    try:
        req_timeout = httpx.Timeout(timeout, connect=10.0, read=timeout, write=10.0)
        async with httpx.AsyncClient(
            timeout=req_timeout, verify=resolve_httpx_verify()
        ) as client:
            resp = await client.post(
                f"{ocr_url}/v1/ocr",
                files=files,
                data=data,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error("Ошибка OCR при обращении к %s: %s", ocr_url, e)
        raise


def _ocr_engine() -> str:
    """Движок OCR для SVC-RAG.

    'backend' (по умолчанию) — vision-LLM через astrachat-backend: временная
    замена отдельного OCR-сервиса. ocr.url для Surya в конфиге уже есть, но
    сам сервис пока не поднят — поэтому по умолчанию backend.
    'surya' — ocr-service (Surya) через _call_ocr_service (или другой сервис
    с тем же контрактом, к которому ведёт settings.ocr.url).
    Переключается env RAG_OCR_ENGINE=surya в момент, когда сервис будет поднят.
    """
    return (os.getenv("RAG_OCR_ENGINE", "backend") or "backend").strip().lower()


def _backend_ocr_read_timeout() -> httpx.Timeout:
    """Таймаут для OCR-вызовов в backend: на большом документе vision-LLM
    занимает минуты — read отдельно от штатного backend.timeout."""
    try:
        read_sec = float(os.getenv("RAG_BACKEND_OCR_READ_TIMEOUT", "1800"))
    except ValueError:
        read_sec = 1800.0
    return httpx.Timeout(30.0, connect=10.0, read=max(120.0, read_sec), write=120.0)


async def _call_backend_ocr_image(image_bytes: bytes, filename: str) -> str:
    """Одно изображение -> текст через backend vision-LLM ('' если текста нет)."""
    cfg = get_settings().backend
    base_url = (cfg.base_url or "").rstrip("/")
    if not base_url:
        raise RuntimeError(
            "backend.base_url не задан в конфиге SVC-RAG (urls.backend_service**) — "
            "OCR через backend недоступен"
        )
    url = f"{base_url}/api/internal/rag/ocr-image"
    payload = {
        "images": [
            {
                "name": filename,
                "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            }
        ]
    }
    # Логин инициатора в backend: без него backend не знает, кто запустил
    # распознавание, и его вызов к модели уходит на шлюз как служебный
    from app.core.cef_logger.cef_audit_context import cef_identity_headers
    from app.core.internal_auth import internal_rag_headers

    async with httpx.AsyncClient(
        timeout=_backend_ocr_read_timeout(), verify=resolve_httpx_verify()
    ) as client:
        resp = await client.post(
            url,
            headers=internal_rag_headers(cef_identity_headers()),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    results = data.get("results") or []
    # Точное совпадение имени — основной путь.
    for r in results:
        if str(r.get("name") or "") == filename:
            text = str(r.get("text") or "").strip()
            if text:
                return text
    # Fallback: единственный/первый непустой результат (имя могло разъехаться
    # после перекодирования PNG/JPEG).
    for r in results:
        text = str(r.get("text") or "").strip()
        if text:
            return text
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return ""


async def _ocr_image_bytes(image_bytes: bytes, filename: str) -> str:
    """OCR одного изображения по движку _ocr_engine(): '' если текста нет.

    Единая точка входа для картинок (PDF/docx/xlsx/pptx/отдельные файлы),
    чтобы движок (backend vision-LLM vs ocr-service) переключался в одном месте.
    """
    if _ocr_engine() == "surya":
        res = await _call_ocr_service(image_bytes, filename, languages="ru,en")
        return (res.get("text") or "").strip() if isinstance(res, dict) else ""
    return await _call_backend_ocr_image(image_bytes, filename)


# Пространство имён WordprocessingML: нужен явно — таблицы читаются по XML,
# минуя объектную обёртку python-docx (почему — в _docx_table_to_markdown).
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

def _docx_tc_grid_span(tc) -> int:
    """Сколько колонок сетки занимает ячейка (горизонтальное объединение)."""
    tc_pr = tc.find(_W_NS + "tcPr")
    if tc_pr is None:
        return 1
    grid_span = tc_pr.find(_W_NS + "gridSpan")
    if grid_span is None:
        return 1
    try:
        return max(1, int(grid_span.get(_W_NS + "val") or 1))
    except (TypeError, ValueError):
        return 1

def _docx_tc_vmerge(tc) -> Optional[str]:
    """Вертикальное объединение ячейки: None / "restart" / "continue"."""
    tc_pr = tc.find(_W_NS + "tcPr")
    if tc_pr is None:
        return None
    v_merge = tc_pr.find(_W_NS + "vMerge")
    if v_merge is None:
        return None
    val = str(v_merge.get(_W_NS + "val") or "continue").strip().lower()
    return "restart" if val == "restart" else "continue"

def _docx_tc_text(tc) -> str:
    """Текст ячейки по её XML-элементу, включая вложенные таблицы.

    Берём все w:t внутри tc; переводы строк и табуляции схлопываем в пробел —
    строка markdown-таблицы должна остаться одной строкой. Правки в режиме
    рецензирования: удалённое (w:delText) под условие не подходит и в текст не
    идёт, добавленное (w:t внутри w:ins) идёт — как и показывает Word.
    """
    parts: List[str] = []
    for node in tc.iter():
        tag = node.tag
        if not isinstance(tag, str):
            continue  # комментарии и PI lxml отдаёт с нестроковым тегом
        if tag == _W_NS + "p":
            # Границы абзацев: без разделителя «Иван»+«Петров» склеивались (BUG-RAG-24).
            parts.append(" ")
        elif tag == _W_NS + "t":
            if node.text:
                parts.append(node.text)
        elif tag in (_W_NS + "tab", _W_NS + "br", _W_NS + "cr"):
            parts.append(" ")
    return " ".join("".join(parts).split())

def _docx_table_rows(table) -> list:
    """Строки таблицы как XML-элементы w:tr."""
    tbl = getattr(table, "_tbl", table)
    rows = getattr(tbl, "tr_lst", None)
    if rows is None:
        rows = tbl.findall(_W_NS + "tr")
    return list(rows)


def _docx_table_to_markdown(table) -> str:
    """Таблица docx -> markdown-pipe строки. Одна строка таблицы = одна строка
    текста: чанкер (_protect_tables) узнаёт такой блок и не режет его посередине.

    Ячейки читаются напрямую из XML (w:tr/w:tc), а НЕ через row.cells.
    Причина: python-docx на обращении к row.cells сам разворачивает вертикальные
    объединения и, встретив в самой верхней строке ячейку с
    <w:vMerge w:val="continue"> — продолжение объединения, начала которого в
    файле нет, — падает с ValueError("no tr above topmost tr in w:tbl"). Такие
    таблицы даёт разрыв по страницам и сторонние конвертеры; Word открывает их
    молча, а у нас падал разбор ВСЕГО документа. По XML этой ловушки нет:
    горизонтальные объединения и так лежат одной ячейкой (gridSpan), а
    вертикальные разбираем сами — продолжение берёт текст той же колонки сетки
    из предыдущей строки, а если предыдущей строки нет, остаётся пустым.
    """
    lines: List[str] = []
    rows = _docx_table_rows(table)
    prev_by_offset: Dict[int, str] = {}
    emitted = 0
    for tr in rows:
        cells: List[str] = []
        by_offset: Dict[int, str] = {}
        offset = 0
        for tc in tr.findall(_W_NS + "tc"):
            text = _docx_tc_text(tc)
            if not text and _docx_tc_vmerge(tc) == "continue":
                text = prev_by_offset.get(offset, "")
            by_offset[offset] = text
            cells.append(text)
            offset += _docx_tc_grid_span(tc)
        prev_by_offset = by_offset
        if not any(cells):
            continue
        lines.append("| " + " | ".join(cells) + " |")
        emitted += 1
        # Разделитель — после первой НЕПУСТОЙ строки: если таблица начинается с
        # пустой строки, шапкой становится первая, в которой что-то есть.
        if emitted == 1 and len(rows) > 1:
            lines.append("|" + " --- |" * len(cells))
    return "\n".join(lines)


def extract_text_from_docx(file_data: bytes) -> str:
    if not DOCX_AVAILABLE:
        raise RuntimeError("python-docx не установлен")
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(BytesIO(file_data))
    parts = []
    tables_seen = 0
    # Обходим тело В ПОРЯДКЕ ДОКУМЕНТА: абзацы и таблицы вперемешку, как в файле.
    # Старый вариант (все абзацы, потом все таблицы поячеечно) разрушал структуру
    # и порождал сотни мелких бесконтекстных чанков.
    for child in doc.element.body.iterchildren():
        tag = child.tag
        if tag.endswith("}p"):
            parts.append(Paragraph(child, doc).text)
        elif tag.endswith("}tbl"):
            tables_seen += 1
            try:
                md = _docx_table_to_markdown(Table(child, doc))
            except Exception as e:
                # Одна кривая таблица не должна стоить документа целиком: текст
                # вокруг неё проиндексируется, а что именно сломалось — видно
                # в логе сервиса (в контуре это единственная диагностика).
                logger.warning(
                    "DOCX: таблица #%s не разобрана (%s: %s) — пропускаю её, "
                    "остальной текст документа индексируется",
                    tables_seen,
                    type(e).__name__,
                    e,
                )
                md = ""
            if md:
                # Пустые строки вокруг таблицы, чтобы сплиттер не клеил её к абзацу.
                parts.append("")
                parts.append(md)
                parts.append("")
    return "\n".join(parts)


def _docx_image_blobs(file_data: bytes) -> list:
    """Байты встроенных изображений .docx (для OCR)."""
    try:
        doc = docx.Document(BytesIO(file_data))
        out = []
        for rel in doc.part.rels.values():
            if "image" in (getattr(rel, "reltype", "") or ""):
                try:
                    out.append(rel.target_part.blob)
                except Exception:
                    continue
        return out
    except Exception as e:
        logger.warning("DOCX: не удалось извлечь изображения: %s", e)
        return []


def _xlsx_image_blobs(file_data: bytes) -> list:
    """Байты картинок, вставленных на листы .xlsx (для OCR)."""
    try:
        wb = openpyxl.load_workbook(BytesIO(file_data))
        out = []
        for ws in wb.worksheets:
            for img in getattr(ws, "_images", []) or []:
                try:
                    data = (
                        img._data() if callable(getattr(img, "_data", None)) else None
                    )
                    if data:
                        out.append(data)
                except Exception:
                    continue
        return out
    except Exception as e:
        logger.warning("XLSX: не удалось извлечь изображения: %s", e)
        return []


def _mupdf_store_shrink() -> None:
    """Опустошить кэш распакованных объектов MuPDF.

    Кэш общий на процесс, а не на документ: doc.close() его не трогает, и
    за проход по страницам он растёт вместе с их числом.

    Пишем в DEBUG, сколько в нём лежало: до выката настоящую цифру взять
    негде, а по логу она видна сразу.

    Тихая и необязательная: на другой версии PyMuPDF ручки может не быть,
    тогда просто ничего не делаем.
    """
    if not PYMUPDF_AVAILABLE:
        return
    try:
        tools = getattr(fitz, "TOOLS", None)
        if tools is None or not hasattr(tools, "store_shrink"):
            return
        before = int(getattr(tools, "store_size", 0) or 0)
        tools.store_shrink(100)
        after = int(getattr(tools, "store_size", 0) or 0)
        logger.debug(
            "MuPDF: кэш сброшен, %s -> %s байт (освободили %s)",
            before,
            after,
            before - after,
        )
    except Exception as e:
        logger.debug("MuPDF: сбросить кэш не вышло (%s)", e)


def _pdf_image_blobs(file_data: bytes) -> List[bytes]:
    """Встроенные изображения страниц PDF (для OCR) через PyMuPDF.

    Для гибридного PDF: страницы с нормальным текстовым слоем, на которых
    вставлены картинки (скриншоты, сканы фрагментов) — их текст без OCR в слой
    не попадает. Мелкие «иконки/буллеты» отсекаются по площади, дедуп по xref.
    """
    if not PYMUPDF_AVAILABLE:
        return []
    min_area = int(os.getenv("RAG_OCR_IMAGE_MIN_AREA", "12000"))  # ~110x110
    max_imgs = int(os.getenv("RAG_MAX_OCR_IMAGES_PER_DOC", "50"))
    try:
        doc = fitz.open(stream=file_data, filetype="pdf")
        out: List[bytes] = []
        seen = set()
        for _i in range(doc.page_count):
            page = doc.load_page(_i)
            try:
                # get_images читает словарь ресурсов страницы. Стоявший тут
                # get_image_info вместо этого прогонял страницу через
                # устройство отрисовки, то есть распаковывал каждую картинку
                # ради её ширины и высоты.
                # Кортеж: (xref, smask, width, height, bpc, colorspace,
                # alt_colorspace, name, filter, referencer). Нужны нулевой,
                # второй и третий.
                infos = page.get_images(full=True) or []
            except Exception:
                continue
            for info in infos:
                # get_images(full=True) → кортеж; get_image_info / тесты —
                # dict с ключом xref (и опционально w/h).
                try:
                    if isinstance(info, dict):
                        xref = int(info.get("xref") or 0)
                        w = int(info.get("w") or info.get("width") or 0)
                        h = int(info.get("h") or info.get("height") or 0)
                        if not (w and h):
                            w = int(info[2] or 0)
                            h = int(info[3] or 0)
                    else:
                        xref = int(info[0] or 0)
                        w = int(info[2] or 0)
                        h = int(info[3] or 0)
                except Exception:
                    continue
                if not xref or xref in seen:
                    continue
                seen.add(xref)
                # Раньше здесь был размер картинки НА СТРАНИЦЕ, теперь -
                # её собственный размер в пикселях. Он обычно больше, так
                # что фильтр по площади стал мягче: картинок на OCR может
                # пройти больше, чем раньше. Если полезут иконки - поднять
                # RAG_OCR_IMAGE_MIN_AREA.
                if w and h and w * h < min_area:
                    continue
                if len(out) >= max_imgs:
                    doc.close()
                    return out
                try:
                    im = doc.extract_image(xref)
                    blob = im.get("image") or b""
                    if blob:
                        out.append(blob)
                except Exception:
                    continue
        doc.close()
        return out
    except Exception as e:
        logger.warning("PDF: не удалось извлечь изображения: %s", e)
        return []


async def _ocr_office_images(blobs: list, filename: str) -> str:
    """OCR списка изображений офисного файла -> склеенный текст (или '').
    Движок — _ocr_engine() (backend vision-LLM по умолчанию, surya — при
    RAG_OCR_ENGINE=surya)."""
    if not blobs:
        return ""
    texts = []
    for i, blob in enumerate(blobs, 1):
        try:
            t = await _ocr_image_bytes(blob, f"{filename}.img{i}.png")
            if t:
                texts.append(t)
        except Exception as e:
            logger.warning("Office OCR картинки пропущена (%s): %s", filename, e)
    if not texts:
        return ""
    return "[Текст из изображений документа (OCR)]:\n" + "\n".join(texts)


def _pptx_table_to_markdown(table) -> str:
    """Таблица pptx -> markdown-pipe строки (формат как у docx)."""
    lines = []
    for r_idx, row in enumerate(table.rows):
        cells = []
        prev_cell = None
        for cell in row.cells:
            if cell is prev_cell:  # merged-ячейка повторяется по колонкам
                continue
            prev_cell = cell
            cells.append(" ".join((cell.text or "").split()))
        if not any(cells):
            continue
        lines.append("| " + " | ".join(cells) + " |")
        if r_idx == 0 and len(table.rows) > 1:
            lines.append("|" + " --- |" * len(cells))
    return "\n".join(lines)


async def _slides_text(
    per_slide: List[List[tuple]],
    img_items: List[tuple],
    notes: List[str],
    *,
    label: str = "PPTX",
) -> str:
    """OCR картинок слайдов + сборка текста (общий путь для pptx/ppt)."""
    ocr_by_name: Dict[str, str] = {}
    for name, blob in img_items:
        send_name, send_blob = name, blob
        lower = (name or "").lower()
        if PIL_AVAILABLE and not (
            lower.endswith(".png") or lower.endswith(".jpg") or lower.endswith(".jpeg")
        ):
            try:
                from PIL import Image

                im = Image.open(BytesIO(blob))
                out = BytesIO()
                im.convert("RGB").save(out, format="PNG")
                send_blob = out.getvalue()
                base = name.rsplit(".", 1)[0] if "." in name else name
                send_name = f"{base}.png"
            except Exception as e:
                logger.warning(
                    "%s: картинка %s не открылась: %s", label, name, e
                )
                continue
        try:
            t = await _ocr_image_bytes(send_blob, send_name)
            if t:
                # Привязка к исходному имени из элементов слайда.
                ocr_by_name[name] = t
                if send_name != name:
                    ocr_by_name[send_name] = t
        except Exception as e:
            logger.warning("%s: OCR картинки %s пропущена: %s", label, name, e)
    return pptx_text(per_slide, notes, ocr_by_name)


async def extract_text_from_pptx(file_data: bytes) -> str:
    """PPTX: python-pptx если есть, иначе zip/XML (pptx_xml); OCR картинок."""
    max_imgs = int(os.getenv("RAG_MAX_OCR_IMAGES_PER_DOC", "50"))

    if not PPTX_AVAILABLE:
        logger.info(
            "PPTX: python-pptx не установлен — разбор через zip/XML (pptx_xml)"
        )
        per_slide, img_items, notes = await asyncio.to_thread(
            collect_pptx, file_data, max_imgs
        )
        return await _slides_text(per_slide, img_items, notes, label="PPTX")

    def _collect():
        from pptx import Presentation

        prs = Presentation(BytesIO(file_data))
        per_slide: List[List[tuple]] = []
        img_items: List[tuple] = []
        for si, slide in enumerate(prs.slides, 1):
            items: List[tuple] = []
            for idx, shape in enumerate(slide.shapes):
                try:
                    if getattr(shape, "has_table", False):
                        md = _pptx_table_to_markdown(shape.table)
                        if md:
                            items.append(("text", md))
                        continue
                except Exception:
                    pass
                try:
                    if getattr(shape, "has_text_frame", False):
                        t = (shape.text_frame.text or "").strip()
                        if t:
                            items.append(("text", t))
                except Exception:
                    pass
                try:
                    if int(shape.shape_type) == 13:  # MSO_SHAPE_TYPE.PICTURE
                        blob = shape.image.blob
                        if blob:
                            if len(img_items) < max_imgs:
                                name = f"slide{si}_s{idx}.png"
                                items.append(("img", name))
                                img_items.append((name, blob))
                            else:
                                logger.warning(
                                    "PPTX: лимит картинок %s превышен — дальше без OCR",
                                    max_imgs,
                                )
                except Exception:
                    pass
            per_slide.append(items)
        notes: List[str] = []
        for slide in prs.slides:
            try:
                notes.append(
                    (
                        slide.notes_slide.notes_text_frame.text
                        if slide.has_notes_slide
                        else ""
                    )
                    or ""
                )
            except Exception:
                notes.append("")
        return per_slide, img_items, notes

    per_slide, img_items, notes = await asyncio.to_thread(_collect)
    return await _slides_text(per_slide, img_items, notes, label="PPTX")


async def extract_text_from_ppt(file_data: bytes, filename: str = "") -> str:
    """PPT 97-2003: свой OLE-разбор (ppt_binary), иначе catppt (legacy_office)."""
    max_imgs = int(os.getenv("RAG_MAX_OCR_IMAGES_PER_DOC", "50"))
    label = filename or "?"
    try:
        per_slide, img_items, notes, _stats = await asyncio.to_thread(
            collect_ppt, file_data, max_imgs
        )
        text = await _slides_text(per_slide, img_items, notes, label="PPT")
        body = re.sub(r"\[Слайд \d+\]", "", text or "").strip()
        # Плейсхолдеры вроде «*» / пустые слайды — считаем «текста нет».
        if body and body not in {"*", "•", "-"}:
            return text
        logger.warning(
            "PPT «%s»: свой разбор - текста нет, пробуем catppt", label
        )
    except PptError as e:
        logger.warning(
            "PPT «%s»: свой разбор не удался (%s: %s), пробуем catppt",
            label,
            type(e).__name__,
            e,
        )
    except Exception as e:
        logger.warning(
            "PPT «%s»: свой разбор не удался (%s: %s), пробуем catppt",
            label,
            type(e).__name__,
            e,
        )
    return await extract_ppt_text(file_data, filename or "?")



def _pdf_magic_bytes(data: bytes) -> bool:
    return bool(data and len(data) >= 5 and data[:5] == b"%PDF-")


def _normalize_extracted_text(text: str) -> str:
    """Нормализация извлечённого текста перед чанкингом/эмбеддингом."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    # Мягкие переносы и разрывы слов на стыках строк из PDF
    t = t.replace("\u00ad", "")
    t = re.sub(r"(?<=\w)-\n(?=\w)", "", t)
    # Убираем «рваные» пробелы, но сохраняем абзацы
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _looks_like_low_quality_pdf_text(text: str, n_pages: int) -> bool:
    """Эвристика плохого текстового слоя PDF (часто сканы/битые шрифты)."""
    t = (text or "").strip()
    if not t:
        return True
    pages = max(int(n_pages or 1), 1)
    min_chars_expected = max(120, pages * 80)
    if len(t) < min_chars_expected:
        return True
    letters = sum(1 for ch in t if ch.isalpha())
    digits = sum(1 for ch in t if ch.isdigit())
    spaces = sum(1 for ch in t if ch.isspace())
    total = max(len(t), 1)
    alpha_ratio = letters / total
    # Очень мало букв при длинном тексте → вероятный "мусорный" слой
    if alpha_ratio < 0.35 and (digits + spaces) / total > 0.45:
        return True
    # Слишком много одиночных токенов/обрывков
    toks = re.findall(r"\w+", t, re.UNICODE)
    if toks:
        short_ratio = sum(1 for w in toks if len(w) <= 2) / len(toks)
        if short_ratio > 0.72:
            return True
    return False


async def _call_backend_ocr_pdf(file_data: bytes, filename: str) -> Dict[str, Any]:
    """OCR PDF через astrachat-backend — временный вариант, пока нет ocr-service.

    Backend сам выполняет ту же логику, что и для вложений в чате
    (_ocr_pdf_payload_if_needed: рендер pypdfium2 + vision-LLM по
    OCR_VISION_MODEL_PATH, параллельно до 4 страниц). Дублируем здесь только
    HTTP-вызов, логику распознавания — нет.

    Возвращает {"text": str, "ocr_used": bool}. При любой ошибке бросает
    исключение — вызывающий сам решает, есть ли фолбэк.
    """
    cfg = get_settings().backend
    base_url = (cfg.base_url or "").rstrip("/")
    if not base_url:
        raise RuntimeError(
            "backend.base_url не задан в конфиге SVC-RAG (urls.backend_service_*) — "
            "OCR PDF через backend недоступен"
        )
    url = f"{base_url}/api/internal/rag/ocr-pdf"
    payload = {
        "filename": filename or "document.pdf",
        "pdf_base64": base64.b64encode(file_data).decode("ascii"),
    }
    # Рендер + OCR каждой страницы vision-моделью на большом скане занимает
    # минуты — read-таймаут отдельный от штатного backend.timeout.
    try:
        read_sec = float(os.getenv("RAG_BACKEND_OCR_READ_TIMEOUT", "1800"))
    except ValueError:
        read_sec = 1800.0
    read_sec = max(120.0, read_sec)
    req_timeout = httpx.Timeout(30.0, connect=10.0, read=read_sec, write=120.0)
    logger.debug(
        "PDF: отправляем PDF в backend на OCR: %s (%s байт)", url, len(file_data)
    )
    # Логин инициатора в backend: см. _call_backend_ocr_image
    from app.core.cef_logger.cef_audit_context import cef_identity_headers
    from app.core.internal_auth import internal_rag_headers

    async with httpx.AsyncClient(
        timeout=req_timeout, verify=resolve_httpx_verify()
    ) as client:
        resp = await client.post(
            url,
            headers=internal_rag_headers(cef_identity_headers()),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    text = str(data.get("text") or "").strip()
    if not text:
        raise RuntimeError(str(data.get("error") or "backend OCR вернул пустой текст"))
    return {"text": text, "ocr_used": bool(data.get("ocr_used"))}


async def extract_text_from_pdf_bytes(
    file_data: bytes, filename: str = "document.pdf"
) -> Dict[str, Any]:
    """
    PyMuPDF извлекает текст.
    Если слой пустой/мусорный (скан) — распознавание идёт через astrachat-backend
    (pypdfium2 + vision-LLM). Локальный OCR (pdf2image + ocr-service) ВРЕМЕННО
    ОТКЛЮЧЁН (need_ocr = False ниже): чтобы вернуть — раскомментируйте условие.
    """
    logger.debug("PDF: извлечение текста, размер=%s байт", len(file_data))
    candidates: List[tuple] = []
    n_pages = 0

    def _extract_pymupdf() -> tuple:
        """Синхронный разбор PyMuPDF - уходит в отдельный поток (см. вызов ниже)."""
        doc = fitz.open(stream=file_data, filetype="pdf")
        if getattr(doc, "needs_pass", False):
            try:
                doc.authenticate("")
            except Exception:
                pass
        pages = doc.page_count
        parts: List[str] = []
        # Кэш MuPDF общий на процесс и за проход растёт вместе с числом
        # страниц: на 254 страницах это стоило до 70 МБ. Сбрасываем его
        # пачками - тогда пик зависит от размера пачки, а не от толщины
        # документа. Ни одна страница не пропускается: сброшенное MuPDF
        # при надобности прочитает заново.
        try:
            flush_every = max(1, int(os.getenv("RAG_PDF_PAGES_PER_FLUSH", "32")))
        except ValueError:
            flush_every = 32
        for i in range(pages):
            parts.append(doc.load_page(i).get_text() or "")
            if (i + 1) % flush_every == 0:
                _mupdf_store_shrink()
        doc.close()
        return pages, parts

    if PYMUPDF_AVAILABLE:
        try:
            with probe("PDF текстовый слой (PyMuPDF)", байт=len(file_data)):
                n_pages, parts = await asyncio.to_thread(_extract_pymupdf)
            t = _normalize_extracted_text("\n".join(parts))
            if t.strip():
                candidates.append(("pymupdf", t))
                logger.debug(
                    "PDF: PyMuPDF извлёк %s символов, страниц=%s", len(t), n_pages
                )
        except Exception as e:
            logger.warning("PDF: PyMuPDF ошибка: %s", e)

    text = ""
    confidence_scores: List[float] = []
    if candidates:
        label, text = max(candidates, key=lambda x: len(x[1].strip()))
        logger.debug(
            "PDF: для индекса выбран движок «%s» (%s символов)",
            label,
            len(text.strip()),
        )

    weak_layer = _looks_like_low_quality_pdf_text(text, n_pages)

    local_ocr_produced_text = False
    ocr_source: Optional[str] = None

    # ==== ocr-service (poppler + Surya): ВРЕМЕННО ОТКЛЮЧЁН ====
    # Пока распознавание PDF-сканов идёт через backend (pypdfium2 + vision-LLM).
    # Чтобы вернуть ocr-service — удалите строку ```need_ocr = False``` и раскомментируйте
    # исходное условие ниже; блок локального OCR (под ```if need_ocr:```) не менялся.
    # need_ocr = (
    #     (not text.strip() or weak_layer) and PDF2IMAGE_AVAILABLE and PIL_AVAILABLE
    # )
    need_ocr = False
    if need_ocr:
        if weak_layer:
            logger.info(
                "PDF: текстовый слой низкого качества (%s симв., страниц=%s) — пробуем OCR",
                len(text.strip()),
                max(n_pages, 1),
            )
        else:
            logger.info(
                "PDF: текстовый слой пуст — rasterize+OCR (poppler + ocr-service)"
            )
        try:

            def _rasterize(dpi: int) -> list:
                """poppler + PIL: десятки секунд на большом файле — тоже в поток."""
                return pdf_convert_from_bytes(file_data, dpi=dpi)

            def _encode_png(img) -> bytes:
                buf = BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()

            try:
                images = await asyncio.to_thread(_rasterize, 300)
            except Exception as e_dpi:
                logger.warning(
                    "PDF: pdf2image dpi=300 не удалось (%s), пробуем dpi=150", e_dpi
                )
                images = await asyncio.to_thread(_rasterize, 150)
            logger.debug("PDF: растеризация в %s страниц для OCR", len(images))

            ocr_text = ""
            ocr_confidence_scores: List[float] = []
            for i, image in enumerate(images):
                page_image_data = await asyncio.to_thread(_encode_png, image)
                result = await _call_ocr_service(
                    page_image_data, f"page*{i+1}.png", languages="ru,en"
                )
                if result.get("success"):
                    page_text = result.get("text", "")
                    ocr_text += f"\n--- Страница {i+1} ---\n{page_text}\n"
                    ocr_confidence_scores.append(
                        float(result.get("confidence", 50.0) or 50.0)
                    )
                else:
                    logger.warning(
                        "PDF OCR: страница %s: %s",
                        i + 1,
                        result.get("error", "Unknown"),
                    )
                    ocr_confidence_scores.append(50.0)

            ocr_text = _normalize_extracted_text(ocr_text)
            if ocr_text.strip():
                local_ocr_produced_text = True
                ocr_source = "ocr_service"
                if len(ocr_text.strip()) >= len(text.strip()):
                    text = ocr_text
                    confidence_scores = ocr_confidence_scores
                    logger.info(
                        "PDF: использован OCR-текст (%s символов)", len(text.strip())
                    )
                else:
                    logger.info(
                        "PDF: OCR не дал больше текста (%s vs %s симв.) — оставляем текстовый слой",
                        len(ocr_text.strip()),
                        len(text.strip()),
                    )
            else:
                logger.warning("PDF: OCR не вернул текст")
        except Exception as e:
            logger.warning(
                "PDF: OCR недоступен или сбой (poppler/pdf2image/ocr-service): %s", e
            )

    # OCR PDF-сканов через astrachat-backend (pypdfium2 + vision-LLM, тот же
    # механизм, что и вложения в чате). Пока ocr-service временно отключён
    # (need_ocr = False выше) — это ОСНОВНОЙ путь распознавания сканов.
    if (not text.strip() or weak_layer) and not local_ocr_produced_text:
        logger.info(
            "PDF: нет извлекаемого текста (скан) — OCR через backend (pypdfium2 + vision-LLM)"
        )
        try:
            be = await _call_backend_ocr_pdf(file_data, filename)
            be_text = _normalize_extracted_text(str(be.get("text", "") or ""))
            if be_text.strip() and len(be_text.strip()) > len((text or "").strip()):
                text = be_text
                # Vision-LLM не отдаёт confidence — ставим 80 (лучше 0, хуже 95+).
                confidence_scores = [80.0] * max(1, n_pages or 1)
                ocr_source = "backend_llm"
                logger.info(
                    "PDF: использован OCR-текст из backend (%s символов)",
                    len(text.strip()),
                )
            else:
                logger.warning(
                    "PDF: backend OCR не дал больше текста (%s vs %s симв.)",
                    len(be_text.strip()),
                    len((text or "").strip()),
                )
        except Exception as e:
            logger.warning("PDF: OCR через backend недоступен или сбой: %s", e)

    # Гибридный PDF: текстовый слой хороший, но на страницах вставлены
    # картинки (скриншоты/сканы фрагментов) — их текст без OCR в слой не
    # попадает. Распознаём встроенные изображения и дописываем их текст.
    # Сканы целиком уже обработаны полным OCR выше (страницы перерисованы,
    # картинки уже внутри рендера) — здесь только text-документы с картинками.
    if text.strip() and not weak_layer:
        try:
            with probe("PDF встроенные картинки (PyMuPDF)"):
                img_blobs = await asyncio.to_thread(_pdf_image_blobs, file_data)
                # Второй проход по документу закончен, дальше только сеть -
                # кэш больше не нужен. doc.close() внутри его не опустошает:
                # он общий на процесс.
                _mupdf_store_shrink()
            img_text = await _ocr_office_images(img_blobs, filename)
            if img_text:
                text = (text + "\n\n" + img_text) if text.strip() else img_text
                if ocr_source is None:
                    ocr_source = (
                        "ocr_service" if _ocr_engine() == "surya" else "backend_llm"
                    )
                logger.info(
                    "PDF: гибридный — добавлен OCR-текст %s встроенных картинок",
                    img_blobs.__len__() if img_blobs else 0,
                )
        except Exception as e:
            logger.warning("PDF: OCR встроенных картинок не удался: %s", e)

    if text.strip() and not confidence_scores:
        confidence_scores = [95.0] * max(1, n_pages or 1)

    avg_confidence = (
        sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0
    )
    confidence_per_word = 100.0 if avg_confidence > 90.0 else avg_confidence
    text = _normalize_extracted_text(text)
    confidence_info = _create_confidence_info_for_text(
        text or "", confidence_per_word, "pdf"
    )
    confidence_info["pages_processed"] = (
        len(confidence_scores) if confidence_scores else (n_pages or 0)
    )
    if ocr_source:
        confidence_info["ocr_source"] = ocr_source

    if not (text or "").strip():
        logger.error(
            "PDF: итоговый текст пуст (страниц≈%s). Для сканов нужен OCR: через backend "
            "(backend.base_url + OCR_VISION_MODEL_PATH) или ocr-service (ocr.url).",
            n_pages,
        )

    return {
        "text": text or "",
        "confidence_info": confidence_info,
        "file_type": "pdf",
        "pages": int(confidence_info.get("pages_processed") or 0),
    }


def extract_text_from_xls_xlrd(file_data: bytes) -> str:
    """Старый формат Excel .xls (не .xlsx)."""
    if not XLRD_AVAILABLE:
        raise RuntimeError("xlrd не установлен")
    book = xlrd.open_workbook(file_contents=file_data)
    parts: List[str] = []
    for si in range(book.nsheets):
        sh = book.sheet_by_index(si)
        parts.append(f"Лист: {sh.name}")
        for ri in range(sh.nrows):
            row = sh.row(ri)
            vals = [str(c.value) for c in row if c.value not in ("", None)]
            if vals:
                parts.append("\t".join(vals))
    return "\n".join(parts)


def extract_text_from_xlsx(file_data: bytes) -> str:
    if not OPENPYXL_AVAILABLE:
        raise RuntimeError("openpyxl не установлен")
    workbook = openpyxl.load_workbook(BytesIO(file_data), data_only=True)
    parts = []
    for sheet_name in workbook.sheetnames:
        sheet = workbook[sheet_name]
        parts.append(f"Лист: {sheet_name}")
        for row in sheet.iter_rows():
            row_vals = [str(c.value) for c in row if c.value is not None]
            if row_vals:
                parts.append("\t".join(row_vals))
    return "\n".join(parts)


def extract_text_from_txt(file_data: bytes) -> str:
    for enc in ("utf-8", "cp1251", "latin-1", "koi8-r"):
        try:
            return file_data.decode(enc)
        except UnicodeDecodeError:
            continue
    return file_data.decode("utf-8", errors="replace")


def extract_text_from_rtf(file_data: bytes) -> str:
    """RTF -> плоский текст."""
    from striprtf.striprtf import rtf_to_text

    return rtf_to_text(file_data.decode("utf-8", errors="ignore"))


async def extract_text_from_image_bytes(
    file_data: bytes, original_filename: str = "image.png"
) -> Dict[str, Any]:
    """Извлечение текста из изображения (OCR). Движок — _ocr_engine():
    backend vision-LLM по умолчанию (временная замена ocr-service),
    ocr-service (Surya) при RAG_OCR_ENGINE=surya."""
    logger.info(
        "Извлекаем текст из изображения (OCR, движок=%s, файл=%s, размер: %s байт)",
        _ocr_engine(),
        original_filename or "?",
        len(file_data),
    )
    if not PIL_AVAILABLE:
        result_text = "[Изображение. Для распознавания текста требуется Pillow и OCR (backend или ocr-service).]"
        return {
            "text": result_text,
            "file_type": "image",
            "confidence_info": _create_confidence_info_for_text(
                result_text, 0.0, "image"
            ),
        }

    # format сбрасывается после convert("RGB") — читаем его до конвертации.
    img = Image.open(BytesIO(file_data))
    src_fmt = (img.format or "").lower() or None
    img = img.convert("RGB")
    # Всегда шлём PNG: имя должно совпадать с фактическими байтами.
    ocr_filename = "image.png"
    if src_fmt in ("jpeg", "jpg", "png", "webp", "bmp"):
        ocr_filename = f"image.{'jpg' if src_fmt == 'jpeg' else src_fmt}"

    logger.debug(
        "Изображение открыто, исходный формат=%s, размер=%s, ocr_name=%s",
        src_fmt,
        img.size,
        ocr_filename,
    )

    # Увеличиваем маленькие изображения, как в backend
    min_side = 1024
    w, h = img.size
    if max(w, h) < min_side and max(w, h) > 0:
        scale = min_side / max(w, h)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        logger.debug(
            "Изображение увеличено для OCR: %sx%s -> %sx%s", w, h, new_w, new_h
        )

    buf = BytesIO()
    # JPEG для исходных JPEG — меньше трафика; иначе PNG (прозрачность уже снята).
    if src_fmt in ("jpeg", "jpg"):
        img.save(buf, format="JPEG", quality=92)
        ocr_filename = "image.jpg"
    else:
        img.save(buf, format="PNG")
        ocr_filename = "image.png"
    image_to_send = buf.getvalue()

    # Движок OCR: backend vision-LLM (по умолчанию, временная замена ocr-service)
    # или ocr-service (Surya) при RAG_OCR_ENGINE=surya — см. _ocr_image_bytes.
    engine = _ocr_engine()
    if engine == "backend":
        try:
            text = await _call_backend_ocr_image(image_to_send, ocr_filename)
        except Exception as e:
            logger.warning("Изображение: OCR через backend не удался: %s", e)
            return {
                "text": "",
                "file_type": "image",
                "confidence_info": {
                    "confidence": 0.0,
                    "text_length": 0,
                    "file_type": "image",
                    "ocr_available": False,
                    "error": str(e),
                    "words": [],
                },
            }
        if not text.strip():
            logger.info("Изображение: backend OCR не нашёл текста")
            return {
                "text": "",
                "file_type": "image",
                "confidence_info": _create_confidence_info_for_text("", 0.0, "image"),
            }
        logger.info("Изображение: backend OCR извлёк %s символов", len(text))
        return {
            "text": text,
            "file_type": "image",
            "confidence_info": _create_confidence_info_for_text(text, 80.0, "image"),
        }

    try:
        result = await _call_ocr_service(image_to_send, ocr_filename, languages="ru,en")
    except Exception as e:
        logger.warning("Ошибка OCR: %s", e)
        return {
            "text": "",
            "file_type": "image",
            "confidence_info": {
                "confidence": 0.0,
                "text_length": 0,
                "file_type": "image",
                "ocr_available": False,
                "error": str(e),
                "words": [],
            },
        }

    if not result.get("success"):
        error_msg = result.get("error", "Неизвестная ошибка")
        logger.warning("Surya OCR вернул ошибку: %s", error_msg)
        return {
            "text": "",
            "file_type": "image",
            "confidence_info": {
                "confidence": 0.0,
                "text_length": 0,
                "file_type": "image",
                "ocr_available": False,
                "error": error_msg,
                "words": [],
            },
        }

    text = result.get("text", "") or ""
    words = result.get("words", []) or []
    avg_confidence = float(result.get("confidence", 0.0) or 0.0)

    if not text.strip():
        logger.info("Surya OCR не смог извлечь текст из изображения (текст пустой)")
        return {
            "text": "",
            "file_type": "image",
            "confidence_info": {
                "confidence": 0.0,
                "text_length": 0,
                "file_type": "image",
                "ocr_available": False,
                "words": [],
            },
        }

    logger.info(
        "Surya OCR успешно извлек %s символов, %s слов, средняя уверенность: %.2f%%",
        len(text),
        len(words),
        avg_confidence,
    )

    confidence_info = {
        "confidence": avg_confidence,
        "text_length": len(text),
        "file_type": "image",
        "ocr_available": True,
        "words": words,
    }
    return {"text": text, "file_type": "image", "confidence_info": confidence_info}


async def parse_document(file_data: bytes, filename: str) -> Optional[Dict[str, Any]]:
    """Разбор файла с защитой от падения конкретного парсера.
    Причина остаётся видимой: logger.exception пишет её со стеком, но
    один раз и на уровне ERROR, а не через падение запроса.
    """
    try:
        # Верхний замер: сюда входит всё, что делает парсер конкретного
        # формата. Вложенные зонды разложат это по стадиям, а лесенка
        # отступов в логе покажет, что во что вложено.
        with probe("разбор файла", файл=filename or "?", байт=len(file_data)):
            return await _parse_document_by_ext(file_data, filename)
    except Exception:
        logger.exception(
            "Не удалось разобрать файл «%s»: парсер завершился ошибкой",
            filename or "?",
        )
        return None
    finally:
        # Разбор - самый тяжёлый кусок запроса, и вся его память в C.
        # Отдаём здесь, а не в конце запроса: иначе пик разбора сложится
        # с эмбеддингами, а на соседней загрузке - и с её пиком.
        mem_release.release(f"разбор файла {filename or '?'}")


async def _parse_document_by_ext(
    file_data: bytes, filename: str
) -> Optional[Dict[str, Any]]:
    """
    По расширению файла выбираем парсер и возвращаем структуру:
    {"text": str, "confidence_info": dict}
    """
    name = (filename or "").lower()
    # Корректно извлекаем только сам суффикс (".docx", ".pdf" и т.п.)
    dot = name.rfind(".")
    ext = name[dot:] if dot != -1 else ""
    # .doc, сохранённый как docx/rtf, и аналоги для xls/ppt.
    ext = real_office_ext(ext, file_data)
    if ext != ".pdf" and _pdf_magic_bytes(file_data):
        logger.info(
            "parse_document: файл «%s» без .pdf, но с сигнатурой PDF — парсим как PDF",
            filename or "?",
        )
        return await extract_text_from_pdf_bytes(file_data, filename or "document.pdf")
    if ext in (".docx", ".docm"):
        if not DOCX_AVAILABLE:
            return None
        text = extract_text_from_docx(file_data)
        img_text = await _ocr_office_images(
            _docx_image_blobs(file_data), filename or "document.docx"
        )
        if img_text:
            text = (text + "\n\n" + img_text) if text.strip() else img_text
        return {
            "text": text,
            "confidence_info": _create_confidence_info_for_text(text, 100.0, "docx"),
        }
    if ext == ".pdf":
        return await extract_text_from_pdf_bytes(file_data, filename or "document.pdf")
    if ext in (".pptx", ".pptm"):
        try:
            text = await extract_text_from_pptx(file_data)
        except Exception as e:
            logger.error("Ошибка парсинга .pptx/.pptm: %s", e)
            return None
        return {
            "text": text,
            "confidence_info": _create_confidence_info_for_text(text, 100.0, "pptx"),
        }
    if ext in (".xlsx", ".xlsm"):
        if not OPENPYXL_AVAILABLE:
            logger.warning("Парсинг .xlsx/.xlsm: openpyxl не установлен")
            return None
        try:
            text = extract_text_from_xlsx(file_data)
        except Exception as e:
            logger.error("Ошибка парсинга .xlsx/.xlsm: %s", e)
            return None
        img_text = await _ocr_office_images(
            _xlsx_image_blobs(file_data), filename or "table.xlsx"
        )
        if img_text:
            text = (text + "\n\n" + img_text) if text.strip() else img_text
        return {
            "text": text,
            "confidence_info": _create_confidence_info_for_text(text, 100.0, "excel"),
        }
    if ext == ".xls":
        if not XLRD_AVAILABLE:
            logger.warning("Парсинг .xls: xlrd не установлен")
            return None
        try:
            text = extract_text_from_xls_xlrd(file_data)
        except Exception as e:
            logger.error("Ошибка парсинга .xls: %s", e)
            return None
        return {
            "text": text,
            "confidence_info": _create_confidence_info_for_text(text, 100.0, "excel"),
        }
    if ext == ".doc":
        try:
            text = await extract_doc_text(file_data, filename or "")
        except Exception as e:
            logger.error("Ошибка парсинга .doc: %s", e)
            return None
        return {
            "text": text,
            "confidence_info": _create_confidence_info_for_text(text, 100.0, "doc"),
        }
    if ext == ".ppt":
        try:
            text = await extract_text_from_ppt(file_data, filename or "")
        except Exception as e:
            logger.error("Ошибка парсинга .ppt: %s", e)
            return None
        return {
            "text": text,
            "confidence_info": _create_confidence_info_for_text(text, 100.0, "ppt"),
        }
    if ext in (".txt", ".md", ".markdown", ".log", ".csv"):
        text = extract_text_from_txt(file_data)
        return {
            "text": text,
            "confidence_info": _create_confidence_info_for_text(text, 100.0, "txt"),
        }
    if ext == ".rtf":
        text = extract_text_from_rtf(file_data)
        return {
            "text": text,
            "confidence_info": _create_confidence_info_for_text(text, 100.0, "rtf"),
        }
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        return await extract_text_from_image_bytes(file_data, filename or "image.png")
    return None
