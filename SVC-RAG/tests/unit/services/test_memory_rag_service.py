"""MemoryRagService на in-memory репозиториях: индексация Библиотеки, реиндекс, поиск, настройки из ENV."""

import pytest

from app.database.models import Document, DocumentVector
from app.database.search_filters import DocumentVectorSearchFilters
from app.services import embed_routing, hierarchical_indexing, memory_rag_service, text_duplicate
from app.services.document_duplicate import DUPLICATE_CODE, file_content_hash

from .fakes import BIG, CATS, DOGS, TAXES

pytestmark = pytest.mark.unit


def _hier_stub(count=4, fail=None):
    calls = []

    async def fake(text, doc_id, *, filename, vector_repo, rag_client, chunk_size=None,
                   chunk_overlap=None, model=None):
        calls.append({"doc_id": doc_id, "filename": filename, "chunk_size": chunk_size,
                      "chunk_overlap": chunk_overlap})
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


class TestGeneration:
    def test_bump_and_current(self):
        before = memory_rag_service.current_memory_reindex_generation()
        assert memory_rag_service.bump_memory_reindex_generation() == before + 1
        assert memory_rag_service.current_memory_reindex_generation() == before + 1


class TestMemoryChunkParams:
    def test_defaults_when_env_empty(self):
        assert memory_rag_service._memory_chunk_params() == (None, None, None)

    def test_values_from_env(self, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", " 700 ")
        monkeypatch.setenv("RAG_MEMORY_CHUNK_OVERLAP", "70")
        monkeypatch.setenv("RAG_MEMORY_CHUNKING_STRATEGY", " semantic ")
        assert memory_rag_service._memory_chunk_params() == (700, 70, "semantic")

    def test_non_numeric_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "много")
        monkeypatch.setenv("RAG_MEMORY_CHUNK_OVERLAP", "10")
        assert memory_rag_service._memory_chunk_params() == (None, 10, None)


class TestMemoryEmbeddingChoice:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("", (None, None)),
            ("   ", (None, None)),
            ("local/bge-m3", ("native", "bge-m3")),
            ("bge-m3", ("native", "bge-m3")),
            ("phoenix/emb-1", ("PHOENIX", "emb-1")),
            ("PHOENIX_Embeddings/emb-2", ("PHOENIX_Embeddings", "emb-2")),
            ("corsur/e5", ("CORSUR", "e5")),
            ("phoenix/  ", (None, None)),
        ],
    )
    def test_formats(self, monkeypatch, raw, expected):
        monkeypatch.setenv("RAG_MEMORY_EMBEDDING_MODEL", raw)
        assert memory_rag_service.memory_embedding_choice() == expected

    def test_provider_ids_are_taken_from_env(self, monkeypatch):
        monkeypatch.setenv("RAG_PHOENIX_PROVIDER_ID", "PX")
        monkeypatch.setenv("RAG_PHOENIX_EMBEDDINGS_PROVIDER_ID", "PXE")
        monkeypatch.setenv("RAG_CORSUR_PROVIDER_ID", "CS")
        for raw, provider in (("phoenix/m", "PX"), ("phoenix_embeddings/m", "PXE"), ("corsur/m", "CS")):
            monkeypatch.setenv("RAG_MEMORY_EMBEDDING_MODEL", raw)
            assert memory_rag_service.memory_embedding_choice() == (provider, "m")

    def test_blank_provider_id_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("RAG_PHOENIX_PROVIDER_ID", "  ")
        monkeypatch.setenv("RAG_MEMORY_EMBEDDING_MODEL", "phoenix/m")
        assert memory_rag_service.memory_embedding_choice() == ("PHOENIX", "m")


class TestIndexDocument:
    async def test_success(self, memory, graph, client):
        res = await memory.index_document(CATS, "cats.txt", minio_object="o/c", minio_bucket="b")
        assert res["ok"] is True and res["filename"] == "cats.txt"
        meta = memory.doc_repo.docs[res["document_id"]].metadata
        assert meta["source"] == "memory_library" and meta["size"] == len(CATS)
        assert meta["minio_object"] == "o/c" and meta["minio_bucket"] == "b"
        assert meta["content_hash"] == file_content_hash(CATS)
        vectors = memory.vector_repo.rows[res["document_id"]]
        assert len(vectors) == res["chunks_count"] >= 1
        assert vectors[0].metadata["document_filename"] == "cats.txt"
        assert graph.rebuilt[0]["store_type"] == "memory"
        assert all(kind == "document" for _, kind in client.embed_calls)

    async def test_without_minio_keys_and_graph(self, memory_nograph):
        res = await memory_nograph.index_document(CATS, "cats.txt")
        meta = memory_nograph.doc_repo.docs[res["document_id"]].metadata
        assert res["ok"] and "minio_object" not in meta and "minio_bucket" not in meta

    async def test_chunk_params_come_from_env_not_from_caller(self, memory, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "300")
        monkeypatch.setenv("RAG_MEMORY_CHUNK_OVERLAP", "0")
        small = await memory.index_document(BIG, "big.txt", chunk_size=5000, chunk_overlap=0)
        monkeypatch.delenv("RAG_MEMORY_CHUNK_SIZE")
        monkeypatch.delenv("RAG_MEMORY_CHUNK_OVERLAP")
        text_duplicate._fingerprints.clear()
        memory.doc_repo.docs.clear()
        default = await memory.index_document(BIG, "big.txt")
        assert small["chunks_count"] > default["chunks_count"]

    async def test_byte_duplicate(self, memory):
        first = await memory.index_document(CATS, "cats.txt")
        again = await memory.index_document(CATS, "other.txt")
        assert again["code"] == DUPLICATE_CODE and again["document_id"] == first["document_id"]

    async def test_text_duplicate(self, memory):
        first = await memory.index_document(CATS, "cats.txt")
        again = await memory.index_document(CATS + b"\n", "cats-copy.txt")
        assert again["code"] == DUPLICATE_CODE and again["document_id"] == first["document_id"]
        assert len(memory.doc_repo.docs) == 1

    async def test_text_of_older_documents_is_read_from_repo_when_not_cached(self, memory):
        first = await memory.index_document(CATS, "cats.txt")
        text_duplicate._fingerprints.clear()
        again = await memory.index_document(CATS + b"\n", "cats-copy.txt")
        assert again["code"] == DUPLICATE_CODE and again["document_id"] == first["document_id"]

    async def test_unsupported_format(self, memory):
        res = await memory.index_document(b"\x00\x01\x02", "file.xyz")
        assert res == {"ok": False, "error": "Не удалось извлечь текст или формат не поддерживается",
                       "document_id": None}

    async def test_empty_text(self, memory):
        res = await memory.index_document(b"  \n ", "empty.txt")
        assert res["ok"] is False and res["document_id"] is None
        assert memory.doc_repo.docs == {}

    async def test_null_bytes_are_stripped_from_name(self, memory):
        res = await memory.index_document(CATS, "ca\x00ts.txt")
        assert res["filename"] == "cats.txt"
        assert memory.doc_repo.docs[res["document_id"]].filename == "cats.txt"

    async def test_route_failure_propagates_before_any_write(self, memory, monkeypatch):
        """В Библиотеке модель выбирается без перехвата ошибки: исключение уходит вызывающему."""

        async def boom(*a, **kw):
            raise RuntimeError("нет модели")

        monkeypatch.setattr(embed_routing, "resolve_for", boom)
        with pytest.raises(RuntimeError, match="нет модели"):
            await memory.index_document(CATS, "cats.txt")
        assert memory.doc_repo.docs == {}

    async def test_insert_failure(self, memory):
        memory.doc_repo.create_returns_none = True
        res = await memory.index_document(CATS, "cats.txt")
        assert res["error"] == "Ошибка сохранения документа в БД" and res["ok"] is False

    async def test_race_duplicate_after_insert(self, memory, monkeypatch):
        answers = iter([None, {"id": 77, "filename": "early.txt"}])

        async def fake_find(**kw):
            return next(answers)

        monkeypatch.setattr(text_duplicate, "find_text_duplicate", fake_find)
        res = await memory.index_document(CATS, "late.txt")
        assert res["code"] == DUPLICATE_CODE and res["document_id"] == 77
        assert memory.doc_repo.deleted == [1] and memory.doc_repo.docs == {}

    async def test_no_chunks(self, memory, monkeypatch):
        monkeypatch.setattr(memory_rag_service, "split_into_chunks_with_meta", lambda *a, **kw: [])
        res = await memory.index_document(CATS, "cats.txt")
        assert res == {"ok": False, "error": "Не удалось нарезать чанки", "document_id": None}
        assert memory.doc_repo.deleted == [1]

    async def test_embedding_failure_rolls_back(self, memory, client):
        client.fail = ConnectionError("models down")
        res = await memory.index_document(CATS, "cats.txt")
        assert res == {"ok": False, "error": "Ошибка эмбеддингов: models down", "document_id": None}
        assert memory.doc_repo.docs == {} and memory.vector_repo.rows == {}

    async def test_embedding_failure_reports_to_backend(self, memory, client, failed_reports):
        # BUG-RAG-08: как в KB, backend узнаёт о неудаче и убирает исходник.
        client.fail = ConnectionError("models down")
        await memory.index_document(CATS, "cats.txt", minio_object="o/k", minio_bucket="b")
        assert len(failed_reports) == 1
        assert failed_reports[0]["minio_object"] == "o/k" and failed_reports[0]["minio_bucket"] == "b"
        assert failed_reports[0]["store"] == "memory" and "models down" in failed_reports[0]["reason"]

    async def test_hierarchical_failure_reports_to_backend(self, memory, monkeypatch, failed_reports):
        monkeypatch.setenv("RAG_MEMORY_CHUNKING_STRATEGY", "hierarchical")
        monkeypatch.setattr(memory_rag_service, "index_document_hierarchically",
                            _hier_stub(fail=RuntimeError("llm down")))
        await memory.index_document(CATS, "cats.txt", minio_object="o/k", minio_bucket="b")
        assert failed_reports[0]["reason"] == "Иерархическая индексация: llm down"
        assert failed_reports[0]["store"] == "memory"

    async def test_partial_wave_failure_leaves_no_vectors(self, memory, monkeypatch):
        monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", "2")
        monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "300")
        monkeypatch.setenv("RAG_MEMORY_CHUNK_OVERLAP", "0")
        memory.vector_repo.fail_create_on_call = 2
        res = await memory.index_document(BIG, "big.txt")
        assert res["ok"] is False
        assert memory.vector_repo.rows == {} and memory.doc_repo.docs == {}

    async def test_graph_failure_does_not_fail_indexing(self, memory, graph):
        graph.fail_rebuild = RuntimeError("graph down")
        res = await memory.index_document(CATS, "cats.txt")
        assert res["ok"] is True

    async def test_hierarchical_success(self, memory, graph, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_CHUNKING_STRATEGY", "Hierarchical")
        stub = _hier_stub(count=3)
        monkeypatch.setattr(memory_rag_service, "index_document_hierarchically", stub)
        res = await memory.index_document(CATS, "cats.txt")
        assert res == {"ok": True, "document_id": 1, "filename": "cats.txt", "chunks_count": 3}
        assert graph.rebuilt[0]["store_type"] == "memory" and len(graph.rebuilt[0]["chunks"]) == 3

    async def test_hierarchical_failure_rolls_back(self, memory, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_CHUNKING_STRATEGY", "hierarchical")
        monkeypatch.setattr(memory_rag_service, "index_document_hierarchically",
                            _hier_stub(fail=RuntimeError("llm down")))
        res = await memory.index_document(CATS, "cats.txt")
        assert res == {"ok": False, "error": "Иерархическая индексация: llm down", "document_id": None}
        assert memory.doc_repo.docs == {}

    async def test_indexing_marks_bm25_dirty(self, memory):
        idx = memory._bm25_for(memory.vector_repo)
        idx.needs_rebuild = False
        await memory.index_document(CATS, "cats.txt")
        assert idx.needs_rebuild is True


class TestReindexDocument:
    async def _indexed(self, memory, data=CATS, name="cats.txt"):
        res = await memory.index_document(data, name)
        assert res["ok"]
        return res["document_id"]

    async def test_missing_document(self, memory):
        assert await memory.reindex_document(404) == 0

    async def test_empty_text(self, memory):
        doc_id = await self._indexed(memory)
        memory.doc_repo.docs[doc_id] = memory.doc_repo.docs[doc_id].model_copy(update={"content": ""})
        before = len(memory.vector_repo.rows[doc_id])
        assert await memory.reindex_document(doc_id) == 0
        assert len(memory.vector_repo.rows[doc_id]) == before

    async def test_replaces_vectors(self, memory, graph, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "300")
        monkeypatch.setenv("RAG_MEMORY_CHUNK_OVERLAP", "0")
        doc_id = await self._indexed(memory, BIG, "big.txt")
        old = len(memory.vector_repo.rows[doc_id])
        monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "1500")
        graph.rebuilt.clear()
        created = await memory.reindex_document(doc_id)
        assert 0 < created < old
        assert len(memory.vector_repo.rows[doc_id]) == created
        assert graph.rebuilt[0]["document_id"] == doc_id

    async def test_hierarchical(self, memory, graph, monkeypatch):
        doc_id = await self._indexed(memory)
        monkeypatch.setenv("RAG_MEMORY_CHUNKING_STRATEGY", "hierarchical")
        monkeypatch.setattr(memory_rag_service, "index_document_hierarchically", _hier_stub(count=5))
        assert await memory.reindex_document(doc_id) == 5
        assert [v.content for v in memory.vector_repo.rows[doc_id]] == [f"hier-{i}" for i in range(5)]
        assert graph.rebuilt[-1]["store_type"] == "memory"

    async def test_graph_is_not_rebuilt_when_no_vectors_remain(self, memory, graph, monkeypatch):
        doc_id = await self._indexed(memory)
        graph.rebuilt.clear()
        monkeypatch.setenv("RAG_MEMORY_CHUNKING_STRATEGY", "hierarchical")
        monkeypatch.setattr(memory_rag_service, "index_document_hierarchically", _hier_stub(count=0))
        assert await memory.reindex_document(doc_id) == 0
        assert graph.rebuilt == []

    async def test_no_chunks(self, memory, monkeypatch):
        doc_id = await self._indexed(memory)
        monkeypatch.setattr(memory_rag_service, "split_into_chunks_with_meta", lambda *a, **kw: [])
        assert await memory.reindex_document(doc_id) == 0

    async def test_graph_rebuild_error_is_swallowed(self, memory, graph):
        doc_id = await self._indexed(memory)
        graph.fail_rebuild = RuntimeError("graph down")
        assert await memory.reindex_document(doc_id) >= 1

    async def test_without_graph_repo(self, memory_nograph):
        doc_id = await self._indexed(memory_nograph)
        assert await memory_nograph.reindex_document(doc_id) >= 1


class TestReindexAll:
    async def _fill(self, memory):
        for data, name in ((CATS, "cats.txt"), (DOGS, "dogs.txt"), (TAXES, "taxes.txt")):
            assert (await memory.index_document(data, name))["ok"]

    async def test_all_documents(self, memory):
        await self._fill(memory)
        res = await memory.reindex_all()
        assert res["documents"] == 3 and res["chunks"] >= 3

    async def test_empty_library(self, memory):
        assert await memory.reindex_all() == {"documents": 0, "chunks": 0}

    async def test_old_generation_aborts_pass(self, memory):
        await self._fill(memory)
        gen = memory_rag_service.bump_memory_reindex_generation()
        memory_rag_service.bump_memory_reindex_generation()
        assert await memory.reindex_all(generation=gen) == {"documents": 0, "chunks": 0}

    async def test_current_generation_does_not_abort(self, memory):
        await self._fill(memory)
        gen = memory_rag_service.bump_memory_reindex_generation()
        assert (await memory.reindex_all(generation=gen))["documents"] == 3

    async def test_document_error_is_counted(self, memory, client):
        await self._fill(memory)
        real = client.embed
        calls = {"n": 0}

        async def flaky(texts, model=None, kind=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("first")
            return await real(texts, model=model, kind=kind)

        client.embed = flaky
        assert (await memory.reindex_all())["documents"] == 2


class TestSearch:
    async def _fill(self, memory):
        ids = {}
        for key, data, name in (("cats", CATS, "cats.txt"), ("dogs", DOGS, "dogs.txt"),
                                ("taxes", TAXES, "Налоги_отчёт.txt")):
            ids[key] = (await memory.index_document(data, name))["document_id"]
        return ids

    @pytest.mark.parametrize("strategy", ["vector", "hybrid", "lexical", "auto", "raw_cosine", "graph", None])
    async def test_strategies_find_relevant_document(self, memory, strategy):
        ids = await self._fill(memory)
        hits = await memory.search("расскажи про кошки", k=3, strategy=strategy, use_reranking=False)
        assert hits and hits[0][2] == ids["cats"]

    async def test_return_trace(self, memory):
        await self._fill(memory)
        hits, trace = await memory.search("кошки", k=2, strategy="vector", use_reranking=False,
                                          return_trace=True)
        assert trace.store == "memory" and len(hits) <= 2

    async def test_document_id_narrows(self, memory):
        ids = await self._fill(memory)
        hits = await memory.search("кошки", k=5, strategy="hybrid", use_reranking=False,
                                   document_id=ids["dogs"])
        assert hits and {h[2] for h in hits} == {ids["dogs"]}

    async def test_filename_filter(self, memory):
        ids = await self._fill(memory)
        flt = DocumentVectorSearchFilters(filename_contains="dogs")
        hits = await memory.search("кошки собаки", k=5, strategy="vector", use_reranking=False, filters=flt)
        assert hits and {h[2] for h in hits} == {ids["dogs"]}

    @pytest.mark.parametrize("strategy", ["hybrid", "vector", "lexical", "reranking"])
    async def test_rerank_applied(self, memory, client, rerank_on, strategy):
        await self._fill(memory)
        hits, trace = await memory.search("кошки собаки", k=3, strategy=strategy, use_reranking=True,
                                          return_trace=True)
        assert client.rerank_calls and trace.used_rerank is True and hits

    async def test_entity_lane_and_filename_anchor(self, memory):
        ids = await self._fill(memory)
        hits, trace = await memory.search("Что сказано в Налоги_отчёт.txt про Константин", k=3,
                                          strategy="reranking", use_reranking=False, return_trace=True)
        assert ids["taxes"] in {h[2] for h in hits}
        assert "entity_lane" in [s["stage"] for s in trace.steps]

    async def test_bm25_index_is_per_dimension(self, memory):
        await self._fill(memory)
        a = memory._bm25_for(memory.vector_repo)
        assert memory._bm25_for(memory.vector_repo) is a

    async def test_bm25_store_enabled(self, memory, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings().rag, "bm25_store_enabled", True)
        await self._fill(memory)
        idx = memory._bm25_for(memory.vector_repo)
        assert await idx.ensure_built() is True
        assert any(key.startswith("memory") for key in memory.vector_repo.store)
        memory._bm25_by_dim.drop(lambda k: True)
        again = memory._bm25_for(memory.vector_repo)
        assert again is not idx and await again.ensure_built() is True

    async def test_bm25_store_disabled(self, memory, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings().rag, "bm25_store_enabled", False)
        await self._fill(memory)
        idx = memory._bm25_for(memory.vector_repo)
        assert await idx.ensure_built() is True and memory.vector_repo.store == {}


class TestManagement:
    async def test_check_duplicate(self, memory):
        first = await memory.index_document(CATS, "cats.txt")
        assert await memory.check_duplicate(content_hash=file_content_hash(CATS)) == {
            "duplicate": True, "document_id": first["document_id"], "filename": "cats.txt"}
        assert await memory.check_duplicate(content_hash="deadbeef") == {"duplicate": False}

    async def test_list_documents(self, memory):
        await memory.index_document(CATS, "cats.txt")
        rows = await memory.list_documents()
        assert [r["filename"] for r in rows] == ["cats.txt"]

    async def test_delete_document(self, memory, graph):
        res = await memory.index_document(CATS, "cats.txt", minio_object="o/c", minio_bucket="b")
        doc_id = res["document_id"]
        out = await memory.delete_document(doc_id)
        assert out == {"ok": True, "document_id": doc_id, "minio_object": "o/c", "minio_bucket": "b"}
        assert memory.doc_repo.docs == {} and memory.vector_repo.rows == {}
        assert graph.deleted == [("memory", doc_id)]

    async def test_delete_unknown(self, memory):
        assert await memory.delete_document(404) == {"ok": False, "error": "not_found"}

    async def test_delete_survives_graph_error(self, memory, graph):
        res = await memory.index_document(CATS, "cats.txt")
        graph.fail_delete = RuntimeError("graph down")
        assert (await memory.delete_document(res["document_id"]))["ok"] is True

    async def test_delete_without_graph(self, memory_nograph):
        res = await memory_nograph.index_document(CATS, "cats.txt")
        assert (await memory_nograph.delete_document(res["document_id"]))["ok"] is True


class TestDocActors:
    def test_object_with_string_metadata(self):
        doc = Document(filename="a.txt", metadata={})
        object.__setattr__(doc, "metadata", '{"owner_user_id": " Al ", "uploaded_by": "bo"}')
        assert memory_rag_service._doc_actors(doc) == ("Al", "bo", "a.txt")

    def test_dict_with_bad_json_and_name_fallback(self):
        assert memory_rag_service._doc_actors({"metadata": "{bad", "name": "n"}) == ("-", "-", "n")

    def test_empty(self):
        assert memory_rag_service._doc_actors({}) == ("-", "-", "?")
