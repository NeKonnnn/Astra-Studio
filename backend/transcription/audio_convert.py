"""Конвертация браузерного аудио (webm/opus) в WAV для WhisperX STT."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from datetime import datetime

from backend.settings.logging import get_logger

logger = get_logger(__name__)


def convert_audio_bytes_to_wav(audio_data: bytes, filename: str) -> tuple[bytes, str]:
    """
    Конвертирует bytes аудио в 16 kHz mono WAV.
    Возвращает (bytes, filename). При ошибке возвращает исходные данные.
    """
    ext = os.path.splitext(filename)[1].lower() or ".webm"
    if ext == ".wav" and audio_data[:4] == b"RIFF":
        return (audio_data, filename)
    temp_dir = tempfile.gettempdir()
    stamp = datetime.now().timestamp()
    in_path = os.path.join(temp_dir, f"stt_in_{stamp}{ext}")
    out_path = os.path.join(temp_dir, f"stt_out_{stamp}.wav")
    try:
        with open(in_path, "wb") as f:
            f.write(audio_data)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            msg = "ffmpeg не найден в PATH"
            raise RuntimeError(msg)
        result = subprocess.run(  # noqa: S603
            [
                ffmpeg,
                "-y",
                "-fflags",
                "+genpts",
                "-i",
                in_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                out_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if (
            result.returncode != 0
            or not os.path.exists(out_path)
            or os.path.getsize(out_path) < 44
        ):
            stderr = (result.stderr or "").strip()
            logger.warning(
                "ffmpeg не сконвертировал %s (%s bytes): %s",
                filename,
                len(audio_data),
                stderr[:400],
            )
            return (audio_data, filename)
        with open(out_path, "rb") as f:
            wav_data = f.read()
        wav_name = os.path.splitext(os.path.basename(filename))[0] + ".wav"
        return (wav_data, wav_name)
    except FileNotFoundError:
        logger.warning("ffmpeg не найден — отправляем исходный формат %s", filename)
        return (audio_data, filename)
    except Exception as e:
        logger.exception("Ошибка операции")
        logger.warning("Ошибка конвертации %s: %s", filename, e)
        return (audio_data, filename)
    finally:
        for path in (in_path, out_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
