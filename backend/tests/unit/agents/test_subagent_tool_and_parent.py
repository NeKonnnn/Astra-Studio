"""Инструмент subagent и его обвязка в цикле родителя (tool.py, parent.py, trace.py)."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from backend.agents.subagents import parent, trace
from backend.agents.subagents.settings import (
    MAX_SUBAGENT_DEPTH,
    NATIVE_SERVER_ID,
    SUBAGENT_TOOL_NAME,
    AgentSubagentsConfig,
)
from backend.agents.subagents.tool import (
    SubagentCall,
    SubagentOutcome,
    SubagentRunContext,
    build_subagent_tools,
    execute_native_tool,
    execute_subagent_tool,
)
from backend.mcp.types import McpToolInfo

NAMES = {1: "Парент", 2: "Аналитик", 3: "Юрист"}


def _cfg(**kw):
    base = dict(enabled=True, allow_self=False, agent_ids=[2, 3], agent_names=NAMES)
    base.update(kw)
    return AgentSubagentsConfig(**base)


def _ctx(executor=None, **kw):
    base = dict(
        parent_agent_id=1,
        parent_profile={"agent_id": 1, "name": "Парент"},
        user={"user_id": "u"},
        user_id="u",
        agent_names=dict(NAMES),
        executor=executor,
    )
    base.update(kw)
    return SubagentRunContext(**base)


def _tool(name=SUBAGENT_TOOL_NAME, server=NATIVE_SERVER_ID):
    return McpToolInfo(
        server_id=server, name=name, qualified_name=name, description="", parameters={}
    )


class TestRunContext:
    def test_reserve_numbers_increase_and_skip_taken(self):
        ctx = _ctx()
        assert [ctx.reserve_answer_no() for _ in range(3)] == [1, 2, 3]
        ctx.answers[9] = {"name": "x", "text": "y"}
        ctx.next_answer_no = 1
        assert ctx.reserve_answer_no() == 2  # len(answers)+1


class TestBuildTools:
    def test_disabled_gives_nothing(self):
        assert build_subagent_tools(_cfg(enabled=False), parent_agent_id=1, agent_names=NAMES) == []

    def test_no_targets_gives_nothing(self):
        cfg = _cfg(agent_ids=[], allow_self=False)
        assert build_subagent_tools(cfg, parent_agent_id=1, agent_names=NAMES) == []

    def test_enum_and_description(self):
        (tool,) = build_subagent_tools(_cfg(), parent_agent_id=1, agent_names=NAMES)
        assert tool.server_id == NATIVE_SERVER_ID and tool.name == SUBAGENT_TOOL_NAME
        enum = tool.parameters["properties"]["subagent_type"]["enum"]
        assert len(enum) == 2 and "self" not in enum
        assert "Аналитик" in tool.description and "Юрист" in tool.description
        assert tool.parameters["required"] == ["subagent_type", "prompt"]

    def test_self_option_needs_parent_id(self):
        cfg = _cfg(allow_self=True)
        (tool,) = build_subagent_tools(cfg, parent_agent_id=1, agent_names=NAMES)
        assert tool.parameters["properties"]["subagent_type"]["enum"][0] == "self"
        assert "Парент" in tool.description
        (tool,) = build_subagent_tools(cfg, parent_agent_id=None, agent_names=NAMES)
        assert "self" not in tool.parameters["properties"]["subagent_type"]["enum"]

    def test_missing_name_gets_placeholder(self):
        (tool,) = build_subagent_tools(_cfg(agent_ids=[7]), parent_agent_id=1, agent_names={})
        assert "Agent 7" in tool.description


class TestExecuteSubagent:
    async def test_depth_limit(self):
        out = await execute_subagent_tool(
            {"subagent_type": "Аналитик", "prompt": "x"},
            ctx=_ctx(depth=MAX_SUBAGENT_DEPTH), config=_cfg(),
        )
        assert "depth limit" in out

    async def test_step_budget(self):
        out = await execute_subagent_tool(
            {"subagent_type": "Аналитик", "prompt": "x"},
            ctx=_ctx(remaining_steps=0), config=_cfg(),
        )
        assert "budget exhausted" in out

    async def test_prompt_required_and_task_alias(self):
        calls = []

        async def ex(**kw):
            calls.append(kw)
            return "готово"

        ctx = _ctx(ex)
        out = await execute_subagent_tool({"subagent_type": "Аналитик", "prompt": " "}, ctx=ctx, config=_cfg())
        assert out == "Subagent prompt is required."
        out = await execute_subagent_tool({"subagent_type": "Аналитик", "task": "сделай"}, ctx=ctx, config=_cfg())
        assert out == "готово" and calls[0]["prompt"] == "сделай"

    async def test_unknown_type_lists_allowed(self):
        out = await execute_subagent_tool(
            {"subagent_type": "Левый", "prompt": "x"}, ctx=_ctx(lambda **k: None), config=_cfg()
        )
        assert out.startswith("Unknown or disallowed subagent_type: 'Левый'")
        assert "Аналитик" in out

    async def test_no_executor(self):
        out = await execute_subagent_tool(
            {"subagent_type": "Аналитик", "prompt": "x"}, ctx=_ctx(None), config=_cfg()
        )
        assert out == "Subagent executor is not configured."

    async def test_success_stores_answer_and_passes_context(self):
        seen = {}

        async def ex(**kw):
            seen.update(kw)
            return SubagentOutcome("ответ", {"hits": [{"file": "a"}]})

        ctx = _ctx(ex, inline_attachments=["f"], user_question="Q", depth=1, remaining_steps=7)
        call = SubagentCall(reserved_no=4)
        out = await execute_subagent_tool(
            {"subagent_type": "Юрист", "prompt": "проверь"}, ctx=ctx, config=_cfg(), call=call
        )
        assert out == "ответ"
        assert call.answer_no == 4 and call.document_search == {"hits": [{"file": "a"}]}
        assert ctx.answers[4]["text"] == "ответ"
        assert seen["target_agent_id"] == 3 and seen["depth"] == 2
        assert seen["remaining_steps"] == 7 and seen["inline_attachments"] == ["f"]
        assert seen["user_question"] == "Q" and seen["prior_answers"] is None
        assert ctx.running_calls == {}

    async def test_non_string_result_is_stringified_and_empty_has_no_number(self):
        async def ex(**kw):
            return None

        call = SubagentCall()
        out = await execute_subagent_tool(
            {"subagent_type": "Аналитик", "prompt": "x"}, ctx=_ctx(ex), config=_cfg(), call=call
        )
        assert out == "" and call.answer_no is None

    async def test_repeated_call_is_not_run_again(self):
        n = {"c": 0}

        async def ex(**kw):
            n["c"] += 1
            return "первый"

        ctx = _ctx(ex)
        args = {"subagent_type": "Аналитик", "prompt": "Сделай   ОТЧЁТ"}
        await execute_subagent_tool(args, ctx=ctx, config=_cfg())
        call = SubagentCall()
        out = await execute_subagent_tool(
            {"subagent_type": "Аналитик", "prompt": "сделай отчёт"}, ctx=ctx, config=_cfg(), call=call
        )
        assert n["c"] == 1
        assert "уже ответил" in out and out.endswith("первый")
        assert call.answer_no == 1

    async def test_failed_call_is_not_remembered(self):
        state = {"fail": True}

        async def ex(**kw):
            if state["fail"]:
                raise RuntimeError("boom")
            return "ок"

        ctx = _ctx(ex)
        args = {"subagent_type": "Аналитик", "prompt": "x"}
        call = SubagentCall(reserved_no=1)
        with pytest.raises(RuntimeError):
            await execute_subagent_tool(args, ctx=ctx, config=_cfg(), call=call)
        assert call.document_search is None and ctx.done_calls == {} and ctx.running_calls == {}
        state["fail"] = False
        assert await execute_subagent_tool(args, ctx=ctx, config=_cfg()) == "ок"

    async def test_parallel_identical_calls_run_once(self):
        gate = asyncio.Event()
        n = {"c": 0}

        async def ex(**kw):
            n["c"] += 1
            await gate.wait()
            return "общий"

        ctx = _ctx(ex)
        args = {"subagent_type": "Аналитик", "prompt": "x"}
        t1 = asyncio.create_task(execute_subagent_tool(args, ctx=ctx, config=_cfg(), call=SubagentCall(reserved_no=1)))
        await asyncio.sleep(0)
        t2 = asyncio.create_task(execute_subagent_tool(args, ctx=ctx, config=_cfg(), call=SubagentCall(reserved_no=2)))
        await asyncio.sleep(0)
        gate.set()
        r1, r2 = await asyncio.gather(t1, t2)
        assert n["c"] == 1 and r1 == "общий" and "уже ответил" in r2

    async def test_marks_in_task_are_replaced_by_prior_answers(self):
        seen = {}

        async def ex(**kw):
            seen.update(kw)
            return "итог"

        ctx = _ctx(ex)
        ctx.answers[1] = {"name": "Аналитик", "text": "цифры 42"}
        await execute_subagent_tool(
            {"subagent_type": "Юрист", "prompt": "Сведи [[ОТВЕТ 1]]"}, ctx=ctx, config=_cfg()
        )
        assert "[[ОТВЕТ 1]]" not in seen["prompt"]
        assert "цифры 42" in seen["prior_answers"]


class TestExecuteNative:
    async def test_foreign_server(self):
        out = await execute_native_tool(_tool(server="mcp"), {})
        assert out == "Unknown native tool server: mcp"

    async def test_unknown_tool(self):
        assert await execute_native_tool(_tool("zzz"), {}) == "Unknown native tool: zzz"

    async def test_subagents_disabled(self):
        out = await execute_native_tool(_tool(), {"prompt": "x"})
        assert out == "Subagents are not enabled for this run."

    async def test_non_dict_arguments_become_empty(self):
        out = await execute_native_tool(_tool(), "bad", subagent_ctx=_ctx(), subagent_config=_cfg())
        assert out == "Subagent prompt is required."


class TestBuildParentRunContext:
    def test_no_profile(self):
        assert parent.build_parent_run_context(None, user={}, user_id=None, enable_thinking=False) == (None, None)

    def test_disabled_subagents_have_no_context(self):
        cfg, ctx = parent.build_parent_run_context(
            {"agent_id": 1}, user={}, user_id="u", enable_thinking=False
        )
        assert cfg is not None and cfg.enabled is False and ctx is None

    def test_enabled_context(self):
        profile = {"agent_id": 5, "recursion_limit": 10, "subagents": {"enabled": True, "agent_ids": [2]}}
        marker = lambda **k: None  # noqa: E731
        cfg, ctx = parent.build_parent_run_context(
            profile, user={"user_id": "u"}, user_id="u", enable_thinking=True,
            inline_attachments=["a"], user_question="Q", executor=marker,
        )
        assert cfg.enabled and ctx.parent_agent_id == 5 and ctx.remaining_steps == 9
        assert ctx.executor is marker and ctx.inline_attachments == ["a"] and ctx.user_question == "Q"
        assert ctx.parent_profile == profile and ctx.parent_profile is not profile

    async def test_default_executor_runs_isolated_subagent(self, monkeypatch):
        captured = {}

        async def fake_run(**kw):
            captured.update(kw)
            return "ok"

        monkeypatch.setattr("backend.agents.subagents.runner.run_isolated_subagent", fake_run)
        profile = {"agent_id": 5, "subagents": {"enabled": True, "agent_ids": [2]}}
        _, ctx = parent.build_parent_run_context(
            profile, user={"user_id": "u"}, user_id="u", enable_thinking=True
        )
        out = await ctx.executor(target_agent_id=2, prompt="p", parent_profile=profile)
        assert out == "ok"
        assert captured["target_agent_id"] == 2 and captured["enable_thinking"] is True
        assert captured["depth"] == 0 and captured["remaining_steps"] == ctx.remaining_steps
        assert captured["user_id"] == "u"


class TestPrepareParentTools:
    async def test_builds_tool_and_remembers_names(self, monkeypatch):
        async def names(ids, user_id, snapshot_names):
            assert ids == [2] and user_id == "u" and snapshot_names == {2: "снимок"}
            return {2: "Аналитик"}

        monkeypatch.setattr(parent, "load_subagent_agent_names", names)
        profile = {"agent_id": 1, "name": "Парент", "subagents": {"enabled": True, "agent_ids": [2], "agent_names": {"2": "снимок"}}}
        ctx = _ctx()
        ctx.agent_names = {9: "старый"}
        tools, cfg = await parent.prepare_parent_tools(
            agent_profile=profile, sub_cfg=None, sub_ctx=ctx, native_tools=None, user_id="u"
        )
        assert [t.name for t in tools] == [SUBAGENT_TOOL_NAME] and cfg.enabled
        assert ctx.agent_names == {9: "старый", 2: "Аналитик", 1: "Парент"}

    async def test_ready_native_tools_are_kept(self):
        existing = [_tool()]
        tools, cfg = await parent.prepare_parent_tools(
            agent_profile={"agent_id": 1, "subagents": {"enabled": True, "agent_ids": [2]}},
            sub_cfg=_cfg(), sub_ctx=None, native_tools=existing, user_id="u",
        )
        assert tools == existing

    async def test_no_profile(self):
        tools, cfg = await parent.prepare_parent_tools(
            agent_profile=None, sub_cfg=None, sub_ctx=None, native_tools=None, user_id=None
        )
        assert tools == [] and cfg is None


class TestParentRules:
    def test_rules_added_only_with_subagent_tool(self):
        assert parent.with_parent_rules("база", [_tool("other")]) == "база"
        out = parent.with_parent_rules("база", [_tool()])
        assert out.startswith("база\n\n") and len(out) > 10
        assert parent.with_parent_rules(None, [_tool()])


class _Result:
    def __init__(self, content, calls=1, iters=1, msgs=None):
        self.content, self.tool_calls_executed, self.iterations = content, calls, iters
        self.new_messages = msgs or []


class TestFinishParentResult:
    async def test_none(self):
        assert await parent.finish_parent_result(None, None) is None

    async def test_marks_are_expanded(self):
        ctx = _ctx()
        ctx.answers[1] = {"name": "Аналитик", "text": "ПОЛНЫЙ ОТВЕТ"}
        res = await parent.finish_parent_result(_Result("Итог:\n[[ОТВЕТ 1]]"), ctx)
        assert "ПОЛНЫЙ ОТВЕТ" in res.content and "[[ОТВЕТ" not in res.content

    async def test_orphan_marks_without_rerun_are_dropped(self):
        res = await parent.finish_parent_result(_Result("текст [[ОТВЕТ 5]]"), _ctx())
        assert res.content == "текст"

    async def test_only_orphan_marks_give_fallback(self):
        res = await parent.finish_parent_result(_Result("[[ОТВЕТ 5]]"), _ctx())
        assert res.content == parent.ORPHAN_MARK_FALLBACK

    async def test_orphans_trigger_one_rerun_with_note(self):
        seen = {}

        async def rerun(extra):
            seen["extra"] = extra
            return _Result("без меток", calls=2, iters=3, msgs=[{"role": "tool", "content": "t"}])

        first = _Result("[[ОТВЕТ 5]]", calls=1, iters=1, msgs=[{"role": "assistant", "content": "m"}])
        res = await parent.finish_parent_result(first, _ctx(), rerun=rerun)
        assert res.content == "без меток"
        assert res.tool_calls_executed == 3 and res.iterations == 4
        assert seen["extra"][-1]["role"] == "user" and "[[ОТВЕТ 5]]" in seen["extra"][-1]["content"]
        assert seen["extra"][-2] == {"role": "assistant", "content": "[[ОТВЕТ 5]]"}
        assert res.new_messages[0] == {"role": "assistant", "content": "m"}

    async def test_rerun_returning_none_keeps_result(self):
        async def rerun(extra):
            return None

        res = await parent.finish_parent_result(_Result("ok"), _ctx(), rerun=None)
        assert res.content == "ok"
        res = await parent.finish_parent_result(_Result("[[ОТВЕТ 2]] a"), _ctx(), rerun=rerun)
        assert res.content == "a"

    async def test_missing_plan_steps_trigger_rerun(self):
        ctx = _ctx()
        ctx.plan = [{"do": "посчитать", "subagent": "Аналитик", "target_id": 2, "uses": [], "check": ""}]
        notes = []

        async def rerun(extra):
            notes.append(extra[-1]["content"])
            return _Result("доделал")

        res = await parent.finish_parent_result(_Result("рано"), ctx, rerun=rerun)
        assert res.content == "доделал" and "посчитать" in notes[0]


class TestPlanForParent:
    async def test_guards(self, monkeypatch):
        tools = [_tool()]
        ctx, cfg = _ctx(), _cfg()
        kw = dict(messages=[], model_path="m", native_tools=tools)
        assert await parent.plan_for_parent(sub_ctx=None, sub_cfg=cfg, **kw) == ""
        assert await parent.plan_for_parent(sub_ctx=ctx, sub_cfg=None, **kw) == ""
        deep = _ctx(depth=1)
        assert await parent.plan_for_parent(sub_ctx=deep, sub_cfg=cfg, **kw) == ""
        assert await parent.plan_for_parent(sub_ctx=ctx, sub_cfg=cfg, messages=[], model_path="m", native_tools=[]) == ""
        monkeypatch.setenv("SUBAGENT_PLAN", "0")
        assert await parent.plan_for_parent(sub_ctx=ctx, sub_cfg=cfg, **kw) == ""

    async def test_plan_is_saved_into_context(self, monkeypatch):
        plan = [{"do": "шаг", "subagent": "Аналитик", "target_id": 2, "uses": [], "check": ""}]

        async def fake_plan(**kw):
            return plan

        monkeypatch.setattr(parent, "make_subagent_plan", fake_plan)
        ctx = _ctx()
        block = await parent.plan_for_parent(
            messages=[], model_path="m", native_tools=[_tool()], sub_ctx=ctx, sub_cfg=_cfg()
        )
        assert ctx.plan == plan and "ПЛАН" in block and "шаг" in block

    async def test_with_parent_plan_appends_or_keeps(self, monkeypatch):
        async def fake_plan_for_parent(**kw):
            return fake_plan_for_parent.block

        monkeypatch.setattr(parent, "plan_for_parent", fake_plan_for_parent)
        kw = dict(user_message="q", history=None, model_path="m", native_tools=[], sub_ctx=None, sub_cfg=None)
        fake_plan_for_parent.block = ""
        assert await parent.with_parent_plan("сис", **kw) == "сис"
        fake_plan_for_parent.block = "ПЛАН"
        assert await parent.with_parent_plan("сис", **kw) == "сис\n\nПЛАН"
        assert await parent.with_parent_plan(None, **kw) == "ПЛАН"


class TestNativeCalls:
    def test_call_args_only_for_subagent_tool(self):
        args = {"subagent_type": "Аналитик", "prompt": "x"}
        assert parent.subagent_call_args(_tool("other"), args, None, None) is args
        assert parent.subagent_call_args(_tool(server="mcp"), args, None, None) is args
        out = parent.subagent_call_args(_tool(), args, _ctx(), _cfg())
        assert out is not args and out["prompt"] == "x"

    def test_new_call_reserves_number(self):
        ctx = _ctx()
        assert parent.new_subagent_call(_tool("other"), ctx) is None
        assert parent.new_subagent_call(None, ctx) is None
        assert parent.new_subagent_call(_tool(), None) is None
        assert parent.new_subagent_call(_tool(), ctx).reserved_no == 1
        assert parent.new_subagent_call(_tool(), ctx).reserved_no == 2

    async def test_run_native_tool_call_labels_for_model_only(self):
        async def ex(**kw):
            return SubagentOutcome("ответ", {"hits": []})

        ctx = _ctx(ex)
        call = parent.new_subagent_call(_tool(), ctx)
        for_model, for_ui, doc = await parent.run_native_tool_call(
            _tool(), {"subagent_type": "Аналитик", "prompt": "x"},
            subagent_ctx=ctx, subagent_config=_cfg(), call=call,
        )
        assert for_ui == "ответ" and doc == {"hits": []}
        assert "[[ОТВЕТ 1]]" in for_model and for_model.endswith("ответ")

    async def test_run_native_tool_call_adds_plan_progress(self):
        async def ex(**kw):
            return "ответ"

        ctx = _ctx(ex)
        ctx.plan = [
            {"do": "а", "subagent": "Аналитик", "target_id": 2, "uses": [], "check": ""},
            {"do": "б", "subagent": "Юрист", "target_id": 3, "uses": [], "check": ""},
        ]
        for_model, _, _ = await parent.run_native_tool_call(
            _tool(), {"subagent_type": "Аналитик", "prompt": "x"},
            subagent_ctx=ctx, subagent_config=_cfg(),
        )
        assert "[План: выполнено" in for_model

    async def test_run_native_tool_call_unknown_tool(self):
        for_model, for_ui, doc = await parent.run_native_tool_call(
            _tool("zzz"), {}, subagent_ctx=None, subagent_config=None
        )
        assert for_model == for_ui == "Unknown native tool: zzz" and doc is None


class TestTrace:
    def test_trace_files_counts_hits(self):
        t = {"hits": [{"file": "a"}, {"file": "a"}, {"file": "b"}, {"x": 1}, "s"]}
        assert trace._trace_files(t) == {"a": 2, "b": 1}
        assert trace._trace_files(None) == {}

    def test_scope_part(self):
        assert trace._scope_log_part({}) == "нет"
        part = trace._scope_log_part({"_scope_info": {"rule": "r", "files": ["f"], "missing": ["m"]}})
        assert "«r»" in part and "['f']" in part and "['m']" in part

    def _log(self, caplog, **over):
        caplog.set_level(logging.DEBUG, logger=trace.log.name)
        kw = dict(
            target_agent_id=2,
            child_profile={"name": "Дитя", "system_prompt": "КАРТОЧКА", "file_search_enabled": True, "kb_document_ids": [1, 2]},
            parent_profile={"agent_id": 1},
            depth=1, task="задача\nстрока", question_added=True, shared_kb_ids=[5, 6],
            shared_trace={"hits": [{"file": "s.pdf"}]}, child_kb_trace={"hits": [{"file": "o.pdf"}]},
            system_prompt="КАРТОЧКА ... ДОСТУПНЫЕ ФАЙЛЫ", prompt="p",
        )
        kw.update(over)
        trace._log_subagent_trace(**kw)
        return caplog.text

    def test_full_trace(self, caplog):
        text = self._log(caplog)
        assert "[SUBAGENT-TRACE]" in text and "«Дитя»" in text
        assert "в системном промпте=да" in text and "перечень файлов=да" in text
        assert "файлов в базе=2" in text and "{'s.pdf': 1}" in text
        assert "файлов=2 фрагментов=1" in text

    def test_card_missing_from_system_prompt(self, caplog):
        assert "в системном промпте=НЕТ" in self._log(caplog, system_prompt="другое")

    def test_empty_card_and_no_sharing(self, caplog):
        text = self._log(
            caplog, shared_kb_ids=[], shared_trace=None, child_kb_trace=None,
            child_profile={"file_search_enabled": False},
        )
        assert "карточка пустая" in text
        assert "у главного не включён общий RAG" in text
        assert "поиск по файлам выключен" in text

    def test_refused_shared_rag(self, caplog):
        text = self._log(caplog, shared_kb_ids=[], child_profile={"_parent_shared_rag_refused": 3})
        assert "выключено «Смотреть общий RAG родителя»" in text and "3 док." in text

    def test_failure_is_swallowed(self, caplog):
        caplog.set_level(logging.DEBUG)
        trace._log_subagent_trace(
            target_agent_id=1, child_profile=None, parent_profile={}, depth=0, task="",
            question_added=False, shared_kb_ids=[], shared_trace=None,
            child_kb_trace=None, system_prompt="", prompt="",
        )
        assert "не удалось собрать" in caplog.text
