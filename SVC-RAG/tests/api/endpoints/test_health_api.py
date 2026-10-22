"""svc-rag /health: ветка, когда диагностика роутинга моделей недоступна (остальное покрыто test_service_api.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.api.endpoints import health as health_module
from app.services import embed_routing

from ._fakes import make_client

pytestmark = pytest.mark.unit


async def test_routing_overview_failure_does_not_break_health(monkeypatch):
    async def get_db():
        return SimpleNamespace(health_check=_true)

    async def _true():
        return True

    class Client:
        async def health(self):
            return True

    def boom():
        raise RuntimeError("роутинг недоступен")

    monkeypatch.setattr(health_module, "get_db", get_db)
    monkeypatch.setattr(health_module, "get_current_rag_client", lambda: Client())
    monkeypatch.setattr(health_module, "get_model_choice", lambda: {})
    monkeypatch.setattr(embed_routing, "routing_overview", boom)
    app = FastAPI()
    app.include_router(health_module.router)

    body = (await make_client(app).get("/health")).json()

    assert body["status"] == "healthy"
    assert body["embedding_routing"] is None
    assert body["embedding_provider"] == "native"
