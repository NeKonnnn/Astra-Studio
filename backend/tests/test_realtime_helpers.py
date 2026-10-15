# -*- coding: utf-8 -*-
"""Тесты чистых функций backend.realtime.helpers: настройки артефактов, MCP и плагины агента."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.realtime import helpers  # noqa: E402


class TestMergeChatArtifactsSettings:
    def test_override_applied(self):
        out = helpers.merge_chat_artifacts_settings(
            {"artifacts_enabled": False, "name": "A"},
            {"artifacts_settings": {"artifacts_enabled": True, "shadcn_enabled": 1, "user_prompt_mode": 0}},
        )
        assert out == {"artifacts_enabled": True, "name": "A", "shadcn_enabled": True, "user_prompt_mode": False}

    def test_only_known_keys_are_taken(self):
        out = helpers.merge_chat_artifacts_settings({}, {"artifacts_settings": {"evil": 1, "shadcn_enabled": True}})
        assert out == {"shadcn_enabled": True}

    def test_missing_keys_keep_profile_values(self):
        out = helpers.merge_chat_artifacts_settings({"shadcn_enabled": True}, {"artifacts_settings": {}})
        assert out == {"shadcn_enabled": True}

    def test_original_profile_not_mutated(self):
        profile = {"artifacts_enabled": False}
        helpers.merge_chat_artifacts_settings(profile, {"artifacts_settings": {"artifacts_enabled": True}})
        assert profile == {"artifacts_enabled": False}

    @pytest.mark.parametrize("data", [{}, {"artifacts_settings": None}, {"artifacts_settings": "x"}, {"artifacts_settings": []}, None, "str"])
    def test_without_valid_override_profile_returned_as_is(self, data):
        profile = {"a": 1}
        assert helpers.merge_chat_artifacts_settings(profile, data) is profile

    def test_non_dict_profile_becomes_new_dict(self):
        out = helpers.merge_chat_artifacts_settings(None, {"artifacts_settings": {"artifacts_enabled": True}})
        assert out == {"artifacts_enabled": True}


class TestAgentMcpToolIds:
    def test_basic(self):
        assert helpers.agent_mcp_tool_ids({"mcp_enabled": True, "mcp_server_ids": ["a", "b"]}) == [
            "server:mcp:a",
            "server:mcp:b",
        ]

    def test_disabled(self):
        assert helpers.agent_mcp_tool_ids({"mcp_enabled": False, "mcp_server_ids": ["a"]}) == []
        assert helpers.agent_mcp_tool_ids({"mcp_server_ids": ["a"]}) == []

    def test_cleaning_and_dedup(self):
        profile = {"mcp_enabled": True, "mcp_server_ids": [" a ", "a", "", "  ", 5, "b"]}
        assert helpers.agent_mcp_tool_ids(profile) == ["server:mcp:a", "server:mcp:5", "server:mcp:b"]

    @pytest.mark.parametrize("profile", [None, "x", [], {"mcp_enabled": True}, {"mcp_enabled": True, "mcp_server_ids": "a"}])
    def test_invalid_input(self, profile):
        assert helpers.agent_mcp_tool_ids(profile) == []


class TestAgentPluginIds:
    def test_enabled_flag_missing_infers_from_ids(self):
        assert helpers.agent_plugin_ids({"plugin_ids": ["p1", " p2 "]}) == ["p1", "p2"]

    def test_explicitly_disabled(self):
        assert helpers.agent_plugin_ids({"plugins_enabled": False, "plugin_ids": ["p1"]}) == []

    def test_explicitly_enabled(self):
        assert helpers.agent_plugin_ids({"plugins_enabled": True, "plugin_ids": ["p1"]}) == ["p1"]

    def test_empty_ids_infer_disabled(self):
        assert helpers.agent_plugin_ids({"plugin_ids": []}) == []
        assert helpers.agent_plugin_ids({"plugin_ids": ["", "  "]}) == []

    def test_non_string_ids_stringified(self):
        assert helpers.agent_plugin_ids({"plugin_ids": [1, "x"]}) == ["1", "x"]

    def test_duplicates_are_kept(self):
        # Контракт текущей реализации: дедупликации нет (в отличие от MCP).
        assert helpers.agent_plugin_ids({"plugin_ids": ["p", "p"]}) == ["p", "p"]

    @pytest.mark.parametrize("profile", [None, "x", [], {}, {"plugin_ids": "p"}])
    def test_invalid_input(self, profile):
        assert helpers.agent_plugin_ids(profile) == []
