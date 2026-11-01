"""routes/documents: определение vision-провайдера, OCR картинок/PDF/DOCX/XLSX/PPTX, извлечение картинок из PDF."""

import io
import sys
from types import SimpleNamespace

import pytest

import backend.agent_llm_svc as llm_svc_module
import backend.app_state as app_state
from backend.routes import documents as module
from backend.tests.api.cov_b2.test_documents_extract import _bmp, _docx, _pdf, _png, _pptx, _xlsx

pytestmark = pytest.mark.unit

LONG_TEXT = "This is a perfectly normal english text layer extracted from a PDF."


def _noise_png(size=(200, 200)):
    from PIL import Image

    buf = io.BytesIO()
    Image.effect_noise(size, 80).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _pdf_with_images(text, images):
    """PDF с текстовым слоем и вставленными картинками (гибридная страница)."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    for i, blob in enumerate(images):
        page.insert_image(fitz.Rect(72, 100 + i * 210, 272, 300 + i * 210), stream=blob)
    data = doc.tobytes()
    doc.close()
    return data


# Собираем заранее: в TestPdfiumFallback fitz скрыт (sys.modules["fitz"] = None).
HYBRID_PDF = _pdf_with_images(LONG_TEXT, [_noise_png()])


class OcrClient:
    """Фейковый клиент OCR: отвечает «OCR[<имя>]», на выбранные имена падает."""

    def __init__(self, ocr_available=True, available=True, fail=(), empty=()):
        self.ocr_available = ocr_available
        self.available = available
        self.fail = tuple(fail)
        self.empty = tuple(empty)
        self.calls = []

    async def recognize_text_from_image(self, blob, name):
        self.calls.append((name, len(blob)))
        if any(f in name for f in self.fail):
            raise RuntimeError("ocr boom")
        if any(e in name for e in self.empty):
            return {"text": "  "}
        return {"text": f"OCR[{name}]"}


@pytest.fixture
def ocr(monkeypatch):
    """``ocr(client)`` подменяет get_llm_service, который documents берёт лениво."""

    def install(client):
        async def get_llm_service():
            return SimpleNamespace(client=client)

        monkeypatch.setattr(llm_svc_module, "get_llm_service", get_llm_service)
        return client

    return install


@pytest.fixture
def vision(monkeypatch):
    """``vision(True/False/Exception)`` — что вернёт определение vision-провайдера."""

    def install(value):
        async def provider_vision():
            if isinstance(value, Exception):
                raise value
            return "prov", value

        monkeypatch.setattr(module, "_attachment_provider_vision", provider_vision)

    return install


# ---------- _attachment_provider_vision ----------


class TestProviderVision:
    @pytest.fixture
    def registry(self, monkeypatch):
        import backend.llm_providers as llm_providers

        state = SimpleNamespace(provider=None, resolved=[])

        class Registry:
            def resolve(self, path):
                state.resolved.append(path)
                return state.provider, "model"

        async def get_registry():
            return Registry()

        monkeypatch.setattr(llm_providers, "get_registry", get_registry)
        monkeypatch.setattr(app_state, "get_current_model_path", lambda: None)
        return state

    @staticmethod
    def _provider(extra=None, kind="openai-compat", vision=True, config=True):
        cfg = SimpleNamespace(extra=extra) if config else None
        return SimpleNamespace(id="p1", config=cfg, kind=kind, capabilities=SimpleNamespace(vision=vision))

    async def test_config_flag_wins_and_empty_path_means_default_model(self, registry):
        registry.provider = self._provider(extra={"vision": 0}, vision=True)

        assert await module._attachment_provider_vision() == ("p1", False)
        assert registry.resolved == [""]

    async def test_config_flag_true(self, registry):
        registry.provider = self._provider(extra={"vision": True}, kind="llm-svc", vision=False)

        assert await module._attachment_provider_vision() == ("p1", True)

    async def test_llm_svc_without_a_flag_is_text_only(self, registry):
        registry.provider = self._provider(extra={}, kind="llm-svc", vision=True)

        assert await module._attachment_provider_vision() == ("p1", False)

    async def test_other_providers_use_class_capability(self, registry):
        registry.provider = self._provider(extra=None, vision=True, config=False)

        assert await module._attachment_provider_vision() == ("p1", True)

    async def test_current_model_path_is_passed_to_the_registry(self, registry, monkeypatch):
        registry.provider = self._provider(extra={"vision": True})
        monkeypatch.setattr(app_state, "get_current_model_path", lambda: "gpt/vision")

        await module._attachment_provider_vision()

        assert registry.resolved == ["gpt/vision"]


# ---------- _analyze_inline_attachment_for_model ----------


class TestAnalyzeAttachment:
    @pytest.fixture(autouse=True)
    def _model_path(self, monkeypatch):
        monkeypatch.setattr(app_state, "get_current_model_path", lambda: "m/path")

    async def test_allowed_document(self, vision):
        vision(False)

        out = await module._analyze_inline_attachment_for_model("a.pdf", "application/pdf", 10)

        assert out["allowed"] is True and out["kind"] == "document" and out["model_compatible"] is True
        assert (out["provider_id"], out["model_vision"], out["model_path"]) == ("prov", False, "m/path")
        assert "reject_reason" not in out and "incompatibility_reason" not in out

    async def test_image_for_text_only_model_is_allowed_but_flagged(self, vision):
        vision(False)

        out = await module._analyze_inline_attachment_for_model("a.png", "image/png", 10)

        assert out["allowed"] is True and out["model_compatible"] is False
        assert "vision" in out["incompatibility_reason"]

    async def test_image_for_vision_model(self, vision):
        vision(True)

        out = await module._analyze_inline_attachment_for_model("a.png", "image/png", 10)

        assert out["model_compatible"] is True and out["model_vision"] is True

    async def test_unsupported_format_is_rejected_with_the_list_of_extensions(self, vision):
        vision(True)

        out = await module._analyze_inline_attachment_for_model("a.exe", "application/octet-stream", 10)

        assert out["allowed"] is False and out["format_allowed"] is False
        assert out["reject_reason"].startswith("Неподдерживаемый формат (") and ".pdf" in out["reject_reason"]

    async def test_oversized_file_is_rejected(self, vision):
        vision(True)

        out = await module._analyze_inline_attachment_for_model("a.pdf", "application/pdf", module.INLINE_ATTACHMENT_MAX_BYTES + 1)

        assert out["allowed"] is False and out["size_allowed"] is False
        assert out["reject_reason"] == "Размер превышает 20MB"

    async def test_limit_is_inclusive(self, vision):
        vision(True)

        out = await module._analyze_inline_attachment_for_model("a.pdf", "application/pdf", module.INLINE_ATTACHMENT_MAX_BYTES)

        assert out["allowed"] is True

    async def test_provider_failure_does_not_block_the_upload(self, vision):
        vision(RuntimeError("registry down"))

        out = await module._analyze_inline_attachment_for_model("a.png", "image/png", 10)

        assert out["allowed"] is True and out["provider_id"] is None and out["model_vision"] is None
        assert out["model_compatible"] is True


# ---------- _ocr_image_payload_if_needed ----------


class TestOcrImage:
    PAYLOAD = {"type": "image", "content": "data:image/png;base64,AAA", "cs1": "image"}

    async def test_text_payload_is_untouched(self):
        payload = {"type": "text", "content": "x", "cs1": "text"}

        assert await module._ocr_image_payload_if_needed(b"x", "a.txt", payload, None) == (payload, None)

    async def test_vision_model_gets_the_picture_itself(self, vision, ocr):
        vision(True)
        client = ocr(OcrClient())

        assert await module._ocr_image_payload_if_needed(b"x", "a.png", self.PAYLOAD, None) == (self.PAYLOAD, None)
        assert client.calls == []

    async def test_text_model_gets_the_recognised_text(self, vision, ocr):
        vision(False)
        client = ocr(OcrClient())

        payload, warning = await module._ocr_image_payload_if_needed(b"img", "a.png", self.PAYLOAD, None)

        assert warning is None and payload["cs1"] == "image-ocr" and payload["type"] == "text"
        assert payload["content"] == "[Текст с изображения «a.png» (OCR)]:\nOCR[a.png]"
        assert client.calls == [("a.png", 3)]

    async def test_routing_failure_defaults_to_ocr(self, vision, ocr):
        vision(RuntimeError("no registry"))
        ocr(OcrClient())

        payload, _ = await module._ocr_image_payload_if_needed(b"img", "a.png", self.PAYLOAD, None)

        assert payload["cs1"] == "image-ocr"

    async def test_ocr_not_configured(self, vision, ocr):
        vision(False)
        ocr(OcrClient(ocr_available=False))

        payload, warning = await module._ocr_image_payload_if_needed(b"img", "a.png", self.PAYLOAD, None)

        assert payload is self.PAYLOAD and "OCR-сервис не настроен" in warning

    async def test_empty_ocr_result(self, vision, ocr):
        vision(False)
        ocr(OcrClient(empty=("a.png",)))

        payload, warning = await module._ocr_image_payload_if_needed(b"img", "a.png", self.PAYLOAD, None)

        assert payload["cs1"] == "image-ocr-empty" and "не нашёл текста" in payload["content"]
        assert warning == "На изображении не обнаружен текст (OCR)"

    async def test_ocr_failure_keeps_the_payload_and_explains(self, vision, ocr):
        vision(False)
        ocr(OcrClient(fail=("a.png",)))

        payload, warning = await module._ocr_image_payload_if_needed(b"img", "a.png", self.PAYLOAD, None)

        assert payload is self.PAYLOAD and warning.startswith("OCR не удался: ocr boom")


# ---------- _ocr_pdf_payload_if_needed ----------


class TestOcrPdf:
    PAYLOAD = {"type": "text", "content": "(PDF не содержит извлекаемого текста)", "cs1": "pdf"}

    async def test_other_payloads_are_untouched(self):
        payload = {"type": "text", "content": "x", "cs1": "docx"}

        assert await module._ocr_pdf_payload_if_needed(b"x", "a.docx", payload) == (payload, None)

    async def test_ocr_not_configured(self, ocr):
        ocr(OcrClient(ocr_available=False))

        payload, warning = await module._ocr_pdf_payload_if_needed(_pdf([""]), "a.pdf", self.PAYLOAD)

        assert payload is self.PAYLOAD and warning == "OCR-сервис не настроен — PDF-скан не распознать"

    async def test_scanned_page_is_rendered_and_recognised(self, ocr):
        client = ocr(OcrClient())

        payload, warning = await module._ocr_pdf_payload_if_needed(_pdf([""]), "scan.pdf", self.PAYLOAD)

        assert warning is None and payload["cs1"] == "pdf-ocr" and "pages_map" not in payload
        assert payload["content"] == "[Текст из PDF «scan.pdf»]:\n[Страница 1 (OCR)]\nOCR[scan.pdf.p1.png]"
        assert [c[0] for c in client.calls] == ["scan.pdf.p1.png"]

    async def test_text_layer_is_used_without_ocr(self, ocr):
        client = ocr(OcrClient())

        payload, warning = await module._ocr_pdf_payload_if_needed(_pdf([LONG_TEXT]), "t.pdf", self.PAYLOAD)

        assert warning is None and payload["cs1"] == "pdf"
        assert payload["content"].startswith("[Текст из PDF «t.pdf»]:\n[Страница 1]\n" + LONG_TEXT[:20])
        assert client.calls == []

    async def test_pages_keep_their_order_whatever_the_ocr_finishes_first(self, ocr):
        client = ocr(OcrClient())
        data = _pdf([LONG_TEXT, "", LONG_TEXT, ""])

        payload, _ = await module._ocr_pdf_payload_if_needed(data, "m.pdf", self.PAYLOAD)

        markers = [ln for ln in payload["content"].splitlines() if ln.startswith("[Страница")]
        assert markers == ["[Страница 1]", "[Страница 2 (OCR)]", "[Страница 3]", "[Страница 4 (OCR)]"]
        assert sorted(c[0] for c in client.calls) == ["m.pdf.p2.png", "m.pdf.p4.png"]

    async def test_hybrid_page_gets_the_text_of_its_pictures(self, ocr):
        client = ocr(OcrClient())
        data = _pdf_with_images(LONG_TEXT, [_noise_png()])

        payload, _ = await module._ocr_pdf_payload_if_needed(data, "h.pdf", self.PAYLOAD)

        assert payload["cs1"] == "pdf-ocr"
        assert "[Текст с изображений страницы (OCR)]:\nOCR[h.pdf.p1.img1.png]" in payload["content"]
        assert [c[0] for c in client.calls] == ["h.pdf.p1.img1.png"]

    async def test_too_many_pictures_disable_picture_ocr(self, ocr, monkeypatch):
        client = ocr(OcrClient())
        monkeypatch.setenv("PDF_OCR_INLINE_IMAGES_MAX", "0")
        data = _pdf_with_images(LONG_TEXT, [_noise_png()])

        payload, _ = await module._ocr_pdf_payload_if_needed(data, "h.pdf", self.PAYLOAD)

        assert payload["cs1"] == "pdf" and client.calls == []

    async def test_failing_picture_ocr_is_skipped(self, ocr):
        ocr(OcrClient(fail=("img1",)))
        data = _pdf_with_images(LONG_TEXT, [_noise_png()])

        payload, warning = await module._ocr_pdf_payload_if_needed(data, "h.pdf", self.PAYLOAD)

        assert warning is None and payload["cs1"] == "pdf" and "изображений страницы" not in payload["content"]

    async def test_picture_extraction_failure_is_not_fatal(self, ocr, monkeypatch):
        ocr(OcrClient())

        def broken(content):
            raise RuntimeError("fitz crashed")

        monkeypatch.setattr(module, "_extract_pdf_page_images", broken)

        payload, warning = await module._ocr_pdf_payload_if_needed(_pdf([LONG_TEXT]), "t.pdf", self.PAYLOAD)

        assert warning is None and payload["cs1"] == "pdf"

    async def test_failing_page_ocr_leaves_nothing_to_return(self, ocr):
        ocr(OcrClient(fail=(".p1.png",)))

        payload, warning = await module._ocr_pdf_payload_if_needed(_pdf([""]), "s.pdf", self.PAYLOAD)

        assert warning == "В PDF не обнаружен текст" and payload["cs1"] == "pdf-ocr-empty"
        assert "не удалось извлечь текст" in payload["content"]

    async def test_blank_ocr_result_on_one_page_is_just_skipped(self, ocr):
        ocr(OcrClient(empty=(".p2.png",)))

        payload, _ = await module._ocr_pdf_payload_if_needed(_pdf([LONG_TEXT, ""]), "s.pdf", self.PAYLOAD)

        assert payload["cs1"] == "pdf" and "[Страница 2" not in payload["content"]

    async def test_unreadable_text_layer_falls_back_to_ocr(self, ocr, monkeypatch):
        import PyPDF2

        ocr(OcrClient())

        class Page:
            def extract_text(self):
                raise ValueError("bad font")

        class Reader:
            def __init__(self, stream):
                self.pages = [Page()]

        monkeypatch.setattr(PyPDF2, "PdfReader", Reader)

        payload, _ = await module._ocr_pdf_payload_if_needed(_pdf([LONG_TEXT]), "s.pdf", self.PAYLOAD)

        assert payload["cs1"] == "pdf-ocr" and "OCR[s.pdf.p1.png]" in payload["content"]

    async def test_rag_call_returns_a_page_map_for_the_requested_pages(self, ocr):
        client = ocr(OcrClient())
        data = _pdf([LONG_TEXT, "", ""])

        payload, _ = await module._ocr_pdf_payload_if_needed(data, "r.pdf", self.PAYLOAD, page_indices=[2, 1, 1, 0, 9, -1])

        assert sorted(payload["pages_map"]) == ["0", "1", "2"]
        assert payload["pages_map"]["0"].startswith("This is a perfectly")
        assert payload["pages_map"]["2"] == "OCR[r.pdf.p3.png]"
        assert sorted(c[0] for c in client.calls) == ["r.pdf.p2.png", "r.pdf.p3.png"]

    async def test_rag_call_limited_to_scan_pages(self, ocr):
        client = ocr(OcrClient())
        data = _pdf([LONG_TEXT, "", ""])

        payload, _ = await module._ocr_pdf_payload_if_needed(data, "r.pdf", self.PAYLOAD, page_indices=[1])

        assert list(payload["pages_map"]) == ["1"]
        assert payload["content"] == "[Текст из PDF «r.pdf»]:\n[Страница 2 (OCR)]\nOCR[r.pdf.p2.png]"
        assert [c[0] for c in client.calls] == ["r.pdf.p2.png"]

    async def test_rag_call_without_any_text_reports_empty(self, ocr):
        ocr(OcrClient(empty=("p",)))

        payload, warning = await module._ocr_pdf_payload_if_needed(_pdf([""]), "r.pdf", self.PAYLOAD, page_indices=[0])

        assert payload["cs1"] == "pdf-ocr-empty" and warning == "В PDF не обнаружен текст"

    async def test_broken_pdf_is_reported_not_raised(self, ocr):
        ocr(OcrClient())

        payload, warning = await module._ocr_pdf_payload_if_needed(b"not a pdf", "x.pdf", self.PAYLOAD)

        assert payload is self.PAYLOAD and warning.startswith("OCR PDF не удался:")


# ---------- _ocr_docx_payload_if_needed ----------


class TestOcrDocx:
    def _payload(self, content="Текст документа", cs1="docx"):
        return {"type": "text", "content": content, "cs1": cs1}

    def _docx_with_picture(self, paragraphs=("Текст документа",)):
        import docx

        doc = docx.Document()
        for p in paragraphs:
            doc.add_paragraph(p)
        doc.add_picture(io.BytesIO(_png()))
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    async def test_other_payloads_are_untouched(self):
        payload = self._payload(cs1="pdf")

        assert await module._ocr_docx_payload_if_needed(b"x", "a.docx", payload) == (payload, None)

    async def test_document_without_pictures(self, ocr):
        client = ocr(OcrClient())
        payload = self._payload()

        assert await module._ocr_docx_payload_if_needed(_docx(["Текст"]), "a.docx", payload) == (payload, None)
        assert client.calls == []

    async def test_ocr_not_configured(self, ocr):
        ocr(OcrClient(ocr_available=False))
        payload = self._payload()

        assert await module._ocr_docx_payload_if_needed(self._docx_with_picture(), "a.docx", payload) == (payload, None)

    async def test_picture_text_is_appended(self, ocr):
        client = ocr(OcrClient())

        out, warning = await module._ocr_docx_payload_if_needed(self._docx_with_picture(), "a.docx", self._payload())

        assert warning is None and out["cs1"] == "docx-ocr"
        assert out["content"] == "Текст документа\n\n[Текст из изображений документа «a.docx» (OCR)]:\nOCR[a.docx.img1.png]"
        assert [c[0] for c in client.calls] == ["a.docx.img1.png"]

    async def test_stub_of_an_empty_document_is_dropped(self, ocr):
        ocr(OcrClient())
        payload = self._payload("(Документ не содержит текста)", cs1="docm")

        out, _ = await module._ocr_docx_payload_if_needed(self._docx_with_picture(()), "a.docm", payload)

        assert out["content"] == "[Текст из изображений документа «a.docm» (OCR)]:\nOCR[a.docm.img1.png]"

    async def test_empty_ocr_results_keep_the_payload(self, ocr):
        ocr(OcrClient(empty=("img1",)))
        payload = self._payload()

        assert await module._ocr_docx_payload_if_needed(self._docx_with_picture(), "a.docx", payload) == (payload, None)

    async def test_failed_picture_is_skipped(self, ocr):
        ocr(OcrClient(fail=("img1",)))
        payload = self._payload()

        assert await module._ocr_docx_payload_if_needed(self._docx_with_picture(), "a.docx", payload) == (payload, None)

    async def test_unreadable_picture_part_is_skipped(self, ocr, monkeypatch):
        import docx

        client = ocr(OcrClient())

        class Rel:
            reltype = "http://x/image"

            @property
            def target_part(self):
                raise KeyError("dangling")

        class Good:
            reltype = "http://x/image"
            target_part = SimpleNamespace(blob=b"png")

        class NoType:
            reltype = None
            target_part = SimpleNamespace(blob=b"zzz")

        fake = SimpleNamespace(part=SimpleNamespace(rels={"a": Rel(), "b": Good(), "c": NoType()}))
        monkeypatch.setattr(docx, "Document", lambda stream: fake)

        out, _ = await module._ocr_docx_payload_if_needed(b"x", "a.docx", self._payload(""))

        assert out["content"] == "[Текст из изображений документа «a.docx» (OCR)]:\nOCR[a.docx.img1.png]"
        assert client.calls == [("a.docx.img1.png", 3)]

    async def test_broken_file_keeps_the_payload(self, ocr):
        ocr(OcrClient())
        payload = self._payload()

        assert await module._ocr_docx_payload_if_needed(b"not a zip", "a.docx", payload) == (payload, None)


# ---------- _ocr_xlsx_payload_if_needed ----------


class TestOcrXlsx:
    def _payload(self, content="=== Лист: S ===\na", cs1="xlsx"):
        return {"type": "text", "content": content, "cs1": cs1}

    def _xlsx_with_picture(self):
        import openpyxl
        from openpyxl.drawing.image import Image as XlImage

        wb = openpyxl.Workbook()
        wb.active.append(["a"])
        wb.active.add_image(XlImage(io.BytesIO(_png())), "C3")
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    async def test_other_payloads_are_untouched(self):
        payload = self._payload(cs1="xls")

        assert await module._ocr_xlsx_payload_if_needed(b"x", "a.xlsx", payload) == (payload, None)

    async def test_workbook_without_pictures(self, ocr):
        client = ocr(OcrClient())
        payload = self._payload()

        assert await module._ocr_xlsx_payload_if_needed(_xlsx({"S": [["a"]]}), "a.xlsx", payload) == (payload, None)
        assert client.calls == []

    async def test_picture_text_is_appended(self, ocr):
        client = ocr(OcrClient())

        out, warning = await module._ocr_xlsx_payload_if_needed(self._xlsx_with_picture(), "a.xlsx", self._payload())

        assert warning is None and out["cs1"] == "xlsx-ocr"
        assert out["content"] == "=== Лист: S ===\na\n\n[Текст из изображений таблицы «a.xlsx» (OCR)]:\nOCR[a.xlsx.img1.png]"
        assert [c[0] for c in client.calls] == ["a.xlsx.img1.png"]

    async def test_stub_of_an_empty_table_is_dropped(self, ocr):
        ocr(OcrClient())

        out, _ = await module._ocr_xlsx_payload_if_needed(self._xlsx_with_picture(), "a.xlsx", self._payload("(Таблица не содержит данных)"))

        assert out["content"] == "[Текст из изображений таблицы «a.xlsx» (OCR)]:\nOCR[a.xlsx.img1.png]"

    async def test_service_unavailable(self, ocr):
        ocr(OcrClient(available=False))
        payload = self._payload()

        assert await module._ocr_xlsx_payload_if_needed(self._xlsx_with_picture(), "a.xlsx", payload) == (payload, None)

    async def test_empty_and_failed_recognition_keep_the_payload(self, ocr):
        payload = self._payload()
        ocr(OcrClient(empty=("img1",)))
        assert await module._ocr_xlsx_payload_if_needed(self._xlsx_with_picture(), "a.xlsx", payload) == (payload, None)
        ocr(OcrClient(fail=("img1",)))
        assert await module._ocr_xlsx_payload_if_needed(self._xlsx_with_picture(), "a.xlsx", payload) == (payload, None)

    async def test_odd_image_objects_are_skipped(self, ocr, monkeypatch):
        import openpyxl

        client = ocr(OcrClient())

        def boom():
            raise ValueError("no data")

        ws = SimpleNamespace(_images=[SimpleNamespace(_data=boom), SimpleNamespace(_data=None), SimpleNamespace(_data=lambda: b""), SimpleNamespace(_data=lambda: b"png")])
        monkeypatch.setattr(openpyxl, "load_workbook", lambda stream: SimpleNamespace(worksheets=[ws]))

        out, _ = await module._ocr_xlsx_payload_if_needed(b"x", "a.xlsx", self._payload(""))

        assert client.calls == [("a.xlsx.img1.png", 3)] and out["cs1"] == "xlsx-ocr"

    async def test_broken_file_keeps_the_payload(self, ocr):
        ocr(OcrClient())
        payload = self._payload()

        assert await module._ocr_xlsx_payload_if_needed(b"not a zip", "a.xlsx", payload) == (payload, None)


# ---------- _ocr_pptx_payload_if_needed ----------


class TestOcrPptx:
    def _payload(self, cs1="pptx"):
        return {"type": "text", "content": "[Слайд 1]", "cs1": cs1}

    async def test_other_payloads_are_untouched(self):
        payload = self._payload("docx")

        assert await module._ocr_pptx_payload_if_needed(b"x", "a.pptx", payload) == (payload, None)

    async def test_picture_text_takes_the_place_of_the_picture(self, ocr):
        client = ocr(OcrClient())

        out, warning = await module._ocr_pptx_payload_if_needed(_pptx("Привет", with_image=True), "p.pptx", self._payload())

        assert warning is None and out["cs1"] == "pptx-ocr"
        assert out["content"] == "[Слайд 1]\n\nПривет\n\n[Текст с изображения (OCR)]: OCR[p.pptx.slide1_img1.png]"
        assert [c[0] for c in client.calls] == ["p.pptx.slide1_img1.png"]

    async def test_presentation_without_pictures(self, ocr):
        client = ocr(OcrClient())
        payload = self._payload()

        assert await module._ocr_pptx_payload_if_needed(_pptx("x"), "p.pptx", payload) == (payload, None)
        assert client.calls == []

    async def test_ocr_not_configured(self, ocr):
        ocr(OcrClient(ocr_available=False))
        payload = self._payload()

        assert await module._ocr_pptx_payload_if_needed(_pptx(with_image=True), "p.pptx", payload) == (payload, None)

    async def test_empty_and_failed_recognition_keep_the_payload(self, ocr):
        payload = self._payload()
        ocr(OcrClient(empty=("img1",)))
        assert await module._ocr_pptx_payload_if_needed(_pptx(with_image=True), "p.pptx", payload) == (payload, None)
        ocr(OcrClient(fail=("img1",)))
        assert await module._ocr_pptx_payload_if_needed(_pptx(with_image=True), "p.pptx", payload) == (payload, None)

    async def test_ppt_pictures_are_converted_to_png_before_ocr(self, ocr, monkeypatch):
        client = ocr(OcrClient())
        images = [("slide1_img1.bmp", _bmp()), ("slide1_img2.tif", b"garbage"), ("slide2_img1.jpg", b"jpeg-bytes")]
        per_slide = [[("img", "slide1_img1.bmp"), ("img", "slide1_img2.tif")], [("img", "slide2_img1.jpg")]]
        monkeypatch.setattr(module, "collect_ppt", lambda data, n: (per_slide, images, ["", ""], {}))

        out, _ = await module._ocr_pptx_payload_if_needed(b"x", "old.ppt", self._payload("ppt"))

        assert out["cs1"] == "ppt-ocr"
        assert [c[0] for c in client.calls] == ["old.ppt.slide1_img1.png", "old.ppt.slide2_img1.jpg"]
        assert "OCR[old.ppt.slide1_img1.png]" in out["content"]
        assert "slide1_img2" not in out["content"]

    async def test_parser_failure_keeps_the_payload(self, ocr, monkeypatch):
        ocr(OcrClient())

        def broken(data, n):
            raise ValueError("bad zip")

        monkeypatch.setattr(module, "collect_pptx", broken)
        payload = self._payload()

        assert await module._ocr_pptx_payload_if_needed(b"x", "p.pptx", payload) == (payload, None)


# ---------- _extract_pdf_page_images ----------


class FakeFitzPage:
    def __init__(self, infos):
        self.infos = infos

    def get_image_info(self, xrefs=False):
        if isinstance(self.infos, Exception):
            raise self.infos
        return self.infos


class FakeFitzDoc:
    def __init__(self, pages, images):
        self.pages = pages
        self.images = images
        self.closed = False
        self.page_count = len(pages)

    def load_page(self, idx):
        return self.pages[idx]

    def extract_image(self, xref):
        value = self.images[xref]
        if isinstance(value, Exception):
            raise value
        return {"image": value}

    def close(self):
        self.closed = True


class TestExtractPdfPageImages:
    def test_real_pdf_gives_png_for_big_pictures_only(self):
        data = _pdf_with_images(LONG_TEXT, [_noise_png((200, 200)), _noise_png((20, 20))])

        out = module._extract_pdf_page_images(data)

        assert list(out) == [0] and [seq for seq, _ in out[0]] == [0]
        assert out[0][0][1].startswith(b"\x89PNG")

    def test_pdf_without_pictures(self):
        assert module._extract_pdf_page_images(_pdf([LONG_TEXT])) == {}

    def test_garbage_gives_an_empty_result(self):
        assert module._extract_pdf_page_images(b"not a pdf") == {}

    def test_pymupdf_branches(self, monkeypatch):
        monkeypatch.setenv("PDF_OCR_IMAGE_MAX_PER_PAGE", "2")
        png = _noise_png((150, 150))
        infos = [
            {"xref": 0},
            {"xref": 1, "width": 10, "height": 10},
            {"xref": 2, "width": 200, "height": 200},
            {"xref": 3, "width": 200, "height": 200},
            {"xref": 4, "width": 200, "height": 200},
            {"xref": 5},
            {"xref": 6},
            {"xref": 7},
        ]
        images = {
            2: RuntimeError("cannot extract"),
            3: b"",
            4: b"junk, not an image",
            5: png,
            6: png,
            7: png,
            1: png,
        }
        dup_page = [{"xref": 5, "width": 200, "height": 200}]
        doc = FakeFitzDoc([FakeFitzPage(infos), FakeFitzPage(RuntimeError("no info")), FakeFitzPage(dup_page), FakeFitzPage([])], images)
        monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda stream, filetype: doc))

        out = module._extract_pdf_page_images(b"%PDF")

        assert doc.closed is True
        assert list(out) == [0]
        assert [seq for seq, _ in out[0]] == [0, 1]
        assert out[0][0][0] == 0 and out[0][0][1] == b"junk, not an image"
        assert out[0][1][1].startswith(b"\x89PNG")


class FakePdfiumImage:
    def __init__(self, size=(200, 200), save_error=None, close_error=None):
        self.size = size
        self.save_error = save_error
        self.close_error = close_error

    def save(self, buf, format):
        if self.save_error:
            raise self.save_error
        buf.write(b"PNGDATA")

    def close(self):
        if self.close_error:
            raise self.close_error


class FakePdfiumObject:
    def __init__(self, type_=3, image="__default__", error=None):
        self.type = type_
        self.image = FakePdfiumImage() if image == "__default__" else image
        self.error = error

    def get_bitmap(self):
        if self.error:
            raise self.error
        return self

    def to_pil(self):
        return self.image


class FakePdfiumPage:
    def __init__(self, objects):
        self.objects = objects
        self.closed = False

    def get_objects(self):
        if isinstance(self.objects, Exception):
            raise self.objects
        return iter(self.objects)

    def close(self):
        self.closed = True


class FakePdfiumDoc:
    def __init__(self, pages):
        self.pages = pages
        self.closed = False

    def __len__(self):
        return len(self.pages)

    def get_page(self, idx):
        return self.pages[idx]

    def close(self):
        self.closed = True


class TestPdfiumFallback:
    @pytest.fixture(autouse=True)
    def _no_pymupdf(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "fitz", None)

    def _install(self, monkeypatch, doc=None, error=None):
        import pypdfium2

        def PdfDocument(content):
            if error:
                raise error
            return doc

        monkeypatch.setattr(pypdfium2, "PdfDocument", PdfDocument)

    def test_unreadable_document(self, monkeypatch):
        self._install(monkeypatch, error=RuntimeError("bad pdf"))

        assert module._extract_pdf_page_images(b"x") == {}

    def test_objects_listing_failure_aborts_with_empty_result(self, monkeypatch):
        page = FakePdfiumPage(RuntimeError("no objects"))
        doc = FakePdfiumDoc([page])
        self._install(monkeypatch, doc)

        assert module._extract_pdf_page_images(b"x") == {}
        assert page.closed and doc.closed

    def test_object_filtering_and_limits(self, monkeypatch):
        monkeypatch.setenv("PDF_OCR_IMAGE_MAX_PER_PAGE", "2")
        objects = [
            FakePdfiumObject(type_=1),
            FakePdfiumObject(error=ValueError("no pil")),
            FakePdfiumObject(image=None),
            FakePdfiumObject(image=FakePdfiumImage(size=(10, 10))),
            FakePdfiumObject(image=FakePdfiumImage(save_error=OSError("save"))),
            FakePdfiumObject(image=FakePdfiumImage(close_error=OSError("close"))),
            FakePdfiumObject(),
            FakePdfiumObject(),
        ]
        pages = [FakePdfiumPage(objects), FakePdfiumPage([FakePdfiumObject(type_=1)])]
        doc = FakePdfiumDoc(pages)
        self._install(monkeypatch, doc)

        out = module._extract_pdf_page_images(b"x")

        assert out == {0: [(0, b"PNGDATA"), (1, b"PNGDATA")]}
        assert all(p.closed for p in pages) and doc.closed

    def test_pictures_of_a_real_pdf_must_be_found(self, monkeypatch):
        out = module._extract_pdf_page_images(HYBRID_PDF)

        assert list(out) == [0]
