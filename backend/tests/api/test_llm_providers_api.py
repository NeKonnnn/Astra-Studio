"""routes/llm_providers: read-only список провайдеров, их health, модели и диагностика."""

from types import SimpleNamespace

import pytest

from backend.llm_providers.base import LLMProviderConfig, ModelInfo, ProviderHealth
from backend.llm_providers.registry import _build_provider
from backend.routes import llm_providers as module

pytestmark = pytest.mark.unit

BASE = "/api/llm-providers"


class FakeRegistry:
    def __init__(self, providers, default):
        self._providers = {p.id: p for p in providers}
        self.default_id = default

    def all(self, *, include_disabled=False):
        return [p for p in self._providers.values() if include_disabled or p.enabled]

    def contains(self, pid):
        return pid in self._providers

    def get(self, pid):
        return self._providers[pid]


def _provider(pid, kind="vllm", *, base_url="http://llm.test", enabled=True, **extra):
    cfg = LLMProviderConfig(id=pid, kind=kind, base_url=base_url, enabled=enabled, **extra)
    return _build_provider(cfg)


@pytest.fixture
def registry(monkeypatch):
    holder = SimpleNamespace(value=None)

    async def get_registry():
        return holder.value

    monkeypatch.setattr(module, "get_registry", get_registry)
    return holder


class TestHint:
    def test_required_only_for_external_kinds(self):
        assert module._hint_for_provider("openai", "openai")["required"] is True
        assert module._hint_for_provider("anthropic", "anthropic")["required"] is True
        assert module._hint_for_provider("local", "vllm")["required"] is False

    def test_env_name_and_shell_snippets(self):
        hint = module._hint_for_provider("my-gw", "openrouter")

        assert hint["env_name"] == "LLM_PROVIDER_MY_GW_API_KEY"
        assert hint["bash"] == 'export LLM_PROVIDER_MY_GW_API_KEY="<ваш-ключ>"'
        assert hint["powershell"] == '$env:LLM_PROVIDER_MY_GW_API_KEY="<ваш-ключ>"'
        assert "sk-" not in str(hint)


class TestListProviders:
    async def test_lists_enabled_providers_with_the_default_marked(self, make_client, registry):
        registry.value = FakeRegistry([_provider("a"), _provider("b", "ollama")], default="a")

        response = await make_client(module.router).get(BASE)

        body = response.json()
        assert body["default_provider_id"] == "a"
        assert [p["id"] for p in body["providers"]] == ["a", "b"]
        first = body["providers"][0]
        assert first["kind"] == "vllm"
        assert "capabilities" in first and "secret_hint" in first
        assert "health" not in first

    async def test_disabled_providers_are_hidden_unless_requested(self, make_client, registry):
        registry.value = FakeRegistry([_provider("a"), _provider("off", enabled=False)], default="a")
        client = make_client(module.router)

        hidden = await client.get(BASE)
        shown = await client.get(BASE, params={"include_disabled": True})

        assert [p["id"] for p in hidden.json()["providers"]] == ["a"]
        assert [p["id"] for p in shown.json()["providers"]] == ["a", "off"]

    async def test_rag_gateways_are_not_listed(self, make_client, registry):
        gateway = _provider("rag", extra={"chat_visible": False})
        registry.value = FakeRegistry([_provider("a"), gateway], default="a")

        response = await make_client(module.router).get(BASE)

        assert [p["id"] for p in response.json()["providers"]] == ["a"]

    async def test_health_is_added_on_request(self, make_client, registry):
        provider = _provider("a")

        async def health():
            return ProviderHealth(healthy=True, loaded_models=["m1"])

        provider.health = health
        registry.value = FakeRegistry([provider], default="a")

        response = await make_client(module.router).get(BASE, params={"include_health": True})

        assert response.json()["providers"][0]["health"] == {"healthy": True, "error": None, "loaded_models": ["m1"]}

    async def test_health_failure_is_reported_per_provider(self, make_client, registry):
        broken, fine = _provider("broken"), _provider("fine")

        async def boom():
            raise RuntimeError("timeout")

        async def ok():
            return ProviderHealth(healthy=True)

        broken.health, fine.health = boom, ok
        registry.value = FakeRegistry([broken, fine], default="fine")

        response = await make_client(module.router).get(BASE, params={"include_health": True})

        health = {p["id"]: p["health"] for p in response.json()["providers"]}
        assert health["broken"]["healthy"] is False and "timeout" not in health["broken"]["error"]
        assert health["fine"]["healthy"] is True

    async def test_registry_failure(self, make_client, monkeypatch):
        async def boom():
            raise RuntimeError("no registry")

        monkeypatch.setattr(module, "get_registry", boom)

        response = await make_client(module.router).get(BASE)

        assert response.status_code == 500
        assert "no registry" not in response.json()["detail"]


class TestSecrets:
    async def test_api_key_value_is_never_returned(self, make_client, registry, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_OAI_API_KEY", "sk-live-0123456789abcdef-SECRET")
        registry.value = FakeRegistry([_provider("oai", "openai", base_url="https://api.openai.com")], default="oai")
        client = make_client(module.router)

        listing = await client.get(BASE)
        detail = await client.get(BASE + "/oai")

        for response in (listing, detail):
            assert "SECRET" not in response.text
            assert "0123456789abcdef" not in response.text
        info = detail.json()
        assert info["api_key_set"] is True
        assert info["api_key_env"] == "LLM_PROVIDER_OAI_API_KEY"
        assert info["api_key_preview"].startswith("sk-l")

    async def test_unset_key_is_reported_as_missing(self, make_client, registry, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER_OAI_API_KEY", raising=False)
        registry.value = FakeRegistry([_provider("oai", "openai", base_url="https://api.openai.com")], default="oai")

        info = (await make_client(module.router).get(BASE + "/oai")).json()

        assert info["api_key_set"] is False
        assert info["api_key_required"] is True
        assert info["api_key_preview"] is None

    async def test_debug_snapshot_contains_names_but_no_secret_values(self, make_client, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_GW_KIND", "openai-compat")
        monkeypatch.setenv("LLM_PROVIDER_GW_BASE_URL", "http://gw.test")
        monkeypatch.setenv("LLM_PROVIDER_GW_API_KEY_ENV", "GW_SECRET_VAR")
        monkeypatch.setenv("GW_SECRET_VAR", "sk-do-not-leak-me")
        monkeypatch.setenv("LLM_PROVIDER_OTHER_API_KEY", "sk-also-secret")

        response = await make_client(module.router).get(BASE + "/debug")

        assert response.status_code == 200
        body = response.json()
        assert "sk-do-not-leak-me" not in response.text
        assert "sk-also-secret" not in response.text
        assert "GW" in body["env"]["prefixed_provider_ids"]
        assert "LLM_PROVIDER_GW_KIND" in body["env"]["prefixed_keys"]

    async def test_debug_route_is_not_shadowed_by_the_provider_route(self, make_client, registry):
        registry.value = FakeRegistry([_provider("a")], default="a")

        response = await make_client(module.router).get(BASE + "/debug")

        assert response.status_code == 200
        assert "env" in response.json()

    async def test_debug_failure(self, make_client, monkeypatch):
        def boom():
            raise RuntimeError("settings broken")

        monkeypatch.setattr(module, "build_registry_debug_snapshot", boom)

        response = await make_client(module.router).get(BASE + "/debug")

        assert response.status_code == 500


class TestProviderDetailAndModels:
    async def test_detail(self, make_client, registry):
        registry.value = FakeRegistry([_provider("a", static_model="qwen")], default="a")

        response = await make_client(module.router).get(BASE + "/a")

        assert response.status_code == 200
        assert (response.json()["id"], response.json()["static_model"]) == ("a", "qwen")

    @pytest.mark.parametrize("path", ["/ghost", "/ghost/models"])
    async def test_unknown_provider_is_404(self, make_client, registry, path):
        registry.value = FakeRegistry([_provider("a")], default="a")

        response = await make_client(module.router).get(BASE + path)

        assert response.status_code == 404
        assert "ghost" in response.json()["detail"]

    async def test_models_listing(self, make_client, registry):
        provider = _provider("a")

        async def list_models():
            return [
                ModelInfo(provider_id="a", model_id="qwen", display_name="Qwen", extra={"size": 1}),
                ModelInfo(provider_id="a", model_id="plain"),
            ]

        provider.list_models = list_models
        registry.value = FakeRegistry([provider], default="a")

        response = await make_client(module.router).get(BASE + "/a/models")

        body = response.json()
        assert (body["provider_id"], body["provider_kind"]) == ("a", "vllm")
        assert body["models"][0] == {"id": "qwen", "path": "a/qwen", "provider_id": "a", "display_name": "Qwen", "extra": {"size": 1}}
        assert body["models"][1]["display_name"] == "plain"

    async def test_provider_failure_is_a_bad_gateway(self, make_client, registry):
        provider = _provider("a")

        async def boom():
            raise RuntimeError("upstream down")

        provider.list_models = boom
        registry.value = FakeRegistry([provider], default="a")

        response = await make_client(module.router).get(BASE + "/a/models")

        assert response.status_code == 502

    async def test_registry_failure_on_detail(self, make_client, monkeypatch):
        async def boom():
            raise RuntimeError("no registry")

        monkeypatch.setattr(module, "get_registry", boom)

        assert (await make_client(module.router).get(BASE + "/a")).status_code == 500
        assert (await make_client(module.router).get(BASE + "/a/models")).status_code == 500
