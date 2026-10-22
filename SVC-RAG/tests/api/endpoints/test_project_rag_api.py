"""svc-rag /project-rag: загрузка, дубликаты, список, чанки, удаление, поиск и фоновый реиндекс проектов."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.endpoints import project_rag as prj_module
from app.services.document_duplicate import DUPLICATE_CODE
from app.services.reindex_queue import GLOBAL_KEY, ReindexQueue

from ._fakes import hit, make_client

pytestmark = pytest.mark.unit


@pytest.fixture
def client(make_app):
    return make_client(make_app((prj_module.router, "/project-rag")))


@pytest.fixture
def queue(monkeypatch):
    from app.services import reindex_queue

    q = ReindexQueue("project")
    monkeypatch.setattr(reindex_queue, "project_queue", q)
    return q


class TestUpload:
    async def test_success_returns_project_id_and_passes_fields(self, client, prj_svc):
        r = await client.post(
            "/project-rag/projects/p1/documents",
            files={"file": ("a.txt", b"data", "text/plain")},
            data={"owner_user_id": "u", "uploaded_by": "w", "chunking_strategy": "semantic", "chunk_size": "100"},
        )

        assert r.status_code == 200
        assert r.json()["project_id"] == "p1" and r.json()["document_id"] == 7
        _, data, filename, kw = prj_svc.last("index_document")
        assert (data, filename) == (b"data", "a.txt")
        assert kw["project_id"] == "p1" and kw["owner_user_id"] == "u" and kw["uploaded_by"] == "w"
        # В отличие от kb/memory, стратегия проекта по умолчанию не подставляется - решает сервис
        assert kw["chunking_strategy"] == "semantic" and kw["chunk_size"] == 100

    async def test_strategy_is_not_defaulted_here(self, client, prj_svc):
        await client.post("/project-rag/projects/p1/documents", files={"file": ("a.txt", b"d", "text/plain")})

        assert prj_svc.last("index_document")[3]["chunking_strategy"] is None

    async def test_empty_file_is_400(self, client, prj_svc):
        r = await client.post("/project-rag/projects/p1/documents", files={"file": ("a.txt", b"", "text/plain")})

        assert r.status_code == 400 and not prj_svc.calls

    async def test_handler_rejects_empty_filename(self, prj_svc):
        with pytest.raises(HTTPException) as exc:
            await prj_module.project_rag_upload("p1", file=SimpleNamespace(filename="", read=None), svc=prj_svc)

        assert exc.value.status_code == 400 and exc.value.detail == "Нужно имя файла"

    async def test_duplicate_is_409(self, client, prj_svc):
        prj_svc.index_result = {"ok": False, "code": DUPLICATE_CODE, "document_id": 9}

        r = await client.post("/project-rag/projects/p1/documents", files={"file": ("d.txt", b"x", "text/plain")})

        assert r.status_code == 409
        assert r.json()["detail"]["document_id"] == 9 and r.json()["detail"]["filename"] == "d.txt"

    async def test_error_is_422_default_and_custom(self, client, prj_svc):
        prj_svc.index_result = {"ok": False}
        r = await client.post("/project-rag/projects/p1/documents", files={"file": ("d.txt", b"x", "text/plain")})
        assert r.status_code == 422 and r.json()["detail"] == "Ошибка индексации"

        prj_svc.index_result = {"ok": False, "error": "нет текста"}
        r = await client.post("/project-rag/projects/p1/documents", files={"file": ("d.txt", b"x", "text/plain")})
        assert r.json()["detail"] == "нет текста"


class TestDuplicateAndList:
    async def test_duplicate_check_passes_project(self, client, prj_svc):
        prj_svc.duplicate_result = {"duplicate": True, "document_id": 5, "filename": "x"}

        r = await client.post("/project-rag/projects/p7/documents/duplicate-check", json={"content_hash": "h"})

        assert r.json() == {"duplicate": True, "document_id": 5, "filename": "x"}
        assert prj_svc.last("check_duplicate")[1] == {"project_id": "p7", "content_hash": "h", "filename": None, "size": None}

    async def test_list_documents(self, client, prj_svc):
        prj_svc.documents = [{"id": 1, "filename": "a", "project_id": "p1", "size": 2}]

        r = await client.get("/project-rag/projects/p1/documents")

        assert r.json() == [{"id": 1, "filename": "a", "project_id": "p1", "created_at": None, "size": 2, "file_type": None}]
        assert prj_svc.last("list_documents") == ("list_documents", ("p1",))


class TestChunks:
    async def test_returns_chunks_and_passes_paging(self, client, prj_svc):
        prj_svc.chunks_result = [("первый", 4, 0), ("второй", 4, 1)]

        r = await client.get("/project-rag/projects/p1/documents/4/chunks?start=1&limit=2")

        assert r.json() == {"chunks": [
            {"content": "первый", "document_id": 4, "chunk_index": 0},
            {"content": "второй", "document_id": 4, "chunk_index": 1},
        ]}
        assert prj_svc.last("get_document_chunks") == ("get_document_chunks", "p1", 4, {"start": 1, "limit": 2})

    async def test_missing_document_is_404(self, client, prj_svc):
        prj_svc.chunks_result = None

        r = await client.get("/project-rag/projects/p1/documents/4/chunks")

        assert r.status_code == 404

    @pytest.mark.parametrize("query", ["start=-1", "limit=0", "limit=11"])
    async def test_paging_bounds_are_validated(self, client, prj_svc, query):
        r = await client.get(f"/project-rag/projects/p1/documents/4/chunks?{query}")

        assert r.status_code == 422 and not prj_svc.calls


class TestDelete:
    async def test_delete_document_ok(self, client, prj_svc):
        r = await client.delete("/project-rag/projects/p1/documents/4")

        assert r.status_code == 200 and r.json() == {"ok": True}

    async def test_delete_document_missing_is_404(self, client, prj_svc):
        prj_svc.delete_result = {"ok": False}

        assert (await client.delete("/project-rag/projects/p1/documents/4")).status_code == 404

    async def test_delete_document_must_receive_project_id(self, client, prj_svc):
        """BUG-RAG-16: project_id из пути передаётся в сервис для проверки владения."""
        await client.delete("/project-rag/projects/p-foreign/documents/4")

        call = prj_svc.last("delete_document")
        assert call == ("delete_document", 4, "p-foreign")

    async def test_delete_document_of_other_project_is_404(self, client, prj_svc):
        prj_svc.delete_result = {"ok": False, "error": "not_found"}

        r = await client.delete("/project-rag/projects/p-foreign/documents/4")

        assert r.status_code == 404

    async def test_delete_project(self, client, prj_svc):
        r = await client.delete("/project-rag/projects/p1")

        assert r.json() == {"ok": True, "deleted": 2}
        assert prj_svc.last("delete_by_project") == ("delete_by_project", "p1")


class TestSearch:
    async def test_hits_trace_and_params(self, client, prj_svc):
        prj_svc.search_hits = [hit("c", 0.3, 2, 5, cosine=0.2)]

        body = (await client.post("/project-rag/projects/p1/search",
                                  json={"query": "q", "debug_trace": True, "document_id": 2, "reranker_model": "rr"})).json()

        assert body["hits"] == [{"content": "c", "score": 0.3, "document_id": 2, "chunk_index": 5, "cosine": 0.2}]
        assert body["trace"] == {"store": "fake", "steps": ["vector"]}
        kw = prj_svc.last("search")[1]
        assert kw["project_id"] == "p1" and kw["document_id"] == 2 and kw["rerank_model"] == "rr"

    async def test_no_trace_by_default(self, client, prj_svc):
        assert (await client.post("/project-rag/projects/p1/search", json={"query": "q"})).json()["trace"] is None

    async def test_blocked_during_cluster_reindex(self, client, prj_svc, queue):
        queue._active[GLOBAL_KEY] = {"key": GLOBAL_KEY}

        r = await client.post("/project-rag/projects/p1/search", json={"query": "q"})

        assert r.status_code == 409 and not prj_svc.calls

    async def test_flag_disables_block(self, client, prj_svc, queue, monkeypatch):
        queue._active[GLOBAL_KEY] = {"key": GLOBAL_KEY}
        monkeypatch.setenv("RAG_BLOCK_SEARCH_DURING_REINDEX", "0")

        assert (await client.post("/project-rag/projects/p1/search", json={"query": "q"})).status_code == 200

    async def test_scoped_reindex_does_not_block(self, client, prj_svc, queue):
        queue._active["project:p2"] = {"key": "project:p2"}

        assert (await client.post("/project-rag/projects/p1/search", json={"query": "q"})).status_code == 200

    @pytest.mark.parametrize("value,expected", [("true", True), ("YES", True), ("no", False), ("", False)])
    def test_block_flag_parsing(self, monkeypatch, value, expected):
        monkeypatch.setenv("RAG_BLOCK_SEARCH_DURING_REINDEX", value)

        assert prj_module._block_search_on_reindex() is expected


class TestReindexKey:
    def test_project_priority(self):
        assert prj_module._project_reindex_key("Bob", " p1 ") == "project:p1"

    def test_owner_and_global(self):
        assert prj_module._project_reindex_key(" Bob ", None) == "owner:bob"
        assert prj_module._project_reindex_key(None, "  ") == GLOBAL_KEY


class TestReindexEndpoint:
    async def test_schedules_job_and_returns_key(self, client, prj_svc, queue, monkeypatch):
        calls = []

        async def fake_bg(*args):
            calls.append(args)

        monkeypatch.setattr(prj_module, "_project_reindex_all_bg", fake_bg)

        r = await client.post("/project-rag/reindex", json={"project_id": "p9", "chunk_size": 111})

        assert r.json() == {"ok": True, "status": "started", "owner_user_id": None, "project_id": "p9",
                            "queue_key": "project:p9"}
        assert calls[0][0] is prj_svc and calls[0][1] == 111 and calls[0][-1] == "p9"

    async def test_status_with_single_project_active(self, client, queue):
        queue._active["project:p9"] = {"key": "project:p9", "scope": "project", "entity_id": "p9"}

        body = (await client.get("/project-rag/reindex/status")).json()

        assert body["reindexing"] is True and body["project_id"] == "p9"

    async def test_status_with_owner_scope_has_no_project_id(self, client, queue):
        queue._active["owner:bob"] = {"key": "owner:bob", "scope": "owner", "entity_id": None}

        assert (await client.get("/project-rag/reindex/status")).json()["project_id"] is None

    async def test_status_idle(self, client, queue):
        body = (await client.get("/project-rag/reindex/status")).json()

        assert body["reindexing"] is False and body["project_id"] is None and body["active"] == []


class TestReindexBackground:
    async def test_single_project_job(self, prj_svc, queue):
        seen = {}

        async def reindex_all(pid, **kw):
            seen["pid"] = pid
            seen["active"] = queue.active()
            seen.update(kw)
            return {"ok": True}

        prj_svc.reindex_all = reindex_all

        await prj_module._project_reindex_all_bg(prj_svc, 300, 30, "universal", None, "m", "p", " p5 ")

        assert seen["pid"] == "p5" and seen["generation_key"] == "project:p5" and seen["generation"] == 1
        assert seen["model"] == "m" and seen["provider"] == "p"
        assert seen["active"][0]["scope"] == "project" and seen["active"][0]["entity_id"] == "p5"
        assert queue.active() == []

    async def test_owner_job_uses_all_projects_with_owner_filter(self, prj_svc, queue):
        await prj_module._project_reindex_all_bg(prj_svc, None, None, None, owner_user_id="Bob")

        call = prj_svc.last("reindex_all_projects")[1]
        assert call["owner_user_id"] == "Bob" and call["generation_key"] == "owner:bob"

    async def test_global_job_scope_all(self, prj_svc, queue):
        seen = {}

        async def reindex_all_projects(**kw):
            seen["active"] = queue.active()
            return {}

        prj_svc.reindex_all_projects = reindex_all_projects

        await prj_module._project_reindex_all_bg(prj_svc, None, None, None)

        assert seen["active"][0]["scope"] == "all" and seen["active"][0]["key"] == GLOBAL_KEY
