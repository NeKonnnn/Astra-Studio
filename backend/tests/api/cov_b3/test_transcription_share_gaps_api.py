"""routes/transcription (MinIO, остановка, диаризация) и routes/share (ошибки БД) — остатки покрытия."""

import os
import tempfile
from types import SimpleNamespace

import pytest

from backend.routes import share as share_module
from backend.routes import transcription as module
from backend.routes import voice as voice_module
from backend.tests.api.conftest import ALICE
from backend.utils import url_safety

pytestmark = pytest.mark.unit

BASE = "/api/transcribe"


class FakeMinio:
    def __init__(self, tmp_path, fail_upload=False):
        self.tmp_path = tmp_path
        self.fail_upload = fail_upload
        self.uploaded = []
        self.deleted = []
        self.local = None
        self.delete_error = False

    def generate_object_name(self, prefix, extension):
        return f"{prefix}1{extension}"

    def upload_file(self, content, name, content_type=None):
        if self.fail_upload:
            raise RuntimeError("minio down")
        self.uploaded.append((content, name, content_type))
        self.local = self.tmp_path / name
        self.local.write_bytes(content)

    def get_file_path(self, name):
        return str(self.local)

    def delete_file(self, name):
        self.deleted.append(name)
        if self.delete_error:
            raise RuntimeError("не удалось удалить")


class Transcriber:
    def __init__(self):
        self.calls = []
        self.result = (True, "[SPEAKER_1] привет")

    def transcribe_with_diarization(self, path):
        self.calls.append(path)
        return self.result

    def transcribe_youtube(self, url):
        self.calls.append(url)
        return self.result


@pytest.fixture(autouse=True)
def _env(services, monkeypatch, tmp_path):
    services(module, "diarization", "stt")
    monkeypatch.setattr(module, "minio_client", None)
    monkeypatch.setattr(module.state, "stop_transcription_flags", {})
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    # Без сети: имя хоста YouTube резолвится в публичный адрес (проверка URL делает DNS-запрос)
    monkeypatch.setattr(url_safety, "_default_resolver", lambda host: ["93.184.216.34"])


@pytest.fixture
def transcriber(monkeypatch):
    fake = Transcriber()
    monkeypatch.setattr(module.state, "transcriber", fake)
    return fake


def _upload(client, path="/upload", name="meeting.mp3", **data):
    return client.post(BASE + path, files={"file": (name, b"ID3audio", "audio/mpeg")}, data=data)


# ------------------------------------------------------------------- /upload


async def test_upload_goes_through_minio_and_cleans_it_up(make_client, transcriber, monkeypatch, tmp_path):
    minio = FakeMinio(tmp_path)
    monkeypatch.setattr(module, "minio_client", minio)

    response = await _upload(make_client(module.router))

    assert response.status_code == 200
    assert minio.uploaded == [(b"ID3audio", "media_1.mp3", "application/octet-stream")]
    assert transcriber.calls == [str(minio.local)]
    assert minio.deleted == ["media_1.mp3"] and not minio.local.exists()


async def test_upload_minio_name_without_extension_and_failing_delete(make_client, transcriber, monkeypatch, tmp_path):
    minio = FakeMinio(tmp_path)
    minio.delete_error = True
    monkeypatch.setattr(module, "minio_client", minio)

    response = await _upload(make_client(module.router), name="audio")

    # без расширения имя объекта «media_1»; ошибка удаления из MinIO не ломает ответ
    assert response.status_code == 200 and minio.uploaded[0][1] == "media_1"
    assert minio.deleted == ["media_1"]


async def test_upload_falls_back_to_local_file_when_minio_fails(make_client, transcriber, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "minio_client", FakeMinio(tmp_path, fail_upload=True))

    response = await _upload(make_client(module.router))

    assert response.status_code == 200
    (path,) = transcriber.calls
    assert path.startswith(str(tmp_path)) and not os.path.exists(path)


async def test_upload_stopped_while_saving_the_file_is_499(make_client, transcriber, monkeypatch, tmp_path):
    minio = FakeMinio(tmp_path)

    def upload_and_stop(content, name, content_type=None):
        FakeMinio.upload_file(minio, content, name, content_type)
        module.state.stop_transcription_flags["job-1"] = True

    minio.upload_file = upload_and_stop
    monkeypatch.setattr(module, "minio_client", minio)

    response = await _upload(make_client(module.router), request_id="job-1")

    assert response.status_code == 499 and "остановлена" in response.json()["detail"]
    assert transcriber.calls == [] and minio.deleted == ["media_1.mp3"]


class StoppingLoop:
    """Цикл событий, у которого флаг остановки взводится прямо перед запуском задачи в потоке."""

    def __init__(self, tid):
        self.tid = tid

    def run_in_executor(self, executor, fn, *args):
        module.state.stop_transcription_flags[self.tid] = True

        async def run():
            return fn(*args)

        return run()


async def test_upload_stopped_right_before_transcription_is_499(make_client, transcriber, monkeypatch):
    monkeypatch.setattr(module.asyncio, "get_event_loop", lambda: StoppingLoop("job-2"))

    response = await _upload(make_client(module.router), request_id="job-2")

    assert response.status_code == 499 and response.json()["detail"] == "Транскрибация была остановлена"
    assert transcriber.calls == []


async def test_upload_transcriber_failure_message_with_stop_word_is_499(make_client, transcriber):
    transcriber.result = (False, "Работа ОСТАНОВЛЕНА пользователем")

    response = await _upload(make_client(module.router))

    assert response.status_code == 499


async def test_upload_transcriber_failure_is_400(make_client, transcriber):
    transcriber.result = (False, "не удалось декодировать")

    response = await _upload(make_client(module.router))

    assert response.status_code == 400 and response.json()["detail"] == "не удалось декодировать"


# ---------------------------------------------------------- /upload/diarization


async def test_diarization_upload_with_minio(make_client, transcriber, monkeypatch, tmp_path):
    minio = FakeMinio(tmp_path)
    monkeypatch.setattr(module, "minio_client", minio)

    response = await _upload(make_client(module.router), path="/upload/diarization")

    assert response.json() == {"transcription": "[SPEAKER_1] привет", "filename": "meeting.mp3", "success": True, "diarization": True}
    assert minio.uploaded[0][1] == "media_diarization_1.mp3"
    assert minio.deleted == ["media_diarization_1.mp3"]


async def test_diarization_upload_minio_failure_falls_back_to_local_file(make_client, transcriber, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "minio_client", FakeMinio(tmp_path, fail_upload=True))

    response = await _upload(make_client(module.router), path="/upload/diarization")

    assert response.status_code == 200
    (path,) = transcriber.calls
    assert "media_diar_" in os.path.basename(path) and not os.path.exists(path)


async def test_diarization_upload_without_diarization_support_uses_plain_transcription(make_client, monkeypatch):
    class Plain:
        def transcribe_audio_file(self, path):
            return True, "обычный текст"

    monkeypatch.setattr(module.state, "transcriber", Plain())

    body = (await _upload(make_client(module.router), path="/upload/diarization")).json()

    assert body["transcription"] == "обычный текст"


async def test_diarization_upload_failure_and_crash(make_client, transcriber):
    transcriber.result = (False, "тишина в записи")
    client = make_client(module.router)

    failed = await _upload(client, path="/upload/diarization")
    assert failed.status_code == 400 and failed.json()["detail"] == "тишина в записи"

    def crash(path):
        raise RuntimeError("модель упала")

    transcriber.transcribe_with_diarization = crash
    crashed = await _upload(client, path="/upload/diarization")
    assert crashed.status_code == 500 and crashed.json()["detail"] == "Внутренняя ошибка сервера"


async def test_diarization_upload_without_transcriber(make_client, monkeypatch):
    monkeypatch.setattr(module.state, "transcriber", None)

    assert (await _upload(make_client(module.router), path="/upload/diarization")).status_code == 503


# ---------------------------------------------------------------------- /youtube


async def test_youtube_unexpected_error_is_500(make_client, transcriber):
    def crash(url):
        raise RuntimeError("yt-dlp сломан")

    transcriber.transcribe_youtube = crash

    response = await make_client(module.router).post(BASE + "/youtube", json={"url": "https://youtu.be/x"})

    assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"


async def test_youtube_failure_result_is_400(make_client, transcriber):
    transcriber.result = (False, "видео недоступно")

    response = await make_client(module.router).post(BASE + "/youtube", json={"url": "https://youtu.be/x"})

    assert response.status_code == 400 and response.json()["detail"] == "видео недоступно"


# ------------------------------------------------------------------- голосовые настройки


async def test_transcription_settings_with_empty_body_only_resaves_current_values(make_client, monkeypatch):
    saved = []
    monkeypatch.setattr(voice_module.state, "current_transcription_engine", "whisperx")
    monkeypatch.setattr(voice_module.state, "current_transcription_language", "ru")
    monkeypatch.setattr(voice_module, "save_app_settings", lambda d: saved.append(d))

    response = await make_client(voice_module.router).put("/api/transcription/settings", json={})

    assert response.json()["success"] is True
    assert saved == [{"transcription_engine": "whisperx", "transcription_language": "ru"}]


async def test_transcription_settings_engine_only_keeps_language(make_client, monkeypatch):
    switched = []
    transcriber = SimpleNamespace(switch_engine=switched.append)
    monkeypatch.setattr(voice_module.state, "transcriber", transcriber)
    monkeypatch.setattr(voice_module.state, "current_transcription_engine", "whisperx")
    monkeypatch.setattr(voice_module.state, "current_transcription_language", "ru")
    monkeypatch.setattr(voice_module, "save_app_settings", lambda d: None)

    await make_client(voice_module.router).put("/api/transcription/settings", json={"engine": "Whisperx"})

    assert switched == ["whisperx"] and voice_module.state.current_transcription_language == "ru"


# ------------------------------------------------------------------ routes/share.py


class BoomCollection:
    def find(self, query):
        raise RuntimeError("mongo exploded")

    async def find_one(self, query):
        raise RuntimeError("mongo exploded")

    async def delete_one(self, query):
        raise RuntimeError("mongo exploded")


@pytest.fixture
def broken_db(monkeypatch):
    conn = SimpleNamespace(db=SimpleNamespace(shared_conversations=BoomCollection()))
    monkeypatch.setattr(share_module, "get_mongodb_connection", lambda: conn)


@pytest.fixture
def no_db(monkeypatch):
    monkeypatch.setattr(share_module, "get_mongodb_connection", lambda: None)


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/share/my-shares"), ("GET", "/api/share/abc"), ("DELETE", "/api/share/abc")],
)
async def test_share_endpoints_report_unavailable_database(make_client, no_db, method, path):
    response = await make_client(share_module.router).request(method, path)

    assert response.status_code == 500 and response.json()["detail"] == "База данных недоступна"


async def test_share_database_without_db_object_is_unavailable(make_client, monkeypatch):
    monkeypatch.setattr(share_module, "get_mongodb_connection", lambda: SimpleNamespace(db=None))

    response = await make_client(share_module.router).get("/api/share/my-shares")

    assert response.status_code == 500


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/share/my-shares"), ("GET", "/api/share/abc"), ("DELETE", "/api/share/abc")],
)
async def test_share_unexpected_errors_do_not_leak_details(make_client, broken_db, method, path):
    response = await make_client(share_module.router).request(method, path)

    assert response.status_code == 500
    assert response.json()["detail"] == "Внутренняя ошибка сервера"
    assert "mongo" not in response.text


# ------------------------------------------------------------------- хвосты


class CrashingLoop:
    def run_in_executor(self, executor, fn, *args):
        raise RuntimeError("пул потоков недоступен")


async def test_upload_unexpected_failure_is_500(make_client, transcriber, monkeypatch):
    monkeypatch.setattr(module.asyncio, "get_event_loop", lambda: CrashingLoop())

    response = await _upload(make_client(module.router))

    assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"
    assert transcriber.calls == []


async def test_diarization_cleanup_tolerates_file_removed_by_transcriber(make_client, transcriber):
    def consume(path):
        os.remove(path)
        return True, "текст"

    transcriber.transcribe_with_diarization = consume

    response = await _upload(make_client(module.router), path="/upload/diarization")

    assert response.status_code == 200 and response.json()["transcription"] == "текст"
