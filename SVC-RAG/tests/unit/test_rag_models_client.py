"""clients/rag_models_client: нативный клиент svc-rag-models (respx) — эмбеддинги, реранк, health, миграция размерности."""

import asyncio
import json

import httpx
import pytest
import respx

from app.clients import embed_parallel
from app.clients import rag_models_client as rmc

pytestmark = pytest.mark.unit

BASE = "http://models.test"
ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    rmc.RagModelsClient._last_ensured_dim = None
    with ROUTER:
        yield
    ROUTER.reset()
    rmc.RagModelsClient._last_ensured_dim = None


@pytest.fixture
def ensured(monkeypatch):
    import app.dependencies as dependencies

    calls = []

    async def ensure(dim):
        calls.append(dim)
        return {"embedding_dim": dim}

    monkeypatch.setattr(dependencies, "ensure_embedding_dim", ensure)
    return calls


def client(**kw):
    c = rmc.RagModelsClient(base_url=BASE + "/", timeout=kw.pop("timeout", 5))
    c.embed_batch_size = kw.pop("batch", 4)
    c.embed_concurrency = kw.pop("concurrency", 1)
    c.embedding_model, c.reranker_model = kw.pop("embedding_model", "emb"), kw.pop("reranker_model", "rr")
    c.api_key_env = kw.pop("api_key_env", "")
    return c


def echo(request, dim=8):
    body = json.loads(request.content)
    return httpx.Response(200, json={"data": [{"index": i, "embedding": [float(len(t))] * dim} for i, t in enumerate(body["input"])]})


class TestSetup:
    def test_base_url_and_defaults(self):
        c = rmc.RagModelsClient(base_url="http://x.test///", timeout=7)

        assert c.base_url == "http://x.test" and c.timeout == 7 and c.embed_batch_size >= 1 and c.embed_concurrency >= 1

    def test_headers(self, monkeypatch):
        monkeypatch.setenv("RM_KEY", " sk ")
        c = client(api_key_env="RM_KEY")

        headers = c._headers()

        assert headers["Authorization"] == "Bearer sk" and headers["X-API-Key"] == "sk" and headers["X-CEF-Caller-Service"] == "SVC-RAG"
        assert "Authorization" not in client()._headers()

    def test_caller_identity_headers_are_forwarded(self, monkeypatch):
        import app.core.cef_logger.cef_audit_context as ctx

        monkeypatch.setattr(ctx, "cef_audit_peek", lambda: (None, {"username": "alice", "user_id": "u1", "sntdom": "CORP"}))

        headers = client()._cef_identity_headers()

        assert (headers["X-CEF-Suser"], headers["X-CEF-Suid"], headers["X-CEF-Sntdom"]) == ("alice", "u1", "CORP")

    def test_identity_failures_leave_the_service_header_only(self, monkeypatch):
        import app.core.cef_logger.cef_audit_context as ctx

        def boom():
            raise RuntimeError("no context")

        monkeypatch.setattr(ctx, "cef_audit_peek", boom)

        assert client()._cef_identity_headers() == {"X-CEF-Caller-Service": "SVC-RAG"}


class TestParse:
    P = staticmethod(rmc.RagModelsClient._parse_embeddings)

    def test_sorted_by_index(self):
        assert self.P({"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}, 2) == [[1.0], [2.0]]

    @pytest.mark.parametrize("data", [{}, {"data": []}, {"data": [{"index": 0, "embedding": [1.0]}]}, {"embeddings": [[1.0], [2.0]]}])
    def test_count_mismatch_and_foreign_shapes(self, data):
        with pytest.raises(ValueError, match="не совпадает"):
            self.P(data, 2)


class TestEmbed:
    async def test_batches_keep_the_order(self, ensured):
        route = ROUTER.post(f"{BASE}/v1/embeddings").mock(side_effect=echo)
        data = ["т" * n for n in range(1, 10)]

        vectors = await client(batch=4, concurrency=3).embed(data)

        assert [v[0] for v in vectors] == [float(len(t)) for t in data] and route.call_count == 3

    async def test_request_carries_model_and_kind(self, ensured):
        route = ROUTER.post(f"{BASE}/v1/embeddings").mock(side_effect=echo)

        await client().embed(["a"], model="other", kind="query")
        await client().embed(["a"])

        first, second = (json.loads(c.request.content) for c in route.calls)
        assert first == {"model": "other", "input": ["a"], "kind": "query"} and second == {"model": "emb", "input": ["a"]}

    async def test_empty_and_unconfigured(self, ensured):
        assert await client().embed([]) == []
        with pytest.raises(ValueError, match="embedding model не задан"):
            await client(embedding_model=None).embed(["a"])

    async def test_cluster_model_migrates_the_schema_once(self, ensured):
        ROUTER.post(f"{BASE}/v1/embeddings").mock(side_effect=lambda r: echo(r, dim=1536))
        c = client()

        await c.embed(["a"])
        await c.embed(["b"])

        assert ensured == [1536]

    async def test_explicit_models_never_touch_the_schema(self, ensured):
        ROUTER.post(f"{BASE}/v1/embeddings").mock(side_effect=lambda r: echo(r, dim=1536))

        await client().embed(["a"], model="user-model")

        assert ensured == []

    async def test_transient_503_is_retried(self, ensured):
        route = ROUTER.post(f"{BASE}/v1/embeddings").mock(side_effect=[httpx.Response(503, text="busy"), echo])

        assert await client().embed(["a"]) == [[1.0] * 8]
        assert route.call_count == 2

    async def test_connect_errors_are_retried_then_raised(self, ensured):
        route = ROUTER.post(f"{BASE}/v1/embeddings").mock(side_effect=httpx.ConnectError("down"))

        with pytest.raises(httpx.ConnectError):
            await client().embed(["a"])

        assert route.call_count == rmc._MAX_ATTEMPTS

    async def test_persistent_503_stops_after_max_attempts(self, ensured):
        route = ROUTER.post(f"{BASE}/v1/embeddings").mock(return_value=httpx.Response(503, text="busy"))

        with pytest.raises(httpx.HTTPStatusError):
            await client().embed(["a"])

        assert route.call_count == rmc._MAX_ATTEMPTS

    async def test_client_errors_are_not_retried(self, ensured):
        route = ROUTER.post(f"{BASE}/v1/embeddings").mock(return_value=httpx.Response(404, text="no route"))

        with pytest.raises(httpx.HTTPStatusError):
            await client().embed(["a"])

        assert route.call_count == 1

    async def test_timeouts_split_the_batch_and_shrink_the_limits(self, ensured):
        def respond(request):
            if len(json.loads(request.content)["input"]) > 2:
                return httpx.Response(504, text="Gateway Time-out")
            return echo(request)

        ROUTER.post(f"{BASE}/v1/embeddings").mock(side_effect=respond)
        c = client(batch=8, concurrency=4)
        data = ["т" * n for n in range(1, 9)]

        vectors = await c.embed(data)

        assert [v[0] for v in vectors] == [float(len(t)) for t in data] and c._batch_cap == 2 and c._conc_cap == 2

    async def test_hard_timeouts_become_batch_timeouts(self, ensured):
        ROUTER.post(f"{BASE}/v1/embeddings").mock(side_effect=httpx.ReadTimeout("slow"))

        with pytest.raises(embed_parallel.EmbedBatchTimeout):
            await client().embed(["a"])

    async def test_embed_single(self, ensured):
        ROUTER.post(f"{BASE}/v1/embeddings").mock(side_effect=echo)

        assert await client().embed_single("abc") == [3.0] * 8


class TestAdaptation:
    @pytest.fixture(autouse=True)
    def _flags(self, monkeypatch):
        monkeypatch.setattr(embed_parallel, "ADAPT_RECOVER_AFTER", 3)
        monkeypatch.setattr(embed_parallel, "ADAPT_ON_TIMEOUT", True)
        monkeypatch.setattr(embed_parallel, "CONC_DROP_BATCH_FLOOR", 2)

    def test_shrink_then_recover(self):
        c = client(batch=8, concurrency=4)

        c._note_batch_shrink(8)
        c._note_batch_shrink(4)
        assert (c._batch_cap, c._conc_cap) == (2, 2)

        for _ in range(3):
            c._note_embed_ok(True)
        assert c._batch_cap == 4
        for _ in range(3):
            c._note_embed_ok(True)
        assert c._batch_cap is None
        for _ in range(3):
            c._note_embed_ok(True)
        assert c._conc_cap is None and c._effective_batch_size() == 8 and c._effective_concurrency() == 4

    def test_search_sized_runs_do_not_prove_anything(self):
        c = client(batch=8)
        c._note_batch_shrink(8)

        for _ in range(10):
            c._note_embed_ok(False)

        assert c._batch_cap == 4

    def test_nothing_to_recover_without_caps(self):
        c = client()
        c._note_embed_ok(True)

        assert c._clean_runs == 0


class TestRerank:
    async def test_results_shape(self):
        route = ROUTER.post(f"{BASE}/v1/rerank").mock(return_value=httpx.Response(200, json={"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0}, {"relevance_score": 1.0}, "x"]}))

        pairs = await client().rerank("q", ["a", "b"], top_k=5)

        assert pairs == [(1, 0.9), (0, 0.0)] and json.loads(route.calls[0].request.content) == {"query": "q", "documents": ["a", "b"], "top_n": 2, "model": "rr"}

    async def test_parallel_arrays_shape(self):
        ROUTER.post(f"{BASE}/v1/rerank").mock(return_value=httpx.Response(200, json={"indices": [2, 0], "scores": [0.8, 0.1]}))

        assert await client().rerank("q", ["a", "b", "c"]) == [(2, 0.8), (0, 0.1)]

    async def test_unknown_answer_is_an_error_not_an_empty_list(self):
        # Непонятный ответ нельзя выдавать за «все пассажи нерелевантны» ([]): реранк тогда считался бы применённым.
        ROUTER.post(f"{BASE}/v1/rerank").mock(return_value=httpx.Response(200, json={"weird": True}))

        with pytest.raises(ValueError):
            await client().rerank("q", ["a"])

    async def test_non_object_answer_is_an_error(self):
        ROUTER.post(f"{BASE}/v1/rerank").mock(return_value=httpx.Response(200, json=[1, 2]))

        with pytest.raises(ValueError):
            await client().rerank("q", ["a"])

    async def test_explicit_empty_results_is_still_an_empty_list(self):
        ROUTER.post(f"{BASE}/v1/rerank").mock(return_value=httpx.Response(200, json={"results": []}))

        assert await client().rerank("q", ["a"]) == []

    async def test_model_is_optional(self):
        route = ROUTER.post(f"{BASE}/v1/rerank").mock(return_value=httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]}))

        await client(reranker_model=None).rerank("q", ["a"])

        assert "model" not in json.loads(route.calls[0].request.content)

    async def test_empty_passages_and_errors(self):
        assert await client().rerank("q", []) == []
        ROUTER.post(f"{BASE}/v1/rerank").mock(return_value=httpx.Response(404, text="no route"))
        with pytest.raises(httpx.HTTPStatusError):
            await client().rerank("q", ["a"])

    async def test_rerank_retries_transient_502(self):
        route = ROUTER.post(f"{BASE}/v1/rerank").mock(
            side_effect=[httpx.Response(502, text="bad gateway"), httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.5}]})]
        )

        assert await client().rerank("q", ["a"]) == [(0, 0.5)]
        assert route.call_count == 2


class TestHealth:
    async def test_health(self):
        route = ROUTER.get(f"{BASE}/v1/models").mock(return_value=httpx.Response(200, json={}))
        assert await client().health() is True

        route.mock(return_value=httpx.Response(500))
        assert await client().health() is False

        route.mock(side_effect=httpx.ConnectError("refused"))
        assert await client().health() is False


class TestDimensionGuard:
    async def test_zero_and_repeated_dims_do_not_call_the_schema(self, ensured):
        c = client()

        await c._ensure_db_dim(0)
        await c._ensure_db_dim(8)
        await c._ensure_db_dim(8)

        assert ensured == [8]

    async def test_explicit_model_is_not_allowed_to_migrate(self, ensured):
        await client()._ensure_db_dim(512, allow_migrate=False)

        assert ensured == [] and rmc.RagModelsClient._last_ensured_dim is None
