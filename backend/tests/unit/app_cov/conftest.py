"""Изоляция unit-тестов application.py от локального .env разработчика."""

import pytest


@pytest.fixture(autouse=True)
def _no_real_dotenv(monkeypatch):
    """Реальный .env проекта (DOCKER_ENV=true и т.п.) не должен влиять на тесты.

    Тесты, проверяющие загрузку .env, подменяют модуль dotenv сами.
    """
    try:
        import dotenv
    except ImportError:
        return
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)
