"""
routes/documents.py - загрузка, удаление, запросы к документам, отчеты OCR
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Annotated
from PIL import Image

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from backend.app_state import (
    minio_client,
    settings,
)
from backend.auth.jwt_handler import get_current_user
from backend.schemas import DocumentQueryRequest
from backend.settings.cef_logger.cef_logger import log_cef_event
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress
from backend.utils.http_errors import public_error_detail

logger = get_logger(__name__)


def _documents_bucket_name() -> str:
    """Имя bucket для документов/вложений — из settings (YAML + ENV), не hardcoded default."""
    minio_cfg = getattr(settings, "minio", None)
    if minio_cfg is not None:
        docs_bucket = getattr(minio_cfg, "documents_bucket_name", None)
        if docs_bucket:
            return str(docs_bucket)
        main_bucket = getattr(minio_cfg, "bucket_name", None)
        if main_bucket:
            return str(main_bucket)
    return (
        os.getenv("MINIO_DOCUMENTS_BUCKET_NAME")
        or os.getenv("MINIO_BUCKET_NAME")
        or "astrachat-documents"
    )


router = APIRouter(prefix="/api/documents", tags=["documents"])
INLINE_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024
# Env INLINE_ATTACH_DIR (ConfigMap/Deployment K8s) — каталог inline-вложений («+») в поде.
INLINE_ATTACH_DIR_ENV = "INLINE_ATTACH_DIR"
# Маркер bucket для inline-вложений («+»), сохранённых в каталоге пода (не MinIO).
INLINE_ATTACH_POD_TMP_BUCKET = "__pod_tmp__"
_INLINE_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_INLINE_DOC_EXTS = {
    ".pdf",
    ".docx",
    ".docm",
    ".doc",
    ".xlsx",
    ".xls",
    ".xlsm",
    ".txt",
    ".md",
    ".log",
    ".rtf",
}
_INLINE_ATTACH_SUPPORTED_EXTENSIONS = sorted(_INLINE_IMAGE_EXTS | _INLINE_DOC_EXTS)
_CONTENT_TYPE_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".doc": "application/msword",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".log": "text/plain",
    ".rtf": "application/rtf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
_CYRILLIC_LATIN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ]")


def _inline_attach_supported_extensions_label() -> str:
    return ", ".join(_INLINE_ATTACH_SUPPORTED_EXTENSIONS)


def _unsupported_inline_attach_extension_detail(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    supported = _inline_attach_supported_extensions_label()
    if ext:
        return f"{ext} файлы не поддерживаются, поддерживаются только следующие расширения файлов: {supported}"
    return f"файлы без расширения не поддерживаются, поддерживаются только следующие расширения файлов: {supported}"


def _emit_attach_info(message: str) -> None:
    logger.info(message)


def _log_inline_attach_upload_success(filename: str) -> None:
    logger.info("файл %s загружен успешно", filename)


def _log_inline_attach_upload_failure(
    request: Request,
    filename: str,
    detail: str,
    *,
    file_size: int | None = None,
    status_code: int = 400,
    reject_code: str | None = None,
) -> None:
    logger.info("файл %s загружен не успешно. %s", filename, detail)
    extra: dict = {"fname": filename, "reason": detail}
    if file_size is not None:
        extra["fsize"] = file_size
    if reject_code:
        extra["cs1"] = reject_code
        extra["cs1Label"] = "RejectReason"
    log_cef_event("FS008", request=request, status_code=status_code, extra=extra)


def _resolve_content_type(filename: str, content_type: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if content_type:
        return content_type.lower()
    return _CONTENT_TYPE_BY_EXT.get(ext, "application/octet-stream")


def _detect_inline_attachment_kind(filename: str, content_type: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    ct = (content_type or "").lower()
    if ct.startswith("image/") or ext in _INLINE_IMAGE_EXTS:
        return "image"
    if ext in _INLINE_DOC_EXTS:
        return "document"
    doc_mimes = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-word.document.macroEnabled.12",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroEnabled.12",
        "application/vnd.ms-excel",
        "text/plain",
        "text/markdown",
        "application/rtf",
        "text/rtf",
    }
    if ct in doc_mimes:
        return "document"
    return "unsupported"


async def _attachment_provider_vision() -> tuple[str | None, bool | None]:
    """(id провайдера, умеет ли он картинки) для решения об OCR.

    Путь модели берётся из глобального состояния и в схеме с провайдерами
    почти всегда пуст: 'get_model_info()["path"]' — это «что загружено
    в llm-svc», а туда никто ничего не грузит. Поэтому пустой путь означает
    «модель по умолчанию», а не «модель неизвестна»: 'registry.resolve("")'
    для того и существует. Раньше проверка стояла под 'if model_path' и
    не выполнялась вовсе — картинка безусловно уходила в OCR, даже когда
    за шлюзом стоит мультимодальная модель.

    Умение читается из конфига провайдера ('extra.vision'), а капабилити
    класса — только запасное значение: у всех 'openai-compat' она равна
    True скопом и о конкретной модели за шлюзом ничего не знает.
    """
    from backend.app_state import get_current_model_path
    from backend.llm_providers import get_registry

    registry = await get_registry()
    provider, _model_id = registry.resolve(get_current_model_path() or "")
    config = getattr(provider, "config", None)
    extra = getattr(config, "extra", None) or {}
    if "vision" in extra:
        return provider.id, bool(extra.get("vision"))
    if getattr(provider, "kind", None) == "llm-svc":
        return provider.id, False
    return provider.id, bool(provider.capabilities.vision)


async def _analyze_inline_attachment_for_model(
    filename: str, content_type: str, file_size: int
) -> dict:
    from backend.app_state import get_current_model_path

    kind = _detect_inline_attachment_kind(filename, content_type)
    format_allowed = kind != "unsupported"
    size_allowed = file_size <= INLINE_ATTACHMENT_MAX_BYTES
    model_path = get_current_model_path()
    provider_id = None
    model_vision = None
    model_compatible = True
    incompatibility_reason = None
    try:
        provider_id, model_vision = await _attachment_provider_vision()
        if kind == "image" and (not model_vision):
            model_compatible = False
            incompatibility_reason = "модель не поддерживает vision (изображения)"
    except Exception:
        logger.exception("[ChatAttach] не удалось определить capabilities модели")
    allowed = format_allowed and size_allowed
    reject_reason = None
    if not format_allowed:
        reject_reason = (
            "Неподдерживаемый формат "
            f"({_inline_attach_supported_extensions_label()})"
        )
    elif not size_allowed:
        reject_reason = (
            f"Размер превышает {INLINE_ATTACHMENT_MAX_BYTES // (1024 * 1024)}MB"
        )
    return {
        "filename": filename,
        "content_type": content_type,
        "size": file_size,
        "kind": kind,
        "format_allowed": format_allowed,
        "size_allowed": size_allowed,
        "allowed": allowed,
        "model_path": model_path,
        "provider_id": provider_id,
        "model_vision": model_vision,
        "model_compatible": model_compatible,
        **({"reject_reason": reject_reason} if reject_reason else {}),
        **(
            {"incompatibility_reason": incompatibility_reason}
            if incompatibility_reason
            else {}
        ),
    }


def _log_inline_attachment_debug(stage: str, analysis: dict, **extra) -> None:
    payload = {"stage": stage, **analysis, **extra}
    logger.debug(
        "[ChatAttach] %s", json.dumps(payload, ensure_ascii=False, default=str)
    )


def _log_inline_attach_pod_structure(
    *,
    filename: str,
    highlight_object: str | None = None,
    stage: str = "attachments-storage-structure",
) -> None:
    # structure = _snapshot_inline_attach_pod_structure(highlight_object=highlight_object)
    # logger.debug(
    #     "[ChatAttach] %s filename=%s\n%s",
    #     stage,
    #     filename,
    #     structure.get("tree", ""),
    # )
    # _log_inline_attachment_debug(stage, {"filename": filename}, **structure)
    pass


def _log_chat_attach_request(request: Request, **extra) -> None:
    logger.debug(
        "[ChatAttach] %s",
        json.dumps(
            {
                "stage": "request-start",
                "content_length_header": request.headers.get("content-length"),
                "content_type_header": request.headers.get("content-type"),
                "client_host": (
                    getattr(request.client, "host", None) if request.client else None
                ),
                **extra,
            },
            ensure_ascii=False,
            default=str,
        ),
    )


def _looks_like_real_text(
    text: str, min_chars: int = 40, min_letter_ratio: float = 0.5
) -> bool:
    """Отличает нормальный текстовый слой PDF от мусора/шума. Проверяем не только длину,
    но и долю букв (кириллица/латиница) среди непробельных символов."""
    stripped = text.strip()
    if len(stripped) < min_chars:
        return False
    non_space = re.sub(r"\s", "", stripped)
    if not non_space:
        return False
    letters = len(_CYRILLIC_LATIN_RE.findall(non_space))
    return (letters / len(non_space)) >= min_letter_ratio


# Пространство имён WordprocessingML — таблицы .docx читаем по XML, см. ниже.
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

def _docx_row_texts(tr) -> list[str]:
    """Тексты ячеек строки таблицы .docx по её XML-элементу w:tr.

    Намеренно НЕ через row.cells: python-docx там разворачивает вертикальные
    объединения и на ячейке <w:vMerge w:val="continue"> в самой верхней строке
    таблицы падает с ValueError("no tr above topmost tr in w:tbl") — начала
    объединения выше просто нет. Такие таблицы приезжают из конвертеров и из
    разрывов по страницам, Word открывает их молча. По XML разворачивать нечего:
    берём w:t каждой ячейки как есть.
    """
    out: list[str] = []
    for tc in tr.findall(_W_NS + "tc"):
        parts: list[str] = []
        for node in tc.iter():
            tag = node.tag
            if isinstance(tag, str) and tag == _W_NS + "t" and node.text:
                parts.append(node.text)
        out.append(" ".join("".join(parts).split()))
    return out


def _extract_inline_payload(content: bytes, filename: str, content_type: str) -> dict:
    """
    Извлекает содержимое для inline-передачи в модель (без RAG).
    Возвращает: { type: 'text'|'image', content: str, cs1: str }
    """
    import base64
    import io

    ext = os.path.splitext(filename)[1].lower()
    ct = (content_type or "").lower()
    if ct.startswith("image/") or ext in _INLINE_IMAGE_EXTS:
        # BMP несжатый (1920x1080 ≈ 6 МБ, +33% в base64) и не заявлен в
        # OpenAI-совместимых vision API - перегоняем в PNG.
        if ext == ".bmp" or ct == "image/bmp":
            buf = io.BytesIO()
            Image.open(io.BytesIO(content)).convert("RGB").save(
                buf, format="PNG", optimize=True
            )
            content = buf.getvalue()
            mime = "image/png"
        else:
            mime = (
                ct
                if ct.startswith("image/")
                else _CONTENT_TYPE_BY_EXT.get(ext, "image/jpeg")
            )
        b64 = base64.b64encode(content).decode("ascii")
        return {"type": "image", "content": f"data:{mime};base64,{b64}", "cs1": "image"}
    if ext == ".pdf":
        from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages_text = []
        for i, page in enumerate(reader.pages, 1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages_text.append(f"""[Страница {i}]
{page_text}""")
        text = (
            "\n\n".join(pages_text)
            if pages_text
            else "(PDF не содержит извлекаемого текста)"
        )
        return {"type": "text", "content": text, "cs1": "pdf"}
    if ext in (".docx", ".docm"):
        import docx as docx_lib

        doc = docx_lib.Document(io.BytesIO(content))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        # Текст внутри таблиц doc.paragraphs не отдаёт - собираем отдельно
        for table in doc.tables:
            for tr in table._tbl.tr_lst:
                line = "\t".join(_docx_row_texts(tr)).strip()
                if line:
                    parts.append(line)
        text = "\n".join(parts) if parts else "(Документ не содержит текста)"
        return {
            "type": "text",
            "content": text,
            "cs1": "docx" if ext == ".docx" else "docm",
        }
    if ext in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        lines = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            lines.append(f"=== Лист: {sheet_name} ===")
            for row in ws.iter_rows(values_only=True):
                row_str = "\t".join(("" if c is None else str(c) for c in row))
                if row_str.strip():
                    lines.append(row_str)
        text = "\n".join(lines) if lines else "(Таблица не содержит данных)"
        return {"type": "text", "content": text, "cs1": "xlsx"}
    if ext == ".xls":
        # Старый бинарный Excel (BIFF): openpyxl его не читает, только xlrd.
        import xlrd

        book = xlrd.open_workbook(file_contents=content)
        lines = []
        for si in range(book.nsheets):
            sh = book.sheet_by_index(si)
            lines.append(f"=== Лист: {sh.name} ===")
            for ri in range(sh.nrows):
                vals = [str(c.value) for c in sh.row(ri) if c.value not in ("", None)]
                if vals:
                    lines.append("\t".join(vals))
        text = "\n".join(lines) if lines else "(Таблица не содержит данных)"
        return {"type": "text", "content": text, "cs1": "xls"}
    if ext == ".rtf":
        from striprtf.striprtf import rtf_to_text

        text = rtf_to_text(content.decode("utf-8", errors="ignore"))
        return {
            "type": "text",
            "content": text or "(RTF не содержит текста)",
            "cs1": "rtf",
        }
    text = content.decode("utf-8", errors="replace")
    return {"type": "text", "content": text, "cs1": "text"}


def _inline_attach_pod_dir(*, required: bool = True) -> str | None:
    """Каталог inline-вложений («+») — только из env INLINE_ATTACH_DIR (ConfigMap K8s)."""
    base = (os.getenv(INLINE_ATTACH_DIR_ENV) or "").strip()
    if not base:
        if required:
            logger.error(
                "%s не задан — укажите путь к каталогу вложений в ConfigMap/Deployment пода astra-chat-backend",
                INLINE_ATTACH_DIR_ENV,
            )
        return None
    os.makedirs(base, exist_ok=True)
    return base


def _sanitize_inline_attach_user_dir(username: str) -> str:
    """Безопасное имя каталога пользователя (gpbuXXXXX)."""
    raw = (username or "").strip()
    if not raw:
        return "anonymous"
    safe = re.sub(r"[^a-zA-Z0-9._-]", "", raw)
    if not safe:
        logger.warning(
            "Inline attach: логин %r не содержит допустимых символов для имени каталога — используется anonymous",
            raw,
        )
        return "anonymous"
    if safe != raw:
        logger.debug("Inline attach: логин %r нормализован в каталог %r", raw, safe)
    return safe


def _inline_attach_date_dir(*, now: datetime | None = None) -> str:
    """Относительный путь каталога дня: yyyy/mm/dd."""
    dt = now or datetime.now()
    return f"{dt.strftime('%Y')}/{dt.strftime('%m')}/{dt.strftime('%d')}"


def _build_inline_attach_storage_dir(
    username: str, *, now: datetime | None = None
) -> str | None:
    """{INLINE_ATTACH_DIR}/{user}/{yyyy}/{mm}/{dd}/"""
    base = _inline_attach_pod_dir()
    if not base:
        return None
    user_dir = _sanitize_inline_attach_user_dir(username)
    date_dir = _inline_attach_date_dir(now=now)
    storage_dir = os.path.join(base, user_dir, *date_dir.split("/"))
    os.makedirs(storage_dir, exist_ok=True)
    return storage_dir


def _normalize_inline_attach_object_key(object_name: str) -> str | None:
    """Относительный ключ объекта (user/date/file) без path traversal."""
    raw = str(object_name or "").strip().replace("\\", "/")
    parts = [part for part in raw.split("/") if part and part not in (".", "..")]
    if not parts:
        return None
    return "/".join(parts)


def _inline_attach_key_belongs_to_user(
    object_name: str, current_user: dict | None
) -> bool:
    """Ключ pod-вложения ``{user}/...`` принадлежит пользователю (админ — любой)."""
    if not isinstance(current_user, dict):
        return False
    if current_user.get("is_admin"):
        return True
    username = current_user.get("username") or current_user.get("user_id")
    if not username:
        return False
    rel_key = _normalize_inline_attach_object_key(object_name)
    if not rel_key or "/" not in rel_key:
        return False
    return rel_key.split("/", 1)[0] == _sanitize_inline_attach_user_dir(str(username))


def _allowed_inline_minio_buckets() -> frozenset[str]:
    """Бакеты MinIO, из которых разрешено отдавать inline-вложения (только настроенные)."""
    return frozenset({_documents_bucket_name()})


def _inline_attach_pod_file_path(object_name: str) -> str | None:
    """Безопасно разрешает путь к файлу в INLINE_ATTACH_DIR (защита от path traversal)."""
    rel_key = _normalize_inline_attach_object_key(object_name)
    if not rel_key:
        return None
    attachments_dir = _inline_attach_pod_dir(required=False)
    if not attachments_dir:
        return None
    root = os.path.realpath(attachments_dir)
    full_path = os.path.realpath(os.path.join(root, *rel_key.split("/")))
    if full_path != root and not full_path.startswith(root + os.sep):
        return None
    return full_path if os.path.isfile(full_path) else None


def read_inline_attachment_bytes(
    bucket: str, object_name: str
) -> tuple[bytes, str] | None:
    """Байты + MIME inline-вложения: pod ``__pod_tmp__`` или MinIO. None если нет файла."""
    bucket_s = str(bucket or "").strip()
    object_s = str(object_name or "").strip()
    if not bucket_s or not object_s:
        return None
    ext = os.path.splitext(object_s)[1].lower()
    mime = _CONTENT_TYPE_BY_EXT.get(ext, "application/octet-stream")
    if bucket_s == INLINE_ATTACH_POD_TMP_BUCKET:
        attach_path = _inline_attach_pod_file_path(object_s)
        if not attach_path:
            return None
        try:
            with open(attach_path, "rb") as attach_file:
                return (attach_file.read(), mime)
        except Exception:
            logger.exception("read_inline_attachment_bytes pod object=%s", object_s)
            return None
    if not minio_client:
        return None
    try:
        data = minio_client.download_file(object_s, bucket_name=bucket_s)
        return (data, mime) if data is not None else None
    except Exception:
        logger.exception(
            "read_inline_attachment_bytes minio bucket=%s object=%s", bucket_s, object_s
        )
        return None


def _collect_inline_attach_files(
    attachments_dir: str, *, highlight_object: str | None = None
) -> tuple[list[dict], int]:
    highlight_rel = (
        _normalize_inline_attach_object_key(highlight_object)
        if highlight_object
        else None
    )
    files: list[dict] = []
    total_bytes = 0
    for dirpath, _, filenames in os.walk(attachments_dir):
        for fname in sorted(filenames):
            full_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(full_path, attachments_dir).replace("\\", "/")
            size: int | None
            modified: str | None
            try:
                stat = os.stat(full_path)
                size = int(stat.st_size)
                modified = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()
                total_bytes += size
            except OSError:
                size = None
                modified = None
            files.append(
                {
                    "name": rel_path,
                    "size": size,
                    "modified": modified,
                    "just_added": bool(highlight_rel and rel_path == highlight_rel),
                }
            )
    files.sort(key=lambda item: item["name"])
    return files, total_bytes


def _is_inline_attach_file_entry(val) -> bool:
    return isinstance(val, dict) and "name" in val


def _build_inline_attach_nested_tree(files: list[dict]) -> dict:
    nested: dict = {}
    for entry in files:
        parts = entry["name"].split("/")
        node = nested
        for part in parts[:-1]:
            child = node.get(part)
            if _is_inline_attach_file_entry(child):
                node[part] = {"__shadow_file__": child}
                child = node[part]
            elif child is None:
                node[part] = {}
                child = node[part]
            node = child
        node[parts[-1]] = entry
    return nested


def _render_inline_attach_tree_lines(files: list[dict]) -> list[str]:
    if not files:
        return ["  (empty)"]

    nested = _build_inline_attach_nested_tree(files)
    lines: list[str] = []

    def render(node: dict, prefix: str) -> None:
        dir_keys = sorted(
            key
            for key, val in node.items()
            if isinstance(val, dict) and not _is_inline_attach_file_entry(val)
        )
        file_items = sorted(
            (
                (key, val)
                for key, val in node.items()
                if _is_inline_attach_file_entry(val)
            ),
            key=lambda item: item[0],
        )
        entries = [(key, True) for key in dir_keys] + [
            (key, False) for key, _ in file_items
        ]
        for idx, (key, is_dir) in enumerate(entries):
            is_last = idx == len(entries) - 1
            branch = "└── " if is_last else "├── "
            if is_dir:
                lines.append(f"{prefix}{branch}{key}/")
                extension = "    " if is_last else "│   "
                render(node[key], prefix + extension)
            else:
                entry = node[key]
                display_key = (
                    os.path.basename(entry["name"]) if key == "__shadow_file__" else key
                )
                size_part = (
                    f" ({entry['size']} B)"
                    if isinstance(entry.get("size"), int)
                    else ""
                )
                marker = "  ← new" if entry.get("just_added") else ""
                lines.append(f"{prefix}{branch}{display_key}{size_part}{marker}")

    render(nested, "  ")
    return lines


def _snapshot_inline_attach_pod_structure(
    *, highlight_object: str | None = None
) -> dict:
    """Снимок структуры INLINE_ATTACH_DIR: /attachments/{user}/{yyyy}/{mm}/{dd}/ — debug-лог."""
    attachments_dir = _inline_attach_pod_dir(required=False)
    if not attachments_dir:
        return {
            "attachments_dir": None,
            "attachments_dir_env": INLINE_ATTACH_DIR_ENV,
            "error": f"{INLINE_ATTACH_DIR_ENV} не задан в окружении пода",
            "file_count": 0,
            "total_bytes": 0,
            "tree": f"({INLINE_ATTACH_DIR_ENV} не задан)",
            "files": [],
        }
    try:
        files, total_bytes = _collect_inline_attach_files(
            attachments_dir, highlight_object=highlight_object
        )
    except OSError as exc:
        return {
            "attachments_dir": attachments_dir,
            "attachments_dir_env": INLINE_ATTACH_DIR_ENV,
            "error": str(exc),
            "file_count": 0,
            "total_bytes": 0,
            "tree": f"{attachments_dir}/\n  (ошибка чтения: {exc})",
            "files": [],
        }

    tree_lines = [f"{attachments_dir}/", *_render_inline_attach_tree_lines(files)]
    return {
        "attachments_dir": attachments_dir,
        "attachments_dir_env": INLINE_ATTACH_DIR_ENV,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "tree": "\n".join(tree_lines),
        "files": files,
        "highlight_object": highlight_object,
    }


def _try_save_bytes_to_pod_attachments(
    content: bytes, filename: str, content_type: str, *, username: str
) -> str | None:
    """Сохраняет inline-вложение («+») в {INLINE_ATTACH_DIR}/{user}/{yyyy}/{mm}/{dd}/."""
    storage_dir = _build_inline_attach_storage_dir(username)
    base_dir = _inline_attach_pod_dir(required=False)
    if not storage_dir or not base_dir:
        return None
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    is_image = ext in _INLINE_IMAGE_EXTS or (content_type or "").startswith("image/")
    prefix = "img_" if is_image else "doc_"
    stored_name = f"{prefix}{uuid.uuid4().hex}{ext}"
    full_path = os.path.join(storage_dir, stored_name)
    try:
        with open(full_path, "wb") as attach_file:
            attach_file.write(content)
        rel_key = os.path.relpath(full_path, base_dir).replace("\\", "/")
        logger.debug(
            "Inline-вложение сохранено в %s/%s/%s: %s",
            INLINE_ATTACH_DIR_ENV,
            _sanitize_inline_attach_user_dir(username),
            _inline_attach_date_dir(),
            full_path,
        )
        return rel_key
    except Exception:
        logger.exception(
            "Pod attachments save error filename=%s user=%s", filename, username
        )
        return None


def _delete_pod_attach(object_name: str) -> None:
    path = _inline_attach_pod_file_path(object_name)
    if not path:
        return
    with logged_suppress(logger):
        os.remove(path)


async def _ocr_image_payload_if_needed(
    content: bytes,
    filename: str,
    payload: dict,
    model_path: str | None,
) -> tuple[dict, str | None]:
    """
    llm-svc + GGUF — text-only: вместо base64 vision прогоняем OCR (Surya).
    """
    if payload.get("type") != "image":
        return payload, None

    use_ocr = True
    try:
        provider_id, vision = await _attachment_provider_vision()
        if vision:
            use_ocr = False
        logger.debug(
            "[ChatAttach] %s: провайдер=%s vision=%s → %s",
            filename,
            provider_id,
            vision,
            "картинка целиком" if not use_ocr else "OCR",
        )
    except Exception as exc:
        logger.debug("[ChatAttach] OCR routing: %s", exc)

    if not use_ocr:
        return payload, None

    try:
        from backend.agent_llm_svc import get_llm_service

        svc = await get_llm_service()
        if not svc.client.ocr_available:
            return (
                payload,
                "OCR-сервис не настроен — текстовая модель не распознает изображение",
            )

        ocr = await svc.client.recognize_text_from_image(content, filename)
        text = (ocr.get("text") or "").strip()
        if not text:
            return {
                "type": "text",
                "content": f"[Изображение «{filename}»: OCR не нашёл текста на изображении]",
                "cs1": "image-ocr-empty",
            }, "На изображении не обнаружен текст (OCR)"
        return {
            "type": "text",
            "content": f"[Текст с изображения «{filename}» (OCR)]:\n{text}",
            "cs1": "image-ocr",
        }, None
    except Exception as exc:
        logger.warning("[ChatAttach] OCR failed for %s: %s", filename, exc)
        return (
            payload,
            f"OCR не удался: {exc}. Текстовая модель не «видит» картинку напрямую.",
        )


def _extract_pdf_page_images(content: bytes) -> dict[int, list[tuple[int, bytes]]]:
    """Встроенные изображения PDF-страниц (0-based idx -> [(seq, png)]).

    Используется для гибридных PDF: страницы с нормальным текстовым слоем, на
    которых вставлены картинки (скриншоты, сканы фрагментов) — их текст без
    OCR в слой не попадает. Мелкие «иконки/буллеты» отсекаются по площади;
    результат перекодируется в PNG (единый формат для OCR-вызова).

    PyMuPDF — основной путь, pypdfium2 — fallback
    на случай, если PyMuPDF не установлен в поде.

    Тело - чистый CPU-код (await внутри нет): вызывать нужно через
    asyncio.to_thread, чтобы не блокировать event loop.
    OCR-этап (vision-LLM) идёт отдельно через общий семафор.
    """
    import io

    min_area = int(os.getenv("PDF_OCR_IMAGE_MIN_AREA", "12000"))  # ~110x110 px
    max_per_page = int(os.getenv("PDF_OCR_IMAGE_MAX_PER_PAGE", "8"))

    # ---- Основной путь: PyMuPDF ----
    try:
        import fitz

        doc = fitz.open(stream=content, filetype="pdf")
        out: dict[int, list[tuple[int, bytes]]] = {}
        seen: set[int] = set()
        try:
            for idx in range(doc.page_count):
                page = doc.load_page(idx)
                try:
                    infos = page.get_image_info(xrefs=True) or []
                except Exception:
                    continue
                found: list[tuple[int, bytes]] = []
                seq = 0
                for info in infos:
                    xref = int(info.get("xref") or 0)
                    if not xref or xref in seen:
                        continue
                    seen.add(xref)
                    w = int(info.get("width") or 0)
                    h = int(info.get("height") or 0)
                    if w and h and w * h < min_area:
                        continue
                    try:
                        im = doc.extract_image(xref)
                        blob = im.get("image") or b""
                    except Exception:
                        continue
                    if not blob:
                        continue
                    try:
                        pil_img = Image.open(io.BytesIO(blob)).convert("RGB")
                        buf = io.BytesIO()
                        pil_img.save(buf, format="PNG")
                        blob = buf.getvalue()
                        pil_img.close()
                    except Exception:
                        pass  # не смогли перекодить — берём как есть
                    found.append((seq, blob))
                    seq += 1
                    if seq >= max_per_page:
                        break
                if found:
                    out[idx] = found
        finally:
            doc.close()
        return out
    except ImportError:
        pass  # PyMuPDF не установлен — пробует pypdfium2 ниже
    except Exception as exc:
        logger.debug("[ChatAttach] PDF inline images (fitz) failed: %s", exc)
        return {}

    # ---- Fallback: pypdfium2 get_objects (только top-level объекты) ----
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(content)
    except Exception:
        return {}
    out: dict[int, list[tuple[int, bytes]]] = {}
    try:
        for idx in range(len(pdf)):
            page = pdf.get_page(idx)
            try:
                try:
                    objs = list(page.get_objects())
                except Exception:
                    return {}
                found: list[tuple[int, bytes]] = []
                seq = 0
                for o in objs:
                    if getattr(o, "type", 0) != 2:  # 2 = PDF_PAGE_OBJ_IMAGE
                        continue
                    try:
                        img = o.get_image_pil()
                    except Exception:
                        continue
                    if img is None:
                        continue
                    try:
                        w, h = img.size
                        if w * h < min_area:
                            continue
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        found.append((seq, buf.getvalue()))
                        seq += 1
                        if len(found) >= max_per_page:
                            break
                    except Exception:
                        continue
                    finally:
                        try:
                            img.close()
                        except Exception:
                            pass
                if found:
                    out[idx] = found
            finally:
                page.close()
    finally:
        pdf.close()
    return out


async def _ocr_pdf_payload_if_needed(
    content: bytes,
    filename: str,
    payload: dict,
    page_indices: list[int] | None = None,
) -> tuple[dict, str | None]:
    """
    PDF постранично, упрощённая логика:
      1) текстовый слой, если он похож на настоящий текст — берём его;
      2) иначе - рендер ВСЕЙ страницы целиком (без кропов встроенных картинок)
         + OCR, параллельно по нескольким страницам сразу (с лимитом).
      3) страницы с текстовым слоем ДОПОЛНИТЕЛЬНО: встроенные картинки
         (гибридный PDF) — извлекаются и OCR-ятся, текст вставляется на
         страницу.
    page_indices — только эти страницы рассматривать (RAG: скан-страницы
    гибрида); None — все.
    Порядок страниц в итоговом тексте всегда 1..N, независимо от того, в
    каком порядке завершились параллельные OCR-запросы.
    """
    if payload.get("cs1") != "pdf":
        return payload, None

    try:
        import io
        import asyncio
        import pypdfium2 as pdfium
        from PyPDF2 import PdfReader
        from backend.agent_llm_svc import get_llm_service

        svc = await get_llm_service()
        if not svc.client.ocr_available:
            logger.warning("[ChatAttach] PDF OCR skip %s: OCR не настроен", filename)
            return payload, "OCR-сервис не настроен — PDF-скан не распознать"

        reader = PdfReader(io.BytesIO(content))
        pdf = pdfium.PdfDocument(content)
        n_pages = len(pdf)
        if page_indices is not None:
            target_pages = [
                idx
                for idx in sorted(set(int(p) for p in page_indices))
                if 0 <= idx < n_pages
            ]
        else:
            target_pages = list(range(n_pages))
        logger.info(
            "[ChatAttach] PDF OCR start %s pages=%s target=%s",
            filename,
            n_pages,
            target_pages if page_indices is not None else "all",
        )

        page_layers: dict[int, str] = {}
        page_images: dict[int, bytes] = {}
        try:
            # Текст или OCR
            for idx in target_pages:
                try:
                    layer = (reader.pages[idx].extract_text() or "").strip()
                except Exception:
                    layer = ""

                if _looks_like_real_text(layer):
                    page_layers[idx] = layer
                    logger.info(
                        "[ChatAttach] PDF page %s/%s: текстовый слой OK (%s симв.)",
                        idx + 1,
                        n_pages,
                        len(layer),
                    )
                    continue

                page = pdf.get_page(idx)
                try:
                    pil_image = page.render(scale=2.0).to_pil()
                    buf = io.BytesIO()
                    pil_image.save(buf, format="PNG")
                    page_images[idx] = buf.getvalue()
                finally:
                    page.close()
        finally:
            pdf.close()

        # Гибридный PDF: страницы с текстовым слоем, на которых вставлены
        # картинки — их текст без OCR в слой не попадает. Извлекаем и OCR-им.
        page_inline_images: dict[int, list[tuple[int, bytes]]] = {}
        if page_layers:
            try:
                page_inline_images = await asyncio.to_thread(
                    _extract_pdf_page_images, content
                )
            except Exception as exc:
                logger.debug(
                    "[ChatAttach] PDF inline images extract failed %s: %s",
                    filename,
                    exc,
                )
            page_inline_images = {
                idx: imgs
                for idx, imgs in page_inline_images.items()
                if idx in page_layers
            }
        inline_count = sum(len(v) for v in page_inline_images.values())
        max_inline = int(os.getenv("PDF_OCR_INLINE_IMAGES_MAX", "50"))
        if inline_count > max_inline:
            logger.warning(
                "[ChatAttach] PDF inline images: %s > лимита %s — отключаем OCR картинок",
                inline_count,
                max_inline,
            )
            page_inline_images = {}
        if page_inline_images:
            logger.info(
                "[ChatAttach] PDF inline images %s: %s картинок на %s страницах",
                filename,
                inline_count,
                len(page_inline_images),
            )

        # Параллельный OCR всех страниц, требующих распознавания
        ocr_texts_by_page: dict[int, str] = {}
        inline_ocr_by_page: dict[int, list[tuple[int, str]]] = {}
        if page_images or page_inline_images:
            sem = asyncio.Semaphore(int(os.getenv("OCR_MAX_CONCURRENCY", "4")))

            async def _ocr_one_page(idx: int, img_bytes: bytes) -> tuple[int, str]:
                async with sem:
                    try:
                        ocr = await svc.client.recognize_text_from_image(
                            img_bytes, f"{filename}.p{idx + 1}.png"
                        )
                        return idx, (ocr.get("text") or "").strip()
                    except Exception as exc:
                        logger.debug(
                            "[ChatAttach] PDF page OCR failed p%s: %s", idx + 1, exc
                        )
                        return idx, ""

            async def _ocr_one_inline(
                idx: int, seq: int, img_bytes: bytes
            ) -> tuple[int, int, str]:
                async with sem:
                    try:
                        ocr = await svc.client.recognize_text_from_image(
                            img_bytes, f"{filename}.p{idx + 1}.img{seq + 1}.png"
                        )
                        return idx, seq, (ocr.get("text") or "").strip()
                    except Exception as exc:
                        logger.debug(
                            "[ChatAttach] PDF inline image OCR failed p%s img%s: %s",
                            idx + 1,
                            seq + 1,
                            exc,
                        )
                        return idx, seq, ""

            jobs = [_ocr_one_page(idx, img) for idx, img in page_images.items()] + [
                _ocr_one_inline(idx, seq, data)
                for idx, imgs in page_inline_images.items()
                for seq, data in imgs
            ]
            results = await asyncio.gather(*jobs)
            for r in results:
                if r[0] is None:
                    continue
                if len(r) == 3:
                    idx, seq, text = r
                    if text:
                        inline_ocr_by_page.setdefault(idx, []).append((seq, text))
                else:
                    idx, text = r
                    if text:
                        ocr_texts_by_page[idx] = text
            for idx in inline_ocr_by_page:
                inline_ocr_by_page[idx].sort(key=lambda x: x[0])

        pages_out: list[str] = []
        ocr_used = False
        for idx in range(n_pages):
            if idx in page_layers:
                page_text = page_layers[idx]
                inline = inline_ocr_by_page.get(idx) or []
                if inline:
                    ocr_used = True
                    page_text += (
                        "\n\n[Текст с изображений страницы (OCR)]:\n"
                        + "\n".join(t for _s, t in inline)
                    )
                pages_out.append(f"[Страница {idx + 1}]\n{page_text}")
            elif idx in ocr_texts_by_page:
                ocr_used = True
                pages_out.append(
                    f"[Страница {idx + 1} (OCR)]\n{ocr_texts_by_page[idx]}"
                )
                logger.debug(
                    "[ChatAttach] PDF page %s/%s: OCR %s симв.",
                    idx + 1,
                    n_pages,
                    len(ocr_texts_by_page[idx]),
                )
            elif page_indices is None:
                logger.debug(
                    "[ChatAttach] PDF page %s/%s: текст не найден (ни слой, ни OCR)",
                    idx + 1,
                    n_pages,
                )

        if not pages_out:
            return {
                "type": "text",
                "content": f"[PDF «{filename}»: не удалось извлечь текст (ни слоя, ни OCR)]",
                "cs1": "pdf-ocr-empty",
            }, "В PDF не обнаружен текст"

        text = "\n\n".join(pages_out)
        logger.info(
            "[ChatAttach] PDF OCR done %s ocr_used=%s out_chars=%s ocr_pages=%s inline_images=%s",
            filename,
            ocr_used,
            len(text),
            len(page_images),
            sum(len(v) for v in inline_ocr_by_page.values()),
        )
        out_payload = {
            "type": "text",
            "content": f"[Текст из PDF «{filename}»]:\n{text}",
            "cs1": "pdf-ocr" if ocr_used else "pdf",
        }
        if page_indices is not None:
            # RAG-вызов: отдаём и постраничную карту (включая страницы,
            # по которым текста не нашлось — там просто пусто).
            out_payload["pages_map"] = {
                str(idx): page_text.strip()
                for idx, page_text in (
                    (
                        (idx, page_layers[idx])
                        if idx in page_layers
                        else (idx, ocr_texts_by_page[idx])
                    )
                    for idx in target_pages
                    if idx in page_layers or idx in ocr_texts_by_page
                )
            }
        return out_payload, None
    except Exception as exc:
        logger.warning("[ChatAttach] PDF OCR failed for %s: %s", filename, exc)
        return payload, f"OCR PDF не удался: {exc}"


async def _ocr_docx_payload_if_needed(
    content: bytes,
    filename: str,
    payload: dict,
) -> tuple[dict, str | None]:
    """
    DOCX: распознаём встроенные изображения (сканы/скриншоты внутри документа) через OCR
    и добавляем их текст к уже извлечённым параграфам и таблицам.
    """
    if payload.get("cs1") not in ("docx", "docm"):
        return payload, None

    try:
        import io
        import docx as docx_lib
        from backend.agent_llm_svc import get_llm_service

        doc = docx_lib.Document(io.BytesIO(content))
        image_blobs: list[bytes] = []
        for rel in doc.part.rels.values():
            if "image" in (rel.reltype or ""):
                try:
                    image_blobs.append(rel.target_part.blob)
                except Exception:
                    continue
        if not image_blobs:
            return payload, None

        svc = await get_llm_service()
        if not svc.client.ocr_available:
            return payload, None

        ocr_texts: list[str] = []
        for i, blob in enumerate(image_blobs, 1):
            try:
                ocr = await svc.client.recognize_text_from_image(
                    blob, f"{filename}.img{i}.png"
                )
                t = (ocr.get("text") or "").strip()
                if t:
                    ocr_texts.append(t)
            except Exception as exc:
                logger.debug(
                    "[ChatAttach] DOCX image OCR skipped (%s): %s", filename, exc
                )

        if not ocr_texts:
            return payload, None

        base = payload.get("content") or ""
        if base.strip() == "(Документ не содержит текста)":
            base = ""
        joined = "\n".join(ocr_texts)
        new_content = (f"{base}\n\n" if base else "") + (
            f"[Текст из изображений документа «{filename}» (OCR)]:\n{joined}"
        )
        return {"type": "text", "content": new_content.strip(), "cs1": "docx-ocr"}, None
    except Exception as exc:
        logger.warning("[ChatAttach] DOCX OCR failed for %s: %s", filename, exc)
        return payload, None


async def _ocr_xlsx_payload_if_needed(
    content: bytes,
    filename: str,
    payload: dict,
) -> tuple[dict, str | None]:
    """
    XLSX: распознаём картинки, вставленные на листы, через OCR и добавляем их текст
    к уже извлечённым значениям ячеек.
    """
    if payload.get("cs1") != "xlsx":
        return payload, None

    try:
        import io
        import openpyxl
        from backend.agent_llm_svc import get_llm_service

        # read_only=True не грузит картинки — открываем в обычном режиме.
        wb = openpyxl.load_workbook(io.BytesIO(content))
        image_blobs: list[bytes] = []
        for ws in wb.worksheets:
            for img in getattr(ws, "_images", []) or []:
                try:
                    data = (
                        img._data() if callable(getattr(img, "_data", None)) else None
                    )
                    if data:
                        image_blobs.append(data)
                except Exception:
                    continue
        if not image_blobs:
            return payload, None

        svc = await get_llm_service()
        if not svc.client.available:
            return payload, None

        ocr_texts: list[str] = []
        for i, blob in enumerate(image_blobs, 1):
            try:
                ocr = await svc.client.recognize_text_from_image(
                    blob, f"{filename}.img{i}.png"
                )
                t = (ocr.get("text") or "").strip()
                if t:
                    ocr_texts.append(t)
            except Exception as exc:
                logger.debug(
                    "[ChatAttach] XLSX image OCR skipped (%s): %s", filename, exc
                )

        if not ocr_texts:
            return payload, None

        base = payload.get("content") or ""
        if base.strip() == "(Таблица не содержит данных)":
            base = ""
        joined = "\n".join(ocr_texts)
        new_content = (f"{base}\n\n" if base else "") + (
            f"[Текст из изображений таблицы «{filename}» (OCR)]:\n{joined}"
        )
        return {"type": "text", "content": new_content.strip(), "cs1": "xlsx-ocr"}, None
    except Exception as exc:
        logger.warning("[ChatAttach] XLSX OCR failed for %s: %s", filename, exc)
        return payload, None


def _try_upload_bytes_to_minio(
    request: Request, content: bytes, filename: str, content_type: str
) -> tuple[str | None, str | None]:
    """
    Пытается загрузить байты в MinIO. При недоступности MinIO возвращает (None, None)
    — прикрепление к сообщению всё равно работает (inline в модель).
    """
    if not minio_client:
        logger.warning(
            "MinIO недоступен — файл %s не сохранён в объектное хранилище", filename
        )
        return (None, None)
    documents_bucket = _documents_bucket_name()
    ext = os.path.splitext(filename)[1].lower()
    is_image = ext in _INLINE_IMAGE_EXTS or content_type.startswith("image/")
    file_object_name = minio_client.generate_object_name(
        prefix="img_" if is_image else "doc_", extension=ext or ".bin"
    )
    try:
        minio_client.upload_file(
            content,
            file_object_name,
            content_type=content_type,
            bucket_name=documents_bucket,
            cef_display_name=filename,
        )
        return (file_object_name, documents_bucket)
    except Exception:
        logger.exception("MinIO attach upload error")
        return (None, None)


def _persist_inline_attachment(
    request: Request,
    content: bytes,
    filename: str,
    content_type: str,
    *,
    username: str,
) -> tuple[str | None, str | None]:
    """Сначала MinIO (локальный docker / прод), иначе INLINE_ATTACH_DIR (K8s PVC)."""
    minio_object, minio_bucket = _try_upload_bytes_to_minio(
        request, content, filename, content_type
    )
    if minio_object:
        return minio_object, minio_bucket
    attach_object = _try_save_bytes_to_pod_attachments(
        content, filename, content_type, username=username
    )
    if attach_object:
        return attach_object, INLINE_ATTACH_POD_TMP_BUCKET
    return None, None


_GLOBAL_RAG_GONE = "Global documents store отключён. Используйте Базу Знаний, библиотеку памяти или RAG проекта."


@router.post("/upload")
async def upload_document(request: Request, file: Annotated[UploadFile, File(...)]):
    """Global store удалён из продукта — загрузка только в KB / memory / project."""
    raise HTTPException(status_code=410, detail=_GLOBAL_RAG_GONE)


@router.post("/query")
async def query_document(request: DocumentQueryRequest):
    """Global store удалён из продукта."""
    raise HTTPException(status_code=410, detail=_GLOBAL_RAG_GONE)


@router.get("")
@router.get("/")
async def get_documents():
    """Global store удалён — список всегда пустой."""
    return {
        "documents": [],
        "count": 0,
        "success": True,
        "deprecated": True,
        "detail": _GLOBAL_RAG_GONE,
    }


@router.delete("/{filename}")
async def delete_document(filename: str, request: Request):
    """Global store удалён из продукта."""
    raise HTTPException(status_code=410, detail=_GLOBAL_RAG_GONE)


@router.get("/report/generate")
async def generate_confidence_report():
    """Global store удалён из продукта - отчет недоступен"""
    raise HTTPException(status_code=410, detail=_GLOBAL_RAG_GONE)


@router.get("/report/download")
async def download_confidence_report():
    """Global store удалён из продукта - отчет недоступен"""
    raise HTTPException(status_code=410, detail=_GLOBAL_RAG_GONE)


@router.post("/extract")
async def extract_document_inline(
    request: Request, file: Annotated[UploadFile, File(...)]
):
    """Устаревший путь (in-memory extract). Используйте POST /attach."""
    raise HTTPException(
        status_code=410,
        detail="Рут /extract отключён. Используйте POST /api/documents/attach.",
    )


@router.post("/attach")
async def attach_document_for_message(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """
    Прикрепление к сообщению: сохранение в MinIO (или INLINE_ATTACH_DIR как fallback)
    + извлечение содержимого для модели. Без RAG/эмбеддингов.
    """
    username = (
        current_user.get("username") or current_user.get("user_id") or "anonymous"
    )
    _log_chat_attach_request(
        request,
        upload_filename=file.filename,
        upload_content_type=file.content_type,
        upload_user=username,
    )
    filename = file.filename or "unknown"
    try:
        content = await file.read()
    except Exception as exc:
        logger.exception("[ChatAttach] read-upload-failed filename=%s", file.filename)
        _log_inline_attach_upload_failure(
            request,
            filename,
            f"Не удалось прочитать файл: {exc}",
            reject_code="read_error",
        )
        raise HTTPException(
            status_code=400, detail=f"Не удалось прочитать файл: {exc}"
        ) from exc
    if not content:
        _log_inline_attachment_debug("rejected-empty", {"filename": filename})
        _log_inline_attach_upload_failure(
            request, filename, "Файл пустой", file_size=0, reject_code="empty"
        )
        raise HTTPException(status_code=400, detail="Файл пустой")
    content_type = _resolve_content_type(filename, file.content_type or "")
    file_size = len(content)
    _log_inline_attachment_debug(
        "read-complete",
        {
            "filename": filename,
            "content_type": content_type,
            "size": file_size,
            "size_mb": round(file_size / (1024 * 1024), 3),
            "header_content_length": request.headers.get("content-length"),
        },
    )
    analysis = await _analyze_inline_attachment_for_model(
        filename, content_type, file_size
    )
    _log_inline_attachment_debug("validate", analysis)
    if not analysis["allowed"]:
        _log_inline_attachment_debug("rejected", analysis)
        if not analysis["format_allowed"]:
            failure_detail = _unsupported_inline_attach_extension_detail(filename)
            reject_code = "unsupported_format"
        else:
            max_mb = INLINE_ATTACHMENT_MAX_BYTES // (1024 * 1024)
            failure_detail = f"размер файла превышает допустимый лимит {max_mb} МБ, поддерживается максимальный размер до {max_mb} МБ"
            reject_code = "size_exceeded"
        _log_inline_attach_upload_failure(
            request,
            filename,
            failure_detail,
            file_size=file_size,
            reject_code=reject_code,
        )
        # _log_inline_attach_pod_structure(filename=filename, stage="attachments-storage-structure-rejected")
        raise HTTPException(
            status_code=400,
            detail=analysis.get("reject_reason") or "Файл не может быть прикреплён",
        )
    if not analysis["model_compatible"]:
        _log_inline_attachment_debug("model-incompatible", analysis)
    _log_inline_attach_upload_success(filename)
    minio_object, minio_bucket = _persist_inline_attachment(
        request, content, filename, content_type, username=username
    )
    _log_inline_attachment_debug(
        "attachments-save",
        analysis,
        attachments_saved=bool(minio_object),
        minio_object=minio_object,
        minio_bucket=minio_bucket,
        attachments_dir=_inline_attach_pod_dir(required=False),
        upload_user=username,
        upload_date_dir=_inline_attach_date_dir(),
    )
    try:
        payload = _extract_inline_payload(content, filename, content_type)
        ocr_warning = None
        if payload.get("type") == "image":
            payload, ocr_warning = await _ocr_image_payload_if_needed(
                content, filename, payload, analysis.get("model_path")
            )
        elif payload.get("cs1") == "pdf":
            payload, ocr_warning = await _ocr_pdf_payload_if_needed(
                content, filename, payload
            )
        elif payload.get("cs1") in ("docx", "docm"):
            payload, ocr_warning = await _ocr_docx_payload_if_needed(
                content, filename, payload
            )
        elif payload.get("cs1") == "xlsx":
            payload, ocr_warning = await _ocr_xlsx_payload_if_needed(
                content, filename, payload
            )
    except Exception as e:
        logger.exception(
            "[ChatAttach] extract-error filename=%s size=%s", filename, file_size
        )
        _log_inline_attachment_debug(
            "extract-error", analysis, error=str(e), error_type=type(e).__name__
        )
        _log_inline_attach_upload_failure(
            request,
            filename,
            f"Не удалось извлечь содержимое: {e}",
            file_size=file_size,
            status_code=500,
            reject_code="extract_error",
        )
        if minio_object and minio_bucket == INLINE_ATTACH_POD_TMP_BUCKET:
            _delete_pod_attach(minio_object)
        elif minio_object and minio_bucket and minio_client:
            with logged_suppress(logger):
                minio_client.delete_file(minio_object, bucket_name=minio_bucket)
        raise HTTPException(
            status_code=500,
            detail=public_error_detail(e, "Не удалось извлечь содержимое файла"),
        ) from e
    inline_content = payload.get("content") or ""
    inline_content_chars = len(inline_content)
    _log_inline_attachment_debug(
        "extract-complete",
        analysis,
        extracted_type=payload["type"],
        extracted_cs1=payload.get("cs1"),
        inline_content_chars=inline_content_chars,
        attachments_saved=bool(minio_object),
    )
    _cef_extra: dict = {"fname": filename, "fsize": file_size, "cs1": payload["cs1"]}
    if minio_object:
        _cef_extra["cs2"] = minio_object
        _cef_extra["cs2Label"] = (
            "PodAttachmentsObject"
            if minio_bucket == INLINE_ATTACH_POD_TMP_BUCKET
            else "MinioObject"
        )
    log_cef_event("FS007", request=request, status_code=200, extra=_cef_extra)
    _log_inline_attachment_debug(
        "success",
        analysis,
        extracted_type=payload["type"],
        attachments_saved=bool(minio_object),
        response_content_chars=inline_content_chars,
    )
    result = {
        "success": True,
        "type": payload["type"],
        "content": payload["content"],
        "filename": filename,
        "attachments_saved": bool(minio_object),
        # "minio_saved": bool(minio_object),
    }
    if minio_object:
        result["minio_object"] = minio_object
        result["minio_bucket"] = minio_bucket
        if minio_bucket == INLINE_ATTACH_POD_TMP_BUCKET:
            result["attachments_path"] = _inline_attach_pod_file_path(minio_object) or ""
    if not minio_object:
        result["warning"] = (
            "Не удалось сохранить файл в MinIO и на диск "
            f"({INLINE_ATTACH_DIR_ENV}); прикрепление работает только inline"
        )
    if ocr_warning:
        result["warning"] = (
            f"{result['warning']} {ocr_warning}".strip()
            if result.get("warning")
            else ocr_warning
        )
    return result


@router.get("/inline-file")
async def get_inline_attachment_file(
    bucket: Annotated[str, Query(..., min_length=1)],
    object_name: Annotated[str, Query(..., min_length=1, alias="object")],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Отдаёт файл вложения для превью в UI: из INLINE_ATTACH_DIR пода (inline «+») или из MinIO (legacy).

    Безопасность:
    - pod-вложения (``__pod_tmp__``): ключ обязан начинаться с каталога текущего
      пользователя (``{user}/yyyy/mm/dd/file``); админ читает любые;
    - MinIO: bucket берётся только из белого списка настроенных бакетов.
    """
    if bucket != INLINE_ATTACH_POD_TMP_BUCKET and not minio_client:
        raise HTTPException(status_code=503, detail="MinIO недоступен")
    if bucket == INLINE_ATTACH_POD_TMP_BUCKET:
        if not _inline_attach_key_belongs_to_user(object_name, current_user):
            raise HTTPException(status_code=403, detail="Доступ к файлу запрещён")
    elif bucket not in _allowed_inline_minio_buckets():
        raise HTTPException(status_code=403, detail="Доступ к хранилищу запрещён")
    loaded = read_inline_attachment_bytes(bucket, object_name)
    if not loaded:
        raise HTTPException(status_code=404, detail="Файл не найден")
    data, mime = loaded
    return Response(content=data, media_type=mime)
