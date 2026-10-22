"""database/bm25_store: упаковка индекса без pickle, защита имён таблиц, ключи, отпечаток корпуса и ошибки хранилища."""

import json
import zlib

import pytest
from rank_bm25 import BM25Okapi

from app.database import bm25_store as store

pytestmark = pytest.mark.unit

CORPUS = [["кошка", "сидит", "окне"], ["собака", "бежит", "двору"], ["кошка", "собака", "дружат"]]
METAS = [{"document_id": 1, "chunk": 0}, {"document_id": 1, "chunk": 1}, {"document_id": 2, "chunk": 0}]


@pytest.fixture(autouse=True)
def _reset_table_state():
    store._table_ready = None
    yield
    store._table_ready = None


def pack(corpus=CORPUS, metas=METAS):
    return store.encode(BM25Okapi(corpus), metas)


class FakeConn:
    def __init__(self, row=None, fetch_error=None, execute_error=None, execute_result="DELETE 3"):
        self.row, self.fetch_error, self.execute_error, self.execute_result = row, fetch_error, execute_error, execute_result
        self.executed = []
        self.fetched = []

    async def execute(self, sql, *args, timeout=None):
        self.executed.append((" ".join(sql.split()), args, timeout))
        if self.execute_error:
            raise self.execute_error
        return self.execute_result

    async def fetchrow(self, sql, *args, timeout=None):
        self.fetched.append((" ".join(sql.split()), args, timeout))
        if self.fetch_error:
            raise self.fetch_error
        return self.row


class TestPacking:
    def test_roundtrip_gives_the_same_scores(self):
        original = BM25Okapi(CORPUS)

        restored, metas, count = store.decode(store.encode(original, METAS))

        assert metas == METAS and count == 3
        query = ["кошка", "собака"]
        assert list(restored.get_scores(query)) == pytest.approx(list(original.get_scores(query)))

    def test_blob_is_json_plus_zlib_and_never_a_pickle(self):
        blob = pack()

        assert blob.startswith(b"B1")
        raw = zlib.decompress(blob[6:])
        assert json.loads(raw)["v"] == store.FORMAT_VERSION and int.from_bytes(blob[2:6], "big") == len(raw)
        assert b"\x80\x04" not in raw[:4]  # pickle protocol header

    def test_a_pickle_in_the_database_is_not_executed(self):
        import pickle

        class Evil:
            def __reduce__(self):
                return (exit, (1,))

        assert store.decode(pickle.dumps(Evil())) is None
        assert store.decode(b"B1" + (10).to_bytes(4, "big") + zlib.compress(pickle.dumps(Evil()))) is None

    @pytest.mark.parametrize(
        "blob",
        [b"", b"B", b"B1", b"xx" + b"\x00" * 20, b"B1" + b"\x00\x00\x00\x05" + b"not zlib", zlib.compress(b"{}"), b"\x78\x9c old format"],
    )
    def test_garbage_blobs_are_rejected(self, blob):
        assert store.decode(blob) is None

    def test_wrong_format_version(self):
        raw = json.dumps({"v": 99}).encode()

        assert store.decode(b"B1" + len(raw).to_bytes(4, "big") + zlib.compress(raw)) is None

    @pytest.mark.parametrize("payload", [[1, 2], "text", None, 5])
    def test_non_object_payloads(self, payload):
        raw = json.dumps(payload).encode()

        assert store.decode(b"B1" + len(raw).to_bytes(4, "big") + zlib.compress(raw)) is None

    def test_missing_fields(self):
        raw = json.dumps({"v": store.FORMAT_VERSION, "idf": {}}).encode()

        assert store.decode(b"B1" + len(raw).to_bytes(4, "big") + zlib.compress(raw)) is None

    def test_metadata_count_must_match_the_corpus_size(self):
        # Выдача строится по номеру чанка: расхождение хуже медленной сборки.
        assert store.decode(store.encode(BM25Okapi(CORPUS), METAS[:2])) is None


class TestTableNames:
    @pytest.mark.parametrize("name", ["document_vectors", "v", "Table_1", "a" * 60])
    def test_valid(self, name):
        assert store._safe_table(name) == name

    @pytest.mark.parametrize(
        "name",
        ["", "1table", "_t", "t; DROP TABLE users", "t--", "a b", "t.x", 'a"b', "t'", "tаble", "t​", "t\n", "t(1)"],
    )
    def test_dangerous_or_invalid(self, name):
        with pytest.raises(ValueError):
            store._safe_table(name)

    def test_homoglyphs_are_reported_with_codes(self):
        with pytest.raises(ValueError, match="U\\+0430"):
            store._safe_table("tаble")  # кириллическая «а»

    def test_fingerprint_expression_validates_the_alias(self):
        assert store.fingerprint_expr("v") == "(count(*)::text || ':' || coalesce(max(v.id), 0)::text)"
        with pytest.raises(ValueError):
            store.fingerprint_expr("v); DROP TABLE x; --")


class TestKeys:
    def test_cache_key(self):
        assert store.cache_key("kb", "agent_5", 1024) == "kb:agent_5:1024" and store.cache_key("kb", "all", "768") == "kb:all:768"

    def test_entity_of_documents_is_order_insensitive(self):
        assert store.documents_entity([3, 1, 2]) == store.documents_entity([2, 3, 1, 1])
        assert store.documents_entity([1]) != store.documents_entity([2])

    def test_entity_shape(self):
        assert store.documents_entity(None) == "all" and store.documents_entity([]) == "all"
        entity = store.documents_entity([5, 7])
        assert entity.startswith("d") and len(entity) == 17 and entity[1:].isalnum()

    def test_entity_rejects_garbage_ids(self):
        with pytest.raises(ValueError):
            store.documents_entity(["x"])

    def test_string_ids_are_sorted_before_conversion_known_issue(self):
        # sorted() идёт ДО int(): смесь str/int падает TypeError, а строковые id сортируются лексикографически —
        # поэтому один и тот же набор документов даёт разные ключи кэша ("10,9" против "9,10").
        with pytest.raises(TypeError):
            store.documents_entity(["2", 1])
        assert store.documents_entity(["10", "9"]) != store.documents_entity([10, 9])


class TestSchema:
    async def test_table_is_created_once_and_stale_rows_dropped(self):
        conn = FakeConn()

        assert await store.ensure_table_once(conn) is True and await store.ensure_table_once(conn) is True

        statements = [sql for sql, _, _ in conn.executed]
        assert sum("CREATE TABLE IF NOT EXISTS" in s for s in statements) == 1 and sum("DELETE FROM" in s for s in statements) == 1
        assert all(timeout == store.QUERY_TIMEOUT for _, _, timeout in conn.executed)

    async def test_failure_is_remembered_and_not_retried(self):
        conn = FakeConn(execute_error=RuntimeError("no rights"))

        assert await store.ensure_table_once(conn) is False
        conn.execute_error = None
        assert await store.ensure_table_once(conn) is False
        assert len(conn.executed) == 1

    async def test_stale_cleanup_failure_is_harmless(self):
        conn = FakeConn()
        calls = []

        async def execute(sql, *args, timeout=None):
            calls.append(sql)
            if "DELETE" in sql:
                raise RuntimeError("timeout")

        conn.execute = execute

        assert await store.ensure_table_once(conn) is True


class TestLoad:
    async def test_hit(self):
        conn = FakeConn(row={"fingerprint": "6:42", "payload": b"blob"})

        result = await store.load(conn, cache_key="kb:all:1024", vectors_table="document_vectors")

        assert result == ("6:42", b"blob")
        sql, args, timeout = conn.fetched[0]
        assert args == ("kb:all:1024",) and timeout == store.QUERY_TIMEOUT and "s.fingerprint = fp.value" in sql and "FROM document_vectors v" in sql

    async def test_miss_still_returns_the_fingerprint(self):
        conn = FakeConn(row={"fingerprint": "6:42", "payload": None})

        assert await store.load(conn, cache_key="k", vectors_table="document_vectors") == ("6:42", None)

    async def test_scope_parameters_come_before_the_key(self):
        conn = FakeConn(row={"fingerprint": "f", "payload": None})

        await store.load(conn, cache_key="k", vectors_table="document_vectors", scope_where="WHERE v.document_id = ANY($1)", scope_params=[[1, 2]])

        sql, args, _ = conn.fetched[0]
        assert args == ([1, 2], "k") and "s.cache_key = $2" in sql and "WHERE v.document_id = ANY($1)" in sql

    async def test_invalid_table_name_never_reaches_the_database(self):
        conn = FakeConn()

        with pytest.raises(ValueError):
            await store.load(conn, cache_key="k", vectors_table="vectors; DROP TABLE x")

        assert conn.fetched == []

    async def test_errors_and_missing_rows_mean_no_store(self):
        assert await store.load(FakeConn(fetch_error=RuntimeError("down")), cache_key="k", vectors_table="t") == (None, None)
        assert await store.load(FakeConn(row=None), cache_key="k", vectors_table="t") == (None, None)

    async def test_unavailable_store(self):
        conn = FakeConn(execute_error=RuntimeError("no rights"))

        assert await store.load(conn, cache_key="k", vectors_table="t") == (None, None) and conn.fetched == []


class TestSaveAndDelete:
    async def test_upsert(self):
        conn = FakeConn()

        await store.save(conn, cache_key="kb:all:1024", fingerprint="6:42", payload=b"blob", chunk_count=6.0)

        sql, args, timeout = conn.executed[0]
        assert "ON CONFLICT (cache_key) DO UPDATE" in sql and args == ("kb:all:1024", "6:42", b"blob", 6) and timeout == store.QUERY_TIMEOUT

    async def test_save_errors_are_swallowed(self):
        await store.save(FakeConn(execute_error=RuntimeError("readonly")), cache_key="k", fingerprint="f", payload=b"x", chunk_count=1)

    async def test_delete_by_prefix(self):
        conn = FakeConn(execute_result="DELETE 3")

        assert await store.delete_by_prefix(conn, "kb:agent_5:") == 3
        assert conn.executed[0][1] == ("kb:agent\\_5:%",)

    @pytest.mark.parametrize(("result", "expected"), [("DELETE 0", 0), ("DELETE", 0), ("", 0)])
    async def test_delete_counts(self, result, expected):
        assert await store.delete_by_prefix(FakeConn(execute_result=result), "p") == expected

    async def test_delete_errors_give_zero(self):
        assert await store.delete_by_prefix(FakeConn(execute_error=RuntimeError("x")), "p") == 0

    async def test_like_wildcards_in_the_prefix_are_escaped(self):
        # BUG-RAG-08: «_» и «%» в имени сущности — литералы, «kb:agent_5:» не заденет «kb:agentX5:».
        conn = FakeConn()

        await store.delete_by_prefix(conn, "kb:agent_5:%\\")

        assert conn.executed[0][1] == ("kb:agent\\_5:\\%\\\\%",)
