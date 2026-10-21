"""Общие фикстуры API-тестов: изолированное FastAPI-приложение вокруг одного роутера.

Не поднимаем application.app: у него lifespan с БД, Socket.IO и восстановлением модели.
Авторизацию подменяем через dependency_overrides, сервисы — monkeypatch'ем в пространстве
имён самого роутера.
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.auth.jwt_handler import get_current_user, get_optional_user
from backend.settings.service_toggles import ServiceDisabledError

ALICE = {
    "username": "alice",
    "user_id": "alice",
    "is_admin": False,
    "groups": [],
    "ldap_groups": [],
}
ADMIN = {**ALICE, "username": "root", "user_id": "root", "is_admin": True}


@pytest.fixture
def make_client():
    """Фабрика: ``make_client(router, user=ALICE)`` → httpx.AsyncClient.

    ``user=None`` не подменяет авторизацию — запросы идут как анонимные.
    """

    def _make(router, user=ALICE):
        app = FastAPI()
        app.include_router(router)

        # Как в application.py: выключенный микросервис → 503.
        @app.exception_handler(ServiceDisabledError)
        async def _service_disabled(request, exc):
            return JSONResponse(status_code=503, content={"detail": f"Микросервис '{exc.display}' в разработке"})

        if user is not None:
            app.dependency_overrides[get_current_user] = lambda: dict(user)
            app.dependency_overrides[get_optional_user] = lambda: dict(user)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    return _make


@pytest.fixture
def services(monkeypatch):
    """``services(module, "tts", "stt")`` включает только перечисленные микросервисы."""

    def install(module, *enabled):
        def require(key):
            if key not in enabled:
                raise ServiceDisabledError(key)

        monkeypatch.setattr(module, "require_service", require)

    return install


@pytest.fixture
def cef_events(monkeypatch):
    """Перехватчик CEF-событий роутера: ``events = cef_events(module)`` → список (код, kwargs)."""
    events = []

    def install(module):
        monkeypatch.setattr(
            module, "log_cef_event", lambda code, **kwargs: events.append((code, kwargs))
        )
        return events

    return install
