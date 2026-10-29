"""Лимиты Socket.IO (без импорта ASGI/settings — удобно для unit-тестов)."""

from __future__ import annotations

import os

_DEFAULT_SOCKET_MAX_HTTP_BUFFER_BYTES = 52 * 1024 * 1024


def socket_max_http_buffer_size() -> int:
    raw = os.getenv("SOCKET_MAX_HTTP_BUFFER_BYTES")
    if raw is None or not str(raw).strip():
        return _DEFAULT_SOCKET_MAX_HTTP_BUFFER_BYTES
    try:
        value = int(str(raw).strip())
    except ValueError:
        return _DEFAULT_SOCKET_MAX_HTTP_BUFFER_BYTES
    return max(value, 1_048_576)


def socket_ping_timeout_sec() -> int:
    """Дефолт 900с — длинная генерация изображений ComfyUI/Flux."""
    raw = os.getenv("SOCKET_PING_TIMEOUT_SEC")
    if raw is None or not str(raw).strip():
        return 900
    try:
        value = int(str(raw).strip())
    except ValueError:
        return 900
    return max(value, 30)


def socket_ping_interval_sec() -> int:
    raw = os.getenv("SOCKET_PING_INTERVAL_SEC")
    if raw is None or not str(raw).strip():
        return 15
    try:
        value = int(str(raw).strip())
    except ValueError:
        return 15
    return max(value, 5)
