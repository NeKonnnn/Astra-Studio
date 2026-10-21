"""auth/jwt_handler: выпуск и проверка токенов, привязка к браузеру, лимит сессий."""

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from jose import jwt

pytestmark = pytest.mark.unit


@pytest.fixture
def jh(monkeypatch):
    """Модуль jwt_handler с чистым состоянием и перехваченным CEF-логом."""
    from backend.auth import jwt_handler

    jwt_handler._active_sessions_by_user.clear()
    jwt_handler._client_bindings.clear()
    events = []
    monkeypatch.setattr(
        jwt_handler,
        "log_cef_event",
        lambda code, **kwargs: events.append((code, kwargs)),
    )
    jwt_handler.cef_events = events
    yield jwt_handler
    jwt_handler._active_sessions_by_user.clear()
    jwt_handler._client_bindings.clear()


def _login(jh, username="alice", **profile):
    """Полный login: sid + сессия + привязка браузера + пара токенов."""
    sid = jh.create_session_id()
    jh.register_user_session(username, sid)
    binding = jh.create_client_binding(username, sid)
    access = jh.create_access_token(username, sid, user_profile=profile or None)
    refresh = jh.create_refresh_token(username, sid, user_profile=profile or None)
    return sid, binding, access, refresh


def _raw_token(jh, claims, secret=None):
    return jwt.encode(
        claims, secret or jh.JWT_ACCESS_SECRET, algorithm=jh.JWT_ALGORITHM
    )


def _claims(jh, **override):
    base = {
        "sub": "alice",
        "sid": "sid-1",
        "token_type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=5),
    }
    base.update(override)
    return base


class TestTokenIssue:
    def test_access_token_carries_standard_claims(self, jh):
        token = jh.create_access_token("alice", "sid-1")

        payload = jh._decode_jwt(token, jh.JWT_ACCESS_SECRET)

        assert payload["sub"] == "alice"
        assert payload["sid"] == "sid-1"
        assert payload["token_type"] == "access"
        assert payload["iss"] == jh.JWT_ISSUER
        assert payload["aud"] == jh.JWT_AUDIENCE
        assert payload["exp"] > payload["iat"]

    def test_session_id_generated_when_absent(self, jh):
        payload = jh._decode_jwt(
            jh.create_access_token("alice"), jh.JWT_ACCESS_SECRET
        )

        assert len(payload["sid"]) == 36

    def test_refresh_token_signed_with_refresh_secret(self, jh):
        token = jh.create_refresh_token("alice", "sid-1")

        assert jh._decode_jwt(token, jh.JWT_REFRESH_SECRET)["token_type"] == "refresh"
        with pytest.raises(Exception):
            jh._decode_jwt(token, jh.JWT_ACCESS_SECRET)

    def test_ttl_comes_from_env_constants(self, jh):
        payload = jh._decode_jwt(
            jh.create_access_token("alice", "s"), jh.JWT_ACCESS_SECRET
        )
        ttl = payload["exp"] - payload["iat"]

        assert ttl == jh.JWT_ACCESS_TOKEN_EXPIRE_SECONDS


class TestProfileClaims:
    def test_profile_is_compacted_into_claims(self, jh):
        token = jh.create_access_token(
            "alice",
            "sid-1",
            user_profile={
                "auth_method": "sso",
                "azp": "astra-web",
                "is_admin": True,
                "email": "a@example.com",
                "full_name": "Alice A",
            },
        )

        payload = jh._decode_jwt(token, jh.JWT_ACCESS_SECRET)

        assert payload["am"] == "sso"
        assert payload["azp"] == "astra-web"
        assert payload["adm"] is True
        assert payload["email"] == "a@example.com"
        assert payload["name"] == "Alice A"

    def test_groups_are_merged_deduplicated_case_insensitively(self, jh):
        claims = {}
        jh._apply_user_profile_claims(
            claims,
            {"groups": ["Admins", " devs ", ""], "ldap_groups": ["admins", "Ops"]},
        )

        assert claims["grp"] == ["Admins", "devs", "Ops"]

    def test_groups_capped_at_64(self, jh):
        claims = {}
        jh._apply_user_profile_claims(
            claims, {"groups": [f"g{i}" for i in range(100)]}
        )

        assert len(claims["grp"]) == 64

    def test_http_avatar_becomes_pic_and_is_truncated(self, jh):
        claims = {}
        jh._apply_user_profile_claims(
            claims, {"avatar_url": "https://cdn.example.com/" + "a" * 600}
        )

        assert claims["pic"].startswith("https://cdn.example.com/")
        assert len(claims["pic"]) == 512

    def test_local_avatar_becomes_flag(self, jh):
        claims = {}
        jh._apply_user_profile_claims(claims, {"avatar_url": "/api/auth/me/avatar"})

        assert claims["hav"] is True
        assert "pic" not in claims

    def test_non_admin_has_no_adm_claim(self, jh):
        claims = {}
        jh._apply_user_profile_claims(claims, {"is_admin": False})

        assert "adm" not in claims

    def test_empty_profile_changes_nothing(self, jh):
        claims = {}
        jh._apply_user_profile_claims(claims, None)

        assert claims == {}

    def test_user_dict_defaults(self, jh):
        user = jh._user_dict_from_payload({"sub": "alice", "sid": "s"})

        assert user["username"] == "alice"
        assert user["is_admin"] is False
        assert user["auth_method"] == "ldap"
        assert user["groups"] == []

    def test_user_dict_roundtrip_with_avatar_and_groups(self, jh):
        user = jh._user_dict_from_payload(
            {"sub": "alice", "grp": ["a", "b"], "hav": True, "adm": True, "am": "sso"}
        )

        assert user["ldap_groups"] == ["a", "b"]
        assert user["avatar_url"] == "/api/auth/me/avatar"
        assert user["is_admin"] is True
        assert user["auth_method"] == "sso"

    def test_scalar_group_claim_is_wrapped_into_list(self, jh):
        assert jh._user_dict_from_payload({"sub": "a", "grp": "solo"})["groups"] == [
            "solo"
        ]

    def test_legacy_user_id_used_when_sub_missing(self, jh):
        assert jh._subject_from_payload({"user_id": 42}) == "42"
        assert jh._subject_from_payload({}) is None


class TestStandardClaims:
    @pytest.mark.parametrize(
        ("claim", "expected"),
        [
            (None, True),
            ("astra-chat-api", True),
            (["x", "astra-chat-api"], True),
            ("other", False),
            (["x", "y"], False),
        ],
    )
    def test_aud_matching(self, jh, claim, expected):
        assert jh._aud_claim_matches("astra-chat-api", claim) is expected

    def test_legacy_token_without_iss_and_aud_is_accepted(self, jh):
        payload = jh._decode_jwt(_raw_token(jh, _claims(jh)), jh.JWT_ACCESS_SECRET)

        assert payload["sub"] == "alice"

    def test_wrong_audience_rejected(self, jh):
        token = _raw_token(jh, _claims(jh, aud="someone-else"))

        with pytest.raises(Exception, match="audience"):
            jh._decode_jwt(token, jh.JWT_ACCESS_SECRET)

    def test_wrong_issuer_rejected(self, jh):
        token = _raw_token(jh, _claims(jh, iss="evil"))

        with pytest.raises(Exception, match="issuer"):
            jh._decode_jwt(token, jh.JWT_ACCESS_SECRET)

    def test_standard_claims_check_can_be_skipped(self, jh):
        token = _raw_token(jh, _claims(jh, iss="evil", aud="x"))

        payload = jh._decode_jwt(
            token, jh.JWT_ACCESS_SECRET, verify_standard_claims=False
        )

        assert payload["iss"] == "evil"

    def test_alg_none_token_rejected(self, jh):
        import base64
        import json

        def b64(data):
            return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=")

        forged = b".".join(
            [b64({"alg": "none", "typ": "JWT"}), b64({"sub": "admin"}), b""]
        ).decode()

        with pytest.raises(Exception):
            jh._decode_jwt(forged, jh.JWT_ACCESS_SECRET)


class TestDecodeToken:
    def test_valid_login_decodes_to_user(self, jh):
        sid, binding, access, _ = _login(
            jh, "alice", email="a@example.com", is_admin=True
        )

        user = jh.decode_token(access, client_binding_id=binding)

        assert user["username"] == "alice"
        assert user["session_id"] == sid
        assert user["email"] == "a@example.com"
        assert user["is_admin"] is True

    def test_refresh_token_is_not_accepted_as_access(self, jh):
        _, binding, _, refresh = _login(jh)

        with pytest.raises(HTTPException) as exc:
            jh.decode_token(refresh, client_binding_id=binding)

        assert exc.value.status_code == 401

    def test_access_signed_token_with_refresh_type_is_rejected(self, jh):
        _, binding, _, _ = _login(jh)
        forged = _raw_token(jh, _claims(jh, sid="x", token_type="refresh"))

        with pytest.raises(HTTPException) as exc:
            jh.decode_token(forged, client_binding_id=binding)

        assert exc.value.detail == "Неверный тип токена"

    def test_expired_token_is_rejected(self, jh):
        _, binding, _, _ = _login(jh)
        token = _raw_token(
            jh, _claims(jh, exp=datetime.utcnow() - timedelta(seconds=5))
        )

        with pytest.raises(HTTPException) as exc:
            jh.decode_token(token, client_binding_id=binding)

        assert exc.value.status_code == 401
        assert any(
            kw["extra"]["reason"] == "token expired (exp)"
            for code, kw in jh.cef_events
            if code == "SEC007"
        )

    def test_tampered_signature_is_rejected(self, jh):
        _, binding, access, _ = _login(jh)

        with pytest.raises(HTTPException) as exc:
            jh.decode_token(access[:-3] + "abc", client_binding_id=binding)

        assert exc.value.status_code == 401

    def test_garbage_token_is_rejected(self, jh):
        with pytest.raises(HTTPException) as exc:
            jh.decode_token("not-a-jwt", client_binding_id="b")

        assert exc.value.status_code == 401

    def test_missing_sub_is_rejected(self, jh):
        token = _raw_token(jh, _claims(jh, sub=""))

        with pytest.raises(HTTPException) as exc:
            jh.decode_token(token, client_binding_id="b")

        assert exc.value.detail == "Неверный токен"

    def test_missing_binding_cookie_is_rejected(self, jh):
        _, _, access, _ = _login(jh)

        with pytest.raises(HTTPException) as exc:
            jh.decode_token(access)

        assert exc.value.detail == "Токен не привязан к сессии браузера"

    def test_binding_of_another_user_is_rejected(self, jh):
        _, _, alice_access, _ = _login(jh, "alice")
        _, bob_binding, _, _ = _login(jh, "bob")

        with pytest.raises(HTTPException) as exc:
            jh.decode_token(alice_access, client_binding_id=bob_binding)

        assert exc.value.detail == "Токен не соответствует сессии браузера"

    def test_unknown_binding_is_rejected(self, jh):
        _, _, access, _ = _login(jh)

        with pytest.raises(HTTPException) as exc:
            jh.decode_token(access, client_binding_id="never-issued")

        assert exc.value.detail == "Токен не соответствует сессии браузера"

    def test_binding_read_from_request_cookie(self, jh):
        class Req:
            cookies = {}

        _, binding, access, _ = _login(jh)
        Req.cookies = {jh.CLIENT_BINDING_COOKIE: binding}

        assert jh.decode_token(access, request=Req())["username"] == "alice"

    def test_logged_out_session_is_rejected(self, jh):
        sid, binding, access, _ = _login(jh)
        jh.revoke_user_session("alice", sid)
        # revoke снимает и привязку; вернём её, чтобы дойти до проверки сессии
        jh._client_bindings[binding] = {"user_id": "alice", "sid": sid}

        with pytest.raises(HTTPException) as exc:
            jh.decode_token(access, client_binding_id=binding)

        assert "сервер перезапущен" in exc.value.detail

    def test_token_without_sid_is_rejected(self, jh):
        token = _raw_token(jh, {**_claims(jh), "sid": None})

        with pytest.raises(HTTPException):
            jh.decode_token(token, client_binding_id="b")


class TestSessionSuperseded:
    def test_oldest_session_revoked_over_limit(self, jh, monkeypatch):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 1)
        sid1, binding1, access1, _ = _login(jh)
        _login(jh)  # второй login вытесняет первый
        jh._client_bindings[binding1] = {"user_id": "alice", "sid": sid1}

        with pytest.raises(HTTPException) as exc:
            jh.decode_token(access1, client_binding_id=binding1)

        assert "с другого устройства" in exc.value.detail

    def test_message_mentions_limit_when_many_allowed(self, jh, monkeypatch):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 2)
        sid1, binding1, access1, _ = _login(jh)
        _login(jh)
        _login(jh)
        jh._client_bindings[binding1] = {"user_id": "alice", "sid": sid1}

        with pytest.raises(HTTPException) as exc:
            jh.decode_token(access1, client_binding_id=binding1)

        assert "(2)" in exc.value.detail


class TestSignatureOnlyDecode:
    def test_restores_missing_binding_after_restart(self, jh):
        sid = "sid-ws"
        token = jh.create_access_token("alice", sid)

        user = jh.decode_token_signature_only(token, client_binding_id="fresh-binding")

        assert user["username"] == "alice"
        assert jh._client_bindings["fresh-binding"] == {"user_id": "alice", "sid": sid}

    def test_does_not_require_active_session(self, jh):
        token = jh.create_access_token("alice", "no-such-session")
        jh._client_bindings["b"] = {"user_id": "alice", "sid": "no-such-session"}

        assert jh.decode_token_signature_only(token, client_binding_id="b")

    def test_foreign_existing_binding_still_rejected(self, jh):
        _, bob_binding, _, _ = _login(jh, "bob")
        token = jh.create_access_token("alice", "s")

        with pytest.raises(HTTPException):
            jh.decode_token_signature_only(token, client_binding_id=bob_binding)

    def test_without_binding_rejected(self, jh):
        with pytest.raises(HTTPException):
            jh.decode_token_signature_only(jh.create_access_token("alice", "s"))

    def test_bad_signature_rejected(self, jh):
        with pytest.raises(HTTPException):
            jh.decode_token_signature_only("zzz", client_binding_id="b")


class TestDecodeRefresh:
    def test_valid_refresh(self, jh):
        _, binding, _, refresh = _login(jh)

        class Req:
            cookies = {jh.CLIENT_BINDING_COOKIE: binding}

        assert jh.decode_refresh_token(refresh, request=Req())["username"] == "alice"

    def test_access_token_is_not_accepted_as_refresh(self, jh):
        _, binding, access, _ = _login(jh)

        class Req:
            cookies = {jh.CLIENT_BINDING_COOKIE: binding}

        with pytest.raises(HTTPException):
            jh.decode_refresh_token(access, request=Req())

    def test_refresh_without_binding_rejected(self, jh):
        _, _, _, refresh = _login(jh)

        with pytest.raises(HTTPException) as exc:
            jh.decode_refresh_token(refresh)

        assert exc.value.detail == "Токен не привязан к сессии браузера"


class TestSessions:
    def test_first_registration_flagged(self, jh):
        first = jh.register_user_session("alice", "s1")
        second = jh.register_user_session("alice", "s2")

        assert first == {"invalidated_count": 0, "first_seen": True}
        assert second == {"invalidated_count": 0, "first_seen": False}

    def test_eviction_drops_oldest_and_logs_sec006(self, jh, monkeypatch):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 2)
        for sid in ("s1", "s2"):
            jh.register_user_session("alice", sid)

        result = jh.register_user_session("alice", "s3")

        assert result["invalidated_count"] == 1
        assert jh._active_sessions_by_user["alice"] == ["s2", "s3"]
        assert [c for c, _ in jh.cef_events] == ["SEC006"]

    def test_reregistering_moves_session_to_newest(self, jh):
        for sid in ("s1", "s2", "s3"):
            jh.register_user_session("alice", sid)

        jh.register_user_session("alice", "s1")

        assert jh._active_sessions_by_user["alice"] == ["s2", "s3", "s1"]

    def test_evicted_session_loses_its_binding(self, jh, monkeypatch):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 1)
        jh.register_user_session("alice", "s1")
        old = jh.create_client_binding("alice", "s1")

        jh.register_user_session("alice", "s2")

        assert old not in jh._client_bindings

    def test_revoke_single_session_keeps_others(self, jh):
        jh.register_user_session("alice", "s1")
        jh.register_user_session("alice", "s2")

        jh.revoke_user_session("alice", "s1")

        assert jh._active_sessions_by_user["alice"] == ["s2"]

    def test_revoking_last_session_removes_user(self, jh):
        jh.register_user_session("alice", "s1")

        jh.revoke_user_session("alice", "s1")

        assert "alice" not in jh._active_sessions_by_user

    def test_revoke_all_sessions_and_bindings(self, jh):
        jh.register_user_session("alice", "s1")
        jh.register_user_session("alice", "s2")
        binding = jh.create_client_binding("alice", "s2")

        jh.revoke_user_session("alice")

        assert "alice" not in jh._active_sessions_by_user
        assert binding not in jh._client_bindings

    def test_revoke_unknown_is_noop(self, jh):
        jh.revoke_user_session("nobody")
        jh.revoke_user_session("nobody", "sid")

    def test_session_is_active(self, jh):
        jh.register_user_session("alice", "s1")

        assert jh._session_is_active("alice", "s1") is True
        assert jh._session_is_active("alice", "other") is False
        assert jh._session_is_active("alice", None) is False

    def test_session_ids_unique(self, jh):
        assert jh.create_session_id() != jh.create_session_id()

    def test_server_instance_id_is_stable(self, jh):
        assert jh.get_server_instance_id() == jh.get_server_instance_id()


class TestClientBindings:
    def test_new_binding_replaces_previous_of_same_user(self, jh):
        first = jh.create_client_binding("alice", "s1")
        second = jh.create_client_binding("alice", "s2")

        assert first not in jh._client_bindings
        assert jh._client_bindings[second] == {"user_id": "alice", "sid": "s2"}

    def test_other_users_bindings_untouched(self, jh):
        bob = jh.create_client_binding("bob", "s1")
        jh.create_client_binding("alice", "s1")

        assert bob in jh._client_bindings

    def test_revoke_binding(self, jh):
        binding = jh.create_client_binding("alice", "s1")

        jh.revoke_client_binding(binding)
        jh.revoke_client_binding(None)

        assert binding not in jh._client_bindings

    def test_cookie_header_parsing(self, jh):
        header = f"a=1; {jh.CLIENT_BINDING_COOKIE}=xyz; b=2"

        assert jh.get_client_binding_id_from_cookie_header(header) == "xyz"
        assert jh.get_client_binding_id_from_cookie_header("a=1") is None
        assert jh.get_client_binding_id_from_cookie_header("") is None

    def test_request_without_cookies_attribute_is_safe(self, jh):
        assert jh.get_client_binding_id_from_request(None) is None
        assert jh.get_client_binding_id_from_request(object()) is None

    def test_set_cookie_flags(self, jh, monkeypatch):
        monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
        calls = {}

        class Resp:
            def set_cookie(self, **kwargs):
                calls.update(kwargs)

        jh.set_client_binding_cookie(Resp(), "bid")

        assert calls["key"] == jh.CLIENT_BINDING_COOKIE
        assert calls["httponly"] is True
        assert calls["secure"] is True
        assert calls["samesite"] == "lax"
        assert calls["max_age"] == jh.JWT_REFRESH_TOKEN_EXPIRE_SECONDS

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("true", True), ("1", True), ("YES", True), ("false", False), ("0", False)],
    )
    def test_cookie_secure_flag_parsing(self, jh, monkeypatch, raw, expected):
        monkeypatch.setenv("AUTH_COOKIE_SECURE", raw)

        assert jh._cookie_secure_flag() is expected


class TestHelpers:
    def test_bearer_parsing(self, jh):
        class Req:
            def __init__(self, value):
                self.headers = {"authorization": value} if value else {}

        assert jh._parse_bearer_token(Req("Bearer abc")) == "abc"
        assert jh._parse_bearer_token(Req("bearer   abc ")) == "abc"
        assert jh._parse_bearer_token(Req("Basic abc")) is None
        assert jh._parse_bearer_token(Req("Bearer")) is None
        assert jh._parse_bearer_token(Req(None)) is None

    def test_try_user_from_request(self, jh):
        sid, _, access, _ = _login(jh)

        class Req:
            headers = {"authorization": f"Bearer {access}"}

        user = jh.try_user_from_request(Req())

        assert user["username"] == "alice"
        assert user["session_id"] == sid

    def test_try_user_from_request_ignores_dead_session(self, jh):
        sid, _, access, _ = _login(jh)
        jh.revoke_user_session("alice", sid)

        class Req:
            headers = {"authorization": f"Bearer {access}"}

        assert jh.try_user_from_request(Req()) is None

    def test_try_user_from_request_ignores_refresh_and_garbage(self, jh):
        _, _, _, refresh = _login(jh)

        class Req:
            def __init__(self, value):
                self.headers = {"authorization": value}

        assert jh.try_user_from_request(Req(f"Bearer {refresh}")) is None
        assert jh.try_user_from_request(Req("Bearer junk")) is None

    def test_token_byte_length(self, jh):
        assert jh.jwt_token_byte_length("abc") == 3
        assert jh.jwt_token_byte_length(b"abcd") == 4
        assert jh.jwt_token_byte_length("ы") == 2

    def test_jwt_error_reason(self, jh):
        from jose import JWTError
        from jose.exceptions import ExpiredSignatureError

        assert jh._jwt_error_reason(ExpiredSignatureError()) == "token expired (exp)"
        assert jh._jwt_error_reason(JWTError("bad")) == "bad"
        assert "JWTError" in jh._jwt_error_reason(JWTError())
        assert "ValueError" in jh._jwt_error_reason(ValueError("x"))

    def test_required_env_int(self, jh, monkeypatch):
        monkeypatch.setenv("X_TTL", "30")
        assert jh._required_env_int("X_TTL") == 30
        monkeypatch.setenv("X_TTL", "0")
        with pytest.raises(RuntimeError, match="positive"):
            jh._required_env_int("X_TTL")
        monkeypatch.delenv("X_TTL")
        with pytest.raises(RuntimeError, match="Missing"):
            jh._required_env_int("X_TTL")

    def test_optional_env_str(self, jh, monkeypatch):
        monkeypatch.delenv("X_OPT", raising=False)
        assert jh._optional_env_str("X_OPT", "dflt") == "dflt"
        monkeypatch.setenv("X_OPT", "   ")
        assert jh._optional_env_str("X_OPT", "dflt") == "dflt"
        monkeypatch.setenv("X_OPT", " val ")
        assert jh._optional_env_str("X_OPT", "dflt") == "val"

    def test_peek_sub_ignores_expiry(self, jh):
        token = _raw_token(
            jh, _claims(jh, exp=datetime.utcnow() - timedelta(days=1), iss="x")
        )

        assert jh._peek_jwt_sub(token, "access") == "alice"
        assert jh._peek_jwt_sub("junk", "access") is None


class TestDependencies:
    @pytest.fixture
    def client(self, jh):
        import httpx
        from fastapi import Depends, FastAPI

        app = FastAPI()

        @app.get("/me")
        async def me(user=Depends(jh.get_current_user)):
            return {"username": user["username"], "can_use_agents": "can_use_agents" in user}

        @app.get("/maybe")
        async def maybe(user=Depends(jh.get_optional_user)):
            return {"user": user["username"] if user else None}

        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    async def test_current_user_requires_token(self, client):
        response = await client.get("/me")

        assert response.status_code in (401, 403)

    async def test_current_user_with_valid_login(self, jh, client):
        _, binding, access, _ = _login(jh)

        response = await client.get(
            "/me",
            headers={"Authorization": f"Bearer {access}"},
            cookies={jh.CLIENT_BINDING_COOKIE: binding},
        )

        assert response.status_code == 200
        assert response.json() == {"username": "alice", "can_use_agents": True}

    async def test_current_user_rejects_missing_binding_cookie(self, jh, client):
        _, _, access, _ = _login(jh)

        response = await client.get(
            "/me", headers={"Authorization": f"Bearer {access}"}
        )

        assert response.status_code == 401

    async def test_optional_user_anonymous(self, client):
        response = await client.get("/maybe")

        assert response.json() == {"user": None}

    async def test_optional_user_bad_token_is_anonymous(self, client):
        response = await client.get("/maybe", headers={"Authorization": "Bearer junk"})

        assert response.json() == {"user": None}
