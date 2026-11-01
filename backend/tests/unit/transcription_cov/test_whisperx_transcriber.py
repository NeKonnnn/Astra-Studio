"""Тесты backend/transcription/whisperx_transcriber.py: WhisperX + диаризация (модели подменены)."""

import os
import sys
import tempfile
from types import SimpleNamespace

import pytest

from backend.transcription import whisperx_transcriber as wxt


# ---------------- подмены ----------------


def make_torch(cuda=False):
    emptied = []
    return SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda, empty_cache=lambda: emptied.append(1)),
        backends=SimpleNamespace(
            cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=False)),
            cudnn=SimpleNamespace(allow_tf32=False),
        ),
        emptied=emptied,
    )


class FakeModel:
    def __init__(self, result=None, fail_with=None):
        self.result = result if result is not None else {"segments": [{"start": 0, "end": 2, "text": " Привет "}]}
        self.fail_with = fail_with
        self.paths = []

    def transcribe(self, path):
        self.paths.append(path)
        if self.fail_with:
            raise self.fail_with
        return {"segments": [dict(s) for s in self.result["segments"]]}


class FakeWhisperX:
    def __init__(self):
        self.load_calls = []
        self.models = []
        self.assign_raises = None
        self.assign_adds_speaker = True

    def load_model(self, size, device, **kwargs):
        self.load_calls.append((size, device, kwargs))
        model = self.models.pop(0) if self.models else FakeModel()
        if isinstance(model, Exception):
            raise model
        return model

    def assign_word_speakers(self, diarize_segments, result):
        if self.assign_raises:
            raise self.assign_raises
        if self.assign_adds_speaker:
            for s in result["segments"]:
                s["speaker"] = "SPEAKER_00"
        return result


@pytest.fixture
def env(monkeypatch):
    """Локальный режим WhisperX с фейковыми whisperx/torch/ffmpeg/subprocess."""
    fake = SimpleNamespace(
        whisperx=FakeWhisperX(),
        torch=make_torch(cuda=False),
        runs=[],
        sleeps=[],
        run_result=SimpleNamespace(returncode=0, stdout="ffmpeg version", stderr=""),
    )
    monkeypatch.setattr(wxt, "USE_LLM_SVC", False)
    monkeypatch.setattr(wxt, "WHISPERX_AVAILABLE", True)
    monkeypatch.setattr(wxt, "whisperx", fake.whisperx)
    monkeypatch.setattr(wxt, "_torch", lambda: fake.torch)
    monkeypatch.setattr(wxt, "_ffmpeg_bin", lambda: "ffmpeg")

    def run(cmd, **kwargs):
        fake.runs.append((cmd, kwargs))
        if isinstance(fake.run_result, Exception):
            raise fake.run_result
        return fake.run_result

    monkeypatch.setattr(wxt, "subprocess", SimpleNamespace(run=run))
    monkeypatch.setattr(wxt, "time", SimpleNamespace(sleep=lambda s: fake.sleeps.append(s)))
    return fake


@pytest.fixture
def tx(env):
    t = wxt.WhisperXTranscriber()
    yield t
    t.cleanup()


# ---------------- вспомогательные функции модуля ----------------


def test_ffmpeg_bin_found_and_missing(monkeypatch):
    monkeypatch.setattr(wxt.shutil, "which", lambda n: "/bin/ffmpeg")
    assert wxt._ffmpeg_bin() == "/bin/ffmpeg"
    monkeypatch.setattr(wxt.shutil, "which", lambda n: None)
    with pytest.raises(RuntimeError, match="ffmpeg не найден"):
        wxt._ffmpeg_bin()


def test_lazy_torch_import(monkeypatch):
    fake = object()
    monkeypatch.setitem(sys.modules, "torch", fake)
    assert wxt._torch() is fake


# ---------------- поведение, определяемое на импорте ----------------


def _fake_modules(monkeypatch, torch):
    monkeypatch.setitem(sys.modules, "whisperx", SimpleNamespace(name="whisperx"))
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "moviepy", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "moviepy.editor", SimpleNamespace(VideoFileClip="CLIP"))
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(name="sd"))


def test_import_with_all_optional_dependencies_enables_tf32(monkeypatch, load_copy):
    monkeypatch.delenv("USE_LLM_SVC", raising=False)
    torch = make_torch(cuda=True)
    _fake_modules(monkeypatch, torch)
    mod = load_copy("whisperx_transcriber.py", "wxt_copy_full")
    assert mod.WHISPERX_AVAILABLE is True and mod.whisperx.name == "whisperx"
    assert mod.MOVIEPY_AVAILABLE is True and mod.VideoFileClip == "CLIP"
    assert mod.SOUNDDEVICE_AVAILABLE is True and mod.sd.name == "sd"
    assert torch.backends.cuda.matmul.allow_tf32 is True
    assert torch.backends.cudnn.allow_tf32 is True


def test_import_without_cuda_keeps_tf32_off(monkeypatch, load_copy):
    monkeypatch.delenv("USE_LLM_SVC", raising=False)
    torch = make_torch(cuda=False)
    _fake_modules(monkeypatch, torch)
    load_copy("whisperx_transcriber.py", "wxt_copy_nocuda")
    assert torch.backends.cuda.matmul.allow_tf32 is False


def test_import_survives_tf32_setup_failure(monkeypatch, load_copy):
    monkeypatch.delenv("USE_LLM_SVC", raising=False)
    torch = make_torch()
    torch.cuda.is_available = lambda: (_ for _ in ()).throw(RuntimeError("драйвер"))
    _fake_modules(monkeypatch, torch)
    mod = load_copy("whisperx_transcriber.py", "wxt_copy_tf32fail")
    assert mod.WHISPERX_AVAILABLE is True


def test_import_in_svc_mode_skips_local_models(monkeypatch, load_copy):
    monkeypatch.setenv("USE_LLM_SVC", "true")
    monkeypatch.setitem(sys.modules, "torch", None)  # обращение к torch упало бы
    monkeypatch.setitem(sys.modules, "whisperx", None)
    mod = load_copy("whisperx_transcriber.py", "wxt_copy_svc")
    assert mod.USE_LLM_SVC is True and mod.WHISPERX_AVAILABLE is False and mod.whisperx is None


def test_import_without_optional_packages(monkeypatch, load_copy):
    monkeypatch.delenv("USE_LLM_SVC", raising=False)
    for name in ("whisperx", "torch", "moviepy.editor", "sounddevice"):
        monkeypatch.setitem(sys.modules, name, None)
    mod = load_copy("whisperx_transcriber.py", "wxt_copy_none")
    assert mod.WHISPERX_AVAILABLE is False and mod.whisperx is None
    assert mod.MOVIEPY_AVAILABLE is False and mod.VideoFileClip is None
    assert mod.SOUNDDEVICE_AVAILABLE is False and mod.sd is None


# ---------------- конструктор ----------------


def test_init_svc_mode(monkeypatch):
    monkeypatch.setattr(wxt, "USE_LLM_SVC", True)
    t = wxt.WhisperXTranscriber()
    assert (t.model, t.diarize_model, t.device, t.language) == (None, None, "cpu", "ru")


def test_init_requires_local_whisperx(monkeypatch):
    monkeypatch.setattr(wxt, "USE_LLM_SVC", False)
    monkeypatch.setattr(wxt, "WHISPERX_AVAILABLE", False)
    with pytest.raises(ImportError, match="WhisperX не установлен локально"):
        wxt.WhisperXTranscriber()


def test_init_local_cpu(env, tx):
    assert (tx.device, tx.compute_type) == ("cpu", "float32")
    assert tx.language == "ru" and tx.sample_rate == 16000 and tx.model_size == "medium"
    assert tx.use_ffmpeg is True
    assert os.path.isdir(tx.temp_dir)
    assert tx.whisper_model_path.endswith(wxt.WHISPERX_MODELS_DIR)
    assert tx.diarize_model_path.endswith(wxt.DIARIZE_MODELS_DIR)
    assert tx._cached_diarize_model is None


def test_init_local_cuda(env):
    env.torch = make_torch(cuda=True)
    t = wxt.WhisperXTranscriber()
    try:
        assert (t.device, t.compute_type) == ("cuda", "float16")
    finally:
        t.cleanup()


# ---------------- ffmpeg ----------------


def test_check_ffmpeg_availability(env, tx, monkeypatch):
    assert tx._check_ffmpeg_availability() is True
    env.run_result = SimpleNamespace(returncode=1, stdout="", stderr="x")
    assert tx._check_ffmpeg_availability() is False
    env.run_result = FileNotFoundError("ffmpeg")
    assert tx._check_ffmpeg_availability() is False
    monkeypatch.setattr(wxt, "_ffmpeg_bin", lambda: (_ for _ in ()).throw(RuntimeError("нет в PATH")))
    assert tx._check_ffmpeg_availability() is False


# ---------------- прогресс / настройки / очистка ----------------


def test_update_progress(tx):
    tx._update_progress(5)  # без callback - не падает
    seen = []
    tx.set_progress_callback(seen.append)
    tx._update_progress(42)
    assert seen == [42]

    def bad(p):
        raise ValueError("callback упал")

    tx.set_progress_callback(bad)
    tx._update_progress(1)  # исключение подавляется


def test_set_language(tx):
    tx.set_language("en")
    assert tx.language == "en"


def test_cleanup_removes_temp_dir_and_is_idempotent(env):
    t = wxt.WhisperXTranscriber()
    path = t.temp_dir
    t.cleanup()
    assert not os.path.exists(path)
    t.cleanup()


def test_cleanup_swallows_rmtree_errors(tx, monkeypatch):
    def boom(path):
        raise OSError("занято")

    monkeypatch.setattr(wxt.shutil, "rmtree", boom)
    tx.cleanup()  # не падает


def test_destructor_cleans_up(env):
    t = wxt.WhisperXTranscriber()
    path = t.temp_dir
    t.__del__()
    assert not os.path.exists(path)


def test_cleanup_without_temp_dir_in_svc_mode(monkeypatch):
    monkeypatch.setattr(wxt, "USE_LLM_SVC", True)
    t = wxt.WhisperXTranscriber()
    t.cleanup()
    t.__del__()


# ---------------- локальный пайплайн диаризации ----------------


class FakePipeline:
    seen_config_text = None
    seen_path = None
    result = "PIPELINE"

    @classmethod
    def from_pretrained(cls, path):
        cls.seen_path = path
        with open(path, encoding="utf-8") as f:
            cls.seen_config_text = f.read()
        return cls.result


@pytest.fixture
def pyannote(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyannote", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pyannote.audio", SimpleNamespace(Pipeline=FakePipeline))
    FakePipeline.seen_config_text = FakePipeline.seen_path = None


def _write_config(tx, text):
    os.makedirs(tx.diarize_model_path, exist_ok=True)
    with open(os.path.join(tx.diarize_model_path, "pyannote_diarization_config.yaml"), "w", encoding="utf-8") as f:
        f.write(text)


@pytest.fixture
def diarize_dir(tx, tmp_path):
    tx.diarize_model_path = str(tmp_path / "diarize")
    return tx


def test_load_pipeline_missing_config(pyannote, diarize_dir):
    assert diarize_dir._load_local_diarization_pipeline() is None


def test_load_pipeline_resolves_relative_model_paths(pyannote, diarize_dir):
    _write_config(
        diarize_dir,
        "pipeline:\n  params:\n    embedding: emb/model.bin\n    segmentation: seg/model.bin\n",
    )
    assert diarize_dir._load_local_diarization_pipeline() == "PIPELINE"
    import yaml

    params = yaml.safe_load(FakePipeline.seen_config_text)["pipeline"]["params"]
    base = diarize_dir.diarize_model_path
    assert params["embedding"] == os.path.abspath(os.path.join(base, "emb/model.bin"))
    assert params["segmentation"] == os.path.abspath(os.path.join(base, "seg/model.bin"))
    # временный конфиг удалён после загрузки
    assert not os.path.exists(FakePipeline.seen_path)


def test_load_pipeline_keeps_absolute_urls_and_non_strings(pyannote, diarize_dir):
    _write_config(
        diarize_dir,
        "pipeline:\n  params:\n    embedding: /abs/model.bin\n    segmentation: https://hf.co/model\n",
    )
    diarize_dir._load_local_diarization_pipeline()
    import yaml

    params = yaml.safe_load(FakePipeline.seen_config_text)["pipeline"]["params"]
    assert params == {"embedding": "/abs/model.bin", "segmentation": "https://hf.co/model"}
    _write_config(diarize_dir, "pipeline:\n  params:\n    embedding: 5\n    segmentation: [1]\n")
    diarize_dir._load_local_diarization_pipeline()
    params = yaml.safe_load(FakePipeline.seen_config_text)["pipeline"]["params"]
    assert params == {"embedding": 5, "segmentation": [1]}


def test_load_pipeline_config_without_pipeline_section(pyannote, diarize_dir):
    _write_config(diarize_dir, "other: 1\n")
    assert diarize_dir._load_local_diarization_pipeline() == "PIPELINE"


def test_load_pipeline_params_without_embedding(pyannote, diarize_dir):
    _write_config(diarize_dir, "pipeline:\n  params:\n    clustering: x\n")
    assert diarize_dir._load_local_diarization_pipeline() == "PIPELINE"


def test_load_pipeline_failure_returns_none(diarize_dir, monkeypatch):
    _write_config(diarize_dir, "pipeline: {}\n")
    monkeypatch.setitem(sys.modules, "pyannote.audio", None)  # импорт падает
    assert diarize_dir._load_local_diarization_pipeline() is None


# ---------------- транскрибация: режим сервиса ----------------


@pytest.fixture
def audio(tmp_path):
    p = tmp_path / "rec.wav"
    p.write_bytes(b"RIFF")
    return str(p)


@pytest.fixture
def svc_tx(monkeypatch):
    monkeypatch.setattr(wxt, "USE_LLM_SVC", True)
    return wxt.WhisperXTranscriber()


def test_transcribe_missing_file(tx, tmp_path):
    path = str(tmp_path / "нет.wav")
    assert tx.transcribe_audio_file(path) == (False, f"Файл не найден: {path}")


def test_transcribe_svc_success(svc_tx, audio, monkeypatch):
    seen = {}

    def fake(data, filename, language, engine):
        seen.update(data=data, filename=filename, language=language, engine=engine)
        return {"success": True, "segments": [
            {"speaker": "SPEAKER_00", "text": "Привет"}, {"speaker": "SPEAKER_00", "text": "мир"},
            {"speaker": "SPEAKER_01", "text": "Ответ"}]}

    monkeypatch.setattr(wxt, "transcribe_with_diarization_llm_svc", fake)
    assert svc_tx.transcribe_audio_file(audio) == (True, "SPEAKER_00: Привет мир\nSPEAKER_01: Ответ")
    assert seen == {"data": b"RIFF", "filename": "rec.wav", "language": "ru", "engine": "whisperx"}


def test_transcribe_svc_failures(svc_tx, audio, monkeypatch):
    monkeypatch.setattr(wxt, "transcribe_with_diarization_llm_svc", lambda *a, **k: {"success": True})
    assert svc_tx.transcribe_audio_file(audio) == (False, "Сервис вернул пустые сегменты")
    monkeypatch.setattr(
        wxt, "transcribe_with_diarization_llm_svc", lambda *a, **k: {"success": False, "error": "нет GPU"})
    assert svc_tx.transcribe_audio_file(audio) == (False, "Ошибка сервиса: нет GPU")

    def boom(*a, **k):
        raise ConnectionError("обрыв")

    monkeypatch.setattr(wxt, "transcribe_with_diarization_llm_svc", boom)
    assert svc_tx.transcribe_audio_file(audio) == (False, "Ошибка микросервиса: обрыв")


# ---------------- транскрибация: локальный режим ----------------


def _diarizer(calls=None):
    def diarize(path):
        if calls is not None:
            calls.append(path)
        return "DIAR"

    return diarize


def test_transcribe_local_success_reports_progress(tx, env, audio, monkeypatch):
    progress = []
    tx.set_progress_callback(progress.append)
    calls = []
    monkeypatch.setattr(tx, "_load_local_diarization_pipeline", lambda: _diarizer(calls))
    assert tx.transcribe_audio_file(audio) == (True, "SPEAKER_00: Привет")
    assert progress == [10, 50, 70, 80, 90, 100]
    assert calls == [audio]
    size, device, kwargs = env.whisperx.load_calls[0]
    assert (size, device) == ("medium", "cpu")
    assert kwargs["compute_type"] == "float32" and kwargs["language"] == "ru"
    assert kwargs["download_root"] == tx.whisper_model_path
    assert tx._cached_diarize_model is not None
    assert env.torch.emptied == []  # на CPU кэш CUDA не чистится


def test_transcribe_local_uses_cached_diarization_model(tx, env, audio, monkeypatch):
    cached_calls = []
    tx._cached_diarize_model = _diarizer(cached_calls)
    monkeypatch.setattr(tx, "_load_local_diarization_pipeline", lambda: pytest.fail("кэш должен сработать"))
    assert tx.transcribe_audio_file(audio)[0] is True
    assert cached_calls == [audio]


def test_transcribe_local_cuda_clears_cache(tx, env, audio, monkeypatch):
    tx.device = "cuda"
    monkeypatch.setattr(tx, "_load_local_diarization_pipeline", lambda: _diarizer())
    assert tx.transcribe_audio_file(audio)[0] is True
    assert env.torch.emptied == [1]


def test_transcribe_local_fails_when_diarization_unavailable(tx, audio, monkeypatch):
    monkeypatch.setattr(tx, "_load_local_diarization_pipeline", lambda: None)
    assert tx.transcribe_audio_file(audio) == (False, "Не удалось загрузить модель диаризации")


def test_transcribe_local_video_is_converted_before_diarization(tx, env, tmp_path, monkeypatch):
    video = tmp_path / "meeting.MP4"
    video.write_bytes(b"x")
    diar_calls = []
    monkeypatch.setattr(tx, "_load_local_diarization_pipeline", lambda: _diarizer(diar_calls))
    assert tx.transcribe_audio_file(str(video)) == (True, "SPEAKER_00: Привет")
    temp_audio = os.path.join(tempfile.gettempdir(), "diarize_tmp_meeting.MP4.wav")
    assert diar_calls == [temp_audio]  # диаризация идёт по извлечённой WAV-дорожке
    (cmd, _), = [r for r in env.runs if "-ar" in r[0]]
    assert cmd[cmd.index("-i") + 1] == str(video) and cmd[-1] == temp_audio
    assert cmd[cmd.index("-ar") + 1] == "16000" and cmd[cmd.index("-ac") + 1] == "1"


def test_transcribe_local_cudnn_error_falls_back_to_cpu(tx, env, audio, monkeypatch):
    env.whisperx.models = [FakeModel(fail_with=RuntimeError("cuDNN error: not found")), FakeModel()]
    monkeypatch.setattr(tx, "_load_local_diarization_pipeline", lambda: _diarizer())
    assert tx.transcribe_audio_file(audio) == (True, "SPEAKER_00: Привет")
    assert [c[1] for c in env.whisperx.load_calls] == ["cpu", "cpu"]
    assert env.whisperx.load_calls[1][2]["compute_type"] == "float32"


def test_transcribe_local_other_transcribe_error_is_reported(tx, env, audio):
    env.whisperx.models = [FakeModel(fail_with=ValueError("битое аудио"))]
    assert tx.transcribe_audio_file(audio) == (False, "битое аудио")


def test_transcribe_local_model_load_error_is_reported(tx, env, audio):
    env.whisperx.models = [OSError("нет весов")]
    assert tx.transcribe_audio_file(audio) == (False, "нет весов")


def test_transcribe_local_falls_back_to_manual_assignment_on_error(tx, env, audio, monkeypatch):
    env.whisperx.assign_raises = RuntimeError("не совпало")
    diar = [{"start": 0, "end": 5, "speaker": "SPEAKER_07"}]
    monkeypatch.setattr(tx, "_load_local_diarization_pipeline", lambda: (lambda p: diar))
    assert tx.transcribe_audio_file(audio) == (True, "SPEAKER_07: Привет")


def test_transcribe_local_manual_assignment_when_no_speakers_assigned(tx, env, audio, monkeypatch):
    env.whisperx.assign_adds_speaker = False
    diar = [{"start": 0, "end": 5, "speaker": "SPEAKER_03"}]
    monkeypatch.setattr(tx, "_load_local_diarization_pipeline", lambda: (lambda p: diar))
    assert tx.transcribe_audio_file(audio) == (True, "SPEAKER_03: Привет")


def test_transcribe_local_unknown_diarization_format_must_not_return_none_text(tx, env, audio, monkeypatch):
    env.whisperx.assign_adds_speaker = False
    monkeypatch.setattr(tx, "_load_local_diarization_pipeline", lambda: (lambda p: object()))
    ok, text = tx.transcribe_audio_file(audio)
    assert ok is True and text != "None" and text.strip()


def test_transcribe_local_assign_error_and_unknown_format_keeps_result(tx, env, audio, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("assign")

    env.whisperx.assign_word_speakers = boom
    monkeypatch.setattr(tx, "_load_local_diarization_pipeline", lambda: (lambda p: object()))
    ok, text = tx.transcribe_audio_file(audio)
    assert ok is True and text != "None" and text.strip()


# ---------------- YouTube ----------------


def test_transcribe_youtube_flow(tx, monkeypatch):
    monkeypatch.setattr(tx, "_download_youtube_audio", lambda url: None)
    assert tx.transcribe_youtube("u") == (False, "Не удалось загрузить аудио с YouTube")
    monkeypatch.setattr(tx, "_download_youtube_audio", lambda url: "/a.wav")
    monkeypatch.setattr(tx, "transcribe_audio_file", lambda p: (True, "txt " + p))
    assert tx.transcribe_youtube("u") == (True, "txt /a.wav")

    def boom(url):
        raise RuntimeError("сеть")

    monkeypatch.setattr(tx, "_download_youtube_audio", boom)
    assert tx.transcribe_youtube("u") == (False, "Не удалось выполнить транскрибацию YouTube")


class FakeStream:
    def __init__(self, write=True):
        self.write = write

    def download(self, output_path, filename):
        if self.write:
            with open(os.path.join(output_path, filename), "wb") as f:
                f.write(b"audio")


class FakeYouTube:
    behaviour = None  # callable(url) -> stream | None, либо исключение

    def __init__(self, url):
        self.url = url
        result = FakeYouTube.behaviour(url)
        if isinstance(result, Exception):
            raise result
        stream = result
        self.streams = SimpleNamespace(
            filter=lambda only_audio: SimpleNamespace(first=lambda: stream)
        )


@pytest.fixture
def youtube(env, monkeypatch):
    monkeypatch.setattr(wxt, "pytubefix", SimpleNamespace(YouTube=FakeYouTube))
    return env


def test_download_youtube_success_converts_to_wav(tx, youtube):
    FakeYouTube.behaviour = lambda url: FakeStream()
    path = tx._download_youtube_audio("http://y")
    assert path.endswith("youtube_audio.wav")
    (cmd, kwargs), = [r for r in youtube.runs if "-acodec" in r[0]]
    assert cmd[0] == "ffmpeg" and cmd[cmd.index("-ar") + 1] == "16000" and cmd[-1] == path
    assert kwargs["timeout"] == 60


def test_download_youtube_keeps_original_when_ffmpeg_fails(tx, youtube):
    FakeYouTube.behaviour = lambda url: FakeStream()
    youtube.run_result = SimpleNamespace(returncode=1, stdout="", stderr="bad")
    path = tx._download_youtube_audio("http://y")
    assert path.endswith("youtube_audio") and os.path.exists(path)


def test_download_youtube_no_stream_or_no_file(tx, youtube):
    FakeYouTube.behaviour = lambda url: None
    assert tx._download_youtube_audio("u") is None
    FakeYouTube.behaviour = lambda url: FakeStream(write=False)
    assert tx._download_youtube_audio("u") is None


def test_download_youtube_retries_on_ssl_then_succeeds(tx, youtube):
    attempts = []

    def behaviour(url):
        attempts.append(1)
        return OSError("SSL: UNEXPECTED_EOF") if len(attempts) < 3 else FakeStream()

    FakeYouTube.behaviour = behaviour
    assert tx._download_youtube_audio("u").endswith(".wav")
    assert len(attempts) == 3 and youtube.sleeps == [2, 2]


def test_download_youtube_gives_up_after_ssl_errors(tx, youtube):
    attempts = []

    def behaviour(url):
        attempts.append(1)
        return OSError("EOF occurred")

    FakeYouTube.behaviour = behaviour
    assert tx._download_youtube_audio("u") is None
    assert len(attempts) == 3 and youtube.sleeps == [2, 2]


def test_download_youtube_other_errors_retry_with_longer_pause(tx, youtube):
    attempts = []

    def behaviour(url):
        attempts.append(1)
        return ValueError("видео недоступно")

    FakeYouTube.behaviour = behaviour
    assert tx._download_youtube_audio("u") is None
    assert len(attempts) == 3 and youtube.sleeps == [3, 3]


def test_download_youtube_ffmpeg_missing_is_handled(tx, youtube, monkeypatch):
    FakeYouTube.behaviour = lambda url: FakeStream()
    monkeypatch.setattr(wxt, "_ffmpeg_bin", lambda: (_ for _ in ()).throw(RuntimeError("нет ffmpeg")))
    assert tx._download_youtube_audio("u") is None


# ---------------- форматирование ----------------


def test_format_transcript_merges_adjacent_same_speaker(tx):
    result = {"segments": [
        {"speaker": "A", "text": " раз "}, {"speaker": "A", "text": "два"},
        {"speaker": "B", "text": ""}, {"speaker": "B", "text": "три"}, {"text": "четыре"},
    ]}
    assert tx._format_transcript_with_speakers(result) == "A: раз два\nB: три\nSPEAKER_?: четыре"


def test_format_transcript_without_segments_or_text_uses_simple(tx):
    assert tx._format_transcript_with_speakers({"segments": []}) == ""
    assert tx._format_transcript_with_speakers({}) == ""
    assert tx._format_transcript_with_speakers({"segments": [{"text": "   "}]}) == ""


def test_format_transcript_broken_segments_fall_back_to_string(tx):
    result = {"segments": [1, 2]}
    assert tx._format_transcript_with_speakers(result) == str(result)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "00:00"), (0, "00:00"), (75.9, "01:15"), (3725, "62:05"), ("x", "00:00")],
)
def test_format_time(tx, seconds, expected):
    assert tx._format_time(seconds) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "00:00"), (75, "01:15"), (3725, "01:02"), (59, "00:59"), ("x", "00:00")],
)
def test_format_time_simple(tx, seconds, expected):
    assert tx._format_time_simple(seconds) == expected


def test_format_simple_transcript(tx):
    assert tx._format_simple_transcript({"segments": [{"text": " а "}, {"text": ""}, {"x": 1}, {"text": "б"}]}) == "а б"
    broken = {"segments": [1]}
    assert tx._format_simple_transcript(broken) == str(broken)


# ---------------- ручное назначение спикеров ----------------


class Turn:
    def __init__(self, start, end, track=None, label=None):
        self.start, self.end = start, end
        if track is not None:
            self.track = track
        if label is not None:
            self.label = label


def test_manual_assign_returns_none_without_segments(tx):
    assert tx._manual_assign_speakers([], {"segments": []}) is None
    assert tx._manual_assign_speakers([], {}) is None


def test_manual_assign_with_timeline_object_and_skips_empty_text(tx):
    class Annotation:
        def get_timeline(self):
            return [{"start": 0, "end": 3, "speaker": "S0"}, {"start": 3, "end": 6, "speaker": "S1"}]

    whisper = {"language": "ru", "segments": [
        {"start": 0, "end": 2, "text": "один"}, {"start": 3, "end": 5, "text": "два"},
        {"start": 5, "end": 6, "text": "  "}]}
    out = tx._manual_assign_speakers(Annotation(), whisper)
    assert [(s["text"], s["speaker"]) for s in out["segments"]] == [("один", "S0"), ("два", "S1")]
    assert out["language"] == "ru"
    assert "speaker" not in whisper["segments"][0]  # исходный результат не мутируется


def test_manual_assign_with_list_and_unknown_format(tx):
    whisper = {"segments": [{"start": 0, "end": 1, "text": "а"}]}
    out = tx._manual_assign_speakers([{"start": 0, "end": 1, "speaker": "S9"}], whisper)
    assert out["segments"][0]["speaker"] == "S9"
    assert tx._manual_assign_speakers("не таймлайн", whisper) is None


def test_manual_assign_error_returns_none(tx):
    assert tx._manual_assign_speakers([], {"segments": [1]}) is None


def test_refine_segments_returns_same_object(tx):
    segs = [{"a": 1}]
    assert tx._refine_segments_for_diarization(segs, []) is segs


def test_find_speaker_by_best_overlap_for_different_shapes(tx):
    timeline = [
        {"start": 0, "end": 2, "speaker": "D0"},
        {"start": 2, "end": 10, "speaker": "D1"},
        "строка-пропускается",
    ]
    assert tx._find_speaker_for_time(timeline, 1, 8) == "D1"
    assert tx._find_speaker_for_time(timeline, 20, 30) == "SPEAKER_?"
    assert tx._find_speaker_for_time([Turn(0, 5, track="T1")], 0, 4) == "T1"
    assert tx._find_speaker_for_time([Turn(0, 5, label="L1")], 0, 4) == "L1"
    assert tx._find_speaker_for_time([Turn(0, 5)], 0, 4) == "SPEAKER_?"
    assert tx._find_speaker_for_time([{"start": 0, "end": 5}], 0, 4) == "SPEAKER_?"


def test_find_speaker_tolerates_broken_items_and_input(tx):
    class NoEnd:
        start = 0

    assert tx._find_speaker_for_time([NoEnd(), {"start": 0, "end": 3, "speaker": "OK"}], 0, 3) == "OK"
    assert tx._find_speaker_for_time(None, 0, 1) == "SPEAKER_?"


def test_normalize_speaker_name(tx):
    names = [tx._normalize_speaker_name(f"id{i}") for i in range(10)]
    assert names[:8] == [f"Speaker_{c}" for c in "ABCDEFGH"]
    assert names[8:] == ["Speaker_9", "Speaker_10"]
    assert tx._normalize_speaker_name("id0") == "Speaker_A"  # стабильное соответствие
    assert tx._normalize_speaker_name(["нехешируемый"]) == "Speaker_A"


def test_analyze_diarization_speakers(tx):
    timeline = [{"speaker": "B"}, {"speaker": "A"}, {"speaker": "A"}, Turn(0, 1, track="T"), 5]
    assert tx._analyze_diarization_speakers(timeline) == ["A", "B", "TRACK_T"]
    assert tx._analyze_diarization_speakers(None) == []
    assert tx._analyze_diarization_speakers([{"speaker": 1}, {"speaker": "A"}]) == []  # несравнимые типы
