"""Общие фикстуры тестов backend/transcription (зона B6).

torch / whisperx / silero / librosa / sounddevice в окружении тестов нет и ставить их нельзя,
поэтому всё, что решается на импорте модуля, проверяется на КОПИИ модуля, выполняемой
с подменёнными sys.modules (load_copy). Исходный код компилируется с настоящим путём файла,
поэтому покрытие засчитывается исходному файлу.
"""

import sys
import tempfile
import types
from pathlib import Path

import pytest

TRANSCRIPTION_DIR = Path(__file__).resolve().parents[3] / "transcription"


@pytest.fixture
def load_copy():
    """Фабрика: выполнить исходник модуля заново в новом пространстве имён."""

    def _load(filename: str, name: str, file_override: str | None = None) -> types.ModuleType:
        path = TRANSCRIPTION_DIR / filename
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        module = types.ModuleType(name)
        module.__file__ = file_override or str(path)
        module.__package__ = "backend.transcription"
        sys.modules[name] = module  # нужен dataclass/typing при исполнении
        try:
            exec(code, module.__dict__)  # noqa: S102
        finally:
            sys.modules.pop(name, None)
        return module

    return _load


@pytest.fixture(autouse=True)
def tmp_tempdir(monkeypatch, tmp_path):
    """Все временные файлы (tempfile) - только внутри tmp_path."""
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    return tmp
