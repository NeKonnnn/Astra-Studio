"""mcp/orchestrator_bridge: синхронный LLM-вызов через ProviderRegistry и подключение MCP tools к оркестратору."""

import asyncio
import sys
from types import SimpleNamespace

import httpx
import pytest

from backend.llm_providers import routing
from backend.mcp import orchestrator_bridge as ob

pytestmark = pytest.mark.unit


class TestStreamCallback:
    def test_three_arg_callback(self):
        seen = []
        assert ob._invoke_stream_callback(lambda c, a, r: seen.append((c, a, r)), "x", "ax", "reasoning") is True
        assert seen == [("x", "ax", "reasoning")]

    def test_false_result_means_stop(self):
        assert ob._invoke_stream_callback(lambda c, a, r: False, "x", "a") is False

    def test_none_result_means_continue(self):
        assert ob._invoke_stream_callback(lambda c, a, r: None, "x", "a") is True

    def test_old_two_arg_callback_is_supported(self):
        seen = []
        assert ob._invoke_stream_callback(lambda c, a: seen.append((c, a)), "x", "a", "content") is True
        assert seen == [("x", "a")]

    def test_heartbeat_is_skipped_for_old_callback(self):
        seen = []
        assert ob._invoke_stream_callback(lambda c, a: seen.append(1), "", "a", "heartbeat") is True
        assert seen == []


class TestRunAsync:
    def test_without_running_loop(self):
        async def work():
            return 5

        assert ob._run_async(work()) == 5

    async def test_inside_running_loop_uses_thread(self):
        async def work():
            await asyncio.sleep(0)
            return "done"

        assert ob._run_async(work()) == "done"


class TestPickChatModel:
    def models(self, *ids):
        return [SimpleNamespace(model_id=i) for i in ids]

    def test_empty(self):
        assert ob._pick_chat_model(None, provider_id="p") == ""
        assert ob._pick_chat_model([], provider_id="p") == ""
        assert ob._pick_chat_model(self.models("", "  "), provider_id="p") == ""

    def test_skips_embedders_and_rerankers(self):
        ms = self.models("embed/FRIDA", "BGE-reranker", "bge-m3", "qwen3-32b")
        assert ob._pick_chat_model(ms, provider_id="p") == "qwen3-32b"

    def test_first_chat_model_wins(self):
        assert ob._pick_chat_model(self.models("a-chat", "b-chat"), provider_id="p") == "a-chat"

    def test_only_non_chat_models_returns_first_with_warning(self, monkeypatch):
        warnings = []
        monkeypatch.setattr(ob, "log", SimpleNamespace(warning=lambda *a, **k: warnings.append(a)))
        assert ob._pick_chat_model(self.models("embed/x", "rerank-y"), provider_id="corsur") == "embed/x"
        assert len(warnings) == 1 and warnings[0][1] == "corsur"


class FakeProvider:
    id = "prov"

    def __init__(self, models=()):
        self.models = list(models)
        self.chat_calls, self.stream_calls = [], []
        self.chat_result = "ответ"
        self.chat_error = None

    async def list_models(self):
        return list(self.models)

    async def chat(self, messages, model_id, **kw):
        self.chat_calls.append((messages, model_id, kw))
        if self.chat_error:
            raise self.chat_error
        return self.chat_result

    async def stream_chat(self, messages, model_id, callback, **kw):
        self.stream_calls.append((messages, model_id, kw))
        callback("ab", "ab", "content")
        return "streamed"


@pytest.fixture
def env(monkeypatch):
    import backend.llm_providers as llm
    import backend.app_state as app_state

    state = SimpleNamespace(provider=FakeProvider(), model_id="m1", resolved=[])

    class Registry:
        def resolve(self, path):
            state.resolved.append(path)
            return state.provider, state.model_id

    async def get_registry():
        return Registry()

    monkeypatch.setattr(llm, "get_registry", get_registry)
    monkeypatch.setattr(app_state, "get_current_model_path", lambda: "cur/model")
    # Поля выборки/мышления зависят от окружения - фиксируем.
    monkeypatch.setattr(routing, "merge_sampling_request_extra", lambda extra: extra)
    monkeypatch.setattr(routing, "service_call_request_extra", lambda: {"chat_template_kwargs": {"enable_thinking": False}})
    monkeypatch.setattr(routing, "service_min_max_tokens", lambda: 1024)
    monkeypatch.setattr(routing, "service_no_think_tag_enabled", lambda: True)
    return state


class TestSyncChat:
    def test_plain_call(self, env):
        assert ob.sync_chat_via_registry("привет", history=[{"role": "user", "content": "q"}], system_prompt="sys", model_path="x/y") == "ответ"
        messages, model, kw = env.provider.chat_calls[0]
        assert env.resolved == ["x/y"] and model == "m1"
        assert [m["role"] for m in messages] == ["system", "user", "user"] and messages[-1]["content"] == "привет"
        assert kw == {
            "temperature": 0.7, "max_tokens": 1024,
            "request_extra": {"chat_template_kwargs": {"enable_thinking": False}},
        }

    def test_thinking_flag_is_forwarded(self, env):
        ob.sync_chat_via_registry("q", enable_thinking=True)
        assert env.provider.chat_calls[0][2]["request_extra"] == {"chat_template_kwargs": {"enable_thinking": True}}

    def test_default_model_path_comes_from_app_state(self, env):
        ob.sync_chat_via_registry("q")
        assert env.resolved == ["cur/model"]

    def test_app_state_import_failure_falls_back_to_empty_path(self, env, monkeypatch):
        monkeypatch.setitem(sys.modules, "backend.app_state", None)
        ob.sync_chat_via_registry("q")
        assert env.resolved == [""]

    def test_model_is_picked_from_catalog_when_not_resolved(self, env):
        env.model_id = ""
        env.provider = FakeProvider([SimpleNamespace(model_id="embed/FRIDA"), SimpleNamespace(model_id="qwen")])
        ob.sync_chat_via_registry("q")
        assert env.provider.chat_calls[0][1] == "qwen"

    def test_no_model_at_all_returns_none(self, env):
        env.model_id = ""
        assert ob.sync_chat_via_registry("q") is None
        assert env.provider.chat_calls == []

    def test_service_call_disables_thinking_and_raises_token_floor(self, env):
        ob.sync_chat_via_registry("q", service_call=True, max_tokens=100)
        messages, _, kw = env.provider.chat_calls[0]
        assert kw["max_tokens"] == 1024 and kw["request_extra"] == {"chat_template_kwargs": {"enable_thinking": False}}
        assert messages[-1]["content"].endswith("/no_think")

    def test_service_call_keeps_larger_token_budget(self, env):
        ob.sync_chat_via_registry("q", service_call=True, max_tokens=4000)
        assert env.provider.chat_calls[0][2]["max_tokens"] == 4000

    def test_service_call_without_thinking_switch(self, env, monkeypatch):
        monkeypatch.setattr(routing, "service_call_request_extra", lambda: {})
        ob.sync_chat_via_registry("q", service_call=True)
        messages, _, kw = env.provider.chat_calls[0]
        assert kw["request_extra"] is None and messages[-1]["content"] == "q"

    def test_service_call_no_think_tag_can_be_disabled(self, env, monkeypatch):
        monkeypatch.setattr(routing, "service_no_think_tag_enabled", lambda: False)
        ob.sync_chat_via_registry("q", service_call=True)
        assert env.provider.chat_calls[0][0][-1]["content"] == "q"

    def test_service_flag_is_ignored_when_thinking_requested(self, env):
        ob.sync_chat_via_registry("q", service_call=True, enable_thinking=True, max_tokens=50)
        _, _, kw = env.provider.chat_calls[0]
        assert kw["max_tokens"] == 50 and kw["request_extra"] == {"chat_template_kwargs": {"enable_thinking": True}}

    def test_sampling_fields_are_merged_last(self, env, monkeypatch):
        monkeypatch.setattr(routing, "merge_sampling_request_extra", lambda extra: {**(extra or {}), "top_p": 0.5})
        ob.sync_chat_via_registry("q", service_call=True)
        assert env.provider.chat_calls[0][2]["request_extra"]["top_p"] == 0.5

    def test_streaming_uses_callback_and_effective_tokens(self, env):
        chunks = []
        result = ob.sync_chat_via_registry(
            "q", streaming=True, stream_callback=lambda c, a: chunks.append((c, a)), service_call=True, max_tokens=10
        )
        assert result == "streamed" and chunks == [("ab", "ab")]
        assert env.provider.stream_calls[0][2]["max_tokens"] == 1024 and env.provider.chat_calls == []

    def test_streaming_without_callback_uses_chat(self, env):
        assert ob.sync_chat_via_registry("q", streaming=True) == "ответ"

    def test_http_status_error_is_reraised(self, env):
        request = httpx.Request("POST", "http://llm")
        env.provider.chat_error = httpx.HTTPStatusError("422", request=request, response=httpx.Response(422, request=request))
        with pytest.raises(httpx.HTTPStatusError):
            ob.sync_chat_via_registry("q")

    def test_other_errors_give_none(self, env):
        env.provider.chat_error = RuntimeError("down")
        assert ob.sync_chat_via_registry("q") is None

    async def test_works_inside_running_loop(self, env):
        assert await asyncio.to_thread(ob.sync_chat_via_registry, "q") == "ответ"
        assert ob.sync_chat_via_registry("q") == "ответ"  # цикл уже запущен: вызов уходит в отдельный поток


class FakeTool:
    def __init__(self, name):
        self.name = name


class FakeOrchestrator:
    def __init__(self, names=()):
        self.tools = [FakeTool(n) for n in names]
        self.tools_by_name = {n: t for n, t in zip(names, self.tools)}
        self.tool_status = {n: True for n in names}


class TestAttachMcpTools:
    @pytest.fixture
    def loader(self, monkeypatch):
        import backend.mcp.langgraph_tools as lt

        state = SimpleNamespace(calls=[], tools=[FakeTool("mcp_a_x")])

        async def fake_load(*, server_ids, context):
            state.calls.append((server_ids, context))
            return list(state.tools)

        monkeypatch.setattr(lt, "load_mcp_langgraph_tools", fake_load)
        return state

    async def test_no_servers(self, loader):
        orch = FakeOrchestrator()
        assert await ob.attach_mcp_tools_to_orchestrator(orch, {"tool_ids": []}) == 0
        assert loader.calls == []

    async def test_attaches_tools_from_tool_ids(self, loader):
        orch = FakeOrchestrator(["builtin"])
        ctx = {"tool_ids": ["server:mcp:a", "mcp:a", "b"]}
        assert await ob.attach_mcp_tools_to_orchestrator(orch, ctx) == 1
        assert loader.calls[0][0] == ["a", "b"] and loader.calls[0][1] is ctx
        assert [t.name for t in orch.tools] == ["builtin", "mcp_a_x"]
        assert orch.tool_status["mcp_a_x"] is True and orch._dynamic_mcp_tool_names == {"mcp_a_x"}

    async def test_scalar_tool_id_and_mcp_tool_ids_key(self, loader):
        orch = FakeOrchestrator()
        await ob.attach_mcp_tools_to_orchestrator(orch, {"mcp_tool_ids": "docs"})
        assert loader.calls[0][0] == ["docs"]

    async def test_previous_dynamic_tools_are_removed(self, loader):
        orch = FakeOrchestrator(["builtin", "old_mcp"])
        orch._dynamic_mcp_tool_names = {"old_mcp"}
        loader.tools = []
        assert await ob.attach_mcp_tools_to_orchestrator(orch, {"tool_ids": ["a"]}) == 0
        assert [t.name for t in orch.tools] == ["builtin"]
        assert "old_mcp" not in orch.tools_by_name and "old_mcp" not in orch.tool_status
        assert orch._dynamic_mcp_tool_names == set()
