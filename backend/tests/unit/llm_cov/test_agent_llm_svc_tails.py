"""agent_llm_svc: fallback-ветки перезагрузки модели и сохранения настроек (хвосты)."""

import asyncio
import os
from types import SimpleNamespace

import pytest

from backend import agent_llm_svc as als

pytestmark = pytest.mark.unit


@pytest.fixture
def settings_env(monkeypatch, tmp_path):
    target = tmp_path / "primary" / "llm_settings.json"
    fallback = tmp_path / "fallback" / "llm_settings.json"
    monkeypatch.setattr(als, "resolve_llm_settings_file_path", lambda: target)
    monkeypatch.setattr(als, "llm_settings_seed_paths", lambda: [])
    monkeypatch.setattr(als, "llm_settings_defaults", lambda: {"temperature": 0.5})
    monkeypatch.setattr(als, "llm_settings_file_fallbacks", lambda path: [path, str(fallback)])
    monkeypatch.setattr(als, "is_settings_key", lambda key: not key.startswith("_"))
    return SimpleNamespace(target=target, fallback=fallback)


class TestSaveFallback:
    def test_oserror_on_primary_switches_to_fallback_and_remembers_it(self, settings_env, monkeypatch):
        s = als.ModelSettings()
        real_makedirs = os.makedirs

        def flaky_makedirs(path, *a, **k):
            if str(path).endswith("primary"):
                raise PermissionError("read-only volume")
            return real_makedirs(path, *a, **k)

        monkeypatch.setattr(als.os, "makedirs", flaky_makedirs)

        assert s.set("temperature", 0.9) is True
        assert s.settings_file == str(settings_env.fallback)
        assert settings_env.fallback.is_file() and not settings_env.target.exists()

    def test_unexpected_error_falls_through_to_next_target(self, settings_env, monkeypatch):
        s = als.ModelSettings()
        calls = []
        real_dump = als.json.dump

        def dump_once_broken(obj, fp, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("not serialisable")
            return real_dump(obj, fp, **kw)

        monkeypatch.setattr(als.json, "dump", dump_once_broken)

        assert s.save_settings() is True
        assert s.settings_file == str(settings_env.fallback)

    def test_set_unknown_key_and_non_persist(self, settings_env):
        s = als.ModelSettings()

        assert s.set("unknown", 1) is False
        assert s.set("temperature", 0.1, persist=False) is True
        assert not settings_env.target.exists()
        assert s.update_many({"unknown": 1}) is False
        assert s.update_many({"temperature": 0.2}, persist=False) is True and s.get("temperature") == 0.2


@pytest.fixture
def loop_guard():
    """Возвращает глобальный event loop потока в исходное состояние после теста."""
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None
    yield
    asyncio.set_event_loop(previous)


@pytest.fixture
def broken_registry(monkeypatch):
    """Реестр провайдеров недоступен -> reload_model_by_path падает в legacy-логику."""
    import backend.llm_providers as providers

    async def get_registry():
        msg = "registry is down"
        raise RuntimeError(msg)

    monkeypatch.setattr(providers, "get_registry", get_registry)
    monkeypatch.setattr(als, "_selected_model_name", None)


class FakeClient:
    llm_hosts = {"h1": object()}
    default_llm_host = "h1"

    def __init__(self, ok=True):
        self.ok = ok
        self.loaded = []

    async def load_model_if_needed(self, mid, host_id=None):
        self.loaded.append((mid, host_id))
        return self.ok


def use_service(monkeypatch, client, resolved=("h1", "")):
    service = SimpleNamespace(model_name="old", client=client)

    async def get_service():
        return service

    monkeypatch.setattr(als, "get_llm_service", get_service)
    monkeypatch.setattr(als, "resolve_llm_host_and_model_for_svc", lambda *a: resolved)
    return service


class TestLegacyFallbackBranches:
    def test_directory_is_rejected(self, broken_registry, tmp_path, loop_guard):
        assert als.reload_model_by_path(str(tmp_path)) is False

    def test_local_gguf_file_is_accepted_with_a_warning(self, broken_registry, tmp_path, loop_guard):
        model = tmp_path / "m.gguf"
        model.write_text("x")

        assert als.reload_model_by_path(str(model)) is True

    def test_known_model_id_is_loaded_and_selected(self, broken_registry, monkeypatch, loop_guard):
        client = FakeClient()
        service = use_service(monkeypatch, client, resolved=("h1", ""))
        monkeypatch.setattr(als, "resolve_llm_svc_model_id_for_request", lambda path, default: "resolved-id")

        assert als.reload_model_by_path("some/model") is True
        assert client.loaded == [("resolved-id", "h1")]
        assert service.model_name == "resolved-id" and als._selected_model_name == "resolved-id"

    def test_resolver_model_id_wins_over_requested(self, broken_registry, monkeypatch, loop_guard):
        client = FakeClient()
        use_service(monkeypatch, client, resolved=("h2", "from-host"))
        monkeypatch.setattr(als, "resolve_llm_svc_model_id_for_request", lambda path, default: "resolved-id")

        assert als.reload_model_by_path("some/model") is True
        assert client.loaded == [("from-host", "h2")]

    def test_failed_load_returns_false_and_keeps_selection(self, broken_registry, monkeypatch, loop_guard):
        client = FakeClient(ok=False)
        use_service(monkeypatch, client)
        monkeypatch.setattr(als, "resolve_llm_svc_model_id_for_request", lambda path, default: "resolved-id")

        assert als.reload_model_by_path("some/model") is False
        assert als._selected_model_name is None

    def test_load_error_returns_false(self, broken_registry, monkeypatch, loop_guard):
        monkeypatch.setattr(als, "resolve_llm_svc_model_id_for_request", lambda path, default: "resolved-id")

        async def get_service():
            msg = "llm-svc down"
            raise RuntimeError(msg)

        monkeypatch.setattr(als, "get_llm_service", get_service)

        assert als.reload_model_by_path("some/model") is False

    def test_unresolvable_path_is_just_acknowledged(self, broken_registry, monkeypatch, loop_guard):
        monkeypatch.setattr(als, "resolve_llm_svc_model_id_for_request", lambda path, default: "")

        assert als.reload_model_by_path("some/model") is True

    def test_llm_svc_scheme_inside_running_loop_uses_worker_thread(self, monkeypatch, loop_guard):
        client = FakeClient()
        use_service(monkeypatch, client, resolved=("h1", "m"))

        async def call():
            return als.reload_model_by_path("llm-svc://m")

        assert asyncio.run(call()) is True
        assert client.loaded == [("m", "h1")]

    def test_resolved_branch_inside_running_loop_uses_worker_thread(self, broken_registry, monkeypatch, loop_guard):
        client = FakeClient()
        use_service(monkeypatch, client, resolved=("h1", ""))
        monkeypatch.setattr(als, "resolve_llm_svc_model_id_for_request", lambda path, default: "rid")

        async def call():
            return als.reload_model_by_path("x/y")

        assert asyncio.run(call()) is True
        assert client.loaded == [("rid", "h1")]

    def test_missing_event_loop_is_recreated(self, broken_registry, monkeypatch, loop_guard):
        client = FakeClient()
        use_service(monkeypatch, client, resolved=("h1", ""))
        monkeypatch.setattr(als, "resolve_llm_svc_model_id_for_request", lambda path, default: "rid")
        created = []
        real_new = asyncio.new_event_loop

        def no_loop():
            msg = "There is no current event loop"
            raise RuntimeError(msg)

        def tracking_new():
            loop = real_new()
            created.append(loop)
            return loop

        monkeypatch.setattr(asyncio, "get_event_loop", no_loop)
        monkeypatch.setattr(asyncio, "new_event_loop", tracking_new)

        try:
            assert als.reload_model_by_path("x/y") is True
        finally:
            for loop in created:
                loop.close()

        assert created and client.loaded == [("rid", "h1")]


class TestProviderPathLoopHandling:
    @pytest.fixture
    def registry(self, monkeypatch):
        import backend.llm_providers as providers

        class Provider:
            id = "prov"
            ensured = []

            async def ensure_model_loaded(self, model_id):
                self.ensured.append(model_id)
                return True

        class Registry:
            def resolve(self, path):
                return Provider(), "m1"

        async def get_registry():
            return Registry()

        monkeypatch.setattr(providers, "get_registry", get_registry)
        monkeypatch.setattr(als, "_selected_model_name", None)
        return Provider

    def test_inside_running_loop_a_worker_thread_runs_the_check(self, registry, loop_guard):
        async def call():
            return als.reload_model_by_path("prov/m1")

        assert asyncio.run(call()) is True
        assert registry.ensured == ["m1"]

    def test_without_current_loop_a_new_one_is_created(self, registry, monkeypatch, loop_guard):
        created = []
        real_new = asyncio.new_event_loop

        def no_loop():
            msg = "There is no current event loop"
            raise RuntimeError(msg)

        def tracking_new():
            loop = real_new()
            created.append(loop)
            return loop

        monkeypatch.setattr(asyncio, "get_event_loop", no_loop)
        monkeypatch.setattr(asyncio, "new_event_loop", tracking_new)

        try:
            assert als.reload_model_by_path("prov/m1") is True
        finally:
            for loop in created:
                loop.close()

        assert created


class TestModelInfoTails:
    def run(self, monkeypatch, health, selected=None):
        service = SimpleNamespace(model_name="svc-model", client=SimpleNamespace(
            llm_hosts={}, default_llm_host="h", health_check=None))

        async def health_check(host_id=None):
            return health

        service.client.health_check = health_check

        async def get_service():
            return service

        monkeypatch.setattr(als, "get_llm_service", get_service)
        monkeypatch.setattr(als, "resolve_llm_host_and_model_for_svc", lambda *a: ("h", ""))
        monkeypatch.setattr(als, "_selected_model_name", selected)
        return als.get_model_info()

    def test_health_model_name_is_used_without_selection(self, monkeypatch, loop_guard):
        info = self.run(monkeypatch, {"status": "healthy", "model_name": "from-health", "model_loaded": True})

        assert info["path"] == "" and info["name"] == "Модель не загружена"
        assert info["metadata"]["general.name"] == "from-health"
        assert info["loaded"] is True

    def test_service_model_name_is_the_last_resort(self, monkeypatch, loop_guard):
        info = self.run(monkeypatch, {"status": "healthy"})

        assert info["metadata"]["general.name"] == "svc-model"

    def test_running_loop_uses_worker_thread(self, monkeypatch, loop_guard):
        async def call():
            return self.run(monkeypatch, {"status": "healthy"}, selected="picked")

        info = asyncio.run(call())

        assert info["path"] == "llm-svc://picked" and info["name"] == "picked"


class TestMissingLoopRecreated:
    @pytest.fixture
    def no_current_loop(self, monkeypatch):
        created = []
        real_new = asyncio.new_event_loop

        def no_loop():
            msg = "There is no current event loop"
            raise RuntimeError(msg)

        def tracking_new():
            loop = real_new()
            created.append(loop)
            return loop

        monkeypatch.setattr(asyncio, "get_event_loop", no_loop)
        monkeypatch.setattr(asyncio, "new_event_loop", tracking_new)
        yield created
        for loop in created:
            loop.close()

    def test_llm_svc_scheme_creates_a_loop(self, monkeypatch, no_current_loop, loop_guard):
        client = FakeClient()
        use_service(monkeypatch, client, resolved=("h1", "m"))

        assert als.reload_model_by_path("llm-svc://m") is True
        assert no_current_loop and client.loaded == [("m", "h1")]

    def test_model_info_creates_a_loop(self, monkeypatch, no_current_loop, loop_guard):
        async def health_check(host_id=None):
            return {"status": "healthy", "model_name": "n"}

        service = SimpleNamespace(
            model_name="svc", client=SimpleNamespace(llm_hosts={}, default_llm_host="h", health_check=health_check)
        )

        async def get_service():
            return service

        monkeypatch.setattr(als, "get_llm_service", get_service)
        monkeypatch.setattr(als, "resolve_llm_host_and_model_for_svc", lambda *a: ("h", ""))
        monkeypatch.setattr(als, "_selected_model_name", None)

        info = als.get_model_info()

        assert no_current_loop and info["metadata"]["general.name"] == "n"
