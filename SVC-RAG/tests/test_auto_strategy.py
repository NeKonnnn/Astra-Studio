import os
import unittest
from unittest.mock import patch

from app.services.rag_search_helpers import resolve_auto_pipeline_strategy


class TestAutoStrategy(unittest.TestCase):
    def _resolve(self, query: str, *, graph: bool = True, hybrid: bool = True) -> str:
        with patch.dict(
            os.environ,
            {"RAG_AUTO_MODE": "heuristic", "RAG_AUTO_PREFER_HYBRID": "0"},
        ):
            return resolve_auto_pipeline_strategy(
                query,
                store="kb",
                document_id=None,
                hierarchical_available=False,
                graph_available=graph,
                hybrid_available=hybrid,
            )

    def test_defaults_to_hybrid_for_general_question(self):
        self.assertEqual(self._resolve("Какие условия указаны в договоре?"), "hybrid")

    def test_selects_lexical_for_exact_code(self):
        self.assertEqual(self._resolve("Найди код ZXQ-917"), "lexical")

    def test_selects_lexical_for_quoted_phrase(self):
        self.assertEqual(
            self._resolve("Найди точную фразу «аварийный режим»"), "lexical"
        )

    def test_selects_vector_for_semantic_question(self):
        self.assertEqual(
            self._resolve(
                "Объясни другими словами, о чем говорится в политике отпусков"
            ),
            "vector",
        )

    def test_selects_graph_for_relationship_question(self):
        self.assertEqual(
            self._resolve("Как связаны причины инцидента и его последствия?"),
            "graph",
        )

    def test_selects_graph_for_filename_question(self):
        self.assertEqual(self._resolve("Сделай обзор файла report_2025.pdf"), "graph")

    def test_falls_back_to_vector_when_other_strategies_unavailable(self):
        self.assertEqual(
            self._resolve("Обычный вопрос", graph=False, hybrid=False),
            "vector",
        )


if __name__ == "__main__":
    unittest.main()
