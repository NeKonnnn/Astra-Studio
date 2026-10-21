# -*- coding: utf-8 -*-
"""Тесты лимитов шагов и субагентов."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.agents import config as agent_config  # noqa: E402
from backend.agents import subagents  # noqa: E402


class TestAgentConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = agent_config
        cls.subagents = subagents

    def test_resolve_recursion_limit_per_agent(self):
        got = self.config.resolve_recursion_limit({"recursion_limit": 30})
        self.assertEqual(got, 30)

    def test_resolve_recursion_limit_global_fallback(self):
        import os

        prev = os.environ.get("AGENT_GRAPH_STEPS")
        try:
            os.environ["AGENT_GRAPH_STEPS"] = "42"
            got = self.config.resolve_recursion_limit({})
            self.assertEqual(got, 42)
        finally:
            if prev is None:
                os.environ.pop("AGENT_GRAPH_STEPS", None)
            else:
                os.environ["AGENT_GRAPH_STEPS"] = prev

    def test_parse_subagents_config(self):
        cfg = self.subagents.parse_subagents_config(
            {"enabled": True, "allow_self": False, "agent_ids": [2, 2, 3]},
            exclude_id=1,
        )
        self.assertTrue(cfg.enabled)
        self.assertFalse(cfg.allow_self)
        self.assertEqual(cfg.agent_ids, [2, 3])

    def test_resolve_max_subagents_per_agent(self):
        self.assertEqual(
            self.subagents.resolve_max_subagents({"max_subagents": 4}),
            4,
        )
        got = self.subagents.parse_subagents_config(
            {"enabled": True, "allow_self": False, "agent_ids": [2, 3, 4, 5, 6]},
            exclude_id=1,
            agent_profile={"max_subagents": 2},
        )
        self.assertEqual(got.agent_ids, [2, 3])

    def test_build_subagent_tools_includes_self_and_agents(self):
        tools = self.subagents.build_subagent_tools(
            self.subagents.AgentSubagentsConfig(
                enabled=True, allow_self=True, agent_ids=[5]
            ),
            parent_agent_id=1,
            agent_names={1: "Parent", 5: "Helper"},
        )
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "subagent")
        enum_values = tools[0].parameters["properties"]["subagent_type"]["enum"]
        self.assertIn("self", enum_values)
        # В enum — отображаемое имя субагента (или agent_<id>, если имени нет).
        self.assertTrue("Helper" in enum_values or "agent_5" in enum_values)

    def test_resolve_subagent_target(self):
        cfg = self.subagents.AgentSubagentsConfig(
            enabled=True, allow_self=True, agent_ids=[7]
        )
        self.assertEqual(
            self.subagents.resolve_subagent_target(
                "self", parent_agent_id=3, config=cfg
            ),
            3,
        )
        self.assertEqual(
            self.subagents.resolve_subagent_target(
                "agent_7", parent_agent_id=3, config=cfg
            ),
            7,
        )
        self.assertIsNone(
            self.subagents.resolve_subagent_target(
                "agent_99", parent_agent_id=3, config=cfg
            ),
        )

    def test_accepts_parent_shared_rag_default_on(self):
        from backend.agents.shared_rag import accepts_parent_shared_rag

        self.assertTrue(accepts_parent_shared_rag({}))
        self.assertTrue(accepts_parent_shared_rag({"use_parent_shared_rag": True}))
        self.assertFalse(accepts_parent_shared_rag({"use_parent_shared_rag": False}))


if __name__ == "__main__":
    unittest.main()
