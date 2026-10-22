"""settings/rag_client: HTTP-клиент SVC-RAG — запросы и ошибки, поиск с пайплайном, CRUD, согласование моделей."""

import json

import httpx
import pytest
import respx

from backend.rag_query.pipeline import ProcessedQuery
from backend.settings import rag_client as rc

pytestmark = pytest.mark.unit

BASE = "http://rag.test"
ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)


@pytest.fixture(autouse=True)
def _router():
    with ROUTER:
        yield
    ROUTER.reset()


@pytest.fixture
def client():
    return rc.RagClient(base_url=BASE + "/v1/")


def url(path):
    return f"{BASE}/v1{path}"


# --------------------------------------------------------------------- helpers


class TestHelpers:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("http://rag:8010", "http://rag:8010"),
            ("http://rag:8010/", "http://rag:8010"),
            ("http://rag:8010/v1", "http://rag:8010"),
            ("http://rag:8010/v1/", "http://rag:8010"),
            ("  http://rag  ", "http://rag"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalize_base(self, raw, expected):
        assert rc._normalize_rag_service_base(raw) == expected

    def test_request_url_adds_prefix_and_slash(self):
        assert rc._rag_request_url("http://r/", "/search") == "http://r/v1/search"
        assert rc._rag_request_url("http://r", "search") == "http://r/v1/search"

    def test_chunk_form_data(self):
        assert rc._with_chunk_index_form_data({}) == {}
        out = rc._with_chunk_index_form_data({"a": 1}, chunk_size=500, chunk_overlap=0, chunking_strategy=" Hierarchical ")
        assert out == {"a": 1, "chunk_size": "500", "chunk_overlap": "0", "chunking_strategy": "hierarchical"}
        assert "chunking_strategy" not in rc._with_chunk_index_form_data({}, chunking_strategy="  ")

    @pytest.mark.parametrize(
        "detail,expected",
        [
            ("Embedding service failed", True),
            ({"error": "Реранк недоступен"}, True),
            ({"message": "модель не загружена"}, True),
            ("LLM timeout", True),
            ("not found", False),
            ({"x": None}, False),
            (None, False),
        ],
    )
    def test_model_error_detection(self, detail, expected):
        assert rc._looks_like_rag_model_error(detail) is expected

    def test_timeouts_and_their_floors(self, monkeypatch):
        monkeypatch.delenv("SVC_RAG_SEARCH_READ_TIMEOUT", raising=False)
        monkeypatch.delenv("SVC_RAG_INDEX_READ_TIMEOUT", raising=False)
        assert rc._svc_rag_search_timeout().read == 240.0
        assert rc._svc_rag_document_index_timeout().read == 900.0
        monkeypatch.setenv("SVC_RAG_SEARCH_READ_TIMEOUT", "5")
        monkeypatch.setenv("SVC_RAG_INDEX_READ_TIMEOUT", "10")
        assert rc._svc_rag_search_timeout().read == 30.0
        assert rc._svc_rag_document_index_timeout().read == 60.0
        monkeypatch.setenv("SVC_RAG_SEARCH_READ_TIMEOUT", "abc")
        assert rc._svc_rag_search_timeout().read == 240.0

    def test_query_preview(self):
        assert rc._rag_query_preview("a\nb") == "a b"
        long = "x" * 100
        assert rc._rag_query_preview(long) == "x" * 69 + "..."
        assert rc._rag_query_preview(None) == ""

    def test_dedupe_threshold(self, monkeypatch):
        monkeypatch.delenv("RAG_DEDUP_JACCARD", raising=False)
        assert rc._dedupe_jaccard_threshold() == 0.88
        monkeypatch.setenv("RAG_DEDUP_JACCARD", "0.5")
        assert rc._dedupe_jaccard_threshold() == 0.5
        monkeypatch.setenv("RAG_DEDUP_JACCARD", "x")
        assert rc._dedupe_jaccard_threshold() == 0.88

    def test_banner_does_not_raise(self):
        rc._log_backend_rag_strategy_banner(
            path="/search", strategy=None, k=5, document_id=None, use_reranking=None, hits=0,
            query_preview="q", prep_suffix=" multi-query", from_cache=True,
        )

    def test_duplicate_exception_keeps_details(self):
        e = rc.RagDuplicateDocument("dup", document_id=7, filename="a.txt", detail={"x": 1})
        assert (e.document_id, e.filename, e.detail) == (7, "a.txt", {"x": 1}) and str(e) == "dup"


class TestInit:
    def test_explicit_base_url_is_normalised(self):
        assert rc.RagClient(base_url="http://x:1/v1/").base_url == "http://x:1"

    def test_defaults_come_from_settings(self, monkeypatch):
        class Settings:
            def microservice_http_base(self, docker_key, port_key):
                assert (docker_key, port_key) == ("rag_service_docker", "rag_service_port")
                return "http://from-settings:9/v1"

        monkeypatch.setattr(rc, "get_settings", lambda: Settings())
        assert rc.RagClient().base_url == "http://from-settings:9"

    def test_singleton(self, monkeypatch):
        monkeypatch.setattr(rc, "_rag_client_singleton", None)
        monkeypatch.setattr(rc, "RagClient", lambda: object())
        assert rc.get_rag_client() is rc.get_rag_client()

    def test_cef_extra_ports(self, client):
        assert client._cef_extra("GET", "/x", "rid") == {
            "_deviceDirection": 1, "dhost": "rag.test", "dpt": 80, "methodName": "GET /v1/x",
            "serviceName": "SVC-RAG", "requestUuid": "rid",
        }
        assert rc.RagClient(base_url="https://rag.test")._cef_extra("GET", "/x", "r")["dpt"] == 443
        assert rc.RagClient(base_url="http://rag.test:8010")._cef_extra("GET", "/x", "r")["dpt"] == 8010

    def test_identity_headers_without_user_are_empty(self, client):
        assert client._cef_identity_headers() == {}


# -------------------------------------------------------------------- _request


class TestRequest:
    async def test_returns_json(self, client):
        ROUTER.get(url("/health")).respond(200, json={"ok": True})
        assert await client.health() == {"ok": True}

    async def test_sends_json_body_and_params(self, client):
        route = ROUTER.post(url("/x")).respond(200, json={})
        await client._request("POST", "/x", json={"a": 1}, params={"p": "v"})
        req = route.calls.last.request
        assert json.loads(req.content) == {"a": 1} and req.url.params["p"] == "v"

    async def test_http_error_becomes_runtime_error_with_status_and_detail(self, client):
        ROUTER.get(url("/x")).respond(500, json={"detail": "boom"})
        with pytest.raises(RuntimeError, match="failed: 500") as exc:
            await client._request("GET", "/x")
        assert "boom" in str(exc.value)

    async def test_non_json_error_body_is_kept_as_text(self, client):
        ROUTER.get(url("/x")).respond(502, text="Bad gateway page")
        with pytest.raises(RuntimeError, match="Bad gateway page"):
            await client._request("GET", "/x")

    async def test_409_is_reindex_in_progress(self, client):
        ROUTER.post(url("/search")).respond(409, json={"detail": "reindexing"})
        with pytest.raises(rc.RagReindexInProgress):
            await client._request("POST", "/search", json={})

    async def test_409_with_duplicate_detail_is_a_duplicate_error(self, client):
        ROUTER.post(url("/kb/documents")).respond(
            409, json={"detail": {"code": "duplicate_document", "message": "Уже есть", "document_id": "12", "filename": "a.pdf"}}
        )
        with pytest.raises(rc.RagDuplicateDocument) as exc:
            await client._request("POST", "/kb/documents")
        assert exc.value.document_id == 12 and exc.value.filename == "a.pdf" and "Уже есть" in str(exc.value)

    async def test_duplicate_with_garbage_document_id(self, client):
        ROUTER.post(url("/kb/documents")).respond(409, json={"detail": {"code": "duplicate_document", "document_id": "abc"}})
        with pytest.raises(rc.RagDuplicateDocument) as exc:
            await client._request("POST", "/kb/documents")
        assert exc.value.document_id is None

    async def test_network_errors_become_runtime_errors(self, client):
        ROUTER.get(url("/x")).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(RuntimeError, match="error: refused") as exc:
            await client._request("GET", "/x")
        assert isinstance(exc.value.__cause__, httpx.ConnectError)

    @pytest.mark.parametrize("error", [httpx.ReadTimeout("t"), httpx.ConnectTimeout("t"), httpx.ReadError("r")])
    async def test_other_transport_errors(self, client, error):
        ROUTER.get(url("/x")).mock(side_effect=error)
        with pytest.raises(RuntimeError):
            await client._request("GET", "/x")

    async def test_invalid_json_success_is_an_error(self, client):
        ROUTER.get(url("/x")).respond(200, text="<html>")
        with pytest.raises(RuntimeError):
            await client._request("GET", "/x")

    async def test_polling_paths_work_without_audit(self, client):
        ROUTER.get(url("/kb/reindex/status")).respond(200, json={"reindexing": False})
        assert await client._request("GET", "/kb/reindex/status") == {"reindexing": False}

    async def test_svc_rag_api_key_sent_as_x_api_key(self, client, monkeypatch):
        """Без X-API-Key svc-rag (ключ задан) отвечает 401 на reindex/status."""
        monkeypatch.setenv("SVC_RAG_API_KEY", "dev-svc-rag-key")
        monkeypatch.delenv("RAG_API_KEY", raising=False)
        route = ROUTER.get(url("/kb/reindex/status")).respond(
            200, json={"reindexing": False}
        )
        assert await client._request("GET", "/kb/reindex/status") == {"reindexing": False}
        assert route.calls.last.request.headers["x-api-key"] == "dev-svc-rag-key"

    async def test_svc_rag_api_key_absent_omits_header(self, client, monkeypatch):
        monkeypatch.delenv("SVC_RAG_API_KEY", raising=False)
        monkeypatch.delenv("RAG_API_KEY", raising=False)
        rc.RagClient._warned_missing_svc_rag_key = False
        route = ROUTER.get(url("/kb/reindex/status")).respond(
            200, json={"reindexing": False}
        )
        await client._request("GET", "/kb/reindex/status")
        assert "x-api-key" not in route.calls.last.request.headers

    async def test_model_header_is_forwarded(self, client, monkeypatch):
        from backend.settings.cef_logger import cef_audit_context as ctx

        monkeypatch.setattr(ctx, "chat_model_peek", lambda: "gw://model")
        route = ROUTER.get(url("/health")).respond(200, json={})
        await client.health()
        assert route.calls.last.request.headers["x-astra-model"] == "gw://model"


class TestParseHits:
    def test_maps_fields_and_cosine(self):
        hits = rc.RagClient._parse_hits(
            {"hits": [
                {"content": "a", "score": 0.5, "document_id": 1, "chunk_index": 2, "cosine": "0.7"},
                {"content": "b", "score": "0.1"},
                {"content": "c", "score": 0.2, "cosine": "oops"},
            ]}
        )
        assert [tuple(h) for h in hits] == [("a", 0.5, 1, 2), ("b", 0.1, None, None), ("c", 0.2, None, None)]
        assert [h.cosine for h in hits] == [0.7, None, None]

    @pytest.mark.parametrize("resp", [None, [], "x", {}, {"hits": []}])
    def test_non_dict_or_empty(self, resp):
        assert rc.RagClient._parse_hits(resp) == []


class TestMergeVariants:
    async def test_runs_each_variant_and_keeps_best_score_per_chunk(self, client):
        answers = {
            "основной": {"hits": [{"content": "a", "score": 0.4, "document_id": 1, "chunk_index": 0}]},
            "вариант": {"hits": [
                {"content": "a2", "score": 0.9, "document_id": 1, "chunk_index": 0},
                {"content": "b", "score": 0.5, "document_id": 2, "chunk_index": 1},
            ]},
        }
        seen = []

        def handler(request):
            body = json.loads(request.content)
            seen.append(body)
            return httpx.Response(200, json=answers[body["query"]])

        ROUTER.post(url("/search")).mock(side_effect=handler)
        out = await client._merge_variant_searches(
            "/search", {"query": "основной", "k": 5, "vector_query": "hyde"}, ["вариант", "  ", "основной"], k=5
        )
        assert [h[0] for h in out] == ["a2", "b"] and out[0][1] == 0.9
        assert seen[0].get("vector_query") == "hyde" and "vector_query" not in seen[1]
        assert len(seen) == 2

    async def test_limits_to_k(self, client):
        ROUTER.post(url("/search")).respond(200, json={"hits": [
            {"content": str(i), "score": 1 - i / 10, "document_id": i, "chunk_index": 0} for i in range(5)
        ]})
        assert len(await client._merge_variant_searches("/search", {"query": "q"}, [], k=2)) == 2


# ----------------------------------------------------------------- CRUD / forms


class TestSimpleEndpoints:
    async def test_schema_endpoints(self, client):
        r1 = ROUTER.post(url("/schema/embedding-dim")).respond(200, json={"migrated": False})
        r2 = ROUTER.post(url("/schema/models-provider")).respond(200, json={})
        await client.ensure_embedding_dim("768")
        await client.set_models_provider("embedding", "native", None)
        assert json.loads(r1.calls.last.request.content) == {"embedding_dim": 768}
        assert json.loads(r2.calls.last.request.content) == {"model_type": "embedding", "provider": "native", "model": None}

    async def test_legacy_documents(self, client):
        ROUTER.get(url("/documents")).respond(200, json=[{"id": 1}])
        ROUTER.delete(url("/documents/5")).respond(200, json={"ok": True})
        ROUTER.delete(url("/documents/by-filename/a.txt")).respond(200, json={"ok": True})
        ROUTER.get(url("/documents/report/confidence")).respond(200, json={"c": 1})
        ROUTER.get(url("/documents/minio-info/a.png")).respond(200, json={"bucket": "b"})
        assert await client.list_documents() == [{"id": 1}]
        assert await client.delete_document_by_id(5) == {"ok": True}
        assert await client.delete_document_by_filename("a.txt") == {"ok": True}
        assert await client.get_confidence_report() == {"c": 1}
        assert await client.get_image_minio_info("a.png") == {"bucket": "b"}

    async def test_minio_info_null_is_none(self, client):
        ROUTER.get(url("/documents/minio-info/x.png")).respond(200, content=b"null")
        assert await client.get_image_minio_info("x.png") is None

    async def test_upload_document_form(self, client):
        route = ROUTER.post(url("/documents")).respond(200, json={"ok": True})
        await client.upload_document(b"DATA", "a.txt", minio_object="o", minio_bucket="b", original_path="/p", chunk_size=300, chunk_overlap=10)
        body = route.calls.last.request.content
        for needle in (b'filename="a.txt"', b"DATA", b'name="minio_object"', b'name="original_path"', b'name="chunk_size"'):
            assert needle in body

    async def test_start_chunks_map_to_hits_and_failures_give_empty_lists(self, client):
        ROUTER.get(url("/documents/1/chunks")).respond(200, json={"chunks": [{"content": "c", "document_id": 1, "chunk_index": 0}]})
        ROUTER.get(url("/documents/2/chunks")).respond(404)
        ROUTER.get(url("/project-rag/projects/p/documents/3/chunks")).respond(200, json={"chunks": [{"content": "x", "document_id": 3, "chunk_index": 1}]})
        ROUTER.get(url("/project-rag/projects/p/documents/4/chunks")).respond(500)
        assert await client.get_document_start_chunks(1) == [("c", 1.0, 1, 0)]
        assert await client.get_document_start_chunks(2) == []
        assert await client.project_rag_get_document_start_chunks("p", 3) == [("x", 1.0, 3, 1)]
        assert await client.project_rag_get_document_start_chunks("p", 4) == []

    async def test_reindex_status_combines_three_stores(self, client):
        ROUTER.get(url("/kb/reindex/status")).respond(200, json={"reindexing": True})
        ROUTER.get(url("/memory-rag/reindex/status")).respond(200, json={"reindexing": False})
        ROUTER.get(url("/project-rag/reindex/status")).respond(500)
        out = await client.get_reindex_status()
        assert out == {
            "memory": {"reindexing": False},
            "project": {"reindexing": False},
            "kb": {"reindexing": True},
            "any_reindexing": True,
        }

    async def test_reindex_status_all_idle_or_unreachable(self, client):
        for store in ("kb", "memory-rag", "project-rag"):
            ROUTER.get(url(f"/{store}/reindex/status")).mock(side_effect=httpx.ConnectError("x"))
        assert (await client.get_reindex_status())["any_reindexing"] is False


class TestKnowledgeBase:
    async def test_upload_lowercases_ids_but_keeps_model_case(self, client):
        route = ROUTER.post(url("/kb/documents")).respond(200, json={"ok": True})
        await client.kb_upload_document(
            b"X", "a.pdf", chunk_size=400, chunking_strategy=" Universal ", owner_user_id=" Alice ", uploaded_by="BOB",
            agent_id=7, embedding_model=" embed/FRIDA ", embedding_provider=" CORSUR ",
        )
        body = route.calls.last.request.content.decode("latin-1")
        assert "alice" in body and "bob" in body and "embed/FRIDA" in body and "CORSUR" in body
        assert 'name="agent_id"\r\n\r\n7' in body and "universal" in body

    async def test_duplicate_check_normalises_hash(self, client):
        route = ROUTER.post(url("/kb/documents/duplicate-check")).respond(200, json={"duplicate": True})
        assert await client.kb_check_duplicate(content_hash=" ABC ", filename="a", size=5, agent_id=3) == {"duplicate": True}
        assert json.loads(route.calls.last.request.content) == {"content_hash": "abc", "filename": "a", "size": 5, "agent_id": 3}

    async def test_duplicate_check_odd_answer_means_no_duplicate(self, client):
        ROUTER.post(url("/kb/documents/duplicate-check")).respond(200, json=["x"])
        assert await client.kb_check_duplicate(content_hash="a") == {"duplicate": False}

    async def test_list_get_delete(self, client):
        ROUTER.get(url("/kb/documents")).respond(200, json={"not": "a list"})
        ROUTER.get(url("/kb/documents/4")).respond(200, json=[1])
        ROUTER.delete(url("/kb/documents/4")).respond(200, json={"ok": True})
        assert await client.kb_list_documents() == []
        assert await client.kb_get_document(4) is None
        assert await client.kb_delete_document(4) == {"ok": True}

    async def test_clone_body_and_odd_answer(self, client):
        route = ROUTER.post(url("/kb/documents/4/clone")).respond(200, json={"ok": True, "document_id": 9})
        out = await client.kb_clone_document(4, agent_id=2, owner_user_id=" Bob ", minio_object="", minio_bucket="b")
        assert out["document_id"] == 9
        assert json.loads(route.calls.last.request.content) == {"agent_id": 2, "owner_user_id": "bob", "minio_object": "", "minio_bucket": "b"}
        ROUTER.post(url("/kb/documents/5/clone")).respond(200, json="x")
        assert await client.kb_clone_document(5) == {"ok": False}

    async def test_reindex_body(self, client):
        route = ROUTER.post(url("/kb/reindex")).respond(200, json={"started": True})
        await client.kb_reindex(
            chunk_size=300, chunk_overlap=0, chunking_strategy="u", owner_user_id=" Alice ", document_ids=[1, None, "2"],
            embedding_model="m", embedding_provider="p", agent_id=5,
        )
        assert json.loads(route.calls.last.request.content) == {
            "chunk_size": 300, "chunk_overlap": 0, "chunking_strategy": "u", "owner_user_id": "alice",
            "agent_id": 5, "document_ids": [1, 2], "embedding_model": "m", "embedding_provider": "p",
        }

    async def test_reindex_without_options_sends_an_empty_body(self, client):
        route = ROUTER.post(url("/kb/reindex")).respond(200, json={})
        await client.kb_reindex()
        assert json.loads(route.calls.last.request.content) == {}


class TestMemoryAndProjectEndpoints:
    async def test_memory_endpoints(self, client, monkeypatch):
        monkeypatch.setattr("backend.services.memory_rag_env.get_memory_embedding_fields", lambda: {"embedding_provider": "native"})
        index = ROUTER.post(url("/memory-rag/documents")).respond(200, json={"ok": True})
        reindex = ROUTER.post(url("/memory-rag/reindex")).respond(200, json={})
        ROUTER.post(url("/memory-rag/documents/duplicate-check")).respond(200, json={"duplicate": False})
        ROUTER.get(url("/memory-rag/documents")).respond(200, json=[{"id": 1}])
        ROUTER.delete(url("/memory-rag/documents/3")).respond(200, json={"ok": True})
        await client.memory_rag_index_document(b"x", "a.txt", chunk_size=100)
        assert b"embedding_provider" in index.calls.last.request.content
        await client.memory_rag_reindex(chunk_size=200, chunking_strategy="u")
        assert json.loads(reindex.calls.last.request.content) == {"chunk_size": 200, "chunking_strategy": "u"}
        assert await client.memory_rag_check_duplicate(content_hash="A") == {"duplicate": False}
        assert await client.memory_rag_list_documents() == [{"id": 1}]
        assert await client.memory_rag_delete_document(3) == {"ok": True}

    async def test_project_endpoints(self, client):
        upload = ROUTER.post(url("/project-rag/projects/p1/documents")).respond(200, json={"ok": True})
        ROUTER.post(url("/project-rag/projects/p1/documents/duplicate-check")).respond(200, json={"duplicate": True})
        ROUTER.get(url("/project-rag/projects/p1/documents")).respond(200, json=[{"id": 1}])
        ROUTER.delete(url("/project-rag/projects/p1/documents/7")).respond(200, json={"ok": True})
        ROUTER.delete(url("/project-rag/projects/p1")).respond(200, json={"ok": True})
        reindex = ROUTER.post(url("/project-rag/reindex")).respond(200, json={})

        await client.project_rag_upload_document(b"x", "a.txt", "p1", owner_user_id="ALICE", embedding_model="embed/X")
        assert b"alice" in upload.calls.last.request.content and b"embed/X" in upload.calls.last.request.content
        assert await client.project_rag_check_duplicate(project_id="p1", content_hash="H") == {"duplicate": True}
        assert await client.project_rag_list_documents("p1") == [{"id": 1}]
        assert await client.project_rag_delete_document("p1", 7) == {"ok": True}
        assert await client.project_rag_delete_project("p1") == {"ok": True}
        await client.project_rag_reindex_all(owner_user_id=" Bob ", project_id=" p1 ", chunk_size=100)
        assert json.loads(reindex.calls.last.request.content) == {"chunk_size": 100, "owner_user_id": "bob", "project_id": "p1"}

    async def test_non_list_listing_is_empty(self, client):
        ROUTER.get(url("/project-rag/projects/p/documents")).respond(200, json={"x": 1})
        ROUTER.get(url("/memory-rag/documents")).respond(200, json="x")
        assert await client.project_rag_list_documents("p") == []
        assert await client.memory_rag_list_documents() == []


# ------------------------------------------------------- search with the pipeline


class FakePipeline:
    """Подменяет всё, что делает поиск вокруг HTTP-вызова."""

    def __init__(self, monkeypatch, *, query="q", multi=None, vector_query=None, filters=None, rag=None, judge_fn=None, rewritten=None):
        self.judge_calls, self.rewrite_calls = [], []
        user_rag = {"rag_reranking_enabled": False, "rag_rerank_top_n": 0, **(rag or {})}

        async def process(text, **kw):
            return ProcessedQuery(text, text, query if query != "q" else text, vector_query, multi, filters)

        async def judge(q, hits):
            self.judge_calls.append(q)
            return judge_fn(hits) if callable(judge_fn) else hits

        async def rewrite(q):
            self.rewrite_calls.append(q)
            return rewritten

        monkeypatch.setattr("backend.rag_query.pipeline.process_user_query", process)
        monkeypatch.setattr("backend.rag_query.pipeline.rewrite_query_with_context", rewrite)
        monkeypatch.setattr(rc, "judge_and_filter_hits", judge)
        monkeypatch.setattr("backend.services.user_rag_settings.get_runtime_rag_settings", lambda scope=None: user_rag)
        monkeypatch.setattr("backend.services.user_rag_settings.runtime_rag_strategy", lambda scope=None: "hybrid")
        monkeypatch.setattr("backend.services.user_rag_settings.runtime_memory_strategy", lambda: "vector")
        monkeypatch.setattr("backend.services.user_rag_settings.runtime_embedding_fields", lambda scope=None: {"embedding_provider": "native"})
        monkeypatch.setattr("backend.services.user_rag_settings.runtime_reranker_fields", lambda scope=None: {})
        monkeypatch.setattr("backend.services.memory_rag_env.get_memory_embedding_fields", lambda: {"embedding_model": "mem"})
        monkeypatch.setattr("backend.services.memory_rag_env.get_memory_reranker_fields", lambda: {})
        monkeypatch.setattr("backend.services.memory_rag_env.get_memory_rag_retrieval_settings", lambda: user_rag)
        monkeypatch.setattr("backend.services.memory_rag_env.get_memory_rag_chat_top_k", lambda: 4)
        monkeypatch.setattr("backend.services.memory_rag_env.filter_hits_by_memory_similarity", lambda hits: hits)
        monkeypatch.setattr("backend.rag_query.semantic_cache.semantic_cache_enabled", lambda: False)


HIT = {"content": "Текст фрагмента", "score": 0.9, "document_id": 1, "chunk_index": 0, "cosine": 0.8}


class TestSearchPipeline:
    async def test_basic_search_builds_body_from_settings(self, client, monkeypatch):
        FakePipeline(monkeypatch)
        route = ROUTER.post(url("/kb/search")).respond(200, json={"hits": [HIT]})

        hits = await client.kb_search("вопрос", k=3, document_ids=[2, 1], strategy="auto")

        assert [h[0] for h in hits] == ["Текст фрагмента"] and hits[0].cosine == 0.8
        body = json.loads(route.calls.last.request.content)
        assert body["query"] == "вопрос" and body["k"] == 3 and body["document_ids"] == [2, 1]
        assert body["strategy"] == "hybrid"  # стратегия берётся из настроек, а не из запроса
        assert body["embedding_provider"] == "native" and body["use_reranking"] is False

    async def test_raw_cosine_skips_preprocessing_and_postprocessing(self, client, monkeypatch):
        fake = FakePipeline(monkeypatch)
        monkeypatch.setattr("backend.services.user_rag_settings.runtime_rag_strategy", lambda scope=None: "raw_cosine")
        route = ROUTER.post(url("/kb/search")).respond(200, json={"hits": [HIT]})
        hits = await client.kb_search("q", k=2, document_id=5)
        body = json.loads(route.calls.last.request.content)
        assert body == {"query": "q", "k": 2, "strategy": "raw_cosine", "document_id": 5, "embedding_provider": "native"}
        assert len(hits) == 1 and fake.judge_calls == []

    async def test_hyde_filters_and_rerank_flags(self, client, monkeypatch):
        FakePipeline(monkeypatch, vector_query="гипотетический ответ", filters={"filename_contains": "отчёт"}, rag={"rag_reranking_enabled": True, "rag_rerank_top_n": 100})
        route = ROUTER.post(url("/kb/search")).respond(200, json={"hits": [HIT, {**HIT, "document_id": 2, "content": "Другой совсем текст"}]})
        await client.kb_search("q", k=2)
        body = json.loads(route.calls.last.request.content)
        assert body["vector_query"] == "гипотетический ответ" and body["filters"] == {"filename_contains": "отчёт"}
        assert body["use_reranking"] is True

    async def test_explicit_rerank_flag_wins_over_settings(self, client, monkeypatch):
        FakePipeline(monkeypatch, rag={"rag_reranking_enabled": True})
        route = ROUTER.post(url("/kb/search")).respond(200, json={"hits": [HIT]})
        await client.kb_search("q", use_reranking=False)
        assert json.loads(route.calls.last.request.content)["use_reranking"] is False

    async def test_lexical_strategy_disables_rerank(self, client, monkeypatch):
        FakePipeline(monkeypatch, rag={"rag_reranking_enabled": True})
        monkeypatch.setattr("backend.services.user_rag_settings.runtime_rag_strategy", lambda scope=None: "lexical")
        route = ROUTER.post(url("/kb/search")).respond(200, json={"hits": [HIT]})
        await client.kb_search("q", use_reranking=True)
        assert json.loads(route.calls.last.request.content)["use_reranking"] is False

    async def test_multi_query_merges_variants(self, client, monkeypatch):
        FakePipeline(monkeypatch, multi=["иначе"])
        route = ROUTER.post(url("/kb/search")).respond(200, json={"hits": [HIT]})
        hits = await client.kb_search("q")
        assert len(route.calls) == 2 and len(hits) == 1

    async def test_duplicates_are_removed(self, client, monkeypatch):
        FakePipeline(monkeypatch)
        long_text = "Достаточно длинный фрагмент текста, чтобы сравнение по словам вообще применялось к нему"
        base = {**HIT, "content": long_text}
        ROUTER.post(url("/kb/search")).respond(200, json={"hits": [base, {**base, "document_id": 2, "chunk_index": 5}]})
        assert len(await client.kb_search("q")) == 1

    async def test_weak_results_trigger_one_rewritten_retry_and_merge(self, client, monkeypatch):
        fake = FakePipeline(monkeypatch, rewritten="переписанный вопрос")
        monkeypatch.setenv("RAG_REWRITE_MIN_COSINE", "0.5")
        weak = {**HIT, "cosine": 0.1}
        strong = {"content": "Совсем другой найденный фрагмент", "score": 0.7, "document_id": 9, "chunk_index": 3, "cosine": 0.9}
        queries = []

        def handler(request):
            q = json.loads(request.content)["query"]
            queries.append(q)
            return httpx.Response(200, json={"hits": [weak] if q == "q" else [strong]})

        ROUTER.post(url("/kb/search")).mock(side_effect=handler)
        hits = await client.kb_search("q")
        assert queries == ["q", "переписанный вопрос"] and fake.rewrite_calls == ["q"]
        assert {h[2] for h in hits} == {1, 9}

    async def test_empty_results_are_retried_but_a_failed_rewrite_changes_nothing(self, client, monkeypatch):
        fake = FakePipeline(monkeypatch, rewritten=None)
        ROUTER.post(url("/kb/search")).respond(200, json={"hits": []})
        assert await client.kb_search("q") == []
        assert fake.rewrite_calls == ["q"]

    async def test_good_results_do_not_spend_a_rewrite(self, client, monkeypatch):
        fake = FakePipeline(monkeypatch, rewritten="x")
        monkeypatch.setenv("RAG_REWRITE_MIN_COSINE", "0.3")
        ROUTER.post(url("/kb/search")).respond(200, json={"hits": [HIT]})
        await client.kb_search("q")
        assert fake.rewrite_calls == []

    async def test_zero_threshold_disables_the_cosine_check(self, client, monkeypatch):
        fake = FakePipeline(monkeypatch, rewritten="x")
        monkeypatch.setenv("RAG_REWRITE_MIN_COSINE", "0")
        ROUTER.post(url("/kb/search")).respond(200, json={"hits": [{**HIT, "cosine": 0.01}]})
        await client.kb_search("q")
        assert fake.rewrite_calls == []

    async def test_semantic_cache_serves_repeated_queries(self, client, monkeypatch):
        FakePipeline(monkeypatch)
        monkeypatch.setattr("backend.rag_query.semantic_cache.semantic_cache_enabled", lambda: True)
        store = {}
        monkeypatch.setattr("backend.rag_query.semantic_cache.cache_get", lambda key: store.get(key))
        monkeypatch.setattr("backend.rag_query.semantic_cache.cache_set", lambda key, hits: store.__setitem__(key, hits))
        route = ROUTER.post(url("/kb/search")).respond(200, json={"hits": [HIT]})
        first = await client.kb_search("q")
        second = await client.kb_search("q")
        assert len(route.calls) == 1 and [h[0] for h in first] == [h[0] for h in second]

    async def test_memory_search_uses_memory_settings_and_top_k(self, client, monkeypatch):
        FakePipeline(monkeypatch)
        route = ROUTER.post(url("/memory-rag/search")).respond(200, json={"hits": [HIT]})
        await client.memory_rag_search("q")
        body = json.loads(route.calls.last.request.content)
        assert body["k"] == 4 and body["embedding_model"] == "mem" and body["strategy"] == "vector"

    async def test_project_search_path_and_scope(self, client, monkeypatch):
        FakePipeline(monkeypatch)
        route = ROUTER.post(url("/project-rag/projects/p1/search")).respond(200, json={"hits": [HIT]})
        assert len(await client.project_rag_search("q", "p1", k=5)) == 1
        assert json.loads(route.calls.last.request.content)["k"] == 5

    async def test_plain_search_keeps_the_requested_strategy_for_user_scope(self, client, monkeypatch):
        FakePipeline(monkeypatch)
        route = ROUTER.post(url("/search")).respond(200, json={"hits": [HIT]})
        await client.search("q", strategy="vector")
        assert json.loads(route.calls.last.request.content)["strategy"] == "vector"

    async def test_service_errors_propagate(self, client, monkeypatch):
        FakePipeline(monkeypatch)
        ROUTER.post(url("/kb/search")).respond(409, json={"detail": "reindexing"})
        with pytest.raises(rc.RagReindexInProgress):
            await client.kb_search("q")


# ------------------------------------------------------- model reconciliation


class TestModelPathParsing:
    def test_native_paths_have_no_model(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.user_rag_settings.embedding_fields_from_path",
            lambda path: {"embedding_provider": "native", "embedding_model": "x"},
        )
        assert rc.rag_model_path_to_provider("local/x") == ("native", None)

    def test_gateway_paths(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.user_rag_settings.embedding_fields_from_path",
            lambda path: {"embedding_provider": "CORSUR", "embedding_model": "embed/FRIDA"},
        )
        assert rc.rag_model_path_to_provider("corsur/embed/FRIDA") == ("CORSUR", "embed/FRIDA")

    def test_missing_provider_is_native(self, monkeypatch):
        monkeypatch.setattr("backend.services.user_rag_settings.embedding_fields_from_path", lambda path: {})
        assert rc.rag_model_path_to_provider("") == ("native", None)


class FakeRag:
    def __init__(self, result=None, schema=None):
        self.pushed, self.ensured = [], []
        self.result, self.schema = result or {}, schema or {}

    async def set_models_provider(self, model_type, provider, model):
        self.pushed.append((model_type, provider, model))
        return self.result

    async def ensure_embedding_dim(self, dim):
        self.ensured.append(dim)
        return self.schema


class TestReconcile:
    @pytest.fixture(autouse=True)
    def _paths(self, monkeypatch):
        table = {
            "local/emb": ("native", None),
            "corsur/embed/FRIDA": ("CORSUR", "embed/FRIDA"),
            "local/rr": ("native", None),
        }
        monkeypatch.setattr(rc, "rag_model_path_to_provider", lambda path: table[path])

    async def test_old_service_without_switching_is_left_alone(self):
        client = FakeRag()
        await rc.reconcile_rag_models_provider(client, {"status": "ok"}, "local/emb", "local/rr")
        await rc.reconcile_rag_models_provider(client, None, "local/emb", "local/rr")
        assert client.pushed == []

    async def test_matching_state_pushes_nothing(self):
        client = FakeRag()
        health = {"embedding_provider": "native", "reranker_provider": "native"}
        await rc.reconcile_rag_models_provider(client, health, "local/emb", "local/rr")
        assert client.pushed == []

    async def test_mismatch_pushes_user_choice_and_syncs_dimension(self):
        client = FakeRag(result={"embedding_dim": 1024}, schema={"migrated": False})
        health = {"embedding_provider": "native", "reranker_provider": "native"}
        await rc.reconcile_rag_models_provider(client, health, "corsur/embed/FRIDA", "local/rr")
        assert client.pushed == [("embedding", "CORSUR", "embed/FRIDA")]
        assert client.ensured == [1024]

    async def test_reranker_mismatch_does_not_touch_dimension(self):
        client = FakeRag()
        health = {"embedding_provider": "native", "reranker_provider": "CORSUR", "reranker_model": "r"}
        await rc.reconcile_rag_models_provider(client, health, "local/emb", "local/rr")
        assert client.pushed == [("reranker", "native", None)] and client.ensured == []

    async def test_migration_triggers_background_restore(self, monkeypatch):
        calls = []

        async def background():
            calls.append("restored")

        import backend.routes.rag as routes_rag

        monkeypatch.setattr(routes_rag, "_reindex_on_model_change", lambda: True)
        monkeypatch.setattr(routes_rag, "_run_background_reindex_after_model_change", background)
        client = FakeRag(result={"embedding_dim": 8}, schema={"migrated": True})
        await rc.reconcile_rag_models_provider(client, {"embedding_provider": "native"}, "corsur/embed/FRIDA", "local/rr")
        import asyncio

        await asyncio.sleep(0)
        assert calls == ["restored"]

    async def test_migration_without_restore_switch_does_nothing_extra(self, monkeypatch):
        import backend.routes.rag as routes_rag

        monkeypatch.setattr(routes_rag, "_reindex_on_model_change", lambda: False)
        client = FakeRag(result={"embedding_dim": 8}, schema={"migrated": True})
        await rc.reconcile_rag_models_provider(client, {"embedding_provider": "native"}, "corsur/embed/FRIDA", "local/rr")
        assert client.ensured == [8]
