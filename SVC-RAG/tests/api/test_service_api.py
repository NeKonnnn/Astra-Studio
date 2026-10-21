"""svc-rag HTTP API: инвентаризация маршрутов (аутентификации нет), health, смена размерности/провайдера моделей, диагностика памяти."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.api import router
from app.api.endpoints import health as health_module
from app.api.endpoints import memdiag as memdiag_module
from app.api.endpoints import schema as schema_module
from app.api import rag_common

pytestmark = pytest.mark.unit


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(router, prefix="/v1")
    return application


@pytest.fixture
def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://rag")


EXPECTED_ROUTES = {
    ("GET", "/v1/health"),
    ("POST", "/v1/kb/documents"), ("POST", "/v1/kb/documents/duplicate-check"), ("GET", "/v1/kb/documents"),
    ("GET", "/v1/kb/documents/{document_id}"), ("POST", "/v1/kb/documents/{document_id}/clone"),
    ("DELETE", "/v1/kb/documents/{document_id}"),
    ("POST", "/v1/kb/search"), ("POST", "/v1/kb/reindex"), ("GET", "/v1/kb/reindex/status"),
    ("POST", "/v1/memory-rag/documents"), ("POST", "/v1/memory-rag/documents/duplicate-check"), ("GET", "/v1/memory-rag/documents"),
    ("DELETE", "/v1/memory-rag/documents/{document_id}"), ("POST", "/v1/memory-rag/reindex"), ("GET", "/v1/memory-rag/reindex/status"), ("POST", "/v1/memory-rag/search"),
    ("POST", "/v1/project-rag/projects/{project_id}/documents"), ("POST", "/v1/project-rag/projects/{project_id}/documents/duplicate-check"),
    ("GET", "/v1/project-rag/projects/{project_id}/documents"), ("GET", "/v1/project-rag/projects/{project_id}/documents/{document_id}/chunks"),
    ("DELETE", "/v1/project-rag/projects/{project_id}/documents/{document_id}"), ("DELETE", "/v1/project-rag/projects/{project_id}"),
    ("POST", "/v1/project-rag/projects/{project_id}/search"), ("POST", "/v1/project-rag/reindex"), ("GET", "/v1/project-rag/reindex/status"),
    ("POST", "/v1/schema/embedding-dim"), ("POST", "/v1/schema/models-provider"),
    ("POST", "/v1/diag/search"), ("POST", "/v1/diag/document"),
    ("GET", "/v1/memory-diag/snapshot"), ("POST", "/v1/memory-diag/mode"), ("POST", "/v1/memory-diag/gc"),
}


class TestRouteInventory:
    def routes(self, app):
        return {(m, r.path): r for r in app.routes if isinstance(r, APIRoute) for m in r.methods - {"HEAD", "OPTIONS"}}

    def test_the_set_of_routes_is_exactly_the_documented_one(self, app):
        # Любой новый маршрут — осознанное решение: сюда его нужно добавить вместе с проверкой доступа.
        assert set(self.routes(app)) == EXPECTED_ROUTES

    def test_protected_routes_declare_shared_secret_dependency(self, app):
        # Health без ключа; остальные маршруты объявляют require_svc_rag_api_key
        # (при пустом SVC_RAG_API_KEY проверка — no-op, при заданном — 401).
        routes = self.routes(app)
        health_deps = {
            d.call.__name__
            for (m, p), r in routes.items()
            if p.rstrip("/").endswith("/health")
            for d in r.dependant.dependencies
        }
        protected_deps = {
            d.call.__name__
            for (m, p), r in routes.items()
            if not p.rstrip("/").endswith("/health")
            for d in r.dependant.dependencies
        }

        assert "require_svc_rag_api_key" not in health_deps
        assert "require_svc_rag_api_key" in protected_deps

    def test_reindex_status_endpoints_are_read_only(self, app):
        routes = self.routes(app)

        assert all(m == "GET" for (m, p) in routes if p.endswith("/reindex/status"))


class FakeDb:
    def __init__(self, ok=True, error=None):
        self.ok, self.error = ok, error

    async def health_check(self):
        if self.error:
            raise self.error
        return self.ok


class FakeModelsClient:
    def __init__(self, ok=True, error=None):
        self.ok, self.error = ok, error

    async def health(self):
        if self.error:
            raise self.error
        return self.ok


@pytest.fixture
def health(monkeypatch):
    state = SimpleNamespace(db=FakeDb(), client=FakeModelsClient(), choice={}, db_error=None, client_none=False)

    async def get_db():
        if state.db_error:
            raise state.db_error
        return state.db

    def get_client():
        if state.client_none:
            raise RuntimeError("не создан")
        return state.client

    def get_choice():
        if isinstance(state.choice, Exception):
            raise state.choice
        return state.choice

    monkeypatch.setattr(health_module, "get_db", get_db)
    monkeypatch.setattr(health_module, "get_current_rag_client", get_client)
    monkeypatch.setattr(health_module, "get_model_choice", get_choice)
    monkeypatch.setattr(health_module, "RagModelsClient", lambda: state.client)
    return state


class TestHealth:
    async def test_healthy(self, client, health):
        health.choice = {"embedding": {"provider": "PHOENIX", "model": "frida"}, "reranker": {"provider": "native"}}

        body = (await client.get("/v1/health")).json()

        assert body["status"] == "healthy" and body["rag_models"] is True and body["postgresql"] is True
        assert (body["embedding_provider"], body["embedding_model"], body["reranker_provider"], body["reranker_model"]) == ("PHOENIX", "frida", "native", None)
        assert set(body["embedding_routing"]) >= {"models", "clients", "repos"}

    async def test_database_down_is_degraded_not_an_error(self, client, health):
        health.db_error = RuntimeError("pg down")

        response = await client.get("/v1/health")

        assert response.status_code == 200 and response.json()["status"] == "degraded" and response.json()["postgresql"] is False

    @pytest.mark.parametrize("db", [FakeDb(ok=False), FakeDb(error=RuntimeError("timeout"))])
    async def test_failed_database_checks(self, client, health, db):
        health.db = db

        assert (await client.get("/v1/health")).json()["status"] == "degraded"

    async def test_models_service_down(self, client, health):
        health.client = FakeModelsClient(error=RuntimeError("refused"))

        body = (await client.get("/v1/health")).json()

        assert body["status"] == "degraded" and body["rag_models"] is False and body["postgresql"] is True

    async def test_missing_client_falls_back_to_a_native_one(self, client, health):
        health.client_none = True

        assert (await client.get("/v1/health")).json()["rag_models"] is True

    async def test_defaults_without_a_model_choice(self, client, health):
        health.choice = RuntimeError("не задан")

        body = (await client.get("/v1/health")).json()

        assert (body["embedding_provider"], body["reranker_provider"], body["embedding_model"]) == ("native", "native", None)


class TestSchema:
    @pytest.fixture
    def dim(self, monkeypatch):
        state = SimpleNamespace(calls=[], error=None)

        async def ensure(value):
            state.calls.append(value)
            if state.error:
                raise state.error
            return {"embedding_dim": value, "tables": ["kb_vectors"]}

        monkeypatch.setattr(schema_module, "ensure_embedding_dim", ensure)
        return state

    async def test_dimension_is_applied(self, client, dim):
        body = (await client.post("/v1/schema/embedding-dim", json={"embedding_dim": 1536})).json()

        assert body == {"success": True, "embedding_dim": 1536, "tables": ["kb_vectors"]} and dim.calls == [1536]

    @pytest.mark.parametrize("value", [0, -1, 8193, "x", None, 1.5])
    async def test_dimension_is_validated_before_any_work(self, client, dim, value):
        assert (await client.post("/v1/schema/embedding-dim", json={"embedding_dim": value})).status_code == 422 and dim.calls == []

    @pytest.mark.parametrize("value", [1, 8192])
    async def test_dimension_boundaries(self, client, dim, value):
        assert (await client.post("/v1/schema/embedding-dim", json={"embedding_dim": value})).status_code == 200

    async def test_value_errors_are_400_and_other_errors_500(self, client, dim):
        dim.error = ValueError("неподдерживаемая размерность")
        response = await client.post("/v1/schema/embedding-dim", json={"embedding_dim": 5})
        assert response.status_code == 400 and "неподдерживаемая" in response.json()["detail"]

        dim.error = RuntimeError("pg: permission denied for table kb_vectors")
        response = await client.post("/v1/schema/embedding-dim", json={"embedding_dim": 5})
        assert response.status_code == 500 and "permission denied" in response.json()["detail"]  # known issue: текст ошибки БД в ответе

    @pytest.fixture
    def provider(self, monkeypatch):
        import app.dependencies as dependencies

        state = SimpleNamespace(calls=[], error=None)

        async def switch(model_type, provider, model):
            state.calls.append((model_type, provider, model))
            if state.error:
                raise state.error
            return {"embedding_dim": 1024}

        monkeypatch.setattr(dependencies, "set_rag_models_provider", switch)
        return state

    async def test_provider_switch(self, client, provider):
        body = (await client.post("/v1/schema/models-provider", json={"model_type": "embedding", "provider": "PHOENIX", "model": "frida"})).json()

        assert body == {"success": True, "embedding_dim": 1024} and provider.calls == [("embedding", "PHOENIX", "frida")]

    async def test_model_is_optional_for_native(self, client, provider):
        await client.post("/v1/schema/models-provider", json={"model_type": "reranker", "provider": "native"})

        assert provider.calls == [("reranker", "native", None)]

    @pytest.mark.parametrize("body", [{}, {"model_type": "chat", "provider": "x"}, {"model_type": "embedding", "provider": ""}, {"provider": "x"}])
    async def test_provider_payload_is_validated(self, client, provider, body):
        assert (await client.post("/v1/schema/models-provider", json=body)).status_code == 422 and provider.calls == []

    async def test_provider_errors(self, client, provider):
        provider.error = ValueError("провайдер не найден")
        assert (await client.post("/v1/schema/models-provider", json={"model_type": "embedding", "provider": "x"})).status_code == 400

        provider.error = RuntimeError("probe failed")
        assert (await client.post("/v1/schema/models-provider", json={"model_type": "embedding", "provider": "x"})).status_code == 500


class TestMemoryDiagnostics:
    @pytest.fixture
    def probe(self, monkeypatch):
        state = SimpleNamespace(mode="off", traced=False, snapshot_error=None, rss=[100.0, 90.5], blocks_seq=[1000, 800])

        monkeypatch.setattr(memdiag_module.memprobe, "mode", lambda: state.mode)

        def set_mode(value):
            state.mode = value or "on"
            return state.mode

        monkeypatch.setattr(memdiag_module.memprobe, "set_mode", set_mode)
        monkeypatch.setattr(memdiag_module.memprobe, "ensure_trace_started", lambda: setattr(state, "traced", True))
        monkeypatch.setattr(memdiag_module.memprobe, "tracing", lambda: state.traced)

        def snapshot(deep=False, limit=25):
            if state.snapshot_error:
                raise state.snapshot_error
            return {"deep": deep, "limit": limit}

        monkeypatch.setattr(memdiag_module.memprobe, "snapshot", snapshot)
        monkeypatch.setattr(memdiag_module.memprobe, "rss_mb", lambda: state.rss.pop(0) if state.rss else None)
        monkeypatch.setattr(memdiag_module.memprobe, "blocks", lambda: state.blocks_seq.pop(0))
        return state

    async def test_snapshot_parameters(self, client, probe):
        assert (await client.get("/v1/memory-diag/snapshot")).json() == {"deep": False, "limit": 25}
        assert (await client.get("/v1/memory-diag/snapshot", params={"deep": True, "limit": 5})).json() == {"deep": True, "limit": 5}

    @pytest.mark.parametrize("limit", [0, 201])
    async def test_snapshot_limit_bounds(self, client, probe, limit):
        assert (await client.get("/v1/memory-diag/snapshot", params={"limit": limit})).status_code == 422

    async def test_snapshot_errors_are_data_not_500(self, client, probe):
        probe.snapshot_error = RuntimeError("tracemalloc off")

        response = await client.get("/v1/memory-diag/snapshot")

        assert response.status_code == 200 and response.json()["тип_ошибки"] == "RuntimeError"

    async def test_mode_switch_and_tracemalloc(self, client, probe):
        body = (await client.post("/v1/memory-diag/mode", params={"value": "full"})).json()

        assert body == {"было": "off", "стало": "full", "tracemalloc_идёт": True}

    async def test_mode_reset(self, client, probe):
        assert (await client.post("/v1/memory-diag/mode")).json()["стало"] == "on"

    async def test_gc_report(self, client, probe):
        body = (await client.post("/v1/memory-diag/gc")).json()

        assert body["блоков_освобождено"] == 200 and body["rss_вернулось_мб"] == 9.5 and "как_читать" in body and "собрано_объектов" in body

    async def test_gc_without_rss(self, client, probe):
        probe.rss = []

        body = (await client.post("/v1/memory-diag/gc")).json()

        assert "rss_мб_до" not in body


class TestCommonRequestFields:
    def test_filters_are_converted_only_when_active(self):
        from datetime import datetime

        assert rag_common.filters_body_to_domain(None) is None
        assert rag_common.filters_body_to_domain(rag_common.RagSearchFiltersBody()) is None
        assert rag_common.filters_body_to_domain(rag_common.RagSearchFiltersBody(filename_contains="   ")) is None
        active = rag_common.filters_body_to_domain(rag_common.RagSearchFiltersBody(filename_contains="договор", date_from=datetime(2026, 1, 1)))
        assert active.filename_contains == "договор" and active.date_from == datetime(2026, 1, 1)

    def test_filename_filter_is_bounded(self):
        with pytest.raises(Exception):
            rag_common.RagSearchFiltersBody(filename_contains="я" * 513)

    def test_eval_kwargs(self):
        body = rag_common.RagSearchEvalBody(eval_gold_document_ids=[1, 2], eval_gold_chunks=[{"document_id": 3, "chunk_index": 0}], eval_llm_judge=True)

        assert rag_common.eval_search_kwargs_from_body(body) == {"eval_gold_document_ids": [1, 2], "eval_gold_chunks": [(3, 0)], "eval_llm_judge": True}
        assert rag_common.eval_search_kwargs_from_body(rag_common.RagSearchEvalBody()) == {"eval_gold_document_ids": None, "eval_gold_chunks": None, "eval_llm_judge": False}
