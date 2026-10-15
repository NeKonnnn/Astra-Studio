# -*- coding: utf-8 -*-
"""Тесты SVC-RAG: чанкер, text_sanitize, фильтры по скору, sentence window, RagHit."""

from __future__ import annotations

import pytest

from app.services import chunker
from app.services.hit_postprocess import apply_rerank_min_and_window
from app.services.hit_types import RagHit, hit_cosine
from app.services.rerank_helpers import filter_by_min_score
from app.text_sanitize import strip_null_bytes

SENTENCE = "Это тестовое предложение для проверки разбиения текста на чанки. "


class TestStripNullBytes:
    @pytest.mark.parametrize(
        "raw, expected",
        [("", ""), (None, None), ("abc", "abc"), ("a\x00b\x00", "ab"), ("\x00\x00", "")],
    )
    def test_strip(self, raw, expected):
        assert strip_null_bytes(raw) == expected


class TestNormalizeStrategy:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (None, "universal"),
            ("", "universal"),
            ("  FIXED ", "fixed"),
            ("Markdown", "markdown"),
            ("separators", "separators"),
            ("semantic", "semantic"),
            ("hierarchical", "hierarchical"),
            ("structure", "universal"),
            ("library", "universal"),
            ("default", "universal"),
            ("unknown-strategy", "universal"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert chunker.normalize_chunking_strategy(raw) == expected


class TestResolveChunkParams:
    def test_explicit_values(self):
        assert chunker.resolve_chunk_params(500, 50) == (500, 50)

    def test_min_chunk_size_is_200(self):
        assert chunker.resolve_chunk_params(10, 0)[0] == 200

    def test_negative_overlap_clamped(self):
        assert chunker.resolve_chunk_params(500, -5) == (500, 0)

    def test_overlap_not_less_than_size_is_reduced(self):
        size, overlap = chunker.resolve_chunk_params(400, 400)
        assert size == 400
        assert overlap == 100

    def test_defaults_come_from_settings(self):
        size, overlap = chunker.resolve_chunk_params()
        assert size >= 200
        assert 0 <= overlap < size


class TestDescribeEmbedClient:
    def test_native_client(self):
        class Native:
            pass

        assert chunker.describe_embed_client(Native()) == "native(svc-rag-models)"

    def test_openai_compat_client(self):
        class Compat:
            provider_id = "acme"
            embedding_model = "emb-1"

        assert chunker.describe_embed_client(Compat()) == "acme(emb-1)"

    def test_provider_without_model(self):
        class Compat:
            provider_id = "acme"
            embedding_model = None

        assert chunker.describe_embed_client(Compat()) == "acme(?)"

    def test_split_client_unwrapped(self):
        class Inner:
            provider_id = "inner"
            embedding_model = "m"

        class Split:
            embed_client = Inner()

        assert chunker.describe_embed_client(Split()) == "inner(m)"


class TestStructureHelpers:
    def test_page_breaks(self):
        text = "стр1 текст\n--- Страница 2 ---\nстр2 текст"
        assert chunker._split_by_page_breaks(text) == ["стр1 текст", "стр2 текст"]

    def test_page_breaks_none(self):
        assert chunker._split_by_page_breaks("без разрывов") == ["без разрывов"]

    def test_heading_positions_include_start(self):
        assert chunker._find_heading_positions("просто текст") == [0]

    def test_split_by_headings(self):
        text = "# Первый\nтекст 1\n# Второй\nтекст 2"
        assert chunker._split_by_headings(text) == ["# Первый\nтекст 1", "# Второй\nтекст 2"]

    def test_split_by_numbered_and_named_headings(self):
        text = "Введение\nГлава 1 Начало\nтекст\n1.2 Подраздел\nещё текст"
        sections = chunker._split_by_headings(text)
        assert any(s.startswith("Глава 1") for s in sections)
        assert any(s.startswith("1.2") for s in sections)

    def test_split_by_headings_empty(self):
        assert chunker._split_by_headings("") == []

    def test_protect_tables_keeps_table_block_together(self):
        section = "Вступление\n| a | b |\n|---|---|\n| 1 | 2 |\n\nЗаключение"
        blocks = chunker._protect_tables(section)
        table = next(b for b in blocks if b.startswith("| a |"))
        assert "| 1 | 2 |" in table
        assert blocks[0] == "Вступление"
        assert blocks[-1] == "Заключение"


class TestSplitIntoChunks:
    @pytest.mark.parametrize("text", ["", "   \n\t  ", None])
    def test_empty_text(self, text):
        assert chunker.split_into_chunks_with_meta(text) == []
        assert chunker.split_into_chunks(text) == []

    @pytest.mark.parametrize("strategy", ["universal", "fixed", "markdown", "separators", "semantic", "hierarchical"])
    def test_every_strategy_produces_bounded_chunks(self, strategy):
        text = "# Раздел\n\n" + SENTENCE * 60
        chunks = chunker.split_into_chunks(text, chunk_size=400, chunk_overlap=50, chunking_strategy=strategy)
        assert chunks
        # Мягкий лимит у semantic/universal: допускаем превышение, но не неограниченное.
        assert all(len(c) <= 400 * 3 for c in chunks)

    @pytest.mark.parametrize("strategy", ["universal", "fixed", "markdown", "separators"])
    def test_no_text_lost(self, strategy):
        words = [f"слово{i}" for i in range(400)]
        text = " ".join(words)
        chunks = chunker.split_into_chunks(text, chunk_size=300, chunk_overlap=0, chunking_strategy=strategy)
        joined = " ".join(chunks)
        assert all(w in joined for w in words)

    def test_meta_has_strategy_for_hierarchical(self):
        out = chunker.split_into_chunks_with_meta(
            "# T\n\n" + SENTENCE * 30, chunk_size=300, chunk_overlap=0, chunking_strategy="hierarchical"
        )
        assert out
        assert all(meta["chunking_strategy"] == "hierarchical" for _, meta in out)

    def test_markdown_meta_has_section_heading(self):
        out = chunker.split_into_chunks_with_meta(
            "# Заголовок\n\n" + SENTENCE * 5, chunk_size=1000, chunk_overlap=0, chunking_strategy="markdown"
        )
        assert out[0][1]["section_heading"] == "# Заголовок"

    def test_short_fragments_are_dropped(self):
        out = chunker.split_into_chunks_with_meta("коротко", chunking_strategy="fixed")
        assert out == []

    def test_unknown_strategy_falls_back_to_universal(self):
        text = SENTENCE * 20
        assert chunker.split_into_chunks(text, chunk_size=300, chunk_overlap=0, chunking_strategy="nope") == (
            chunker.split_into_chunks(text, chunk_size=300, chunk_overlap=0, chunking_strategy="universal")
        )

    def test_table_not_split_in_universal(self):
        table = "\n".join(["| колонка A | колонка B |", "|---|---|"] + [f"| значение {i} | данные {i} |" for i in range(6)])
        text = "Описание таблицы ниже.\n\n" + table + "\n\nКонец документа с заключением."
        chunks = chunker.split_into_chunks(text, chunk_size=1500, chunk_overlap=0, chunking_strategy="universal")
        assert any("| значение 0 |" in c and "| значение 5 |" in c for c in chunks)


class TestFilterByMinScore:
    ROWS = [("a", 0.9, 1, 0), ("b", 0.5, 1, 1), ("c", 0.1, 2, 0)]

    def test_zero_threshold_returns_same_list(self):
        assert filter_by_min_score(self.ROWS, 0.0) is self.ROWS

    def test_negative_threshold_returns_same_list(self):
        assert filter_by_min_score(self.ROWS, -1.0) is self.ROWS

    def test_threshold_inclusive(self):
        assert filter_by_min_score(self.ROWS, 0.5) == self.ROWS[:2]

    def test_all_filtered(self):
        assert filter_by_min_score(self.ROWS, 2.0) == []

    def test_custom_score_index(self):
        rows = [("a", 1, 0.2), ("b", 2, 0.8)]
        assert filter_by_min_score(rows, 0.5, score_index=2) == [rows[1]]

    def test_bad_rows_skipped(self):
        rows = [("a", "not-a-number"), ("b",), ("c", None), ("d", 0.7)]
        assert filter_by_min_score(rows, 0.5) == [("d", 0.7)]


class FakeRepo:
    def __init__(self, chunks=None, fail=False):
        self.chunks = chunks or {}
        self.fail = fail
        self.calls = []

    async def get_chunk_contents_by_indices(self, doc_id, indices):
        self.calls.append((doc_id, list(indices)))
        if self.fail:
            raise RuntimeError("db down")
        return {i: self.chunks[(doc_id, i)] for i in indices if (doc_id, i) in self.chunks}


class TestApplyRerankMinAndWindow:
    async def test_threshold_only_after_rerank(self):
        rows = [("a", 0.9, 1, 0), ("b", 0.2, 1, 1)]
        out = await apply_rerank_min_and_window(
            FakeRepo(), rows, rerank_min_score=0.5, sentence_window=0, used_rerank=True
        )
        assert out == [rows[0]]

    async def test_threshold_ignored_without_rerank(self):
        rows = [("a", 0.9, 1, 0), ("b", 0.2, 1, 1)]
        out = await apply_rerank_min_and_window(
            FakeRepo(), rows, rerank_min_score=0.5, sentence_window=0, used_rerank=False
        )
        assert out == rows

    async def test_window_merges_neighbours_in_order(self):
        repo = FakeRepo({(1, 0): "zero", (1, 1): "one", (1, 2): "two"})
        out = await apply_rerank_min_and_window(
            repo, [("one", 0.9, 1, 1)], rerank_min_score=0, sentence_window=1, used_rerank=False
        )
        assert out == [("zero\n---\none\n---\ntwo", 0.9, 1, 1)]
        assert repo.calls == [(1, [0, 1, 2])]

    async def test_window_never_asks_negative_indices(self):
        repo = FakeRepo({(1, 0): "zero", (1, 1): "one"})
        await apply_rerank_min_and_window(
            repo, [("zero", 0.9, 1, 0)], rerank_min_score=0, sentence_window=2, used_rerank=False
        )
        assert repo.calls == [(1, [0, 1, 2])]

    async def test_window_keeps_rag_hit_cosine(self):
        repo = FakeRepo({(1, 0): "a", (1, 1): "b"})
        hit = RagHit("a", 0.9, 1, 0, cosine=0.42)
        out = await apply_rerank_min_and_window(
            repo, [hit], rerank_min_score=0, sentence_window=1, used_rerank=False
        )
        assert isinstance(out[0], RagHit)
        assert out[0][0] == "a\n---\nb"
        assert hit_cosine(out[0]) == pytest.approx(0.42)

    async def test_rows_without_ids_are_kept_untouched(self):
        repo = FakeRepo()
        rows = [("x", 0.9, None, None), ("y", 0.8, 5, None)]
        out = await apply_rerank_min_and_window(
            repo, rows, rerank_min_score=0, sentence_window=1, used_rerank=False
        )
        assert out == rows
        assert repo.calls == []

    async def test_fetch_failure_falls_back_to_original_row(self):
        rows = [("a", 0.9, 1, 0)]
        out = await apply_rerank_min_and_window(
            FakeRepo(fail=True), rows, rerank_min_score=0, sentence_window=1, used_rerank=False
        )
        assert out == rows

    async def test_empty_neighbour_map_keeps_row(self):
        rows = [("a", 0.9, 1, 0)]
        out = await apply_rerank_min_and_window(
            FakeRepo(), rows, rerank_min_score=0, sentence_window=1, used_rerank=False
        )
        assert out == rows

    async def test_repo_without_chunk_api(self):
        rows = [("a", 0.9, 1, 0)]
        out = await apply_rerank_min_and_window(
            object(), rows, rerank_min_score=0, sentence_window=1, used_rerank=False
        )
        assert out == rows

    async def test_empty_rows(self):
        assert await apply_rerank_min_and_window(
            FakeRepo(), [], rerank_min_score=0.5, sentence_window=2, used_rerank=True
        ) == []


class TestRagHit:
    def test_with_content_preserves_everything_else(self):
        hit = RagHit("old", 0.3, 7, 2, cosine=0.9)
        copy = hit.with_content("new")
        assert tuple(copy) == ("new", 0.3, 7, 2)
        assert copy.cosine == pytest.approx(0.9)
        assert tuple(hit) == ("old", 0.3, 7, 2)

    def test_hit_cosine_for_plain_tuple_and_none(self):
        assert hit_cosine(("a", 1.0, 1, 1)) is None
        assert hit_cosine(RagHit("a", 1.0, 1, 1)) is None
