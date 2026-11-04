"""Локальное хранилище профиля: статус и свой аватар (поверх LDAP/Keycloak)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.settings.config import get_settings
from backend.settings.logging import get_logger

logger = get_logger(__name__)

_SAFE_USER = re.compile(r"[^a-zA-Z0-9._@-]+")
_ALLOWED_AVATAR = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_MAX_AVATAR_BYTES = 5 * 1024 * 1024
_MAX_STATUS_LEN = 160
# Несколько codepoint'ов на один эмодзи (ZWJ, skin tone) — запас до 16 символов.
_MAX_STATUS_EMOJI_LEN = 16


def _user_key(user_id: str) -> str:
    return _SAFE_USER.sub("_", (user_id or "").strip().lower()) or "unknown"


def _roots() -> Tuple[Path, Path]:
    base = Path(get_settings().files.upload_dir) / "user_profiles"
    meta = base / "meta"
    avatars = base / "avatars"
    meta.mkdir(parents=True, exist_ok=True)
    avatars.mkdir(parents=True, exist_ok=True)
    return meta, avatars


def _meta_path(user_id: str) -> Path:
    meta, _ = _roots()
    return meta / f"{_user_key(user_id)}.json"


def load_profile(user_id: str) -> Dict[str, Any]:
    path = _meta_path(user_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("user_profile_store: read failed user=%s", user_id)
        return {}


def save_profile(user_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    current = load_profile(user_id)
    for key, value in patch.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    path = _meta_path(user_id)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def get_status(user_id: str) -> Optional[str]:
    status = load_profile(user_id).get("status")
    if isinstance(status, str):
        text = status.strip()
        return text or None
    return None


def set_status(user_id: str, status: Optional[str]) -> Optional[str]:
    if status is None:
        save_profile(user_id, {"status": None})
        return None
    text = str(status).strip()[:_MAX_STATUS_LEN]
    save_profile(user_id, {"status": text or None})
    return text or None


def get_status_emoji(user_id: str) -> Optional[str]:
    emoji = load_profile(user_id).get("status_emoji")
    if isinstance(emoji, str):
        text = emoji.strip()
        return text or None
    return None


def set_status_emoji(user_id: str, emoji: Optional[str]) -> Optional[str]:
    if emoji is None:
        save_profile(user_id, {"status_emoji": None})
        return None
    text = str(emoji).strip()[:_MAX_STATUS_EMOJI_LEN]
    # Не принимаем «текст вместо эмодзи»: только короткий фрагмент без пробелов/переносов.
    if not text or any(ch.isspace() for ch in text):
        save_profile(user_id, {"status_emoji": None})
        return None
    save_profile(user_id, {"status_emoji": text})
    return text


def has_custom_avatar(user_id: str) -> bool:
    meta = load_profile(user_id)
    rel = meta.get("avatar_file")
    if not isinstance(rel, str) or not rel.strip():
        return False
    _, avatars = _roots()
    return (avatars / Path(rel).name).is_file()


def read_custom_avatar(user_id: str) -> Optional[Tuple[bytes, str]]:
    meta = load_profile(user_id)
    rel = meta.get("avatar_file")
    ctype = meta.get("avatar_content_type") or "image/jpeg"
    if not isinstance(rel, str) or not rel.strip():
        return None
    _, avatars = _roots()
    path = avatars / Path(rel).name
    if not path.is_file():
        return None
    try:
        return path.read_bytes(), str(ctype)
    except Exception:
        logger.exception("user_profile_store: avatar read failed user=%s", user_id)
        return None


def save_custom_avatar(user_id: str, data: bytes, content_type: str) -> str:
    ctype = (content_type or "").split(";")[0].strip().lower()
    ext = _ALLOWED_AVATAR.get(ctype)
    if not ext:
        raise ValueError("Поддерживаются JPEG, PNG, WebP и GIF")
    if not data:
        raise ValueError("Пустой файл")
    if len(data) > _MAX_AVATAR_BYTES:
        raise ValueError("Размер аватара не должен превышать 5MB")
    _, avatars = _roots()
    key = _user_key(user_id)
    # Удаляем старые расширения того же пользователя
    for old in avatars.glob(f"{key}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    filename = f"{key}{ext}"
    path = avatars / filename
    path.write_bytes(data)
    save_profile(
        user_id,
        {"avatar_file": filename, "avatar_content_type": ctype},
    )
    return filename


def delete_custom_avatar(user_id: str) -> None:
    meta = load_profile(user_id)
    rel = meta.get("avatar_file")
    if isinstance(rel, str) and rel.strip():
        _, avatars = _roots()
        path = avatars / Path(rel).name
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                logger.exception("user_profile_store: avatar delete failed")
    save_profile(user_id, {"avatar_file": None, "avatar_content_type": None})
