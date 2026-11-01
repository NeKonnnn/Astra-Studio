"""login_helpers.complete_application_login: выдача JWT после SSO (JWT/сессии/CEF подменены)."""

from unittest.mock import MagicMock

import pytest

from backend.auth import login_helpers as lh
from backend.auth.sso_logging import KeycloakSsoError

pytestmark = pytest.mark.unit


@pytest.fixture
def env(monkeypatch):
    """Фейки всех внешних зависимостей; возвращает журнал вызовов."""
    st = MagicMock()
    st.session_info = {"first_seen": False}
    st.cef = []
    monkeypatch.setattr(lh, "create_session_id", lambda: "sid-1")
    monkeypatch.setattr(lh, "register_user_session", lambda *a, **k: (st.reg.append((a, k)), st.session_info)[1])
    st.reg = []
    monkeypatch.setattr(lh, "create_access_token", lambda **k: "ACCESS")
    monkeypatch.setattr(lh, "create_refresh_token", lambda **k: "REFRESH")
    monkeypatch.setattr(lh, "jwt_token_byte_length", lambda t: len(t))
    monkeypatch.setattr(lh, "get_session_policy", lambda: {"timeout_seconds": 1800, "enabled": True})
    monkeypatch.setattr(lh, "create_client_binding", lambda u, s: f"bind-{u}-{s}")
    monkeypatch.setattr(lh, "set_client_binding_cookie", lambda resp, b: st.cookie.append(b))
    st.cookie = []
    monkeypatch.setattr(lh, "get_server_instance_id", lambda: "srv-1")
    monkeypatch.setattr(lh, "login_audit_cef_extra", lambda user, method: {"method": method})
    monkeypatch.setattr(lh, "log_cef_event", lambda code, **k: st.cef.append((code, k)))
    monkeypatch.setattr(
        "backend.auth.agent_platform_roles.enrich_user_feature_flags",
        lambda user: user.update(flags_enriched=True),
    )
    monkeypatch.delenv("LDAP_USER_SEARCH_BASE", raising=False)
    return st


def login(user, env_=None):
    return lh.complete_application_login(user, MagicMock(), MagicMock(), auth_method="keycloak")


def test_returns_tokens_policy_and_public_user(env):
    user = {"username": "ivan", "email": "ivan@corp.ru", "keycloak_sub": "secret-sub"}

    result = login(user)

    assert result["access_token"] == "ACCESS" and result["refresh_token"] == "REFRESH"
    assert result["token_type"] == "bearer"
    assert result["session_policy"] == {"timeout_seconds": 1800, "enabled": True}
    assert result["server_instance_id"] == "srv-1" and result["_binding_id"] == "bind-ivan-sid-1"
    assert env.cookie == ["bind-ivan-sid-1"]
    # публичный профиль: метод входа проставлен, keycloak_sub скрыт, флаги обогащены
    assert result["user"]["auth_method"] == "keycloak" and "keycloak_sub" not in result["user"]
    assert result["user"]["flags_enriched"] is True
    assert [c[0] for c in env.cef] == ["SEC001"]


def test_username_falls_back_to_user_id(env):
    result = login({"user_id": "petr"})

    assert result["_binding_id"] == "bind-petr-sid-1"


def test_profile_without_username_is_500(env):
    with pytest.raises(KeycloakSsoError) as ei:
        login({"username": "  "})

    assert ei.value.status_code == 500 and "username" in ei.value.detail


def test_token_failure_is_wrapped_as_500(env, monkeypatch):
    def boom(**k):
        raise RuntimeError("нет ключа")

    monkeypatch.setattr(lh, "create_access_token", boom)

    with pytest.raises(KeycloakSsoError) as ei:
        login({"username": "ivan"})

    err = ei.value
    assert err.status_code == 500 and err.log_exception is True and err.username == "ivan"
    assert isinstance(err.__cause__, RuntimeError)


def test_first_login_emits_usr001_with_domain_from_email(env):
    env.session_info = {"first_seen": True}

    login({"username": "ivan", "email": "ivan@corp.ru"})

    codes = [c[0] for c in env.cef]
    assert codes == ["SEC001", "USR001"]
    assert env.cef[1][1]["extra"]["dntdom"] == "corp.ru"


def test_first_login_domain_from_ldap_base_dn(env, monkeypatch):
    env.session_info = {"first_seen": True}
    monkeypatch.setenv("LDAP_USER_SEARCH_BASE", "OU=Users,DC=corp,DC=local")

    login({"username": "ivan"})

    assert env.cef[1][1]["extra"]["dntdom"] == "corp.local"


def test_first_login_without_email_and_base_has_no_domain(env):
    env.session_info = {"first_seen": True}

    login({"username": "ivan"})

    assert env.cef[1][1]["extra"]["dntdom"] is None
