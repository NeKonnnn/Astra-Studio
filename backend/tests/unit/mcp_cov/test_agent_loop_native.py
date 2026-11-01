"""mcp/agent_loop: встроенные (native) инструменты в цикле и выбор модели при запуске с MCP-сессиями."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.agents.subagents.settings import NATIVE_SERVER_ID
from backend.llm_providers.base import ChatResult, ToolCall
from backend.mcp import agent_loop as al
from backend.mcp.types import AgentLoopResult, McpCallContext, McpToolInfo

pytestmark = pytest.mark.unit

CTX = McpCallContext(user_id="u1", username="ivan", chat_id="chat-1")
NATIVE = McpToolInfo(NATIVE_SERVER_ID, "search_docs", "search_docs", "native", {"type": "object", "properties": {}})
MESSAGES = [{"role": "user", "content": "найди"}]


class FakeProvider:
    id = "prov"

    def __init__(self, completions=(), models=()):
        self.completions, self.models = list(completions), list(models)
        self.chat_calls = []

    async def chat_completion(self, messages, model_id, **kw):
        return self.completions.pop(0)

    async def chat(self, messages, model_id, **kw):
        self.chat_calls.append(model_id)
        return "финал"

    async def list_models(self):
        return list(self.models)


class FakePlatform:
    @asynccontextmanager
    async def request_sessions(self, ids, context):
        yield {}


class Events:
    def __init__(self):
        self.items = []

    async def __call__(self, payload):
        self.items.append(payload)


@pytest.fixture
def env(monkeypatch):
    state = SimpleNamespace(provider=FakeProvider(), model_id="m1", mode="native_openai_tools", fc=[], native_calls=[], native_result=("ответ [[ОТВЕТ 1]]", "ответ для UI", {"hits": 2}))

    class Registry:
        def resolve(self, path):
            return state.provider, state.model_id

    async def get_registry():
        return Registry()

    async def fake_native(tool_info, args, *, subagent_ctx=None, subagent_config=None, call=None):
        state.native_calls.append((tool_info.name, args))
        if isinstance(state.native_result, Exception):
            raise state.native_result
        return state.native_result

    async def fake_fc(**kw):
        state.fc.append(kw)
        return AgentLoopResult(content="fc")

    monkeypatch.setattr(al, "get_registry", get_registry)
    monkeypatch.setattr(al, "resolve_tool_calling_mode", lambda provider: state.mode)
    monkeypatch.setattr(al, "get_settings", lambda: SimpleNamespace(mcp=SimpleNamespace(fc_task_model="")))
    monkeypatch.setattr(al, "run_native_tool_call", fake_native)
    monkeypatch.setattr(al, "_run_prompt_json_fc", fake_fc)
    monkeypatch.setattr(al, "log_cef_event", lambda *a, **k: None)
    state.loop = al.McpAgentLoop(FakePlatform())
    return state


def run(env, **kw):
    params = dict(messages=list(MESSAGES), model_path="prov/m1", mcp_tools=[], mcp_context=CTX, enabled_server_ids=[], native_tools=[NATIVE])
    params.update(kw)
    return env.loop.run(**params)


class TestNativeToolInLoop:
    async def test_native_tool_result_goes_to_model_and_ui(self, env):
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[ToolCall(id="c1", name="search_docs", arguments={"q": "x"})]), ChatResult(content="готово")])
        events = Events()
        result = await run(env, event_callback=events)
        assert result.content == "готово" and result.tool_calls_executed == 1
        assert env.native_calls == [("search_docs", {"q": "x"})]
        # модели уходит текст с меткой, в UI - без метки
        assert result.new_messages[1]["content"] == "ответ [[ОТВЕТ 1]]"
        start, end = events.items
        assert start["server_id"] == NATIVE_SERVER_ID and end["success"] is True
        assert end["result"] == "ответ для UI" and end["result_preview"] == "ответ для UI"
        assert end["document_search"] == {"hits": 2}

    async def test_native_tool_failure_is_reported_to_the_model(self, env):
        env.native_result = ValueError("индекс недоступен")
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[ToolCall(id="c1", name="search_docs", arguments={})]), ChatResult(content="не вышло")])
        events = Events()
        result = await run(env, event_callback=events)
        assert result.new_messages[1]["content"] == "Native tool error: индекс недоступен"
        end = events.items[-1]
        assert end["success"] is False and end["error"] == "Native tool error: индекс недоступен"
        assert "document_search" not in end and result.tool_calls_executed == 1

    async def test_empty_ui_preview_is_omitted(self, env):
        env.native_result = ("текст", "", None)
        env.provider = FakeProvider([ChatResult(content="", tool_calls=[ToolCall(id="c1", name="search_docs", arguments={})]), ChatResult(content="ок")])
        events = Events()
        await run(env, event_callback=events)
        assert "result_preview" not in events.items[-1]


class TestModelSelectionWithTools:
    async def test_first_catalog_model_is_used(self, env):
        env.model_id = ""
        env.provider = FakeProvider([ChatResult(content="сразу")], models=[SimpleNamespace(model_id="first"), SimpleNamespace(model_id="second")])
        seen = []
        orig = env.provider.chat_completion

        async def spy(messages, model_id, **kw):
            seen.append(model_id)
            return await orig(messages, model_id, **kw)

        env.provider.chat_completion = spy
        result = await run(env)
        assert result.content == "сразу" and seen == ["first"]

    async def test_no_models_gives_empty_model_id(self, env):
        env.model_id = ""
        env.provider = FakeProvider([ChatResult(content="ответ")], models=[])
        seen = []
        orig = env.provider.chat_completion

        async def spy(messages, model_id, **kw):
            seen.append(model_id)
            return await orig(messages, model_id, **kw)

        env.provider.chat_completion = spy
        result = await run(env)
        assert seen == [""] and result.content == "ответ"
