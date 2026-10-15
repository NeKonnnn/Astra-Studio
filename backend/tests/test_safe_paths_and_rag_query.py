# -*- coding: utf-8 -*-
"""Тесты backend.utils.safe_paths и чистых функций backend.rag_query (preprocess/postprocess/hit_types)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.rag_query.hit_types import RagHit, hit_cosine  # noqa: E402
from backend.rag_query.metadata_filters import extract_filters_from_query  # noqa: E402
from backend.rag_query.postprocess import _word_jaccard, dedupe_rag_hits  # noqa: E402
from backend.rag_query.preprocess import normalize_query  # noqa: E402
from backend.utils import safe_paths  # noqa: E402


class TestSanitizeLogValue:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("plain", "plain"),
            ("a\nb", "a b"),
            ("a\r\nb", "a  b"),
            ("nul\x00byte", "nul byte"),
            (123, "123"),
            (None, "None"),
        ],
    )
    def test_sanitize(self, value, expected):
        assert safe_paths.sanitize_log_value(value) == expected

    def test_forged_log_line_neutralized(self):
        forged = "user\n2026-01-01 INFO admin logged in"
        assert "\n" not in safe_paths.sanitize_log_value(forged)


class TestResolvePathUnderBase:
    def test_relative_inside_base(self, tmp_path):
        got = safe_paths.resolve_path_under_base("sub/file.txt", tmp_path)
        assert got == (tmp_path / "sub" / "file.txt").resolve()

    def test_absolute_inside_base(self, tmp_path):
        target = tmp_path / "a.log"
        assert safe_paths.resolve_path_under_base(str(target), tmp_path) == target.resolve()

    @pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
    def test_empty_rejected(self, tmp_path, raw):
        assert safe_paths.resolve_path_under_base(raw, tmp_path) is None

    @pytest.mark.parametrize("raw", ["../escape.txt", "a/../../escape.txt", "..", "a/../b"])
    def test_parent_components_rejected(self, tmp_path, raw):
        assert safe_paths.resolve_path_under_base(raw, tmp_path) is None

    @pytest.mark.parametrize("raw, expected", [("./x", "x"), ("a/./b", "a/b")])
    def test_single_dot_is_normalized_by_pathlib(self, tmp_path, raw, expected):
        # pathlib схлопывает «.» ещё до проверки, путь остаётся внутри base.
        got = safe_paths.resolve_path_under_base(raw, tmp_path)
        assert got == (tmp_path / expected).resolve()

    def test_absolute_outside_base_rejected(self, tmp_path):
        other = tmp_path.parent / "outside.txt"
        assert safe_paths.resolve_path_under_base(str(other), tmp_path) is None

    def test_symlink_escape_rejected(self, tmp_path):
        outside = tmp_path.parent / f"{tmp_path.name}_outside"
        outside.mkdir()
        base = tmp_path / "base"
        base.mkdir()
        (base / "link").symlink_to(outside, target_is_directory=True)
        assert safe_paths.resolve_path_under_base("link/secret.txt", base) is None

    def test_create_parent(self, tmp_path):
        got = safe_paths.resolve_path_under_base("deep/er/file.txt", tmp_path, create_parent=True)
        assert got is not None
        assert got.parent.is_dir()

    def test_no_parent_created_by_default(self, tmp_path):
        safe_paths.resolve_path_under_base("deep/er/file.txt", tmp_path)
        assert not (tmp_path / "deep").exists()

    def test_create_parent_failure_returns_none(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file")
        assert safe_paths.resolve_path_under_base("blocker/x/file.txt", tmp_path, create_parent=True) is None


class TestIsWritablePath:
    def test_writable_dir(self, tmp_path):
        assert safe_paths.is_writable_path(tmp_path / "new_dir" / "f.txt") is True
        assert (tmp_path / "new_dir").is_dir()

    def test_parent_is_a_file(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        assert safe_paths.is_writable_path(blocker / "child.txt") is False


class TestResolveDirFromEnv:
    def test_env_inside_base_is_used(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOME_DIR", "custom")
        default = tmp_path / "default"
        got = safe_paths._resolve_dir_from_env("SOME_DIR", default, tmp_path)
        assert got == (tmp_path / "custom").resolve()

    def test_env_outside_base_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOME_DIR", "../evil")
        default = tmp_path / "default"
        got = safe_paths._resolve_dir_from_env("SOME_DIR", default, tmp_path)
        assert got == default
        assert default.is_dir()

    def test_env_unset_uses_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SOME_DIR", raising=False)
        default = tmp_path / "default"
        assert safe_paths._resolve_dir_from_env("SOME_DIR", default, tmp_path) == default


class TestNormalizeQuery:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("", ""),
            (None, ""),
            ("  hello  ", "hello"),
            ("a    b\t\tc", "a b c"),
            ("a\n\n\n\nb", "a\n\nb"),
            ("a\n\nb", "a\n\nb"),
        ],
    )
    def test_whitespace(self, raw, expected):
        assert normalize_query(raw) == expected

    def test_nfc_normalization(self):
        decomposed = "ё"  # «ё» как е + диакритика
        assert normalize_query(decomposed) == "ё"


def test_metadata_filters_are_noop():
    assert extract_filters_from_query("документы 2024 файл report.pdf") is None


class TestWordJaccard:
    def test_identical(self):
        assert _word_jaccard("кот пёс рыба", "кот пёс рыба") == 1.0

    def test_disjoint(self):
        assert _word_jaccard("one two", "three four") == 0.0

    def test_both_empty(self):
        assert _word_jaccard("", "") == 1.0

    def test_one_empty(self):
        assert _word_jaccard("word", "") == 0.0

    def test_partial(self):
        assert _word_jaccard("a b c d", "a b c e") == pytest.approx(3 / 5)


LONG = "Это достаточно длинный фрагмент текста для проверки дедупликации хитов в выдаче RAG."


class TestDedupeRagHits:
    def test_empty(self):
        assert dedupe_rag_hits([]) == []

    def test_same_doc_and_chunk_removed(self):
        hits = [("a", 0.9, 1, 0), ("b", 0.8, 1, 0), ("c", 0.7, 1, 1)]
        assert dedupe_rag_hits(hits) == [("a", 0.9, 1, 0), ("c", 0.7, 1, 1)]

    def test_none_keys_never_deduped_by_key(self):
        hits = [("a", 0.9, None, None), ("b", 0.8, None, None)]
        assert dedupe_rag_hits(hits) == hits

    def test_near_duplicate_text_removed(self):
        hits = [(LONG, 0.9, 1, 0), (LONG + " ", 0.8, 2, 0)]
        assert dedupe_rag_hits(hits) == [hits[0]]

    def test_different_long_text_kept(self):
        other = "Совсем другой текст про погоду, астрономию и устройство подводных лодок в двадцатом веке."
        hits = [(LONG, 0.9, 1, 0), (other, 0.8, 2, 0)]
        assert dedupe_rag_hits(hits) == hits

    def test_short_text_not_compared_by_jaccard(self):
        hits = [("same", 0.9, 1, 0), ("same", 0.8, 2, 0)]
        assert dedupe_rag_hits(hits) == hits

    def test_threshold_override(self):
        a = "альфа бета гамма дельта эпсилон дзета эта тета йота каппа лямбда мю ню кси омикрон"
        b = "альфа бета гамма дельта эпсилон дзета эта тета йота каппа лямбда мю ню кси пи"
        hits = [(a, 0.9, 1, 0), (b, 0.8, 2, 0)]
        assert len(dedupe_rag_hits(hits, jaccard_threshold=0.99)) == 2
        assert len(dedupe_rag_hits(hits, jaccard_threshold=0.5)) == 1

    def test_max_hits(self):
        hits = [(f"t{i}", 0.5, i, 0) for i in range(10)]
        assert len(dedupe_rag_hits(hits, max_hits=3)) == 3

    def test_keeps_original_objects_with_cosine(self):
        hit = RagHit("x", 0.5, 1, 0, cosine=0.77)
        out = dedupe_rag_hits([hit])
        assert out[0] is hit
        assert hit_cosine(out[0]) == pytest.approx(0.77)


class TestRagHit:
    def test_unpacks_as_four_tuple(self):
        content, score, doc_id, chunk = RagHit("c", 0.1, 7, 3, cosine=0.5)
        assert (content, score, doc_id, chunk) == ("c", 0.1, 7, 3)

    def test_len_is_four(self):
        assert len(RagHit("c", 0.1, 1, 1)) == 4

    def test_cosine_coerced_to_float(self):
        assert RagHit("c", 0.1, 1, 1, cosine=1).cosine == 1.0

    def test_cosine_none(self):
        assert RagHit("c", 0.1, 1, 1).cosine is None
        assert hit_cosine(RagHit("c", 0.1, 1, 1)) is None

    def test_plain_tuple_has_no_cosine(self):
        assert hit_cosine(("c", 0.1, 1, 1)) is None
