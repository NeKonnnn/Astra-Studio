"""rag_query/stage_timer: пошаговые замеры (вложенные стадии, форматирование, лог)."""

import pytest

from backend.rag_query import stage_timer as st

pytestmark = pytest.mark.unit


@pytest.fixture
def clock(monkeypatch):
    t = {"now": 100.0}
    monkeypatch.setattr(st.time, "perf_counter", lambda: t["now"])
    return t


class TestStages:
    def test_defaults(self):
        tm = st.StageTimer("", store=None)
        assert tm.kind == "OP" and tm.store == ""

    def test_stage_measures_duration(self, clock):
        tm = st.StageTimer("SEARCH")
        with tm.stage("embed"):
            clock["now"] += 2.5
        assert tm.items() == [("embed", 2.5)]

    def test_blank_stage_name_is_unknown(self, clock):
        tm = st.StageTimer("X")
        with tm.stage("  "):
            clock["now"] += 1
        assert tm.items() == [("unknown", 1.0)]

    def test_nested_stage_closes_previous(self, clock):
        tm = st.StageTimer("X")
        with tm.stage("outer"):
            clock["now"] += 1
            with tm.stage("inner"):
                clock["now"] += 2
        # outer закрыт при входе во вложенную стадию (1 с), inner - 2 с, повторно outer не пишется.
        assert tm.items() == [("outer", 1.0), ("inner", 2.0)]

    def test_exception_still_records_stage(self, clock):
        tm = st.StageTimer("X")
        with pytest.raises(ValueError):
            with tm.stage("boom"):
                clock["now"] += 3
                raise ValueError
        assert tm.items() == [("boom", 3.0)]

    def test_mark_clamps_negative_and_normalizes_name(self):
        tm = st.StageTimer("X")
        tm.mark("a", -5)
        tm.mark(None, "1.5")
        assert tm.items() == [("a", 0.0), ("unknown", 1.5)]

    def test_items_returns_copy(self):
        tm = st.StageTimer("X")
        tm.mark("a", 1)
        tm.items().append(("z", 9))
        assert tm.items() == [("a", 1.0)]

    def test_elapsed(self, clock):
        tm = st.StageTimer("X")
        clock["now"] += 4
        assert tm.elapsed() == 4


class TestFormat:
    def test_no_stages(self, clock):
        assert st.StageTimer("SEARCH").format_line() == "[RAG-TIMING SEARCH] total=0.000s | (нет стадий)"

    def test_full_line_with_meta(self, clock):
        tm = st.StageTimer("UPLOAD", store="project", file="a b.txt", n=3, empty="", none=None)
        tm.mark("parse", 1.234)
        tm.mark("index", 0.5)
        clock["now"] += 2
        assert tm.format_line() == (
            "[RAG-TIMING UPLOAD project] file='a b.txt' n=3 total=2.000s | parse=1.234s | index=0.500s"
        )


class TestLog:
    def test_logs_at_level(self):
        calls = []

        class L:
            def log(self, level, msg):
                calls.append((level, msg))

        st.StageTimer("X").log(L(), level=30)
        assert calls[0][0] == 30 and calls[0][1].startswith("[RAG-TIMING X]")

    def test_logger_failure_is_swallowed(self):
        class L:
            def log(self, level, msg):
                raise RuntimeError("log down")

        st.StageTimer("X").log(L())
