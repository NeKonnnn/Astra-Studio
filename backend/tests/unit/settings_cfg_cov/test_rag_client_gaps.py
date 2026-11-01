"""settings/rag_client: необязательные поля форм/JSON, CEF-заголовки, пограничные ветки поиска."""

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


class _BadStr:
    def __str__(self):
        msg = "не превращается в строку"
        raise RuntimeError(msg)


class TestHelperEdges:
    def test_model_error_detection_survives_unstringable_values(self):
        # str(value) падает внутри ветки dict -> запасной путь через str(detail)
        assert rc._looks_like_rag_model_error({"k": _BadStr()}) is False

    def test_index_timeout_with_garbage_env_uses_default(self, monkeypatch):
        monkeypatch.setenv("SVC_RAG_INDEX_READ_TIMEOUT", "abc")

        assert rc._svc_rag_document_index_timeout().read == 900.0


class TestCefIdentityHeaders:
    def _peek(self, monkeypatch, user):
        monkeypatch.setattr(
            "backend.settings.cef_logger.cef_audit_context.cef_audit_peek",
            lambda: (None, user, None),
        )

    def test_full_identity(self, client, monkeypatch):
        self._peek(monkeypatch, {"username": "alice", "user_id": "u1", "sntdom": "CORP"})

        assert client._cef_identity_headers() == {
            "X-CEF-Suser": "alice",
            "X-CEF-Suid": "u1",
            "X-CEF-Sntdom": "CORP",
        }

    def test_domain_fallback_and_user_id_as_username(self, client, monkeypatch):
        self._peek(monkeypatch, {"user_id": "u2", "domain": "D"})

        assert client._cef_identity_headers() == {
            "X-CEF-Suser": "u2",
            "X-CEF-Suid": "u2",
            "X-CEF-Sntdom": "D",
        }

    def test_empty_values_are_skipped(self, client, monkeypatch):
        self._peek(monkeypatch, {"username": " ", "user_id": ""})

        assert client._cef_identity_headers() == {}

    def test_no_user_gives_no_headers(self, client, monkeypatch):
        self._peek(monkeypatch, None)

        assert client._cef_identity_headers() == {}

    def test_peek_failure_is_swallowed(self, client, monkeypatch):
        def boom():
            raise RuntimeError("no context")

        monkeypatch.setattr("backend.settings.cef_logger.cef_audit_context.cef_audit_peek", boom)

        assert client._cef_identity_headers() == {}


class TestRequestDuplicate:
    async def test_409_duplicate_with_plain_text_detail_uses_it_as_message(self, client):
        ROUTER.post(url("/kb/documents")).respond(
            409, json={"detail": "duplicate_document: файл уже есть"}
        )

        with pytest.raises(rc.RagDuplicateDocument) as exc:
            await client._request("POST", "/kb/documents")

        assert str(exc.value) == "duplicate_document: файл уже есть"
        assert exc.value.document_id is None


def _form(route) -> bytes:
    return route.calls.last.request.content


class TestOptionalFields:
    async def test_upload_document_minio_and_original_path(self, client):
        route = ROUTER.post(url("/documents")).respond(200, json={})

        await client.upload_document(
            b"x", "a.txt", minio_object="o", minio_bucket="b", original_path="/p/a.txt"
        )

        body = _form(route)
        assert b'name="minio_object"\r\n\r\no' in body
        assert b'name="minio_bucket"\r\n\r\nb' in body
        assert b'name="original_path"\r\n\r\n/p/a.txt' in body

    async def test_kb_upload_all_optional_fields(self, client):
        route = ROUTER.post(url("/kb/documents")).respond(200, json={})

        await client.kb_upload_document(
            b"x",
            "a.txt",
            chunk_size=100,
            chunk_overlap=10,
            chunking_strategy="Hier",
            minio_object="obj",
            minio_bucket="bkt",
            owner_user_id=" ALICE ",
            agent_id=3,
            uploaded_by=" BoB ",
            embedding_model=" embed/FRIDA ",
            embedding_provider=" gw ",
        )

        body = _form(route)
        for fragment in (
            b'name="minio_object"\r\n\r\nobj',
            b'name="minio_bucket"\r\n\r\nbkt',
            b'name="owner_user_id"\r\n\r\nalice',
            b'name="uploaded_by"\r\n\r\nbob',
            b'name="agent_id"\r\n\r\n3',
            b'name="embedding_model"\r\n\r\nembed/FRIDA',
            b'name="embedding_provider"\r\n\r\ngw',
            b'name="chunk_size"\r\n\r\n100',
            b'name="chunking_strategy"\r\n\r\nhier',
        ):
            assert fragment in body

    async def test_kb_clone_document_all_fields(self, client):
        route = ROUTER.post(url("/kb/documents/5/clone")).respond(200, json={"id": 6})

        res = await client.kb_clone_document(
            5,
            agent_id=2,
            owner_user_id=" ALICE ",
            uploaded_by=" BOB ",
            minio_object="o",
            minio_bucket="b",
        )

        assert res == {"id": 6}
        assert json.loads(route.calls.last.request.content) == {
            "agent_id": 2,
            "owner_user_id": "alice",
            "uploaded_by": "bob",
            "minio_object": "o",
            "minio_bucket": "b",
        }

    async def test_kb_reindex_chunk_fields(self, client):
        route = ROUTER.post(url("/kb/reindex")).respond(200, json={})

        await client.kb_reindex(chunk_size=300, chunk_overlap=30, chunking_strategy="x")

        sent = json.loads(route.calls.last.request.content)
        assert sent["chunk_size"] == 300 and sent["chunk_overlap"] == 30
        assert sent["chunking_strategy"] == "x"

    async def test_memory_reindex_all_fields(self, client):
        route = ROUTER.post(url("/memory-rag/reindex")).respond(200, json={})

        await client.memory_rag_reindex(chunk_size=300, chunk_overlap=30, chunking_strategy="s")

        assert json.loads(route.calls.last.request.content) == {
            "chunk_size": 300,
            "chunk_overlap": 30,
            "chunking_strategy": "s",
        }

    async def test_memory_index_document_minio_fields(self, client, monkeypatch):
        monkeypatch.setattr(
            "backend.services.memory_rag_env.get_memory_embedding_fields", lambda: {}
        )
        route = ROUTER.post(url("/memory-rag/documents")).respond(200, json={})

        await client.memory_rag_index_document(
            b"x", "a.txt", minio_object="mo", minio_bucket="mb"
        )

        assert b'name="minio_object"\r\n\r\nmo' in _form(route)
        assert b'name="minio_bucket"\r\n\r\nmb' in _form(route)

    async def test_memory_duplicate_check_filename_and_size(self, client):
        route = ROUTER.post(url("/memory-rag/documents/duplicate-check")).respond(
            200, json={"duplicate": False}
        )

        await client.memory_rag_check_duplicate(content_hash=" ABC ", filename="a.txt", size=12)

        assert json.loads(route.calls.last.request.content) == {
            "content_hash": "abc",
            "filename": "a.txt",
            "size": 12,
        }

    async def test_project_upload_all_optional_fields(self, client):
        route = ROUTER.post(url("/project-rag/projects/p1/documents")).respond(200, json={})

        await client.project_rag_upload_document(
            b"x",
            "a.txt",
            "p1",
            minio_object="po",
            minio_bucket="pb",
            owner_user_id=" ALICE ",
            uploaded_by=" BOB ",
            embedding_model="embed/X",
            embedding_provider="gw",
        )

        body = _form(route)
        for fragment in (
            b'name="minio_object"\r\n\r\npo',
            b'name="minio_bucket"\r\n\r\npb',
            b'name="owner_user_id"\r\n\r\nalice',
            b'name="uploaded_by"\r\n\r\nbob',
            b'name="embedding_model"\r\n\r\nembed/X',
            b'name="embedding_provider"\r\n\r\ngw',
        ):
            assert fragment in body

    async def test_project_duplicate_check_filename_and_size(self, client):
        route = ROUTER.post(
            url("/project-rag/projects/p1/documents/duplicate-check")
        ).respond(200, json={"duplicate": False})

        await client.project_rag_check_duplicate(
            project_id="p1", content_hash="H", filename="f.txt", size=5
        )

        assert json.loads(route.calls.last.request.content) == {
            "content_hash": "h",
            "filename": "f.txt",
            "size": 5,
        }

    async def test_project_reindex_all_fields(self, client):
        route = ROUTER.post(url("/project-rag/reindex")).respond(200, json={})

        await client.project_rag_reindex_all(
            chunk_size=100,
            chunk_overlap=5,
            chunking_strategy="s",
            owner_user_id="U",
            project_id="p",
            embedding_model="embed/M",
            embedding_provider="gw",
        )

        assert json.loads(route.calls.last.request.content) == {
            "chunk_size": 100,
            "chunk_overlap": 5,
            "chunking_strategy": "s",
            "owner_user_id": "u",
            "project_id": "p",
            "embedding_model": "embed/M",
            "embedding_provider": "gw",
        }


class TestReindexStatus:
    async def test_non_dict_payload_and_failure_count_as_not_reindexing(self, client):
        ROUTER.get(url("/kb/reindex/status")).respond(200, json=["unexpected"])
        ROUTER.get(url("/memory-rag/reindex/status")).respond(200, json={"reindexing": True})
        ROUTER.get(url("/project-rag/reindex/status")).respond(500, json={"detail": "x"})

        status = await client.get_reindex_status()

        assert status == {
            "memory": {"reindexing": True},
            "project": {"reindexing": False},
            "kb": {"reindexing": False},
            "any_reindexing": True,
        }


class _Pipeline:
    """Минимальная подмена окружения поиска (см. FakePipeline в test_rag_client)."""

    def __init__(self, monkeypatch, rag=None):
        user_rag = {"rag_reranking_enabled": False, "rag_rerank_top_n": 0, **(rag or {})}

        async def process(text, **kw):
            return ProcessedQuery(text, text, text, None, None, None)

        async def judge(q, hits):
            return hits

        async def rewrite(q):
            return None

        monkeypatch.setattr("backend.rag_query.pipeline.process_user_query", process)
        monkeypatch.setattr("backend.rag_query.pipeline.rewrite_query_with_context", rewrite)
        monkeypatch.setattr(rc, "judge_and_filter_hits", judge)
        monkeypatch.setattr(
            "backend.services.user_rag_settings.get_runtime_rag_settings",
            lambda scope=None: user_rag,
        )
        monkeypatch.setattr(
            "backend.services.user_rag_settings.runtime_rag_strategy", lambda scope=None: "hybrid"
        )
        monkeypatch.setattr(
            "backend.services.user_rag_settings.runtime_embedding_fields", lambda scope=None: {}
        )
        monkeypatch.setattr(
            "backend.services.user_rag_settings.runtime_reranker_fields", lambda scope=None: {}
        )
        monkeypatch.setattr("backend.rag_query.semantic_cache.semantic_cache_enabled", lambda: False)


HIT = {"content": "Текст", "score": 0.9, "document_id": 1, "chunk_index": 0, "cosine": 0.8}


class TestSearchEdges:
    async def test_raw_cosine_with_document_ids(self, client, monkeypatch):
        _Pipeline(monkeypatch)
        monkeypatch.setattr(
            "backend.services.user_rag_settings.runtime_rag_strategy",
            lambda scope=None: "raw_cosine",
        )
        route = ROUTER.post(url("/kb/search")).respond(200, json={"hits": [HIT]})

        await client.kb_search("q", k=2, document_ids=["3", 4])

        assert json.loads(route.calls.last.request.content)["document_ids"] == [3, 4]

    async def test_regular_search_with_document_id(self, client, monkeypatch):
        _Pipeline(monkeypatch)
        route = ROUTER.post(url("/kb/search")).respond(200, json={"hits": [HIT]})

        await client.kb_search("q", document_id=9)

        assert json.loads(route.calls.last.request.content)["document_id"] == 9

    async def test_garbage_rerank_top_n_means_no_cut(self, client, monkeypatch):
        _Pipeline(monkeypatch, rag={"rag_reranking_enabled": True, "rag_rerank_top_n": "many"})
        hits = [
            {**HIT, "document_id": i, "content": f"совершенно уникальный фрагмент номер {i} " * 3}
            for i in range(1, 4)
        ]
        ROUTER.post(url("/kb/search")).respond(200, json={"hits": hits})

        found = await client.kb_search("q", k=5)

        assert len(found) == 3

    async def test_rerank_top_n_cuts_to_min_of_n_and_k(self, client, monkeypatch):
        _Pipeline(monkeypatch, rag={"rag_reranking_enabled": True, "rag_rerank_top_n": 2})
        hits = [
            {**HIT, "document_id": i, "content": f"совершенно уникальный фрагмент номер {i} " * 3}
            for i in range(1, 5)
        ]
        ROUTER.post(url("/kb/search")).respond(200, json={"hits": hits})

        found = await client.kb_search("q", k=5)

        assert len(found) == 2

    async def test_garbage_rewrite_threshold_env_is_zero(self, client, monkeypatch):
        _Pipeline(monkeypatch)
        monkeypatch.setenv("RAG_REWRITE_MIN_COSINE", "abc")
        route = ROUTER.post(url("/kb/search")).respond(200, json={"hits": [{**HIT, "cosine": 0.01}]})

        found = await client.kb_search("q")

        # порог невалиден -> 0, проверка косинуса выключена, переспроса нет
        assert len(found) == 1
        assert len(route.calls) == 1
