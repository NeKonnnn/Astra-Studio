"""Тесты backend/transcription/voice.py: TTS (Silero/pyttsx3/сервис) и STT (микрофон, WhisperX).

Silero, torch, librosa, sounddevice, pyttsx3 в окружении нет - используются фейки.
Часть поведения определяется на импорте (скачивание модели, пути, флаги), её проверяют
тесты "import_*" на копии модуля (см. conftest.load_copy).
"""

import io
import os
import sys
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from backend.transcription import voice as v


# ---------------- фейки ----------------


class FakeTensor:
    """Подмена torch.Tensor с цепочкой .cpu().numpy()."""

    def __init__(self, arr):
        self.arr = np.asarray(arr, dtype="float32")

    def cpu(self):
        return self

    def numpy(self):
        return self.arr


def make_torch(downloads=None, importer=None):
    def download_url_to_file(url, dst):
        if downloads is not None:
            downloads.append((url, dst))
        with open(dst, "wb") as f:
            f.write(b"model")

    return SimpleNamespace(
        Tensor=FakeTensor,
        from_numpy=lambda a: ("tensor", a),
        cat=lambda items, dim=0: FakeTensor(np.concatenate([i.arr for i in items])),
        hub=SimpleNamespace(download_url_to_file=download_url_to_file),
        package=SimpleNamespace(PackageImporter=importer or (lambda path: FakeImporter(path))),
    )


class FakeSilero:
    def __init__(self, level=0.5):
        self.level = level
        self.calls = []
        self.device = None

    def to(self, device):
        self.device = device

    def apply_tts(self, **kwargs):
        self.calls.append(kwargs)
        return FakeTensor([self.level, -self.level])


class FakeImporter:
    def __init__(self, path):
        self.path = path

    def load_pickle(self, package, resource):
        assert (package, resource) == ("tts_models", "model")
        return FakeSilero()


class FakeSd:
    def __init__(self):
        self.played = []
        self.waits = 0
        self.recorded = []

    def play(self, audio, rate):
        self.played.append((audio, rate))

    def wait(self):
        self.waits += 1

    def rec(self, frames, samplerate, channels, dtype):
        self.recorded.append((frames, samplerate, channels, dtype))
        return np.zeros((frames, 1), dtype="int16")


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    """Изолированное состояние модуля: пути моделей в tmp_path, пустой кэш моделей."""
    monkeypatch.setattr(v, "USE_LLM_SVC", False)
    monkeypatch.setattr(v, "models", {})
    monkeypatch.setattr(v, "tts_model_loaded", False)
    monkeypatch.setattr(v, "pyttsx3_engine", None)
    monkeypatch.setattr(
        v, "MODEL_PATHS",
        {"ru": str(tmp_path / "silero" / "ru" / "model.pt"), "en": str(tmp_path / "silero" / "en" / "model.pt")},
    )


@pytest.fixture
def sd(monkeypatch):
    fake = FakeSd()
    monkeypatch.setattr(v, "sd", fake)
    return fake


@pytest.fixture
def torch_fake(monkeypatch):
    fake = make_torch()
    monkeypatch.setattr(v, "torch", fake, raising=False)
    monkeypatch.setattr(v, "TORCH_AVAILABLE", True)
    return fake


# ---------------- поведение на импорте ----------------


def _fake_import_env(monkeypatch, tmp_path, *, torch=True, sd=True, librosa=True, pyttsx3=True, downloads=None):
    monkeypatch.setenv("SILERO_MODELS_DIR", str(tmp_path / "silero_import"))
    monkeypatch.delenv("USE_LLM_SVC", raising=False)
    if torch:
        monkeypatch.setitem(sys.modules, "torch", make_torch(downloads))
    else:
        monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(name="sd") if sd else None)
    if librosa:
        monkeypatch.setitem(sys.modules, "librosa", SimpleNamespace(effects=SimpleNamespace()))
        monkeypatch.setitem(sys.modules, "librosa.effects", SimpleNamespace())
    else:
        monkeypatch.setitem(sys.modules, "librosa", None)
    if pyttsx3:
        class _Voice:
            id = "en-1"

            def __str__(self):
                return "english"

        engine = SimpleNamespace(getProperty=lambda n: [_Voice()], setProperty=lambda *a: None)
        monkeypatch.setitem(sys.modules, "pyttsx3", SimpleNamespace(init=lambda: engine))
    else:
        monkeypatch.setitem(sys.modules, "pyttsx3", None)


def test_import_downloads_and_loads_models_with_fake_torch_hub(monkeypatch, tmp_path, load_copy):
    downloads = []
    _fake_import_env(monkeypatch, tmp_path, downloads=downloads)
    mod = load_copy("voice.py", "voice_copy_full")
    assert mod.TORCH_AVAILABLE is True and mod.SOUNDDEVICE_AVAILABLE is True
    assert mod.librosa_available is True and mod.pyttsx3_available is True
    # скачивание - через torch.hub, в каталог SILERO_MODELS_DIR; обе модели загружены
    assert [u for u, _ in downloads] == [mod.MODELS_URLS["ru"], mod.MODELS_URLS["en"]]
    assert all(str(tmp_path / "silero_import") in dst for _, dst in downloads)
    assert set(mod.models) == {"ru", "en"} and mod.tts_model_loaded is True
    assert mod.models["ru"].device == "cpu"
    assert mod.pyttsx3_engine is not None


def test_import_without_optional_packages(monkeypatch, tmp_path, load_copy):
    _fake_import_env(monkeypatch, tmp_path, torch=False, sd=False, librosa=False, pyttsx3=False)
    mod = load_copy("voice.py", "voice_copy_bare")
    assert (mod.TORCH_AVAILABLE, mod.SOUNDDEVICE_AVAILABLE, mod.librosa_available) == (False, False, False)
    assert mod.sd is None and mod.pyttsx3_available is False
    assert mod.models == {} and mod.tts_model_loaded is False  # скачать нечем - ошибка залогирована, не брошена


def test_import_in_svc_mode_skips_local_tts(monkeypatch, tmp_path, load_copy):
    _fake_import_env(monkeypatch, tmp_path, torch=False)
    monkeypatch.setenv("USE_LLM_SVC", "True")
    mod = load_copy("voice.py", "voice_copy_svc")
    assert mod.USE_LLM_SVC is True and mod.models == {} and mod.pyttsx3_engine is None


@pytest.mark.parametrize(
    ("file_path", "env_dir", "expected_suffix"),
    [
        ("/srv/app/backend/transcription/voice.py", None, "/srv/app/silero_models"),
        ("/srv/other/svc/transcription/voice.py", None, "/srv/other/svc/models/silero"),
        ("/srv/app/backend/transcription/voice.py", "/custom/silero", "/custom/silero"),
    ],
)
def test_import_silero_dir_resolution(monkeypatch, tmp_path, load_copy, file_path, env_dir, expected_suffix):
    _fake_import_env(monkeypatch, tmp_path, torch=False)
    monkeypatch.setenv("USE_LLM_SVC", "true")  # без попыток скачивания/создания каталогов
    if env_dir:
        monkeypatch.setenv("SILERO_MODELS_DIR", env_dir)
    else:
        monkeypatch.delenv("SILERO_MODELS_DIR", raising=False)
    mod = load_copy("voice.py", "voice_copy_dirs", file_override=file_path.replace("/", os.sep))
    got = mod.SILERO_MODELS_DIR.replace("\\", "/")
    assert got.endswith(expected_suffix) or got == expected_suffix
    assert mod.MODEL_PATHS["ru"].replace("\\", "/").endswith("/ru/model.pt")


# ---------------- change_audio_speed ----------------


def test_change_speed_without_librosa_returns_input(monkeypatch):
    monkeypatch.setattr(v, "librosa_available", False)
    sentinel = object()
    assert v.change_audio_speed(sentinel, 48000, 1.5) is sentinel


def test_change_speed_numpy_audio(monkeypatch):
    calls = []
    fake = SimpleNamespace(effects=SimpleNamespace(
        time_stretch=lambda a, rate: calls.append(rate) or a * 2))
    monkeypatch.setattr(v, "librosa", fake, raising=False)
    monkeypatch.setattr(v, "librosa_available", True)
    monkeypatch.setattr(v, "TORCH_AVAILABLE", False)
    out = v.change_audio_speed(np.array([1.0, 2.0]), 48000, 1.25)
    assert calls == [1.25] and out.tolist() == [2.0, 4.0]


def test_change_speed_tensor_roundtrip(monkeypatch, torch_fake):
    fake = SimpleNamespace(effects=SimpleNamespace(time_stretch=lambda a, rate: a + 1))
    monkeypatch.setattr(v, "librosa", fake, raising=False)
    monkeypatch.setattr(v, "librosa_available", True)
    kind, arr = v.change_audio_speed(FakeTensor([1.0]), 48000, 1.1)
    assert kind == "tensor" and arr.tolist() == [2.0]


def test_change_speed_error_returns_original_audio(monkeypatch):
    def boom(a, rate):
        raise ValueError("не растягивается")

    monkeypatch.setattr(v, "librosa", SimpleNamespace(effects=SimpleNamespace(time_stretch=boom)), raising=False)
    monkeypatch.setattr(v, "librosa_available", True)
    monkeypatch.setattr(v, "TORCH_AVAILABLE", False)
    audio = np.array([1.0])
    assert v.change_audio_speed(audio, 48000, 2.0) is audio


# ---------------- pyttsx3 ----------------


def test_init_pyttsx3_unavailable(monkeypatch):
    monkeypatch.setattr(v, "pyttsx3_available", False)
    assert v.init_pyttsx3() is False


def test_init_pyttsx3_selects_russian_voice(monkeypatch):
    props = {}
    class Voice:
        def __init__(self, id_, text):
            self.id, self.text = id_, text

        def __str__(self):
            return self.text

    voices = [Voice("en-1", "english"), Voice("ru-1", "Microsoft Russian"), Voice("x", "russian-2")]
    engine = SimpleNamespace(getProperty=lambda n: voices, setProperty=lambda k, val: props.update({k: val}))
    monkeypatch.setattr(v, "pyttsx3", SimpleNamespace(init=lambda: engine), raising=False)
    monkeypatch.setattr(v, "pyttsx3_available", True)
    assert v.init_pyttsx3() is True
    assert props == {"voice": "ru-1"} and v.pyttsx3_engine is engine


def test_init_pyttsx3_without_russian_voice_and_on_error(monkeypatch):
    class Voice:
        id = "de-1"

        def __str__(self):
            return "german"

    props = {}
    engine = SimpleNamespace(getProperty=lambda n: [Voice()], setProperty=lambda k, val: props.update({k: val}))
    monkeypatch.setattr(v, "pyttsx3", SimpleNamespace(init=lambda: engine), raising=False)
    monkeypatch.setattr(v, "pyttsx3_available", True)
    assert v.init_pyttsx3() is True and props == {}

    def boom():
        raise RuntimeError("нет драйвера речи")

    monkeypatch.setattr(v, "pyttsx3", SimpleNamespace(init=boom), raising=False)
    assert v.init_pyttsx3() is False


def test_speak_pyttsx3(monkeypatch):
    assert v.speak_text_pyttsx3("", 1.0) is False
    assert v.speak_text_pyttsx3("текст", 1.0) is False  # движка нет
    log = []
    engine = SimpleNamespace(
        setProperty=lambda k, val: log.append((k, val)), say=lambda t: log.append(("say", t)),
        runAndWait=lambda: log.append("run"),
    )
    monkeypatch.setattr(v, "pyttsx3_engine", engine)
    assert v.speak_text_pyttsx3("привет", 1.5) is True
    assert log == [("rate", 300), ("say", "привет"), "run"]

    def boom():
        raise RuntimeError("занято")

    monkeypatch.setattr(v, "pyttsx3_engine", SimpleNamespace(setProperty=lambda *a: boom()))
    assert v.speak_text_pyttsx3("x") is False


# ---------------- скачивание и загрузка моделей ----------------


def test_download_model_service_mode_does_nothing(monkeypatch):
    monkeypatch.setattr(v, "USE_LLM_SVC", True)
    assert v.download_model("ru") is True
    assert not os.path.exists(v.MODEL_PATHS["ru"])


def test_download_model_downloads_when_missing(torch_fake):
    downloads = []
    torch_fake.hub.download_url_to_file = lambda url, dst: downloads.append((url, dst))
    assert v.download_model("en") is True
    assert downloads == [(v.MODELS_URLS["en"], v.MODEL_PATHS["en"])]
    assert os.path.isdir(os.path.dirname(v.MODEL_PATHS["en"]))


def test_download_model_skips_existing_file(torch_fake, tmp_path):
    import os

    os.makedirs(os.path.dirname(v.MODEL_PATHS["ru"]))
    with open(v.MODEL_PATHS["ru"], "wb") as f:
        f.write(b"x")
    torch_fake.hub.download_url_to_file = lambda *a: pytest.fail("повторное скачивание")
    assert v.download_model("ru") is True


def test_download_model_failure_returns_false(torch_fake):
    def boom(url, dst):
        raise OSError("сеть заблокирована")

    torch_fake.hub.download_url_to_file = boom
    assert v.download_model("ru") is False


def test_load_model_service_mode_and_cached(monkeypatch):
    monkeypatch.setattr(v, "USE_LLM_SVC", True)
    assert v.load_model("ru") is False
    monkeypatch.setattr(v, "USE_LLM_SVC", False)
    v.models["ru"] = object()
    assert v.load_model("ru") is True


def test_load_model_missing_file(torch_fake):
    assert v.load_model("ru") is False and v.tts_model_loaded is False


def test_load_model_success_moves_to_cpu(torch_fake):
    import os

    os.makedirs(os.path.dirname(v.MODEL_PATHS["ru"]))
    open(v.MODEL_PATHS["ru"], "wb").close()
    assert v.load_model("ru") is True
    assert v.models["ru"].device == "cpu" and v.tts_model_loaded is True


def test_load_model_failure(torch_fake):
    import os

    os.makedirs(os.path.dirname(v.MODEL_PATHS["ru"]))
    open(v.MODEL_PATHS["ru"], "wb").close()

    def boom(path):
        raise RuntimeError("битый пакет")

    torch_fake.package.PackageImporter = boom
    assert v.load_model("ru") is False


def test_load_model_unknown_language_returns_false():
    assert v.load_model("de") is False


def test_download_model_unknown_language_returns_false():
    assert v.download_model("de") is False


def test_speak_text_with_unknown_voice_id_falls_back_to_pyttsx3(monkeypatch):
    monkeypatch.setattr(v, "tts_model_loaded", True)
    monkeypatch.setattr(v, "speak_text_pyttsx3", lambda text, rate: True)
    assert v.speak_text("Привет, мир!", voice_id="de") is True


def test_init_tts_service_mode(monkeypatch):
    monkeypatch.setattr(v, "USE_LLM_SVC", True)
    monkeypatch.setattr(v, "init_pyttsx3", lambda: pytest.fail("не нужен в режиме сервиса"))
    v.init_tts()


@pytest.mark.parametrize(
    ("ru_ok", "expected_loaded", "expected_calls"),
    [
        (True, True, ["pyttsx3", "dl:ru", "load:ru", "dl:en", "load:en"]),
        (False, False, ["pyttsx3", "dl:ru", "dl:en", "load:en"]),
    ],
)
def test_init_tts_local(monkeypatch, ru_ok, expected_loaded, expected_calls):
    calls = []
    monkeypatch.setattr(v, "init_pyttsx3", lambda: calls.append("pyttsx3"))
    monkeypatch.setattr(v, "download_model", lambda lang: calls.append(f"dl:{lang}") or (lang != "ru" or ru_ok))
    monkeypatch.setattr(v, "load_model", lambda lang: calls.append(f"load:{lang}") or True)
    v.init_tts()
    assert calls == expected_calls and v.tts_model_loaded is expected_loaded


# ---------------- вспомогательные функции текста ----------------


def test_split_text_into_chunks():
    assert v.split_text_into_chunks("Раз. Два! Три?", 1000) == ["Раз. Два! Три?"]
    assert v.split_text_into_chunks("Aaaa. Bbbb. Cccc.", 12) == ["Aaaa. Bbbb.", "Cccc."]
    assert v.split_text_into_chunks("") == [""]  # пустая строка даёт один пустой фрагмент
    long_sentence = "x" * 50
    assert v.split_text_into_chunks(long_sentence, 10) == [long_sentence]  # предложение не режется


@pytest.mark.parametrize(
    ("text", "lang"),
    [("Привет, мир", "ru"), ("Hello world", "en"), ("", "en"), ("Hi, привет мир как дела", "ru"), ("ёїєґ", "ru"), ("123", "en")],
)
def test_detect_language(text, lang):
    assert v.detect_language(text) == lang


# ---------------- speak_text_silero ----------------


def _preload(lang="ru", level=0.5):
    model = FakeSilero(level)
    v.models[lang] = model
    return model


def test_silero_empty_text_and_unloadable_model():
    assert v.speak_text_silero("") is False
    assert v.speak_text_silero("Привет, как дела у тебя?", lang="ru") is False  # модели нет и файла нет


def test_silero_plays_each_chunk(sd, torch_fake):
    model = _preload("ru")
    assert v.speak_text_silero("Первое предложение. Второе предложение.") is True
    assert len(model.calls) == 1 and model.calls[0]["speaker"] == "baya"
    assert model.calls[0]["sample_rate"] == 48000 and model.calls[0]["put_accent"] is False
    assert len(sd.played) == 1 and sd.played[0][1] == 48000 and sd.waits == 1


def test_silero_detects_language_and_uses_requested_speaker(sd, torch_fake):
    en, ru = _preload("en"), _preload("ru")
    assert v.speak_text_silero("Hello there, how are you today?", speaker="en_0") is True
    assert len(en.calls) == 1 and not ru.calls and en.calls[0]["speaker"] == "en_0"


def test_silero_short_text_is_spelled_out(sd, torch_fake):
    model = _preload("ru")
    assert v.speak_text_silero("1,2.3", lang="ru") is True
    assert model.calls[0]["text"] == "Ответ: один и два точкатри"


def test_silero_applies_speech_rate(sd, torch_fake, monkeypatch):
    _preload("ru")
    stretched = []
    monkeypatch.setattr(v, "change_audio_speed", lambda a, sr, rate: stretched.append((sr, rate)) or a)
    assert v.speak_text_silero("Достаточно длинный текст для озвучки.", lang="ru", speech_rate=1.2) is True
    assert stretched == [(48000, 1.2)]


def test_silero_saves_scaled_int16_wav(torch_fake, tmp_path):
    _preload("ru", level=0.5)
    out = tmp_path / "out.wav"
    assert v.speak_text_silero("Это достаточно длинный текст. И ещё одно предложение.", lang="ru",
                               save_to_file=str(out)) is True
    import scipy.io.wavfile

    rate, data = scipy.io.wavfile.read(str(out))
    assert rate == 48000 and data.dtype == np.int16
    assert data.tolist() == [16383, -16383]


def test_silero_saves_without_scaling_when_already_integer_range(torch_fake, tmp_path):
    _preload("ru", level=5000.0)
    out = tmp_path / "out.wav"
    assert v.speak_text_silero("Это достаточно длинный текст для проверки.", lang="ru", save_to_file=str(out))
    import scipy.io.wavfile

    _, data = scipy.io.wavfile.read(str(out))
    assert data.dtype == np.float32 and data.tolist() == [5000.0, -5000.0]


def test_silero_error_returns_false(sd, torch_fake):
    model = _preload("ru")
    model.apply_tts = lambda **kw: (_ for _ in ()).throw(RuntimeError("OOM"))
    assert v.speak_text_silero("Достаточно длинный текст для озвучки.", lang="ru") is False


# ---------------- speak_text ----------------


@pytest.fixture
def svc_tts(monkeypatch):
    monkeypatch.setattr(v, "USE_LLM_SVC", True)
    seen = {}

    def synth(text, language, speaker, sample_rate, speech_rate):
        seen.update(text=text, language=language, speaker=speaker, sample_rate=sample_rate, speech_rate=speech_rate)
        return seen.get("audio_override", b"AUDIO")

    monkeypatch.setattr(v, "synthesize_speech_llm_svc", synth)
    return seen


def test_speak_text_service_saves_file(svc_tts, tmp_path):
    out = tmp_path / "speech.wav"
    assert v.speak_text("Привет", speaker="kseniya", speech_rate=1.1, save_to_file=str(out)) is True
    assert out.read_bytes() == b"AUDIO"
    assert (svc_tts["text"], svc_tts["language"], svc_tts["speaker"]) == ("Привет", "ru", "kseniya")
    assert (svc_tts["sample_rate"], svc_tts["speech_rate"]) == (48000, 1.1)


def test_speak_text_service_language_by_text_for_foreign_speaker(svc_tts, monkeypatch, tmp_path):
    out = tmp_path / "s.wav"
    v.speak_text("Hello world", speaker="en_0", save_to_file=str(out))
    assert svc_tts["language"] == "en"
    v.speak_text("Привет мир hello", speaker="en_0", save_to_file=str(out))
    assert svc_tts["language"] == "ru"  # доля кириллицы > 0.3


def test_speak_text_service_plays_audio(svc_tts, sd, monkeypatch):
    buf = io.BytesIO()
    import scipy.io.wavfile

    scipy.io.wavfile.write(buf, 48000, np.array([1, 2, 3], dtype=np.int16))
    svc_tts["audio_override"] = buf.getvalue()
    assert v.speak_text("Привет", speaker="baya") is True
    (audio, rate), = sd.played
    assert rate == 48000 and audio.tolist() == [1, 2, 3] and sd.waits == 1


def test_speak_text_service_failure_falls_back_to_local(svc_tts, monkeypatch):
    def boom(**kw):
        raise ConnectionError("сервис недоступен")

    monkeypatch.setattr(v, "synthesize_speech_llm_svc", lambda **kw: boom(**kw))
    monkeypatch.setattr(v, "tts_model_loaded", False)
    engine_calls = []
    monkeypatch.setattr(v, "speak_text_pyttsx3", lambda t, r: engine_calls.append((t, r)) or True)
    assert v.speak_text("Привет", speech_rate=1.3) is True
    assert engine_calls == [("Привет", 1.3)]


def test_speak_text_service_empty_audio_falls_back_to_local(svc_tts, monkeypatch):
    svc_tts["audio_override"] = b""
    monkeypatch.setattr(v, "speak_text_pyttsx3", lambda t, r: False)
    assert v.speak_text("Привет") is False


def test_speak_text_empty_text_returns_false():
    assert v.speak_text("") is False


def test_speak_text_prefers_silero_then_pyttsx3(monkeypatch):
    monkeypatch.setattr(v, "tts_model_loaded", True)
    silero = []
    monkeypatch.setattr(v, "speak_text_silero", lambda *a, **k: silero.append((a, k)) or True)
    monkeypatch.setattr(v, "speak_text_pyttsx3", lambda *a: pytest.fail("Silero уже озвучил"))
    assert v.speak_text("Привет", speaker="aidar", voice_id="ru", speech_rate=1.2, save_to_file="f.wav") is True
    assert silero == [(("Привет", "aidar"), {"lang": "ru", "speech_rate": 1.2, "save_to_file": "f.wav"})]


def test_speak_text_falls_back_to_pyttsx3_only_without_file(monkeypatch):
    monkeypatch.setattr(v, "tts_model_loaded", True)
    monkeypatch.setattr(v, "speak_text_silero", lambda *a, **k: False)
    spoken = []
    monkeypatch.setattr(v, "speak_text_pyttsx3", lambda t, r: spoken.append(t) or True)
    assert v.speak_text("Привет") is True and spoken == ["Привет"]
    assert v.speak_text("Привет", save_to_file="f.wav") is False  # pyttsx3 в файл не пишет
    assert spoken == ["Привет"]


# ---------------- STT ----------------


def test_check_stt_available(monkeypatch):
    from backend.transcription import whisperx_transcriber as wxt

    monkeypatch.setattr(v, "USE_LLM_SVC", True)
    assert v.check_stt_available() is True
    monkeypatch.setattr(v, "USE_LLM_SVC", False)
    monkeypatch.setattr(wxt, "WHISPERX_AVAILABLE", True)
    assert v.check_stt_available() is True
    monkeypatch.setattr(wxt, "WHISPERX_AVAILABLE", False)
    assert v.check_stt_available() is False

    class Bad:
        def __bool__(self):
            raise RuntimeError("битый флаг")

    monkeypatch.setattr(wxt, "WHISPERX_AVAILABLE", Bad())
    assert v.check_stt_available() is False


def test_recognize_speech_requires_stt_and_sounddevice(monkeypatch):
    monkeypatch.setattr(v, "check_stt_available", lambda: False)
    with pytest.raises(RuntimeError, match="STT недоступен"):
        v.recognize_speech()
    monkeypatch.setattr(v, "check_stt_available", lambda: True)
    monkeypatch.setattr(v, "SOUNDDEVICE_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="sounddevice"):
        v.recognize_speech()


def test_recognize_speech_records_wav_and_cleans_up(monkeypatch, sd, tmp_tempdir):
    monkeypatch.setattr(v, "check_stt_available", lambda: True)
    monkeypatch.setattr(v, "SOUNDDEVICE_AVAILABLE", True)
    seen = {}

    def fake_from_file(path):
        with wave.open(path, "rb") as wf:
            seen.update(channels=wf.getnchannels(), width=wf.getsampwidth(), rate=wf.getframerate(),
                        frames=wf.getnframes())
        seen["path"] = path
        return "распознанный текст"

    monkeypatch.setattr(v, "recognize_speech_from_file", fake_from_file)
    assert v.recognize_speech() == "распознанный текст"
    assert sd.recorded == [(96000, 16000, 1, "int16")] and sd.waits == 1
    assert seen["channels"] == 1 and seen["width"] == 2 and seen["rate"] == 16000 and seen["frames"] == 96000
    assert not os.path.exists(seen["path"])


def test_recognize_speech_unlink_failure_is_ignored(monkeypatch, sd, tmp_tempdir):
    import os

    monkeypatch.setattr(v, "check_stt_available", lambda: True)
    monkeypatch.setattr(v, "SOUNDDEVICE_AVAILABLE", True)
    monkeypatch.setattr(v, "recognize_speech_from_file", lambda p: "ok")
    with monkeypatch.context() as m:
        m.setattr(v.os, "unlink", lambda p: (_ for _ in ()).throw(OSError("занято")))
        assert v.recognize_speech() == "ok"
    leftovers = list(tmp_tempdir.iterdir())
    assert len(leftovers) == 1  # временный wav не удалился, но ошибка не вышла наружу
    leftovers[0].unlink()


def test_recognize_from_file_service_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(v, "USE_LLM_SVC", True)
    path = tmp_path / "in.webm"
    path.write_bytes(b"raw")
    monkeypatch.setattr("backend.transcription.audio_convert.convert_audio_bytes_to_wav",
                        lambda data, name: (b"WAV:" + data, "in.wav"))
    seen = {}
    monkeypatch.setattr(v, "transcribe_audio_whisperx_llm_svc",
                        lambda data, filename, language: seen.update(data=data, filename=filename, language=language) or "текст")
    assert v.recognize_speech_from_file(str(path)) == "текст"
    assert seen == {"data": b"WAV:raw", "filename": "in.wav", "language": "ru"}


def test_recognize_from_file_service_mode_import_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(v, "USE_LLM_SVC", True)
    path = tmp_path / "in.webm"
    path.write_bytes(b"raw")
    monkeypatch.setitem(sys.modules, "backend.transcription.audio_convert", None)  # ImportError
    monkeypatch.setitem(sys.modules, "transcription", SimpleNamespace(__path__=[]))
    monkeypatch.setitem(
        sys.modules, "transcription.audio_convert",
        SimpleNamespace(convert_audio_bytes_to_wav=lambda d, n: (d + b"!", "alt.wav")),
    )
    monkeypatch.setattr(v, "transcribe_audio_whisperx_llm_svc", lambda data, filename, language: f"{data!r}/{filename}")
    assert v.recognize_speech_from_file(str(path)) == "b'raw!'/alt.wav"


class _FakeWX:
    result = (True, "локальный текст")
    cleaned = False

    def transcribe_audio_file(self, path):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def cleanup(self):
        type(self).cleaned = True


def test_recognize_from_file_local_mode(monkeypatch):
    monkeypatch.setattr("backend.transcription.whisperx_transcriber.WhisperXTranscriber", _FakeWX)
    _FakeWX.cleaned = False
    assert v.recognize_speech_from_file("a.wav") == "локальный текст" and _FakeWX.cleaned is True
    monkeypatch.setattr(_FakeWX, "result", (False, "ошибка"))
    assert v.recognize_speech_from_file("a.wav") == ""
    monkeypatch.setattr(_FakeWX, "result", RuntimeError("нет модели"))
    assert v.recognize_speech_from_file("a.wav") == ""


# ---------------- run_voice ----------------


def test_run_voice_requires_stt(monkeypatch):
    monkeypatch.setattr(v, "check_stt_available", lambda: False)
    with pytest.raises(RuntimeError, match="STT недоступен"):
        v.run_voice()


def test_run_voice_loop_processes_phrases_and_survives_errors(monkeypatch):
    monkeypatch.setattr(v, "check_stt_available", lambda: True)
    events = []
    monkeypatch.setattr(v, "init_tts", lambda: events.append("init_tts"))
    inputs = iter(["", "привет", RuntimeError("микрофон"), KeyboardInterrupt()])

    def recognize():
        item = next(inputs)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(v, "recognize_speech", recognize)
    monkeypatch.setattr(v, "save_to_memory", lambda who, text: events.append(("mem", who, text)))
    monkeypatch.setattr(v, "ask_agent", lambda phrase: events.append(("ask", phrase)) or "ответ агента")
    monkeypatch.setattr(v, "speak_text", lambda text: events.append(("speak", text)))
    v.run_voice()  # KeyboardInterrupt завершает цикл штатно
    assert events == [
        "init_tts",
        ("mem", "Пользователь", "привет"),
        ("ask", "привет"),
        ("speak", "ответ агента"),
        ("mem", "Агент", "ответ агента"),
    ]
    # необработанный генератор исчерпан целиком: пустая фраза и ошибка не остановили цикл
    assert next(inputs, "конец") == "конец"
