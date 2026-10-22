"""services/document_parser: OCR (ocr-service и backend vision-LLM), изображения, PDF со сканами и встроенными картинками."""

from __future__ import annotations

import base64
import json
import logging
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.core.cef_logger import cef_audit_context as audit
from app.services import document_parser as dp

pytestmark = pytest.mark.unit

fitz = pytest.importorskip("fitz")
PIL_Image = pytest.importorskip("PIL.Image")

OCR_URL = "http://ocr.test:8002/v1/ocr"
BACKEND_IMG = "http://backend.test:8000/api/internal/rag/ocr-image"
BACKEND_PDF = "http://backend.test:8000/api/internal/rag/ocr-pdf"


@pytest.fixture(autouse=True)
def stable_env(monkeypatch):
    for key in ("RAG_OCR_ENGINE", "RAG_BACKEND_OCR_READ_TIMEOUT", "RAG_OCR_IMAGE_MIN_AREA", "RAG_MAX_OCR_IMAGES_PER_DOC",
                "RAG_PDF_PAGES_PER_FLUSH", "backend_service_port", "BACKEND_SERVICE_PORT"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


@pytest.fixture
def backend_url(monkeypatch):
    """Адрес backend из тестового конфига (urls.backend_service_port)."""
    from app.core.config import get_settings

    assert get_settings().backend.base_url == "http://backend.test:8000"


def png_bytes(size=(64, 64), color=(200, 30, 30), fmt="PNG"):
    buf = BytesIO()
    PIL_Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


def noisy_png(size=(300, 300)):
    buf = BytesIO()
    PIL_Image.effect_noise(size, 80).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


# ───────────────────────────── ocr-service (Surya) ─────────────────────────────


class TestCallOcrService:
    @pytest.mark.parametrize("filename,mime", [
        ("a.png", "image/png"), ("A.PNG", "image/png"), ("a.bmp", "image/bmp"), ("a.webp", "image/webp"),
        ("a.jpg", "image/jpeg"), ("a.unknown", "image/jpeg"),
    ])
    async def test_mime_by_extension_and_payload(self, router, filename, mime):
        route = router.post(OCR_URL).mock(return_value=httpx.Response(200, json={"success": True, "text": "привет"}))

        result = await dp._call_ocr_service(b"IMG", filename, languages="ru")

        assert result == {"success": True, "text": "привет"}
        request = route.calls.last.request
        body = request.content
        assert request.headers["accept"] == "application/json"
        assert f'filename="{filename}"'.encode() in body and mime.encode() in body and b"IMG" in body
        assert b'name="languages"' in body and b"\r\n\r\nru\r\n" in body

    async def test_http_error_is_logged_and_raised(self, router, caplog):
        router.post(OCR_URL).mock(return_value=httpx.Response(503, text="busy"))

        with caplog.at_level(logging.ERROR, logger=dp.logger.name):
            with pytest.raises(httpx.HTTPStatusError):
                await dp._call_ocr_service(b"x", "a.png")

        assert any("Ошибка OCR при обращении к http://ocr.test:8002" in r.getMessage() for r in caplog.records)

    async def test_network_error_is_raised(self, router):
        router.post(OCR_URL).mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(httpx.ConnectError):
            await dp._call_ocr_service(b"x", "a.png")


class TestEngineSettings:
    def test_engine_default_and_env(self, monkeypatch):
        assert dp._ocr_engine() == "backend"
        monkeypatch.setenv("RAG_OCR_ENGINE", " SURYA ")
        assert dp._ocr_engine() == "surya"
        monkeypatch.setenv("RAG_OCR_ENGINE", "")
        assert dp._ocr_engine() == "backend"

    def test_backend_timeout_has_floor_and_survives_garbage(self, monkeypatch):
        assert dp._backend_ocr_read_timeout().read == 1800.0
        monkeypatch.setenv("RAG_BACKEND_OCR_READ_TIMEOUT", "5")
        assert dp._backend_ocr_read_timeout().read == 120.0
        monkeypatch.setenv("RAG_BACKEND_OCR_READ_TIMEOUT", "медленно")
        t = dp._backend_ocr_read_timeout()
        assert (t.read, t.connect, t.write) == (1800.0, 10.0, 120.0)


# ───────────────────────────── backend vision-LLM ─────────────────────────────


class TestBackendOcrImage:
    async def test_exact_name_match_and_payload(self, router, backend_url):
        route = router.post(BACKEND_IMG).mock(return_value=httpx.Response(200, json={"results": [
            {"name": "other.png", "text": "чужой"}, {"name": "pic.png", "text": "  свой  "}]}))

        text = await dp._call_backend_ocr_image(b"\x89PNG", "pic.png")

        assert text == "свой"
        payload = json.loads(route.calls.last.request.content)
        assert payload == {"images": [{"name": "pic.png", "image_base64": base64.b64encode(b"\x89PNG").decode()}]}

    async def test_falls_back_to_first_non_empty_result(self, router, backend_url):
        router.post(BACKEND_IMG).mock(return_value=httpx.Response(200, json={"results": [
            {"name": "pic.png", "text": "  "}, {"name": "renamed.png", "text": "нашлось"}]}))

        assert await dp._call_backend_ocr_image(b"x", "pic.png") == "нашлось"

    async def test_empty_results_return_empty_string(self, router, backend_url):
        router.post(BACKEND_IMG).mock(return_value=httpx.Response(200, json={"results": [{"name": "pic.png", "text": ""}]}))

        assert await dp._call_backend_ocr_image(b"x", "pic.png") == ""

    async def test_error_field_is_raised_when_nothing_recognised(self, router, backend_url):
        router.post(BACKEND_IMG).mock(return_value=httpx.Response(200, json={"results": [], "error": "модель недоступна"}))

        with pytest.raises(RuntimeError, match="модель недоступна"):
            await dp._call_backend_ocr_image(b"x", "pic.png")

    async def test_http_error_propagates(self, router, backend_url):
        router.post(BACKEND_IMG).mock(return_value=httpx.Response(500))

        with pytest.raises(httpx.HTTPStatusError):
            await dp._call_backend_ocr_image(b"x", "pic.png")

    async def test_initiator_headers_are_forwarded(self, router, backend_url):
        route = router.post(BACKEND_IMG).mock(return_value=httpx.Response(200, json={"results": []}))
        tokens = audit.cef_audit_set(user={"username": "ivan", "user_id": "42", "sntdom": "CORP"})
        try:
            await dp._call_backend_ocr_image(b"x", "pic.png")
        finally:
            audit.cef_audit_reset(tokens)

        headers = route.calls.last.request.headers
        assert (headers["x-cef-suser"], headers["x-cef-suid"], headers["x-cef-sntdom"]) == ("ivan", "42", "CORP")

    async def test_backend_url_must_be_configured(self, monkeypatch):
        monkeypatch.setattr(dp, "get_settings", lambda: SimpleNamespace(backend=SimpleNamespace(base_url="")))

        with pytest.raises(RuntimeError, match="backend.base_url не задан"):
            await dp._call_backend_ocr_image(b"x", "pic.png")

    async def test_trailing_slash_in_base_url_is_trimmed(self, router, monkeypatch):
        monkeypatch.setattr(dp, "get_settings", lambda: SimpleNamespace(backend=SimpleNamespace(base_url="http://be.local/")))
        route = router.post("http://be.local/api/internal/rag/ocr-image").mock(return_value=httpx.Response(200, json={"results": []}))

        assert await dp._call_backend_ocr_image(b"x", "p.png") == ""
        assert route.called


class TestOcrImageBytesRouting:
    async def test_backend_engine_is_default(self, monkeypatch):
        async def backend(blob, name):
            return f"backend:{name}"

        monkeypatch.setattr(dp, "_call_backend_ocr_image", backend)

        assert await dp._ocr_image_bytes(b"x", "a.png") == "backend:a.png"

    async def test_surya_engine_uses_ocr_service_and_strips_text(self, monkeypatch):
        monkeypatch.setenv("RAG_OCR_ENGINE", "surya")

        async def service(blob, name, languages="ru,en"):
            return {"text": "  из surya  "}

        monkeypatch.setattr(dp, "_call_ocr_service", service)

        assert await dp._ocr_image_bytes(b"x", "a.png") == "из surya"

    async def test_surya_non_dict_answer_gives_empty(self, monkeypatch):
        monkeypatch.setenv("RAG_OCR_ENGINE", "surya")

        async def service(blob, name, languages="ru,en"):
            return ["unexpected"]

        monkeypatch.setattr(dp, "_call_ocr_service", service)

        assert await dp._ocr_image_bytes(b"x", "a.png") == ""


class TestOcrOfficeImages:
    async def test_no_blobs(self):
        assert await dp._ocr_office_images([], "f.docx") == ""

    async def test_texts_are_joined_and_failures_skipped(self, monkeypatch):
        names = []

        async def ocr(blob, name):
            names.append(name)
            if blob == b"bad":
                raise RuntimeError("OCR упал")
            return {b"1": "первый", b"2": "", b"3": "третий"}[blob]

        monkeypatch.setattr(dp, "_ocr_image_bytes", ocr)

        text = await dp._ocr_office_images([b"1", b"2", b"bad", b"3"], "отчёт.docx")

        assert text == "[Текст из изображений документа (OCR)]:\nпервый\nтретий"
        assert names == ["отчёт.docx.img1.png", "отчёт.docx.img2.png", "отчёт.docx.img3.png", "отчёт.docx.img4.png"]

    async def test_nothing_recognised(self, monkeypatch):
        async def ocr(blob, name):
            return ""

        monkeypatch.setattr(dp, "_ocr_image_bytes", ocr)

        assert await dp._ocr_office_images([b"1"], "f.docx") == ""


# ───────────────────────────── изображения целиком ─────────────────────────────


class TestImageBytes:
    async def test_without_pillow(self, monkeypatch):
        monkeypatch.setattr(dp, "PIL_AVAILABLE", False)

        result = await dp.extract_text_from_image_bytes(b"x", "a.png")

        assert result["file_type"] == "image" and "требуется Pillow" in result["text"]
        assert result["confidence_info"]["confidence"] == 0.0

    async def test_backend_engine_success_sends_png_upscaled(self, monkeypatch):
        sent = {}

        async def backend(blob, name):
            sent["name"] = name
            sent["image"] = PIL_Image.open(BytesIO(blob))
            return "Текст с фото"

        monkeypatch.setattr(dp, "_call_backend_ocr_image", backend)

        result = await dp.extract_text_from_image_bytes(png_bytes((100, 50)), "a.png")

        assert result["text"] == "Текст с фото" and result["confidence_info"]["confidence"] == 80.0
        assert sent["name"] == "image.png" and sent["image"].format == "PNG"
        # маленькое изображение увеличивается до 1024 по большей стороне
        assert sent["image"].size == (1024, 512)

    async def test_jpeg_stays_jpeg_and_large_image_is_not_resized(self, monkeypatch):
        sent = {}

        async def backend(blob, name):
            sent["name"] = name
            sent["image"] = PIL_Image.open(BytesIO(blob))
            return "x"

        monkeypatch.setattr(dp, "_call_backend_ocr_image", backend)

        await dp.extract_text_from_image_bytes(png_bytes((1200, 800), fmt="JPEG"), "photo.jpg")

        assert sent["name"] == "image.jpg" and sent["image"].format == "JPEG" and sent["image"].size == (1200, 800)

    async def test_backend_engine_no_text(self, monkeypatch):
        async def backend(blob, name):
            return "   "

        monkeypatch.setattr(dp, "_call_backend_ocr_image", backend)

        result = await dp.extract_text_from_image_bytes(png_bytes(), "a.png")

        assert result["text"] == "" and result["confidence_info"]["text_length"] == 0
        assert "error" not in result["confidence_info"]

    async def test_backend_engine_error_is_reported_in_confidence_info(self, monkeypatch):
        async def backend(blob, name):
            raise RuntimeError("backend лёг")

        monkeypatch.setattr(dp, "_call_backend_ocr_image", backend)

        result = await dp.extract_text_from_image_bytes(png_bytes(), "a.png")

        assert result["text"] == "" and result["confidence_info"]["error"] == "backend лёг"
        assert result["confidence_info"]["ocr_available"] is False
        assert dp.empty_document_index_error(result, "a.png") == "OCR изображения: backend лёг"

    async def test_broken_image_raises(self):
        with pytest.raises(Exception):
            await dp.extract_text_from_image_bytes(b"not an image", "a.png")

    @pytest.fixture
    def surya(self, monkeypatch):
        monkeypatch.setenv("RAG_OCR_ENGINE", "surya")
        state = SimpleNamespace(result={}, error=None, calls=[])

        async def service(blob, name, languages="ru,en"):
            state.calls.append((name, languages))
            if state.error:
                raise state.error
            return state.result

        monkeypatch.setattr(dp, "_call_ocr_service", service)
        return state

    async def test_surya_success(self, surya):
        surya.result = {"success": True, "text": "строка", "words": [{"word": "строка"}], "confidence": 91.5}

        result = await dp.extract_text_from_image_bytes(png_bytes(), "a.png")

        assert result["text"] == "строка"
        assert result["confidence_info"] == {"confidence": 91.5, "text_length": 6, "file_type": "image",
                                             "ocr_available": True, "words": [{"word": "строка"}]}
        assert surya.calls == [("image.png", "ru,en")]

    async def test_surya_exception(self, surya):
        surya.error = RuntimeError("сервис недоступен")

        result = await dp.extract_text_from_image_bytes(png_bytes(), "a.png")

        assert result["text"] == "" and result["confidence_info"]["error"] == "сервис недоступен"

    async def test_surya_reports_failure(self, surya):
        surya.result = {"success": False, "error": "GPU busy"}

        result = await dp.extract_text_from_image_bytes(png_bytes(), "a.png")

        assert result["text"] == "" and result["confidence_info"]["error"] == "GPU busy"

    async def test_surya_failure_without_message(self, surya):
        surya.result = {"success": False}

        result = await dp.extract_text_from_image_bytes(png_bytes(), "a.png")

        assert result["confidence_info"]["error"] == "Неизвестная ошибка"

    async def test_surya_empty_text(self, surya):
        surya.result = {"success": True, "text": "  "}

        result = await dp.extract_text_from_image_bytes(png_bytes(), "a.png")

        assert result["text"] == "" and result["confidence_info"]["ocr_available"] is False


# ───────────────────────────── PDF через backend ─────────────────────────────


class TestBackendOcrPdf:
    async def test_success(self, router, backend_url):
        route = router.post(BACKEND_PDF).mock(return_value=httpx.Response(200, json={"text": " распознано ", "ocr_used": 1}))

        result = await dp._call_backend_ocr_pdf(b"%PDF-1.4", "scan.pdf")

        assert result == {"text": "распознано", "ocr_used": True}
        payload = json.loads(route.calls.last.request.content)
        assert payload == {"filename": "scan.pdf", "pdf_base64": base64.b64encode(b"%PDF-1.4").decode()}

    async def test_default_filename(self, router, backend_url):
        route = router.post(BACKEND_PDF).mock(return_value=httpx.Response(200, json={"text": "t"}))

        await dp._call_backend_ocr_pdf(b"%PDF", "")

        assert json.loads(route.calls.last.request.content)["filename"] == "document.pdf"

    async def test_empty_text_raises_with_backend_error(self, router, backend_url):
        router.post(BACKEND_PDF).mock(return_value=httpx.Response(200, json={"text": "", "error": "нет страниц"}))
        with pytest.raises(RuntimeError, match="нет страниц"):
            await dp._call_backend_ocr_pdf(b"x", "a.pdf")

        router.post(BACKEND_PDF).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(RuntimeError, match="backend OCR вернул пустой текст"):
            await dp._call_backend_ocr_pdf(b"x", "a.pdf")

    async def test_garbage_timeout_env_and_url_check(self, router, backend_url, monkeypatch):
        monkeypatch.setenv("RAG_BACKEND_OCR_READ_TIMEOUT", "nope")
        router.post(BACKEND_PDF).mock(return_value=httpx.Response(200, json={"text": "ok"}))

        assert (await dp._call_backend_ocr_pdf(b"x", "a.pdf"))["text"] == "ok"

    async def test_backend_url_must_be_configured(self, monkeypatch):
        monkeypatch.setattr(dp, "get_settings", lambda: SimpleNamespace(backend=SimpleNamespace(base_url="")))

        with pytest.raises(RuntimeError, match="OCR PDF через backend недоступен"):
            await dp._call_backend_ocr_pdf(b"x", "a.pdf")

    async def test_http_error(self, router, backend_url):
        router.post(BACKEND_PDF).mock(return_value=httpx.Response(502))

        with pytest.raises(httpx.HTTPStatusError):
            await dp._call_backend_ocr_pdf(b"x", "a.pdf")


# ───────────────────────────── PDF: текстовый слой, сканы, картинки ─────────────────────────────

LONG_TEXT = "Это достаточно длинный текст страницы документа для проверки качества слоя. " * 4


def make_pdf(pages=1, text=LONG_TEXT, image=None):
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_textbox(fitz.Rect(40, 40, 550, 400), text, fontname="helv", fontsize=10) if text.isascii() else page.insert_text((40, 60), "x")
        if image:
            page.insert_image(fitz.Rect(100, 420, 400, 720), stream=image)
    return doc.tobytes()


ASCII_TEXT = "This is a sufficiently long page of text used to check the quality of the text layer. " * 5


@pytest.fixture
def pdf_ocr(monkeypatch):
    state = SimpleNamespace(backend_result={"text": ""}, backend_error=None, backend_calls=0, office="", office_calls=[])

    async def backend(data, filename):
        state.backend_calls += 1
        if state.backend_error:
            raise state.backend_error
        return state.backend_result

    async def office(blobs, filename):
        state.office_calls.append((list(blobs) if blobs is not None else None, filename))
        return state.office

    monkeypatch.setattr(dp, "_call_backend_ocr_pdf", backend)
    monkeypatch.setattr(dp, "_ocr_office_images", office)
    return state


class TestPdfExtraction:
    async def test_good_text_layer_does_not_call_backend_ocr(self, pdf_ocr):
        result = await dp.extract_text_from_pdf_bytes(make_pdf(2, ASCII_TEXT), "doc.pdf")

        assert "sufficiently long page" in result["text"] and result["file_type"] == "pdf" and result["pages"] == 2
        assert pdf_ocr.backend_calls == 0
        assert result["confidence_info"]["confidence"] == 100.0 and "ocr_source" not in result["confidence_info"]

    async def test_empty_layer_uses_backend_text(self, pdf_ocr):
        pdf_ocr.backend_result = {"text": "Распознанный текст скана страницы " * 5}

        result = await dp.extract_text_from_pdf_bytes(make_pdf(1, text=""), "scan.pdf")

        assert result["text"].startswith("Распознанный текст скана")
        assert result["confidence_info"]["ocr_source"] == "backend_llm"
        assert result["confidence_info"]["confidence"] == 80.0 and pdf_ocr.backend_calls == 1

    async def test_weak_layer_replaced_only_if_backend_gives_more(self, pdf_ocr):
        pdf_ocr.backend_result = {"text": "ab"}
        weak = make_pdf(1, "short text")

        result = await dp.extract_text_from_pdf_bytes(weak, "w.pdf")

        assert result["text"] == "short text" and "ocr_source" not in result["confidence_info"]

    async def test_backend_failure_is_survivable(self, pdf_ocr):
        pdf_ocr.backend_error = RuntimeError("backend недоступен")

        result = await dp.extract_text_from_pdf_bytes(make_pdf(1, text=""), "scan.pdf")

        assert result["text"] == "" and result["pages"] == 1 and result["confidence_info"]["text_length"] == 0

    async def test_garbage_bytes_give_empty_text(self, pdf_ocr):
        result = await dp.extract_text_from_pdf_bytes(b"%PDF-broken", "bad.pdf")

        assert result["text"] == "" and result["pages"] == 0

    async def test_without_pymupdf_goes_straight_to_backend(self, pdf_ocr, monkeypatch):
        monkeypatch.setattr(dp, "PYMUPDF_AVAILABLE", False)
        pdf_ocr.backend_result = {"text": "из backend " * 20}

        result = await dp.extract_text_from_pdf_bytes(b"%PDF-1.4", "x.pdf")

        assert result["text"].startswith("из backend") and result["confidence_info"]["ocr_source"] == "backend_llm"
        assert result["pages"] == 1

    async def test_flush_interval_from_env_and_garbage(self, pdf_ocr, monkeypatch):
        calls = []
        monkeypatch.setattr(dp, "_mupdf_store_shrink", lambda: calls.append(1))
        monkeypatch.setenv("RAG_PDF_PAGES_PER_FLUSH", "1")
        await dp.extract_text_from_pdf_bytes(make_pdf(3, ASCII_TEXT), "a.pdf")
        flushes_per_page = len(calls)

        calls.clear()
        monkeypatch.setenv("RAG_PDF_PAGES_PER_FLUSH", "много")
        await dp.extract_text_from_pdf_bytes(make_pdf(3, ASCII_TEXT), "a.pdf")

        # при шаге 1 - сброс после каждой страницы (3 раза) + один сброс после обхода картинок; при мусоре - шаг 32
        assert flushes_per_page == 4 and len(calls) == 1

    async def test_hybrid_pdf_appends_image_text(self, pdf_ocr, monkeypatch):
        pdf_ocr.office = "[Текст из изображений документа (OCR)]:\nна скриншоте"
        monkeypatch.setattr(dp, "_pdf_image_blobs", lambda data: [b"img1"])

        result = await dp.extract_text_from_pdf_bytes(make_pdf(1, ASCII_TEXT), "h.pdf")

        assert result["text"].endswith("на скриншоте") and "sufficiently long" in result["text"]
        assert result["confidence_info"]["ocr_source"] == "backend_llm"
        assert pdf_ocr.office_calls == [([b"img1"], "h.pdf")]

    async def test_hybrid_pdf_with_surya_engine_marks_source(self, pdf_ocr, monkeypatch):
        monkeypatch.setenv("RAG_OCR_ENGINE", "surya")
        pdf_ocr.office = "картинка"
        monkeypatch.setattr(dp, "_pdf_image_blobs", lambda data: [b"i"])

        result = await dp.extract_text_from_pdf_bytes(make_pdf(1, ASCII_TEXT), "h.pdf")

        assert result["confidence_info"]["ocr_source"] == "ocr_service"

    async def test_image_ocr_failure_does_not_lose_text(self, pdf_ocr, monkeypatch):
        def boom(data):
            raise RuntimeError("картинки сломаны")

        monkeypatch.setattr(dp, "_pdf_image_blobs", boom)

        result = await dp.extract_text_from_pdf_bytes(make_pdf(1, ASCII_TEXT), "h.pdf")

        assert "sufficiently long" in result["text"]

    async def test_encrypted_pdf_with_empty_password(self, pdf_ocr):
        doc = fitz.open()
        doc.new_page().insert_textbox(fitz.Rect(40, 40, 550, 400), ASCII_TEXT, fontname="helv", fontsize=10)
        data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="")

        result = await dp.extract_text_from_pdf_bytes(data, "enc.pdf")

        assert "sufficiently long" in result["text"]

    async def test_failed_empty_password_authentication_is_ignored(self, pdf_ocr, monkeypatch):
        class Page:
            def get_text(self):
                return ASCII_TEXT

        class Doc:
            needs_pass = True
            page_count = 1

            def authenticate(self, password):
                raise RuntimeError("не подошёл")

            def load_page(self, i):
                return Page()

            def close(self):
                pass

        monkeypatch.setattr(dp.fitz, "open", lambda **kw: Doc())
        monkeypatch.setattr(dp, "_pdf_image_blobs", lambda data: [])

        result = await dp.extract_text_from_pdf_bytes(b"x", "enc.pdf")

        assert "sufficiently long" in result["text"]

    async def test_password_protected_pdf_is_unreadable_not_fatal(self, pdf_ocr):
        doc = fitz.open()
        doc.new_page().insert_textbox(fitz.Rect(40, 40, 550, 400), ASCII_TEXT, fontname="helv", fontsize=10)
        data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")

        result = await dp.extract_text_from_pdf_bytes(data, "locked.pdf")

        assert result["text"] == ""


class TestMupdfStoreShrink:
    def test_without_pymupdf_is_noop(self, monkeypatch):
        monkeypatch.setattr(dp, "PYMUPDF_AVAILABLE", False)

        dp._mupdf_store_shrink()

    def test_tools_without_store_shrink(self, monkeypatch):
        monkeypatch.setattr(dp.fitz, "TOOLS", SimpleNamespace(), raising=False)

        dp._mupdf_store_shrink()

    def test_missing_tools(self, monkeypatch):
        monkeypatch.setattr(dp.fitz, "TOOLS", None, raising=False)

        dp._mupdf_store_shrink()

    def test_shrinks_and_logs_sizes(self, monkeypatch, caplog):
        tools = SimpleNamespace(store_size=1000, calls=[])

        def shrink(percent):
            tools.calls.append(percent)
            tools.store_size = 400

        tools.store_shrink = shrink
        monkeypatch.setattr(dp.fitz, "TOOLS", tools, raising=False)

        with caplog.at_level(logging.DEBUG, logger=dp.logger.name):
            dp._mupdf_store_shrink()

        assert tools.calls == [100]
        assert any("1000 -> 400" in r.getMessage() and "освободили 600" in r.getMessage() for r in caplog.records)

    def test_errors_are_swallowed(self, monkeypatch):
        def boom(percent):
            raise RuntimeError("mupdf")

        monkeypatch.setattr(dp.fitz, "TOOLS", SimpleNamespace(store_shrink=boom, store_size=1), raising=False)

        dp._mupdf_store_shrink()


class TestPdfImageBlobs:
    def test_without_pymupdf(self, monkeypatch):
        monkeypatch.setattr(dp, "PYMUPDF_AVAILABLE", False)

        assert dp._pdf_image_blobs(b"%PDF") == []

    def test_pdf_without_images(self):
        assert dp._pdf_image_blobs(make_pdf(1, ASCII_TEXT)) == []

    def test_embedded_image_must_be_extracted(self):
        pdf = make_pdf(1, ASCII_TEXT, image=noisy_png())

        blobs = dp._pdf_image_blobs(pdf)

        assert blobs and all(isinstance(b, bytes) and b for b in blobs)

    def test_garbage_pdf_gives_empty_list(self):
        # Любая ошибка открытия: warning и неявный None вместо [] (контракт «List[bytes]» нарушен).
        assert dp._pdf_image_blobs(b"not a pdf") == []

    def test_page_level_get_images_failure_is_skipped(self, monkeypatch):
        class Page:
            def get_images(self, full=True):
                raise RuntimeError("страница битая")

        class Doc:
            page_count = 2

            def load_page(self, i):
                return Page()

            def close(self):
                self.closed = True

        monkeypatch.setattr(dp.fitz, "open", lambda **kw: Doc())

        assert dp._pdf_image_blobs(b"x") == []

    def test_dict_style_image_info_covers_filtering_and_limits(self, monkeypatch):
        """Ветки фильтрации по площади, дедупа по xref и лимита - на объектах, которые ведут себя как dict+tuple."""

        class Info(dict):
            def __getitem__(self, key):
                return {2: self["w"], 3: self["h"]}[key] if isinstance(key, int) else super().__getitem__(key)

        def info(xref, w, h):
            return Info(xref=xref, w=w, h=h)

        pages = [[info(0, 500, 500), info(1, 10, 10), info(2, 400, 400), info(2, 400, 400), info(3, 400, 400), info(4, 400, 400)]]

        class Doc:
            page_count = 1
            closed = False

            def load_page(self, i):
                return SimpleNamespace(get_images=lambda full=True: pages[i])

            def extract_image(self, xref):
                if xref == 3:
                    raise RuntimeError("битая картинка")
                return {"image": b"" if xref == 4 else b"IMG%d" % xref}

            def close(self):
                Doc.closed = True

        monkeypatch.setattr(dp.fitz, "open", lambda **kw: Doc())

        # xref 0 пропущен, 1 - мелкая, 2 - дубль, 3 - ошибка, 4 - пустой blob
        assert dp._pdf_image_blobs(b"x") == [b"IMG2"] and Doc.closed

    def test_limit_of_images_per_document(self, monkeypatch):
        monkeypatch.setenv("RAG_MAX_OCR_IMAGES_PER_DOC", "1")

        class Info(dict):
            def __getitem__(self, key):
                return 400

        class Doc:
            page_count = 1

            def load_page(self, i):
                return SimpleNamespace(get_images=lambda full=True: [Info(xref=1), Info(xref=2)])

            def extract_image(self, xref):
                return {"image": b"I%d" % xref}

            def close(self):
                pass

        monkeypatch.setattr(dp.fitz, "open", lambda **kw: Doc())

        assert dp._pdf_image_blobs(b"x") == [b"I1"]
