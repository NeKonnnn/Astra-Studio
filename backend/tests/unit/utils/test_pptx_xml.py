"""pptx_xml: разбор .pptx без python-pptx на собранном в тесте архиве."""

import zipfile
from io import BytesIO

import pytest

from backend.utils import pptx_xml as px

NS = (
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
T = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def _rels(*items):
    body = "".join(
        f'<Relationship Id="{i}" Type="{T}{t}" Target="{tg}"{" TargetMode=%s" % chr(34) + "External" + chr(34) if ext else ""}/>'
        for i, t, tg, ext in items
    )
    return f'<Relationships xmlns="{REL_NS}">{body}</Relationships>'


def _sp(text):
    paras = "".join(f"<a:p><a:r><a:t>{p}</a:t></a:r></a:p>" for p in text.split("|"))
    return f"<p:sp><p:txBody>{paras}</p:txBody></p:sp>"


def _slide(shapes):
    return f'<p:sld {NS}><p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>'


def _pptx(slides, rels_per_slide=None, extra=None, sld_ids=None):
    """slides: список xml-строк; rels_per_slide: {индекс: xml}."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        ids = "".join(f'<p:sldId id="{256+i}" r:id="rId{i+1}"/>' for i in range(len(slides)))
        z.writestr("ppt/presentation.xml", f'<p:presentation {NS}><p:sldIdLst>{sld_ids if sld_ids is not None else ids}</p:sldIdLst></p:presentation>')
        z.writestr(
            "ppt/_rels/presentation.xml.rels",
            _rels(*[(f"rId{i+1}", "slide", f"slides/slide{i+1}.xml", False) for i in range(len(slides))]),
        )
        for i, xml in enumerate(slides):
            z.writestr(f"ppt/slides/slide{i+1}.xml", xml)
        for i, xml in (rels_per_slide or {}).items():
            z.writestr(f"ppt/slides/_rels/slide{i+1}.xml.rels", xml)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


class TestTextHelpers:
    def test_text_of_joins_paragraphs_and_breaks(self):
        from xml.etree import ElementTree as ET

        node = ET.fromstring(
            f"<r {NS}><a:p><a:r><a:t>один</a:t></a:r><a:br/><a:r><a:t>два</a:t></a:r></a:p>"
            "<a:p><a:r><a:t>три</a:t></a:r></a:p></r>"
        )
        assert px._text_of(node) == "один\nдва\nтри"

    def test_table_markdown_header_separator_and_merged_cells(self):
        from xml.etree import ElementTree as ET

        def tc(t, extra=""):
            return f"<a:tc{extra}><a:txBody><a:p><a:r><a:t>{t}</a:t></a:r></a:p></a:txBody></a:tc>"

        tbl = ET.fromstring(
            f"<a:tbl {NS}>"
            f"<a:tr>{tc('Имя')}{tc('Сумма')}</a:tr>"
            f"<a:tr>{tc('А  б')}{tc('1', ' hMerge=\"1\"')}</a:tr>"
            "<a:tr><a:tc><a:txBody><a:p/></a:txBody></a:tc></a:tr>"
            "</a:tbl>"
        )
        assert px._table_markdown(tbl).split("\n") == [
            "| Имя | Сумма |",
            "| --- | --- |",
            "| А б |",
        ]

    def test_single_row_table_has_no_separator(self):
        from xml.etree import ElementTree as ET

        tbl = ET.fromstring(
            f"<a:tbl {NS}><a:tr><a:tc><a:txBody><a:p><a:r><a:t>x</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl>"
        )
        assert px._table_markdown(tbl) == "| x |"


class TestRels:
    def test_missing_rels_file(self):
        with zipfile.ZipFile(BytesIO(_pptx([_slide("")]))) as z:
            assert px._rels(z, "ppt/slides/slide1.xml") == {}

    def test_relative_absolute_and_external(self):
        xml = _rels(
            ("rId1", "image", "../media/a.png", False),
            ("rId2", "image", "/ppt/media/b.png", False),
            ("rId3", "hyperlink", "http://x", True),
        )
        data = _pptx([_slide("")], {0: xml})
        with zipfile.ZipFile(BytesIO(data)) as z:
            rels = px._rels(z, "ppt/slides/slide1.xml")
        assert rels == {"rId1": ("image", "ppt/media/a.png"), "rId2": ("image", "ppt/media/b.png")}


class TestCollect:
    def test_text_table_and_group(self):
        tbl = (
            "<p:graphicFrame><a:graphic><a:graphicData><a:tbl>"
            "<a:tr><a:tc><a:txBody><a:p><a:r><a:t>H</a:t></a:r></a:p></a:txBody></a:tc></a:tr>"
            "</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
        )
        group = f"<p:grpSp>{_sp('в группе')}</p:grpSp>"
        empty = "<p:sp><p:txBody><a:p/></p:txBody></p:sp><p:sp/><p:graphicFrame/>"
        data = _pptx([_slide(_sp("Заголовок|строка 2") + group + tbl + empty)])
        slides, images, notes = px.collect_pptx(data, max_images=5)
        assert slides == [[("text", "Заголовок\nстрока 2"), ("text", "в группе"), ("text", "| H |")]]
        assert images == [] and notes == [""]

    def test_pictures_only_raster_within_limit_and_existing(self):
        def pic(rid):
            return f'<p:pic><p:blipFill><a:blip r:embed="{rid}"/></p:blipFill></p:pic>'

        shapes = pic("rId1") + pic("rId2") + pic("rId3") + pic("rId4") + pic("rIdX") + "<p:pic/>"
        rels = _rels(
            ("rId1", "image", "../media/a.PNG", False),
            ("rId2", "image", "../media/v.emf", False),
            ("rId3", "image", "../media/gone.jpg", False),
            ("rId4", "image", "../media/b.jpg", False),
        )
        data = _pptx(
            [_slide(shapes)], {0: rels},
            extra={"ppt/media/a.PNG": b"A", "ppt/media/v.emf": b"V", "ppt/media/b.jpg": b"B"},
        )
        slides, images, _ = px.collect_pptx(data, max_images=1)
        assert images == [("slide1_img1.png", b"A")]
        assert slides == [[("img", "slide1_img1.png")]]
        slides, images, _ = px.collect_pptx(data, max_images=10)
        assert [n for n, _ in images] == ["slide1_img1.png", "slide1_img2.jpg"]  # gone.jpg и emf пропущены

    def test_slide_without_tree_and_unknown_slide_ids(self):
        data = _pptx(
            [f"<p:sld {NS}/>", _slide(_sp("второй"))],
            sld_ids='<p:sldId id="1" r:id="rId1"/><p:sldId id="2" r:id="rId99"/><p:sldId id="3" r:id="rId2"/>',
        )
        slides, _, notes = px.collect_pptx(data, 0)
        assert slides == [[], [("text", "второй")]] and len(notes) == 2

    def test_no_slide_list(self):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("ppt/presentation.xml", f"<p:presentation {NS}/>")
        assert px.collect_pptx(buf.getvalue(), 3) == ([], [], [])

    def test_not_a_zip(self):
        with pytest.raises(zipfile.BadZipFile):
            px.collect_pptx(b"not a zip", 1)


class TestNotes:
    def _notes_xml(self):
        def sp(ph_type, text):
            return (
                f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="n"/><p:nvPr><p:ph type="{ph_type}"/></p:nvPr></p:nvSpPr>'
                f"<p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
            )

        return f"<p:notes {NS}><p:cSld><p:spTree>{sp('sldImg', 'миниатюра')}{sp('body', 'речь')}{sp('sldNum', '7')}</p:spTree></p:cSld></p:notes>"

    def test_only_body_placeholder_is_taken(self):
        slide_rels = _rels(("rId1", "notesSlide", "../notesSlides/notesSlide1.xml", False))
        data = _pptx([_slide(_sp("t"))], {0: slide_rels}, extra={"ppt/notesSlides/notesSlide1.xml": self._notes_xml()})
        _, _, notes = px.collect_pptx(data, 0)
        assert notes == ["речь"]

    def test_missing_notes_part_gives_empty(self):
        slide_rels = _rels(("rId1", "notesSlide", "../notesSlides/none.xml", False))
        data = _pptx([_slide(_sp("t"))], {0: slide_rels})
        assert px.collect_pptx(data, 0)[2] == [""]


class TestPptxText:
    def test_assembles_slides_ocr_and_notes(self):
        per_slide = [[("text", "Заголовок"), ("img", "a.png"), ("img", "b.png")], []]
        out = px.pptx_text(per_slide, ["заметка"], {"a.png": "  распознано ", "b.png": "  "})
        assert out == (
            "[Слайд 1]\n\nЗаголовок\n\n[Текст с изображения (OCR)]: распознано\n\n"
            "[Заметки докладчика]: заметка\n\n[Слайд 2]"
        )

    def test_no_ocr_mapping(self):
        assert px.pptx_text([[("img", "a.png")]], []) == "[Слайд 1]"
