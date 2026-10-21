"""services/bm25_index: токенизация, RRF, жизненный цикл индекса (хранилище/сборка/освобождение), кэш и гибридное объединение."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.services import bm25_index as bm

pytestmark = pytest.mark.unit

ROWS = [
    (1, 0, "кошка сидит на окне"),
    (1, 1, "собака бежит по двору"),
    (2, 0, "кошка и собака дружат"),
    (2, 1, "налоговая отчётность за квартал"),
    (3, 0, "договор поставки оборудования"),
    (3, 1, "приложение к договору поставки"),
]


def make_index(rows=ROWS, **kw):
    calls = {"fetch": 0}

    async def fetch():
        calls["fetch"] += 1
        return list(rows)

    index = bm.InMemoryBm25Index(fetch, **kw)
    index.calls = calls
    # Как у индекса из Bm25IndexCache: держим после поиска. Ноль (значение по умолчанию вне кэша) освобождает сразу.
    index.retain_seconds = 3600.0
    return index


@pytest.fixture(autouse=True)
def _no_trim(monkeypatch):
    monkeypatch.setattr(bm, "_trim_heap", lambda reason: None)


def dv(doc, chunk, text="x"):
    return SimpleNamespace(document_id=doc, chunk_index=chunk, content=text)


class TestHelpers:
    def test_tokenizer(self):
        assert bm.tokenize_ru_en("Кошка, СОБАКА! cat_dog-2 ёж") == ["кошка", "собака", "cat_dog", "2", "ёж"]
        assert bm.tokenize_ru_en("") == [] and bm.tokenize_ru_en(None) == [] and bm.tokenize_ru_en("!!!  ...") == []

    def test_rrf_is_monotonic_and_zero_based(self):
        assert bm.rrf_score(0) == pytest.approx(1 / 61) and bm.rrf_score(0, rrf_k=10) == pytest.approx(1 / 11)
        assert bm.rrf_score(0) > bm.rrf_score(1) > bm.rrf_score(50) > 0


class TestBuildAndSearch:
    async def test_search_ranks_by_relevance(self):
        index = make_index()

        results = await index.search("договор поставки", 3)

        assert {(d, c) for d, c, _ in results[:2]} == {(3, 0), (3, 1)}
        assert all(score > 0 for _, _, score in results) and results == sorted(results, key=lambda r: r[2], reverse=True)
        assert index.ready and index.chunk_count == 6 and index.calls["fetch"] == 1

    async def test_index_is_built_once(self):
        index = make_index()

        await index.search("кошка", 3)
        await index.search("собака", 3)

        assert index.calls["fetch"] == 1

    async def test_zero_scores_are_cut_on_a_normal_corpus(self):
        index = make_index()

        results = await index.search("кошка", 6)

        assert {(d, c) for d, c, _ in results} == {(1, 0), (2, 0)}

    async def test_small_corpora_keep_zero_scores(self):
        index = make_index(ROWS[:2])

        results = await index.search("кошка", 5)

        assert len(results) == 2  # IDF вырождается при N<5: ничего не отсекаем

    async def test_k_limits_the_result(self):
        assert len(await make_index().search("договор поставки кошка собака", 2)) == 2

    @pytest.mark.parametrize("query", ["", "   ", "!!!", None])
    async def test_empty_queries(self, query):
        assert await make_index().search(query, 3) == []

    async def test_empty_corpus_refetches_on_every_search_known_issue(self):
        # Пустой корпус: needs_rebuild сбрасывается, но ensure_built требует непустой index —
        # поэтому каждый поиск заново читает все тексты из БД (TESTING_KNOWN_BUGS.md).
        index = make_index([])

        assert await index.search("кошка", 3) == []
        assert await index.search("кошка", 3) == []
        assert index.calls["fetch"] == 2 and not index.ready and index.needs_rebuild is False

    async def test_fetch_errors_are_swallowed(self):
        async def broken():
            raise RuntimeError("db down")

        index = bm.InMemoryBm25Index(broken)

        assert await index.search("кошка", 3) == [] and not index.ready

    async def test_mark_dirty_releases_and_forces_a_rebuild(self):
        index = make_index()
        await index.search("кошка", 3)

        index.mark_dirty()
        assert not index.ready and index.chunk_count == 0 and index.needs_rebuild is True

        await index.search("кошка", 3)
        assert index.calls["fetch"] == 2

    async def test_parallel_searches_share_one_build(self):
        index = make_index()

        await asyncio.gather(*[index.search("кошка", 3) for _ in range(5)])

        assert index.calls["fetch"] == 1

    async def test_in_flight_counter_returns_to_zero_even_on_errors(self):
        index = make_index()
        await index.search("кошка", 3)
        index.index = SimpleNamespace(get_scores=lambda q: (_ for _ in ()).throw(RuntimeError("boom")))

        assert await index.search("кошка", 3) == [] and index.in_flight == 0


class TestStore:
    class Store:
        def __init__(self, fingerprint="fp1", blob=None, load_error=None, save_error=None):
            self.fingerprint, self.blob, self.load_error, self.save_error = fingerprint, blob, load_error, save_error
            self.saved = []

        async def load(self):
            if self.load_error:
                raise self.load_error
            return self.fingerprint, self.blob

        async def save(self, fingerprint, payload, count):
            if self.save_error:
                raise self.save_error
            self.saved.append((fingerprint, payload, count))

    async def test_miss_builds_from_text_and_saves_under_the_loaded_fingerprint(self):
        store = self.Store()
        index = make_index(load_from_store=store.load, save_to_store=store.save)

        await index.search("кошка", 3)

        (fingerprint, payload, count), = store.saved
        assert fingerprint == "fp1" and count == 6 and isinstance(payload, bytes) and payload

    async def test_hit_skips_the_build(self):
        first = self.Store()
        index = make_index(load_from_store=first.load, save_to_store=first.save)
        await index.search("кошка", 3)
        payload = first.saved[0][1]

        second = self.Store(blob=payload)
        restored = make_index(load_from_store=second.load, save_to_store=second.save)

        results = await restored.search("договор", 3)

        assert restored.calls["fetch"] == 0 and {d for d, _, _ in results} == {3} and second.saved == []

    async def test_a_corrupt_blob_falls_back_to_building(self):
        store = self.Store(blob=b"not a valid blob")
        index = make_index(load_from_store=store.load, save_to_store=store.save)

        assert await index.search("кошка", 3) and index.calls["fetch"] == 1

    async def test_storage_errors_never_break_the_search(self):
        store = self.Store(load_error=RuntimeError("pg down"), save_error=RuntimeError("pg down"))
        index = make_index(load_from_store=store.load, save_to_store=store.save)

        assert await index.search("кошка", 3) and index.ready

    async def test_nothing_is_saved_without_a_fingerprint(self):
        store = self.Store(fingerprint=None)
        index = make_index(load_from_store=store.load, save_to_store=store.save)

        await index.search("кошка", 3)

        assert store.saved == []

    async def test_save_failure_keeps_the_built_index(self):
        store = self.Store(save_error=RuntimeError("readonly"))
        index = make_index(load_from_store=store.load, save_to_store=store.save)

        assert await index.search("кошка", 3) and index.ready


class TestRelease:
    async def test_release_drops_everything(self):
        index = make_index()
        await index.search("кошка", 3)
        index._pending_fingerprint = "fp"

        index.release()
        index.release()  # повторно — безопасно

        assert not index.ready and index.metadatas == [] and index.chunk_count == 0 and index._pending_fingerprint is None

    async def test_idle_index_is_released_right_after_the_search(self):
        index = make_index()
        index.retain_seconds = 0  # значение по умолчанию вне кэша

        await index.search("кошка", 3)

        assert not index.ready and index.needs_rebuild is True

    async def test_claimed_index_is_kept(self):
        index = make_index()
        index.retain_seconds = 0
        index.claim(ttl=60)
        index.claimed_until = time.monotonic() + 60  # claim() сбрасывается поиском, проверяем сам _release_idle
        await index.ensure_built()

        index._release_idle("test")

        assert index.ready

    async def test_active_searches_block_the_release(self):
        index = make_index()
        await index.ensure_built()
        index.in_flight = 1

        index._release_idle("test")

        assert index.ready

    async def test_deferred_release_skips_an_index_used_in_the_meantime(self):
        index = make_index()
        await index.ensure_built()

        index._release_idle("pause", only_if_last_used=index.last_used - 5)
        assert index.ready

        index._release_idle("pause", only_if_last_used=index.last_used)
        assert not index.ready

    async def test_retention_pause_schedules_a_deferred_release(self):
        index = make_index()
        index.retain_seconds = 0.05

        await index.search("кошка", 3)
        assert index.ready  # пауза ещё идёт

        await asyncio.sleep(0.2)
        assert not index.ready

    async def test_claim_is_cleared_by_the_search(self):
        index = make_index()
        index.claim()
        assert index.claimed_until > time.monotonic()

        await index.search("кошка", 3)

        assert index.claimed_until == 0.0


class TestCache:
    def factory(self):
        return lambda: make_index()

    def test_same_key_gives_the_same_index_and_it_is_claimed(self):
        cache = bm.Bm25IndexCache(retain_seconds=30)

        a = cache.get_or_create("k", self.factory())
        b = cache.get_or_create("k", self.factory())

        assert a is b and len(cache) == 1 and a.retain_seconds == 30 and a.claimed_until > time.monotonic()

    def test_negative_retention_becomes_zero(self):
        assert bm.Bm25IndexCache(retain_seconds=-5).get_or_create("k", self.factory()).retain_seconds == 0.0

    async def test_idle_indexes_are_swept_on_the_next_access(self):
        cache = bm.Bm25IndexCache(retain_seconds=10)
        idle = cache.get_or_create("idle", self.factory())
        await idle.ensure_built()
        idle.claimed_until = 0.0
        idle.last_used = time.monotonic() - 100

        cache.get_or_create("other", self.factory())

        assert not idle.ready and idle.needs_rebuild is True and len(cache) == 2  # запись остаётся, индекс отпущен

    async def test_busy_claimed_and_requested_indexes_survive_the_sweep(self):
        cache = bm.Bm25IndexCache(retain_seconds=10)
        busy, claimed, requested = (cache.get_or_create(k, self.factory()) for k in ("busy", "claimed", "requested"))
        for idx in (busy, claimed, requested):
            await idx.ensure_built()
            idx.claimed_until = 0.0
            idx.last_used = time.monotonic() - 100
        busy.in_flight = 1
        claimed.claim()

        cache.get_or_create("requested", self.factory())

        assert busy.ready and claimed.ready and requested.ready

    async def test_drop_by_predicate_releases_memory(self):
        cache = bm.Bm25IndexCache(retain_seconds=10)
        a = cache.get_or_create(("kb", 1), self.factory())
        b = cache.get_or_create(("agent", 2), self.factory())
        await a.ensure_built()

        assert cache.drop(lambda key: key[0] == "kb") == 1

        assert not a.ready and len(cache) == 1 and cache.values() == [b] and cache.items() == [(("agent", 2), b)]
        assert cache.drop(lambda key: False) == 0


class TestHybrid:
    async def combine(self, vectors, *, rows=ROWS, k=3, w=0.35, **kw):
        index = make_index(rows)
        store = {(d, c): dv(d, c, t) for d, c, t in rows}
        fetched = []

        async def fetch_chunk(doc, chunk):
            fetched.append((doc, chunk))
            return store.get((doc, chunk))

        out = await bm.hybrid_combine_vector_bm25("договор поставки", vectors, k, bm25_index=index, bm25_weight=w, fetch_chunk=fetch_chunk, **kw)
        return out, fetched

    async def test_documents_found_by_both_channels_lead(self):
        vectors = [(dv(3, 0), 0.9), (dv(1, 0), 0.8), (dv(2, 0), 0.7)]

        out, _ = await self.combine(vectors)

        assert (out[0][0].document_id, out[0][0].chunk_index) == (3, 0) and out == sorted(out, key=lambda r: r[1], reverse=True)

    async def test_bm25_only_hits_are_fetched_and_guaranteed_a_slot(self):
        vectors = [(dv(1, 0), 0.9), (dv(1, 1), 0.8), (dv(2, 0), 0.7), (dv(2, 1), 0.6)]

        out, fetched = await self.combine(vectors, k=3, w=0.35)

        keys = {(v.document_id, v.chunk_index) for v, _ in out}
        assert (3, 0) in keys and (3, 0) in fetched and len(out) == 3  # sparse-гарантия: round(3*0.35)=1 слот

    async def test_zero_weight_is_vector_only(self):
        vectors = [(dv(1, 0), 0.9), (dv(1, 1), 0.8)]

        out, _ = await self.combine(vectors, k=2, w=0.0)

        assert [(v.document_id, v.chunk_index) for v, _ in out] == [(1, 0), (1, 1)]

    async def test_weight_is_clamped(self):
        vectors = [(dv(1, 0), 0.9)]

        high, _ = await self.combine(vectors, k=2, w=5.0)
        low, _ = await self.combine(vectors, k=2, w=-1.0)

        assert high and low

    async def test_document_filter_narrows_the_sparse_channel(self):
        out, _ = await self.combine([], k=5, document_id=3)

        assert {v.document_id for v, _ in out} == {3}

    async def test_allowed_documents_filter(self):
        out, _ = await self.combine([], k=5, allowed_document_ids={1})

        assert {v.document_id for v, _ in out} <= {1}

    async def test_nothing_found(self):
        index = make_index([])

        async def fetch_chunk(doc, chunk):
            return None

        assert await bm.hybrid_combine_vector_bm25("q", [], 3, bm25_index=index, bm25_weight=0.3, fetch_chunk=fetch_chunk) == []

    async def test_missing_chunks_are_skipped(self):
        index = make_index()

        async def fetch_chunk(doc, chunk):
            return None

        out = await bm.hybrid_combine_vector_bm25("договор поставки", [(dv(1, 0), 0.9)], 5, bm25_index=index, bm25_weight=0.35, fetch_chunk=fetch_chunk)

        assert [(v.document_id, v.chunk_index) for v, _ in out] == [(1, 0)]

    async def test_fetch_may_return_a_tuple(self):
        index = make_index()

        async def fetch_chunk(doc, chunk):
            return (dv(doc, chunk), 0.0)

        out = await bm.hybrid_combine_vector_bm25("договор", [], 2, bm25_index=index, bm25_weight=0.5, fetch_chunk=fetch_chunk)

        assert out and all(hasattr(v, "document_id") for v, _ in out)

    async def test_duplicate_vector_hits_count_once(self):
        vectors = [(dv(1, 0), 0.9), (dv(1, 0), 0.8), (dv(1, 1), 0.7)]

        out, _ = await self.combine(vectors, k=5, w=0.0)

        keys = [(v.document_id, v.chunk_index) for v, _ in out]
        assert keys[:2] == [(1, 0), (1, 1)] and keys.count((1, 0)) == 1  # BM25-кандидаты при w=0 остаются в хвосте с нулевым весом
