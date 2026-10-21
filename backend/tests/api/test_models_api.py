"""routes/models: каталог моделей из провайдеров, загрузка модели, настройки генерации."""

from types import SimpleNamespace

import pytest

from backend.llm_providers.base import ModelInfo, ProviderCapabilities
from backend.routes import models as module
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

BASE = "/api/models"


class FakeProvider:
    def __init__(self, pid, models=(), *, kind="vllm", extra=None, fail=False, load_ok=True):
        self.id = pid
        self.kind = kind
        self.enabled = True
        self.capabilities = ProviderCapabilities(hot_swap=True, multi_loaded=False, streaming=True, vision=True)
        self.config = SimpleNamespace(extra=extra or {})
        self._models = list(models)
        self._fail = fail
        self._load_ok = load_ok
        self.loaded = []

    async def list_models(self):
        if self._fail:
            raise RuntimeError("provider down")
        return self._models

    async def ensure_model_loaded(self, model_id):
        self.loaded.append(model_id)
        return self._load_ok


class FakeRegistry:
    def __init__(self, providers, default):
        self._providers = {p.id: p for p in providers}
        self.default_id = default

    def all(self):
        return list(self._providers.values())

    def contains(self, pid):
        return pid in self._providers

    def get(self, pid):
        return self._providers.get(pid) or self._providers[self.default_id]

    def resolve(self, path):
        head, _, tail = (path or "").partition("/")
        if head in self._providers:
            return self._providers[head], tail
        return self._providers[self.default_id], path


def _info(pid, mid, **kw):
    return ModelInfo(provider_id=pid, model_id=mid, **kw)


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(module, "_models_available_cache_payload", None)
    monkeypatch.setattr(module, "_models_available_cache_ts", 0.0)
    monkeypatch.setattr(module, "_models_available_cache_seconds", lambda: 60)


@pytest.fixture
def registry(monkeypatch):
    holder = SimpleNamespace(value=None)

    async def get_registry():
        return holder.value

    import backend.llm_providers as llm_providers

    monkeypatch.setattr(llm_providers, "get_registry", get_registry)
    return holder


class TestChatModelFilter:
    @pytest.mark.parametrize("model_id", ["vllm/qwen3-8b", "gpt-4o", "llama-3"])
    def test_chat_models_pass(self, model_id):
        assert module._is_chat_model(model_id) is True

    @pytest.mark.parametrize("model_id", ["embed/FRIDA", "bge-m3-embedding", "rerank/ms-marco", "bge-reranker"])
    def test_embedders_and_rerankers_are_hidden(self, model_id):
        assert module._is_chat_model(model_id) is False

    def test_provider_visibility_is_decided_by_config_only(self):
        assert module._is_chat_provider(FakeProvider("a")) is True
        assert module._is_chat_provider(FakeProvider("a", extra={"chat_visible": False})) is False
        assert module._is_chat_provider(FakeProvider("a", extra={"chat_visible": True})) is True
        assert module._is_chat_provider(object()) is True


class TestAvailableModels:
    async def test_builds_rows_for_all_chat_providers(self, make_client, registry):
        registry.value = FakeRegistry(
            [
                FakeProvider("main", [_info("main", "qwen3", display_name="Qwen 3", context_size=32000, extra={"size_mb": 5, "owned_by": "org"})]),
                FakeProvider("other", [_info("other", "gpt")], kind="openai"),
            ],
            default="main",
        )

        response = await make_client(module.router).get(BASE + "/available")

        rows = {r["path"]: r for r in response.json()["models"]}
        assert set(rows) == {"main/qwen3", "other/gpt"}
        main = rows["main/qwen3"]
        assert (main["display_name"], main["context_size"], main["provider_default"]) == ("Qwen 3", 32000, True)
        assert (main["size_mb"], main["owned_by"], main["provider_kind"]) == (5, "org", "vllm")
        assert main["capabilities"] == {"hot_swap": True, "multi_loaded": False, "streaming": True, "vision": True}
        assert rows["other/gpt"]["provider_default"] is False
        assert rows["other/gpt"]["display_name"] == "gpt"
        assert "warning" not in response.json()

    async def test_embedders_are_not_offered_for_chat(self, make_client, registry):
        registry.value = FakeRegistry(
            [FakeProvider("gw", [_info("gw", "vllm/qwen"), _info("gw", "embed/FRIDA"), _info("gw", "rerank/x")])], default="gw"
        )

        response = await make_client(module.router).get(BASE + "/available")

        assert [m["name"] for m in response.json()["models"]] == ["vllm/qwen"]

    async def test_hidden_provider_is_skipped(self, make_client, registry):
        registry.value = FakeRegistry(
            [FakeProvider("rag", [_info("rag", "m")], extra={"chat_visible": False}), FakeProvider("chat", [_info("chat", "m2")])],
            default="chat",
        )

        response = await make_client(module.router).get(BASE + "/available")

        assert [m["provider_id"] for m in response.json()["models"]] == ["chat"]

    async def test_failing_provider_adds_a_warning_but_keeps_the_rest(self, make_client, registry):
        registry.value = FakeRegistry(
            [FakeProvider("bad", fail=True), FakeProvider("ok", [_info("ok", "m")])], default="ok"
        )

        response = await make_client(module.router).get(BASE + "/available")

        body = response.json()
        assert [m["path"] for m in body["models"]] == ["ok/m"]
        assert body["warning"] == "bad: list_models error"

    async def test_result_is_cached(self, make_client, registry):
        provider = FakeProvider("p", [_info("p", "m")])
        registry.value = FakeRegistry([provider], default="p")
        client = make_client(module.router)

        first = await client.get(BASE + "/available")
        provider._models = [_info("p", "changed")]
        second = await client.get(BASE + "/available")

        assert first.json() == second.json()

    async def test_empty_result_is_not_served_from_cache(self, make_client, registry):
        provider = FakeProvider("p", [])
        registry.value = FakeRegistry([provider], default="p")
        client = make_client(module.router)

        assert (await client.get(BASE + "/available")).json()["models"] == []
        provider._models = [_info("p", "m")]

        assert [m["name"] for m in (await client.get(BASE + "/available")).json()["models"]] == ["m"]

    async def test_zero_cache_time_always_refreshes(self, make_client, registry, monkeypatch):
        monkeypatch.setattr(module, "_models_available_cache_seconds", lambda: 0)
        provider = FakeProvider("p", [_info("p", "a")])
        registry.value = FakeRegistry([provider], default="p")
        client = make_client(module.router)

        await client.get(BASE + "/available")
        provider._models = [_info("p", "b")]

        assert [m["name"] for m in (await client.get(BASE + "/available")).json()["models"]] == ["b"]

    async def test_aliases_return_the_same_catalog(self, make_client, registry):
        registry.value = FakeRegistry([FakeProvider("p", [_info("p", "m")])], default="p")
        client = make_client(module.router)

        a = (await client.get(BASE)).json()
        b = (await client.get(BASE + "/")).json()
        c = (await client.get(BASE + "/available")).json()

        assert a == b == c

    async def test_registry_failure(self, make_client, monkeypatch):
        import backend.llm_providers as llm_providers

        async def boom():
            raise RuntimeError("no registry")

        monkeypatch.setattr(llm_providers, "get_registry", boom)

        response = await make_client(module.router).get(BASE + "/available")

        assert response.status_code == 500


class TestLoadModel:
    @pytest.fixture(autouse=True)
    def _persist(self, monkeypatch):
        self.saved = []
        monkeypatch.setattr(module, "save_app_settings", lambda data: self.saved.append(data))

    async def _load(self, make_client, path, user=ALICE):
        return await make_client(module.router, user=user).post(BASE + "/load", json={"model_path": path})

    @pytest.mark.parametrize("path", ["", "   "])
    async def test_empty_path(self, make_client, registry, path):
        response = await self._load(make_client, path)

        assert response.json() == {"message": "model_path пуст", "success": False}

    async def test_directory_path_is_refused(self, make_client, registry, tmp_path):
        response = await self._load(make_client, str(tmp_path))

        assert response.json()["success"] is False
        assert "директории" in response.json()["message"]

    async def test_loads_through_the_resolved_provider_and_persists_the_choice(self, make_client, registry):
        provider = FakeProvider("main", [_info("main", "qwen3")])
        registry.value = FakeRegistry([provider], default="main")

        response = await self._load(make_client, "main/qwen3")

        assert response.json() == {"message": "Модель успешно загружена", "success": True}
        assert provider.loaded == ["qwen3"]
        assert self.saved == [
            {"current_model_path": "main/qwen3", "current_model_name": "qwen3", "current_model_status": "loaded"}
        ]

    async def test_provider_id_alone_picks_its_first_model(self, make_client, registry):
        provider = FakeProvider("main", [_info("main", "first"), _info("main", "second")])
        registry.value = FakeRegistry([provider], default="other")
        registry.value._providers["other"] = FakeProvider("other")
        registry.value.resolve = lambda path: (provider, "")

        response = await self._load(make_client, "main")

        assert response.json()["success"] is True
        assert provider.loaded == ["first"]

    async def test_provider_without_models(self, make_client, registry):
        provider = FakeProvider("main", [])
        registry.value = FakeRegistry([provider], default="main")
        registry.value.resolve = lambda path: (provider, "")

        response = await self._load(make_client, "main")

        assert response.json() == {"message": "Не удалось определить model_id", "success": False}

    async def test_provider_refuses_to_load(self, make_client, registry):
        registry.value = FakeRegistry([FakeProvider("main", load_ok=False)], default="main")

        response = await self._load(make_client, "main/m")

        assert response.json() == {"message": "Не удалось загрузить модель", "success": False}
        assert self.saved == []

    async def test_errors_are_reported_in_the_message_without_leaking_details(self, make_client, registry):
        class Boom(FakeProvider):
            async def ensure_model_loaded(self, model_id):
                raise RuntimeError("token sk-secret rejected")

        registry.value = FakeRegistry([Boom("main")], default="main")

        response = await self._load(make_client, "main/m")

        assert response.status_code == 200
        assert response.json()["success"] is False
        assert "sk-secret" not in response.text  # CWE-209

    async def test_request_validation(self, make_client, registry):
        response = await make_client(module.router).post(BASE + "/load", json={})

        assert response.status_code == 422


class TestCurrentModel:
    async def test_returns_info_and_remembers_it(self, make_client, monkeypatch):
        saved = []
        monkeypatch.setattr(module, "get_model_info", lambda: {"path": "main/qwen", "name": "Qwen", "status": "loaded"})
        monkeypatch.setattr(module, "save_app_settings", lambda d: saved.append(d))

        response = await make_client(module.router).get(BASE + "/current")

        assert response.json()["path"] == "main/qwen"
        assert saved == [{"current_model_path": "main/qwen", "current_model_name": "Qwen", "current_model_status": "loaded"}]

    async def test_falls_back_to_saved_settings_when_the_file_exists(self, make_client, monkeypatch, tmp_path):
        weights = tmp_path / "m.gguf"
        weights.write_bytes(b"x" * 2048)
        monkeypatch.setattr(module, "get_model_info", lambda: (_ for _ in ()).throw(RuntimeError("no model")))
        monkeypatch.setattr(module, "load_app_settings", lambda: {"current_model_path": str(weights), "current_model_name": "Saved"})

        response = await make_client(module.router).get(BASE + "/current")

        assert response.json()["status"] == "loaded_from_settings"
        assert response.json()["name"] == "Saved"
        assert response.json()["size"] == 2048

    async def test_not_loaded(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "get_model_info", None)
        monkeypatch.setattr(module, "load_app_settings", lambda: {})

        response = await make_client(module.router).get(BASE + "/current")

        assert response.json() == {"name": "Модель не загружена", "path": "", "status": "not_loaded"}


class TestUserSettings:
    @pytest.fixture
    def store(self, monkeypatch):
        calls = []

        async def get(uid):
            calls.append(("get", uid))
            return {"temperature": 0.5}

        async def save(uid, data):
            calls.append(("save", uid, data))
            return data

        async def reset(uid):
            calls.append(("reset", uid))
            return {"temperature": 0.7}

        monkeypatch.setattr(module, "get_user_model_settings", get)
        monkeypatch.setattr(module, "save_user_model_settings", save)
        monkeypatch.setattr(module, "reset_user_model_settings", reset)
        return calls

    @pytest.mark.parametrize(("method", "path"), [("GET", "/settings"), ("PUT", "/settings"), ("POST", "/settings/reset")])
    async def test_require_authentication(self, make_client, store, method, path):
        response = await make_client(module.router, user=None).request(method, BASE + path, json={} if method != "GET" else None)

        assert response.status_code in (401, 403)
        assert store == []

    async def test_read_uses_the_callers_id(self, make_client, store):
        response = await make_client(module.router).get(BASE + "/settings")

        assert response.json() == {"temperature": 0.5}
        assert store == [("get", "alice")]

    async def test_read_falls_back_to_defaults_on_errors(self, make_client, monkeypatch):
        calls = []

        async def get(uid):
            calls.append(uid)
            if uid is not None:
                raise RuntimeError("db")
            return {"temperature": 0.7}

        monkeypatch.setattr(module, "get_user_model_settings", get)

        response = await make_client(module.router).get(BASE + "/settings")

        assert response.json() == {"temperature": 0.7}
        assert calls == ["alice", None]

    async def test_update_saves_validated_settings_for_the_caller(self, make_client, store):
        response = await make_client(module.router).put(BASE + "/settings", json={"temperature": 0.3, "context_size": 4096})

        assert response.status_code == 200
        (call,) = store
        assert call[:2] == ("save", "alice")
        assert call[2]["temperature"] == 0.3 and call[2]["context_size"] == 4096
        assert response.json()["success"] is True

    async def test_update_rejects_invalid_values(self, make_client, store):
        response = await make_client(module.router).put(BASE + "/settings", json={"temperature": "hot"})

        assert response.status_code == 422
        assert store == []

    async def test_reset(self, make_client, store):
        response = await make_client(module.router, user={**ALICE, "user_id": "bob", "username": "bob"}).post(BASE + "/settings/reset")

        assert response.json()["settings"] == {"temperature": 0.7}
        assert store == [("reset", "bob")]


class TestRecommendedSettings:
    async def test_returns_recommendations(self, make_client, monkeypatch):
        fake = SimpleNamespace(get_recommended_settings=lambda: {"temperature": 0.7}, get_max_values=lambda: {"temperature": 2})
        monkeypatch.setattr(module, "model_settings", fake)

        response = await make_client(module.router).get(BASE + "/settings/recommended")

        assert response.json() == {"recommended": {"temperature": 0.7}, "max_values": {"temperature": 2}}

    async def test_unavailable_agent(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "model_settings", None)

        response = await make_client(module.router).get(BASE + "/settings/recommended")

        assert response.status_code == 503
