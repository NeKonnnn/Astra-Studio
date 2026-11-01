"""routes/documents: извлечение содержимого вложений, структура каталога пода, MinIO-загрузка, служебные логи."""

import base64
import io
import os
import zipfile
from types import SimpleNamespace

import pytest

from backend.routes import documents as module

pytestmark = pytest.mark.unit


class RecLogger:
    """Заглушка логгера: запоминает вызовы (уровень, сообщение, аргументы)."""

    def __init__(self):
        self.records = []

    def _rec(self, level):
        def write(msg, *args, **kwargs):
            self.records.append((level, msg, args))

        return write

    def __getattr__(self, name):
        return self._rec(name)


@pytest.fixture
def rec_logger(monkeypatch):
    log = RecLogger()
    monkeypatch.setattr(module, "logger", log)
    return log


# ---------- фабрики файлов ----------


def _png(size=(4, 4), color=(200, 10, 10)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _bmp():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (3, 3), (1, 2, 3)).save(buf, format="BMP")
    return buf.getvalue()


def _pdf(texts):
    import fitz

    doc = fitz.open()
    for text in texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _docx(paragraphs=(), table=None):
    import docx

    doc = docx.Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    if table:
        t = doc.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, val in enumerate(row):
                t.cell(r, c).text = val
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _xlsx(sheets):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _pptx(slide_text="Заголовок слайда", with_image=False):
    """Минимальный валидный для collect_pptx архив: один слайд с текстом и (опц.) картинкой."""
    pic = ""
    slide_rels = ""
    if with_image:
        pic = f'<p:pic><p:blipFill><a:blip xmlns:r="{_R}" r:embed="rId1"/></p:blipFill></p:pic>'
        slide_rels = (
            f'<Relationships xmlns="{_REL}"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="../media/image1.png"/></Relationships>'
        )
    slide = (
        f'<p:sld xmlns:p="{_P}" xmlns:a="{_A}"><p:cSld><p:spTree>'
        f"<p:sp><p:txBody><a:p><a:r><a:t>{slide_text}</a:t></a:r></a:p></p:txBody></p:sp>"
        f"{pic}</p:spTree></p:cSld></p:sld>"
    )
    pres = (
        f'<p:presentation xmlns:p="{_P}" xmlns:r="{_R}">'
        '<p:sldIdLst><p:sldId r:id="rId1"/></p:sldIdLst></p:presentation>'
    )
    pres_rels = (
        f'<Relationships xmlns="{_REL}"><Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
        'Target="slides/slide1.xml"/></Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ppt/presentation.xml", pres)
        z.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        z.writestr("ppt/slides/slide1.xml", slide)
        if with_image:
            z.writestr("ppt/slides/_rels/slide1.xml.rels", slide_rels)
            z.writestr("ppt/media/image1.png", _png())
    return buf.getvalue()


# ---------- _extract_inline_payload ----------


class TestExtractInlinePayload:
    def test_image_keeps_declared_mime(self):
        data = _png()

        out = module._extract_inline_payload(data, "pic.png", "image/png")

        assert out["type"] == "image" and out["cs1"] == "image"
        assert out["content"] == "data:image/png;base64," + base64.b64encode(data).decode()

    def test_image_mime_is_taken_from_the_extension_when_type_is_not_image(self):
        out = module._extract_inline_payload(b"jpegbytes", "pic.JPG", "application/octet-stream")

        assert out["content"].startswith("data:image/jpeg;base64,")

    def test_unknown_image_extension_with_image_type_uses_the_declared_type(self):
        out = module._extract_inline_payload(b"x", "pic.heic", "image/heic")

        assert out["content"].startswith("data:image/heic;base64,")

    def test_bmp_is_converted_to_png(self):
        out = module._extract_inline_payload(_bmp(), "pic.bmp", "image/bmp")

        raw = base64.b64decode(out["content"].split(",", 1)[1])
        assert out["content"].startswith("data:image/png;base64,")
        assert raw.startswith(b"\x89PNG")

    def test_broken_bmp_raises(self):
        with pytest.raises(Exception):
            module._extract_inline_payload(b"not a bmp", "pic.bmp", "")

    def test_pdf_with_text_layer_is_split_by_pages(self):
        data = _pdf(["First page text", "Second page text"])

        out = module._extract_inline_payload(data, "a.pdf", "application/pdf")

        assert out["cs1"] == "pdf" and out["type"] == "text"
        assert "[Страница 1]" in out["content"] and "Second page text" in out["content"]

    def test_pdf_without_text_has_a_stub(self):
        out = module._extract_inline_payload(_pdf([""]), "scan.pdf", "")

        assert out["content"] == "(PDF не содержит извлекаемого текста)"

    def test_docx_paragraphs_and_tables(self):
        data = _docx(["Абзац один", "  "], table=[["A", "B"], ["1", "2"]])

        out = module._extract_inline_payload(data, "d.docx", "")

        assert out["cs1"] == "docx"
        assert out["content"].split("\n") == ["Абзац один", "A\tB", "1\t2"]

    def test_table_rows_without_text_are_skipped(self):
        data = _docx(["Текст"], table=[["", ""], ["x", ""]])

        out = module._extract_inline_payload(data, "d.docx", "")

        assert out["content"].split(chr(10)) == ["Текст", "x"]

    def test_docm_keeps_its_own_marker(self):
        out = module._extract_inline_payload(_docx(["x"]), "d.docm", "")

        assert out["cs1"] == "docm" and out["content"] == "x"

    def test_empty_docx_has_a_stub(self):
        out = module._extract_inline_payload(_docx(), "d.docx", "")

        assert out["content"] == "(Документ не содержит текста)"

    def test_doc_that_is_really_docx_is_parsed_as_docx(self):
        out = module._extract_inline_payload(_docx(["внутри zip"]), "old.doc", "")

        assert out["cs1"] == "docx" and out["content"] == "внутри zip"

    def test_xlsx_sheets_and_rows(self):
        data = _xlsx({"Лист1": [["a", None, 3], [None, None, None]], "Второй": [["x"]]})

        out = module._extract_inline_payload(data, "t.xlsx", "")

        assert out["cs1"] == "xlsx"
        assert out["content"].split("\n") == ["=== Лист: Лист1 ===", "a\t\t3", "=== Лист: Второй ===", "x"]

    def test_xls_is_read_through_xlrd(self, monkeypatch):
        import xlrd

        def cell(v):
            return SimpleNamespace(value=v)

        rows = [[cell("a"), cell(""), cell(2.0)], [cell(""), cell(None)], [cell("z")]]
        sheet = SimpleNamespace(name="S", nrows=3, row=lambda i: rows[i])
        book = SimpleNamespace(nsheets=1, sheet_by_index=lambda i: sheet)
        seen = {}

        def open_workbook(file_contents):
            seen["c"] = file_contents
            return book

        monkeypatch.setattr(xlrd, "open_workbook", open_workbook)

        out = module._extract_inline_payload(b"BIFF", "t.xls", "")

        assert out == {"type": "text", "content": "=== Лист: S ===\na\t2.0\nz", "cs1": "xls"}
        assert seen["c"] == b"BIFF"

    def test_doc_uses_the_external_extractor(self, monkeypatch):
        monkeypatch.setattr(module, "extract_doc_text", lambda content, name: "текст doc" if name == "o.doc" else "")

        assert module._extract_inline_payload(b"\xd0\xcf", "o.doc", "")["content"] == "текст doc"

    def test_doc_without_text_has_a_stub(self, monkeypatch):
        monkeypatch.setattr(module, "extract_doc_text", lambda content, name: "")

        out = module._extract_inline_payload(b"\xd0\xcf", "o.doc", "")

        assert out["content"] == "(Документ не содержит текста)" and out["cs1"] == "doc"

    def test_ppt_goes_through_ppt_inline_text(self, monkeypatch):
        monkeypatch.setattr(module, "_ppt_inline_text", lambda content, name: "слайды")

        assert module._extract_inline_payload(b"x", "p.ppt", "") == {"type": "text", "content": "слайды", "cs1": "ppt"}

    def test_empty_ppt_has_a_stub(self, monkeypatch):
        monkeypatch.setattr(module, "_ppt_inline_text", lambda content, name: "")

        assert module._extract_inline_payload(b"x", "p.ppt", "")["content"] == "(Презентация не содержит текста)"

    def test_pptx_text(self):
        out = module._extract_inline_payload(_pptx("Привет, слайд"), "p.pptx", "")

        assert out["cs1"] == "pptx" and out["content"] == "[Слайд 1]\n\nПривет, слайд"

    def test_empty_pptx_has_a_stub(self, monkeypatch):
        monkeypatch.setattr(module, "collect_pptx", lambda data, n: ([], [], []))

        assert module._extract_inline_payload(b"x", "p.pptx", "")["content"] == "(Презентация не содержит текста)"

    def test_rtf_is_stripped(self):
        out = module._extract_inline_payload(rb"{\rtf1\ansi Hello world}", "a.rtf", "")

        assert out["cs1"] == "rtf" and "Hello world" in out["content"]

    def test_empty_rtf_has_a_stub(self):
        out = module._extract_inline_payload(rb"{\rtf1\ansi }", "a.rtf", "")

        assert out["content"] == "(RTF не содержит текста)"

    def test_doc_with_rtf_inside_is_parsed_as_rtf(self):
        out = module._extract_inline_payload(rb"{\rtf1\ansi Inside}", "a.doc", "")

        assert out["cs1"] == "rtf" and "Inside" in out["content"]

    def test_plain_text_replaces_bad_bytes(self):
        out = module._extract_inline_payload("Привет".encode("utf-8") + b"\xff", "a.txt", "text/plain")

        assert out == {"type": "text", "content": "Привет�", "cs1": "text"}


# ---------- _ppt_inline_text ----------


class TestPptInlineText:
    def _collect(self, monkeypatch, text_items, images=0, error=None):
        def collect(content, limit):
            if error:
                raise error
            return [text_items], [], [""], {"картинок": images, "слайдов": 1}

        monkeypatch.setattr(module, "collect_ppt", collect)

    def test_own_parser_result_is_used(self, monkeypatch):
        self._collect(monkeypatch, [("text", "Свой разбор")])
        monkeypatch.setattr(module, "extract_ppt_text", lambda *a: pytest.fail("catppt не должен вызываться"))

        assert module._ppt_inline_text(b"x", "a.ppt") == "[Слайд 1]\n\nСвой разбор"

    def test_pictures_only_still_returns_for_ocr(self, monkeypatch):
        self._collect(monkeypatch, [], images=2)
        monkeypatch.setattr(module, "extract_ppt_text", lambda *a: pytest.fail("catppt не должен вызываться"))

        assert module._ppt_inline_text(b"x", "a.ppt") == "[Слайд 1]"

    def test_nothing_found_falls_back_to_catppt(self, monkeypatch):
        self._collect(monkeypatch, [], images=0)
        monkeypatch.setattr(module, "extract_ppt_text", lambda data, name: f"catppt:{name}")

        assert module._ppt_inline_text(b"x", "a.ppt") == "catppt:a.ppt"

    def test_parser_crash_falls_back_to_catppt(self, monkeypatch):
        self._collect(monkeypatch, [], error=ValueError("битый файл"))
        monkeypatch.setattr(module, "extract_ppt_text", lambda data, name: "запасной")

        assert module._ppt_inline_text(b"x", "a.ppt") == "запасной"

    def test_catppt_failure_gives_an_empty_string(self, monkeypatch):
        self._collect(monkeypatch, [], error=ValueError("битый файл"))

        def broken(data, name):
            raise RuntimeError("catppt not installed")

        monkeypatch.setattr(module, "extract_ppt_text", broken)

        assert module._ppt_inline_text(b"x", "a.ppt") == ""


# ---------- _docx_row_texts ----------


class TestDocxRowTexts:
    def test_cells_are_joined_and_whitespace_is_collapsed(self):
        import docx

        doc = docx.Document(io.BytesIO(_docx(table=[["  a   b ", "c"]])))
        tr = doc.tables[0]._tbl.tr_lst[0]

        assert module._docx_row_texts(tr) == ["a b", "c"]


# ---------- каталог пода: сбор и дерево ----------


class TestPodTree:
    def test_collect_files_with_sizes_and_highlight(self, tmp_path):
        (tmp_path / "alice" / "2026").mkdir(parents=True)
        (tmp_path / "alice" / "2026" / "b.txt").write_bytes(b"12345")
        (tmp_path / "alice" / "a.txt").write_bytes(b"12")

        files, total = module._collect_inline_attach_files(str(tmp_path), highlight_object="alice\\a.txt")

        assert [f["name"] for f in files] == ["alice/2026/b.txt", "alice/a.txt"]
        assert total == 7
        assert [f["just_added"] for f in files] == [False, True]
        assert files[0]["size"] == 5 and files[0]["modified"].endswith("+00:00")

    def test_stat_failure_keeps_the_entry_without_size(self, tmp_path, monkeypatch):
        (tmp_path / "f.txt").write_bytes(b"abc")
        real_stat = os.stat

        def fake_stat(path, *a, **k):
            if str(path).endswith("f.txt"):
                raise OSError("denied")
            return real_stat(path, *a, **k)

        monkeypatch.setattr(module.os, "stat", fake_stat)

        files, total = module._collect_inline_attach_files(str(tmp_path))

        assert files == [{"name": "f.txt", "size": None, "modified": None, "just_added": False}] and total == 0

    def test_file_entry_detection(self):
        assert module._is_inline_attach_file_entry({"name": "x"}) is True
        assert module._is_inline_attach_file_entry({"other": 1}) is False
        assert module._is_inline_attach_file_entry("x") is False

    def test_nested_tree_shadows_a_file_that_is_also_a_directory(self):
        files = [
            {"name": "a", "size": 1},
            {"name": "a/b.txt", "size": 2},
            {"name": "c/d.txt", "size": 3},
        ]

        tree = module._build_inline_attach_nested_tree(files)

        assert tree["a"]["__shadow_file__"] == files[0]
        assert tree["a"]["b.txt"] == files[1]
        assert tree["c"]["d.txt"] == files[2]

    def test_render_empty(self):
        assert module._render_inline_attach_tree_lines([]) == ["  (empty)"]

    def test_render_tree_lines(self):
        files = [
            {"name": "alice/x.png", "size": 10, "just_added": True},
            {"name": "alice/sub/y.txt", "size": None, "just_added": False},
            {"name": "zed.txt", "size": 5, "just_added": False},
        ]

        lines = module._render_inline_attach_tree_lines(files)

        assert lines[0] == "  ├── alice/"
        assert any("sub/" in ln for ln in lines)
        assert any(ln.endswith("y.txt") for ln in lines)
        assert any(ln.endswith("x.png (10 B)  ← new") for ln in lines)
        assert lines[-1] == "  └── zed.txt (5 B)"

    def test_render_shadowed_file_uses_its_base_name(self):
        files = [{"name": "a", "size": 1}, {"name": "a/b.txt", "size": 2}]

        lines = module._render_inline_attach_tree_lines(files)

        assert lines == ["  └── a/", "      ├── a (1 B)", "      └── b.txt (2 B)"]

    def test_snapshot_without_a_configured_directory(self, monkeypatch):
        monkeypatch.delenv(module.INLINE_ATTACH_DIR_ENV, raising=False)

        snap = module._snapshot_inline_attach_pod_structure()

        assert snap["attachments_dir"] is None and snap["file_count"] == 0 and snap["files"] == []
        assert module.INLINE_ATTACH_DIR_ENV in snap["error"]

    def test_snapshot_of_a_real_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv(module.INLINE_ATTACH_DIR_ENV, str(tmp_path))
        (tmp_path / "u").mkdir()
        (tmp_path / "u" / "f.txt").write_bytes(b"abcd")

        snap = module._snapshot_inline_attach_pod_structure(highlight_object="u/f.txt")

        assert snap["file_count"] == 1 and snap["total_bytes"] == 4
        assert snap["highlight_object"] == "u/f.txt"
        assert snap["tree"].splitlines()[0] == f"{tmp_path}/"
        assert "f.txt (4 B)  ← new" in snap["tree"]

    def test_snapshot_survives_a_read_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv(module.INLINE_ATTACH_DIR_ENV, str(tmp_path))

        def broken(*a, **k):
            raise OSError("io error")

        monkeypatch.setattr(module, "_collect_inline_attach_files", broken)

        snap = module._snapshot_inline_attach_pod_structure()

        assert snap["error"] == "io error" and snap["files"] == [] and "ошибка чтения" in snap["tree"]

    def test_structure_logger_is_a_noop(self):
        assert module._log_inline_attach_pod_structure(filename="a.txt", highlight_object="x") is None


# ---------- запись/чтение вложений: сбои ----------


class TestPodFailures:
    def test_object_key_without_usable_parts_is_not_resolved(self, tmp_path, monkeypatch):
        monkeypatch.setenv(module.INLINE_ATTACH_DIR_ENV, str(tmp_path))

        assert module._inline_attach_pod_file_path("..") is None
        assert module._inline_attach_pod_file_path("") is None

    def test_text_layer_check_with_zero_minimum_and_no_characters(self):
        assert module._looks_like_real_text("", min_chars=0) is False
        assert module._looks_like_real_text("   ", min_chars=0) is False

    def test_write_error_is_swallowed(self, tmp_path, monkeypatch, rec_logger):
        monkeypatch.setenv(module.INLINE_ATTACH_DIR_ENV, str(tmp_path))
        real_open = open

        def fake_open(path, mode="r", *a, **k):
            if "wb" in mode and "doc_" in str(path):
                raise OSError("disk full")
            return real_open(path, mode, *a, **k)

        monkeypatch.setattr("builtins.open", fake_open)

        assert module._try_save_bytes_to_pod_attachments(b"x", "a.txt", "text/plain", username="alice") is None
        assert any(r[0] == "exception" for r in rec_logger.records)

    def test_file_without_extension_gets_bin(self, tmp_path, monkeypatch):
        monkeypatch.setenv(module.INLINE_ATTACH_DIR_ENV, str(tmp_path))

        key = module._try_save_bytes_to_pod_attachments(b"x", "README", "", username="alice")

        assert key.endswith(".bin")

    def test_read_error_of_an_existing_pod_file(self, tmp_path, monkeypatch, rec_logger):
        monkeypatch.setenv(module.INLINE_ATTACH_DIR_ENV, str(tmp_path))
        (tmp_path / "f.txt").write_bytes(b"x")
        real_open = open

        def fake_open(path, mode="r", *a, **k):
            if str(path).endswith("f.txt") and "rb" in mode:
                raise PermissionError("denied")
            return real_open(path, mode, *a, **k)

        monkeypatch.setattr("builtins.open", fake_open)

        assert module.read_inline_attachment_bytes(module.INLINE_ATTACH_POD_TMP_BUCKET, "f.txt") is None
        assert any(r[0] == "exception" for r in rec_logger.records)

    def test_minio_returning_nothing_means_no_file(self, monkeypatch):
        monkeypatch.setattr(module, "minio_client", SimpleNamespace(download_file=lambda name, bucket_name=None: None))

        assert module.read_inline_attachment_bytes("docs", "a.pdf") is None

    def test_required_dir_missing_logs_an_error(self, monkeypatch, rec_logger):
        monkeypatch.delenv(module.INLINE_ATTACH_DIR_ENV, raising=False)

        assert module._inline_attach_pod_dir() is None
        assert rec_logger.records[0][0] == "error"

    def test_sanitize_logs_a_normalised_login(self, rec_logger):
        assert module._sanitize_inline_attach_user_dir("a b") == "ab"
        assert module._sanitize_inline_attach_user_dir("иван") == "anonymous"
        assert [r[0] for r in rec_logger.records] == ["debug", "warning"]


# ---------- MinIO ----------


class FakeMinio:
    def __init__(self, fail=False):
        self.fail = fail
        self.uploads = []

    def generate_object_name(self, prefix, extension):
        return f"{prefix}obj{extension}"

    def upload_file(self, content, name, **kw):
        if self.fail:
            raise RuntimeError("minio down")
        self.uploads.append((content, name, kw))


class TestMinioUpload:
    def test_without_minio_nothing_is_stored(self, monkeypatch):
        monkeypatch.setattr(module, "minio_client", None)

        assert module._try_upload_bytes_to_minio(None, b"x", "a.pdf", "application/pdf") == (None, None)

    def test_document_and_image_prefixes(self, monkeypatch):
        fake = FakeMinio()
        monkeypatch.setattr(module, "minio_client", fake)
        monkeypatch.setattr(module, "_documents_bucket_name", lambda: "docs")

        doc = module._try_upload_bytes_to_minio(None, b"x", "a.pdf", "application/pdf")
        img = module._try_upload_bytes_to_minio(None, b"y", "pic.bin", "image/png")
        raw = module._try_upload_bytes_to_minio(None, b"z", "noext", "text/plain")

        assert doc == ("doc_obj.pdf", "docs") and img == ("img_obj.bin", "docs") and raw == ("doc_obj.bin", "docs")
        assert fake.uploads[0][2] == {"content_type": "application/pdf", "bucket_name": "docs", "cef_display_name": "a.pdf"}

    def test_upload_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(module, "minio_client", FakeMinio(fail=True))

        assert module._try_upload_bytes_to_minio(None, b"x", "a.pdf", "application/pdf") == (None, None)


class TestDocumentsBucketName:
    def test_prefers_the_documents_bucket(self, monkeypatch):
        monkeypatch.setattr(module, "settings", SimpleNamespace(minio=SimpleNamespace(documents_bucket_name="docs", bucket_name="main")))

        assert module._documents_bucket_name() == "docs"

    def test_falls_back_to_the_main_bucket(self, monkeypatch):
        monkeypatch.setattr(module, "settings", SimpleNamespace(minio=SimpleNamespace(documents_bucket_name="", bucket_name="main")))

        assert module._documents_bucket_name() == "main"

    def test_environment_and_default(self, monkeypatch):
        monkeypatch.setattr(module, "settings", SimpleNamespace(minio=None))
        monkeypatch.delenv("MINIO_DOCUMENTS_BUCKET_NAME", raising=False)
        monkeypatch.delenv("MINIO_BUCKET_NAME", raising=False)
        assert module._documents_bucket_name() == "astrachat-documents"
        monkeypatch.setenv("MINIO_BUCKET_NAME", "env-main")
        assert module._documents_bucket_name() == "env-main"
        monkeypatch.setenv("MINIO_DOCUMENTS_BUCKET_NAME", "env-docs")
        assert module._documents_bucket_name() == "env-docs"

    def test_empty_bucket_names_go_to_the_environment(self, monkeypatch):
        monkeypatch.setattr(module, "settings", SimpleNamespace(minio=SimpleNamespace(documents_bucket_name=None, bucket_name=None)))
        monkeypatch.setenv("MINIO_DOCUMENTS_BUCKET_NAME", "from-env")

        assert module._documents_bucket_name() == "from-env"


# ---------- служебные логи ----------


class TestAttachLogging:
    def test_info_and_success(self, rec_logger):
        module._emit_attach_info("hello")
        module._log_inline_attach_upload_success("a.pdf")

        assert rec_logger.records == [("info", "hello", ()), ("info", "файл %s загружен успешно", ("a.pdf",))]

    def test_failure_sends_a_cef_event_with_the_reject_code(self, rec_logger, cef_events):
        events = cef_events(module)
        request = object()

        module._log_inline_attach_upload_failure(
            request, "a.exe", "нельзя", file_size=12, status_code=415, reject_code="unsupported_format"
        )

        ((code, kwargs),) = events
        assert code == "FS008" and kwargs["request"] is request and kwargs["status_code"] == 415
        assert kwargs["extra"] == {
            "fname": "a.exe",
            "reason": "нельзя",
            "fsize": 12,
            "cs1": "unsupported_format",
            "cs1Label": "RejectReason",
        }

    def test_failure_without_optional_fields(self, rec_logger, cef_events):
        events = cef_events(module)

        module._log_inline_attach_upload_failure(object(), "a", "причина")

        assert events[0][1]["extra"] == {"fname": "a", "reason": "причина"}
        assert events[0][1]["status_code"] == 400

    def test_debug_payload_is_json(self, rec_logger):
        module._log_inline_attachment_debug("stage", {"filename": "ф.txt"}, extra=1)

        ((level, _msg, args),) = rec_logger.records
        assert level == "debug" and '"stage": "stage"' in args[0] and "ф.txt" in args[0] and '"extra": 1' in args[0]

    def test_request_start_log_with_and_without_client(self, rec_logger):
        with_client = SimpleNamespace(
            headers={"content-length": "5", "content-type": "x/y"}, client=SimpleNamespace(host="10.0.0.1")
        )
        without = SimpleNamespace(headers={}, client=None)

        module._log_chat_attach_request(with_client, upload_user="alice")
        module._log_chat_attach_request(without)

        first, second = (r[2][0] for r in rec_logger.records)
        assert '"client_host": "10.0.0.1"' in first and '"upload_user": "alice"' in first
        assert '"content_length_header": "5"' in first
        assert '"client_host": null' in second
