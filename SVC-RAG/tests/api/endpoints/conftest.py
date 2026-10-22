"""Общие фикстуры API-тестов kb / memory_rag / project_rag / diag / health.

Приложение собирается из «голых» роутеров конкретных модулей, а сервисы
подменяются через ``app.dependency_overrides`` на записывающие фейки: БД,
эмбеддинги и сеть не нужны.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from app.dependencies import (
    get_kb_service,
    get_memory_rag_service,
    get_project_rag_service,
)
from tests.api.endpoints._fakes import FakeStoreService


@pytest.fixture
def kb_svc() -> FakeStoreService:
    return FakeStoreService()


@pytest.fixture
def mem_svc() -> FakeStoreService:
    return FakeStoreService()


@pytest.fixture
def prj_svc() -> FakeStoreService:
    return FakeStoreService()


@pytest.fixture
def make_app(kb_svc, mem_svc, prj_svc):
    """Фабрика приложения: подключает нужные роутеры и подменяет get_*_service."""

    def _make(*routers_with_prefix) -> FastAPI:
        application = FastAPI()
        for router, prefix in routers_with_prefix:
            application.include_router(router, prefix=prefix)
        application.dependency_overrides[get_kb_service] = lambda: kb_svc
        application.dependency_overrides[get_memory_rag_service] = lambda: mem_svc
        application.dependency_overrides[get_project_rag_service] = lambda: prj_svc
        return application

    return _make


@pytest.fixture(autouse=True)
def _clean_block_env(monkeypatch):
    monkeypatch.delenv("RAG_BLOCK_SEARCH_DURING_REINDEX", raising=False)
