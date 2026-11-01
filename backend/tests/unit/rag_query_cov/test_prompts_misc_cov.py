"""rag_query/prompts, metrics, pipeline: недостающие ветки (конфиг, флаги, сборка промпта, логирование)."""

import logging

import pytest

from backend.rag_query import metrics as mt
from backend.rag_query import pipeline as pl
from backend.rag_query import prompts as pr

pytestmark = pytest.mark.unit


@pytest.fixture
def cfg(monkeypatch):
    """Подменяет backend.config.get_config; возвращает изменяемый словарь секции rag_prompts."""
    import backend.config as bc

    section = {}
    monkeypatch.setattr(bc, "get_config", lambda: {"rag_prompts": section})
    for name in (
        "RAG_STRICT_ANSWER_MODE", "RAG_CITE_SOURCES", "RAG_ANSWER_SUMMARY",
        "RAG_ANSWER_GAPS_NOTE", "RAG_DOCUMENTS_INVENTORY",
    ):
        monkeypatch.delenv(name, raising=False)
    return section


class TestConfigAccess:
    def test_config_error_gives_empty(self, monkeypatch):
        import backend.config as bc

        def boom():
            raise RuntimeError("нет конфига")

        monkeypatch.setattr(bc, "get_config", boom)
        assert pr._prompts_config() == {}

    def test_non_dict_section_gives_empty(self, monkeypatch):
        import backend.config as bc

        monkeypatch.setattr(bc, "get_config", lambda: {"rag_prompts": ["x"]})
        assert pr._prompts_config() == {}

    def test_config_text_requires_dict_texts(self, cfg):
        cfg["texts"] = "строка"
        assert pr._config_text("not_found_message") is None

    def test_config_text_blank_ignored_and_stripped(self, cfg):
        cfg["texts"] = {"not_found_message": "   ", "citation_rule": "  свой  "}
        assert pr._config_text("not_found_message") is None
        assert pr._config_text("citation_rule") == "свой"
        assert pr._text("not_found_message") == pr._DEFAULT_TEXTS["not_found_message"]
        assert pr._text("citation_rule") == "свой"

    def test_rule_substitutes_not_found_phrase_without_format(self, cfg):
        cfg["texts"] = {
            "not_found_message": "НЕТ ДАННЫХ",
            "answer_guarantees": "Скобка { и фраза: {not_found_message}",
        }
        assert pr._rule("answer_guarantees") == "Скобка { и фраза: НЕТ ДАННЫХ"
        assert pr.rag_strict_not_found_message() == "НЕТ ДАННЫХ"


class TestFlags:
    @pytest.mark.parametrize("env,expected", [("1", True), ("on", True), ("0", False), ("Off", False)])
    def test_env_wins(self, cfg, monkeypatch, env, expected):
        cfg["cite_sources"] = not expected
        monkeypatch.setenv("RAG_CITE_SOURCES", env)
        assert pr.cite_sources_enabled() is expected

    @pytest.mark.parametrize("value,expected", [(True, True), (False, False), ("yes", True), (" NO ", False)])
    def test_config_value(self, cfg, value, expected):
        cfg["answer_summary"] = value
        assert pr.answer_summary_enabled() is expected

    def test_unrecognized_values_fall_back_to_default(self, cfg, monkeypatch):
        monkeypatch.setenv("RAG_ANSWER_GAPS_NOTE", "возможно")
        cfg["answer_gaps_note"] = "возможно"
        assert pr.answer_gaps_note_enabled() is True  # дефолт
        cfg["documents_inventory"] = 5
        assert pr.documents_inventory_enabled() is True
        assert pr.cite_sources_enabled() is False  # дефолт False

    @pytest.mark.parametrize("env,conf,expected", [
        ("strict", None, "strict"), ("HARD", None, "strict"), ("1", None, "strict"),
        ("soft", None, "soft"), (None, "strict", "strict"), (None, None, "soft"),
        (None, "что-то", "soft"),
    ])
    def test_strict_mode(self, cfg, monkeypatch, env, conf, expected):
        if env is not None:
            monkeypatch.setenv("RAG_STRICT_ANSWER_MODE", env)
        if conf is not None:
            cfg["strict_answer_mode"] = conf
        assert pr._strict_answer_mode() == expected


class TestPromptAssembly:
    def test_policy_soft_has_all_optional_blocks(self, cfg):
        out = pr._policy_rules()
        assert pr._rule("soft_context_rules") in out
        for key in ("documents_inventory_rule", "gaps_note_rule", "summary_rule"):
            assert pr._rule(key) in out

    def test_policy_strict_and_optional_blocks_off(self, cfg):
        cfg.update(strict_answer_mode="strict", documents_inventory=False,
                   answer_gaps_note=False, answer_summary=False)
        out = pr._policy_rules()
        assert out == pr._rule("strict_context_rules")
        assert pr._active_rules() == out

    def test_mandatory_rules_citations_switch(self, cfg):
        with_cite = pr.mandatory_output_rules(with_citations=True)
        no_cite = pr.mandatory_output_rules(with_citations=False)
        assert pr._rule("citation_rule") in with_cite
        assert pr._rule("no_service_markup_rule") in no_cite
        assert pr._rule("citation_rule") not in no_cite
        assert pr._rule("answer_guarantees") in with_cite and pr._rule("no_raw_chunks_rule") in no_cite

    def test_mandatory_rules_follow_config_by_default(self, cfg):
        cfg["cite_sources"] = True
        assert pr._rule("citation_rule") in pr.mandatory_output_rules()

    def test_formatting_rules_exclude_policy(self, cfg):
        out = pr.answer_formatting_rules(with_citations=False)
        assert out == "\n\n".join([pr._rule("no_service_markup_rule"), pr._rule("no_raw_chunks_rule")])
        cfg["cite_sources"] = True
        assert pr._rule("citation_rule") in pr.answer_formatting_rules()
        assert pr._rule("citation_rule") in pr.answer_formatting_rules(with_citations=True)

    def test_build_instruction(self, cfg):
        assert pr.build_rag_user_instruction() == pr._policy_rules() + "\n\n" + pr.mandatory_output_rules()

    def test_merge_without_existing(self, cfg):
        assert pr.merge_strict_rag_system_prompt(None) == pr.build_rag_user_instruction()
        assert pr.merge_strict_rag_system_prompt("   ") == pr.build_rag_user_instruction()

    def test_merge_with_custom_prompt_keeps_policy_and_precedence(self, cfg):
        out = pr.merge_strict_rag_system_prompt(None, "Ты юрист компании")
        assert out.startswith("Ты юрист компании")
        assert pr._rule("entity_prompt_precedence") in out
        assert pr._policy_rules() in out and pr._rule("answer_guarantees") in out

    def test_merge_appends_to_existing(self, cfg):
        out = pr.merge_strict_rag_system_prompt("Базовый промпт")
        assert out.startswith("Базовый промпт\n\n") and out.endswith(pr.build_rag_user_instruction())

    def test_merge_is_idempotent(self, cfg):
        once = pr.merge_strict_rag_system_prompt("Базовый")
        assert pr.merge_strict_rag_system_prompt(once) == once

    @pytest.mark.parametrize("marker", ["только предоставленный контекст (CONTEXT)", "Правила ответа по CONTEXT"])
    def test_merge_skips_when_rules_already_present(self, cfg, marker):
        existing = f"Мой промпт. {marker}."
        assert pr.merge_strict_rag_system_prompt(existing) == existing

    def test_merge_custom_ignores_marker_shortcut(self, cfg):
        existing = "Правила ответа по CONTEXT уже есть"
        out = pr.merge_strict_rag_system_prompt(existing, "Кастом")
        assert out != existing and "Кастом" in out

    def test_overview_reports_config_overrides(self, cfg):
        cfg["texts"] = {"citation_rule": "свой текст"}
        ov = pr.prompts_overview()
        assert ov["strict_answer_mode"] == "soft" and ov["cite_sources"] is False
        assert ov["answer_summary"] is True and ov["answer_gaps_note"] is True
        assert ov["texts_from_config"] == ["citation_rule"]
        assert "citation_rule" not in ov["texts_from_defaults"]
        assert len(ov["texts_from_defaults"]) == len(pr._DEFAULT_TEXTS) - 1


class TestMetricsGaps:
    def test_extract_json_invalid(self):
        # Хвост похож на объект, но невалиден; целиком - тоже не JSON.
        assert mt._extract_json_object("префикс {не json}") is None
        assert mt._extract_json_object("просто текст") is None

    async def test_ok_status_logs_debug_line(self, monkeypatch, caplog):
        async def judge(query, passages):
            return [True, True], None

        async def faith(ctx, answer):
            return 1.0, {"supported": 1, "total": 1}

        monkeypatch.setattr(mt, "judge_chunk_relevance", judge)
        monkeypatch.setattr(mt, "faithfulness_score", faith)
        monkeypatch.delenv("RAG_ONLINE_METRICS", raising=False)
        with caplog.at_level(logging.DEBUG):
            out = await mt.compute_online_rag_metrics(
                query="q", hits=[("a", 1, 1, 0), ("b", 1, 1, 1)], answer="o", context_text="c"
            )
        assert out["status"] == {"mrr": "OK", "faithfulness": "OK", "context_precision": "OK"}
        assert out["reciprocal_rank"] == 1.0 and out["relevant_chunks"] == 2


class TestPipelineGaps:
    @pytest.fixture(autouse=True)
    def _ctx(self):
        pl.set_rag_dialog_context([], None)
        yield
        pl.set_rag_dialog_context([], None)

    def test_dialog_context_skips_non_dict_messages(self):
        pl.set_rag_dialog_context([{"role": "user", "content": "прошлый вопрос"}, "не словарь", None], "новый")
        assert pl._prev_user_question.get() == "прошлый вопрос"

    async def test_multi_query_llm_failure_is_swallowed(self, monkeypatch):
        async def boom(prompt, system, max_tokens=512):
            raise RuntimeError("llm down")

        monkeypatch.setattr(pl, "_llm_short", boom)
        out = await pl.process_user_query("поиск договора", multi_query=True)
        assert out.multi_variants is None and out.query_for_search == "поиск договора"

    async def test_filters_extracted_from_query_are_returned(self, monkeypatch):
        monkeypatch.setattr(pl, "extract_filters_from_query", lambda q: {"year": 2024})
        out = await pl.process_user_query("отчёт 2024")
        assert out.filters == {"year": 2024}

    async def test_warning_only_and_no_relevant_chunks(self, monkeypatch):
        # Все чанки нерелевантны: first_relevant_rank=None; faithfulness между WARN и TARGET.
        async def judge(query, passages):
            return [False], None

        async def faith(ctx, answer):
            return 0.8, {"supported": 4, "total": 5}

        monkeypatch.setattr(mt, "judge_chunk_relevance", judge)
        monkeypatch.setattr(mt, "faithfulness_score", faith)
        monkeypatch.setattr(mt, "MRR_WARN", 0.0)
        monkeypatch.setattr(mt, "CONTEXT_PRECISION_WARN", 0.0)
        out = await mt.compute_online_rag_metrics(query="q", hits=[("a", 1, 1, 0)], answer="o", context_text="c")
        assert out["first_relevant_rank"] is None and out["relevant_chunks"] == 0
        assert out["status"]["faithfulness"] == "WARNING"
