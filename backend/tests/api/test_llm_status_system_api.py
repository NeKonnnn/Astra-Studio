"""routes/llm (агрегированный статус провайдеров), model_comparison, system (health, root)."""

from types import SimpleNamespace

import pytest

from backend.llm_providers.base import ProviderCapabilities, ProviderHealth
from backend.routes import llm as llm_module
from backend.routes import model_comparison as comparison_module
from backend.routes import system as system_module

pytestmark = pytest.mark.unit


class FakeProvider:
    def __init__(self, pid, *, kind="vllm", health=None, error=None, key_set=False):
        self.id = pid
        self.kind = kind
        self.base_url = f"http://{pid}.test"
        self.capabilities = ProviderCapabilities(hot_swap=False, multi_loaded=True, streaming=True, vision=False)
        self._health = health or ProviderHealth(healthy=True, loaded_models=["m"])
        self._error = error
        self._key_set = key_set
        self.calls = 0

    async def health(self):
        self.calls += 1
        if self._error:
            raise self._error
        return self._health

    def secret_status(self):
        return {"api_key_env": None, "api_key_required": False, "api_key_set": self._key_set, "api_key_preview": None}


class FakeRegistry:
    def __init__(self, providers, default):
        self._providers = providers
        self.default_id = default

    def all(self):
        return list(self._providers)


@pytest.fixture(autouse=True)
def _reset_llm_cache(monkeypatch):
    monkeypatch.setattr(llm_module, "_llm_status_cache_payload", None)
    monkeypatch.setattr(llm_module, "_llm_status_cache_ts", 0.0)
    monkeypatch.setattr(llm_module, "_LLM_STATUS_CACHE_SECONDS", 60)


def _registry(monkeypatch, providers, default="a"):
    async def get_registry():
        return FakeRegistry(providers, default)

    monkeypatch.setattr(llm_module, "get_registry", get_registry)


class TestLlmStatus:
    async def test_aggregates_provider_health(self, make_client, monkeypatch):
        _registry(
            monkeypatch,
            [
                FakeProvider("a", kind="llm-svc"),
                FakeProvider("b", health=ProviderHealth(healthy=False, error="down")),
                FakeProvider("c", error=RuntimeError("timeout")),
            ],
        )

        body = (await make_client(llm_module.router).get("/api/llm/status")).json()

        assert body["connected"] is True
        assert body["default_provider_id"] == "a" and body["default_host_id"] == "a"
        assert body["use_llm_svc"] is True
        by_id = {p["id"]: p for p in body["providers"]}
        assert by_id["a"]["healthy"] is True and by_id["a"]["loaded_models"] == ["m"] and by_id["a"]["error"] is None
        assert by_id["b"]["healthy"] is False and by_id["b"]["error"] == "down"
        assert by_id["c"]["healthy"] is False and by_id["c"]["error"] == "timeout"
        assert {h["id"]: h["healthy"] for h in body["hosts"]} == {"a": True, "b": False, "c": False}
        assert body["message"] is None

    async def test_no_healthy_provider_gives_a_hint(self, make_client, monkeypatch):
        _registry(monkeypatch, [FakeProvider("a", health=ProviderHealth(healthy=False))])

        body = (await make_client(llm_module.router).get("/api/llm/status")).json()

        assert body["connected"] is False
        assert body["providers"][0]["error"] == "unhealthy"
        assert "Подключиться к LLM не удалось" in body["message"]

    async def test_no_llm_svc_provider(self, make_client, monkeypatch):
        _registry(monkeypatch, [FakeProvider("a", kind="openai")])

        assert (await make_client(llm_module.router).get("/api/llm/status")).json()["use_llm_svc"] is False

    async def test_secrets_status_is_included_without_values(self, make_client, monkeypatch):
        _registry(monkeypatch, [FakeProvider("a", key_set=True)])

        provider = (await make_client(llm_module.router).get("/api/llm/status")).json()["providers"][0]

        assert provider["api_key_set"] is True and "api_key" not in provider

    async def test_result_is_cached_and_health_is_not_polled_again(self, make_client, monkeypatch):
        provider = FakeProvider("a")
        _registry(monkeypatch, [provider])
        client = make_client(llm_module.router)

        first = await client.get("/api/llm/status")
        second = await client.get("/api/llm/status")

        assert first.json() == second.json()
        assert provider.calls == 1

    async def test_expired_cache_is_refreshed(self, make_client, monkeypatch):
        provider = FakeProvider("a")
        _registry(monkeypatch, [provider])
        monkeypatch.setattr(llm_module, "_LLM_STATUS_CACHE_SECONDS", 0)
        client = make_client(llm_module.router)

        await client.get("/api/llm/status")
        await client.get("/api/llm/status")

        assert provider.calls == 2

    async def test_registry_failure_is_reported_in_the_body_and_not_cached(self, make_client, monkeypatch):
        async def boom():
            raise RuntimeError("config broken")

        monkeypatch.setattr(llm_module, "get_registry", boom)
        client = make_client(llm_module.router)

        first = (await client.get("/api/llm/status")).json()
        _registry(monkeypatch, [FakeProvider("a")])
        second = (await client.get("/api/llm/status")).json()

        assert first["connected"] is False and "config broken" in first["message"]
        assert second["connected"] is True

    def test_request_counter_is_traced(self, monkeypatch):
        monkeypatch.setattr(llm_module, "_STATUS_COUNT", 0)
        monkeypatch.setattr(llm_module, "_STATUS_LAST_TS", 0.0)

        llm_module._trace_llm_status_request()
        llm_module._trace_llm_status_request()

        assert llm_module._STATUS_COUNT == 2


class TestModelComparison:
    URL = "/api/model-comparison/models"

    @pytest.fixture
    def store(self, monkeypatch):
        state = SimpleNamespace(models=["a/x"], saved=[])
        monkeypatch.setattr(comparison_module, "get_model_comparison_models", lambda: state.models)

        def save(models):
            state.saved.append(models)
            state.models = list(models)
            return list(models)

        monkeypatch.setattr(comparison_module, "set_model_comparison_models", save)
        return state

    async def test_read(self, make_client, store):
        assert (await make_client(comparison_module.router).get(self.URL)).json() == {"models": ["a/x"], "success": True}

    async def test_save(self, make_client, store):
        response = await make_client(comparison_module.router).post(self.URL, json={"models": ["a/x", "b/y"]})

        body = response.json()
        assert body["models"] == ["a/x", "b/y"] and body["success"] is True
        assert body["message"].endswith("a/x, b/y")
        assert store.saved == [["a/x", "b/y"]]

    @pytest.mark.parametrize(("models", "status"), [([], 400), (["1", "2", "3", "4", "5"], 400), (["1", "2", "3", "4"], 200), (["1"], 200)])
    async def test_limits(self, make_client, store, models, status):
        response = await make_client(comparison_module.router).post(self.URL, json={"models": models})

        assert response.status_code == status

    async def test_validates_the_payload(self, make_client, store):
        assert (await make_client(comparison_module.router).post(self.URL, json={"models": "x"})).status_code == 422
        assert store.saved == []


class TestSystem:
    async def test_root(self, make_client):
        body = (await make_client(system_module.router, user=None).get("/")).json()

        assert body == {"message": "astrachat Web API", "status": "active", "version": "1.0.0"}

    async def test_socket_test_lists_origins(self, make_client):
        body = (await make_client(system_module.router).get("/socket-test")).json()

        assert body["socketio_status"] == "active" and body["endpoint"] == "/socket.io/"
        assert len(body["cors_origins"]) == 4

    @pytest.fixture
    def healthy_env(self, monkeypatch):
        monkeypatch.setattr(system_module, "get_model_info", lambda: {"loaded": True})
        monkeypatch.setattr(system_module, "check_stt_available", lambda: True)
        monkeypatch.setattr(system_module, "rag_client", None)

    async def test_health(self, make_client, healthy_env):
        body = (await make_client(system_module.router, user=None).get("/health")).json()

        assert body["status"] == "healthy"
        assert body["services"]["llm_model"] is True
        assert body["services"]["stt_whisperx"] is True
        assert body["services"]["rag_service"] is False
        assert body["services"]["mcp"]["enabled"] is False

    async def test_health_with_a_reachable_rag_service(self, make_client, healthy_env, monkeypatch):
        calls = []

        class Rag:
            async def health(self):
                return {"status": "ok"}

        async def reconcile(client, health, emb, rerank):
            calls.append(health)

        import backend.settings.rag_client as rag_client_module

        monkeypatch.setattr(system_module, "rag_client", Rag())
        monkeypatch.setattr(rag_client_module, "reconcile_rag_models_provider", reconcile)

        body = (await make_client(system_module.router, user=None).get("/health")).json()

        assert body["services"]["rag_service"] is True
        assert calls == [{"status": "ok"}]

    async def test_health_survives_a_broken_rag_service(self, make_client, healthy_env, monkeypatch):
        class Rag:
            async def health(self):
                raise RuntimeError("rag down")

        monkeypatch.setattr(system_module, "rag_client", Rag())

        body = (await make_client(system_module.router, user=None).get("/health")).json()

        assert body["status"] == "healthy" and body["services"]["rag_service"] is False

    async def test_health_reports_unhealthy_without_the_error_text(self, make_client, monkeypatch):
        def boom():
            raise RuntimeError("model registry at /srv/secret broke")

        monkeypatch.setattr(system_module, "get_model_info", boom)

        body = (await make_client(system_module.router, user=None).get("/health")).json()

        assert body["status"] == "unhealthy"
        assert "/srv/secret" not in body["error"]  # CWE-209
