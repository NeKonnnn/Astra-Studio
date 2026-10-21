"""llm_providers/secrets: API-ключи провайдеров только из ENV, без утечек в UI."""

import pytest

from backend.llm_providers import secrets

pytestmark = pytest.mark.unit


class TestEnvName:
    @pytest.mark.parametrize(
        ("provider_id", "expected"),
        [
            ("openai", "LLM_PROVIDER_OPENAI_API_KEY"),
            ("my-corp.gateway", "LLM_PROVIDER_MY_CORP_GATEWAY_API_KEY"),
            ("  spaced id  ", "LLM_PROVIDER_SPACED_ID_API_KEY"),
            ("--x--", "LLM_PROVIDER_X_API_KEY"),
            ("", "LLM_PROVIDER_DEFAULT_API_KEY"),
            ("***", "LLM_PROVIDER_DEFAULT_API_KEY"),
            (None, "LLM_PROVIDER_DEFAULT_API_KEY"),
        ],
    )
    def test_default_name(self, provider_id, expected):
        assert secrets.default_env_name_for_provider(provider_id) == expected


class TestReadApiKey:
    def test_explicit_env_wins(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "  sk-explicit  ")
        monkeypatch.setenv("LLM_PROVIDER_OPENAI_API_KEY", "sk-default")

        key = secrets.read_api_key(
            api_key_env="MY_KEY", provider_id="openai", provider_kind="openai"
        )

        assert key == "sk-explicit"

    @pytest.mark.parametrize("kind", ["openai", "anthropic", "openrouter"])
    def test_external_kinds_fall_back_to_default_name(self, monkeypatch, kind):
        monkeypatch.setenv("LLM_PROVIDER_PROV_API_KEY", "sk-default")

        key = secrets.read_api_key(
            api_key_env=None, provider_id="prov", provider_kind=kind
        )

        assert key == "sk-default"

    @pytest.mark.parametrize("kind", ["vllm", "ollama", "litellm", "llm-svc"])
    def test_local_kinds_do_not_guess_env(self, monkeypatch, kind):
        monkeypatch.setenv("LLM_PROVIDER_PROV_API_KEY", "sk-default")

        key = secrets.read_api_key(
            api_key_env=None, provider_id="prov", provider_kind=kind
        )

        assert key is None

    def test_local_kind_can_still_use_explicit_env(self, monkeypatch):
        monkeypatch.setenv("VLLM_TOKEN", "tok")

        key = secrets.read_api_key(
            api_key_env="VLLM_TOKEN", provider_id="v", provider_kind="vllm"
        )

        assert key == "tok"

    def test_unset_or_empty_returns_none(self, monkeypatch):
        monkeypatch.delenv("NOPE", raising=False)
        monkeypatch.setenv("EMPTY", "")

        for env in ("NOPE", "EMPTY"):
            assert (
                secrets.read_api_key(
                    api_key_env=env, provider_id="p", provider_kind="openai"
                )
                is None
            )

    def test_blank_explicit_env_name_uses_default_for_external(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_P_API_KEY", "k")

        assert (
            secrets.read_api_key(api_key_env="  ", provider_id="p", provider_kind="openai")
            == "k"
        )


class TestMask:
    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_values(self, value):
        assert secrets.mask_api_key(value) is None

    @pytest.mark.parametrize("value", ["short", "12345678", "  1234  "])
    def test_short_keys_fully_hidden(self, value):
        assert secrets.mask_api_key(value) == "***"

    def test_long_key_shows_only_edges(self):
        masked = secrets.mask_api_key("sk-abcdefghijklmnop-wxyz")

        assert masked == "sk-a…wxyz"
        assert "efghijklmnop" not in masked

    def test_masked_value_never_contains_middle_of_key(self):
        key = "A" * 10 + "SECRETMIDDLE" + "B" * 10

        assert "SECRETMIDDLE" not in secrets.mask_api_key(key)


class TestDescribeStatus:
    def test_external_provider_requires_key_and_reports_missing(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER_OPENAI_API_KEY", raising=False)

        status = secrets.describe_secret_status(
            api_key_env=None, provider_id="openai", provider_kind="openai"
        )

        assert status == {
            "api_key_env": "LLM_PROVIDER_OPENAI_API_KEY",
            "api_key_required": True,
            "api_key_set": False,
            "api_key_preview": None,
        }

    def test_reports_set_key_without_leaking_it(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_OPENAI_API_KEY", "sk-0123456789abcdef")

        status = secrets.describe_secret_status(
            api_key_env=None, provider_id="openai", provider_kind="openai"
        )

        assert status["api_key_set"] is True
        assert status["api_key_preview"] == "sk-0…cdef"
        assert "0123456789ab" not in str(status)

    def test_local_provider_without_env_is_optional(self):
        status = secrets.describe_secret_status(
            api_key_env=None, provider_id="v", provider_kind="vllm"
        )

        assert status == {
            "api_key_env": None,
            "api_key_required": False,
            "api_key_set": False,
            "api_key_preview": None,
        }
