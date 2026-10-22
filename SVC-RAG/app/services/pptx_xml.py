"""Текст .pptx без python-pptx: файл - zip с XML, читаем zipfile и xml.etree.

Тот же файл лежит в бэкенде (backend/utils/pptx_xml.py) и в SVC-RAG
(app/services/pptx_xml.py) - держать одинаковыми.

collect_pptx() отдаёт то же, что сборщик на python-pptx в SVC-RAG:
по слайдам элементы ("text", str) и ("img", имя) в порядке фигур, картинки
для OCR и заметки докладчика. pptx_text() собирает из этого текст.
"""

import posixpath
import zipfile
from io import BytesIO
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"

# Векторные картинки (emf, wmf, svg) OCR не прочитает.
_RASTER_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp")

# Лимит распакованного размера одной части zip (защита от zip-бомбы, BUG-RAG-17).
_MAX_MEMBER_BYTES = 16 * 1024 * 1024

SlideItems = List[Tuple[str, str]]


def _read_member(z: zipfile.ZipFile, name: str) -> bytes:
    """Читает часть архива; при превышении лимита размера поднимает ValueError."""
    info = z.getinfo(name)
    if info.file_size > _MAX_MEMBER_BYTES:
        raise ValueError(
            f"pptx member too large: {name} ({info.file_size} > {_MAX_MEMBER_BYTES})"
        )
    return z.read(name)


def _rels(z: zipfile.ZipFile, part: str) -> Dict[str, Tuple[str, str]]:
    """rId -> (тип связи, путь цели в архиве) для части part."""
    folder, name = posixpath.split(part)
    rels_path = posixpath.join(folder, "_rels", name + ".rels")
    try:
        root = ET.fromstring(_read_member(z, rels_path))
    except KeyError:
        return {}
    out: Dict[str, Tuple[str, str]] = {}
    for rel in root.iter(_REL):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target") or ""
        if target.startswith("/"):
            target = target.lstrip("/")
        else:
            target = posixpath.normpath(posixpath.join(folder, target))
        out[rel.get("Id") or ""] = ((rel.get("Type") or "").rsplit("/", 1)[-1], target)
    return out


def _text_of(node: ET.Element) -> str:
    """Абзацы текста фигуры через перевод строки, как text_frame.text."""
    paras = []
    for p in node.iter(_A + "p"):
        parts = []
        for el in p.iter():
            if el.tag == _A + "t" and el.text:
                parts.append(el.text)
            elif el.tag == _A + "br":
                parts.append("\n")
        paras.append("".join(parts))
    return "\n".join(paras).strip()


def _table_markdown(tbl: ET.Element) -> str:
    """Таблица -> markdown-pipe строки (как _pptx_table_to_markdown в SVC-RAG)."""
    rows = tbl.findall(_A + "tr")
    lines = []
    for r_idx, tr in enumerate(rows):
        cells = [
            " ".join(_text_of(tc).split())
            for tc in tr.findall(_A + "tc")
            if tc.get("hMerge") != "1"  # продолжение объединённой ячейки
        ]
        if not any(cells):
            continue
        lines.append("| " + " | ".join(cells) + " |")
        if r_idx == 0 and len(rows) > 1:
            lines.append("|" + " --- |" * len(cells))
    return "\n".join(lines)


def collect_pptx(
    data: bytes, max_images: int
) -> Tuple[List[SlideItems], List[Tuple[str, bytes]], List[str]]:
    """(элементы по слайдам, картинки (имя, байты) для OCR, заметки по слайдам)."""
    per_slide: List[SlideItems] = []
    images: List[Tuple[str, bytes]] = []
    notes: List[str] = []
    with zipfile.ZipFile(BytesIO(data)) as z:
        pres_part = "ppt/presentation.xml"
        pres_rels = _rels(z, pres_part)
        pres = ET.fromstring(_read_member(z, pres_part))
        sld_ids = pres.find(_P + "sldIdLst")
        slide_parts = [
            pres_rels[sid.get(_R + "id") or ""][1]
            for sid in (sld_ids if sld_ids is not None else [])
            if (sid.get(_R + "id") or "") in pres_rels
        ]
        for si, part in enumerate(slide_parts, 1):
            rels = _rels(z, part)
            items: SlideItems = []

            def walk(tree: ET.Element) -> None:
                for shape in tree:
                    tag = shape.tag
                    if tag == _P + "grpSp":
                        walk(shape)
                    elif tag == _P + "sp":
                        body = shape.find(_P + "txBody")
                        text = _text_of(body) if body is not None else ""
                        if text:
                            items.append(("text", text))
                    elif tag == _P + "graphicFrame":
                        tbl = shape.find(".//" + _A + "tbl")
                        md = _table_markdown(tbl) if tbl is not None else ""
                        if md:
                            items.append(("text", md))
                    elif tag == _P + "pic":
                        blip = shape.find(".//" + _A + "blip")
                        rid = blip.get(_R + "embed") if blip is not None else None
                        target = rels.get(rid or "", ("", ""))[1]
                        ext = posixpath.splitext(target)[1].lower()
                        if ext in _RASTER_EXTS and len(images) < max_images:
                            name = f"slide{si}_img{len(images) + 1}{ext}"
                            try:
                                blob = _read_member(z, target)
                            except KeyError:
                                continue
                            except ValueError:
                                # Слишком большая картинка — пропускаем, не грузим в память.
                                continue
                            images.append((name, blob))
                            items.append(("img", name))

            tree = ET.fromstring(_read_member(z, part)).find(f"{_P}cSld/{_P}spTree")
            if tree is not None:
                walk(tree)
            per_slide.append(items)
            notes.append(_notes_text(z, rels))
    return per_slide, images, notes


def _notes_text(z: zipfile.ZipFile, slide_rels: Dict[str, Tuple[str, str]]) -> str:
    """Текст заметок: только плейсхолдер body (без номера слайда и миниатюры)."""
    part = next((t for kind, t in slide_rels.values() if kind == "notesSlide"), None)
    if not part:
        return ""
    try:
        root = ET.fromstring(_read_member(z, part))
    except (KeyError, ValueError):
        return ""
    out = []
    for sp in root.iter(_P + "sp"):
        ph = sp.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
        body = sp.find(_P + "txBody")
        if ph is not None and ph.get("type") == "body" and body is not None:
            out.append(_text_of(body))
    return "\n".join(t for t in out if t)


def pptx_text(
    per_slide: List[SlideItems],
    notes: List[str],
    ocr_by_name: Optional[Dict[str, str]] = None,
) -> str:
    """Текст по слайдам; распознанный текст картинки - на её месте в слайде."""
    ocr_by_name = ocr_by_name or {}
    parts = []
    for si, items in enumerate(per_slide, 1):
        slide_parts = [f"[Слайд {si}]"]
        for kind, value in items:
            if kind == "text":
                slide_parts.append(value)
            elif (ocr_by_name.get(value) or "").strip():
                slide_parts.append(
                    f"[Текст с изображения (OCR)]: {ocr_by_name[value].strip()}"
                )
        nt = notes[si - 1].strip() if si - 1 < len(notes) else ""
        if nt:
            slide_parts.append(f"[Заметки докладчика]: {nt}")
        parts.append("\n\n".join(slide_parts))
    return "\n\n".join(parts)
