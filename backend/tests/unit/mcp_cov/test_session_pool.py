"""mcp/session_pool: warm-пул streamable-http сессий — TTL, лимит, метрики, вытеснение, shutdown."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.mcp import session_pool as sp

pytestmark = pytest.mark.unit


class Clock:
    """Управляемые часы вместо time.monotonic в модуле пула."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class FakeSession:
    def __init__(self, server_id="srv", close_error=None):
        self.server_id = server_id
        self.close_error = close_error
        self.closed = 0
        self.from_pool = False

    async def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error


def make_pool(factory=None, max_size=2, ttl=60, acquire_timeout=1.0, monkeypatch=None):
    cfg = SimpleNamespace(max_size=max_size, idle_ttl_seconds=ttl, acquire_timeout_seconds=acquire_timeout)
    created = []

    async def default_factory(server_id, **kw):
        s = FakeSession(server_id)
        created.append((server_id, kw))
        return s

    pool = sp.McpSessionPool(cfg, factory or default_factory)
    pool.created = created
    return pool


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(sp, "time", SimpleNamespace(monotonic=c.monotonic, perf_counter=lambda: 0.0))
    return c


class TestMetrics:
    def test_empty_metrics(self):
        d = sp.McpPoolMetrics().to_dict()
        assert d["hit_rate"] == 0.0 and d["avg_acquire_ms"] == 0.0 and d["acquire_total"] == 0

    def test_rates_are_rounded(self):
        m = sp.McpPoolMetrics(acquire_total=3, hit_total=1, miss_total=2, acquire_ms_total=10.0, discard_total=4, idle_sessions=1)
        d = m.to_dict()
        assert d["hit_rate"] == 0.3333 and d["avg_acquire_ms"] == 3.33
        assert d["discard_total"] == 4 and d["idle_sessions"] == 1


class TestAcquire:
    async def test_miss_creates_session_and_passes_kwargs(self, clock):
        pool = make_pool()
        s = await pool.acquire("srv", headers={"A": "1"}, fingerprint="fp")
        assert isinstance(s, FakeSession) and pool.created == [("srv", {"headers": {"A": "1"}, "fingerprint": "fp"})]
        assert pool.metrics.acquire_total == 1 and pool.metrics.miss_total == 1 and pool.metrics.hit_total == 0

    async def test_release_then_acquire_reuses_session(self, clock):
        pool = make_pool()
        s = await pool.acquire("srv")
        await pool.release(s)
        assert pool.metrics.idle_sessions == 1 and s.from_pool is True
        again = await pool.acquire("srv")
        assert again is s and pool.metrics.hit_total == 1 and pool.metrics.idle_sessions == 0
        assert len(pool.created) == 1

    async def test_pools_are_separate_per_server(self, clock):
        pool = make_pool()
        a = await pool.acquire("a")
        await pool.release(a)
        b = await pool.acquire("b")
        assert b is not a and len(pool.created) == 2

    async def test_expired_entry_is_closed_and_replaced(self, clock):
        pool = make_pool(ttl=60)
        old = await pool.acquire("srv")
        await pool.release(old)
        clock.now += 61
        fresh = await pool.acquire("srv")
        assert old.closed == 1 and fresh is not old and pool.metrics.miss_total == 2

    async def test_expired_close_error_is_suppressed(self, clock):
        pool = make_pool(ttl=60)
        old = FakeSession("srv", close_error=RuntimeError("boom"))
        await pool.release(old)
        clock.now += 100
        fresh = await pool.acquire("srv")
        assert old.closed == 1 and fresh is not old

    async def test_ttl_boundary_is_inclusive(self, clock):
        pool = make_pool(ttl=60)
        s = await pool.acquire("srv")
        await pool.release(s)
        clock.now += 60
        assert await pool.acquire("srv") is s

    async def test_timeout_falls_back_to_direct_connect(self, clock):
        calls = []

        async def factory(server_id, **kw):
            calls.append(server_id)
            if len(calls) == 1:
                await asyncio.sleep(10)
            return FakeSession(server_id)

        pool = make_pool(factory, acquire_timeout=0.01)
        s = await pool.acquire("srv")
        assert isinstance(s, FakeSession) and calls == ["srv", "srv"] and pool.metrics.miss_total == 1

    async def test_closed_pool_connects_without_pooling(self, clock):
        pool = make_pool()
        pool._closed = True
        s = await pool.acquire("srv", x=1)
        assert pool.created == [("srv", {"x": 1})] and pool.metrics.miss_total == 1
        assert pool._pools == {}
        assert s.server_id == "srv"


class TestRelease:
    async def test_over_capacity_closes_session(self, clock):
        pool = make_pool(max_size=1)
        s1, s2 = FakeSession(), FakeSession()
        await pool.release(s1)
        await pool.release(s2)
        assert s1.closed == 0 and s2.closed == 1 and pool.metrics.idle_sessions == 1

    async def test_closed_pool_closes_session(self, clock):
        pool = make_pool()
        pool._closed = True
        s = FakeSession()
        await pool.release(s)
        assert s.closed == 1 and pool._pools == {}


class TestDiscard:
    async def test_counts_and_closes(self):
        pool = make_pool()
        s = FakeSession()
        await pool.discard(s)
        assert s.closed == 1 and pool.metrics.discard_total == 1

    async def test_close_error_is_swallowed(self):
        pool = make_pool()
        s = FakeSession(close_error=RuntimeError("x"))
        await pool.discard(s)
        assert pool.metrics.discard_total == 1


class TestGetMetrics:
    async def test_includes_per_server_and_config(self, clock):
        pool = make_pool(max_size=3, ttl=99)
        await pool.release(FakeSession("a"))
        await pool.release(FakeSession("a"))
        await pool.release(FakeSession("b"))
        m = pool.get_metrics()
        assert m["per_server_idle"] == {"a": 2, "b": 1} and m["idle_sessions"] == 3
        assert m["max_size"] == 3 and m["idle_ttl_seconds"] == 99


class TestEviction:
    async def test_evict_idle_closes_only_expired(self, clock):
        pool = make_pool(ttl=60)
        old, young = FakeSession("a"), FakeSession("a")
        await pool.release(old)
        clock.now += 50
        await pool.release(young)
        clock.now += 20  # old: 70с, young: 20с
        await pool._evict_idle()
        assert old.closed == 1 and young.closed == 0
        assert list(pool._pools["a"]) and pool._pools["a"][0].session is young

    async def test_evict_removes_empty_server_bucket_and_ignores_close_errors(self, clock):
        pool = make_pool(ttl=10)
        bad = FakeSession("a", close_error=RuntimeError("x"))
        await pool.release(bad)
        clock.now += 11
        await pool._evict_idle()
        assert "a" not in pool._pools and bad.closed == 1 and pool.metrics.idle_sessions == 0

    async def test_loop_sleeps_half_ttl_and_evicts(self, monkeypatch):
        pool = make_pool(ttl=200)
        sleeps = []
        orig_sleep = asyncio.sleep

        async def fake_sleep(delay):
            sleeps.append(delay)
            await orig_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        evicted = []

        async def fake_evict():
            evicted.append(1)
            pool._closed = True

        pool._evict_idle = fake_evict
        await pool._evict_loop()
        assert sleeps == [100] and evicted == [1]

    async def test_loop_minimum_sleep_is_five_seconds(self, monkeypatch):
        pool = make_pool(ttl=2)
        sleeps = []
        orig_sleep = asyncio.sleep

        async def fake_sleep(delay):
            sleeps.append(delay)
            await orig_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        async def fake_evict():
            pool._closed = True

        pool._evict_idle = fake_evict
        await pool._evict_loop()
        assert sleeps == [5]

    async def test_loop_survives_errors(self, monkeypatch):
        pool = make_pool(ttl=10)
        orig_sleep = asyncio.sleep

        async def fake_sleep(delay):
            await orig_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("transient")
            pool._closed = True

        pool._evict_idle = flaky
        await pool._evict_loop()
        assert len(calls) == 2

    async def test_loop_stops_on_cancel(self, monkeypatch):
        pool = make_pool(ttl=10)

        async def cancelled(delay):
            raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", cancelled)
        await pool._evict_loop()  # CancelledError перехвачен, цикл завершён


class TestLifecycle:
    async def test_start_creates_task_once_and_shutdown_cleans_up(self, clock):
        pool = make_pool()
        pool.start()
        task = pool._evict_task
        assert task is not None
        pool.start()
        assert pool._evict_task is task
        s1, s2 = FakeSession("a"), FakeSession("b", close_error=RuntimeError("x"))
        await pool.release(s1)
        await pool.release(s2)
        await pool.shutdown()
        assert pool._closed and task.cancelled() and pool._evict_task is None
        assert s1.closed == 1 and s2.closed == 1
        assert pool._pools == {} and pool.metrics.idle_sessions == 0

    async def test_shutdown_without_task(self, clock):
        pool = make_pool()
        await pool.shutdown()
        assert pool._closed is True
