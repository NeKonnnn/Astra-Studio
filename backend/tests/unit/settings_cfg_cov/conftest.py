"""Фикстуры тестов конфигурации: чистое окружение и загрузка YAML из tmp_path."""

import os

import pytest

_PREFIXES = (
    "MCP_",
    "PLUGIN_",
    "PLUGINS_",
    "AUTH_LOGIN_LOCKOUT_",
    "MODEL_HEALTH_",
    "MODELS_AVAILABLE_CACHE_SECONDS",
)


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch):
    """Убираем ENV, которые влияют на разбор конфигурации, и фиксируем не-Docker."""
    for name in list(os.environ):
        if name.startswith(_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOCKER_ENV", "false")
