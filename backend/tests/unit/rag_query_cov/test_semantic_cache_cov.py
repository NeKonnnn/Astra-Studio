"""rag_query/semantic_cache: ключ, TTL, вытеснение, сброс версии индекса."""

import pytest

from backend.rag_query import semantic_cache as sc

pytestmark = pytest.mark.unit

HITS = [("текст", 0.9, 1, 0)]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    sc._store.clear()
    monkeypatch.setattr(sc, "_index_version", 0)
    monkeypatch.setenv("RAG_SEMANTIC_CACHE", "1")
    yield
    sc._store.clear()


def key(**over):
    args = dict(path="/s", normalized_query="q", k=5, strategy=None, document_id=None,
                use_reranking=None, filters=None, project_id=None)
    args.update(over)
    return sc.make_cache_key(**args)


class TestEnabled:
    @pytest.mark.parametrize("raw,expected", [(None, False), ("", False), ("1", True), ("On", True), ("no", False)])
    def test_flag(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("RAG_SEMANTIC_CACHE", raising=False)
        else:
            monkeypatch.setenv("RAG_SEMANTIC_CACHE", raw)
        assert sc.semantic_cache_enabled() is expected


class TestTtl:
    @pytest.mark.parametrize("raw,expected", [("300", 300.0), ("10", 30.0), ("600.5", 600.5), ("abc", 300.0)])
    def test_ttl(self, monkeypatch, raw, expected):
        monkeypatch.setenv("RAG_SEMANTIC_CACHE_TTL", raw)
        assert sc._ttl_seconds() == expected

    def test_default_ttl(self, monkeypatch):
        monkeypatch.delenv("RAG_SEMANTIC_CACHE_TTL", raising=False)
        assert sc._ttl_seconds() == 300.0


class TestKey:
    def test_stable_and_hex(self):
        assert key() == key() and len(key()) == 64

    @pytest.mark.parametrize(
        "over",
        [
            {"path": "/other"}, {"normalized_query": "z"}, {"k": 6}, {"strategy": "mmr"},
            {"document_id": 3}, {"use_reranking": True}, {"filters": {"a": 1}},
            {"project_id": "p"}, {"rag_fix_typos": True}, {"rag_multi_query": True}, {"rag_hyde": True},
        ],
    )
    def test_each_param_changes_key(self, over):
        assert key(**over) != key()

    def test_none_filters_equal_empty(self):
        assert key(filters=None) == key(filters={})

    def test_version_bump_changes_key(self):
        before = key()
        sc.bump_rag_semantic_cache()
        assert key() != before


class TestGetSet:
    def test_roundtrip_returns_copy(self):
        sc.cache_set("k", HITS)
        got = sc.cache_get("k")
        assert got == HITS and got is not HITS
        got.append("мусор")
        assert sc.cache_get("k") == HITS

    def test_miss(self):
        assert sc.cache_get("нет") is None

    def test_disabled_neither_reads_nor_writes(self, monkeypatch):
        monkeypatch.setenv("RAG_SEMANTIC_CACHE", "0")
        sc.cache_set("k", HITS)
        assert sc._store == {}
        monkeypatch.setenv("RAG_SEMANTIC_CACHE", "1")
        sc.cache_set("k", HITS)
        monkeypatch.setenv("RAG_SEMANTIC_CACHE", "0")
        assert sc.cache_get("k") is None

    def test_expired_entry_is_dropped(self, monkeypatch):
        t = {"now": 1000.0}
        monkeypatch.setattr(sc.time, "monotonic", lambda: t["now"])
        sc.cache_set("k", HITS)
        t["now"] += 299
        assert sc.cache_get("k") == HITS
        t["now"] += 2
        assert sc.cache_get("k") is None
        assert "k" not in sc._store

    def test_bump_clears_store(self):
        sc.cache_set("k", HITS)
        sc.bump_rag_semantic_cache()
        assert sc.cache_get("k") is None and sc._index_version == 1

    def test_overflow_evicts_oldest_500(self):
        for i in range(2000):
            sc._store[f"old{i}"] = (1e18, HITS)
        sc.cache_set("new", HITS)
        assert len(sc._store) == 2001 - 500
        assert "old0" not in sc._store and "old499" not in sc._store
        assert "old500" in sc._store and "new" in sc._store
