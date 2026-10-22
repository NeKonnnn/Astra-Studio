"""svc-rag /memory-rag: загрузка, дубликаты, список, удаление, поиск и фоновый реиндекс Библиотеки."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.endpoints import memory_rag as mem_module
from app.services import memory_rag_service
from app.services.document_duplicate import DUPLICATE_CODE, DUPLICATE_MESSAGE

from ._fakes import hit, make_client

pytestmark = pytest.mark.unit


@pytest.fixture
def client(make_app):
    return make_client(make_app((mem_module.router, "/memory-rag")))


@pytest.fixture(autouse=True)
def fresh_lock(monkeypatch):
    """Свой лок на тест: модульный asyncio.Lock не должен делиться между event loop'ами."""
    lock = asyncio.Lock()
    monkeypatch.setattr(mem_module, "_memory_reindex_lock", lock)
    return lock


@pytest.fixture(autouse=True)
def fresh_generation(monkeypatch):
    monkeypatch.setattr(memory_rag_service, "_memory_reindex_generation", 0)


class TestUpload:
    async def test_success_passes_fields_and_defaults_strategy(self, client, mem_svc):
        r = await client.post(
            "/memory-rag/documents",
            files={"file": ("lib.txt", b"data", "text/plain")},
            data={"chunk_size": "400", "minio_object": "obj", "minio_bucket": "b", "embedding_model": "m"},
        )

        assert r.status_code == 200
        assert r.json()["document_id"] == 7 and r.json()["chunks_count"] == 3
        _, data, filename, kw = mem_svc.last("index_document")
        assert (data, filename) == (b"data", "lib.txt")
        assert kw["chunk_size"] == 400 and kw["minio_object"] == "obj" and kw["minio_bucket"] == "b"
        assert kw["model"] == "m" and kw["chunking_strategy"] == "universal"

    async def test_empty_file_is_400(self, client, mem_svc):
        r = await client.post("/memory-rag/documents", files={"file": ("a.txt", b"", "text/plain")})

        assert r.status_code == 400 and r.json()["detail"] == "Файл пустой"
        assert not mem_svc.calls

    async def test_handler_rejects_empty_filename(self, mem_svc):
        with pytest.raises(HTTPException) as exc:
            await mem_module.index_memory_rag_document(file=SimpleNamespace(filename="", read=None), svc=mem_svc)

        assert exc.value.status_code == 400 and exc.value.detail == "Нужно имя файла"

    async def test_duplicate_is_409(self, client, mem_svc):
        mem_svc.index_result = {"ok": False, "code": DUPLICATE_CODE, "document_id": 2, "filename": "same.txt"}

        r = await client.post("/memory-rag/documents", files={"file": ("x.txt", b"x", "text/plain")})

        assert r.status_code == 409
        assert r.json()["detail"] == {"code": DUPLICATE_CODE, "message": DUPLICATE_MESSAGE,
                                      "document_id": 2, "filename": "same.txt"}

    async def test_error_is_422(self, client, mem_svc):
        mem_svc.index_result = {"ok": False, "error": "битый файл"}

        r = await client.post("/memory-rag/documents", files={"file": ("x.txt", b"x", "text/plain")})

        assert r.status_code == 422 and r.json()["detail"] == "битый файл"

    async def test_error_without_text_has_default(self, client, mem_svc):
        mem_svc.index_result = {"ok": False}

        r = await client.post("/memory-rag/documents", files={"file": ("x.txt", b"x", "text/plain")})

        assert r.status_code == 422 and r.json()["detail"] == "Ошибка индексации"


class TestDuplicateListDelete:
    async def test_duplicate_check(self, client, mem_svc):
        mem_svc.duplicate_result = {"duplicate": True, "document_id": 1, "filename": "a"}

        r = await client.post("/memory-rag/documents/duplicate-check", json={"content_hash": "h", "size": 3})

        assert r.json() == {"duplicate": True, "document_id": 1, "filename": "a"}
        assert mem_svc.last("check_duplicate")[1] == {"content_hash": "h", "filename": None, "size": 3}

    async def test_list(self, client, mem_svc):
        mem_svc.documents = [{"id": 1, "filename": "a.txt", "size": 4, "file_type": "txt", "created_at": "t"}]

        r = await client.get("/memory-rag/documents")

        assert r.json() == [{"id": 1, "filename": "a.txt", "created_at": "t", "size": 4, "file_type": "txt"}]

    async def test_delete_ok_and_404(self, client, mem_svc):
        assert (await client.delete("/memory-rag/documents/3")).json() == {"ok": True}
        mem_svc.delete_result = {"ok": False}

        r = await client.delete("/memory-rag/documents/3")

        assert r.status_code == 404 and r.json()["detail"] == "Документ не найден"


class TestSearch:
    async def test_hits_and_trace(self, client, mem_svc):
        mem_svc.search_hits = [hit("x", 0.7, 3, 1, cosine=0.6)]

        body = (await client.post("/memory-rag/search", json={"query": "q", "debug_trace": True, "document_id": 3})).json()

        assert body["hits"] == [{"content": "x", "score": 0.7, "document_id": 3, "chunk_index": 1, "cosine": 0.6}]
        assert body["trace"] == {"store": "fake", "steps": ["vector"]}
        kw = mem_svc.last("search")[1]
        assert kw["document_id"] == 3 and kw["return_trace"] is True

    async def test_no_trace_by_default(self, client, mem_svc):
        body = (await client.post("/memory-rag/search", json={"query": "q"})).json()

        assert body == {"hits": [], "trace": None}

    async def test_search_blocked_while_reindexing(self, client, mem_svc, fresh_lock):
        await fresh_lock.acquire()

        r = await client.post("/memory-rag/search", json={"query": "q"})

        assert r.status_code == 409
        assert not mem_svc.calls

    async def test_search_respects_block_search_env_flag(self, client, mem_svc, fresh_lock, monkeypatch):
        monkeypatch.setenv("RAG_BLOCK_SEARCH_DURING_REINDEX", "false")
        await fresh_lock.acquire()

        r = await client.post("/memory-rag/search", json={"query": "q"})

        assert r.status_code == 200


class TestReindex:
    async def test_endpoint_schedules_background_job(self, client, mem_svc, monkeypatch):
        calls = []

        async def fake_bg(*args):
            calls.append(args)

        monkeypatch.setattr(mem_module, "_memory_reindex_bg", fake_bg)

        r = await client.post("/memory-rag/reindex", json={"chunk_size": 200, "embedding_provider": "p"})

        assert r.json() == {"ok": True, "status": "started"}
        assert calls == [(mem_svc, 200, None, None, None, "p")]

    async def test_status_follows_lock(self, client, fresh_lock):
        assert (await client.get("/memory-rag/reindex/status")).json() == {"reindexing": False}
        await fresh_lock.acquire()

        assert (await client.get("/memory-rag/reindex/status")).json() == {"reindexing": True}

    async def test_bg_job_calls_service_under_lock_with_generation(self, mem_svc, fresh_lock):
        seen = {}

        async def reindex_all(**kw):
            seen["locked"] = fresh_lock.locked()
            seen.update(kw)
            return {"ok": True}

        mem_svc.reindex_all = reindex_all

        await mem_module._memory_reindex_bg(mem_svc, 300, 30, "universal", "m", "p")

        assert seen["locked"] is True and seen["generation"] == 1
        assert (seen["chunk_size"], seen["chunk_overlap"], seen["model"], seen["provider"]) == (300, 30, "m", "p")
        assert not fresh_lock.locked()

    async def test_bg_job_skipped_when_generation_became_stale(self, mem_svc, fresh_lock):
        # Пока первая задача ждёт лок, вторая поднимает поколение - первая должна пропуститься.
        await fresh_lock.acquire()
        first = asyncio.create_task(mem_module._memory_reindex_bg(mem_svc, None, None, None, None, None))
        await asyncio.sleep(0)
        memory_rag_service.bump_memory_reindex_generation()
        fresh_lock.release()

        await first

        assert not [c for c in mem_svc.calls if c[0] == "reindex_all"]

    async def test_bg_job_swallows_service_errors(self, mem_svc):
        mem_svc.reindex_error = RuntimeError("упало")

        await mem_module._memory_reindex_bg(mem_svc, None, None, None, None, None)

        assert mem_svc.last("reindex_all")
