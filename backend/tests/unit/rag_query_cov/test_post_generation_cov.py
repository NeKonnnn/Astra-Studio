"""rag_query/post_generation: faithfulness-guard и зачистка канонической фразы «не нашёл»."""

import sys
import types

import pytest

from backend.rag_query import post_generation as pg
from backend.settings.cef_logger import cef_audit_context as cef

pytestmark = pytest.mark.unit

REP = "Извините, не нашёл."


@pytest.fixture
def fake_ask_agent(monkeypatch):
    calls = []
    state = {"answer": "да", "error": None}

    def ask_agent(prompt, **kwargs):
        calls.append((prompt, kwargs))
        if state["error"]:
            raise state["error"]
        return state["answer"]

    mod = types.ModuleType("backend.agent_llm_svc")
    mod.ask_agent = ask_agent
    monkeypatch.setitem(sys.modules, "backend.agent_llm_svc", mod)
    ask_agent.calls, ask_agent.state = calls, state
    return ask_agent


class TestStrip:
    def test_empty_answer(self):
        assert pg.strip_strict_not_found_message("", REP) == ""

    def test_empty_replacement(self):
        assert pg.strip_strict_not_found_message("ответ", "") == "ответ"

    def test_exact_match_untouched(self):
        assert pg.strip_strict_not_found_message(f"  {REP} ", REP) == f"  {REP} "

    def test_phrase_removed_and_spaces_collapsed(self):
        out = pg.strip_strict_not_found_message(f"Ответ  {REP}  дальше", REP)
        assert REP not in out and out == "Ответ дальше"

    def test_replacement_with_outer_spaces_is_still_removed(self):
        # rep с краевыми пробелами не встречается в ответе дословно - чистим по rep без пробелов.
        out = pg.strip_strict_not_found_message(f"Ответ.{REP}", f" {REP} ")
        assert out == "Ответ."

    def test_nothing_left_after_cleanup_returns_original(self):
        # Очистка оставила бы пустую строку - возвращаем исходный ответ.
        assert pg.strip_strict_not_found_message(REP, f"{REP}  ") == REP

    def test_no_phrase_returns_answer(self):
        assert pg.strip_strict_not_found_message("просто ответ", REP) == "просто ответ"


class TestVerifyEnabled:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, False), ("", False), ("  ", False), ("0", False), ("False", False),
            ("no", False), ("OFF", False), ("1", True), ("true", True), ("anything", True),
        ],
    )
    def test_flag(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("RAG_POST_VERIFY", raising=False)
        else:
            monkeypatch.setenv("RAG_POST_VERIFY", raw)
        assert pg.post_verify_enabled() is expected


class TestVerifyGrounded:
    @pytest.mark.parametrize(
        "raw,expected",
        [("Да", True), ("да, обоснован", True), ("Yes", True), ("нет", False),
         ("no", False), ("", False), (None, False)],
    )
    async def test_verdicts(self, fake_ask_agent, raw, expected):
        fake_ask_agent.state["answer"] = raw
        assert await pg.verify_answer_grounded("ctx", "ans") is expected

    async def test_truncates_and_passes_model(self, fake_ask_agent):
        token = cef._chat_model.set("m-1")
        try:
            await pg.verify_answer_grounded("к" * 20000, "о" * 20000)
        finally:
            cef._chat_model.reset(token)
        prompt, kw = fake_ask_agent.calls[0]
        assert prompt.count("к") <= 12000 + 10 and prompt.count("о") <= 8000 + 200
        assert kw["model_path"] == "m-1" and kw["max_tokens"] == 8
        assert kw["service_call"] is True and kw["streaming"] is False
        assert "аудитор" in kw["system_prompt"]

    async def test_none_inputs(self, fake_ask_agent):
        assert await pg.verify_answer_grounded(None, None) is True
        assert "CONTEXT:\n\n" in fake_ask_agent.calls[0][0]

    async def test_llm_error_means_grounded(self, fake_ask_agent):
        fake_ask_agent.state["error"] = RuntimeError("down")
        assert await pg.verify_answer_grounded("c", "a") is True


class TestMaybeReplace:
    async def test_disabled_returns_cleaned(self, monkeypatch):
        monkeypatch.delenv("RAG_POST_VERIFY", raising=False)
        assert await pg.maybe_replace_ungrounded("ctx", f"Ответ {REP}", REP) == "Ответ"

    async def test_grounded_keeps_answer(self, monkeypatch):
        monkeypatch.setenv("RAG_POST_VERIFY", "1")

        async def ok(ctx, ans):
            return True

        monkeypatch.setattr(pg, "verify_answer_grounded", ok)
        assert await pg.maybe_replace_ungrounded("ctx", "ответ", REP) == "ответ"

    async def test_ungrounded_replaced(self, monkeypatch):
        monkeypatch.setenv("RAG_POST_VERIFY", "1")
        seen = {}

        async def bad(ctx, ans):
            seen["args"] = (ctx, ans)
            return False

        monkeypatch.setattr(pg, "verify_answer_grounded", bad)
        assert await pg.maybe_replace_ungrounded("ctx", f"a {REP}", REP) == REP
        # Судье уходит уже очищенный ответ.
        assert seen["args"] == ("ctx", "a")
