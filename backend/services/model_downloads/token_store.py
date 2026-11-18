"""Хранение HF token (файл рядом с app settings, маскирование)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_TOKEN_FILE_NAME = "hf_token.txt"


def _token_file() -> Path:
    backend = Path(__file__).resolve().parents[2]
    # settings.json живёт в backend/
    return backend / _TOKEN_FILE_NAME


def get_hf_token() -> Optional[str]:
    env = (os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN") or "").strip()
    if env:
        return env
    path = _token_file()
    if path.is_file():
        try:
            val = path.read_text(encoding="utf-8").strip()
            return val or None
        except OSError:
            return None
    return None


def set_hf_token(token: Optional[str]) -> None:
    path = _token_file()
    value = (token or "").strip()
    if not value:
        if path.exists():
            path.unlink(missing_ok=True)
        return
    path.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def mask_token(token: Optional[str]) -> dict:
    t = (token or "").strip()
    if not t:
        return {"configured": False, "masked": ""}
    if len(t) <= 8:
        masked = "*" * len(t)
    else:
        masked = t[:4] + "*" * (len(t) - 8) + t[-4:]
    return {"configured": True, "masked": masked}
