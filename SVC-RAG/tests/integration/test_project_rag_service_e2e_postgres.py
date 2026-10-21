"""Сквозной сценарий ProjectRagService на реальном pgvector: изоляция проектов, поиск, удаление."""

import asyncio

import pytest

from app.database.graph_repository import GraphRepository
from app.database.project_rag_repository import (
    ProjectRagDocumentRepository,
    ProjectRagVectorRepository,
)
from app.services import index_failed_report, text_duplicate
from app.services.project_rag_service import ProjectRagService

pytestmark = pytest.mark.integration

DIM = 8
TOPICS = ["кошк", "собак", "птиц", "налог", "космос", "музык", "футбол"]


def vector_for(text: str):
    low = (text or "").lower()
    vec = [float(low.count(t)) for t in TOPICS] + [0.01]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


class FakeRagClient:
    def __init__(self):
        self.fail = None
        self.calls = 0

    async def embed(self, texts, model=None, kind=None):
        if self.fail:
            raise self.fail
        self.calls += 1
        return [vector_for(t) for t in texts]

    async def embed_single(self, text, model=None, kind=None):
        return vector_for(text)


CATS = ("Кошки любят спать. Кошка мурлычет на окне, а кошки охотятся на мышей. " * 6).encode()
DOGS = ("Собаки верные друзья. Собака охраняет дом, а собаки любят гулять во дворе. " * 6).encode()
TAXES = ("Налог на прибыль уплачивается ежеквартально. Налоговая служба проверяет налог. " * 6).encode()


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    for name in ("_SEEN", "_seen", "_REGISTRY", "_registry"):
        store = getattr(text_duplicate, name, None)
        if hasattr(store, "clear"):
            store.clear()
    reports = []

    async def report(**kw):
        reports.append(kw)

    monkeypatch.setattr(index_failed_report, "report_index_failed", report)
    yield reports


@pytest.fixture
async def svc(pg_connection):
    docs = ProjectRagDocumentRepository(pg_connection)
    vecs = ProjectRagVectorRepository(pg_connection, embedding_dim=DIM)
    graph = GraphRepository(pg_connection)
    await docs.create_tables()
    await vecs.create_tables()
    await graph.create_tables()
    service = ProjectRagService(docs, vecs, FakeRagClient(), graph_repo=graph)
    service.fake = service.rag_client
    return service


async def _count(pg_connection, table, where="TRUE"):
    async with await pg_connection.acquire() as conn:
        return await conn.fetchval(f"SELECT COUNT(*) FROM {table} WHERE {where}")


async def test_index_and_search_inside_one_project(svc, pg_connection):
    cats = await svc.index_document(CATS, "cats.txt", "p1", owner_user_id=" Alice ", uploaded_by="Bob")
    dogs = await svc.index_document(DOGS, "dogs.txt", "p1")
    assert cats["ok"] and dogs["ok"]
    doc = await svc.doc_repo.get_document(cats["document_id"])
    assert doc["project_id"] == "p1" and doc["metadata"]["owner_user_id"] == "alice"
    hits = await svc.search("кошки", "p1", k=3, use_reranking=False)
    assert hits and hits[0][2] == cats["document_id"]
    assert await _count(pg_connection, "rag_graph_nodes", "store_type = 'project'") > 0


async def test_projects_are_isolated_in_search(svc):
    a = (await svc.index_document(CATS, "cats.txt", "p1"))["document_id"]
    b = (await svc.index_document(DOGS, "dogs.txt", "p2"))["document_id"]
    in_p1 = await svc.search("кошки собаки", "p1", k=10, use_reranking=False)
    in_p2 = await svc.search("кошки собаки", "p2", k=10, use_reranking=False)
    assert {h[2] for h in in_p1} == {a} and {h[2] for h in in_p2} == {b}
    assert await svc.search("кошки", "no-such-project", k=5, use_reranking=False) == []


async def test_same_file_in_two_projects_is_not_a_duplicate(svc, pg_connection):
    one = await svc.index_document(CATS, "cats.txt", "p1")
    two = await svc.index_document(CATS, "cats.txt", "p2")
    assert one["ok"] and two["ok"] and one["document_id"] != two["document_id"]
    assert await _count(pg_connection, "project_rag_documents") == 2


async def test_duplicate_inside_project_is_skipped(svc, pg_connection):
    first = await svc.index_document(CATS, "cats.txt", "p1")
    again = await svc.index_document(CATS, "renamed.txt", "p1")
    assert again["document_id"] == first["document_id"]
    assert await _count(pg_connection, "project_rag_documents") == 1
    assert await svc.check_duplicate(project_id="p2", filename="cats.txt", size=len(CATS)) == {"duplicate": False}


async def test_embedding_failure_cleans_up_but_does_not_report_known_issue(
    svc, pg_connection, _isolated_state
):
    """Известная проблема: в отличие от KB, проектная ветка не зовёт
    ``report_index_failed`` — backend не узнаёт о неудаче, и исходник в MinIO
    остаётся без документа (по таймауту backend его сам не удаляет)."""
    svc.fake.fail = ConnectionError("models down")
    res = await svc.index_document(CATS, "cats.txt", "p1", minio_object="o/k", minio_bucket="b")
    assert res["ok"] is False and res["document_id"] is None
    assert await _count(pg_connection, "project_rag_documents") == 0
    assert await _count(pg_connection, "project_rag_vectors") == 0
    assert _isolated_state == []


async def test_empty_file_creates_nothing(svc, pg_connection):
    res = await svc.index_document(b"  \n ", "empty.txt", "p1")
    assert res["ok"] is False
    assert await _count(pg_connection, "project_rag_documents") == 0


async def test_list_documents_per_project(svc):
    await svc.index_document(CATS, "cats.txt", "p1")
    await svc.index_document(DOGS, "dogs.txt", "p2")
    items = await svc.list_documents("p1")
    assert [i["filename"] for i in items] == ["cats.txt"]
    assert items[0]["project_id"] == "p1" and items[0]["size"] == len(CATS)
    assert isinstance(items[0]["created_at"], str)
    assert await svc.list_documents("nope") == []


async def test_get_document_chunks_respects_project_boundary_and_paging(svc):
    big = ("\n\n".join(f"Абзац {i}. " + "кошки " * 80 for i in range(8))).encode()
    doc = (await svc.index_document(big, "big.txt", "p1", chunk_size=300, chunk_overlap=0))["document_id"]
    first = await svc.get_document_chunks("p1", doc, start=0, limit=2)
    assert [c[2] for c in first] == [0, 1] and all(c[1] == doc for c in first)
    assert [c[2] for c in await svc.get_document_chunks("p1", doc, start=2, limit=2)] == [2, 3]
    assert await svc.get_document_chunks("p2", doc) is None  # чужой проект
    assert await svc.get_document_chunks("p1", 999999) is None


async def test_get_document_chunks_puts_summaries_after_details(svc):
    text = ("\n\n".join(f"Раздел {i}. " + "космос " * 90 for i in range(8))).encode()
    doc = (
        await svc.index_document(
            text, "s.txt", "p1", chunking_strategy="hierarchical", chunk_size=400, chunk_overlap=0
        )
    )["document_id"]
    everything = await svc.get_document_chunks("p1", doc, start=0, limit=1000)
    indexes = [c[2] for c in everything]
    non_negative = [i for i in indexes if i >= 0]
    assert indexes[: len(non_negative)] == non_negative
    assert all(i < 0 for i in indexes[len(non_negative):])


async def test_delete_document_returns_storage_keys_and_clears_data(svc, pg_connection):
    res = await svc.index_document(CATS, "cats.txt", "p1", minio_object="o/c", minio_bucket="b")
    out = await svc.delete_document(res["document_id"])
    assert out == {"ok": True, "document_id": res["document_id"], "minio_object": "o/c", "minio_bucket": "b"}
    for table in ("project_rag_documents", "project_rag_vectors", "rag_graph_nodes"):
        assert await _count(pg_connection, table) == 0
    assert await svc.delete_document(res["document_id"]) == {"ok": False, "error": "not_found"}


async def test_delete_by_project_removes_only_that_project(svc, pg_connection):
    await svc.index_document(CATS, "cats.txt", "p1", minio_object="o/1", minio_bucket="b")
    await svc.index_document(DOGS, "dogs.txt", "p1", minio_object="o/2", minio_bucket="b")
    keep = await svc.index_document(TAXES, "taxes.txt", "p2")
    await svc.search("кошки", "p1", k=3, use_reranking=False)  # прогреть индексы
    out = await svc.delete_by_project("p1")
    assert out["ok"] and out["deleted_count"] == 2
    assert sorted(k["minio_object"] for k in out["minio_keys"]) == ["o/1", "o/2"]
    assert await svc.search("кошки", "p1", k=3, use_reranking=False) == []
    assert (await svc.search("налог", "p2", k=3, use_reranking=False))[0][2] == keep["document_id"]
    assert await _count(pg_connection, "project_rag_documents") == 1


async def test_delete_by_project_drops_its_bm25_store_entries(svc, pg_connection):
    await svc.index_document(CATS, "cats.txt", "p1")
    async with await pg_connection.acquire() as conn:
        from app.database import bm25_store

        bm25_store._table_ready = None
        await bm25_store.ensure_table(conn)
        await conn.execute(
            "INSERT INTO bm25_index_store (cache_key, fingerprint, payload) "
            "VALUES ('project:p1:8', 'f', 'x'), ('project:p2:8', 'f', 'x')"
        )
    await svc.delete_by_project("p1")
    async with await pg_connection.acquire() as conn:
        keys = [r["cache_key"] for r in await conn.fetch("SELECT cache_key FROM bm25_index_store")]
    assert keys == ["project:p2:8"]


async def test_delete_by_project_prefix_overlap_known_issue(svc, pg_connection):
    """Известная проблема: ключ BM25-хранилища ``project:<id>:<dim>`` чистится по
    LIKE-префиксу ``project:<id>:`` — идентификатор с ``_`` или ``%`` задевает
    чужие проекты (``p_1`` стирает и ``pX1``)."""
    await svc.index_document(CATS, "cats.txt", "p_1")
    async with await pg_connection.acquire() as conn:
        from app.database import bm25_store

        bm25_store._table_ready = None
        await bm25_store.ensure_table(conn)
        await conn.execute(
            "INSERT INTO bm25_index_store (cache_key, fingerprint, payload) "
            "VALUES ('project:p_1:8', 'f', 'x'), ('project:pX1:8', 'f', 'x')"
        )
    await svc.delete_by_project("p_1")
    async with await pg_connection.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM bm25_index_store") == 0


async def test_delete_empty_project(svc):
    out = await svc.delete_by_project("never-existed")
    assert out == {"ok": True, "project_id": "never-existed", "deleted_count": 0, "minio_keys": []}


async def test_reindex_all_projects_for_owner_only_touches_their_documents(svc, pg_connection):
    def big(word):
        return ("\n\n".join(f"Абзац {i}. " + word * 80 for i in range(8))).encode()

    mine = await svc.index_document(
        big("кошки "), "mine.txt", "p1", owner_user_id="alice", chunk_size=2000, chunk_overlap=0
    )
    other = await svc.index_document(
        big("собаки "), "other.txt", "p2", owner_user_id="bob", chunk_size=2000, chunk_overlap=0
    )

    async def vectors(doc_id):
        async with await pg_connection.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM project_rag_vectors WHERE document_id = $1", doc_id
            )

    before_mine, before_other = await vectors(mine["document_id"]), await vectors(other["document_id"])
    result = await svc.reindex_all_projects(owner_user_id="alice", chunk_size=300, chunk_overlap=0)
    assert await vectors(other["document_id"]) == before_other
    assert await vectors(mine["document_id"]) > before_mine
    assert result and result.get("errors", 0) == 0


async def test_concurrent_identical_uploads_leave_one_document(svc, pg_connection):
    results = await asyncio.gather(
        *(svc.index_document(CATS, f"cats-{i}.txt", "p1") for i in range(4)), return_exceptions=True
    )
    assert not [r for r in results if isinstance(r, Exception)], results
    assert await _count(pg_connection, "project_rag_documents") == 1
