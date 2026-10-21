"""Проверка каркаса тестов svc-rag."""

import pytest

pytestmark = pytest.mark.unit


def test_text_sanitize_importable():
    import app.text_sanitize as ts

    assert ts is not None


def test_settings_come_from_test_config():
    """Тесты не должны читать рабочий config/config.yml."""
    from app.core.config import settings

    assert settings.postgresql.database == "astrachat_test"
    assert settings.rag.chunk_size == 500
