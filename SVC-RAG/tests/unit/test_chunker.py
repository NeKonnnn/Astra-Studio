"""services/chunker: нарезка текста на чанки для RAG."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services import chunker

pytestmark = pytest.mark.unit

PARAGRAPH = (
    "Банк выдаёт кредиты физическим лицам на срок до пяти лет. "
    "Ставка зависит от суммы и кредитной истории заёмщика. "
)


def _words(text: str) -> set:
    return set(text.split())


class TestNormalizeStrategy:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, "universal"),
            ("", "universal"),
            ("FIXED", "fixed"),
            ("  markdown ", "markdown"),
            ("structure", "universal"),
            ("structural", "universal"),
            ("default", "universal"),
            ("library", "universal"),
            ("separators", "separators"),
            ("semantic", "semantic"),
            ("hierarchical", "hierarchical"),
            ("nonsense", "universal"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert chunker.normalize_chunking_strategy(raw) == expected


class TestResolveParams:
    def test_defaults_come_from_config(self):
        assert chunker.resolve_chunk_params() == (500, 50)

    def test_explicit_values(self):
        assert chunker.resolve_chunk_params(800, 100) == (800, 100)

    def test_size_has_a_floor(self):
        size, _ = chunker.resolve_chunk_params(10, 0)

        assert size == 200

    def test_negative_overlap_is_zero(self):
        assert chunker.resolve_chunk_params(500, -5) == (500, 0)

    def test_overlap_not_smaller_than_size_is_reset_to_quarter(self):
        assert chunker.resolve_chunk_params(400, 400) == (400, 100)
        assert chunker.resolve_chunk_params(400, 900) == (400, 100)

    def test_string_numbers_are_accepted(self):
        assert chunker.resolve_chunk_params("600", "60") == (600, 60)


class TestDescribeEmbedClient:
    def test_native_client(self):
        assert chunker.describe_embed_client(object()) == "native(svc-rag-models)"

    def test_provider_client(self):
        class Client:
            provider_id = "openai"
            embedding_model = "text-embedding-3"

        assert chunker.describe_embed_client(Client()) == "openai(text-embedding-3)"

    def test_split_client_is_unwrapped(self):
        class Inner:
            provider_id = "vllm"
            embedding_model = None

        class Split:
            embed_client = Inner()

        assert chunker.describe_embed_client(Split()) == "vllm(?)"


class TestHelpers:
    def test_page_breaks(self):
        text = "Первая страница\n--- Страница 2 ---\nВторая страница"

        parts = chunker._split_by_page_breaks(text)

        assert [p.strip() for p in parts] == ["Первая страница", "Вторая страница"]

    def test_headings_positions_start_at_zero(self):
        text = "вступление\n# Заголовок\nтекст"

        assert chunker._find_heading_positions(text)[0] == 0

    def test_split_by_headings(self):
        text = "# Один\nтекст один\n# Два\nтекст два"

        sections = chunker._split_by_headings(text)

        assert len(sections) == 2
        assert sections[0].startswith("# Один")
        assert sections[1].startswith("# Два")

    @pytest.mark.parametrize(
        "heading",
        ["Глава 1 Общие положения", "Раздел 2. Права", "1.2.3 Подраздел", "IV. Итоги"],
    )
    def test_russian_and_numbered_headings_are_recognized(self, heading):
        sections = chunker._split_by_headings(f"вступление\n{heading}\nтекст")

        assert len(sections) == 2

    def test_no_headings_gives_single_section(self):
        assert chunker._split_by_headings("просто текст") == ["просто текст"]

    def test_empty_text_has_no_sections(self):
        assert chunker._split_by_headings("") == []

    def test_tables_are_kept_as_separate_blocks(self):
        section = "до таблицы\n| a | b |\n|---|---|\n| 1 | 2 |\nпосле таблицы"

        blocks = chunker._protect_tables(section)

        assert blocks == [
            "до таблицы",
            "| a | b |\n|---|---|\n| 1 | 2 |",
            "после таблицы",
        ]

    def test_text_without_tables_is_one_block(self):
        assert chunker._protect_tables("раз\nдва") == ["раз\nдва"]


class TestSplitIntoChunks:
    def test_empty_inputs(self):
        assert chunker.split_into_chunks_with_meta("") == []
        assert chunker.split_into_chunks_with_meta("   \n ") == []
        assert chunker.split_into_chunks("") == []

    def test_short_text_is_one_chunk(self):
        chunks = chunker.split_into_chunks_with_meta("Короткий, но осмысленный документ.")

        assert len(chunks) == 1
        assert chunks[0][0] == "Короткий, но осмысленный документ."

    def test_tiny_document_is_not_lost_by_min_length_filter(self):
        assert chunker.split_into_chunks("да") == ["да"]

    def test_universal_metadata(self):
        ((_, meta),) = chunker.split_into_chunks_with_meta(
            "# Заголовок\nТекст раздела документа."
        )

        assert meta == {
            "section_index": 0,
            "section_heading": "# Заголовок",
            "chunk_of_section": 0,
            "is_table": False,
            "chunking_strategy": "universal",
        }

    def test_sections_get_increasing_index(self):
        text = "# Первый\n" + "а " * 50 + "\n# Второй\n" + "б " * 50

        chunks = chunker.split_into_chunks_with_meta(text)

        assert [m["section_index"] for _, m in chunks] == [0, 1]
        assert [m["section_heading"] for _, m in chunks] == ["# Первый", "# Второй"]

    def test_long_text_respects_chunk_size(self):
        text = PARAGRAPH * 60

        chunks = chunker.split_into_chunks(text, chunk_size=400, chunk_overlap=40)

        assert len(chunks) > 3
        assert all(len(c) <= 400 for c in chunks)

    def test_long_section_chunks_are_numbered(self):
        text = "# Раздел\n" + PARAGRAPH * 40

        chunks = chunker.split_into_chunks_with_meta(text, chunk_size=300)
        indices = [m["chunk_of_section"] for _, m in chunks]

        # Нумерация сквозная. Индекс 0 занимает короткий заголовок: сплиттер
        # отделяет его по "\n", а фильтр обрывков (min_useful) его отбрасывает,
        # поэтому первый оставшийся чанк имеет индекс 1.
        assert indices == list(range(1, len(indices) + 1))
        assert all(m["section_heading"] == "# Раздел" for _, m in chunks)
        assert not any(t.startswith("# Раздел") for t, _ in chunks)

    def test_table_stays_whole_and_is_flagged(self):
        table = "\n".join(
            ["| имя | сумма |", "|---|---|"]
            + [f"| клиент {i} | {i * 100} |" for i in range(8)]
        )
        text = "Введение к таблице.\n" + table + "\nКонец документа."

        chunks = chunker.split_into_chunks_with_meta(text, chunk_size=400)

        tables = [(t, m) for t, m in chunks if m["is_table"]]
        assert len(tables) == 1
        assert tables[0][0] == table

    def test_oversized_table_is_still_split(self):
        table = "\n".join(
            ["| a | b |", "|---|---|"]
            + [f"| значение {i} | {'x' * 40} |" for i in range(80)]
        )

        chunks = chunker.split_into_chunks_with_meta(table, chunk_size=300)

        assert len(chunks) > 1
        assert all(m["is_table"] for _, m in chunks)

    def test_page_breaks_separate_sections(self):
        text = (
            "Страница один содержит текст.\n--- Страница 2 ---\n"
            "Страница два тоже содержит текст."
        )

        chunks = chunker.split_into_chunks(text)

        assert len(chunks) == 2

    def test_hierarchical_relabels_strategy(self):
        chunks = chunker.split_into_chunks_with_meta(
            "# А\nтекст раздела номер один.", chunking_strategy="hierarchical"
        )

        assert {m["chunking_strategy"] for _, m in chunks} == {"hierarchical"}

    @pytest.mark.parametrize("strategy", ["fixed", "markdown", "separators", "semantic"])
    def test_every_strategy_produces_labelled_chunks(self, strategy):
        text = ("# Заголовок\n" + PARAGRAPH * 8 + "\n\n") * 5

        chunks = chunker.split_into_chunks_with_meta(
            text, chunk_size=400, chunk_overlap=40, chunking_strategy=strategy
        )

        assert chunks
        assert {m["chunking_strategy"] for _, m in chunks} == {strategy}
        assert all(t.strip() for t, _ in chunks)

    def test_fixed_strategy_respects_size(self):
        chunks = chunker.split_into_chunks(
            PARAGRAPH * 40, chunk_size=300, chunk_overlap=30, chunking_strategy="fixed"
        )

        assert all(len(c) <= 300 for c in chunks)

    def test_semantic_keeps_paragraphs_together_within_soft_limit(self):
        paras = ["первый абзац " * 5, "второй абзац " * 5]
        text = "\n\n".join(p.strip() for p in paras)

        chunks = chunker.split_into_chunks(
            text, chunk_size=400, chunking_strategy="semantic"
        )

        assert len(chunks) == 1
        assert "\n\n" in chunks[0]

    def test_unknown_strategy_behaves_like_universal(self):
        text = "# А\nтекст раздела.\n# Б\nещё текст раздела."

        assert chunker.split_into_chunks(
            text, chunking_strategy="???"
        ) == chunker.split_into_chunks(text)

    def test_overlap_repeats_text_between_neighbours(self):
        text = " ".join(f"слово{i}" for i in range(400))

        chunks = chunker.split_into_chunks(
            text, chunk_size=300, chunk_overlap=100, chunking_strategy="fixed"
        )

        assert _words(chunks[0]) & _words(chunks[1])

    def test_no_overlap_gives_disjoint_neighbours(self):
        text = " ".join(f"слово{i}" for i in range(400))

        chunks = chunker.split_into_chunks(
            text, chunk_size=300, chunk_overlap=0, chunking_strategy="fixed"
        )

        assert not (_words(chunks[0]) & _words(chunks[1]))

    @settings(max_examples=60, deadline=None)
    @given(
        st.lists(
            st.sampled_from(["альфа", "бета", "gamma", "дельта", "7", "x1"]),
            min_size=1,
            max_size=300,
        )
    )
    def test_no_word_is_lost_by_fixed_strategy(self, words):
        text = " ".join(words)

        chunks = chunker.split_into_chunks(
            text, chunk_size=200, chunk_overlap=20, chunking_strategy="fixed"
        )

        assert _words(text) <= _words(" ".join(chunks))
