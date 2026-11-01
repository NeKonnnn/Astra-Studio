"""llm_providers/registry: источники конфигурации (ENV, JSON, YAML, legacy hosts), сборка и жизненный цикл реестра."""

import json
import os
from types import SimpleNamespace

import pytest

from backend.llm_providers import registry as reg
from backend.llm_providers.base import LLMProviderConfig, ModelInfo
from backend.llm_providers.llm_svc import LlmSvcProvider
from backend.llm_providers.openai_compat import OpenAICompatProvider

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("LLM_PROVIDER_") or name in ("LLM_PROVIDERS_JSON", "DEFAULT_LLM_PROVIDER"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reg, "_registry", None)


def settings(**kw):
    base = {"llm_providers": [], "default_llm_provider": None, "llm_service": None}
    base.update(kw)
    return SimpleNamespace(**base)


def cfg(pid="p", kind="openai-compat", base="http://h", **kw):
    return LLMProviderConfig(id=pid, kind=kind, base_url=base, **kw)


class TestPrefixedEnv:
    def test_groups_fields_by_provider_id(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_ALPHA_KIND", "vllm")
        monkeypatch.setenv("LLM_PROVIDER_ALPHA_BASE_URL", " http://alpha ")
        monkeypatch.setenv("LLM_PROVIDER_ALPHA_ENABLED", "no")
        monkeypatch.setenv("LLM_PROVIDER_ALPHA_TIMEOUT", "300")
        monkeypatch.setenv("LLM_PROVIDER_ALPHA_API_KEY_ENV", " ALPHA_KEY ")
        monkeypatch.setenv("LLM_PROVIDER_ALPHA_STATIC_MODEL", "qwen")

        [c] = reg._parse_provider_configs_from_prefixed_env()

        assert (c.id, c.kind, c.base_url, c.enabled, c.timeout) == ("ALPHA", "vllm", "http://alpha", False, 300.0)
        assert c.api_key_env == "ALPHA_KEY" and c.static_model == "qwen"

    def test_unknown_suffix_and_empty_id_are_ignored(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_ALPHA_COLOR", "red")
        monkeypatch.setenv("LLM_PROVIDER__KIND", "vllm")

        assert reg._parse_provider_configs_from_prefixed_env() == []

    def test_missing_kind_is_skipped(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_BETA_BASE_URL", "http://beta")

        assert reg._parse_provider_configs_from_prefixed_env() == []

    def test_invalid_config_is_skipped(self, monkeypatch):
        # локальный kind без base_url -> LLMProviderConfig бросает ValueError
        monkeypatch.setenv("LLM_PROVIDER_GAMMA_KIND", "vllm")
        monkeypatch.setenv("LLM_PROVIDER_OK_KIND", "openai")

        configs = reg._parse_provider_configs_from_prefixed_env()

        assert [c.id for c in configs] == ["OK"]
        assert configs[0].base_url == "https://api.openai.com"

    def test_bad_timeout_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_DELTA_KIND", "openai")
        monkeypatch.setenv("LLM_PROVIDER_DELTA_TIMEOUT", "soon")

        [c] = reg._parse_provider_configs_from_prefixed_env()

        assert c.timeout == 120.0

    @pytest.mark.parametrize(("raw", "default", "expected"), [("", True, True), ("", False, False), ("Yes", False, True), ("off", True, False)])
    def test_parse_bool_env(self, raw, default, expected):
        assert reg._parse_bool_env(raw, default) is expected

    def test_parse_float_env(self):
        assert reg._parse_float_env(" 2.5 ", 1.0) == 2.5
        assert reg._parse_float_env("x", 7.0) == 7.0


class TestParseConfigsFromSettings:
    def test_prefixed_env_wins_over_json_and_yaml(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_ENVP_KIND", "openai")
        monkeypatch.setenv("LLM_PROVIDERS_JSON", json.dumps([{"id": "j", "kind": "openai"}]))
        monkeypatch.setenv("DEFAULT_LLM_PROVIDER", "ENVP")

        configs, default = reg._parse_configs_from_settings(settings(llm_providers=[{"id": "y", "kind": "openai"}]))

        assert [c.id for c in configs] == ["ENVP", "y"]
        assert default == "ENVP"

    def test_json_env_items(self, monkeypatch):
        monkeypatch.setenv(
            "LLM_PROVIDERS_JSON",
            json.dumps(
                [
                    {"id": "a", "kind": "openai", "llm_key": "A_ENV"},
                    {"id": "bad", "kind": "vllm"},  # нет base_url
                    "just-a-string",
                ]
            ),
        )

        configs, default = reg._parse_configs_from_settings(settings())

        assert [c.id for c in configs] == ["a"]
        assert configs[0].api_key_env == "A_ENV"
        assert default == "a"

    def test_json_env_must_be_a_list(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDERS_JSON", '{"id": "x"}')

        assert reg._parse_configs_from_settings(settings()) == ([], None)

    def test_broken_json_is_ignored(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDERS_JSON", "{not json")

        assert reg._parse_configs_from_settings(settings()) == ([], None)

    def test_yaml_items_of_all_supported_shapes(self):
        class Dumpable:
            def model_dump(self):
                return {"id": "dumped", "kind": "openai"}

        ready = cfg("ready")
        section = [
            ready,
            {"id": "raw", "kind": "openai", "models": {"name": [" m1 ", ""]}},
            Dumpable(),
            {"id": "bad", "kind": "vllm"},
            42,
        ]

        configs, default = reg._parse_configs_from_settings(settings(llm_providers=section, default_llm_provider="raw"))

        assert [c.id for c in configs] == ["ready", "raw", "dumped"]
        assert configs[1].models == ["m1"]
        assert default == "raw"

    def test_yaml_section_that_is_not_a_list(self):
        configs, default = reg._parse_configs_from_settings(settings(llm_providers={"id": "x"}))

        assert configs == [] and default is None

    def test_duplicates_keep_first_and_unknown_default_is_replaced(self):
        section = [{"id": "a", "kind": "openai"}, {"id": "a", "kind": "openai", "timeout": 5}, {"id": "b", "kind": "openai"}]

        configs, default = reg._parse_configs_from_settings(settings(llm_providers=section, default_llm_provider="missing"))

        assert [c.id for c in configs] == ["a", "b"]
        assert configs[0].timeout == 120.0
        assert default == "a"

    def test_legacy_hosts_become_llm_svc_providers(self):
        hosts = [
            SimpleNamespace(id="h1", base_url="http://h1"),
            {"id": "h2", "base_url": "http://h2"},
            {"id": "h3"},
            None,
            "junk",
        ]
        llm = SimpleNamespace(hosts=hosts, default_host_id=" h2 ", timeout=33, base_url="")

        configs, default = reg._parse_configs_from_settings(settings(llm_service=llm))

        assert [(c.id, c.kind, c.timeout) for c in configs] == [("h1", "llm-svc", 33.0), ("h2", "llm-svc", 33.0)]
        assert default == "h2"

    def test_legacy_default_host_unknown_uses_first(self):
        llm = SimpleNamespace(hosts=[{"id": "h1", "base_url": "http://h1"}], default_host_id="zzz", timeout=None, base_url="")

        configs, default = reg._parse_configs_from_settings(settings(llm_service=llm))

        assert default == "h1" and configs[0].timeout == 120.0

    def test_single_base_url_becomes_local_llmsvc(self):
        llm = SimpleNamespace(hosts=None, default_host_id=None, timeout=10, base_url=" http://single ")

        configs, default = reg._parse_configs_from_settings(settings(llm_service=llm))

        assert [(c.id, c.kind, c.base_url, c.timeout) for c in configs] == [
            ("local-llmsvc", "llm-svc", "http://single", 10.0)
        ]
        assert default == "local-llmsvc"

    def test_legacy_host_with_invalid_id_is_skipped(self):
        llm = SimpleNamespace(hosts=[{"id": 123, "base_url": "http://x"}, {"id": "ok", "base_url": "http://ok"}], default_host_id=None, timeout=1, base_url="")

        configs, default = reg._parse_configs_from_settings(settings(llm_service=llm))

        assert [c.id for c in configs] == ["ok"] and default == "ok"

    def test_legacy_config_construction_errors_are_swallowed(self, monkeypatch):
        def boom(**kw):
            msg = "cannot construct"
            raise ValueError(msg)

        monkeypatch.setattr(reg, "LLMProviderConfig", boom)
        with_hosts = SimpleNamespace(hosts=[{"id": "h", "base_url": "http://h"}], default_host_id=None, timeout=1, base_url="")
        single = SimpleNamespace(hosts=[], default_host_id=None, timeout=1, base_url="http://single")

        assert reg._parse_configs_from_settings(settings(llm_service=with_hosts)) == ([], None)
        assert reg._parse_configs_from_settings(settings(llm_service=single)) == ([], None)

    def test_legacy_without_anything(self):
        llm = SimpleNamespace(hosts=[], default_host_id=None, timeout=10, base_url="")

        assert reg._parse_configs_from_settings(settings(llm_service=llm)) == ([], None)

    def test_host_entry_to_tuple(self):
        assert reg._host_entry_to_tuple(None) == (None, None)
        assert reg._host_entry_to_tuple(SimpleNamespace(id="", base_url="u")) == (None, "u")
        assert reg._host_entry_to_tuple({"id": "i", "base_url": "u"}) == ("i", "u")
        assert reg._host_entry_to_tuple(5) == (None, None)


class TestNormalizeLegacyKeys:
    def test_llm_key_with_provider_suffix(self):
        item = reg._normalize_provider_item({"id": "Phoenix", "llm_key_phoenix": " PHX_ENV ", "llm_key_other": "NOPE"})

        assert item["api_key_env"] == "PHX_ENV"

    def test_any_llm_key_variant_is_used_last(self):
        item = reg._normalize_provider_item({"id": "p", "llm_key_extra-1": "EXTRA_ENV", "unrelated": "x"})

        assert item["api_key_env"] == "EXTRA_ENV"

    def test_blank_legacy_keys_do_not_set_anything(self):
        item = reg._normalize_provider_item({"id": "", "llm_key_x": "  ", "llm_keyboard": "no"})

        assert "api_key_env" not in item


class TestRegistryRuntime:
    class FakeProvider:
        def __init__(self, pid, models=None, error=None, enabled=True):
            self.id = pid
            self.enabled = enabled
            self._models = models or []
            self._error = error
            self.initialized = False
            self.closed = False

        async def list_models(self):
            if self._error:
                raise self._error
            return self._models

        async def initialize(self):
            if self._error:
                raise self._error
            self.initialized = True

        async def close(self):
            if self._error:
                raise self._error
            self.closed = True

    async def test_list_all_models_merges_enabled_and_skips_failures(self):
        good = self.FakeProvider("a", models=[ModelInfo("a", "m1")])
        broken = self.FakeProvider("b", error=RuntimeError("down"))
        off = self.FakeProvider("c", models=[ModelInfo("c", "m2")], enabled=False)
        registry = reg.ProviderRegistry({"a": good, "b": broken, "c": off}, "a")

        models = await registry.list_all_models()

        assert [m.path for m in models] == ["a/m1"]

    async def test_initialize_and_close_survive_provider_errors(self):
        good = self.FakeProvider("a")
        broken = self.FakeProvider("b", error=RuntimeError("boom"))
        registry = reg.ProviderRegistry({"a": good, "b": broken}, "a")

        await registry.initialize()
        await registry.close()

        assert good.initialized is True and good.closed is True

    def test_get_with_unknown_id_returns_default(self):
        registry = reg.ProviderRegistry({"a": self.FakeProvider("a")}, "a")

        assert registry.get("zzz").id == "a"
        assert registry.get(None).id == "a"
        assert registry.contains("zzz") is False and registry.contains("a") is True

    def test_resolve_by_unique_configured_model_name(self):
        a, b = self.FakeProvider("a"), self.FakeProvider("b")
        registry = reg.ProviderRegistry({"a": a, "b": b}, "a", {"b": ["only-b", " "], "a": ["shared"], "ghost": ["x"]})
        registry2 = reg.ProviderRegistry({"a": a, "b": b}, "a", {"a": ["shared"], "b": ["shared"]})

        assert registry.resolve("only-b") == (b, "only-b")
        # неоднозначное имя -> default-провайдер
        assert registry2.resolve("shared")[0] is a

    def test_resolve_unknown_provider_prefix_goes_to_default_with_full_path(self):
        a = self.FakeProvider("a")
        registry = reg.ProviderRegistry({"a": a}, "a")

        assert registry.resolve("ghost/model/x") == (a, "ghost/model/x")
        assert registry.resolve("") == (a, "")

    async def test_get_registry_builds_once_and_caches(self, monkeypatch):
        built = []
        sentinel = reg.ProviderRegistry({"a": self.FakeProvider("a")}, "a")

        async def fake_build():
            built.append(1)
            return sentinel

        monkeypatch.setattr(reg, "_build_from_settings", fake_build)

        assert await reg.get_registry() is sentinel
        assert await reg.get_registry() is sentinel
        assert built == [1]
        assert reg.get_registry_sync_or_none() is sentinel

    async def test_get_registry_returns_instance_created_while_waiting_for_the_lock(self, monkeypatch):
        sentinel = reg.ProviderRegistry({"a": self.FakeProvider("a")}, "a")

        class RacingLock:
            async def __aenter__(self):
                reg._registry = sentinel  # другой корутине удалось раньше
                return self

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(reg, "_registry_lock", RacingLock())

        assert await reg.get_registry() is sentinel

    async def test_reload_registry_closes_the_old_one(self, monkeypatch):
        old = self.FakeProvider("old")
        old_registry = reg.ProviderRegistry({"old": old}, "old")
        monkeypatch.setattr(reg, "_registry", old_registry)
        fresh = reg.ProviderRegistry({"new": self.FakeProvider("new")}, "new")

        async def fake_build():
            return fresh

        monkeypatch.setattr(reg, "_build_from_settings", fake_build)

        assert await reg.reload_registry() is fresh
        assert old.closed is True

    async def test_reload_registry_without_previous(self, monkeypatch):
        fresh = reg.ProviderRegistry({"new": self.FakeProvider("new")}, "new")

        async def fake_build():
            return fresh

        monkeypatch.setattr(reg, "_build_from_settings", fake_build)

        assert await reg.reload_registry() is fresh


class TestBuildFromSettings:
    async def test_without_providers_raises_clear_error(self, monkeypatch):
        monkeypatch.setattr(reg, "get_settings", lambda: settings())

        with pytest.raises(RuntimeError, match="Нет настроенных LLM-провайдеров"):
            await reg._build_from_settings()

    async def test_builds_providers_with_configured_models(self, monkeypatch):
        section = [
            {"id": "svc", "kind": "llm-svc", "base_url": "http://svc"},
            {"id": "gw", "kind": "openai-compat", "base_url": "http://gw", "models": {"name": ["m1"]}},
        ]
        monkeypatch.setattr(reg, "get_settings", lambda: settings(llm_providers=section, default_llm_provider="gw"))

        async def no_init(self):
            return None

        monkeypatch.setattr(LlmSvcProvider, "initialize", no_init)
        monkeypatch.setattr(OpenAICompatProvider, "initialize", no_init)

        registry = await reg._build_from_settings()

        assert registry.default_id == "gw"
        assert isinstance(registry.get("svc"), LlmSvcProvider)
        assert registry.resolve("m1")[0].id == "gw"

    async def test_provider_build_errors_are_skipped(self, monkeypatch):
        section = [{"id": "bad", "kind": "openai-compat", "base_url": "http://x"}, {"id": "good", "kind": "openai-compat", "base_url": "http://y"}]
        monkeypatch.setattr(reg, "get_settings", lambda: settings(llm_providers=section))
        original = reg._build_provider

        def flaky(config):
            if config.id == "bad":
                msg = "cannot build"
                raise RuntimeError(msg)
            return original(config)

        async def no_init(self):
            return None

        monkeypatch.setattr(reg, "_build_provider", flaky)
        monkeypatch.setattr(OpenAICompatProvider, "initialize", no_init)

        registry = await reg._build_from_settings()

        assert registry.ids() == ["good"] and registry.default_id == "good"

    async def test_all_builds_failing_raises(self, monkeypatch):
        monkeypatch.setattr(reg, "get_settings", lambda: settings(llm_providers=[{"id": "bad", "kind": "openai", "base_url": ""}]))

        def always_fails(config):
            msg = "nope"
            raise RuntimeError(msg)

        monkeypatch.setattr(reg, "_build_provider", always_fails)

        with pytest.raises(RuntimeError, match="Ни один провайдер не инициализирован"):
            await reg._build_from_settings()


class TestDebugSnapshot:
    def test_snapshot_describes_all_sources_without_secrets(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_ENVP_KIND", "openai")
        monkeypatch.setenv("LLM_PROVIDER_ENVP_API_KEY_ENV", "SECRET_ENV_NAME")
        monkeypatch.setenv("LLM_PROVIDERS_JSON", "[]")
        monkeypatch.setenv("DEFAULT_LLM_PROVIDER", "ENVP")
        yaml_items = [{"id": " from-yaml "}, SimpleNamespace(id="obj"), {"id": ""}, SimpleNamespace(id=None)]
        monkeypatch.setattr(reg, "get_settings", lambda: settings(llm_providers=yaml_items, default_llm_provider="from-yaml"))
        monkeypatch.setattr(
            reg,
            "_registry",
            reg.ProviderRegistry({"x": SimpleNamespace(id="x", enabled=False)}, "x"),
        )

        snap = reg.build_registry_debug_snapshot()

        assert snap["env"]["default_llm_provider"] == "ENVP"
        assert snap["env"]["has_llm_providers_json"] is True
        assert snap["env"]["prefixed_provider_ids"] == ["ENVP"]
        assert snap["env"]["prefixed_keys"] == ["LLM_PROVIDER_ENVP_API_KEY_ENV", "LLM_PROVIDER_ENVP_KIND"]
        assert snap["yaml"] == {"default_llm_provider": "from-yaml", "provider_ids": ["from-yaml", "obj"], "providers_count": 2}
        assert snap["parsed"]["providers"][0]["api_key_env"] == "SECRET_ENV_NAME"
        assert snap["runtime_registry"] == {"default_provider_id": "x", "provider_ids": ["x"]}

    def test_snapshot_survives_yaml_items_that_raise(self, monkeypatch):
        class Weird:
            @property
            def id(self):
                msg = "no id for you"
                raise RuntimeError(msg)

        monkeypatch.setattr(reg, "get_settings", lambda: settings(llm_providers=[Weird(), {"id": "ok", "kind": "openai"}]))

        snap = reg.build_registry_debug_snapshot()

        assert snap["yaml"]["provider_ids"] == ["ok"]

    def test_snapshot_without_registry_or_yaml_list(self, monkeypatch):
        monkeypatch.setattr(reg, "get_settings", lambda: settings(llm_providers=None))

        snap = reg.build_registry_debug_snapshot()

        assert snap["runtime_registry"] is None
        assert snap["yaml"]["provider_ids"] == []
        assert snap["env"]["default_llm_provider"] is None


class TestBuildProviderKinds:
    def test_unknown_kind_falls_back_to_openai_compat(self):
        config = LLMProviderConfig.model_construct(id="x", kind="weird", base_url="http://x", extra={}, models=[], timeout=5.0, enabled=True, api_key_env=None, static_model=None)

        provider = reg._build_provider(config)

        assert type(provider) is OpenAICompatProvider

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
        provider = reg._build_provider(cfg("p", kind, "http://x"))

        assert type(provider).__name__ == class_name
