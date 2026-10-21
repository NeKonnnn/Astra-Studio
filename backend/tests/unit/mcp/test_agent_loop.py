"""mcp/agent_loop: цикл вызова инструментов — без инструментов, native function calling, fallback, события для UI."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.llm_providers.base import ChatResult, ToolCall
from backend.mcp import agent_loop as al
from backend.mcp.types import AgentLoopResult, McpCallContext, McpToolInfo

pytestmark = pytest.mark.unit

CTX = McpCallContext(user_id="u1", username="ivan", chat_id="chat-1")
TOOL = McpToolInfo("math", "add", "mcp_math_add", "adds", {"type": "object", "properties": {}})
MESSAGES = [{"role": "user", "content": "2+2?"}]


class FakeProvider:
    id = "prov"

    def __init__(self, completions=(), chat_text="финальный ответ", error=None, models=()):
        self.completions, self.chat_text, self.error = list(completions), chat_text, error
        self.models = models
        self.completion_calls, self.chat_calls = [], []

    async def chat_completion(self, messages, model_id, **kw):
        self.completion_calls.append((list(messages), model_id, kw))
        if self.error:
            raise self.error
        return self.completions.pop(0)

    async def chat(self, messages, model_id, **kw):
        self.chat_calls.append((list(messages), model_id, kw))
        return self.chat_text

    async def list_models(self):
        return list(self.models)


class FakePlatform:
    def __init__(self, raw=None, error=None):
        self.raw = raw if raw is not None else {"content": [{"type": "text", "text": "result: 4"}]}
        self.error, self.calls, self.sessions_requested = error, [], []

    @asynccontextmanager
    async def request_sessions(self, ids, context):
        self.sessions_requested.append(list(ids))
        yield {"math": "SESSION"}

    async def call_tool(self, server_id, name, args, context, session):
        self.calls.append((server_id, name, args, session))
        if self.error:
            raise self.error
        return self.raw


@pytest.fixture
def env(monkeypatch):
    state = SimpleNamespace(provider=FakeProvider(), mode="native_openai_tools", fallbacks=[], model_id="m1")

    class Registry:
        def resolve(self, path):
            return state.provider, state.model_id

    async def get_registry():
        return Registry()

    async def fake_prompt_fc(**kw):
        state.fallbacks.append(kw)
        return AgentLoopResult(content="fc-answer", tool_calls_executed=1, mode="prompt_json_fc", new_messages=[{"role": "tool", "content": "x"}])

    monkeypatch.setattr(al, "get_registry", get_registry)
    monkeypatch.setattr(al, "resolve_tool_calling_mode", lambda provider: state.mode)
    monkeypatch.setattr(al, "get_settings", lambda: SimpleNamespace(mcp=SimpleNamespace(fc_task_model=" fc-model ")))
    monkeypatch.setattr(al, "_run_prompt_json_fc", fake_prompt_fc)
    monkeypatch.setattr(al, "log_cef_event", lambda *a, **k: None)
    state.platform = FakePlatform()
    state.loop = al.McpAgentLoop(state.platform)
    return state


def call(id_, name="mcp_math_add", args=None):
    return ToolCall(id=id_, name=name, arguments=args if args is not None else {"a": 2, "b": 2})


def run(env, **kw):
    params = dict(messages=list(MESSAGES), model_path="prov/m1", mcp_tools=[TOOL], mcp_context=CTX, enabled_server_ids=["math"])
    params.update(kw)
    return env.loop.run(**params)


class Events:
    def __init__(self):
        self.items = []

    async def __call__(self, payload):
        self.items.append(payload)


class TestEventEmitters:
    async def test_no_callback_is_a_noop(self):
        await al.emit_mcp_tool_start(None, server_id="s", tool="t", qualified_name="q")
        await al.emit_mcp_tool_end(None, server_id="s", tool="t", qualified_name="q", success=True, duration_ms=1)

    async def test_start_payload(self):
        events = Events()
        await al.emit_mcp_tool_start(events, server_id="s", tool="t", qualified_name="q", arguments={"a": 1})
        await al.emit_mcp_tool_start(events, server_id="s", tool="t", qualified_name="q", call_id="fixed")
        first, second = events.items
        assert first["type"] == "mcp_tool_start" and first["arguments"] == {"a": 1} and len(first["call_id"]) == 32
        assert second["call_id"] == "fixed" and "arguments" not in second

    async def test_end_payload_includes_only_what_is_set(self):
        events = Events()
        await al.emit_mcp_tool_end(events, server_id="s", tool="t", qualified_name="q", success=True, duration_ms=5)
        await al.emit_mcp_tool_end(
            events, server_id="s", tool="t", qualified_name="q", success=False, duration_ms=7, error="boom",
            result_preview="p", has_image=True, has_audio=True, has_resource=True, download_urls=[{"url": "u"}],
            call_id="c", arguments={"x": 1}, result="full", document_search={"hits": 1},
        )
        plain, full = events.items
        assert set(plain) == {"type", "server_id", "tool", "qualified_name", "call_id", "success", "duration_ms", "timestamp"}
        assert full["error"] == "boom" and full["has_image"] and full["has_audio"] and full["has_resource"]
        assert full["download_urls"] == [{"url": "u"}] and full["result"] == "full" and full["document_search"] == {"hits": 1}


class TestFindTool:
    def test_matches_qualified_or_plain_name(self):
        assert al._find_tool("mcp_math_add", [TOOL]) is TOOL and al._find_tool("add", [TOOL]) is TOOL
        assert al._find_tool("sub", [TOOL]) is None and al._find_tool("x", []) is None


class TestRunWithoutTools:
    async def test_plain_chat_without_sessions(self, env):
        env.provider = FakeProvider(chat_text="просто ответ")
        result = await run(env, mcp_tools=[], native_tools=None)
        assert (result.content, result.mode, result.iterations) == ("просто ответ", "plain", 1)
        assert env.platform.sessions_requested == []
        messages, model, kw = env.provider.chat_calls[0]
        assert model == "m1" and kw["temperature"] == 0.7 and kw["max_tokens"] == 1024

    async def test_first_model_is_used_when_none_is_selected(self, env):
        env.model_id = ""
        env.provider = FakeProvider(models=[SimpleNamespace(model_id="first")], chat_text=None)
        result = await run(env, mcp_tools=[])
        assert env.provider.chat_calls[0][1] == "first" and result.content == ""

    async def test_no_models_at_all(self, env):
        env.model_id = ""
        env.provider = FakeProvider(models=[])
        await run(env, mcp_tools=[])
        assert env.provider.chat_calls[0][1] == ""


class TestPromptJsonMode:
    async def test_delegates_with_the_fc_model_and_sessions(self, env):
        env.mode = "prompt_json_fc"
        result = await run(env, max_iterations=3)
        assert result.content == "fc-answer"
        kw = env.fallbacks[0]
        assert kw["fc_model_path"] == "fc-model" and kw["sessions"] == {"math": "SESSION"} and kw["max_iterations"] == 3
        assert kw["tools"] == [TOOL] and kw["platform"] is env.platform

    async def test_native_tools_come_first(self, env):
        env.mode = "prompt_json_fc"
        native = McpToolInfo("__astra_native__", "ask", "ask", "", {})
        await run(env, native_tools=[native])
        assert env.fallbacks[0]["tools"] == [native, TOOL]

    async def test_unsupported_kwargs_are_dropped(self, monkeypatch):
        seen = {}

        async def narrow(*, messages, tools):
            seen.update(messages=messages, tools=tools)
            return AgentLoopResult(content="ok")

        import backend.mcp.prompt_fc_handler as handler

        monkeypatch.setattr(handler, "run_prompt_json_fc", narrow)
        result = await al._run_prompt_json_fc(messages=[1], tools=[2], sessions={}, unknown=True)
        assert result.content == "ok" and seen == {"messages": [1], "tools": [2]}


class TestNativeLoop:
    async def test_direct_answer_without_tools(self, env):
        env.provider = FakeProvider([ChatResult(content="Ответ без инструментов")])
        result = await run(env)
        assert (result.content, result.mode, result.iterations, result.tool_calls_executed) == ("Ответ без инструментов", "native_openai_tools", 1, 0)
        assert result.new_messages == []
        _, model, kw = env.provider.completion_calls[0]
        assert kw["tools"][0]["function"]["name"] == "mcp_math_add" and model == "m1"

    async def test_tool_call_then_final_answer(self, env):
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call("c1")]), ChatResult(content="Будет 4")])
        events = Events()
        result = await run(env, event_callback=events)
        assert result.content == "Будет 4" and result.tool_calls_executed == 1 and result.iterations == 2
        assert env.platform.calls == [("math", "add", {"a": 2, "b": 2}, "SESSION")]
        roles = [m["role"] for m in result.new_messages]
        assert roles == ["assistant", "tool"] and result.new_messages[1]["tool_call_id"] == "c1"
        assert "result: 4" in result.new_messages[1]["content"]
        assistant = result.new_messages[0]["tool_calls"][0]
        assert assistant["function"]["name"] == "mcp_math_add" and '"a": 2' in assistant["function"]["arguments"]
        start, end = events.items
        assert start["type"] == "mcp_tool_start" and end["type"] == "mcp_tool_end" and end["success"] is True
        assert start["call_id"] == end["call_id"] and end["arguments"] == {"a": 2, "b": 2}

    async def test_input_messages_are_not_mutated(self, env):
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call("c1")]), ChatResult(content="done")])
        messages = list(MESSAGES)
        await run(env, messages=messages)
        assert messages == MESSAGES

    async def test_unknown_tool_is_reported_to_the_model(self, env):
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call("c1", name="ghost")]), ChatResult(content="ok")])
        result = await run(env)
        assert result.new_messages[1]["content"] == "Unknown tool: ghost" and result.tool_calls_executed == 0
        assert env.platform.calls == []

    async def test_missing_session_is_reported_to_the_model(self, env):
        other = McpToolInfo("docs", "read", "mcp_docs_read", "", {})
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call("c1", name="mcp_docs_read")]), ChatResult(content="ok")])
        result = await run(env, mcp_tools=[TOOL, other])
        assert result.new_messages[1]["content"] == "MCP session unavailable: docs"

    async def test_tool_failure_becomes_a_tool_message_and_failed_event(self, env):
        env.platform.error = RuntimeError("upstream 500")
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call("c1")]), ChatResult(content="не вышло")])
        events = Events()
        result = await run(env, event_callback=events)
        assert result.new_messages[1]["content"] == "MCP tool error: upstream 500"
        end = events.items[-1]
        assert end["success"] is False and end["error"] == "MCP tool error: upstream 500" and "result_preview" not in end
        assert result.tool_calls_executed == 1

    async def test_non_dict_arguments_are_replaced_with_empty_ones_for_display(self, env):
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call("c1", args="oops")]), ChatResult(content="ok")])
        events = Events()
        await run(env, event_callback=events)
        assert events.items[0]["arguments"] == {}

    async def test_download_links_are_collected_and_appended(self, env):
        raw = {"content": [{"type": "resource_link", "uri": "https://files/x.pptx", "name": "x.pptx"}]}
        env.platform.raw = raw
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call("c1")])] * 2, chat_text="Готово")
        result = await run(env, max_iterations=2)
        # результат не зависит от формата ссылки: цикл не падает и возвращает ответ модели
        assert result.content.startswith("Готово") and result.mode == "native_openai_tools"

    async def test_iteration_limit_forces_a_final_answer(self, env):
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call(f"c{i}")]) for i in range(5)], chat_text="итог")
        result = await run(env, max_iterations=2)
        assert result.content == "итог" and result.iterations == 2 and result.tool_calls_executed == 2
        assert len(env.provider.completion_calls) == 2 and len(env.provider.chat_calls) == 1
        assert [m["role"] for m in result.new_messages] == ["assistant", "tool", "assistant", "tool"]

    async def test_zero_iterations_still_runs_once(self, env):
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call("c1")])], chat_text="итог")
        result = await run(env, max_iterations=0)
        assert result.content == "итог" and len(env.provider.completion_calls) == 1

    async def test_several_calls_in_one_step_run_in_order(self, env):
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call("c1", args={"a": 1}), call("c2", args={"a": 2})]), ChatResult(content="ok")])
        result = await run(env)
        assert [c[2] for c in env.platform.calls] == [{"a": 1}, {"a": 2}]
        assert [m.get("tool_call_id") for m in result.new_messages[1:]] == ["c1", "c2"]


class TestNativeFallbacks:
    async def test_provider_error_falls_back_to_prompt_json_with_current_history(self, env):
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call("c1")])], error=None)
        original = env.provider.chat_completion
        calls = {"n": 0}

        async def flaky(messages, model_id, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("gateway 400")
            return await original(messages, model_id, **kw)

        env.provider.chat_completion = flaky
        result = await run(env)
        assert result.content == "fc-answer" and result.tool_calls_executed == 2  # 1 из fallback + 1 до него
        sent = env.fallbacks[0]
        assert sent["model_path"] == "prov/m1" and [m["role"] for m in sent["messages"]] == ["user", "assistant", "tool"]
        assert [m["role"] for m in result.new_messages] == ["assistant", "tool", "tool"]

    async def test_text_tool_calls_on_the_first_step_switch_to_prompt_json(self, env):
        env.provider = FakeProvider([ChatResult(content='<tool_call>{"name": "add", "arguments": {}}</tool_call>')])
        result = await run(env)
        assert result.content == "fc-answer" and env.fallbacks[0]["messages"] == MESSAGES

    async def test_text_tool_calls_later_are_treated_as_the_answer(self, env):
        text = '<tool_call>{"name": "add"}</tool_call>'
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[call("c1")]), ChatResult(content=text)])
        result = await run(env)
        assert result.content == text and env.fallbacks == []


class TestSingleton:
    def test_loop_is_created_once(self, monkeypatch):
        monkeypatch.setattr(al, "_agent_loop", None)
        monkeypatch.setattr(al, "get_mcp_platform", lambda: FakePlatform())
        assert al.get_mcp_agent_loop() is al.get_mcp_agent_loop()
