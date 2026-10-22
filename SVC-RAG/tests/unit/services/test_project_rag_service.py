"""ProjectRagService на in-memory репозиториях: индексация файлов проекта, реиндекс, поиск, удаление."""

import json
from datetime import datetime, timedelta

import pytest

from app.core.config import get_settings
from app.database.models import DocumentVector
from app.database.search_filters import DocumentVectorSearchFilters
from app.services import embed_routing, hierarchical_indexing, project_rag_service, text_duplicate
from app.services.document_duplicate import DUPLICATE_CODE, file_content_hash

from .fakes import BIG, CATS, DOGS, TAXES

pytestmark = pytest.mark.unit

P1 = "proj-1"
P2 = "proj-2"


def _hier_stub(count=4, fail=None):
    calls = []

    async def fake(text, doc_id, *, filename, vector_repo, rag_client, chunk_size=None,
                   chunk_overlap=None, model=None):
        calls.append({"doc_id": doc_id, "filename": filename, "chunk_size": chunk_size, "model": model})
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
    def test_per_key_generation(self):
        key = "project:r1-test"
        before = project_rag_service.current_project_reindex_generation(key)
        gen = project_rag_service.bump_project_reindex_generation(key)
        assert gen == before + 1
        assert project_rag_service.current_project_reindex_generation(key) == gen
        assert project_rag_service.project_generation_is_current(key, gen) is True
        assert project_rag_service.project_generation_is_current(key, gen - 1) is False
        assert project_rag_service.project_generation_is_current(key, None) is True


class TestDocActors:
    def test_object_with_string_metadata(self):
        class Doc:
            filename = "a.txt"
            metadata = json.dumps({"owner_user_id": " Al ", "uploaded_by": "bo"})

        assert project_rag_service._doc_actors(Doc()) == ("Al", "bo", "a.txt")

    def test_dict_bad_json_and_name_fallback(self):
        assert project_rag_service._doc_actors({"metadata": "{bad", "name": "n"}) == ("-", "-", "n")

    def test_empty(self):
        assert project_rag_service._doc_actors({}) == ("-", "-", "?")

    def test_object_without_metadata(self):
        class Doc:
            filename = None

        assert project_rag_service._doc_actors(Doc()) == ("-", "-", "?")


class TestIndexDocument:
    async def test_success(self, project, graph, client):
        res = await project.index_document(
            CATS, "cats.txt", P1, minio_object="o/c", minio_bucket="b",
            owner_user_id=" Alice ", uploaded_by="Bob",
        )
        assert res == {"ok": True, "document_id": 1, "filename": "cats.txt",
                       "chunks_count": res["chunks_count"], "project_id": P1}
        row = project.doc_repo.rows[1]
        meta = row["metadata"]
        assert row["project_id"] == P1 and meta["project_id"] == P1 and meta["source"] == "project"
        assert meta["owner_user_id"] == "alice" and meta["uploaded_by"] == "bob"
        assert meta["minio_object"] == "o/c" and meta["minio_bucket"] == "b"
        assert meta["content_hash"] == file_content_hash(CATS) and meta["size"] == len(CATS)
        vectors = project.vector_repo.rows[1]
        assert len(vectors) == res["chunks_count"] >= 1
        assert vectors[0].metadata["document_filename"] == "cats.txt"
        assert graph.rebuilt[0]["store_type"] == "project"
        assert all(kind == "document" for _, kind in client.embed_calls)

    async def test_without_optional_metadata_and_graph(self, project_nograph):
        res = await project_nograph.index_document(CATS, "cats.txt", P1)
        meta = project_nograph.doc_repo.rows[res["document_id"]]["metadata"]
        assert res["ok"]
        assert not ({"minio_object", "minio_bucket", "owner_user_id", "uploaded_by"} & set(meta))

    async def test_byte_duplicate_is_scoped_to_project(self, project):
        first = await project.index_document(CATS, "cats.txt", P1)
        again = await project.index_document(CATS, "other.txt", P1)
        assert again["code"] == DUPLICATE_CODE and again["document_id"] == first["document_id"]
        other_project = await project.index_document(CATS, "cats.txt", P2)
        assert other_project["ok"] is True

    async def test_same_text_in_other_bytes(self, project):
        first = await project.index_document(CATS, "cats.txt", P1)
        again = await project.index_document(CATS + b"\n", "cats-copy.txt", P1)
        assert again["code"] == DUPLICATE_CODE and again["document_id"] == first["document_id"]
        assert len(project.doc_repo.rows) == 1

    async def test_text_of_older_documents_is_read_from_repo_when_not_cached(self, project):
        first = await project.index_document(CATS, "cats.txt", P1)
        text_duplicate._fingerprints.clear()
        again = await project.index_document(CATS + b"\n", "cats-copy.txt", P1)
        assert again["code"] == DUPLICATE_CODE and again["document_id"] == first["document_id"]

    async def test_unsupported_format(self, project):
        res = await project.index_document(b"\x00\x01\x02", "file.xyz", P1)
        assert res == {"ok": False, "error": "Не удалось извлечь текст или формат не поддерживается",
                       "document_id": None}

    async def test_empty_text(self, project):
        res = await project.index_document(b" \n ", "empty.txt", P1)
        assert res["ok"] is False and res["document_id"] is None
        assert project.doc_repo.rows == {}

    async def test_route_failure_returns_error(self, project, monkeypatch):
        async def boom(*a, **kw):
            raise RuntimeError("модель недоступна")

        monkeypatch.setattr(embed_routing, "resolve_for", boom)
        res = await project.index_document(CATS, "cats.txt", P1, model="m", provider="p")
        assert res == {"ok": False, "error": "Модель эмбеддинга: модель недоступна", "document_id": None}
        assert project.doc_repo.rows == {}

    async def test_insert_failure(self, project):
        project.doc_repo.create_returns_none = True
        res = await project.index_document(CATS, "cats.txt", P1)
        assert res["error"] == "Ошибка сохранения документа в БД"

    async def test_race_duplicate_after_insert(self, project, monkeypatch):
        answers = iter([None, {"id": 55, "filename": "early.txt"}])

        async def fake_find(**kw):
            return next(answers)

        monkeypatch.setattr(text_duplicate, "find_text_duplicate", fake_find)
        res = await project.index_document(CATS, "late.txt", P1)
        assert res["code"] == DUPLICATE_CODE and res["document_id"] == 55
        assert project.doc_repo.deleted == [1] and project.doc_repo.rows == {}

    async def test_no_chunks(self, project, monkeypatch):
        monkeypatch.setattr(project_rag_service, "split_into_chunks_with_meta", lambda *a, **kw: [])
        res = await project.index_document(CATS, "cats.txt", P1)
        assert res == {"ok": False, "error": "Не удалось нарезать чанки", "document_id": None}
        assert project.doc_repo.deleted == [1]

    async def test_embedding_failure_rolls_back(self, project, client):
        client.fail = ConnectionError("models down")
        res = await project.index_document(CATS, "cats.txt", P1)
        assert res == {"ok": False, "error": "Ошибка эмбеддингов: models down", "document_id": None}
        assert project.doc_repo.rows == {} and project.vector_repo.rows == {}

    async def test_embedding_failure_reports_to_backend(self, project, client, failed_reports):
        # BUG-RAG-08: как в KB, backend узнаёт о неудаче и убирает исходник.
        client.fail = ConnectionError("models down")
        await project.index_document(CATS, "cats.txt", P1, minio_object="o/k", minio_bucket="b")
        assert len(failed_reports) == 1
        assert failed_reports[0]["minio_object"] == "o/k" and failed_reports[0]["minio_bucket"] == "b"
        assert failed_reports[0]["store"] == "project" and "models down" in failed_reports[0]["reason"]

    async def test_hierarchical_failure_reports_to_backend(self, project, monkeypatch, failed_reports):
        monkeypatch.setattr(project_rag_service, "index_document_hierarchically",
                            _hier_stub(fail=RuntimeError("llm down")))
        await project.index_document(CATS, "cats.txt", P1, chunking_strategy="hierarchical",
                                     minio_object="o/k", minio_bucket="b")
        assert failed_reports[0]["reason"] == "Иерархическая индексация: llm down"
        assert failed_reports[0]["store"] == "project"

    async def test_partial_wave_failure_leaves_no_vectors(self, project, monkeypatch):
        monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", "2")
        project.vector_repo.fail_create_on_call = 2
        res = await project.index_document(BIG, "big.txt", P1, chunk_size=300, chunk_overlap=0)
        assert res["ok"] is False
        assert project.vector_repo.rows == {} and project.doc_repo.rows == {}

    async def test_graph_failure_does_not_fail_indexing(self, project, graph):
        graph.fail_rebuild = RuntimeError("graph down")
        assert (await project.index_document(CATS, "cats.txt", P1))["ok"] is True

    async def test_hierarchical_success(self, project, graph, monkeypatch):
        stub = _hier_stub(count=3)
        monkeypatch.setattr(project_rag_service, "index_document_hierarchically", stub)
        res = await project.index_document(CATS, "cats.txt", P1, chunking_strategy="HIERARCHICAL",
                                           chunk_size=900, owner_user_id="a", uploaded_by="b")
        assert res == {"ok": True, "document_id": 1, "filename": "cats.txt", "chunks_count": 3,
                       "project_id": P1}
        assert stub.calls[0]["chunk_size"] == 900
        assert graph.rebuilt[0]["store_type"] == "project" and len(graph.rebuilt[0]["chunks"]) == 3

    async def test_hierarchical_failure_rolls_back(self, project, monkeypatch):
        monkeypatch.setattr(project_rag_service, "index_document_hierarchically",
                            _hier_stub(fail=RuntimeError("llm down")))
        res = await project.index_document(CATS, "cats.txt", P1, chunking_strategy="hierarchical")
        assert res == {"ok": False, "error": "Иерархическая индексация: llm down", "document_id": None}
        assert project.doc_repo.rows == {}

    async def test_hierarchical_without_graph_repo(self, project_nograph, monkeypatch):
        monkeypatch.setattr(project_rag_service, "index_document_hierarchically", _hier_stub(count=2))
        res = await project_nograph.index_document(CATS, "cats.txt", P1, chunking_strategy="hierarchical")
        assert res["ok"] and res["chunks_count"] == 2

    async def test_graph_not_rebuilt_without_chunks(self, project, graph, monkeypatch):
        monkeypatch.setattr(project_rag_service, "index_document_hierarchically", _hier_stub(count=0))
        await project.index_document(CATS, "cats.txt", P1, chunking_strategy="hierarchical")
        assert graph.rebuilt == []

    async def test_graph_rebuild_error_in_hierarchical_branch_is_swallowed(self, project, graph, monkeypatch):
        monkeypatch.setattr(project_rag_service, "index_document_hierarchically", _hier_stub(count=2))
        graph.fail_rebuild = RuntimeError("boom")
        assert (await project.index_document(CATS, "cats.txt", P1, chunking_strategy="hierarchical"))["ok"]

    async def test_indexing_marks_only_this_project_bm25_dirty(self, project):
        mine = project._bm25_for_project(P1)
        other = project._bm25_for_project(P2)
        mine.needs_rebuild = other.needs_rebuild = False
        await project.index_document(CATS, "cats.txt", P1)
        assert mine.needs_rebuild is True and other.needs_rebuild is False


class TestBm25:
    def test_index_is_cached_per_project_and_dimension(self, project):
        a = project._bm25_for_project(P1)
        assert project._bm25_for_project(P1) is a
        assert project._bm25_for_project(P2) is not a

    def test_mark_dirty_for_all_projects(self, project):
        a = project._bm25_for_project(P1)
        b = project._bm25_for_project(P2)
        a.needs_rebuild = b.needs_rebuild = False
        project._mark_bm25_dirty()
        assert a.needs_rebuild and b.needs_rebuild

    async def test_store_enabled_saves_and_reloads(self, project, monkeypatch):
        monkeypatch.setattr(get_settings().rag, "bm25_store_enabled", True)
        await project.index_document(CATS, "cats.txt", P1)
        idx = project._bm25_for_project(P1)
        assert await idx.ensure_built() is True
        assert any(key.startswith("project") for key in project.vector_repo.store)
        project._bm25_by_key.drop(lambda k: True)
        again = project._bm25_for_project(P1)

        async def no_fetch(*a, **kw):
            raise AssertionError("корпус не должен читаться: индекс лежит в хранилище")

        project.vector_repo.get_all_contents_for_bm25 = no_fetch
        assert await again.ensure_built() is True

    async def test_store_disabled(self, project, monkeypatch):
        monkeypatch.setattr(get_settings().rag, "bm25_store_enabled", False)
        await project.index_document(CATS, "cats.txt", P1)
        idx = project._bm25_for_project(P1)
        assert await idx.ensure_built() is True and project.vector_repo.store == {}


class TestReindexDocument:
    async def _indexed(self, project, data=CATS, name="cats.txt", pid=P1, **kw):
        res = await project.index_document(data, name, pid, **kw)
        assert res["ok"]
        return await project.doc_repo.get_document(res["document_id"])

    async def test_empty_text(self, project):
        assert await project.reindex_document({"id": 1, "project_id": P1, "content": "  "}) == 0

    async def test_replaces_vectors_with_new_chunking(self, project, graph):
        doc = await self._indexed(project, BIG, "big.txt", chunk_size=300, chunk_overlap=0)
        old = len(project.vector_repo.rows[doc["id"]])
        graph.rebuilt.clear()
        created = await project.reindex_document(doc, chunk_size=1500, chunk_overlap=0)
        assert 0 < created < old
        assert len(project.vector_repo.rows[doc["id"]]) == created
        assert graph.rebuilt[0]["store_type"] == "project"

    async def test_unknown_filename_and_missing_project(self, project):
        doc = await self._indexed(project)
        doc.pop("filename")
        doc["project_id"] = None
        assert await project.reindex_document(doc) >= 1

    async def test_hierarchical(self, project, graph, monkeypatch):
        doc = await self._indexed(project)
        monkeypatch.setattr(hierarchical_indexing, "index_document_hierarchically", _hier_stub(count=5))
        assert await project.reindex_document(doc, chunking_strategy="Hierarchical") == 5
        assert len(project.vector_repo.rows[doc["id"]]) == 5
        assert graph.rebuilt[-1]["document_id"] == doc["id"]

    async def test_no_chunks(self, project, monkeypatch):
        doc = await self._indexed(project)
        monkeypatch.setattr(project_rag_service, "split_into_chunks_with_meta", lambda *a, **kw: [])
        assert await project.reindex_document(doc) == 0

    async def test_graph_error_is_swallowed(self, project, graph):
        doc = await self._indexed(project)
        graph.fail_rebuild = RuntimeError("boom")
        assert await project.reindex_document(doc) >= 1

    async def test_without_graph_repo(self, project_nograph):
        doc = await self._indexed(project_nograph)
        assert await project_nograph.reindex_document(doc) >= 1

    async def test_rebuild_graph_helper_without_graph_or_vectors(self, project_nograph, project, graph):
        await project_nograph._rebuild_graph_for_document(1)  # без graph_repo — тихо
        await project._rebuild_graph_for_document(404)  # нет векторов — граф не трогаем
        assert graph.rebuilt == []

    async def test_rebuild_graph_helper_swallows_errors(self, project, graph):
        doc = await self._indexed(project)
        graph.fail_rebuild = RuntimeError("boom")
        await project._rebuild_graph_for_document(doc["id"])


class TestReindexAll:
    async def _fill(self, project):
        a = (await project.index_document(CATS, "cats.txt", P1, owner_user_id="alice"))["document_id"]
        b = (await project.index_document(DOGS, "dogs.txt", P1, owner_user_id="bob"))["document_id"]
        c = (await project.index_document(TAXES, "taxes.txt", P2, owner_user_id="alice"))["document_id"]
        return a, b, c

    async def test_reindexes_only_given_project(self, project):
        await self._fill(project)
        res = await project.reindex_all(P1)
        assert res["project_id"] == P1 and res["documents"] == 2 and res["errors"] == 0
        assert res["chunks"] >= 2

    async def test_falls_back_to_brief_row_when_document_vanished(self, project, monkeypatch):
        await self._fill(project)

        async def gone(doc_id):
            return None

        monkeypatch.setattr(project.doc_repo, "get_document", gone)
        # у «краткой» записи текста нет — документ дает 0 чанков, но не ошибку
        res = await project.reindex_all(P1)
        assert res["documents"] == 2 and res["chunks"] == 0 and res["errors"] == 0

    async def test_document_error_is_counted(self, project, client):
        await self._fill(project)
        real = client.embed
        calls = {"n": 0}

        async def flaky(texts, model=None, kind=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("first")
            return await real(texts, model=model, kind=kind)

        client.embed = flaky
        res = await project.reindex_all(P1)
        assert res["errors"] == 1 and res["documents"] == 1

    async def test_generation_abort(self, project):
        await self._fill(project)
        key = "project:r1-abort"
        gen = project_rag_service.bump_project_reindex_generation(key)
        project_rag_service.bump_project_reindex_generation(key)
        res = await project.reindex_all(P1, generation=gen, generation_key=key)
        assert res["documents"] == 0 and res["chunks"] == 0

    async def test_accepts_precomputed_route(self, project):
        await self._fill(project)
        route = await project._route()
        res = await project.reindex_all(P1, route=route)
        assert res["documents"] == 2

    async def test_all_projects(self, project):
        await self._fill(project)
        res = await project.reindex_all_projects()
        assert res["projects"] == 2 and res["documents"] == 3 and res["chunks"] >= 3

    async def test_all_projects_generation_abort(self, project):
        await self._fill(project)
        key = "*r1-all"
        gen = project_rag_service.bump_project_reindex_generation(key)
        project_rag_service.bump_project_reindex_generation(key)
        res = await project.reindex_all_projects(generation=gen, generation_key=key)
        assert res == {"projects": 2, "documents": 0, "chunks": 0}

    async def test_all_projects_survives_failing_project(self, project, monkeypatch):
        await self._fill(project)
        real = project.reindex_all

        async def flaky(pid, **kw):
            if pid == P1:
                raise RuntimeError("project broken")
            return await real(pid, **kw)

        monkeypatch.setattr(project, "reindex_all", flaky)
        res = await project.reindex_all_projects()
        assert res["projects"] == 2 and res["documents"] == 1

    async def test_by_owner(self, project):
        await self._fill(project)
        res = await project.reindex_all_projects(owner_user_id="alice")
        assert res["owner_user_id"] == "alice"
        assert res["documents"] == 2 and res["projects"] == 2 and res["chunks"] >= 2

    async def test_by_owner_without_documents(self, project):
        await self._fill(project)
        res = await project.reindex_all_projects(owner_user_id="nobody")
        assert res == {"projects": 0, "documents": 0, "chunks": 0, "owner_user_id": "nobody"}

    async def test_by_owner_generation_abort(self, project):
        await self._fill(project)
        key = "owner:r1-abort"
        gen = project_rag_service.bump_project_reindex_generation(key)
        project_rag_service.bump_project_reindex_generation(key)
        res = await project.reindex_all_projects(owner_user_id="alice", generation=gen, generation_key=key)
        assert res["documents"] == 0

    async def test_by_owner_counts_errors_and_skips_rows_without_project(self, project, client, monkeypatch):
        a, b, c = await self._fill(project)
        project.doc_repo.rows[c]["project_id"] = ""
        real = client.embed
        calls = {"n": 0}

        async def flaky(texts, model=None, kind=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("first")
            return await real(texts, model=model, kind=kind)

        client.embed = flaky
        res = await project.reindex_all_projects(owner_user_id="alice")
        assert res["documents"] == 1 and res["projects"] == 0  # второй упал, у первого нет проекта


class TestSearch:
    async def _fill(self, project):
        ids = {}
        for key, data, name, pid in (("cats", CATS, "cats.txt", P1), ("dogs", DOGS, "dogs.txt", P1),
                                     ("taxes", TAXES, "Налоги_отчёт.txt", P1),
                                     ("alien", CATS + " Инопланетные кошки.".encode(), "alien-cats.txt", P2)):
            ids[key] = (await project.index_document(data, name, pid))["document_id"]
        return ids

    @pytest.mark.parametrize("strategy", ["vector", "hybrid", "lexical", "auto", "raw_cosine", "graph", None])
    async def test_strategies_stay_inside_project(self, project, strategy):
        ids = await self._fill(project)
        hits = await project.search("расскажи про кошки", P1, k=5, strategy=strategy, use_reranking=False)
        assert hits and hits[0][2] == ids["cats"]
        assert ids["alien"] not in {h[2] for h in hits}

    async def test_other_project_sees_only_its_documents(self, project):
        ids = await self._fill(project)
        hits = await project.search("кошки", P2, k=5, strategy="hybrid", use_reranking=False)
        assert {h[2] for h in hits} == {ids["alien"]}

    async def test_return_trace(self, project):
        await self._fill(project)
        hits, trace = await project.search("кошки", P1, k=2, strategy="vector", use_reranking=False,
                                           return_trace=True)
        assert trace.store == "project" and len(hits) <= 2

    async def test_document_id_narrows(self, project):
        ids = await self._fill(project)
        hits = await project.search("кошки", P1, k=5, strategy="hybrid", use_reranking=False,
                                    document_id=ids["dogs"])
        assert hits and {h[2] for h in hits} == {ids["dogs"]}

    async def test_filters(self, project):
        ids = await self._fill(project)
        flt = DocumentVectorSearchFilters(filename_contains="dogs")
        hits = await project.search("кошки собаки", P1, k=5, strategy="vector", use_reranking=False,
                                    filters=flt)
        assert {h[2] for h in hits} == {ids["dogs"]}
        future = datetime.utcnow() + timedelta(days=1)
        none = await project.search("кошки", P1, k=5, strategy="vector", use_reranking=False,
                                    filters=DocumentVectorSearchFilters(date_from=future))
        assert none == []

    @pytest.mark.parametrize("strategy", ["hybrid", "vector", "lexical", "reranking"])
    async def test_rerank_applied(self, project, client, rerank_on, strategy):
        await self._fill(project)
        hits, trace = await project.search("кошки собаки", P1, k=3, strategy=strategy,
                                           use_reranking=True, return_trace=True)
        assert client.rerank_calls and trace.used_rerank is True and hits

    async def test_filename_lookup_is_scoped_to_project(self, project):
        ids = await self._fill(project)
        # alien-cats.txt лежит в P2: в P1 по имени файла он не находится
        hits, trace = await project.search("Что сказано в alien-cats.txt", P1, k=3,
                                           strategy="reranking", use_reranking=False, return_trace=True)
        assert ids["alien"] not in {h[2] for h in hits}
        hits, trace = await project.search("Что сказано в Налоги_отчёт.txt про Константин", P1, k=3,
                                           strategy="reranking", use_reranking=False, return_trace=True)
        assert ids["taxes"] in {h[2] for h in hits}
        assert "entity_lane" in [s["stage"] for s in trace.steps]

    async def test_tuple_chunk_lookup_from_project_repo_is_supported(self, project):
        ids = await self._fill(project)
        project.vector_repo.tuple_single = True  # project-репозиторий отдаёт (dv, score)
        hits = await project.search("кошки", P1, k=3, strategy="lexical", use_reranking=False)
        assert hits and hits[0][2] == ids["cats"]


class TestManagement:
    async def test_check_duplicate(self, project):
        first = await project.index_document(CATS, "cats.txt", P1)
        got = await project.check_duplicate(project_id=P1, content_hash=file_content_hash(CATS))
        assert got == {"duplicate": True, "document_id": first["document_id"], "filename": "cats.txt"}
        assert await project.check_duplicate(project_id=P2, content_hash=file_content_hash(CATS)) == {
            "duplicate": False}

    async def test_list_documents(self, project):
        res = await project.index_document(CATS, "cats.txt", P1)
        rows = await project.list_documents(P1)
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == res["document_id"] and row["filename"] == "cats.txt"
        # file_type здесь не проверяем: см. test_file_type_known_issue.py (баг парсера/сервисов)
        assert row["size"] == len(CATS) and row["project_id"] == P1
        assert row["created_at"] == project.doc_repo.rows[res["document_id"]]["created_at"].isoformat()
        assert await project.list_documents(P2) == []

    async def test_list_documents_without_created_at_and_metadata(self, project):
        project.doc_repo.rows[9] = {"id": 9, "project_id": P1, "filename": "x", "content": None,
                                    "metadata": None, "created_at": None, "updated_at": None}
        row = (await project.list_documents(P1))[0]
        assert row["created_at"] is None and row["size"] == 0 and row["file_type"] == ""

    async def test_get_document_chunks(self, project):
        res = await project.index_document(BIG, "big.txt", P1, chunk_size=300, chunk_overlap=0)
        doc_id = res["document_id"]
        first = await project.get_document_chunks(P1, doc_id)
        assert [c[2] for c in first] == [0, 1, 2] and all(c[1] == doc_id for c in first)
        page = await project.get_document_chunks(P1, doc_id, start=1, limit=2)
        assert [c[2] for c in page] == [1, 2]

    async def test_get_document_chunks_negative_indexes_go_last(self, project):
        res = await project.index_document(CATS, "cats.txt", P1)
        doc_id = res["document_id"]
        await project.vector_repo.create_vectors_batch([
            DocumentVector(document_id=doc_id, chunk_index=-1, embedding=[0.1] * 8, content="summary")])
        got = await project.get_document_chunks(P1, doc_id, start=0, limit=10)
        assert got[-1][2] == -1

    async def test_get_document_chunks_guards(self, project):
        res = await project.index_document(CATS, "cats.txt", P1)
        assert await project.get_document_chunks(P1, 404) is None
        assert await project.get_document_chunks(P2, res["document_id"]) is None  # чужой проект
        project.doc_repo.rows[10] = {"id": 10, "project_id": P1, "filename": "e", "content": "",
                                     "metadata": {}, "created_at": None, "updated_at": None}
        assert await project.get_document_chunks(P1, 10) == []

    async def test_delete_document(self, project, graph):
        res = await project.index_document(CATS, "cats.txt", P1, minio_object="o/c", minio_bucket="b")
        doc_id = res["document_id"]
        out = await project.delete_document(doc_id)
        assert out == {"ok": True, "document_id": doc_id, "minio_object": "o/c", "minio_bucket": "b"}
        assert project.doc_repo.rows == {} and project.vector_repo.rows == {}
        assert graph.deleted == [("project", doc_id)]

    async def test_delete_with_matching_project_id(self, project):
        res = await project.index_document(CATS, "cats.txt", P1)
        out = await project.delete_document(res["document_id"], project_id=P1)
        assert out["ok"] is True and project.doc_repo.rows == {}

    async def test_delete_of_foreign_project_document_is_refused(self, project, graph):
        """BUG-RAG-16: документ чужого проекта не удаляется и выглядит как несуществующий."""
        res = await project.index_document(CATS, "cats.txt", P1)
        out = await project.delete_document(res["document_id"], project_id=P2)
        assert out == {"ok": False, "error": "not_found"}
        assert res["document_id"] in project.doc_repo.rows and project.vector_repo.rows
        assert graph.deleted == []

    async def test_delete_marks_only_project_bm25_dirty(self, project):
        res = await project.index_document(CATS, "cats.txt", P1)
        mine, other = project._bm25_for_project(P1), project._bm25_for_project(P2)
        mine.needs_rebuild = other.needs_rebuild = False
        await project.delete_document(res["document_id"])
        assert mine.needs_rebuild is True and other.needs_rebuild is False

    async def test_delete_unknown(self, project):
        assert await project.delete_document(404) == {"ok": False, "error": "not_found"}

    async def test_delete_survives_graph_error_and_missing_project_meta(self, project, graph):
        project.doc_repo.rows[3] = {"id": 3, "project_id": P1, "filename": "x", "content": "t",
                                    "metadata": None, "created_at": None, "updated_at": None}
        graph.fail_delete = RuntimeError("graph down")
        out = await project.delete_document(3)
        assert out["ok"] is True and out["minio_object"] is None

    async def test_delete_without_graph(self, project_nograph):
        res = await project_nograph.index_document(CATS, "cats.txt", P1)
        assert (await project_nograph.delete_document(res["document_id"]))["ok"] is True

    async def test_delete_by_project(self, project, monkeypatch):
        monkeypatch.setattr(get_settings().rag, "bm25_store_enabled", True)
        await project.index_document(CATS, "cats.txt", P1, minio_object="o/1", minio_bucket="b")
        await project.index_document(DOGS, "dogs.txt", P1)
        await project.index_document(TAXES, "taxes.txt", P2, minio_object="o/3", minio_bucket="b")
        project._bm25_for_project(P1)
        project._bm25_for_project(P2)
        out = await project.delete_by_project(P1)
        assert out == {"ok": True, "project_id": P1, "deleted_count": 2,
                       "minio_keys": [{"minio_object": "o/1", "minio_bucket": "b"}]}
        assert project.vector_repo.store_dropped == [P1]
        remaining = [k for k, _ in project._bm25_by_key.items()]
        assert all(k[0] != P1 for k in remaining) and any(k[0] == P2 for k in remaining)

    async def test_delete_by_project_store_error_is_swallowed(self, project, monkeypatch):
        monkeypatch.setattr(get_settings().rag, "bm25_store_enabled", True)
        await project.index_document(CATS, "cats.txt", P1)
        project.vector_repo.fail_store_drop = RuntimeError("db down")
        assert (await project.delete_by_project(P1))["deleted_count"] == 1

    async def test_delete_by_project_with_store_disabled(self, project, monkeypatch):
        monkeypatch.setattr(get_settings().rag, "bm25_store_enabled", False)
        await project.index_document(CATS, "cats.txt", P1)
        await project.delete_by_project(P1)
        assert project.vector_repo.store_dropped == []
