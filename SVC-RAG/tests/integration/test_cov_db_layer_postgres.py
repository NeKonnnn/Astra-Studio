"""Слой БД svc-rag на реальном PostgreSQL (pgvector): фильтры, смена модели, атомарность, пул.

Дополняет test_kb_repository_postgres / test_project_memory_rag_postgres: здесь то, чего там нет —
фильтры по датам и имени файла для всех трёх сторов, поиск чанков, проиндексированных прежней
моделью, пробелы замечаний обзора RAG-04 / RAG-11 / RAG-17 и жизненный цикл пула соединений.
"""

import os
from datetime import datetime
from urllib.parse import urlparse

import asyncpg
import pytest

from app.database import bm25_store
from app.database.connection import PostgreSQLConnection
from app.database.graph_repository import GraphRepository
from app.database.kb_repository import KbDocumentRepository, KbVectorRepository
from app.database.memory_rag_repository import (
    MemoryRagDocumentRepository,
    MemoryRagVectorRepository,
)
from app.database.models import Document, DocumentVector
from app.database.project_rag_repository import (
    ProjectRagDocumentRepository,
    ProjectRagVectorRepository,
)
from app.database.search_filters import DocumentVectorSearchFilters

pytestmark = pytest.mark.integration

DIM = 4
OLD = datetime(2020, 1, 1, 12, 0, 0)
NEW = datetime(2025, 1, 1, 12, 0, 0)
Q = [1, 0, 0, 0]


@pytest.fixture(autouse=True)
def _reset_bm25_state():
    bm25_store._table_ready = None
    yield
    bm25_store._table_ready = None


def _vec(doc_id, idx, emb, content, meta=None):
    return DocumentVector(
        document_id=doc_id, chunk_index=idx, embedding=emb, content=content, metadata=meta or {}
    )


class Stack:
    """Единый вид на три стора: документы + векторы одной размерности."""

    def __init__(self, kind, docs, vecs):
        self.kind = kind
        self.docs = docs
        self.vecs = vecs

    async def add_doc(self, filename, created_at=None, metadata=None):
        extra = {"created_at": created_at, "updated_at": created_at} if created_at else {}
        doc = Document(filename=filename, content="тело", metadata=metadata or {}, **extra)
        if self.kind == "project":
            return await self.docs.create_document("p1", doc)
        return await self.docs.create_document(doc)

    async def add_chunk(self, doc_id, text, idx=0, emb=None):
        await self.vecs.create_vectors_batch([_vec(doc_id, idx, emb or Q, text)])


async def make_stack(kind, conn, dim=DIM):
    docs_cls, vecs_cls = {
        "kb": (KbDocumentRepository, KbVectorRepository),
        "project": (ProjectRagDocumentRepository, ProjectRagVectorRepository),
        "memory": (MemoryRagDocumentRepository, MemoryRagVectorRepository),
    }[kind]
    docs = docs_cls(conn)
    await docs.create_tables()
    vecs = vecs_cls(conn, embedding_dim=dim)
    await vecs.create_tables()
    return Stack(kind, docs, vecs)


@pytest.fixture(params=["kb", "project", "memory"])
async def stack(request, pg_connection):
    return await make_stack(request.param, pg_connection)


def _doc_ids(results):
    return sorted(r[0].document_id for r in results)


# --------------------------------------------------- фильтры по дате и имени файла


@pytest.fixture
async def old_and_new(stack):
    old = await stack.add_doc("old_report.txt", OLD)
    new = await stack.add_doc("new.txt", NEW)
    for d in (old, new):
        await stack.add_chunk(d, "налог договор")
    return stack, old, new


FILTER_CASES = [
    ("from", DocumentVectorSearchFilters(date_from=datetime(2024, 1, 1)), "new"),
    ("to", DocumentVectorSearchFilters(date_to=datetime(2021, 1, 1)), "old"),
    ("range", DocumentVectorSearchFilters(date_from=datetime(2019, 1, 1), date_to=datetime(2021, 1, 1)), "old"),
    ("future", DocumentVectorSearchFilters(date_from=datetime(2030, 1, 1)), None),
    ("filename-ci", DocumentVectorSearchFilters(filename_contains="OLD_rep"), "old"),
    ("filename-trimmed", DocumentVectorSearchFilters(filename_contains="  new  "), "new"),
    ("inactive", DocumentVectorSearchFilters(), "both"),
]


async def test_search_filters_by_document_date_and_filename(old_and_new):
    """Фильтры метаданных документа действуют и на векторный, и на полнотекстовый поиск."""
    stack, old, new = old_and_new
    expected_by_key = {"old": [old], "new": [new], "both": sorted([old, new]), None: []}
    for name, filters, expect in FILTER_CASES:
        for search in ("similarity", "keyword"):
            if search == "similarity":
                res = await stack.vecs.similarity_search(Q, filters=filters)
            else:
                res = await stack.vecs.keyword_search("договор", filters=filters)
            assert _doc_ids(res) == expected_by_key[expect], (stack.kind, name, search)


async def test_substring_search_is_scoped_by_document(stack):
    a = await stack.add_doc("a.txt")
    b = await stack.add_doc("b.txt")
    await stack.add_chunk(a, "артикул ABC-1")
    await stack.add_chunk(b, "артикул ABC-1")
    assert _doc_ids(await stack.vecs.substring_search(["abc-1"])) == sorted([a, b])
    assert _doc_ids(await stack.vecs.substring_search(["abc-1"], document_id=b)) == [b]
    assert await stack.vecs.substring_search(["abc-1"], document_id=999999) == []


async def test_kb_substring_search_is_scoped_by_document_list(pg_connection):
    stack = await make_stack("kb", pg_connection)
    a = await stack.add_doc("a.txt")
    b = await stack.add_doc("b.txt")
    c = await stack.add_doc("c.txt")
    for d in (a, b, c):
        await stack.add_chunk(d, "артикул ABC-1")
    assert _doc_ids(await stack.vecs.substring_search(["abc-1"], document_ids=[a, c, None])) == sorted([a, c])
    # список документов приоритетнее одиночного document_id
    assert _doc_ids(await stack.vecs.substring_search(["abc-1"], document_id=b, document_ids=[a])) == [a]


# -------------------------------------------- чанки, проиндексированные прежней моделью


@pytest.mark.parametrize("kind", ["kb", "project"])
async def test_lookups_find_chunks_indexed_by_another_model(kind, pg_connection):
    """После смены модели чанки документа лежат в таблице другой размерности: точечные
    запросы обходят все таблицы стора (в отличие от memory — см. known_issue в соседнем файле)."""
    stack = await make_stack(kind, pg_connection)
    other = type(stack.vecs)(pg_connection, embedding_dim=6)
    await other.create_tables()
    doc = await stack.add_doc("a.txt")
    await other.create_vectors_batch([_vec(doc, 0, [1, 0, 0, 0, 0, 0], "шесть", {"m": 1})])
    await other.create_vectors_batch([_vec(doc, 1, [0, 1, 0, 0, 0, 0], "второй")])
    assert await stack.vecs.get_chunk_contents_by_indices(doc, [0, 1]) == {0: "шесть", 1: "второй"}
    assert [v.content for v in await stack.vecs.get_vectors_by_document(doc)] == ["шесть", "второй"]
    one = await stack.vecs.get_vector_by_document_and_chunk(doc, 0)
    if isinstance(one, tuple):
        one = one[0]
    assert (one.content, one.metadata) == ("шесть", {"m": 1})
    assert await stack.vecs.get_vector_by_document_and_chunk(doc, 5) is None
    # перечисление документов охватывает таблицы всех размерностей
    assert await stack.vecs.get_all_document_ids() == [doc]


async def test_chunk_lookup_prefers_first_table_that_has_the_document(pg_connection):
    stack = await make_stack("kb", pg_connection)
    other = KbVectorRepository(pg_connection, embedding_dim=6)
    await other.create_tables()
    doc = await stack.add_doc("a.txt")
    await stack.add_chunk(doc, "своя")
    await other.create_vectors_batch([_vec(doc, 0, [1, 0, 0, 0, 0, 0], "чужая")])
    assert await stack.vecs.get_chunk_contents_by_indices(doc, [0]) == {0: "своя"}
    assert [v.content for v in await stack.vecs.get_vectors_by_document(doc)] == ["своя"]
    assert (await stack.vecs.get_vector_by_document_and_chunk(doc, 0)).content == "своя"


# ----------------------------------------------------------------- BM25-хранилище


async def test_project_bm25_store_without_project_scope_sees_whole_table(pg_connection):
    stack = await make_stack("project", pg_connection)
    a = await stack.add_doc("a.txt")
    await stack.add_chunk(a, "x")
    fp, blob = await stack.vecs.bm25_store_load("project:all:4")
    assert blob is None
    await stack.vecs.bm25_store_save("project:all:4", fp, b"blob", 1)
    assert (await stack.vecs.bm25_store_load("project:all:4"))[1] is not None
    await stack.add_chunk(a, "y", idx=1)
    assert (await stack.vecs.bm25_store_load("project:all:4"))[1] is None


async def test_kb_bm25_store_scope_by_documents(pg_connection):
    stack = await make_stack("kb", pg_connection)
    a = await stack.add_doc("a.txt")
    b = await stack.add_doc("b.txt")
    await stack.add_chunk(a, "x")
    await stack.add_chunk(b, "y")
    fp, _ = await stack.vecs.bm25_store_load("kb:a", [a])
    await stack.vecs.bm25_store_save("kb:a", fp, b"blob", 1)
    assert (await stack.vecs.bm25_store_load("kb:a", [a]))[1] is not None
    await stack.vecs.delete_vectors_by_document(b)  # чужой документ не трогает отпечаток
    assert (await stack.vecs.bm25_store_load("kb:a", [a]))[1] is not None
    # без белого списка (None / только None) область не сужается до пустой
    fp_all, _ = await stack.vecs.bm25_store_load("kb:all")
    assert fp_all == "1:1"
    # список из одних None не превращается в «весь корпус»
    assert await stack.vecs.get_all_contents_for_bm25([None]) == []


# ------------------------------------------------------------- RAG-04: ILIKE без экранирования


@pytest.fixture
async def wildcard_docs(stack):
    plain = await stack.add_doc("abc.txt")
    underscore = await stack.add_doc("a_c.txt")
    percent = await stack.add_doc("100%.txt")
    for d in (plain, underscore, percent):
        await stack.add_chunk(d, "договор поставки")
    return stack, plain, underscore, percent


@pytest.mark.parametrize("search", ["similarity", "keyword"])
async def test_filename_filter_treats_underscore_literally(wildcard_docs, search):
    stack, plain, underscore, percent = wildcard_docs
    f = DocumentVectorSearchFilters(filename_contains="a_c")
    if search == "similarity":
        res = await stack.vecs.similarity_search(Q, filters=f)
    else:
        res = await stack.vecs.keyword_search("договор", filters=f)
    assert _doc_ids(res) == [underscore]  # сейчас найдётся и abc.txt


async def test_filename_filter_treats_percent_literally(wildcard_docs):
    stack, plain, underscore, percent = wildcard_docs
    res = await stack.vecs.similarity_search(Q, filters=DocumentVectorSearchFilters(filename_contains="%"))
    assert _doc_ids(res) == [percent]


async def test_find_document_ids_by_filename_treats_underscore_literally(wildcard_docs):
    stack, plain, underscore, percent = wildcard_docs
    assert await stack.docs.find_document_ids_by_filename("a_c") == [underscore]


# --------------------------------------- RAG-17: поиск имени по всей KB с LIMIT 10


async def test_kb_filename_lookup_can_be_scoped_to_agent_documents(pg_connection):
    docs = KbDocumentRepository(pg_connection)
    await docs.create_tables()
    own = await docs.create_document(Document(filename="report_own.pdf", content="x", created_at=OLD, updated_at=OLD))
    for i in range(11):
        await docs.create_document(Document(filename=f"report_{i}.pdf", content="x"))
    assert await docs.find_document_ids_by_filename("report", document_ids=[own]) == [own]


# ---------------------------------------- RAG-11: удаление без общей транзакции


async def _block_deletes(db, table):
    async with await db.acquire() as conn:
        await conn.execute(
            "CREATE OR REPLACE FUNCTION itest_block_delete() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'delete blocked'; END; $$ LANGUAGE plpgsql"
        )
        await conn.execute(
            f"CREATE TRIGGER itest_block_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION itest_block_delete()"
        )


async def _count(db, table, doc_id):
    async with await db.acquire() as conn:
        return await conn.fetchval(f"SELECT COUNT(*) FROM {table} WHERE document_id = $1", doc_id)


_TABLES = {"kb": "kb_vectors", "project": "project_rag_vectors", "memory": "memory_rag_vectors"}


@pytest.mark.parametrize("kind", ["kb", "project", "memory"])
async def test_delete_vectors_is_all_or_nothing_across_dimension_tables(kind, pg_connection):
    stack = await make_stack(kind, pg_connection)
    other = type(stack.vecs)(pg_connection, embedding_dim=6)
    await other.create_tables()
    doc = await stack.add_doc("a.txt")
    base = _TABLES[kind]
    await stack.add_chunk(doc, "четыре")
    await other.create_vectors_batch([_vec(doc, 0, [1, 0, 0, 0, 0, 0], "шесть")])
    await _block_deletes(pg_connection, f"{base}_6")
    with pytest.raises(asyncpg.PostgresError, match="delete blocked"):
        await stack.vecs.delete_vectors_by_document(doc)
    # откат: вектора обеих размерностей на месте
    assert await _count(pg_connection, base, doc) == 1
    assert await _count(pg_connection, f"{base}_6", doc) == 1


async def test_kb_vectors_then_document_delete_leaves_document_without_vectors_known_issue(pg_connection):
    """Известная проблема (RAG-11): kb_service.delete_document вызывает удаление векторов и
    удаление строки документа двумя независимыми операциями. Если вторая падает, документ
    остаётся в базе без единого вектора — в списке есть, в поиске не находится."""
    stack = await make_stack("kb", pg_connection)
    doc = await stack.add_doc("a.txt")
    await stack.add_chunk(doc, "текст")
    await _block_deletes(pg_connection, "kb_documents")
    assert await stack.vecs.delete_vectors_by_document(doc) is True
    with pytest.raises(asyncpg.PostgresError, match="delete blocked"):
        await stack.docs.delete_document(doc)
    assert (await stack.docs.get_document(doc)) is not None
    assert await stack.vecs.get_vectors_by_document(doc) == []


# ------------------------------------------------------------------------ пул


def _dsn_parts():
    dsn = os.environ.get("ASTRA_TEST_PG_DSN", "").strip()
    if not dsn:
        pytest.skip("ASTRA_TEST_PG_DSN не задан — интеграционный стенд не поднят")
    p = urlparse(dsn)
    return p.hostname or "127.0.0.1", p.port or 5432, (p.path or "/postgres").lstrip("/"), p.username or "postgres", p.password or ""


async def test_health_check_follows_pool_lifecycle(pg_connection):
    assert await pg_connection.health_check() is True
    pool = pg_connection.pool
    await pg_connection.disconnect()
    assert pg_connection.pool is None
    assert await pg_connection.health_check() is False
    await pg_connection.disconnect()  # повтор безвреден
    assert pool.is_closing()


async def test_health_check_is_false_when_pool_was_closed_behind_our_back(pg_connection):
    await pg_connection.pool.close()
    assert await pg_connection.health_check() is False


async def test_acquire_connects_lazily_with_real_database():
    host, port, db, user, password = _dsn_parts()
    pg = PostgreSQLConnection(host, port, db, user, password)
    assert pg.pool is None
    try:
        async with await pg.acquire() as conn:
            assert await conn.fetchval("SELECT 1") == 1
            assert await conn.fetchval("SELECT extname FROM pg_extension WHERE extname = 'vector'") == "vector"
        assert await pg.health_check() is True
    finally:
        await pg.disconnect()


async def test_connect_with_wrong_password_reports_false():
    host, port, db, user, _ = _dsn_parts()
    pg = PostgreSQLConnection(host, port, db, user, "definitely-wrong-password")
    assert await pg.connect(min_size=1, max_size=1) is False
    assert pg.pool is None
    with pytest.raises(RuntimeError, match="Пул PostgreSQL не создан"):
        await pg.acquire()


# ------------------------------------------------------------------------ граф


async def test_expand_neighbors_stops_where_propagated_score_falls_below_threshold(pg_connection):
    """Вес по цепочке затухает как 0.82^(hop+1) на каждом шаге; ниже 0.03 соседи отсекаются,
    сколько бы прыжков ни разрешили."""
    graph = GraphRepository(pg_connection)
    await graph.create_tables()
    await graph.rebuild_document_graph("kb", 1, [(i, f"w{i}a w{i}b w{i}c") for i in range(9)])
    got = await graph.expand_neighbors("kb", 1, [0], max_hops=10, max_nodes=100)
    assert set(got) == {(1, i) for i in range(6)}  # чанк 6 получил бы ~0.015 < 0.03
    assert got[(1, 0)] == 1.0
    assert got[(1, 5)] == pytest.approx(0.82 * 0.82**2 * 0.82**3 * 0.82**4 * 0.82**5, rel=1e-6)
