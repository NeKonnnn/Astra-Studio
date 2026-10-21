"""svc-rag: RagHit, порог реранка и sentence window, дубликаты по тексту, мелкие хелперы."""

import math

import pytest

from app.database.search_filters import DocumentVectorSearchFilters
from app.services import text_duplicate
from app.services.hit_postprocess import apply_rerank_min_and_window
from app.services.hit_types import RagHit, as_rag_hit, cosine_similarity, hit_cosine
from app.text_sanitize import strip_null_bytes

pytestmark = pytest.mark.unit


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_opposite(self):
        assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_scale_invariant(self):
        assert cosine_similarity([1, 2], [10, 20]) == pytest.approx(1.0)

    @pytest.mark.parametrize(
        ("a", "b"),
        [([], [1]), ([1], []), ([1, 2], [1]), ([0, 0], [1, 1]), ([1, 1], [0, 0])],
    )
    def test_undefined_cases_give_none(self, a, b):
        assert cosine_similarity(a, b) is None

    def test_nan_input_gives_none(self):
        assert cosine_similarity([math.nan, 1], [1, 1]) is None

    def test_result_is_clamped_to_unit_interval(self):
        value = cosine_similarity([0.1, 0.1, 0.1], [0.1, 0.1, 0.1])

        assert -1.0 <= value <= 1.0


class TestRagHit:
    def test_tuple_semantics(self):
        hit = RagHit("t", 0.5, 3, 1, cosine=0.9)

        assert tuple(hit) == ("t", 0.5, 3, 1)
        assert hit.cosine == 0.9

    def test_with_content_keeps_everything_else(self):
        hit = RagHit("old", 0.5, 3, 1, cosine=0.9)

        new = hit.with_content("new")

        assert new == ("new", 0.5, 3, 1)
        assert new.cosine == 0.9
        assert isinstance(new, RagHit)

    def test_hit_cosine_variants(self):
        assert hit_cosine(RagHit("t", 1, 1, 1, cosine=0.3)) == 0.3
        assert hit_cosine(("t", 1, 1, 1)) is None

        class Bad:
            cosine = "x"

        assert hit_cosine(Bad()) is None

    def test_as_rag_hit_from_plain_tuple(self):
        hit = as_rag_hit(("t", 2, 5, 6))

        assert isinstance(hit, RagHit)
        assert hit == ("t", 2.0, 5, 6)
        assert hit.cosine is None

    def test_as_rag_hit_returns_ready_hit_untouched(self):
        hit = RagHit("t", 1, 1, 1, cosine=0.4)

        assert as_rag_hit(hit) is hit

    def test_as_rag_hit_can_override_cosine(self):
        hit = RagHit("t", 1, 1, 1, cosine=0.4)

        assert as_rag_hit(hit, cosine=0.8).cosine == 0.8


class FakeVectorRepo:
    def __init__(self, chunks=None, fail=False):
        self.chunks = chunks or {}
        self.fail = fail
        self.calls = []

    async def get_chunk_contents_by_indices(self, doc_id, indices):
        self.calls.append((doc_id, list(indices)))
        if self.fail:
            raise RuntimeError("db down")
        return {i: self.chunks[doc_id][i] for i in indices if i in self.chunks.get(doc_id, {})}


class TestRerankMinAndWindow:
    async def test_threshold_applies_only_after_rerank(self):
        rows = [("a", 0.9, 1, 0), ("b", 0.2, 1, 1)]

        with_rerank = await apply_rerank_min_and_window(
            FakeVectorRepo(), rows, rerank_min_score=0.5, sentence_window=0, used_rerank=True
        )
        without = await apply_rerank_min_and_window(
            FakeVectorRepo(), rows, rerank_min_score=0.5, sentence_window=0, used_rerank=False
        )

        assert with_rerank == [rows[0]]
        assert without == rows

    async def test_zero_threshold_keeps_everything(self):
        rows = [("a", -3.0, 1, 0)]

        out = await apply_rerank_min_and_window(
            FakeVectorRepo(), rows, rerank_min_score=0, sentence_window=0, used_rerank=True
        )

        assert out == rows

    async def test_window_merges_neighbours_in_order(self):
        repo = FakeVectorRepo({1: {0: "zero", 1: "one", 2: "two", 3: "three"}})

        out = await apply_rerank_min_and_window(
            repo, [("one", 0.9, 1, 1)], rerank_min_score=0, sentence_window=1, used_rerank=False
        )

        assert out[0][0] == "zero\n---\none\n---\ntwo"
        assert repo.calls == [(1, [0, 1, 2])]

    async def test_window_does_not_ask_for_negative_indices(self):
        repo = FakeVectorRepo({1: {0: "zero", 1: "one"}})

        await apply_rerank_min_and_window(
            repo, [("zero", 0.9, 1, 0)], rerank_min_score=0, sentence_window=2, used_rerank=False
        )

        assert repo.calls == [(1, [0, 1, 2])]

    async def test_window_keeps_cosine(self):
        repo = FakeVectorRepo({1: {0: "a", 1: "b"}})
        hit = RagHit("b", 0.9, 1, 1, cosine=0.77)

        out = await apply_rerank_min_and_window(
            repo, [hit], rerank_min_score=0, sentence_window=1, used_rerank=False
        )

        assert out[0].cosine == 0.77
        assert out[0][0] == "a\n---\nb"

    async def test_rows_without_ids_are_not_expanded(self):
        repo = FakeVectorRepo()
        rows = [("x", 0.5, None, None), ("y", 0.5, 1, None)]

        out = await apply_rerank_min_and_window(
            repo, rows, rerank_min_score=0, sentence_window=1, used_rerank=False
        )

        assert out == rows
        assert repo.calls == []

    async def test_fetch_error_keeps_original_row(self):
        rows = [("x", 0.5, 1, 1)]

        out = await apply_rerank_min_and_window(
            FakeVectorRepo(fail=True), rows, rerank_min_score=0, sentence_window=1, used_rerank=False
        )

        assert out == rows

    async def test_empty_neighbour_map_keeps_original_row(self):
        rows = [("x", 0.5, 1, 1)]

        out = await apply_rerank_min_and_window(
            FakeVectorRepo({}), rows, rerank_min_score=0, sentence_window=1, used_rerank=False
        )

        assert out == rows

    async def test_repo_without_chunk_lookup_returns_filtered_rows(self):
        rows = [("x", 0.5, 1, 1)]

        out = await apply_rerank_min_and_window(
            object(), rows, rerank_min_score=0, sentence_window=1, used_rerank=False
        )

        assert out == rows

    async def test_empty_input(self):
        assert (
            await apply_rerank_min_and_window(
                FakeVectorRepo(), [], rerank_min_score=1, sentence_window=2, used_rerank=True
            )
            == []
        )


class TestTextFingerprint:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        text_duplicate._fingerprints.clear()
        yield
        text_duplicate._fingerprints.clear()

    def test_ignores_whitespace_newlines_and_invisible_characters(self):
        a = "Привет,   мир!\n"
        b = "﻿Привет,​ мир!"

        assert text_duplicate.text_fingerprint(a) == text_duplicate.text_fingerprint(b)

    def test_is_case_sensitive(self):
        assert text_duplicate.text_fingerprint("Abc") != text_duplicate.text_fingerprint("abc")

    def test_nfkc_equivalents_match(self):
        assert text_duplicate.text_fingerprint("ﬁle") == text_duplicate.text_fingerprint("file")

    @pytest.mark.parametrize("empty", [None, "", "   \n\t", "​﻿"])
    def test_empty_text_has_empty_fingerprint(self, empty):
        assert text_duplicate.text_fingerprint(empty) == ""

    def test_fingerprint_is_sha256_hex(self):
        assert len(text_duplicate.text_fingerprint("x")) == 64


class TestFindTextDuplicate:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        text_duplicate._fingerprints.clear()
        yield
        text_duplicate._fingerprints.clear()

    @staticmethod
    def _loader(texts, calls=None):
        async def load(doc_id):
            if calls is not None:
                calls.append(doc_id)
            return texts[doc_id]

        return load

    async def test_finds_document_with_same_text(self):
        texts = {1: "other", 2: "Тот же  текст\n", 3: "third"}

        found = await text_duplicate.find_text_duplicate(
            store="kb",
            text="Тот же текст",
            candidates=[(1, "a.txt"), (2, "b.txt"), (3, "c.txt")],
            load_text=self._loader(texts),
        )

        assert found == {"id": 2, "filename": "b.txt"}

    async def test_no_match(self):
        found = await text_duplicate.find_text_duplicate(
            store="kb",
            text="unique",
            candidates=[(1, "a")],
            load_text=self._loader({1: "different"}),
        )

        assert found is None

    async def test_empty_text_is_never_a_duplicate(self):
        found = await text_duplicate.find_text_duplicate(
            store="kb", text="  ", candidates=[(1, "a")], load_text=self._loader({1: ""})
        )

        assert found is None

    async def test_text_is_loaded_only_once_per_document(self):
        calls = []
        loader = self._loader({1: "aaa", 2: "bbb"}, calls)
        kwargs = dict(store="kb", candidates=[(1, "a"), (2, "b")], load_text=loader)

        await text_duplicate.find_text_duplicate(text="new one", **kwargs)
        await text_duplicate.find_text_duplicate(text="another", **kwargs)

        assert calls == [1, 2]

    async def test_stores_are_isolated(self):
        calls = []
        loader = self._loader({1: "aaa"}, calls)

        await text_duplicate.find_text_duplicate(
            store="kb", text="x", candidates=[(1, "a")], load_text=loader
        )
        await text_duplicate.find_text_duplicate(
            store="project", text="x", candidates=[(1, "a")], load_text=loader
        )

        assert calls == [1, 1]

    async def test_older_than_skips_newer_documents(self):
        texts = {5: "same", 9: "same"}

        found = await text_duplicate.find_text_duplicate(
            store="kb",
            text="same",
            candidates=[(5, "early"), (9, "late")],
            load_text=self._loader(texts),
            older_than=9,
        )
        none_for_earliest = await text_duplicate.find_text_duplicate(
            store="kb",
            text="same",
            candidates=[(5, "early"), (9, "late")],
            load_text=self._loader(texts),
            older_than=5,
        )

        assert found == {"id": 5, "filename": "early"}
        assert none_for_earliest is None

    async def test_unreadable_document_is_skipped(self):
        async def load(doc_id):
            if doc_id == 1:
                raise OSError("gone")
            return "same"

        found = await text_duplicate.find_text_duplicate(
            store="kb", text="same", candidates=[(1, "a"), (2, "b")], load_text=load
        )

        assert found == {"id": 2, "filename": "b"}

    async def test_remember_text_avoids_loading(self):
        calls = []
        text_duplicate.remember_text("kb", 1, "known text")

        found = await text_duplicate.find_text_duplicate(
            store="kb",
            text="known  text",
            candidates=[(1, "a")],
            load_text=self._loader({1: "ignored"}, calls),
        )

        assert found == {"id": 1, "filename": "a"}
        assert calls == []

    def test_remember_text_ignores_missing_id(self):
        text_duplicate.remember_text("kb", None, "x")

        assert text_duplicate._fingerprints == {}

    async def test_cache_is_dropped_when_it_grows_too_large(self, monkeypatch):
        monkeypatch.setattr(text_duplicate, "_MAX_CACHED", 2)
        for i in range(5):
            text_duplicate.remember_text("kb", i, f"t{i}")

        await text_duplicate.find_text_duplicate(
            store="kb", text="x", candidates=[], load_text=self._loader({})
        )

        assert text_duplicate._fingerprints == {}


class TestSmallHelpers:
    def test_strip_null_bytes(self):
        assert strip_null_bytes("a\x00b\x00") == "ab"
        assert strip_null_bytes("clean") == "clean"
        assert strip_null_bytes("") == ""
        assert strip_null_bytes(None) is None

    @pytest.mark.parametrize(
        ("filters", "active"),
        [
            (DocumentVectorSearchFilters(), False),
            (DocumentVectorSearchFilters(filename_contains="  "), False),
            (DocumentVectorSearchFilters(filename_contains="report"), True),
        ],
    )
    def test_search_filters_activity(self, filters, active):
        assert filters.active() is active

    def test_search_filters_with_dates_are_active(self):
        from datetime import datetime

        assert DocumentVectorSearchFilters(date_from=datetime(2026, 1, 1)).active()
        assert DocumentVectorSearchFilters(date_to=datetime(2026, 1, 1)).active()
