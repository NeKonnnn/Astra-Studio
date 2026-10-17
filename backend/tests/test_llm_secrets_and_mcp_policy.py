# -*- coding: utf-8 -*-
"""Тесты backend.llm_providers.secrets, backend.mcp.policy, backend.mcp.text_tool_calls."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.llm_providers import secrets  # noqa: E402
from backend.mcp import policy  # noqa: E402
from backend.mcp import text_tool_calls as ttc  # noqa: E402


class TestDefaultEnvName:
    @pytest.mark.parametrize(
        "provider_id, expected",
        [
            ("openai", "LLM_PROVIDER_OPENAI_API_KEY"),
            ("my-provider.v2", "LLM_PROVIDER_MY_PROVIDER_V2_API_KEY"),
            ("  spaced  ", "LLM_PROVIDER_SPACED_API_KEY"),
            ("", "LLM_PROVIDER_DEFAULT_API_KEY"),
            ("---", "LLM_PROVIDER_DEFAULT_API_KEY"),
            (None, "LLM_PROVIDER_DEFAULT_API_KEY"),
        ],
    )
    def test_names(self, provider_id, expected):
        assert secrets.default_env_name_for_provider(provider_id) == expected


class TestReadApiKey:
    def test_explicit_env(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "  secret-value  ")
        got = secrets.read_api_key(api_key_env="MY_KEY", provider_id="x", provider_kind="vllm")
        assert got == "secret-value"

    def test_fallback_for_external_kind(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_ACME_API_KEY", "k")
        got = secrets.read_api_key(api_key_env=None, provider_id="acme", provider_kind="openai")
        assert got == "k"

    def test_no_fallback_for_local_kind(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_ACME_API_KEY", "k")
        got = secrets.read_api_key(api_key_env=None, provider_id="acme", provider_kind="vllm")
        assert got is None

    def test_unset_or_empty_env(self, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        assert secrets.read_api_key(api_key_env="MISSING_KEY", provider_id="x", provider_kind="openai") is None
        monkeypatch.setenv("EMPTY_KEY", "")
        assert secrets.read_api_key(api_key_env="EMPTY_KEY", provider_id="x", provider_kind="openai") is None


class TestMaskApiKey:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, None),
            ("", None),
            ("short", "***"),
            ("12345678", "***"),
            ("sk-abcdefghijklmnop", "sk-a…mnop"),
            ("  sk-abcdefghijklmnop  ", "sk-a…mnop"),
        ],
    )
    def test_mask(self, value, expected):
        assert secrets.mask_api_key(value) == expected

    def test_mask_never_reveals_middle(self):
        masked = secrets.mask_api_key("sk-SECRETMIDDLEPART-tail")
        assert "SECRETMIDDLEPART" not in masked


class TestDescribeSecretStatus:
    def test_external_provider_set(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_OPENAI_API_KEY", "sk-1234567890abcdef")
        status = secrets.describe_secret_status(
            api_key_env=None, provider_id="openai", provider_kind="openai"
        )
        assert status == {
            "api_key_env": "LLM_PROVIDER_OPENAI_API_KEY",
            "api_key_required": True,
            "api_key_set": True,
            "api_key_preview": "sk-1…cdef",
        }

    def test_external_provider_missing(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER_OPENAI_API_KEY", raising=False)
        status = secrets.describe_secret_status(
            api_key_env=None, provider_id="openai", provider_kind="openai"
        )
        assert status["api_key_set"] is False
        assert status["api_key_preview"] is None
        assert status["api_key_required"] is True

    def test_local_provider_without_env(self):
        status = secrets.describe_secret_status(
            api_key_env=None, provider_id="local", provider_kind="vllm"
        )
        assert status == {
            "api_key_env": None,
            "api_key_required": False,
            "api_key_set": False,
            "api_key_preview": None,
        }


def _fake_settings(allowlist):
    return SimpleNamespace(mcp=SimpleNamespace(llm_provider_allowlist=allowlist))


class TestMcpPolicy:
    @pytest.mark.parametrize(
        "model_path, expected",
        [
            (None, ""),
            ("", ""),
            ("   ", ""),
            ("openai", "openai"),
            ("openai/gpt-4o", "openai"),
            (" vllm /qwen/3 ", "vllm"),
        ],
    )
    def test_extract_provider_id(self, model_path, expected):
        assert policy.extract_provider_id(model_path) == expected

    def test_allowlist_normalized(self, monkeypatch):
        monkeypatch.setattr(policy, "get_settings", lambda: _fake_settings([" a ", "", "b"]))
        assert policy.get_llm_provider_allowlist() == ["a", "b"]

    @pytest.mark.parametrize("allowlist", [None, [], ()])
    def test_allowlist_empty_means_none(self, monkeypatch, allowlist):
        monkeypatch.setattr(policy, "get_settings", lambda: _fake_settings(allowlist))
        assert policy.get_llm_provider_allowlist() is None

    def test_everything_allowed_without_allowlist(self, monkeypatch):
        monkeypatch.setattr(policy, "get_settings", lambda: _fake_settings(None))
        assert policy.is_mcp_provider_allowed("anything/model") is True

    def test_allowlist_filters(self, monkeypatch):
        monkeypatch.setattr(policy, "get_settings", lambda: _fake_settings(["openai", "vllm"]))
        assert policy.is_mcp_provider_allowed("openai/gpt") is True
        assert policy.is_mcp_provider_allowed("anthropic/claude") is False

    def test_empty_model_path_is_allowed(self, monkeypatch):
        monkeypatch.setattr(policy, "get_settings", lambda: _fake_settings(["openai"]))
        assert policy.is_mcp_provider_allowed(None) is True
        assert policy.is_mcp_provider_allowed("") is True


class TestContentHasTextToolCalls:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("", False),
            (None, False),
            ("обычный ответ", False),
            ("<tool_call>{}</tool_call>", True),
            ("<TOOL_CALL>x", True),
            ("<function=search>", True),
        ],
    )
    def test_detect(self, text, expected):
        assert ttc.content_has_text_tool_calls(text) is expected


class TestExtractToolCalls:
    def test_empty(self):
        assert ttc.extract_tool_calls_from_content("") == ("", [])

    def test_no_calls_keeps_text(self):
        cleaned, calls = ttc.extract_tool_calls_from_content("  просто текст  ")
        assert cleaned == "просто текст"
        assert calls == []

    def test_json_in_tool_call_block(self):
        text = 'До <tool_call>{"name": "search", "arguments": {"q": "кот"}}</tool_call> после'
        cleaned, calls = ttc.extract_tool_calls_from_content(text)
        assert len(calls) == 1
        assert calls[0].name == "search"
        assert calls[0].arguments == {"q": "кот"}
        assert calls[0].id.startswith("call_")
        assert "tool_call" not in cleaned
        assert "До" in cleaned and "после" in cleaned

    def test_json_alt_keys_function_and_parameters(self):
        text = '<tool_call>{"function": "calc", "parameters": {"x": 1}}</tool_call>'
        _, calls = ttc.extract_tool_calls_from_content(text)
        assert [(c.name, c.arguments) for c in calls] == [("calc", {"x": 1})]

    def test_invalid_json_block_is_dropped_without_calls(self):
        cleaned, calls = ttc.extract_tool_calls_from_content("<tool_call>{broken</tool_call>ok")
        assert calls == []
        assert cleaned == "ok"

    def test_non_dict_arguments_become_empty(self):
        text = '<tool_call>{"name": "f", "arguments": [1, 2]}</tool_call>'
        _, calls = ttc.extract_tool_calls_from_content(text)
        assert calls[0].arguments == {}

    def test_hermes_function_tag_inside_block(self):
        text = '<tool_call><function=lookup>{"id": 7}</function></tool_call>'
        _, calls = ttc.extract_tool_calls_from_content(text)
        assert [(c.name, c.arguments) for c in calls] == [("lookup", {"id": 7})]

    def test_bare_function_tag_without_block(self):
        text = 'Сейчас вызову <function=weather>{"city": "Омск"}</function> и отвечу'
        cleaned, calls = ttc.extract_tool_calls_from_content(text)
        assert [(c.name, c.arguments) for c in calls] == [("weather", {"city": "Омск"})]
        assert "<function" not in cleaned
        assert "Сейчас вызову" in cleaned

    def test_self_closing_function_tag(self):
        cleaned, calls = ttc.extract_tool_calls_from_content("<function=ping/> готово")
        assert [(c.name, c.arguments) for c in calls] == [("ping", {})]
        assert "<function" not in cleaned

    def test_duplicate_names_collapsed(self):
        text = (
            '<tool_call>{"name": "a", "arguments": {}}</tool_call>'
            '<tool_call>{"name": "a", "arguments": {"x": 1}}</tool_call>'
        )
        _, calls = ttc.extract_tool_calls_from_content(text)
        assert [c.name for c in calls] == ["a"]

    def test_multiple_distinct_calls_keep_order(self):
        text = (
            '<tool_call>{"name": "first", "arguments": {}}</tool_call>'
            '<tool_call>{"name": "second", "arguments": {}}</tool_call>'
        )
        _, calls = ttc.extract_tool_calls_from_content(text)
        assert [c.name for c in calls] == ["first", "second"]
        assert len({c.id for c in calls}) == 2

    def test_non_json_function_body_gives_empty_args(self):
        _, calls = ttc.extract_tool_calls_from_content("<function=f>plain text</function>")
        assert calls[0].arguments == {}
