"""embedding_schema и fts на реальном PostgreSQL: таблицы по размерностям, ANN-индексы, миграция, tsquery."""

import pytest

from app.database import embedding_schema as es
from app.database import fts
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

pytestmark = pytest.mark.integration


@pytest.fixture
async def conn(pg_connection):
    async with await pg_connection.acquire() as c:
        await c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await c.execute("CREATE TABLE parent (id SERIAL PRIMARY KEY)")
        yield c


async def _indexes(conn, table):
    rows = await conn.fetch("SELECT indexname FROM pg_indexes WHERE tablename = $1", table)
    return {r["indexname"] for r in rows}


# ----------------------------------------------------------------- schema basics


async def test_ensure_vector_table_creates_table_column_and_index(conn):
    table = await es.ensure_vector_table(conn, "vecs", 8, "parent")
    assert table == "vecs"
    assert await es.table_exists(conn, "vecs")
    assert await es.get_column_vector_dim(conn, "vecs") == 8
    idx = await _indexes(conn, "vecs")
    assert "idx_vecs_embedding_hnsw" in idx and "idx_vecs_document_id" in idx


async def test_ensure_is_idempotent_and_keeps_rows(conn):
    await es.ensure_vector_table(conn, "vecs", 3, "parent")
    await conn.execute("INSERT INTO parent DEFAULT VALUES")
    await conn.execute(
        "INSERT INTO vecs (document_id, chunk_index, embedding, content) VALUES (1, 0, '[1,2,3]', 'x')"
    )
    assert await es.ensure_vector_table(conn, "vecs", 3, "parent") == "vecs"
    assert await conn.fetchval("SELECT COUNT(*) FROM vecs") == 1


async def test_other_dimension_goes_to_suffixed_table_and_legacy_is_untouched(conn):
    assert await es.ensure_vector_table(conn, "vecs", 3, "parent") == "vecs"
    assert await es.resolve_vector_table(conn, "vecs", 3) == "vecs"
    assert await es.resolve_vector_table(conn, "vecs", 5) == "vecs_5"
    assert await es.ensure_vector_table(conn, "vecs", 5, "parent") == "vecs_5"
    assert await es.get_column_vector_dim(conn, "vecs") == 3
    assert await es.get_column_vector_dim(conn, "vecs_5") == 5
    assert await es.list_vector_tables(conn, "vecs") == ["vecs", "vecs_5"]


async def test_first_table_defines_the_base_dimension(conn):
    assert await es.resolve_vector_table(conn, "vecs", 7) == "vecs"
    assert await es.ensure_vector_table(conn, "vecs", 7, "parent") == "vecs"
    assert await es.resolve_vector_table(conn, "vecs", 3) == "vecs_3"


async def test_list_vector_tables_ignores_similarly_named_tables(conn):
    await es.ensure_vector_table(conn, "vecs", 3, "parent")
    await conn.execute("CREATE TABLE vecs_backup (id INT)")
    await conn.execute("CREATE TABLE vecs_x5 (id INT)")
    await conn.execute("CREATE TABLE other_vecs_5 (id INT)")
    assert await es.list_vector_tables(conn, "vecs") == ["vecs"]
    assert await es.list_vector_tables(conn, "nothing") == []


async def test_missing_table_has_no_dimension(conn):
    assert await es.get_column_vector_dim(conn, "no_such") is None
    assert not await es.table_exists(conn, "no_such")


@pytest.mark.parametrize("dim", [0, -4])
async def test_invalid_dimension_rejected(conn, dim):
    with pytest.raises(ValueError):
        await es.ensure_vector_table(conn, "vecs", dim, "parent")


async def test_halfvec_for_dimensions_above_2000_with_index(conn):
    table = await es.ensure_vector_table(conn, "big", 2500, "parent")
    row = await conn.fetchval(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = $1::regclass AND attname = 'embedding'",
        table,
    )
    assert row == "halfvec(2500)"
    assert await es.get_column_vector_dim(conn, table) == 2500
    assert "idx_big_embedding_hnsw" in await _indexes(conn, table)


async def test_no_ann_index_above_halfvec_limit(conn):
    table = await es.ensure_vector_table(conn, "huge", 4100, "parent")
    assert await es.get_column_vector_dim(conn, table) == 4100
    assert not any("embedding" in i for i in await _indexes(conn, table))


async def test_ivfflat_swaps_out_hnsw(conn, monkeypatch):
    await es.ensure_vector_table(conn, "vecs", 3, "parent")
    assert "idx_vecs_embedding_hnsw" in await _indexes(conn, "vecs")
    original = es.resolve_index_method
    monkeypatch.setattr(es, "resolve_index_method", lambda raw=None: original(raw) if raw is not None else "ivfflat")
    assert await es.create_embedding_index(conn, "vecs", 3) is True
    idx = await _indexes(conn, "vecs")
    assert "idx_vecs_embedding_ivfflat" in idx and "idx_vecs_embedding_hnsw" not in idx


async def test_drop_embedding_index_removes_both_methods(conn):
    await es.ensure_vector_table(conn, "vecs", 3, "parent")
    await es.drop_embedding_index(conn, "vecs")
    assert not any("embedding" in i for i in await _indexes(conn, "vecs"))
    await es.drop_embedding_index(conn, "vecs")  # повтор безвреден


async def test_create_index_on_missing_table_reports_false(conn):
    assert await es.create_embedding_index(conn, "no_such", 3) is False


async def test_apply_ann_search_settings_sets_session_value(conn):
    await es.apply_ann_search_settings(conn)
    assert int(await conn.fetchval("SHOW hnsw.ef_search")) >= 1


# --------------------------------------------------------- overview / migration


async def _make_stores(pg_connection, dim):
    docs = {
        "kb": KbDocumentRepository(pg_connection),
        "memory": MemoryRagDocumentRepository(pg_connection),
        "project": ProjectRagDocumentRepository(pg_connection),
    }
    vecs = {
        "kb": KbVectorRepository(pg_connection, embedding_dim=dim),
        "memory": MemoryRagVectorRepository(pg_connection, embedding_dim=dim),
        "project": ProjectRagVectorRepository(pg_connection, embedding_dim=dim),
    }
    for r in docs.values():
        await r.create_tables()
    for r in vecs.values():
        await r.create_tables()
    return docs, vecs


async def _fill(docs, vecs, dim):
    emb = [1.0] + [0.0] * (dim - 1)
    kb = await docs["kb"].create_document(Document(filename="a", content="x"))
    mem = await docs["memory"].create_document(Document(filename="a", content="x"))
    prj = await docs["project"].create_document("p", Document(filename="a", content="x"))
    for key, doc_id in (("kb", kb), ("memory", mem), ("project", prj)):
        await vecs[key].create_vectors_batch(
            [DocumentVector(document_id=doc_id, chunk_index=0, embedding=emb, content="c")]
        )


async def test_overview_lists_tables_dims_and_rows(pg_connection):
    docs, vecs = await _make_stores(pg_connection, 4)
    await _fill(docs, vecs, 4)
    async with await pg_connection.acquire() as conn:
        out = await es.vector_tables_overview(conn)
    assert out["kb_vectors"] == [{"table": "kb_vectors", "dim": 4, "rows": 1}]
    assert out["memory_rag_vectors"][0]["rows"] == 1
    assert out["project_rag_vectors"][0]["dim"] == 4
    assert out["ann"]["method"] in ("hnsw", "ivfflat")


async def test_migrate_to_same_dimension_keeps_data(pg_connection):
    docs, vecs = await _make_stores(pg_connection, 4)
    await _fill(docs, vecs, 4)
    async with await pg_connection.acquire() as conn:
        result = await es.migrate_vector_tables(conn, 4)
        assert result["migrated"] is False and result["cleared_rows"] == 0
        assert sorted(result["unchanged_tables"]) == sorted(es.VECTOR_TABLES)
        assert await conn.fetchval("SELECT COUNT(*) FROM kb_vectors") == 1


async def test_migrate_to_other_dimension_truncates_everything(pg_connection):
    docs, vecs = await _make_stores(pg_connection, 4)
    await _fill(docs, vecs, 4)
    async with await pg_connection.acquire() as conn:
        result = await es.migrate_vector_tables(conn, 6)
        assert result["migrated"] is True
        assert sorted(result["changed_tables"]) == sorted(es.VECTOR_TABLES)
        assert result["cleared_rows"] == 3
        for table in es.VECTOR_TABLES:
            assert await es.get_column_vector_dim(conn, table) == 6
            assert await conn.fetchval(f"SELECT COUNT(*) FROM {table}") == 0
        assert result["ann_enabled"] is True and result["hnsw_max_dim"] == 2000


async def test_migrate_above_ann_limit_disables_ann(pg_connection):
    await _make_stores(pg_connection, 4)
    async with await pg_connection.acquire() as conn:
        result = await es.migrate_vector_tables(conn, 5000)
        assert result["ann_enabled"] is False and result["hnsw_enabled"] is False
        assert await es.get_column_vector_dim(conn, "kb_vectors") == 5000
        assert not any("embedding" in i for i in await _indexes(conn, "kb_vectors"))


async def test_migrate_skips_missing_tables_and_validates_dimension(pg_connection):
    async with await pg_connection.acquire() as conn:
        result = await es.migrate_vector_tables(conn, 8)
        assert result["changed_tables"] == [] and result["unchanged_tables"] == []
        with pytest.raises(ValueError):
            await es.migrate_vector_tables(conn, 0)


# -------------------------------------------------------------------------- fts


async def _fts_table(conn):
    await conn.execute(
        "CREATE TABLE chunks (id SERIAL PRIMARY KEY, content TEXT)"
    )
    await fts.ensure_fts_columns(conn, "chunks")


async def test_ensure_fts_columns_is_idempotent_and_generated(conn):
    await _fts_table(conn)
    await fts.ensure_fts_columns(conn, "chunks")
    await conn.execute("INSERT INTO chunks (content) VALUES ('Кошки сидят на окнах'), (NULL)")
    row = await conn.fetchrow("SELECT content_tsv_ru, content_tsv_simple FROM chunks WHERE id = 1")
    assert "кошк" in row["content_tsv_ru"] and "кошки" in row["content_tsv_simple"]
    assert await conn.fetchval("SELECT content_tsv_ru::text FROM chunks WHERE id = 2") == ""
    idx = await _indexes(conn, "chunks")
    assert {"idx_chunks_tsv_ru_gin", "idx_chunks_tsv_simple_gin"} <= idx


async def test_ensure_fts_columns_rejects_unsafe_table_name(conn):
    with pytest.raises(ValueError):
        await fts.ensure_fts_columns(conn, "chunks; DROP TABLE parent")


WEIRD_QUERIES = [
    "a&b | !c (d) e:* 'f' \\ g<->h",
    "ab&cd | !ef (gh) ij:* 'kl' \\ mn<->op",
    "44-ФЗ v3.2 СК0050629",
    "кошка'; DROP TABLE chunks; --",
    "🙂 эмодзи 日本語 テスト",
    "ёжик Ёлка",
    "x" * 3000,
    "\t\n  много   пробелов  ",
    "под_черкивание __dunder__ a_b",
    "100% скидка _wild_",
    "email@example.com http://x.y/z?q=1&b=2",
]


@pytest.mark.parametrize("text", WEIRD_QUERIES)
async def test_built_or_query_never_breaks_to_tsquery(conn, text):
    q = fts.build_fts_or_query(text)
    if q is None:
        return
    for cfg in ("russian", "simple"):
        await conn.fetchval(f"SELECT to_tsquery('{cfg}'::regconfig, $1)", q)


async def test_search_matches_russian_morphology_and_codes(conn):
    await _fts_table(conn)
    await conn.executemany(
        "INSERT INTO chunks (content) VALUES ($1)",
        [("Константину выдали документы",), ("Отчёт по СК0050629 готов",), ("Совсем другое",)],
    )
    where, rank, _ = fts.fts_where_and_rank(vectors_alias="v", first_placeholder_idx=1)

    async def find(text):
        q = fts.build_fts_or_query(text)
        rows = await conn.fetch(
            f"SELECT v.id, {rank} AS r FROM chunks v WHERE {where} ORDER BY r DESC, v.id", q, q
        )
        return [r["id"] for r in rows]

    assert await find("Константин") == [1]
    assert await find("документ") == [1]
    assert await find("ск0050629") == [2]
    assert await find("константин код") == [1]
    assert await find("отсутствует") == []


async def test_substring_fallback_finds_what_fts_misses(conn):
    await _fts_table(conn)
    await conn.execute("INSERT INTO chunks (content) VALUES ('Konstantin.nf@yandex.ru'), ('x')")
    where, rank, used, params = fts.substring_where_and_rank(
        vectors_alias="v", tokens=["nf@yandex"], first_placeholder_idx=1
    )
    rows = await conn.fetch(f"SELECT v.id, {rank} AS r FROM chunks v WHERE {where}", *params)
    assert [r["id"] for r in rows] == [1] and rows[0]["r"] == 1.0
