"""
Локальная аутентификация: username/password в PostgreSQL (таблица local_users).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

import bcrypt

from backend.auth.registration_policy import get_registration_policy
from backend.settings.logging import get_logger

logger = get_logger(__name__)

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def _min_password_length() -> int:
    return int(get_registration_policy()["min_password_length"])


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except Exception:
        logger.exception("local_auth: verify_password failed")
        return False


def validate_username(username: str) -> Optional[str]:
    """Вернуть текст ошибки или None, если логин валиден."""
    uname = (username or "").strip()
    if not uname:
        return "Укажите имя пользователя"
    if not _USERNAME_RE.match(uname):
        return (
            "Имя пользователя: 3–64 символа, латиница, цифры, "
            "точки, дефис или подчёркивание"
        )
    return None


def validate_password(password: str) -> Optional[str]:
    min_len = _min_password_length()
    if not password:
        return "Укажите пароль"
    if len(password) < min_len:
        return f"Пароль должен быть не короче {min_len} символов"
    if len(password.encode("utf-8")) > 72:
        # bcrypt обрезает до 72 байт — лучше не принимать такие пароли молча
        return "Пароль слишком длинный (максимум 72 байта)"
    return None


def profile_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    username = row["username"]
    return {
        "user_id": username,
        "username": username,
        "email": None,
        "full_name": username,
        "is_active": bool(row.get("is_active", True)),
        "is_admin": bool(row.get("is_admin", False)),
        "auth_source": "local",
        "auth_method": "local",
        "groups": [],
        "ldap_groups": [],
    }


async def authenticate_local(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Проверить логин/пароль в PostgreSQL. None — нет пользователя или неверный пароль."""
    uname = (username or "").strip()
    if not uname or not password:
        return None
    try:
        from backend.database.init_db import get_local_user_repository

        repo = get_local_user_repository()
    except Exception:
        logger.debug("local_auth: PostgreSQL / local_users недоступны")
        return None

    row = await repo.get_by_username(uname)
    if not row or not row.get("is_active"):
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return profile_from_row(row)


async def fetch_local_user_profile(username: str) -> Optional[Dict[str, Any]]:
    """Профиль локального пользователя без проверки пароля (для refresh)."""
    uname = (username or "").strip()
    if not uname:
        return None
    try:
        from backend.database.init_db import get_local_user_repository

        repo = get_local_user_repository()
    except Exception:
        return None
    row = await repo.get_by_username(uname)
    if not row or not row.get("is_active"):
        return None
    return profile_from_row(row)


async def register_local_user(
    username: str,
    password: str,
    *,
    is_admin: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Создать локального пользователя.

    is_admin по умолчанию берётся из политики (обычно False).
    Если локальных пользователей ещё нет и bootstrap_admin=True — первый
    пользователь становится админом (безопасный bootstrap после установки).
    """
    err = validate_username(username)
    if err:
        raise ValueError(err)
    err = validate_password(password)
    if err:
        raise ValueError(err)

    uname = username.strip()
    policy = get_registration_policy()
    try:
        from backend.database.init_db import get_local_user_repository

        repo = get_local_user_repository()
    except Exception as e:
        logger.exception("local_auth: register — PostgreSQL недоступен")
        raise RuntimeError(
            "Регистрация временно недоступна: база данных не инициализирована"
        ) from e

    existing = await repo.get_by_username(uname)
    if existing:
        raise ValueError("Пользователь с таким именем уже существует")

    want_admin = bool(policy["default_is_admin"]) if is_admin is None else bool(is_admin)
    if policy["bootstrap_admin"]:
        total = await repo.count_users()
        if total == 0:
            want_admin = True

    password_hash = hash_password(password)
    row = await repo.create_user(
        uname, password_hash, is_admin=want_admin, is_active=True
    )
    if not row:
        raise ValueError("Пользователь с таким именем уже существует")
    return profile_from_row(row)
