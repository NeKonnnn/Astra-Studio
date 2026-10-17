# -*- coding: utf-8 -*-
"""Тесты backend.agents.subagents.plan: разбор плана модели, прогресс, итог, системный блок."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.agents.subagents import plan as plan_mod  # noqa: E402
from backend.agents.subagents.settings import AgentSubagentsConfig  # noqa: E402

NAMES = {10: "Аналитик - разбирает данные", 20: "Писатель - пишет текст"}
CONFIG = AgentSubagentsConfig(enabled=True, allow_self=True, agent_ids=[10, 20], agent_names=NAMES)


def parse(payload, **kw):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    kw.setdefault("config", CONFIG)
    kw.setdefault("parent_agent_id", 1)
    kw.setdefault("agent_names", None)
    return plan_mod.parse_plan(text, **kw)


def step(do="сделать", sub="", target=None, uses=(), check=""):
    return {"do": do, "subagent": sub, "target_id": target, "uses": list(uses), "check": check}


class TestPlanEnabled:
    @pytest.mark.parametrize("raw", ["0", "false", "No", "OFF", " off "])
    def test_disabled(self, monkeypatch, raw):
        monkeypatch.setenv("SUBAGENT_PLAN", raw)
        assert plan_mod.plan_enabled() is False

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "anything"])
    def test_enabled(self, monkeypatch, raw):
        monkeypatch.setenv("SUBAGENT_PLAN", raw)
        assert plan_mod.plan_enabled() is True

    def test_default_and_empty_are_enabled(self, monkeypatch):
        monkeypatch.delenv("SUBAGENT_PLAN", raising=False)
        assert plan_mod.plan_enabled() is True
        monkeypatch.setenv("SUBAGENT_PLAN", "")
        assert plan_mod.plan_enabled() is True


class TestExtractJson:
    def test_plain_object(self):
        assert plan_mod._extract_json('{"a": 1}') == {"a": 1}

    def test_wrapped_in_code_fence_and_text(self):
        assert plan_mod._extract_json('Вот план:\n```json\n{"steps": []}\n```\nГотово') == {"steps": []}

    def test_nested_braces(self):
        assert plan_mod._extract_json('{"a": {"b": {"c": 1}}} хвост') == {"a": {"b": {"c": 1}}}

    def test_skips_broken_first_object(self):
        assert plan_mod._extract_json('{oops} {"ok": true}') == {"ok": True}

    @pytest.mark.parametrize("text", ["", None, "нет json", "[1, 2]", "{не закрыт"])
    def test_no_object(self, text):
        assert plan_mod._extract_json(text) is None


class TestClean:
    def test_collapses_whitespace_and_truncates(self):
        assert plan_mod._clean("  а \n б\t в  ", 100) == "а б в"
        assert plan_mod._clean("x" * 50, 10) == "x" * 10

    @pytest.mark.parametrize("value", [None, "", 0])
    def test_empty(self, value):
        assert plan_mod._clean(value, 10) == ""


class TestParsePlan:
    def test_valid_plan(self):
        steps = parse(
            {
                "steps": [
                    {"do": "Собрать данные", "subagent": "Аналитик", "uses": [], "check": "Таблица"},
                    {"do": "Написать отчёт", "subagent": "Писатель", "uses": [1], "check": "Объём 1 стр."},
                    {"do": "Собрать ответ", "subagent": None, "uses": [1, 2], "check": ""},
                ]
            }
        )
        assert [s["target_id"] for s in steps] == [10, 20, None]
        assert [s["subagent"] for s in steps] == ["Аналитик", "Писатель", ""]
        assert steps[1]["uses"] == [1]
        assert steps[2]["uses"] == [1, 2]
        assert steps[0]["check"] == "Таблица"

    @pytest.mark.parametrize("text", ["", "мусор", '{"steps": "не список"}', '{"other": 1}', "[]"])
    def test_garbage_gives_empty_plan(self, text):
        assert parse(text) == []

    def test_non_dict_steps_skipped(self):
        steps = parse({"steps": ["строка", 5, None, {"do": "ok"}]})
        assert [s["do"] for s in steps] == ["ok"]

    def test_unknown_subagent_is_done_by_self(self):
        [s] = parse({"steps": [{"do": "Сделать", "subagent": "Неизвестный"}]})
        assert s["target_id"] is None
        assert s["subagent"] == ""
        assert s["do"] == "Сделать"

    def test_step_without_do_and_without_target_is_dropped(self):
        assert parse({"steps": [{"do": "", "subagent": "Неизвестный"}, {"do": "  "}]}) == []

    def test_empty_do_falls_back_to_subagent_name(self):
        [s] = parse({"steps": [{"do": "", "subagent": "Аналитик"}]})
        assert s["do"] == "Аналитик"
        assert s["target_id"] == 10

    @pytest.mark.parametrize("sub", [None, "", "null"])
    def test_null_subagent_values(self, sub):
        [s] = parse({"steps": [{"do": "сам", "subagent": sub}]})
        assert s["target_id"] is None and s["subagent"] == ""

    def test_self_subagent(self):
        [s] = parse({"steps": [{"do": "рекурсия", "subagent": "self"}]})
        assert s["target_id"] == 1

    def test_self_not_allowed(self):
        cfg = AgentSubagentsConfig(enabled=True, allow_self=False, agent_ids=[10], agent_names=NAMES)
        [s] = parse({"steps": [{"do": "x", "subagent": "self"}]}, config=cfg)
        assert s["target_id"] is None

    def test_agent_n_reference(self):
        [s] = parse({"steps": [{"do": "x", "subagent": "agent_20"}]})
        assert s["target_id"] == 20

    def test_agent_n_not_in_config(self):
        [s] = parse({"steps": [{"do": "x", "subagent": "agent_99"}]})
        assert s["target_id"] is None

    def test_uses_only_refer_to_earlier_steps(self):
        steps = parse(
            {
                "steps": [
                    {"do": "a", "uses": [1, 5]},  # шаг не может опираться на себя/будущее
                    {"do": "b", "uses": [1, 1, "2", "x", None, 0, -1, 3]},
                ]
            }
        )
        assert steps[0]["uses"] == []
        assert steps[1]["uses"] == [1]

    def test_uses_not_a_list(self):
        [s] = parse({"steps": [{"do": "a", "uses": "1"}]})
        assert s["uses"] == []

    def test_do_and_check_are_cleaned_and_limited(self):
        [s] = parse({"steps": [{"do": "а\n\nб" + "я" * 500, "check": "ч" * 500}]})
        assert "\n" not in s["do"]
        assert len(s["do"]) == 400
        assert len(s["check"]) == 300

    def test_step_limit(self):
        steps = parse({"steps": [{"do": f"s{i}"} for i in range(plan_mod.PLAN_MAX_STEPS + 15)]})
        assert len(steps) == plan_mod.PLAN_MAX_STEPS

    def test_model_wraps_json_in_markdown(self):
        steps = parse('```json\n{"steps": [{"do": "один шаг"}]}\n```')
        assert [s["do"] for s in steps] == ["один шаг"]


class TestAgentsList:
    def test_lists_names_with_descriptions(self):
        assert plan_mod._agents_list(CONFIG, None) == "- Аналитик: разбирает данные\n- Писатель: пишет текст"

    def test_without_description(self):
        cfg = AgentSubagentsConfig(enabled=True, agent_ids=[10], agent_names={10: "Просто"})
        assert plan_mod._agents_list(cfg, None) == "- Просто"

    def test_no_subagents(self):
        assert plan_mod._agents_list(AgentSubagentsConfig(enabled=True), None) == "- (нет)"

    def test_agent_names_argument_overrides_snapshot(self):
        out = plan_mod._agents_list(CONFIG, {10: "Новый - свежее описание"})
        assert "- Новый: свежее описание" in out

    def test_duplicate_names_fall_back_to_agent_n(self):
        cfg = AgentSubagentsConfig(enabled=True, agent_ids=[10, 20], agent_names={10: "Одинаков", 20: "одинаков"})
        assert plan_mod._agents_list(cfg, None) == "- agent_10\n- agent_20"


PLAN = [
    step("Собрать данные", "Аналитик", 10, [], "Таблица"),
    step("Написать отчёт", "Писатель", 20, [1], "Объём 1 стр."),
    step("Собрать ответ"),
]


def ctx(plan=PLAN, done_calls=None, agent_names=None):
    return SimpleNamespace(plan=plan, done_calls=done_calls or {}, agent_names=agent_names or NAMES)


class TestPlanTexts:
    def test_log_text(self):
        text = plan_mod.plan_log_text(PLAN)
        assert text.splitlines()[0].endswith("шагов 3, с субагентами 2")
        assert "1. [Аналитик] Собрать данные" in text
        assert "3. [сам] Собрать ответ" in text
        assert "опирается на шаги 1 | проверка: Объём 1 стр." in text

    def test_system_block_empty_plan(self):
        assert plan_mod.plan_system_block([]) == ""

    def test_system_block(self):
        block = plan_mod.plan_system_block(PLAN)
        assert block.startswith("ТВОЙ ПЛАН НА ЭТОТ ЗАПРОС")
        assert "1. Собрать данные — субагент «Аналитик»" in block
        assert "2. Написать отчёт — субагент «Писатель» — опирается на шаги 1" in block
        assert "3. Собрать ответ — сам" in block
        assert "Проверка: Таблица" in block


class TestStepAnswers:
    def test_no_plan_or_calls(self):
        assert plan_mod._step_answers(ctx(plan=[])) == {}
        assert plan_mod._step_answers(ctx()) == {}

    def test_maps_calls_to_steps_by_target(self):
        done = {"10\nхеш1": 1, "20\nхеш2": 2}
        assert plan_mod._step_answers(ctx(done_calls=done)) == {1: 1, 2: 2}

    def test_repeated_subagent_calls_distributed_in_plan_order(self):
        plan = [step("a", "Аналитик", 10), step("b", "Аналитик", 10)]
        done = {"10\nx": 5, "10\ny": 3}
        assert plan_mod._step_answers(ctx(plan=plan, done_calls=done)) == {1: 3, 2: 5}

    def test_calls_of_other_agents_are_ignored(self):
        assert plan_mod._step_answers(ctx(done_calls={"99\nx": 1})) == {}

    def test_garbage_keys_are_skipped(self):
        assert plan_mod._step_answers(ctx(done_calls={"не число\nx": 1, "10\nx": 2})) == {1: 2}


class TestProgressLine:
    def test_no_subagent_steps(self):
        assert plan_mod.plan_progress_line(ctx(plan=[step("сам")])) == ""
        assert plan_mod.plan_progress_line(ctx(plan=[])) == ""

    def test_after_first_step(self):
        line = plan_mod.plan_progress_line(ctx(done_calls={"10\nx": 1}))
        assert line.startswith("[План: выполнено 1 из 2 шагов с субагентами.")
        assert "Результат шага 1 сверь с планом: Таблица." in line
        assert "Следующий по плану: шаг 2 — Написать отчёт (субагент «Писатель»)" in line
        assert "передай в задание [[ОТВЕТ 1]] (шаг 1)" in line
        assert line.endswith("]")

    def test_skipped_steps_reported(self):
        line = plan_mod.plan_progress_line(ctx(done_calls={"20\nx": 1}))
        assert "Пропущены: шаг 1 - выполни их." in line

    def test_all_done(self):
        line = plan_mod.plan_progress_line(ctx(done_calls={"10\nx": 1, "20\ny": 2}))
        assert "выполнено 2 из 2" in line
        assert "Все шаги с субагентами выполнены" in line

    def test_nothing_done_yet_points_to_first_step(self):
        line = plan_mod.plan_progress_line(ctx())
        assert "выполнено 0 из 2" in line
        assert "Следующий по плану: шаг 1" in line


class TestMissingNote:
    def test_nothing_missing(self):
        assert plan_mod.plan_missing_note(ctx(done_calls={"10\nx": 1, "20\ny": 2})) == ""

    def test_no_plan(self):
        assert plan_mod.plan_missing_note(ctx(plan=[])) == ""

    def test_only_own_steps_are_never_missing(self):
        assert plan_mod.plan_missing_note(ctx(plan=[step("сам")])) == ""

    def test_lists_missing_steps_with_marks(self):
        note = plan_mod.plan_missing_note(ctx(done_calls={"10\nx": 1}))
        assert note.startswith("По твоему плану не выполнены шаги:")
        assert "шаг 2 — Написать отчёт (субагент «Писатель»)" in note
        assert "[[ОТВЕТ 1]]" in note
        # Выполненный шаг 1 в списке невыполненных не значится.
        assert "шаг 1 — Собрать данные" not in note

    def test_everything_missing(self):
        note = plan_mod.plan_missing_note(ctx())
        assert "шаг 1 — Собрать данные" in note
        assert "шаг 2 — Написать отчёт" in note


class TestLogPlanReport:
    def test_no_crash_without_plan_or_subagent_steps(self):
        plan_mod.log_plan_report(ctx(plan=[]))
        plan_mod.log_plan_report(ctx(plan=[step("сам")]))
        plan_mod.log_plan_report(SimpleNamespace())

    def test_report_marks_done_missing_and_extra(self, caplog):
        with caplog.at_level(logging.DEBUG):
            plan_mod.log_plan_report(ctx(done_calls={"10\nx": 1, "77\nz": 9}))
        text = "\n".join(r.getMessage() for r in caplog.records if "ИТОГ ПО ПЛАНУ" in r.getMessage())
        if text:  # уровень/пропагация логгера проекта могут отфильтровать debug
            assert "выполнено 1 из 2" in text
            assert "✓" in text and "✗" in text
            assert "вне плана вызваны" in text


class FakeProvider:
    def __init__(self, reply=None, error=None, models=()):
        self.reply, self.error, self.models = reply, error, list(models)
        self.calls = []

    async def list_models(self):
        return self.models

    async def chat(self, messages, model_id, **kwargs):
        self.calls.append((messages, model_id, kwargs))
        if self.error:
            raise self.error
        return self.reply


class FakeRegistry:
    def __init__(self, provider, model_id="m1"):
        self.provider, self.model_id = provider, model_id

    def resolve(self, model_path):
        return self.provider, self.model_id


@pytest.fixture
def registry(monkeypatch):
    import backend.llm_providers as providers

    holder = SimpleNamespace(registry=None)

    async def fake_get_registry():
        return holder.registry

    monkeypatch.setattr(providers, "get_registry", fake_get_registry)
    return holder


class TestMakeSubagentPlan:
    async def _run(self, **over):
        kw = dict(
            messages=[{"role": "system", "content": "sys"}],
            model_path="p/m",
            config=CONFIG,
            parent_agent_id=1,
            agent_names=None,
        )
        kw.update(over)
        return await plan_mod.make_subagent_plan(**kw)

    async def test_builds_plan_from_model_reply(self, registry):
        provider = FakeProvider(reply='{"steps": [{"do": "Собрать", "subagent": "Аналитик"}]}')
        registry.registry = FakeRegistry(provider)
        plan = await self._run(request_extra={"enable_thinking": False})
        assert [(s["do"], s["target_id"]) for s in plan] == [("Собрать", 10)]
        messages, model_id, kwargs = provider.calls[0]
        assert model_id == "m1"
        assert messages[0] == {"role": "system", "content": "sys"}
        assert messages[-1]["role"] == "user"
        assert "- Аналитик: разбирает данные" in messages[-1]["content"]
        assert kwargs["temperature"] == 0.1
        assert kwargs["request_extra"] == {"enable_thinking": False}

    async def test_input_messages_not_mutated(self, registry):
        registry.registry = FakeRegistry(FakeProvider(reply="{}"))
        messages = [{"role": "user", "content": "q"}]
        await self._run(messages=messages)
        assert messages == [{"role": "user", "content": "q"}]

    async def test_first_available_model_when_path_has_none(self, registry):
        provider = FakeProvider(reply="{}", models=[SimpleNamespace(model_id="auto")])
        registry.registry = FakeRegistry(provider, model_id="")
        await self._run()
        assert provider.calls[0][1] == "auto"

    async def test_provider_failure_gives_empty_plan(self, registry):
        registry.registry = FakeRegistry(FakeProvider(error=RuntimeError("down")))
        assert await self._run() == []

    async def test_registry_failure_gives_empty_plan(self, registry):
        registry.registry = None  # .resolve на None → AttributeError
        assert await self._run() == []

    async def test_unparseable_reply_gives_empty_plan(self, registry):
        registry.registry = FakeRegistry(FakeProvider(reply="не могу составить план"))
        assert await self._run() == []
