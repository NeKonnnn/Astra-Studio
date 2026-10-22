"""core: http_verify (выбор CA bundle), heap_trim (glibc malloc_trim через подмену ctypes), mem_release (возврат памяти).

Linux-специфику (/proc, malloc_trim, sysconf) подменяем monkeypatch'ем, чтобы тесты шли и на Windows, и на Linux.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import io
import logging
from types import SimpleNamespace

import pytest

from app.core import heap_trim, http_verify, mem_release

pytestmark = pytest.mark.unit


# ───────────────────────────── http_verify ─────────────────────────────


class TestHttpVerify:
    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        for k in ("TLS_CERT_PATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            monkeypatch.delenv(k, raising=False)

    def test_defaults_to_true_without_env(self):
        assert http_verify.resolve_httpx_verify() is True

    def test_returns_path_of_existing_bundle(self, tmp_path, monkeypatch):
        ca = tmp_path / "ca.pem"
        ca.write_text("pem")
        monkeypatch.setenv("TLS_CERT_PATH", str(ca))

        assert http_verify.resolve_httpx_verify() == str(ca)

    def test_priority_tls_cert_path_over_ssl_cert_file(self, tmp_path, monkeypatch):
        first, second = tmp_path / "1.pem", tmp_path / "2.pem"
        first.write_text("a")
        second.write_text("b")
        monkeypatch.setenv("SSL_CERT_FILE", str(second))
        monkeypatch.setenv("TLS_CERT_PATH", str(first))

        assert http_verify.resolve_httpx_verify() == str(first)

    def test_missing_file_is_skipped_with_warning_and_next_is_used(self, tmp_path, monkeypatch, caplog):
        good = tmp_path / "good.pem"
        good.write_text("x")
        monkeypatch.setenv("TLS_CERT_PATH", str(tmp_path / "nope.pem"))
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(good))

        with caplog.at_level(logging.WARNING, logger=http_verify.logger.name):
            result = http_verify.resolve_httpx_verify()

        assert result == str(good)
        assert any("файл не найден" in r.getMessage() and "TLS_CERT_PATH" in r.getMessage() for r in caplog.records)

    def test_only_missing_file_falls_back_to_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "nope.pem"))

        assert http_verify.resolve_httpx_verify() is True

    def test_blank_value_is_ignored(self, monkeypatch):
        monkeypatch.setenv("TLS_CERT_PATH", "   ")

        assert http_verify.resolve_httpx_verify() is True

    def test_getsize_failure_does_not_break_resolution(self, tmp_path, monkeypatch):
        ca = tmp_path / "ca.pem"
        ca.write_text("pem")
        monkeypatch.setenv("TLS_CERT_PATH", str(ca))

        def boom(path):
            raise OSError("нет доступа")

        monkeypatch.setattr(http_verify.os.path, "getsize", boom)

        assert http_verify.resolve_httpx_verify() == str(ca)


# ───────────────────────────── heap_trim ─────────────────────────────


class FakeFn:
    """Заменяет функцию libc: принимает argtypes/restype и возвращает заданное значение."""

    def __init__(self, result=1, error=None):
        self.result, self.error, self.calls = result, error, []
        self.argtypes = self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.result


class FakeLibc:
    def __init__(self, trim_result=1, trim_error=None, mallopt_result=1, mallopt_error=None):
        self.malloc_trim = FakeFn(trim_result, trim_error)
        self.mallopt = FakeFn(mallopt_result, mallopt_error)


@pytest.fixture
def reset_heap(monkeypatch):
    """Сбросить модульное состояние heap_trim и подставить управляемые часы."""
    monkeypatch.setattr(heap_trim, "_libc", None)
    monkeypatch.setattr(heap_trim, "_looked_up", False)
    monkeypatch.setattr(heap_trim, "_last_call", 0.0)
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(heap_trim, "time", SimpleNamespace(monotonic=lambda: clock.now, perf_counter=lambda: clock.now))
    return clock


@pytest.fixture
def libc(monkeypatch, reset_heap):
    lib = FakeLibc()
    monkeypatch.setattr(ctypes, "CDLL", lambda name: lib)
    monkeypatch.setattr(ctypes.util, "find_library", lambda name: "libc.so.6")
    return lib


class TestResolve:
    def test_missing_library_gives_none_and_is_remembered(self, monkeypatch, reset_heap):
        attempts = []

        def cdll(name):
            attempts.append(name)
            raise OSError("нет libc")

        monkeypatch.setattr(ctypes, "CDLL", cdll)
        monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)

        assert heap_trim._resolve() is None
        assert heap_trim._resolve() is None
        # find_library не нашла - берём libc.so.6; попытка одна, результат запоминается
        assert attempts == ["libc.so.6"]

    def test_found_library_gets_signature_and_is_cached(self, libc):
        lib = heap_trim._resolve()

        assert lib is libc
        assert libc.malloc_trim.argtypes == [ctypes.c_size_t] and libc.malloc_trim.restype is ctypes.c_int
        assert heap_trim._resolve() is lib

    def test_library_without_malloc_trim_symbol_is_unavailable(self, monkeypatch, reset_heap):
        class Musl:
            def __getattr__(self, name):
                raise AttributeError(name)

        monkeypatch.setattr(ctypes, "CDLL", lambda name: Musl())
        monkeypatch.setattr(ctypes.util, "find_library", lambda name: "c")

        assert heap_trim._resolve() is None


class TestRss:
    def test_reads_statm_and_converts_pages_to_mb(self, monkeypatch):
        monkeypatch.setattr(heap_trim, "open", lambda *a, **k: io.BytesIO(b"1000 256 10 0 0 0 0"), raising=False)
        monkeypatch.setattr(heap_trim.os, "sysconf", lambda name: 4096, raising=False)

        assert heap_trim._rss_mb() == 1.0

    def test_unreadable_proc_gives_none(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("/proc")

        monkeypatch.setattr(heap_trim, "open", boom, raising=False)

        assert heap_trim._rss_mb() is None


class TestArenaMax:
    def test_without_libc_returns_false(self, monkeypatch, reset_heap):
        monkeypatch.setattr(heap_trim, "_resolve", lambda: None)

        assert heap_trim.set_arena_max(2) is False

    def test_sets_arena_limit_through_mallopt(self, libc):
        assert heap_trim.set_arena_max(2) is True
        assert libc.mallopt.calls == [(heap_trim.M_ARENA_MAX, 2)]
        assert heap_trim.M_ARENA_MAX == -8

    def test_mallopt_refusal_returns_false(self, monkeypatch, reset_heap):
        lib = FakeLibc(mallopt_result=0)
        monkeypatch.setattr(heap_trim, "_resolve", lambda: lib)

        assert heap_trim.set_arena_max(4) is False

    @pytest.mark.parametrize("value", [0, -3])
    def test_non_positive_count_is_rejected_without_call(self, libc, value):
        assert heap_trim.set_arena_max(value) is False
        assert libc.mallopt.calls == []

    def test_non_numeric_count_and_mallopt_exceptions_are_swallowed(self, monkeypatch, reset_heap):
        lib = FakeLibc(mallopt_error=RuntimeError("boom"))
        monkeypatch.setattr(heap_trim, "_resolve", lambda: lib)

        assert heap_trim.set_arena_max("abc") is False
        assert heap_trim.set_arena_max(2) is False

    def test_env_not_set_does_nothing(self, monkeypatch, libc):
        monkeypatch.delenv("RAG_MALLOC_ARENA_MAX", raising=False)

        assert heap_trim.set_arena_max_from_env() is False
        assert libc.mallopt.calls == []

    def test_env_garbage_is_ignored(self, monkeypatch, libc):
        monkeypatch.setenv("RAG_MALLOC_ARENA_MAX", "много")

        assert heap_trim.set_arena_max_from_env() is False
        assert libc.mallopt.calls == []

    def test_env_value_is_applied(self, monkeypatch, libc):
        monkeypatch.setenv("RAG_MALLOC_ARENA_MAX", " 3 ")

        assert heap_trim.set_arena_max_from_env() is True
        assert libc.mallopt.calls == [(-8, 3)]


class TestTrim:
    def test_no_libc_is_noop(self, monkeypatch, reset_heap):
        monkeypatch.setattr(heap_trim, "_resolve", lambda: None)

        heap_trim.trim("тест")

        assert heap_trim._last_call == 0.0

    def test_calls_malloc_trim_and_logs_rss_delta(self, libc, monkeypatch, caplog):
        rss = iter([100.0, 90.0])
        monkeypatch.setattr(heap_trim, "_rss_mb", lambda: next(rss))

        with caplog.at_level(logging.DEBUG, logger=heap_trim.logger.name):
            heap_trim.trim("индексация")

        assert libc.malloc_trim.calls == [(0,)]
        messages = [r.getMessage() for r in caplog.records]
        assert any("индексация" in m and "100.0 -> 90.0" in m and "вернули 10.0" in m for m in messages)

    def test_logs_short_message_when_rss_unknown(self, libc, monkeypatch, caplog):
        monkeypatch.setattr(heap_trim, "_rss_mb", lambda: None)

        with caplog.at_level(logging.DEBUG, logger=heap_trim.logger.name):
            heap_trim.trim("поиск")

        assert any("«поиск»" in r.getMessage() and "RSS" not in r.getMessage() for r in caplog.records)

    def test_rate_limited_to_once_per_interval(self, libc, monkeypatch, reset_heap):
        monkeypatch.setattr(heap_trim, "_rss_mb", lambda: None)

        heap_trim.trim("a")
        reset_heap.now += heap_trim.MIN_INTERVAL_SECONDS / 2
        heap_trim.trim("b")
        assert len(libc.malloc_trim.calls) == 1

        reset_heap.now += heap_trim.MIN_INTERVAL_SECONDS
        heap_trim.trim("c")
        assert len(libc.malloc_trim.calls) == 2

    def test_malloc_trim_failure_is_swallowed(self, monkeypatch, reset_heap, caplog):
        lib = FakeLibc(trim_error=OSError("segv"))
        monkeypatch.setattr(heap_trim, "_resolve", lambda: lib)

        with caplog.at_level(logging.DEBUG, logger=heap_trim.logger.name):
            heap_trim.trim("x")

        assert any("вызов не прошёл" in r.getMessage() for r in caplog.records)


# ───────────────────────────── mem_release ─────────────────────────────


@pytest.fixture
def mem(monkeypatch):
    """Чистое состояние mem_release; heap_trim.trim и gc заменены счётчиками."""
    for key in ("RAG_MEM_RELEASE", "RAG_MEM_RELEASE_MB", "RAG_MEM_RELEASE_EVERY_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(mem_release, "_floor_mb", None)
    monkeypatch.setattr(mem_release, "_task", None)
    state = SimpleNamespace(rss=[], trims=[], collects=0)

    def next_rss():
        values = state.rss
        return values.pop(0) if len(values) > 1 else (values[0] if values else None)

    monkeypatch.setattr(mem_release, "rss_mb", next_rss)
    monkeypatch.setattr(mem_release.heap_trim, "trim", lambda reason: state.trims.append(reason))

    def collect():
        state.collects += 1
        return 5

    monkeypatch.setattr(mem_release.gc, "collect", collect)
    return state


class TestEnvHelpers:
    @pytest.mark.parametrize("value,expected", [("true", True), (" TRUE ", True), ("false", False), ("0", False), ("", True)])
    def test_enabled(self, monkeypatch, value, expected):
        monkeypatch.setenv("RAG_MEM_RELEASE", value)

        assert mem_release._enabled() is expected

    def test_threshold_default_custom_and_garbage(self, monkeypatch):
        monkeypatch.delenv("RAG_MEM_RELEASE_MB", raising=False)
        assert mem_release._threshold_mb() == 16.0
        monkeypatch.setenv("RAG_MEM_RELEASE_MB", "5.5")
        assert mem_release._threshold_mb() == 5.5
        monkeypatch.setenv("RAG_MEM_RELEASE_MB", "мусор")
        assert mem_release._threshold_mb() == 16.0

    def test_period_default_custom_and_garbage(self, monkeypatch):
        monkeypatch.delenv("RAG_MEM_RELEASE_EVERY_SECONDS", raising=False)
        assert mem_release._period_seconds() == 1800.0
        monkeypatch.setenv("RAG_MEM_RELEASE_EVERY_SECONDS", "60")
        assert mem_release._period_seconds() == 60.0
        monkeypatch.setenv("RAG_MEM_RELEASE_EVERY_SECONDS", "x")
        assert mem_release._period_seconds() == 1800.0


class TestRelease:
    def test_disabled_does_nothing(self, mem, monkeypatch):
        monkeypatch.setenv("RAG_MEM_RELEASE", "false")
        mem.rss = [100.0]

        mem_release.release("r", force=True)

        assert mem.collects == 0 and mem.trims == []

    def test_first_call_only_remembers_floor(self, mem):
        mem.rss = [100.0]

        mem_release.release("r")

        assert mem_release._floor_mb == 100.0 and mem.collects == 0

    def test_growth_below_threshold_does_not_release_and_lowers_floor(self, mem):
        mem_release._floor_mb = 100.0
        mem.rss = [105.0]
        mem_release.release("r")
        assert mem.collects == 0 and mem_release._floor_mb == 100.0

        mem.rss = [90.0]
        mem_release.release("r")
        assert mem.collects == 0 and mem_release._floor_mb == 90.0

    def test_growth_over_threshold_collects_trims_and_moves_floor(self, mem):
        mem_release._floor_mb = 100.0
        mem.rss = [130.0, 110.0]

        mem_release.release("индексация")

        assert mem.collects == 1 and mem.trims == ["индексация"]
        assert mem_release._floor_mb == 110.0

    def test_force_ignores_threshold(self, mem):
        mem_release._floor_mb = 100.0
        mem.rss = [101.0, 100.0]

        mem_release.release("расписание", force=True)

        assert mem.collects == 1 and mem.trims == ["расписание"]

    def test_loud_logs_info(self, mem, caplog):
        mem.rss = [50.0, 40.0]

        with caplog.at_level(logging.INFO, logger=mem_release.logger.name):
            mem_release.release("расписание", force=True, loud=True)

        assert any(r.levelno == logging.INFO and "50.0 -> 40.0" in r.getMessage() for r in caplog.records)

    def test_without_rss_skips_unless_forced(self, mem):
        mem.rss = []

        mem_release.release("r")
        assert mem.collects == 0

        mem_release.release("r", force=True)
        assert mem.collects == 1 and mem.trims == ["r"]

    def test_failure_inside_is_swallowed(self, mem, monkeypatch, caplog):
        def boom():
            raise RuntimeError("gc сломан")

        monkeypatch.setattr(mem_release.gc, "collect", boom)
        mem.rss = [10.0]

        with caplog.at_level(logging.DEBUG, logger=mem_release.logger.name):
            mem_release.release("r", force=True)

        assert any("Освободить память" in r.getMessage() for r in caplog.records)


class TestPeriodic:
    async def test_start_disabled_by_env(self, mem, monkeypatch):
        monkeypatch.setenv("RAG_MEM_RELEASE", "false")

        mem_release.start_periodic()

        assert mem_release._task is None

    async def test_start_with_zero_period_does_not_start(self, mem, monkeypatch):
        monkeypatch.setenv("RAG_MEM_RELEASE_EVERY_SECONDS", "0")

        mem_release.start_periodic()

        assert mem_release._task is None

    async def test_start_creates_one_task_and_stop_cancels_it(self, mem, monkeypatch):
        monkeypatch.setenv("RAG_MEM_RELEASE_EVERY_SECONDS", "3600")

        mem_release.start_periodic()
        task = mem_release._task
        mem_release.start_periodic()

        assert task is not None and mem_release._task is task
        mem_release.stop_periodic()
        assert mem_release._task is None
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_start_failure_is_swallowed(self, mem, monkeypatch):
        def boom(coro):
            coro.close()
            raise RuntimeError("нет loop")

        monkeypatch.setattr(mem_release.asyncio, "create_task", boom)

        mem_release.start_periodic()

        assert mem_release._task is None

    def test_stop_without_task_is_noop(self, mem):
        mem_release.stop_periodic()

        assert mem_release._task is None

    def test_stop_swallows_cancel_errors(self, mem):
        class BadTask:
            def cancel(self):
                raise RuntimeError("x")

        mem_release._task = BadTask()

        mem_release.stop_periodic()

    async def test_loop_releases_forcibly_on_every_tick(self, mem, monkeypatch):
        calls = []
        ticks = iter([None, None])

        async def fake_sleep(period):
            try:
                next(ticks)
            except StopIteration:
                raise asyncio.CancelledError()

        monkeypatch.setattr(mem_release.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(mem_release, "release", lambda reason, force=False, loud=False: calls.append((reason, force, loud)))

        with pytest.raises(asyncio.CancelledError):
            await mem_release._periodic_loop(1)

        assert calls == [("расписание", True, True)] * 2

    async def test_loop_survives_exceptions(self, mem, monkeypatch):
        attempts = []

        async def fake_sleep(period):
            if len(attempts) >= 2:
                raise asyncio.CancelledError()

        def release(reason, force=False, loud=False):
            attempts.append(1)
            raise RuntimeError("не вышло")

        monkeypatch.setattr(mem_release.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(mem_release, "release", release)

        with pytest.raises(asyncio.CancelledError):
            await mem_release._periodic_loop(1)

        assert len(attempts) == 2
