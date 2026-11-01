"""rag_query/llm_judge: LLM-судья релевантности чанков (включение по env, разбор JSON, fail-open)."""

import contextvars
import sys
import types

import pytest

from backend.rag_query import llm_judge as lj
from backend.settings.cef_logger import cef_audit_context as cef

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_ask_agent(monkeypatch):
    """Подменяет backend.agent_llm_svc.ask_agent и копит вызовы."""
    calls = []
    state = {"answer": "", "error": None}

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


class TestEnabled:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, False), ("", False), ("1", True), ("TRUE", True), (" yes ", True),
            ("on", True), ("0", False), ("off", False), ("maybe", False),
        ],
    )
    def test_flag(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("RAG_LLM_JUDGE_ENABLED", raising=False)
        else:
            monkeypatch.setenv("RAG_LLM_JUDGE_ENABLED", raw)
        assert lj.llm_judge_enabled() is expected


class TestExtractJson:
    def test_empty(self):
        assert lj._extract_json_object("") is None
        assert lj._extract_json_object(None) is None

    def test_pure_json(self):
        assert lj._extract_json_object('{"relevant": [true]}') == {"relevant": [True]}

    def test_json_after_prose(self):
        assert lj._extract_json_object('Вот ответ: {"relevant": [false]}') == {"relevant": [False]}

    def test_broken_tail_returns_none(self):
        # Хвост похож на объект, но не парсится; целиком тоже не JSON.
        assert lj._extract_json_object("текст {не json}") is None

    def test_plain_text(self):
        assert lj._extract_json_object("совсем не json") is None


class TestJudgeLlm:
    async def test_passes_chat_model_and_params(self, fake_ask_agent):
        fake_ask_agent.state["answer"] = "ответ"
        token = cef._chat_model.set("model-x")
        try:
            out = await lj._judge_llm("промпт")
        finally:
            cef._chat_model.reset(token)
        assert out == "ответ"
        prompt, kw = fake_ask_agent.calls[0]
        assert prompt == "промпт"
        assert kw["model_path"] == "model-x"
        assert kw["streaming"] is False and kw["service_call"] is True
        assert kw["max_tokens"] == 512 and kw["temperature"] == 0.0 and kw["history"] == []

    async def test_none_answer_becomes_empty(self, fake_ask_agent):
        fake_ask_agent.state["answer"] = None
        assert await lj._judge_llm("p") == ""

    async def test_contextvars_are_propagated_into_thread(self, monkeypatch):
        # В поток executor contextvars не копируются: модель чата читается до потока
        # (chat_model_peek) и передаётся в ask_agent явным model_path.
        from backend.settings.cef_logger import cef_audit_context as ctx

        seen = []

        def spy(prompt, **kw):
            seen.append(kw.get("model_path"))
            return "ok"

        mod = types.ModuleType("backend.agent_llm_svc")
        mod.ask_agent = spy
        monkeypatch.setitem(sys.modules, "backend.agent_llm_svc", mod)
        token = ctx._chat_model.set("gw://есть")
        try:
            await lj._judge_llm("p")
        finally:
            ctx._chat_model.reset(token)
        assert seen == ["gw://есть"]


class TestJudgeChunkRelevance:
    async def test_empty_passages(self):
        assert await lj.judge_chunk_relevance("q", []) == []

    async def test_prompt_contains_numbered_trimmed_passages(self, monkeypatch):
        captured = {}

        async def fake(prompt):
            captured["p"] = prompt
            return '{"relevant": [true, false]}'

        monkeypatch.setattr(lj, "_judge_llm", fake)
        out = await lj.judge_chunk_relevance("  вопрос  ", ["строка\nодин", "x" * 1000])
        assert out == [True, False]
        p = captured["p"]
        assert "Запрос:\nвопрос\n" in p
        assert "[0] строка один" in p
        assert p.count("x") == lj._MAX_PASSAGE_CHARS
        assert "ровно из 2 булевых" in p and "0..1" in p

    async def test_none_passage_is_empty_snippet(self, monkeypatch):
        seen = {}

        async def fake(prompt):
            seen["p"] = prompt
            return '{"relevant": [false]}'

        monkeypatch.setattr(lj, "_judge_llm", fake)
        assert await lj.judge_chunk_relevance("q", [None]) == [False]
        assert "[0] \n" in seen["p"]

    @pytest.mark.parametrize("content", ["", "не json", "{}", '{"relevant": "да"}', "[1]"])
    async def test_unparsable_answer_keeps_everything(self, monkeypatch, content):
        async def fake(prompt):
            return content

        monkeypatch.setattr(lj, "_judge_llm", fake)
        assert await lj.judge_chunk_relevance("q", ["a", "b"]) == [True, True]

    async def test_short_flag_list_is_padded_with_true(self, monkeypatch):
        async def fake(prompt):
            return '{"relevant": [false]}'

        monkeypatch.setattr(lj, "_judge_llm", fake)
        assert await lj.judge_chunk_relevance("q", ["a", "b", "c"]) == [False, True, True]

    async def test_flags_are_cast_to_bool(self, monkeypatch):
        async def fake(prompt):
            return '{"relevant": [1, 0, null]}'

        monkeypatch.setattr(lj, "_judge_llm", fake)
        assert await lj.judge_chunk_relevance("q", ["a", "b", "c"]) == [True, False, False]

    async def test_llm_failure_fails_open(self, monkeypatch):
        async def boom(prompt):
            raise RuntimeError("llm down")

        monkeypatch.setattr(lj, "_judge_llm", boom)
        assert await lj.judge_chunk_relevance("q", ["a", "b"]) == [True, True]


class TestJudgeAndFilter:
    HITS = [("a", 0.9, 1, 0), ("b", 0.8, 1, 1), ("c", 0.7, 2, 0)]

    async def test_disabled_returns_input(self, monkeypatch):
        monkeypatch.delenv("RAG_LLM_JUDGE_ENABLED", raising=False)
        assert await lj.judge_and_filter_hits("q", self.HITS) is self.HITS

    async def test_empty_hits(self, monkeypatch):
        monkeypatch.setenv("RAG_LLM_JUDGE_ENABLED", "1")
        assert await lj.judge_and_filter_hits("q", []) == []

    async def test_filters_irrelevant(self, monkeypatch):
        monkeypatch.setenv("RAG_LLM_JUDGE_ENABLED", "1")

        async def fake(q, passages):
            assert passages == ["a", "b", "c"]
            return [True, False, True]

        monkeypatch.setattr(lj, "judge_chunk_relevance", fake)
        assert await lj.judge_and_filter_hits("q", self.HITS) == [self.HITS[0], self.HITS[2]]

    async def test_all_rejected_returns_original(self, monkeypatch):
        monkeypatch.setenv("RAG_LLM_JUDGE_ENABLED", "1")

        async def fake(q, passages):
            return [False] * len(passages)

        monkeypatch.setattr(lj, "judge_chunk_relevance", fake)
        assert await lj.judge_and_filter_hits("q", self.HITS) == self.HITS
