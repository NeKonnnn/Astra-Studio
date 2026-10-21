"""auth/sso_routes: Keycloak OIDC — начало входа, callback, обмен ticket на JWT приложения."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from backend.auth import sso_routes as module
from backend.auth.sso_logging import KeycloakSsoError

pytestmark = pytest.mark.unit

BASE = "/api/auth/sso"
FRONT = "https://app.corp/login/sso-callback"


def _sso_error(status=502, detail="Keycloak недоступен"):
    return KeycloakSsoError(status, detail, log_message="boom")


@pytest.fixture
def kc(monkeypatch):
    state = SimpleNamespace(
        enabled=True, failures=[], recorded=[], login_error=None, exchange_error=None, exchange_result=None, callback_error=None,
        popped_state={"code_verifier": "ver"}, calls=[], bindings=[], ticket_payload={"access_token": "A", "_binding_id": "b1"},
    )

    monkeypatch.setattr(module, "is_keycloak_enabled", lambda: state.enabled)
    monkeypatch.setattr(module, "get_keycloak_settings", lambda: {"issuer": "https://sso", "client_id": "astra", "frontend_callback_url": FRONT})
    monkeypatch.setattr(module, "resolve_keycloak_redirect_uri", lambda base, settings: f"{base}/cb")

    def callback_url():
        if state.callback_error:
            raise state.callback_error
        return FRONT

    monkeypatch.setattr(module, "resolve_frontend_callback_url", callback_url)
    monkeypatch.setattr(module, "create_oauth_login_state", lambda: {"state": "ST", "code_challenge": "CH", "code_verifier": "ver"})

    async def build_url(**kwargs):
        state.calls.append(("build", kwargs))
        if state.login_error:
            raise state.login_error
        return "https://sso/auth?x=1"

    monkeypatch.setattr(module, "build_authorization_url", build_url)
    monkeypatch.setattr(module, "pop_oauth_state", lambda s: state.popped_state if s == "ST" else None)

    async def exchange(**kwargs):
        state.calls.append(("exchange", kwargs))
        if state.exchange_error:
            raise state.exchange_error
        return state.exchange_result or {"username": "alice"}

    monkeypatch.setattr(module, "exchange_authorization_code", exchange)

    def login(profile, request, response, auth_method):
        state.calls.append(("login", profile, auth_method))
        return {"access_token": "A"}

    monkeypatch.setattr(module, "complete_application_login", login)
    monkeypatch.setattr(module, "store_sso_ticket", lambda payload: "tick/et+1")
    monkeypatch.setattr(module, "pop_sso_ticket", lambda t: dict(state.ticket_payload) if t == "good" else None)
    monkeypatch.setattr(module, "set_client_binding_cookie", lambda response, binding: state.bindings.append(binding))
    monkeypatch.setattr(module, "log_keycloak_sso_failure", lambda **kw: state.failures.append(kw))
    monkeypatch.setattr(module, "record_keycloak_sso_error", lambda request, exc: state.recorded.append(exc))
    return state


def _client(make_client):
    return make_client(module.router, user=None)


class TestProviders:
    async def test_enabled(self, make_client, kc):
        body = (await _client(make_client).get(BASE + "/providers")).json()

        assert body == {"keycloak_enabled": True, "keycloak_login_url": "http://test/api/auth/sso/keycloak/login"}

    async def test_disabled_hides_the_login_url(self, make_client, kc):
        kc.enabled = False

        assert (await _client(make_client).get(BASE + "/providers")).json() == {"keycloak_enabled": False, "keycloak_login_url": None}


class TestLogin:
    async def test_redirects_to_keycloak_with_no_cache_headers(self, make_client, kc):
        response = await _client(make_client).get(BASE + "/keycloak/login", follow_redirects=False)

        assert response.status_code == 302 and response.headers["location"] == "https://sso/auth?x=1"
        assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"
        build = kc.calls[0][1]
        assert build == {"request_base_url": "http://test", "state": "ST", "code_challenge": "CH"}

    async def test_disabled(self, make_client, kc):
        kc.enabled = False

        response = await _client(make_client).get(BASE + "/keycloak/login", follow_redirects=False)

        assert response.status_code == 503 and len(kc.failures) == 1 and kc.calls == []

    async def test_provider_errors_are_recorded_and_returned(self, make_client, kc):
        kc.login_error = _sso_error(503, "authorization_endpoint не найден")

        response = await _client(make_client).get(BASE + "/keycloak/login", follow_redirects=False)

        assert response.status_code == 503 and response.json()["detail"] == "authorization_endpoint не найден"
        assert kc.recorded == [kc.login_error]


class TestCallback:
    URL = BASE + "/keycloak/callback"

    async def _get(self, make_client, **params):
        return await _client(make_client).get(self.URL, params=params, follow_redirects=False)

    @staticmethod
    def _error(response):
        return parse_qs(urlparse(response.headers["location"]).query)["sso_error"][0]

    async def test_success_puts_the_ticket_in_the_fragment_not_the_query(self, make_client, kc):
        response = await self._get(make_client, code="C", state="ST")

        assert response.status_code == 302
        location = response.headers["location"]
        assert location == f"{FRONT}#ticket=tick%2Fet%2B1"
        assert urlparse(location).query == "" and response.headers["cache-control"] == "no-store"
        assert ("exchange", {"code": "C", "code_verifier": "ver", "request_base_url": "http://test"}) in kc.calls
        assert any(c[0] == "login" and c[2] == "keycloak" for c in kc.calls)

    async def test_frontend_not_configured_is_a_plain_error(self, make_client, kc):
        kc.callback_error = _sso_error(500, "Не настроен KEYCLOAK_FRONTEND_CALLBACK_URL")

        response = await self._get(make_client, code="C", state="ST")

        assert response.status_code == 500 and kc.recorded == [kc.callback_error]

    async def test_provider_error_redirects_with_the_description(self, make_client, kc):
        response = await self._get(make_client, error="access_denied", error_description="User denied")

        assert response.headers["location"].startswith(FRONT + "?") and self._error(response) == "User denied"
        assert kc.failures[0]["status_code"] == 401 and kc.calls == []

    async def test_provider_error_text_cannot_inject_extra_query_parameters(self, make_client, kc):
        response = await self._get(make_client, error="x", error_description="a&sso_error=evil&admin=1")

        assert parse_qs(urlparse(response.headers["location"]).query) == {"sso_error": ["a&sso_error=evil&admin=1"]}

    @pytest.mark.parametrize("params", [{"code": "C"}, {"state": "ST"}, {}])
    async def test_missing_code_or_state(self, make_client, kc, params):
        response = await self._get(make_client, **params)

        assert response.status_code == 302 and "code или state" in self._error(response)
        assert kc.failures[0]["status_code"] == 400 and kc.calls == []

    async def test_unknown_state_does_not_reach_keycloak(self, make_client, kc):
        response = await self._get(make_client, code="C", state="FORGED")

        assert "истекла" in self._error(response) and kc.calls == []

    async def test_sso_error_is_forwarded_to_the_frontend(self, make_client, kc):
        kc.exchange_error = _sso_error(403, "Нет нужной роли")

        response = await self._get(make_client, code="C", state="ST")

        assert self._error(response) == "Нет нужной роли" and kc.recorded == [kc.exchange_error]

    async def test_http_exception_detail_is_forwarded(self, make_client, kc):
        kc.exchange_error = HTTPException(status_code=429, detail="Слишком много сессий")

        assert self._error(await self._get(make_client, code="C", state="ST")) == "Слишком много сессий"
        assert kc.failures[-1]["status_code"] == 429

    async def test_non_string_http_detail_gets_a_generic_message(self, make_client, kc):
        kc.exchange_error = HTTPException(status_code=400, detail={"secret": "x"})

        assert self._error(await self._get(make_client, code="C", state="ST")) == "Ошибка входа через SSO"

    async def test_unexpected_errors_do_not_leak_details(self, make_client, kc):
        kc.exchange_error = RuntimeError("password=hunter2 at db:5432")

        response = await self._get(make_client, code="C", state="ST")

        assert self._error(response) == "Внутренняя ошибка SSO" and "hunter2" not in response.headers["location"]
        assert kc.failures[-1]["status_code"] == 500


class TestExchange:
    URL = BASE + "/keycloak/exchange"

    async def test_good_ticket_returns_the_login_payload(self, make_client, kc):
        response = await _client(make_client).post(self.URL, json={"ticket": "good"})

        assert response.status_code == 200 and response.json() == {"access_token": "A"}
        assert kc.bindings == ["b1"]  # служебное поле не уходит клиенту, а превращается в cookie

    async def test_no_binding_cookie_when_ticket_has_none(self, make_client, kc):
        kc.ticket_payload = {"access_token": "A"}

        await _client(make_client).post(self.URL, json={"ticket": "good"})

        assert kc.bindings == []

    @pytest.mark.parametrize("ticket", ["", "   "])
    async def test_empty_ticket(self, make_client, kc, ticket):
        response = await _client(make_client).post(self.URL, json={"ticket": ticket})

        assert response.status_code == 400 and kc.failures[0]["status_code"] == 400

    async def test_invalid_ticket(self, make_client, kc):
        response = await _client(make_client).post(self.URL, json={"ticket": "forged"})

        assert response.status_code == 401 and kc.bindings == []

    async def test_ticket_is_required(self, make_client, kc):
        assert (await _client(make_client).post(self.URL, json={})).status_code == 422

    async def test_whitespace_around_the_ticket_is_trimmed(self, make_client, kc):
        assert (await _client(make_client).post(self.URL, json={"ticket": "  good  "})).status_code == 200


class TestHelpers:
    def test_secure_redirect_headers(self):
        response = module._secure_redirect("https://x/y")

        assert response.status_code == 302 and response.headers["pragma"] == "no-cache"

    def test_error_redirect_encodes_the_message(self):
        response = module._redirect_sso_error("https://x/cb", "a b&c=d")

        assert response.headers["location"] == "https://x/cb?sso_error=a+b%26c%3Dd"
