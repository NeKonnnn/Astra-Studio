"""routes/rag: каталог RAG-моделей (шлюзы, проба эмбеддера, кэш), выбор модели сущности, /models и /models/current."""

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi import HTTPException

import backend.app_state as state
from backend.routes import rag as module
from backend.tests.api.conftest import ALICE
from backend.tests.api.cov_b2.test_documents_extract import RecLogger

pytestmark = pytest.mark.unit


@pytest.fixture
def rec_logger(monkeypatch):
    log = RecLogger()
    monkeypatch.setattr(module, "logger", log)
    return log


def _provider(pid="PHX", base_url="http://gw", models=(), extra=None, key="k", verify=None):
    async def list_models():
        if isinstance(models, Exception):
            raise models
        return [SimpleNamespace(model_id=m) for m in models]

    def get_api_key():
        if isinstance(key, Exception):
            raise key
        return key

    prov = SimpleNamespace(
        id=pid,
        base_url=base_url,
        config=SimpleNamespace(extra=extra if extra is not None else {"rag_gateway": True}),
        list_models=list_models,
        get_api_key=get_api_key,
    )
    if verify is not None:
        prov._http_verify = verify
    return prov


class FakeRegistry:
    def __init__(self, providers):
        self.providers = {p.id: p for p in providers}

    def all(self):
        return list(self.providers.values())

    def contains(self, pid):
        return pid in self.providers

    def get(self, pid):
        return self.providers[pid]


@pytest.fixture
def registry(monkeypatch):
    """``registry(*providers)`` подменяет реестр провайдеров, который rag берёт лениво."""
    import backend.llm_providers.registry as registry_module

    def install(*providers):
        reg = FakeRegistry(providers)

        async def get_registry():
            return reg

        monkeypatch.setattr(registry_module, "get_registry", get_registry)
        return reg

    return install


# ---------- каталог шлюзов ----------


class TestGatewayCatalog:
    async def test_only_declared_gateways_become_entries(self, registry):
        registry(
            _provider("PHOENIX"),
            _provider("CORSUR", extra={"rag_gateway": 1}),
            _provider("chat", extra={}),
            _provider("", extra={"rag_gateway": True}),
            SimpleNamespace(id="noconf", config=None),
        )

        entries = await module._rag_gateway_catalog()

        assert entries == [
            {"provider_id": "PHOENIX", "path_prefix": "phoenix", "source": "phoenix", "description": "Модель PHOENIX", "rag_only": True},
            {"provider_id": "CORSUR", "path_prefix": "corsur", "source": "corsur", "description": "Модель CORSUR", "rag_only": True},
        ]

    async def test_empty_catalog_is_reported(self, registry, rec_logger):
        registry(_provider("chat", extra={}))

        assert await module._rag_gateway_catalog() == []
        assert rec_logger.records[0][0] == "warning" and "rag_gateway" in rec_logger.records[0][1]


class TestDanglingDefault:
    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        monkeypatch.setattr(module, "_dangling_default_warned", set())
        monkeypatch.setattr(state, "rag_embedding_model_path", "", raising=False)
        monkeypatch.setattr(state, "rag_reranker_model_path", "", raising=False)

    def test_empty_catalog_is_ignored(self, rec_logger, monkeypatch):
        monkeypatch.setattr(state, "rag_embedding_model_path", "ghost/emb")

        module._warn_if_dangling_cluster_default([])

        assert rec_logger.records == []

    def test_unknown_provider_prefix_is_reported_once(self, rec_logger, monkeypatch):
        monkeypatch.setattr(state, "rag_embedding_model_path", "ghost/emb")
        monkeypatch.setattr(state, "rag_reranker_model_path", "Ghost2/rr")
        rows = [{"source": "phoenix"}]

        module._warn_if_dangling_cluster_default(rows)
        module._warn_if_dangling_cluster_default(rows)

        assert [r[0] for r in rec_logger.records] == ["warning", "warning"]
        assert rec_logger.records[0][2][1:3] == ("ghost/emb", "ghost")
        assert rec_logger.records[1][2][1:3] == ("Ghost2/rr", "ghost2")

    @pytest.mark.parametrize("path", ["phoenix/emb", "local/bge", "native/x", "bare-name", ""])
    def test_known_or_local_paths_are_fine(self, rec_logger, monkeypatch, path):
        monkeypatch.setattr(state, "rag_embedding_model_path", path)

        module._warn_if_dangling_cluster_default([{"source": "phoenix"}])

        assert rec_logger.records == []


# ---------- настройки окружения ----------


class TestEnvironmentSwitches:
    def test_cache_seconds_from_env(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_CACHE_SECONDS", "30")
        assert module._rag_models_cache_seconds() == 30
        monkeypatch.setenv("RAG_MODELS_CACHE_SECONDS", "0")
        assert module._rag_models_cache_seconds() == 1

    def test_cache_seconds_fall_back_to_settings_then_default(self, monkeypatch):
        import backend.settings as settings_pkg

        monkeypatch.setenv("RAG_MODELS_CACHE_SECONDS", "abc")
        monkeypatch.setattr(
            settings_pkg,
            "get_settings",
            lambda: SimpleNamespace(model_health=SimpleNamespace(models_available_cache_seconds=77)),
        )
        assert module._rag_models_cache_seconds() == 77

        def broken():
            raise RuntimeError("no settings")

        monkeypatch.setattr(settings_pkg, "get_settings", broken)
        monkeypatch.delenv("RAG_MODELS_CACHE_SECONDS")
        assert module._rag_models_cache_seconds() == 60

    def test_invalidation_resets_the_cache(self, monkeypatch):
        monkeypatch.setattr(module, "_rag_models_catalog_cache", [{"x": 1}])
        monkeypatch.setattr(module, "_rag_models_catalog_cache_ts", 123.0)

        module._invalidate_rag_models_catalog_cache()

        assert module._rag_models_catalog_cache is None and module._rag_models_catalog_cache_ts == 0.0

    @pytest.mark.parametrize(("value", "expected"), [("1", True), ("", True), ("yes", True), ("0", False), ("False", False), (" no ", False)])
    def test_probe_switch(self, monkeypatch, value, expected):
        monkeypatch.setenv("RAG_MODEL_PROBE", value)
        assert module._probe_enabled() is expected

    @pytest.mark.parametrize(("value", "expected"), [("1", True), ("true", True), ("0", False), ("No", False)])
    def test_hide_switch(self, monkeypatch, value, expected):
        monkeypatch.setenv("RAG_MODEL_PROBE_HIDE_UNAVAILABLE", value)
        assert module._hide_unavailable() is expected


# ---------- проба эмбеддера ----------

VECTOR = {"data": [{"embedding": [0.1] * 16}]}


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setattr(module, "_embedding_probe_cache", {})
    monkeypatch.setattr(module, "_PROBE_TTL_SECONDS", 600.0)
    monkeypatch.setattr(module, "_PROBE_TTL_UNKNOWN", 120.0)


@pytest.mark.usefixtures("probe")
class TestProbeEmbeddingModel:
    async def test_direct_embeddings_route_works(self):
        with respx.mock(assert_all_called=False) as mock:
            emb = mock.post("http://gw/v1/embeddings").respond(200, json=VECTOR)
            chat = mock.post("http://gw/v1/chat/completions").respond(200, json=VECTOR)

            result = await module._probe_embedding_model(_provider(), "bge-m3")

        assert result == (module._PROBE_OK, 16, "")
        assert emb.called and not chat.called
        request = emb.calls[0].request
        assert request.headers["authorization"] == "Bearer k" and request.headers["x-api-key"] == "k"
        assert request.read() == b'{"model":"bge-m3","input":["ping"]}'

    async def test_missing_route_falls_back_to_the_passthrough_tunnel(self):
        with respx.mock() as mock:
            mock.post("http://gw/v1/embeddings").respond(404, text="no route")
            tunnel = mock.post("http://gw/v1/chat/completions").respond(200, json=VECTOR)

            result = await module._probe_embedding_model(_provider(), "FRIDA")

        assert result == (module._PROBE_OK, 16, "")
        request = tunnel.calls[0].request
        assert request.headers["x-bf-passthrough-extra-params"] == "true"
        assert b'"texts":["ping"]' in request.read() and b'"input"' not in request.read()

    async def test_rejection_stays_a_rejection_when_the_tunnel_is_only_unhealthy(self):
        with respx.mock() as mock:
            mock.post("http://gw/v1/embeddings").respond(400, text="Invalid model name")
            mock.post("http://gw/v1/chat/completions").respond(503, text="busy")

            status, dim, reason = await module._probe_embedding_model(_provider(), "QWEN3 EMBEDDING 8B")

        assert status == module._PROBE_BAD and dim == 0 and reason.startswith("HTTP 400: Invalid model name")

    async def test_both_routes_unhealthy_keeps_the_model_visible(self):
        with respx.mock() as mock:
            mock.post("http://gw/v1/embeddings").respond(404, text="a")
            mock.post("http://gw/v1/chat/completions").respond(500, text="b")

            status, _, reason = await module._probe_embedding_model(_provider(), "m")

        assert status == module._PROBE_UNKNOWN
        assert reason == "/v1/embeddings: HTTP 404: a; passthrough: HTTP 500: b"

    async def test_both_routes_reject(self):
        with respx.mock() as mock:
            mock.post("http://gw/v1/embeddings").respond(400, text="a")
            mock.post("http://gw/v1/chat/completions").respond(422, text="b")

            status, _, reason = await module._probe_embedding_model(_provider(), "m")

        assert status == module._PROBE_BAD and reason == "/v1/embeddings: HTTP 400: a; passthrough: HTTP 422: b"

    async def test_chat_model_answer_without_a_vector_is_bad(self):
        with respx.mock() as mock:
            mock.post("http://gw/v1/embeddings").respond(200, json={"choices": [{"message": {"content": "hi"}}]})
            mock.post("http://gw/v1/chat/completions").respond(200, json={"choices": [{"message": {"content": "hi"}}]})

            status, _, reason = await module._probe_embedding_model(_provider(), "llm")

        assert status == module._PROBE_BAD and "не эмбеддер" in reason

    async def test_network_failure_is_unknown(self):
        with respx.mock() as mock:
            mock.post("http://gw/v1/embeddings").mock(side_effect=httpx.ConnectError("refused"))

            status, dim, reason = await module._probe_embedding_model(_provider(), "m")

        assert (status, dim) == (module._PROBE_UNKNOWN, 0) and reason == "ConnectError: refused"

    async def test_no_base_url_means_nothing_to_check(self):
        assert await module._probe_embedding_model(_provider(base_url=""), "m") == (module._PROBE_UNKNOWN, 0, "base_url не задан")

    async def test_missing_key_sends_no_auth_headers(self):
        with respx.mock() as mock:
            emb = mock.post("http://gw/v1/embeddings").respond(200, json=VECTOR)

            await module._probe_embedding_model(_provider(key=RuntimeError("no key")), "m")
            await module._probe_embedding_model(_provider(pid="OTHER", key=""), "m")

        for call in emb.calls:
            assert "authorization" not in call.request.headers and "x-api-key" not in call.request.headers

    async def test_certificate_verification_follows_the_provider(self, monkeypatch):
        seen = []
        real = httpx.AsyncClient

        class Spy(real):
            def __init__(self, *a, **kw):
                seen.append(kw.get("verify"))
                super().__init__(*a, **kw)

        monkeypatch.setattr(module.httpx, "AsyncClient", Spy)
        with respx.mock() as mock:
            mock.post("http://gw/v1/embeddings").respond(200, json=VECTOR)

            await module._probe_embedding_model(_provider(pid="A", verify=lambda: "/ca.pem"), "m")
            await module._probe_embedding_model(_provider(pid="B"), "m")

        assert seen == ["/ca.pem", True]

    async def test_result_is_cached(self):
        with respx.mock() as mock:
            route = mock.post("http://gw/v1/embeddings").respond(200, json=VECTOR)

            first = await module._probe_embedding_model(_provider(), "m")
            second = await module._probe_embedding_model(_provider(), "m")

        assert first == second and route.call_count == 1

    async def test_unknown_results_expire_sooner(self, monkeypatch):
        now = time.monotonic()
        key = ("PHX", "m")
        module._embedding_probe_cache[key] = (module._PROBE_UNKNOWN, 0, "old", now - 200)
        with respx.mock() as mock:
            mock.post("http://gw/v1/embeddings").respond(200, json=VECTOR)

            result = await module._probe_embedding_model(_provider(), "m")

        assert result[0] == module._PROBE_OK

    async def test_fresh_unknown_and_old_ok_results_are_reused(self):
        now = time.monotonic()
        module._embedding_probe_cache[("PHX", "u")] = (module._PROBE_UNKNOWN, 0, "blink", now - 10)
        module._embedding_probe_cache[("PHX", "ok")] = (module._PROBE_OK, 8, "", now - 500)

        assert await module._probe_embedding_model(_provider(), "u") == (module._PROBE_UNKNOWN, 0, "blink")
        assert await module._probe_embedding_model(_provider(), "ok") == (module._PROBE_OK, 8, "")


# ---------- _annotate_availability ----------


class TestAnnotateAvailability:
    def _rows(self):
        return [
            {"path": "p/ok", "name": "ok", "kind": "embedding"},
            {"path": "p/bad", "name": "bad", "kind": "embedding"},
            {"path": "p/unk", "name": "unk", "kind": "embedding"},
            {"path": "p/rr", "name": "rr", "kind": "reranker"},
        ]

    @pytest.fixture
    def probes(self, monkeypatch):
        results = {
            "ok": (module._PROBE_OK, 1024, ""),
            "bad": (module._PROBE_BAD, 0, "HTTP 400: nope"),
            "unk": (module._PROBE_UNKNOWN, 0, "timeout"),
        }

        async def fake(provider, name):
            return results[name]

        monkeypatch.setattr(module, "_probe_embedding_model", fake)

    async def test_bad_models_are_hidden_and_unknown_ones_stay(self, probes, rec_logger, monkeypatch):
        monkeypatch.delenv("RAG_MODEL_PROBE_HIDE_UNAVAILABLE", raising=False)
        rows = self._rows()

        out = await module._annotate_availability(object(), rows, "phoenix")

        assert [r["name"] for r in out] == ["ok", "unk", "rr"]
        assert rows[0]["dim"] == 1024 and rows[0]["probe_status"] == "ok" and "probe_note" not in rows[0]
        assert rows[2]["probe_status"] == "unknown" and rows[2]["available"] is True and rows[2]["probe_note"] == "timeout"
        levels = [r[0] for r in rec_logger.records]
        assert levels == ["warning", "warning"]
        assert "скрыты" in rec_logger.records[0][1] and "оставлены" in rec_logger.records[1][1]

    async def test_healthy_gateway_logs_nothing(self, rec_logger, monkeypatch):
        async def healthy(provider, name):
            return module._PROBE_OK, 8, ""

        monkeypatch.setattr(module, "_probe_embedding_model", healthy)
        rows = [{"path": "p/ok", "name": "ok", "kind": "embedding"}]

        out = await module._annotate_availability(object(), rows, "phoenix")

        assert out == rows and rows[0]["available"] is True and rec_logger.records == []

    async def test_hiding_can_be_turned_off(self, probes, monkeypatch):
        monkeypatch.setenv("RAG_MODEL_PROBE_HIDE_UNAVAILABLE", "0")
        rows = self._rows()

        out = await module._annotate_availability(object(), rows, "phoenix")

        assert out is rows and rows[1]["available"] is False and rows[1]["probe_note"] == "HTTP 400: nope"

    async def test_probe_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("RAG_MODEL_PROBE", "0")
        rows = self._rows()

        assert await module._annotate_availability(object(), rows, "phoenix") is rows
        assert "probe_status" not in rows[0]

    async def test_rows_without_embedders_are_untouched(self):
        rows = [{"path": "p/rr", "name": "rr", "kind": "reranker"}]

        assert await module._annotate_availability(object(), rows, "phoenix") is rows


# ---------- _openai_compat_rag_models / _external_rag_models ----------


class TestCompatCatalog:
    @pytest.fixture(autouse=True)
    def _no_probe(self, monkeypatch):
        monkeypatch.setenv("RAG_MODEL_PROBE", "0")

    async def test_unknown_provider_gives_nothing(self, registry, rec_logger):
        registry()

        out = await module._openai_compat_rag_models(provider_id="X", path_prefix="x", source="x", description="d")

        assert out == [] and rec_logger.records[0][0] == "warning"

    async def test_rag_only_gateway_keeps_unknown_names_as_embedders(self, registry):
        registry(_provider("PHX", models=["bge-reranker", "FRIDA", "", "vllm/qwen"]))

        out = await module._openai_compat_rag_models(
            provider_id="PHX", path_prefix="phx", source="phx", description="Модель PHX", rag_only=True
        )

        assert [(r["path"], r["kind"]) for r in out] == [
            ("phx/bge-reranker", "reranker"),
            ("phx/FRIDA", "embedding"),
            ("phx/vllm/qwen", "embedding"),
        ]
        assert out[0] == {
            "path": "phx/bge-reranker",
            "name": "bge-reranker",
            "display_name": "bge-reranker",
            "source": "phx",
            "kind": "reranker",
            "description": "Модель PHX",
            "available": True,
        }

    async def test_chat_models_are_skipped_on_mixed_gateways(self, registry):
        registry(_provider("PHX", models=["qwen3-8b", "bge-m3"]))

        out = await module._openai_compat_rag_models(provider_id="PHX", path_prefix="phx", source="phx", description="d")

        assert [r["name"] for r in out] == ["bge-m3"]

    async def test_type_filter(self, registry):
        registry(_provider("PHX", models=["bge-m3", "bge-reranker"]))

        out = await module._openai_compat_rag_models(
            provider_id="PHX", path_prefix="phx", source="phx", description="d", model_type="reranker", rag_only=True
        )

        assert [r["name"] for r in out] == ["bge-reranker"]

    async def test_gateway_without_models(self, registry):
        registry(_provider("PHX", models=[]))

        assert await module._openai_compat_rag_models(provider_id="PHX", path_prefix="phx", source="phx", description="d") == []


class TestExternalCatalog:
    async def test_gateways_are_merged_and_a_broken_one_is_skipped(self, monkeypatch):
        async def catalog():
            return [
                {"provider_id": "A", "path_prefix": "a", "source": "a", "description": "da", "rag_only": True},
                {"provider_id": "B", "path_prefix": "b", "source": "b", "description": "db"},
                {"provider_id": "C", "path_prefix": "c", "source": "c", "description": "dc"},
            ]

        calls = []

        async def compat(**kw):
            calls.append(kw)
            if kw["provider_id"] == "B":
                raise RuntimeError("gateway down")
            return [{"path": kw["path_prefix"] + "/m", "kind": "embedding"}]

        monkeypatch.setattr(module, "_rag_gateway_catalog", catalog)
        monkeypatch.setattr(module, "_openai_compat_rag_models", compat)

        rows = await module._external_rag_models("embedding")

        assert [r["path"] for r in rows] == ["a/m", "c/m"]
        assert [(c["provider_id"], c["model_type"], c["rag_only"]) for c in calls] == [("A", "embedding", True), ("B", "embedding", False), ("C", "embedding", False)]


@pytest.fixture
def clean_cache(monkeypatch):
    monkeypatch.setattr(module, "_rag_models_catalog_cache", None)
    monkeypatch.setattr(module, "_rag_models_catalog_cache_ts", 0.0)
    monkeypatch.setattr(module, "_rag_models_catalog_cache_lock", asyncio.Lock())
    monkeypatch.setattr(module, "_rag_models_cache_seconds", lambda: 60)


@pytest.mark.usefixtures("clean_cache")
class TestCachedCatalog:
    ROWS = [{"path": "a/e", "kind": "embedding"}, {"path": "a/r", "kind": "reranker"}, {"path": "a/x"}]

    @pytest.fixture
    def source(self, monkeypatch):
        calls = []

        async def external(model_type=None):
            calls.append(model_type)
            await asyncio.sleep(0)
            return [dict(r) for r in self.ROWS]

        monkeypatch.setattr(module, "_external_rag_models", external)
        return calls

    async def test_full_catalog_is_cached_and_filtered_per_request(self, source):
        everything = await module._external_rag_models_cached()
        embeddings = await module._external_rag_models_cached("embedding")
        rerankers = await module._external_rag_models_cached("reranker")

        assert source == [None]
        assert len(everything) == 3
        assert [r["path"] for r in embeddings] == ["a/e", "a/x"]
        assert [r["path"] for r in rerankers] == ["a/r"]

    async def test_cache_expires(self, source, monkeypatch):
        await module._external_rag_models_cached()
        monkeypatch.setattr(module, "_rag_models_catalog_cache_ts", time.monotonic() - 100)

        await module._external_rag_models_cached()

        assert source == [None, None]

    async def test_empty_result_is_not_cached(self, monkeypatch):
        calls = []

        async def external(model_type=None):
            calls.append(model_type)
            return []

        monkeypatch.setattr(module, "_external_rag_models", external)

        assert await module._external_rag_models_cached() == []
        assert await module._external_rag_models_cached() == []
        assert calls == [None, None] and module._rag_models_catalog_cache is None

    async def test_concurrent_requests_share_one_refresh(self, source):
        results = await asyncio.gather(*(module._external_rag_models_cached() for _ in range(3)))

        assert source == [None] and all(len(r) == 3 for r in results)


# ---------- GET /api/rag/models, /api/rag/models/current ----------


class Store:
    def __init__(self):
        self.rows = {}
        self.error = None

    async def get(self, scope, entity_id):
        if self.error:
            raise self.error
        return dict(self.rows.get((scope, entity_id), {}))


@pytest.fixture
def env(monkeypatch, services):
    store = Store()
    monkeypatch.setattr(module, "get_entity_rag_settings", store.get)
    monkeypatch.setattr(module, "_warn_if_dangling_cluster_default", lambda rows: None)
    return SimpleNamespace(store=store, mp=monkeypatch)


def _client(make_client, user=ALICE):
    return make_client(module.router, user=user)


class TestListModelsEndpoint:
    async def test_requires_login(self, make_client, env):
        assert (await _client(make_client, None).get("/api/rag/models")).status_code in (401, 403)

    async def test_models_are_grouped_by_kind_and_current_is_overlaid(self, make_client, env):
        seen = []

        async def cached(model_type):
            seen.append(model_type)
            return [{"path": "p/e", "kind": "embedding"}, {"path": "p/r", "kind": "reranker"}, {"path": "p/n"}]

        env.mp.setattr(module, "_external_rag_models_cached", cached)
        env.store.rows[("agent", "7")] = {"rag_embedding_model_path": "p/e"}

        body = (await _client(make_client).get("/api/rag/models", params={"type": "embedding", "scope": "agent", "agent_id": 7})).json()

        assert seen == ["embedding"]
        assert [m["path"] for m in body["models"]["embedding"]] == ["p/e", "p/n"]
        assert [m["path"] for m in body["models"]["reranker"]] == ["p/r"]
        assert body["current"]["embedding"]["path"] == "p/e" and body["current"]["embedding"]["name"] == "e"
        assert body["current"]["embedding"]["source"] == "p"

    async def test_catalog_failure_gives_an_empty_catalog(self, make_client, env):
        async def broken(model_type):
            raise RuntimeError("gateway down")

        env.mp.setattr(module, "_external_rag_models_cached", broken)

        response = await _client(make_client).get("/api/rag/models")

        assert response.status_code == 200 and response.json()["models"] == {"embedding": [], "reranker": []}

    async def test_unknown_kind_gets_its_own_bucket(self, make_client, env):
        async def cached(model_type):
            return [{"path": "p/x", "kind": "colbert"}]

        env.mp.setattr(module, "_external_rag_models_cached", cached)

        body = (await _client(make_client).get("/api/rag/models")).json()

        assert body["models"]["colbert"][0]["path"] == "p/x"

    async def test_settings_failure_is_a_502(self, make_client, env):
        async def cached(model_type):
            return []

        env.mp.setattr(module, "_external_rag_models_cached", cached)
        env.store.error = RuntimeError("db down")

        response = await _client(make_client).get("/api/rag/models")

        assert response.status_code == 502 and response.json()["detail"] == "Ошибка при обращении к внешнему сервису"


class TestCurrentModelEndpoint:
    class Models:
        def __init__(self, data=None, error=None):
            self.data, self.error = data, error

        async def get_current(self):
            if self.error:
                raise self.error
            return self.data

    async def test_cluster_default_with_the_entity_override(self, make_client, env):
        env.mp.setattr(module, "rag_models_client", self.Models({"current": {"embedding": {"path": "c/e"}, "reranker": {"path": "c/r"}}}))
        env.store.rows[("project", "p1")] = {"rag_reranker_model_path": "phoenix/rr"}

        body = (await _client(make_client).get("/api/rag/models/current", params={"project_id": "p1"})).json()

        assert body["current"]["embedding"] == {"path": "c/e"}
        assert body["current"]["reranker"]["path"] == "phoenix/rr" and body["current"]["reranker"]["source"] == "phoenix"
        assert body["cluster_default"]["reranker"] == {"path": "c/r"}

    async def test_unavailable_models_service_still_answers(self, make_client, env):
        env.mp.setattr(module, "rag_models_client", self.Models(error=RuntimeError("down")))
        env.store.rows[("project", "p1")] = {"rag_embedding_model_path": "phoenix/e"}

        body = (await _client(make_client).get("/api/rag/models/current", params={"project_id": "p1"})).json()

        assert body["current"]["embedding"]["name"] == "e" and body["cluster_default"] == {}

    @pytest.mark.parametrize("client", [None, "not-a-dict-client"])
    async def test_without_models_service_or_with_garbage(self, make_client, env, client):
        env.mp.setattr(module, "rag_models_client", self.Models(["garbage"]) if client else None)

        response = await _client(make_client).get("/api/rag/models/current")

        assert response.status_code == 200 and response.json()["current"] == {}

    async def test_settings_failure_is_a_502(self, make_client, env):
        env.mp.setattr(module, "rag_models_client", None)
        env.store.error = RuntimeError("db down")

        assert (await _client(make_client).get("/api/rag/models/current")).status_code == 502


class TestClusterEmbeddingDim:
    async def test_without_a_client(self, monkeypatch):
        monkeypatch.setattr(module, "rag_models_client", None)

        assert await module._current_cluster_embedding_dim() is None

    @pytest.mark.parametrize(
        ("health", "expected"),
        [({"embedding_dim": "1024"}, 1024), ({"embedding_dim": 0}, None), ({}, None), (["x"], None), (RuntimeError("down"), None)],
    )
    async def test_health_payloads(self, monkeypatch, health, expected):
        async def get_health():
            if isinstance(health, Exception):
                raise health
            return health

        monkeypatch.setattr(module, "rag_models_client", SimpleNamespace(health=get_health))

        assert await module._current_cluster_embedding_dim() == expected


# ---------- выбор внешней модели ----------


class SelectEnv:
    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        self.saved, self.bumped, self.reindex = [], [], []
        self.catalog = []
        self.catalog_error = None
        self.permission = "owner"

        async def save(scope, entity_id, updates, user_id):
            self.saved.append((scope, entity_id, updates, user_id))
            return {"rag_chunk_size": 500, **updates}

        async def external(model_type=None):
            if self.catalog_error:
                raise self.catalog_error
            return self.catalog

        async def background(user_id, params, scope, **kw):
            self.reindex.append((user_id, params, scope, kw))

        async def permission(scope, entity_id, user_id, *, is_admin=False):
            return self.permission

        monkeypatch.setattr(module, "rag_client", object())
        monkeypatch.setattr(module, "save_entity_rag_settings", save)
        monkeypatch.setattr(module, "_external_rag_models", external)
        monkeypatch.setattr(module, "_run_background_reindex_for_user", background)
        monkeypatch.setattr(module, "_entity_permission", permission)
        monkeypatch.setattr(module, "bump_rag_semantic_cache", lambda: self.bumped.append(True))
        monkeypatch.setattr(module, "chunk_params_from_rag_settings", lambda merged: {"chunk_size": merged["rag_chunk_size"]})
        monkeypatch.delenv("RAG_REINDEX_ON_MODEL_CHANGE", raising=False)


@pytest.fixture
def sel(monkeypatch, services):
    services(module, "rag", "rag_models")
    return SelectEnv(monkeypatch)


class TestSelectExternalModel:
    async def _select(self, model_type="embedding", path="phoenix/bge", **kw):
        return await module._select_external_rag_model(
            model_type, path, "alice", path_prefix="phoenix", source_label="Phoenix", scope="agent", entity_id="7", project_id=None, agent_id=7, **kw
        )

    async def test_without_rag_service(self, sel):
        sel.mp.setattr(module, "rag_client", None)

        with pytest.raises(HTTPException) as exc:
            await self._select()

        assert exc.value.status_code == 503

    @pytest.mark.parametrize(("path", "fragment"), [("corsur/bge", "Ожидался путь phoenix/"), ("phoenix/", "Пустой id модели Phoenix"), ("phoenix/  ", "Пустой id")])
    async def test_malformed_paths(self, sel, path, fragment):
        with pytest.raises(HTTPException) as exc:
            await self._select(path=path)

        assert exc.value.status_code == 400 and fragment in exc.value.detail

    async def test_catalog_failure_is_a_502(self, sel):
        sel.catalog_error = RuntimeError("gateway down")

        with pytest.raises(HTTPException) as exc:
            await self._select()

        assert exc.value.status_code == 502 and exc.value.detail == "Ошибка при обращении к внешнему сервису"

    async def test_model_missing_from_the_catalog(self, sel):
        sel.catalog = [{"path": "phoenix/other"}, {"path": "corsur/bge"}, None]

        with pytest.raises(HTTPException) as exc:
            await self._select()

        assert exc.value.status_code == 400 and "не найдена в каталоге" in exc.value.detail
        assert sel.saved == []

    async def test_embedding_is_saved_and_starts_a_scoped_reindex(self, sel):
        sel.catalog = [{"path": "phoenix/bge"}]

        result = await self._select()
        await asyncio.sleep(0)

        assert result == {
            "success": True,
            "model_type": "embedding",
            "model_path": "phoenix/bge",
            "cluster_changed": False,
            "message": "Модель сохранена в настройках этого агента или проекта",
            "reindexed": True,
        }
        assert sel.saved == [("agent", "7", {"rag_embedding_model_path": "phoenix/bge"}, "alice")]
        assert sel.reindex == [("alice", {"chunk_size": 500}, "agent", {"project_id": None, "agent_id": 7})]
        assert sel.bumped == [True]

    async def test_empty_catalog_accepts_the_model(self, sel):
        sel.catalog = []

        assert (await self._select())["success"] is True

    async def test_reindex_can_be_disabled(self, sel):
        sel.mp.setenv("RAG_REINDEX_ON_MODEL_CHANGE", "off")

        result = await self._select()
        await asyncio.sleep(0)

        assert result["reindexed"] is False and sel.reindex == []

    async def test_reranker_never_reindexes(self, sel):
        result = await self._select("reranker", "phoenix/rr")
        await asyncio.sleep(0)

        assert result["reindexed"] is False and sel.reindex == []
        assert sel.saved == [("agent", "7", {"rag_reranker_model_path": "phoenix/rr"}, "alice")]
        assert sel.bumped == [True]

    async def test_phoenix_wrapper_forwards_to_the_generic_selector(self, sel):
        result = await module._select_phoenix_rag_model("reranker", "phoenix/rr", "alice", "project")

        assert result["model_path"] == "phoenix/rr"
        assert sel.saved == [("project", None, {"rag_reranker_model_path": "phoenix/rr"}, "alice")]


# ---------- POST /api/rag/models/select ----------


def _select(make_client, body, user=ALICE):
    return _client(make_client, user).post("/api/rag/models/select", json=body)


class FakeModels:
    def __init__(self, paths=("local/bge",), error=None):
        self.paths, self.error = paths, error

    async def list_models(self, model_type):
        if self.error:
            raise self.error
        return {"models": {model_type: [{"path": p} for p in self.paths]}}


class TestSelectEndpoint:
    BODY = {"model_type": "embedding", "model_path": "local/bge", "agent_id": 7, "scope": "agent"}

    async def test_requires_login(self, make_client, sel):
        assert (await _select(make_client, self.BODY, user=None)).status_code in (401, 403)

    async def test_user_without_an_id(self, make_client, sel):
        response = await _select(make_client, self.BODY, user={"username": "x", "user_id": ""})

        assert response.status_code == 401

    @pytest.mark.parametrize(
        ("patch", "fragment"),
        [
            ({"model_type": "llm"}, "embedding или reranker"),
            ({"model_path": "  "}, "model_path обязателен"),
            ({"agent_id": None}, "project_id или agent_id"),
            ({"model_path": "HuggingFace/bge"}, "huggingface отключён"),
        ],
    )
    async def test_invalid_requests(self, make_client, sel, patch, fragment):
        response = await _select(make_client, {**self.BODY, **patch})

        assert response.status_code == 400 and fragment in response.json()["detail"]

    async def test_model_type_is_case_insensitive(self, make_client, sel):
        sel.mp.setattr(module, "rag_models_client", FakeModels())

        response = await _select(make_client, {**self.BODY, "model_type": " Embedding "})

        assert response.status_code == 200 and response.json()["model_type"] == "embedding"

    async def test_viewer_cannot_select(self, make_client, sel):
        sel.permission = "viewer"

        response = await _select(make_client, self.BODY)

        assert response.status_code == 403 and sel.saved == []

    async def test_gateway_model_goes_to_the_generic_selector(self, make_client, sel):
        calls = []

        async def gateways():
            return [{"path_prefix": "corsur", "provider_id": "CORSUR"}, {"path_prefix": "phoenix", "provider_id": "PHOENIX"}]

        async def external(model_type, model_path, user_id, **kw):
            calls.append((model_type, model_path, user_id, kw))
            return {"success": True, "via": "gateway"}

        sel.mp.setattr(module, "_rag_gateway_catalog", gateways)
        sel.mp.setattr(module, "_select_external_rag_model", external)

        response = await _select(make_client, {**self.BODY, "model_path": "Phoenix/bge"})

        assert response.json() == {"success": True, "via": "gateway"}
        assert calls == [
            (
                "embedding",
                "Phoenix/bge",
                "alice",
                {"path_prefix": "phoenix", "source_label": "PHOENIX", "scope": "agent", "entity_id": "7", "project_id": None, "agent_id": 7},
            )
        ]

    async def test_local_embedding_is_saved_and_reindexed(self, make_client, sel):
        async def gateways():
            return []

        sel.mp.setattr(module, "_rag_gateway_catalog", gateways)
        sel.mp.setattr(module, "rag_models_client", FakeModels())

        response = await _select(make_client, self.BODY)
        await asyncio.sleep(0)

        body = response.json()
        assert body["success"] and body["reindexed"] is True and body["cluster_changed"] is False
        assert sel.saved == [("agent", "7", {"rag_embedding_model_path": "local/bge"}, "alice")]
        assert sel.reindex[0][2] == "agent" and sel.reindex[0][3] == {"project_id": None, "agent_id": 7}

    async def test_local_reranker_is_saved_and_resets_the_cache(self, make_client, sel):
        sel.mp.setattr(module, "rag_models_client", FakeModels(paths=("local/rr",)))

        response = await _select(make_client, {**self.BODY, "model_type": "reranker", "model_path": "local/rr"})

        assert response.json()["reindexed"] is False
        assert sel.saved == [("agent", "7", {"rag_reranker_model_path": "local/rr"}, "alice")]
        assert sel.bumped == [True] and sel.reindex == []

    async def test_local_embedding_without_reindex_switch(self, make_client, sel):
        sel.mp.setattr(module, "rag_models_client", FakeModels())
        sel.mp.setenv("RAG_REINDEX_ON_MODEL_CHANGE", "0")

        response = await _select(make_client, self.BODY)

        assert response.json()["reindexed"] is False and sel.reindex == []

    async def test_models_service_disabled(self, make_client, sel, services):
        services(module, "rag")

        assert (await _select(make_client, self.BODY)).status_code == 503

    async def test_models_service_missing(self, make_client, sel):
        sel.mp.setattr(module, "rag_models_client", None)

        response = await _select(make_client, self.BODY)

        assert response.status_code == 503 and "RAG-models" in response.json()["detail"]

    async def test_unknown_local_model(self, make_client, sel):
        sel.mp.setattr(module, "rag_models_client", FakeModels(paths=("local/other",)))

        response = await _select(make_client, self.BODY)

        assert response.status_code == 400 and "не найдена в каталоге" in response.json()["detail"]
        assert sel.saved == []

    async def test_models_service_failure_is_a_502(self, make_client, sel):
        sel.mp.setattr(module, "rag_models_client", FakeModels(error=RuntimeError("models down")))

        response = await _select(make_client, self.BODY)

        assert response.status_code == 502 and response.json()["detail"] == "Ошибка при обращении к внешнему сервису"

    async def test_local_embedding_choice_resets_the_semantic_cache(self, make_client, sel):
        sel.mp.setattr(module, "rag_models_client", FakeModels())

        await _select(make_client, self.BODY)

        assert sel.bumped == [True]
