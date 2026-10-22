"""services/ppt_binary: разбор .ppt (OLE + записи PowerPoint) на файлах, собранных кодом (ppt_factory)."""

from __future__ import annotations

import struct

import pytest

from app.services import ppt_binary as pb

from . import ppt_factory as pf
from .ppt_factory import Note, Slide

pytestmark = pytest.mark.unit

PNG = b"\x89PNG\r\n\x1a\n-png-body"


def collect(data, max_images=10):
    return pb.collect_ppt(data, max_images)


def text_slide(*texts, **kw):
    return Slide(shapes=[pf.shape(pf.textbox(pf.text_chars(t))) for t in texts], **kw)


# ───────────────────────────── OLE ─────────────────────────────


class TestOle:
    def test_not_ole_is_rejected(self):
        with pytest.raises(pb.PptError, match="не OLE-файл"):
            pb._Ole(b"PK\x03\x04" + b"\0" * 600)

    def test_small_streams_live_in_ministream_and_big_in_fat(self):
        big = bytes(range(256)) * 40  # 10 КБ - больше порога 4096
        ole = pb._Ole(pf.build_ole({"Small": b"hello", "Other": b"x" * 100, "Big": big}))

        assert ole.stream("Small") == b"hello"
        assert ole.stream("other") == b"x" * 100  # имена регистронезависимы
        assert ole.stream("BIG") == big
        assert ole.stream("Missing") is None

    def test_difat_sector_is_followed(self):
        streams = {"Current User": b"abc", "PowerPoint Document": b"d" * 6000}

        ole = pb._Ole(pf.build_ole(streams, difat_sector=True))

        assert ole.stream("PowerPoint Document") == b"d" * 6000 and ole.stream("Current User") == b"abc"

    def test_storage_entries_are_ignored(self):
        ole = pb._Ole(pf.build_ole({"S": b"1"}, extra_storage=True))

        assert list(ole.entries) == ["s"]

    def test_file_without_ministream_streams(self):
        ole = pb._Ole(pf.build_ole({"Big": b"z" * 5000}))

        assert ole.ministream == b"" and ole.minifat == [] and ole.stream("Big") == b"z" * 5000

    def test_custom_mini_cutoff_moves_stream_to_fat(self):
        ole = pb._Ole(pf.build_ole({"A": b"q" * 300}, cutoff=256))

        assert ole.stream("A") == b"q" * 300 and ole.ministream == b""

    def test_looping_minifat_chain_is_cut(self):
        ole = pb._Ole(pf.build_ole({"A": b"a" * 200}))
        ole.minifat[0] = 0  # первая мини-секция ссылается на себя

        assert ole.stream("A") == b"a" * 64  # одна секция вместо бесконечного цикла

    def test_looping_fat_chain_is_cut(self):
        ole = pb._Ole(pf.build_ole({"B": b"b" * 5000}))
        start = ole.entries["b"][0]
        ole.fat[start] = start

        assert len(ole._chain(start)) == 512

    def test_chain_with_out_of_range_sector_stops(self):
        ole = pb._Ole(pf.build_ole({"B": b"b" * 5000}))

        assert ole._chain(10**6) == b""

    def test_zero_length_stream_is_empty(self):
        ole = pb._Ole(pf.build_ole({"E": b"", "F": b"x"}))

        assert ole.stream("E") == b""


# ───────────────────────────── собранные файлы ─────────────────────────────


class TestCollectTexts:
    def test_text_chars_and_text_bytes_from_shapes(self):
        slide = Slide(shapes=[pf.shape(pf.textbox(pf.text_chars("Привет\rмир"))), pf.shape(pf.textbox(pf.text_bytes("latin\x0bsoft")))])

        per_slide, images, notes, stats = collect(pf.build_ppt([slide]))

        assert per_slide == [[("text", "Привет\nмир"), ("text", "latin\nsoft")]]
        assert images == [] and notes == [""]
        assert stats["слайдов"] == 1 and stats["текстов"] == 2 and stats["сохранений"] == 1 and stats["в_bstore"] == 0

    def test_slides_keep_listed_order(self):
        data = pf.build_ppt([text_slide("первый"), text_slide("второй"), text_slide("третий")])

        assert [s[0][1] for s in collect(data)[0]] == ["первый", "второй", "третий"]

    def test_field_placeholders_and_blank_texts_are_dropped(self):
        slide = Slide(shapes=[pf.shape(pf.textbox(pf.text_chars("*"), pf.text_chars("  "), pf.text_chars("реальный")))])

        assert collect(pf.build_ppt([slide]))[0] == [[("text", "реальный")]]

    def test_outline_text_is_referenced_by_shape_and_not_duplicated(self):
        slide = Slide(shapes=[pf.shape(pf.textbox(pf.outline_ref(1))), pf.shape(pf.textbox(pf.outline_ref(1)))],
                      outline=[pf.text_chars("нулевой"), pf.text_chars("первый")])

        per_slide = collect(pf.build_ppt([slide]))[0]

        # k=1 использован один раз (повторная ссылка игнорируется); неиспользованный k=0 добавляется в конец
        assert per_slide == [[("text", "первый"), ("text", "нулевой")]]

    def test_bad_outline_references_are_ignored(self):
        slide = Slide(shapes=[pf.shape(pf.textbox(pf.outline_ref(5), pf.outline_ref(-1), pf.outline_ref(0)))],
                      outline=[pf.text_bytes("*"), pf.text_chars("")])

        # k=0 - «*» (пустой после чистки), k=1 пуст: в слайд ничего не попадает
        assert collect(pf.build_ppt([slide]))[0] == [[]]

    def test_unreferenced_outline_text_goes_to_slide(self):
        slide = Slide(shapes=[], outline=[pf.text_chars("только в списке слайдов")])

        per_slide, _, _, stats = collect(pf.build_ppt([slide]))

        assert per_slide == [[("text", "только в списке слайдов")]] and stats["текстов"] == 1

    def test_groups_are_expanded_including_group_inside_shape(self):
        inner = pf.shape(pf.textbox(pf.text_chars("в группе")))
        nested = pf.shape(pf.group(pf.shape(pf.textbox(pf.text_chars("вложенная")))))
        slide = Slide(shapes=[pf.shape(pf.textbox(pf.text_chars("до"))), pf.group(inner), nested])

        per_slide = collect(pf.build_ppt([slide]))[0]

        assert [t for _, t in per_slide[0]] == ["до", "в группе", "вложенная"]

    def test_slide_without_drawing_still_gets_outline_text(self):
        slide = Slide(with_drawing=False, outline=[pf.text_chars("заголовок")])

        assert collect(pf.build_ppt([slide]))[0] == [[("text", "заголовок")]]

    def test_slide_record_of_wrong_type_is_treated_as_empty(self):
        slide = Slide(wrong_type=True, shapes=[pf.shape(pf.textbox(pf.text_chars("потеряно")))], outline=[pf.text_chars("список")])

        assert collect(pf.build_ppt([slide]))[0] == [[("text", "список")]]

    def test_slide_missing_from_directory_is_treated_as_empty(self):
        data = pf.build_ppt([text_slide("a"), text_slide("b")], drop_slide_from_directory=2)

        assert collect(data)[0] == [[], [("text", "b")]]

    def test_newer_edit_replaces_slide_and_older_is_not_overwritten(self):
        data = pf.build_ppt([text_slide("старый"), text_slide("без правок")], newer_slides={0: text_slide("новый")})

        per_slide, _, _, stats = collect(data)

        assert per_slide == [[("text", "новый")], [("text", "без правок")]] and stats["сохранений"] == 2

    def test_no_slides(self):
        per_slide, images, notes, stats = collect(pf.build_ppt([]))

        assert (per_slide, images, notes, stats["слайдов"]) == ([], [], [], 0)


class TestNotes:
    def test_notes_from_list_and_page_without_duplicates(self):
        notes = [Note(own_id=77, listed=[pf.text_chars("из списка"), pf.text_chars("")],
                      page_boxes=[pf.textbox(pf.text_chars("из списка"), pf.text_chars("со страницы"))])]
        data = pf.build_ppt([text_slide("слайд", notes_id=77)], notes)

        assert collect(data)[2] == ["из списка\nсо страницы"]

    def test_slide_without_notes_reference(self):
        data = pf.build_ppt([text_slide("слайд")], [Note(own_id=5, listed=[pf.text_chars("чужие")])])

        assert collect(data)[2] == [""]

    def test_dangling_notes_reference(self):
        data = pf.build_ppt([text_slide("слайд", notes_id=999)], [Note(own_id=5)])

        assert collect(data)[2] == [""]

    def test_notes_page_of_wrong_type_gives_only_listed_text(self):
        notes = [Note(own_id=7, listed=[pf.text_chars("список")], page_boxes=[pf.textbox(pf.text_chars("страница"))], wrong_type=True)]

        assert collect(pf.build_ppt([text_slide("с", notes_id=7)], notes))[2] == ["список"]

    def test_notes_page_text_bytes(self):
        notes = [Note(own_id=7, page_boxes=[pf.textbox(pf.text_bytes("ascii note"))])]

        assert collect(pf.build_ppt([text_slide("с", notes_id=7)], notes))[2] == ["ascii note"]


class TestImages:
    def _picture_ppt(self, blips, pibs, **kw):
        pics, offsets = pf.pictures_stream(*blips)
        slide = Slide(shapes=[pf.shape(pf.fopt(p)) for p in pibs])
        return pf.build_ppt([slide], bstore_children=[pf.fbse(o) for o in offsets], pictures=pics, **kw)

    def test_png_and_jpeg_blips_are_collected_in_order(self):
        data = self._picture_ppt([pf.blip(0xF01E, 0x6E0, PNG), pf.blip(0xF01D, 0x46A, b"JPEGDATA"), pf.blip(0xF01D, 0x46B, b"JPEG2")],
                                 [1, 2, 3])

        per_slide, images, _, stats = collect(data)

        assert images == [("slide1_img1.png", PNG), ("slide1_img2.jpg", b"JPEGDATA"), ("slide1_img3.jpg", b"JPEG2")]
        assert per_slide == [[("img", "slide1_img1.png"), ("img", "slide1_img2.jpg"), ("img", "slide1_img3.jpg")]]
        assert stats["в_bstore"] == 3 and stats["картинок"] == 3 and stats["растровых"] == 3

    def test_two_uid_instances_shift_the_body(self):
        data = self._picture_ppt([pf.blip(0xF01E, 0x6E1, PNG), pf.blip(0xF02A, 0x6E3, b"CMYK")], [1, 2])

        assert [b for _, b in collect(data)[1]] == [PNG, b"CMYK"]

    def test_vector_formats_are_counted_but_not_returned(self):
        emf = pf.blip(0xF01A, 0x3D4, b"EMF")
        data = self._picture_ppt([emf, pf.blip(0xF01E, 0x6E0, PNG)], [1, 2])

        per_slide, images, _, stats = collect(data)

        assert [n for n, _ in images] == ["slide1_img1.png"] and stats["картинок"] == 2 and stats["растровых"] == 1

    def test_image_limit(self):
        data = self._picture_ppt([pf.blip(0xF01E, 0x6E0, PNG)] * 3, [1, 2, 3])

        assert len(collect(data, max_images=2)[1]) == 2

    def test_pib_out_of_range_or_zero_is_ignored(self):
        data = self._picture_ppt([pf.blip(0xF01E, 0x6E0, PNG)], [0, 5])

        assert collect(data)[1] == []

    def test_bad_bstore_entry_and_bad_offset(self):
        pics, offsets = pf.pictures_stream(pf.blip(0xF01E, 0x6E0, PNG))
        bstore = [pf.rec(0xF008, b"short"), pf.fbse(10**6), pf.fbse(offsets[0])]
        slide = Slide(shapes=[pf.shape(pf.fopt(1)), pf.shape(pf.fopt(2)), pf.shape(pf.fopt(3))])
        data = pf.build_ppt([slide], bstore_children=bstore, pictures=pics)

        _, images, _, stats = collect(data)

        assert stats["в_bstore"] == 3 and [b for _, b in images] == [PNG]

    def test_missing_pictures_stream(self):
        slide = Slide(shapes=[pf.shape(pf.fopt(1))])
        data = pf.build_ppt([slide], bstore_children=[pf.fbse(0)])

        assert collect(data)[1] == []

    def test_options_with_other_properties_before_picture_index(self):
        pics, offsets = pf.pictures_stream(pf.blip(0xF01E, 0x6E0, PNG))
        opt = pf.rec(pf.RT_FOPT, struct.pack("<HI", 0x0001, 7) + struct.pack("<HI", 0x4104, 1), inst=2)
        slide = Slide(shapes=[pf.shape(opt)])
        data = pf.build_ppt([slide], bstore_children=[pf.fbse(offsets[0])], pictures=pics)

        assert [b for _, b in collect(data)[1]] == [PNG]

    def test_truncated_options_record_is_safe(self):
        opt = pf.rec(pf.RT_FOPT, struct.pack("<HI", 0x0001, 7), inst=3)  # заявлено 3 свойства, есть одно
        slide = Slide(shapes=[pf.shape(opt)])

        assert collect(pf.build_ppt([slide], bstore_children=[]))[1] == []

    def test_bmp_blip_gets_file_header(self):
        dib = struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 8, 0, 16, 0, 0, 0, 0) + b"\0" * 1024 + b"\x01\x02\x03\x04"
        data = self._picture_ppt([pf.blip(0xF01F, 0x7A8, dib)], [1])

        name, body = collect(data)[1][0]

        assert name == "slide1_img1.bmp" and body[:2] == b"BM" and body[14:] == dib
        # bits=8 и colors=0 в заголовке: 256 цветов палитры -> смещение пикселей
        assert struct.unpack_from("<I", body, 10)[0] == 14 + 40 + 256 * 4

    def test_tiff_blip(self):
        data = self._picture_ppt([pf.blip(0xF029, 0x6E4, b"II*\0tiff")], [1])

        assert collect(data)[1] == [("slide1_img1.tif", b"II*\0tiff")]

    def test_empty_blip_body_is_skipped(self):
        data = self._picture_ppt([pf.blip(0xF01E, 0x6E0, b"")], [1])

        assert collect(data)[1] == []


class TestBlipHelpers:
    def test_blip_offset_checks(self):
        assert pb._blip(b"", 0) is None
        assert pb._blip(b"\0" * 20, -1) is None
        assert pb._blip(b"\0" * 20, 15) is None

    def test_unknown_blip_type(self):
        assert pb._blip(pf.blip(0xF01A, 0x3D4, b"x"), 0) is None

    def test_dib_to_bmp(self):
        assert pb._dib_to_bmp(b"short") == b""

        palette_dib = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 4, 0, 4, 0, 0, 3, 0) + b"\0" * 12 + b"px"
        bmp = pb._dib_to_bmp(palette_dib)
        assert bmp[:2] == b"BM" and struct.unpack_from("<I", bmp, 10)[0] == 14 + 40 + 3 * 4

        truecolor = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0) + b"pix"
        assert struct.unpack_from("<I", pb._dib_to_bmp(truecolor), 10)[0] == 14 + 40

        bitfields = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 16, 3, 4, 0, 0, 0, 0) + b"\0" * 12
        assert struct.unpack_from("<I", pb._dib_to_bmp(bitfields), 10)[0] == 14 + 40 + 12

    def test_old_style_dib_header_has_no_colors_field(self):
        old = struct.pack("<IiiHHI", 28, 1, 1, 1, 24, 0).ljust(40, b"\0")
        # hsize < 36: количество цветов в заголовке не читается
        assert struct.unpack_from("<I", pb._dib_to_bmp(old), 10)[0] == 14 + 28


class TestTextAtom:
    def test_decoding(self):
        buf = "Строка\rвторая\x0bтретья".encode("utf-16-le")

        assert pb._text_of_atom(buf, pb._RT_TEXT_CHARS, 0, len(buf)) == "Строка\nвторая\nтретья"
        assert pb._text_of_atom(b"caf\xe9", pb._RT_TEXT_BYTES, 0, 4) == "café"
        assert pb._text_of_atom(b"**", pb._RT_TEXT_BYTES, 0, 2) == ""
        assert pb._text_of_atom(b"   ", pb._RT_TEXT_BYTES, 0, 3) == ""


class TestRecords:
    def test_truncated_record_length_is_clipped(self):
        buf = struct.pack("<HHI", 0, 0x1234, 1000) + b"abc"

        assert list(pb._records(buf)) == [(0x1234, 0, 0, 8, 3)]

    def test_trailing_garbage_shorter_than_header_is_ignored(self):
        buf = pb.struct.pack("<HHI", 0x12, 7, 0) + b"abc"

        assert [r[0] for r in pb._records(buf)] == [7]

    def test_find(self):
        buf = pf.rec(1, b"a") + pf.rec(2, b"bb")

        assert pb._find(buf, 0, len(buf), 2)[4] == 2 and pb._find(buf, 0, len(buf), 3) is None


# ───────────────────────────── ошибки структуры ─────────────────────────────


def ole_with(doc=None, cur=None, extra=None):
    streams = {}
    if doc is not None:
        streams["PowerPoint Document"] = doc
    if cur is not None:
        streams["Current User"] = cur
    streams.update(extra or {})
    return pf.build_ole(streams)


class TestStructureErrors:
    def test_not_ole(self):
        with pytest.raises(pb.PptError):
            collect(b"%PDF-1.4" + b"\0" * 600)

    def test_missing_streams(self):
        parts = pf.build_parts([text_slide("x")])

        with pytest.raises(pb.PptError, match="нет потока «PowerPoint Document»"):
            collect(ole_with(cur=parts.current_user))
        with pytest.raises(pb.PptError, match="нет потока «Current User»"):
            collect(ole_with(doc=parts.doc))

    def test_current_user_without_atom(self):
        parts = pf.build_parts([text_slide("x")])

        with pytest.raises(pb.PptError, match="нет CurrentUserAtom"):
            collect(ole_with(doc=parts.doc, cur=pf.rec(0x0001, b"\0" * 12)))

    def test_document_reference_missing_from_directory(self):
        parts = pf.build_parts([text_slide("x")])
        # Current User указывает на смещение, где лежит не UserEditAtom: цикл правок сразу прерывается
        cur = pf.rec(pf.RT_CURRENT_USER, b"\0" * 8 + struct.pack("<I", 0) + b"\0" * 8)

        with pytest.raises(pb.PptError, match="не найден DocumentContainer"):
            collect(ole_with(doc=parts.doc, cur=cur))

    def test_edit_offset_beyond_stream(self):
        parts = pf.build_parts([text_slide("x")])
        cur = pf.rec(pf.RT_CURRENT_USER, b"\0" * 8 + struct.pack("<I", 10**7) + b"\0" * 8)

        with pytest.raises(pb.PptError, match="не найден DocumentContainer"):
            collect(ole_with(doc=parts.doc, cur=cur))

    def test_self_referencing_edit_chain_terminates(self):
        parts = pf.build_parts([text_slide("x")])
        doc = bytearray(parts.doc)
        edit_off = struct.unpack_from("<I", parts.current_user, 8 + 8)[0]
        struct.pack_into("<I", doc, edit_off + 8 + 8, edit_off)  # last_edit = он сам

        _, _, _, stats = collect(ole_with(doc=bytes(doc), cur=parts.current_user))

        assert stats["сохранений"] == 1

    def test_persist_points_to_non_document_record(self):
        parts = pf.build_parts([text_slide("x")], doc_pid_type_ok=False)

        with pytest.raises(pb.PptError, match="указывает не на DocumentContainer"):
            collect(ole_with(doc=parts.doc, cur=parts.current_user))

    def test_persist_directory_with_overstated_run_length_is_tolerated(self):
        parts = pf.build_parts([text_slide("x")])
        doc = bytearray(parts.doc)
        edit_off = struct.unpack_from("<I", parts.current_user, 16)[0]
        pdir_off = struct.unpack_from("<I", doc, edit_off + 8 + 12)[0]
        word = struct.unpack_from("<I", doc, pdir_off + 8)[0]
        # прогон заявляет 7 элементов, а в записи их 2: лишние не читаются, остальное разбирается
        struct.pack_into("<I", doc, pdir_off + 8, (word & 0xFFFFF) | (7 << 20))

        per_slide = collect(ole_with(doc=bytes(doc), cur=parts.current_user))[0]

        assert per_slide == [[("text", "x")]]

    def test_edit_chain_stops_at_record_that_is_not_user_edit(self):
        parts = pf.build_parts([text_slide("a"), text_slide("b")])
        # Current User ведёт на контейнер второго слайда, а не на UserEditAtom
        cur = pf.rec(pf.RT_CURRENT_USER, b"\x00" * 8 + struct.pack("<I", parts.slide_offsets[1]) + b"\x00" * 8)

        with pytest.raises(pb.PptError, match="не найден DocumentContainer"):
            collect(ole_with(doc=parts.doc, cur=cur))

    def test_difat_chain_ends_early_on_free_sector(self):
        data = bytearray(pf.build_ppt([text_slide("x")], ole_kw={"difat_sector": True}))
        struct.pack_into("<I", data, 0x48, 3)  # заявлено 3 DIFAT-сектора, реально один

        assert collect(bytes(data))[0] == [[("text", "x")]]

    def test_drawing_group_without_bstore(self):
        group = pf.container(pf.RT_PPDRAWING_GROUP, pf.rec(0xF006, b"\x00" * 4), pf.container(0xF000, pf.rec(0xF00B, b"")))
        data = pf.build_ppt([text_slide("x")], extra_doc_children=[group])

        _, _, _, stats = collect(data)

        assert stats["в_bstore"] == 0

    def test_empty_bstore_helper(self):
        assert pb._bstore_offsets(b"", 0, 0) == []
