"""keycloak_auth: HTTP-часть (discovery, обмен кода, userinfo) через respx, claims и вспомогательные функции."""

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
import respx

from backend.auth import keycloak_auth as kc
from backend.auth.sso_logging import KeycloakSsoError

pytestmark = pytest.mark.unit

ISSUER = "https://sso.corp/realms/main"
DISCOVERY = f"{ISSUER}/.well-known/openid-configuration"
TOKEN_URL = f"{ISSUER}/protocol/openid-connect/token"
USERINFO_URL = f"{ISSUER}/protocol/openid-connect/userinfo"
METADATA = {
    "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
    "token_endpoint": TOKEN_URL,
    "userinfo_endpoint": USERINFO_URL,
}

ENV = (
    "KEYCLOAK_ENABLED", "KEYCLOAK_ISSUER", "KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET",
    "KEYCLOAK_REDIRECT_URI", "KEYCLOAK_FRONTEND_CALLBACK_URL", "KEYCLOAK_ADMIN_GROUPS",
    "KEYCLOAK_REQUIRED_ROLES", "KEYCLOAK_GROUPS_CLAIM", "KEYCLOAK_LDAP_GROUPS_CLAIM",
    "KEYCLOAK_USERNAME_CLAIM", "KEYCLOAK_FRONTEND_URL", "FRONTEND_URL", "FRONTEND_PORT",
    "TLS_CERT_PATH", "LDAP_CA_CERT_PATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KEYCLOAK_ENABLED", "true")
    monkeypatch.setenv("KEYCLOAK_ISSUER", ISSUER + "/")
    monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "astra")
    monkeypatch.setattr(kc, "_oidc_metadata_cache", {"fetched_at": 0.0, "data": None})


def make_jwt(payload: dict) -> str:
    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc(payload)}.sig"


class TestFormatError:
    def test_type_message_and_body(self):
        resp = httpx.Response(500, text="внутренняя\nошибка")

        text = kc._format_error(ValueError("плохо"), http_response=resp)

        assert text.startswith("ValueError | плохо | response_body=внутренняя")
        assert "\n" not in text

    def test_message_equal_to_type_name_is_not_duplicated(self):
        class Err(Exception):
            pass

        assert kc._format_error(Err("Err")) == "Err"

    def test_unreadable_body_is_ignored(self):
        class Broken:
            @property
            def text(self):
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")

        assert kc._format_error(RuntimeError("x"), http_response=Broken()) == "RuntimeError | x"


class TestHttpVerify:
    def test_first_existing_bundle_wins(self, monkeypatch, tmp_path):
        bundle = tmp_path / "ca.pem"
        bundle.write_text("x")
        monkeypatch.setenv("TLS_CERT_PATH", str(tmp_path / "missing.pem"))
        monkeypatch.setenv("LDAP_CA_CERT_PATH", str(bundle))

        assert kc._http_verify() == str(bundle)

    def test_system_default_when_nothing_set(self):
        assert kc._http_verify() is True


class TestLogOutbound:
    def test_with_and_without_fields(self, caplog):
        kc._log_sso_outbound("step", "GET", "https://x/\nfake")
        kc._log_sso_outbound("step", "GET", "https://x", a="1", b=None, c="")
        # не падает; секреты/пустые поля не попадают в аргументы


class TestDiscovery:
    @respx.mock
    async def test_fetches_and_caches_metadata(self):
        route = respx.get(DISCOVERY).respond(200, json=METADATA)
        settings = kc.get_keycloak_settings()

        first = await kc._fetch_oidc_metadata(settings)
        second = await kc._fetch_oidc_metadata(settings)

        assert first == METADATA and second == METADATA
        assert route.call_count == 1  # второй раз - из кэша

    @respx.mock
    async def test_expired_cache_is_refetched(self, monkeypatch):
        route = respx.get(DISCOVERY).respond(200, json=METADATA)
        monkeypatch.setattr(kc, "_oidc_metadata_cache", {"fetched_at": 1.0, "data": {"old": True}})

        assert await kc._fetch_oidc_metadata(kc.get_keycloak_settings()) == METADATA
        assert route.call_count == 1

    @respx.mock
    async def test_http_error_becomes_503(self):
        respx.get(DISCOVERY).respond(502, text="bad gateway")

        with pytest.raises(KeycloakSsoError) as ei:
            await kc._fetch_oidc_metadata(kc.get_keycloak_settings())

        assert ei.value.status_code == 503
        assert "bad gateway" in ei.value.error_detail
        assert ei.value.log_args[0] == 502

    @respx.mock
    async def test_transport_error_becomes_503(self):
        respx.get(DISCOVERY).mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(KeycloakSsoError) as ei:
            await kc._fetch_oidc_metadata(kc.get_keycloak_settings())

        assert ei.value.status_code == 503 and "refused" in ei.value.error_detail
        assert kc._oidc_metadata_cache["data"] is None  # неудача не кэшируется


class TestFrontendCallbackFallback:
    def test_falls_back_to_settings_urls(self, monkeypatch):
        fake = SimpleNamespace(urls=SimpleNamespace(frontend_port="http://front:3000/"))
        monkeypatch.setattr("backend.settings.config.get_settings", lambda: fake)

        assert kc.resolve_frontend_callback_url() == "http://front:3000/login/sso-callback"

    def test_nothing_configured_raises_500(self, monkeypatch):
        fake = SimpleNamespace(urls=SimpleNamespace(frontend_port=""))
        monkeypatch.setattr("backend.settings.config.get_settings", lambda: fake)

        with pytest.raises(KeycloakSsoError) as ei:
            kc.resolve_frontend_callback_url()

        assert ei.value.status_code == 500

    def test_settings_failure_is_logged_then_raises(self, monkeypatch):
        def boom():
            raise ValueError("нет конфига")

        monkeypatch.setattr("backend.settings.config.get_settings", boom)

        with pytest.raises(KeycloakSsoError, match="KEYCLOAK_FRONTEND_CALLBACK_URL"):
            kc.resolve_frontend_callback_url()


class TestAuthorizationUrl:
    async def test_missing_authorization_endpoint(self, monkeypatch):
        async def meta(settings):
            return {}

        monkeypatch.setattr(kc, "_fetch_oidc_metadata", meta)

        with pytest.raises(KeycloakSsoError) as ei:
            await kc.build_authorization_url(request_base_url="http://b", state="s", code_challenge="c")

        assert ei.value.status_code == 503 and "authorization_endpoint" in ei.value.detail


class TestJwtPayloadDecoding:
    def test_valid_payload(self):
        assert kc._decode_jwt_payload_unverified(make_jwt({"sub": "u1"})) == {"sub": "u1"}

    @pytest.mark.parametrize("token", ["oneword", "a.!!!.c", "a.bm90LWpzb24.c", f"a.{base64.urlsafe_b64encode(b'[1]').decode()}.c"])
    def test_garbage_gives_empty_dict(self, token):
        assert kc._decode_jwt_payload_unverified(token) == {}


class TestMergeClaims:
    def test_empty_sides(self):
        assert kc._merge_claims({}, {"a": 1}) == {"a": 1}
        assert kc._merge_claims({"a": 1}, {}) == {"a": 1}

    def test_roles_and_profile_fields_are_added_without_overwrite(self):
        primary = {"name": "Иван", "email": ""}
        secondary = {
            "name": "Другой", "email": "i@x.ru", "picture": "http://p", "realm_access": {"roles": ["r"]},
            "groups": ["g"], "ldap": ["CN=A"], "preferred_username": "ivan",
        }

        merged = kc._merge_claims(primary, secondary)

        assert merged["name"] == "Иван"  # заполненное не затирается
        assert merged["email"] == "i@x.ru" and merged["picture"] == "http://p"
        assert merged["realm_access"] == {"roles": ["r"]} and merged["groups"] == ["g"]
        assert merged["ldap"] == ["CN=A"] and merged["preferred_username"] == "ivan"

    def test_custom_group_claims_are_copied(self, monkeypatch):
        monkeypatch.setenv("KEYCLOAK_GROUPS_CLAIM", "roles2")
        monkeypatch.setenv("KEYCLOAK_LDAP_GROUPS_CLAIM", "ad")

        merged = kc._merge_claims({"sub": "1"}, {"roles2": ["x"], "ad": ["y"]})

        assert merged["roles2"] == ["x"] and merged["ad"] == ["y"]


class TestUserinfo:
    async def test_no_endpoint_or_token(self):
        assert await kc._fetch_userinfo_claims("", METADATA) == {}
        assert await kc._fetch_userinfo_claims("tok", {}) == {}

    @respx.mock
    async def test_returns_dict_and_sends_bearer(self):
        route = respx.get(USERINFO_URL).respond(200, json={"picture": "http://p"})

        assert await kc._fetch_userinfo_claims("tok", METADATA) == {"picture": "http://p"}
        assert route.calls.last.request.headers["authorization"] == "Bearer tok"

    @respx.mock
    async def test_non_dict_body_is_ignored(self):
        respx.get(USERINFO_URL).respond(200, json=["x"])

        assert await kc._fetch_userinfo_claims("tok", METADATA) == {}

    @respx.mock
    async def test_http_error_gives_empty(self):
        respx.get(USERINFO_URL).respond(401, text="no")

        assert await kc._fetch_userinfo_claims("tok", METADATA) == {}

    @respx.mock
    async def test_transport_error_gives_empty(self):
        respx.get(USERINFO_URL).mock(side_effect=httpx.ConnectError("down"))

        assert await kc._fetch_userinfo_claims("tok", METADATA) == {}


class TestRolesFromClaims:
    def test_string_claims_and_resource_access(self):
        settings = kc.get_keycloak_settings()
        claims = {
            "groups": " Staff ",
            "resource_access": {"astra": {"roles": ["Dev", None]}},
            "ldap": "CN=Team",
        }

        assert kc._collect_roles_from_claims(claims, settings) == {"staff", "dev", "cn=team"}

    def test_ldap_claim_as_list(self):
        settings = kc.get_keycloak_settings()

        assert kc._collect_roles_from_claims({"ldap": ["CN=A", "", "CN=B"]}, settings) == {"cn=a", "cn=b"}


class TestExchangeAuthorizationCode:
    @pytest.fixture(autouse=True)
    def _meta(self, monkeypatch):
        async def meta(settings):
            return dict(METADATA)

        monkeypatch.setattr(kc, "_fetch_oidc_metadata", meta)

    @respx.mock
    async def test_success_builds_profile_with_merged_claims(self, monkeypatch):
        monkeypatch.setenv("KEYCLOAK_CLIENT_SECRET", "sec")
        id_token = make_jwt({"sub": "s1", "preferred_username": "ivan", "name": "Иван"})
        access = make_jwt({"realm_access": {"roles": ["admin"]}})
        monkeypatch.setenv("KEYCLOAK_ADMIN_GROUPS", "admin")
        token = respx.post(TOKEN_URL).respond(200, json={"id_token": id_token, "access_token": access})
        respx.get(USERINFO_URL).respond(200, json={"picture": "http://p", "email": "i@x.ru"})

        profile = await kc.exchange_authorization_code(code="c", code_verifier="v", request_base_url="http://b")

        sent = token.calls.last.request.content.decode()
        assert "client_secret=sec" in sent and "code_verifier=v" in sent and "grant_type=authorization_code" in sent
        assert profile["username"] == "ivan" and profile["is_admin"] is True
        assert profile["avatar_url"] == "http://p" and profile["email"] == "i@x.ru"
        assert profile["keycloak_sub"] == "s1" and profile["auth_method"] == "keycloak"

    async def test_missing_token_endpoint(self, monkeypatch):
        async def meta(settings):
            return {}

        monkeypatch.setattr(kc, "_fetch_oidc_metadata", meta)

        with pytest.raises(KeycloakSsoError) as ei:
            await kc.exchange_authorization_code(code="c", code_verifier="v", request_base_url="http://b")

        assert ei.value.status_code == 503 and "token_endpoint" in ei.value.detail

    @respx.mock
    async def test_rejection_status_is_401_and_keeps_body(self):
        respx.post(TOKEN_URL).respond(400, text="invalid_grant")

        with pytest.raises(KeycloakSsoError) as ei:
            await kc.exchange_authorization_code(code="c", code_verifier="v", request_base_url="http://b")

        assert ei.value.status_code == 401
        assert "status=400" in ei.value.error_detail and "invalid_grant" in ei.value.error_detail

    @respx.mock
    async def test_rejection_with_unreadable_body(self, monkeypatch):
        respx.post(TOKEN_URL).respond(403, content=b"x")

        def broken(self):
            raise ValueError("нет тела")

        monkeypatch.setattr(httpx.Response, "text", property(broken))

        with pytest.raises(KeycloakSsoError) as ei:
            await kc.exchange_authorization_code(code="c", code_verifier="v", request_base_url="http://b")

        assert ei.value.status_code == 401 and ei.value.error_detail == "HTTPStatusError | status=403"

    @respx.mock
    async def test_transport_error_is_503(self):
        respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("down"))

        with pytest.raises(KeycloakSsoError) as ei:
            await kc.exchange_authorization_code(code="c", code_verifier="v", request_base_url="http://b")

        assert ei.value.status_code == 503 and ei.value.log_exception is False

    @respx.mock
    async def test_non_json_response_is_unexpected_error_503(self):
        respx.post(TOKEN_URL).respond(200, text="<html>")

        with pytest.raises(KeycloakSsoError) as ei:
            await kc.exchange_authorization_code(code="c", code_verifier="v", request_base_url="http://b")

        assert ei.value.status_code == 503 and ei.value.log_exception is True

    @respx.mock
    async def test_empty_claims_are_401(self):
        respx.post(TOKEN_URL).respond(200, json={})

        with pytest.raises(KeycloakSsoError) as ei:
            await kc.exchange_authorization_code(code="c", code_verifier="v", request_base_url="http://b")

        assert ei.value.status_code == 401 and "данные пользователя" in ei.value.detail

    @respx.mock
    async def test_userinfo_failure_does_not_break_login(self):
        id_token = make_jwt({"preferred_username": "ivan"})
        respx.post(TOKEN_URL).respond(200, json={"id_token": id_token, "access_token": "opaque"})
        respx.get(USERINFO_URL).respond(500, text="boom")

        profile = await kc.exchange_authorization_code(code="c", code_verifier="v", request_base_url="http://b")

        assert profile["username"] == "ivan" and "avatar_url" not in profile
