"""Прореживание CEF-аудита для фонового поллинга интерфейса."""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

# Пути SVC-RAG, которые опрашиваются по таймеру
_POLL_PATH_MARKERS = (
    "/reindex/status",
    "/documents",
)

_ENV_ENABLED = "CEF_AUDIT_POLLING"
_ENV_INTERVAL = "CEF_AUDIT_POLLING_INTERVAL"

_DEFAULT_ENABLED = True
_DEFAULT_INTERVAL = 60

# Окно: (пользователь, метод, путь) -> когда писали последний раз и сколько
# обращений с тех пор пропустили.
_LAST_EMIT: Dict[Tuple[str, str, str], float] = {}
_SUPPRESSED: Dict[Tuple[str, str, str], int] = {}
_LOCK = threading.Lock()
_KEYS_MAX = 512


def _setting(env_name: str, yml_field: str, default: Any) -> Any:
    """Настройка аудита: ENV -> config.yml -> дефолт.

    Импорт конфига внутри функции намеренно: аудит не должен падать, если
    конфиг почему-то не прочитался. get_settings() кэширует результат,
    поэтому обращение на каждый вызов стоит один поиск по словарю.
    """
    raw = (os.getenv(env_name) or "").strip()
    if raw:
        if isinstance(default, bool):
            return raw.lower() in ("1", "true", "yes", "on")
        try:
            return int(raw)
        except ValueError:
            return default
    try:
        from backend.settings import get_settings

        value = getattr(getattr(get_settings(), "audit", None), yml_field, None)
        return default if value is None else value
    except Exception:
        return default


def is_polling_read(method: str, path: str, status: Optional[int] = 200) -> bool:
    """Успешное ЧТЕНИЕ по поллинговому пути - фоновый опрос, не действие."""
    if str(method or "").upper() not in ("GET", "HEAD"):
        return False
    if status is not None:
        try:
            code = int(status)
        except (TypeError, ValueError):
            # Статус не разобрали - не рискуем, пусть аудируется.
            return False
        if not 200 <= code < 400:
            return False
    normalized = (path or "").rstrip("/")
    return any(normalized.endswith(m.rstrip("/")) for m in _POLL_PATH_MARKERS)


def poll_audit_decision(
    user: Optional[dict], method: str, path: str
) -> Tuple[bool, int]:
    """Писать ли событие поллинга и сколько обращений оно представляет."""
    if not _setting(_ENV_ENABLED, "cef_polling", _DEFAULT_ENABLED):
        return (False, 0)
    try:
        interval = float(
            _setting(_ENV_INTERVAL, "cef_polling_interval_seconds", _DEFAULT_INTERVAL)
        )
    except (TypeError, ValueError):
        interval = float(_DEFAULT_INTERVAL)
    if interval <= 0:
        return (True, 1)

    suser = ""
    if user:
        suser = str(user.get("username") or user.get("user_id") or "")
    key = (suser or "SYSTEM", str(method or "").upper(), (path or "").rstrip("/"))
    now = time.monotonic()
    with _LOCK:
        last = _LAST_EMIT.get(key)
        if last is not None and (now - last) < interval:
            _SUPPRESSED[key] = _SUPPRESSED.get(key, 0) + 1
            return (False, 0)
        count = _SUPPRESSED.pop(key, 0) + 1
        _LAST_EMIT[key] = now
        if len(_LAST_EMIT) > _KEYS_MAX:
            stale = [k for k, t in _LAST_EMIT.items() if (now - t) > interval * 10]
            for k in stale:
                _LAST_EMIT.pop(k, None)
                _SUPPRESSED.pop(k, None)
        return (True, count)


def poll_count_extra(count: int) -> Dict[str, Any]:
    """Поля CEF со счётчиком - только когда событие представляет больше одного."""
    if count > 1:
        return {"cn1": count, "cn1Label": "RequestCount"}
    return {}


def reset_state() -> None:
    """Забыть окна (для тестов)."""
    with _LOCK:
        _LAST_EMIT.clear()
        _SUPPRESSED.clear()
