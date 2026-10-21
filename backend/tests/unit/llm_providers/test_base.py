"""llm_providers/base: разбор путей моделей, конфиг провайдера, мелкие структуры."""

import pytest
from pydantic import ValidationError

from backend.llm_providers.base import (
    LLMProviderConfig,
    ModelInfo,
    ProviderCapabilities,
    ProviderHealth,
    join_model_path,
    split_model_path,
)

pytestmark = pytest.mark.unit


class TestSplitModelPath:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("", (None, "")),
            (None, (None, "")),
            ("   ", (None, "")),
            ("openai/gpt-4o", ("openai", "gpt-4o")),
            ("openrouter/anthropic/claude-3", ("openrouter", "anthropic/claude-3")),
            ("  vllm/qwen3  ", ("vllm", "qwen3")),
            ("qwen3", (None, "qwen3")),
            ("model.gguf", (None, "model.gguf")),
        ],
    )
    def test_new_format_and_plain_ids(self, path, expected):
        assert split_model_path(path) == expected

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("llm-svc://host1/qwen", ("host1", "qwen")),
            ("llm-svc://qwen", (None, "qwen")),
            ("LLM-SVC://host1/qwen", ("host1", "qwen")),
            ("llm-svc:///host1/qwen", ("host1", "qwen")),
            ("llm-svc://host/org/model", ("host", "org/model")),
            ("llm-svc://", (None, "")),
            ("llm-svc:///", (None, "")),
        ],
    )
    def test_legacy_llm_svc_uris(self, path, expected):
        assert split_model_path(path) == expected

    @pytest.mark.parametrize("path", ["1lm-svc://host/m", "11m-svc://host/m"])
    def test_typographic_typos_are_repaired(self, path):
        assert split_model_path(path) == ("host", "m")

    @pytest.mark.parametrize("path", ["/model", "provider/", "/"])
    def test_malformed_slashes_fall_back_to_a_plain_id(self, path):
        provider, model = split_model_path(path)

        assert provider is None
        assert model == path

    def test_round_trip_with_join(self):
        for provider, model in [("openai", "gpt-4o"), ("openrouter", "a/b/c")]:
            assert split_model_path(join_model_path(provider, model)) == (provider, model)


class TestJoinModelPath:
    def test_joins_with_a_single_slash(self):
        assert join_model_path("openai", "gpt-4o") == "openai/gpt-4o"

    def test_strips_slashes_around_the_provider(self):
        assert join_model_path("/openai/", "gpt") == "openai/gpt"

    def test_returns_the_model_alone_without_a_provider(self):
        assert join_model_path("", "gpt") == "gpt"
        assert join_model_path("  ", "gpt") == "gpt"

    def test_returns_empty_without_a_model(self):
        assert join_model_path("openai", "") == ""
        assert join_model_path("", "") == ""


class TestProviderConfig:
    def test_external_kinds_get_default_base_url(self):
        assert LLMProviderConfig(id="o", kind="openai").base_url == "https://api.openai.com"
        assert LLMProviderConfig(id="r", kind="openrouter").base_url == "https://openrouter.ai/api"
        assert LLMProviderConfig(id="a", kind="anthropic").base_url == "https://api.anthropic.com"

    def test_explicit_base_url_is_kept(self):
        cfg = LLMProviderConfig(id="o", kind="openai", base_url="https://proxy.local")

        assert cfg.base_url == "https://proxy.local"

    @pytest.mark.parametrize("kind", ["llm-svc", "vllm", "ollama", "litellm"])
    def test_local_kinds_require_base_url(self, kind):
        with pytest.raises(ValueError, match="base_url обязателен"):
            LLMProviderConfig(id="x", kind=kind)

    def test_blank_base_url_counts_as_missing(self):
        with pytest.raises(ValueError):
            LLMProviderConfig(id="x", kind="vllm", base_url="   ")

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValidationError):
            LLMProviderConfig(id="x", kind="skynet", base_url="http://x")

    def test_defaults(self):
        cfg = LLMProviderConfig(id="v", kind="vllm", base_url="http://v")

        assert (cfg.timeout, cfg.enabled, cfg.models, cfg.static_model, cfg.api_key_env) == (
            120.0,
            True,
            [],
            None,
            None,
        )

    def test_extra_fields_are_allowed(self):
        cfg = LLMProviderConfig(id="v", kind="vllm", base_url="http://v", custom_flag=True)

        assert cfg.custom_flag is True

    def test_repr_does_not_leak_the_secret_value(self, monkeypatch):
        monkeypatch.setenv("SECRET_ENV", "sk-very-secret-value")
        cfg = LLMProviderConfig(id="o", kind="openai", api_key_env="SECRET_ENV")

        assert "sk-very-secret-value" not in repr(cfg)
        assert "sk-very-secret-value" not in str(cfg.model_dump())

    def test_models_list_is_not_shared_between_instances(self):
        a = LLMProviderConfig(id="a", kind="vllm", base_url="http://a")
        b = LLMProviderConfig(id="b", kind="vllm", base_url="http://b")
        a.models.append("m")

        assert b.models == []


class TestDataclasses:
    def test_model_info_path(self):
        assert ModelInfo(provider_id="openai", model_id="gpt-4o").path == "openai/gpt-4o"

    def test_model_info_defaults(self):
        info = ModelInfo(provider_id="p", model_id="m")

        assert (info.display_name, info.context_size, info.extra) == ("", None, {})

    def test_health_defaults(self):
        health = ProviderHealth(healthy=False, error="down")

        assert (health.loaded_models, health.raw) == ([], {})

    def test_capabilities_defaults_are_conservative(self):
        caps = ProviderCapabilities()

        assert caps.hot_swap is False
        assert caps.vision is False
        assert caps.function_calling is False
        assert caps.native_chat_api is True

    def test_capabilities_are_immutable(self):
        with pytest.raises(Exception):
            ProviderCapabilities().vision = True  # type: ignore[misc]
