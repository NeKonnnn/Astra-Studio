"""jwt_handler: хвосты (конфигурация, cookie-заголовок, ошибки refresh/ws-connect, optional user)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt

pytestmark = pytest.mark.unit


@pytest.fixture
def jh(monkeypatch):
    from backend.auth import jwt_handler

    jwt_handler._active_sessions_by_user.clear()
    jwt_handler._client_bindings.clear()
    events = []
    monkeypatch.setattr(jwt_handler, "log_cef_event", lambda code, **kw: events.append((code, kw)))
    jwt_handler.cef_events = events
    yield jwt_handler
    jwt_handler._active_sessions_by_user.clear()
    jwt_handler._client_bindings.clear()


def raw(jh, secret=None, **override):
    claims = {
        "sub": "alice", "sid": "sid-1", "token_type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=5),
    }
    claims.update(override)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, secret or jh.JWT_ACCESS_SECRET, algorithm=jh.JWT_ALGORITHM)


def login(jh, username="alice", **profile):
    sid = jh.create_session_id()
    jh.register_user_session(username, sid)
    binding = jh.create_client_binding(username, sid)
    return (
        sid, binding,
        jh.create_access_token(username, sid, user_profile=profile or None),
        jh.create_refresh_token(username, sid, user_profile=profile or None),
    )


def request_with(cookie=None, authorization=None):
    headers = {"authorization": authorization} if authorization else {}
    return SimpleNamespace(
        cookies={"astra_client_binding": cookie} if cookie else {}, headers=headers
    )


class TestConfiguration:
    def test_max_active_sessions_getter(self, jh, monkeypatch):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 7)

        assert jh.get_max_active_sessions() == 7

    def test_missing_secrets_fail_startup_check(self, jh, monkeypatch):
        monkeypatch.setattr(jh, "JWT_ACCESS_SECRET", "")
        monkeypatch.setattr(jh, "JWT_REFRESH_SECRET", "")

        with pytest.raises(RuntimeError, match="JWT_SECRET, JWT_REFRESH_SECRET"):
            jh._log_jwt_configuration()

    def test_single_missing_secret_is_named(self, jh, monkeypatch):
        monkeypatch.setattr(jh, "JWT_REFRESH_SECRET", "")

        with pytest.raises(RuntimeError) as ei:
            jh._log_jwt_configuration()

        assert str(ei.value).endswith("JWT_REFRESH_SECRET") and "JWT_SECRET," not in str(ei.value)

    def test_complete_config_passes(self, jh):
        jh._log_jwt_configuration()


class TestCookieHeader:
    def test_parser_failure_returns_none(self, jh, monkeypatch):
        class Boom:
            def load(self, header):
                raise RuntimeError("битый заголовок")

        monkeypatch.setattr(jh, "SimpleCookie", Boom)

        assert jh.get_client_binding_id_from_cookie_header("astra_client_binding=x") is None

    def test_empty_value_gives_none(self, jh):
        assert jh.get_client_binding_id_from_cookie_header("astra_client_binding=; a=b") is None


class TestByteLength:
    def test_bytes_str_and_other(self, jh):
        assert jh.jwt_token_byte_length(b"abc") == 3
        assert jh.jwt_token_byte_length("привет") == 12
        assert jh.jwt_token_byte_length(12345) == 5


class TestTryUserFromRequest:
    def _req(self, token):
        return SimpleNamespace(headers={"authorization": f"Bearer {token}"})

    def test_wrong_token_type_ignored(self, jh):
        assert jh.try_user_from_request(self._req(raw(jh, token_type="refresh"))) is None

    def test_token_without_subject_ignored(self, jh):
        assert jh.try_user_from_request(self._req(raw(jh, sub=None))) is None

    def test_no_authorization_header(self, jh):
        assert jh.try_user_from_request(SimpleNamespace(headers={})) is None

    def test_active_session_returns_minimal_user(self, jh):
        sid, _, access, _ = login(jh)

        user = jh.try_user_from_request(self._req(access))

        assert user["username"] == "alice" and user["session_id"] == sid and user["is_admin"] is False


class TestPeekSub:
    def test_without_secret_returns_none(self, jh, monkeypatch):
        monkeypatch.setattr(jh, "JWT_ACCESS_SECRET", "")

        assert jh._peek_jwt_sub("x", jh.JWT_TYPE_ACCESS) is None

    def test_garbage_token_returns_none(self, jh):
        assert jh._peek_jwt_sub("не-jwt", jh.JWT_TYPE_REFRESH) is None

    def test_expired_token_still_gives_sub(self, jh):
        token = raw(jh, exp=datetime.utcnow() - timedelta(hours=1))

        assert jh._peek_jwt_sub(token, jh.JWT_TYPE_ACCESS) == "alice"


class TestSessions:
    def test_zero_limit_leaves_no_user_entry(self, jh, monkeypatch):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 0)

        info = jh.register_user_session("alice", "s1")

        assert info["invalidated_count"] == 1
        assert "alice" not in jh._active_sessions_by_user

    def test_revoking_unknown_session_keeps_others(self, jh):
        jh.register_user_session("alice", "s1")

        jh.revoke_user_session("alice", "other")

        assert jh._active_sessions_by_user["alice"] == ["s1"]

    def test_missing_sid_is_rejected_directly(self, jh):
        with pytest.raises(HTTPException) as ei:
            jh._assert_active_session("alice", None, token_type=jh.JWT_TYPE_ACCESS)

        assert ei.value.status_code == 401 and ei.value.detail == "Сессия завершена"


class TestProfileClaims:
    def test_has_avatar_flag_with_non_http_url(self, jh):
        claims = {}

        jh._apply_user_profile_claims(claims, {"avatar_url": "relative/a.png", "has_avatar": True})

        assert claims == {"hav": True}

    def test_azp_and_pic_roundtrip_into_user_dict(self, jh):
        user = jh._user_dict_from_payload({"sub": "a", "azp": "astra", "pic": "http://p/x.png"})

        assert user["azp"] == "astra" and user["avatar_url"] == "http://p/x.png"


class TestWsConnectErrors:
    def test_wrong_token_type(self, jh):
        with pytest.raises(HTTPException) as ei:
            jh.decode_token_signature_only(raw(jh, token_type="refresh"))

        assert ei.value.status_code == 401 and ei.value.detail == "Неверный тип токена"

    def test_missing_subject(self, jh):
        with pytest.raises(HTTPException) as ei:
            jh.decode_token_signature_only(raw(jh, sub=None))

        assert ei.value.detail == "Неверный токен"

    def test_expired(self, jh):
        token = raw(jh, exp=datetime.utcnow() - timedelta(minutes=1))

        with pytest.raises(HTTPException) as ei:
            jh.decode_token_signature_only(token)

        assert ei.value.detail == "Не удалось валидировать токен"


class TestRefreshErrors:
    def test_wrong_token_type(self, jh):
        token = raw(jh, secret=jh.JWT_REFRESH_SECRET, token_type="access")

        with pytest.raises(HTTPException) as ei:
            jh.decode_refresh_token(token)

        assert ei.value.detail == "Неверный тип токена"

    def test_missing_subject(self, jh):
        token = raw(jh, secret=jh.JWT_REFRESH_SECRET, token_type="refresh", sub=None)

        with pytest.raises(HTTPException) as ei:
            jh.decode_refresh_token(token)

        assert ei.value.detail == "Неверный refresh токен"

    def test_expired(self, jh):
        token = raw(
            jh, secret=jh.JWT_REFRESH_SECRET, token_type="refresh",
            exp=datetime.utcnow() - timedelta(minutes=1),
        )

        with pytest.raises(HTTPException) as ei:
            jh.decode_refresh_token(token)

        assert ei.value.detail == "Не удалось валидировать refresh токен"

    def test_garbage_token(self, jh):
        with pytest.raises(HTTPException) as ei:
            jh.decode_refresh_token("garbage")

        assert ei.value.status_code == 401


class TestOptionalUser:
    async def test_valid_token_gives_enriched_user(self, jh):
        _, binding, access, _ = login(jh, "alice", email="a@x.ru", full_name="Алиса")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=access)

        user = await jh.get_optional_user(request_with(cookie=binding), creds)

        assert user["username"] == "alice" and user["email"] == "a@x.ru" and user["full_name"] == "Алиса"
        assert user["auth_method"] == "ldap" and user["is_admin"] is False

    async def test_no_credentials(self, jh):
        assert await jh.get_optional_user(request_with(), None) is None

    async def test_invalid_token_gives_none(self, jh):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad")

        assert await jh.get_optional_user(request_with(), creds) is None


class TestGetCurrentUser:
    async def test_valid_token(self, jh):
        _, binding, access, _ = login(jh)
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=access)

        user = await jh.get_current_user(request_with(cookie=binding), creds)

        assert user["user_id"] == "alice" and user["is_active"] is True


class TestClearCookie:
    def test_deletes_binding_cookie(self, jh):
        calls = []
        response = SimpleNamespace(delete_cookie=lambda **kw: calls.append(kw))

        jh.clear_client_binding_cookie(response)

        assert calls[0]["key"] == jh.CLIENT_BINDING_COOKIE and calls[0]["path"] == "/"
        assert calls[0]["httponly"] is True
