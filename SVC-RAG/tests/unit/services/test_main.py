"""app/main: lifespan (старт БД, перенарезка Библиотеки, CEF-события), фабрика приложения, middleware.

Реальная БД не поднимается: get_db и ensure_memory_chunk_consistency подменены.
"""

import runpy

import httpx
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import dependencies
from app import main
from app.core import mem_release
from app.core.cef_logger import CefAuditMiddleware

pytestmark = pytest.mark.unit


@pytest.fixture
def lifecycle(monkeypatch):
    """Подмена всего, что lifespan трогает снаружи; events — журнал вызовов по порядку."""
    events = []

    async def fake_get_db():
        events.append("get_db")

    async def fake_consistency():
        events.append("consistency")

    monkeypatch.setattr(main, "get_db", fake_get_db)
    monkeypatch.setattr(dependencies, "ensure_memory_chunk_consistency", fake_consistency)
    monkeypatch.setattr(main, "log_cef_event", lambda code, *a, **kw: events.append(code))
    monkeypatch.setattr(mem_release, "start_periodic", lambda: events.append("mem_start"))
    monkeypatch.setattr(mem_release, "stop_periodic", lambda: events.append("mem_stop"))
    return events


class TestLifespan:
    async def test_startup_and_shutdown_order(self, lifecycle):
        async with main.lifespan(main.app):
            assert lifecycle == ["get_db", "consistency", "SYS001", "mem_start"]
        assert lifecycle[-2:] == ["mem_stop", "SYS002"]

    async def test_memory_chunk_check_failure_does_not_stop_startup(self, lifecycle, monkeypatch):
        async def broken():
            raise RuntimeError("таблица недоступна")

        monkeypatch.setattr(dependencies, "ensure_memory_chunk_consistency", broken)
        async with main.lifespan(main.app):
            assert "SYS001" in lifecycle
        assert lifecycle[-1] == "SYS002"

    async def test_db_failure_aborts_startup(self, lifecycle, monkeypatch):
        async def no_db():
            raise RuntimeError("PostgreSQL недоступен")

        monkeypatch.setattr(main, "get_db", no_db)
        with pytest.raises(RuntimeError, match="PostgreSQL недоступен"):
            async with main.lifespan(main.app):
                pytest.fail("приложение не должно стартовать без БД")
        assert lifecycle == []  # ни SYS001, ни фоновой задачи памяти

    async def test_lifespan_runs_through_asgi_app(self, lifecycle):
        """Через настоящий ASGI-протокол: старт и остановка идут из lifespan приложения."""
        scope = {"type": "lifespan", "asgi": {"version": "3.0"}}
        sent, to_send = [], [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

        async def receive():
            return to_send.pop(0)

        async def send(message):
            sent.append(message["type"])

        await main.create_application()(scope, receive, send)
        assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        assert lifecycle[0] == "get_db" and lifecycle[-1] == "SYS002"


class TestCreateApplication:
    def test_builds_app_with_metadata_and_router(self):
        app = main.create_application()
        assert isinstance(app, FastAPI)
        assert app.title == main.settings.app.title and app.version == main.settings.app.version
        paths = {r.path for r in app.routes}
        assert any(p.startswith("/v1/") for p in paths)

    def test_middleware_stack_has_cors_and_cef(self):
        app = main.create_application()
        classes = [m.cls for m in app.user_middleware]
        assert CORSMiddleware in classes and CefAuditMiddleware in classes
        # CEF добавлен после CORS, то есть стоит снаружи в списке user_middleware раньше
        assert classes.index(CefAuditMiddleware) < classes.index(CORSMiddleware)

    def test_each_call_returns_new_app(self):
        assert main.create_application() is not main.create_application()

    def test_module_level_app_exists(self):
        assert isinstance(main.app, FastAPI)


class TestRequestMiddleware:
    @pytest.fixture
    def spies(self, monkeypatch):
        released, debug = [], []

        monkeypatch.setattr(mem_release, "release", lambda reason: released.append(reason))

        class Log:
            def debug(self, fmt, *args):
                debug.append(fmt % args)

            def __getattr__(self, name):
                return lambda *a, **kw: None

        monkeypatch.setattr(main, "logger", Log())
        return released, debug

    async def _get(self, path):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    async def test_request_is_logged_and_memory_released(self, spies, monkeypatch):
        released, debug = spies
        monkeypatch.setattr(main, "is_quiet_path", lambda m, p: False)
        monkeypatch.setattr(main, "is_quiet_access", lambda m, p, s: False)
        resp = await self._get("/v1/no-such-route")
        assert resp.status_code == 404
        assert "запрос GET /v1/no-such-route" in released
        assert len(debug) == 2
        assert "GET /v1/no-such-route" in debug[0] and debug[1].endswith("404")
        assert debug[0].split()[0] == debug[1].split()[0]  # один request_id на обе строки

    async def test_polling_requests_are_not_logged(self, spies, monkeypatch):
        released, debug = spies
        monkeypatch.setattr(main, "is_quiet_path", lambda m, p: True)
        monkeypatch.setattr(main, "is_quiet_access", lambda m, p, s: True)
        await self._get("/v1/no-such-route")
        assert debug == [] and released  # память отдаём и для «тихих» запросов

    async def test_memory_is_released_even_if_handler_fails(self, spies):
        released, _ = spies
        app = main.app

        @app.get("/v1/_r1_boom")
        async def boom():
            raise RuntimeError("handler crashed")

        try:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/_r1_boom")
        finally:
            app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/v1/_r1_boom"]
        assert resp.status_code == 500
        assert "запрос GET /v1/_r1_boom" in released


class TestEntryPoint:
    def test_running_module_starts_uvicorn_with_config(self, monkeypatch):
        import uvicorn

        calls = []
        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
        runpy.run_module("app.main", run_name="__main__")
        (args, kwargs), = calls
        assert args == ("app.main:app",)
        assert kwargs["host"] == main.settings.server.host and kwargs["port"] == main.settings.server.port
        assert "log_config" in kwargs
