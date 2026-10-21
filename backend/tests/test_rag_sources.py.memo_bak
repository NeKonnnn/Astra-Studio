import unittest

import pytest

try:
    from backend.rag_query.metrics import context_precision, reciprocal_rank
    from backend.realtime.rag_evidence import (
        ActiveRagSources,
        build_reindex_status_message,
        rag_reindex_blocks_active_sources,
        resolve_active_rag_sources,
        should_block_rag_send,
    )
except Exception as e:  # noqa: BLE001
    # Импорт пакета backend тянет рантайм-зависимости (asyncpg и пр.). В окружении
    # без них тест логики источников/метрик не запускаем, а не падаем на сборе.
    pytest.skip(f"backend runtime deps unavailable: {e}", allow_module_level=True)


class TestResolveActiveRagSources(unittest.TestCase):
    def test_memory_only(self):
        s = resolve_active_rag_sources(
            project_id=None,
            use_agent_scoped_kb=False,
            agent_kb_doc_ids=[],
            use_memory_library_rag=True,
        )
        self.assertTrue(s.memory)
        self.assertFalse(s.project)
        self.assertFalse(s.agent_kb)
        self.assertEqual(s.store_list(), ["memory"])

    def test_memory_plus_agent(self):
        s = resolve_active_rag_sources(
            project_id=None,
            use_agent_scoped_kb=True,
            agent_kb_doc_ids=[10, 11],
            use_memory_library_rag=True,
        )
        self.assertTrue(s.memory)
        self.assertTrue(s.agent_kb)
        self.assertFalse(s.project)
        self.assertEqual(set(s.store_list()), {"kb", "memory"})

    def test_project_only_no_leak(self):
        s = resolve_active_rag_sources(
            project_id="proj-1",
            use_agent_scoped_kb=False,
            agent_kb_doc_ids=[],
            use_memory_library_rag=False,
        )
        self.assertTrue(s.project)
        self.assertFalse(s.memory)
        self.assertFalse(s.agent_kb)
        self.assertEqual(s.store_list(), ["project"])

    def test_project_plus_agent_plus_memory(self):
        s = resolve_active_rag_sources(
            project_id="proj-1",
            use_agent_scoped_kb=True,
            agent_kb_doc_ids=[3],
            use_memory_library_rag=True,
        )
        self.assertEqual(set(s.store_list()), {"project", "kb", "memory"})

    def test_kb_rag_flag_enables_memory_search(self):
        # UI-кнопка «Библиотека» может прислать только use_kb_rag — memory всё
        # равно должен искаться (документы «Открыть базу данных» лежат в memory-rag).
        s = resolve_active_rag_sources(
            project_id=None,
            use_agent_scoped_kb=False,
            agent_kb_doc_ids=[],
            use_memory_library_rag=False,
            use_kb_rag=True,
        )
        self.assertTrue(s.memory)
        self.assertEqual(s.store_list(), ["memory"])

    def test_both_library_flags_together(self):
        s = resolve_active_rag_sources(
            project_id=None,
            use_agent_scoped_kb=False,
            agent_kb_doc_ids=[],
            use_memory_library_rag=True,
            use_kb_rag=True,
        )
        self.assertTrue(s.memory)

    def test_agent_kb_requires_doc_ids(self):
        # file_search включён, но документов нет -> agent_kb неактивен
        s = resolve_active_rag_sources(
            project_id=None,
            use_agent_scoped_kb=True,
            agent_kb_doc_ids=[],
            use_memory_library_rag=False,
        )
        self.assertFalse(s.agent_kb)
        self.assertFalse(s.any)
        self.assertEqual(s.store_list(), [])

    def test_nothing_active(self):
        s = resolve_active_rag_sources(
            project_id=None,
            use_agent_scoped_kb=False,
            agent_kb_doc_ids=None,
            use_memory_library_rag=False,
        )
        self.assertFalse(s.any)


class TestShouldBlockRagSend(unittest.TestCase):
    def _status(self, *, memory=False, project=False, kb=False):
        return {
            "memory": {"reindexing": memory},
            "project": {"reindexing": project},
            "kb": {"reindexing": kb},
        }

    def test_memory_blocks_when_library_on(self):
        self.assertTrue(
            should_block_rag_send(
                self._status(memory=True),
                library_enabled=True,
                project_has_documents=False,
                agent_has_kb=False,
            )
        )

    def test_memory_allows_when_library_off(self):
        self.assertFalse(
            should_block_rag_send(
                self._status(memory=True),
                library_enabled=False,
                project_has_documents=False,
                agent_has_kb=False,
            )
        )

    def test_project_blocks_only_with_documents(self):
        self.assertTrue(
            should_block_rag_send(
                self._status(project=True),
                library_enabled=False,
                project_has_documents=True,
                agent_has_kb=False,
            )
        )

    def test_project_allows_without_documents(self):
        self.assertFalse(
            should_block_rag_send(
                self._status(project=True),
                library_enabled=False,
                project_has_documents=False,
                agent_has_kb=False,
            )
        )

    def test_kb_blocks_with_agent_kb(self):
        self.assertTrue(
            should_block_rag_send(
                self._status(kb=True),
                library_enabled=False,
                project_has_documents=False,
                agent_has_kb=True,
            )
        )

    def test_kb_allows_without_agent_kb(self):
        self.assertFalse(
            should_block_rag_send(
                self._status(kb=True),
                library_enabled=False,
                project_has_documents=False,
                agent_has_kb=False,
            )
        )


class TestReindexStatusMessage(unittest.TestCase):
    def test_single_store(self):
        msg = build_reindex_status_message(
            memory_reindexing=True,
            project_reindexing=False,
            kb_reindexing=False,
        )
        self.assertIn("Библиотеки", msg)

    def test_empty_when_idle(self):
        self.assertEqual(
            build_reindex_status_message(
                memory_reindexing=False,
                project_reindexing=False,
                kb_reindexing=False,
            ),
            "",
        )


class TestRagReindexBlocksActiveSources(unittest.IsolatedAsyncioTestCase):
    async def test_blocks_memory_source(self):
        class _Client:
            async def get_reindex_status(self):
                return {
                    "memory": {"reindexing": True},
                    "project": {"reindexing": False},
                    "kb": {"reindexing": False},
                }

        sources = ActiveRagSources(project=False, agent_kb=False, memory=True)
        self.assertTrue(await rag_reindex_blocks_active_sources(sources, _Client()))

    async def test_allows_when_no_active_sources(self):
        class _Client:
            async def get_reindex_status(self):
                return {
                    "memory": {"reindexing": True},
                    "project": {"reindexing": True},
                    "kb": {"reindexing": True},
                }

        sources = ActiveRagSources(project=False, agent_kb=False, memory=False)
        self.assertFalse(await rag_reindex_blocks_active_sources(sources, _Client()))


class TestOnlineMetricPureFns(unittest.TestCase):
    def test_reciprocal_rank(self):
        self.assertEqual(reciprocal_rank([True, False]), 1.0)
        self.assertAlmostEqual(reciprocal_rank([False, False, True]), 1.0 / 3)
        self.assertEqual(reciprocal_rank([False, False]), 0.0)

    def test_context_precision(self):
        self.assertAlmostEqual(context_precision([True, False, True, False]), 0.5)
        self.assertEqual(context_precision([True, True]), 1.0)
        self.assertIsNone(context_precision([]))


if __name__ == "__main__":
    unittest.main()
