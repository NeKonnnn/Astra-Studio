"""rag_query/metrics: онлайн-метрики RAG (RR, точность контекста, достоверность) и их отказоустойчивость."""

import json

import pytest

from backend.rag_query import metrics as m

pytestmark = pytest.mark.unit


@pytest.fixture
def judge(monkeypatch):
    """Подменяет вызов модели-судьи: ответы берутся по очереди."""
    answers = []
    prompts = []

    async def fake(prompt, *, system, max_tokens=512):
        prompts.append((prompt, system, max_tokens))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(m, "_ask_judge", fake)
    fake.answers, fake.prompts = answers, prompts
    return fake


class TestSwitchAndStatus:
    @pytest.mark.parametrize("raw,expected", [(None, True), ("", True), ("1", True), ("true", True), ("0", False), ("false", False), ("No", False), ("OFF", False)])
    def test_enabled(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("RAG_ONLINE_METRICS", raising=False)
        else:
            monkeypatch.setenv("RAG_ONLINE_METRICS", raw)
        assert m.online_metrics_enabled() is expected

    @pytest.mark.parametrize("value,expected", [(None, None), (0.9, "OK"), (0.75, "OK"), (0.7, "WARNING"), (0.6, "WARNING"), (0.59, "CRITICAL"), (0.0, "CRITICAL")])
    def test_threshold_status(self, value, expected):
        assert m._threshold_status(value, 0.75, 0.6) == expected


class TestRankMetrics:
    @pytest.mark.parametrize("flags,expected", [([], 0.0), ([False, False], 0.0), ([True], 1.0), ([False, True, True], 0.5), ([False, False, False, True], 0.25)])
    def test_reciprocal_rank(self, flags, expected):
        assert m.reciprocal_rank(flags) == expected

    @pytest.mark.parametrize("flags,expected", [([], None), ([True, False], 0.5), ([False], 0.0), ([True, True, True], 1.0)])
    def test_context_precision(self, flags, expected):
        assert m.context_precision(flags) == expected


class TestJsonExtraction:
    def test_plain_json(self):
        assert m._extract_json_object('{"a": 1}') == {"a": 1}

    def test_json_after_prose(self):
        assert m._extract_json_object('Вот результат: {"relevant": [true]}') == {"relevant": [True]}

    @pytest.mark.parametrize("text", ["", None, "no json", "{broken", '{"a": 1} спасибо!'])
    def test_unparsable(self, text):
        assert m._extract_json_object(text) is None


class TestPassages:
    def test_from_dicts_and_tuples(self):
        hits = [{"content": "a"}, ("b", 0.5, 1, 0), ["c"], {"content": None}, 5, (), None]
        assert m._passages_from_hits(hits) == ["a", "b", "c", ""]
        assert m._passages_from_hits(None) == []


class TestRelevanceJudge:
    async def test_no_passages_needs_no_call(self, judge):
        assert await m.judge_chunk_relevance("q", []) == ([], None)
        assert judge.prompts == []

    async def test_flags_in_order(self, judge):
        judge.answers.append('{"relevant": [true, false, true]}')
        assert await m.judge_chunk_relevance("вопрос", ["a", "b", "c"]) == ([True, False, True], None)
        prompt, system, _ = judge.prompts[0]
        assert "Запрос:\nвопрос" in prompt and "[2] c" in prompt and "ровно из 3" in prompt and "JSON" in system

    async def test_short_answer_is_padded_with_false(self, judge):
        judge.answers.append('{"relevant": [true]}')
        assert (await m.judge_chunk_relevance("q", ["a", "b"]))[0] == [True, False]

    async def test_passages_are_truncated_and_flattened(self, judge):
        judge.answers.append('{"relevant": [true]}')
        await m.judge_chunk_relevance("q", ["строка\nс переносом " + "я" * 1000])
        prompt = judge.prompts[0][0]
        assert "\nс переносом" not in prompt.split("Фрагменты:")[1] and prompt.count("я") <= 480

    @pytest.mark.parametrize("answer", ["не знаю", '{"other": 1}', '{"relevant": "yes"}'])
    async def test_unusable_answers_give_none_not_all_true(self, judge, answer):
        judge.answers.append(answer)
        assert await m.judge_chunk_relevance("q", ["a"]) == (None, "parse_error")

    async def test_judge_failure_gives_none_and_message(self, judge):
        judge.answers.append(RuntimeError("gateway down"))
        assert await m.judge_chunk_relevance("q", ["a"]) == (None, "gateway down")

    async def test_string_false_counts_as_relevant_known_issue(self, judge):
        """Известная проблема: ``bool("false")`` — True, как и в svc-rag-судье."""
        judge.answers.append('{"relevant": ["false"]}')
        assert (await m.judge_chunk_relevance("q", ["a"]))[0] == [True]


class TestFaithfulness:
    async def test_share_of_supported_claims(self, judge):
        judge.answers.append(json.dumps({"claims": [{"text": "a", "supported": True}, {"text": "b", "supported": False}, {"text": "c", "supported": True}, {"text": "d", "supported": True}]}))
        score, details = await m.faithfulness_score("контекст", "ответ")
        assert score == 0.75 and details == {"supported": 3, "total": 4}
        assert judge.prompts[0][2] == 800 and "КОНТЕКСТ:\nконтекст" in judge.prompts[0][0]

    async def test_empty_inputs_need_no_call(self, judge):
        assert await m.faithfulness_score("", "ответ") == (None, {"reason": "empty_context_or_answer"})
        assert await m.faithfulness_score("ctx", "   ") == (None, {"reason": "empty_context_or_answer"})
        assert judge.prompts == []

    async def test_no_factual_claims_is_not_penalised(self, judge):
        judge.answers.append('{"claims": []}')
        assert await m.faithfulness_score("ctx", "Не знаю") == (None, {"reason": "no_factual_claims", "total": 0})

    @pytest.mark.parametrize("answer", ["мусор", '{"claims": "x"}', "{}"])
    async def test_unparsable(self, judge, answer):
        judge.answers.append(answer)
        assert await m.faithfulness_score("ctx", "ответ") == (None, {"reason": "parse_error"})

    async def test_junk_claims_count_as_unsupported(self, judge):
        judge.answers.append('{"claims": [{"supported": true}, "junk", 5, {"supported": false}]}')
        score, details = await m.faithfulness_score("ctx", "ответ")
        assert score == 0.25 and details["total"] == 4

    async def test_judge_error(self, judge):
        judge.answers.append(TimeoutError("slow"))
        assert await m.faithfulness_score("ctx", "ответ") == (None, {"reason": "slow"})

    async def test_inputs_are_truncated(self, judge):
        judge.answers.append('{"claims": []}')
        await m.faithfulness_score("к" * 20_000, "о" * 20_000)
        prompt = judge.prompts[0][0]
        assert prompt.count("к") <= 12_000 + 20 and prompt.count("о") <= 8_000 + 200


class TestOnlineMetrics:
    HITS = [("первый", 0.9, 1, 0), ("второй", 0.8, 1, 1), ("третий", 0.7, 2, 0)]

    async def run(self, **kw):
        base = dict(query="вопрос", hits=self.HITS, answer="ответ", context_text="контекст")
        base.update(kw)
        return await m.compute_online_rag_metrics(**base)

    async def test_full_report(self, judge):
        judge.answers += ['{"relevant": [false, true, true]}', '{"claims": [{"supported": true}, {"supported": true}]}']
        out = await self.run()
        assert out["reciprocal_rank"] == 0.5 and out["context_precision"] == 0.6667
        assert out["faithfulness"] == 1.0 and out["first_relevant_rank"] == 2
        assert out["relevant_chunks"] == 2 and out["retrieved_chunks"] == 3
        assert out["status"] == {"mrr": "CRITICAL", "faithfulness": "OK", "context_precision": "WARNING"}
        assert out["faithfulness_details"] == {"supported": 2, "total": 2} and out["judge_error"] is None

    async def test_good_scores_have_ok_status(self, judge):
        judge.answers += ['{"relevant": [true, true, true]}', '{"claims": [{"supported": true}]}']
        out = await self.run()
        assert set(out["status"].values()) == {"OK"}

    async def test_judge_failure_leaves_ranking_metrics_empty_but_keeps_faithfulness(self, judge):
        judge.answers += [RuntimeError("down"), '{"claims": [{"supported": false}]}']
        out = await self.run()
        assert out["reciprocal_rank"] is None and out["context_precision"] is None and out["relevant_chunks"] is None
        assert out["faithfulness"] == 0.0 and out["judge_error"] == "down"
        assert out["status"]["mrr"] is None and out["status"]["faithfulness"] == "CRITICAL"

    async def test_disabled_or_no_hits_return_none_without_calls(self, judge, monkeypatch):
        assert await self.run(hits=[]) is None and await self.run(hits=None) is None
        monkeypatch.setenv("RAG_ONLINE_METRICS", "0")
        assert await self.run() is None
        assert judge.prompts == []

    async def test_never_raises(self, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(m, "judge_chunk_relevance", boom)
        assert await self.run() is None

    async def test_dict_hits_from_a_trace_are_supported(self, judge):
        judge.answers += ['{"relevant": [true]}', '{"claims": []}']
        out = await self.run(hits=[{"content": "из трейса"}])
        assert out["retrieved_chunks"] == 1 and out["faithfulness"] is None


class TestAskJudge:
    async def test_runs_ask_agent_in_a_worker_thread_with_zero_temperature(self, monkeypatch):
        seen = {}

        def fake_ask_agent(prompt, **kw):
            seen.update(prompt=prompt, **kw)
            return "ответ судьи"

        monkeypatch.setattr("backend.agent_llm_svc.ask_agent", fake_ask_agent)
        monkeypatch.setattr("backend.settings.cef_logger.cef_audit_context.chat_model_peek", lambda: "gw://m")
        out = await m._ask_judge("промпт", system="sys", max_tokens=99)
        assert out == "ответ судьи"
        assert seen["system_prompt"] == "sys" and seen["temperature"] == 0.0 and seen["max_tokens"] == 99
        assert seen["model_path"] == "gw://m" and seen["streaming"] is False and seen["history"] == []

    async def test_empty_answer_becomes_empty_string(self, monkeypatch):
        monkeypatch.setattr("backend.agent_llm_svc.ask_agent", lambda *a, **k: None)
        assert await m._ask_judge("p", system="s") == ""

    async def test_request_context_is_visible_in_the_worker(self, monkeypatch):
        import contextvars

        var = contextvars.ContextVar("probe", default="empty")
        seen = []

        def fake_ask_agent(prompt, **kw):
            seen.append(var.get())
            return "ok"

        monkeypatch.setattr("backend.agent_llm_svc.ask_agent", fake_ask_agent)
        var.set("from-request")
        await m._ask_judge("p", system="s")
        assert seen == ["from-request"]
