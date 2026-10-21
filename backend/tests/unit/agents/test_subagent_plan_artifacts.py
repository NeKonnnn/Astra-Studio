"""agents/subagents/plan, artifacts, trace: план хода главного, перенос артефактов из ответов детей, trace-лог."""

from types import SimpleNamespace

import pytest

from backend.agents.subagents import artifacts, plan, trace
from backend.agents.subagents.settings import AgentSubagentsConfig

pytestmark = pytest.mark.unit

CONFIG = AgentSubagentsConfig(enabled=True, agent_ids=[1, 2], agent_names={1: "Таблицы - строит таблицы", 2: "Графики"})
FENCE = "`" * 3


def parse(text, parent=9):
    return plan.parse_plan(text, CONFIG, parent_agent_id=parent, agent_names=None)


class TestPlanParsing:
    def test_json_extraction_from_wrapped_text(self):
        assert plan._extract_json('Вот план:\n```json\n{"steps": [{"do": "x"}]}\n```\nготово') == {"steps": [{"do": "x"}]}
        assert plan._extract_json('мусор {не json} потом {"a": {"b": 1}}') == {"a": {"b": 1}}
        assert plan._extract_json("[1, 2]") is None
        assert plan._extract_json("") is None and plan._extract_json(None) is None
        assert plan._extract_json("{незакрытый") is None

    def test_plan_with_subagents_and_dependencies(self):
        text = (
            '{"steps": ['
            '{"do": "Собрать таблицу", "subagent": "Таблицы", "uses": [], "check": "Есть итоги."},'
            '{"do": "Нарисовать график", "subagent": "Графики", "uses": [1, 1, 5, "x"], "check": "С подписями"},'
            '{"do": "Собрать итог", "subagent": null, "uses": [1, 2]}]}'
        )

        steps = parse(text)

        assert [(s["subagent"], s["target_id"]) for s in steps] == [("Таблицы", 1), ("Графики", 2), ("", None)]
        assert steps[1]["uses"] == [1]  # будущие, дублирующиеся и мусорные номера отброшены
        assert steps[2]["uses"] == [1, 2] and steps[0]["check"] == "Есть итоги."

    def test_unknown_subagent_is_downgraded_to_a_self_step(self):
        steps = parse('{"steps": [{"do": "Сделать", "subagent": "Призрак"}]}')

        assert steps == [{"do": "Сделать", "subagent": "", "target_id": None, "uses": [], "check": ""}]

    def test_step_without_text_and_target_is_dropped(self):
        assert parse('{"steps": [{"do": "", "subagent": "Призрак"}, {"do": ""}, "str", 5, {"do": "ок"}]}')[0]["do"] == "ок"

    def test_step_with_only_a_known_subagent_uses_its_name_as_the_text(self):
        steps = parse('{"steps": [{"subagent": "Таблицы"}]}')

        assert steps[0]["do"] == "Таблицы" and steps[0]["target_id"] == 1

    def test_self_subagent(self):
        steps = parse('{"steps": [{"do": "Сам", "subagent": "self"}]}', parent=9)

        assert steps[0]["target_id"] == 9

    @pytest.mark.parametrize("text", ["", "не JSON", '{"steps": "x"}', '{"other": []}', "[]", None])
    def test_garbage_gives_an_empty_plan(self, text):
        assert parse(text) == []

    def test_plan_is_capped_and_texts_are_trimmed(self):
        many = ",".join(['{"do": "%s"}' % ("я" * 1000)] * 60)

        steps = parse('{"steps": [%s]}' % many)

        assert len(steps) == plan.PLAN_MAX_STEPS and len(steps[0]["do"]) == 400

    @pytest.mark.parametrize(("raw", "expected"), [(None, True), ("1", True), ("", True), ("0", False), ("off", False), (" No ", False)])
    def test_flag(self, monkeypatch, raw, expected):
        monkeypatch.delenv("SUBAGENT_PLAN", raising=False)
        if raw is not None:
            monkeypatch.setenv("SUBAGENT_PLAN", raw)

        assert plan.plan_enabled() is expected


def sample_plan():
    return [
        {"do": "Собрать таблицу", "subagent": "Таблицы", "target_id": 1, "uses": [], "check": "Есть итоги."},
        {"do": "Нарисовать график", "subagent": "Графики", "target_id": 2, "uses": [1], "check": ""},
        {"do": "Собрать итог", "subagent": "", "target_id": None, "uses": [1, 2], "check": ""},
    ]


def ctx(done=None, plan_steps=None):
    return SimpleNamespace(plan=sample_plan() if plan_steps is None else plan_steps, done_calls=done or {}, agent_names={1: "Таблицы - x"})


class TestPlanProgress:
    def test_system_block(self):
        block = plan.plan_system_block(sample_plan())

        assert block.startswith("ТВОЙ ПЛАН") and "1. Собрать таблицу — субагент «Таблицы»" in block
        assert "2. Нарисовать график — субагент «Графики» — опирается на шаги 1" in block
        assert "Проверка: Есть итоги." in block and "3. Собрать итог — сам — опирается на шаги 1, 2" in block
        assert plan.plan_system_block([]) == ""

    def test_steps_are_matched_with_answers(self):
        assert plan._step_answers(ctx({"1\nзадание": 1, "2\nзадание": 2})) == {1: 1, 2: 2}

    def test_repeated_calls_of_one_subagent_are_dealt_in_plan_order(self):
        two_steps = [
            {"do": "a", "subagent": "Т", "target_id": 1, "uses": [], "check": ""},
            {"do": "b", "subagent": "Т", "target_id": 1, "uses": [], "check": ""},
        ]

        assert plan._step_answers(ctx({"1\nпервое": 4, "1\nвторое": 7}, two_steps)) == {1: 4, 2: 7}

    def test_malformed_done_keys_are_ignored(self):
        assert plan._step_answers(ctx({"x\nзадание": 1, "1\nзадание": 2})) == {1: 2}

    def test_progress_after_the_first_answer(self):
        line = plan.plan_progress_line(ctx({"1\nz": 1}))

        assert line.startswith("[План: выполнено 1 из 2 шагов") and "сверь с планом: Есть итоги" in line
        assert "Следующий по плану: шаг 2 — Нарисовать график" in line and "[[ОТВЕТ 1]] (шаг 1)" in line

    def test_progress_reports_skipped_steps(self):
        line = plan.plan_progress_line(ctx({"2\nz": 1}))

        assert "Пропущены: шаг 1" in line

    def test_progress_when_everything_is_done(self):
        assert "Все шаги с субагентами выполнены" in plan.plan_progress_line(ctx({"1\nz": 1, "2\nz": 2}))

    def test_no_progress_line_without_subagent_steps(self):
        assert plan.plan_progress_line(ctx(plan_steps=[])) == ""
        assert plan.plan_progress_line(ctx(plan_steps=[{"do": "сам", "subagent": "", "target_id": None, "uses": [], "check": ""}])) == ""

    def test_missing_note(self):
        note = plan.plan_missing_note(ctx({"1\nz": 1}))

        assert "шаг 2 — Нарисовать график" in note and "шаг 1 —" not in note and "[[ОТВЕТ 1]] (шаг 1)" in note
        assert plan.plan_missing_note(ctx({"1\nz": 1, "2\nz": 2})) == "" and plan.plan_missing_note(ctx(plan_steps=[])) == ""

    def test_log_text(self):
        text = plan.plan_log_text(sample_plan())

        assert "шагов 3, с субагентами 2" in text and "[сам] Собрать итог" in text and "опирается на шаги 1, 2" in text

    def test_plan_report_never_fails(self, caplog):
        caplog.set_level("DEBUG")
        plan.log_plan_report(ctx({"1\nz": 1, "9\nvне плана": 2}))
        plan.log_plan_report(ctx(plan_steps=[]))
        plan.log_plan_report(SimpleNamespace())

    def test_agents_list_for_the_planning_prompt(self):
        assert plan._agents_list(CONFIG, None) == "- Таблицы: строит таблицы\n- Графики"
        assert plan._agents_list(AgentSubagentsConfig(), None) == "- (нет)"


class TestMakePlan:
    @pytest.fixture
    def registry(self, monkeypatch):
        import backend.llm_providers as providers

        state = SimpleNamespace(answer='{"steps": [{"do": "Собрать таблицу", "subagent": "Таблицы"}]}', error=None, chats=[], model_id="m1", models=[])

        class Provider:
            async def chat(self, messages, model, **kwargs):
                state.chats.append((messages, model, kwargs))
                if state.error:
                    raise state.error
                return state.answer

            async def list_models(self):
                return state.models

        class Registry:
            def resolve(self, path):
                return Provider(), state.model_id

        async def get_registry():
            return Registry()

        monkeypatch.setattr(providers, "get_registry", get_registry)
        return state

    async def make(self, **kw):
        return await plan.make_subagent_plan(messages=[{"role": "user", "content": "q"}], model_path="gw/m1", config=CONFIG, parent_agent_id=9, agent_names=None, **kw)

    async def test_plan_request_is_appended_to_the_dialog(self, registry):
        steps = await self.make(request_extra={"enable_thinking": False})

        assert [s["target_id"] for s in steps] == [1]
        messages, model, kwargs = registry.chats[0]
        assert model == "m1" and messages[0] == {"role": "user", "content": "q"} and "- Таблицы: строит таблицы" in messages[-1]["content"]
        assert kwargs["temperature"] == 0.1 and kwargs["request_extra"] == {"enable_thinking": False}

    async def test_first_model_is_used_when_the_path_has_no_model(self, registry):
        registry.model_id = ""
        registry.models = [SimpleNamespace(model_id="first")]

        await self.make()

        assert registry.chats[0][1] == "first"

    async def test_failures_give_an_empty_plan(self, registry):
        registry.error = RuntimeError("llm down")
        assert await self.make() == []

        registry.error = None
        registry.answer = "я не умею в JSON"
        assert await self.make() == []


class TestArtifactBlocks:
    def block(self, ident="a1", body="<h1>x</h1>"):
        return f':::artifact{{identifier="{ident}" type="text/html" title="T"}}\n{FENCE}html\n{body}\n{FENCE}\n:::'

    def test_closed_blocks(self):
        text = "до\n" + self.block("a1") + "\nпосле\n" + self.block("a2")

        assert [b.split('"')[1] for b in artifacts.subagent_artifact_blocks(text)] == ["a1", "a2"]

    def test_unclosed_block_is_closed_together_with_its_fence(self):
        text = f':::artifact{{identifier="a1" type="text/html" title="T"}}\n{FENCE}html\n<p>x</p>\n'

        (block,) = artifacts.subagent_artifact_blocks(text)

        assert block.endswith(f"\n{FENCE}\n:::") and block.count(FENCE) == 2

    def test_unclosed_block_stops_at_the_next_header(self):
        text = f':::artifact{{identifier="a1" type="t" title="T"}}\nтело1\n:::artifact{{identifier="a2" type="t" title="T"}}\nтело2\n:::\n'

        blocks = artifacts.subagent_artifact_blocks(text)

        assert len(blocks) == 2 and "тело2" not in blocks[0] and blocks[0].endswith("\n:::")

    def test_bare_html_document_in_a_fence_is_wrapped(self):
        text = f"Вот:\n{FENCE}html\n<!DOCTYPE html><html><title>Отчёт</title><body>x</body></html>\n{FENCE}\n"

        (block,) = artifacts.subagent_artifact_blocks(text)

        assert block.startswith(':::artifact{identifier="subagent-html-') and 'title="Отчёт"' in block and block.endswith(":::")

    def test_html_snippets_are_not_wrapped(self):
        assert artifacts.subagent_artifact_blocks(f"{FENCE}html\n<p>фрагмент</p>\n{FENCE}") == []

    @pytest.mark.parametrize("value", [None, "", "  ", "простой текст", 5])
    def test_nothing_to_carry(self, value):
        assert artifacts.subagent_artifact_blocks(value) == []


def end(result="", tool="subagent", urls=None):
    return {"type": "mcp_tool_end", "tool": tool, "result": result, "download_urls": urls or []}


class TestCarryArtifacts:
    def block(self, ident):
        return f':::artifact{{identifier="{ident}" type="text/html" title="T"}}\nтело\n:::'

    def test_missing_artifacts_are_appended(self):
        out = artifacts.carry_subagent_artifacts("Итог родителя", [end(self.block("a1"))])

        assert out.startswith("Итог родителя") and 'identifier="a1"' in out

    def test_artifacts_the_parent_already_reproduced_are_not_duplicated(self):
        parent = "Итог\n\n" + self.block("a1")

        assert artifacts.carry_subagent_artifacts(parent, [end(self.block("a1"))]) == parent

    def test_identical_artifact_from_two_children_is_added_once(self):
        out = artifacts.carry_subagent_artifacts("x", [end(self.block("a1")), end(self.block("a1"))])

        assert out.count('identifier="a1"') == 1

    def test_only_subagent_results_carry_artifacts(self):
        assert artifacts.carry_subagent_artifacts("x", [end(self.block("a1"), tool="other")]) == "x"

    def test_download_links_are_added_once(self):
        links = [{"url": "https://x/a.pptx", "label": "Deck"}, {"url": "https://x/a.pptx", "label": "Dup"}, {"url": "", "label": "empty"}, "junk"]

        out = artifacts.carry_subagent_artifacts("Итог", [end("", tool="mcp", urls=links)])

        assert out.count("https://x/a.pptx") == 1 and "**Файлы:**" in out

    def test_links_already_in_the_answer_are_skipped(self):
        text = "Скачать: https://x/a.pptx"

        assert artifacts.carry_subagent_artifacts(text, [end("", urls=[{"url": "https://x/a.pptx"}])]) == text

    @pytest.mark.parametrize("events", [None, [], ["x", 5, {"type": "other"}]])
    def test_no_events_means_no_change(self, events):
        assert artifacts.carry_subagent_artifacts("текст", events) == "текст"

    def test_none_response(self):
        assert artifacts.carry_subagent_artifacts(None, None) == ""


class TestTrace:
    def test_files_are_counted_per_document(self):
        hits = {"hits": [{"file": "a.pdf"}, {"file": "a.pdf"}, {"file": "b.pdf"}, {"x": 1}, "junk"]}

        assert trace._trace_files(hits) == {"a.pdf": 2, "b.pdf": 1}
        assert trace._trace_files(None) == {} and trace._trace_files({"hits": None}) == {}

    def test_scope_description(self):
        assert trace._scope_log_part({}) == "нет"
        assert trace._scope_log_part({"_scope_info": {"rule": "по имени", "files": ["a"], "missing": ["b"]}}) == "правило «по имени» файлы=['a'] не_найдены=['b']"

    def test_trace_line_reports_card_presence(self, monkeypatch):
        lines = []
        monkeypatch.setattr(trace.log, "debug", lambda fmt, *args: lines.append(fmt % args))
        trace._log_subagent_trace(
            target_agent_id=3, child_profile={"name": "Таблицы", "system_prompt": "Ты строишь таблицы", "kb_document_ids": [1], "file_search_enabled": True},
            parent_profile={"agent_id": 1}, depth=1, task="задание", question_added=True, shared_kb_ids=[5, 6], shared_trace={"hits": [{"file": "a"}]},
            child_kb_trace={"hits": []}, system_prompt="Ты строишь таблицы\n+навыки", prompt="вопрос\nДОСТУПНЫЕ ФАЙЛЫ: a",
        )

        message = next(line for line in lines if "SUBAGENT-TRACE" in line)
        assert "в системном промпте=да" in message and "вопрос пользователя передан=да" in message and "перечень файлов=да" in message

    def test_trace_survives_broken_input(self, caplog):
        trace._log_subagent_trace(
            target_agent_id=3, child_profile=None, parent_profile=None, depth=0, task=None, question_added=False,
            shared_kb_ids=[], shared_trace=None, child_kb_trace=None, system_prompt="", prompt="",
        )
