"""backend/application.py: сборка приложения, lifespan, восстановление модели, run_server.

Реальные БД/MCP/Socket.IO не поднимаем: lifespan вызывается напрямую, внешние сервисы
подменяются в пространствах имён модулей.
"""

import asyncio
import importlib
import importlib.util
import sys
import types
from pathlib import Path

import httpx
import pytest

import backend.application as application
import backend.app_state as state
import backend.llm_providers as llm_providers_pkg

_APP_PATH = Path(application.__file__)


# ----------------------------------------------------------------- вспомогательное


@pytest.fixture
def events(monkeypatch):
    """Перехват CEF-событий приложения."""
    seen = []
    monkeypatch.setattr(application, "log_cef_event", lambda code, **kw: seen.append(code))
    return seen


@pytest.fixture
def exec_application(monkeypatch, tmp_path):
    """Фабрика: повторное выполнение application.py в изолированном окружении.

    Позволяет проверить ветки уровня модуля (нет plugins, есть .env, есть сборка фронта).
    """
    counter = {"n": 0}

    def _exec(*, cwd=None):
        if cwd is not None:
            monkeypatch.chdir(cwd)
        counter["n"] += 1
        spec = importlib.util.spec_from_file_location(f"backend._application_copy{counter['n']}", _APP_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _exec


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


# ---------------------------------------------------------------- сборка приложения


def test_app_is_configured_from_settings():
    assert application.app.title == application.settings.app.name
    assert application.app.version == application.settings.app.version
    assert application.app.router.lifespan_context is not None


def test_app_includes_all_expected_routers():
    paths = {getattr(r, "path", "") for r in application.app.routes}
    assert "/socket.io" in paths
    for prefix in ("/api/agents", "/api/skills", "/api/mcp", "/api/chat", "/api/auth"):
        assert any(p.startswith(prefix) for p in paths), prefix


def test_cors_origins_are_taken_from_config():
    cors = [m for m in application.app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
    assert cors
    assert cors[0].kwargs["allow_origins"] == [o for o in application.settings.cors.allowed_origins if o]
    assert any(m.cls.__name__ == "CefAuditMiddleware" for m in application.app.user_middleware)


async def test_service_disabled_error_is_503():
    from backend.settings.service_toggles import ServiceDisabledError

    @application.app.get("/__b3_disabled")
    async def _boom():
        raise ServiceDisabledError("tts")

    try:
        async with _client(application.app) as c:
            r = await c.get("/__b3_disabled")
        assert r.status_code == 503
        assert "в разработке" in r.json()["detail"]
    finally:
        application.app.router.routes[:] = [
            r for r in application.app.router.routes if getattr(r, "path", "") != "/__b3_disabled"
        ]


def test_module_without_plugins_router_still_builds(monkeypatch, exec_application):
    monkeypatch.setitem(sys.modules, "backend.routes.plugins", None)
    module = exec_application()
    assert module.plugins_router is None
    assert isinstance(module._plugins_import_error, ImportError)
    assert not any(getattr(r, "path", "").startswith("/api/plugins") for r in module.app.routes)


def test_module_with_plugins_router_includes_it(exec_application):
    module = exec_application()
    assert module.plugins_router is not None and module._plugins_import_error is None
    assert any(getattr(r, "path", "").startswith("/api/plugins") for r in module.app.routes)


def test_module_loads_dotenv_when_file_exists(monkeypatch, exec_application):
    loaded = []
    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(load_dotenv=loaded.append))
    real_exists = importlib.import_module("os").path.exists
    monkeypatch.setattr("os.path.exists", lambda p: str(p).endswith(".env") or real_exists(p))
    exec_application()
    assert len(loaded) == 1 and loaded[0].endswith(".env")


def test_module_without_dotenv_package(monkeypatch, exec_application):
    monkeypatch.setitem(sys.modules, "dotenv", None)
    assert exec_application().load_dotenv is None


def test_module_survives_broken_optional_routers(monkeypatch, exec_application):
    monkeypatch.setitem(sys.modules, "backend.routes.mcp_atlassian", None)
    real_import = importlib.import_module

    def _fake_import(name, *a, **k):
        if name == "backend.api_users":
            raise RuntimeError("битый модуль")
        return real_import(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", _fake_import)
    module = exec_application()
    paths = {getattr(r, "path", "") for r in module.app.routes}
    assert not any(p.startswith("/api/mcp/atlassian") for p in paths)
    assert any(p.startswith("/api/agents") for p in paths)  # остальные роутеры подключены


async def test_frontend_build_is_served_when_present(monkeypatch, tmp_path, exec_application):
    monkeypatch.delenv("DOCKER_ENV", raising=False)
    backend_dir = tmp_path / "backend"
    (tmp_path / "frontend" / "build" / "static").mkdir(parents=True)
    backend_dir.mkdir()
    (tmp_path / "frontend" / "build" / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (tmp_path / "frontend" / "build" / "static" / "a.js").write_text("1", encoding="utf-8")
    module = exec_application(cwd=backend_dir)
    async with _client(module.app) as c:
        spa = await c.get("/some/client/route")
        asset = await c.get("/static/a.js")
        # без собранного index.html отдаётся заглушка
        (tmp_path / "frontend" / "build" / "index.html").unlink()
        stub = await c.get("/some/client/route")
    assert spa.status_code == 200 and "spa" in spa.text
    assert asset.status_code == 200 and asset.text == "1"
    assert stub.json() == {"message": "Frontend not built"}


def test_frontend_build_ignored_in_docker(monkeypatch, tmp_path, exec_application):
    monkeypatch.setenv("DOCKER_ENV", "TRUE")
    (tmp_path / "frontend" / "build" / "static").mkdir(parents=True)
    (tmp_path / "backend").mkdir()
    module = exec_application(cwd=tmp_path / "backend")
    assert not any(getattr(r, "name", "") == "static" for r in module.app.routes)


# ------------------------------------------------------------------------ lifespan


class _Platform:
    def __init__(self, initialized=True, fail_shutdown=False):
        self.initialized = initialized
        self.calls = []
        self.fail_shutdown = fail_shutdown

    async def initialize(self):
        self.calls.append("initialize")

    async def shutdown(self):
        self.calls.append("shutdown")
        if self.fail_shutdown:
            raise RuntimeError("shutdown boom")


@pytest.fixture
def lifespan_env(monkeypatch, events):
    """Все внешние зависимости lifespan заменены заглушками; возвращает журнал вызовов."""
    log = types.SimpleNamespace(
        calls=[], restore_started=0, platform=_Platform(), plugin_enabled=True
    )

    async def _init():
        log.calls.append("init_databases")
        return True

    async def _close():
        log.calls.append("close_databases")

    async def _restore():
        log.restore_started += 1

    async def _ensure_pool():
        log.calls.append("ensure_pool")

    monkeypatch.setattr(application, "init_databases", _init)
    monkeypatch.setattr(application, "close_databases", _close)
    monkeypatch.setattr(application, "database_available", True)
    monkeypatch.setattr(application, "minio_client", types.SimpleNamespace(endpoint="minio:9000"))
    monkeypatch.setattr(application, "_restore_saved_model_after_startup", _restore)
    monkeypatch.setattr(application, "settings", types.SimpleNamespace(mcp=types.SimpleNamespace(enabled=True)))
    monkeypatch.setattr(state, "memory_clear_on_restart", False)
    monkeypatch.setattr(application, "clear_dialog_history", None)

    import backend.database.init_db as init_db
    import backend.mcp.package_check as package_check
    import backend.mcp.platform as mcp_platform
    import backend.plugins.platform as plugins_platform
    import backend.rag_query.prompts as prompts

    monkeypatch.setattr(init_db, "postgresql_connection", types.SimpleNamespace(ensure_pool=_ensure_pool), raising=False)
    monkeypatch.setattr(package_check, "log_mcp_package_status", lambda: log.calls.append("mcp_pkg"))
    monkeypatch.setattr(mcp_platform, "get_mcp_platform", lambda: log.platform)
    monkeypatch.setattr(prompts, "prompts_overview", lambda: {"ok": True})
    plugin = types.SimpleNamespace(id="p1")
    monkeypatch.setattr(
        plugins_platform,
        "get_plugins_platform",
        lambda: types.SimpleNamespace(
            initialize=lambda: log.calls.append("plugins_init"),
            list_plugins=lambda: [plugin],
            enabled=True,
        ),
    )
    log.modules = types.SimpleNamespace(
        init_db=init_db, package_check=package_check, mcp_platform=mcp_platform,
        plugins_platform=plugins_platform, prompts=prompts,
    )
    return log


async def _run_lifespan():
    async with application.lifespan(application.app):
        pass
    await asyncio.sleep(0)  # дать отработать запланированной задаче восстановления модели


async def test_lifespan_happy_path(lifespan_env, events):
    await _run_lifespan()
    assert lifespan_env.calls == [
        "init_databases", "ensure_pool", "mcp_pkg", "plugins_init", "close_databases",
    ]
    assert lifespan_env.platform.calls == ["initialize", "shutdown"]
    assert lifespan_env.restore_started == 1
    assert events == ["SYS001", "SYS002"]


async def test_lifespan_partial_database_init_continues(lifespan_env, monkeypatch, events):
    async def _partial():
        return False

    monkeypatch.setattr(application, "init_databases", _partial)
    await _run_lifespan()
    assert "ensure_pool" not in lifespan_env.calls
    assert events == ["SYS001", "SYS002"]


async def test_lifespan_init_databases_error_does_not_stop_startup(lifespan_env, monkeypatch, events):
    async def _fail():
        raise RuntimeError("db down")

    monkeypatch.setattr(application, "init_databases", _fail)
    await _run_lifespan()
    assert events == ["SYS001", "SYS002"]
    assert "close_databases" in lifespan_env.calls


async def test_lifespan_postgres_pool_error_is_logged_only(lifespan_env, monkeypatch, events):
    async def _pool_fail():
        raise RuntimeError("pool")

    monkeypatch.setattr(
        lifespan_env.modules.init_db, "postgresql_connection", types.SimpleNamespace(ensure_pool=_pool_fail)
    )
    await _run_lifespan()
    assert events == ["SYS001", "SYS002"]


async def test_lifespan_without_postgres_connection_and_minio(lifespan_env, monkeypatch):
    monkeypatch.setattr(lifespan_env.modules.init_db, "postgresql_connection", None)
    monkeypatch.setattr(application, "minio_client", None)
    await _run_lifespan()
    assert "ensure_pool" not in lifespan_env.calls


async def test_lifespan_skips_databases_when_unavailable(lifespan_env, monkeypatch):
    monkeypatch.setattr(application, "database_available", False)
    await _run_lifespan()
    assert "init_databases" not in lifespan_env.calls
    assert "close_databases" not in lifespan_env.calls


async def test_lifespan_prompt_overview_error_is_swallowed(lifespan_env, monkeypatch, events):
    def _fail():
        raise RuntimeError("no prompt")

    monkeypatch.setattr(lifespan_env.modules.prompts, "prompts_overview", _fail)
    await _run_lifespan()
    assert events == ["SYS001", "SYS002"]


async def test_lifespan_mcp_disabled_skips_platform(lifespan_env, monkeypatch):
    monkeypatch.setattr(application, "settings", types.SimpleNamespace(mcp=types.SimpleNamespace(enabled=False)))
    lifespan_env.platform.initialized = False
    await _run_lifespan()
    assert lifespan_env.platform.calls == []  # ни initialize, ни shutdown
    assert "mcp_pkg" not in lifespan_env.calls


async def test_lifespan_without_mcp_settings(lifespan_env, monkeypatch):
    monkeypatch.setattr(application, "settings", types.SimpleNamespace())
    await _run_lifespan()
    assert "mcp_pkg" not in lifespan_env.calls


async def test_lifespan_mcp_errors_are_swallowed(lifespan_env, monkeypatch, events):
    def _fail():
        raise RuntimeError("mcp init")

    monkeypatch.setattr(lifespan_env.modules.package_check, "log_mcp_package_status", _fail)
    lifespan_env.platform.fail_shutdown = True
    await _run_lifespan()
    assert lifespan_env.platform.calls == ["shutdown"]
    assert events == ["SYS001", "SYS002"]
    assert "close_databases" in lifespan_env.calls  # остановка БД выполняется после ошибки MCP


async def test_lifespan_plugins_error_is_swallowed(lifespan_env, monkeypatch, events):
    def _fail():
        raise RuntimeError("plugins")

    monkeypatch.setattr(lifespan_env.modules.plugins_platform, "get_plugins_platform", _fail)
    await _run_lifespan()
    assert events == ["SYS001", "SYS002"]


async def test_lifespan_clears_memory_on_restart(lifespan_env, monkeypatch):
    cleared = []

    async def _clear():
        cleared.append(1)

    monkeypatch.setattr(state, "memory_clear_on_restart", True)
    monkeypatch.setattr(application, "clear_dialog_history", _clear)
    await _run_lifespan()
    assert cleared == [1]


async def test_lifespan_clear_memory_error_is_swallowed(lifespan_env, monkeypatch, events):
    async def _clear():
        raise RuntimeError("mongo")

    monkeypatch.setattr(state, "memory_clear_on_restart", True)
    monkeypatch.setattr(application, "clear_dialog_history", _clear)
    await _run_lifespan()
    assert events == ["SYS001", "SYS002"]


async def test_lifespan_restore_scheduling_error_is_swallowed(lifespan_env, monkeypatch, events):
    def _fail(coro):
        coro.close()
        raise RuntimeError("no loop")

    monkeypatch.setattr(application, "asyncio", types.SimpleNamespace(create_task=_fail, sleep=None))
    await _run_lifespan()
    assert events == ["SYS001", "SYS002"]


async def test_lifespan_database_close_error_is_swallowed(lifespan_env, monkeypatch, events):
    async def _fail():
        raise RuntimeError("close")

    monkeypatch.setattr(application, "close_databases", _fail)
    await _run_lifespan()
    assert events == ["SYS001", "SYS002"]


# -------------------------------------------------- восстановление модели при старте


class _Provider:
    id = "ollama"

    def __init__(self, ensure=True, models=()):
        self._ensure = ensure
        self._models = list(models)
        self.ensured = []

    async def list_models(self):
        return [types.SimpleNamespace(model_id=m) for m in self._models]

    async def ensure_model_loaded(self, model_id):
        self.ensured.append(model_id)
        if isinstance(self._ensure, Exception):
            raise self._ensure
        return self._ensure


class _Registry:
    def __init__(self, provider, resolved=("p", "m"), contains=False):
        self.provider = provider
        self.resolved = resolved
        self._contains = contains

    def resolve(self, saved):
        return self.provider, self.resolved[1] if self.resolved else ""

    def contains(self, saved):
        return self._contains

    def get(self, saved):
        return self.provider


@pytest.fixture
def restore_env(monkeypatch):
    env = types.SimpleNamespace(sleeps=[], saved_settings=[], registry=None, registry_error=None)

    async def _sleep(delay):
        env.sleeps.append(delay)

    async def _get_registry():
        if env.registry_error:
            raise env.registry_error
        return env.registry

    monkeypatch.setattr(application, "asyncio", types.SimpleNamespace(sleep=_sleep))
    monkeypatch.setattr(llm_providers_pkg, "get_registry", _get_registry)
    monkeypatch.setattr(state, "save_app_settings", lambda data: env.saved_settings.append(data) or True)
    monkeypatch.setattr(application, "load_app_settings", lambda: {"current_model_path": "ollama/llama3"})
    return env


async def test_restore_without_saved_or_default_model_does_nothing(restore_env, monkeypatch):
    monkeypatch.setattr(application, "load_app_settings", lambda: {})
    monkeypatch.setattr(application, "settings", types.SimpleNamespace(default_llm_provider="", llm_service=None))
    await application._restore_saved_model_after_startup()
    assert restore_env.sleeps == []


async def test_restore_settings_read_error_falls_back_to_default_model(restore_env, monkeypatch):
    def _fail():
        raise RuntimeError("settings")

    monkeypatch.setattr(application, "load_app_settings", _fail)
    monkeypatch.setattr(
        application,
        "settings",
        types.SimpleNamespace(
            default_llm_provider="ollama", llm_service=types.SimpleNamespace(default_model="qwen")
        ),
    )
    provider = _Provider()
    restore_env.registry = _Registry(provider, resolved=("p", "qwen"))
    await application._restore_saved_model_after_startup()
    assert provider.ensured == ["qwen"]
    assert restore_env.saved_settings[0]["current_model_path"] == "ollama/qwen"


async def test_restore_default_model_lookup_error_is_swallowed(restore_env, monkeypatch):
    monkeypatch.setattr(application, "load_app_settings", lambda: {})

    class _BadSettings:
        @property
        def default_llm_provider(self):
            raise RuntimeError("bad")

    monkeypatch.setattr(application, "settings", _BadSettings())
    await application._restore_saved_model_after_startup()
    assert restore_env.sleeps == []


async def test_restore_success_saves_selected_model(restore_env):
    provider = _Provider(ensure=True)
    restore_env.registry = _Registry(provider, resolved=("ollama", "llama3"))
    await application._restore_saved_model_after_startup()
    assert restore_env.sleeps == [2]
    assert provider.ensured == ["llama3"]
    assert restore_env.saved_settings == [
        {
            "current_model_path": "ollama/llama3",
            "current_model_name": "llama3",
            "current_model_status": "loaded",
        }
    ]


async def test_restore_save_settings_error_is_swallowed(restore_env, monkeypatch):
    def _fail(data):
        raise RuntimeError("disk")

    monkeypatch.setattr(state, "save_app_settings", _fail)
    restore_env.registry = _Registry(_Provider(ensure=True), resolved=("ollama", "llama3"))
    await application._restore_saved_model_after_startup()
    assert restore_env.sleeps == [2]  # одна попытка: ошибка записи не ведёт к ретраям


async def test_restore_picks_first_model_of_provider_when_only_provider_given(restore_env):
    provider = _Provider(ensure=True, models=["first", "second"])
    restore_env.registry = _Registry(provider, resolved=("ollama", ""), contains=True)
    await application._restore_saved_model_after_startup()
    assert provider.ensured == ["first"]
    assert restore_env.saved_settings[0]["current_model_path"] == "ollama/first"


async def test_restore_gives_up_after_all_attempts_when_model_id_unknown(restore_env):
    provider = _Provider(models=[])
    restore_env.registry = _Registry(provider, resolved=("ollama", ""), contains=True)
    await application._restore_saved_model_after_startup()
    assert restore_env.sleeps == [2, 5, 10, 20, 30, 45]
    assert provider.ensured == []
    assert restore_env.saved_settings == []


async def test_restore_unknown_name_without_provider_is_retried(restore_env):
    restore_env.registry = _Registry(_Provider(), resolved=("ollama", ""), contains=False)
    await application._restore_saved_model_after_startup()
    assert len(restore_env.sleeps) == 6


async def test_restore_retries_when_model_not_loaded_then_succeeds(restore_env):
    provider = _Provider(ensure=False)
    restore_env.registry = _Registry(provider, resolved=("ollama", "llama3"))

    async def _ensure(model_id):
        provider.ensured.append(model_id)
        return len(provider.ensured) == 3

    provider.ensure_model_loaded = _ensure
    await application._restore_saved_model_after_startup()
    assert restore_env.sleeps == [2, 5, 10]
    assert len(restore_env.saved_settings) == 1


async def test_restore_registry_errors_exhaust_attempts(restore_env):
    restore_env.registry_error = RuntimeError("llm-svc прогревается")
    await application._restore_saved_model_after_startup()
    assert restore_env.sleeps == [2, 5, 10, 20, 30, 45]
    assert restore_env.saved_settings == []


# --------------------------------------------------------------------- run_server


@pytest.fixture
def server_env(monkeypatch, tmp_path):
    env = types.SimpleNamespace(uvicorn_calls=[], reload_calls=[], reload_result=True, saved="")
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: env.uvicorn_calls.append((app, kw)))
    monkeypatch.setattr(application, "load_app_settings", lambda: {"current_model_path": env.saved})
    monkeypatch.setattr(
        state, "reload_model_by_path", lambda p: env.reload_calls.append(p) or env.reload_result
    )
    env.tmp = tmp_path
    return env


def test_run_server_starts_uvicorn_with_app(server_env, monkeypatch):
    monkeypatch.setenv("UVICORN_HOST", "0.0.0.0")
    application.run_server()
    app, kwargs = server_env.uvicorn_calls[0]
    assert app is application.app
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 8000 and kwargs["reload"] is False
    assert server_env.reload_calls == []


def test_run_server_uses_configured_host_by_default(server_env, monkeypatch):
    monkeypatch.delenv("UVICORN_HOST", raising=False)
    application.run_server()
    assert server_env.uvicorn_calls[0][1]["host"] == application.settings.server.host


def test_run_server_skips_llm_svc_models(server_env):
    server_env.saved = "llm-svc://ollama/llama3"
    application.run_server()
    assert server_env.reload_calls == []
    assert len(server_env.uvicorn_calls) == 1


def test_run_server_restores_local_model_file(server_env):
    model = server_env.tmp / "m.gguf"
    model.write_text("w", encoding="utf-8")
    server_env.saved = str(model)
    application.run_server()
    assert server_env.reload_calls == [str(model)]


def test_run_server_failed_model_restore_still_starts(server_env):
    model = server_env.tmp / "m.gguf"
    model.write_text("w", encoding="utf-8")
    server_env.saved = str(model)
    server_env.reload_result = False
    application.run_server()
    assert server_env.reload_calls == [str(model)]
    assert len(server_env.uvicorn_calls) == 1


def test_run_server_ignores_directories_and_missing_paths(server_env):
    server_env.saved = str(server_env.tmp)  # каталог
    application.run_server()
    server_env.saved = str(server_env.tmp / "nope.gguf")
    application.run_server()
    assert server_env.reload_calls == []
    assert len(server_env.uvicorn_calls) == 2


def test_run_server_settings_error_does_not_prevent_start(server_env, monkeypatch):
    def _fail():
        raise RuntimeError("settings")

    monkeypatch.setattr(application, "load_app_settings", _fail)
    application.run_server()
    assert len(server_env.uvicorn_calls) == 1
