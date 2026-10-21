"""Общие фикстуры svc-rag. Модели эмбеддингов и БД в юнит-тестах не нужны."""

import os
from pathlib import Path

import pytest

# Config читается при импорте app.* (module-level settings), поэтому — здесь.
_TEST_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.test.yml"
if _TEST_CONFIG.is_file():
    os.environ["CONFIG_PATH"] = str(_TEST_CONFIG)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _caplog_sees_app_logs():
    """App-логгер с propagate=False не доходит до caplog (root); для unit-тестов включаем."""
    import logging

    from app.core.logging.setup import APP_LOGGER_NAME

    app_log = logging.getLogger(APP_LOGGER_NAME)
    prev = app_log.propagate
    app_log.propagate = True
    yield
    app_log.propagate = prev
