"""Тесты backend/transcription/universal_transcriber.py: фасад над WhisperX (локально или через STT-сервис)."""

import sys

import pytest

from backend.transcription import universal_transcriber as ut


class FakeWX:
    """Подмена WhisperXTranscriber: фиксирует вызовы."""

    instances: list = []
    fail_init = False

    def __init__(self):
        if FakeWX.fail_init:
            raise RuntimeError("модель не загрузилась")
        self.calls = []
        self.result = (True, "локальный текст")
        self.raises = None
        FakeWX.instances.append(self)

    def _rec(self, name, *args):
        self.calls.append((name, *args))

    def set_progress_callback(self, cb):
        self._rec("progress", cb)

    def set_model_size(self, size):
        self._rec("model_size", size)

    def set_language(self, lang):
        self._rec("language", lang)

    def set_compute_type(self, ct):
        self._rec("compute_type", ct)

    def set_hf_token(self, token):
        self._rec("hf_token", token)

    def transcribe_audio_file(self, path):
        self._rec("transcribe", path)
        if self.raises:
            raise self.raises
        return self.result

    def transcribe_youtube(self, url):
        self._rec("youtube", url)
        return (True, "yt:" + url)

    def cleanup(self):
        self._rec("cleanup")


@pytest.fixture(autouse=True)
def local_mode(monkeypatch):
    FakeWX.instances = []
    FakeWX.fail_init = False
    monkeypatch.setattr(ut, "USE_LLM_SVC", False)
    monkeypatch.setattr(ut, "LOCAL_TRANSCRIBERS_AVAILABLE", True)
    monkeypatch.setattr(ut, "WhisperXTranscriber", FakeWX)


@pytest.fixture
def svc_mode(monkeypatch):
    monkeypatch.setattr(ut, "USE_LLM_SVC", True)
    monkeypatch.setattr(ut, "LOCAL_TRANSCRIBERS_AVAILABLE", False)
    monkeypatch.setattr(ut, "WhisperXTranscriber", None)


# ---------------- импорт модуля ----------------


def test_import_local_transcribers_unavailable_when_whisperx_module_missing(monkeypatch, load_copy):
    monkeypatch.delenv("USE_LLM_SVC", raising=False)
    monkeypatch.setitem(sys.modules, "backend.transcription.whisperx_transcriber", None)
    mod = load_copy("universal_transcriber.py", "ut_copy_noimport")
    assert mod.LOCAL_TRANSCRIBERS_AVAILABLE is False and mod.WhisperXTranscriber is None


def test_import_in_svc_mode_does_not_load_local_models(monkeypatch, load_copy):
    monkeypatch.setenv("USE_LLM_SVC", "TRUE")
    monkeypatch.setitem(sys.modules, "backend.transcription.whisperx_transcriber", None)
    mod = load_copy("universal_transcriber.py", "ut_copy_svc")
    assert mod.USE_LLM_SVC is True
    assert mod.LOCAL_TRANSCRIBERS_AVAILABLE is False and mod.WhisperXTranscriber is None


def test_import_local_mode_exposes_whisperx_class(monkeypatch, load_copy):
    monkeypatch.delenv("USE_LLM_SVC", raising=False)
    mod = load_copy("universal_transcriber.py", "ut_copy_local")
    assert mod.LOCAL_TRANSCRIBERS_AVAILABLE is True and mod.WhisperXTranscriber is not None


@pytest.mark.parametrize(
    ("name", "expected"),
    [("vosk", "whisperx"), ("WHISPERX", "whisperx"), ("", "whisperx"), (None, "whisperx"), ("other", "whisperx")],
)
def test_normalize_engine(name, expected):
    assert ut._normalize_engine(name) == expected


# ---------------- инициализация ----------------


def test_init_local_creates_whisperx_instance():
    t = ut.UniversalTranscriber(engine="vosk", hf_token="tok")
    assert t.engine == "whisperx" and t.hf_token == "tok"
    assert t.whisperx_transcriber is FakeWX.instances[0]
    assert t.current_transcriber is t.whisperx_transcriber
    assert t.get_current_engine() == "whisperx"
    assert t.get_hf_token() == "tok"


def test_init_svc_mode_uses_service_marker(svc_mode):
    t = ut.UniversalTranscriber()
    assert t.current_transcriber == "llm-svc" and t.whisperx_transcriber is None


def test_init_fails_when_local_models_missing(monkeypatch):
    monkeypatch.setattr(ut, "LOCAL_TRANSCRIBERS_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="Локальные модели отсутствуют"):
        ut.UniversalTranscriber()


def test_init_propagates_whisperx_failure():
    FakeWX.fail_init = True
    with pytest.raises(RuntimeError, match="модель не загрузилась"):
        ut.UniversalTranscriber()


def test_engine_info_and_available_engines():
    t = ut.UniversalTranscriber()
    assert t.get_available_engines() == ["whisperx"]
    assert t.get_engine_info() == {
        "current_engine": "whisperx",
        "available_engines": ["whisperx"],
        "features": {"diarization": True, "gpu_support": True},
    }


# ---------------- switch_engine ----------------


def test_switch_engine_keeps_whisperx_and_returns_true():
    t = ut.UniversalTranscriber()
    assert t.switch_engine("vosk") is True
    assert t.engine == "whisperx" and len(FakeWX.instances) == 1


def test_switch_engine_creates_missing_local_transcriber():
    t = ut.UniversalTranscriber()
    t.whisperx_transcriber = None
    assert t.switch_engine("whisperx") is True
    assert t.whisperx_transcriber is FakeWX.instances[-1] and t.current_transcriber is t.whisperx_transcriber


def test_switch_engine_logs_change_when_engine_differs():
    t = ut.UniversalTranscriber()
    t.engine = "legacy"
    assert t.switch_engine("whisperx") is True and t.engine == "whisperx"


def test_switch_engine_returns_false_when_loading_fails():
    t = ut.UniversalTranscriber()
    t.whisperx_transcriber = None
    FakeWX.fail_init = True
    assert t.switch_engine("whisperx") is False


def test_switch_engine_in_svc_mode_does_not_create_local(svc_mode):
    t = ut.UniversalTranscriber()
    assert t.switch_engine("whisperx") is True and t.whisperx_transcriber is None


# ---------------- сеттеры ----------------


def test_setters_delegate_to_whisperx():
    t = ut.UniversalTranscriber()
    cb = lambda p: None  # noqa: E731
    t.set_progress_callback(cb)
    t.set_model_size("small")
    t.set_language("en")
    t.set_compute_type("int8")
    t.set_hf_token("new")
    wx = FakeWX.instances[0]
    assert wx.calls == [
        ("progress", cb), ("model_size", "small"), ("language", "en"), ("compute_type", "int8"), ("hf_token", "new"),
    ]
    assert t.get_hf_token() == "new"


def test_setters_are_noops_without_local_transcriber(svc_mode):
    t = ut.UniversalTranscriber()
    t.set_progress_callback(lambda p: None)
    t.set_model_size("x")
    t.set_language("ru")
    t.set_compute_type("x")
    t.set_hf_token("t")
    assert t.get_hf_token() == "t"


def test_set_progress_callback_skips_transcriber_without_support():
    t = ut.UniversalTranscriber()
    t.current_transcriber = object()
    t.set_progress_callback(lambda p: None)  # не падает
    t.set_language("ru")


# ---------------- транскрибация ----------------


def test_transcribe_audio_file_local_success_and_error():
    t = ut.UniversalTranscriber()
    assert t.transcribe_audio_file("a.wav") == (True, "локальный текст")
    FakeWX.instances[0].raises = ValueError("плохой файл")
    assert t.transcribe_audio_file("a.wav") == (False, "плохой файл")


def test_transcribe_audio_file_without_transcriber():
    t = ut.UniversalTranscriber()
    t.whisperx_transcriber = None
    assert t.transcribe_audio_file("a.wav") == (False, "Транскрайбер не инициализирован")


def test_transcribe_audio_file_svc_success(svc_mode, monkeypatch, tmp_path):
    audio = tmp_path / "rec.wav"
    audio.write_bytes(b"RIFFdata")
    seen = {}

    def fake(data, filename, language):
        seen.update(data=data, filename=filename, language=language)
        return "текст из сервиса"

    monkeypatch.setattr(ut, "transcribe_audio_whisperx_llm_svc", fake)
    assert ut.UniversalTranscriber().transcribe_audio_file(str(audio)) == (True, "текст из сервиса")
    assert seen == {"data": b"RIFFdata", "filename": "rec.wav", "language": "auto"}


def test_transcribe_audio_file_svc_empty_and_error(svc_mode, monkeypatch, tmp_path):
    audio = tmp_path / "rec.wav"
    audio.write_bytes(b"x")
    t = ut.UniversalTranscriber()
    monkeypatch.setattr(ut, "transcribe_audio_whisperx_llm_svc", lambda *a, **k: "")
    assert t.transcribe_audio_file(str(audio)) == (False, "Сервис вернул пустой текст")

    def boom(*a, **k):
        raise ConnectionError("нет связи")

    monkeypatch.setattr(ut, "transcribe_audio_whisperx_llm_svc", boom)
    assert t.transcribe_audio_file(str(audio)) == (False, "Ошибка микросервиса: нет связи")
    assert t.transcribe_audio_file(str(tmp_path / "нет.wav"))[0] is False  # файл не читается


# ---------------- YouTube ----------------


def test_transcribe_youtube_local_delegates():
    t = ut.UniversalTranscriber()
    assert t.transcribe_youtube("http://y/1") == (True, "yt:http://y/1")


def test_transcribe_youtube_unsupported_by_current_engine():
    t = ut.UniversalTranscriber()
    t.current_transcriber = object()
    assert t.transcribe_youtube("u") == (False, "YouTube транскрибация не поддерживается текущим движком")


class _Loader:
    path = "/tmp/audio.wav"
    cleaned = False

    def _download_youtube_audio(self, url):
        return type(self).path

    def cleanup(self):
        type(self).cleaned = True


def test_transcribe_youtube_svc_downloads_locally_and_transcribes(svc_mode, monkeypatch):
    monkeypatch.setattr("backend.transcription.whisperx_transcriber.WhisperXTranscriber", _Loader)
    _Loader.cleaned = False
    t = ut.UniversalTranscriber()
    monkeypatch.setattr(t, "transcribe_audio_file", lambda p: (True, "расшифровка " + p))
    assert t.transcribe_youtube("u") == (True, "расшифровка /tmp/audio.wav")
    assert _Loader.cleaned is True


def test_transcribe_youtube_svc_download_failure_and_exception(svc_mode, monkeypatch):
    t = ut.UniversalTranscriber()
    monkeypatch.setattr("backend.transcription.whisperx_transcriber.WhisperXTranscriber", _Loader)
    monkeypatch.setattr(_Loader, "path", None)
    assert t.transcribe_youtube("u") == (False, "Не удалось скачать аудио с YouTube")

    class Broken:
        def __init__(self):
            raise OSError("диск")

    monkeypatch.setattr("backend.transcription.whisperx_transcriber.WhisperXTranscriber", Broken)
    assert t.transcribe_youtube("u") == (False, "Не удалось выполнить транскрибацию YouTube")


# ---------------- диаризация ----------------


def _svc_diar(monkeypatch, response):
    seen = {}

    def fake(data, filename, language, engine):
        seen.update(filename=filename, language=language, engine=engine)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(ut, "transcribe_with_diarization_llm_svc", fake)
    return seen


@pytest.fixture
def audio_file(tmp_path):
    p = tmp_path / "meet.wav"
    p.write_bytes(b"RIFF")
    return str(p)


def test_diarization_svc_merges_adjacent_segments_and_sorts(svc_mode, monkeypatch, audio_file):
    seen = _svc_diar(monkeypatch, {
        "success": True,
        "segments": [
            {"start": 5, "speaker": "SPEAKER_01", "text": " Вторая "},
            {"start": 0, "speaker": "SPEAKER_00", "text": "Привет"},
            {"start": 2, "speaker": "SPEAKER_00", "text": "мир"},
            {"start": 3, "speaker": "SPEAKER_00", "text": "   "},
            {"start": 4, "text": "без спикера"},
        ],
    })
    ok, text = ut.UniversalTranscriber().transcribe_with_diarization(audio_file)
    assert ok is True
    assert text == "SPEAKER_00: Привет мир\nSPEAKER_?: без спикера\nSPEAKER_01: Вторая"
    assert seen == {"filename": "meet.wav", "language": "auto", "engine": "whisperx"}


def test_diarization_svc_failure_modes(svc_mode, monkeypatch, audio_file):
    t = ut.UniversalTranscriber()
    _svc_diar(monkeypatch, {"success": True, "segments": []})
    assert t.transcribe_with_diarization(audio_file) == (False, "Сервис вернул пустые сегменты")
    _svc_diar(monkeypatch, {"success": True, "segments": [{"start": 0, "speaker": "A", "text": " "}]})
    assert t.transcribe_with_diarization(audio_file) == (False, "Сервис вернул пустые сегменты")
    _svc_diar(monkeypatch, {"success": False, "error": "GPU занят"})
    assert t.transcribe_with_diarization(audio_file) == (False, "GPU занят")
    _svc_diar(monkeypatch, {"success": False})
    assert t.transcribe_with_diarization(audio_file) == (False, "Unknown error")
    _svc_diar(monkeypatch, RuntimeError("таймаут"))
    assert t.transcribe_with_diarization(audio_file) == (False, "таймаут")


def test_diarization_local_and_unavailable():
    t = ut.UniversalTranscriber()
    assert t.transcribe_with_diarization("a.wav") == (True, "локальный текст")
    t.whisperx_transcriber = None
    assert t.transcribe_with_diarization("a.wav") == (
        False, "Диаризация доступна только через WhisperX или микросервис")


# ---------------- очистка ----------------


def test_cleanup_delegates_and_del_calls_cleanup():
    t = ut.UniversalTranscriber()
    t.cleanup()
    assert ("cleanup",) in FakeWX.instances[0].calls
    FakeWX.instances[0].calls.clear()
    t.__del__()
    assert FakeWX.instances[0].calls == [("cleanup",)]


def test_cleanup_without_transcriber_is_noop(svc_mode):
    ut.UniversalTranscriber().cleanup()
