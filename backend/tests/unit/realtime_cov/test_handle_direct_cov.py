"""realtime/handlers._handle_direct: прямой чат (RAG, плагины, навыки, субагенты, MCP, стрим, стоп, сохранение)."""

import asyncio
import sys
import types

import pytest

from backend.rag_query.prompts import RAG_STRICT_NOT_FOUND_MESSAGE
from backend.realtime import handlers as h
from backend.realtime.rag_evidence import RAG_SEARCH_FAILED_MESSAGE
from backend.settings.rag_client import RagReindexInProgress

pytestmark = pytest.mark.unit

USER = {"user_id": "alice", "username": "alice"}


def profile(**over):
    base = {"model_path": "llm-svc://m", "system_prompt": "", "temperature": 0.5, "max_tokens": 2000,
            "agent_id": 7, "name": "Агент"}
    base.update(over)
    return base


class Cb:
    """Фейковый sync_stream_cb: записывает вызовы, умеет flush."""

    def __init__(self, result=True):
        self.calls = []
        self.flushed = 0
        self.result = result

    def __call__(self, chunk, acc, role="content"):
        self.calls.append((chunk, acc, role))
        return self.result

    async def flush(self):
        self.flushed += 1


class CollectSio:
    def __init__(self):
        self.emitted = []

    async def emit(self, name, payload=None, room=None, **kw):
        self.emitted.append((name, payload, room))

    def payloads(self, name):
        return [p for n, p, _ in self.emitted if n == name]

    def names(self):
        return [n for n, _, _ in self.emitted]


class Env:
    def __init__(self):
        self.answer = "ответ модели"
        self.ask_calls = []
        self.stream_script = []
        self.after_ask = None
        self.saved = []
        self.save_error = None
        self.cpm = None
        self.feedback = ""
        self.replace_with = None
        self.replace_calls = []
        self.skills_result = None
        self.skills_error = None
        self.required_result = None
        self.required_error = None
        self.required_events = []
        self.mcp_calls = []
        self.mcp_result = None
        self.mcp_error = None
        self.mcp_events = []
        self.metrics = {"mrr": 1.0}
        self.metrics_calls = []
        self.metrics_error = None
        self.plugin_run = None
        self.plugin_outcome = None
        self.plugin_error = None
        self.plugin_run_calls = []
        self.tool_ctx = {}


@pytest.fixture
def env(monkeypatch, rag):
    e = Env()
    import backend.agents.subagents.required as required
    import backend.mcp.chat_integration as chat_integration
    import backend.rag_query.metrics as metrics
    import backend.services.inline_context as inline_context
    import backend.services.plugins_direct as pdirect
    import backend.services.skills as skills
    import backend.tools.tool_context as tc

    def ask(final_message, **kw):
        e.ask_calls.append((final_message, kw))
        cb = kw.get("stream_callback")
        for chunk, acc, role in e.stream_script:
            if cb is not None:
                cb(chunk, acc, role)
        if e.after_ask:
            e.after_ask()
        if isinstance(e.answer, Exception):
            raise e.answer
        return e.answer

    monkeypatch.setattr(h, "ask_agent", ask)
    monkeypatch.setattr(h, "get_current_model_path", lambda: "global-model")

    async def save(*a, **kw):
        if e.save_error:
            raise e.save_error
        e.saved.append((a, kw))

    monkeypatch.setattr(h, "save_assistant_response", save)

    async def feedback(uid, conversation_id=None):
        return e.feedback

    async def get_cpm(uid):
        return e.cpm

    monkeypatch.setattr(h, "build_user_feedback_system_block", feedback)
    monkeypatch.setattr(h, "get_user_prompt_manager", get_cpm)
    monkeypatch.setattr(h, "context_prompt_manager", None)

    async def replace(ctx, answer, replacement):
        e.replace_calls.append((ctx, answer, replacement))
        return e.replace_with if e.replace_with is not None else answer

    monkeypatch.setattr(h, "maybe_replace_ungrounded", replace)

    async def apply_skills(**kw):
        if e.skills_error:
            raise e.skills_error
        if e.skills_result is not None:
            return e.skills_result
        return (kw["system_prompt"], "", [], [], {})

    monkeypatch.setattr(skills, "apply_skills_to_chat", apply_skills)

    async def run_required(**kw):
        if e.required_error:
            raise e.required_error
        for ev in e.required_events:
            await kw["emit_event"](dict(ev))
        if e.required_result is not None:
            return e.required_result
        return (kw["final_message"], kw["agent_profile"], False, None)

    monkeypatch.setattr(required, "run_required_for_chat", run_required)

    async def run_mcp(**kw):
        e.mcp_calls.append(kw)
        if e.mcp_error:
            raise e.mcp_error
        for ev in e.mcp_events:
            await kw["emit_event"](dict(ev))
        return e.mcp_result

    monkeypatch.setattr(chat_integration, "run_mcp_for_chat", run_mcp)

    async def compute(**kw):
        e.metrics_calls.append(kw)
        if e.metrics_error:
            raise e.metrics_error
        return e.metrics

    monkeypatch.setattr(metrics, "compute_online_rag_metrics", compute)

    def pick(plugin_ids, attachments, message, chat_mode="chat"):
        return e.plugin_run

    async def run_plugin(run, chat_mode="chat"):
        e.plugin_run_calls.append(run)
        if e.plugin_error:
            raise e.plugin_error
        return e.plugin_outcome

    monkeypatch.setattr(pdirect, "pick_plugin_run", pick)
    monkeypatch.setattr(pdirect, "run_plugin_direct", run_plugin)
    monkeypatch.setattr(inline_context, "prepare_inline_context",
                        lambda ctx, **kw: f"<{ctx}|{kw['plugin_ids']}|{kw['note_override'][:12]}>")
    monkeypatch.setattr(tc, "get_tool_context", lambda: dict(e.tool_ctx) if e.tool_ctx is not None else None)
    monkeypatch.setattr(tc, "set_tool_context", lambda ctx: e.tool_ctx.update(ctx))
    monkeypatch.setenv("RAG_DOCUMENTS_INVENTORY", "0")
    monkeypatch.setenv("RAG_ANSWER_SUMMARY", "0")
    return e


async def run(env, sio=None, cb=None, **over):
    sio = sio or CollectSio()
    cb = cb or Cb()
    h._begin_generation("s")
    args = dict(
        sio=sio, sid="s", data=over.pop("data", {"message_id": "m1"}), user_message=over.pop("user_message", "вопрос"),
        streaming=over.pop("streaming", False), conversation_id=over.pop("conversation_id", "c1"),
        history=over.pop("history", []), use_kb_rag=False, use_memory_library_rag=False,
        agent_profile=over.pop("agent_profile", profile()), sync_stream_cb=cb, loop=asyncio.get_running_loop(),
    )
    args.update(over)
    result = await h._handle_direct(**args)
    await asyncio.sleep(0)
    run.sio, run.cb = sio, cb
    return result


def ok_outcome(**over):
    base = dict(ok=True, elapsed_sec=1.0, verdict_markdown="вердикт", artifact_markdown=":::artifact\nA\n:::",
                result_status="ok", error="", bytes_sent=10, invoke_url="http://x")
    base.update(over)
    return types.SimpleNamespace(**base)


def plugin_run_obj():
    return types.SimpleNamespace(plugin_id="cash-flow", label="Аудит", file_name="m.xlsx", minio_object="o",
                                 minio_bucket="b", prompt="p")


class TestPlainAnswer:
    async def test_non_streaming_answer_is_emitted_and_saved(self, env):
        result = await run(env, current_user=USER, generation_started_at=None)
        sio = run.sio
        (final, kw), = env.ask_calls
        assert final == "вопрос" and kw["streaming"] is False and kw["stream_callback"] is None
        assert kw["model_path"] == "llm-svc://m" and kw["temperature"] == 0.5 and kw["max_tokens"] == 4096
        done = sio.payloads("chat_complete")[0]
        assert done["response"] == "ответ модели" and done["was_streaming"] is False
        assert done["generation_duration_sec"] == 1 and "document_search" not in done
        assert sio.emitted[-1][2] == "s"
        (sargs, skwargs), = env.saved
        assert sargs[0] == "ответ модели" and sargs[1] == {"generation_duration_sec": 1}
        assert skwargs == {"conversation_id": "c1", "user_id": "alice", "project_id": None, "regenerate": False,
                           "assistant_message_id": None, "alternative_responses": None,
                           "current_response_index": None}
        assert result == {"content": "ответ модели", "reasoning": "", "document_search": None}

    async def test_model_path_falls_back_to_global(self, env):
        await run(env, agent_profile=profile(model_path=None))
        assert env.ask_calls[0][1]["model_path"] == "global-model"

    @pytest.mark.parametrize("answer,expected", [(None, ""), (123, "123")])
    async def test_non_string_answer(self, env, answer, expected):
        env.answer = answer
        result = await run(env)
        assert result["content"] == expected

    async def test_emit_complete_and_save_can_be_disabled(self, env):
        result = await run(env, emit_complete=False, save_response=False, complete_response="видимое",
                           stream_prefix="[шаг] ")
        assert run.sio.payloads("chat_complete") == [] and env.saved == []
        assert result["content"] == "ответ модели"

    async def test_complete_response_and_prefix(self, env):
        await run(env, stream_prefix="[1] ")
        assert run.sio.payloads("chat_complete")[0]["response"] == "[1] ответ модели"
        await run(env, complete_response="свой текст")
        assert run.sio.payloads("chat_complete")[0]["response"] == "свой текст"
        assert env.saved[-1][0][0] == "свой текст"

    async def test_chain_steps_in_payload_and_meta(self, env):
        await run(env, extra_save_meta={"chain_steps": [1, 2], "hide_sequential_outputs": True})
        done = run.sio.payloads("chat_complete")[0]
        assert done["chain_steps"] == [1, 2] and done["hide_sequential_outputs"] is True
        meta = env.saved[0][0][1]
        assert meta["chain_steps"] == [1, 2] and "generation_duration_sec" in meta

    async def test_regenerate_fields_forwarded(self, env):
        await run(env, data={"regenerate": True, "assistant_message_id": "a1", "alternative_responses": ["x"],
                             "current_response_index": 0})
        kw = env.saved[0][1]
        assert kw["regenerate"] is True and kw["assistant_message_id"] == "a1" and kw["current_response_index"] == 0

    @pytest.mark.parametrize("error", [RuntimeError("mongo"), ValueError("x")])
    async def test_save_error_does_not_break_response(self, env, error):
        env.save_error = error
        result = await run(env)
        assert run.sio.payloads("chat_complete") and result["content"] == "ответ модели"

    async def test_images_history_and_tokens_are_passed(self, env):
        await run(env, inline_images=["i1"], history=[{"role": "user", "content": "раньше"}], enable_thinking=True)
        final, kw = env.ask_calls[0]
        assert kw["images"] == ["i1"] and kw["history"] == [{"role": "user", "content": "раньше"}]
        assert kw["enable_thinking"] is True and kw["custom_prompt_id"] is None

    async def test_user_message_is_appended_when_rag_query_differs(self, env):
        await run(env, user_message="исходный вопрос", rag_query="переписанный запрос")
        assert env.ask_calls[0][0] == "исходный вопрос"  # RAG-контекста нет -> user_message уже в сообщении

    async def test_user_message_added_after_context(self, env, rag):
        rag.memory_hits = [("факт", 0.9, 1, 0)]
        await run(env, use_memory_library_rag=True, user_message="исходный", rag_query="переписанный")
        final = env.ask_calls[0][0]
        assert final.endswith("\n\nисходный") and "факт" in final

    async def test_ungrounded_answer_is_replaced(self, env, rag):
        rag.memory_hits = [("факт", 0.9, 1, 0)]
        env.replace_with = "заменено"
        result = await run(env, use_memory_library_rag=True)
        assert result["content"] == "заменено" and env.replace_calls[0][2] == RAG_STRICT_NOT_FOUND_MESSAGE


class TestStreaming:
    async def test_stream_callback_relays_content_with_prefix(self, env):
        env.stream_script = [("а", "а", "content"), ("", "", "heartbeat"), ("б", "аб", "content")]
        await run(env, streaming=True, stream_prefix="[1] ")
        assert run.cb.calls == [("а", "[1] а", "content"), ("б", "[1] аб", "content")]
        assert run.cb.flushed == 1
        assert run.sio.payloads("chat_complete")[0]["was_streaming"] is True

    async def test_reasoning_is_accumulated_and_saved(self, env):
        env.stream_script = [("п", "п", "reasoning"), ("", "пр", "reasoning"), ("ив", "", "reasoning"),
                             ("о", "о", "content")]
        result = await run(env, streaming=True)
        assert ("п", "п", "reasoning") in run.cb.calls
        assert result["reasoning"] == "пр" + "ив"
        assert env.saved[0][0][1]["reasoning_content"] == "прив"

    async def test_suppressed_ui_stream_does_not_call_callback(self, env):
        env.stream_script = [("а", "а", "content"), ("р", "р", "reasoning")]
        result = await run(env, streaming=True, suppress_ui_stream=True)
        assert run.cb.calls == [] and result["reasoning"] == "р"

    async def test_heartbeat_reports_stop(self, env):
        results = []

        def ask(final_message, **kw):
            h._stopped_upto["s"] = h._generation_seq["s"]
            results.append(kw["stream_callback"]("", "", "heartbeat"))
            return None

        h.ask_agent = ask
        env.answer = None
        await run(env, streaming=True)
        assert results == [False]

    async def test_stopped_stream_saves_partial_and_reports(self, env):
        env.stream_script = [("ча", "ча", "reasoning")]
        env.after_ask = lambda: h._stopped_upto.__setitem__("s", h._generation_seq["s"])
        env.answer = "частичный ответ"
        result = await run(env, streaming=True, stream_prefix="[1] ", generation_started_at=None, current_user=USER,
                           project_id="p1")
        assert result is None
        assert run.sio.names() == ["generation_stopped"] and run.sio.payloads("generation_stopped")[0]["message"] == "Генерация остановлена"
        (sargs, skwargs), = env.saved
        assert sargs[0] == "[1] частичный ответ"
        assert sargs[1] == {"stopped": True, "reasoning_content": "ча", "generation_duration_sec": 1}
        assert skwargs["project_id"] == "p1" and skwargs["user_id"] == "alice"

    async def test_stopped_stream_without_partial_saves_nothing(self, env):
        env.answer = None
        result = await run(env, streaming=True)
        assert result is None and env.saved == [] and run.sio.names() == ["generation_stopped"]

    async def test_stopped_stream_partial_not_saved_when_disabled(self, env):
        env.after_ask = lambda: h._stopped_upto.__setitem__("s", h._generation_seq["s"])
        await run(env, streaming=True, save_response=False)
        assert env.saved == []

    async def test_partial_save_error_is_swallowed(self, env):
        env.after_ask = lambda: h._stopped_upto.__setitem__("s", h._generation_seq["s"])
        env.save_error = RuntimeError("mongo")
        result = await run(env, streaming=True)
        assert result is None and run.sio.names() == ["generation_stopped"]

    async def test_stop_after_non_streaming_answer_should_keep_response(self, env):
        env.after_ask = lambda: h._stopped_upto.__setitem__("s", h._generation_seq["s"])
        result = await run(env, streaming=False)
        assert result is None and run.sio.names() == ["generation_stopped"]
        assert any(a[0] == "ответ модели" and k.get("conversation_id") for a, k in env.saved)
        assert env.saved[-1][0][1].get("stopped") is True

    async def test_stop_after_non_streaming_answer_save_failure_is_logged(self, env):
        env.after_ask = lambda: h._stopped_upto.__setitem__("s", h._generation_seq["s"])
        env.save_error = RuntimeError("mongo")
        result = await run(env, streaming=False)
        assert result is None and run.sio.names() == ["generation_stopped"]


class TestModelFailures:
    async def test_model_error_propagates_and_nothing_is_saved_known_issue(self, env):
        env.answer = RuntimeError("модель недоступна")
        with pytest.raises(RuntimeError, match="модель недоступна"):
            await run(env, streaming=False)
        assert env.saved == [] and run.sio.payloads("chat_complete") == [] if hasattr(run, "sio") else True

    async def test_stream_failure_keeps_partial_and_still_raises(self, env):
        env.stream_script = [("часть", "часть ответа", "content")]
        env.answer = ConnectionError("обрыв стрима")
        sio = CollectSio()
        with pytest.raises(ConnectionError):
            await run(env, sio=sio, streaming=True)
        assert sio.payloads("chat_complete") == []
        assert any("часть ответа" in str(a[0]) for a, _ in env.saved)

    async def test_stream_failure_without_text_saves_nothing(self, env):
        env.answer = ConnectionError("обрыв стрима")
        with pytest.raises(ConnectionError):
            await run(env, streaming=True)
        assert env.saved == []

    async def test_stream_failure_partial_with_reasoning_and_prefix(self, env):
        env.stream_script = [("д", "думаю", "reasoning"), ("часть", "часть ответа", "content")]
        env.answer = ConnectionError("обрыв стрима")
        with pytest.raises(ConnectionError):
            await run(env, streaming=True, stream_prefix="[префикс] ")
        (args, _), = env.saved
        assert args[0] == "[префикс] часть ответа"
        assert args[1]["failed"] is True and args[1]["reasoning_content"] == "думаю"

    async def test_stream_failure_partial_save_error_is_swallowed(self, env):
        env.stream_script = [("часть", "часть ответа", "content")]
        env.answer = ConnectionError("обрыв стрима")
        env.save_error = RuntimeError("db")
        with pytest.raises(ConnectionError):
            await run(env, streaming=True)


class TestReindexGuard:
    async def test_proactive_guard_returns_wait_message(self, env, rag):
        rag.status = {"memory": {"reindexing": True}}
        result = await run(env, use_memory_library_rag=True, current_user=USER)
        assert result is None and env.ask_calls == []
        assert run.sio.names() == ["chat_info", "chat_complete"]
        assert run.sio.payloads("chat_complete")[0]["response"] == h.REINDEX_WAIT_MESSAGE
        assert env.saved[0][0][1] == {"reindex_wait": True}

    async def test_project_search_reindex_error(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        rag.errors["project_rag_search"] = RagReindexInProgress("reindex")
        result = await run(env, project_id="p1")
        assert result is None and run.sio.names() == ["chat_info", "chat_complete"] and env.ask_calls == []

    async def test_agent_kb_reindex_error(self, env, rag, monkeypatch):
        async def boom(*a, **kw):
            raise RagReindexInProgress("reindex")

        # Реальный kb_search_agent_documents глотает любые исключения, поэтому ветка достижима
        # только при подмене помощника (см. тест *_swallowed_by_helper_known_issue).
        monkeypatch.setattr(h, "kb_search_agent_documents", boom)
        result = await run(env, use_agent_scoped_kb=True, agent_kb_doc_ids=[1])
        assert result is None and run.sio.names() == ["chat_info", "chat_complete"]

    async def test_agent_kb_reindex_is_propagated_by_helper(self, env, rag):
        rag.kb_docs = [{"id": 1, "filename": "k.txt"}]
        rag.errors["kb_search"] = RagReindexInProgress("reindex")
        result = await run(env, use_agent_scoped_kb=True, agent_kb_doc_ids=[1])
        # Вместо «не нашёл» пользователь получает просьбу подождать.
        assert result is None and run.sio.names() == ["chat_info", "chat_complete"]

    async def test_memory_reindex_error(self, env, rag):
        rag.errors["memory_rag_search"] = RagReindexInProgress("reindex")
        result = await run(env, use_memory_library_rag=True)
        assert result is None and run.sio.names() == ["chat_info", "chat_complete"]


class TestProjectRag:
    async def test_context_trace_and_named_document(self, env, rag):
        rag.project_docs = [{"id": 5, "filename": "Смета.xlsx"}, {"id": 6, "filename": "other.txt"}]
        rag.project_hits = [("общий", 0.9, 6, 0), ("без документа", 0.5, None, 1)]
        rag.by_document[("project", 5)] = [("из сметы", 0.8, 5, 1)]
        result = await run(env, project_id="p1", rag_strategy="vector", user_message="что в Смета.xlsx?")
        final = env.ask_calls[0][0]
        assert final.startswith("Документы проекта (RAG):") and "из сметы" in final
        assert "Ответь на основе этих документов" in final
        trace = result["document_search"]
        assert trace["strategy"] == "vector" and trace["query"] == "что в Смета.xlsx?"
        assert trace["sourceFiles"] == ["other.txt", "Смета.xlsx"]
        assert {x["file"] for x in trace["hits"]} == {"other.txt", "Смета.xlsx"}
        assert all(x["store"] == "project" for x in trace["hits"])
        assert run.sio.payloads("chat_complete")[0]["document_search"] == trace
        assert env.saved[0][0][1]["document_search"] == trace
        assert env.saved[0][0][1]["rag_metrics"] == env.metrics
        assert run.sio.payloads("chat_rag_metrics")[0]["metrics"] == env.metrics
        assert env.metrics_calls[0]["hits"] == trace["hits"]

    async def test_search_query_differs_from_prompt_query(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        rag.project_hits = [("факт", 0.9, 1, 0)]
        await run(env, project_id="p1", rag_query="вопрос для промпта", search_query=" запрос поиска ")
        assert rag.called("project_rag_search")[0][1] == ("запрос поиска",)
        assert "Вопрос: вопрос для промпта" in env.ask_calls[0][0]

    async def test_structure_query_pulls_document_start(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        rag.project_hits = [("середина", 0.9, 1, 7)]
        rag.start_chunks = [("начало", 0.0, 1, 0), ("середина", 0.9, 1, 7)]
        await run(env, project_id="p1", user_message="покажи оглавление")
        assert rag.called("project_rag_get_document_start_chunks")[0][1] == ("p1", 1)
        final = env.ask_calls[0][0]
        assert final.index("начало") < final.index("середина") and final.count("середина") == 1

    async def test_structure_query_start_chunks_error_is_swallowed(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        rag.project_hits = [("середина", 0.9, 1, 7)]
        rag.errors["project_rag_get_document_start_chunks"] = RuntimeError("x")
        await run(env, project_id="p1", user_message="глава 2")
        assert "середина" in env.ask_calls[0][0]

    async def test_list_failure_falls_back_to_second_listing_for_trace(self, env, rag, monkeypatch):
        calls = {"n": 0}

        async def flaky(project_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("list down")
            return [{"id": 1, "filename": "a.txt"}]

        monkeypatch.setattr(rag, "project_rag_list_documents", flaky)
        rag.project_hits = [("факт", 0.9, 1, 0), ("битый", 0.5, "x", 1), ("чужой", 0.4, 77, 2)]
        result = await run(env, project_id="p1")
        files = [x["file"] for x in result["document_search"]["hits"]]
        assert files[0] == "a.txt" and "doc*x" in files and "doc*77" in files

    async def test_both_listings_failing_use_doc_placeholders(self, env, rag):
        rag.errors["project_rag_list_documents"] = RuntimeError("list down")
        rag.project_hits = [("факт", 0.9, 1, 0)]
        result = await run(env, project_id="p1")
        assert result["document_search"]["hits"][0]["file"] == "doc*1"

    async def test_search_error_is_swallowed(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        rag.errors["project_rag_search"] = RuntimeError("down")
        result = await run(env, project_id="p1", history=[{"role": "user", "content": "раньше"}])
        assert env.ask_calls[0][0] == "вопрос" and result["document_search"] is None

    async def test_empty_hits_with_corpus_gives_canned_answer(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        result = await run(env, project_id="p1", streaming=True)
        assert env.ask_calls == [] and result["content"] == RAG_STRICT_NOT_FOUND_MESSAGE
        chunk = run.sio.payloads("chat_chunk")[0]
        assert chunk == {"chunk": RAG_STRICT_NOT_FOUND_MESSAGE, "accumulated": RAG_STRICT_NOT_FOUND_MESSAGE}

    async def test_canned_with_prefix_and_suppressed_stream(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        await run(env, project_id="p1", streaming=True, stream_prefix="[1] ")
        assert run.sio.payloads("chat_chunk")[0]["accumulated"] == "[1] " + RAG_STRICT_NOT_FOUND_MESSAGE
        await run(env, project_id="p1", streaming=True, suppress_ui_stream=True)
        assert run.sio.payloads("chat_chunk") == []

    async def test_history_makes_model_answer_instead_of_canned(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        await run(env, project_id="p1", history=[{"role": "user", "content": "раньше"}])
        assert len(env.ask_calls) == 1

    async def test_inventory_adds_context_without_hits(self, env, rag, monkeypatch):
        monkeypatch.setenv("RAG_DOCUMENTS_INVENTORY", "1")
        rag.project_docs = [{"id": 1, "filename": "список.txt"}]
        await run(env, project_id="p1")
        final, kw = env.ask_calls[0]
        assert final.startswith("ДОСТУПНЫЕ ФАЙЛЫ") and RAG_STRICT_NOT_FOUND_MESSAGE in kw["system_prompt"]


class TestKbAndMemory:
    async def test_kb_and_memory_context_trace(self, env, rag):
        rag.kb_docs = [{"id": 1, "filename": "Регламент.pdf"}, {"id": 2, "filename": "чужой.pdf"}]
        rag.kb_hits = [("текст регламента", 0.7, 1, 0), ("без id", 0.1, None, 1)]
        rag.by_document[("kb", 1)] = [("дотянутый", 0.6, 1, 3)]
        rag.memory_docs = [{"id": 9, "filename": "Инструкция.docx"}]
        rag.memory_hits = [("про настройку", 0.9, 9, 0), ("битый", 0.5, "x", 1)]
        rag.by_document[("memory", 9)] = [("из инструкции", 0.4, 9, 2)]
        result = await run(env, use_agent_scoped_kb=True, agent_kb_doc_ids=[1], use_memory_library_rag=True,
                           user_message="Регламент.pdf и Инструкция.docx")
        final = env.ask_calls[0][0]
        assert "База Знаний (постоянные документы):" in final and "дотянутый" in final
        assert "Документы из настроек (библиотека памяти):" in final and "из инструкции" in final
        trace = result["document_search"]
        stores = {x["store"] for x in trace["hits"]}
        assert stores == {"kb", "memory"}
        assert "Регламент.pdf" in trace["sourceFiles"] and "doc*x" in trace["sourceFiles"]
        assert "чужой" not in final

    async def test_project_and_kb_traces_are_merged(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "p.txt"}]
        rag.project_hits = [("проект", 0.9, 1, 0)]
        rag.kb_docs = [{"id": 2, "filename": "k.txt"}]
        rag.kb_hits = [("база", 0.9, 2, 0)]
        result = await run(env, project_id="p1", use_agent_scoped_kb=True, agent_kb_doc_ids=[2])
        assert [x["store"] for x in result["document_search"]["hits"]] == ["project", "kb"]
        assert result["document_search"]["sourceFiles"] == ["k.txt", "p.txt"]

    async def test_list_and_search_errors_are_swallowed(self, env, rag):
        rag.errors["kb_list_documents"] = RuntimeError("x")
        rag.errors["memory_rag_list_documents"] = RuntimeError("x")
        rag.errors["kb_search"] = RuntimeError("x")
        rag.errors["memory_rag_search"] = RuntimeError("x")
        result = await run(env, use_agent_scoped_kb=True, agent_kb_doc_ids=[1], use_memory_library_rag=True,
                           history=[{"role": "user", "content": "раньше"}])
        assert len(env.ask_calls) == 1 and result["document_search"]["hits"] == []

    async def test_memory_error_without_kb(self, env, rag):
        rag.errors["memory_rag_search"] = RuntimeError("x")
        rag.memory_docs = [{"id": 1, "filename": "a.txt"}]
        result = await run(env, use_memory_library_rag=True)
        assert result["content"] == RAG_SEARCH_FAILED_MESSAGE

    async def test_project_search_error_without_history_reports_failure(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        rag.errors["project_rag_search"] = RuntimeError("down")
        result = await run(env, project_id="p1")
        assert env.ask_calls == [] and result["content"] == RAG_SEARCH_FAILED_MESSAGE


class TestPluginsDirect:
    async def test_no_plugins_no_note(self, env):
        await run(env, agent_profile=profile(plugins_enabled=False, plugin_ids=["cash-flow"]))
        assert env.plugin_run_calls == []
        assert "автоматически" not in (env.ask_calls[0][1]["system_prompt"] or "")

    async def test_plugin_without_matching_attachment_adds_note(self, env):
        env.plugin_run = None
        await run(env, agent_profile=profile(plugin_ids=["cash-flow"], plugins_enabled=True))
        sp = env.ask_calls[0][1]["system_prompt"]
        assert "В этом режиме вызов инструментов недоступен" in sp

    async def test_plugin_success_adds_verdict_and_artifact(self, env):
        env.plugin_run = plugin_run_obj()
        env.plugin_outcome = ok_outcome()
        data = {"inline_attachments": [{"name": "m.xlsx"}]}
        result = await run(env, data=data, inline_context="дамп ячеек",
                           agent_profile=profile(plugin_ids=["cash-flow"], plugins_enabled=True))
        final, kw = env.ask_calls[0]
        assert "вердикт" in final and "<дамп ячеек|['cash-flow']|" in final
        assert "уже запущен системой" in kw["system_prompt"]
        assert result["content"].endswith(":::artifact\nA\n:::")
        status = run.sio.payloads("chat_thinking")[0]
        assert status["status"] == "processing" and "Аудит" in status["message"]
        assert env.tool_ctx["inline_attachments"] == data["inline_attachments"]

    async def test_plugin_failure_is_reported_to_ui(self, env):
        env.plugin_run = plugin_run_obj()
        env.plugin_outcome = ok_outcome(ok=False, error="таймаут", artifact_markdown="")
        result = await run(env, agent_profile=profile(plugin_ids=["cash-flow"], plugins_enabled=True))
        info = run.sio.payloads("chat_info")[0]["message"]
        assert "не смог обработать файл" in info and "таймаут" in info
        assert ":::artifact" not in result["content"]
        assert "не смог обработать файл «m.xlsx»" in env.ask_calls[0][0]

    async def test_plugin_crash_is_swallowed(self, env):
        env.plugin_run = plugin_run_obj()
        env.plugin_error = RuntimeError("svc down")
        result = await run(env, agent_profile=profile(plugin_ids=["cash-flow"], plugins_enabled=True))
        assert result["content"] == "ответ модели"

    async def test_plugin_selection_failure_is_swallowed(self, env, monkeypatch):
        import backend.plugins.orchestrator_bridge as bridge

        def boom(profile):
            raise RuntimeError("x")

        monkeypatch.setattr(bridge, "resolve_agent_plugin_ids", boom)
        result = await run(env)
        assert result["content"] == "ответ модели"

    async def test_plugin_prompt_injection_failure_is_swallowed(self, env, monkeypatch):
        import backend.services.plugins_chat as pchat

        def boom(**kw):
            raise RuntimeError("x")

        monkeypatch.setattr(pchat, "apply_plugins_to_context", boom)
        result = await run(env)
        assert result["content"] == "ответ модели"

    async def test_plugins_prompt_block_is_appended(self, env):
        await run(env, agent_profile=profile(system_prompt="База", plugin_ids=["cash-flow"], plugins_enabled=True))
        sp = env.ask_calls[0][1]["system_prompt"]
        assert sp.startswith("База") and "audit_cash_flow_model" in sp
        assert env.tool_ctx["__plugin_ids__"] == ["cash-flow"]


class TestSystemPrompt:
    async def test_prompt_manager_gets_agent_and_project(self, env):
        seen = []

        class Cpm:
            def resolve_chat_system_prompt(self, model, agent_system_prompt="", project_instructions=""):
                seen.append((model, agent_system_prompt, project_instructions))
                return "из менеджера"

        env.cpm = Cpm()
        env.feedback = "ФИДБЕК"
        await run(env, agent_profile=profile(system_prompt="агент"), project_instructions=" инструкции ",
                  project_id="p1")
        assert seen == [("llm-svc://m", "агент", " инструкции ")]
        sp = env.ask_calls[0][1]["system_prompt"]
        assert "из менеджера" in sp and "ФИДБЕК" in sp

    @pytest.mark.parametrize("instr,agent,expected", [
        (" инструкции ", "агент", "инструкции\n\nагент"), (" инструкции ", "", "инструкции"),
        ("", "агент", "агент"), ("  ", "", None),
    ])
    async def test_without_prompt_manager(self, env, instr, agent, expected):
        await run(env, agent_profile=profile(system_prompt=agent), project_instructions=instr)
        assert env.ask_calls[0][1]["system_prompt"] == expected

    async def test_rag_rules_are_added_when_context_present(self, env, rag):
        rag.memory_hits = [("факт", 0.9, 1, 0)]
        await run(env, use_memory_library_rag=True, agent_profile=profile(system_prompt="Я юрист"))
        sp = env.ask_calls[0][1]["system_prompt"]
        assert sp.startswith("Я юрист") and RAG_STRICT_NOT_FOUND_MESSAGE in sp

    async def test_artifacts_block_and_skill_prompt(self, env):
        env.skills_result = ("База\n\n[SKILL] навык", "", [], [], {})
        await run(env, agent_profile=profile(artifacts_enabled=True))
        sp = env.ask_calls[0][1]["system_prompt"]
        assert sp.startswith("База\n\n[SKILL] навык") and ":::artifact" in sp

    async def test_artifacts_injection_failure_is_swallowed(self, env, monkeypatch):
        import backend.prompts.artifacts as art

        def boom(p):
            raise RuntimeError("x")

        monkeypatch.setattr(art, "maybe_artifacts_prompt_for_agent", boom)
        assert (await run(env))["content"] == "ответ модели"


class TestSkills:
    async def test_lazy_skills_allowed_tools_and_presentation(self, env):
        env.skills_result = ("", "", ["skill-1"], ["tool-x", "tool-a"], {"manual": ["my-pptx"]})
        env.mcp_result = types.SimpleNamespace(content="итог", mode="m", iterations=1, tool_calls_executed=0,
                                               attachments=None)
        data = {"tool_ids": ["tool-a"], "message_id": "m1"}
        await run(env, data=data, current_user=USER)
        assert env.tool_ctx["__skill_ids__"] == ["skill-1"] and env.tool_ctx["current_user"] == USER
        assert data["tool_ids"] == ["tool-a", "tool-x"]
        assert env.mcp_calls[0]["max_tokens"] >= h._PRESENTATION_MAX_TOKENS_FLOOR

    async def test_presentation_raises_plain_call_token_budget(self, env):
        env.skills_result = ("", "", [], [], {"always_apply": ["gpb-html"]})
        await run(env)
        assert env.ask_calls[0][1]["max_tokens"] >= h._PRESENTATION_MAX_TOKENS_FLOOR

    async def test_allowed_tools_without_lazy(self, env):
        env.skills_result = ("", "", [], ["tool-x"], {})
        data = {"mcp_tool_ids": ["t0"]}
        await run(env, data=data)
        assert data["tool_ids"] == ["t0", "tool-x"] and "__skill_ids__" not in env.tool_ctx

    async def test_tag_mention_failure_is_ignored(self, env, monkeypatch):
        import backend.services.tag_mentions as tm

        def boom(text):
            raise RuntimeError("x")

        monkeypatch.setattr(tm, "strip_tag_mentions", boom)
        assert (await run(env))["content"] == "ответ модели"

    async def test_skills_failure_is_swallowed(self, env):
        env.skills_error = RuntimeError("db down")
        assert (await run(env))["content"] == "ответ модели"


class TestRequiredSubagents:
    async def test_message_and_profile_come_from_required_run(self, env):
        new_profile = profile(subagents=None, name="Без субагентов")
        env.required_result = ("сообщение с ответами субагентов", new_profile, True, None)
        await run(env, current_user=USER)
        assert env.ask_calls[0][0] == "сообщение с ответами субагентов"

    async def test_events_go_to_ui_and_complete_payload(self, env):
        env.required_events = [{"type": "mcp_tool_end", "tool": "subagent", "result": "x"}]
        result = await run(env, current_user=USER)
        assert run.sio.payloads("chat_mcp_event")[0]["tool"] == "subagent"
        assert run.sio.payloads("chat_complete")[0]["mcp_tool_calls"] == env.required_events
        assert env.saved[0][0][1]["mcp_tool_calls"] == env.required_events
        assert result["content"].startswith("ответ модели")

    async def test_events_suppressed_for_chain_steps(self, env):
        env.required_events = [{"type": "mcp_tool_start"}]
        await run(env, current_user=USER, suppress_ui_stream=True)
        assert run.sio.payloads("chat_mcp_event") == []
        assert run.sio.payloads("chat_complete")[0]["mcp_tool_calls"] == env.required_events

    async def test_mention_direct_answer_skips_llm(self, env):
        env.required_result = ("сообщение", profile(), True, "Ответ вызванных агентов")
        result = await run(env, current_user=USER, streaming=True)
        assert env.ask_calls == [] and result["content"] == "Ответ вызванных агентов"
        assert run.sio.payloads("chat_chunk")[0]["chunk"] == "Ответ вызванных агентов"

    async def test_required_failure_is_swallowed(self, env):
        env.required_error = RuntimeError("x")
        assert (await run(env, current_user=USER))["content"] == "ответ модели"

    async def test_not_called_without_user(self, env):
        env.required_error = RuntimeError("не должно вызываться")
        assert (await run(env, current_user=None))["content"] == "ответ модели"

    async def test_stopped_callback_follows_generation(self, env, monkeypatch):
        import backend.agents.subagents.required as required

        seen = []

        async def run_required(**kw):
            seen.append(kw["stopped"]())
            h._stopped_upto["s"] = h._generation_seq["s"]
            seen.append(kw["stopped"]())
            return (kw["final_message"], kw["agent_profile"], False, None)

        monkeypatch.setattr(required, "run_required_for_chat", run_required)
        await run(env, current_user=USER)
        assert seen == [False, True]


class TestMcp:
    async def test_mcp_result_replaces_llm_call(self, env):
        env.mcp_result = types.SimpleNamespace(content="итог mcp", mode="tools", iterations=2, tool_calls_executed=3,
                                               attachments=[{"name": "f"}])
        env.mcp_events = [{"type": "mcp_tool_end", "tool": "search"}]
        result = await run(env, data={"tool_ids": ["t1"], "message_id": "m1", "inline_attachments": [{"n": 1}]},
                           current_user=USER, streaming=True, stream_prefix="[1] ", user_message="вопрос",
                           history=[{"role": "user", "content": "x"}],
                           agent_profile=profile(temperature=None))
        assert env.ask_calls == []
        kw = env.mcp_calls[0]
        assert kw["tool_ids"] == ["t1"] and kw["temperature"] == 0.7 and kw["user_question"] == "вопрос"
        assert kw["inline_attachments"] == [{"n": 1}] and kw["message_id"] == "m1" and kw["user"] == USER
        assert run.sio.payloads("chat_chunk")[0] == {"chunk": "итог mcp", "accumulated": "[1] итог mcp"}
        meta = env.saved[0][0][1]
        assert meta["mcp_attachments"] == [{"name": "f"}] and meta["mcp_tool_calls"] == env.mcp_events
        assert result["content"] == "итог mcp"

    async def test_mcp_without_streaming_or_content(self, env):
        env.mcp_result = types.SimpleNamespace(content="", mode="tools", iterations=1, tool_calls_executed=0,
                                               attachments=None)
        await run(env, data={"tool_ids": ["t1"]}, current_user=USER, streaming=True)
        assert run.sio.payloads("chat_chunk") == []

    async def test_mcp_suppressed_ui_stream(self, env):
        env.mcp_result = types.SimpleNamespace(content="итог", mode="tools", iterations=1, tool_calls_executed=0,
                                               attachments=None)
        env.mcp_events = [{"type": "mcp_tool_start"}]
        await run(env, data={"tool_ids": ["t1"]}, current_user=USER, streaming=True, suppress_ui_stream=True)
        assert run.sio.payloads("chat_chunk") == [] and run.sio.payloads("chat_mcp_event") == []

    async def test_subagents_enable_mcp(self, env):
        env.mcp_result = types.SimpleNamespace(content="от субагента", mode="sub", iterations=1,
                                               tool_calls_executed=1, attachments=None)
        result = await run(env, current_user=USER, agent_profile=profile(subagents={"enabled": True}))
        assert len(env.mcp_calls) == 1 and result["content"] == "от субагента"

    async def test_mcp_none_falls_back_to_llm(self, env):
        env.mcp_result = None
        result = await run(env, data={"tool_ids": ["t1"]}, current_user=USER)
        assert len(env.mcp_calls) == 1 and result["content"] == "ответ модели"

    async def test_mcp_error_falls_back_to_llm(self, env):
        env.mcp_error = RuntimeError("mcp down")
        result = await run(env, data={"tool_ids": ["t1"]}, current_user=USER)
        assert result["content"] == "ответ модели"

    async def test_no_mcp_without_user(self, env):
        await run(env, data={"tool_ids": ["t1"]}, current_user=None)
        assert env.mcp_calls == []

    async def test_subagent_artifacts_are_carried_into_answer(self, env, monkeypatch):
        env.mcp_events = [{"type": "mcp_tool_end", "tool": "subagent", "result": "r"}]
        env.mcp_result = None
        monkeypatch.setattr(h, "carry_subagent_artifacts", lambda resp, events: resp + "\n[+артефакты]")
        result = await run(env, data={"tool_ids": ["t1"]}, current_user=USER)
        assert result["content"] == "ответ модели\n[+артефакты]"


class TestDirectBranchGaps:
    async def test_kb_search_generic_error_is_swallowed(self, env, rag, monkeypatch):
        async def boom(*a, **kw):
            raise ValueError("kb сломался")

        monkeypatch.setattr(h, "kb_search_agent_documents", boom)
        rag.kb_docs = [{"id": 1, "filename": "k.txt"}]
        result = await run(env, use_agent_scoped_kb=True, agent_kb_doc_ids=[1], history=[{"role": "user", "content": "x"}])
        assert result["document_search"]["hits"] == [] and len(env.ask_calls) == 1

    async def test_hits_without_document_id_are_not_traced(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "p.txt"}]
        rag.project_hits = [("проектный без id", 0.9, None, 0)]
        rag.kb_hits = [("kb без id", 0.9, None, 0)]
        rag.memory_hits = [("память без id", 0.9, None, 0)]
        result = await run(env, project_id="p1", use_agent_scoped_kb=True, agent_kb_doc_ids=[1],
                           use_memory_library_rag=True)
        assert result["document_search"]["hits"] == []
        assert "kb без id" in env.ask_calls[0][0] and "проектный без id" in env.ask_calls[0][0]

    async def test_inline_context_is_put_before_rag_context(self, env, rag):
        rag.memory_hits = [("факт", 0.9, 1, 0)]
        await run(env, use_memory_library_rag=True, inline_context="файл пользователя")
        final = env.ask_calls[0][0]
        assert final.startswith("[Прикреплённый документ]\nфайл пользователя\n\nДокументы из настроек")

    async def test_inline_context_alone(self, env):
        await run(env, inline_context="файл")
        assert env.ask_calls[0][0] == "[Прикреплённый документ]\nфайл\n\n[Вопрос пользователя]\nвопрос"

    async def test_original_question_is_appended_when_prompt_uses_rewritten_query(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "p.txt"}]
        rag.project_hits = [("факт", 0.9, 1, 0)]
        await run(env, project_id="p1", user_message="исходный вопрос", rag_query="переписанный запрос")
        final = env.ask_calls[0][0]
        assert "Вопрос: переписанный запрос" in final and final.endswith("\n\nисходный вопрос")

    async def test_prompt_manager_without_project_instructions(self, env):
        class Cpm:
            def resolve_chat_system_prompt(self, model, agent_system_prompt="", project_instructions=""):
                return f"cpm:{agent_system_prompt}"

        env.cpm = Cpm()
        await run(env, agent_profile=profile(system_prompt="агент"))
        assert env.ask_calls[0][1]["system_prompt"] == "cpm:агент"

    async def test_empty_reasoning_chunks_do_not_change_trace(self, env):
        env.stream_script = [("", "", "reasoning"), (None, None, "reasoning")]
        result = await run(env, streaming=True)
        assert result["reasoning"] == ""
