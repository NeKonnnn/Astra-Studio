"""svc-rag /diag/search и /diag/document: диагностика поиска и чанкинга на фейковых репозиториях.

Роутер diag в ``app.api.router`` не подключён (маршрутов /v1/diag/* в сервисе нет) - тестируем его
отдельным приложением; см. отчёт: кандидат на мёртвый код.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.api.endpoints import diag as diag_module
from app.api import router as api_router

from ._fakes import make_client

pytestmark = pytest.mark.unit


def dv(content="текст", doc=1, idx=0):
    return SimpleNamespace(content=content, document_id=doc, chunk_index=idx)


class FakeVectorRepo:
    def __init__(self):
        self.substring_hits: List[Any] = []
        self.similarity_hits: List[Any] = []
        self.keyword_hits: List[Any] = []
        self.vectors_by_doc: Dict[int, List[Any]] = {}
        self.errors: Dict[str, Exception] = {}
        self.calls: List[tuple] = []

    async def substring_search(self, tokens, **kw):
        self.calls.append(("substring_search", tokens, kw))
        if "substring" in self.errors:
            raise self.errors["substring"]
        return self.substring_hits

    async def similarity_search(self, **kw):
        self.calls.append(("similarity_search", kw))
        if "similarity" in self.errors:
            raise self.errors["similarity"]
        return self.similarity_hits

    async def keyword_search(self, q, **kw):
        self.calls.append(("keyword_search", q, kw))
        if "keyword" in self.errors:
            raise self.errors["keyword"]
        return self.keyword_hits

    async def get_vectors_by_document(self, did):
        self.calls.append(("get_vectors_by_document", did))
        err = self.errors.get(f"vectors:{did}")
        if err:
            raise err
        return self.vectors_by_doc.get(did, [])


class FakeDocRepo:
    def __init__(self):
        self.ids_by_name: Dict[str, List[int]] = {}
        self.docs: Dict[int, Any] = {}
        self.find_error: Exception | None = None
        self.get_error: Exception | None = None
        self.calls: List[tuple] = []

    async def find_document_ids_by_filename(self, name, **kw):
        self.calls.append(("find", name, kw))
        if self.find_error:
            raise self.find_error
        return self.ids_by_name.get(name, [])

    async def get_document(self, did):
        if self.get_error:
            raise self.get_error
        return self.docs.get(did)


class FakeRagClient:
    def __init__(self, emb=(0.1, 0.2)):
        self.emb = emb

    async def embed_single(self, q):
        return self.emb


@pytest.fixture
def stores(kb_svc, mem_svc, prj_svc):
    for svc in (kb_svc, mem_svc, prj_svc):
        svc.vector_repo = FakeVectorRepo()
        svc.doc_repo = FakeDocRepo()
    kb_svc.rag_client = FakeRagClient()
    return SimpleNamespace(kb=kb_svc, memory=mem_svc, project=prj_svc)


@pytest.fixture
def client(make_app, stores):
    return make_client(make_app((diag_module.router, "/diag")))


@pytest.fixture(autouse=True)
def english_reranker(monkeypatch):
    monkeypatch.setattr(diag_module, "reranker_is_english_only", lambda: True)


def test_diag_router_is_mounted_in_the_public_router():
    # /diag подключён к публичному router (с API-key auth), рядом с /memory-diag.
    paths = {getattr(r, "path", "") for r in api_router.routes}

    assert any("diag" in p and "memory-diag" not in p for p in paths)


class TestDiagSearch:
    async def test_empty_query_is_400(self, client):
        r = await client.post("/diag/search", json={"query": "   "})

        assert r.status_code == 400 and r.json()["detail"] == "query пустой"

    async def test_unknown_store_is_400(self, client):
        r = await client.post("/diag/search", json={"query": "что-то", "store": "foo"})

        assert r.status_code == 400 and "Неизвестный store" in r.json()["detail"]

    async def test_healthy_pipeline_for_kb(self, client, stores, monkeypatch):
        monkeypatch.setattr(diag_module, "reranker_is_english_only", lambda: False)
        repo = stores.kb.vector_repo
        repo.substring_hits = [(dv("Константин Иванов инженер"), 1.0)]
        repo.similarity_hits = [(dv("a", 1, 0), 0.8), (dv("b", 1, 1), 0.6)]
        repo.keyword_hits = [(dv("Константин", 1, 2), 0.3)]

        r = await client.post("/diag/search", json={"query": "кто такой Константин Иванов", "store": "KB"})

        body = r.json()
        assert r.status_code == 200 and body["store"] == "kb"
        assert body["query_analysis"]["entities"] == ["константин", "иванов"]
        assert body["reranker"]["english_only"] is False and body["reranker"]["note"] is None
        assert body["corpus"]["substring_hit_count"] == 1
        assert body["corpus"]["samples"][0]["content_preview"] == "Константин Иванов инженер"
        assert body["vector_search"]["top_scores"] == [0.8, 0.6]
        assert body["vector_search"]["interpretation"] is None
        assert body["keyword_fts"]["hit_count"] == 1 and body["keyword_fts"]["interpretation"] is None
        assert body["entity_ilike_fallback"]["used_as_safety_net"] is False
        assert body["suggestions"] == ["Пайплайн выглядит здоровым для этого запроса."]

    async def test_no_hits_anywhere_gives_corpus_suggestion(self, client):
        body = (await client.post("/diag/search", json={"query": "кто такой Константин"})).json()

        assert body["corpus"]["substring_hit_count"] == 0
        assert "0 чанков содержат" in body["corpus"]["interpretation"]
        assert any("НЕТ чанков" in s for s in body["suggestions"])
        assert any("англоязычный" in s for s in body["suggestions"])
        assert body["reranker"]["note"].startswith("reranker_is_english_only=true")

    async def test_fts_empty_but_ilike_found_is_flagged_as_tokenizer_problem(self, client, stores):
        stores.kb.vector_repo.substring_hits = [(dv("Константин"), 1.0)]

        body = (await client.post("/diag/search", json={"query": "Константин"})).json()

        assert body["keyword_fts"]["interpretation"].startswith("FTS не нашёл")
        assert body["entity_ilike_fallback"]["used_as_safety_net"] is True
        assert body["entity_ilike_fallback"]["count_if_used"] == 1
        assert any("FTS/tsvector пустой" in s for s in body["suggestions"])

    async def test_low_vector_scores_are_flagged(self, client, stores):
        stores.kb.vector_repo.similarity_hits = [(dv(), 0.1), (dv(), 0.2)]

        body = (await client.post("/diag/search", json={"query": "Константин"})).json()

        assert body["vector_search"]["interpretation"].startswith("Все скоры < 0.25")
        assert any("Векторные скоры очень низкие" in s for s in body["suggestions"])

    async def test_repo_errors_are_reported_per_stage_not_as_500(self, client, stores):
        repo = stores.kb.vector_repo
        repo.errors = {"substring": RuntimeError("ilike down"), "similarity": RuntimeError("vec down"),
                       "keyword": RuntimeError("fts down")}

        r = await client.post("/diag/search", json={"query": "Константин"})

        body = r.json()
        assert r.status_code == 200
        assert body["corpus"]["error"] == "ilike down" and body["corpus"]["substring_hit_count"] == 0
        assert body["vector_search"]["error"] == "vec down"
        assert body["keyword_fts"]["error"] == "fts down"

    async def test_unusable_query_for_fts_reports_reason(self, client, stores):
        # Из одних стоп-слов FTS-запрос не строится: стадия не вызывается, причина объяснена.
        body = (await client.post("/diag/search", json={"query": "и в на"})).json()

        assert body["keyword_fts"]["fts_or_query"] is None
        assert body["keyword_fts"]["error"].startswith("build_fts_or_query вернул None")
        assert not [c for c in stores.kb.vector_repo.calls if c[0] == "keyword_search"]

    async def test_missing_rag_client_reports_vector_error(self, client, stores):
        stores.kb.rag_client = None

        body = (await client.post("/diag/search", json={"query": "Константин"})).json()

        assert body["vector_search"]["error"] == "rag_client недоступен / не вернул эмбеддинг"

    async def test_project_store_passes_project_and_document_filters(self, client, stores):
        r = await client.post("/diag/search", json={
            "query": "Константин", "store": "project", "project_id": "p1", "document_id": 5, "sample_limit": 2,
        })

        assert r.status_code == 200
        calls = {c[0]: c for c in stores.project.vector_repo.calls}
        assert calls["substring_search"][2] == {"limit": 8, "project_id": "p1", "document_id": 5}
        assert calls["keyword_search"][2] == {"limit": 8, "project_id": "p1", "document_id": 5}

    async def test_memory_store_uses_memory_repo_but_kb_embedder(self, client, stores):
        stores.memory.vector_repo.similarity_hits = [(dv("m"), 0.9)]

        body = (await client.post("/diag/search", json={"query": "Константин", "store": "memory"})).json()

        assert body["vector_search"]["hit_count"] == 1
        assert not stores.kb.vector_repo.calls

    async def test_filename_anchor_resolved_with_chunks(self, client, stores):
        stores.kb.doc_repo.ids_by_name = {"отчёт.docx": [3]}
        stores.kb.vector_repo.vectors_by_doc = {3: [dv("x"), dv("y")]}

        body = (await client.post("/diag/search", json={"query": "саммари по отчёт.docx"})).json()

        anchor = body["filename_anchor"]
        assert anchor["filenames_detected"] == ["отчёт.docx"]
        assert anchor["resolved"] == [{"filename": "отчёт.docx", "document_ids": [3], "chunk_counts": {"3": 2}}]
        assert anchor["interpretation"].startswith("Документ(ы) найдены")

    async def test_filename_anchor_not_found(self, client, stores):
        body = (await client.post("/diag/search", json={"query": "саммари по отчёт.docx"})).json()

        assert body["filename_anchor"]["interpretation"].startswith("Имя файла в запросе не резолвится")
        assert any("не нашлось в БД" in s for s in body["suggestions"])

    async def test_filename_anchor_document_without_chunks(self, client, stores):
        stores.kb.doc_repo.ids_by_name = {"отчёт.docx": [3]}

        body = (await client.post("/diag/search", json={"query": "саммари по отчёт.docx"})).json()

        assert body["filename_anchor"]["interpretation"].startswith("Документ найден, но у него 0 чанков")
        assert any("0 чанков в _vectors" in s for s in body["suggestions"])

    async def test_filename_lookup_error_and_chunk_error_are_collected(self, client, stores):
        stores.kb.doc_repo.find_error = RuntimeError("lookup failed")
        body = (await client.post("/diag/search", json={"query": "саммари по отчёт.docx"})).json()
        assert body["filename_anchor"]["resolved"][0]["error"] == "lookup failed"

        stores.kb.doc_repo.find_error = None
        stores.kb.doc_repo.ids_by_name = {"отчёт.docx": [3]}
        stores.kb.vector_repo.errors = {"vectors:3": RuntimeError("no vectors")}
        body = (await client.post("/diag/search", json={"query": "саммари по отчёт.docx"})).json()

        resolved = body["filename_anchor"]["resolved"]
        assert resolved[0]["error"] == "get_vectors_by_document(3): no vectors"
        assert resolved[1]["chunk_counts"] == {"3": -1}

    async def test_project_filename_lookup_is_scoped_by_project(self, client, stores):
        stores.project.doc_repo.ids_by_name = {"отчёт.docx": [3]}
        stores.project.vector_repo.vectors_by_doc = {3: [dv()]}

        await client.post("/diag/search", json={"query": "саммари по отчёт.docx", "store": "project", "project_id": "px"})

        assert stores.project.doc_repo.calls == [("find", "отчёт.docx", {"project_id": "px"})]

    async def test_doc_repo_without_filename_lookup_is_skipped(self, client, stores):
        stores.kb.doc_repo = object()

        body = (await client.post("/diag/search", json={"query": "саммари по отчёт.docx"})).json()

        assert body["filename_anchor"]["resolved"] == []


class TestChunkVerdict:
    @pytest.mark.parametrize("count,lengths,short,empty,expected", [
        (0, {}, 0, 0, "КРИТИЧНО"),
        (2, {"avg": 500}, 0, 0, "ПОДОЗРИТЕЛЬНО"),
        (10, {"avg": 100}, 0, 0, "ПЛОХОЙ ЧАНКИНГ: средняя длина чанка 100"),
        (10, {"avg": 300}, 4, 4, "ПЛОХОЙ ЧАНКИНГ: 4 из 10"),
        (10, {"avg": 300}, 5, 0, "СОМНИТЕЛЬНО"),
        (10, {"avg": 300}, 1, 1, "OK: 10 чанков"),
    ])
    def test_verdicts(self, count, lengths, short, empty, expected):
        assert diag_module._chunk_verdict(count, lengths, short, empty).startswith(expected)


class TestDiagDocument:
    async def test_empty_filename_is_400(self, client):
        assert (await client.post("/diag/document", json={"filename": " "})).status_code == 400

    async def test_unknown_store_is_400(self, client):
        r = await client.post("/diag/document", json={"filename": "a", "store": "zzz"})

        assert r.status_code == 400 and "Неизвестный store" in r.json()["detail"]

    async def test_repo_without_filename_search_is_501(self, client, stores):
        stores.memory.doc_repo = object()

        assert (await client.post("/diag/document", json={"filename": "a"})).status_code == 501

    async def test_lookup_error_is_500(self, client, stores):
        stores.memory.doc_repo.find_error = RuntimeError("db")

        r = await client.post("/diag/document", json={"filename": "a"})

        assert r.status_code == 500 and "find_document_ids_by_filename: db" in r.json()["detail"]

    async def test_no_matches_has_overall_hint(self, client):
        body = (await client.post("/diag/document", json={"filename": "a"})).json()

        assert body["matches"] == [] and body["overall_suggestion"].startswith("Файлов с таким именем не найдено")

    async def test_good_document_stats_and_optional_chunks(self, client, stores):
        stores.memory.doc_repo.ids_by_name = {"a": [1]}
        stores.memory.doc_repo.docs = {1: {"filename": "a.docx"}}
        stores.memory.vector_repo.vectors_by_doc = {1: [
            dv("б" * 300, 1, 2), dv("а" * 400, 1, 0), dv("в" * 500, 1, 1),
        ]}

        body = (await client.post("/diag/document", json={"filename": "a", "show_chunks": True, "chunk_preview_len": 10})).json()

        entry = body["matches"][0]
        assert entry["filename"] == "a.docx" and entry["chunk_count"] == 3 and entry["total_chars"] == 1200
        assert entry["lengths"] == {"min": 300, "max": 500, "avg": 400, "median": 400}
        assert entry["verdict"].startswith("OK: 3 чанков")
        assert body["overall_suggestion"] is None
        # чанки отсортированы по индексу, превью обрезано до max(30, len)
        assert [c["chunk_index"] for c in entry["chunks"]] == [0, 1, 2]
        assert entry["chunks"][0]["content_preview"] == "а" * 30 + "…"

    async def test_chunks_hidden_by_default(self, client, stores):
        stores.memory.doc_repo.ids_by_name = {"a": [1]}
        stores.memory.vector_repo.vectors_by_doc = {1: [dv("x" * 300)] * 3}

        assert (await client.post("/diag/document", json={"filename": "a"})).json()["matches"][0]["chunks"] is None

    async def test_bad_chunking_gives_overall_suggestion(self, client, stores):
        stores.kb.doc_repo.ids_by_name = {"a": [1]}
        stores.kb.vector_repo.vectors_by_doc = {1: [dv("x" * 10)] * 5}

        body = (await client.post("/diag/document", json={"filename": "a", "store": "kb"})).json()

        assert "ПЛОХОЙ ЧАНКИНГ" in body["matches"][0]["verdict"]
        assert body["matches"][0]["very_short_chunks"] == 5 and body["matches"][0]["empty_like_chunks"] == 5
        assert body["overall_suggestion"].startswith("Документ проиндексирован плохо")

    async def test_vectors_error_is_reported_in_verdict(self, client, stores):
        stores.memory.doc_repo.ids_by_name = {"a": [1]}
        stores.memory.vector_repo.errors = {"vectors:1": RuntimeError("broken")}

        entry = (await client.post("/diag/document", json={"filename": "a"})).json()["matches"][0]

        assert entry["verdict"] == "ОШИБКА get_vectors_by_document: broken" and entry["chunk_count"] == 0

    async def test_get_document_failure_falls_back_to_query_name(self, client, stores):
        stores.memory.doc_repo.ids_by_name = {"a": [1]}
        stores.memory.doc_repo.get_error = RuntimeError("x")
        stores.memory.vector_repo.vectors_by_doc = {1: []}

        entry = (await client.post("/diag/document", json={"filename": "a"})).json()["matches"][0]

        assert entry["filename"] == "a" and entry["verdict"].startswith("КРИТИЧНО")

    async def test_object_style_document_and_project_scope(self, client, stores):
        stores.project.doc_repo.ids_by_name = {"a": [1]}
        stores.project.doc_repo.docs = {1: SimpleNamespace(filename="obj.txt")}

        body = (await client.post("/diag/document", json={"filename": "a", "store": "project", "project_id": "pp"})).json()

        assert body["matches"][0]["filename"] == "obj.txt"
        assert stores.project.doc_repo.calls[0] == ("find", "a", {"project_id": "pp"})

    async def test_doc_repo_without_get_document(self, client, stores):
        class OnlyFind:
            async def find_document_ids_by_filename(self, name):
                return [1]

        stores.kb.doc_repo = OnlyFind()
        stores.kb.vector_repo.vectors_by_doc = {1: [dv("x" * 300)] * 3}

        entry = (await client.post("/diag/document", json={"filename": "q", "store": "kb"})).json()["matches"][0]

        assert entry["filename"] == "q"


def test_preview_truncates_and_flattens_newlines():
    assert diag_module._preview("a\nb") == "a b"
    assert diag_module._preview("x" * 5, n=3) == "xxx…"
    assert diag_module._preview(None) == ""
