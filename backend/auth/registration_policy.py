"""Политика локальной регистрации (безопасные дефолты + ENV).

ENV:
  AUTH_REGISTRATION_ENABLED — разрешить POST /api/auth/register (default: true)
  AUTH_REGISTER_DEFAULT_IS_ADMIN — новым пользователям выдавать is_admin (default: false)
  AUTH_REGISTER_BOOTSTRAP_ADMIN — первый локальный пользователь становится admin (default: true)
  AUTH_MIN_PASSWORD_LENGTH — минимальная длина пароля (default: 8)
"""

from __future__ import annotations

import os
from typing import Any, Dict


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def get_registration_policy() -> Dict[str, Any]:
    """Публичная политика для UI и /register."""
    return {
        "registration_enabled": _env_bool("AUTH_REGISTRATION_ENABLED", True),
        "default_is_admin": _env_bool("AUTH_REGISTER_DEFAULT_IS_ADMIN", False),
        "bootstrap_admin": _env_bool("AUTH_REGISTER_BOOTSTRAP_ADMIN", True),
        "min_password_length": max(4, _env_int("AUTH_MIN_PASSWORD_LENGTH", 8)),
    }


def is_registration_enabled() -> bool:
    return bool(get_registration_policy()["registration_enabled"])
