"""rag_query/postprocess, preprocess, metadata_filters, document_duplicate."""

import hashlib

import pytest
from fastapi import HTTPException

from backend.rag_query import document_duplicate as dup
from backend.rag_query.hit_types import RagHit
from backend.rag_query.metadata_filters import extract_filters_from_query
from backend.rag_query.postprocess import _word_jaccard, dedupe_rag_hits
from backend.rag_query.preprocess import normalize_query

pytestmark = pytest.mark.unit

LONG_A = "Квартальный отчёт показывает рост выручки на двенадцать процентов по сравнению с прошлым годом"
LONG_B = "Совершенно другой документ описывает порядок согласования договоров поставки с контрагентами"


class TestNormalizeQuery:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", ""),
            (None, ""),
            ("  hello   world  ", "hello world"),
            ("a\t\tb", "a b"),
            ("a\n\n\n\nb", "a\n\nb"),
            ("a\nb", "a\nb"),
            ("привет   мир", "привет мир"),
        ],
    )
    def test_cases(self, raw, expected):
        assert normalize_query(raw) == expected

    def test_composes_unicode_to_nfc(self):
        decomposed = "ё"  # е + диакритика (NFD)

        assert normalize_query(decomposed) == "ё"

    def test_is_idempotent(self):
        once = normalize_query("  a \t b\n\n\n\n c ")

        assert normalize_query(once) == once


def test_metadata_filters_are_disabled():
    assert extract_filters_from_query("отчёт за 2024 файл x.pdf") is None


class TestWordJaccard:
    def test_identical(self):
        assert _word_jaccard("a b c", "a b c") == 1.0

    def test_disjoint(self):
        assert _word_jaccard("a b", "c d") == 0.0

    def test_partial(self):
        assert _word_jaccard("a b c", "b c d") == pytest.approx(0.5)

    def test_both_empty_are_equal(self):
        assert _word_jaccard("", "") == 1.0

    def test_one_empty(self):
        assert _word_jaccard("a", "") == 0.0

    def test_cyrillic_words_are_tokenized(self):
        assert _word_jaccard("привет мир", "мир привет") == 1.0


class TestDedupe:
    def test_empty(self):
        assert dedupe_rag_hits([]) == []

    def test_same_document_and_chunk_is_dropped(self):
        hits = [("first", 0.9, 1, 0), ("second", 0.8, 1, 0)]

        assert dedupe_rag_hits(hits) == [hits[0]]

    def test_same_chunk_index_in_other_document_is_kept(self):
        hits = [("a", 0.9, 1, 0), ("b", 0.8, 2, 0)]

        assert dedupe_rag_hits(hits) == hits

    def test_hits_without_ids_are_never_deduped_by_key(self):
        hits = [("a", 0.9, None, None), ("b", 0.8, None, None)]

        assert dedupe_rag_hits(hits) == hits

    def test_near_duplicate_text_is_dropped_keeping_first(self):
        hits = [
            (LONG_A, 0.9, 1, 0),
            (LONG_A + " !", 0.8, 2, 5),
            (LONG_B, 0.7, 3, 1),
        ]

        kept = dedupe_rag_hits(hits)

        assert [h[2] for h in kept] == [1, 3]

    def test_short_texts_skip_text_comparison(self):
        hits = [("same short", 0.9, 1, 0), ("same short", 0.8, 2, 0)]

        assert len(dedupe_rag_hits(hits)) == 2

    def test_threshold_is_configurable(self):
        hits = [(LONG_A, 0.9, 1, 0), (LONG_A + " дополнительный хвост текста", 0.8, 2, 0)]

        assert len(dedupe_rag_hits(hits, jaccard_threshold=0.99)) == 2
        assert len(dedupe_rag_hits(hits, jaccard_threshold=0.5)) == 1

    def test_max_hits_truncates(self):
        hits = [(f"t{i}", 1.0, i, 0) for i in range(10)]

        assert len(dedupe_rag_hits(hits, max_hits=3)) == 3

    def test_comparison_ignores_case_and_whitespace(self):
        hits = [(LONG_A, 0.9, 1, 0), ("  " + LONG_A.upper().replace(" ", "   "), 0.8, 2, 0)]

        assert len(dedupe_rag_hits(hits)) == 1

    def test_keeps_original_objects_so_cosine_survives(self):
        hit = RagHit(LONG_A, 0.9, 1, 0, cosine=0.77)

        kept = dedupe_rag_hits([hit])

        assert kept[0] is hit
        assert kept[0].cosine == 0.77

    def test_order_is_preserved(self):
        hits = [(LONG_B, 0.5, 2, 0), (LONG_A, 0.9, 1, 0)]

        assert dedupe_rag_hits(hits) == hits


class TestDocumentDuplicate:
    def test_hash_is_sha256_hex(self):
        assert dup.file_content_hash(b"abc") == hashlib.sha256(b"abc").hexdigest()

    def test_hash_of_empty_and_none(self):
        assert dup.file_content_hash(b"") == dup.file_content_hash(None)

    def test_different_content_different_hash(self):
        assert dup.file_content_hash(b"a") != dup.file_content_hash(b"b")

    def test_detail_defaults(self):
        detail = dup.duplicate_http_detail()

        assert detail == {
            "code": "duplicate_document",
            "message": dup.DUPLICATE_MESSAGE,
            "document_id": None,
            "filename": None,
        }

    def test_detail_custom(self):
        detail = dup.duplicate_http_detail(document_id=5, filename="a.pdf", message="m")

        assert (detail["document_id"], detail["filename"], detail["message"]) == (5, "a.pdf", "m")

    def test_raise_gives_409(self):
        with pytest.raises(HTTPException) as exc:
            dup.raise_duplicate_http(document_id=1, filename="x")

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "duplicate_document"

    @pytest.mark.parametrize(
        ("detail", "expected"),
        [
            ({"code": "duplicate_document"}, True),
            ({"code": " duplicate_document "}, True),
            ({"code": "other"}, False),
            ({}, False),
            ("duplicate_document", True),
            ("Упс, такой документ УЖЕ ПОДГРУЖЕН В БАЗУ", True),
            ("something else", False),
            (None, False),
            (123, False),
        ],
    )
    def test_is_duplicate_detail(self, detail, expected):
        assert dup.is_duplicate_detail(detail) is expected

    def test_raised_detail_is_recognized(self):
        with pytest.raises(HTTPException) as exc:
            dup.raise_duplicate_http()

        assert dup.is_duplicate_detail(exc.value.detail)
