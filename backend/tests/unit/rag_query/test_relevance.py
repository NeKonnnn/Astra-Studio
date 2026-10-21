"""rag_query/relevance + hit_types: проценты релевантности на абсолютной шкале cosine."""

import math

import pytest

from backend.rag_query import relevance
from backend.rag_query.hit_types import RagHit, hit_cosine

pytestmark = pytest.mark.unit


class TestRagHit:
    def test_unpacks_like_four_tuple(self):
        hit = RagHit("text", 0.5, 7, 2, cosine=0.8)

        content, score, doc_id, chunk = hit

        assert (content, score, doc_id, chunk) == ("text", 0.5, 7, 2)
        assert len(hit) == 4

    def test_cosine_is_attribute_not_tuple_member(self):
        hit = RagHit("t", 1.0, None, None, cosine=3)

        assert hit.cosine == 3.0
        assert isinstance(hit.cosine, float)

    def test_cosine_defaults_to_none(self):
        assert RagHit("t", 1.0, 1, 1).cosine is None

    def test_equal_to_plain_tuple(self):
        assert RagHit("t", 1.0, 1, 1, cosine=0.1) == ("t", 1.0, 1, 1)


class TestHitCosine:
    def test_reads_value(self):
        assert hit_cosine(RagHit("t", 1, 1, 1, cosine=0.42)) == 0.42

    def test_plain_tuple_has_no_cosine(self):
        assert hit_cosine(("t", 1.0, 1, 1)) is None

    def test_garbage_value_is_none(self):
        class Fake:
            cosine = "not-a-number"

        assert hit_cosine(Fake()) is None

    def test_numeric_string_is_parsed(self):
        class Fake:
            cosine = "0.5"

        assert hit_cosine(Fake()) == 0.5


class TestCosinePercent:
    @pytest.mark.parametrize(
        ("cosine", "expected"),
        [
            (1.0, 100),
            (0.5, 50),
            (0.876, 88),
            (0.004, 1),  # нижняя граница шкалы — 1, а не 0
            (0.0, 1),
            (-0.7, 1),  # противоположное направление = нет близости
            (1.5, 100),
        ],
    )
    def test_values(self, cosine, expected):
        assert relevance.cosine_to_relevance_percent(cosine) == expected

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc", None])
    def test_non_finite_or_garbage_gives_minimum(self, bad):
        assert relevance.cosine_to_relevance_percent(bad) == 1


class TestScorePercent:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [(0.0, 1), (0.25, 25), (1.0, 100)],
    )
    def test_unit_interval_is_read_as_cosine(self, score, expected):
        assert relevance.score_to_relevance_percent(score) == expected

    def test_logits_go_through_sigmoid(self):
        assert relevance.score_to_relevance_percent(5) == round(100 / (1 + math.exp(-5)))
        assert relevance.score_to_relevance_percent(-5) == 1

    def test_large_logit_is_capped(self):
        assert relevance.score_to_relevance_percent(50) == 100

    def test_nan_gives_minimum(self):
        assert relevance.score_to_relevance_percent(float("nan")) == 1

    @pytest.mark.parametrize("score", [-100, -1, 0.3, 2, 40])
    def test_always_in_range(self, score):
        assert 1 <= relevance.score_to_relevance_percent(score) <= 100


class TestPercentOrNone:
    def test_uses_cosine(self):
        assert relevance.relevance_percent_or_none(RagHit("t", 0.01, 1, 1, cosine=0.9)) == 90

    def test_none_when_cosine_unknown(self):
        assert relevance.relevance_percent_or_none(RagHit("t", 0.9, 1, 1)) is None


class TestHitsToPercents:
    def test_empty(self):
        assert relevance.hits_to_relevance_percents([]) == []

    def test_all_with_cosine_use_absolute_scale(self):
        hits = [RagHit("a", 0.0164, 1, 0, cosine=0.9), RagHit("b", 0.0159, 1, 1, cosine=0.8)]

        assert relevance.hits_to_relevance_percents(hits) == [90, 80]

    def test_neighbouring_rrf_scores_are_not_stretched(self):
        # Регресс: раньше min-max давал 100% против ~6% для соседних рангов.
        hits = [RagHit("a", 0.01639, 1, 0), RagHit("b", 0.01587, 1, 1)]

        percents = relevance.hits_to_relevance_percents(hits)

        assert percents == [2, 2]

    def test_without_any_cosine_falls_back_to_score(self):
        hits = [("a", 0.8, 1, 0), ("b", 0.4, 1, 1)]

        assert relevance.hits_to_relevance_percents(hits) == [80, 40]

    def test_mixed_hits_use_cosine_where_present(self):
        hits = [RagHit("a", 0.3, 1, 0, cosine=0.95), RagHit("b", 0.4, 1, 1)]

        assert relevance.hits_to_relevance_percents(hits) == [95, 40]

    def test_broken_hit_scores_as_zero(self):
        assert relevance.hits_to_relevance_percents([object()]) == [1]

    def test_percents_follow_input_order_and_length(self):
        hits = [RagHit(str(i), 0.1, 1, i, cosine=i / 10) for i in range(1, 6)]

        assert relevance.hits_to_relevance_percents(hits) == [10, 20, 30, 40, 50]


class TestScoresToPercents:
    def test_empty(self):
        assert relevance.scores_to_relevance_percents([]) == []

    def test_no_min_max_stretch(self):
        assert relevance.scores_to_relevance_percents([0.9, 0.9]) == [90, 90]

    def test_single_equals_batch_conversion(self):
        scores = [0.12, 0.7, 3.5, -2.0]

        assert relevance.scores_to_relevance_percents(scores) == [
            relevance.score_to_relevance_percent(s) for s in scores
        ]
