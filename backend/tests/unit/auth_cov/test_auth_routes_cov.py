"""auth/routes: хвосты (SSO-пользователь не проверяется в LDAP, ошибка выдачи токена, refresh Keycloak)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from backend.auth import login_lockout
from backend.auth import routes as module

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _state(monkeypatch):
    login_lockout._states.clear()
    monkeypatch.setattr(module, "log_cef_event", lambda *a, **k: None)
    yield
    login_lockout._states.clear()


def test_keycloak_user_skips_ldap_account_check(monkeypatch):
    monkeypatch.setattr(
        module, "_ensure_ldap_user_active", lambda user: pytest.fail("LDAP не должен вызываться")
    )

    assert module._ensure_user_account_active({"auth_method": "keycloak", "username": "u"}) is None


def test_ldap_user_is_checked(monkeypatch):
    seen = []
    monkeypatch.setattr(module, "_ensure_ldap_user_active", seen.append)

    module._ensure_user_account_active({"username": "u"})

    assert seen == [{"username": "u"}]


async def test_login_token_failure_is_500_and_chained(monkeypatch):
    monkeypatch.setattr(module, "is_ldap_enabled", lambda: True)
    monkeypatch.setattr(
        module, "authenticate_ldap", lambda u, p: {"username": u, "user_id": u, "email": None}
    )
    monkeypatch.setattr(module, "register_user_session", lambda *a, **k: {"first_seen": False})

    def boom(**kw):
        raise RuntimeError("signing failed")

    monkeypatch.setattr(module, "create_access_token", boom)

    with pytest.raises(HTTPException) as ei:
        await module.login(
            module.LoginRequest(username="alice", password="pw"), MagicMock(), MagicMock()
        )

    assert ei.value.status_code == 500 and ei.value.detail == "Ошибка выдачи токена"
    assert isinstance(ei.value.__cause__, RuntimeError)


async def test_refresh_for_keycloak_session_uses_token_profile(monkeypatch):
    token_data = {
        "username": "kc-user", "session_id": "sid", "auth_method": "keycloak",
        "email": "kc@x.ru", "full_name": "KC", "is_admin": True, "groups": ["g"], "ldap_groups": ["g"],
    }
    issued = []
    monkeypatch.setattr(module, "decode_refresh_token", lambda t, request=None: dict(token_data))
    monkeypatch.setattr(
        module, "fetch_ldap_user_profile", lambda s: pytest.fail("LDAP-профиль для SSO не нужен")
    )
    monkeypatch.setattr(module, "create_access_token", lambda **k: issued.append(("a", k)) or "A2")
    monkeypatch.setattr(module, "create_refresh_token", lambda **k: issued.append(("r", k)) or "R2")
    monkeypatch.setattr(module, "get_server_instance_id", lambda: "srv")

    result = await module.refresh_tokens(
        MagicMock(), MagicMock(), module.RefreshRequest(refresh_token="old")
    )

    assert result["access_token"] == "A2" and result["refresh_token"] == "R2"
    assert result["user"]["username"] == "kc-user" and result["user"]["is_admin"] is True
    assert issued[0][1]["user_profile"]["auth_method"] == "keycloak"
