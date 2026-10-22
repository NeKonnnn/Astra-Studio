"""embedding_schema / fts / connection / graph_repository: сбои и граничные ветки на фейках без БД."""

import logging
from types import SimpleNamespace

import asyncpg
import pytest

from app.core import config as app_config
from app.database import connection as connection_module
from app.database import embedding_schema as es
from app.database import fts
from app.database.connection import PostgreSQLConnection, get_postgres_connection
from app.database.graph_repository import GraphRepository, _jaccard
from tests.unit.database.fakes import FakeConn, FakeDb, FakePool, raising

pytestmark = pytest.mark.unit


def _no_settings(monkeypatch):
    monkeypatch.setattr(app_config, "get_settings", raising(RuntimeError("нет конфига")))


# ------------------------------------------------------------- embedding_schema


def test_index_method_falls_back_to_env_when_settings_unavailable(monkeypatch):
    _no_settings(monkeypatch)
    monkeypatch.delenv("RAG_VECTOR_INDEX_METHOD", raising=False)
    assert es.resolve_index_method() == es.INDEX_METHOD_HNSW
    monkeypatch.setenv("RAG_VECTOR_INDEX_METHOD", "IVF-Flat")
    assert es.resolve_index_method() == es.INDEX_METHOD_IVFFLAT


def test_index_params_without_settings_come_from_env_with_clamping(monkeypatch):
    _no_settings(monkeypatch)
    for name in (
        "RAG_VECTOR_IVFFLAT_LISTS",
        "RAG_VECTOR_IVFFLAT_PROBES",
        "RAG_VECTOR_HNSW_M",
        "RAG_VECTOR_HNSW_EF_CONSTRUCTION",
        "RAG_VECTOR_HNSW_EF_SEARCH",
    ):
        monkeypatch.delenv(name, raising=False)
    assert es._index_params() == {
        "ivfflat_lists": 100,
        "ivfflat_probes": 10,
        "hnsw_m": 16,
        "hnsw_ef_construction": 64,
        "hnsw_ef_search": 40,
    }
    monkeypatch.setenv("RAG_VECTOR_HNSW_M", "1")  # ниже допустимого минимума 2
    monkeypatch.setenv("RAG_VECTOR_IVFFLAT_LISTS", "250")
    params = es._index_params()
    assert params["hnsw_m"] == 2 and params["ivfflat_lists"] == 250


async def test_ann_session_settings_for_ivfflat_set_probes(monkeypatch):
    monkeypatch.setattr(es, "resolve_index_method", lambda raw=None: raw or es.INDEX_METHOD_IVFFLAT)
    monkeypatch.setattr(es, "_index_params", lambda: {"ivfflat_probes": 7, "hnsw_ef_search": 40})
    conn = FakeConn()
    await es.apply_ann_search_settings(conn)
    assert conn.sql_of("execute") == ["SET ivfflat.probes = 7"]


@pytest.mark.parametrize("method", [es.INDEX_METHOD_IVFFLAT, es.INDEX_METHOD_HNSW])
async def test_ann_session_settings_failure_is_swallowed(monkeypatch, method):
    """Старый pgvector не знает параметра: поиск продолжается без него, а не падает."""
    monkeypatch.setattr(es, "resolve_index_method", lambda raw=None: method)
    monkeypatch.setattr(es, "_index_params", lambda: {"ivfflat_probes": 7, "hnsw_ef_search": 33})
    conn = FakeConn(execute=raising(RuntimeError("unrecognized configuration parameter")))
    await es.apply_ann_search_settings(conn)
    assert len(conn.calls) == 1


async def test_create_index_without_known_dimension_reports_false():
    """Колонки нет в БД и размерность не передана — индекс создавать не из чего."""
    conn = FakeConn(fetchrow=None)
    assert await es.create_embedding_index(conn, "kb_vectors", 0) is False
    assert conn.sql_of("execute") == []


async def test_create_index_survives_failure_to_drop_other_method_index(monkeypatch):
    # без явного метода активен hnsw; явно переданный (для имени индекса) остаётся как есть
    monkeypatch.setattr(es, "resolve_index_method", lambda raw=None: raw or es.INDEX_METHOD_HNSW)

    def execute(sql, *args):
        if sql.lstrip().upper().startswith("DROP"):
            raise RuntimeError("index in use")
        return "OK"

    conn = FakeConn(execute=execute, fetchrow={"ft": "vector(4)"})
    assert await es.create_embedding_index(conn, "kb_vectors", 4) is True
    sqls = conn.sql_of("execute")
    assert sqls[0].startswith("DROP INDEX IF EXISTS") and "ivfflat" in sqls[0]
    assert "USING hnsw" in sqls[1]


async def test_overview_marks_table_with_broken_stats_as_unknown():
    conn = FakeConn(fetch=[{"name": "kb_vectors"}], fetchrow=raising(RuntimeError("нет доступа")))
    out = await es.vector_tables_overview(conn)
    assert out["kb_vectors"] == [{"table": "kb_vectors", "dim": None, "rows": -1}]
    assert set(out["ann"]) == {"method", "params"}


# -------------------------------------------------------------------------- fts


async def test_fts_columns_failure_is_logged_and_indexes_still_attempted(caplog):
    """Колонку не удалось создать (ошибка Postgres): предупреждение, остальное выполняется."""

    def execute(sql, *args):
        if "ADD COLUMN" in sql:
            raise asyncpg.exceptions.InsufficientPrivilegeError("must be owner")
        return "OK"

    conn = FakeConn(execute=execute)
    with caplog.at_level(logging.WARNING, logger=fts.logger.name):
        await fts.ensure_fts_columns(conn, "kb_vectors")
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("content_tsv_ru" in m and "kb_vectors" in m and "must be owner" in m for m in messages)
    assert any("content_tsv_simple" in m for m in messages)
    created = [s for s in conn.sql_of("execute") if s.startswith("CREATE INDEX")]
    assert len(created) == 2


async def test_fts_columns_non_postgres_error_propagates():
    conn = FakeConn(execute=raising(RuntimeError("связь потеряна")))
    with pytest.raises(RuntimeError, match="связь потеряна"):
        await fts.ensure_fts_columns(conn, "kb_vectors")


def test_extract_filenames_deduplicates_quoted_and_plain_mentions():
    assert fts.extract_filenames('саммари по "Report.pdf" и report.pdf, ещё data.xlsx') == [
        "Report.pdf",
        "data.xlsx",
    ]
    # повтор одного и того же имени в кавычках тоже схлопывается (без учёта регистра)
    assert fts.extract_filenames('«Отчёт.docx» и "отчёт.DOCX"') == ["Отчёт.docx"]


# ------------------------------------------------------------------- connection


def _patch_pool(monkeypatch, pool=None, error=None):
    seen = {}

    async def create_pool(**kwargs):
        seen.update(kwargs)
        if error is not None:
            raise error
        return pool

    monkeypatch.setattr(connection_module.asyncpg, "create_pool", create_pool)
    return seen


def _pg():
    return PostgreSQLConnection("h", 5433, "db", "u", "secret")


async def test_connect_creates_pool_and_checks_pgvector(monkeypatch):
    conn = FakeConn(fetchval=lambda sql, *a: "0.8.0" if "pg_extension" in sql else "17.2")
    pool = FakePool(conn)
    seen = _patch_pool(monkeypatch, pool)
    pg = _pg()
    assert await pg.connect(min_size=1, max_size=3) is True
    assert pg.pool is pool
    assert seen == {
        "host": "h", "port": 5433, "database": "db", "user": "u", "password": "secret",
        "min_size": 1, "max_size": 3,
    }
    assert "CREATE EXTENSION IF NOT EXISTS vector" in conn.sql_of("execute")


async def test_connect_failure_returns_false_and_logs(monkeypatch, caplog):
    _patch_pool(monkeypatch, error=OSError("connection refused"))
    pg = _pg()
    with caplog.at_level(logging.ERROR, logger=connection_module.logger.name):
        assert await pg.connect() is False
    assert pg.pool is None
    assert any("connection refused" in r.getMessage() for r in caplog.records)


async def test_failed_pgvector_setup_closes_pool(monkeypatch):
    """BUG-RAG-34: если пул создан, а ``CREATE EXTENSION vector`` упал, ``connect`` возвращает
    False, закрывает пул и обнуляет ``self.pool`` — следующий ``acquire`` переподключится."""
    conn = FakeConn(execute=raising(RuntimeError("extension vector is not available")))
    pool = FakePool(conn)
    _patch_pool(monkeypatch, pool)
    pg = _pg()
    assert await pg.connect() is False
    assert pg.pool is None
    assert pool.closed is True


async def test_disconnect_closes_pool_once_and_is_idempotent(monkeypatch):
    pool = FakePool(FakeConn())
    pg = _pg()
    pg.pool = pool
    await pg.disconnect()
    assert pool.closed is True and pg.pool is None
    await pg.disconnect()  # без пула — ничего не делает


async def test_acquire_connects_lazily_and_yields_connection(monkeypatch):
    conn = FakeConn(fetchval="x")
    _patch_pool(monkeypatch, FakePool(conn))
    pg = _pg()
    async with await pg.acquire() as got:
        assert got is conn
    assert pg.pool is not None


async def test_acquire_without_possible_pool_raises(monkeypatch):
    _patch_pool(monkeypatch, error=OSError("down"))
    with pytest.raises(RuntimeError, match="Пул PostgreSQL не создан"):
        await _pg().acquire()


async def test_context_manager_does_not_swallow_errors_from_the_body():
    pg = _pg()
    pg.pool = FakePool(FakeConn())
    with pytest.raises(KeyError):
        async with await pg.acquire():
            raise KeyError("x")


async def test_health_check_states():
    pg = _pg()
    assert await pg.health_check() is False  # пула нет
    pg.pool = FakePool(FakeConn())
    assert await pg.health_check() is True
    pg.pool = FakePool(FakeConn(), acquire_error=OSError("pool is closed"))
    assert await pg.health_check() is False
    pg.pool = FakePool(FakeConn(execute=raising(OSError("reset"))))
    assert await pg.health_check() is False


def test_get_postgres_connection_uses_settings(monkeypatch):
    cfg = SimpleNamespace(postgresql=SimpleNamespace(host="pg", port=6543, database="rag", user="svc", password="pw"))
    monkeypatch.setattr(connection_module, "get_settings", lambda: cfg)
    pg = get_postgres_connection()
    assert (pg.host, pg.port, pg.database, pg.user, pg.password) == ("pg", 6543, "rag", "svc", "pw")
    assert pg.pool is None


# ------------------------------------------------------------- graph_repository


def test_jaccard_of_two_empty_token_sets_is_zero():
    assert _jaccard([], []) == 0.0
    assert _jaccard(["a"], []) == 0.0
    assert _jaccard(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)


def _graph_conn(*, skip_chunk_inserts=()):
    """Соединение, выдающее id узлов по порядку вставки; для некоторых чанков вставка «не вернула строку»."""
    state = {"next": 0, "edges": []}

    def fetchrow(sql, store, doc, chunk_index, *rest):
        if chunk_index in skip_chunk_inserts:
            return None
        state["next"] += 1
        return {"id": state["next"]}

    def execute(sql, *args):
        if "INSERT INTO rag_graph_edges" in sql:
            state["edges"].append(args)  # (store, from, to, type, weight, meta)
        return "OK"

    return FakeConn(execute=execute, fetchrow=fetchrow), state


async def test_rebuild_skips_edges_for_chunk_whose_node_was_not_created():
    conn, state = _graph_conn(skip_chunk_inserts={1})
    chunks = [(0, "alpha beta"), (1, "alpha beta"), (2, "alpha beta")]
    await GraphRepository(FakeDb(conn)).rebuild_document_graph("kb", 7, chunks)
    adjacent = [e for e in state["edges"] if e[3] == "adjacent"]
    semantic = [e for e in state["edges"] if e[3] == "semantic"]
    assert adjacent == []  # обе пары соседей содержат чанк 1 без узла
    assert sorted((e[1], e[2]) for e in semantic) == [(1, 2), (2, 1)]  # только 0<->2 (узлы 1 и 2)


async def test_rebuild_caps_semantic_edges_at_8000_pairs():
    """300 одинаковых чанков дали бы ~8100 пар в окне 30; пар строится не больше 8000 (по 2 ребра)."""
    conn, state = _graph_conn()
    chunks = [(i, "alpha beta gamma") for i in range(300)]
    await GraphRepository(FakeDb(conn)).rebuild_document_graph("kb", 1, chunks)
    semantic = [e for e in state["edges"] if e[3] == "semantic"]
    assert len(semantic) == 2 * 8000
    assert len([e for e in state["edges"] if e[3] == "adjacent"]) == 2 * 299


async def test_rebuild_runs_delete_and_inserts_in_one_transaction():
    """BUG-RAG-06: удаление старого графа и вставка нового — в одной транзакции."""
    conn, state = _graph_conn()
    await GraphRepository(FakeDb(conn)).rebuild_document_graph("kb", 7, [(0, "a b"), (1, "a b")])
    assert conn.tx_log == ["begin", "commit"]
    kinds = [c[1].split()[0] for c in conn.calls if c[0] == "execute"]
    assert kinds[0] == "DELETE"


async def test_rebuild_failure_rolls_back_so_old_graph_survives():
    state = {"n": 0}

    def fetchrow(sql, *args):
        state["n"] += 1
        if state["n"] == 2:
            raise asyncpg.UniqueViolationError("duplicate chunk_index")
        return {"id": state["n"]}

    conn = FakeConn(fetchrow=fetchrow)
    with pytest.raises(asyncpg.UniqueViolationError):
        await GraphRepository(FakeDb(conn)).rebuild_document_graph("kb", 7, [(0, "a"), (0, "b")])
    assert conn.tx_log == ["begin", "rollback"]


async def test_rebuild_with_no_chunks_only_deletes_the_graph():
    conn = FakeConn()
    await GraphRepository(FakeDb(conn)).rebuild_document_graph("kb", 7, [])
    assert [c[1].split()[0] for c in conn.calls] == ["DELETE"]
    assert conn.tx_log == []


async def test_expand_neighbors_without_document_scope_returns_nothing():
    """BUG-RAG-06: без document_id и пар seed поиск по одному chunk_index смешивал бы документы."""
    conn = FakeConn()
    got = await GraphRepository(FakeDb(conn)).expand_neighbors("kb", None, [0])
    assert got == {} and conn.calls == []


def _expand_conn(edges_by_node, missing_nodes):
    def fetch(sql, *args):
        if "FROM rag_graph_edges" in sql:
            return edges_by_node.get(args[1], [])
        if "id = ANY($1::bigint[])" in sql:
            return [missing_nodes[n] for n in args[0] if n in missing_nodes]
        return [{"id": 1, "document_id": 1, "chunk_index": 0}]  # seed-узлы

    return FakeConn(fetch=fetch)


async def test_expand_neighbors_ignores_nodes_that_vanished_and_keeps_best_score_per_chunk():
    """Узел-сосед исчез между запросами (нет в таблице узлов) — пропускается; два узла одного
    чанка дают максимум, а не меньшую оценку поверх большей."""
    conn = _expand_conn(
        edges_by_node={1: [{"to_node_id": 2, "weight": 0.9}, {"to_node_id": 3, "weight": 0.9}]},
        missing_nodes={2: {"id": 2, "document_id": 1, "chunk_index": 0}},  # узел 3 не найден вовсе
    )
    got = await GraphRepository(FakeDb(conn)).expand_neighbors("kb", 1, [0])
    assert got == {(1, 0): 1.0}
