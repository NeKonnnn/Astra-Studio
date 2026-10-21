"""

Блокировка входа после N неудачных попыток (настройки: auth.login_lockout в config.yml).

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from backend.settings.logging import get_logger

logger = get_logger(__name__)

_lock = RLock()

_states: Dict[str, "_AttemptState"] = {}

_config_logged = False


@dataclass
class _AttemptState:

    failed_attempts: int = 0

    locked_until: Optional[datetime] = None


def _normalize_username(username: str) -> str:

    return (username or "").strip().lower()


def _get_lockout_config():

    from backend.settings.config import get_settings

    return get_settings().auth.login_lockout


def format_duration_ru(total_seconds: int) -> str:
    """Человекочитаемая длительность для UI: «15 мин. 30 сек.»"""

    sec = max(0, int(total_seconds))

    minutes, seconds = divmod(sec, 60)

    if minutes > 0 and seconds > 0:

        return f"{minutes} мин. {seconds} сек."

    if minutes > 0:

        return f"{minutes} мин."

    return f"{seconds} сек."


def _is_enabled() -> bool:

    cfg = _get_lockout_config()

    return (
        bool(cfg.enabled)
        and cfg.max_failed_attempts > 0
        and cfg.lockout_duration_seconds > 0
    )


def _log_lockout_config_once() -> None:

    global _config_logged

    if _config_logged:

        return

    _config_logged = True

    cfg = _get_lockout_config()

    active = _is_enabled()

    logger.warning(
        "auth login lockout config: active=%s enabled=%s max_failed=%s lockout_sec=%s",
        active,
        cfg.enabled,
        cfg.max_failed_attempts,
        cfg.lockout_duration_seconds,
    )


def _get_state(key: str) -> _AttemptState:

    with _lock:

        if key not in _states:

            _states[key] = _AttemptState()

        return _states[key]


def _clear_state(key: str) -> None:

    with _lock:

        _states.pop(key, None)


def _expire_lock_if_needed(state: _AttemptState) -> None:

    if state.locked_until and datetime.utcnow() >= state.locked_until:

        state.failed_attempts = 0

        state.locked_until = None


def _remaining_lock_seconds(state: _AttemptState) -> int:

    if not state.locked_until:

        return 0

    delta = state.locked_until - datetime.utcnow()

    return max(0, int(delta.total_seconds()))


def assert_login_not_locked(username: str) -> None:
    """HTTP 429, если учётная запись временно заблокирована."""

    _log_lockout_config_once()

    if not _is_enabled():

        return

    key = _normalize_username(username)

    if not key:

        return

    with _lock:

        state = _get_state(key)

        _expire_lock_if_needed(state)

        if state.locked_until and datetime.utcnow() < state.locked_until:

            seconds = _remaining_lock_seconds(state)

            logger.warning(
                "auth login lockout active user=%s remaining_sec=%s",
                username,
                seconds,
            )

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=build_lockout_detail(username),
                headers={"Retry-After": str(seconds)},
            )


def record_failed_login(username: str) -> None:
    """Увеличить счётчик неудачных попыток; при достижении лимита — заблокировать."""

    _log_lockout_config_once()

    if not _is_enabled():

        logger.warning(
            "auth login lockout: счётчик не увеличен (блокировка выключена) user=%s",
            username,
        )

        return

    key = _normalize_username(username)

    if not key:

        return

    cfg = _get_lockout_config()

    with _lock:

        state = _get_state(key)

        _expire_lock_if_needed(state)

        if state.locked_until and datetime.utcnow() < state.locked_until:
            return

        state.failed_attempts += 1

        remaining = max(0, cfg.max_failed_attempts - state.failed_attempts)

        logger.warning(
            "auth login failed attempt user=%s failed_count=%s remaining=%s max=%s",
            username,
            state.failed_attempts,
            remaining,
            cfg.max_failed_attempts,
        )

        if state.failed_attempts >= cfg.max_failed_attempts:

            state.locked_until = datetime.utcnow() + timedelta(
                seconds=cfg.lockout_duration_seconds
            )

            logger.warning(
                "auth login lockout triggered user=%s lockout_sec=%s",
                username,
                cfg.lockout_duration_seconds,
            )


def record_successful_login(username: str) -> None:
    """Сбросить счётчик после успешного входа."""

    key = _normalize_username(username)

    if not key:

        return

    _clear_state(key)


def get_lockout_policy() -> dict:
    """Публичные параметры политики (для UI и /api/auth/login-lockout-policy)."""

    cfg = _get_lockout_config()

    enabled = _is_enabled()

    return {
        "enabled": enabled,
        "max_failed_attempts": cfg.max_failed_attempts,
        "lockout_duration_seconds": cfg.lockout_duration_seconds,
    }


def get_attempt_info(username: str) -> dict:
    """Состояние попыток для пользователя после неудачного входа."""

    policy = get_lockout_policy()

    if not policy["enabled"]:

        return {**policy, "remaining_attempts": None}

    key = _normalize_username(username)

    if not key:

        return {**policy, "remaining_attempts": policy["max_failed_attempts"]}

    cfg = _get_lockout_config()

    with _lock:
        state = _get_state(key)
        _expire_lock_if_needed(state)
        remaining = max(0, cfg.max_failed_attempts - state.failed_attempts)
    return {**policy, "remaining_attempts": remaining}


def build_login_failed_detail(username: str) -> dict:
    """Тело ответа 401: сообщение + оставшиеся попытки."""

    info = get_attempt_info(username)

    lockout_sec = info.get("lockout_duration_seconds", 900)

    detail = {
        "message": "Неверное имя пользователя или пароль",
        "remaining_attempts": info.get("remaining_attempts"),
        "max_failed_attempts": info.get("max_failed_attempts"),
        "lockout_duration_seconds": lockout_sec,
    }

    if info.get("enabled") and info.get("remaining_attempts") == 0:

        detail["message"] = (
            "Неверное имя пользователя или пароль. "
            f"Следующая неудачная попытка заблокирует вход на {format_duration_ru(lockout_sec)}."
        )

    return detail


def build_cef_sec003_extra(username: str, *, account_locked: bool) -> Dict[str, Any]:
    """Поля reason, cn1, cn2 для SEC003 (остаток попыток / длительность блокировки в секундах)."""
    from backend.settings.cef_logger.cef_logger import ldap_reason_suffix

    suffix = ldap_reason_suffix()
    if not _is_enabled():
        if account_locked:
            return {
                "reason": f"Account temporarily locked{suffix}",
                "auth_method": "ldap",
            }
        return {"reason": f"Invalid username/password{suffix}", "auth_method": "ldap"}

    info = get_attempt_info(username)
    remaining = info.get("remaining_attempts")
    max_attempts = info.get("max_failed_attempts")
    lockout_sec = info.get("lockout_duration_seconds")

    if account_locked:
        detail = build_lockout_detail(username)
        retry_sec = int(detail.get("retry_after_seconds") or 0)
        reason = (
            f"Account temporarily locked: retry after {retry_sec}s "
            f"({format_duration_ru(retry_sec)}), remaining attempts 0/{max_attempts}{suffix}"
        )

        return {
            "reason": reason,
            "cn1": 0,
            "cn1Label": "RemainingLoginAttempts",
            "cn2": retry_sec,
            "cn2Label": "LockoutDurationSeconds",
            "auth_method": "ldap",
        }

    reason = (
        f"Invalid username/password: remaining attempts {remaining}/{max_attempts}, "
        f"lockout {lockout_sec}s ({format_duration_ru(lockout_sec)}) if limit exceeded{suffix}"
    )

    return {
        "reason": reason,
        "cn1": remaining if remaining is not None else max_attempts,
        "cn1Label": "RemainingLoginAttempts",
        "cn2": lockout_sec,
        "cn2Label": "LockoutDurationSeconds",
        "auth_method": "ldap",
    }


def build_lockout_detail(username: str) -> dict:
    """Тело ответа 429 при активной блокировке."""

    key = _normalize_username(username)

    policy = get_lockout_policy()

    seconds = 0

    if key:
        with _lock:
            state = _get_state(key)
            seconds = _remaining_lock_seconds(state)

    if not seconds:
        seconds = int(policy.get("lockout_duration_seconds") or 900)

    return {
        "message": (
            "Слишком много неудачных попыток входа. "
            f"Повторите через {format_duration_ru(seconds)}."
        ),
        "remaining_attempts": 0,
        "max_failed_attempts": policy.get("max_failed_attempts"),
        "lockout_duration_seconds": policy.get("lockout_duration_seconds"),
        "retry_after_seconds": seconds,
    }
