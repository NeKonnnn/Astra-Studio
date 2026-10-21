"""llm_providers/registry: нормализация конфигов, сборка провайдеров, поиск и резолв путей."""

from types import SimpleNamespace

import pytest

from backend.llm_providers import registry as reg
from backend.llm_providers.base import LLMProviderConfig

pytestmark = pytest.mark.unit


def _provider(pid, *, enabled=True):
    return SimpleNamespace(id=pid, enabled=enabled)


def _registry(ids=("a", "b"), default="a", configured=None, disabled=()):
    providers = {pid: _provider(pid, enabled=pid not in disabled) for pid in ids}
    return reg.ProviderRegistry(providers, default, configured)


class TestNormalizeProviderItem:
    def test_non_dict_is_returned_as_is(self):
        assert reg._normalize_provider_item("x") == "x"
        assert reg._normalize_provider_item(None) is None

    def test_does_not_mutate_the_input(self):
        raw = {"id": "p", "models": [" a "]}

        reg._normalize_provider_item(raw)

        assert raw == {"id": "p", "models": [" a "]}

    @pytest.mark.parametrize(
        ("models", "expected"),
        [
            ([" a ", "", None, "b"], ["a", "b"]),
            ("single", ["single"]),
            ("   ", None),
            ({"name": ["x", " y ", ""]}, ["x", "y"]),
            ({"name": "solo"}, ["solo"]),
            ({"name": 5}, []),
            ({}, []),
        ],
    )
    def test_models_are_flattened_to_a_clean_list(self, models, expected):
        item = reg._normalize_provider_item({"id": "p", "models": models})

        if expected is None:
            assert item["models"] == "   "
        else:
            assert item["models"] == expected

    def test_new_api_key_env_wins(self):
        item = reg._normalize_provider_item({"id": "p", "api_key_env": "NEW", "llm_key": "OLD"})

        assert item["api_key_env"] == "NEW"

    def test_legacy_llm_key(self):
        assert reg._normalize_provider_item({"id": "p", "llm_key": " OLD_ENV "})["api_key_env"] == "OLD_ENV"

    def test_legacy_per_provider_key(self):
        item = reg._normalize_provider_item({"id": "Phoenix", "llm_key_phoenix": "PHX_KEY"})

        assert item["api_key_env"] == "PHX_KEY"

    def test_any_other_llm_key_variant_is_picked_up(self):
        item = reg._normalize_provider_item({"id": "p", "llm_key_other-name": "OTHER"})

        assert item["api_key_env"] == "OTHER"

    def test_no_key_fields_leaves_it_unset(self):
        assert "api_key_env" not in reg._normalize_provider_item({"id": "p"})

    def test_blank_values_are_ignored(self):
        item = reg._normalize_provider_item({"id": "p", "llm_key": "  ", "api_key_env": ""})

        assert not item.get("api_key_env")

    def test_unrelated_keys_are_not_mistaken_for_keys(self):
        assert "api_key_env" not in reg._normalize_provider_item({"id": "p", "llm_keys": "X", "llm_keyword": "Y"})


class TestEnvParsers:
    @pytest.mark.parametrize(
        ("raw", "default", "expected"),
        [("1", False, True), ("true", False, True), ("YES", False, True), ("on", False, True), ("no", True, False), ("", True, True), ("", False, False), ("0", True, False)],
    )
    def test_bool(self, raw, default, expected):
        assert reg._parse_bool_env(raw, default) is expected

    def test_float(self):
        assert reg._parse_float_env(" 2.5 ", 1.0) == 2.5
        assert reg._parse_float_env("abc", 7.0) == 7.0
        assert reg._parse_float_env("", 7.0) == 7.0


class TestPrefixedEnv:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        import os

        for key in list(os.environ):
            if key.startswith("LLM_PROVIDER_"):
                monkeypatch.delenv(key)

    def test_nothing_without_variables(self):
        assert reg._parse_provider_configs_from_prefixed_env() == []

    def test_builds_a_provider_from_prefixed_variables(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_GW_KIND", "openai-compat")
        monkeypatch.setenv("LLM_PROVIDER_GW_BASE_URL", " https://gw.local ")
        monkeypatch.setenv("LLM_PROVIDER_GW_ENABLED", "false")
        monkeypatch.setenv("LLM_PROVIDER_GW_TIMEOUT", "300")
        monkeypatch.setenv("LLM_PROVIDER_GW_API_KEY_ENV", "GW_SECRET")
        monkeypatch.setenv("LLM_PROVIDER_GW_STATIC_MODEL", "qwen")

        (cfg,) = reg._parse_provider_configs_from_prefixed_env()

        assert (cfg.id, cfg.kind, cfg.base_url) == ("GW", "openai-compat", "https://gw.local")
        assert (cfg.enabled, cfg.timeout, cfg.api_key_env, cfg.static_model) == (False, 300.0, "GW_SECRET", "qwen")

    def test_provider_id_may_contain_underscores(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_MY_GATEWAY_KIND", "vllm")
        monkeypatch.setenv("LLM_PROVIDER_MY_GATEWAY_BASE_URL", "http://v")

        (cfg,) = reg._parse_provider_configs_from_prefixed_env()

        assert cfg.id == "MY_GATEWAY"

    def test_skips_providers_without_a_kind(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_X_BASE_URL", "http://x")

        assert reg._parse_provider_configs_from_prefixed_env() == []

    def test_skips_invalid_providers_but_keeps_valid_ones(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_BAD_KIND", "vllm")  # base_url обязателен
        monkeypatch.setenv("LLM_PROVIDER_GOOD_KIND", "vllm")
        monkeypatch.setenv("LLM_PROVIDER_GOOD_BASE_URL", "http://ok")

        configs = reg._parse_provider_configs_from_prefixed_env()

        assert [c.id for c in configs] == ["GOOD"]

    def test_unrelated_variables_are_ignored(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_API_KEY", "x")
        monkeypatch.setenv("LLM_PROVIDER_FOO_UNKNOWN", "x")

        assert reg._parse_provider_configs_from_prefixed_env() == []

    def test_secret_values_are_never_read_into_the_config(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_GW_KIND", "openai")
        monkeypatch.setenv("LLM_PROVIDER_GW_API_KEY_ENV", "REAL_SECRET")
        monkeypatch.setenv("REAL_SECRET", "sk-do-not-copy")

        (cfg,) = reg._parse_provider_configs_from_prefixed_env()

        assert "sk-do-not-copy" not in str(cfg.model_dump())


class TestBuildProvider:
    @pytest.mark.parametrize(
        ("kind", "class_name"),
        [
            ("llm-svc", "LlmSvcProvider"),
            ("vllm", "VLLMProvider"),
            ("ollama", "OllamaProvider"),
            ("litellm", "LiteLLMProvider"),
            ("openai", "OpenAIProvider"),
            ("openrouter", "OpenRouterProvider"),
            ("anthropic", "AnthropicProvider"),
            ("openai-compat", "OpenAICompatProvider"),
        ],
    )
    def test_each_kind_gets_its_class(self, kind, class_name):
        provider = reg._build_provider(LLMProviderConfig(id="p", kind=kind, base_url="http://p.test"))

        assert type(provider).__name__ == class_name
        assert provider.id == "p"
        assert provider.kind == kind

    def test_unknown_kind_falls_back_to_openai_compat(self):
        # model_construct обходит валидацию kind, имитируя запись, попавшую в обход схемы
        config = LLMProviderConfig.model_construct(id="p", kind="weird", base_url="http://p.test")

        assert type(reg._build_provider(config)).__name__ == "OpenAICompatProvider"


class TestRegistryLookup:
    def test_get_by_id(self):
        r = _registry()

        assert r.get("b").id == "b"

    @pytest.mark.parametrize("pid", [None, "", "   "])
    def test_get_without_id_returns_default(self, pid):
        assert _registry().get(pid).id == "a"

    def test_get_unknown_returns_default(self):
        assert _registry().get("nope").id == "a"

    def test_contains(self):
        r = _registry()

        assert r.contains("a") and r.contains(" a".strip())
        assert not r.contains("zzz") and not r.contains("") and not r.contains(None)

    def test_all_and_ids_hide_disabled_by_default(self):
        r = _registry(ids=("a", "b", "c"), disabled=("b",))

        assert r.ids() == ["a", "c"]
        assert r.ids(include_disabled=True) == ["a", "b", "c"]
        assert [p.id for p in r.all()] == ["a", "c"]

    def test_default_id(self):
        assert _registry(default="b").default_id == "b"


class TestRegistryResolve:
    def test_provider_prefix(self):
        provider, model = _registry().resolve("b/qwen3")

        assert (provider.id, model) == ("b", "qwen3")

    def test_model_id_may_contain_slashes(self):
        provider, model = _registry().resolve("a/org/model")

        assert (provider.id, model) == ("a", "org/model")

    def test_legacy_uri_with_host(self):
        provider, model = _registry().resolve("llm-svc://b/qwen")

        assert (provider.id, model) == ("b", "qwen")

    def test_legacy_uri_without_host_uses_default(self):
        provider, model = _registry().resolve("llm-svc://qwen")

        assert (provider.id, model) == ("a", "qwen")

    def test_plain_model_uses_default(self):
        provider, model = _registry().resolve("qwen")

        assert (provider.id, model) == ("a", "qwen")

    @pytest.mark.parametrize("path", [None, "", "   "])
    def test_empty_path_returns_default_and_empty_model(self, path):
        provider, model = _registry().resolve(path)

        assert (provider.id, model) == ("a", "")

    def test_unknown_provider_prefix_keeps_the_whole_path_as_model(self):
        provider, model = _registry().resolve("ghost/model")

        assert (provider.id, model) == ("a", "ghost/model")

    def test_unique_configured_model_name_routes_to_its_provider(self):
        r = _registry(configured={"b": ["special-model"]})

        provider, model = r.resolve("special-model")

        assert (provider.id, model) == ("b", "special-model")

    def test_ambiguous_configured_model_name_falls_back_to_default(self):
        r = _registry(configured={"a": ["shared"], "b": ["shared"]}, default="a")

        provider, model = r.resolve("shared")

        assert (provider.id, model) == ("a", "shared")

    def test_configured_models_of_unknown_providers_are_ignored(self):
        r = _registry(configured={"ghost": ["m"]})

        provider, _ = r.resolve("m")

        assert provider.id == "a"

    def test_blank_configured_model_names_are_ignored(self):
        r = _registry(configured={"b": ["", "  ", "ok"]})

        assert r.resolve("ok")[0].id == "b"
        assert r.resolve("")[0].id == "a"


class TestRegistryLifecycle:
    async def test_initialize_and_close_survive_a_failing_provider(self):
        calls = []

        class Good:
            id = "good"
            enabled = True

            async def initialize(self):
                calls.append("good-init")

            async def close(self):
                calls.append("good-close")

        class Bad(Good):
            id = "bad"

            async def initialize(self):
                raise RuntimeError("down")

            async def close(self):
                raise RuntimeError("stuck")

        r = reg.ProviderRegistry({"bad": Bad(), "good": Good()}, "good")

        await r.initialize()
        await r.close()

        assert calls == ["good-init", "good-close"]
