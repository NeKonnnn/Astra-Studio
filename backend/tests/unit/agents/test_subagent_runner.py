"""run_isolated_subagent: сборка контекста ребёнка и запуск, на фейках зависимостей."""

from types import SimpleNamespace

import pytest

from backend.agents.shared_rag import SHARED_RAG_KEY
from backend.agents.subagents import runner
from backend.agents.subagents.answers import SUBAGENT_CHILD_NOTE


class Env:
    def __init__(self):
        self.profile = {
            "agent_id": 2,
            "name": "Дитя",
            "model_path": "m/child",
            "system_prompt": "КАРТОЧКА",
            "max_tokens": 100,
            "temperature": 0.3,
        }
        self.resolved = []
        self.shared_ctx = ("", None)
        self.shared_calls = []
        self.kb = None  # (prompt, system, trace) override
        self.plugin = None
        self.scope = None
        self.mcp_result = None
        self.mcp_calls = []
        self.ask_calls = []
        self.ask_reply = "ответ модели"
        self.tool_ids = []
        self.names = {}
        self.events = []


@pytest.fixture
def env(monkeypatch):
    e = Env()

    async def resolve(agent_id, user_id, user=None, skip_access_check=False):
        e.resolved.append((agent_id, user_id, skip_access_check))
        return dict(e.profile)

    async def resolve_scope(child_profile, shared_ids, agent_id):
        return e.scope

    async def shared(prompt, ids, profile=None):
        e.shared_calls.append((prompt, list(ids)))
        return e.shared_ctx

    async def kb(profile, prompt, system, agent_id):
        return e.kb if e.kb is not None else (prompt, system, None)

    async def skills(profile, prompt, system, user, agent_id):
        return prompt, system, []

    async def plugin(profile, prompt, system, attachments, agent_id, emit):
        return e.plugin if e.plugin is not None else (prompt, system, "")

    async def names(ids, user_id=None, snapshot_names=None):
        return dict(e.names)

    async def mcp(**kw):
        e.mcp_calls.append(kw)
        return e.mcp_result

    def ask_agent(prompt, **kw):
        e.ask_calls.append((prompt, kw))
        return e.ask_reply

    monkeypatch.setattr(runner, "_resolve_agent_chat_params", resolve)
    monkeypatch.setattr(runner, "_resolve_card_scope", resolve_scope)
    monkeypatch.setattr(runner, "_build_shared_rag_context", shared)
    monkeypatch.setattr(runner, "_apply_child_kb_context", kb)
    monkeypatch.setattr(runner, "_apply_child_skills_and_artifacts", skills)
    monkeypatch.setattr(runner, "_run_child_plugin", plugin)
    monkeypatch.setattr(runner, "load_subagent_agent_names", names)
    monkeypatch.setattr(runner, "agent_mcp_tool_ids", lambda p: list(e.tool_ids))
    monkeypatch.setattr("backend.mcp.chat_integration.maybe_run_mcp_agent", mcp)
    monkeypatch.setattr(
        "backend.mcp.chat_integration.build_mcp_context_from_user",
        lambda user, chat_id=None, message_id=None: SimpleNamespace(user=user),
    )
    monkeypatch.setattr("backend.app_state.ask_agent", ask_agent)
    return e


async def _run(**over):
    kw = dict(
        target_agent_id=2,
        prompt="задача",
        parent_profile={"agent_id": 1},
        user={"user_id": "u"},
        user_id="u",
        depth=1,
        remaining_steps=10,
    )
    kw.update(over)
    return await runner.run_isolated_subagent(**kw)


class TestParentDeclares:
    def test_listed_in_card(self):
        p = {"subagents": {"enabled": True, "agent_ids": [2]}}
        assert runner._parent_declares_subagent(p, 2) is True
        assert runner._parent_declares_subagent(p, 9) is False

    def test_self_spawn(self):
        p = {"agent_id": 1, "subagents": {"enabled": True, "allow_self": True}}
        assert runner._parent_declares_subagent(p, 1) is True
        p["subagents"]["allow_self"] = False
        assert runner._parent_declares_subagent(p, 1) is False

    def test_garbage_ids(self):
        assert runner._parent_declares_subagent({}, "abc") is False


class TestMaxTokensFloor:
    @pytest.mark.parametrize("raw,expected", [(None, 4096), ("8000", 8000), ("10", 256), ("abc", 4096)])
    def test_floor(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("SUBAGENT_MAX_TOKENS_FLOOR", raising=False)
        else:
            monkeypatch.setenv("SUBAGENT_MAX_TOKENS_FLOOR", raw)
        assert runner._subagent_max_tokens_floor() == expected


class TestUnavailable:
    async def test_not_found(self, env):
        env.profile = {}
        out = await _run()
        assert "not available" in out.text and out.document_search is None

    async def test_no_model(self, env):
        env.profile["model_path"] = ""
        out = await _run()
        assert out.text == "Subagent 2: model is not configured."

    async def test_trust_flag_follows_parent_card(self, env):
        env.ask_reply = "x"
        await _run(parent_profile={"agent_id": 1, "subagents": {"enabled": True, "agent_ids": [2]}})
        await _run(parent_profile={"agent_id": 1})
        assert [r[2] for r in env.resolved] == [True, False]


class TestPlainCall:
    async def test_asks_model_with_child_note_and_question(self, env):
        out = await _run(user_question="Что с выручкой?")
        assert out.text == "ответ модели"
        prompt, kw = env.ask_calls[0]
        assert prompt.startswith("задача") and "Что с выручкой?" in prompt
        assert kw["model_path"] == "m/child" and kw["temperature"] == 0.3
        assert kw["system_prompt"] == f"КАРТОЧКА\n\n{SUBAGENT_CHILD_NOTE}"
        assert kw["max_tokens"] == 4096  # пол поднимает 100 карточки
        assert kw["history"] == []

    async def test_no_note_without_parent_agent(self, env):
        await _run(parent_profile={})
        assert env.ask_calls[0][1]["system_prompt"] == "КАРТОЧКА"

    async def test_empty_system_prompt_gets_only_note(self, env):
        env.profile["system_prompt"] = ""
        await _run()
        assert env.ask_calls[0][1]["system_prompt"] == SUBAGENT_CHILD_NOTE
        await _run(parent_profile={})
        assert env.ask_calls[1][1]["system_prompt"] is None

    async def test_history_keeps_last_six_non_empty(self, env):
        history = [{"role": "user", "content": f"m{i}"} for i in range(8)]
        history[-1] = {"role": "assistant", "content": ""}
        await _run(history=history)
        got = env.ask_calls[0][1]["history"]
        assert [m["content"] for m in got] == ["m2", "m3", "m4", "m5", "m6"]

    async def test_empty_reply_gets_placeholder(self, env):
        env.ask_reply = "   "
        out = await _run()
        assert out.text == "Subagent finished without a response."

    async def test_prior_answers_and_scope_note_come_after_search(self, env, monkeypatch):
        monkeypatch.setattr(runner, "_card_scope_note", lambda p: "ТОЛЬКО a.pdf")
        await _run(prior_answers="ОТВЕТЫ")
        prompt = env.ask_calls[0][0]
        assert prompt.startswith("ТОЛЬКО a.pdf\n\nзадача") and prompt.endswith("\n\nОТВЕТЫ")

    async def test_plugin_artifact_is_appended(self, env, monkeypatch):
        env.plugin = ("p", "s", "ARTIFACT")
        monkeypatch.setattr(
            runner, "_with_child_plugin_artifact", lambda text, art: f"{text}|{art}"
        )
        out = await _run()
        assert out.text == "ответ модели|ARTIFACT"


class TestSharedRag:
    PARENT = {
        "agent_id": 1, "shared_chain_rag": True, "file_search_enabled": True,
        "kb_document_ids": [10, 11],
    }

    async def test_shared_context_goes_to_system_prompt(self, env):
        trace = {"hits": [{"file": "a.pdf", "text": "t"}], "sourceFiles": ["a.pdf"]}
        env.shared_ctx = ("ФРАГМЕНТЫ", trace)
        out = await _run(parent_profile=self.PARENT)
        assert env.shared_calls == [("задача", [10, 11])]
        assert "КАРТОЧКА\n\nФРАГМЕНТЫ" in env.ask_calls[0][1]["system_prompt"]
        assert out.document_search["hits"][0]["file"] == "a.pdf"

    async def test_empty_system_prompt_is_replaced_by_shared_context(self, env):
        env.profile["system_prompt"] = ""
        env.shared_ctx = ("ФРАГМЕНТЫ", None)
        await _run(parent_profile=self.PARENT)
        assert env.ask_calls[0][1]["system_prompt"].startswith("ФРАГМЕНТЫ")

    async def test_child_refuses_parent_rag(self, env):
        env.profile["use_parent_shared_rag"] = False
        await _run(parent_profile=self.PARENT)
        assert env.shared_calls == []

    async def test_scope_limits_shared_search(self, env):
        env.scope = {11}
        await _run(parent_profile=self.PARENT)
        assert env.shared_calls == [("задача", [11])]

    async def test_scope_excluding_everything_skips_search(self, env):
        env.scope = set()
        out = await _run(parent_profile=self.PARENT)
        assert env.shared_calls == [] and out.document_search is None

    async def test_child_kb_trace_is_merged(self, env):
        env.kb = ("задача+kb", "СИС", {"hits": [{"file": "own.pdf"}]})
        out = await _run()
        assert out.document_search["hits"] == [{"file": "own.pdf"}]
        assert env.ask_calls[0][0].startswith("задача+kb")


class TestToolLoop:
    async def test_mcp_result_is_returned(self, env):
        env.tool_ids = ["t1"]
        env.mcp_result = SimpleNamespace(content="  итог цикла ")
        out = await _run(remaining_steps=5)
        assert out.text == "итог цикла" and env.ask_calls == []
        call = env.mcp_calls[0]
        assert call["tool_ids"] == ["t1"] and call["max_iterations"] == 5
        assert call["model_path"] == "m/child" and call["max_tokens"] == 4096
        assert call["native_tools"] == [] and call["subagent_ctx"].remaining_steps == 4

    async def test_child_limit_is_bounded_by_card(self, env):
        env.tool_ids = ["t1"]
        env.profile["recursion_limit"] = 3
        env.mcp_result = SimpleNamespace(content="x")
        await _run(remaining_steps=30)
        assert env.mcp_calls[0]["max_iterations"] == 3

    async def test_child_limit_never_below_one(self, env):
        env.tool_ids = ["t1"]
        env.mcp_result = SimpleNamespace(content="x")
        await _run(remaining_steps=0)
        assert env.mcp_calls[0]["max_iterations"] == 1

    async def test_empty_loop_content(self, env):
        env.tool_ids = ["t1"]
        env.mcp_result = SimpleNamespace(content="")
        out = await _run()
        assert out.text == "Subagent finished without a response."

    async def test_loop_not_started_falls_back_to_plain_call(self, env):
        env.tool_ids = ["t1"]
        env.mcp_result = None
        out = await _run()
        assert out.text == "ответ модели" and len(env.ask_calls) == 1

    async def test_own_subagents_give_native_tool_and_nested_executor(self, env, monkeypatch):
        env.profile["subagents"] = {"enabled": True, "agent_ids": [3], "allow_self": False}
        env.names = {3: "Внук"}
        env.mcp_result = SimpleNamespace(content="готово")
        out = await _run(user_question="Q", inline_attachments=["f"])
        assert out.text == "готово"
        call = env.mcp_calls[0]
        assert [t.name for t in call["native_tools"]] == ["subagent"]
        ctx = call["subagent_ctx"]
        assert ctx.parent_agent_id == 2 and ctx.agent_names[3] == "Внук" and ctx.agent_names[2] == "Дитя"
        assert ctx.user_question == "Q" and ctx.inline_attachments == ["f"]

        # вложенный запуск идёт через тот же раннер с глубиной и остатком шагов
        seen = {}

        async def fake_run(**kw):
            seen.update(kw)
            return "внук"

        monkeypatch.setattr(runner, "run_isolated_subagent", fake_run)
        assert await ctx.executor(
            target_agent_id=3, prompt="p", parent_profile={"agent_id": 2}, depth=2
        ) == "внук"
        assert seen["target_agent_id"] == 3 and seen["depth"] == 2
        assert seen["remaining_steps"] == ctx.remaining_steps
        assert seen["user_question"] == "Q" and seen["inline_attachments"] == ["f"]
        assert seen["parent_profile"] == {"agent_id": 2}

    async def test_shared_rag_key_goes_down_the_branch(self, env):
        env.tool_ids = ["t1"]
        env.mcp_result = SimpleNamespace(content="x")
        parent = {"agent_id": 1, "shared_chain_rag": True, "file_search_enabled": True, "kb_document_ids": [10]}
        await _run(parent_profile=parent)
        assert env.mcp_calls[0]["subagent_ctx"].parent_profile[SHARED_RAG_KEY] == [10]
