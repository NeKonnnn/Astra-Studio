"""Лимиты Socket.IO для длинной image-gen и крупных inline-payload."""

from __future__ import annotations

from backend.realtime.socket_limits import (
    socket_max_http_buffer_size,
    socket_ping_interval_sec,
    socket_ping_timeout_sec,
)


def test_default_ping_timeout_is_900_for_comfy_image_gen(monkeypatch):
    monkeypatch.delenv("SOCKET_PING_TIMEOUT_SEC", raising=False)
    assert socket_ping_timeout_sec() == 900


def test_ping_timeout_env_override_and_floor(monkeypatch):
    monkeypatch.setenv("SOCKET_PING_TIMEOUT_SEC", "1200")
    assert socket_ping_timeout_sec() == 1200
    monkeypatch.setenv("SOCKET_PING_TIMEOUT_SEC", "5")
    assert socket_ping_timeout_sec() == 30
    monkeypatch.setenv("SOCKET_PING_TIMEOUT_SEC", "not-a-number")
    assert socket_ping_timeout_sec() == 900


def test_default_http_buffer_is_large_enough_for_inline_images(monkeypatch):
    monkeypatch.delenv("SOCKET_MAX_HTTP_BUFFER_BYTES", raising=False)
    assert socket_max_http_buffer_size() >= 50 * 1024 * 1024


def test_ping_interval_defaults_and_floor(monkeypatch):
    monkeypatch.delenv("SOCKET_PING_INTERVAL_SEC", raising=False)
    assert socket_ping_interval_sec() == 15
    monkeypatch.setenv("SOCKET_PING_INTERVAL_SEC", "1")
    assert socket_ping_interval_sec() == 5
