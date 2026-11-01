"""realtime/handlers: вспомогательные функции уровня модуля (reindex-заглушки, метрики, trace, поток)."""

import asyncio
import logging
import sys
import types

import pytest

from backend.realtime import handlers as h

pytestmark = pytest.mark.unit


class Sio:
    def __init__(self, fail_on=None):
        self.emitted = []
        self.fail_on = fail_on

    async def emit(self, name, payload=None, room=None, **kw):
        if self.fail_on and name in self.fail_on:
            raise RuntimeError("emit failed")
        self.emitted.append((name, payload, room))


@pytest.fixture(autouse=True)
def _ctx_clean():
    h._generation_seq.clear()
    h._stopped_upto.clear()
    h._my_generation.set(0)
    tokens = h._bind_stream_ids(None, None)
    yield
    h._reset_stream_ids(tokens)
    h._my_generation.set(0)


class TestNotifyReindexWait:
    async def test_emits_chat_info(self):
        sio = Sio()
        await h._notify_reindex_wait(sio, "s1")
        assert sio.emitted[0][0] == "chat_info" and sio.emitted[0][2] == "s1"
        assert "переиндексируется" in sio.emitted[0][1]["message"]

    async def test_emit_failure_is_swallowed(self):
        await h._notify_reindex_wait(Sio(fail_on={"chat_info"}), "s1")


class TestStreamIds:
    def test_reset_with_empty_tokens_is_noop(self):
        h._reset_stream_ids(None)
        h._reset_stream_ids(())

    def test_reset_with_garbage_tokens_is_swallowed(self):
        h._reset_stream_ids(("не токен", "тоже"))

    def test_payload_gets_ids_without_overriding(self):
        tokens = h._bind_stream_ids("c1", "  r1 ")
        try:
            assert h._stream_ids_payload({"a": 1}) == {"a": 1, "conversation_id": "c1", "request_id": "r1"}
            assert h._stream_ids_payload({"conversation_id": "own"})["conversation_id"] == "own"
        finally:
            h._reset_stream_ids(tokens)
        assert h._stream_ids_payload() == {}

    def test_blank_request_id_is_none(self):
        tokens = h._bind_stream_ids("c", "   ")
        try:
            assert "request_id" not in h._stream_ids_payload()
        finally:
            h._reset_stream_ids(tokens)


class TestFlushHelper:
    async def test_flush_without_attribute_is_noop(self):
        await h._flush_ui_stream_cb(lambda *a: None)

    async def test_flush_calls_attribute(self):
        called = []

        async def flush():
            called.append(1)

        cb = lambda *a: None  # noqa: E731
        cb.flush = flush
        await h._flush_ui_stream_cb(cb)
        assert called == [1]


class TestThrottledStreamExtra:
    @staticmethod
    def _state(flush):
        return next(c.cell_contents for c in flush.__closure__
                    if isinstance(c.cell_contents, dict) and "in_flight" in c.cell_contents)

    async def test_flush_waits_for_pump_started_by_callback(self, monkeypatch):
        sio = Sio()
        monkeypatch.setattr(h, "_STREAM_EMIT_MIN_INTERVAL_S", 0.0)
        h._begin_generation("s")
        cb, flush = h._make_throttled_ui_stream_cb(sio, "s", asyncio.get_running_loop())
        assert cb("ч", "ч") is True  # pump запланирован, in_flight=True
        await flush()  # ждёт, пока pump отправит чанк
        assert [(e[0], e[1]["accumulated"]) for e in sio.emitted] == [("chat_chunk", "ч")]
        assert self._state(flush)["in_flight"] is False

    async def test_flush_emits_leftover_pending_when_idle(self, monkeypatch):
        # Состояние гонки: pump уже вышел, а pending остался - хвост уходит из flush.
        sio = Sio()
        h._begin_generation("s")
        cb, flush = h._make_throttled_ui_stream_cb(sio, "s", asyncio.get_running_loop())
        st = self._state(flush)
        st["pending"] = ("хвост", "хвост", "reasoning")
        await flush()
        assert sio.emitted[0][0] == "chat_thinking" and sio.emitted[0][1]["accumulated"] == "хвост"
        assert st["pending"] is None


class TestGenerationBookkeeping:
    def test_forget_generation(self):
        h._begin_generation("s")
        h._stopped_upto["s"] = 1
        h._forget_generation("s")
        assert "s" not in h._generation_seq and "s" not in h._stopped_upto
        h._forget_generation("s")  # повторно - без ошибки

    def test_stopped_logic(self):
        assert h._generation_stopped("s") is False  # номера нет
        seq = h._begin_generation("s")
        assert h._generation_stopped("s") is False
        h._stopped_upto["s"] = seq
        assert h._generation_stopped("s") is True


class TestAbortChatReindex:
    async def test_emits_and_saves(self, monkeypatch):
        saved = []

        async def save(text, meta, **kw):
            saved.append((text, meta, kw))

        monkeypatch.setattr(h, "save_assistant_response", save)
        sio = Sio()
        await h._abort_chat_reindex(sio, "s", "c1", "p1", {"user_id": "u"})
        name, payload, room = sio.emitted[0]
        assert name == "chat_complete" and room == "s"
        assert payload["response"] == h.REINDEX_WAIT_MESSAGE and payload["was_streaming"] is False
        assert saved == [(h.REINDEX_WAIT_MESSAGE, {"reindex_wait": True},
                          {"conversation_id": "c1", "user_id": "u", "project_id": "p1"})]

    async def test_save_error_does_not_propagate(self, monkeypatch):
        async def save(*a, **kw):
            raise RuntimeError("mongo down")

        monkeypatch.setattr(h, "save_assistant_response", save)
        sio = Sio()
        await h._abort_chat_reindex(sio, "s", "c1", None, None)
        assert sio.emitted[0][0] == "chat_complete"


class TestAbortMultiReindex:
    async def test_closes_all_slots_and_saves_combined(self, monkeypatch):
        saved = []

        async def save(text, meta, **kw):
            saved.append((text, meta, kw))

        monkeypatch.setattr(h, "save_assistant_response", save)
        sio = Sio()
        await h._abort_multi_llm_reindex(sio, "s", ["m1", "m2"], "c1", "p", {"user_id": "u"})
        assert [(e[1]["model"], e[1]["index"], e[1]["total"]) for e in sio.emitted] == [("m1", 0, 2), ("m2", 1, 2)]
        text, meta, kw = saved[0]
        assert text == f"m1:\n{h.REINDEX_WAIT_MESSAGE}\n\nm2:\n{h.REINDEX_WAIT_MESSAGE}"
        assert meta["reindex_wait"] is True and len(meta["multi_llm_responses"]) == 2
        assert kw == {"conversation_id": "c1", "user_id": "u", "project_id": "p"}

    async def test_no_conversation_means_no_save(self, monkeypatch):
        async def save(*a, **kw):
            raise AssertionError("не должно вызываться")

        monkeypatch.setattr(h, "save_assistant_response", save)
        await h._abort_multi_llm_reindex(Sio(), "s", ["m1"], None, None, None)

    async def test_save_error_swallowed(self, monkeypatch):
        async def save(*a, **kw):
            raise RuntimeError("down")

        monkeypatch.setattr(h, "save_assistant_response", save)
        await h._abort_multi_llm_reindex(Sio(), "s", ["m1"], "c", None, None)


class TestComputeRagMetrics:
    @pytest.fixture
    def fake_metrics(self, monkeypatch):
        mod = types.ModuleType("backend.rag_query.metrics")
        state = {"result": {"mrr": 1}, "error": None, "calls": []}

        async def compute(**kw):
            state["calls"].append(kw)
            if state["error"]:
                raise state["error"]
            return state["result"]

        mod.compute_online_rag_metrics = compute
        monkeypatch.setitem(sys.modules, "backend.rag_query.metrics", mod)
        return state

    @pytest.mark.parametrize("kw", [
        {"context_added": False, "document_search_trace": {"hits": [1]}, "answer": "a"},
        {"context_added": True, "document_search_trace": None, "answer": "a"},
        {"context_added": True, "document_search_trace": {"hits": [1]}, "answer": ""},
    ])
    async def test_skipped(self, fake_metrics, kw):
        sio = Sio()
        assert await h._compute_and_emit_rag_metrics(sio, "s", query="q", context_text="c", **kw) is None
        assert fake_metrics["calls"] == [] and sio.emitted == []

    async def test_emits_metrics(self, fake_metrics):
        sio = Sio()
        out = await h._compute_and_emit_rag_metrics(
            sio, "s", query="q", document_search_trace={"hits": ["h"]}, context_text="c",
            answer="a", context_added=True)
        assert out == {"mrr": 1}
        assert sio.emitted == [("chat_rag_metrics", {"metrics": {"mrr": 1}}, "s")]
        assert fake_metrics["calls"][0]["hits"] == ["h"]

    async def test_empty_metrics_not_emitted(self, fake_metrics):
        fake_metrics["result"] = None
        sio = Sio()
        out = await h._compute_and_emit_rag_metrics(
            sio, "s", query="q", document_search_trace={"x": 1}, context_text="c",
            answer="a", context_added=True)
        assert out is None and sio.emitted == []
        assert fake_metrics["calls"][0]["hits"] == []

    async def test_compute_error_returns_none(self, fake_metrics):
        fake_metrics["error"] = RuntimeError("judge down")
        sio = Sio()
        out = await h._compute_and_emit_rag_metrics(
            sio, "s", query="q", document_search_trace={"hits": [1]}, context_text="c",
            answer="a", context_added=True)
        assert out is None and sio.emitted == []

    async def test_emit_error_still_returns_metrics(self, fake_metrics):
        out = await h._compute_and_emit_rag_metrics(
            Sio(fail_on={"chat_rag_metrics"}), "s", query="q", document_search_trace={"hits": [1]},
            context_text="c", answer="a", context_added=True)
        assert out == {"mrr": 1}


class TestRegenSaveKwargs:
    def test_not_dict(self):
        assert h._regen_save_kwargs(None) == {"regenerate": False}
        assert h._regen_save_kwargs("x") == {"regenerate": False}

    def test_snake_case(self):
        out = h._regen_save_kwargs({"regenerate": 1, "assistant_message_id": " m1 ",
                                    "alternative_responses": ["a"], "current_response_index": 2})
        assert out == {"regenerate": True, "assistant_message_id": "m1",
                       "alternative_responses": ["a"], "current_response_index": 2}

    def test_camel_case_fallback(self):
        out = h._regen_save_kwargs({"alternativeResponses": ["b"], "currentResponseIndex": 0})
        assert out["alternative_responses"] == ["b"] and out["current_response_index"] == 0
        assert out["regenerate"] is False and out["assistant_message_id"] is None

    def test_bad_types_are_none(self):
        out = h._regen_save_kwargs({"alternative_responses": "x", "alternativeResponses": "y",
                                    "current_response_index": "1", "currentResponseIndex": "2"})
        assert out["alternative_responses"] is None and out["current_response_index"] is None


class TestDuration:
    def test_no_start(self):
        assert h._generation_duration_sec(None) == 1
        assert h._generation_duration_sec(0) == 1

    def test_rounds_and_min_one(self, monkeypatch):
        monkeypatch.setattr(h.time, "perf_counter", lambda: 110.0)
        assert h._generation_duration_sec(100.0) == 10
        assert h._generation_duration_sec(109.9) == 1

    def test_with_duration_copies_meta(self, monkeypatch):
        monkeypatch.setattr(h.time, "perf_counter", lambda: 105.0)
        src = {"a": 1}
        out = h._with_generation_duration(src, 100.0)
        assert out == {"a": 1, "generation_duration_sec": 5} and src == {"a": 1}
        assert h._with_generation_duration(None, None) == {"generation_duration_sec": 1}


class TestTokenBudget:
    def test_system_floor_from_defaults(self, monkeypatch):
        import backend.utils.llm_defaults as d

        monkeypatch.setattr(d, "llm_settings_defaults", lambda: {"output_tokens": 50000})
        assert h._system_output_tokens_floor() == 50000

    @pytest.mark.parametrize("defaults", [{"output_tokens": 0}, {}, {"output_tokens": "x"}])
    def test_system_floor_fallback(self, monkeypatch, defaults):
        import backend.utils.llm_defaults as d

        monkeypatch.setattr(d, "llm_settings_defaults", lambda: defaults)
        assert h._system_output_tokens_floor() == h._PRESENTATION_MAX_TOKENS_FLOOR

    def test_system_floor_when_defaults_raise(self, monkeypatch):
        import backend.utils.llm_defaults as d

        def boom():
            raise RuntimeError

        monkeypatch.setattr(d, "llm_settings_defaults", boom)
        assert h._system_output_tokens_floor() == h._PRESENTATION_MAX_TOKENS_FLOOR

    @pytest.mark.parametrize("slug,expected", [
        ("my-presentation", True), ("GPB-HTML", True), ("gpb_html_v2", True), ("deck_html", True), ("PPTX", True),
        ("slides", True), ("Презентация", True), ("coder", False), (None, False),
    ])
    def test_slug(self, slug, expected):
        assert h._slug_implies_presentation(slug) is expected

    def test_presentation_skill_active(self):
        assert h._presentation_skill_active(None) is False
        assert h._presentation_skill_active({"manual": ["x", "pptx-maker"]}) is True
        assert h._presentation_skill_active({"always_apply": ["slide"]}) is True
        assert h._presentation_skill_active({"manual": ["x"]}, ["y"], None, ["gpb-html"]) is True
        assert h._presentation_skill_active({}, ["y"], []) is False

    def test_effective_max_tokens(self, monkeypatch):
        monkeypatch.setattr(h, "_system_output_tokens_floor", lambda: 50000)
        assert h._effective_max_tokens(100, presentation=False) == 4096
        assert h._effective_max_tokens(8000, presentation=False) == 8000
        assert h._effective_max_tokens(0, presentation=False, floor=10) == 10
        assert h._effective_max_tokens(8000, presentation=True) == 50000
        monkeypatch.setattr(h, "_system_output_tokens_floor", lambda: 100)
        assert h._effective_max_tokens(8000, presentation=True) == h._PRESENTATION_MAX_TOKENS_FLOOR


class TestCtxRunners:
    def test_run_sync_preserving(self):
        assert h._run_sync_preserving_cef_audit(lambda: 42) == 42

    def test_make_ctx_runner_captures_context(self):
        h._my_generation.set(7)
        runner = h._make_ctx_runner(lambda: h._my_generation.get())
        h._my_generation.set(0)
        assert runner() == 7


class RecLogger:
    """Подмена logger модуля: логгеры проекта не пропагируют в caplog."""

    def __init__(self):
        self.records = []

    def _rec(self, level, msg, *args, **kw):
        self.records.append((level, (msg % args) if args else msg))

    def debug(self, msg, *a, **kw):
        self._rec("debug", msg, *a)

    def info(self, msg, *a, **kw):
        self._rec("info", msg, *a)

    def warning(self, msg, *a, **kw):
        self._rec("warning", msg, *a)

    def exception(self, msg, *a, **kw):
        self._rec("exception", msg, *a)

    def messages(self):
        return [m for _, m in self.records]


class TestPromptTrace:
    @pytest.fixture
    def log(self, monkeypatch):
        rec = RecLogger()
        monkeypatch.setattr(h, "logger", rec)
        return rec

    def test_plain_line(self, log):
        h._log_prompt_trace({"agent_id": "a1", "system_prompt": "Свой"}, "Свой промпт", "вопрос",
                            [{"role": "user"}, {"role": "user"}, {"role": "assistant"}, "мусор", {}])
        msg = log.messages()[0]
        assert msg.startswith("[PROMPT-TRACE] agent_id=a1")
        assert "own_prompt_in_system=True" in msg and "history=5 msgs" in msg
        assert "'user': 2" in msg and "'?': 1" in msg and "chain step" not in msg

    def test_chain_trace_detects_leaks(self, log):
        prof = {"agent_id": "b", "_chain_trace": {"index": 2, "total": 3, "prev": [
            {"agent_id": "a", "system_prompt": "секрет", "output": "результат шага"},
            {"agent_id": "z", "system_prompt": "", "output": ""},
            "не dict",
        ]}}
        h._log_prompt_trace(prof, "системный секрет", "вход: результат шага", [])
        msg = log.messages()[0]
        assert "chain step=2/3" in msg and "prev_prompts_in_system=['a']" in msg
        assert "prev_outputs_in_user=['a']" in msg

    def test_non_dict_profile_and_none_values(self, log):
        h._log_prompt_trace(None, None, None, None)
        assert "agent_id=None" in log.messages()[0] and "history=0 msgs" in log.messages()[0]

    def test_failure_is_swallowed(self, log):
        class BadDict(dict):
            def get(self, *a):
                raise RuntimeError("x")

        h._log_prompt_trace(BadDict(), "s", "u", [])
        assert log.records == [("info", "[PROMPT-TRACE] не удалось собрать строку")]


class TestPoolStylePath:
    def test_provider_path(self, monkeypatch):
        monkeypatch.setattr(h, "split_model_path", lambda p: ("prov", "m") if "::" in p else (None, p))
        assert h._multi_llm_llm_svc_pool_style_path("prov::m") is True
        assert h._multi_llm_llm_svc_pool_style_path("local.gguf") is False
        assert h._multi_llm_llm_svc_pool_style_path(None) is False


class TestConversationProjectId:
    @pytest.fixture
    def repo(self, monkeypatch):
        import backend.database.init_db as db

        state = {"conv": None, "error": None}

        class Repo:
            async def get_conversation(self, cid):
                if state["error"]:
                    raise state["error"]
                return state["conv"]

        monkeypatch.setattr(db, "get_conversation_repository", lambda: Repo(), raising=False)
        return state

    async def test_empty_id(self):
        assert await h.get_conversation_project_id("") is None

    async def test_project_returned(self, repo):
        repo["conv"] = types.SimpleNamespace(project_id="p1")
        assert await h.get_conversation_project_id("c") == "p1"

    async def test_missing_conversation(self, repo):
        assert await h.get_conversation_project_id("c") is None

    async def test_error_gives_none(self, repo):
        repo["error"] = RuntimeError("mongo")
        assert await h.get_conversation_project_id("c") is None


class TestInlineAttachmentsMetadata:
    def test_nothing(self):
        assert h._build_user_inline_attachments_metadata(None) is None
        assert h._build_user_inline_attachments_metadata([], "  ") is None

    def test_items_filtered_and_normalized(self):
        raw = [
            "не dict",
            {"name": "  ", "contentType": "text", "minio_object": "o", "minio_bucket": "b", "size": 12.7,
             "tokenEstimate": 3},
            {"name": "img.png", "content_type": "image", "size": -1, "tokenEstimate": 0},
            {"name": "x.bin", "contentType": "binary"},
        ]
        out = h._build_user_inline_attachments_metadata(raw, " текст файла ")
        assert out["inline_context"] == "текст файла"
        assert out["inline_attachments"] == [
            {"name": "file", "contentType": "text", "minio_object": "o", "minio_bucket": "b",
             "size": 12, "tokenEstimate": 3},
            {"name": "img.png", "contentType": "image"},
        ]

    def test_only_context(self):
        assert h._build_user_inline_attachments_metadata("не список", "ctx") == {"inline_context": "ctx"}
