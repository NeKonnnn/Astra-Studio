"""mcp/langgraph_tools: MCP-инструменты как LangChain StructuredTool."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.mcp import langgraph_tools as lt
from backend.mcp.types import McpToolInfo

ADD = McpToolInfo("math", "add", "mcp_math_add", "складывает", {})
BARE = McpToolInfo("math", "noop", "mcp_math_noop", "", {})


class FakePlatform:
    def __init__(self, tools=None, enabled=True, initialized=True, raw=None, call_error=None, no_session=False, list_fail=()):
        self.enabled, self.initialized = enabled, initialized
        self.tools = tools if tools is not None else {"math": [ADD, BARE]}
        self.raw = raw if raw is not None else {"content": [{"type": "text", "text": "result: 4"}]}
        self.call_error, self.no_session, self.list_fail = call_error, no_session, set(list_fail)
        self.calls, self.filtered_with = [], None

    async def list_tools_for_server(self, sid, ctx):
        if sid in self.list_fail:
            raise RuntimeError("list failed")
        return list(self.tools.get(sid, []))

    def filter_tools_by_context(self, specs, ctx, enabled_server_ids=None):
        self.filtered_with = enabled_server_ids
        return specs

    @asynccontextmanager
    async def request_sessions(self, ids, ctx):
        yield {} if self.no_session else {ids[0]: "SESSION"}

    async def call_tool(self, sid, name, args, ctx, session):
        self.calls.append((sid, name, args, session))
        if self.call_error:
            raise self.call_error
        return self.raw


class FakeSio:
    def __init__(self, boom=False):
        self.events, self.boom = [], boom

    async def emit(self, name, payload, room=None):
        if self.boom:
            raise RuntimeError("socket gone")
        self.events.append((name, payload, room))


@pytest.fixture
def platform(monkeypatch):
    holder = SimpleNamespace(p=FakePlatform(), ctx_args=None)

    def build_ctx(user, chat_id=None, message_id=None):
        holder.ctx_args = (user, chat_id, message_id)
        return SimpleNamespace(user=user)

    monkeypatch.setattr("backend.mcp.platform.get_mcp_platform", lambda: holder.p)
    monkeypatch.setattr("backend.mcp.chat_integration.build_mcp_context_from_user", build_ctx)
    return holder


async def load(context=None, server_ids=("math",)):
    return await lt.load_mcp_langgraph_tools(server_ids=list(server_ids), context=context if context is not None else {})


class TestLoad:
    async def test_platform_disabled_or_not_ready(self, platform):
        platform.p = FakePlatform(enabled=False)
        assert await load() == []
        platform.p = FakePlatform(initialized=False)
        assert await load() == []

    async def test_no_specs(self, platform):
        platform.p = FakePlatform(tools={})
        assert await load() == []

    async def test_one_tool_per_spec_with_names_and_descriptions(self, platform):
        tools = await load({"current_user": {"user_id": "u"}, "conversation_id": "c", "message_id": "m"})
        assert [t.name for t in tools] == ["mcp_math_add", "mcp_math_noop"]
        assert tools[0].description == "складывает" and tools[1].description == "noop"
        assert platform.ctx_args == ({"user_id": "u"}, "c", "m")
        assert platform.p.filtered_with == ["math"]

    async def test_failing_server_does_not_hide_others(self, platform):
        platform.p = FakePlatform(tools={"ok": [ADD]}, list_fail={"bad"})
        tools = await load(server_ids=("bad", "ok"))
        assert [t.name for t in tools] == ["mcp_math_add"]

    async def test_missing_langchain_gives_empty(self, platform, monkeypatch):
        import builtins

        real = builtins.__import__

        def fake(name, *a, **k):
            if name == "langchain_core.tools":
                raise ImportError(name)
            return real(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake)
        assert await load() == []


class TestToolCall:
    async def test_success_returns_text_and_emits_start_end(self, platform):
        sio = FakeSio()
        (add, _) = await load({"sio": sio, "socket_id": "sid"})
        out = await add.coroutine(a=2, b=2)
        assert out == "result: 4"
        assert platform.p.calls == [("math", "add", {"a": 2, "b": 2}, "SESSION")]
        assert [e[1]["type"] for e in sio.events] == ["mcp_tool_start", "mcp_tool_end"]
        end = sio.events[1][1]
        assert end["success"] is True and end["qualified_name"] == "mcp_math_add"
        assert "error" not in end and sio.events[0][2] == "sid"

    async def test_tool_error_becomes_text_result(self, platform):
        platform.p = FakePlatform(call_error=RuntimeError("boom"))
        sio = FakeSio()
        (add, _) = await load({"sio": sio, "socket_id": "sid"})
        out = await add.coroutine()
        assert out == "MCP tool error: boom"
        end = sio.events[1][1]
        assert end["success"] is False and end["error"] == "boom"

    async def test_missing_session_is_an_error(self, platform):
        platform.p = FakePlatform(no_session=True)
        (add, _) = await load()
        assert "MCP session unavailable: math" in await add.coroutine()

    async def test_media_flags_and_preview_reach_the_event(self, platform, monkeypatch):
        parsed = SimpleNamespace(images=[1], audio=[1], resources=[1])
        monkeypatch.setattr("backend.mcp.result_parser.parse_mcp_result_to_struct", lambda raw: parsed)
        monkeypatch.setattr("backend.mcp.result_parser.format_parsed_for_llm", lambda p: "")
        monkeypatch.setattr("backend.mcp.result_parser.preview_parsed_for_ui", lambda p: "превью")
        sio = FakeSio()
        (add, _) = await load({"sio": sio, "socket_id": "s"})
        out = await add.coroutine()
        assert out == str(platform.p.raw)  # пустой текст -> сырой ответ
        end = sio.events[1][1]
        assert end["result_preview"] == "превью"
        assert end["has_image"] and end["has_audio"] and end["has_resource"]

    async def test_no_socket_means_no_events_and_emit_failure_is_swallowed(self, platform):
        (add, _) = await load({})
        assert await add.coroutine() == "result: 4"
        (add, _) = await load({"sio": FakeSio(boom=True), "socket_id": "s"})
        assert await add.coroutine() == "result: 4"

    def test_sync_wrapper_runs_without_a_loop(self, platform):
        tools = asyncio.run(load())
        out = tools[0].func()
        assert out == "result: 4"

    async def test_sync_wrapper_inside_running_loop_uses_a_thread(self, platform):
        tools = await load()
        out = await asyncio.get_running_loop().run_in_executor(None, tools[0].func)
        assert out == "result: 4"
        # прямой вызов из работающего цикла идёт через отдельный поток с новым loop
        assert tools[0].func() == "result: 4"


class TestCreateCompat:
    def test_without_loop(self, platform):
        tools = lt.create_mcp_langgraph_tools(server_ids=["math"], context_factory=lambda: {"current_user": {}})
        assert [t.name for t in tools] == ["mcp_math_add", "mcp_math_noop"]

    def test_empty_factory_result(self, platform):
        assert len(lt.create_mcp_langgraph_tools(server_ids=["math"], context_factory=lambda: None)) == 2

    async def test_inside_running_loop(self, platform):
        tools = lt.create_mcp_langgraph_tools(server_ids=["math"], context_factory=dict)
        assert len(tools) == 2
