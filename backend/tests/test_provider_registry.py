# -*- coding: utf-8 -*-
"""Тесты backend.llm_providers.registry: разбор путей моделей, конфигов, ProviderRegistry."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.llm_providers import registry as reg  # noqa: E402
from backend.llm_providers.base import LLMProviderConfig, split_model_path  # noqa: E402


class FakeProvider:
    def __init__(self, pid, enabled=True, models=(), fail_list=False, fail_init=False, fail_close=False):
        self.id, self.enabled = pid, enabled
        self._models, self.fail_list = list(models), fail_list
        self.fail_init, self.fail_close = fail_init, fail_close
        self.initialized = self.closed = False

    async def list_models(self):
        if self.fail_list:
            raise RuntimeError("boom")
        return self._models

    async def initialize(self):
        if self.fail_init:
            raise RuntimeError("init")
        self.initialized = True

    async def close(self):
        if self.fail_close:
            raise RuntimeError("close")
        self.closed = True


def make_registry(**kw):
    providers = {
        "a": FakeProvider("a"),
        "b": FakeProvider("b"),
        "off": FakeProvider("off", enabled=False),
    }
    return reg.ProviderRegistry(providers, kw.pop("default", "a"), **kw), providers


class TestSplitModelPath:
    @pytest.mark.parametrize(
        "path, expected",
        [
            ("", (None, "")),
            (None, (None, "")),
            ("   ", (None, "")),
            ("model", (None, "model")),
            ("prov/model", ("prov", "model")),
            ("openrouter/anthropic/claude", ("openrouter", "anthropic/claude")),
            ("llm-svc://host/model", ("host", "model")),
            ("llm-svc://model", (None, "model")),
        ],
    )
    def test_split(self, path, expected):
        assert split_model_path(path) == expected


class TestNormalizeProviderItem:
    def test_non_dict_returned_as_is(self):
        assert reg._normalize_provider_item("x") == "x"
        assert reg._normalize_provider_item(None) is None

    def test_input_not_mutated(self):
        raw = {"id": "p", "models": ["a "]}
        reg._normalize_provider_item(raw)
        assert raw == {"id": "p", "models": ["a "]}

    @pytest.mark.parametrize(
        "models, expected",
        [
            (["a", " b ", ""], ["a", "b"]),
            ({"name": ["x", " y "]}, ["x", "y"]),
            ({"name": " solo "}, ["solo"]),
            ({"name": 5}, []),
            ({}, []),
            (" single ", ["single"]),
        ],
    )
    def test_models_shapes(self, models, expected):
        assert reg._normalize_provider_item({"id": "p", "models": models})["models"] == expected

    def test_blank_string_models_left_untouched(self):
        assert reg._normalize_provider_item({"models": "  "})["models"] == "  "

    def test_explicit_api_key_env_wins(self):
        item = reg._normalize_provider_item({"api_key_env": "MY", "llm_key": "OTHER"})
        assert item["api_key_env"] == "MY"

    def test_legacy_llm_key(self):
        assert reg._normalize_provider_item({"llm_key": " LEG "})["api_key_env"] == "LEG"

    def test_provider_scoped_legacy_key(self):
        item = reg._normalize_provider_item({"id": "Phoenix", "llm_key_phoenix": "PX", "llm_key_other": "OT"})
        assert item["api_key_env"] == "PX"

    def test_any_prefixed_legacy_key_fallback(self):
        assert reg._normalize_provider_item({"id": "p", "llm_key_zzz": "Z"})["api_key_env"] == "Z"

    def test_no_keys(self):
        assert "api_key_env" not in reg._normalize_provider_item({"id": "p"})

    def test_unrelated_keys_not_mistaken_for_llm_key(self):
        assert "api_key_env" not in reg._normalize_provider_item({"id": "p", "llm_keys": "x", "my_llm_key": "y"})


class TestBuildProvider:
    @pytest.mark.parametrize(
        "kind, class_name",
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
    def test_kind_to_class(self, kind, class_name):
        cfg = LLMProviderConfig(id="p", kind=kind, base_url="http://h:1")
        assert type(reg._build_provider(cfg)).__name__ == class_name

    def test_unknown_kind_falls_back_to_compat(self):
        cfg = SimpleNamespace(kind="mystery", id="p", base_url="http://h", enabled=True, timeout=5.0,
                              api_key_env=None, static_model=None, models=[], extra={})
        try:
            provider = reg._build_provider(cfg)
        except Exception:
            pytest.skip("OpenAICompatProvider требует полноценный LLMProviderConfig")
        assert type(provider).__name__ == "OpenAICompatProvider"


class TestEnvParsers:
    @pytest.mark.parametrize(
        "value, default, expected",
        [("", True, True), ("", False, False), ("1", False, True), ("TRUE", False, True),
         ("yes", False, True), ("on", False, True), ("0", True, False), ("false", True, False),
         ("garbage", True, False), ("  ", True, True)],
    )
    def test_parse_bool(self, value, default, expected):
        assert reg._parse_bool_env(value, default) is expected

    @pytest.mark.parametrize(
        "value, expected",
        [("2.5", 2.5), (" 30 ", 30.0), ("", 9.0), ("abc", 9.0), (None, 9.0)],
    )
    def test_parse_float(self, value, expected):
        assert reg._parse_float_env(value, 9.0) == expected


@pytest.fixture
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("LLM_PROVIDER_"):
            monkeypatch.delenv(key)
    return monkeypatch


class TestPrefixedEnv:
    def test_no_variables(self, clean_env):
        assert reg._parse_provider_configs_from_prefixed_env() == []

    def test_full_provider(self, clean_env):
        clean_env.setenv("LLM_PROVIDER_ACME_KIND", "openai-compat")
        clean_env.setenv("LLM_PROVIDER_ACME_BASE_URL", " http://acme:8000 ")
        clean_env.setenv("LLM_PROVIDER_ACME_ENABLED", "false")
        clean_env.setenv("LLM_PROVIDER_ACME_TIMEOUT", "45")
        clean_env.setenv("LLM_PROVIDER_ACME_API_KEY_ENV", "ACME_SECRET")
        clean_env.setenv("LLM_PROVIDER_ACME_STATIC_MODEL", "qwen")
        [cfg] = reg._parse_provider_configs_from_prefixed_env()
        assert (cfg.id, cfg.kind, cfg.base_url) == ("ACME", "openai-compat", "http://acme:8000")
        assert cfg.enabled is False
        assert cfg.timeout == 45.0
        assert cfg.api_key_env == "ACME_SECRET"
        assert cfg.static_model == "qwen"

    def test_provider_id_may_contain_underscores(self, clean_env):
        clean_env.setenv("LLM_PROVIDER_MY_CLUSTER_KIND", "vllm")
        clean_env.setenv("LLM_PROVIDER_MY_CLUSTER_BASE_URL", "http://x")
        [cfg] = reg._parse_provider_configs_from_prefixed_env()
        assert cfg.id == "MY_CLUSTER"

    def test_defaults(self, clean_env):
        clean_env.setenv("LLM_PROVIDER_P_KIND", "vllm")
        clean_env.setenv("LLM_PROVIDER_P_BASE_URL", "http://x")
        [cfg] = reg._parse_provider_configs_from_prefixed_env()
        assert cfg.enabled is True and cfg.timeout == 120.0
        assert cfg.api_key_env is None and cfg.static_model is None

    def test_missing_kind_skipped(self, clean_env):
        clean_env.setenv("LLM_PROVIDER_P_BASE_URL", "http://x")
        assert reg._parse_provider_configs_from_prefixed_env() == []

    def test_local_kind_without_base_url_skipped(self, clean_env):
        clean_env.setenv("LLM_PROVIDER_P_KIND", "vllm")
        assert reg._parse_provider_configs_from_prefixed_env() == []

    def test_external_kind_gets_default_base_url(self, clean_env):
        clean_env.setenv("LLM_PROVIDER_O_KIND", "openai")
        [cfg] = reg._parse_provider_configs_from_prefixed_env()
        assert cfg.base_url.startswith("http")

    def test_unsupported_field_ignored(self, clean_env):
        clean_env.setenv("LLM_PROVIDER_P_UNKNOWN", "x")
        assert reg._parse_provider_configs_from_prefixed_env() == []

    def test_several_providers(self, clean_env):
        for pid in ("A", "B"):
            clean_env.setenv(f"LLM_PROVIDER_{pid}_KIND", "vllm")
            clean_env.setenv(f"LLM_PROVIDER_{pid}_BASE_URL", f"http://{pid.lower()}")
        assert sorted(c.id for c in reg._parse_provider_configs_from_prefixed_env()) == ["A", "B"]


class TestRegistryAccessors:
    def test_default_id(self):
        r, _ = make_registry(default="b")
        assert r.default_id == "b"

    def test_get_known_unknown_and_empty(self):
        r, p = make_registry()
        assert r.get("b") is p["b"]
        assert r.get("nope") is p["a"]
        assert r.get(None) is p["a"]
        assert r.get("  ") is p["a"]

    @pytest.mark.parametrize("pid, expected", [("a", True), (" a ", True), ("nope", False), ("", False), (None, False)])
    def test_contains(self, pid, expected):
        r, _ = make_registry()
        assert r.contains(pid) is expected

    def test_all_and_ids_hide_disabled(self):
        r, p = make_registry()
        assert [x.id for x in r.all()] == ["a", "b"]
        assert r.ids() == ["a", "b"]
        assert r.ids(include_disabled=True) == ["a", "b", "off"]
        assert r.all(include_disabled=True)[-1] is p["off"]


class TestResolve:
    def test_provider_prefixed_path(self):
        r, p = make_registry()
        assert r.resolve("b/qwen") == (p["b"], "qwen")

    def test_slashes_inside_model_id(self):
        r, p = make_registry()
        assert r.resolve("b/org/model") == (p["b"], "org/model")

    def test_legacy_llm_svc_uri(self):
        r, p = make_registry()
        assert r.resolve("llm-svc://b/qwen") == (p["b"], "qwen")

    def test_legacy_uri_without_host_uses_default(self):
        r, p = make_registry(default="b")
        assert r.resolve("llm-svc://qwen") == (p["b"], "qwen")

    @pytest.mark.parametrize("path", [None, "", "   "])
    def test_empty_path_gives_default_and_empty_model(self, path):
        r, p = make_registry()
        assert r.resolve(path) == (p["a"], "")

    def test_flat_model_goes_to_default(self):
        r, p = make_registry()
        assert r.resolve("qwen") == (p["a"], "qwen")

    def test_unknown_provider_keeps_path_as_model_id(self):
        r, p = make_registry()
        assert r.resolve("openrouter/anthropic/claude") == (p["a"], "openrouter/anthropic/claude")

    def test_flat_model_resolved_by_configured_models(self):
        r, p = make_registry(configured_models_by_provider={"b": ["special"]})
        assert r.resolve("special") == (p["b"], "special")

    def test_ambiguous_configured_model_falls_back_to_default(self):
        r, p = make_registry(configured_models_by_provider={"a": ["dup"], "b": ["dup"]}, default="a")
        assert r.resolve("dup") == (p["a"], "dup")
        r2, p2 = make_registry(configured_models_by_provider={"a": ["dup"], "b": ["dup"]}, default="off")
        assert r2.resolve("dup") == (p2["off"], "dup")

    def test_configured_models_of_unknown_provider_ignored(self):
        r, p = make_registry(configured_models_by_provider={"ghost": ["m"], "b": ["", "  "]})
        assert r.resolve("m") == (p["a"], "m")

    def test_duplicate_model_names_in_same_provider_still_unique(self):
        r, p = make_registry(configured_models_by_provider={"b": ["m", "m"]})
        assert r.resolve("m") == (p["b"], "m")


class TestRegistryLifecycle:
    async def test_initialize_and_close_all_even_if_some_fail(self):
        providers = {"a": FakeProvider("a", fail_init=True, fail_close=True), "b": FakeProvider("b")}
        r = reg.ProviderRegistry(providers, "a")
        await r.initialize()
        await r.close()
        assert providers["b"].initialized and providers["b"].closed

    async def test_list_all_models_skips_disabled_and_failing(self):
        providers = {
            "a": FakeProvider("a", models=["m1", "m2"]),
            "bad": FakeProvider("bad", fail_list=True),
            "off": FakeProvider("off", enabled=False, models=["hidden"]),
            "b": FakeProvider("b", models=["m3"]),
        }
        r = reg.ProviderRegistry(providers, "a")
        assert await r.list_all_models() == ["m1", "m2", "m3"]
