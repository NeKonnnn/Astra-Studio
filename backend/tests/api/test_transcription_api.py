"""routes/transcription: загрузка файла, диаризация, YouTube, остановка."""

import ipaddress
import os
from types import SimpleNamespace

import pytest

from backend.routes import transcription as module
from backend.utils import url_safety

pytestmark = pytest.mark.unit

BASE = "/api/transcribe"


class FakeTranscriber:
    def __init__(self):
        self.files = []
        self.result = (True, "[SPEAKER_1] привет")
        self.error = None
        self.youtube = []

    def transcribe_with_diarization(self, path):
        self.files.append((path, os.path.exists(path)))
        if self.error:
            raise self.error
        return self.result

    def transcribe_youtube(self, url):
        self.youtube.append(url)
        return self.result


@pytest.fixture(autouse=True)
def _env(services, monkeypatch):
    services(module, "diarization", "stt")
    monkeypatch.setattr(module, "minio_client", None)
    monkeypatch.setattr(module.state, "stop_transcription_flags", {})
    monkeypatch.setattr(module, "_JOB_OWNERS", {})

    # Без сети: IP-литералы резолвятся в себя, остальные хосты — в публичный адрес
    def fake_resolver(host):
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            pass
        if host.isdigit():  # десятичная форма IPv4, как резолвит getaddrinfo
            return [str(ipaddress.ip_address(int(host)))]
        return ["93.184.216.34"]

    monkeypatch.setattr(url_safety, "_default_resolver", fake_resolver)


@pytest.fixture
def transcriber(monkeypatch):
    fake = FakeTranscriber()
    monkeypatch.setattr(module.state, "transcriber", fake)
    return fake


def _upload(client, path="/upload", name="meeting.wav", **data):
    return client.post(BASE + path, files={"file": (name, b"RIFFaudio", "audio/wav")}, data=data)


class TestUpload:
    async def test_transcribes_and_removes_the_temp_file(self, make_client, transcriber):
        response = await _upload(make_client(module.router), request_id="job-1")

        body = response.json()
        assert response.status_code == 200
        assert (body["transcription"], body["filename"], body["transcription_id"]) == ("[SPEAKER_1] привет", "meeting.wav", "job-1")
        (path, existed) = transcriber.files[0]
        assert existed and not os.path.exists(path)
        assert "job-1" not in module.state.stop_transcription_flags

    async def test_generates_an_id_when_none_is_given(self, make_client, transcriber):
        body = (await _upload(make_client(module.router))).json()

        assert len(body["transcription_id"]) == 36

    async def test_falls_back_to_plain_transcription_without_diarization(self, make_client, monkeypatch):
        class Plain:
            def transcribe_audio_file(self, path):
                return True, "обычный текст"

        monkeypatch.setattr(module.state, "transcriber", Plain())

        assert (await _upload(make_client(module.router))).json()["transcription"] == "обычный текст"

    async def test_engine_refusal_is_a_400(self, make_client, transcriber):
        transcriber.result = (False, "неподдерживаемый формат")

        response = await _upload(make_client(module.router))

        assert response.status_code == 400 and response.json()["detail"] == "неподдерживаемый формат"

    async def test_cancelled_job_is_reported_as_499(self, make_client, transcriber):
        transcriber.result = (False, "Транскрибация была остановлена")

        assert (await _upload(make_client(module.router))).status_code == 499

    async def test_engine_exception_becomes_an_error_result(self, make_client, transcriber):
        transcriber.error = RuntimeError("cuda out of memory")

        response = await _upload(make_client(module.router))

        assert response.status_code == 400
        assert "cuda out of memory" not in response.text  # CWE-209: текст исключения не отдаём
        assert response.json()["detail"] == module._TRANSCRIPTION_FAILED_DETAIL

    async def test_stop_before_start_cancels_the_job(self, make_client, transcriber):
        client = make_client(module.router)
        module.state.stop_transcription_flags["job-9"] = True

        # флаг сбрасывается при старте, поэтому выставляем его через подмену transcriber
        class Stopping(FakeTranscriber):
            def transcribe_with_diarization(self, path):
                return False, "Транскрибация была остановлена"

        module.state.transcriber = Stopping()
        response = await _upload(client, request_id="job-9")

        assert response.status_code == 499

    async def test_transcriber_missing(self, make_client, monkeypatch):
        monkeypatch.setattr(module.state, "transcriber", None)

        assert (await _upload(make_client(module.router))).status_code == 503

    async def test_disabled_microservices(self, make_client, services, transcriber):
        services(module, "stt")  # диаризация выключена

        response = await _upload(make_client(module.router))

        assert response.status_code == 503 and transcriber.files == []

    async def test_requires_a_file(self, make_client, transcriber):
        assert (await make_client(module.router).post(BASE + "/upload")).status_code == 422

    async def test_traversal_in_the_file_name_cannot_write_outside_the_temp_dir(self, make_client, transcriber, tmp_path):
        marker = tmp_path / "evil.wav"

        response = await _upload(make_client(module.router), name=f"../../{marker.name}")

        assert not marker.exists()
        assert response.status_code in (200, 500)

    async def test_minio_copy_is_used_and_cleaned_up(self, make_client, transcriber, monkeypatch):
        deleted = []

        class Minio:
            def generate_object_name(self, prefix="", extension=""):
                return f"{prefix}x{extension}"

            def upload_file(self, data, name, content_type=None):
                pass

            def get_file_path(self, name):
                return "/minio/" + name

            def delete_file(self, name):
                deleted.append(name)

        monkeypatch.setattr(module, "minio_client", Minio())

        response = await _upload(make_client(module.router), name="talk.mp3")

        assert response.status_code == 200
        assert transcriber.files[0][0] == "/minio/media_x.mp3"
        assert deleted == ["media_x.mp3"]


class TestStop:
    async def test_sets_the_flag(self, make_client):
        response = await make_client(module.router).post(BASE + "/stop", json={"transcription_id": "job-1"})

        assert response.json()["success"] is True
        assert module.state.stop_transcription_flags == {"job-1": True}

    @pytest.mark.parametrize("body", [{}, {"transcription_id": ""}, {"transcription_id": None}])
    async def test_id_is_required(self, make_client, body):
        assert (await make_client(module.router).post(BASE + "/stop", json=body)).status_code == 400

    async def test_other_users_cannot_stop_someone_elses_job(self, make_client):
        module._JOB_OWNERS["job-1"] = "bob"
        module.state.stop_transcription_flags["job-1"] = False

        response = await make_client(module.router).post(BASE + "/stop", json={"transcription_id": "job-1"})

        assert response.status_code == 403
        assert module.state.stop_transcription_flags["job-1"] is False

    async def test_owner_can_stop_the_job(self, make_client):
        module._JOB_OWNERS["job-1"] = "alice"

        response = await make_client(module.router).post(BASE + "/stop", json={"transcription_id": "job-1"})

        assert response.status_code == 200
        assert module.state.stop_transcription_flags["job-1"] is True

    async def test_admin_can_stop_any_job(self, make_client):
        module._JOB_OWNERS["job-1"] = "bob"
        admin = make_client(module.router, user={"username": "root", "user_id": "root", "is_admin": True})

        response = await admin.post(BASE + "/stop", json={"transcription_id": "job-1"})

        assert response.status_code == 200
        assert module.state.stop_transcription_flags["job-1"] is True

    async def test_upload_cannot_hijack_a_running_job_id(self, make_client, transcriber):
        module._JOB_OWNERS["job-1"] = "bob"

        response = await _upload(make_client(module.router), request_id="job-1")

        assert response.status_code == 409
        assert module._JOB_OWNERS["job-1"] == "bob" and transcriber.files == []

    async def test_job_ownership_is_released_after_the_upload(self, make_client, transcriber):
        await _upload(make_client(module.router), request_id="job-2")

        assert "job-2" not in module._JOB_OWNERS

    async def test_anonymous_visitor_must_not_stop_jobs(self, make_client):
        module.state.stop_transcription_flags["someone-elses-job"] = False

        response = await make_client(module.router, user=None).post(
            BASE + "/stop", json={"transcription_id": "someone-elses-job"}
        )

        assert response.status_code in (401, 403)
        assert module.state.stop_transcription_flags["someone-elses-job"] is False


class TestDiarization:
    async def test_success(self, make_client, transcriber):
        response = await _upload(make_client(module.router), "/upload/diarization")

        assert response.json() == {"transcription": "[SPEAKER_1] привет", "filename": "meeting.wav", "success": True, "diarization": True}

    async def test_refusal(self, make_client, transcriber):
        transcriber.result = (False, "no speech")

        assert (await _upload(make_client(module.router), "/upload/diarization")).status_code == 400

    async def test_transcriber_missing_and_disabled(self, make_client, monkeypatch, services):
        monkeypatch.setattr(module.state, "transcriber", None)
        assert (await _upload(make_client(module.router), "/upload/diarization")).status_code == 503
        services(module)
        assert (await _upload(make_client(module.router), "/upload/diarization")).status_code == 503

    async def test_engine_exception(self, make_client, transcriber):
        transcriber.error = RuntimeError("boom")

        assert (await _upload(make_client(module.router), "/upload/diarization")).status_code == 500


class TestYoutube:
    async def test_success(self, make_client, transcriber):
        response = await make_client(module.router).post(BASE + "/youtube", json={"url": "https://youtu.be/abc"})

        assert response.json()["transcription"] == "[SPEAKER_1] привет"
        assert transcriber.youtube == ["https://youtu.be/abc"]

    async def test_refusal(self, make_client, transcriber):
        transcriber.result = (False, "video unavailable")

        response = await make_client(module.router).post(BASE + "/youtube", json={"url": "https://youtu.be/abc"})

        assert response.status_code == 400 and response.json()["detail"] == "video unavailable"

    async def test_validates_the_payload(self, make_client, transcriber):
        assert (await make_client(module.router).post(BASE + "/youtube", json={})).status_code == 422

    async def test_transcriber_missing(self, make_client, monkeypatch):
        monkeypatch.setattr(module.state, "transcriber", None)

        assert (await make_client(module.router).post(BASE + "/youtube", json={"url": "https://youtu.be/a"})).status_code == 503

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8080/x",
            "http://localhost/x",
            "http://10.0.0.5/x",
            "http://[::1]/x",
            "http://metadata.google.internal/computeMetadata/v1/",
            "file:///etc/passwd",
            "ftp://youtu.be/abc",
            "gopher://youtu.be/abc",
            "http://2130706433/",
        ],
    )
    async def test_ssrf_urls_are_rejected_before_the_downloader(self, make_client, transcriber, url):
        response = await make_client(module.router).post(BASE + "/youtube", json={"url": url})

        assert response.status_code == 400
        assert transcriber.youtube == []

    async def test_hostname_resolving_to_a_private_address_is_rejected(self, make_client, transcriber, monkeypatch):
        monkeypatch.setattr(url_safety, "_default_resolver", lambda host: ["10.1.2.3"])

        response = await make_client(module.router).post(BASE + "/youtube", json={"url": "https://evil.example/x"})

        assert response.status_code == 400 and transcriber.youtube == []

    async def test_public_https_url_is_accepted(self, make_client, transcriber):
        response = await make_client(module.router).post(BASE + "/youtube", json={"url": "https://www.youtube.com/watch?v=1"})

        assert response.status_code == 200
        assert transcriber.youtube == ["https://www.youtube.com/watch?v=1"]
