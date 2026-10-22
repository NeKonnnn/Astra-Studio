"""KbService на in-memory репозиториях: индексация, дубликаты, откаты, реиндекс, поиск, клонирование."""

from datetime import datetime, timedelta

import pytest

from app.core.config import get_settings
from app.database.models import Document, DocumentVector
from app.database.search_filters import DocumentVectorSearchFilters
from app.services import embed_routing, kb_service, text_duplicate
from app.services import hierarchical_indexing
from app.services.document_duplicate import DUPLICATE_CODE, file_content_hash

from .fakes import BIG, CATS, DOGS, TAXES

pytestmark = pytest.mark.unit


def _hier_stub(count=4, fail=None, store=None):
    """Подмена иерархической индексации: кладёт level-0 чанки в репозиторий, как настоящая."""

    calls = []

    async def fake(text, doc_id, *, filename, vector_repo, rag_client, chunk_size=None,
                   chunk_overlap=None, model=None):
        calls.append({"doc_id": doc_id, "filename": filename, "model": model,
                      "chunk_size": chunk_size, "chunk_overlap": chunk_overlap})
        if fail:
            raise fail
        await vector_repo.create_vectors_batch([
            DocumentVector(document_id=doc_id, chunk_index=i, embedding=[0.1] * 8,
                           content=f"hier-{i}", metadata={"level": 0})
            for i in range(count)
        ])
        return count

    fake.calls = calls
    return fake


class TestGenerations:
    def test_bump_current_and_is_current_per_key(self):
        key = "agent:r1-test"
        before = kb_service.current_kb_reindex_generation(key)
        gen = kb_service.bump_kb_reindex_generation(key)
        assert gen == before + 1
        assert kb_service.current_kb_reindex_generation(key) == gen
        assert kb_service.kb_generation_is_current(key, gen) is True
        assert kb_service.kb_generation_is_current(key, gen - 1) is False
        # None — прерывание не запрашивалось
        assert kb_service.kb_generation_is_current(key, None) is True

    def test_keys_do_not_interfere(self):
        a = kb_service.bump_kb_reindex_generation("agent:r1-a")
        kb_service.bump_kb_reindex_generation("agent:r1-b")
        assert kb_service.kb_generation_is_current("agent:r1-a", a) is True


class TestIndexDocument:
    async def test_success_stores_document_vectors_graph_and_metadata(self, kb, graph, client):
        res = await kb.index_document(
            CATS, "cats.txt", owner_user_id=" Alice ", uploaded_by="Bob", agent_id=7,
            minio_object="o/cats", minio_bucket="b",
        )
        assert res["ok"] is True and res["filename"] == "cats.txt"
        doc = kb.doc_repo.docs[res["document_id"]]
        meta = doc.metadata
        assert meta["owner_user_id"] == "alice" and meta["uploaded_by"] == "bob"
        assert meta["agent_id"] == 7 and meta["size"] == len(CATS) and meta["source"] == "agent"
        assert meta["minio_object"] == "o/cats" and meta["minio_bucket"] == "b"
        assert meta["content_hash"] == file_content_hash(CATS)
        vectors = kb.vector_repo.rows[res["document_id"]]
        assert len(vectors) == res["chunks_count"] >= 1
        assert all(v.embedding is not None and v.metadata["start"] == v.chunk_index for v in vectors)
        assert graph.rebuilt[0]["store_type"] == "kb"
        assert len(graph.rebuilt[0]["chunks"]) == res["chunks_count"]
        assert all(kind == "document" for _, kind in client.embed_calls)

    async def test_without_optional_metadata(self, kb_nograph):
        res = await kb_nograph.index_document(CATS, "cats.txt")
        meta = kb_nograph.doc_repo.docs[res["document_id"]].metadata
        assert res["ok"] is True
        assert not ({"minio_object", "minio_bucket", "owner_user_id", "uploaded_by", "agent_id"} & set(meta))

    async def test_byte_duplicate_is_rejected_with_existing_id(self, kb):
        first = await kb.index_document(CATS, "cats.txt")
        again = await kb.index_document(CATS, "other.txt")
        assert again["ok"] is False and again["code"] == DUPLICATE_CODE
        assert again["document_id"] == first["document_id"]
        assert len(kb.doc_repo.docs) == 1

    async def test_same_file_for_another_agent_is_allowed(self, kb):
        a = await kb.index_document(CATS, "cats.txt", agent_id=1)
        b = await kb.index_document(CATS, "cats.txt", agent_id=2)
        assert a["ok"] and b["ok"] and a["document_id"] != b["document_id"]

    async def test_same_text_in_other_bytes_is_text_duplicate(self, kb):
        first = await kb.index_document(CATS, "cats.txt")
        again = await kb.index_document(CATS + b"\n", "cats-copy.txt")
        assert again["ok"] is False and again["code"] == DUPLICATE_CODE
        assert again["document_id"] == first["document_id"]
        assert "cats.txt" in again["error"]
        assert len(kb.doc_repo.docs) == 1

    async def test_text_duplicate_ignores_documents_of_other_agent(self, kb):
        await kb.index_document(CATS, "cats.txt", agent_id=1)
        other = await kb.index_document(CATS + b"\n", "cats-copy.txt", agent_id=2)
        assert other["ok"] is True

    async def test_non_numeric_agent_id_is_rejected_before_any_write(self, kb):
        """agent_id приводится к int ещё в проверке дубликата — до создания документа (поэтому
        `except (TypeError, ValueError)` в сборке metadata недостижим)."""
        with pytest.raises(ValueError):
            await kb.index_document(CATS, "cats.txt", agent_id="abc")
        assert kb.doc_repo.docs == {}

    async def test_unsupported_format_creates_nothing(self, kb):
        res = await kb.index_document(b"\x00\x01\x02", "file.xyz")
        assert res == {"ok": False, "error": "Не удалось извлечь текст или формат не поддерживается",
                       "document_id": None}
        assert kb.doc_repo.docs == {}

    async def test_empty_text_creates_nothing(self, kb):
        res = await kb.index_document(b"   \n  ", "empty.txt")
        assert res["ok"] is False and res["document_id"] is None and res["error"]
        assert kb.doc_repo.docs == {}

    async def test_route_failure_returns_error_before_document_creation(self, kb, monkeypatch):
        async def boom(*a, **kw):
            raise RuntimeError("модель недоступна")

        monkeypatch.setattr(embed_routing, "resolve_for", boom)
        res = await kb.index_document(CATS, "cats.txt", model="m", provider="p")
        assert res == {"ok": False, "error": "Модель эмбеддинга: модель недоступна", "document_id": None}
        assert kb.doc_repo.docs == {}

    async def test_insert_failure_returns_db_error(self, kb):
        kb.doc_repo.create_returns_none = True
        res = await kb.index_document(CATS, "cats.txt")
        assert res["ok"] is False and res["error"] == "Ошибка сохранения документа в БД"
        assert kb.vector_repo.rows == {}

    async def test_race_duplicate_after_insert_deletes_late_document(self, kb, monkeypatch):
        """Две одновременные загрузки: после вставки поздний документ уступает раннему."""
        answers = iter([None, {"id": 99, "filename": "early.txt"}])

        async def fake_find(**kw):
            return next(answers)

        monkeypatch.setattr(text_duplicate, "find_text_duplicate", fake_find)
        res = await kb.index_document(CATS, "late.txt")
        assert res["code"] == DUPLICATE_CODE and res["document_id"] == 99
        assert "early.txt" in res["error"]
        assert kb.doc_repo.deleted == [1] and kb.doc_repo.docs == {}

    async def test_no_chunks_deletes_document(self, kb, monkeypatch):
        monkeypatch.setattr(kb_service, "split_into_chunks_with_meta", lambda *a, **kw: [])
        res = await kb.index_document(CATS, "cats.txt")
        assert res == {"ok": False, "error": "Не удалось нарезать чанки", "document_id": None}
        assert kb.doc_repo.deleted == [1]

    async def test_embedding_failure_rolls_back_and_reports_to_backend(self, kb, client, failed_reports):
        client.fail = ConnectionError("models down")
        res = await kb.index_document(CATS, "cats.txt", minio_object="o/cats", minio_bucket="b")
        assert res["ok"] is False and res["document_id"] is None
        assert res["error"] == "Ошибка эмбеддингов: models down"
        assert kb.doc_repo.docs == {} and kb.vector_repo.rows == {}
        assert len(failed_reports) == 1
        assert failed_reports[0]["minio_object"] == "o/cats" and failed_reports[0]["minio_bucket"] == "b"
        assert failed_reports[0]["filename"] == "cats.txt"
        assert "models down" in failed_reports[0]["reason"]

    async def test_partial_wave_failure_leaves_no_vectors(self, kb, monkeypatch, failed_reports):
        monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", "2")
        kb.vector_repo.fail_create_on_call = 2
        res = await kb.index_document(BIG, "big.txt", chunk_size=300, chunk_overlap=0)
        assert res["ok"] is False and "insert failed" in res["error"]
        assert kb.vector_repo.create_calls == 2
        assert kb.vector_repo.rows == {} and kb.doc_repo.docs == {}

    async def test_multi_wave_indexing_keeps_contiguous_indexes(self, kb, monkeypatch):
        monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", "3")
        res = await kb.index_document(BIG, "big.txt", chunk_size=300, chunk_overlap=0)
        assert res["ok"] and res["chunks_count"] > 3
        indexes = sorted(v.chunk_index for v in kb.vector_repo.rows[res["document_id"]])
        assert indexes == list(range(res["chunks_count"]))
        assert kb.vector_repo.create_calls > 1

    async def test_text_of_older_documents_is_read_from_repo_when_not_cached(self, kb):
        first = await kb.index_document(CATS, "cats.txt")
        text_duplicate._fingerprints.clear()  # как после перезапуска процесса
        dup = await kb.index_document(CATS + b"\n", "cats-copy.txt")
        assert dup["code"] == DUPLICATE_CODE and dup["document_id"] == first["document_id"]

    async def test_hierarchical_without_graph_repo(self, kb_nograph, monkeypatch):
        monkeypatch.setattr(kb_service, "index_document_hierarchically", _hier_stub(count=2))
        res = await kb_nograph.index_document(CATS, "cats.txt", chunking_strategy="hierarchical")
        assert res["ok"] is True and res["chunks_count"] == 2

    async def test_graph_failure_does_not_fail_indexing(self, kb, graph):
        graph.fail_rebuild = RuntimeError("graph down")
        res = await kb.index_document(CATS, "cats.txt")
        assert res["ok"] is True and kb.vector_repo.rows[res["document_id"]]

    async def test_indexing_marks_bm25_dirty(self, kb):
        idx = kb._bm25_for(kb.vector_repo)
        idx.needs_rebuild = False
        await kb.index_document(CATS, "cats.txt")
        assert idx.needs_rebuild is True

    async def test_hierarchical_success(self, kb, graph, monkeypatch):
        stub = _hier_stub(count=3)
        monkeypatch.setattr(kb_service, "index_document_hierarchically", stub)
        res = await kb.index_document(CATS, "cats.txt", chunking_strategy=" Hierarchical ",
                                      chunk_size=900, chunk_overlap=100, agent_id=3)
        assert res == {"ok": True, "document_id": 1, "filename": "cats.txt", "chunks_count": 3}
        assert stub.calls[0]["chunk_size"] == 900 and stub.calls[0]["filename"] == "cats.txt"
        assert graph.rebuilt[0]["store_type"] == "kb" and len(graph.rebuilt[0]["chunks"]) == 3

    async def test_hierarchical_failure_rolls_back_and_reports(self, kb, monkeypatch, failed_reports):
        monkeypatch.setattr(kb_service, "index_document_hierarchically",
                            _hier_stub(fail=RuntimeError("llm down")))
        res = await kb.index_document(CATS, "cats.txt", chunking_strategy="hierarchical",
                                      minio_object="o/c", minio_bucket="b")
        assert res == {"ok": False, "error": "Иерархическая индексация: llm down", "document_id": None}
        assert kb.doc_repo.docs == {}
        assert failed_reports[0]["reason"] == "Иерархическая индексация: llm down"


class TestReindexDocument:
    async def _indexed(self, kb, data=CATS, name="cats.txt", **kw):
        res = await kb.index_document(data, name, **kw)
        assert res["ok"]
        return res["document_id"]

    async def test_missing_document_returns_zero(self, kb):
        assert await kb.reindex_document(404) == 0

    async def test_empty_text_returns_zero_and_keeps_vectors(self, kb):
        doc_id = await self._indexed(kb)
        kb.doc_repo.docs[doc_id] = kb.doc_repo.docs[doc_id].model_copy(update={"content": "   "})
        before = len(kb.vector_repo.rows[doc_id])
        assert await kb.reindex_document(doc_id) == 0
        assert len(kb.vector_repo.rows[doc_id]) == before

    async def test_replaces_vectors_with_new_chunking(self, kb, graph):
        doc_id = await self._indexed(kb, BIG, "big.txt", chunk_size=300, chunk_overlap=0)
        old = len(kb.vector_repo.rows[doc_id])
        graph.rebuilt.clear()
        created = await kb.reindex_document(doc_id, chunk_size=1500, chunk_overlap=0)
        assert 0 < created < old
        assert len(kb.vector_repo.rows[doc_id]) == created
        assert graph.rebuilt[0]["document_id"] == doc_id

    async def test_hierarchical_strategy(self, kb, graph, monkeypatch):
        doc_id = await self._indexed(kb)
        stub = _hier_stub(count=5)
        monkeypatch.setattr(hierarchical_indexing, "index_document_hierarchically", stub)
        assert await kb.reindex_document(doc_id, chunking_strategy="HIERARCHICAL") == 5
        assert [v.content for v in kb.vector_repo.rows[doc_id]] == [f"hier-{i}" for i in range(5)]
        assert graph.rebuilt[-1]["store_type"] == "kb"

    async def test_no_chunks_returns_zero(self, kb, monkeypatch):
        doc_id = await self._indexed(kb)
        monkeypatch.setattr(kb_service, "split_into_chunks_with_meta", lambda *a, **kw: [])
        assert await kb.reindex_document(doc_id) == 0
        assert doc_id not in kb.vector_repo.rows  # старые вектора уже удалены

    async def test_graph_failure_is_swallowed(self, kb, graph):
        doc_id = await self._indexed(kb)
        graph.fail_rebuild = RuntimeError("graph down")
        assert await kb.reindex_document(doc_id) >= 1

    async def test_without_graph_repo(self, kb_nograph):
        doc_id = await self._indexed(kb_nograph)
        assert await kb_nograph.reindex_document(doc_id) >= 1

    async def test_graph_rebuild_skipped_without_chunks(self, kb, graph, monkeypatch):
        doc_id = await self._indexed(kb)
        graph.rebuilt.clear()
        monkeypatch.setattr(hierarchical_indexing, "index_document_hierarchically", _hier_stub(count=0))
        assert await kb.reindex_document(doc_id, chunking_strategy="hierarchical") == 0
        assert graph.rebuilt == []

    async def test_graph_rebuild_error_on_hierarchical_is_logged_not_raised(self, kb, graph, monkeypatch):
        doc_id = await self._indexed(kb)
        monkeypatch.setattr(hierarchical_indexing, "index_document_hierarchically", _hier_stub(count=2))
        graph.fail_rebuild = RuntimeError("boom")
        assert await kb.reindex_document(doc_id, chunking_strategy="hierarchical") == 2


class TestReindexAll:
    async def _fill(self, kb):
        a = (await kb.index_document(CATS, "cats.txt", owner_user_id="alice"))["document_id"]
        b = (await kb.index_document(DOGS, "dogs.txt", owner_user_id="bob"))["document_id"]
        c = (await kb.index_document(TAXES, "taxes.txt", owner_user_id="alice"))["document_id"]
        return a, b, c

    async def test_all_documents(self, kb):
        await self._fill(kb)
        res = await kb.reindex_all()
        assert res["documents"] == 3 and res["chunks"] >= 3

    async def test_owner_filter(self, kb):
        await self._fill(kb)
        assert (await kb.reindex_all(owner_user_id="alice"))["documents"] == 2

    async def test_document_ids_filter_merges_with_owner(self, kb):
        a, b, c = await self._fill(kb)
        res = await kb.reindex_all(owner_user_id="alice", document_ids=[b, 999])
        assert res["documents"] == 3
        assert (await kb.reindex_all(document_ids=[b]))["documents"] == 1

    async def test_rows_without_id_are_skipped(self, kb, monkeypatch):
        a, b, c = await self._fill(kb)
        nameless = Document(filename="ghost.txt", content="x", metadata={})  # id=None

        async def by_owner(owner):
            return [nameless, kb.doc_repo.docs[a]]

        async def by_ids(ids):
            return [nameless, kb.doc_repo.docs[b]]

        monkeypatch.setattr(kb.doc_repo, "get_documents_by_owner", by_owner)
        monkeypatch.setattr(kb.doc_repo, "get_documents_by_ids", by_ids)
        res = await kb.reindex_all(owner_user_id="alice", document_ids=[b])
        assert res["documents"] == 2  # a и b; документ без id пропущен

    async def test_unknown_owner_means_no_documents_not_all(self, kb):
        await self._fill(kb)
        assert (await kb.reindex_all(owner_user_id="nobody")) == {"documents": 0, "chunks": 0}

    async def test_new_generation_aborts_pass(self, kb):
        await self._fill(kb)
        key = "agent:r1-abort"
        gen = kb_service.bump_kb_reindex_generation(key)
        kb_service.bump_kb_reindex_generation(key)  # пришёл новый проход той же сущности
        res = await kb.reindex_all(generation=gen, generation_key=key)
        assert res == {"documents": 0, "chunks": 0}

    async def test_other_key_generation_does_not_abort(self, kb):
        await self._fill(kb)
        gen = kb_service.bump_kb_reindex_generation("agent:r1-mine")
        kb_service.bump_kb_reindex_generation("agent:r1-neighbour")
        res = await kb.reindex_all(generation=gen, generation_key="agent:r1-mine")
        assert res["documents"] == 3

    async def test_document_error_is_counted_and_pass_continues(self, kb, client):
        await self._fill(kb)
        real = client.embed
        calls = {"n": 0}

        async def flaky(texts, model=None, kind=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("first doc")
            return await real(texts, model=model, kind=kind)

        client.embed = flaky
        res = await kb.reindex_all()
        assert res["documents"] == 2


class TestSearch:
    async def _fill(self, kb):
        ids = {}
        for key, data, name in (("cats", CATS, "cats.txt"), ("dogs", DOGS, "dogs.txt"),
                                ("taxes", TAXES, "Налоги_отчёт.txt")):
            ids[key] = (await kb.index_document(data, name))["document_id"]
        return ids

    @pytest.mark.parametrize("strategy", ["vector", "hybrid", "lexical", "auto", "raw_cosine",
                                          "graph", "hierarchical", "flat", "standard", "bm25", None])
    async def test_every_strategy_finds_relevant_document(self, kb, strategy):
        ids = await self._fill(kb)
        hits = await kb.search("расскажи про кошки", k=3, strategy=strategy, use_reranking=False)
        assert hits and hits[0][2] == ids["cats"]
        content, score, doc_id, chunk_idx = hits[0]
        assert isinstance(content, str) and isinstance(score, float) and isinstance(chunk_idx, int)

    async def test_return_trace(self, kb):
        await self._fill(kb)
        hits, trace = await kb.search("налоговая проверка", k=2, strategy="vector",
                                      use_reranking=False, return_trace=True)
        assert trace.store == "kb" and trace.resolved_strategy == "vector"
        assert len(hits) <= 2

    async def test_whitelist_restricts_vector_keyword_and_bm25(self, kb):
        ids = await self._fill(kb)
        for strategy in ("vector", "hybrid", "lexical", "auto"):
            hits = await kb.search("кошки собаки налог", k=10, strategy=strategy,
                                   use_reranking=False, document_ids=[ids["dogs"]])
            assert hits, strategy
            assert {h[2] for h in hits} == {ids["dogs"]}, strategy

    async def test_document_id_narrows_search(self, kb):
        ids = await self._fill(kb)
        hits = await kb.search("кошки", k=5, document_id=ids["taxes"], use_reranking=False)
        assert {h[2] for h in hits} <= {ids["taxes"]}

    async def test_document_id_bm25_scope_in_hybrid(self, kb):
        ids = await self._fill(kb)
        hits = await kb.search("кошки", k=5, strategy="hybrid", use_reranking=False,
                               document_id=ids["cats"])
        assert hits and {h[2] for h in hits} == {ids["cats"]}

    async def test_filename_filter(self, kb):
        ids = await self._fill(kb)
        flt = DocumentVectorSearchFilters(filename_contains="dogs")
        hits = await kb.search("кошки собаки", k=5, strategy="vector", use_reranking=False, filters=flt)
        assert hits and {h[2] for h in hits} == {ids["dogs"]}

    async def test_date_filter(self, kb):
        ids = await self._fill(kb)
        future = datetime.utcnow() + timedelta(days=1)
        flt = DocumentVectorSearchFilters(date_from=future)
        assert await kb.search("кошки", k=5, strategy="vector", use_reranking=False, filters=flt) == []
        flt = DocumentVectorSearchFilters(date_to=future)
        assert await kb.search("кошки", k=5, strategy="vector", use_reranking=False, filters=flt)

    async def test_filename_in_query_pins_that_document(self, kb):
        ids = await self._fill(kb)
        hits = await kb.search("сделай саммари по dogs.txt", k=3, strategy="vector", use_reranking=False)
        assert ids["dogs"] in {h[2] for h in hits}

    async def test_filename_anchor_is_scoped_to_whitelist(self, kb):
        """BUG-RAG-33: якорь по имени файла не подхватывает документы вне белого списка."""
        ids = await self._fill(kb)
        scoped = await kb.search("сделай саммари по dogs.txt", k=5, strategy="vector",
                                 use_reranking=False, document_ids=[ids["cats"]])
        assert ids["dogs"] not in {h[2] for h in scoped}

    @pytest.mark.parametrize("strategy", ["hybrid", "vector", "lexical", "reranking", "graph"])
    async def test_rerank_is_applied_via_rerank_client(self, kb, client, rerank_on, strategy):
        await self._fill(kb)
        hits, trace = await kb.search("кошки собаки", k=3, strategy=strategy, use_reranking=True,
                                      return_trace=True)
        assert client.rerank_calls and trace.used_rerank is True and hits
        assert client.rerank_calls[0][0] == "кошки собаки"

    async def test_rerank_failure_falls_back_to_vector_order(self, kb, client, rerank_on):
        await self._fill(kb)
        client.rerank_fail = ConnectionError("rerank down")
        hits, trace = await kb.search("кошки", k=3, strategy="hybrid", use_reranking=True,
                                      return_trace=True)
        assert hits and trace.used_rerank is False
        assert any("rerank" in w for w in trace.warnings)

    async def test_entity_lane_and_filename_anchor_on_graph_pipeline(self, kb, graph):
        ids = await self._fill(kb)
        graph.neighbors = {(ids["cats"], 0): 0.9}
        hits, trace = await kb.search("Что сказано в Налоги_отчёт.txt про Константин", k=3,
                                      strategy="graph", use_reranking=False, return_trace=True)
        stage_names = [s["stage"] for s in trace.steps]
        assert "entity_lane" in stage_names and "graph_expand_rrf" in stage_names
        assert ids["taxes"] in {h[2] for h in hits}
        assert graph.expand_calls and graph.expand_calls[0]["store_type"] == "kb"

    async def test_search_on_empty_base_returns_nothing(self, kb):
        assert await kb.search("что угодно", k=5, use_reranking=False) == []

    async def test_bm25_index_is_cached_per_scope(self, kb):
        ids = await self._fill(kb)
        a = kb._bm25_for(kb.vector_repo, [ids["cats"]])
        assert kb._bm25_for(kb.vector_repo, [ids["cats"], None]) is a
        assert kb._bm25_for(kb.vector_repo, [ids["dogs"]]) is not a
        assert kb._bm25_for(kb.vector_repo) is not a


class TestBm25Store:
    @pytest.fixture
    def store_on(self, monkeypatch):
        monkeypatch.setattr(get_settings().rag, "bm25_store_enabled", True)

    async def test_store_enabled_saves_and_reloads_index(self, kb, store_on):
        ids = {}
        ids["cats"] = (await kb.index_document(CATS, "cats.txt"))["document_id"]
        ids["dogs"] = (await kb.index_document(DOGS, "dogs.txt"))["document_id"]
        idx = kb._bm25_for(kb.vector_repo, [ids["cats"]])
        assert await idx.ensure_built() is True
        assert list(kb.vector_repo.store) and "kb" in next(iter(kb.vector_repo.store))
        # второй индекс того же корпуса берёт готовый из хранилища, а не собирает
        kb._bm25_by_key.drop(lambda k: True)
        again = kb._bm25_for(kb.vector_repo, [ids["cats"]])

        async def no_fetch(*a, **kw):
            raise AssertionError("корпус не должен читаться: индекс лежит в хранилище")

        kb.vector_repo.get_all_contents_for_bm25 = no_fetch
        assert await again.ensure_built() is True
        assert again.chunk_count > 0

    async def test_store_disabled_builds_from_contents(self, kb, monkeypatch):
        monkeypatch.setattr(get_settings().rag, "bm25_store_enabled", False)
        await kb.index_document(CATS, "cats.txt")
        idx = kb._bm25_for(kb.vector_repo)
        assert await idx.ensure_built() is True
        assert kb.vector_repo.store == {}


class TestManagement:
    async def test_check_duplicate(self, kb):
        first = await kb.index_document(CATS, "cats.txt", agent_id=3)
        got = await kb.check_duplicate(content_hash=file_content_hash(CATS), agent_id=3)
        assert got == {"duplicate": True, "document_id": first["document_id"], "filename": "cats.txt"}
        assert await kb.check_duplicate(content_hash="deadbeef", agent_id=3) == {"duplicate": False}

    async def test_list_documents(self, kb):
        await kb.index_document(CATS, "cats.txt", agent_id=3)
        rows = await kb.list_documents()
        assert [r["filename"] for r in rows] == ["cats.txt"]
        assert rows[0]["metadata"] == {"agent_id": 3}

    async def test_get_document_info(self, kb):
        res = await kb.index_document(CATS, "cats.txt")
        info = await kb.get_document_info(res["document_id"])
        assert info["id"] == res["document_id"] and info["filename"] == "cats.txt"
        assert info["metadata"]["size"] == len(CATS)
        assert info["created_at"] == kb.doc_repo.docs[res["document_id"]].created_at.isoformat()
        assert await kb.get_document_info(404) is None

    async def test_get_document_info_without_created_at(self, kb):
        kb.doc_repo.docs[5] = Document.model_construct(
            id=5, filename="x.txt", content="t", metadata={}, created_at=None, updated_at=None)
        assert (await kb.get_document_info(5))["created_at"] is None

    async def test_delete_document(self, kb, graph):
        res = await kb.index_document(CATS, "cats.txt", minio_object="o/c", minio_bucket="b")
        doc_id = res["document_id"]
        out = await kb.delete_document(doc_id)
        assert out == {"ok": True, "document_id": doc_id, "minio_object": "o/c", "minio_bucket": "b"}
        assert kb.doc_repo.docs == {} and kb.vector_repo.rows == {}
        assert graph.deleted == [("kb", doc_id)]

    async def test_delete_unknown_document(self, kb):
        assert await kb.delete_document(404) == {"ok": False, "document_id": 404}

    async def test_delete_survives_graph_error(self, kb, graph):
        res = await kb.index_document(CATS, "cats.txt")
        graph.fail_delete = RuntimeError("graph down")
        out = await kb.delete_document(res["document_id"])
        assert out["ok"] is True and kb.doc_repo.docs == {}

    async def test_delete_without_graph_repo(self, kb_nograph):
        res = await kb_nograph.index_document(CATS, "cats.txt")
        assert (await kb_nograph.delete_document(res["document_id"]))["ok"] is True


class TestCloneDocument:
    async def test_not_found(self, kb):
        assert await kb.clone_document(404) == {"ok": False, "document_id": 404, "error": "not_found"}

    async def test_clone_copies_row_and_vectors_with_new_meta(self, kb):
        res = await kb.index_document(CATS, "cats.txt", agent_id=1, owner_user_id="alice",
                                      minio_object="o/src", minio_bucket="bsrc")
        src = res["document_id"]
        out = await kb.clone_document(src, agent_id=2, owner_user_id=" Bob ", uploaded_by=" Carl ",
                                      minio_object="o/new", minio_bucket="bnew")
        assert out["ok"] is True and out["source_document_id"] == src and out["filename"] == "cats.txt"
        assert out["vectors_copied"] == res["chunks_count"]
        assert out["source_minio_object"] == "o/src" and out["source_minio_bucket"] == "bsrc"
        meta = kb.doc_repo.docs[out["document_id"]].metadata
        assert meta["cloned_from"] == src and meta["agent_id"] == 2
        assert meta["owner_user_id"] == "bob" and meta["uploaded_by"] == "carl"
        assert meta["minio_object"] == "o/new" and meta["minio_bucket"] == "bnew"
        assert len(kb.vector_repo.rows[out["document_id"]]) == res["chunks_count"]
        # исходник не тронут
        assert kb.doc_repo.docs[src].metadata["agent_id"] == 1

    async def test_empty_minio_keys_are_removed_none_keeps(self, kb):
        res = await kb.index_document(CATS, "cats.txt", minio_object="o/src", minio_bucket="bsrc")
        out = await kb.clone_document(res["document_id"], minio_object="", minio_bucket="")
        meta = kb.doc_repo.docs[out["document_id"]].metadata
        assert "minio_object" not in meta and "minio_bucket" not in meta
        kept = await kb.clone_document(res["document_id"])
        meta = kb.doc_repo.docs[kept["document_id"]].metadata
        assert meta["minio_object"] == "o/src" and meta["minio_bucket"] == "bsrc"

    async def test_clone_failed_when_row_not_created(self, kb):
        res = await kb.index_document(CATS, "cats.txt")
        kb.doc_repo.create_returns_none = True
        assert await kb.clone_document(res["document_id"]) == {
            "ok": False, "document_id": res["document_id"], "error": "clone_failed"}

    async def test_vector_copy_failure_rolls_back_document(self, kb):
        res = await kb.index_document(CATS, "cats.txt")
        kb.vector_repo.fail_clone = RuntimeError("copy failed")
        out = await kb.clone_document(res["document_id"])
        assert out == {"ok": False, "document_id": res["document_id"], "error": "vectors_failed"}
        assert list(kb.doc_repo.docs) == [res["document_id"]]  # клон откатан

    async def test_rollback_failure_is_swallowed(self, kb):
        res = await kb.index_document(CATS, "cats.txt")
        kb.vector_repo.fail_clone = RuntimeError("copy failed")
        kb.doc_repo.fail_delete = RuntimeError("delete failed")
        out = await kb.clone_document(res["document_id"])
        assert out["error"] == "vectors_failed"
        assert len(kb.doc_repo.docs) == 2  # откатить не удалось — клон остался, но ошибка не вылетела


class TestDocActors:
    def test_object_with_string_metadata(self):
        doc = Document(filename="a.txt", metadata={})
        object.__setattr__(doc, "metadata", '{"owner_user_id": " Al ", "uploaded_by": "bo"}')
        assert kb_service._doc_actors(doc) == ("Al", "bo", "a.txt")

    def test_dict_with_bad_json_and_name_fallback(self):
        assert kb_service._doc_actors({"metadata": "{not json", "name": "n.txt"}) == ("-", "-", "n.txt")

    def test_dict_without_anything(self):
        assert kb_service._doc_actors({}) == ("-", "-", "?")

    def test_blank_values_become_dash(self):
        meta = {"owner_user_id": "  ", "uploaded_by": None}
        assert kb_service._doc_actors({"metadata": meta, "filename": "f"}) == ("-", "-", "f")

    def test_object_without_filename(self):
        class Obj:
            metadata = None
            filename = ""

        assert kb_service._doc_actors(Obj()) == ("-", "-", "?")
