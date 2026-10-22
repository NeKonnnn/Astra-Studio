"""Сборка минимальных .pptx вручную (zip + XML): python-pptx в окружении нет, да и нужны «кривые» варианты."""

from __future__ import annotations

import zipfile
from io import BytesIO
from typing import Dict, List, Optional
from xml.sax.saxutils import escape

NS = (
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
T_SLIDE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
T_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
T_NOTES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"

# 1x1 PNG
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0f01f0005000201a58f4cb40000000049454e44ae426082"
)


def para(text: str) -> str:
    return f"<a:p><a:r><a:t>{escape(text)}</a:t></a:r></a:p>"


def paras(*texts: str) -> str:
    return "".join(para(t) for t in texts)


def sp(*texts: str) -> str:
    """Фигура с текстом."""
    return f"<p:sp><p:txBody>{paras(*texts)}</p:txBody></p:sp>"


def sp_no_body() -> str:
    return "<p:sp><p:nvSpPr/></p:sp>"


def sp_raw_paragraph(inner: str) -> str:
    return f"<p:sp><p:txBody><a:p>{inner}</a:p></p:txBody></p:sp>"


def group(*children: str) -> str:
    return f"<p:grpSp>{''.join(children)}</p:grpSp>"


def pic(rid: Optional[str]) -> str:
    blip = f'<a:blip r:embed="{rid}"/>' if rid else ""
    return f"<p:pic><p:blipFill>{blip}</p:blipFill></p:pic>"


def table(rows: List[List[str]], merged_continuation: Optional[set] = None) -> str:
    """Таблица в graphicFrame. merged_continuation - {(row, col)} ячеек с hMerge=1."""
    merged_continuation = merged_continuation or set()
    out = []
    for r, row in enumerate(rows):
        cells = []
        for c, text in enumerate(row):
            attr = ' hMerge="1"' if (r, c) in merged_continuation else ""
            body = para(text) if text else "<a:p/>"
            cells.append(f"<a:tc{attr}><a:txBody>{body}</a:txBody></a:tc>")
        out.append(f"<a:tr>{''.join(cells)}</a:tr>")
    return (
        "<p:graphicFrame><a:graphic><a:graphicData>"
        f"<a:tbl>{''.join(out)}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )


def graphic_frame_without_table() -> str:
    return "<p:graphicFrame><a:graphic><a:graphicData/></a:graphic></p:graphicFrame>"


def slide_xml(*shapes: str) -> str:
    return (
        f'<p:sld {NS}><p:cSld><p:spTree>{"".join(shapes)}</p:spTree></p:cSld></p:sld>'
    )


def notes_xml(body_text: str = "", with_slide_number: bool = True) -> str:
    shapes = []
    if with_slide_number:
        shapes.append(
            '<p:sp><p:nvSpPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvSpPr>'
            f"<p:txBody>{para('7')}</p:txBody></p:sp>"
        )
    if body_text:
        shapes.append(
            '<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
            f"<p:txBody>{para(body_text)}</p:txBody></p:sp>"
        )
    shapes.append("<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody>" + para("без плейсхолдера") + "</p:txBody></p:sp>")
    return f'<p:notes {NS}><p:cSld><p:spTree>{"".join(shapes)}</p:spTree></p:cSld></p:notes>'


def rels_xml(items: List[tuple]) -> str:
    """items: (id, type, target[, mode])."""
    rels = []
    for item in items:
        rid, typ, target = item[:3]
        mode = f' TargetMode="{item[3]}"' if len(item) > 3 else ""
        rels.append(f'<Relationship Id="{rid}" Type="{typ}" Target="{target}"{mode}/>')
    return f'<Relationships xmlns="{REL_NS}">{"".join(rels)}</Relationships>'


def build_pptx(
    slides: List[str],
    slide_rels: Optional[Dict[int, List[tuple]]] = None,
    extra_files: Optional[Dict[str, bytes]] = None,
    pres_rels_extra: Optional[List[tuple]] = None,
    sld_id_extra: str = "",
    include_sld_id_list: bool = True,
) -> bytes:
    """slides - XML каждого слайда (1-based индексы для slide_rels)."""
    slide_rels = slide_rels or {}
    ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{i + 1}"/>' for i in range(len(slides)))
    lst = f"<p:sldIdLst>{ids}{sld_id_extra}</p:sldIdLst>" if include_sld_id_list else ""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ppt/presentation.xml", f"<p:presentation {NS}>{lst}</p:presentation>")
        pres_rels = [(f"rId{i + 1}", T_SLIDE, f"slides/slide{i + 1}.xml") for i in range(len(slides))]
        z.writestr("ppt/_rels/presentation.xml.rels", rels_xml(pres_rels + (pres_rels_extra or [])))
        for i, xml in enumerate(slides, 1):
            z.writestr(f"ppt/slides/slide{i}.xml", xml)
            if i in slide_rels:
                z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", rels_xml(slide_rels[i]))
        for name, content in (extra_files or {}).items():
            z.writestr(name, content)
    return buf.getvalue()
