"""rag_query/pipeline: предобработка запроса — опечатки, multi-query, HyDE, контекст диалога, переписывание."""

import json

import pytest

from backend.rag_query import pipeline as pl

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_context():
    """Контекст диалога живёт в contextvars — сбрасываем между тестами."""
    pl.set_rag_dialog_context([], None)
    yield
    pl.set_rag_dialog_context([], None)


@pytest.fixture
def llm(monkeypatch):
    answers, prompts = [], []

    async def fake(prompt, system, max_tokens=512):
        prompts.append((prompt, system, max_tokens))
        answer = answers.pop(0) if answers else ""
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(pl, "_llm_short", fake)
    fake.answers, fake.prompts = answers, prompts
    return fake


class TestEnvHelpers:
    @pytest.mark.parametrize("raw,expected", [(None, 2000), ("500", 500), ("10", 200), ("99999", 8000), ("x", 2000)])
    def test_hyde_limit(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("RAG_HYDE_MAX_CHARS", raising=False)
        else:
            monkeypatch.setenv("RAG_HYDE_MAX_CHARS", raw)
        assert pl._hyde_max_chars() == expected

    @pytest.mark.parametrize("raw,expected", [(None, 0.6), ("0.8", 0.8), ("0", 0.0), ("5", 1.0), ("-1", 0.0), ("x", 0.6)])
    def test_typos_similarity(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("RAG_TYPOS_MIN_SIMILARITY", raising=False)
        else:
            monkeypatch.setenv("RAG_TYPOS_MIN_SIMILARITY", raw)
        assert pl._typos_min_similarity() == expected

    @pytest.mark.parametrize("name,fn", [("RAG_QUERY_CONTEXTUALIZE", pl._contextualize_enabled), ("RAG_QUERY_REWRITE", pl._rewrite_enabled)])
    @pytest.mark.parametrize("raw,expected", [(None, True), ("1", True), ("0", False), ("false", False), ("No", False), ("OFF", False), ("yes", True)])
    def test_switches(self, monkeypatch, name, fn, raw, expected):
        if raw is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, raw)
        assert fn() is expected

    def test_cut(self):
        assert pl._cut("a\nb  c") == "a b c"
        assert pl._cut("x" * 400, 10) == "x" * 10 + "…(+390 симв.)"
        assert pl._cut(None) == ""


class TestFollowUpDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "посмотри в файле smeta.xlsx",
            "А что там по срокам договора поставки у нас было вообще вчера?",
            "Что включено, а также какие условия оплаты предусмотрены для клиента?",
            "Какие штрафы предусмотрены в нём для поставщика при просрочке платежа?",
        ],
    )
    def test_followups(self, text):
        assert pl.looks_like_followup(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Какие штрафы предусмотрены договором поставки при просрочке платежа поставщиком?",
            "Расскажите подробнее об устройстве системы хранения данных в компании сегодня",
            "",
            "   ",
        ],
    )
    def test_standalone_or_empty(self, text):
        assert pl.looks_like_followup(text) is False

    def test_short_question_is_always_a_followup(self):
        assert pl.looks_like_followup("сроки?") is True


class TestDialogContext:
    def test_previous_user_question_is_remembered(self):
        pl.set_rag_dialog_context(
            [
                {"role": "user", "content": "Первый  вопрос"},
                {"role": "assistant", "content": "ответ"},
                {"role": "user", "content": "Второй"},
            ],
            current="Текущий",
        )
        assert pl._prev_user_question.get() == "Второй"

    def test_current_message_is_not_its_own_predecessor(self):
        pl.set_rag_dialog_context(
            [{"role": "user", "content": "старый"}, {"role": "user", "content": "текущий"}], current="  текущий "
        )
        assert pl._prev_user_question.get() == "старый"

    def test_context_is_reset_by_an_empty_history(self):
        pl.set_rag_dialog_context([{"role": "user", "content": "что-то"}])
        pl.set_rag_dialog_context([], None)
        assert pl._prev_user_question.get() is None and pl._dialog_turns.get() is None

    def test_junk_entries_are_ignored_and_long_question_is_cut(self):
        pl.set_rag_dialog_context(
            ["junk", {"role": "system", "content": "s"}, {"role": "user", "content": "я" * 1000}, {"role": "user", "content": ""}]
        )
        assert len(pl._prev_user_question.get()) == 300

    def test_turns_for_rewriting_include_assistant_replies(self):
        history = [
            {"role": "user", "content": "в файле smeta.xlsx есть итоги"},
            {"role": "assistant", "content": "да, есть раздел"},
            {"role": "tool", "content": "x"},
        ]
        pl.set_rag_dialog_context(history)
        assert pl._dialog_turns.get() == ["Пользователь: в файле smeta.xlsx есть итоги", "Ассистент: да, есть раздел"]

    def test_turns_are_limited_to_the_tail(self):
        history = [{"role": "user", "content": f"q{i}"} for i in range(20)]
        pl.set_rag_dialog_context(history)
        assert len(pl._dialog_turns.get()) == 8 and pl._dialog_turns.get()[-1] == "Пользователь: q19"

    def test_rewrite_cache_is_reset_for_each_message(self):
        pl._rewritten_query.set("старое")
        pl.set_rag_dialog_context([{"role": "user", "content": "x"}])
        assert pl._rewritten_query.get() is None


class TestProcessUserQuery:
    async def test_plain_query_is_only_normalised(self, llm):
        out = await pl.process_user_query("  Как   оформить   отпуск?  ")
        assert out.original == "  Как   оформить   отпуск?  " and out.query_for_search == out.normalized
        assert out.vector_query is None and out.multi_variants is None and llm.prompts == []

    async def test_empty_input(self, llm):
        out = await pl.process_user_query("")
        assert out.original == "" and out.query_for_search == ""

    async def test_typo_fix_is_applied_when_similar_enough(self, llm):
        llm.answers.append("Как оформить отпуск")
        out = await pl.process_user_query("Как оформить отпск", fix_typos=True)
        assert out.query_for_search == "Как оформить отпуск"

    async def test_rephrasing_instead_of_fixing_is_rejected(self, llm):
        llm.answers.append("Совершенно другая формулировка про мониторинг гарантий клиента")
        out = await pl.process_user_query("Мы выдаем БГ новому клиенту", fix_typos=True)
        assert out.query_for_search == "Мы выдаем БГ новому клиенту"

    @pytest.mark.parametrize("answer", ["", "а", "x" * 5000, "\n"])
    async def test_implausible_answers_are_rejected(self, llm, answer):
        llm.answers.append(answer)
        out = await pl.process_user_query("обычный запрос", fix_typos=True)
        assert out.query_for_search == "обычный запрос"

    async def test_only_the_first_line_of_the_fix_is_used(self, llm):
        llm.answers.append("как оформить отпуск\nПояснение: исправил")
        out = await pl.process_user_query("как оформить отпск", fix_typos=True)
        assert out.query_for_search == "как оформить отпуск"

    async def test_similarity_check_can_be_disabled(self, llm, monkeypatch):
        monkeypatch.setenv("RAG_TYPOS_MIN_SIMILARITY", "0")
        llm.answers.append("совсем другое")
        out = await pl.process_user_query("исходный текст", fix_typos=True)
        assert out.query_for_search == "совсем другое"

    async def test_typo_step_errors_keep_the_original_query(self, llm):
        llm.answers.append(RuntimeError("down"))
        out = await pl.process_user_query("запрос", fix_typos=True)
        assert out.query_for_search == "запрос"

    async def test_multi_query_variants(self, llm):
        llm.answers.append('Вот: {"variants": ["иначе 1", "  иначе 2 ", "", 5]}')
        out = await pl.process_user_query("запрос", multi_query=True)
        assert out.multi_variants == ["иначе 1", "иначе 2", "5"]

    @pytest.mark.parametrize("raw", ["", "нет json", '{"variants": []}', '{"variants": "x"}', "{broken"])
    async def test_multi_query_without_usable_variants(self, llm, raw):
        llm.answers.append(raw)
        assert (await pl.process_user_query("запрос", multi_query=True)).multi_variants is None

    async def test_multi_query_variants_are_capped_at_six(self, llm):
        llm.answers.append(json.dumps({"variants": [f"v{i}" for i in range(10)]}))
        assert len((await pl.process_user_query("запрос", multi_query=True)).multi_variants) == 6

    async def test_hyde_builds_a_vector_query(self, llm):
        llm.answers.append("Гипотетический ответ достаточной длины, чтобы пройти проверку.")
        out = await pl.process_user_query("запрос", hyde=True)
        assert out.vector_query.startswith("запрос\n\nГипотетический ответ")
        assert out.query_for_search == "запрос"

    async def test_short_hyde_is_ignored_and_long_one_is_cut(self, llm, monkeypatch):
        llm.answers.append("коротко")
        assert (await pl.process_user_query("запрос", hyde=True)).vector_query is None
        monkeypatch.setenv("RAG_HYDE_MAX_CHARS", "200")
        llm.answers.append("я" * 1000)
        out = await pl.process_user_query("запрос", hyde=True)
        assert len(out.vector_query) == len("запрос\n\n") + 200

    async def test_hyde_error_is_absorbed(self, llm):
        llm.answers.append(RuntimeError("down"))
        assert (await pl.process_user_query("запрос", hyde=True)).vector_query is None

    async def test_all_steps_use_separate_model_calls(self, llm):
        llm.answers += ["запрос", '{"variants": ["a"]}', "Гипотетический ответ достаточной длины для проверки."]
        out = await pl.process_user_query("запрос", fix_typos=True, multi_query=True, hyde=True)
        assert len(llm.prompts) == 3 and out.multi_variants == ["a"] and out.vector_query

    async def test_followup_vector_is_prefixed_with_the_previous_question(self, llm):
        pl.set_rag_dialog_context([{"role": "user", "content": "Какие штрафы в договоре поставки"}], current="а сроки?")
        out = await pl.process_user_query("а сроки?")
        assert out.vector_query == "Какие штрафы в договоре поставки\nа сроки?"
        assert out.query_for_search == "а сроки?"

    async def test_followup_context_extends_hyde_text_too(self, llm):
        pl.set_rag_dialog_context([{"role": "user", "content": "про штрафы"}], current="а сроки?")
        llm.answers.append("Гипотетический ответ достаточной длины, чтобы пройти проверку.")
        out = await pl.process_user_query("а сроки?", hyde=True)
        assert out.vector_query.startswith("про штрафы\nа сроки?\n\nГипотетический")

    async def test_contextualisation_can_be_switched_off(self, llm, monkeypatch):
        monkeypatch.setenv("RAG_QUERY_CONTEXTUALIZE", "0")
        pl.set_rag_dialog_context([{"role": "user", "content": "про штрафы"}], current="а сроки?")
        assert (await pl.process_user_query("а сроки?")).vector_query is None

    async def test_standalone_questions_are_not_extended(self, llm):
        pl.set_rag_dialog_context([{"role": "user", "content": "про штрафы"}], current="x")
        long_q = "Какие штрафы предусмотрены договором поставки при просрочке платежа поставщиком?"
        assert (await pl.process_user_query(long_q)).vector_query is None


class TestRewrite:
    async def test_rewrites_using_the_dialog_tail(self, llm):
        pl.set_rag_dialog_context(
            [{"role": "user", "content": "в файле smeta.xlsx есть итоги"}, {"role": "assistant", "content": "да"}],
            current="а сумма?",
        )
        llm.answers.append("Какова итоговая сумма затрат в файле smeta.xlsx")
        assert await pl.rewrite_query_with_context("а сумма?") == "Какова итоговая сумма затрат в файле smeta.xlsx"
        prompt = llm.prompts[0][0]
        assert "Переписка:" in prompt and "Пользователь: в файле smeta.xlsx есть итоги" in prompt
        assert "Последний вопрос: а сумма?" in prompt

    async def test_result_is_cached_for_the_message(self, llm):
        pl.set_rag_dialog_context([{"role": "user", "content": "про штрафы"}])
        llm.answers.append("Какие штрафы предусмотрены")
        first = await pl.rewrite_query_with_context("а какие?")
        second = await pl.rewrite_query_with_context("а какие?")
        assert first == second and len(llm.prompts) == 1

    async def test_failure_is_cached_too(self, llm):
        pl.set_rag_dialog_context([{"role": "user", "content": "про штрафы"}])
        llm.answers.append("а какие?")
        assert await pl.rewrite_query_with_context("а какие?") is None
        assert await pl.rewrite_query_with_context("а какие?") is None
        assert len(llm.prompts) == 1

    async def test_nothing_to_rewrite_with(self, llm):
        assert await pl.rewrite_query_with_context("вопрос") is None
        pl.set_rag_dialog_context([{"role": "user", "content": "x"}])
        assert await pl.rewrite_query_with_context("   ") is None
        assert llm.prompts == []

    async def test_disabled_by_env(self, llm, monkeypatch):
        monkeypatch.setenv("RAG_QUERY_REWRITE", "0")
        pl.set_rag_dialog_context([{"role": "user", "content": "x"}])
        assert await pl.rewrite_query_with_context("вопрос") is None and llm.prompts == []

    async def test_an_answer_instead_of_a_question_is_rejected(self, llm):
        pl.set_rag_dialog_context([{"role": "user", "content": "x"}])
        llm.answers.append("я" * 500)
        assert await pl.rewrite_query_with_context("коротко") is None


class TestLlmShort:
    async def test_error_text_from_the_model_is_discarded(self, monkeypatch):
        monkeypatch.setattr("backend.agent_llm_svc.ask_agent", lambda *a, **k: "Ошибка LLM (HTTP 500): boom")
        monkeypatch.setattr("backend.llm_providers.routing.is_llm_error_text", lambda text: text.startswith("Ошибка"))
        assert await pl._llm_short("p", "s") == ""

    async def test_normal_answer_passes_through_with_service_flags(self, monkeypatch):
        seen = {}

        def fake(prompt, **kw):
            seen.update(prompt=prompt, **kw)
            return "ответ"

        monkeypatch.setattr("backend.agent_llm_svc.ask_agent", fake)
        monkeypatch.setattr("backend.settings.cef_logger.cef_audit_context.chat_model_peek", lambda: "gw://m")
        assert await pl._llm_short("p", "sys", max_tokens=77) == "ответ"
        assert seen["system_prompt"] == "sys" and seen["max_tokens"] == 77 and seen["service_call"] is True
        assert seen["model_path"] == "gw://m" and seen["streaming"] is False

    async def test_empty_answer(self, monkeypatch):
        monkeypatch.setattr("backend.agent_llm_svc.ask_agent", lambda *a, **k: None)
        assert await pl._llm_short("p", "s") == ""
