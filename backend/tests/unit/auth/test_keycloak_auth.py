"""keycloak_auth: настройки, подписанный OAuth state (PKCE), SSO-ticket, профиль из claims."""

import base64
import hashlib
import json

import jwt
import pytest

from backend.auth import keycloak_auth as kc
from backend.auth.sso_logging import KeycloakSsoError

pytestmark = pytest.mark.unit

ENV = ("KEYCLOAK_ENABLED", "KEYCLOAK_ISSUER", "KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET", "KEYCLOAK_REDIRECT_URI", "KEYCLOAK_FRONTEND_CALLBACK_URL",
       "KEYCLOAK_ADMIN_GROUPS", "KEYCLOAK_REQUIRED_ROLES", "KEYCLOAK_GROUPS_CLAIM", "KEYCLOAK_LDAP_GROUPS_CLAIM", "KEYCLOAK_USERNAME_CLAIM",
       "KEYCLOAK_FRONTEND_URL", "FRONTEND_URL", "FRONTEND_PORT")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    kc.reset_sso_handoff_store()
    monkeypatch.setenv("SSO_TICKET_REUSE_GRACE_SECONDS", "0")


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ENABLED", "true")
    monkeypatch.setenv("KEYCLOAK_ISSUER", "https://sso.corp/realms/main/")
    monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "astra")


class TestSettings:
    def test_defaults(self):
        s = kc.get_keycloak_settings()

        assert s["enabled"] is False and s["issuer"] == "" and s["groups_claim"] == "groups"
        assert s["ldap_groups_claim"] == "ldap" and s["username_claim"] == "preferred_username"
        assert s["admin_groups"] == [] and s["client_secret"] is None

    def test_values_are_normalized(self, monkeypatch, enabled):
        monkeypatch.setenv("KEYCLOAK_ADMIN_GROUPS", " Admins , ,ROOT ")
        monkeypatch.setenv("KEYCLOAK_REQUIRED_ROLES", "Astra-User")

        s = kc.get_keycloak_settings()

        assert s["issuer"] == "https://sso.corp/realms/main"  # хвостовой / срезан
        assert s["admin_groups"] == ["admins", "root"] and s["required_roles"] == ["astra-user"]

    @pytest.mark.parametrize(("value", "expected"), [("1", True), ("YES", True), ("on", True), ("true", True), ("0", False), ("", False), ("enabled", False)])
    def test_boolean_flag_parsing(self, monkeypatch, value, expected):
        monkeypatch.setenv("KEYCLOAK_ENABLED", value)

        assert kc.get_keycloak_settings()["enabled"] is expected

    def test_enabled_needs_issuer_and_client(self, monkeypatch):
        monkeypatch.setenv("KEYCLOAK_ENABLED", "true")
        assert kc.is_keycloak_enabled() is False
        monkeypatch.setenv("KEYCLOAK_ISSUER", "https://sso")
        assert kc.is_keycloak_enabled() is False
        monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "astra")
        assert kc.is_keycloak_enabled() is True

    def test_disabled_flag_wins(self, monkeypatch, enabled):
        monkeypatch.setenv("KEYCLOAK_ENABLED", "false")

        assert kc.is_keycloak_enabled() is False


class TestRedirectUris:
    def test_explicit_redirect_uri_wins(self, monkeypatch):
        monkeypatch.setenv("KEYCLOAK_REDIRECT_URI", "https://app/cb")

        assert kc.resolve_keycloak_redirect_uri("http://internal:8000/") == "https://app/cb"

    def test_derived_from_request_base(self):
        assert kc.resolve_keycloak_redirect_uri("http://internal:8000/") == "http://internal:8000/api/auth/sso/keycloak/callback"

    def test_frontend_callback_sources_in_priority_order(self, monkeypatch):
        monkeypatch.setenv("FRONTEND_PORT", "http://fe3/")
        monkeypatch.setenv("FRONTEND_URL", "http://fe2/")
        assert kc.resolve_frontend_callback_url() == "http://fe2/login/sso-callback"
        monkeypatch.setenv("KEYCLOAK_FRONTEND_URL", "http://fe1")
        assert kc.resolve_frontend_callback_url() == "http://fe1/login/sso-callback"
        monkeypatch.setenv("KEYCLOAK_FRONTEND_CALLBACK_URL", "https://exact/cb")
        assert kc.resolve_frontend_callback_url() == "https://exact/cb"

    async def test_authorization_url_carries_pkce_parameters(self, monkeypatch, enabled):
        from urllib.parse import parse_qs, urlparse

        async def metadata(settings):
            return {"authorization_endpoint": "https://sso.corp/auth"}

        monkeypatch.setattr(kc, "_fetch_oidc_metadata", metadata)

        url = await kc.build_authorization_url(request_base_url="http://b", state="S", code_challenge="C")
        query = parse_qs(urlparse(url).query)

        assert url.startswith("https://sso.corp/auth?")
        assert query["state"] == ["S"] and query["code_challenge"] == ["C"] and query["code_challenge_method"] == ["S256"]
        assert query["redirect_uri"] == ["http://b/api/auth/sso/keycloak/callback"] and query["response_type"] == ["code"]


class TestOauthState:
    def test_cannot_start_when_disabled(self):
        with pytest.raises(KeycloakSsoError) as exc:
            kc.create_oauth_login_state()

        assert exc.value.status_code == 503

    def test_roundtrip_returns_the_verifier(self, enabled):
        created = kc.create_oauth_login_state()

        assert kc.pop_oauth_state(created["state"]) == {"code_verifier": created["code_verifier"]}

    def test_pkce_challenge_matches_the_verifier(self, enabled):
        created = kc.create_oauth_login_state()
        digest = hashlib.sha256(created["code_verifier"].encode()).digest()

        assert created["code_challenge"] == base64.urlsafe_b64encode(digest).decode().rstrip("=")
        assert len(created["code_verifier"]) >= 43

    def test_every_state_is_unique(self, enabled):
        assert kc.create_oauth_login_state()["state"] != kc.create_oauth_login_state()["state"]

    def test_tampered_signature_or_payload_is_rejected(self, enabled):
        state = kc.create_oauth_login_state()["state"]
        payload, sig = state.split(".")
        forged_payload = kc._b64url_encode(json.dumps({"exp": 9999999999, "cv": "evil"}).encode())

        assert kc.pop_oauth_state(f"{forged_payload}.{sig}") is None
        assert kc.pop_oauth_state(f"{payload}.{kc._b64url_encode(b'x' * 32)}") is None

    @pytest.mark.parametrize("state", ["", "nodot", ".", "a.b", "!!!.???", "💥.💥"])
    def test_garbage_is_rejected(self, enabled, state):
        assert kc.pop_oauth_state(state) is None

    def test_expired_state_is_rejected(self, enabled, monkeypatch):
        state = kc.create_oauth_login_state()["state"]
        real = kc.time.time()
        monkeypatch.setattr(kc.time, "time", lambda: real + kc._OAUTH_STATE_TTL_SECONDS + 5)

        assert kc.pop_oauth_state(state) is None

    def test_state_signed_with_another_secret_is_rejected(self, enabled, monkeypatch):
        state = kc.create_oauth_login_state()["state"]
        monkeypatch.setenv("JWT_SECRET", "another-secret-value")

        assert kc.pop_oauth_state(state) is None

    def test_state_without_verifier_is_rejected(self, enabled):
        import hmac

        payload = kc._b64url_encode(json.dumps({"exp": 9999999999, "cv": ""}).encode())
        sig = kc._b64url_encode(hmac.new(kc._sso_hmac_secret(), payload.encode(), hashlib.sha256).digest())

        assert kc.pop_oauth_state(f"{payload}.{sig}") is None

    def test_missing_signing_secret(self, enabled, monkeypatch):
        monkeypatch.delenv("JWT_SECRET", raising=False)
        monkeypatch.delenv("JWT_REFRESH_SECRET", raising=False)

        with pytest.raises(KeycloakSsoError) as exc:
            kc.create_oauth_login_state()

        assert exc.value.status_code == 500

    def test_state_is_one_time(self, enabled):
        state = kc.create_oauth_login_state()["state"]

        assert kc.pop_oauth_state(state) is not None
        assert kc.pop_oauth_state(state) is None

    def test_consumed_states_are_pruned_after_expiry(self, enabled, monkeypatch):
        state = kc.create_oauth_login_state()["state"]
        assert kc.pop_oauth_state(state) is not None
        real = kc.time.time()
        monkeypatch.setattr(kc.time, "time", lambda: real + kc._OAUTH_STATE_TTL_SECONDS + 5)

        assert kc.pop_oauth_state(state) is None  # истёк
        assert kc._consume_oauth_state("fresh-sig", real + 10_000) is True
        assert state.split(".", 1)[1] not in kc._consumed_oauth_states


class TestSsoTicket:
    def test_roundtrip(self):
        ticket = kc.store_sso_ticket({"access_token": "a", "user": {"username": "alice"}})

        assert kc.pop_sso_ticket(ticket) == {"access_token": "a", "user": {"username": "alice"}}

    def test_handoff_code_stays_short_even_with_large_tokens(self):
        # Регресс: Fernet+JWT в URL давал ticket_len≈5KB и ломал Location/#ticket.
        fat = {
            "access_token": "A" * 2000,
            "refresh_token": "R" * 2000,
            "user": {"username": "alice", "groups": ["g"] * 50},
        }
        ticket = kc.store_sso_ticket(fat)

        assert len(ticket) < 80
        assert "A" * 20 not in ticket and "R" * 20 not in ticket
        assert kc.pop_sso_ticket(ticket) == fat

    def test_ticket_is_one_time(self):
        ticket = kc.store_sso_ticket({"access_token": "a"})

        assert kc.pop_sso_ticket(ticket) == {"access_token": "a"}
        assert kc.pop_sso_ticket(ticket) is None  # повторный обмен отклонён (grace=0)

    def test_reuse_within_grace_window_is_tolerated(self, monkeypatch):
        monkeypatch.setenv("SSO_TICKET_REUSE_GRACE_SECONDS", "30")
        ticket = kc.store_sso_ticket({"access_token": "a"})

        assert kc.pop_sso_ticket(ticket) == {"access_token": "a"}
        assert kc.pop_sso_ticket(ticket) == {"access_token": "a"}

    def test_each_ticket_is_consumed_independently(self):
        first = kc.store_sso_ticket({"n": 1})
        second = kc.store_sso_ticket({"n": 2})

        assert kc.pop_sso_ticket(first) == {"n": 1}
        assert kc.pop_sso_ticket(second) == {"n": 2}

    def test_tokens_are_not_present_in_the_handoff_code(self):
        ticket = kc.store_sso_ticket({"access_token": "SECRET-ACCESS", "refresh_token": "SECRET-REFRESH"})

        assert "SECRET-ACCESS" not in ticket and "SECRET-REFRESH" not in ticket

    def test_legacy_jwt_ticket_is_rejected(self):
        legacy = jwt.encode(
            {"exp": 9999999999, "typ": "sso_ticket", "jti": "j", "pld": {"access_token": "a"}},
            kc.JWT_REFRESH_SECRET, algorithm=kc.JWT_ALGORITHM,
        )

        assert kc.pop_sso_ticket(legacy) is None

    def test_expired_ticket(self, monkeypatch):
        ticket = kc.store_sso_ticket({"a": 1})
        real = kc.time.time()
        monkeypatch.setattr(kc.time, "time", lambda: real + kc._SSO_TICKET_TTL_SECONDS + 5)

        assert kc.pop_sso_ticket(ticket) is None

    def test_unknown_code_is_rejected(self):
        assert kc.pop_sso_ticket("totally-unknown-handoff-code") is None

    @pytest.mark.parametrize("ticket", ["", "a.b.c", "x.y"])
    def test_garbage(self, ticket):
        assert kc.pop_sso_ticket(ticket) is None

    def test_store_requires_a_secret(self, monkeypatch):
        monkeypatch.setattr(kc, "JWT_REFRESH_SECRET", "")

        with pytest.raises(KeycloakSsoError):
            kc.store_sso_ticket({})
        assert kc.pop_sso_ticket("x.y.z") is None


class TestClaims:
    SETTINGS = {
        "client_id": "astra", "groups_claim": "groups", "ldap_groups_claim": "ldap", "username_claim": "preferred_username",
        "admin_groups": ["admins"], "required_roles": [],
    }

    def profile(self, claims, **override):
        return kc.build_user_profile_from_claims(claims, {**self.SETTINGS, **override})

    def test_basic_profile(self):
        p = self.profile({"preferred_username": " alice ", "email": "a@corp", "name": "Alice A", "picture": "http://x/a.png", "azp": "astra"})

        assert p["username"] == p["user_id"] == "alice" and p["email"] == "a@corp" and p["full_name"] == "Alice A"
        assert p["avatar_url"] == "http://x/a.png" and p["azp"] == "astra" and p["auth_method"] == "keycloak"
        assert p["is_admin"] is False and p["is_active"] is True

    @pytest.mark.parametrize(
        ("claims", "expected"),
        [({"email": "bob@corp"}, "bob"), ({"sub": "uuid-1"}, "uuid-1"), ({"custom": "carol", "preferred_username": "x"}, "carol")],
    )
    def test_username_fallbacks(self, claims, expected):
        assert self.profile(claims, username_claim="custom")["username"] == expected

    def test_no_username_is_an_error(self):
        with pytest.raises(KeycloakSsoError) as exc:
            self.profile({"email": ""})

        assert exc.value.status_code == 401

    def test_roles_are_collected_from_every_claim_and_lowercased(self):
        claims = {
            "preferred_username": "a",
            "groups": ["Team-A", "", None],
            "realm_access": {"roles": ["Realm-R"]},
            "resource_access": {"astra": {"roles": ["Client-R"]}, "other": {"roles": ["Foreign"]}},
            "ldap": "AD-Group",
        }

        assert self.profile(claims)["groups"] == ["ad-group", "client-r", "realm-r", "team-a"]

    def test_garbage_claim_shapes_do_not_crash(self):
        claims = {"preferred_username": "a", "groups": 5, "realm_access": "x", "resource_access": {"astra": []}, "ldap": {"a": 1}}

        assert self.profile(claims)["groups"] == []

    def test_admin_detection(self):
        assert self.profile({"preferred_username": "a", "groups": ["Admins"]})["is_admin"] is True
        assert self.profile({"preferred_username": "a", "groups": ["users"]})["is_admin"] is False
        assert self.profile({"preferred_username": "a", "groups": ["Admins"]}, admin_groups=[])["is_admin"] is False

    def test_required_roles_gate_the_login(self):
        with pytest.raises(KeycloakSsoError) as exc:
            self.profile({"preferred_username": "a", "groups": ["users"]}, required_roles=["astra-user"])
        assert exc.value.status_code == 403

        assert self.profile({"preferred_username": "a", "realm_access": {"roles": ["Astra-User"]}}, required_roles=["astra-user"])

    def test_azp_falls_back_to_client_id(self):
        assert self.profile({"preferred_username": "a"})["azp"] == "astra"
        assert self.profile({"preferred_username": "a", "client_id": "other"})["azp"] == "other"

    def test_group_names_from_a_nested_keycloak_path_are_kept_whole(self):
        # Здесь путь не обрезается (в отличие от agent_platform_roles): «/team/Admins» ≠ «admins».
        assert self.profile({"preferred_username": "a", "groups": ["/team/Admins"]})["is_admin"] is False
