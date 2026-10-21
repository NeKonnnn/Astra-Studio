"""bm25_store на реальном PostgreSQL: таблица, отпечаток корпуса, запись, чтение, чистка."""

import asyncio

import pytest
from rank_bm25 import BM25Okapi

from app.database import bm25_store as store
from app.database.kb_repository import KbDocumentRepository, KbVectorRepository
from app.database.models import Document, DocumentVector

pytestmark = pytest.mark.integration

DIM = 4
CORPUS = [["кошка", "сидит", "окне"], ["собака", "бежит", "двору"], ["птица", "летит", "небу"]]
METAS = [{"document_id": 1, "chunk": 0}, {"document_id": 2, "chunk": 0}, {"document_id": 3, "chunk": 0}]


@pytest.fixture(autouse=True)
def _reset_table_state():
    store._table_ready = None
    yield
    store._table_ready = None


@pytest.fixture
async def repos(pg_connection):
    docs = KbDocumentRepository(pg_connection)
    vectors = KbVectorRepository(pg_connection, embedding_dim=DIM)
    await docs.create_tables()
    await vectors.create_tables()
    return docs, vectors


async def _add_doc(repos, name="a.txt", chunks=2):
    docs, vectors = repos
    doc_id = await docs.create_document(Document(filename=name, content="x"))
    await vectors.create_vectors_batch(
        [
            DocumentVector(
                document_id=doc_id,
                chunk_index=i,
                embedding=[1.0, 0.0, 0.0, float(i)],
                content=f"{name} chunk {i}",
            )
            for i in range(chunks)
        ]
    )
    return doc_id


def _blob():
    return store.encode(BM25Okapi(CORPUS), METAS)


async def test_ensure_table_is_idempotent(pg_connection):
    async with await pg_connection.acquire() as conn:
        assert await store.ensure_table(conn) is True
        assert await store.ensure_table(conn) is True
        assert await store.ensure_table_once(conn) is True
        assert store._table_ready is True


async def test_miss_returns_fingerprint_without_payload(pg_connection, repos):
    await _add_doc(repos)
    fp, payload = await repos[1].bm25_store_load("kb:x:4")
    assert fp is not None and payload is None


async def test_fingerprint_of_empty_table_is_stable(pg_connection, repos):
    fp, payload = await repos[1].bm25_store_load("kb:x:4")
    assert fp == "0:0" and payload is None


async def test_save_then_load_hits_and_decodes(pg_connection, repos):
    await _add_doc(repos)
    vectors = repos[1]
    fp, _ = await vectors.bm25_store_load("kb:all:4")
    await vectors.bm25_store_save("kb:all:4", fp, _blob(), 2)
    fp2, payload = await vectors.bm25_store_load("kb:all:4")
    assert fp2 == fp and payload is not None
    index, metas, count = store.decode(bytes(payload))
    assert metas == METAS and count == 3
    assert index.get_scores(["кошка"])[0] > index.get_scores(["кошка"])[1]


async def test_new_chunk_changes_fingerprint_and_invalidates(pg_connection, repos):
    await _add_doc(repos, "a.txt")
    vectors = repos[1]
    fp, _ = await vectors.bm25_store_load("k")
    await vectors.bm25_store_save("k", fp, _blob(), 2)
    await _add_doc(repos, "b.txt")
    fp2, payload = await vectors.bm25_store_load("k")
    assert fp2 != fp and payload is None


async def test_deleting_document_changes_fingerprint(pg_connection, repos):
    first = await _add_doc(repos, "a.txt")
    await _add_doc(repos, "b.txt")
    vectors = repos[1]
    fp, _ = await vectors.bm25_store_load("k")
    await vectors.bm25_store_save("k", fp, _blob(), 2)
    await vectors.delete_vectors_by_document(first)
    fp2, payload = await vectors.bm25_store_load("k")
    assert fp2 != fp and payload is None


async def test_scope_restricts_fingerprint_to_listed_documents(pg_connection, repos):
    a = await _add_doc(repos, "a.txt")
    b = await _add_doc(repos, "b.txt")
    vectors = repos[1]
    fp_a, _ = await vectors.bm25_store_load("ka", [a])
    await vectors.bm25_store_save("ka", fp_a, _blob(), 2)
    # чужой документ изменился — отпечаток белого списка прежний, блоб жив
    await vectors.delete_vectors_by_document(b)
    fp_a2, payload = await vectors.bm25_store_load("ka", [a])
    assert fp_a2 == fp_a and payload is not None


async def test_save_overwrites_same_key(pg_connection, repos):
    await _add_doc(repos)
    vectors = repos[1]
    fp, _ = await vectors.bm25_store_load("k")
    await vectors.bm25_store_save("k", fp, b"old", 1)
    await vectors.bm25_store_save("k", fp, b"new", 2)
    _, payload = await vectors.bm25_store_load("k")
    assert bytes(payload) == b"new"
    async with await pg_connection.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM bm25_index_store") == 1
        assert await conn.fetchval("SELECT chunk_count FROM bm25_index_store") == 2


async def test_concurrent_saves_keep_single_row(pg_connection, repos):
    await _add_doc(repos)
    vectors = repos[1]
    fp, _ = await vectors.bm25_store_load("k")
    await asyncio.gather(
        *(vectors.bm25_store_save("k", fp, f"p{i}".encode(), i) for i in range(10))
    )
    async with await pg_connection.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM bm25_index_store") == 1


async def test_delete_by_prefix(pg_connection, repos):
    await _add_doc(repos)
    vectors = repos[1]
    fp, _ = await vectors.bm25_store_load("kb:agent_5:384")
    for key in ("kb:agent_5:384", "kb:agent_5:768", "kb:agent_6:384"):
        await vectors.bm25_store_save(key, fp, b"x", 1)
    async with await pg_connection.acquire() as conn:
        assert await store.delete_by_prefix(conn, "kb:agent_5:") == 2
        assert await conn.fetchval("SELECT COUNT(*) FROM bm25_index_store") == 1
        assert await store.delete_by_prefix(conn, "kb:agent_5:") == 0


async def test_delete_by_prefix_underscore_wildcard_known_issue(pg_connection, repos):
    """``_`` в LIKE — любой символ: префикс ``kb:agent_5:`` задевает и ``kb:agentX5:``."""
    await _add_doc(repos)
    vectors = repos[1]
    fp, _ = await vectors.bm25_store_load("k")
    await vectors.bm25_store_save("kb:agentX5:384", fp, b"x", 1)
    async with await pg_connection.acquire() as conn:
        assert await store.delete_by_prefix(conn, "kb:agent_5:") == 1


async def test_stale_entries_dropped_on_first_use(pg_connection, repos):
    await _add_doc(repos)
    vectors = repos[1]
    async with await pg_connection.acquire() as conn:
        await store.ensure_table(conn)
        await conn.execute(
            "INSERT INTO bm25_index_store (cache_key, fingerprint, payload, updated_at) "
            "VALUES ('old', 'f', 'x', NOW() - INTERVAL '30 days'), "
            "('fresh', 'f', 'x', NOW() - INTERVAL '1 day')"
        )
    await vectors.bm25_store_load("anything")
    async with await pg_connection.acquire() as conn:
        keys = {r["cache_key"] for r in await conn.fetch("SELECT cache_key FROM bm25_index_store")}
    assert keys == {"fresh"}


async def test_load_with_missing_vectors_table_degrades_to_miss(pg_connection):
    async with await pg_connection.acquire() as conn:
        fp, payload = await store.load(
            conn, cache_key="k", vectors_table="no_such_table"
        )
    assert (fp, payload) == (None, None)


async def test_injection_in_table_name_rejected(pg_connection):
    async with await pg_connection.acquire() as conn:
        with pytest.raises(ValueError):
            await store.load(conn, cache_key="k", vectors_table="kb_vectors; DROP TABLE x")


async def test_corrupted_payload_in_db_is_ignored_by_decode(pg_connection, repos):
    await _add_doc(repos)
    vectors = repos[1]
    fp, _ = await vectors.bm25_store_load("k")
    await vectors.bm25_store_save("k", fp, b"B1garbage", 1)
    _, payload = await vectors.bm25_store_load("k")
    assert store.decode(bytes(payload)) is None
