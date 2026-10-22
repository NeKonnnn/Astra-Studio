"""svc-rag /kb: загрузка, дубликаты, список, удаление, поиск (с блокировкой на реиндексе), фоновый реиндекс."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.endpoints import kb as kb_module
from app.services.document_duplicate import DUPLICATE_CODE, DUPLICATE_MESSAGE
from app.services.reindex_queue import GLOBAL_KEY, ReindexQueue

from ._fakes import hit, make_client

pytestmark = pytest.mark.unit


@pytest.fixture
def client(make_app):
    return make_client(make_app((kb_module.router, "/kb")))


@pytest.fixture
def queue(monkeypatch):
    """Чистая очередь вместо глобальной, чтобы тесты не делили состояние."""
    from app.services import reindex_queue

    q = ReindexQueue("kb")
    monkeypatch.setattr(reindex_queue, "kb_queue", q)
    return q


class TestUpload:
    async def test_success_passes_form_fields_to_service(self, client, kb_svc):
        r = await client.post(
            "/kb/documents",
            files={"file": ("a.txt", b"hello", "text/plain")},
            data={"chunk_size": "500", "chunk_overlap": "50", "agent_id": "9",
                  "owner_user_id": "u1", "embedding_model": "m", "embedding_provider": "p"},
        )

        assert r.status_code == 200
        assert r.json() == {"ok": True, "document_id": 7, "filename": "a.txt", "chunks_count": 3, "error": None}
        _, data, filename, kw = kb_svc.last("index_document")
        assert (data, filename) == (b"hello", "a.txt")
        assert kw["chunk_size"] == 500 and kw["chunk_overlap"] == 50 and kw["agent_id"] == 9
        assert kw["owner_user_id"] == "u1" and kw["model"] == "m" and kw["provider"] == "p"
        # стратегия по умолчанию — universal
        assert kw["chunking_strategy"] == "universal"

    async def test_empty_file_is_400(self, client, kb_svc):
        r = await client.post("/kb/documents", files={"file": ("a.txt", b"", "text/plain")})

        assert r.status_code == 400 and r.json()["detail"] == "Файл пустой"
        assert not kb_svc.calls

    async def test_duplicate_is_409_with_structured_detail(self, client, kb_svc):
        kb_svc.index_result = {"ok": False, "code": DUPLICATE_CODE, "document_id": 4, "filename": None, "error": None}

        r = await client.post("/kb/documents", files={"file": ("dup.txt", b"x", "text/plain")})

        assert r.status_code == 409
        assert r.json()["detail"] == {
            "code": DUPLICATE_CODE, "message": DUPLICATE_MESSAGE, "document_id": 4, "filename": "dup.txt",
        }

    async def test_indexing_error_is_422_with_service_message(self, client, kb_svc):
        kb_svc.index_result = {"ok": False, "error": "не удалось распарсить"}

        r = await client.post("/kb/documents", files={"file": ("a.pdf", b"x", "application/pdf")})

        assert r.status_code == 422 and r.json()["detail"] == "не удалось распарсить"

    async def test_indexing_error_without_message_has_default_text(self, client, kb_svc):
        kb_svc.index_result = {"ok": False}

        r = await client.post("/kb/documents", files={"file": ("a.pdf", b"x", "application/pdf")})

        assert r.status_code == 422 and r.json()["detail"] == "Ошибка индексации"

    async def test_handler_rejects_empty_filename_with_400(self, kb_svc):
        # Пустое имя multipart-парсер отсекает раньше обработчика, поэтому зовём его напрямую.
        upload = SimpleNamespace(filename="", read=None)

        with pytest.raises(HTTPException) as exc:
            await kb_module.kb_index_document(file=upload, kb=kb_svc)

        assert exc.value.status_code == 400 and exc.value.detail == "Нужно имя файла"
        assert not kb_svc.calls


class TestDuplicateAndList:
    async def test_duplicate_check_maps_service_answer(self, client, kb_svc):
        kb_svc.duplicate_result = {"duplicate": 1, "document_id": 3, "filename": "x.txt"}

        r = await client.post("/kb/documents/duplicate-check", json={"content_hash": "abc", "agent_id": 2, "size": 10})

        assert r.json() == {"duplicate": True, "document_id": 3, "filename": "x.txt"}
        assert kb_svc.last("check_duplicate")[1] == {
            "content_hash": "abc",
            "filename": None,
            "size": 10,
            "agent_id": 2,
            "knowledge_base_id": None,
        }

    async def test_list_documents_keeps_agent_metadata(self, client, kb_svc):
        kb_svc.documents = [
            {"id": 1, "filename": "a.txt", "created_at": "2025-01-01", "size": 5, "file_type": "txt", "metadata": {"agent_id": 3}},
            {"id": 2, "filename": "b.txt"},
        ]

        body = (await client.get("/kb/documents")).json()

        assert body[0]["metadata"] == {"agent_id": 3} and body[0]["file_type"] == "txt"
        assert body[1] == {"id": 2, "filename": "b.txt", "created_at": None, "size": None, "file_type": None, "metadata": None}


class TestDelete:
    async def test_delete_ok(self, client, kb_svc):
        r = await client.delete("/kb/documents/5")

        assert r.status_code == 200 and r.json() == {"ok": True}
        assert kb_svc.last("delete_document") == ("delete_document", 5)

    async def test_delete_missing_is_404(self, client, kb_svc):
        kb_svc.delete_result = {"ok": False}

        r = await client.delete("/kb/documents/5")

        assert r.status_code == 404


class TestSearch:
    async def test_returns_hits_with_cosine_and_no_trace_by_default(self, client, kb_svc):
        kb_svc.search_hits = [hit("a", 0.9, 1, 0, cosine=0.8), ("b", 0.4, 2, 1)]

        r = await client.post("/kb/search", json={"query": "q", "k": 2})

        body = r.json()
        assert body["trace"] is None
        assert body["hits"][0] == {"content": "a", "score": 0.9, "document_id": 1, "chunk_index": 0, "cosine": 0.8}
        # Обычный кортеж без cosine -> None
        assert body["hits"][1]["cosine"] is None

    async def test_passes_filters_eval_and_models_and_returns_trace(self, client, kb_svc):
        r = await client.post("/kb/search", json={
            "query": "q", "document_ids": [1, 2], "debug_trace": True, "strategy": "hybrid",
            "filters": {"filename_contains": "отчёт"},
            "eval_gold_chunks": [{"document_id": 1, "chunk_index": 2}],
            "embedding_model": "m", "reranker_provider": "rp",
        })

        assert r.json()["trace"] == {"store": "fake", "steps": ["vector"]}
        kw = kb_svc.last("search")[1]
        assert kw["document_ids"] == [1, 2] and kw["strategy"] == "hybrid"
        assert kw["filters"].filename_contains == "отчёт"
        assert kw["eval_gold_chunks"] == [(1, 2)] and kw["model"] == "m" and kw["rerank_provider"] == "rp"
        assert kw["return_trace"] is True

    async def test_search_blocked_during_cluster_reindex(self, client, kb_svc, queue):
        queue._active[GLOBAL_KEY] = {"key": GLOBAL_KEY}

        r = await client.post("/kb/search", json={"query": "q"})

        assert r.status_code == 409
        assert "переиндексация" in r.json()["detail"]
        assert not kb_svc.calls

    async def test_search_not_blocked_by_scoped_reindex(self, client, kb_svc, queue):
        queue._active["agent:1"] = {"key": "agent:1"}

        r = await client.post("/kb/search", json={"query": "q"})

        assert r.status_code == 200

    async def test_block_can_be_disabled_by_env(self, client, kb_svc, queue, monkeypatch):
        queue._active[GLOBAL_KEY] = {"key": GLOBAL_KEY}
        monkeypatch.setenv("RAG_BLOCK_SEARCH_DURING_REINDEX", "false")

        r = await client.post("/kb/search", json={"query": "q"})

        assert r.status_code == 200

    @pytest.mark.parametrize("value,expected", [
        ("1", True), ("TRUE", True), (" yes ", True), ("on", True), ("0", False), ("off", False), ("", False),
    ])
    def test_block_flag_parsing(self, monkeypatch, value, expected):
        monkeypatch.setenv("RAG_BLOCK_SEARCH_DURING_REINDEX", value)

        assert kb_module._block_search_on_reindex() is expected


class TestReindexKey:
    def test_agent_has_priority_over_owner(self):
        assert kb_module._kb_reindex_key("Bob", 5) == "agent:5"

    def test_owner_is_lowercased_and_stripped(self):
        assert kb_module._kb_reindex_key("  Bob ", None) == "owner:bob"

    def test_without_filters_is_global(self):
        assert kb_module._kb_reindex_key(None, None) == GLOBAL_KEY
        assert kb_module._kb_reindex_key("   ", None) == GLOBAL_KEY


class TestReindexEndpoint:
    async def test_schedules_background_task_and_returns_queue_key(self, client, kb_svc, queue, monkeypatch):
        calls = []

        async def fake_bg(*args):
            calls.append(args)

        monkeypatch.setattr(kb_module, "_kb_reindex_bg", fake_bg)

        r = await client.post("/kb/reindex", json={"agent_id": 4, "chunk_size": 300, "document_ids": [1]})

        assert r.json() == {"ok": True, "status": "started", "owner_user_id": None, "agent_id": 4,
                            "document_ids": [1], "queue_key": "agent:4"}
        # ASGI-транспорт выполняет BackgroundTasks до завершения ответа
        assert calls and calls[0][0] is kb_svc and calls[0][1] == 300 and calls[0][-1] == 4

    async def test_status_reflects_queue(self, client, queue):
        queue._active["agent:1"] = {"key": "agent:1", "owner_user_id": "bob"}

        body = (await client.get("/kb/reindex/status")).json()

        assert body["reindexing"] is True and body["owner_user_id"] == "bob"
        assert body["active"][0]["key"] == "agent:1"


class TestReindexBackground:
    async def test_agent_job_runs_service_with_generation_and_registers_meta(self, kb_svc, queue):
        seen = {}

        async def reindex_all(**kw):
            seen["active"] = queue.active()
            seen.update(kw)
            return {"ok": True}

        kb_svc.reindex_all = reindex_all

        await kb_module._kb_reindex_bg(kb_svc, 300, 30, "universal", None, [1, 2], "m", "p", agent_id=5)

        assert seen["generation_key"] == "agent:5" and seen["generation"] == 1
        assert seen["document_ids"] == [1, 2] and seen["model"] == "m" and seen["provider"] == "p"
        assert seen["active"][0]["scope"] == "agent" and seen["active"][0]["entity_id"] == "5"
        # После завершения слот освобождён
        assert queue.active() == []

    async def test_owner_scope_meta(self, kb_svc, queue):
        seen = {}

        async def reindex_all(**kw):
            seen["active"] = queue.active()
            return {}

        kb_svc.reindex_all = reindex_all

        await kb_module._kb_reindex_bg(kb_svc, None, None, None, owner_user_id="Bob")

        assert seen["active"][0]["scope"] == "owner" and seen["active"][0]["owner_user_id"] == "bob"

    async def test_global_scope_and_service_error_is_swallowed(self, kb_svc, queue):
        kb_svc.reindex_error = RuntimeError("boom")

        await kb_module._kb_reindex_bg(kb_svc, None, None, None)

        # исключение глотает очередь (логирует), фоновая задача не падает
        assert kb_svc.last("reindex_all")[2]["generation_key"] == GLOBAL_KEY
        assert queue.active() == []
