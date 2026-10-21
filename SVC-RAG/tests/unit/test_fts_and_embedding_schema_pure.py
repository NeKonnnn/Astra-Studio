"""database/fts и database/embedding_schema: чистые функции без базы данных."""

import re

import pytest

from app.database import embedding_schema as es
from app.database import fts

pytestmark = pytest.mark.unit


class TestBuildFtsOrQuery:
    def test_joins_lexemes_with_or_lowercased_and_deduplicated(self):
        assert fts.build_fts_or_query("Константин документ КОНСТАНТИН") == "константин | документ"

    def test_drops_stopwords_and_one_letter_tokens(self):
        assert fts.build_fts_or_query("и в на что x") is None
        assert fts.build_fts_or_query("The list of the documents") == "list | documents"

    @pytest.mark.parametrize("text", ["", "   ", "!!! ??? ...", None])
    def test_no_content_gives_none(self, text):
        assert fts.build_fts_or_query(text) is None

    def test_tsquery_metacharacters_never_survive(self):
        out = fts.build_fts_or_query("ab&cd | !ef (gh) ij:* 'kl' \\ mn<->op")
        assert out == "ab | cd | ef | gh | ij | kl | mn | op"
        assert not re.search(r"[&!():*'\\<>]", out.replace(" | ", " "))

    def test_underscore_and_digits_are_kept(self):
        assert fts.build_fts_or_query("44-ФЗ v3_2") == "44 | фз | v3_2"

    def test_cyrillic_yo_is_tokenised(self):
        assert fts.build_fts_or_query("Пётр ёлка") == "пётр | ёлка"


class TestSearchableContent:
    @pytest.mark.parametrize(
        "text,expected",
        [("слово", True), ("a", True), ("5", True), ("", False), (None, False), ("?!", False), (" - ", False)],
    )
    def test_cases(self, text, expected):
        assert fts.query_has_searchable_content(text) is expected


class TestProperNouns:
    def test_names_abbreviations_and_codes(self):
        got = fts.extract_proper_nouns("Что говорится про Константина и НДС в 2025 году, код 12345?")
        assert got == ["константина", "ндс", "2025", "12345"]

    def test_question_words_are_not_entities(self):
        assert fts.extract_proper_nouns("Выведи Покажи Какой Список") == []

    def test_deduplicates_and_lowercases(self):
        assert fts.extract_proper_nouns("Лев и Лев и ЛЕВ") == ["лев"]

    def test_empty(self):
        assert fts.extract_proper_nouns("") == []
        assert fts.extract_proper_nouns(None) == []

    def test_entity_query_is_or_of_nouns(self):
        assert fts.build_entity_or_query("Расскажи про Бортякова и ОФЗ") == "бортякова | офз"
        assert fts.build_entity_or_query("расскажи про это") is None

    def test_sentence_initial_word_is_a_false_positive_known_issue(self):
        """Известная особенность: слово с заглавной в начале предложения считается
        именем собственным (исключены только глаголы-команды из списка)."""
        assert fts.extract_proper_nouns("Привет, мир") == ["привет"]


class TestExtractFilenames:
    def test_plain_names(self):
        assert fts.extract_filenames("саммари по report.pdf и Воронин_Михаил.docx") == [
            "report.pdf",
            "Воронин_Михаил.docx",
        ]

    def test_quoted_name_with_spaces_also_yields_its_tail_known_issue(self):
        """Известная особенность: имя в кавычках находится целиком, но «безкавычечный»
        шаблон дополнительно вытаскивает хвост без пробелов (``2024.pdf``), и по
        нему тоже ищется документ (``ILIKE %2024.pdf%``)."""
        assert fts.extract_filenames("Найди «Годовой отчёт 2024.pdf» пожалуйста") == [
            "Годовой отчёт 2024.pdf",
            "2024.pdf",
        ]

    def test_download_suffix(self):
        assert fts.extract_filenames("открой CV_Nekrasov (1).pdf сейчас") == ["CV_Nekrasov (1).pdf"]

    def test_trailing_punctuation_is_removed(self):
        assert fts.extract_filenames("Что в data.xlsx?") == ["data.xlsx"]

    def test_deduplicates_case_insensitively(self):
        assert fts.extract_filenames('"a.txt" и A.TXT и a.txt') == ["a.txt"]

    def test_unknown_extension_and_plain_text_ignored(self):
        assert fts.extract_filenames("файл run.exe и версия 1.2.3") == []
        assert fts.extract_filenames("") == []
        assert fts.extract_filenames(None) == []

    def test_long_input_does_not_hang(self):
        # защита от катастрофического бэктрекинга на длинном «имени»
        assert isinstance(fts.extract_filenames("a" * 5000 + ".pdf"), list)
        assert isinstance(fts.extract_filenames("a." * 2000 + "pdf"), list)


class TestSqlFragments:
    def test_fts_fragment_uses_both_configs_and_placeholders(self):
        where, rank, used = fts.fts_where_and_rank(vectors_alias="v", first_placeholder_idx=3)
        assert used == 2
        assert "v.content_tsv_ru @@ to_tsquery('russian'::regconfig, $3)" in where
        assert "v.content_tsv_simple @@ to_tsquery('simple'::regconfig, $4)" in where
        assert rank.startswith("GREATEST(") and "$3" in rank and "$4" in rank

    def test_substring_fragment_wraps_tokens_and_counts_matches(self):
        where, rank, used, params = fts.substring_where_and_rank(
            vectors_alias="v", tokens=["abc", "x_y"], first_placeholder_idx=2
        )
        assert used == 2 and params == ["%abc%", "%x_y%"]
        assert where == "(v.content ILIKE $2 OR v.content ILIKE $3)"
        assert rank.endswith("::float") and rank.count("CASE WHEN") == 2

    def test_substring_without_tokens_matches_nothing(self):
        assert fts.substring_where_and_rank(
            vectors_alias="v", tokens=[], first_placeholder_idx=1
        ) == ("FALSE", "0", 0, [])

    @pytest.mark.parametrize("alias", ["", "v; DROP TABLE x", "1v", "v.x", "v v", "я"])
    def test_unsafe_alias_rejected(self, alias):
        with pytest.raises(ValueError):
            fts.fts_where_and_rank(vectors_alias=alias, first_placeholder_idx=1)
        with pytest.raises(ValueError):
            fts.substring_where_and_rank(vectors_alias=alias, tokens=["a"], first_placeholder_idx=1)

    def test_substring_token_metacharacters_are_parameters_not_sql(self):
        where, _, _, params = fts.substring_where_and_rank(
            vectors_alias="v", tokens=["'; DROP TABLE x; --"], first_placeholder_idx=1
        )
        assert "DROP" not in where and params == ["%'; DROP TABLE x; --%"]


class TestEmbeddingSchemaPure:
    @pytest.mark.parametrize(
        "dim,col,cast",
        [
            (1, "vector(1)", "vector"),
            (384, "vector(384)", "vector"),
            (2000, "vector(2000)", "vector"),
            (2001, "halfvec(2001)", "halfvec"),
            (4000, "halfvec(4000)", "halfvec"),
            (4001, "vector(4001)", "vector"),
            (8192, "vector(8192)", "vector"),
        ],
    )
    def test_type_boundaries(self, dim, col, cast):
        assert es.vector_column_type(dim) == col
        assert es.vector_cast(dim) == cast

    def test_string_dimension_is_accepted(self):
        assert es.vector_column_type("768") == "vector(768)"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("hnsw", "hnsw"),
            ("HNSW", "hnsw"),
            ("ivfflat", "ivfflat"),
            ("IVF-Flat", "ivfflat"),
            ("ivf_flat", "ivfflat"),
            ("ivf", "ivfflat"),
            ("garbage", "hnsw"),
            ("  ", "hnsw"),
        ],
    )
    def test_resolve_index_method(self, raw, expected):
        assert es.resolve_index_method(raw) == expected

    def test_index_names(self):
        assert es.index_name_for("kb_vectors", "hnsw") == "idx_kb_vectors_embedding_hnsw"
        assert es.index_name_for("memory_rag_vectors", "hnsw") == "idx_memory_rag_vectors_embedding_hnsw"
        assert es.index_name_for("project_rag_vectors", "hnsw") == "idx_proj_rag_vectors_embedding_hnsw"
        assert es.index_name_for("kb_vectors_768", "hnsw") == "idx_kb_vectors_768_embedding_hnsw"
        assert es.index_name_for("kb_vectors", "ivfflat") == "idx_kb_vectors_embedding_ivfflat"

    def test_index_params_have_safe_minimums(self):
        params = es._index_params()
        assert params["ivfflat_lists"] >= 1 and params["ivfflat_probes"] >= 1
        assert params["hnsw_m"] >= 2 and params["hnsw_ef_construction"] >= 4
        assert params["hnsw_ef_search"] >= 1
