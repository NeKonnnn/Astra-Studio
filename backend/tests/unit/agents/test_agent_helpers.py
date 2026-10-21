"""agents/*: лимиты цепочки и субагентов, метки [[ОТВЕТ N]], выбор субагента по имени, параллельный запуск инструментов."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.agents import config as agent_config
from backend.agents.chain import answers as chain_answers
from backend.agents.chain import profiles as chain_profiles
from backend.agents.chain import settings as chain_settings
from backend.agents.subagents import answers as sub_answers
from backend.agents.subagents import names as sub_names
from backend.agents.subagents import parallel
from backend.agents.subagents import settings as sub_settings
from backend.agents.subagents.settings import AgentSubagentsConfig
from backend.agents.subagents.tool import SubagentRunContext

pytestmark = pytest.mark.unit

ENV = ("AGENT_CHAIN_MAX_AGENTS", "AGENT_GRAPH_STEPS", "AGENT_CHAIN_STEP_QUERY", "AGENT_SUBAGENTS_MAX", "SUBAGENT_PARALLEL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


class TestChainSettings:
    @pytest.mark.parametrize(("raw", "expected"), [(None, 10), ("", 10), ("abc", 10), ("0", 1), ("-3", 1), ("5", 5), ("999", 50)])
    def test_platform_chain_limit(self, monkeypatch, raw, expected):
        if raw is not None:
            monkeypatch.setenv("AGENT_CHAIN_MAX_AGENTS", raw)

        assert chain_settings.get_max_chain_agents() == expected

    @pytest.mark.parametrize(("raw", "expected"), [(None, 50), ("abc", 50), ("0", 1), ("120", 120), ("9999", 500)])
    def test_graph_steps(self, monkeypatch, raw, expected):
        if raw is not None:
            monkeypatch.setenv("AGENT_GRAPH_STEPS", raw)

        assert chain_settings.get_agent_graph_steps() == expected

    @pytest.mark.parametrize(("raw", "expected"), [(None, True), ("1", True), ("", True), ("0", False), ("false", False), (" OFF ", False), ("no", False), ("yes", True)])
    def test_step_query_flag(self, monkeypatch, raw, expected):
        if raw is not None:
            monkeypatch.setenv("AGENT_CHAIN_STEP_QUERY", raw)

        assert chain_settings.chain_step_query_enabled() is expected

    def test_per_agent_limit_cannot_exceed_the_platform_one(self, monkeypatch):
        monkeypatch.setenv("AGENT_CHAIN_MAX_AGENTS", "4")

        assert chain_settings.resolve_max_chain_agents({"max_chain_agents": 2}) == 2
        assert chain_settings.resolve_max_chain_agents({"max_chain_agents": "3"}) == 3
        assert chain_settings.resolve_max_chain_agents({"max_chain_agents": 99}) == 4
        assert chain_settings.resolve_max_chain_agents({"max_chain_agents": 0}) == 4
        assert chain_settings.resolve_max_chain_agents({"max_chain_agents": "x"}) == 4
        assert chain_settings.resolve_max_chain_agents(None) == 4

    def test_agent_ids_are_normalized(self):
        assert chain_settings.parse_agent_ids([3, "2", 3, 0, -1, "x", None, 5.0], exclude_id=2) == [3, 5]

    @pytest.mark.parametrize("raw", [None, "1,2", {"a": 1}, 5])
    def test_agent_ids_of_garbage(self, raw):
        assert chain_settings.parse_agent_ids(raw) == []

    def test_agent_ids_respect_limits(self, monkeypatch):
        monkeypatch.setenv("AGENT_CHAIN_MAX_AGENTS", "3")

        assert chain_settings.parse_agent_ids(list(range(1, 20))) == [1, 2, 3]
        assert chain_settings.parse_agent_ids(list(range(1, 20)), max_agents=5) == [1, 2, 3, 4, 5]
        assert chain_settings.parse_agent_ids(list(range(1, 20)), max_agents=0) == [1]
        assert chain_settings.parse_agent_ids(list(range(1, 20)), agent_profile={"max_chain_agents": 2}) == [1, 2]


class TestRecursionLimit:
    def test_priority_agent_then_global_then_default(self, monkeypatch):
        assert agent_config.resolve_recursion_limit() == 50
        monkeypatch.setenv("AGENT_GRAPH_STEPS", "80")
        assert agent_config.resolve_recursion_limit() == 80
        assert agent_config.resolve_recursion_limit({"recursion_limit": 30}) == 30
        assert agent_config.resolve_recursion_limit({"recursion_limit": " 25 "}) == 25
        assert agent_config.resolve_recursion_limit({"recursion_limit": 0}) == 80
        assert agent_config.resolve_recursion_limit({"recursion_limit": "abc"}) == 80

    def test_explicit_global_default_and_cap(self):
        assert agent_config.resolve_recursion_limit(global_default=7) == 7
        assert agent_config.resolve_recursion_limit(global_default=-1) == 50
        assert agent_config.resolve_recursion_limit({"recursion_limit": 9000}) == 500
        assert agent_config.resolve_recursion_limit({"recursion_limit": 9000}, max_cap=100) == 100
        assert agent_config.resolve_recursion_limit({"recursion_limit": 9000}, max_cap=0) == 9000


STEPS = [
    {"agent_name": "Аналитик", "content": "Выручка 100"},
    {"agent_name": " ", "content": "  "},
    {"agent_name": "", "content": "Риски: высокие"},
    {"agent_name": "Итог", "content": "итог"},
]


class TestChainAnswers:
    def test_marks_only_for_the_last_non_single_step(self):
        assert chain_answers.chain_marks_enabled(is_first=False, is_last=True) is True
        assert chain_answers.chain_marks_enabled(is_first=True, is_last=True) is False
        assert chain_answers.chain_marks_enabled(is_first=False, is_last=False) is False

    def test_used_marks_are_ordered_unique_and_known(self):
        text = "[[ОТВЕТ 3]] и [[ответ 1]] и [[ОТВЕТ 3]], а ещё [[ОТВЕТ 2]] (пустой шаг) и [[ОТВЕТ 9]]"

        assert chain_answers.chain_marks_used(text, STEPS) == [3, 1]

    def test_expand_inserts_step_answers_and_drops_unknown_marks(self):
        out = chain_answers.expand_chain_answers("Вот:\n[[ОТВЕТ 1]]\nи\n[[ОТВЕТ 9]]\nконец", STEPS)

        assert "Выручка 100" in out and "[[" not in out and out.startswith("Вот:") and out.endswith("конец")

    def test_expand_without_answers_returns_the_text(self):
        assert chain_answers.expand_chain_answers("[[ОТВЕТ 1]] текст", [{"content": ""}]) == "[[ОТВЕТ 1]] текст"
        assert chain_answers.expand_chain_answers(None, []) == ""

    def test_empty_final_answer_falls_back_to_all_step_answers(self):
        out = chain_answers.expand_chain_answers("", STEPS)

        assert "Аналитик" in out and "Выручка 100" in out and "Агент" in out  # безымянный шаг назван «Агент»

    def test_fold_without_marks_changes_nothing(self):
        steps = [{"n": 1}, {"n": 2}, {"n": 3}]

        assert chain_answers.fold_inserted_steps(steps, [], hide_sequential=False) == (steps, False)
        assert chain_answers.fold_inserted_steps(steps, [1], hide_sequential=True) == (steps, True)
        assert chain_answers.fold_inserted_steps([], [1], hide_sequential=False) == ([], False)

    def test_fold_removes_inserted_steps_from_the_message(self):
        steps = [{"n": 1}, {"n": 2}, {"n": 3}]

        assert chain_answers.fold_inserted_steps(steps, [1], hide_sequential=False) == ([{"n": 2}, {"n": 3}], False)

    def test_fold_collapses_intermediate_steps_when_all_were_inserted(self):
        steps = [{"n": 1}, {"n": 2}, {"n": 3}]

        assert chain_answers.fold_inserted_steps(steps, [1, 2], hide_sequential=False) == (steps, True)


@pytest.fixture
def helpers(monkeypatch):
    """prepare_step_socket_data / resolve_agent_chain тянут realtime.helpers — подменяем границы."""
    import backend.realtime.helpers as rh

    calls = []

    async def resolve(aid, user_id, user=None):
        calls.append((aid, user_id))
        return {"ghost": None, 3: {"name": "Три"}, 4: {"system_prompt": "промпт"}, 5: {}, 6: "garbage"}.get(aid, {"name": f"Агент {aid}"})

    monkeypatch.setattr(rh, "_resolve_agent_chat_params", resolve)
    return SimpleNamespace(calls=calls)


class TestChainProfiles:
    async def test_chain_is_primary_plus_valid_followers(self, helpers):
        chain = await chain_profiles.resolve_agent_chain("1", {"name": "Главный", "agent_ids": [1, 3, 4, 5, 6, 7, 3]}, "alice")

        assert [(p["agent_id"], p.get("name") or p.get("system_prompt")) for p in chain] == [(1, "Главный"), (3, "Три"), (4, "промпт"), (7, "Агент 7")]
        assert [c[0] for c in helpers.calls] == [3, 4, 5, 6, 7]  # сам 1 и дубликат 3 не запрашиваются

    async def test_followers_are_not_resolved_transitively(self, helpers):
        chain = await chain_profiles.resolve_agent_chain(1, {"agent_ids": [3]}, "alice")

        assert len(chain) == 2 and [c[0] for c in helpers.calls] == [3]

    @pytest.mark.parametrize("primary_id", [None, 0, "x"])
    async def test_without_a_valid_primary_id_the_chain_is_just_the_primary(self, helpers, primary_id):
        chain = await chain_profiles.resolve_agent_chain(primary_id, {"agent_ids": [3]}, "alice")

        assert len(chain) == 1 and helpers.calls == []

    async def test_non_dict_profile(self, helpers):
        assert await chain_profiles.resolve_agent_chain(None, "oops", "alice") == [{}]

    def test_first_step_keeps_the_ui_choices(self):
        data = {"message": "вопрос", "coding_mode": True, "tag_ids": [1], "tool_ids": ["server:mcp:ui"]}
        profile = {"mcp_enabled": True, "mcp_server_ids": ["card"], "plugin_ids": ["cash-flow"]}

        step = chain_profiles.prepare_step_socket_data(data, profile, is_first=True)

        assert step["coding_mode"] is True and step["tag_ids"] == [1] and step["tool_ids"] == ["server:mcp:ui"]
        assert step["__plugin_ids__"] == ["cash-flow"] and data["tool_ids"] == ["server:mcp:ui"]  # исходный payload не меняется

    def test_first_step_takes_card_mcp_tools_when_ui_has_none(self):
        step = chain_profiles.prepare_step_socket_data({"message": "q"}, {"mcp_enabled": True, "mcp_server_ids": ["card", "card"]}, is_first=True)

        assert step["tool_ids"] == ["server:mcp:card"]

    def test_following_steps_drop_ui_modes_and_tags(self):
        data = {"message": "<#7> вопрос", "coding_mode": True, "plan_mode": True, "approved_plan": "p", "tag_ids": [7], "tool_ids": ["server:mcp:ui"], "__plugin_ids__": ["stale"]}

        step = chain_profiles.prepare_step_socket_data(data, {"mcp_enabled": True, "mcp_server_ids": ["card"]}, is_first=False)

        assert "coding_mode" not in step and "plan_mode" not in step and "approved_plan" not in step and "tag_ids" not in step
        assert step["tool_ids"] == ["server:mcp:card"] and "__plugin_ids__" not in step and "<#7>" not in step["message"]

    def test_none_payload(self):
        assert chain_profiles.prepare_step_socket_data(None, {}, is_first=False) == {"tool_ids": []}


class TestSubagentSettings:
    def test_limits(self, monkeypatch):
        assert sub_settings.get_max_subagents() == 10
        monkeypatch.setenv("AGENT_SUBAGENTS_MAX", "3")
        assert sub_settings.resolve_max_subagents({"max_subagents": 2}) == 2
        assert sub_settings.resolve_max_subagents({"max_subagents": 50}) == 3
        assert sub_settings.resolve_max_subagents(None) == 3

    def test_names_map_parsing(self):
        assert sub_settings.parse_agent_names_map({"1": " Один ", 2: "Два", "x": "bad", 0: "zero", "3": "", "4": None}) == {1: "Один", 2: "Два"}
        assert sub_settings.parse_agent_names_map("x") == {}

    def test_config_parsing(self):
        cfg = sub_settings.parse_subagents_config(
            {"enabled": True, "allowSelf": False, "agent_ids": [2, 3, 3, 9], "agent_names": {"2": "Два"}, "required_tag_ids": ["5", 5, "x", 0], "required_only": True}, exclude_id=9,
        )

        assert cfg == AgentSubagentsConfig(enabled=True, allow_self=False, agent_ids=[2, 3], agent_names={2: "Два"}, required_tag_ids=[5], required_only=True)

    def test_disabled_by_default_and_strict_enabled_flag(self):
        assert sub_settings.parse_subagents_config(None).enabled is False
        assert sub_settings.parse_subagents_config({"enabled": "true"}).enabled is False  # только булево True
        assert sub_settings.parse_subagents_config({"enabled": True}).allow_self is True

    def test_profile_helper_excludes_the_parent(self):
        cfg = sub_settings.subagents_from_profile({"agent_id": 2, "subagents": {"enabled": True, "agent_ids": [2, 3]}})

        assert cfg.agent_ids == [3]
        assert sub_settings.subagents_from_profile({"subagents": {"enabled": True}}).enabled is True

    def test_profile_recursion_limit(self):
        assert sub_settings.profile_recursion_limit({"recursion_limit": 12}) == 12


def cfg(ids, names=None, allow_self=True):
    return AgentSubagentsConfig(enabled=True, allow_self=allow_self, agent_ids=ids, agent_names=names or {})


class TestSubagentTargets:
    def test_name_is_the_subagent_type_when_unique(self):
        mapping = sub_settings.subagent_type_by_name(cfg([1, 2], {1: "Таблицы - строит таблицы", 2: "Графики"}))

        assert mapping == {1: "Таблицы", 2: "Графики"}

    @pytest.mark.parametrize(
        "names",
        [
            {1: "Дубль", 2: "дубль"},  # одинаковые имена
            {1: "self", 2: "Другой"},  # зарезервированное
            {1: "agent_2", 2: "Другой"},  # похоже на номер
            {1: "", 2: "Другой"},  # нет имени
        ],
    )
    def test_ambiguous_names_fall_back_to_ids(self, names):
        mapping = sub_settings.subagent_type_by_name(cfg([1, 2], names))

        assert mapping[1] == "agent_1"

    def test_live_names_override_the_snapshot(self):
        assert sub_settings.subagent_type_by_name(cfg([1], {1: "Старое"}), {1: "Новое - описание"}) == {1: "Новое"}

    def test_resolution(self):
        c = cfg([1, 2], {1: "«Таблицы»", 2: "Ёлка"})

        assert sub_settings.resolve_subagent_target("self", parent_agent_id=7, config=c) == 7
        assert sub_settings.resolve_subagent_target("self", parent_agent_id=None, config=c) is None
        assert sub_settings.resolve_subagent_target("self", parent_agent_id=7, config=cfg([1], allow_self=False)) is None
        assert sub_settings.resolve_subagent_target("agent_2", parent_agent_id=7, config=c) == 2
        assert sub_settings.resolve_subagent_target("agent_9", parent_agent_id=7, config=c) is None  # чужой номер
        assert sub_settings.resolve_subagent_target("  ТАБЛИЦЫ ", parent_agent_id=7, config=c) == 1
        assert sub_settings.resolve_subagent_target("елка", parent_agent_id=7, config=c) == 2  # ё/е не различаются
        assert sub_settings.resolve_subagent_target("", parent_agent_id=7, config=c) is None
        assert sub_settings.resolve_subagent_target("неизвестный", parent_agent_id=7, config=c) is None

    def test_agent_n_with_garbage_number_falls_back_to_the_name_lookup(self):
        assert sub_settings.resolve_subagent_target("agent_x", parent_agent_id=1, config=cfg([2], {2: "agent_x_name"})) is None

    def test_type_for_id(self):
        assert sub_settings.subagent_type_for_agent_id("7") == "agent_7"


class TestSubagentNames:
    def test_short_label(self):
        assert sub_names.short_agent_label("Таблицы - строит таблицы") == "Таблицы"
        assert sub_names.short_agent_label("  Просто  ") == "Просто"
        assert sub_names.short_agent_label(None) == ""

    def test_display_name(self):
        c = cfg([1], {1: "Таблицы - описание"})

        assert sub_names.resolve_subagent_display_name({"agent_name": "Свой - x"}, parent_agent_id=9, config=c, agent_names={}) == "Свой"
        assert sub_names.resolve_subagent_display_name({"subagent_type": "agent_1"}, parent_agent_id=9, config=c, agent_names=c.agent_names) == "Таблицы"
        assert sub_names.resolve_subagent_display_name({"subagent_type": "agent_5"}, parent_agent_id=9, config=c, agent_names={}) is None
        assert sub_names.resolve_subagent_display_name({"subagent_type": "agent_1"}, parent_agent_id=9, config=c, agent_names={}) is None

    def test_arguments_are_enriched_without_mutation(self):
        c = cfg([1], {1: "Таблицы"})
        args = {"subagent_type": "agent_1", "prompt": "p"}

        out = sub_names.with_subagent_display_name(args, parent_agent_id=9, config=c)

        assert out == {"subagent_type": "agent_1", "prompt": "p", "agent_name": "Таблицы"} and "agent_name" not in args
        assert sub_names.with_subagent_display_name(args, parent_agent_id=9, config=None) == args
        assert sub_names.with_subagent_display_name(None, parent_agent_id=9, config=c) == {}

    async def test_names_are_loaded_from_the_repository(self, monkeypatch):
        import backend.database.init_db as init_db

        class Repo:
            async def get_agent_names_map(self, ids):
                return {2: "Живое имя", 3: "Из карты"}

            async def get_agent(self, aid, user_id):
                return {1: SimpleNamespace(name="Таблицы", description="  строит   таблицы  "), 2: None, 3: SimpleNamespace(name="", description="")}.get(aid)

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: Repo())

        names = await sub_names.load_subagent_agent_names([1, 2, 3, 4], user_id="alice", snapshot_names={4: "Снимок"})

        assert names == {1: "Таблицы - строит таблицы", 2: "Живое имя", 3: "Из карты", 4: "Снимок"}

    async def test_names_fall_back_to_the_snapshot_when_the_repository_fails(self, monkeypatch):
        import backend.database.init_db as init_db

        def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(init_db, "get_agent_repository", boom)

        assert await sub_names.load_subagent_agent_names([1], user_id="a", snapshot_names={1: "Снимок"}) == {1: "Снимок"}
        assert await sub_names.load_subagent_agent_names([], user_id="a") == {}

    async def test_no_repository(self, monkeypatch):
        import backend.database.init_db as init_db

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: None)

        assert await sub_names.load_subagent_agent_names([1], user_id="a", snapshot_names={1: "Снимок"}) == {1: "Снимок"}

    async def test_enrichment_updates_cards_with_live_names(self, monkeypatch):
        import backend.database.init_db as init_db

        class Repo:
            async def get_agent_names_map(self, ids):
                return {1: "Живое"}

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: Repo())
        agents = [
            SimpleNamespace(config={"subagents": {"agent_ids": [1, "2", "x"], "agent_names": {"2": "Снимок2"}}, "other": 1}),
            SimpleNamespace(config={"subagents": "bad"}),
            SimpleNamespace(config=None),
            SimpleNamespace(config={"subagents": {"agent_ids": []}}),
        ]

        await sub_names.enrich_agents_subagent_names(agents)

        assert agents[0].config["subagents"]["agent_names"] == {"1": "Живое", "2": "Снимок2"} and agents[0].config["other"] == 1
        assert agents[1].config == {"subagents": "bad"} and agents[2].config is None
        await sub_names.enrich_agents_subagent_names([])


class TestParallelToolCalls:
    @pytest.mark.parametrize(("raw", "expected"), [(None, 4), ("", 4), ("x", 4), ("0", 1), ("-2", 1), ("8", 8), ("999", 16)])
    def test_limit(self, monkeypatch, raw, expected):
        if raw is not None:
            monkeypatch.setenv("SUBAGENT_PARALLEL", raw)

        assert parallel.subagent_parallel_limit() == expected

    async def test_results_keep_the_order_of_the_calls(self):
        async def run(call):
            await asyncio.sleep(0.03 if call == "slow" else 0)
            return call.upper()

        results = await parallel.run_tool_calls(["slow", "fast", "tool", "last"], run, parallel=lambda c: c != "tool")

        assert results == ["SLOW", "FAST", "TOOL", "LAST"]

    async def test_subagents_really_run_at_the_same_time_but_not_above_the_limit(self, monkeypatch):
        monkeypatch.setenv("SUBAGENT_PARALLEL", "2")
        active = peak = 0

        async def run(call):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return call

        await parallel.run_tool_calls(list(range(6)), run, parallel=lambda c: True)

        assert peak == 2

    async def test_a_single_parallel_call_or_limit_one_runs_sequentially(self, monkeypatch):
        order = []

        async def run(call):
            order.append(("start", call))
            await asyncio.sleep(0)
            order.append(("end", call))

        await parallel.run_tool_calls(["a", "b"], run, parallel=lambda c: c == "a")
        assert order == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]

        order.clear()
        monkeypatch.setenv("SUBAGENT_PARALLEL", "1")
        await parallel.run_tool_calls(["a", "b"], run, parallel=lambda c: True)
        assert order == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]

    async def test_a_failure_cancels_the_other_calls(self):
        cancelled = []

        async def run(call):
            if call == "bad":
                raise RuntimeError("boom")
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(call)
                raise

        with pytest.raises(RuntimeError):
            await parallel.run_tool_calls(["ok1", "bad", "ok2"], run, parallel=lambda c: True)

        assert sorted(cancelled) == ["ok1", "ok2"]

    async def test_empty(self):
        assert await parallel.run_tool_calls([], lambda c: c, parallel=lambda c: True) == []


def ctx(answers=None):
    return SubagentRunContext(parent_agent_id=1, parent_profile={}, user=None, user_id="alice", answers=answers or {})


ANSWERS = {1: {"name": "Таблицы", "text": "Таблица: 100 и 200"}, 2: {"name": "Графики", "text": "График роста"}}


class TestSubagentAnswers:
    @pytest.mark.parametrize("mark", ["[[ОТВЕТ 1]]", "[[Ответ 1]]", "[[ОТВЕТ №1]]", "[[ОТВЕТ 1:]]", "[[ ответ  1 ]]", "[[ОТВЕТ субагента 1]]"])
    def test_mark_variants_are_recognised(self, mark):
        assert sub_answers._ANSWER_MARK_RE.search(mark).group(1) == "1"

    @pytest.mark.parametrize("text", ["[[ОТВЕТ]]", "ОТВЕТ 1", "[ОТВЕТ 1]", "[[ANSWER 1]]"])
    def test_non_marks(self, text):
        assert sub_answers._ANSWER_MARK_RE.search(text) is None

    def test_expand_replaces_marks_with_the_children_answers(self):
        out = sub_answers.expand_subagent_answers("Итог:\n[[ОТВЕТ 1]]\nИ ещё:\n[[ОТВЕТ 2]]", ctx(ANSWERS))

        assert [line for line in out.splitlines() if line.strip()] == ["Итог:", "Таблица: 100 и 200", "И ещё:", "График роста"]

    def test_unknown_marks_vanish(self):
        out = sub_answers.expand_subagent_answers("до [[ОТВЕТ 7]] после", ctx(ANSWERS))

        assert out == "до  после" and sub_answers.orphan_answer_marks("до [[ОТВЕТ 7]] [[ОТВЕТ 1]]", ctx(ANSWERS)) == ["[[ОТВЕТ 7]]"]

    def test_empty_parent_answer_falls_back_to_children(self):
        assert sub_answers.expand_subagent_answers("  ", ctx({1: ANSWERS[1]})) == "Таблица: 100 и 200"
        both = sub_answers.expand_subagent_answers("", ctx(ANSWERS))
        assert "**▸ Таблицы**" in both and "**▸ Графики**" in both

    def test_without_answers_text_is_unchanged(self):
        assert sub_answers.expand_subagent_answers("[[ОТВЕТ 1]]", ctx()) == "[[ОТВЕТ 1]]"
        assert sub_answers.expand_subagent_answers(None, None) == ""

    def test_drop_marks(self):
        assert sub_answers.drop_answer_marks("a [[ОТВЕТ 1]] b [[ответ 22]]") == "a  b"

    def test_duplicate_heading_above_a_mark_is_removed(self):
        answers = {1: {"name": "T", "text": "## Выводы по выручке\nТекст"}}

        out = sub_answers.drop_duplicate_headings("Введение\n\n## Выводы по выручке\n\n[[ОТВЕТ 1]]", answers)

        assert "## Выводы" not in out and "[[ОТВЕТ 1]]" in out and out.startswith("Введение")

    def test_different_heading_is_kept(self):
        answers = {1: {"name": "T", "text": "Таблица остатков"}}
        text = "## Совсем другое\n[[ОТВЕТ 1]]"

        assert sub_answers.drop_duplicate_headings(text, answers) == text

    def test_result_for_the_model_carries_the_mark(self):
        c = ctx(ANSWERS)

        out = sub_answers.subagent_result_for_model(c, "тело ответа", 1)

        assert out.startswith("[[ОТВЕТ 1]] - ответ субагента «Таблицы»") and out.endswith("тело ответа")
        assert sub_answers.subagent_result_for_model(c, "тело", None) == "тело" and sub_answers.subagent_result_for_model(None, "тело", 1) == "тело"

    def test_remembering_answers(self):
        c = ctx()
        c.agent_names = {5: "Таблицы - описание"}

        first = sub_answers._remember_subagent_answer(c, 5, "  ответ  ")
        second = sub_answers._remember_subagent_answer(c, 6, "ещё")

        assert (first, second) == (1, 2) and c.answers[1] == {"name": "Таблицы", "text": "ответ"} and c.answers[2]["name"] == "Agent 6"
        assert sub_answers._remember_subagent_answer(c, 5, "   ") is None
        assert sub_answers._remember_subagent_answer(c, 5, "заранее", n=9) == 9

    def test_answers_for_the_child(self):
        task, materials = sub_answers.answers_for_child("Сделай выводы по [[ОТВЕТ 1]] и [[ОТВЕТ 1]] и [[ОТВЕТ 8]]", ctx(ANSWERS))

        assert "[[ОТВЕТ 1]]" not in task and "[[ОТВЕТ 8]]" in task and task.count("ответ субагента «Таблицы» - ниже") == 2
        assert materials.count("### Ответ субагента «Таблицы»") == 1 and "Таблица: 100 и 200" in materials
        assert sub_answers.answers_for_child("без меток", ctx(ANSWERS)) == ("без меток", "")
        assert sub_answers.answers_for_child("[[ОТВЕТ 1]]", None) == ("[[ОТВЕТ 1]]", "")

    def test_question_for_the_child(self):
        out = sub_answers.subagent_question_for_child("Составь отчёт по продажам", "Подготовь раздел 2")

        assert out.startswith("Подготовь раздел 2") and "Составь отчёт по продажам" in out and "адресовано не тебе" in out

    def test_question_already_in_the_task_is_not_duplicated(self):
        assert sub_answers.subagent_question_for_child("отчёт  по продажам", "Сделай ОТЧЁТ по продажам сейчас") == "Сделай ОТЧЁТ по продажам сейчас"
        assert sub_answers.subagent_question_for_child("  ", "задание") == "задание" and sub_answers.subagent_question_for_child(None, "задание") == "задание"

    def test_long_question_is_cut(self):
        out = sub_answers.subagent_question_for_child("я" * 20000, "задание")

        assert len(out) < 9000 and " …" in out

    def test_rules_are_appended_to_the_prompt(self):
        assert sub_answers.append_subagent_rules("Ты помощник.").startswith("Ты помощник.\n\nРАБОТА С СУБАГЕНТАМИ")
        assert sub_answers.append_subagent_rules(None) == sub_answers.SUBAGENT_RULES

    def test_reserved_numbers_never_repeat(self):
        c = ctx({1: ANSWERS[1]})

        assert [c.reserve_answer_no(), c.reserve_answer_no()] == [2, 3]
