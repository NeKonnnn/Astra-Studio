"""POST /api/documents/attach: проверки файла, сохранение в каталог пода, OCR-ветки, ошибки извлечения."""

import os

import pytest

from backend.routes import documents as module
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

URL = "/api/documents/attach"
POD = module.INLINE_ATTACH_POD_TMP_BUCKET


@pytest.fixture
def attach_dir(tmp_path, monkeypatch):
    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setenv(module.INLINE_ATTACH_DIR_ENV, str(root))
    return root


@pytest.fixture
def env(monkeypatch, attach_dir, cef_events):
    """Определение провайдера — заглушка (vision включён), CEF-события перехватываются."""
    import backend.app_state as app_state

    async def provider_vision():
        return "prov", True

    monkeypatch.setattr(module, "_attachment_provider_vision", provider_vision)
    monkeypatch.setattr(app_state, "get_current_model_path", lambda: "m/path")
    events = cef_events(module)
    return type("Env", (), {"dir": attach_dir, "events": events, "mp": monkeypatch})


def _post(make_client, name="note.txt", content=b"hello", ctype="text/plain", user=ALICE):
    return make_client(module.router, user=user).post(URL, files={"file": (name, content, ctype)})


def _stored(root):
    return [p for p in root.rglob("*") if p.is_file()]


class TestAuth:
    async def test_anonymous_upload_is_rejected(self, make_client, env):
        response = await _post(make_client, user=None)

        assert response.status_code in (401, 403)
        assert _stored(env.dir) == [] and env.events == []


class TestSuccess:
    async def test_text_file_is_stored_and_returned_inline(self, make_client, env):
        response = await _post(make_client, content="Привет".encode("utf-8"))

        body = response.json()
        assert response.status_code == 200
        assert (body["success"], body["type"], body["content"], body["filename"]) == (True, "text", "Привет", "note.txt")
        assert body["attachments_saved"] is True and body["minio_bucket"] == POD
        assert "warning" not in body
        (path,) = _stored(env.dir)
        assert path.read_bytes() == "Привет".encode("utf-8")
        assert body["minio_object"] == path.relative_to(env.dir).as_posix()
        assert body["minio_object"].startswith("alice/")
        assert os.path.samefile(body["attachments_path"], path)

    async def test_cef_event_describes_the_stored_object(self, make_client, env):
        response = await _post(make_client)

        (code, kwargs) = env.events[-1]
        extra = kwargs["extra"]
        assert code == "FS007" and kwargs["status_code"] == 200
        assert extra["fname"] == "note.txt" and extra["fsize"] == 5 and extra["cs1"] == "text"
        assert extra["cs2"] == response.json()["minio_object"] and extra["cs2Label"] == "PodAttachmentsObject"

    @pytest.mark.parametrize(
        ("user", "folder"),
        [({"user_id": "bob"}, "bob/"), ({"username": "", "user_id": ""}, "anonymous/")],
    )
    async def test_directory_name_falls_back_to_user_id_and_anonymous(self, make_client, env, user, folder):
        body = (await _post(make_client, user=user)).json()

        assert body["minio_object"].startswith(folder)

    async def test_without_a_directory_the_file_is_only_inline(self, make_client, env, monkeypatch):
        monkeypatch.delenv(module.INLINE_ATTACH_DIR_ENV)

        body = (await _post(make_client)).json()

        assert body["attachments_saved"] is False and body["content"] == "hello"
        assert "minio_object" not in body and "minio_bucket" not in body
        assert module.INLINE_ATTACH_DIR_ENV in body["warning"] and "inline" in body["warning"]
        assert "cs2" not in env.events[-1][1]["extra"]


class TestRejects:
    async def test_empty_file(self, make_client, env):
        response = await _post(make_client, content=b"")

        assert response.status_code == 400 and response.json()["detail"] == "Файл пустой"
        (code, kwargs) = env.events[-1]
        assert code == "FS008" and kwargs["extra"]["cs1"] == "empty" and kwargs["extra"]["fsize"] == 0
        assert _stored(env.dir) == []

    async def test_unsupported_extension(self, make_client, env):
        response = await _post(make_client, name="virus.exe", ctype="application/octet-stream")

        assert response.status_code == 400
        assert response.json()["detail"].startswith("Неподдерживаемый формат (")
        (code, kwargs) = env.events[-1]
        assert code == "FS008" and kwargs["extra"]["cs1"] == "unsupported_format"
        assert kwargs["extra"]["reason"].startswith(".exe файлы не поддерживаются")
        assert _stored(env.dir) == []

    async def test_oversized_file(self, make_client, env, monkeypatch):
        limit = 2 * 1024 * 1024
        monkeypatch.setattr(module, "INLINE_ATTACHMENT_MAX_BYTES", limit)

        response = await _post(make_client, content=b"x" * (limit + 1))

        assert response.status_code == 400 and response.json()["detail"] == "Размер превышает 2MB"
        (code, kwargs) = env.events[-1]
        assert kwargs["extra"]["cs1"] == "size_exceeded" and kwargs["extra"]["fsize"] == limit + 1
        assert "лимит 2 МБ" in kwargs["extra"]["reason"]
        assert _stored(env.dir) == []

    async def test_file_exactly_at_the_limit_is_accepted(self, make_client, env, monkeypatch):
        limit = 1024
        monkeypatch.setattr(module, "INLINE_ATTACHMENT_MAX_BYTES", limit)

        assert (await _post(make_client, content=b"x" * limit)).status_code == 200

    async def test_unreadable_upload(self, make_client, env, monkeypatch):
        from starlette.datastructures import UploadFile

        async def broken(self, size=-1):
            raise OSError("stream closed")

        monkeypatch.setattr(UploadFile, "read", broken)

        response = await _post(make_client)

        assert response.status_code == 400 and response.json()["detail"] == "Не удалось прочитать файл"
        assert env.events[-1][1]["extra"]["cs1"] == "read_error"

    async def test_content_type_is_guessed_from_the_extension(self, make_client, env, monkeypatch):
        seen = {}

        def extract(content, filename, content_type):
            seen["ct"] = content_type
            return {"type": "text", "content": "x", "cs1": "pdf-stub"}

        monkeypatch.setattr(module, "_extract_inline_payload", extract)

        await _post(make_client, name="a.pdf", ctype="")

        assert seen["ct"] == "application/pdf"


class TestOcrDispatch:
    @pytest.mark.parametrize(
        ("payload", "ocr_function"),
        [
            ({"type": "image", "content": "data:", "cs1": "image"}, "_ocr_image_payload_if_needed"),
            ({"type": "text", "content": "pdf", "cs1": "pdf"}, "_ocr_pdf_payload_if_needed"),
            ({"type": "text", "content": "docx", "cs1": "docx"}, "_ocr_docx_payload_if_needed"),
            ({"type": "text", "content": "docm", "cs1": "docm"}, "_ocr_docx_payload_if_needed"),
            ({"type": "text", "content": "xlsx", "cs1": "xlsx"}, "_ocr_xlsx_payload_if_needed"),
            ({"type": "text", "content": "pptx", "cs1": "pptx"}, "_ocr_pptx_payload_if_needed"),
            ({"type": "text", "content": "ppt", "cs1": "ppt"}, "_ocr_pptx_payload_if_needed"),
        ],
    )
    async def test_each_kind_goes_through_its_ocr_step(self, make_client, env, monkeypatch, payload, ocr_function):
        calls = []
        monkeypatch.setattr(module, "_extract_inline_payload", lambda content, filename, ct: dict(payload))

        async def step(content, filename, current, *rest):
            calls.append((ocr_function, content, filename, rest))
            return {**current, "content": "после OCR"}, "предупреждение OCR"

        monkeypatch.setattr(module, ocr_function, step)

        body = (await _post(make_client, name="doc.pdf", content=b"raw", ctype="application/pdf")).json()

        assert calls == [(ocr_function, b"raw", "doc.pdf", ("m/path",) if ocr_function == "_ocr_image_payload_if_needed" else ())]
        assert body["content"] == "после OCR" and body["warning"] == "предупреждение OCR"

    async def test_plain_text_skips_ocr(self, make_client, env, monkeypatch):
        for name in (
            "_ocr_image_payload_if_needed",
            "_ocr_pdf_payload_if_needed",
            "_ocr_docx_payload_if_needed",
            "_ocr_xlsx_payload_if_needed",
            "_ocr_pptx_payload_if_needed",
        ):
            monkeypatch.setattr(module, name, lambda *a, **k: pytest.fail("OCR не должен вызываться"))

        assert (await _post(make_client)).json()["content"] == "hello"

    async def test_ocr_warning_is_appended_to_the_storage_warning(self, make_client, env, monkeypatch):
        monkeypatch.delenv(module.INLINE_ATTACH_DIR_ENV)
        monkeypatch.setattr(module, "_extract_inline_payload", lambda *a: {"type": "text", "content": "x", "cs1": "pdf"})

        async def step(content, filename, payload):
            return payload, "OCR не настроен"

        monkeypatch.setattr(module, "_ocr_pdf_payload_if_needed", step)

        warning = (await _post(make_client, name="a.pdf", ctype="application/pdf")).json()["warning"]

        assert warning.startswith("Не удалось сохранить файл") and warning.endswith(" OCR не настроен")

    async def test_image_for_text_only_model_is_still_accepted(self, make_client, env, monkeypatch):
        async def text_only():
            return "prov", False

        monkeypatch.setattr(module, "_attachment_provider_vision", text_only)
        monkeypatch.setattr(module, "_extract_inline_payload", lambda *a: {"type": "image", "content": "data:image/png;base64,AA", "cs1": "image"})

        async def step(content, filename, payload, model_path):
            return {"type": "text", "content": "распознано", "cs1": "image-ocr"}, None

        monkeypatch.setattr(module, "_ocr_image_payload_if_needed", step)

        body = (await _post(make_client, name="p.png", ctype="image/png")).json()

        assert body["type"] == "text" and body["content"] == "распознано"
        assert env.events[-1][1]["extra"]["cs1"] == "image-ocr"


class TestExtractErrors:
    async def test_extraction_error_is_a_500_and_removes_the_stored_file(self, make_client, env, monkeypatch):
        def broken(content, filename, ct):
            raise ValueError("битый документ")

        monkeypatch.setattr(module, "_extract_inline_payload", broken)

        response = await _post(make_client, name="a.docx", ctype="")

        assert response.status_code == 500
        assert response.json()["detail"] == "Не удалось извлечь содержимое файла"
        assert _stored(env.dir) == []
        (code, kwargs) = env.events[-1]
        assert code == "FS008" and kwargs["status_code"] == 500 and kwargs["extra"]["cs1"] == "extract_error"

    async def test_extraction_error_without_stored_file(self, make_client, env, monkeypatch):
        monkeypatch.delenv(module.INLINE_ATTACH_DIR_ENV)

        def broken(content, filename, ct):
            raise ValueError("битый документ")

        monkeypatch.setattr(module, "_extract_inline_payload", broken)

        assert (await _post(make_client)).status_code == 500

    async def test_corrupt_real_pdf_is_a_500(self, make_client, env):
        response = await _post(make_client, name="broken.pdf", content=b"not really a pdf", ctype="application/pdf")

        assert response.status_code == 500 and response.json()["detail"] == "Не удалось извлечь содержимое файла"
        assert _stored(env.dir) == []

    async def test_error_text_is_not_returned_to_the_client(self, make_client, env, monkeypatch):
        # BUG-BE-08 исправлен: сообщение исключения (пути/детали библиотек) клиенту не отдаётся.
        def broken(content, filename, ct):
            raise RuntimeError("C:\\srv\\app\\secret\\path failed")

        monkeypatch.setattr(module, "_extract_inline_payload", broken)

        response = await _post(make_client)

        assert "secret" not in response.json()["detail"]
        assert response.json()["detail"] == "Не удалось извлечь содержимое файла"
