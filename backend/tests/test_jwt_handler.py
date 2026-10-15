# -*- coding: utf-8 -*-
"""Тесты backend.auth.jwt_handler: выпуск/проверка токенов, сессии, SEC006/SEC007."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from jose import jwt as jose_jwt

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.auth import jwt_handler as jh  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    """Чистое хранилище сессий и перехват CEF-событий на каждый тест."""
    jh._active_sessions_by_user.clear()
    cef = MagicMock()
    monkeypatch.setattr(jh, "log_cef_event", cef)
    yield cef
    jh._active_sessions_by_user.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _events(cef: MagicMock) -> list[str]:
    return [call.args[0] for call in cef.call_args_list]


def _raw_token(secret: str, **claims) -> str:
    payload = {"sub": "alice", "user_id": "u1", "sid": "s1", "token_type": "access"}
    payload.update(claims)
    payload.setdefault("exp", _now() + timedelta(minutes=5))
    return jose_jwt.encode(payload, secret, algorithm=jh.JWT_ALGORITHM)


def _login(user_id="u1", username="alice", sid="s1", **profile):
    jh.register_user_session(user_id, sid, username)
    return jh.create_access_token(username, user_id, sid, user_profile=profile or None)


class TestCreateTokens:
    def test_access_token_claims(self):
        token = jh.create_access_token("alice", "u1", "sid-1")
        payload = jose_jwt.decode(token, jh.JWT_ACCESS_SECRET, algorithms=[jh.JWT_ALGORITHM])
        assert payload["sub"] == "alice"
        assert payload["user_id"] == "u1"
        assert payload["sid"] == "sid-1"
        assert payload["token_type"] == "access"

    def test_user_id_and_sid_defaults(self):
        token = jh.create_access_token("alice")
        payload = jose_jwt.decode(token, jh.JWT_ACCESS_SECRET, algorithms=[jh.JWT_ALGORITHM])
        assert payload["user_id"] == "alice"
        assert payload["sid"]

    def test_refresh_token_uses_refresh_secret(self):
        token = jh.create_refresh_token("alice", "u1", "sid-1")
        payload = jose_jwt.decode(token, jh.JWT_REFRESH_SECRET, algorithms=[jh.JWT_ALGORITHM])
        assert payload["token_type"] == "refresh"
        with pytest.raises(Exception):
            jose_jwt.decode(token, jh.JWT_ACCESS_SECRET, algorithms=[jh.JWT_ALGORITHM])

    def test_ttl_matches_config(self):
        token = jh.create_access_token("alice")
        payload = jose_jwt.decode(token, jh.JWT_ACCESS_SECRET, algorithms=[jh.JWT_ALGORITHM])
        remaining = payload["exp"] - _now().timestamp()
        assert jh.JWT_ACCESS_TOKEN_EXPIRE_SECONDS - 5 <= remaining <= jh.JWT_ACCESS_TOKEN_EXPIRE_SECONDS + 5

    def test_session_ids_are_unique(self):
        assert jh.create_session_id() != jh.create_session_id()

    def test_profile_claims(self):
        token = jh.create_access_token(
            "alice",
            "u1",
            "s1",
            user_profile={
                "is_admin": True,
                "groups": ["g1", "", "g2"],
                "email": "a@x.test",
                "full_name": "Alice A",
                "ldap_groups": ["cn=a"],
            },
        )
        payload = jose_jwt.decode(token, jh.JWT_ACCESS_SECRET, algorithms=[jh.JWT_ALGORITHM])
        assert payload["adm"] is True
        assert payload["grp"] == ["g1", "g2"]
        assert payload["email"] == "a@x.test"
        assert payload["name"] == "Alice A"
        assert payload["ldap_grp"] == ["cn=a"]

    def test_profile_claims_limits_and_absence(self):
        claims: dict = {}
        jh._apply_user_profile_claims(claims, None)
        assert claims == {}
        jh._apply_user_profile_claims(claims, {"groups": [f"g{i}" for i in range(100)]})
        assert len(claims["grp"]) == 64
        assert "adm" not in claims

    def test_token_byte_length(self):
        assert jh.jwt_token_byte_length("абв") == 6
        assert jh.jwt_token_byte_length(b"abc") == 3
        assert jh.jwt_token_byte_length(12345) == 5


class TestDecodeAccessToken:
    def test_valid_token_with_active_session(self):
        token = _login(is_admin=True, groups=["g1"], email="a@x.test")
        user = jh.decode_token(token)
        assert user["username"] == "alice"
        assert user["user_id"] == "u1"
        assert user["session_id"] == "s1"
        assert user["is_admin"] is True
        assert user["groups"] == ["g1"]
        assert user["email"] == "a@x.test"
        assert user["is_active"] is True

    def test_expired_token(self, isolated_state):
        _login()
        token = _raw_token(jh.JWT_ACCESS_SECRET, exp=_now() - timedelta(minutes=1))
        with pytest.raises(HTTPException) as exc:
            jh.decode_token(token)
        assert exc.value.status_code == 401
        assert "SEC007" in _events(isolated_state)

    def test_wrong_signature(self, isolated_state):
        _login()
        token = _raw_token("some-other-secret-value-0123456789abcdef")
        with pytest.raises(HTTPException) as exc:
            jh.decode_token(token)
        assert exc.value.status_code == 401
        assert "SEC007" in _events(isolated_state)

    def test_garbage_token(self):
        with pytest.raises(HTTPException) as exc:
            jh.decode_token("not.a.jwt")
        assert exc.value.status_code == 401

    def test_alg_none_rejected(self):
        header = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"  # {"alg":"none","typ":"JWT"}
        body = "eyJzdWIiOiJhbGljZSIsInRva2VuX3R5cGUiOiJhY2Nlc3MifQ"
        with pytest.raises(HTTPException):
            jh.decode_token(f"{header}.{body}.")

    def test_refresh_typed_token_rejected_as_access(self):
        _login()
        token = _raw_token(jh.JWT_ACCESS_SECRET, token_type="refresh")
        with pytest.raises(HTTPException) as exc:
            jh.decode_token(token)
        assert exc.value.detail == "Неверный тип токена"

    def test_real_refresh_token_rejected_as_access(self):
        jh.register_user_session("u1", "s1", "alice")
        refresh = jh.create_refresh_token("alice", "u1", "s1")
        with pytest.raises(HTTPException):
            jh.decode_token(refresh)

    def test_missing_sub(self):
        token = jose_jwt.encode(
            {"token_type": "access", "sid": "s1", "exp": _now() + timedelta(minutes=5)},
            jh.JWT_ACCESS_SECRET,
            algorithm=jh.JWT_ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc:
            jh.decode_token(token)
        assert exc.value.detail == "Неверный токен"

    def test_session_unknown_after_restart(self):
        token = jh.create_access_token("alice", "u1", "s1")  # сессия не зарегистрирована
        with pytest.raises(HTTPException) as exc:
            jh.decode_token(token)
        assert "сервер перезапущен" in exc.value.detail

    def test_missing_sid(self):
        _login()
        token = jose_jwt.encode(
            {"sub": "alice", "user_id": "u1", "token_type": "access", "exp": _now() + timedelta(minutes=5)},
            jh.JWT_ACCESS_SECRET,
            algorithm=jh.JWT_ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc:
            jh.decode_token(token)
        assert exc.value.detail == "Сессия завершена"

    def test_superseded_session_message_for_single_session_limit(self, monkeypatch):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 1)
        old = _login(sid="old")
        _login(sid="new")
        with pytest.raises(HTTPException) as exc:
            jh.decode_token(old)
        assert "с другого устройства" in exc.value.detail

    def test_superseded_session_message_for_multi_session_limit(self, monkeypatch):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 2)
        tokens = [_login(sid=f"s{i}") for i in range(3)]
        with pytest.raises(HTTPException) as exc:
            jh.decode_token(tokens[0])
        assert "лимит активных сессий (2)" in exc.value.detail
        assert jh.decode_token(tokens[2])["session_id"] == "s2"


class TestDecodeSignatureOnly:
    def test_does_not_require_active_session(self):
        token = jh.create_access_token("alice", "u1", "ghost")
        assert jh.decode_token_signature_only(token)["username"] == "alice"

    def test_still_checks_signature_and_type(self):
        with pytest.raises(HTTPException):
            jh.decode_token_signature_only(_raw_token("another-secret-0123456789abcdef0123"))
        with pytest.raises(HTTPException):
            jh.decode_token_signature_only(_raw_token(jh.JWT_ACCESS_SECRET, token_type="refresh"))

    def test_expired(self):
        token = _raw_token(jh.JWT_ACCESS_SECRET, exp=_now() - timedelta(minutes=1))
        with pytest.raises(HTTPException):
            jh.decode_token_signature_only(token)


class TestDecodeRefreshToken:
    def test_valid(self):
        jh.register_user_session("u1", "s1", "alice")
        user = jh.decode_refresh_token(jh.create_refresh_token("alice", "u1", "s1"))
        assert user["username"] == "alice"

    def test_access_token_rejected(self):
        jh.register_user_session("u1", "s1", "alice")
        with pytest.raises(HTTPException):
            jh.decode_refresh_token(jh.create_access_token("alice", "u1", "s1"))

    def test_access_typed_token_signed_with_refresh_secret(self):
        jh.register_user_session("u1", "s1", "alice")
        token = _raw_token(jh.JWT_REFRESH_SECRET, token_type="access")
        with pytest.raises(HTTPException) as exc:
            jh.decode_refresh_token(token)
        assert exc.value.detail == "Неверный тип токена"

    def test_expired(self):
        jh.register_user_session("u1", "s1", "alice")
        token = _raw_token(jh.JWT_REFRESH_SECRET, token_type="refresh", exp=_now() - timedelta(seconds=5))
        with pytest.raises(HTTPException):
            jh.decode_refresh_token(token)

    def test_revoked_session(self):
        jh.register_user_session("u1", "s1", "alice")
        token = jh.create_refresh_token("alice", "u1", "s1")
        jh.revoke_user_session("u1", "s1")
        with pytest.raises(HTTPException):
            jh.decode_refresh_token(token)


class TestSessionRegistry:
    def test_first_seen_flag(self):
        assert jh.register_user_session("u1", "a")["first_seen"] is True
        assert jh.register_user_session("u1", "b")["first_seen"] is False

    def test_limit_evicts_oldest_and_emits_sec006(self, monkeypatch, isolated_state):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 2)
        jh.register_user_session("u1", "a")
        jh.register_user_session("u1", "b")
        result = jh.register_user_session("u1", "c", username="alice")
        assert result["invalidated_count"] == 1
        assert jh._active_sessions_by_user["u1"] == ["b", "c"]
        assert _events(isolated_state) == ["SEC006"]
        assert isolated_state.call_args.kwargs["extra"]["cn1"] == 1

    def test_reregister_moves_session_to_newest(self, monkeypatch):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 3)
        for sid in ("a", "b", "c"):
            jh.register_user_session("u1", sid)
        result = jh.register_user_session("u1", "a")
        assert result["invalidated_count"] == 0
        assert jh._active_sessions_by_user["u1"] == ["b", "c", "a"]

    def test_users_are_isolated(self, monkeypatch):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 1)
        jh.register_user_session("u1", "a")
        jh.register_user_session("u2", "b")
        assert jh._session_is_active("u1", "a")
        assert jh._session_is_active("u2", "b")
        assert not jh._session_is_active("u1", "b")

    def test_revoke_one(self):
        jh.register_user_session("u1", "a")
        jh.register_user_session("u1", "b")
        jh.revoke_user_session("u1", "a")
        assert jh._active_sessions_by_user["u1"] == ["b"]

    def test_revoke_last_removes_user(self):
        jh.register_user_session("u1", "a")
        jh.revoke_user_session("u1", "a")
        assert "u1" not in jh._active_sessions_by_user

    def test_revoke_all(self):
        jh.register_user_session("u1", "a")
        jh.register_user_session("u1", "b")
        jh.revoke_user_session("u1")
        assert "u1" not in jh._active_sessions_by_user

    def test_revoke_unknown_is_noop(self):
        jh.revoke_user_session("nobody")
        jh.register_user_session("u1", "a")
        jh.revoke_user_session("u1", "zzz")
        assert jh._active_sessions_by_user["u1"] == ["a"]

    @pytest.mark.parametrize("sid", [None, ""])
    def test_session_is_active_without_sid(self, sid):
        jh.register_user_session("u1", "a")
        assert jh._session_is_active("u1", sid) is False

    def test_get_max_active_sessions(self, monkeypatch):
        monkeypatch.setattr(jh, "AUTH_MAX_ACTIVE_SESSION", 7)
        assert jh.get_max_active_sessions() == 7

    def test_server_instance_id_is_stable(self):
        assert jh.get_server_instance_id() == jh.get_server_instance_id()


class TestRequiredEnvInt:
    def test_valid(self, monkeypatch):
        monkeypatch.setenv("X_TTL", " 42 ")
        assert jh._required_env_int("X_TTL") == 42

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_missing(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv("X_TTL", raising=False)
        else:
            monkeypatch.setenv("X_TTL", raw)
        with pytest.raises(RuntimeError, match="Missing required"):
            jh._required_env_int("X_TTL")

    @pytest.mark.parametrize("raw", ["0", "-1"])
    def test_non_positive(self, monkeypatch, raw):
        monkeypatch.setenv("X_TTL", raw)
        with pytest.raises(RuntimeError, match="positive"):
            jh._required_env_int("X_TTL")

    def test_non_numeric(self, monkeypatch):
        monkeypatch.setenv("X_TTL", "abc")
        with pytest.raises(ValueError):
            jh._required_env_int("X_TTL")


def _request(authorization=None):
    headers = {}
    if authorization is not None:
        headers["authorization"] = authorization
    return SimpleNamespace(headers=headers)


class TestBearerAndSilentUser:
    @pytest.mark.parametrize(
        "header, expected",
        [
            (None, None),
            ("", None),
            ("Bearer abc", "abc"),
            ("bearer   abc  ", "abc"),
            ("BEARER abc", "abc"),
            ("Basic abc", None),
            ("Bearer", None),
            ("Bearer    ", None),
            ("abc", None),
        ],
    )
    def test_parse_bearer(self, header, expected):
        assert jh._parse_bearer_token(_request(header)) == expected

    def test_try_user_from_request_valid(self):
        token = _login()
        user = jh.try_user_from_request(_request(f"Bearer {token}"))
        assert user["username"] == "alice"
        assert user["is_admin"] is False

    def test_try_user_from_request_never_raises(self, isolated_state):
        assert jh.try_user_from_request(_request(None)) is None
        assert jh.try_user_from_request(_request("Bearer garbage")) is None
        expired = _raw_token(jh.JWT_ACCESS_SECRET, exp=_now() - timedelta(minutes=1))
        assert jh.try_user_from_request(_request(f"Bearer {expired}")) is None
        # Тихое извлечение не должно порождать SEC007.
        assert "SEC007" not in _events(isolated_state)

    def test_try_user_from_request_inactive_session(self):
        token = jh.create_access_token("alice", "u1", "ghost")
        assert jh.try_user_from_request(_request(f"Bearer {token}")) is None

    def test_try_user_from_request_wrong_type(self):
        jh.register_user_session("u1", "s1", "alice")
        token = _raw_token(jh.JWT_ACCESS_SECRET, token_type="refresh")
        assert jh.try_user_from_request(_request(f"Bearer {token}")) is None


class TestPeekSubject:
    def test_reads_expired_token(self):
        token = _raw_token(jh.JWT_ACCESS_SECRET, exp=_now() - timedelta(hours=1))
        assert jh._peek_jwt_subject(token, "access") == ("alice", "u1")

    def test_refresh_uses_refresh_secret(self):
        token = _raw_token(jh.JWT_REFRESH_SECRET, token_type="refresh")
        assert jh._peek_jwt_subject(token, "refresh") == ("alice", "u1")
        assert jh._peek_jwt_subject(token, "access") == (None, None)

    def test_garbage(self):
        assert jh._peek_jwt_subject("junk", "access") == (None, None)

    def test_failure_log_uses_peeked_identity(self, isolated_state):
        token = _raw_token(jh.JWT_ACCESS_SECRET, exp=_now() - timedelta(hours=1))
        with pytest.raises(HTTPException):
            jh.decode_token(token)
        user = isolated_state.call_args.kwargs["current_user"]
        assert user["username"] == "alice"
        assert user["user_id"] == "u1"

    def test_failure_log_anonymous_for_garbage(self, isolated_state):
        with pytest.raises(HTTPException):
            jh.decode_token("junk")
        assert isolated_state.call_args.kwargs["current_user"]["username"] == "anonymous"
