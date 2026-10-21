# -*- coding: utf-8 -*-
"""Тесты relevance % и context budget (без тяжёлого backend.__init__)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    path = _ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestRelevanceLibreChatStyle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rel = _load("rag_relevance_ut", "backend/rag_query/relevance.py")

    def test_cosine_to_percent(self):
        self.assertEqual(self.rel.score_to_relevance_percent(0.75), 75)
        self.assertEqual(self.rel.score_to_relevance_percent(1.0), 100)
        self.assertEqual(self.rel.score_to_relevance_percent(0.0), 1)

    def test_batch_cosine(self):
        self.assertEqual(self.rel.scores_to_relevance_percents([0.1, 0.5, 0.9]), [10, 50, 90])

    def test_batch_relative_for_rrf(self):
        out = self.rel.scores_to_relevance_percents([0.01, 0.02, 0.03])
        self.assertEqual(out[-1], 100)
        self.assertEqual(out[0], 1)
        self.assertTrue(1 <= out[1] <= 100)


class TestContextBudget(unittest.TestCase):
    def test_default_chars(self):
        budget = _load("rag_ctx_budget_ut", "backend/rag_query/context_budget.py")
        self.assertEqual(budget.rag_context_max_tokens(), 256_000)
        self.assertEqual(budget.rag_context_max_chars(), 256_000 * 4)


class TestMetadataFiltersGone(unittest.TestCase):
    def test_always_none(self):
        mf = _load("rag_meta_gone_ut", "backend/rag_query/metadata_filters.py")
        self.assertIsNone(mf.extract_filters_from_query("бюджет 2024 файл report.pdf"))


if __name__ == "__main__":
    unittest.main()
