"""Разбор .pptx через XML (без python-pptx)."""

from __future__ import annotations

import posixpath
import re
import zipfile
from io import BytesIO
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_RASTER_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_WS_RE = re.compile(r"\s+")


def _text_of(node: ET.Element) -> str:
    """Собрать текст: a:t, переносы на a:br и границах a:p."""
    paragraphs: List[str] = []
    for p in node.findall(f".//{A}p"):
        parts: List[str] = []
        buf: List[str] = []
        for child in list(p):
            if child.tag == f"{A}br":
                parts.append("".join(buf))
                buf = []
            else:
                for t in child.iter(f"{A}t"):
                    buf.append(t.text or "")
        parts.append("".join(buf))
        paragraphs.append("\n".join(parts))
    return "\n".join(paragraphs)


def _table_markdown(tbl: ET.Element) -> str:
    """Markdown-таблица; hMerge-ячейки пропускаются, пустые строки опускаются."""
    rows_out: List[List[str]] = []
    for tr in tbl.findall(f"{A}tr"):
        cells: List[str] = []
        for tc in tr.findall(f"{A}tc"):
            if tc.get("hMerge") is not None:
                continue
            raw = _text_of(tc)
            cells.append(_WS_RE.sub(" ", raw).strip())
        if any(cells):
            rows_out.append(cells)

    if not rows_out:
        return ""

    def fmt(row: List[str]) -> str:
        return "| " + " | ".join(row) + " |"

    lines = [fmt(rows_out[0])]
    if len(rows_out) > 1:
        lines.append("| " + " | ".join("---" for _ in rows_out[0]) + " |")
        for row in rows_out[1:]:
            lines.append(fmt(row))
    return "\n".join(lines)


def _rels_path(part_path: str) -> str:
    d = posixpath.dirname(part_path)
    b = posixpath.basename(part_path)
    return f"{d}/_rels/{b}.rels"


def _resolve_target(part_path: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    base = posixpath.dirname(part_path)
    return posixpath.normpath(posixpath.join(base, target)).replace("\\", "/")


def _rels(zf: zipfile.ZipFile, part_path: str) -> Dict[str, Tuple[str, str]]:
    """Разбор relationships для части: {rId: (type_suffix, resolved_path)}."""
    path = _rels_path(part_path)
    try:
        data = zf.read(path)
    except KeyError:
        return {}
    root = ET.fromstring(data)
    out: Dict[str, Tuple[str, str]] = {}
    for rel in root.findall(f"{REL}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        rid = rel.get("Id")
        typ = rel.get("Type") or ""
        target = rel.get("Target") or ""
        if not rid or not target:
            continue
        suffix = typ.rsplit("/", 1)[-1]
        out[rid] = (suffix, _resolve_target(part_path, target))
    return out


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _notes_text(zf: zipfile.ZipFile, slide_path: str, slide_rels: Dict[str, Tuple[str, str]]) -> str:
    notes_path = None
    for _suffix, path in slide_rels.values():
        if _suffix == "notesSlide":
            notes_path = path
            break
    if not notes_path:
        return ""
    try:
        data = zf.read(notes_path)
    except KeyError:
        return ""
    root = ET.fromstring(data)
    chunks: List[str] = []
    for sp in root.findall(f".//{P}sp"):
        ph = sp.find(f".//{P}ph")
        if ph is None or ph.get("type") != "body":
            continue
        tx = sp.find(f"{P}txBody")
        if tx is None:
            continue
        text = _text_of(tx).strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks)


def _walk_shapes(
    parent: ET.Element,
    slide_items: List[Tuple[str, str]],
    images: List[Tuple[str, bytes]],
    slide_rels: Dict[str, Tuple[str, str]],
    zf: zipfile.ZipFile,
    slide_idx: int,
    max_images: int,
    names: set,
) -> None:
    for child in list(parent):
        tag = _local(child.tag)
        if tag == "sp":
            tx = child.find(f"{P}txBody")
            if tx is not None:
                text = _text_of(tx)
                if text.strip():
                    slide_items.append(("text", text))
        elif tag == "grpSp":
            _walk_shapes(
                child, slide_items, images, slide_rels, zf, slide_idx, max_images, names
            )
        elif tag == "graphicFrame":
            tbl = child.find(f".//{A}tbl")
            if tbl is not None:
                md = _table_markdown(tbl)
                if md.strip():
                    slide_items.append(("text", md))
        elif tag == "pic":
            if len(images) >= max_images:
                continue
            blip = child.find(f".//{A}blip")
            if blip is None:
                continue
            embed = blip.get(f"{R}embed")
            if not embed or embed not in slide_rels:
                continue
            _type, media_path = slide_rels[embed]
            ext = posixpath.splitext(media_path)[1].lower()
            if ext not in _RASTER_EXTS:
                continue
            if media_path not in names:
                continue
            try:
                blob = zf.read(media_path)
            except KeyError:
                continue
            n_for_slide = 1 + sum(
                1 for n, _ in images if n.startswith(f"slide{slide_idx}_img")
            )
            fname = f"slide{slide_idx}_img{n_for_slide}{ext}"
            images.append((fname, blob))
            slide_items.append(("img", fname))


def collect_pptx(
    data: bytes, max_images: int
) -> Tuple[List[List[Tuple[str, str]]], List[Tuple[str, bytes]], List[str]]:
    """Извлечь слайды, растровые изображения и заметки докладчика."""
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = set(zf.namelist())
        try:
            pres_xml = zf.read("ppt/presentation.xml")
        except KeyError:
            return [], [], []

        pres = ET.fromstring(pres_xml)
        sld_id_lst = pres.find(f"{P}sldIdLst")
        if sld_id_lst is None:
            return [], [], []

        pres_rels = _rels(zf, "ppt/presentation.xml")
        slides_out: List[List[Tuple[str, str]]] = []
        images: List[Tuple[str, bytes]] = []
        notes_out: List[str] = []

        slide_num = 0
        for sld_id in sld_id_lst.findall(f"{P}sldId"):
            rid = sld_id.get(f"{R}id")
            if not rid or rid not in pres_rels:
                continue
            _t, slide_path = pres_rels[rid]
            slide_num += 1
            slide_items: List[Tuple[str, str]] = []
            slide_rels = _rels(zf, slide_path)

            try:
                slide_xml = zf.read(slide_path)
            except KeyError:
                slides_out.append(slide_items)
                notes_out.append(_notes_text(zf, slide_path, slide_rels))
                continue

            root = ET.fromstring(slide_xml)
            tree = root.find(f".//{P}spTree")
            if tree is not None:
                _walk_shapes(
                    tree,
                    slide_items,
                    images,
                    slide_rels,
                    zf,
                    slide_num,
                    max_images,
                    names,
                )

            slides_out.append(slide_items)
            notes_out.append(_notes_text(zf, slide_path, slide_rels))

        return slides_out, images, notes_out


def pptx_text(
    per_slide: List[List[Tuple[str, str]]],
    notes: List[str],
    ocr_map: Optional[Dict[str, str]] = None,
) -> str:
    """Собрать плоский текст со слайдов, OCR и заметок докладчика."""
    ocr_map = ocr_map or {}
    blocks: List[str] = []
    for i, items in enumerate(per_slide):
        parts: List[str] = [f"[Слайд {i + 1}]"]
        for kind, payload in items:
            if kind == "text":
                parts.append(payload)
            elif kind == "img":
                ocr = (ocr_map.get(payload) or "").strip()
                if ocr:
                    parts.append(f"[Текст с изображения (OCR)]: {ocr}")
        if i < len(notes) and notes[i]:
            parts.append(f"[Заметки докладчика]: {notes[i]}")
        blocks.append("\n\n".join(parts))
    return "\n\n".join(blocks)
