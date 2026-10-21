"""mcp/prompt_fc_handler: выбор инструментов через JSON-промпт (fallback без native tool-calls)."""

import json
from types import SimpleNamespace

import pytest

from backend.llm_providers.base import ChatResult
from backend.mcp import prompt_fc_handler as h
from backend.mcp.types import McpCallContext, McpToolInfo

CTX = McpCallContext(user_id="u1", username="ivan", chat_id="chat-1")
TOOL = McpToolInfo("math", "add", "mcp_math_add", "adds", {"type": "object"})
NATIVE = McpToolInfo("__astra_native__", "subagent", "subagent", "spawn", {})


def fc(*calls):
    return ChatResult(content=json.dumps({"tool_calls": list(calls)}))


class FakeProvider:
    id = "prov"

    def __init__(self, completions=(), final="финал", models=()):
        self.completions, self.final, self.models = list(completions), final, models
        self.completion_calls, self.chat_calls = [], []

    async def chat_completion(self, messages, model_id, **kw):
        self.completion_calls.append((list(messages), model_id, kw))
        return self.completions.pop(0)

    async def chat(self, messages, model_id, **kw):
        self.chat_calls.append((list(messages), model_id, kw))
        return self.final

    async def list_models(self):
        return list(self.models)


class FakePlatform:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    async def call_tool(self, server_id, name, args, context, session):
        self.calls.append((server_id, name, args, session))
        if self.error:
            raise self.error
        return {"content": [{"type": "text", "text": "result: 4"}]}


class Events:
    def __init__(self):
        self.items = []

    async def __call__(self, event):
        self.items.append(event)


@pytest.fixture
def env(monkeypatch):
    st = SimpleNamespace(provider=FakeProvider(), model_id="m1", resolved=[])

    class Registry:
        def resolve(self, path):
            st.resolved.append(path)
            return st.provider, st.model_id

    async def get_registry():
        return Registry()

    monkeypatch.setattr(h, "get_registry", get_registry)
    monkeypatch.setattr(h, "get_settings", lambda: SimpleNamespace(mcp=SimpleNamespace(fc_task_model="fc-default")))
    st.platform = FakePlatform()
    return st


async def run(env, **over):
    kw = dict(
        messages=[{"role": "user", "content": "2+2?"}],
        model_path="main",
        tools=[TOOL],
        context=CTX,
        platform=env.platform,
        sessions={"math": "SESSION"},
    )
    kw.update(over)
    return await h.run_prompt_json_fc(**kw)


class TestHelpers:
    def test_render_prompt_contains_tools(self):
        text = h._render_tools_prompt([TOOL])
        assert "{{TOOLS}}" not in text and '"mcp_math_add"' in text and "tool_calls" in text

    @pytest.mark.parametrize(
        "text,expected",
        [
            ('до {"a": 1} после', {"a": 1}),
            ('```json\n{"tool_calls": []}\n```', {"tool_calls": []}),
            ("", None),
            ("нет json", None),
            ("{сломан", None),
            ("} {", None),
        ],
    )
    def test_extract_json(self, text, expected):
        assert h._extract_json_object(text) == expected

    def test_find_tool_by_either_name(self):
        assert h._find_tool("mcp_math_add", [TOOL]) is TOOL
        assert h._find_tool("add", [TOOL]) is TOOL
        assert h._find_tool("zzz", [TOOL]) is None


class TestModelChoice:
    async def test_explicit_fc_model_wins(self, env):
        env.provider = FakeProvider([ChatResult(content="{}")])
        await run(env, fc_model_path="special")
        assert env.resolved == ["special"]

    async def test_main_model_then_settings(self, env):
        env.provider = FakeProvider([ChatResult(content="{}"), ChatResult(content="{}")])
        await run(env)
        await run(env, model_path="")  # пусто - берётся fc_task_model из настроек
        assert env.resolved == ["main", "fc-default"]


    async def test_first_listed_model_when_unresolved(self, env):
        env.model_id = ""
        env.provider = FakeProvider([ChatResult(content="{}")], models=[SimpleNamespace(model_id="first")])
        await run(env)
        assert env.provider.completion_calls[0][1] == "first"


class TestNoToolCalls:
    async def test_unparsed_answer_is_returned_as_is(self, env):
        env.provider = FakeProvider([ChatResult(content="просто текст")])
        res = await run(env)
        assert res.content == "просто текст" and res.mode == "prompt_json_fc"
        assert res.iterations == 1 and res.tool_calls_executed == 0
        assert env.provider.chat_calls == []

    async def test_empty_unparsed_answer_has_placeholder(self, env):
        env.provider = FakeProvider([ChatResult(content="")])
        res = await run(env)
        assert res.content == "Не удалось распознать вызов инструмента."

    async def test_empty_tool_calls_make_final_answer(self, env):
        env.provider = FakeProvider([fc()], final="ответ без инструментов")
        res = await run(env)
        assert res.content == "ответ без инструментов" and res.tool_calls_executed == 0
        # в запросе к модели выбора - история и вопрос пользователя
        fc_messages = env.provider.completion_calls[0][0]
        assert fc_messages[0]["role"] == "system" and "Query: 2+2?" in fc_messages[1]["content"]


class TestToolExecution:
    async def test_tool_runs_then_final_answer(self, env):
        env.provider = FakeProvider(
            [fc({"name": "mcp_math_add", "parameters": {"a": 2, "b": 2}}), fc()], final="Будет 4"
        )
        events = Events()
        res = await run(env, event_callback=events, max_iterations=2)
        assert env.platform.calls == [("math", "add", {"a": 2, "b": 2}, "SESSION")]
        assert res.content.startswith("Будет 4") and res.tool_calls_executed == 1 and res.iterations == 2
        roles = [m["role"] for m in res.new_messages]
        assert roles[:2] == ["assistant", "user"]
        assert "Tool results:\nresult: 4" in res.new_messages[1]["content"]
        assert len(events.items) >= 2  # start и end для карточки в UI

    async def test_arguments_alias_and_non_dict_params(self, env):
        env.provider = FakeProvider(
            [fc({"name": "add", "arguments": {"x": 1}}, {"name": "add", "parameters": "bad"}, "мусор"), fc()]
        )
        await run(env, max_iterations=2)
        assert [c[2] for c in env.platform.calls] == [{"x": 1}, {}]

    async def test_unknown_tool_and_missing_session(self, env):
        env.provider = FakeProvider([fc({"name": "zzz"}, {"name": "add"}), fc()])
        res = await run(env, sessions={})
        text = res.new_messages[1]["content"]
        assert "Tool zzz not found" in text and "MCP session for math unavailable" in text
        assert res.tool_calls_executed == 0

    async def test_tool_error_is_reported_to_model_and_ui(self, env):
        env.platform = FakePlatform(error=RuntimeError("boom"))
        env.provider = FakeProvider([fc({"name": "add"}), fc()])
        events = Events()
        res = await run(env, event_callback=events)
        assert "Error calling add: boom" in res.new_messages[1]["content"]
        assert res.tool_calls_executed == 0

    async def test_download_links_become_attachments(self, env, monkeypatch):
        link = {"url": "http://x/f.pdf", "name": "f.pdf"}
        monkeypatch.setattr(h, "enrich_download_links", lambda parsed, sid: [link, dict(link)])
        env.provider = FakeProvider([fc({"name": "add"}), fc()], final="готово")
        res = await run(env, max_iterations=2)
        assert res.attachments == [link]
        assert "f.pdf" in res.content or "http://x/f.pdf" in res.content


class TestNudgeAndLimit:
    async def test_single_nudge_after_tools_then_final(self, env):
        env.provider = FakeProvider([fc({"name": "add"}), fc(), fc()], final="итог")
        res = await run(env, max_iterations=5)
        # шаг 1 - вызов, шаг 2 - пусто (толчок), шаг 3 - пусто (финал)
        assert res.iterations == 3 and res.content.startswith("итог")
        nudges = [m for m in res.new_messages if "likely not finished" in m["content"]]
        assert len(nudges) == 1

    async def test_no_nudge_on_last_step(self, env):
        env.provider = FakeProvider([fc({"name": "add"}), fc()], final="итог")
        res = await run(env, max_iterations=2)
        assert res.iterations == 2 and not any("likely not finished" in m["content"] for m in res.new_messages)

    async def test_iteration_limit_forces_final_answer(self, env):
        env.provider = FakeProvider([fc({"name": "add"}), fc({"name": "add"})], final="по лимиту")
        res = await run(env, max_iterations=2)
        assert res.content.startswith("по лимиту") and res.iterations == 2 and res.tool_calls_executed == 2
        assert len(env.provider.chat_calls) == 1

    async def test_zero_limit_runs_at_least_once(self, env):
        env.provider = FakeProvider([fc()], final="ok")
        res = await run(env, max_iterations=0)
        assert res.content.startswith("ok")


class FakeSubCall:
    pass


class TestNativeTools:
    async def test_native_tool_success_uses_model_text_and_ui_text(self, env, monkeypatch):
        async def native(tool_info, args, **kw):
            return "[[ОТВЕТ 1]] для модели", "для UI", {"hits": []}

        monkeypatch.setattr(h, "run_native_tool_call", native)
        env.provider = FakeProvider([fc({"name": "subagent", "parameters": {"prompt": "x"}}), fc()])
        events = Events()
        res = await run(env, tools=[NATIVE], event_callback=events, max_iterations=2)
        assert "[[ОТВЕТ 1]] для модели" in res.new_messages[1]["content"]
        assert res.tool_calls_executed == 1
        assert env.platform.calls == []

    async def test_native_tool_error_is_reported(self, env, monkeypatch):
        async def native(tool_info, args, **kw):
            raise RuntimeError("child failed")

        monkeypatch.setattr(h, "run_native_tool_call", native)
        env.provider = FakeProvider([fc({"name": "subagent"}), fc()])
        res = await run(env, tools=[NATIVE])
        assert "Native tool error: child failed" in res.new_messages[1]["content"]
        assert res.tool_calls_executed == 0
