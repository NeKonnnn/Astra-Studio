"""agents/*: редкие ветки (цепочка, параметры, trace, имена, план, артефакты, контекст ребёнка, scope)."""

import sys
from types import SimpleNamespace

import pytest

from backend.agents import step_debug
from backend.agents.chain import profiles as chain_profiles
from backend.agents.chain import settings as chain_settings
from backend.agents.subagents import (
    answers,
    artifacts,
    child_context as cc,
    names,
    plan,
    runner,
    scope,
    trace,
)
from backend.agents.subagents import settings as sub_settings

pytestmark = pytest.mark.unit


# ───────────────────────────── chain ─────────────────────────────


class TestChainProfiles:
    async def test_primary_without_next_agents_stays_alone(self):
        chain = await chain_profiles.resolve_agent_chain(3, {"name": "main", "agent_ids": []}, "u")

        assert chain == [{"name": "main", "agent_ids": [], "agent_id": 3}]

    def test_non_first_step_survives_mention_stripping_failure(self, monkeypatch):
        def boom(text):
            raise RuntimeError("strip failed")

        monkeypatch.setattr("backend.services.tag_mentions.strip_tag_mentions", boom)
        monkeypatch.setattr("backend.realtime.helpers.agent_mcp_tool_ids", lambda p: ["t1"])
        monkeypatch.setattr("backend.realtime.helpers.agent_plugin_ids", lambda p: [])

        data = chain_profiles.prepare_step_socket_data(
            {"message": "привет <#1|X>", "tag_ids": [1], "__plugin_ids__": ["old"]}, {}, is_first=False
        )

        assert data["message"] == "привет <#1|X>"  # текст остался как был
        assert "tag_ids" not in data and "__plugin_ids__" not in data and data["tool_ids"] == ["t1"]

    def test_non_first_step_gets_plugins_of_its_card(self, monkeypatch):
        monkeypatch.setattr("backend.realtime.helpers.agent_mcp_tool_ids", lambda p: [])
        monkeypatch.setattr("backend.realtime.helpers.agent_plugin_ids", lambda p: ["cashflow"])

        data = chain_profiles.prepare_step_socket_data({"message": 5}, {}, is_first=False)

        assert data["__plugin_ids__"] == ["cashflow"] and data["message"] == 5


class TestChainSettings:
    def test_bad_exclude_id_is_ignored(self):
        assert chain_settings.parse_agent_ids([1, 2], exclude_id="не число", max_agents=5) == [1, 2]


# ───────────────────────────── step_debug / settings ─────────────────────────────


def test_args_preview_falls_back_to_str_on_type_error(monkeypatch):
    def boom(*a, **k):
        raise TypeError("не сериализуется")

    monkeypatch.setattr(step_debug.json, "dumps", boom)

    assert step_debug._args_preview({"a": 1}) == "{'a': 1}"


class TestEnvInt:
    def test_invalid_value_falls_back_to_default_within_bounds(self, monkeypatch):
        monkeypatch.setenv("X_TEST_INT", "abc")

        assert sub_settings._env_int("X_TEST_INT", 50, lo=1, hi=10) == 10


# ───────────────────────────── trace ─────────────────────────────


class FakeLog:
    """Перехватчик логгера модуля (get_logger не пропускает записи в caplog)."""

    def __init__(self):
        self.lines = []

    def _add(self, fmt, *args, **kw):
        self.lines.append(str(fmt) % args if args else str(fmt))

    info = debug = warning = exception = _add


def test_trace_survives_missing_top_k(monkeypatch):
    def boom(scope_name):
        raise RuntimeError("нет настроек")

    monkeypatch.setattr("backend.app_state.get_rag_chat_top_k", boom, raising=False)
    fake = FakeLog()
    monkeypatch.setattr(trace, "log", fake)

    trace._log_subagent_trace(
        target_agent_id=2, child_profile={"system_prompt": "Карточка"}, parent_profile={}, depth=1,
        task="задача", question_added=True, shared_kb_ids=[1], shared_trace=None, child_kb_trace=None,
        system_prompt="Карточка", prompt="вопрос",
    )

    assert any("top_k=?" in line for line in fake.lines)


# ───────────────────────────── runner ─────────────────────────────


class TestParentDeclares:
    def test_self_spawn_by_id(self):
        profile = {"agent_id": 5, "subagents": {"enabled": True, "agent_ids": [], "allow_self": True}}

        assert runner._parent_declares_subagent(profile, 5) is True


# ───────────────────────────── artifacts ─────────────────────────────


def test_carry_artifacts_swallows_internal_errors(monkeypatch):
    def boom(result):
        raise RuntimeError("плохой результат")

    monkeypatch.setattr(artifacts, "subagent_artifact_blocks", boom)
    events = [{"type": "mcp_tool_end", "tool": "subagent", "result": "x"}]

    assert artifacts.carry_subagent_artifacts("ответ родителя", events) == "ответ родителя"


# ───────────────────────────── answers ─────────────────────────────


class TestHeadingWords:
    def test_blank_and_too_long_lines_are_not_headings(self):
        assert answers._heading_words("   ") is None
        assert answers._heading_words("а" * 300) is None

    def test_only_markers_is_not_heading(self):
        assert answers._heading_words("###") is None

    def test_plain_line_ending_with_punctuation_is_not_heading(self):
        assert answers._heading_words("Просто предложение.") is None

    def test_plain_overlong_line_is_not_heading(self):
        assert answers._heading_words("слово " * 30) is None

    def test_marked_heading_gives_numbers_and_stems(self):
        nums, stems = answers._heading_words("## Раздел 2 Финансы")

        assert nums == {"2"} and {"разде", "финан"} <= stems


# ───────────────────────────── names ─────────────────────────────


class FakeAgent:
    def __init__(self, ids, names_snapshot=None):
        sub = {"agent_ids": ids}
        if names_snapshot is not None:
            sub["agent_names"] = names_snapshot
        self.config = {"subagents": sub}


@pytest.fixture
def repo(monkeypatch):
    state = SimpleNamespace(repo=None)
    monkeypatch.setattr("backend.database.init_db.get_agent_repository", lambda: state.repo)
    return state


class TestEnrichNames:
    async def test_no_repository(self, repo):
        agent = FakeAgent([1])
        repo.repo = None

        await names.enrich_agents_subagent_names([agent])

        assert agent.config == {"subagents": {"agent_ids": [1]}}

    async def test_repository_without_names_map(self, repo):
        repo.repo = object()
        agent = FakeAgent([1])

        await names.enrich_agents_subagent_names([agent])

        assert "agent_names" not in agent.config["subagents"]

    async def test_nothing_to_look_up(self, repo):
        class Repo:
            async def get_agent_names_map(self, ids):
                pytest.fail("не должен вызываться")

        repo.repo = Repo()
        agents = [FakeAgent([]), SimpleNamespace(config=None), SimpleNamespace(config={"subagents": "x"})]

        await names.enrich_agents_subagent_names(agents)

    async def test_agent_without_any_known_name_is_left_untouched(self, repo):
        class Repo:
            async def get_agent_names_map(self, ids):
                return {}

        repo.repo = Repo()
        agent = FakeAgent([5, "bad", -1])
        before = agent.config

        await names.enrich_agents_subagent_names([agent])

        assert agent.config is before and "agent_names" not in agent.config["subagents"]

    async def test_names_are_merged_from_db_and_snapshot(self, repo):
        class Repo:
            async def get_agent_names_map(self, ids):
                return {5: "Живое имя"}

        repo.repo = Repo()
        agent = FakeAgent([5, 6], {"6": "Из снимка"})

        await names.enrich_agents_subagent_names([agent])

        assert agent.config["subagents"]["agent_names"] == {"5": "Живое имя", "6": "Из снимка"}

    async def test_repository_error_is_swallowed(self, repo):
        class Repo:
            async def get_agent_names_map(self, ids):
                raise RuntimeError("db down")

        repo.repo = Repo()
        agent = FakeAgent([5])

        await names.enrich_agents_subagent_names([agent])

        assert "agent_names" not in agent.config["subagents"]


# ───────────────────────────── plan report ─────────────────────────────


class TestPlanReport:
    def test_plan_with_only_own_steps_logs_nothing(self, monkeypatch):
        fake = FakeLog()
        monkeypatch.setattr(plan, "log", fake)
        ctx = SimpleNamespace(plan=[{"target_id": None, "do": "сам", "subagent": ""}], done_calls={})

        plan.log_plan_report(ctx)

        assert fake.lines == []

    def test_unknown_extra_call_label_falls_back_to_agent_key(self, monkeypatch):
        fake = FakeLog()
        monkeypatch.setattr(plan, "log", fake)
        ctx = SimpleNamespace(
            plan=[{"target_id": 1, "do": "шаг", "subagent": "Юрист"}],
            done_calls={"1\nzzz": 1, "abc\nqqq": 2},
            agent_names={1: "Юрист - право"},
        )

        plan.log_plan_report(ctx)

        text = "\n".join(fake.lines)
        assert "выполнено 1 из 1" in text
        assert "вне плана вызваны: agent_abc [[ОТВЕТ 2]]" in text
