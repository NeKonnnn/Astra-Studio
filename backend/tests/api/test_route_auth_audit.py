"""Аудит авторизации: у каких HTTP-эндпоинтов нет зависимости проверки пользователя.

Тест обходит все подключаемые роутеры и сверяет набор эндпоинтов без авторизации с тремя
списками:

* ``PUBLIC_BY_DESIGN`` — открыты по замыслу (вход, health, публичная ссылка);
* ``INTERNAL_EDGE`` — внутренние вызовы сервисов, закрываются на границе (nginx/shared secret);
* ``REMOVED_ENDPOINTS`` — удалённый функционал, маршруты отвечают 410 и ничего не делают;
* ``KNOWN_UNAUTHENTICATED`` — эндпоинты, которым авторизация нужна, но её нет. Каждый помечен
  ``xfail(strict=True)``: когда авторизацию добавят, тест упадёт и потребует убрать запись.

Новый эндпоинт без авторизации, не входящий ни в один список, ломает ``test_no_new_unauthenticated_routes``.
Ограничение: аудит видит зависимости FastAPI (``get_current_user`` / ``get_optional_user`` и
``Depends`` вокруг них). Ручная проверка токена внутри обработчика им не распознаётся.
"""

import importlib
import pkgutil

import pytest
from fastapi import APIRouter

import backend.auth.jwt_handler as jwt_handler

pytestmark = pytest.mark.unit

AUTH_DEPENDENCIES = {jwt_handler.get_current_user, jwt_handler.get_optional_user}

ROUTER_MODULES = [
    "backend.api_agents",
    "backend.api_skills",
    "backend.api_users",
    "backend.auth.routes",
    "backend.auth.sso_routes",
]

PUBLIC_BY_DESIGN = {
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/api/share/{share_id}"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/refresh"),
    ("GET", "/api/auth/login-lockout-policy"),
    ("GET", "/api/auth/session-policy"),
    ("GET", "/api/auth/server-instance"),
    ("GET", "/api/auth/sso/providers"),
    ("GET", "/api/auth/sso/keycloak/login"),
    ("GET", "/api/auth/sso/keycloak/callback"),
    ("POST", "/api/auth/sso/keycloak/exchange"),
}

INTERNAL_EDGE = {
    ("POST", "/api/internal/rag/index-failed"),
    ("POST", "/api/internal/rag/llm"),
    ("POST", "/api/internal/rag/ocr-pdf"),
    ("POST", "/api/internal/rag/ocr-image"),
}

# Global documents store удалён из продукта: эти маршруты отвечают 410/пустым списком и ничего
# не делают (см. test_documents_api.py::TestRemovedGlobalStore). Не уязвимость.
REMOVED_ENDPOINTS = {
    ("POST", "/api/documents/upload"),
    ("POST", "/api/documents/query"),
    ("GET", "/api/documents/"),
    ("GET", "/api/documents"),
    ("DELETE", "/api/documents/{filename}"),
    ("GET", "/api/documents/report/generate"),
    ("GET", "/api/documents/report/download"),
    ("POST", "/api/documents/extract"),
}

# Пусто: все ранее известные незащищённые эндпоинты получили get_current_user.
KNOWN_UNAUTHENTICATED = sorted(set())


def _dependency_calls(dependant):
    yield dependant.call
    for sub in dependant.dependencies:
        yield from _dependency_calls(sub)


def _collect_routes():
    """{(METHOD, path): authenticated} по всем роутерам приложения."""
    import backend.routes as routes_pkg

    module_names = ["backend.routes." + m.name for m in pkgutil.iter_modules(routes_pkg.__path__)]
    result = {}
    for name in module_names + ROUTER_MODULES:
        module = importlib.import_module(name)
        for value in vars(module).values():
            if not isinstance(value, APIRouter):
                continue
            for route in value.routes:
                if not hasattr(route, "methods") or getattr(route, "dependant", None) is None:
                    continue
                authed = any(call in AUTH_DEPENDENCIES for call in _dependency_calls(route.dependant))
                for method in route.methods - {"HEAD", "OPTIONS"}:
                    key = (method, route.path)
                    result[key] = result.get(key, False) or authed
    return result


@pytest.fixture(scope="module")
def routes():
    return _collect_routes()


def test_audit_sees_a_realistic_number_of_routes(routes):
    assert len(routes) > 150
    assert sum(routes.values()) > 80


def test_no_new_unauthenticated_routes(routes):
    unauthenticated = {key for key, authed in routes.items() if not authed}
    allowed = PUBLIC_BY_DESIGN | INTERNAL_EDGE | REMOVED_ENDPOINTS | set(KNOWN_UNAUTHENTICATED)

    unexpected = sorted(unauthenticated - allowed)

    assert unexpected == [], f"новые эндпоинты без авторизации: {unexpected}"


@pytest.mark.parametrize("key", sorted(PUBLIC_BY_DESIGN | INTERNAL_EDGE | REMOVED_ENDPOINTS))
def test_allowlisted_routes_still_exist(routes, key):
    assert key in routes


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(
            key,
            marks=pytest.mark.xfail(
                strict=True,
                reason="BUG: эндпоинт доступен без авторизации (см. TESTING_KNOWN_BUGS.md)",
            ),
            id=f"{key[0]} {key[1]}",
        )
        for key in KNOWN_UNAUTHENTICATED
    ],
)
def test_endpoint_requires_authentication(routes, key):
    assert key in routes, "эндпоинт исчез — уберите его из KNOWN_UNAUTHENTICATED"
    assert routes[key] is True


def test_login_endpoints_are_public_so_they_stay_out_of_the_known_issues():
    assert not (PUBLIC_BY_DESIGN & set(KNOWN_UNAUTHENTICATED))
    assert not (INTERNAL_EDGE & set(KNOWN_UNAUTHENTICATED))
    assert not (REMOVED_ENDPOINTS & set(KNOWN_UNAUTHENTICATED))


# --- Изменяющие эндпоинты, где вход необязателен (get_optional_user без get_current_user) -----------------

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Допустимо по замыслу: анонимный пользователь может создать публичную ссылку на сообщения.
# Счётчики просмотров/использования агента — публичная аналитика; накрутку анонимами это допускает
# (см. TESTING_KNOWN_BUGS.md, низкая серьёзность).
OPTIONAL_AUTH_BY_DESIGN = {
    ("POST", "/api/share/create"),
    ("POST", "/api/agents/{agent_id}/view"),
    ("POST", "/api/agents/{agent_id}/use"),
}

# Дефекты: изменяющие операции, доступные анонимно (владелец проверяется только «если вошёл»).
# Пусто: DELETE /api/projects/{id} и DELETE /api/share/{id} требуют get_current_user /
# явной авторизации (исправлено).
KNOWN_OPTIONAL_AUTH_MUTATIONS = sorted(set())


def _collect_optional_only_mutations():
    import backend.routes as routes_pkg

    found = set()
    module_names = ["backend.routes." + m.name for m in pkgutil.iter_modules(routes_pkg.__path__)]
    for name in module_names + ROUTER_MODULES:
        module = importlib.import_module(name)
        for value in vars(module).values():
            if not isinstance(value, APIRouter):
                continue
            for route in value.routes:
                if not hasattr(route, "methods") or getattr(route, "dependant", None) is None:
                    continue
                calls = set(_dependency_calls(route.dependant))
                if jwt_handler.get_optional_user in calls and jwt_handler.get_current_user not in calls:
                    for method in route.methods & UNSAFE_METHODS:
                        found.add((method, route.path))
    return found


@pytest.fixture(scope="module")
def optional_mutations():
    return _collect_optional_only_mutations()


def test_no_new_mutations_with_optional_authentication(optional_mutations):
    allowed = OPTIONAL_AUTH_BY_DESIGN | set(KNOWN_OPTIONAL_AUTH_MUTATIONS)

    assert sorted(optional_mutations - allowed) == []


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(
            key,
            marks=pytest.mark.xfail(
                strict=True,
                reason="BUG: изменяющая операция доступна анонимно (get_optional_user). См. TESTING_KNOWN_BUGS.md",
            ),
            id=f"{key[0]} {key[1]}",
        )
        for key in KNOWN_OPTIONAL_AUTH_MUTATIONS
    ],
)
def test_mutation_requires_a_logged_in_user(optional_mutations, key):
    assert key not in optional_mutations
