"""services/rag_search_helpers: реранк для кириллицы, выбор стратегии auto, пороги, RRF-слияние, диверсификация, метрики."""

from types import SimpleNamespace

import pytest

from app.services import rag_search_helpers as h

pytestmark = pytest.mark.unit

ENV = ("RAG_RERANKER_ENGLISH_ONLY", "RAG_FORCE_RERANK_CYRILLIC", "RAG_AUTO_MODE", "RAG_AUTO_PREFER_HYBRID", "RAG_METRICS_STDOUT")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def dv(doc, chunk=0, text="какой-то достаточно длинный текст чанка для проверки фильтров"):
    return SimpleNamespace(document_id=doc, chunk_index=chunk, content=text)


class TestRerankForCyrillic:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("", False), (None, False), ("налоги", True), ("налоговый отчёт 2025", True), ("tax report", False), ("report по налогам", True), ("Q1 отчёт", True), ("quarterly report по", False), ("ab", False), ("123 !!!", False)],
    )
    def test_primarily_cyrillic(self, text, expected):
        assert h._is_primarily_cyrillic(text) is expected

    @pytest.mark.parametrize(("raw", "expected"), [(None, False), ("1", True), ("yes", True), ("0", False), ("false", False), ("OFF", False), ("", False)])
    def test_english_only_flag(self, monkeypatch, raw, expected):
        if raw is not None:
            monkeypatch.setenv("RAG_RERANKER_ENGLISH_ONLY", raw)

        assert h.reranker_is_english_only() is expected

    def test_russian_queries_keep_reranking_by_default(self, monkeypatch):
        # BUG-RAG-03: дефолт RAG_RERANKER_ENGLISH_ONLY=0 (как в докстринге) — реранк на
        # русском запросе не отключается, пока явно не задано RAG_RERANKER_ENGLISH_ONLY=1.
        monkeypatch.delenv("RAG_RERANKER_ENGLISH_ONLY", raising=False)
        monkeypatch.delenv("RAG_FORCE_RERANK_CYRILLIC", raising=False)
        assert h.reranker_is_english_only() is False
        assert "=0" in (h.reranker_is_english_only.__doc__ or "")
        assert h.should_disable_rerank_for_query("налоговая отчётность") is False
        assert h.should_disable_rerank_for_query("tax report") is False

    def test_english_reranker_disables_rerank_for_russian_queries(self, monkeypatch):
        monkeypatch.setenv("RAG_RERANKER_ENGLISH_ONLY", "1")
        monkeypatch.delenv("RAG_FORCE_RERANK_CYRILLIC", raising=False)
        assert h.should_disable_rerank_for_query("налоговая отчётность") is True
        assert h.should_disable_rerank_for_query("tax report") is False

    def test_overrides(self, monkeypatch):
        monkeypatch.setenv("RAG_FORCE_RERANK_CYRILLIC", "yes")
        assert h.should_disable_rerank_for_query("налоговая отчётность") is False
        monkeypatch.delenv("RAG_FORCE_RERANK_CYRILLIC")
        monkeypatch.setenv("RAG_RERANKER_ENGLISH_ONLY", "0")
        assert h.should_disable_rerank_for_query("налоговая отчётность") is False

    @pytest.mark.parametrize(
        ("requested", "cfg", "strategy", "expected"),
        [
            (True, True, "raw_cosine", False), (True, True, "flat", False),
            (None, True, "hybrid", True), (False, True, "hybrid", False), (True, False, "hybrid", False),
            (None, True, "reranking", True), (None, False, "reranking", False),
            (None, True, "auto", True), (None, False, "auto", False), (False, True, "auto", False), (True, False, "auto", False),
            (None, True, "vector", True), (None, True, "lexical", True), (True, True, "Auto", True), (True, True, None, True),
        ],
    )
    def test_effective_reranking_matrix(self, requested, cfg, strategy, expected):
        assert h.effective_use_reranking(requested, cfg, strategy) is expected

    def test_cyrillic_query_keeps_reranking_for_multilingual_reranker(self):
        assert h.effective_use_reranking(None, True, "auto", query_text="налоговая отчётность") is True

    def test_cyrillic_query_turns_reranking_off_for_english_reranker(self, monkeypatch):
        monkeypatch.setenv("RAG_RERANKER_ENGLISH_ONLY", "1")
        assert h.effective_use_reranking(None, True, "auto", query_text="налоговая отчётность") is False
        assert h.effective_use_reranking(None, True, "auto", query_text="tax report") is True
        assert h.effective_use_reranking(None, True, "auto", query_text=None) is True


def auto(query, **kw):
    params = {"store": "global", "document_id": None, "hierarchical_available": False, "graph_available": False, "hybrid_available": False}
    params.update(kw)
    return h.resolve_auto_pipeline_strategy(query, **params)


class TestAutoStrategy:
    def test_hybrid_is_preferred_whenever_it_is_available(self):
        assert auto("любой вопрос", hybrid_available=True) == "hybrid"
        assert auto('Найди "точная фраза"', hybrid_available=True, graph_available=True) == "hybrid"

    def test_vector_is_the_fallback(self):
        assert auto("что-нибудь") == "vector"

    @pytest.fixture
    def heuristic(self, monkeypatch):
        monkeypatch.setenv("RAG_AUTO_PREFER_HYBRID", "0")

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ('Найди "точная фраза из договора"', "lexical"),
            ("Артикул AB-1234 на складе", "lexical"),
            ("какой ИНН у компании", "lexical"),
            ("покажи дословно пункт договора", "lexical"),
            ("номер счёта клиента", "lexical"),
            ("налоги", "hybrid"),
            ("объясни, что такое налоговый вычет", "vector"),
            ("в чем смысл этой нормы права", "vector"),
        ],
    )
    def test_heuristics_with_bm25(self, heuristic, query, expected):
        assert auto(query, hybrid_available=True) == expected

    @pytest.mark.parametrize(
        ("query", "expected"),
        [("как связаны эти два договора между собой", "graph"), ("сравни условия поставки", "graph"), ("что в файле отчёт.pdf написано", "graph"), ("объясни по смыслу этот пункт", "vector")],
    )
    def test_heuristics_with_graph(self, heuristic, query, expected):
        assert auto(query, graph_available=True) == expected

    def test_neutral_queries_stay_on_vector_on_a_tie(self, heuristic):
        # Базовые оценки vector и graph равны (1.0): при ничьей выигрывает дешёвый vector,
        # а в графовый путь уходят только запросы с реальным graph-интентом.
        assert auto("обычный вопрос про налоги", graph_available=True) == "vector"
        assert auto("почему выросли налоги", graph_available=True) == "graph"

    def test_summary_questions_go_to_the_hierarchy_only_for_the_global_store(self, heuristic):
        assert auto("дай краткий обзор документа", hierarchical_available=True) == "hierarchical"
        assert auto("дай краткий обзор документа", hierarchical_available=True, store="kb") == "vector"
        assert auto("дай краткий обзор документа", hierarchical_available=True, document_id=5) == "vector"

    def test_long_queries_nudge_towards_vector(self, heuristic):
        assert auto("слово " * 40, hybrid_available=True) == "hybrid"  # vector 1.8 против hybrid 2.0

    def test_ties_follow_the_fixed_priority(self, heuristic):
        # hybrid(2.0) против graph(1+0): hybrid; при равенстве побеждает порядок lexical, graph, hybrid, vector.
        assert auto("вопрос про налоги и сборы", hybrid_available=True, graph_available=True) == "hybrid"

    def test_priority_mode(self, monkeypatch):
        monkeypatch.setenv("RAG_AUTO_MODE", "priority")

        assert auto("q", hybrid_available=True, graph_available=True) == "hybrid"
        assert auto("q", graph_available=True, hierarchical_available=True) == "graph"
        assert auto("q", hierarchical_available=True) == "hierarchical"
        assert auto("q", hierarchical_available=True, document_id=3) == "vector"
        assert auto("q") == "vector"

    def test_empty_query(self, heuristic):
        assert auto("", hybrid_available=True) == "hybrid" and auto(None) == "vector"


class TestFetchLimit:
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"k": 5, "graph": True, "document_id": None, "use_rerank": False, "rerank_top_k": 0}, 36),
            ({"k": 20, "graph": True, "document_id": None, "use_rerank": False, "rerank_top_k": 0}, 80),
            ({"k": 5, "graph": False, "document_id": 7, "use_rerank": False, "rerank_top_k": 0}, 56),
            ({"k": 20, "graph": False, "document_id": 7, "use_rerank": True, "rerank_top_k": 0}, 100),
            ({"k": 5, "graph": False, "document_id": None, "use_rerank": True, "rerank_top_k": 300}, 300),
            ({"k": 5, "graph": False, "document_id": None, "use_rerank": True, "rerank_top_k": 10}, 120),
            ({"k": 5, "graph": False, "document_id": None, "use_rerank": False, "rerank_top_k": 300}, 120),
            ({"k": 30, "graph": False, "document_id": None, "use_rerank": False, "rerank_top_k": 0}, 420),
        ],
    )
    def test_limits(self, kwargs, expected):
        assert h.vector_fetch_limit(**kwargs) == expected


class TestSimilarityFilter:
    HITS = [(dv(1), 0.9), (dv(2), 0.5), (dv(3), 0.2)]

    def test_disabled(self):
        assert h.filter_by_min_vector_similarity(self.HITS, 0, 5) == self.HITS and h.filter_by_min_vector_similarity(self.HITS, -1, 5) == self.HITS
        assert h.filter_by_min_vector_similarity([], 0.5, 5) == []

    def test_threshold_is_inclusive(self):
        assert [s for _, s in h.filter_by_min_vector_similarity(self.HITS, 0.5, 5)] == [0.9, 0.5]

    def test_everything_below_the_threshold_gives_an_empty_list(self):
        # BUG-RAG-03: как в докстринге — если ничего не прошло порог, выдача пустая (без rescue).
        assert "пустой список" in (h.filter_by_min_vector_similarity.__doc__ or "")
        low = [(dv(i), 0.01 * i) for i in range(1, 8)]

        assert h.filter_by_min_vector_similarity(low, 0.9, 5) == []

    @pytest.mark.parametrize("k", [0, 1, 5, 50])
    def test_no_rescue_for_any_k(self, k):
        low = [(dv(i), 0.01 * i) for i in range(1, 8)]

        assert h.filter_by_min_vector_similarity(low, 0.9, k) == []


class TestDiversifyDecision:
    def hits(self, *scores, docs=None):
        docs = docs or list(range(1, len(scores) + 1))
        return [(dv(d), s) for d, s in zip(docs, scores)]

    def test_needs_at_least_three_hits_from_three_documents(self):
        assert h.should_diversify_hits(self.hits(0.3, 0.29)) is False
        assert h.should_diversify_hits(self.hits(0.3, 0.29, 0.28, docs=[1, 1, 2])) is False

    def test_clear_winner_is_not_diversified(self):
        assert h.should_diversify_hits(self.hits(0.8, 0.4, 0.3)) is False

    def test_close_scores_are_diversified(self):
        assert h.should_diversify_hits(self.hits(0.5, 0.45, 0.4)) is True

    def test_weak_top_score_is_diversified(self):
        assert h.should_diversify_hits(self.hits(0.2, 0.05, 0.01)) is True

    def test_rrf_scale_clear_winner_is_not_diversified(self):
        # BUG-RAG-03: на RRF-шкале (~0.016) cosine-пороги неприменимы — решает относительный отрыв.
        assert h.should_diversify_hits(self.hits(0.0164, 0.0100, 0.0050)) is False

    def test_rrf_scale_close_scores_are_diversified(self):
        assert h.should_diversify_hits(self.hits(0.0164, 0.0160, 0.0150)) is True

    def test_real_cosine_can_be_supplied(self):
        cosine = {0: 0.9, 1: 0.3, 2: 0.2}
        hits = self.hits(0.0164, 0.0100, 0.0050)

        assert h.should_diversify_hits(hits, score_of=lambda hit: cosine[hits.index(hit)]) is False
        assert h.should_diversify_hits(hits, score_of=lambda hit: None) is True


class TestKeywordBoost:
    LONG = "налоговая декларация организации за первый квартал отчётного года содержит сведения о доходах и расходах " * 2

    def test_exact_matches_raise_the_score(self):
        hits = [(dv(1, text="совсем другой длинный текст про погоду и климат " * 4), 0.5), (dv(2, text=self.LONG), 0.5)]

        out = h.keyword_boost_hits(hits, "налоговая декларация")

        assert out[0][0].document_id == 2 and out[0][1] == pytest.approx(0.5 * 1.3) and out[1][1] == pytest.approx(0.5)

    def test_short_chunks_are_never_boosted(self):
        out = h.keyword_boost_hits([(dv(1, text="налоговая декларация"), 0.4)], "налоговая декларация")

        assert out[0][1] == 0.4

    def test_stopwords_and_short_words_do_not_count(self):
        hits = [(dv(1, text=self.LONG), 0.5)]

        assert h.keyword_boost_hits(hits, "и в на по the of")[0][1] == 0.5

    def test_partial_match_gives_a_partial_boost(self):
        out = h.keyword_boost_hits([(dv(1, text=self.LONG), 1.0)], "налоговая погода")

        assert out[0][1] == pytest.approx(1.0 + 0.3 * 0.5)

    @pytest.mark.parametrize("query", ["", "   "])
    def test_empty_query_or_hits(self, query):
        hits = [(dv(1), 0.5)]

        assert h.keyword_boost_hits(hits, query) is hits and h.keyword_boost_hits([], "q") == []


class TestLowSignal:
    def test_short_chunks_are_removed(self):
        hits = [(dv(1, text="я" * 100), 0.9), (dv(2, text="коротко"), 0.8), (dv(3, text="   " + "я" * 39 + "   "), 0.7)]

        assert [d.document_id for d, _ in h.filter_low_signal_chunks(hits)] == [1]

    def test_all_short_means_rescue(self):
        hits = [(dv(i, text="x"), 0.1 * i) for i in range(1, 20)]

        out = h.filter_low_signal_chunks(hits)

        assert len(out) == 12 and out[0][1] == pytest.approx(1.9)

    def test_rescue_never_returns_less_than_three_when_available(self):
        assert len(h.filter_low_signal_chunks([(dv(i, text="x"), 0.1) for i in range(5)], rescue_keep=1)) == 3

    def test_zero_min_len_disables_the_filter(self):
        hits = [(dv(1, text=""), 0.1)]

        assert h.filter_low_signal_chunks(hits, min_len=0) == hits and h.filter_low_signal_chunks([]) == []

    def test_none_content(self):
        out = h.filter_low_signal_chunks([(dv(1, text=None), 0.5), (dv(2, text="я" * 50), 0.4)])

        assert [d.document_id for d, _ in out] == [2]


class TestFusion:
    async def test_graph_neighbours_missing_from_the_seed_pool_are_fetched(self):
        fetched = []

        async def fetch(doc, chunk):
            fetched.append((doc, chunk))
            return dv(doc, chunk)

        seeds = [(dv(1, 0), 0.9), (dv(1, 1), 0.8)]

        out = await h.fuse_seed_and_graph_hits(seeds, {(1, 1): 3.0, (9, 9): 5.0}, fetch_chunk=fetch)

        keys = [(d.document_id, d.chunk_index) for d, _ in out]
        assert set(keys) == {(1, 0), (1, 1), (9, 9)} and fetched == [(9, 9)] and (1, 1) in keys[:2]
        assert [s for _, s in out] == sorted([s for _, s in out], reverse=True)

    async def test_fetch_failures_and_tuples(self):
        async def fetch(doc, chunk):
            if doc == 8:
                raise RuntimeError("db down")
            return (dv(doc, chunk), 0.0)

        out = await h.fuse_seed_and_graph_hits([], {(8, 0): 1.0, (9, 0): 0.5}, fetch_chunk=fetch)

        assert [(d.document_id, d.chunk_index) for d, _ in out] == [(9, 0)]

    async def test_without_a_fetcher_unknown_neighbours_are_dropped(self):
        out = await h.fuse_seed_and_graph_hits([(dv(1, 0), 0.5)], {(9, 9): 1.0}, fetch_chunk=None)

        assert [(d.document_id, d.chunk_index) for d, _ in out] == [(1, 0)]

    async def test_limit_and_weights(self):
        seeds = [(dv(i, 0), 1.0 - i / 100) for i in range(1, 11)]

        assert len(await h.fuse_seed_and_graph_hits(seeds, {}, fetch_chunk=None, limit=3)) == 3
        assert len(await h.fuse_seed_and_graph_hits(seeds, {}, fetch_chunk=None, limit=0)) == 1  # минимум один

    async def test_graph_weight_is_clamped(self):
        out = await h.fuse_seed_and_graph_hits([(dv(1, 0), 0.5)], {(1, 0): 1.0}, fetch_chunk=None, graph_weight=5.0)

        assert out[0][1] == pytest.approx(0.05 / 61 + 0.95 / 61)

    async def test_empty_inputs_and_seeds_without_a_document(self):
        assert await h.fuse_seed_and_graph_hits([], {}, fetch_chunk=None) == []
        assert await h.fuse_seed_and_graph_hits([(dv(None, 0), 0.9)], {}, fetch_chunk=None) == []


class TestMergeKeyword:
    def test_empty_channels_pass_through_sorted(self):
        vec = [(dv(1, 0), 0.5), (dv(2, 0), 0.9)]

        assert [d.document_id for d, _ in h.merge_vector_and_keyword_hits(vec, [])] == [2, 1]
        assert [d.document_id for d, _ in h.merge_vector_and_keyword_hits([], vec)] == [2, 1]
        assert h.merge_vector_and_keyword_hits([], []) == []

    def test_documents_found_by_both_channels_lead(self):
        vec = [(dv(1, 0), 0.9), (dv(2, 0), 0.8), (dv(3, 0), 0.7)]
        kw = [(dv(3, 0), 5.0), (dv(4, 0), 4.0)]

        out = h.merge_vector_and_keyword_hits(vec, kw)

        assert [d.document_id for d, _ in out][0] == 3  # найден обоими каналами
        assert {d.document_id for d, _ in out} == {1, 2, 3, 4}

    def test_keyword_only_hits_are_kept(self):
        out = h.merge_vector_and_keyword_hits([(dv(1, 0), 0.9)], [(dv(2, 0), 3.0)], keyword_weight=0.5)

        assert {d.document_id for d, _ in out} == {1, 2}

    def test_weight_is_clamped(self):
        out = h.merge_vector_and_keyword_hits([(dv(1, 0), 0.9)], [(dv(2, 0), 3.0)], keyword_weight=9.0)

        assert len(out) == 2 and all(s > 0 for _, s in out)

    def test_duplicates_count_once(self):
        vec = [(dv(1, 0), 0.9), (dv(1, 0), 0.8)]

        assert len(h.merge_vector_and_keyword_hits(vec, [(dv(1, 0), 1.0)])) == 1


class TestDiversifyRows:
    ROWS = [("a1", 0.9, 1, 0), ("a2", 0.8, 1, 1), ("a3", 0.7, 1, 2), ("b1", 0.5, 2, 0), ("c1", 0.1, 3, 0)]

    def test_best_chunk_of_each_document_comes_first(self):
        out = h.diversify_result_rows(self.ROWS, 3)

        assert [r[0] for r in out] == ["a1", "b1", "c1"]

    def test_remaining_slots_are_filled_by_score(self):
        assert [r[0] for r in h.diversify_result_rows(self.ROWS, 5)] == ["a1", "b1", "c1", "a2", "a3"]

    def test_pool_limit_cuts_and_rejects_empty(self):
        assert len(h.diversify_result_rows(self.ROWS, 2)) == 2
        assert h.diversify_result_rows([], 3) == [] and h.diversify_result_rows(self.ROWS, 0) == []

    def test_rows_without_a_document_are_one_group(self):
        rows = [("x", 0.9, None, 0), ("y", 0.8, None, 1), ("z", 0.7, 1, 0)]

        assert [r[0] for r in h.diversify_result_rows(rows, 2)] == ["x", "z"]


class TestDiversifyHybrid:
    def hits(self):
        return [(dv(1, 0), 0.9), (dv(1, 1), 0.8), (dv(1, 2), 0.7), (dv(2, 0), 0.6), (dv(3, 0), 0.5)]

    def keys(self, out):
        return [(d.document_id, d.chunk_index) for d, _ in out]

    def test_per_document_limit_keeps_rrf_order(self):
        out = h.diversify_hybrid_rrf_hits(self.hits(), candidate_limit=4, max_chunks_per_document=1, min_results=1)

        assert self.keys(out) == [(1, 0), (2, 0), (3, 0)]

    def test_min_results_pulls_back_the_overflow(self):
        out = h.diversify_hybrid_rrf_hits(self.hits(), candidate_limit=5, max_chunks_per_document=1, min_results=5)

        assert len(out) == 5 and self.keys(out)[:3] == [(1, 0), (2, 0), (3, 0)]

    def test_single_document_is_left_alone(self):
        hits = [(dv(1, i), 1.0 - i / 10) for i in range(5)]

        assert len(h.diversify_hybrid_rrf_hits(hits, candidate_limit=3, max_chunks_per_document=1, min_results=1)) == 3

    def test_zero_per_document_limit_means_plain_truncation(self):
        assert len(h.diversify_hybrid_rrf_hits(self.hits(), candidate_limit=2, max_chunks_per_document=0, min_results=1)) == 2

    def test_empty_and_bad_limits(self):
        assert h.diversify_hybrid_rrf_hits([], candidate_limit=3, max_chunks_per_document=1, min_results=1) == []
        assert h.diversify_hybrid_rrf_hits(self.hits(), candidate_limit=0, max_chunks_per_document=1, min_results=1) == []

    def test_chunks_without_a_document_are_never_limited(self):
        hits = [(dv(None, 0), 0.9), (dv(None, 1), 0.8), (dv(1, 0), 0.7), (dv(2, 0), 0.6)]

        assert len(h.diversify_hybrid_rrf_hits(hits, candidate_limit=4, max_chunks_per_document=1, min_results=1)) == 4


class TestDiversifyByDocument:
    def hits(self):
        return [(dv(1, 0), 0.9), (dv(1, 1), 0.85), (dv(2, 0), 0.5), (dv(3, 0), 0.4), (dv(3, 1), 0.3)]

    def keys(self, out):
        return [(d.document_id, d.chunk_index) for d, _ in out]

    def test_one_best_chunk_per_document_then_fill(self):
        assert self.keys(h.diversify_hits_by_document(self.hits(), 3)) == [(1, 0), (2, 0), (3, 0)]
        assert self.keys(h.diversify_hits_by_document(self.hits(), 5)) == [(1, 0), (2, 0), (3, 0), (1, 1), (3, 1)]

    def test_entity_documents_keep_all_their_chunks(self):
        out = h.diversify_hits_by_document(self.hits(), 3, keep_all_for_docs={3})

        assert self.keys(out)[:2] == [(3, 0), (3, 1)] and len(out) >= 3

    def test_entity_chunks_may_exceed_the_pool_limit(self):
        out = h.diversify_hits_by_document([(dv(3, i), 0.9) for i in range(6)], 2, keep_all_for_docs={3})

        assert len(out) == 6

    def test_empty(self):
        assert h.diversify_hits_by_document([], 3) == [] and h.diversify_hits_by_document(self.hits(), 0) == []

    def test_chunks_without_a_document_only_appear_in_the_fill_phase(self):
        out = h.diversify_hits_by_document([(dv(None, 0), 0.9), (dv(1, 0), 0.5)], 2)

        assert self.keys(out) == [(1, 0), (None, 0)]


class TestMetrics:
    def test_no_hits(self):
        lines = h.build_retrieval_metrics_lines([], 5)

        assert lines[0] == "hits_в_ответе=0 k_запрошено=5" and "подсказка" in lines[1]

    def test_summary_lines(self):
        hits = [("a" * 100, 0.9, 1, 0), ("b" * 10, 0.1, 2, 0), ("", 0.05, 2, 1)]

        lines = h.build_retrieval_metrics_lines(hits, 3)
        text = "\n".join(lines)

        assert "hits_в_ответе=3" in text and "top1=0.9000" in text and "margin_top1_minus_top2=0.8000" in text and "уникальных_document_id=2" in text
        assert "сумма_в_контекст=110" in text and "очень_коротких_чанков(<80 симв)=2" in text and "пустых_чанков=1" in text and "низкий_score(<0.15)=2" in text

    def test_single_hit_has_no_margin_or_low_score_line(self):
        text = "\n".join(h.build_retrieval_metrics_lines([("x" * 200, 0.05, None, None)], 1))

        assert "margin" not in text and "низкий_score" not in text and "уникальных_document_id=0" in text

    def test_report_goes_to_stderr_unless_disabled(self, capsys, monkeypatch):
        kwargs = dict(store="kb", strategy_resolved="hybrid", pipeline="vector+bm25", final_hits=[("текст", 0.5, 1, 0)], k_requested=3, search_seconds=0.1234, extra_lines=["доп"], eval_lines=["gold=1.0"])

        h.log_rag_retrieval_report(**kwargs)
        err = capsys.readouterr().err
        assert "Использована стратегия поиска: hybrid" in err and "Хранилище: kb" in err and "время_поиска_с=0.1234" in err and "gold=1.0" in err and err.count("*" * h.BANNER_WIDTH) == 2

        monkeypatch.setenv("RAG_METRICS_STDOUT", "0")
        h.log_rag_retrieval_report(**kwargs)
        assert capsys.readouterr().err == ""
