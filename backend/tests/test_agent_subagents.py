# -*- coding: utf-8 -*-
"""Тесты лимитов шагов и субагентов (изолированная загрузка модулей)."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

_ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, rel_path: str):
    path = _ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass
class _StubMcpToolInfo:
    server_id: str
    name: str
    qualified_name: str
    description: str
    parameters: Dict[str, Any]
    raw: Dict[str, Any] = field(default_factory=dict)


def _ensure_subagent_import_stubs():
    mcp_types = types.ModuleType("backend.mcp.types")
    mcp_types.McpToolInfo = _StubMcpToolInfo
    sys.modules.setdefault("backend.mcp.types", mcp_types)

    logging_mod = types.ModuleType("backend.settings.logging")

    def _get_logger(_name):
        class _L:
            def debug(self, *a, **k):
                pass

            def exception(self, *a, **k):
                pass

        return _L()

    logging_mod.get_logger = _get_logger
    sys.modules.setdefault("backend.settings.logging", logging_mod)


class TestAgentConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = _load_module("agent_chain_cfg_ut", "backend/agents/chain.py")
        agents_pkg = types.ModuleType("backend.agents")
        agents_pkg.chain = cls.chain
        sys.modules.setdefault("backend.agents", agents_pkg)
        sys.modules["backend.agents.chain"] = cls.chain
        cls.config = _load_module("agent_config_ut", "backend/agents/config.py")
        sys.modules["backend.agents.config"] = cls.config
        _ensure_subagent_import_stubs()
        cls.subagents = _load_module(
            "agent_subagents_ut", "backend/agents/subagents.py"
        )

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
        self.assertIn("agent_5", enum_values)

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


if __name__ == "__main__":
    unittest.main()
