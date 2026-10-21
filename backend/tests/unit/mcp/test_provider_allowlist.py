"""mcp/chat_integration: allowlist LLM-провайдеров (B-29) и контекст вызова из пользователя."""

from types import SimpleNamespace

import pytest

from backend.mcp import chat_integration, policy

pytestmark = pytest.mark.unit


@pytest.fixture
def allowlist(monkeypatch):
    def _set(value):
        monkeypatch.setattr(
            chat_integration,
            "get_settings",
            lambda: SimpleNamespace(mcp=SimpleNamespace(llm_provider_allowlist=value)),
        )

    return _set


class TestExtractProviderId:
    @pytest.mark.parametrize(
        ("model_path", "expected"),
        [
            (None, ""),
            ("", ""),
            ("   ", ""),
            ("openai/gpt-4o", "openai"),
            ("openai", "openai"),
            ("llm-svc://vllm/qwen", "vllm"),
            ("LLM-SVC://vllm/qwen", "vllm"),
            ("llm-svc:///vllm/qwen", "vllm"),
            ("llm-svc://qwen", ""),
            (" anthropic/claude ", "anthropic"),
        ],
    )
    def test_extract(self, model_path, expected):
        assert chat_integration._extract_provider_id(model_path) == expected


class TestProviderAllowlist:
    @pytest.mark.parametrize("value", [None, [], ["", "  "]])
    def test_empty_allowlist_allows_everything(self, allowlist, value):
        allowlist(value)

        assert chat_integration.is_mcp_provider_allowed("anything/model") is True

    def test_listed_provider_is_allowed(self, allowlist):
        allowlist(["openai", "vllm"])

        assert chat_integration.is_mcp_provider_allowed("openai/gpt-4o") is True
        assert chat_integration.is_mcp_provider_allowed("llm-svc://vllm/qwen") is True

    def test_unlisted_provider_is_blocked(self, allowlist):
        allowlist(["openai"])

        assert chat_integration.is_mcp_provider_allowed("anthropic/claude") is False

    def test_entries_are_trimmed(self, allowlist):
        allowlist(["  openai  "])

        assert chat_integration.is_mcp_provider_allowed("openai/x") is True

    @pytest.mark.parametrize("model_path", [None, "", "llm-svc://nomodel"])
    def test_unknown_provider_is_not_blocked(self, allowlist, model_path):
        allowlist(["openai"])

        assert chat_integration.is_mcp_provider_allowed(model_path) is True

    def test_match_is_case_sensitive(self, allowlist):
        allowlist(["openai"])

        assert chat_integration.is_mcp_provider_allowed("OpenAI/gpt") is False

    def test_policy_module_reexports_same_function(self):
        assert policy.is_mcp_provider_allowed is chat_integration.is_mcp_provider_allowed


class TestBuildContext:
    def test_maps_user_fields(self):
        ctx = chat_integration.build_mcp_context_from_user(
            {
                "user_id": "u1",
                "username": "alice",
                "is_admin": True,
                "groups": ["g1"],
                "ldap_groups": ["g2"],
            },
            chat_id="c1",
            message_id="m1",
        )

        assert (ctx.user_id, ctx.username, ctx.chat_id, ctx.message_id) == (
            "u1",
            "alice",
            "c1",
            "m1",
        )
        assert ctx.is_admin is True
        assert ctx.groups == ["g1"]
        assert ctx.ldap_groups == ["g2"]

    def test_user_id_falls_back_to_username(self):
        ctx = chat_integration.build_mcp_context_from_user({"username": "alice"})

        assert ctx.user_id == "alice"
        assert ctx.is_admin is False

    def test_groups_fall_back_to_each_other(self):
        only_ldap = chat_integration.build_mcp_context_from_user(
            {"username": "a", "ldap_groups": ["x"]}
        )
        only_groups = chat_integration.build_mcp_context_from_user(
            {"username": "a", "groups": ["y"]}
        )

        assert only_ldap.groups == ["x"]
        assert only_groups.ldap_groups == ["y"]

    def test_empty_user(self):
        ctx = chat_integration.build_mcp_context_from_user({})

        assert (ctx.user_id, ctx.username, ctx.groups) == ("", "", [])
