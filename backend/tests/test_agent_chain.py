# -*- coding: utf-8 -*-
"""Тесты цепочки агентов (без тяжёлого backend.__init__)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load_chain():
    path = _ROOT / "backend" / "agents" / "chain.py"
    spec = importlib.util.spec_from_file_location("agent_chain_ut", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_chain_ut"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestAgentChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = _load_chain()

    def test_parse_agent_ids_filters_self_dupes_and_limit(self):
        raw = [1, "2", 2, "x", 0, -3, 1, *range(10, 30)]
        got = self.chain.parse_agent_ids(raw, exclude_id=1)
        self.assertNotIn(1, got)
        self.assertEqual(got[0], 2)
        self.assertEqual(len(got), self.chain.MAX_CHAIN_AGENTS)
        self.assertEqual(len(set(got)), len(got))

    def test_build_chain_user_message_includes_previous_outputs(self):
        text = self.chain.build_chain_user_message(
            "Суммируй договор",
            [{"agent_name": "Юрист", "content": "Рисков нет"}],
        )
        self.assertIn("Суммируй договор", text)
        self.assertIn("Юрист", text)
        self.assertIn("Рисков нет", text)
        self.assertIn("specific expertise", text)

    def test_format_visible_hides_intermediate(self):
        steps = [
            {"agent_name": "A", "content": "one"},
            {"agent_name": "B", "content": "two"},
        ]
        hidden = self.chain.format_visible_chain_content(
            steps, hide_sequential_outputs=True
        )
        shown = self.chain.format_visible_chain_content(
            steps, hide_sequential_outputs=False
        )
        self.assertEqual(hidden, "two")
        self.assertIn("**▸ A**", shown)
        self.assertIn("**▸ B**", shown)
        self.assertIn("one", shown)
        self.assertIn("two", shown)

    def test_resolve_max_chain_agents_per_agent(self):
        self.assertEqual(
            self.chain.resolve_max_chain_agents(None), self.chain.MAX_CHAIN_AGENTS
        )
        self.assertEqual(
            self.chain.resolve_max_chain_agents({"max_chain_agents": 4}),
            4,
        )
        self.assertEqual(
            self.chain.resolve_max_chain_agents({"max_chain_agents": 999}),
            self.chain.MAX_CHAIN_AGENTS,
        )

    def test_parse_agent_ids_respects_per_agent_limit(self):
        raw = list(range(2, 20))
        got = self.chain.parse_agent_ids(
            raw,
            exclude_id=1,
            agent_profile={"max_chain_agents": 3},
        )
        self.assertEqual(got, [2, 3, 4])

    def test_env_overrides_chain_and_graph_limits(self):
        import os

        prev_agents = os.environ.get("AGENT_CHAIN_MAX_AGENTS")
        prev_steps = os.environ.get("AGENT_GRAPH_STEPS")
        try:
            os.environ["AGENT_CHAIN_MAX_AGENTS"] = "3"
            os.environ["AGENT_GRAPH_STEPS"] = "7"
            self.assertEqual(self.chain.get_max_chain_agents(), 3)
            self.assertEqual(self.chain.get_agent_graph_steps(), 7)
            got = self.chain.parse_agent_ids(list(range(1, 20)), exclude_id=1)
            self.assertEqual(got, [2, 3, 4])
        finally:
            if prev_agents is None:
                os.environ.pop("AGENT_CHAIN_MAX_AGENTS", None)
            else:
                os.environ["AGENT_CHAIN_MAX_AGENTS"] = prev_agents
            if prev_steps is None:
                os.environ.pop("AGENT_GRAPH_STEPS", None)
            else:
                os.environ["AGENT_GRAPH_STEPS"] = prev_steps

    def test_stream_prefix_grows_with_steps(self):
        prefix, header = self.chain.iter_chain_stream_prefixes(
            [], "Аналитик", hide_sequential_outputs=False
        )
        self.assertTrue(prefix.startswith("**▸ Аналитик**"))
        self.assertEqual(header, prefix)
        later, _ = self.chain.iter_chain_stream_prefixes(
            [{"agent_name": "Аналитик", "content": "готово"}],
            "Юрист",
            hide_sequential_outputs=False,
        )
        self.assertIn("готово", later)
        self.assertIn("**▸ Юрист**", later)
        empty, empty_h = self.chain.iter_chain_stream_prefixes(
            [], "X", hide_sequential_outputs=True
        )
        self.assertEqual(empty, "")
        self.assertEqual(empty_h, "")


if __name__ == "__main__":
    unittest.main()
