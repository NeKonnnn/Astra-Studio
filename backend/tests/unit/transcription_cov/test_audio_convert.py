"""Тесты backend/transcription/audio_convert.py: конвертация webm/opus в WAV через ffmpeg (подменён)."""

import logging
import os
from types import SimpleNamespace

import pytest

from backend.transcription import audio_convert as ac

WAV_HEADER = b"RIFF" + b"\x00" * 60  # >= 44 байт


class _Run:
    """Подмена subprocess.run: записывает аргументы и (опционально) создаёт выходной файл."""

    def __init__(self, returncode=0, stderr="", out_bytes=WAV_HEADER, raises=None):
        self.returncode, self.stderr, self.out_bytes, self.raises = returncode, stderr, out_bytes, raises
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.raises:
            raise self.raises
        if self.out_bytes is not None:
            with open(cmd[-1], "wb") as f:
                f.write(self.out_bytes)
        return SimpleNamespace(returncode=self.returncode, stderr=self.stderr)


@pytest.fixture
def ffmpeg(monkeypatch):
    monkeypatch.setattr(ac.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    fixed = SimpleNamespace(timestamp=lambda: 1234.5)
    monkeypatch.setattr(ac, "datetime", SimpleNamespace(now=lambda: fixed))

    def install(run):
        monkeypatch.setattr(ac.subprocess, "run", run)
        return run

    return install


def test_wav_with_riff_header_is_returned_as_is():
    data = b"RIFF1234WAVE"
    assert ac.convert_audio_bytes_to_wav(data, "a.WAV") == (data, "a.WAV")


def test_successful_conversion_returns_wav_and_renames(ffmpeg, tmp_tempdir):
    run = ffmpeg(_Run(out_bytes=b"RIFFconverted" + b"\x00" * 60))
    data, name = ac.convert_audio_bytes_to_wav(b"opus-bytes", "voice/record.webm")
    assert name == "record.wav"
    assert data.startswith(b"RIFFconverted")
    (cmd, kwargs), = run.calls
    assert cmd[0] == "/usr/bin/ffmpeg"
    assert cmd[cmd.index("-ar") + 1] == "16000" and cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le"
    assert cmd[cmd.index("-i") + 1].endswith("stt_in_1234.5.webm")
    assert kwargs["timeout"] == 30
    # временные файлы убраны
    assert list(tmp_tempdir.iterdir()) == []


def test_extension_defaults_to_webm_and_non_riff_wav_is_converted(ffmpeg):
    run = ffmpeg(_Run())
    ac.convert_audio_bytes_to_wav(b"x", "noext")
    assert run.calls[0][0][run.calls[0][0].index("-i") + 1].endswith(".webm")
    ac.convert_audio_bytes_to_wav(b"not-riff", "bad.wav")  # .wav без RIFF - тоже конвертируется
    assert len(run.calls) == 2


def test_ffmpeg_missing_returns_original(monkeypatch, caplog):
    monkeypatch.setattr(ac.shutil, "which", lambda name: None)
    result = ac.convert_audio_bytes_to_wav(b"abc", "a.webm")
    assert result == (b"abc", "a.webm")


def test_ffmpeg_nonzero_exit_returns_original_and_logs_stderr(ffmpeg, capture_logger_ac):
    ffmpeg(_Run(returncode=1, stderr="  Invalid data  ", out_bytes=None))
    assert ac.convert_audio_bytes_to_wav(b"abc", "a.webm") == (b"abc", "a.webm")
    assert any("Invalid data" in m for m in capture_logger_ac.messages)


def test_too_small_output_returns_original(ffmpeg):
    ffmpeg(_Run(out_bytes=b"RIFF"))
    assert ac.convert_audio_bytes_to_wav(b"abc", "a.webm") == (b"abc", "a.webm")


def test_missing_stderr_is_tolerated(ffmpeg):
    ffmpeg(_Run(returncode=2, stderr=None, out_bytes=None))
    assert ac.convert_audio_bytes_to_wav(b"abc", "a.webm") == (b"abc", "a.webm")


def test_file_not_found_from_subprocess_returns_original(ffmpeg, capture_logger_ac):
    ffmpeg(_Run(raises=FileNotFoundError("ffmpeg")))
    assert ac.convert_audio_bytes_to_wav(b"abc", "a.webm") == (b"abc", "a.webm")
    assert any("ffmpeg не найден" in m for m in capture_logger_ac.messages)


def test_unexpected_error_returns_original(ffmpeg, capture_logger_ac):
    import subprocess

    ffmpeg(_Run(raises=subprocess.TimeoutExpired("ffmpeg", 30)))
    assert ac.convert_audio_bytes_to_wav(b"abc", "a.webm") == (b"abc", "a.webm")
    assert any("Ошибка конвертации a.webm" in m for m in capture_logger_ac.messages)


def test_cleanup_failure_does_not_break_result(ffmpeg, monkeypatch):
    ffmpeg(_Run())
    real_remove = os.remove

    def failing_remove(path):
        real_remove(path)
        raise OSError("занято")

    monkeypatch.setattr(ac.os, "remove", failing_remove)
    data, name = ac.convert_audio_bytes_to_wav(b"x", "a.webm")
    assert name == "a.wav" and data.startswith(b"RIFF")


@pytest.fixture
def capture_logger_ac():
    class H(logging.Handler):
        def __init__(self):
            super().__init__(logging.DEBUG)
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    h = H()
    lg = ac.logger
    old = (lg.level, lg.propagate)
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    yield h
    lg.removeHandler(h)
    lg.setLevel(old[0])
