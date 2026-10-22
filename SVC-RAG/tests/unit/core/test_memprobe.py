"""core.memprobe: режимы, приборы (/proc подменяется), probe(), снимок состояния, middleware запроса."""

from __future__ import annotations

import io
import logging
import tracemalloc
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import app as app_pkg
import app.services as services_pkg
from app.core import config as cfg
from app.core import memprobe

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(memprobe, "_mode_cache", None)
    monkeypatch.setattr(memprobe, "_mode_override", None)
    # Настройки сервиса к этому моменту могут быть загружены (memprobe: on в тестовом конфиге) - изолируемся.
    monkeypatch.setattr(cfg, "_settings", None, raising=False)
    yield
    monkeypatch.undo()
    if tracemalloc.is_tracing():
        tracemalloc.stop()


@pytest.fixture
def debug_log(caplog):
    caplog.set_level(logging.DEBUG, logger=memprobe.logger.name)
    return caplog


def mem_lines(caplog):
    # только строки замеров (с разделителем «|»), без служебных «режим замеров переключён»
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("[MEM]") and " | " in r.getMessage()]


# ───────────────────────────── режимы ─────────────────────────────


class TestNormalize:
    @pytest.mark.parametrize("raw,expected", [
        (True, "on"), (False, "off"), ("TRUE", "on"), (" yes ", "on"), ("1", "on"),
        ("false", "off"), ("no", "off"), ("0", "off"), ("none", "off"), ("", "off"), (None, "off"),
        ("full", "full"), ("ON", "on"), ("off", "off"), ("странное", "off"),
    ])
    def test_values(self, raw, expected):
        assert memprobe._normalize(raw) == expected


class TestMode:
    def test_off_when_settings_not_loaded(self, monkeypatch):
        monkeypatch.setattr(cfg, "_settings", None, raising=False)

        assert memprobe.mode() == "off"
        # «Не загружено» не кэшируется: после загрузки режим подхватится
        assert memprobe._mode_cache is None

    def test_reads_loaded_settings_once_and_caches(self, monkeypatch):
        monkeypatch.setattr(cfg, "_settings", SimpleNamespace(rag=SimpleNamespace(memprobe=True)), raising=False)

        assert memprobe.mode() == "on"
        monkeypatch.setattr(cfg, "_settings", SimpleNamespace(rag=SimpleNamespace(memprobe="full")), raising=False)
        assert memprobe.mode() == "on"

    def test_broken_settings_give_off(self, monkeypatch):
        monkeypatch.setattr(cfg, "_settings", SimpleNamespace(), raising=False)

        assert memprobe.mode() == "off"

    def test_override_wins_and_none_returns_to_settings(self, monkeypatch):
        monkeypatch.setattr(cfg, "_settings", SimpleNamespace(rag=SimpleNamespace(memprobe="on")), raising=False)

        assert memprobe.set_mode("full") == "full" and memprobe.mode() == "full"
        assert memprobe.set_mode(None) == "on"

    def test_leaving_full_stops_tracemalloc(self):
        memprobe.set_mode("full")
        assert memprobe.ensure_trace_started() is True and tracemalloc.is_tracing()

        assert memprobe.set_mode("on") == "on"
        assert not tracemalloc.is_tracing()

    def test_set_mode_normalizes_garbage_to_off(self):
        assert memprobe.set_mode("???") == "off"


# ───────────────────────────── приборы ─────────────────────────────


class TestInstruments:
    def test_rss_mb_from_statm(self, monkeypatch):
        monkeypatch.setattr(memprobe, "open", lambda *a, **k: io.BytesIO(b"10 512 1"), raising=False)
        monkeypatch.setattr(memprobe.os, "sysconf", lambda name: 4096, raising=False)

        assert memprobe.rss_mb() == 2.0

    def test_rss_mb_none_without_proc(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError

        monkeypatch.setattr(memprobe, "open", boom, raising=False)

        assert memprobe.rss_mb() is None

    def test_proc_status_parses_known_keys_in_mb(self, monkeypatch):
        text = "Name:\tpython\nVmPeak:\t 2048 kB\nVmSize: 1024 kB\nVmHWM:\t512 kB\nVmRSS:\t256 kB\nThreads:\t7\nEmpty:\nVmLck:\t5 kB\n"
        monkeypatch.setattr(memprobe, "open", lambda *a, **k: io.StringIO(text), raising=False)

        assert memprobe._proc_status() == {
            "vm_peak_mb": 2.0, "vm_size_mb": 1.0, "rss_peak_mb": 0.5, "rss_mb": 0.25, "threads": 7.0,
        }

    def test_proc_status_skips_key_without_value(self, monkeypatch):
        monkeypatch.setattr(memprobe, "open", lambda *a, **k: io.StringIO("VmRSS:\nThreads: 3\n"), raising=False)

        assert memprobe._proc_status() == {"threads": 3.0}

    def test_proc_status_empty_without_proc(self, monkeypatch):
        def boom(*a, **k):
            raise OSError

        monkeypatch.setattr(memprobe, "open", boom, raising=False)

        assert memprobe._proc_status() == {}

    def test_blocks_counts_and_survives_errors(self, monkeypatch):
        assert memprobe.blocks() > 0

        def boom():
            raise RuntimeError

        monkeypatch.setattr(memprobe.sys, "getallocatedblocks", boom)
        assert memprobe.blocks() == 0

    def test_traced_zero_when_not_tracing_and_real_values_when_tracing(self):
        assert memprobe.traced() == (0, 0)
        tracemalloc.start()
        keep = [bytearray(1000) for _ in range(10)]

        cur, peak = memprobe.traced()

        assert cur > 0 and peak >= cur and keep

    def test_traced_survives_errors(self, monkeypatch):
        monkeypatch.setattr(memprobe.tracemalloc, "is_tracing", lambda: (_ for _ in ()).throw(RuntimeError()))

        assert memprobe.traced() == (0, 0)
        assert memprobe.tracing() is False


class TestTrace:
    def test_start_and_stop(self):
        assert memprobe.tracing() is False
        assert memprobe.stop_trace() is False

        assert memprobe.ensure_trace_started() is True and memprobe.tracing() is True
        # повторный старт - просто True
        assert memprobe.ensure_trace_started() is True
        assert memprobe.stop_trace() is True and memprobe.tracing() is False

    def test_start_failure_returns_false(self, monkeypatch):
        def boom(n):
            raise RuntimeError("нельзя")

        monkeypatch.setattr(memprobe.tracemalloc, "start", boom)

        assert memprobe.ensure_trace_started() is False

    def test_stop_failure_returns_false(self, monkeypatch):
        monkeypatch.setattr(memprobe.tracemalloc, "is_tracing", lambda: True)

        def boom():
            raise RuntimeError

        monkeypatch.setattr(memprobe.tracemalloc, "stop", boom)

        assert memprobe.stop_trace() is False


class TestFormatting:
    @pytest.mark.parametrize("n,expected", [(182350, "+182 350"), (-5, "-5"), (0, "+0"), (1000000, "+1 000 000")])
    def test_fmt_num(self, n, expected):
        assert memprobe._fmt_num(n) == expected

    @pytest.mark.parametrize("x,expected", [(None, "н/д"), (0, "0.00 МБ"), (1.5, "+1.50 МБ"), (-2.25, "-2.25 МБ")])
    def test_fmt_mb(self, x, expected):
        assert memprobe._fmt_mb(x) == expected


# ───────────────────────────── probe ─────────────────────────────


class TestProbe:
    def test_off_mode_runs_body_and_logs_nothing(self, debug_log):
        ran = []

        with memprobe.probe("метка"):
            ran.append(1)

        assert ran == [1] and mem_lines(debug_log) == []

    def test_on_mode_logs_rss_blocks_time_and_extra(self, debug_log, monkeypatch):
        memprobe.set_mode("on")
        values = iter([10.0, 12.5])
        monkeypatch.setattr(memprobe, "rss_mb", lambda: next(values))

        with memprobe.probe("распаковка", байт=42):
            pass

        line = mem_lines(debug_log)[-1]
        assert "распаковка" in line and "RSS 10.0->12.5 (+2.50 МБ)" in line
        assert "блоки" in line and "мс" in line and "байт=42" in line
        assert "питон" not in line

    def test_rss_unavailable_is_reported(self, debug_log, monkeypatch):
        memprobe.set_mode("on")
        monkeypatch.setattr(memprobe, "rss_mb", lambda: None)

        with memprobe.probe("x"):
            pass

        assert "RSS н/д" in mem_lines(debug_log)[-1]

    def test_full_mode_adds_tracemalloc_numbers(self, debug_log):
        memprobe.set_mode("full")

        with memprobe.probe("тяжёлая"):
            keep = [bytearray(100_000) for _ in range(5)]

        line = mem_lines(debug_log)[-1]
        assert "питон +" in line and "пик" in line and keep

    def test_nested_probes_are_indented(self, debug_log):
        memprobe.set_mode("on")

        with memprobe.probe("внешний"):
            with memprobe.probe("внутренний"):
                pass

        lines = mem_lines(debug_log)
        inner = next(l for l in lines if "внутренний" in l)
        outer = next(l for l in lines if "внешний" in l)
        assert inner.startswith("[MEM]   внутренний") and outer.startswith("[MEM] внешний")
        assert memprobe._depth.get() == 0

    def test_exception_in_body_propagates_and_depth_is_restored(self, debug_log):
        memprobe.set_mode("on")

        with pytest.raises(ValueError):
            with memprobe.probe("падает"):
                raise ValueError("из блока")

        assert memprobe._depth.get() == 0
        assert any("падает" in l for l in mem_lines(debug_log))

    def test_failed_instruments_on_entry_do_not_break_body(self, debug_log, monkeypatch):
        memprobe.set_mode("on")
        calls = {"n": 0}

        def flaky_rss():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("прибор сломан")
            return 5.0

        monkeypatch.setattr(memprobe, "rss_mb", flaky_rss)
        ran = []

        with memprobe.probe("тест"):
            ran.append(1)

        # вход не удался: тело выполнено, замер пропущен без исключения
        assert ran == [1]

    def test_logging_failure_is_swallowed(self, monkeypatch):
        memprobe.set_mode("on")

        class BoomLogger:
            def debug(self, *a, **k):
                raise RuntimeError("лог сломан")

        monkeypatch.setattr(memprobe, "logger", BoomLogger())
        ran = []

        with memprobe.probe("x"):
            ran.append(1)

        assert ran == [1]

    def test_depth_reset_failure_is_swallowed(self, monkeypatch):
        memprobe.set_mode("on")

        class BadVar:
            def get(self):
                return 0

            def set(self, v):
                return object()

            def reset(self, token):
                raise RuntimeError("reset")

        monkeypatch.setattr(memprobe, "_depth", BadVar())
        ran = []

        with memprobe.probe("x"):
            ran.append(1)

        assert ran == [1]


# ───────────────────────────── снимок ─────────────────────────────


class Index:
    def __init__(self, ready=True, chunk_count=0, in_flight=0):
        self.ready, self.chunk_count, self.in_flight = ready, chunk_count, in_flight


class TestCacheCounts:
    @pytest.fixture
    def deps(self, monkeypatch):
        from app import dependencies as d

        monkeypatch.setattr(d, "_kb_service", None)
        monkeypatch.setattr(d, "_project_rag_service", None)
        monkeypatch.setattr(d, "_memory_rag_service", None)
        monkeypatch.setattr(d, "_pg", None)
        return d

    def test_services_not_created(self, deps):
        out = memprobe._cache_counts()

        assert out["bm25_база_знаний"] == "сервис ещё не создан"
        assert out["bm25_проекты"] == "сервис ещё не создан"
        assert out["bm25_библиотека"] == "сервис ещё не создан"
        assert "пул_postgres" not in out
        assert set(out["embed_routing"]) >= {"_clients", "_dims", "_repos", "_rerank_clients"}

    def test_bm25_counters_and_pool(self, deps):
        indexes = [Index(True, 10, 1), Index(True, 5, 0), Index(False, 99, 2)]
        deps._kb_service = SimpleNamespace(_bm25_by_key=SimpleNamespace(values=lambda: indexes))
        deps._project_rag_service = SimpleNamespace(_bm25_by_key=SimpleNamespace(values=lambda: []))
        deps._memory_rag_service = SimpleNamespace(_bm25_by_dim=SimpleNamespace(values=lambda: [Index(True, 3)]))
        deps._pg = SimpleNamespace(pool=SimpleNamespace(get_size=lambda: 4, get_idle_size=lambda: 3, get_max_size=lambda: 10))

        out = memprobe._cache_counts()

        assert out["bm25_база_знаний"] == {"индексов_всего": 3, "собрано_сейчас": 2, "чанков_в_памяти": 15, "идёт_поисков": 3}
        assert out["bm25_проекты"]["индексов_всего"] == 0
        assert out["bm25_библиотека"]["чанков_в_памяти"] == 3
        assert out["пул_postgres"] == {"соединений": 4, "простаивает": 3, "предел": 10}

    def test_broken_holder_is_reported_not_raised(self, deps):
        def boom():
            raise RuntimeError("кэш повреждён")

        deps._kb_service = SimpleNamespace(_bm25_by_key=SimpleNamespace(values=boom))

        assert memprobe._cache_counts()["bm25_база_знаний"] == {"ошибка": "кэш повреждён"}

    def test_broken_pool_is_reported(self, deps):
        def boom():
            raise RuntimeError("пул закрыт")

        deps._pg = SimpleNamespace(pool=SimpleNamespace(get_size=boom))

        assert memprobe._cache_counts()["пул_postgres"] == {"ошибка": "пул закрыт"}

    def test_dependencies_attribute_error_is_reported(self, deps, monkeypatch):
        class Hostile:
            def __getattr__(self, name):
                if name == "_kb_service":
                    raise RuntimeError("модуль недоступен")
                return None

        monkeypatch.setattr(app_pkg, "dependencies", Hostile(), raising=False)

        assert memprobe._cache_counts()["bm25"] == {"ошибка": "модуль недоступен"}

    def test_embed_routing_error_is_reported(self, deps, monkeypatch):
        class Hostile:
            def __getattr__(self, name):
                raise RuntimeError("роутинг сломан")

        monkeypatch.setattr(services_pkg, "embed_routing", Hostile(), raising=False)

        assert memprobe._cache_counts()["embed_routing"] == {"ошибка": "роутинг сломан"}


class TestByTypeAndAllocations:
    def test_by_type_counts_and_sorts_by_size(self, monkeypatch):
        class Big:
            def __sizeof__(self):
                return 5 * 1048576

        class Small:
            pass

        class Broken:
            def __sizeof__(self):
                raise RuntimeError("нет размера")

        objs = [Big(), Small(), Small(), Broken()]
        monkeypatch.setattr(memprobe.gc, "get_objects", lambda: objs)

        rows = memprobe._by_type(10)

        assert rows[0]["тип"] == "Big" and rows[0]["штук"] == 1 and rows[0]["размер_мб"] >= 5.0
        small = next(r for r in rows if r["тип"] == "Small")
        assert small["штук"] == 2
        assert all(r["тип"] != "Broken" for r in rows)
        assert len(memprobe._by_type(1)) == 1

    def test_by_type_returns_empty_when_gc_fails(self, monkeypatch):
        def boom():
            raise RuntimeError

        monkeypatch.setattr(memprobe.gc, "get_objects", boom)

        assert memprobe._by_type(5) == []

    def test_top_allocations_empty_when_not_tracing(self):
        assert memprobe._top_allocations(5) == []

    def test_top_allocations_from_snapshot(self, monkeypatch):
        frame = SimpleNamespace(filename="mod.py", lineno=12)
        stats = [
            SimpleNamespace(traceback=[frame], size=2 * 1048576, count=4),
            SimpleNamespace(traceback=[], size=1048576, count=1),
        ]
        snap = SimpleNamespace(statistics=lambda key: stats)
        monkeypatch.setattr(memprobe.tracemalloc, "is_tracing", lambda: True)
        monkeypatch.setattr(memprobe.tracemalloc, "take_snapshot", lambda: snap)

        rows = memprobe._top_allocations(5)

        assert rows == [
            {"где": "mod.py:12", "размер_мб": 2.0, "блоков": 4},
            {"где": "?", "размер_мб": 1.0, "блоков": 1},
        ]

    def test_top_allocations_survive_errors(self, monkeypatch):
        monkeypatch.setattr(memprobe.tracemalloc, "is_tracing", lambda: True)

        def boom():
            raise RuntimeError

        monkeypatch.setattr(memprobe.tracemalloc, "take_snapshot", boom)

        assert memprobe._top_allocations(5) == []


class TestSnapshot:
    @pytest.fixture(autouse=True)
    def stub_caches(self, monkeypatch):
        monkeypatch.setattr(memprobe, "_cache_counts", lambda: {"stub": 1})

    def test_non_linux_snapshot_has_notice_and_hint(self, monkeypatch):
        monkeypatch.setattr(memprobe, "_proc_status", lambda: {})
        monkeypatch.setattr(memprobe, "rss_mb", lambda: None)

        data = memprobe.snapshot()

        assert data["режим"] == "off"
        assert data["процесс"] == {"замечание": "не Linux - /proc недоступен"}
        assert data["кэши"] == {"stub": 1}
        assert data["питон"]["объектов_под_gc"] is None and data["питон"]["tracemalloc_идёт"] is False
        assert "rss_мб" not in data and "по_типам" not in data
        assert "memory-diag/mode?value=full" in data["замечание"]

    def test_linux_snapshot_with_rss_and_deep_scan(self, monkeypatch):
        monkeypatch.setattr(memprobe, "_proc_status", lambda: {"threads": 3.0})
        monkeypatch.setattr(memprobe, "rss_mb", lambda: 123.456)
        monkeypatch.setattr(memprobe, "_by_type", lambda limit: [{"тип": "dict", "limit": limit}])

        data = memprobe.snapshot(deep=True, limit=7)

        assert data["процесс"] == {"threads": 3.0} and data["rss_мб"] == 123.46
        assert data["по_типам"] == [{"тип": "dict", "limit": 7}]
        assert data["питон"]["объектов_под_gc"] > 0

    def test_full_mode_with_tracemalloc_reports_top_allocations(self, monkeypatch):
        memprobe.set_mode("full")
        memprobe.ensure_trace_started()
        monkeypatch.setattr(memprobe, "_top_allocations", lambda limit: [{"где": "a.py:1", "размер_мб": 1.0, "блоков": 1}])

        data = memprobe.snapshot()

        assert data["откуда_память"][0]["где"] == "a.py:1" and "замечание" not in data

    def test_full_mode_without_top_has_no_hint(self, monkeypatch):
        memprobe.set_mode("full")
        monkeypatch.setattr(memprobe, "_top_allocations", lambda limit: [])

        data = memprobe.snapshot()

        assert "откуда_память" not in data and "замечание" not in data


# ───────────────────────────── middleware ─────────────────────────────


class TestRequestProbe:
    @pytest.fixture
    def client(self):
        application = FastAPI()
        memprobe.install_request_probe(application)

        @application.get("/v1/items")
        async def items():
            return {"ok": True}

        @application.get("/v1/kb/reindex/status")
        async def status():
            return {"reindexing": False}

        return httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://rag")

    async def test_install_returns_none(self):
        assert memprobe.install_request_probe(FastAPI()) is None

    async def test_off_mode_passes_through_without_log(self, client, debug_log):
        r = await client.get("/v1/items")

        assert r.json() == {"ok": True} and mem_lines(debug_log) == []

    async def test_on_mode_logs_request_line(self, client, debug_log):
        memprobe.set_mode("on")

        r = await client.get("/v1/items")

        assert r.status_code == 200
        assert any("ЗАПРОС GET /v1/items" in l for l in mem_lines(debug_log))

    async def test_polling_paths_are_not_measured(self, client, debug_log):
        memprobe.set_mode("on")

        r = await client.get("/v1/kb/reindex/status")

        assert r.status_code == 200 and mem_lines(debug_log) == []
