"""Проверка, что каркас тестов работает и чистые модули импортируются."""

import pytest

pytestmark = pytest.mark.unit


def test_normalize_query_collapses_whitespace():
    from backend.rag_query.preprocess import normalize_query

    assert normalize_query("  привет   мир \n\n\n\nконец ") == "привет мир \n\nконец"


def test_normalize_query_empty():
    from backend.rag_query.preprocess import normalize_query

    assert normalize_query("") == ""


def test_settings_come_from_test_config(test_config_path):
    """Тесты не должны читать локальный backend/config/config.yml."""
    from backend.settings.config import get_settings

    settings = get_settings()
    assert settings.app.name == "astrachat-test"
    assert settings.urls.llm_service_port == "http://llm.test:8001"
    assert settings.mcp.enabled is False
