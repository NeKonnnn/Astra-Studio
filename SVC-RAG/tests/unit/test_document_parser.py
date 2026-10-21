"""services/document_parser: извлечение текста из docx/xlsx/pdf/txt/rtf, выбор парсера по содержимому, эвристики качества PDF."""

from io import BytesIO

import pytest

from app.services import document_parser as dp

pytestmark = pytest.mark.unit

docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")
fitz = pytest.importorskip("fitz")


@pytest.fixture(autouse=True)
def _no_ocr(monkeypatch):
    """OCR встроенных картинок и PDF-сканов ходит в backend — в тестах сеть закрыта, подменяем."""
    calls = []

    async def office(blobs, filename):
        calls.append(("office", len(blobs), filename))
        return ""

    async def pdf_ocr(data, filename):
        calls.append(("pdf", filename))
        return {"text": ""}

    monkeypatch.setattr(dp, "_ocr_office_images", office)
    monkeypatch.setattr(dp, "_call_backend_ocr_pdf", pdf_ocr)
    return calls


def docx_bytes(build):
    document = docx.Document()
    build(document)
    buf = BytesIO()
    document.save(buf)
    return buf.getvalue()


def xlsx_bytes(build):
    wb = openpyxl.Workbook()
    build(wb)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def pdf_bytes(pages):
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    return doc.tobytes()


class TestSmallHelpers:
    def test_confidence_info(self):
        info = dp._create_confidence_info_for_text("два слова", 87.5, "docx")

        assert info["confidence"] == 87.5 and info["text_length"] == 9 and info["file_type"] == "docx"
        assert info["words"] == [{"word": "два", "confidence": 87.5}, {"word": "слова", "confidence": 87.5}]
        assert dp._create_confidence_info_for_text(None, 1, "x")["words"] == []

    @pytest.mark.parametrize(
        ("parsed", "filename", "expected"),
        [
            ({"confidence_info": {"error": "vision недоступен"}}, "a.png", "OCR изображения: vision недоступен"),
            ({"file_type": "image"}, "a.bin", "Не удалось распознать текст на изображении. Нужна vision-модель в OCR_VISION_MODEL_PATH или загрузите фото с читаемым текстом."),
            ({}, "scan.JPG", None),
            ({"confidence_info": {"file_type": "IMAGE"}}, "", None),
            ({"file_type": "docx"}, "a.docx", "Документ пустой"),
            (None, "", "Документ пустой"),
        ],
    )
    def test_empty_document_error(self, parsed, filename, expected):
        message = dp.empty_document_index_error(parsed, filename)

        assert (message.startswith("Не удалось распознать") if expected is None else message == expected)

    def test_pdf_signature(self):
        assert dp._pdf_magic_bytes(b"%PDF-1.7 ...") is True
        for data in (b"", None, b"%PDF", b"PK\x03\x04", b"  %PDF-"):
            assert dp._pdf_magic_bytes(data) is False

    def test_text_normalization(self):
        raw = "стро-\nка\r\nс­мягким   переносом\t\tи\r\n\r\n\r\n\r\nабзац  "

        assert dp._normalize_extracted_text(raw) == "строка\nсмягким переносом и\n\nабзац"
        assert dp._normalize_extracted_text(None) == ""

    @pytest.mark.parametrize(
        ("text", "pages", "bad"),
        [
            ("", 1, True), (None, 1, True), ("коротко", 1, True), ("слово " * 30, 1, False), ("слово " * 30, 10, True),
            ("1234 5678 " * 40, 1, True), ("а б в г д е ж з и к " * 20, 1, True), ("Нормальный связный русский текст предложения. " * 6, 2, False),
        ],
    )
    def test_low_quality_pdf_layer(self, text, pages, bad):
        assert dp._looks_like_low_quality_pdf_text(text, pages) is bad


class TestPlainText:
    def test_encodings(self):
        assert dp.extract_text_from_txt("Привет, мир".encode("utf-8")) == "Привет, мир"
        assert dp.extract_text_from_txt("Привет, мир".encode("cp1251")) == "Привет, мир"
        assert dp.extract_text_from_txt(b"") == ""

    def test_koi8r_is_decoded_as_cp1251_known_issue(self):
        # Цепочка кодировок utf-8 → cp1251 → latin-1 → koi8-r: cp1251 принимает почти любые байты,
        # поэтому KOI8-R никогда не достигается и русский текст превращается в мусор.
        assert dp.extract_text_from_txt("Привет".encode("koi8-r")) != "Привет"

    def test_rtf(self):
        text = dp.extract_text_from_rtf(r"{\rtf1\ansi Hello {\b world}\par second}".encode())

        assert "Hello" in text and "world" in text and "second" in text and "\\rtf" not in text


class TestDocx:
    def test_paragraphs_and_tables_keep_document_order(self):
        def build(d):
            d.add_paragraph("До таблицы")
            table = d.add_table(rows=2, cols=2)
            for r, row in enumerate([["Имя", "Сумма"], ["Иван", "100"]]):
                for c, value in enumerate(row):
                    table.cell(r, c).text = value
            d.add_paragraph("После таблицы")

        text = dp.extract_text_from_docx(docx_bytes(build))

        assert text.index("До таблицы") < text.index("| Имя | Сумма |") < text.index("| Иван | 100 |") < text.index("После таблицы")
        assert "| --- | --- |" in text and "\n\n| Имя" in text  # пустая строка перед таблицей, разделитель после шапки

    def test_horizontal_merge_is_one_cell(self):
        def build(d):
            table = d.add_table(rows=2, cols=3)
            table.cell(0, 0).merge(table.cell(0, 2)).text = "Заголовок"
            for c, v in enumerate(["a", "b", "c"]):
                table.cell(1, c).text = v

        text = dp.extract_text_from_docx(docx_bytes(build))

        assert "| Заголовок |" in text and "| a | b | c |" in text

    def test_vertical_merge_repeats_the_value(self):
        def build(d):
            table = d.add_table(rows=3, cols=2)
            table.cell(0, 0).text, table.cell(0, 1).text = "Группа", "Значение"
            table.cell(1, 0).merge(table.cell(2, 0)).text = "А"
            table.cell(1, 1).text, table.cell(2, 1).text = "1", "2"

        text = dp.extract_text_from_docx(docx_bytes(build))

        assert "| А | 1 |" in text and "| А | 2 |" in text

    def test_orphan_vertical_merge_continuation_does_not_break_the_document(self):
        # python-docx падает на row.cells с продолжением без начала; разбор идёт по XML.
        def build(d):
            d.add_paragraph("Текст вокруг")
            table = d.add_table(rows=2, cols=1)
            table.cell(0, 0).text, table.cell(1, 0).text = "", "данные"
            from docx.oxml import OxmlElement

            table.cell(0, 0)._tc.get_or_add_tcPr().append(OxmlElement("w:vMerge"))

        text = dp.extract_text_from_docx(docx_bytes(build))

        assert "Текст вокруг" in text and "данные" in text

    def test_empty_rows_are_skipped_and_cell_whitespace_is_collapsed(self):
        def build(d):
            table = d.add_table(rows=3, cols=2)
            table.cell(0, 0).text, table.cell(0, 1).text = "a\nb", "c\td"
            table.cell(2, 0).text = "x"

        text = dp.extract_text_from_docx(docx_bytes(build))

        assert [line for line in text.splitlines() if line] == ["| a b | c d |", "| --- | --- |", "| x |  |"]

    def test_document_without_tables(self):
        assert dp.extract_text_from_docx(docx_bytes(lambda d: d.add_paragraph("только текст"))).strip() == "только текст"

    def test_broken_input(self):
        with pytest.raises(Exception):
            dp.extract_text_from_docx(b"not a docx")

    def test_images_blobs_of_a_text_only_document(self):
        assert dp._docx_image_blobs(docx_bytes(lambda d: d.add_paragraph("x"))) == [] and dp._docx_image_blobs(b"junk") == []


class TestXlsx:
    def test_sheets_and_rows(self):
        def build(wb):
            ws = wb.active
            ws.title = "Продажи"
            ws.append(["Месяц", "Сумма"])
            ws.append(["Январь", 100])
            ws.append([None, None])
            ws.append(["Февраль", None, 2.5])
            wb.create_sheet("Пустой")

        text = dp.extract_text_from_xlsx(xlsx_bytes(build))

        assert text.splitlines() == ["Лист: Продажи", "Месяц\tСумма", "Январь\t100", "Февраль\t2.5", "Лист: Пустой"]

    def test_empty_cells_shift_columns_known_issue(self):
        # Пустые ячейки отбрасываются, поэтому значения соседних колонок склеиваются в строку без позиций:
        # «Февраль, <пусто>, 2.5» неотличимо от «Февраль, 2.5».
        def build(wb):
            wb.active.append(["a", None, "c"])

        assert dp.extract_text_from_xlsx(xlsx_bytes(build)) == "Лист: Sheet\na\tc"

    def test_broken_input(self):
        with pytest.raises(Exception):
            dp.extract_text_from_xlsx(b"not an xlsx")

    def test_image_blobs_of_a_plain_workbook(self):
        assert dp._xlsx_image_blobs(xlsx_bytes(lambda wb: wb.active.append([1]))) == []


class TestPdf:
    # Встроенный шрифт PyMuPDF без кириллицы — для образцов PDF берём латиницу.
    LONG = "Quarterly tax report of the organisation contains information about income and expenses. " * 4

    async def test_text_layer_is_extracted(self):
        result = await dp.extract_text_from_pdf_bytes(pdf_bytes([self.LONG[:90] + " first page", "Second page of the document"]), "a.pdf")

        assert result["file_type"] == "pdf" and result["pages"] == 2
        assert "first page" in result["text"] and "Second page of the document" in result["text"]

    async def test_result_shape_and_confidence(self):
        result = await dp.extract_text_from_pdf_bytes(pdf_bytes([self.LONG[:100]] * 3), "a.pdf")

        info = result["confidence_info"]
        assert set(result) == {"text", "confidence_info", "file_type", "pages"} and info["file_type"] == "pdf" and info["pages_processed"] >= 1

    async def test_blank_pdf_goes_to_ocr_and_stays_empty_without_it(self, _no_ocr):
        result = await dp.extract_text_from_pdf_bytes(pdf_bytes([""]), "scan.pdf")

        assert result["text"] == "" and ("pdf", "scan.pdf") in _no_ocr

    async def test_backend_ocr_text_replaces_an_empty_layer(self, monkeypatch):
        async def ocr(data, filename):
            return {"text": "распознанный текст скана " * 10}

        monkeypatch.setattr(dp, "_call_backend_ocr_pdf", ocr)

        result = await dp.extract_text_from_pdf_bytes(pdf_bytes([""]), "scan.pdf")

        assert "распознанный текст скана" in result["text"] and result["confidence_info"]["ocr_source"] == "backend_llm"

    async def test_ocr_failures_are_survivable(self, monkeypatch):
        async def boom(data, filename):
            raise RuntimeError("backend down")

        monkeypatch.setattr(dp, "_call_backend_ocr_pdf", boom)

        assert (await dp.extract_text_from_pdf_bytes(pdf_bytes([""]), "scan.pdf"))["text"] == ""


class TestParseDocument:
    async def test_text_formats(self):
        for name in ("a.txt", "A.MD", "x.log", "t.csv", "n.markdown"):
            parsed = await dp.parse_document("текст".encode(), name)
            assert parsed["text"] == "текст" and parsed["confidence_info"]["file_type"] == "txt"

    async def test_docx_with_ocr_text_appended(self, monkeypatch):
        async def ocr(blobs, filename):
            return "[Текст из изображений документа (OCR)]:\nнадпись"

        monkeypatch.setattr(dp, "_ocr_office_images", ocr)

        parsed = await dp.parse_document(docx_bytes(lambda d: d.add_paragraph("абзац")), "a.docx")

        assert parsed["text"].startswith("абзац") and parsed["text"].endswith("надпись") and parsed["confidence_info"]["file_type"] == "docx"

    async def test_xlsx_and_macro_variants(self):
        data = xlsx_bytes(lambda wb: wb.active.append(["x", 1]))

        parsed = await dp.parse_document(data, "t.xlsx")

        assert "x\t1" in parsed["text"] and parsed["confidence_info"]["file_type"] == "excel"

    async def test_pdf_by_extension_and_by_signature(self):
        pdf = pdf_bytes([TestPdf.LONG[:100]])

        assert (await dp.parse_document(pdf, "a.pdf"))["file_type"] == "pdf"
        assert (await dp.parse_document(pdf, "upload.bin"))["file_type"] == "pdf"  # расширение врёт — сигнатура решает
        assert (await dp.parse_document(pdf, "noext"))["file_type"] == "pdf"

    async def test_zip_office_files_with_an_old_extension_are_parsed_by_content(self):
        data = docx_bytes(lambda d: d.add_paragraph("настоящий docx"))

        parsed = await dp.parse_document(data, "report.doc")

        assert "настоящий docx" in parsed["text"] and parsed["confidence_info"]["file_type"] == "docx"

    async def test_rtf_disguised_as_doc(self):
        parsed = await dp.parse_document(rb"{\rtf1\ansi hello rtf}", "letter.doc")

        assert "hello rtf" in parsed["text"] and parsed["confidence_info"]["file_type"] == "rtf"

    @pytest.mark.parametrize("name", ["a.exe", "archive.zip", "noextension", "", None, "photo.gif"])
    async def test_unsupported_formats(self, name):
        assert await dp.parse_document(b"MZ\x90\x00 binary", name) is None

    async def test_parser_errors_become_none(self):
        assert await dp.parse_document(b"definitely not a docx", "a.docx") is None
        assert await dp.parse_document(b"junk", "a.xlsx") is None

    async def test_empty_input(self):
        assert await dp.parse_document(b"", "a.txt") == {"text": "", "confidence_info": dp._create_confidence_info_for_text("", 100.0, "txt")}

    async def test_images_go_to_the_image_ocr(self, monkeypatch):
        seen = []

        async def image(data, filename):
            seen.append(filename)
            return {"text": "распознано", "confidence_info": {}, "file_type": "image"}

        monkeypatch.setattr(dp, "extract_text_from_image_bytes", image)

        for name in ("a.PNG", "b.jpeg", "c.webp", "d.bmp"):
            assert (await dp.parse_document(b"\x89PNG....", name))["text"] == "распознано"
        assert seen == ["a.PNG", "b.jpeg", "c.webp", "d.bmp"]
