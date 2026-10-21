"""Общие фикстуры backend-тестов. Тесты не должны ходить в сеть и в БД."""

import logging
import os
import socket
from pathlib import Path

import pytest

# Юнит-тесты не должны выходить в сеть. Часть модулей на импорте пытается
# скачать модели (backend/transcription/voice.py: torch.hub), поэтому защита
# ставится при загрузке conftest, до сбора тестов. Unix-сокеты (asyncio) не трогаем.
# Интеграционный прогон снимает защиту: ASTRA_TESTS_ALLOW_NETWORK=1.
if os.environ.get("ASTRA_TESTS_ALLOW_NETWORK") != "1":
    _real_connect = socket.socket.connect

    def _guarded_connect(self, address):
        # Unix-сокеты (если есть) и loopback нужны asyncio (socketpair на Windows).
        af_unix = getattr(socket, "AF_UNIX", None)
        if af_unix is not None and self.family == af_unix:
            return _real_connect(self, address)
        host = address[0] if isinstance(address, tuple) and address else None
        if host in ("127.0.0.1", "::1", "localhost"):
            return _real_connect(self, address)
        msg = f"сеть заблокирована в юнит-тестах: connect({address!r})"
        raise RuntimeError(msg)

    socket.socket.connect = _guarded_connect

# Конфиг читается при импорте backend.* (module-level singleton'ы), поэтому
# переменные выставляем здесь, а не в фикстуре. Путь должен лежать внутри
# корня приложения (resolve_config_file_path), каталог tests/ подходит.
_TEST_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.test.yml"
os.environ["CONFIG_PATH"] = str(_TEST_CONFIG)
os.environ.setdefault("LDAP_ENABLED", "false")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("JWT_REFRESH_SECRET", "test-refresh-secret")
# Обязательная переменная: SessionTimeoutConfig читает её только из окружения.
# Force (не setdefault): локальный .env часто задаёт 0/5 для разработки и
# иначе перебивает ожидания тестов (1800 / max 3 активных сессии).
os.environ["AUTH_SESSION_TIMEOUT_SECONDS"] = "1800"
# jwt_handler требует TTL токенов и лимит сессий при импорте.
os.environ.setdefault("JWT_ACCESS_TOKEN_EXPIRE_SECONDS", "900")
os.environ.setdefault("JWT_REFRESH_TOKEN_EXPIRE_SECONDS", "86400")
os.environ["AUTH_MAX_ACTIVE_SESSION"] = "3"


@pytest.fixture(autouse=True)
def _no_real_services(monkeypatch, tmp_path):
    """Отключаем внешние интеграции и уводим запись файлов во временный каталог."""
    monkeypatch.setenv("LDAP_ENABLED", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("JWT_REFRESH_SECRET", "test-refresh-secret")
    monkeypatch.setenv("BACKEND_LOG_DIR", str(tmp_path / "logs"))


@pytest.fixture(autouse=True)
def _backend_logs_reach_caplog():
    """configure_logging ставит backend.propagate=False — caplog на root ничего не видит."""
    backend = logging.getLogger("backend")
    prev = backend.propagate
    backend.propagate = True
    try:
        yield
    finally:
        backend.propagate = prev


@pytest.fixture
def test_config_path() -> Path:
    return _TEST_CONFIG
