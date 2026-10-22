"""services/pptx_xml: разбор .pptx без python-pptx (zip + XML): тексты, группы, таблицы, картинки, заметки, сборка текста."""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from app.services import pptx_xml as px

from . import pptx_factory as f

pytestmark = pytest.mark.unit


def collect(data, max_images=10):
    return px.collect_pptx(data, max_images)


class TestTextAndOrder:
    def test_shapes_in_order_with_multiline_text(self):
        data = f.build_pptx([f.slide_xml(f.sp("Заголовок"), f.sp("Строка 1", "Строка 2")), f.slide_xml(f.sp("Второй"))])

        per_slide, images, notes = collect(data)

        assert per_slide == [[("text", "Заголовок"), ("text", "Строка 1\nСтрока 2")], [("text", "Второй")]]
        assert images == [] and notes == ["", ""]

    def test_line_break_inside_paragraph(self):
        data = f.build_pptx([f.slide_xml(f.sp_raw_paragraph("<a:r><a:t>до</a:t></a:r><a:br/><a:r><a:t>после</a:t></a:r>"))])

        assert collect(data)[0] == [[("text", "до\nпосле")]]

    def test_shapes_without_text_are_skipped(self):
        data = f.build_pptx([f.slide_xml(f.sp_no_body(), f.sp(""), f.sp("   "), f.sp("есть"))])

        assert collect(data)[0] == [[("text", "есть")]]

    def test_groups_are_expanded_recursively(self):
        data = f.build_pptx([f.slide_xml(f.sp("до"), f.group(f.sp("в группе"), f.group(f.sp("глубже"))), f.sp("после"))])

        assert [v for _, v in collect(data)[0][0]] == ["до", "в группе", "глубже", "после"]

    def test_slide_without_sptree_gives_empty_slide(self):
        data = f.build_pptx([f"<p:sld {f.NS}><p:cSld/></p:sld>"])

        assert collect(data)[0] == [[]]

    def test_missing_slide_list_gives_no_slides(self):
        data = f.build_pptx([f.slide_xml(f.sp("x"))], include_sld_id_list=False)

        assert collect(data) == ([], [], [])

    def test_slide_ids_without_relationship_are_skipped(self):
        data = f.build_pptx([f.slide_xml(f.sp("реальный"))], sld_id_extra='<p:sldId id="999" r:id="rId99"/><p:sldId id="1000"/>')

        assert collect(data)[0] == [[("text", "реальный")]]

    def test_corrupt_archive_raises(self):
        with pytest.raises(zipfile.BadZipFile):
            collect(b"not a zip")

    def test_missing_presentation_part_raises(self):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("other.txt", "x")

        with pytest.raises(KeyError):
            collect(buf.getvalue())


class TestTables:
    def test_table_becomes_markdown_with_header_separator(self):
        data = f.build_pptx([f.slide_xml(f.table([["Имя", "Возраст"], ["Анна", "30"], ["Борис", "25"]]))])

        assert collect(data)[0] == [[("text", "| Имя | Возраст |\n| --- | --- |\n| Анна | 30 |\n| Борис | 25 |")]]

    def test_single_row_has_no_separator(self):
        assert px._table_markdown(_tbl([["a", "b"]])) == "| a | b |"

    def test_merged_continuation_cells_are_dropped(self):
        tbl = _tbl([["Шапка", "", "Колонка"], ["1", "2", "3"]], merged_continuation={(0, 1)})

        assert px._table_markdown(tbl) == "| Шапка | Колонка |\n| --- | --- |\n| 1 | 2 | 3 |"

    def test_whitespace_in_cells_is_collapsed_and_empty_rows_skipped(self):
        tbl = _tbl([["  много    пробелов ", "x"], ["", ""], ["a", "b"]])

        assert px._table_markdown(tbl) == "| много пробелов | x |\n| --- | --- |\n| a | b |"

    def test_graphic_frame_without_table_is_ignored(self):
        data = f.build_pptx([f.slide_xml(f.graphic_frame_without_table(), f.sp("текст"))])

        assert collect(data)[0] == [[("text", "текст")]]

    def test_table_with_only_empty_cells_is_ignored(self):
        data = f.build_pptx([f.slide_xml(f.table([["", ""], ["", ""]]))])

        assert collect(data)[0] == [[]]

    def test_empty_first_row_loses_header_separator_known_issue(self):
        # RAG-23: разделитель ставится только если пустой не оказалась именно строка 0 (r_idx == 0).
        # При пустой первой строке таблица уходит без «| --- |», и чанкер/markdown-рендер не считает её таблицей
        # (в docx эта же ситуация обработана счётчиком emitted).
        tbl = _tbl([["", ""], ["Имя", "Возраст"], ["Анна", "30"]])

        assert px._table_markdown(tbl) == "| Имя | Возраст |\n| Анна | 30 |"

    @pytest.mark.xfail(strict=True, reason="BUG RAG-23: пустая первая строка таблицы pptx - нет разделителя шапки |---|")
    def test_empty_first_row_must_still_have_separator_after_header(self):
        tbl = _tbl([["", ""], ["Имя", "Возраст"], ["Анна", "30"]])

        assert px._table_markdown(tbl) == "| Имя | Возраст |\n| --- | --- |\n| Анна | 30 |"


def _tbl(rows, merged_continuation=None):
    from xml.etree import ElementTree as ET

    xml = f.table(rows, merged_continuation)
    root = ET.fromstring(f'<root {f.NS}>{xml}</root>')
    return root.find(".//" + px._A + "tbl")


class TestImages:
    def _data(self, slides, rels, media):
        return f.build_pptx(slides, {1: rels}, media)

    def test_raster_picture_is_collected_in_shape_order(self):
        data = self._data([f.slide_xml(f.sp("до"), f.pic("rId5"), f.sp("после"))],
                          [("rId5", f.T_IMAGE, "../media/image1.png")], {"ppt/media/image1.png": f.PNG_1x1})

        per_slide, images, _ = collect(data)

        assert per_slide == [[("text", "до"), ("img", "slide1_img1.png"), ("text", "после")]]
        assert images == [("slide1_img1.png", f.PNG_1x1)]

    def test_absolute_target_is_resolved_from_archive_root(self):
        data = self._data([f.slide_xml(f.pic("rId1"))], [("rId1", f.T_IMAGE, "/ppt/media/pic.JPG")],
                          {"ppt/media/pic.JPG": b"jpeg"})

        assert collect(data)[1] == [("slide1_img1.jpg", b"jpeg")]

    def test_external_relationships_are_ignored(self):
        data = self._data([f.slide_xml(f.pic("rId1"))], [("rId1", f.T_IMAGE, "http://evil/x.png", "External")], {})

        assert collect(data)[0] == [[]]

    @pytest.mark.parametrize("target", ["../media/image1.emf", "../media/image1.svg", "../media/noext"])
    def test_vector_and_unknown_formats_are_skipped(self, target):
        data = self._data([f.slide_xml(f.pic("rId1"))], [("rId1", f.T_IMAGE, target)], {"ppt/media/" + target.split("/")[-1]: b"v"})

        per_slide, images, _ = collect(data)

        assert per_slide == [[]] and images == []

    def test_picture_without_blip_or_with_unknown_rid_is_skipped(self):
        data = self._data([f.slide_xml(f.pic(None), f.pic("rId404"))], [("rId1", f.T_IMAGE, "../media/a.png")], {})

        assert collect(data)[0] == [[]]

    def test_missing_media_part_is_skipped_without_item(self):
        data = self._data([f.slide_xml(f.pic("rId1"), f.sp("текст"))], [("rId1", f.T_IMAGE, "../media/gone.png")], {})

        per_slide, images, _ = collect(data)

        assert per_slide == [[("text", "текст")]] and images == []

    def test_image_limit_applies_per_document(self):
        pics = f.slide_xml(f.pic("rId1"), f.pic("rId2"), f.pic("rId3"))
        data = self._data([pics], [("rId1", f.T_IMAGE, "../media/1.png"), ("rId2", f.T_IMAGE, "../media/2.png"),
                                   ("rId3", f.T_IMAGE, "../media/3.png")],
                          {"ppt/media/1.png": b"1", "ppt/media/2.png": b"2", "ppt/media/3.png": b"3"})

        per_slide, images, _ = collect(data, max_images=2)

        assert [n for n, _ in images] == ["slide1_img1.png", "slide1_img2.png"]
        assert per_slide == [[("img", "slide1_img1.png"), ("img", "slide1_img2.png")]]

    def test_zero_limit_collects_nothing(self):
        data = self._data([f.slide_xml(f.pic("rId1"))], [("rId1", f.T_IMAGE, "../media/1.png")], {"ppt/media/1.png": b"1"})

        assert collect(data, max_images=0)[1] == []

    def test_decompressed_media_size_is_capped(self):
        # BUG-RAG-17 / RAG-03: части архива > _MAX_MEMBER_BYTES не читаются в память.
        big = b"\0" * (40 * 1024 * 1024)
        data = self._data([f.slide_xml(f.pic("rId1"))], [("rId1", f.T_IMAGE, "../media/bomb.png")], {"ppt/media/bomb.png": big})

        assert len(data) < 200 * 1024
        try:
            _, images, _ = collect(data)
        except Exception:
            return  # отказ разбирать — допустимое поведение
        assert not images or len(images[0][1]) < 20 * 1024 * 1024


class TestNotes:
    def _with_notes(self, notes_part_xml, rel_target="../notesSlides/notesSlide1.xml"):
        rels = [("rId9", f.T_NOTES, rel_target)]
        extra = {"ppt/notesSlides/notesSlide1.xml": notes_part_xml} if notes_part_xml is not None else {}
        return f.build_pptx([f.slide_xml(f.sp("т"))], {1: rels}, extra)

    def test_only_body_placeholder_is_returned(self):
        data = self._with_notes(f.notes_xml("Не забыть показать график"))

        assert collect(data)[2] == ["Не забыть показать график"]

    def test_notes_without_body_are_empty(self):
        assert collect(self._with_notes(f.notes_xml("")))[2] == [""]

    def test_missing_notes_part_is_empty_not_error(self):
        assert collect(self._with_notes(None))[2] == [""]

    def test_slide_without_notes_relationship(self):
        assert collect(f.build_pptx([f.slide_xml(f.sp("т"))]))[2] == [""]


class TestRelsParsing:
    def test_rels_of_part_without_rels_file(self):
        with zipfile.ZipFile(BytesIO(f.build_pptx([f.slide_xml()]))) as z:
            assert px._rels(z, "ppt/slides/slide1.xml") == {}

    def test_rels_normalise_relative_paths_and_strip_type(self):
        data = f.build_pptx([f.slide_xml()], {1: [("rId7", f.T_IMAGE, "../media/x.png"), ("rId8", f.T_NOTES, "../notesSlides/n.xml")]})

        with zipfile.ZipFile(BytesIO(data)) as z:
            assert px._rels(z, "ppt/slides/slide1.xml") == {
                "rId7": ("image", "ppt/media/x.png"), "rId8": ("notesSlide", "ppt/notesSlides/n.xml"),
            }


class TestPptxText:
    def test_slides_text_ocr_and_notes(self):
        per_slide = [[("text", "Заголовок"), ("img", "a.png"), ("img", "b.png")], [("text", "Второй")]]

        out = px.pptx_text(per_slide, ["заметка 1 ", ""], {"a.png": "  текст на картинке ", "b.png": "   "})

        assert out == (
            "[Слайд 1]\n\nЗаголовок\n\n[Текст с изображения (OCR)]: текст на картинке\n\n[Заметки докладчика]: заметка 1"
            "\n\n[Слайд 2]\n\nВторой"
        )

    def test_missing_notes_entries_and_no_ocr_map(self):
        assert px.pptx_text([[("img", "x.png")], []], []) == "[Слайд 1]\n\n[Слайд 2]"

    def test_empty_presentation(self):
        assert px.pptx_text([], []) == ""

    def test_roundtrip_from_real_file(self):
        data = f.build_pptx([f.slide_xml(f.sp("Привет"), f.table([["a", "b"], ["1", "2"]]))])

        per_slide, _, notes = collect(data)

        assert px.pptx_text(per_slide, notes) == "[Слайд 1]\n\nПривет\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"
