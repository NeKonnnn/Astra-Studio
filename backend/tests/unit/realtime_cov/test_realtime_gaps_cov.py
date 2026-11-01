"""realtime: оставшиеся ветки rag_evidence / helpers / instance / handlers (граничные случаи)."""

import asyncio
import importlib
import sys
import types

import pytest

from backend.realtime import handlers as h
from backend.realtime import rag_evidence as ev
from backend.realtime import helpers as helpers

pytestmark = pytest.mark.unit


class RecLogger:
    """Подмена logger модуля с включённым DEBUG."""

    def __init__(self):
        self.lines = []

    def isEnabledFor(self, level):
        return True

    def debug(self, msg, *a, **kw):
        self.lines.append(msg % a if a else msg)

    info = warning = exception = debug


class TestRagEvidenceGaps:
    def test_named_documents_ignore_unparsable_allowed_ids(self):
        out = ev.documents_named_in_query("где Смета.xlsx", {1: "Смета.xlsx", 2: "смета.xlsx"}, only_ids=["x", None, 2])
        assert out == [2]

    def test_merge_extra_hits_skips_malformed_rows(self):
        primary = [("a", 0.9, 1, 0), "мусор", ("короткий",)]
        extra = [("b", 0.8, 1, 0), ("c", 0.7, 2, 1), None, ("a2",)]
        out = ev.merge_extra_hits(primary, extra)
        assert out == [("a", 0.9, 1, 0), "мусор", ("короткий",), ("c", 0.7, 2, 1)]

    def test_nan_scores_are_dropped_with_log(self, monkeypatch):
        rec = RecLogger()
        monkeypatch.setattr(ev, "logger", rec)
        out = ev.filter_rag_hits_by_score([("a", float("nan"), 1, 0)], 0.5)
        assert out == [] and any("отсечены порогом" in line for line in rec.lines)

    def test_bad_rows_are_ignored_when_scoring(self):
        out = ev.filter_rag_hits_by_score([("a", "не число", 1, 0), ("b", 0.9, 1, 1)], 0.5)
        assert out == [("b", 0.9, 1, 1)]

    def test_single_fragment_malformed_row(self):
        assert ev._format_single_fragment(("только", "три", 1), number=1, id_to_name={}, include_chunk_meta=False) is None

    def test_single_fragment_meta_falls_back_when_relevance_breaks(self, monkeypatch):
        import backend.rag_query.relevance as rel

        def boom(row):
            raise RuntimeError("x")

        monkeypatch.setattr(rel, "relevance_percent_or_none", boom)
        out = ev._format_single_fragment(("текст", 0.5, 1, 4), number=2, id_to_name={1: "a.txt"}, include_chunk_meta=True)
        assert out == "Фрагмент 2 (документ «a.txt», чанк 4, релевантность: 1%):\nтекст\n"

    def test_trace_without_relevance_module(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "backend.rag_query.relevance", None)
        assert ev.hits_to_document_search_trace(query="q", hits=[("a", 0.9, 1, 0)], id_to_name={1: "a"}) is None

    def test_trace_skips_bad_rows_and_names_unknown_documents(self):
        trace = ev.hits_to_document_search_trace(
            query="q", id_to_name={1: "a.txt"}, store="memory", strategy="hybrid",
            hits=[("a", 0.9, 1, 0), ("битый",), ("без документа", 0.5, None, 1), ("нечисловой", 0.4, "x", 2)],
        )
        assert [x["file"] for x in trace["hits"]] == ["a.txt", "doc*x"]
        assert trace["strategy"] == "hybrid" and trace["hits"][0]["store"] == "memory"

    def test_format_fragments_skips_trailing_malformed_row(self):
        parts, metrics = ev.format_rag_fragments(
            [("текст", 0.9, 1, 0), ("второй", 0.8, 2, 1), ("битый", 0.9)], {1: "a", 2: "b"},
            max_chars=10000, store_label="t")
        assert len(parts) == 2 and metrics["received"] == 3 and metrics["documents_input"] == 2

    def test_named_documents_skip_non_numeric_ids(self):
        assert ev.documents_named_in_query("файл a.txt", {"x": "a.txt", 3: "a.txt"}) == [3]

    def test_malformed_row_in_the_middle_should_be_skipped(self):
        parts, _ = ev.format_rag_fragments([("текст", 0.9, 1, 0), ("битый", 0.9), ("второй", 0.8, 2, 1)],
                                           {1: "a", 2: "b"}, max_chars=10000, store_label="t")
        assert len(parts) == 2 and parts[1].startswith("Фрагмент 3")

    def test_format_fragments_truncates_when_budget_ends(self):
        parts, metrics = ev.format_rag_fragments(
            [("а" * 100, 0.9, 1, 0), ("б" * 100, 0.8, 1, 1)], {1: "a"}, max_chars=170, store_label="t",
            truncate_marker="[…]")
        assert metrics["truncated_last"] == 1 and parts[-1].endswith("[…]")

    def test_selected_chunk_log_ignores_malformed_rows(self, monkeypatch):
        rec = RecLogger()
        monkeypatch.setattr(ev, "logger", rec)
        ev._log_selected_chunks(
            store_label="t", id_to_name={1: "a"}, truncated_last=False,
            indexed=[(1, 1, ("битый",)), (2, 1, ("текст", 0.5, 1, 0))], selected_entries=[None, "x"])
        text = rec.lines[0]
        assert "✓ фрагмент  2" in text and "битый" not in text


class TestHelpersGap:
    async def test_integer_recursion_limit(self, monkeypatch):
        class Repo:
            async def user_can_access_agent(self, aid, uid):
                return True

            async def get_agent(self, aid, uid):
                return types.SimpleNamespace(name="A", config={"recursion_limit": 9}, system_prompt="")

        monkeypatch.setattr("backend.database.init_db.get_agent_repository", lambda: Repo())
        monkeypatch.setattr("backend.auth.agent_platform_roles.user_can_use_agents", lambda user: True)
        out = await helpers._resolve_agent_chat_params(1)
        assert out["recursion_limit"] == 9


class TestSocketInstance:
    @pytest.mark.parametrize("raw,expected", [
        (None, 52 * 1024 * 1024), ("", 52 * 1024 * 1024), ("  ", 52 * 1024 * 1024), ("abc", 52 * 1024 * 1024),
        ("5000000", 5_000_000), ("10", 1_048_576),
    ])
    def test_max_buffer(self, monkeypatch, raw, expected):
        from backend.realtime import instance

        if raw is None:
            monkeypatch.delenv("SOCKET_MAX_HTTP_BUFFER_BYTES", raising=False)
        else:
            monkeypatch.setenv("SOCKET_MAX_HTTP_BUFFER_BYTES", raw)
        assert instance._socket_max_http_buffer_size() == expected


class FakeSioMin:
    def __init__(self):
        self.handlers = {}
        self.emitted = []
        self.sessions = {}

    def event(self, fn):
        self.handlers[fn.__name__] = fn
        return fn

    async def emit(self, name, payload=None, room=None, **kw):
        self.emitted.append((name, payload, room))

    async def save_session(self, sid, session):
        self.sessions[sid] = session

    async def get_session(self, sid):
        return self.sessions[sid]

    async def enter_room(self, sid, room):
        pass


class TestSocketEventsGaps:
    @pytest.fixture
    def sio(self):
        s = FakeSioMin()
        h.register_handlers(s)
        return s

    async def test_connect_without_environ_and_remote(self, sio, monkeypatch):
        monkeypatch.setattr(h, "decode_token_signature_only",
                            lambda token, client_binding_id=None: {"username": "a", "user_id": "u"})
        monkeypatch.setattr(h, "get_client_binding_id_from_cookie_header", lambda c: None)
        monkeypatch.setattr(h, "cef_socket_remote_from_environ", lambda e: None)
        await sio.handlers["connect"]("s1", None, {"token": "jwt"})
        assert "cef_remote" not in sio.sessions["s1"] and sio.sessions["s1"]["user"]["client_binding_id"] is None

    @pytest.mark.parametrize("payload,expected", [
        ({"conversation_id": "c"}, {"conversation_id": "c"}),
        ({"request_id": "r"}, {"request_id": "r"}),
        ({}, {}),
        ("не словарь", {}),
    ])
    async def test_stop_generation_payload_fields(self, sio, payload, expected):
        await sio.handlers["stop_generation"]("s1", payload)
        _, body, room = sio.emitted[0]
        assert room == "s1" and {k: v for k, v in body.items() if k not in ("content", "timestamp")} == expected

    async def test_chat_message_without_cef_session_dict(self, sio, monkeypatch):
        # get_session возвращает не словарь на втором чтении: cef_remote не определяется, чат продолжается.
        calls = {"n": 0}

        async def get_session(sid):
            calls["n"] += 1
            return {"user": {"user_id": "alice", "token": "jwt"}} if calls["n"] == 1 else "не словарь"

        sio.get_session = get_session
        monkeypatch.setattr(h, "ask_agent", lambda *a, **k: "x")

        async def save(*a, **kw):
            return None

        monkeypatch.setattr(h, "save_dialog_entry", save)
        monkeypatch.setattr(h, "decode_token", lambda t, client_binding_id=None: {"user_id": "alice"})

        async def resolve(*a, **kw):
            return {"model_path": None}

        async def enrich(p, uid):
            return p

        async def conv_project(cid):
            return None

        async def history(**kw):
            return []

        async def settings(*a, **kw):
            return {}

        async def strategy(uid):
            return None

        monkeypatch.setattr(h, "_resolve_agent_chat_params", resolve)
        monkeypatch.setattr(h, "enrich_agent_profile_with_user_settings", enrich)
        monkeypatch.setattr(h, "get_conversation_project_id", conv_project)
        monkeypatch.setattr(h, "get_recent_dialog_history", history)
        monkeypatch.setattr(h, "get_entity_rag_settings", settings)
        monkeypatch.setattr(h, "get_user_memory_strategy", strategy)
        import backend.agents.chain.runner as runner

        seen = []

        async def run_chain(*a, **kw):
            seen.append(a)

        monkeypatch.setattr(runner, "run_direct_or_chain", run_chain)
        await sio.handlers["chat_message"]("s1", {"message": "q"})
        assert len(seen) == 1 and sio.emitted == []
