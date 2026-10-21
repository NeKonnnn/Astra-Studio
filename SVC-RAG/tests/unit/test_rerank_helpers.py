"""services/rerank_helpers: порядок от cross-encoder, честный признак успеха, порог по скору."""

from types import SimpleNamespace

import pytest

from app.services import rerank_helpers as rr

pytestmark = pytest.mark.unit


def dv(doc, chunk, text="текст"):
    return SimpleNamespace(document_id=doc, chunk_index=chunk, content=text)


HITS = [(dv(1, 0, "первый"), 0.9), (dv(1, 1, "второй"), 0.5), (dv(2, 0, "третий"), 0.1)]


class FakeClient:
    def __init__(self, ranked=None, error=None):
        self.ranked = ranked
        self.error = error
        self.calls = []

    async def rerank(self, query, contents, top_k, model=None):
        self.calls.append((query, list(contents), top_k, model))
        if self.error:
            raise self.error
        return self.ranked


class TestRerank:
    async def test_cross_encoder_order_wins_over_the_prior_scores(self):
        client = FakeClient(ranked=[(2, 8.0), (0, 3.0), (1, -1.0)])

        out, ok = await rr.rerank_vector_hits_ex("запрос", HITS, client, top_k=3)

        assert ok is True and [h[0].content for h in out] == ["третий", "первый", "второй"]
        assert [round(h[1], 3) for h in out] == [8.0, 3.0, -1.0]

    async def test_request_to_the_reranker(self):
        client = FakeClient(ranked=[(0, 1.0)])

        await rr.rerank_vector_hits_ex("запрос", HITS, client, top_k=2, model="bge")

        assert client.calls == [("запрос", ["первый", "второй", "третий"], 3, "bge")]  # min(3, max(2*2, 2))

    async def test_requested_pool_never_exceeds_the_candidates(self):
        client = FakeClient(ranked=[(0, 1.0)])

        await rr.rerank_vector_hits_ex("q", HITS, client, top_k=50)

        assert client.calls[0][2] == 3

    async def test_prior_is_only_a_micro_tiebreak(self):
        client = FakeClient(ranked=[(2, 5.0), (0, 5.0)])  # одинаковый скор реранкера

        out, _ = await rr.rerank_vector_hits_ex("q", HITS, client, top_k=2)

        assert [h[0].content for h in out] == ["третий", "первый"]  # порядок реранкера сохранён
        assert abs(out[0][1] - 5.0) < 1e-3 and abs(out[1][1] - 5.0) < 1e-3

    async def test_top_k_trims_the_result(self):
        client = FakeClient(ranked=[(2, 3.0), (1, 2.0), (0, 1.0)])

        out, ok = await rr.rerank_vector_hits_ex("q", HITS, client, top_k=2)

        assert ok and len(out) == 2

    async def test_top_k_zero_returns_everything(self):
        client = FakeClient(ranked=[(2, 3.0), (1, 2.0), (0, 1.0)])

        out, _ = await rr.rerank_vector_hits_ex("q", HITS, client, top_k=0)

        assert len(out) == 3

    async def test_indexes_outside_the_candidate_list_are_skipped(self):
        client = FakeClient(ranked=[(7, 9.0), (1, 2.0)])

        out, ok = await rr.rerank_vector_hits_ex("q", HITS, client, top_k=5)

        assert ok and [h[0].content for h in out] == ["второй"]

    async def test_reranker_failure_is_reported_and_keeps_the_vector_order(self):
        out, ok = await rr.rerank_vector_hits_ex("q", HITS, FakeClient(error=RuntimeError("404 /v1/rerank")), top_k=2)

        assert ok is False and out == HITS[:2]

    async def test_empty_reranker_answer_is_not_a_success(self):
        out, ok = await rr.rerank_vector_hits_ex("q", HITS, FakeClient(ranked=[]), top_k=2)

        assert ok is False and out == HITS[:2]

    async def test_nothing_to_rerank(self):
        client = FakeClient(ranked=[(0, 1.0)])

        assert await rr.rerank_vector_hits_ex("q", [], client, top_k=3) == ([], False)
        assert await rr.rerank_vector_hits_ex("q", HITS, None, top_k=2) == (HITS[:2], False)
        assert await rr.rerank_vector_hits_ex("q", HITS, None, top_k=0) == (HITS, False)
        assert client.calls == []

    async def test_compat_wrapper_returns_only_the_order(self):
        client = FakeClient(ranked=[(1, 2.0), (0, 1.0)])

        out = await rr.rerank_vector_hits("q", HITS, client, top_k=2, vector_weight=0.9)

        assert [h[0].content for h in out] == ["второй", "первый"]

    async def test_debug_table_does_not_break_the_ranking(self, caplog):
        caplog.set_level("DEBUG")

        out, ok = await rr.rerank_vector_hits_ex("  длинный\nзапрос  ", HITS, FakeClient(ranked=[(1, 2.0), (0, 1.0)]), top_k=1)

        assert ok and len(out) == 1


class TestMinScore:
    ROWS = [("a", 0.9), ("b", 0.5), ("c", 0.1), ("d", None), ("e", "x"), ("f",)]

    def test_non_positive_threshold_keeps_everything(self):
        assert rr.filter_by_min_score(self.ROWS, 0.0) == self.ROWS and rr.filter_by_min_score(self.ROWS, -1) == self.ROWS

    def test_threshold_is_inclusive_and_bad_rows_are_dropped(self):
        assert rr.filter_by_min_score(self.ROWS, 0.5) == [("a", 0.9), ("b", 0.5)]

    def test_score_index(self):
        rows = [("a", "x", 0.8), ("b", "y", 0.2)]

        assert rr.filter_by_min_score(rows, 0.5, score_index=2) == [("a", "x", 0.8)]

    def test_numeric_strings_are_accepted(self):
        assert rr.filter_by_min_score([("a", "0.7")], 0.5) == [("a", "0.7")]

    def test_empty(self):
        assert rr.filter_by_min_score([], 0.5) == []


class TestLogHelpers:
    def test_chunk_label_and_preview(self):
        assert rr._chunk_label(dv(3, 7)) == "doc3:чанк7" and rr._chunk_label(SimpleNamespace()) == "docNone:чанкNone"
        assert rr._preview(dv(1, 0, "a  b\n c")) == "a b c"
        assert rr._preview(dv(1, 0, "я" * 200), limit=10) == "я" * 10 + "…" and rr._preview(SimpleNamespace(content=None)) == ""
