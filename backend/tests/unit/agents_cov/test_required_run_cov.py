"""agents/subagents/required: запуск обязательных субагентов (run_required_subagents / run_required_for_chat)."""

from types import SimpleNamespace

import pytest

from backend.agents.subagents import required
from backend.agents.subagents.settings import NATIVE_SERVER_ID, SUBAGENT_TOOL_NAME
from backend.agents.subagents.tool import SubagentOutcome

pytestmark = pytest.mark.unit


@pytest.fixture
def env(monkeypatch):
    """Подменяет резолв агентов, имена, запуск ребёнка и события MCP; пишет журнал вызовов."""
    st = SimpleNamespace(
        allowed=[10, 11], denied=[], resolve_calls=[], runs=[], starts=[], ends=[], results={},
        names={10: "Юрист - право", 11: "Финансист"},
    )

    async def resolve(tag_ids, *, user_id, exclude_id):
        st.resolve_calls.append((list(tag_ids), user_id, exclude_id))
        return list(st.allowed), list(st.denied)

    async def names(ids, *, user_id):
        return dict(st.names)

    async def run(**kw):
        st.runs.append(kw)
        res = st.results.get(kw["target_agent_id"], "ответ")
        if isinstance(res, Exception):
            raise res
        return res

    async def start(emit, **kw):
        st.starts.append(kw)

    async def end(emit, **kw):
        st.ends.append(kw)

    monkeypatch.setattr(required, "resolve_required_agent_ids", resolve)
    monkeypatch.setattr(required, "load_subagent_agent_names", names)
    monkeypatch.setattr("backend.agents.subagents.runner.run_isolated_subagent", run)
    monkeypatch.setattr("backend.mcp.events.emit_mcp_tool_start", start)
    monkeypatch.setattr("backend.mcp.events.emit_mcp_tool_end", end)
    return st


def profile(**sub):
    base = {"agent_id": 1, "subagents": {"enabled": True, "agent_ids": [10, 12], "required_tag_ids": [7]}}
    base["subagents"].update(sub)
    return base


async def call(env, prof, message="вопрос", mention=None, stopped=lambda: False, history=None):
    return await required.run_required_subagents(
        agent_profile=prof, user_message=message, history=history, user={"user_id": "u"}, user_id="u",
        enable_thinking=False, emit_event=None, stopped=stopped, mention_tag_ids=mention,
    )


class TestNothingToCall:
    async def test_no_tags_returns_input_untouched(self, env):
        prof = {"agent_id": 1, "subagents": {"enabled": True}}

        result = await call(env, prof)

        assert result == ("вопрос", prof, [], None) and env.resolve_calls == []

    async def test_card_tags_ignored_when_subagents_disabled(self, env):
        prof = profile(enabled=False)

        result = await call(env, prof)

        assert result[2] == [] and env.resolve_calls == []

    async def test_no_accessible_agents(self, env):
        env.allowed, env.denied = [], [10]

        result = await call(env, profile())

        assert result == ("вопрос", profile(), [], None)

    async def test_non_dict_profile_is_treated_as_empty(self, env):
        result = await call(env, None)

        assert result == ("вопрос", None, [], None)


class TestMentionTags:
    async def test_bad_and_duplicate_mentions_are_dropped(self, env):
        await call(env, profile(required_tag_ids=[7]), mention=["x", None, -3, 0, "7", 8, 8])

        assert env.resolve_calls[0][0] == [7, 8]  # 7 из карточки и из mention не дублируется


class TestSuccessfulRun:
    async def test_answers_are_embedded_into_parent_message(self, env):
        env.results = {10: "Ответ юриста", 11: SubagentOutcome(text="Ответ финансиста", document_search={"hits": 2})}

        message, loop_profile, called, direct = await call(env, profile(), history=[{"role": "user", "content": "раньше"}])

        assert called == [10, 11] and direct is None
        assert "AI (Юрист - право): Ответ юриста" in message and "AI (Финансист): Ответ финансиста" in message
        assert message.endswith("вопрос")
        # родителю остались только необработанные ребёнком агенты
        assert loop_profile["subagents"]["agent_ids"] == [12]
        run = next(r for r in env.runs if r["target_agent_id"] == 10)
        assert run["depth"] == 1 and run["history"] == [{"role": "user", "content": "раньше"}]
        assert run["parent_profile"]["agent_id"] == 1 and run["prompt"] == "вопрос"
        # события tool start/end с метками
        assert {s["call_id"] for s in env.starts} == {e["call_id"] for e in env.ends}
        start10 = next(s for s in env.starts if s["arguments"]["subagent_type"] == "agent_10")
        assert start10["server_id"] == NATIVE_SERVER_ID and start10["tool"] == SUBAGENT_TOOL_NAME
        assert start10["arguments"]["via"] == "card_required_tags" and start10["arguments"]["agent_name"] == "Юрист"
        end11 = next(e for e in env.ends if e["arguments"]["subagent_type"] == "agent_11")
        assert end11["success"] is True and end11["document_search"] == {"hits": 2}
        assert end11["result_preview"] == "Ответ финансиста" and end11["error"] is None

    async def test_required_only_disables_tool_for_parent(self, env):
        _, loop_profile, _, _ = await call(env, profile(required_only=True))

        assert loop_profile["subagents"]["enabled"] is False

    async def test_failed_child_is_reported_and_skipped_in_block(self, env):
        env.results = {10: RuntimeError("упал"), 11: "ок"}

        message, _, called, _ = await call(env, profile())

        assert called == [10, 11]
        assert "Юрист" not in message and "AI (Финансист): ок" in message
        end10 = next(e for e in env.ends if e["arguments"]["subagent_type"] == "agent_10")
        assert end10["success"] is False and end10["error"] == "Subagent error: упал"
        assert end10["result_preview"] is None and end10["document_search"] is None

    async def test_all_children_failed_keeps_message_unchanged(self, env):
        env.results = {10: RuntimeError("a"), 11: RuntimeError("b")}

        message, _, _, _ = await call(env, profile())

        assert message == "вопрос"

    async def test_stop_requested_skips_children(self, env):
        message, _, called, _ = await call(env, profile(), stopped=lambda: True)

        assert message == "вопрос" and env.runs == [] and called == [10, 11]

    async def test_none_outcome_becomes_empty_text(self, env):
        env.results = {10: None, 11: "есть"}

        message, _, _, _ = await call(env, profile())

        assert "AI (Финансист): есть" in message and "Юрист" not in message

    async def test_missing_name_falls_back_to_agent_id(self, env):
        env.names = {}

        message, _, _, _ = await call(env, profile())

        assert "AI (Agent 10): ответ" in message


class TestMentionOnlyWithoutParent:
    async def test_direct_answer_and_tool_disabled(self, env):
        env.allowed = [10]
        env.results = {10: "Прямой ответ"}

        message, loop_profile, called, direct = await call(env, {}, mention=[5])

        assert direct == "Прямой ответ" and called == [10]
        assert message.startswith("Ответы агентов по тегам")
        assert loop_profile is not None and loop_profile.get("subagents", {}).get("enabled", False) is False
        start = env.starts[0]
        assert start["arguments"]["via"] == "chat_hash_tag"

    async def test_mention_with_parent_does_not_produce_direct_answer(self, env):
        env.allowed = [10]

        _, loop_profile, _, direct = await call(env, profile(required_tag_ids=[]), mention=[5])

        assert direct is None
        assert loop_profile["subagents"]["enabled"] is True  # mentions не выключают tool у родителя


class TestRunRequiredForChat:
    async def test_collects_tags_and_forwards_payload(self, env, monkeypatch):
        captured = {}

        async def fake(**kw):
            captured.update(kw)
            return "msg2", {"p": 1}, [10], "direct"

        monkeypatch.setattr(required, "run_required_subagents", fake)
        data = {"message": "привет <#5|Юрист>", "tag_ids": [6], "inline_attachments": ["a"]}

        result = await required.run_required_for_chat(
            data=data, final_message="привет", agent_profile={"agent_id": 3}, history=[],
            current_user={"user_id": "u1"}, enable_thinking=True, emit_event=None, stopped=lambda: False,
        )

        assert result == ("msg2", {"p": 1}, [10], "direct")
        assert sorted(captured["mention_tag_ids"]) == [5, 6]
        assert captured["user_id"] == "u1" and captured["inline_attachments"] == ["a"]
        assert captured["user_message"] == "привет" and captured["enable_thinking"] is True

    async def test_called_agents_without_direct_answer(self, env, monkeypatch):
        async def fake(**kw):
            return "m", None, [10], None

        monkeypatch.setattr(required, "run_required_subagents", fake)

        result = await required.run_required_for_chat(
            data=None, final_message="x", agent_profile=None, history=None, current_user=None,
            enable_thinking=False, emit_event=None, stopped=lambda: False,
        )

        assert result == ("m", None, [10], None)

    async def test_nothing_called(self, env, monkeypatch):
        async def fake(**kw):
            assert kw["user_id"] is None and kw["inline_attachments"] is None and kw["mention_tag_ids"] == []
            return "m", None, [], None

        monkeypatch.setattr(required, "run_required_subagents", fake)

        result = await required.run_required_for_chat(
            data="не dict", final_message="x", agent_profile=None, history=None, current_user=None,
            enable_thinking=False, emit_event=None, stopped=lambda: False,
        )

        assert result == ("m", None, [], None)
