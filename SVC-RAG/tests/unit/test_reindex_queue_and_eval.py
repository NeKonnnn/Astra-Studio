"""services/reindex_queue (поколения, предел параллелизма) и services/retrieval_eval (метрики retrieval)."""

import asyncio
import logging

import pytest

from app.services import reindex_queue as rq
from app.services import retrieval_eval as ev

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------- queue


class TestHelpers:
    @pytest.mark.parametrize(
        "raw,expected",
        [(None, 2), ("", 2), ("  ", 2), ("5", 5), (" 3 ", 3), ("0", 2), ("-4", 2), ("abc", 2), ("2.5", 2)],
    )
    def test_max_parallel(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("RAG_REINDEX_MAX_PARALLEL", raising=False)
        else:
            monkeypatch.setenv("RAG_REINDEX_MAX_PARALLEL", raw)
        assert rq.max_parallel() == expected

    @pytest.mark.parametrize(
        "scope,eid,expected",
        [
            ("agent", 5, "agent:5"),
            (" Project ", "p-1", "project:p-1"),
            ("", 7, "entity:7"),
            (None, "x", "entity:x"),
            ("agent", None, "*"),
            ("agent", "  ", "*"),
            ("owner", 0, "owner:0"),
        ],
    )
    def test_entity_key(self, scope, eid, expected):
        assert rq.entity_key(scope, eid) == expected

    def test_module_level_queues_exist(self):
        assert (rq.kb_queue.store, rq.project_queue.store) == ("kb", "project")


class TestGenerations:
    def test_bump_is_per_key(self):
        q = rq.ReindexQueue("kb")
        assert q.current("agent:1") == 0
        assert q.bump("agent:1") == 1 and q.bump("agent:1") == 2
        assert q.current("agent:2") == 0

    def test_is_current(self):
        q = rq.ReindexQueue("kb")
        g = q.bump("agent:1")
        assert q.is_current("agent:1", g)
        q.bump("agent:1")
        assert not q.is_current("agent:1", g)
        assert q.is_current("agent:1", None)
        assert q.is_current("never-seen", 0)

    def test_global_bump_invalidates_every_key(self):
        q = rq.ReindexQueue("kb")
        a, b = q.bump("agent:1"), q.bump("agent:2")
        g = q.bump(rq.GLOBAL_KEY)
        assert g == 1
        assert not q.is_current("agent:1", a) and not q.is_current("agent:2", b)
        assert q.current("agent:1") == 2

    def test_global_bump_does_not_create_keys_for_unseen_entities(self):
        q = rq.ReindexQueue("kb")
        q.bump(rq.GLOBAL_KEY)
        assert q.current("agent:9") == 0
        # сущность, ни разу не запускавшаяся, не «узнает» о кластерном прогоне
        assert q.is_current("agent:9", 0)


class TestRun:
    async def test_job_runs_and_registry_is_cleaned(self):
        q = rq.ReindexQueue("kb")
        seen = {}

        async def job():
            seen["active"] = q.active()
            seen["busy"] = q.busy()

        await q.run("agent:1", q.bump("agent:1"), job, meta={"owner_user_id": "alice"})
        assert seen["busy"] is True
        assert seen["active"][0]["key"] == "agent:1" and seen["active"][0]["owner_user_id"] == "alice"
        assert "started_at" in seen["active"][0]
        assert q.busy() is False and q.active() == []

    async def test_stale_generation_skips_the_job(self):
        q = rq.ReindexQueue("kb")
        old = q.bump("agent:1")
        q.bump("agent:1")
        ran = []

        async def job():
            ran.append(1)

        await q.run("agent:1", old, job)
        assert ran == [] and not q.busy()

    async def test_job_exception_is_swallowed_and_registry_cleaned(self):
        q = rq.ReindexQueue("kb")

        async def job():
            raise RuntimeError("boom")

        await q.run("agent:1", q.bump("agent:1"), job)
        assert not q.busy()
        # слот освобождён: следующая задача проходит
        ran = []

        async def ok():
            ran.append(1)

        await q.run("agent:1", q.bump("agent:1"), ok)
        assert ran == [1]

    async def test_parallelism_is_limited(self, monkeypatch):
        monkeypatch.setenv("RAG_REINDEX_MAX_PARALLEL", "2")
        q = rq.ReindexQueue("kb")
        running, peak = 0, 0

        async def job():
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1

        await asyncio.gather(*(q.run(f"agent:{i}", q.bump(f"agent:{i}"), job) for i in range(6)))
        assert peak == 2

    async def test_waiting_job_with_outdated_generation_is_dropped(self, monkeypatch):
        monkeypatch.setenv("RAG_REINDEX_MAX_PARALLEL", "1")
        q = rq.ReindexQueue("kb")
        gate = asyncio.Event()
        ran = []

        async def blocker():
            await gate.wait()

        async def waiting():
            ran.append("waiting")

        first = asyncio.create_task(q.run("agent:1", q.bump("agent:1"), blocker))
        await asyncio.sleep(0)
        gen = q.bump("agent:2")
        second = asyncio.create_task(q.run("agent:2", gen, waiting))
        await asyncio.sleep(0)
        q.bump("agent:2")  # пока ждёт слот, настройки поменялись
        gate.set()
        await asyncio.gather(first, second)
        assert ran == []

    async def test_cluster_run_is_exclusive(self, monkeypatch):
        monkeypatch.setenv("RAG_REINDEX_MAX_PARALLEL", "2")
        q = rq.ReindexQueue("kb")
        events = []

        async def entity_job(name):
            events.append(f"start:{name}")
            await asyncio.sleep(0.02)
            events.append(f"end:{name}")

        async def cluster_job():
            events.append("start:cluster")
            assert q.cluster_running()
            await asyncio.sleep(0.02)
            events.append("end:cluster")

        t1 = asyncio.create_task(q.run("agent:1", q.bump("agent:1"), lambda: entity_job("a1")))
        await asyncio.sleep(0)
        t2 = asyncio.create_task(q.run(rq.GLOBAL_KEY, q.bump(rq.GLOBAL_KEY), cluster_job))
        await asyncio.gather(t1, t2)
        assert events.index("end:a1") < events.index("start:cluster")
        assert not q.cluster_running()

    async def test_two_cluster_runs_do_not_deadlock(self, monkeypatch):
        monkeypatch.setenv("RAG_REINDEX_MAX_PARALLEL", "3")
        q = rq.ReindexQueue("kb")
        done = []

        async def job():
            await asyncio.sleep(0.01)
            done.append(1)

        # два кластерных прогона подряд: второй отменяет поколение первого
        g1 = q.bump(rq.GLOBAL_KEY)
        g2 = q.bump(rq.GLOBAL_KEY)
        await asyncio.wait_for(
            asyncio.gather(q.run(rq.GLOBAL_KEY, g1, job), q.run(rq.GLOBAL_KEY, g2, job)), timeout=2
        )
        assert done == [1]  # устаревший не стартовал

    async def test_cancelled_while_taking_slots_releases_everything(self, monkeypatch):
        monkeypatch.setenv("RAG_REINDEX_MAX_PARALLEL", "2")
        q = rq.ReindexQueue("kb")
        gate = asyncio.Event()

        async def blocker():
            await gate.wait()

        holder = asyncio.create_task(q.run("agent:1", q.bump("agent:1"), blocker))
        await asyncio.sleep(0)

        async def cluster_job():
            pass

        cluster = asyncio.create_task(q.run(rq.GLOBAL_KEY, q.bump(rq.GLOBAL_KEY), cluster_job))
        await asyncio.sleep(0.01)
        cluster.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cluster
        gate.set()
        await holder
        ran = []

        async def after():
            ran.append(1)

        await asyncio.wait_for(q.run("agent:2", q.bump("agent:2"), after), timeout=2)
        assert ran == [1]

    async def test_queue_log_when_slots_are_busy(self, monkeypatch, caplog):
        monkeypatch.setenv("RAG_REINDEX_MAX_PARALLEL", "1")
        q = rq.ReindexQueue("kb")
        gate = asyncio.Event()

        async def blocker():
            await gate.wait()

        async def noop():
            pass

        first = asyncio.create_task(q.run("agent:1", q.bump("agent:1"), blocker))
        await asyncio.sleep(0)
        with caplog.at_level(logging.INFO):
            second = asyncio.create_task(q.run("agent:2", q.bump("agent:2"), noop))
            await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(first, second)
        assert any("ждёт свободный слот" in r.getMessage() for r in caplog.records)


class TestStatus:
    async def test_idle_status(self):
        q = rq.ReindexQueue("kb")
        assert q.status() == {"reindexing": False, "owner_user_id": None, "active": [], "max_parallel": 2}

    async def test_single_active_exposes_owner_and_cluster_hides_it(self):
        q = rq.ReindexQueue("kb")
        seen = {}

        async def entity():
            seen["entity"] = q.status()

        async def cluster():
            seen["cluster"] = q.status()

        await q.run("agent:1", q.bump("agent:1"), entity, meta={"owner_user_id": "bob"})
        await q.run(rq.GLOBAL_KEY, q.bump(rq.GLOBAL_KEY), cluster, meta={"owner_user_id": "bob"})
        assert seen["entity"]["owner_user_id"] == "bob" and seen["entity"]["reindexing"] is True
        assert seen["cluster"]["owner_user_id"] is None

    async def test_two_active_have_no_single_owner(self):
        q = rq.ReindexQueue("kb")
        gate = asyncio.Event()
        seen = {}

        async def hold():
            await gate.wait()

        async def peek():
            seen["status"] = q.status()
            gate.set()

        await asyncio.gather(
            q.run("agent:1", q.bump("agent:1"), hold, meta={"owner_user_id": "a"}),
            q.run("agent:2", q.bump("agent:2"), peek, meta={"owner_user_id": "b"}),
        )
        assert len(seen["status"]["active"]) == 2 and seen["status"]["owner_user_id"] is None

    async def test_active_returns_copies(self):
        q = rq.ReindexQueue("kb")

        async def job():
            q.active()[0]["key"] = "tampered"
            assert q.active()[0]["key"] == "agent:1"

        await q.run("agent:1", q.bump("agent:1"), job)


# ------------------------------------------------------------------------ eval

HITS = [
    ("a", 0.9, 1, 0),
    ("b", 0.8, 2, 0),
    ("c", 0.7, 1, 1),
    ("d", 0.6, 3, 0),
    ("e", 0.5, None, None),
]


class TestReciprocalRank:
    def test_by_documents(self):
        assert ev.reciprocal_rank(HITS, gold_document_ids=[3]) == pytest.approx(1 / 4)
        assert ev.reciprocal_rank(HITS, gold_document_ids=[1]) == 1.0

    def test_by_chunks_is_stricter_than_documents(self):
        assert ev.reciprocal_rank(HITS, gold_chunks=[(1, 1)]) == pytest.approx(1 / 3)

    def test_not_found_or_no_gold(self):
        assert ev.reciprocal_rank(HITS, gold_document_ids=[99]) == 0.0
        assert ev.reciprocal_rank(HITS) == 0.0
        assert ev.reciprocal_rank([], gold_document_ids=[1]) == 0.0

    def test_hits_without_ids_never_match(self):
        assert ev.reciprocal_rank([("x", 1.0, None, None)], gold_document_ids=[1], gold_chunks=[(1, 0)]) == 0.0

    def test_chunk_gold_is_checked_before_doc_gold_per_hit(self):
        assert ev.reciprocal_rank(HITS, gold_document_ids=[2], gold_chunks=[(1, 1)]) == pytest.approx(1 / 2)

    def test_mrr(self):
        assert ev.mrr([]) == 0.0
        assert ev.mrr([1.0, 0.5, 0.0]) == pytest.approx(0.5)


class TestComputeMetrics:
    def test_gold_chunks_basis(self):
        m = ev.compute_retrieval_metrics(HITS, 3, gold_chunks=[(1, 1), (9, 9)])
        assert m["basis"] == "gold_chunks" and m["retrieved"] == 5
        assert m["hit_rate_at_k"] == 1.0
        assert m["precision_at_k"] == pytest.approx(1 / 3)
        assert m["recall_at_k"] == pytest.approx(1 / 2)
        assert m["first_relevant_rank"] == 3 and m["reciprocal_rank"] == pytest.approx(1 / 3)

    def test_gold_documents_basis(self):
        m = ev.compute_retrieval_metrics(HITS, 3, gold_document_ids=[1, 5])
        assert m["basis"] == "gold_document_ids"
        assert m["precision_at_k"] == pytest.approx(2 / 3)  # два чанка документа 1 в top-3
        assert m["recall_at_k"] == pytest.approx(1 / 2)
        assert m["first_relevant_rank"] == 1

    def test_chunks_take_priority_over_documents(self):
        m = ev.compute_retrieval_metrics(HITS, 5, gold_chunks=[(2, 0)], gold_document_ids=[1])
        assert m["basis"] == "gold_chunks" and m["first_relevant_rank"] == 2

    def test_cutoff_excludes_hits_below_k_from_precision_and_recall(self):
        m = ev.compute_retrieval_metrics(HITS, 2, gold_chunks=[(3, 0)])
        assert m["hit_rate_at_k"] == 0.0 and m["recall_at_k"] == 0.0
        # но позиция первого релевантного считается по всей выдаче
        assert m["first_relevant_rank"] == 4

    def test_no_gold_leaves_metrics_empty(self):
        m = ev.compute_retrieval_metrics(HITS, 3)
        assert m["basis"] is None and m["precision_at_k"] is None and m["retrieved"] == 5

    def test_k_zero_does_not_divide_by_zero(self):
        m = ev.compute_retrieval_metrics(HITS, 0, gold_chunks=[(1, 0)])
        assert m["precision_at_k"] == 0.0 and m["hit_rate_at_k"] == 0.0

    def test_empty_hits(self):
        m = ev.compute_retrieval_metrics([], 5, gold_document_ids=[1])
        assert m["hit_rate_at_k"] == 0.0 and m["first_relevant_rank"] is None and m["reciprocal_rank"] == 0.0

    def test_duplicate_chunks_in_hits_count_once_for_chunk_precision(self):
        hits = [("a", 1.0, 1, 0), ("a", 0.9, 1, 0)]
        m = ev.compute_retrieval_metrics(hits, 2, gold_chunks=[(1, 0)])
        assert m["precision_at_k"] == pytest.approx(0.5)


class TestEvalLines:
    async def test_no_gold_no_judge_gives_nothing(self):
        assert await ev.build_eval_metric_lines("q", HITS, 3) == []

    async def test_gold_chunk_lines(self):
        lines = await ev.build_eval_metric_lines("q", HITS, 3, gold_chunks=[(1, 1)])
        assert lines[0].startswith("эталон: чанки")
        assert any("hit_rate@k (gold, чанки)=1.0000" in l for l in lines)
        assert any("reciprocal_rank (gold, чанки)=0.3333" in l for l in lines)

    async def test_gold_document_lines_when_no_chunks(self):
        lines = await ev.build_eval_metric_lines("q", HITS, 3, gold_document_ids=[3])
        assert lines[0] == "эталон: document_id"
        assert any("hit_rate@k (gold, документы)=0.0000" in l for l in lines)

    async def test_chunk_gold_hides_document_gold(self):
        lines = await ev.build_eval_metric_lines("q", HITS, 3, gold_chunks=[(1, 0)], gold_document_ids=[3])
        assert not any("документы" in l for l in lines)

    async def test_judge_disabled_line(self, monkeypatch):
        async def judge(q, passages):
            return [False] * len(passages), "LLM-judge отключён (RAG_EVAL_LLM_JUDGE_ALLOWED=false)"

        monkeypatch.setattr(ev, "judge_chunk_relevance", judge)
        lines = await ev.build_eval_metric_lines("q", HITS, 3, llm_judge=True)
        assert lines == ["LLM-judge: пропуск — LLM-judge отключён (RAG_EVAL_LLM_JUDGE_ALLOWED=false)"]

    async def test_judge_error_line(self, monkeypatch):
        async def judge(q, passages):
            return [False] * len(passages), "parse_error"

        monkeypatch.setattr(ev, "judge_chunk_relevance", judge)
        assert await ev.build_eval_metric_lines("q", HITS, 3, llm_judge=True) == ["LLM-judge: ошибка — parse_error"]

    async def test_judge_metrics_without_gold(self, monkeypatch):
        seen = {}

        async def judge(q, passages):
            seen["passages"] = passages
            return [True, False, True], None

        monkeypatch.setattr(ev, "judge_chunk_relevance", judge)
        lines = await ev.build_eval_metric_lines("q", HITS, 3, llm_judge=True)
        assert seen["passages"] == ["a", "b", "c"]
        assert "hit_rate@k (LLM)=1.0000" in lines
        assert any(l.startswith("precision@k (LLM)=0.6667") for l in lines)
        assert any("recall@k (LLM): без эталона" in l for l in lines)
        assert lines[-1] == "LLM-judge: релевантных_чанков=2 из 3"

    async def test_judge_recall_with_gold_chunks(self, monkeypatch):
        async def judge(q, passages):
            return [True, False, False], None

        monkeypatch.setattr(ev, "judge_chunk_relevance", judge)
        lines = await ev.build_eval_metric_lines(
            "q", HITS, 3, gold_chunks=[(1, 0), (2, 0)], llm_judge=True
        )
        assert any(l.startswith("recall@k (LLM∧эталон_чанки)=0.5000") for l in lines)

    async def test_judge_with_empty_hits(self, monkeypatch):
        async def judge(q, passages):
            return [], None

        monkeypatch.setattr(ev, "judge_chunk_relevance", judge)
        lines = await ev.build_eval_metric_lines("q", [], 3, llm_judge=True)
        assert "hit_rate@k (LLM)=0.0000" in lines and lines[-1].endswith("0 из 0")

    async def test_judge_gets_empty_string_for_none_text(self, monkeypatch):
        seen = {}

        async def judge(q, passages):
            seen["p"] = passages
            return [False], None

        monkeypatch.setattr(ev, "judge_chunk_relevance", judge)
        await ev.build_eval_metric_lines("q", [(None, 0.1, 1, 1)], 1, llm_judge=True)
        assert seen["p"] == [""]


class TestLogRetrievalWithEval:
    async def test_passes_eval_lines_to_report(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(ev, "log_rag_retrieval_report", lambda **kw: captured.update(kw))
        await ev.log_retrieval_with_eval(
            store="kb",
            strategy_resolved="hybrid",
            pipeline="p",
            extra_lines=["x"],
            final_hits=HITS,
            k_requested=3,
            query_for_eval="q",
            search_started_perf=0.0,
            gold_chunks=[(1, 0)],
        )
        assert captured["store"] == "kb" and captured["extra_lines"] == ["x"]
        assert captured["eval_lines"][0].startswith("эталон: чанки")
        assert captured["search_seconds"] > 0 and captured["k_requested"] == 3

    async def test_no_eval_lines_become_none(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(ev, "log_rag_retrieval_report", lambda **kw: captured.update(kw))
        await ev.log_retrieval_with_eval(
            store="kb", strategy_resolved="v", pipeline="p", extra_lines=None, final_hits=[],
            k_requested=3, query_for_eval="q", search_started_perf=0.0,
        )
        assert captured["eval_lines"] is None
