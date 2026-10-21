"""Цепочка агентов: промпты шагов, поисковый запрос шага, общий RAG, debug-лог."""

import logging
from types import SimpleNamespace

import pytest

from backend.agents import shared_rag, step_debug
from backend.agents.chain import prompts, step_search


def _steps():
    return [
        {"agent_name": "Аналитик", "content": " цифры "},
        {"agent_name": "", "content": "вывод"},
    ]


class TestRunBuffer:
    def test_question_only(self):
        assert prompts.format_run_buffer("  вопрос ", []) == (
            "Запрос пользователя:\n«вопрос»"
        )

    def test_answers_are_numbered_with_marks(self):
        text = prompts.format_run_buffer("q", _steps(), with_marks=True)
        assert prompts.CHAIN_ANSWERS_HEADER in text
        assert "### Ответ агента «Аналитик» [[ОТВЕТ 1]]\nцифры" in text
        assert "### Ответ агента «Агент» [[ОТВЕТ 2]]\nвывод" in text

    def test_no_marks_by_default(self):
        assert "[[ОТВЕТ" not in prompts.format_run_buffer("q", _steps())


class TestMessages:
    def test_first_step_with_role(self):
        msg = prompts.build_first_step_message("q", agent_name="А", total=3)
        assert msg.startswith("q\n\n")
        assert "цепочка из 3 агентов" in msg and "«А»" in msg
        assert msg.endswith(prompts.CHAIN_NOT_LAST_NOTE)

    def test_first_step_without_role(self):
        msg = prompts.build_first_step_message("q")
        assert msg == f"q\n\n{prompts.CHAIN_NOT_LAST_NOTE}"

    def test_following_step_default_template(self):
        msg = prompts.build_chain_user_message(
            "q", _steps()[:1], agent_name="Б", total=3
        )
        assert "Ты - агент «Б», шаг 2 из 3" in msg
        assert "{role}" not in msg and "{convo}" not in msg
        assert prompts.CHAIN_NOT_LAST_NOTE not in msg
        assert prompts.CHAIN_MARKS_NOTE not in msg

    def test_not_last_and_marks_notes(self):
        msg = prompts.build_chain_user_message(
            "q", _steps(), with_marks=True, is_last=False
        )
        assert prompts.CHAIN_NOT_LAST_NOTE in msg
        assert msg.endswith(prompts.CHAIN_MARKS_NOTE)
        assert "Ты - агент" not in msg

    def test_custom_template_without_convo_appends_it(self):
        msg = prompts.build_chain_user_message("q", [], prompt_template="Сделай")
        assert msg.startswith("Сделай\n\n")
        assert "Запрос пользователя:" in msg

    def test_custom_template_with_convo(self):
        msg = prompts.build_chain_user_message(
            "q", [], prompt_template="[{role}]{convo}!"
        )
        assert msg.startswith("[]Запрос") and msg.endswith("!")

    @pytest.mark.parametrize("tpl", ["", "   ", None])
    def test_blank_template_falls_back_to_default(self, tpl):
        msg = prompts.build_chain_user_message("q", [], prompt_template=tpl)
        assert "Выполни свою часть работы" in msg


class TestVisibleContent:
    def test_empty(self):
        assert prompts.format_visible_chain_content([], hide_sequential_outputs=False) == ""

    def test_hidden_shows_only_last(self):
        assert (
            prompts.format_visible_chain_content(_steps(), hide_sequential_outputs=True)
            == "вывод"
        )

    def test_all_steps_with_headers(self):
        text = prompts.format_visible_chain_content(
            _steps(), hide_sequential_outputs=False
        )
        assert text == "**▸ Аналитик**\n\nцифры\n\n**▸ Агент**\n\nвывод"

    def test_header_default_name(self):
        assert prompts.chain_step_header("  ") == "**▸ Агент**\n\n"

    def test_stream_prefixes(self):
        assert prompts.iter_chain_stream_prefixes([], "А", hide_sequential_outputs=True) == ("", "")
        prefix, header = prompts.iter_chain_stream_prefixes(
            [], "А", hide_sequential_outputs=False
        )
        assert prefix == header == "**▸ А**\n\n"
        prefix, header = prompts.iter_chain_stream_prefixes(
            _steps()[:1], "Б", hide_sequential_outputs=False
        )
        assert prefix == "**▸ Аналитик**\n\nцифры\n\n**▸ Б**\n\n"
        assert header == "**▸ Б**\n\n"


class TestCleanStepQuery:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("  Запрос: «выручка 2024»  ", "выручка 2024"),
            ("Поисковый запрос: ревизия", "ревизия"),
            ("<think>рассуждаю\nещё</think>\nответ", "ответ"),
            ("<think>не закрыт", ""),
            ("строка 1\n\n  строка 2 \n", "строка 1 строка 2"),
            ("**`q`**", "q"),
            (None, ""),
        ],
    )
    def test_cases(self, raw, expected):
        assert step_search.clean_step_query(raw) == expected

    def test_truncated(self):
        out = step_search.clean_step_query("а" * 900)
        assert len(out) == step_search.STEP_QUERY_MAX_CHARS


class _Provider:
    def __init__(self, reply="найди X", models=None, boom=False):
        self.reply, self.boom, self._models = reply, boom, models or []
        self.calls = []

    async def chat(self, messages, model_id, **kw):
        if self.boom:
            raise RuntimeError("llm down")
        self.calls.append((messages, model_id, kw))
        return self.reply

    async def list_models(self):
        return self._models


class _Registry:
    def __init__(self, provider, model_id="m1"):
        self.provider, self.model_id, self.resolved = provider, model_id, []

    def resolve(self, path):
        self.resolved.append(path)
        return self.provider, self.model_id


@pytest.fixture
def registry(monkeypatch):
    holder = {}

    async def _get():
        return holder["reg"]

    monkeypatch.setattr("backend.llm_providers.get_registry", _get)
    monkeypatch.setattr("backend.app_state.get_current_model_path", lambda: "cur/model")

    def install(provider, model_id="m1"):
        holder["reg"] = _Registry(provider, model_id)
        return holder["reg"]

    return install


class TestChainStepSearchQuery:
    async def test_disabled_returns_question(self, monkeypatch):
        monkeypatch.setenv("AGENT_CHAIN_STEP_QUERY", "0")
        out = await step_search.chain_step_search_query(
            {"system_prompt": "s"}, "вопрос", step_label="x"
        )
        assert out == "вопрос"

    async def test_no_system_prompt_returns_question(self):
        out = await step_search.chain_step_search_query(None, "вопрос", step_label="x")
        assert out == "вопрос"

    async def test_builds_query_with_model_reply(self, registry):
        prov = _Provider(reply="Запрос: «отчёт ACME»")
        reg = registry(prov)
        out = await step_search.chain_step_search_query(
            {"system_prompt": " инструкция ", "model_path": "agent/model"},
            "  вопрос ",
            step_label="x",
        )
        assert out == "отчёт ACME"
        assert reg.resolved == ["agent/model"]
        messages, model_id, kw = prov.calls[0]
        assert model_id == "m1"
        assert messages[0] == {"role": "system", "content": "инструкция"}
        assert "«вопрос»" in messages[1]["content"]
        assert kw["max_tokens"] == 256 and kw["temperature"] == 0.1

    async def test_falls_back_to_current_model_path(self, registry):
        reg = registry(_Provider())
        await step_search.chain_step_search_query(
            {"system_prompt": "s"}, "q", step_label="x"
        )
        assert reg.resolved == ["cur/model"]

    async def test_long_question_is_trimmed(self, registry):
        prov = _Provider()
        registry(prov)
        await step_search.chain_step_search_query(
            {"system_prompt": "s"}, "я" * 5000, step_label="x"
        )
        user = prov.calls[0][0][1]["content"]
        assert "я" * 4000 + " …" in user and "я" * 4001 not in user

    async def test_model_id_taken_from_list_when_unresolved(self, registry):
        prov = _Provider(models=[SimpleNamespace(model_id="first")])
        registry(prov, model_id="")
        await step_search.chain_step_search_query(
            {"system_prompt": "s"}, "q", step_label="x"
        )
        assert prov.calls[0][1] == "first"

    async def test_no_models_uses_empty_id(self, registry):
        prov = _Provider(models=[])
        registry(prov, model_id="")
        await step_search.chain_step_search_query(
            {"system_prompt": "s"}, "q", step_label="x"
        )
        assert prov.calls[0][1] == ""

    async def test_provider_error_falls_back(self, registry):
        registry(_Provider(boom=True))
        out = await step_search.chain_step_search_query(
            {"system_prompt": "s"}, "вопрос", step_label="x"
        )
        assert out == "вопрос"

    async def test_empty_reply_falls_back(self, registry):
        registry(_Provider(reply="<think>только думы</think>"))
        out = await step_search.chain_step_search_query(
            {"system_prompt": "s"}, "вопрос", step_label="x"
        )
        assert out == "вопрос"


class TestStepSources:
    def test_counts_by_file(self):
        ds = {"hits": [{"file": "a.pdf"}, {"file": "b.pdf"}, {"file": "a.pdf"}, {}, "x"]}
        assert step_search.step_sources_text(ds) == "a.pdf: 2, b.pdf: 1, ?: 1"

    @pytest.mark.parametrize("ds", [None, {}, {"hits": []}, "x", {"hits": None}])
    def test_nothing_found(self, ds):
        assert step_search.step_sources_text(ds) == "не найдено"

    def test_log_does_not_raise(self):
        step_search.log_step_sources("шаг", {"hits": [{"file": "a"}]})


class TestSharedRag:
    def test_head_ids(self):
        head = {"shared_chain_rag": True, "file_search_enabled": True, "kb_document_ids": [1, "2"]}
        assert shared_rag.head_shared_kb_ids(head) == [1, "2"]

    @pytest.mark.parametrize(
        "head",
        [
            None,
            "x",
            {},
            {"shared_chain_rag": False, "file_search_enabled": True, "kb_document_ids": [1]},
            {"shared_chain_rag": True, "file_search_enabled": False, "kb_document_ids": [1]},
            {"shared_chain_rag": True, "file_search_enabled": True, "kb_document_ids": "1"},
            {"shared_chain_rag": True, "file_search_enabled": True},
        ],
    )
    def test_head_ids_empty(self, head):
        assert shared_rag.head_shared_kb_ids(head) == []

    def test_accepts_parent_by_default(self):
        assert shared_rag.accepts_parent_shared_rag({}) is True
        assert shared_rag.accepts_parent_shared_rag("x") is True
        assert shared_rag.accepts_parent_shared_rag({"use_parent_shared_rag": None}) is True
        assert shared_rag.accepts_parent_shared_rag({"use_parent_shared_rag": False}) is False

    def test_child_ids_inherited_have_priority(self):
        parent = {shared_rag.SHARED_RAG_KEY: [1, "2", " -3 ", "x", None, 4.5, True]}
        # bool - подкласс int: str(True) не цифра, 4.5 не int/str
        assert shared_rag.shared_kb_ids_for_child(parent) == [1, 2, -3]

    def test_child_ids_from_head_profile(self):
        parent = {"shared_chain_rag": True, "file_search_enabled": True, "kb_document_ids": ["5", "bad", 7]}
        assert shared_rag.shared_kb_ids_for_child(parent) == [5, 7]

    def test_child_ids_garbage(self):
        assert shared_rag.shared_kb_ids_for_child(None) == []
        assert shared_rag.shared_kb_ids_for_child({}) == []
        assert shared_rag.shared_kb_ids_for_child({shared_rag.SHARED_RAG_KEY: []}) == []


class TestStepDebug:
    def test_trunc_and_args(self):
        assert step_debug._trunc("a\nb") == "a b"
        assert step_debug._trunc("x" * 20, 5) == "xxxx…"
        assert step_debug._trunc(None) == ""
        assert step_debug._args_preview({"к": 1}) == '{"к": 1}'
        assert step_debug._args_preview({"o": object()}).startswith('{"o": "<object')

    def test_phase_label_known_and_unknown(self):
        assert step_debug._phase_label("skills") == "подготовка: навыки (skills)"
        assert step_debug._phase_label("zzz") == "zzz"

    @pytest.mark.parametrize(
        "profile,expected",
        [
            ({"recursion_limit": 7}, "настройка агента (7)"),
            ({"recursion_limit": " 9 "}, "настройка агента (9)"),
            ({"recursion_limit": 0}, "глобальный"),
            ({"recursion_limit": "abc"}, "глобальный"),
            (None, "глобальный"),
        ],
    )
    def test_limit_source(self, profile, expected):
        assert step_debug.describe_limit_source(profile).startswith(expected)

    def test_all_loggers_emit(self, caplog):
        caplog.set_level(logging.DEBUG)
        step_debug.log_pre_loop(phase="skills", chat_id="c", agent_id=1, recursion_limit=5, limit_source="agent", detail="d")
        step_debug.log_pre_loop(phase="custom")
        step_debug.log_run_start(mode="plain", max_iterations=3, model="m", tool_names=[f"t{i}" for i in range(15)], mcp_servers=["s"])
        step_debug.log_llm_call(step=1, limit=3, kind="plain", model="m", has_tools_schema=True)
        step_debug.log_llm_result(step=1, limit=3, has_tool_calls=True, tool_names=["a", "b"], duration_ms=5)
        step_debug.log_llm_result(step=2, limit=3, has_tool_calls=False, content_preview="итог")
        step_debug.log_tool_call(step=1, limit=3, tool="t", arguments={"a": 1})
        step_debug.log_tool_call(step=1, limit=3, tool="t")
        step_debug.log_tool_result(step=1, limit=3, tool="t", success=True, result_preview="ok")
        step_debug.log_tool_result(step=1, limit=3, tool="t", success=False)
        step_debug.log_run_end(mode="plain", steps_used=3, limit=3, tool_calls_executed=2, reason="max_iterations")
        step_debug.log_chain_hop(index=1, total=2)
        for phase in ("start", "done", "other"):
            step_debug.log_plain_llm(phase=phase, recursion_limit=4, duration_ms=3, content_preview="p", detail="d")
        text = caplog.text
        assert "(+3)" in text
        assert "упёрлись в лимит: да" in text
        assert "ошибка инструмента" in text and "неизвестная ошибка" in text
        assert "Итог по шагам: использовано 1 из 4" in text
        assert "лимит шагов=—" in text

    def test_run_end_not_capped(self, caplog):
        caplog.set_level(logging.INFO)
        step_debug.log_run_end(mode="x", steps_used=1, limit=5, tool_calls_executed=0, reason="final_answer")
        assert "упёрлись в лимит: нет" in caplog.text and "осталось 4" in caplog.text

    def test_plain_llm_invalid_limit_defaults_to_one(self, caplog):
        caplog.set_level(logging.INFO)
        step_debug.log_plain_llm(phase="start", recursion_limit=None)
        assert "Шаг 1/1" in caplog.text
