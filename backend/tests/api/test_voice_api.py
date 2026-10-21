"""routes/voice: синтез и распознавание речи, настройки голоса и транскрибации."""

import os
from types import SimpleNamespace

import pytest

from backend.routes import voice as module
from backend.utils.http_errors import GENERIC_INTERNAL_ERROR_DETAIL

pytestmark = pytest.mark.unit


class FakeMinio:
    def __init__(self):
        self.uploaded = []
        self.deleted = []
        self.fail_upload = False

    def generate_object_name(self, prefix="", extension=""):
        return f"{prefix}obj{extension}"

    def upload_file(self, data, name, content_type=None):
        if self.fail_upload:
            raise RuntimeError("minio down")
        self.uploaded.append((name, len(data), content_type))

    def get_file_path(self, name):
        return f"/minio/{name}"

    def delete_file(self, name):
        self.deleted.append(name)


class TestSynthesize:
    URL = "/api/voice/synthesize"

    @pytest.fixture(autouse=True)
    def _enabled(self, services):
        services(module, "tts", "stt")

    @pytest.fixture
    def speak(self, monkeypatch):
        state = SimpleNamespace(calls=[], succeed=True, error=None)

        def fake(text, speaker, voice_id, speech_rate, save_to_file):
            state.calls.append({"text": text, "speaker": speaker, "voice_id": voice_id, "rate": speech_rate})
            if state.error:
                raise state.error
            if state.succeed:
                with open(save_to_file, "wb") as f:
                    f.write(b"RIFFwav")
            return state.succeed

        monkeypatch.setattr(module, "speak_text", fake)
        monkeypatch.setattr(module, "minio_client", None)
        return state

    async def test_returns_the_wav_and_forwards_parameters(self, make_client, speak):
        response = await make_client(module.router).post(
            self.URL, json={"text": "привет", "voice_id": "ru", "voice_speaker": "xenia", "speech_rate": 1.5}
        )

        assert response.status_code == 200
        assert response.content == b"RIFFwav"
        assert response.headers["content-type"] == "audio/wav"
        assert speak.calls == [{"text": "привет", "speaker": "xenia", "voice_id": "ru", "rate": 1.5}]

    async def test_temp_files_are_removed(self, make_client, speak):
        import glob
        import tempfile

        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "speech_*.wav")))

        await make_client(module.router).post(self.URL, json={"text": "привет"})

        assert set(glob.glob(os.path.join(tempfile.gettempdir(), "speech_*.wav"))) == before

    async def test_a_copy_is_uploaded_to_minio_but_upload_errors_do_not_matter(self, make_client, speak, monkeypatch):
        minio = FakeMinio()
        monkeypatch.setattr(module, "minio_client", minio)

        ok = await make_client(module.router).post(self.URL, json={"text": "привет"})
        minio.fail_upload = True
        still_ok = await make_client(module.router).post(self.URL, json={"text": "привет"})

        assert ok.status_code == 200 and still_ok.status_code == 200
        assert minio.uploaded == [("speech_obj.wav", 7, "audio/wav")]

    async def test_failed_synthesis(self, make_client, speak):
        speak.succeed = False

        response = await make_client(module.router).post(self.URL, json={"text": "привет"})

        assert response.status_code == 500

    async def test_engine_error_does_not_leak_its_message(self, make_client, speak):
        speak.error = RuntimeError("model file /opt/secret/path.onnx missing")

        response = await make_client(module.router).post(self.URL, json={"text": "привет"})

        assert response.status_code == 500
        assert "/opt/secret/path.onnx" not in response.text
        assert response.json()["detail"] == GENERIC_INTERNAL_ERROR_DETAIL

    async def test_module_not_available(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "speak_text", None)

        assert (await make_client(module.router).post(self.URL, json={"text": "x"})).status_code == 503

    async def test_disabled_microservice(self, make_client, services, speak):
        services(module)

        response = await make_client(module.router).post(self.URL, json={"text": "x"})

        assert response.status_code == 503
        assert speak.calls == []

    @pytest.mark.parametrize("body", [{}, {"text": 5}, {"text": "x", "speech_rate": "fast"}])
    async def test_validates_the_payload(self, make_client, speak, body):
        assert (await make_client(module.router).post(self.URL, json=body)).status_code == 422


class TestRecognize:
    URL = "/api/voice/recognize"

    @pytest.fixture(autouse=True)
    def _enabled(self, services):
        services(module, "tts", "stt")

    @pytest.fixture
    def recognize(self, monkeypatch):
        state = SimpleNamespace(paths=[], text="распознанный текст", error=None)

        def fake(path):
            state.paths.append(path)
            assert os.path.exists(path) or path.startswith("/minio/")
            if state.error:
                raise state.error
            return state.text

        monkeypatch.setattr(module, "recognize_speech_from_file", fake)
        monkeypatch.setattr(module, "minio_client", None)
        return state

    @staticmethod
    def _upload(client):
        return client.post("/api/voice/recognize", files={"audio_file": ("a.wav", b"RIFF....", "audio/wav")})

    async def test_recognizes_an_uploaded_file_and_cleans_up(self, make_client, recognize):
        response = await self._upload(make_client(module.router))

        body = response.json()
        assert body["text"] == "распознанный текст" and body["success"] is True
        assert not os.path.exists(recognize.paths[0])

    async def test_minio_copy_is_used_and_deleted(self, make_client, recognize, monkeypatch):
        minio = FakeMinio()
        monkeypatch.setattr(module, "minio_client", minio)

        response = await self._upload(make_client(module.router))

        assert response.status_code == 200
        assert recognize.paths == ["/minio/audio_obj.wav"]
        assert minio.deleted == ["audio_obj.wav"]

    async def test_minio_failure_falls_back_to_a_temp_file(self, make_client, recognize, monkeypatch):
        minio = FakeMinio()
        minio.fail_upload = True
        monkeypatch.setattr(module, "minio_client", minio)

        response = await self._upload(make_client(module.router))

        assert response.status_code == 200
        assert recognize.paths[0].endswith(".wav") and not recognize.paths[0].startswith("/minio")

    async def test_engine_error(self, make_client, recognize):
        recognize.error = RuntimeError("stt crashed")

        assert (await self._upload(make_client(module.router))).status_code == 500

    async def test_module_not_available(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "recognize_speech_from_file", None)

        response = await self._upload(make_client(module.router))

        assert response.json() == {"text": "", "success": False, "error": "Модуль распознавания речи недоступен."}

    async def test_disabled_microservice(self, make_client, services, recognize):
        services(module)

        assert (await self._upload(make_client(module.router))).status_code == 503
        assert recognize.paths == []

    async def test_requires_a_file(self, make_client, recognize):
        assert (await make_client(module.router).post(self.URL)).status_code == 422


class TestSettings:
    async def test_voice_settings_are_constants(self, make_client):
        response = await make_client(module.router).get("/api/voice/settings")

        assert response.json() == {"voice_id": "ru", "speech_rate": 1.0, "voice_speaker": "baya"}

    async def test_voice_settings_update_honestly_reports_that_nothing_is_persisted(self, make_client):
        client = make_client(module.router)

        update = await client.put("/api/voice/settings", json={"voice_id": "en", "speech_rate": 2.0, "voice_speaker": "x"})
        after = await client.get("/api/voice/settings")

        body = update.json()
        assert body["success"] is True and body["settings"]["voice_id"] == "en"
        assert body["persisted"] is False  # сервер не хранит настройки и об этом говорит
        assert after.json()["voice_id"] == "ru"

    @pytest.mark.parametrize("engine", ["evil-engine", "../../etc/passwd", "whisperx; rm -rf /", "pyttsx3"])
    async def test_unknown_transcription_engine_is_rejected(self, make_client, monkeypatch, engine):
        saved, switched = [], []
        monkeypatch.setattr(module.state, "transcriber", SimpleNamespace(switch_engine=switched.append))
        monkeypatch.setattr(module.state, "current_transcription_engine", "whisperx")
        monkeypatch.setattr(module, "save_app_settings", saved.append)

        response = await make_client(module.router).put("/api/transcription/settings", json={"engine": engine})

        assert response.status_code == 400
        assert module.state.current_transcription_engine == "whisperx"
        assert saved == [] and switched == []

    @pytest.mark.parametrize("language", ["../x", "ru; drop", "x" * 40, "1"])
    async def test_malformed_transcription_language_is_rejected(self, make_client, monkeypatch, language):
        monkeypatch.setattr(module.state, "transcriber", None)
        monkeypatch.setattr(module.state, "current_transcription_language", "ru")
        monkeypatch.setattr(module, "save_app_settings", lambda data: None)

        response = await make_client(module.router).put("/api/transcription/settings", json={"language": language})

        assert response.status_code == 400
        assert module.state.current_transcription_language == "ru"

    async def test_transcription_settings_read(self, make_client, monkeypatch):
        monkeypatch.setattr(module.state, "current_transcription_engine", "whisperx")
        monkeypatch.setattr(module.state, "current_transcription_language", "ru")

        response = await make_client(module.router).get("/api/transcription/settings")

        assert response.json() == {"engine": "whisperx", "language": "ru", "auto_detect": True}

    async def test_transcription_settings_update(self, make_client, monkeypatch):
        saved, switched, language = [], [], []
        transcriber = SimpleNamespace(switch_engine=switched.append, set_language=language.append)
        monkeypatch.setattr(module.state, "transcriber", transcriber)
        monkeypatch.setattr(module.state, "current_transcription_engine", "whisperx")
        monkeypatch.setattr(module.state, "current_transcription_language", "ru")
        monkeypatch.setattr(module, "save_app_settings", saved.append)

        response = await make_client(module.router).put(
            "/api/transcription/settings", json={"engine": "VOSK", "language": "en"}
        )

        assert response.json() == {"message": "Настройки обновлены", "success": True}
        assert module.state.current_transcription_engine == "whisperx"  # vosk → whisperx
        assert switched == ["whisperx"] and language == ["en"]
        assert saved == [{"transcription_engine": "whisperx", "transcription_language": "en"}]

    async def test_transcription_settings_partial_update_keeps_the_rest(self, make_client, monkeypatch):
        monkeypatch.setattr(module.state, "transcriber", None)
        monkeypatch.setattr(module.state, "current_transcription_engine", "whisperx")
        monkeypatch.setattr(module.state, "current_transcription_language", "ru")
        monkeypatch.setattr(module, "save_app_settings", lambda data: None)

        await make_client(module.router).put("/api/transcription/settings", json={"language": "de"})

        assert (module.state.current_transcription_engine, module.state.current_transcription_language) == ("whisperx", "de")

    async def test_anonymous_visitor_must_not_change_the_global_transcription_engine(self, make_client, monkeypatch):
        monkeypatch.setattr(module.state, "transcriber", None)
        monkeypatch.setattr(module.state, "current_transcription_engine", "whisperx")
        monkeypatch.setattr(module, "save_app_settings", lambda data: None)

        response = await make_client(module.router, user=None).put(
            "/api/transcription/settings", json={"engine": "evil-engine"}
        )

        assert response.status_code in (401, 403)
        assert module.state.current_transcription_engine == "whisperx"

    async def test_persist_failure(self, make_client, monkeypatch):
        monkeypatch.setattr(module.state, "transcriber", None)

        def boom(data):
            raise OSError("disk full")

        monkeypatch.setattr(module, "save_app_settings", boom)

        response = await make_client(module.router).put("/api/transcription/settings", json={"language": "ru"})

        assert response.status_code == 500
