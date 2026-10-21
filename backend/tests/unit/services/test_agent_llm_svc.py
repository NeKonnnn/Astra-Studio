"""agent_llm_svc: настройки модели (файл + fallback), выбор модели и маршрутизация ask_agent."""

import json
from types import SimpleNamespace

import httpx
import pytest

from backend import agent_llm_svc as als

pytestmark = pytest.mark.unit

DEFAULTS = {"context_size": 4096, "output_tokens": 512, "temperature": 0.5, "verbose": False}


@pytest.fixture
def settings_env(monkeypatch, tmp_path):
    target = tmp_path / "mem" / "llm_settings.json"
    seed = tmp_path / "seed.json"
    monkeypatch.setattr(als, "resolve_llm_settings_file_path", lambda: target)
    monkeypatch.setattr(als, "llm_settings_seed_paths", lambda: [seed])
    monkeypatch.setattr(als, "llm_settings_defaults", lambda: dict(DEFAULTS))
    monkeypatch.setattr(als, "llm_settings_file_fallbacks", lambda path: [path, str(tmp_path / "fallback" / "llm_settings.json")])
    monkeypatch.setattr(als, "is_settings_key", lambda key: not key.startswith("_"))
    return SimpleNamespace(target=target, seed=seed, tmp=tmp_path)


class TestModelSettings:
    def test_defaults_when_no_files_exist(self, settings_env):
        s = als.ModelSettings()
        assert s.get_all() == DEFAULTS and s.get("missing", 7) == 7

    def test_seed_is_used_until_a_writable_file_exists(self, settings_env):
        settings_env.seed.write_text(json.dumps({"temperature": 0.9, "_comment": "x", "unknown": 1}))
        s = als.ModelSettings()
        assert s.get("temperature") == 0.9 and "unknown" not in s.settings and "_comment" not in s.settings

    def test_writable_file_wins_over_the_seed(self, settings_env):
        settings_env.target.parent.mkdir()
        settings_env.target.write_text(json.dumps({"temperature": 0.1}))
        settings_env.seed.write_text(json.dumps({"temperature": 0.9, "verbose": True}))
        s = als.ModelSettings()
        assert s.get("temperature") == 0.1 and s.get("verbose") is False

    def test_broken_file_is_ignored(self, settings_env):
        settings_env.target.parent.mkdir()
        settings_env.target.write_text("{not json")
        assert als.ModelSettings().get_all() == DEFAULTS

    def test_non_object_json_is_ignored(self, settings_env):
        settings_env.target.parent.mkdir()
        settings_env.target.write_text("[1, 2]")
        assert als.ModelSettings().get_all() == DEFAULTS

    def test_set_persists_known_keys_only(self, settings_env):
        s = als.ModelSettings()
        assert s.set("temperature", 0.2) is True
        assert json.loads(settings_env.target.read_text())["temperature"] == 0.2
        assert s.set("nope", 1) is False
        assert s.set("verbose", True, persist=False) is True and json.loads(settings_env.target.read_text())["verbose"] is False

    def test_update_many(self, settings_env):
        s = als.ModelSettings()
        assert s.update_many({"nope": 1}) is False
        assert s.update_many({"temperature": 0.3, "nope": 1}, persist=False) is True
        assert not settings_env.target.exists()
        assert s.update_many({"output_tokens": 99}) is True
        assert json.loads(settings_env.target.read_text())["output_tokens"] == 99

    def test_unwritable_primary_switches_to_the_fallback_path(self, settings_env):
        settings_env.target.parent.mkdir()
        settings_env.target.parent.chmod(0o500)
        try:
            s = als.ModelSettings()
            if s.save_settings() and s.settings_file != str(settings_env.target):
                assert s.settings_file.endswith("fallback/llm_settings.json")
            else:
                pytest.skip("процесс может писать в read-only каталог (root)")
        finally:
            settings_env.target.parent.chmod(0o700)

    def test_all_paths_failing_returns_false(self, settings_env, monkeypatch):
        s = als.ModelSettings()
        monkeypatch.setattr(als, "llm_settings_file_fallbacks", lambda path: [path])
        monkeypatch.setattr(als.json, "dump", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad value")))
        assert s.save_settings() is False

    def test_reset_and_copies(self, settings_env):
        s = als.ModelSettings()
        s.set("temperature", 0.99)
        s.reset_to_defaults()
        assert s.get("temperature") == 0.5
        s.get_recommended_settings()["temperature"] = 5
        s.get_all()["temperature"] = 5
        assert s.get("temperature") == 0.5

    def test_max_values_come_from_the_helper(self, settings_env, monkeypatch):
        monkeypatch.setattr(als, "llm_settings_max_values", lambda: {"temperature": 2})
        assert als.ModelSettings().get_max_values() == {"temperature": 2}


class TestModuleLevelSettings:
    def test_update_model_settings_refreshes_module_constants(self, monkeypatch):
        fake = SimpleNamespace(
            update_many=lambda updates: True,
            get=lambda key, default=None: {"context_size": 1, "output_tokens": 2, "verbose": True}.get(key, default),
        )
        monkeypatch.setattr(als, "model_settings", fake)
        assert als.update_model_settings({"context_size": 1}) is True
        assert (als.MODEL_CONTEXT_SIZE, als.DEFAULT_OUTPUT_TOKENS, als.VERBOSE_OUTPUT) == (1, 2, True)
        monkeypatch.setattr(fake, "update_many", lambda updates: False, raising=False)
        assert als.update_model_settings({"x": 1}) is False


class TestPreparePrompt:
    def test_explicit_system_prompt_and_history(self):
        prompt = als.prepare_prompt(
            "  вопрос  ", system_prompt="Ты помощник",
            history=[{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}, {"role": "system", "content": "skip"}, {"content": "no role"}],
        )
        assert prompt == (
            "<|im_start|>system\nТы помощник\n<|im_end|>"
            "<|im_start|>user\nq1\n<|im_end|><|im_start|>assistant\na1\n<|im_end|>"
            "<|im_start|>user\nno role\n<|im_end|>"
            "<|im_start|>user\nвопрос\n<|im_end|><|im_start|>assistant\n"
        )

    def test_blank_system_prompt_is_omitted(self):
        assert "system" not in als.prepare_prompt("q", system_prompt="   ")

    def test_default_prompt_comes_from_the_manager(self, monkeypatch):
        manager = SimpleNamespace(get_effective_prompt=lambda path, pid=None: f"P:{path}:{pid}", get_global_prompt=lambda: "GLOBAL")
        monkeypatch.setattr(als, "context_prompt_manager", manager)
        assert "P:m:c1" in als.prepare_prompt("q", model_path="m", custom_prompt_id="c1")
        assert "GLOBAL" in als.prepare_prompt("q")


class TestInitialize:
    def test_success_and_failure(self, monkeypatch):
        import asyncio

        # initialize_model зовёт get_event_loop(): после asyncio.run в соседних
        # тестах у главного потока нет текущего loop - создаём свой.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        calls = []

        async def service():
            calls.append(1)

        monkeypatch.setattr(als, "get_llm_service", service)
        assert als.initialize_model() is True and calls == [1]

        async def broken():
            raise RuntimeError("down")

        monkeypatch.setattr(als, "get_llm_service", broken)
        assert als.initialize_model() is False
        loop.close()

    async def test_inside_a_running_loop_a_worker_thread_is_used(self, monkeypatch):
        calls = []

        async def service():
            calls.append(1)

        monkeypatch.setattr(als, "get_llm_service", service)
        assert als.initialize_model() is True and calls == [1]

    def test_disabled_llm_svc(self, monkeypatch):
        monkeypatch.setattr(als, "USE_LLM_SVC", False)
        assert als.initialize_model() is True and als.reload_model_by_path("x") is True
        assert als.get_model_info()["loaded"] is True


class FakeProvider:
    id = "prov"

    def __init__(self, models=("m1",), loaded=True):
        self.models, self.loaded, self.ensured = models, loaded, []

    async def list_models(self):
        return [SimpleNamespace(model_id=m) for m in self.models]

    async def ensure_model_loaded(self, model_id):
        self.ensured.append(model_id)
        return self.loaded


class FakeRegistry:
    def __init__(self, provider, resolved):
        self.provider, self.resolved = provider, resolved

    def resolve(self, path):
        return self.provider, self.resolved

    def contains(self, raw):
        return raw == "prov"

    def get(self, raw):
        return self.provider


@pytest.fixture
def registry(monkeypatch):
    import backend.llm_providers as providers

    state = SimpleNamespace(provider=FakeProvider(), resolved="m1")

    async def get_registry():
        return FakeRegistry(state.provider, state.resolved)

    monkeypatch.setattr(providers, "get_registry", get_registry)
    monkeypatch.setattr(als, "_selected_model_name", None)
    return state


class FakeClient:
    llm_hosts = {"h1": object()}
    default_llm_host = "h1"

    def __init__(self, ok=True, health=None):
        self.ok, self.health, self.loaded = ok, health, []

    async def load_model_if_needed(self, mid, host_id=None):
        self.loaded.append((mid, host_id))
        return self.ok

    async def health_check(self, host_id=None):
        return self.health


class TestReloadModel:
    def test_provider_path_loads_the_model(self, registry):
        assert als.reload_model_by_path("prov/m1") is True
        assert registry.provider.ensured == ["m1"] and als._selected_model_name == "prov/m1"

    def test_provider_only_path_picks_the_first_model(self, registry):
        registry.resolved = ""
        assert als.reload_model_by_path("prov") is True and registry.provider.ensured == ["m1"]

    @pytest.mark.parametrize("models", [(), ("",)])
    def test_provider_without_usable_models(self, registry, models):
        registry.resolved, registry.provider = "", FakeProvider(models=models)
        assert als.reload_model_by_path("prov") is False

    def test_unknown_provider_path(self, registry):
        registry.resolved = ""
        # Известная особенность: для любого пути, не начинающегося с llm-svc://, возвращается результат
        # ветки «provider path»; ветки директории/.gguf/multi-LLM ниже по коду достижимы только после исключения.
        assert als.reload_model_by_path("unknown-thing") is False

    def test_failed_load_is_reported(self, registry):
        registry.provider = FakeProvider(loaded=False)
        assert als.reload_model_by_path("prov/m1") is False and als._selected_model_name is None

    def test_directory_paths_are_rejected(self, registry, tmp_path):
        registry.resolved = ""
        assert als.reload_model_by_path(str(tmp_path)) is False  # отклонён уже веткой provider path

    def test_llm_svc_scheme_switches_the_model(self, monkeypatch):
        client = FakeClient()
        service = SimpleNamespace(model_name="old", client=client)

        async def get_service():
            return service

        monkeypatch.setattr(als, "get_llm_service", get_service)
        monkeypatch.setattr(als, "resolve_llm_host_and_model_for_svc", lambda path, name, hosts, default: ("h1", "new-model"))
        assert als.reload_model_by_path("llm-svc://new-model") is True
        assert client.loaded == [("new-model", "h1")] and service.model_name == "new-model" and als._selected_model_name == "new-model"

    def test_llm_svc_scheme_edge_cases(self, monkeypatch):
        assert als.reload_model_by_path("llm-svc://  ") is False
        service = SimpleNamespace(model_name="old", client=FakeClient())

        async def get_service():
            return service

        monkeypatch.setattr(als, "get_llm_service", get_service)
        monkeypatch.setattr(als, "resolve_llm_host_and_model_for_svc", lambda *a: ("h1", ""))
        assert als.reload_model_by_path("llm-svc://x") is False
        monkeypatch.setattr(als, "resolve_llm_host_and_model_for_svc", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        assert als.reload_model_by_path("llm-svc://x") is False

    def test_local_gguf_file_hits_the_provider_branch_first(self, registry, tmp_path):
        registry.resolved = ""
        model = tmp_path / "m.gguf"
        model.write_text("x")
        # Ветка «.gguf → True» недостижима: provider path отвечает False раньше (см. тест выше).
        assert als.reload_model_by_path(str(model)) is False


class TestModelInfo:
    def run(self, monkeypatch, health, selected=None, error=None):
        service = SimpleNamespace(model_name="svc-model", client=FakeClient(health=health))

        async def get_service():
            if error:
                raise error
            return service

        monkeypatch.setattr(als, "get_llm_service", get_service)
        monkeypatch.setattr(als, "resolve_llm_host_and_model_for_svc", lambda *a: ("h1", "m"))
        monkeypatch.setattr(als, "_selected_model_name", selected)
        return als.get_model_info()

    def test_healthy_with_selected_model(self, monkeypatch):
        info = self.run(monkeypatch, {"status": "healthy", "model_loaded": True}, selected="prov/m1")
        assert info["loaded"] is True and info["name"] == "prov/m1" and info["path"] == "llm-svc://prov/m1"
        assert info["metadata"]["general.architecture"] == "LLM-SVC"

    def test_healthy_without_selection_reports_not_loaded_name(self, monkeypatch):
        info = self.run(monkeypatch, {"status": "healthy", "model_name": "svc"})
        assert info["name"] == "Модель не загружена" and info["path"] == "" and info["metadata"]["general.name"] == "svc"

    def test_unhealthy_or_failing(self, monkeypatch):
        assert self.run(monkeypatch, {"status": "down"})["error"] == "llm-svc недоступен"
        assert self.run(monkeypatch, None)["loaded"] is False
        assert self.run(monkeypatch, None, error=RuntimeError("no route"))["error"] == "no route"


@pytest.fixture
def routing(monkeypatch):
    import backend.llm_providers.routing as routing_module
    import backend.mcp.orchestrator_bridge as bridge

    state = SimpleNamespace(direct=False, registry_result="registry answer", registry_calls=[], svc_calls=[], svc_result="svc answer", usable=True)

    def sync_chat(prompt, **kw):
        state.registry_calls.append((prompt, kw))
        if isinstance(state.registry_result, Exception):
            raise state.registry_result
        return state.registry_result

    def svc(**kw):
        state.svc_calls.append(kw)
        if isinstance(state.svc_result, BaseException):
            raise state.svc_result
        return state.svc_result

    monkeypatch.setattr(routing_module, "should_use_llm_svc_direct", lambda model_path=None, images=None: state.direct)
    monkeypatch.setattr(routing_module, "registry_response_usable", lambda r: state.usable)
    monkeypatch.setattr(bridge, "sync_chat_via_registry", sync_chat)
    monkeypatch.setattr(als, "ask_agent_llm_svc", svc)
    monkeypatch.setattr(als, "get_active_model_settings", lambda: {"output_tokens": 256, "temperature": 0.4})
    monkeypatch.setattr(als, "context_prompt_manager", SimpleNamespace(get_effective_prompt=lambda p: "  CTX  ", get_global_prompt=lambda: ""))
    return state


def status_error(code):
    request = httpx.Request("POST", "http://x")
    return httpx.HTTPStatusError("err", request=request, response=httpx.Response(code, request=request))


class TestAskAgentRegistry:
    def test_registry_answer_with_defaults(self, routing):
        assert als.ask_agent("привет", model_path="prov/m1", enable_thinking=1, service_call=1) == "registry answer"
        prompt, kw = routing.registry_calls[0]
        assert prompt == "привет" and kw["max_tokens"] == 256 and kw["temperature"] == 0.4
        assert kw["system_prompt"] == "CTX" and kw["enable_thinking"] is True and kw["service_call"] is True

    def test_explicit_values_win(self, routing):
        als.ask_agent("q", max_tokens=10, temperature=0, system_prompt="S", model_path="m")
        kw = routing.registry_calls[0][1]
        assert kw["max_tokens"] == 10 and kw["temperature"] == 0.0 and kw["system_prompt"] == "S"

    def test_global_prompt_is_used_without_a_model_path(self, routing):
        als.ask_agent("q")
        assert routing.registry_calls[0][1]["system_prompt"] is None

    def test_prompt_manager_failure_does_not_break_the_call(self, routing, monkeypatch):
        def broken(path):
            raise RuntimeError("no prompts")

        monkeypatch.setattr(als, "context_prompt_manager", SimpleNamespace(get_effective_prompt=broken, get_global_prompt=broken))
        assert als.ask_agent("q", model_path="m") == "registry answer"

    def test_unusable_registry_answer(self, routing):
        routing.usable = False
        assert "Не удалось получить ответ" in als.ask_agent("q")

    def test_http_error_is_formatted(self, routing):
        routing.registry_result = status_error(429)
        assert isinstance(als.ask_agent("q"), str) and als.ask_agent("q")

    def test_unexpected_error(self, routing):
        routing.registry_result = RuntimeError("x")
        assert als.ask_agent("q") == "Ошибка при обращении к модели."


class TestAskAgentDirectLlmSvc:
    def test_direct_call_with_defaults(self, routing):
        routing.direct = True
        assert als.ask_agent("q", model_path="llm-svc://m", images=["img"]) == "svc answer"
        kw = routing.svc_calls[0]
        assert kw["max_tokens"] == 256 and kw["temperature"] == 0.4 and kw["images"] == ["img"] and kw["prompt"] == "q"
        assert routing.registry_calls == []

    def test_cancelled_generation_returns_none(self, routing):
        routing.direct = True
        routing.svc_result = None
        assert als.ask_agent("q") is None
        import asyncio

        routing.svc_result = asyncio.CancelledError()
        assert als.ask_agent("q") is None

    def test_busy_service_message(self, routing):
        routing.direct, routing.svc_result = True, status_error(503)
        assert "занят или перезагружается" in als.ask_agent("q")

    def test_other_http_error_and_generic_error(self, routing):
        routing.direct, routing.svc_result = True, status_error(500)
        assert als.ask_agent("q")
        routing.svc_result = RuntimeError("socket closed")
        out = als.ask_agent("q")
        assert "Извините, произошла ошибка" in out and "socket closed" not in out

    def test_disabled_service_fallback_message(self, routing, monkeypatch):
        routing.direct = True
        monkeypatch.setattr(als, "USE_LLM_SVC", False)
        assert "llm-svc недоступен" in als.ask_agent("q")
