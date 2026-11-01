"""llm_providers/base (хвосты) и model_health: фильтрация каталога моделей по health-check."""

import logging
from types import SimpleNamespace

import pytest

from backend.llm_providers import model_health as mh
from backend.llm_providers.base import (
    ChatResult,
    LLMProvider,
    LLMProviderConfig,
    ModelInfo,
    ProviderCapabilities,
    ProviderHealth,
)

pytestmark = pytest.mark.unit


class _Provider(LLMProvider):
    """Минимальная реализация абстрактного провайдера."""

    def __init__(self, config, caps=None):
        super().__init__(config)
        self._caps = caps or ProviderCapabilities()
        self.chat_calls = []

    @property
    def capabilities(self):
        return self._caps

    async def health(self):
        return ProviderHealth(healthy=True)

    async def list_models(self):
        return []

    async def ensure_model_loaded(self, model_id):
        return True

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024, *, request_extra=None):
        self.chat_calls.append((messages, model, temperature, max_tokens, request_extra))
        return "ответ"

    async def stream_chat(self, messages, model, callback, temperature=0.7, max_tokens=1024, *, request_extra=None):
        return ""


def _cfg(**kw):
    return LLMProviderConfig(id="p1", kind="vllm", base_url="http://h:1/", **kw)


class TestLLMProviderBase:
    def test_properties_and_repr(self):
        prov = _Provider(_cfg())

        assert prov.id == "p1"
        assert prov.kind == "vllm"
        assert prov.base_url == "http://h:1"
        assert prov.enabled is True
        assert prov.config.id == "p1"
        assert repr(prov) == "<_Provider id='p1' base_url='http://h:1'>"

    def test_secret_status_and_api_key_follow_env(self, monkeypatch):
        monkeypatch.setenv("MY_P1_KEY", "sk-secret-123456")
        prov = _Provider(_cfg(api_key_env="MY_P1_KEY"))

        assert prov.get_api_key() == "sk-secret-123456"
        assert prov.has_api_key() is True
        status = prov.secret_status()
        assert status["api_key_env"] == "MY_P1_KEY" or "MY_P1_KEY" in str(status)

    def test_no_key(self, monkeypatch):
        monkeypatch.delenv("MISSING_P1_KEY", raising=False)
        prov = _Provider(_cfg(api_key_env="MISSING_P1_KEY"))

        assert prov.has_api_key() is False

    async def test_default_lifecycle_hooks_are_noops(self):
        prov = _Provider(_cfg())

        assert await prov.initialize() is None
        assert await prov.close() is None

    async def test_default_chat_completion_wraps_chat(self):
        prov = _Provider(_cfg())

        res = await prov.chat_completion(
            [{"role": "user", "content": "hi"}],
            "m",
            temperature=0.1,
            max_tokens=5,
            tools=[{"type": "function"}],
            request_extra={"x": 1},
        )

        assert isinstance(res, ChatResult)
        assert res.content == "ответ"
        assert res.tool_calls == []
        assert prov.chat_calls == [([{"role": "user", "content": "hi"}], "m", 0.1, 5, {"x": 1})]

    def test_describe_lists_capabilities_without_secrets(self):
        prov = _Provider(_cfg(static_model="qwen"), ProviderCapabilities(vision=True, function_calling=True))

        d = prov.describe()

        assert d["id"] == "p1" and d["static_model"] == "qwen"
        assert d["capabilities"]["vision"] is True
        assert d["capabilities"]["function_calling"] is True
        assert "api_key" not in d

    @pytest.mark.parametrize(
        ("extra", "expected"),
        [
            ({}, "auto"),
            ({"mcp_tool_calling_mode": " PROMPT_JSON_FC "}, "prompt_json_fc"),
            ({"mcp_tool_calling_mode": "native_openai_tools"}, "native_openai_tools"),
            ({"mcp_tool_calling_mode": "weird"}, "auto"),
            ({"mcp_tool_calling_mode": None}, "auto"),
        ],
    )
    def test_mcp_tool_calling_mode(self, extra, expected):
        assert _Provider(_cfg(extra=extra)).mcp_tool_calling_mode() == expected

    def test_native_function_calling_from_extra_overrides_capabilities(self):
        caps = ProviderCapabilities(function_calling=True)

        assert _Provider(_cfg(extra={"function_calling": False}), caps).supports_native_function_calling() is False
        assert _Provider(_cfg(extra={"function_calling": 1}), ProviderCapabilities()).supports_native_function_calling() is True
        assert _Provider(_cfg(), caps).supports_native_function_calling() is True
        assert _Provider(_cfg(), ProviderCapabilities()).supports_native_function_calling() is False


# ----------------------------------------------------------------- model_health


def _models(*ids, pid="p"):
    return [ModelInfo(provider_id=pid, model_id=i) for i in ids]


def _use_health_settings(monkeypatch, *, enabled=True, probe_max=64):
    monkeypatch.setattr(
        mh,
        "get_settings",
        lambda: SimpleNamespace(model_health=SimpleNamespace(filter_enabled=enabled, probe_max=probe_max)),
    )


class TestFilterEnabled:
    def test_globally_disabled(self, monkeypatch):
        _use_health_settings(monkeypatch, enabled=False)

        assert mh.model_health_filter_enabled(_cfg()) is False

    def test_enabled_by_default(self, monkeypatch):
        _use_health_settings(monkeypatch)

        assert mh.model_health_filter_enabled(_cfg()) is True

    def test_explicit_extra_flag_beats_rag_gateway(self, monkeypatch):
        _use_health_settings(monkeypatch)

        cfg = _cfg(extra={"filter_unhealthy_models": True, "rag_gateway": True})
        assert mh.model_health_filter_enabled(cfg) is True
        assert mh.model_health_filter_enabled(_cfg(extra={"filter_unhealthy_models": 0})) is False

    def test_rag_gateway_disables_filter_by_default(self, monkeypatch):
        _use_health_settings(monkeypatch)

        assert mh.model_health_filter_enabled(_cfg(extra={"rag_gateway": True})) is False

    def test_rag_gateway_attribute_on_config(self, monkeypatch):
        _use_health_settings(monkeypatch)

        assert mh.model_health_filter_enabled(_cfg(rag_gateway=True)) is False


class TestProbeMax:
    def test_from_settings(self, monkeypatch):
        _use_health_settings(monkeypatch, probe_max=7)

        assert mh.model_health_probe_max(_cfg()) == 7

    def test_extra_override_is_clamped_to_one(self, monkeypatch):
        _use_health_settings(monkeypatch, probe_max=7)

        assert mh.model_health_probe_max(_cfg(extra={"per_model_health_max": "3"})) == 3
        assert mh.model_health_probe_max(_cfg(extra={"per_model_health_max": -5})) == 1

    def test_garbage_override_falls_back_to_settings(self, monkeypatch):
        _use_health_settings(monkeypatch, probe_max=9)

        assert mh.model_health_probe_max(_cfg(extra={"per_model_health_max": "lots"})) == 9
        assert mh.model_health_probe_max(_cfg(extra={"per_model_health_max": None})) == 9


class TestModelIdMatching:
    @pytest.mark.parametrize(
        ("model_id", "names", "expected"),
        [
            ("", {"a"}, False),
            ("  ", {"a"}, False),
            ("a", set(), False),
            ("gpt-4", {"gpt-4"}, True),
            ("gpt-4", {"openai/gpt-4"}, True),
            ("gpt-4", {"openai\\gpt-4"}, True),
            ("openai/gpt-4", {"gpt-4"}, True),
            ("openai\\gpt-4", {"gpt-4"}, True),
            ("x/model", {"y/model"}, False),
            ("Qwen-2.5_7B", {"qwen2.5-7b"}, True),
            ("Qwen-2.5_7B", {"vendor/qwen2.5-7b"}, True),
            ("org/Qwen-2.5_7B", {"vendor/qwen2.5-7b"}, False),
            ("gpt-4", {"gpt-3"}, False),
            ("gpt-4", {"", "gpt-5"}, False),
        ],
    )
    def test_fuzzy_match(self, model_id, names, expected):
        assert mh.model_id_matches_any(model_id, names) is expected

    def test_model_id_is_stripped_before_lookup(self):
        assert mh.model_id_matches_any(" abc ", {"abc"}) is True

    def test_normalize_key_handles_none(self):
        assert mh._normalize_model_key(None) == ""


class TestUnhealthyNames:
    def test_extracts_names_from_all_supported_keys(self):
        payload = {
            "unhealthy_endpoints": [
                {"model": " m1 "},
                {"model_name": "m2"},
                {"model_id": "m3"},
                {"model": ""},
                {"other": 1},
                "junk",
            ]
        }

        assert mh.unhealthy_names_from_litellm_payload(payload) == {"m1", "m2", "m3"}

    def test_blank_after_strip_is_skipped(self):
        assert mh.unhealthy_names_from_litellm_payload({"unhealthy_endpoints": [{"model": "   "}]}) == set()

    def test_non_list_payload(self):
        assert mh.unhealthy_names_from_litellm_payload({"unhealthy_endpoints": "x"}) == set()
        assert mh.unhealthy_names_from_litellm_payload({}) == set()


class TestKeepModelsIfEmpty:
    def test_returns_original_when_filter_removed_everything(self, caplog):
        original = _models("a", "b")

        with caplog.at_level(logging.WARNING):
            res = mh.keep_models_if_empty(original, [], provider_id="p", reason="r")

        assert res is original

    def test_returns_filtered_otherwise(self):
        original = _models("a", "b")
        filtered = original[:1]

        assert mh.keep_models_if_empty(original, filtered, provider_id="p", reason="r") is filtered
        assert mh.keep_models_if_empty([], [], provider_id="p", reason="r") == []


class TestExcludeByNames:
    def test_empty_exclude_returns_same_list(self):
        models = _models("a")

        assert mh.exclude_models_by_names(models, set(), provider_id="p") is models

    def test_removes_matching_models(self):
        res = mh.exclude_models_by_names(_models("a", "b", "c"), {"vendor/b"}, provider_id="p")

        assert [m.model_id for m in res] == ["a", "c"]

    def test_nothing_matched_keeps_all(self):
        models = _models("a", "b")

        res = mh.exclude_models_by_names(models, {"zzz"}, provider_id="p")

        assert [m.model_id for m in res] == ["a", "b"]

    def test_all_matched_returns_original_catalog(self):
        models = _models("a", "b")

        assert mh.exclude_models_by_names(models, {"a", "b"}, provider_id="p") is models


class TestChatProbeFilter:
    def test_keeps_healthy_and_unprobed(self):
        models = _models("ok", "bad", "unknown")

        res = mh.filter_models_by_chat_probe(models, {"ok": True, "bad": False}, provider_id="p")

        assert [m.model_id for m in res] == ["ok", "unknown"]

    def test_models_without_id_are_dropped(self):
        models = [ModelInfo(provider_id="p", model_id=""), *_models("ok")]

        res = mh.filter_models_by_chat_probe(models, {}, provider_id="p")

        assert [m.model_id for m in res] == ["ok"]

    def test_all_unhealthy_falls_back_to_full_list(self):
        models = _models("a", "b")

        assert mh.filter_models_by_chat_probe(models, {"a": False, "b": False}, provider_id="p") is models

    def test_all_healthy_no_change(self):
        models = _models("a")

        res = mh.filter_models_by_chat_probe(models, {"a": True}, provider_id="p")

        assert res == models
