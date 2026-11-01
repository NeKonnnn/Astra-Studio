"""share_recipients: хвосты (пустой список, недоступный LDAP-модуль, ветка понижения роли)."""

import sys

import pytest

from backend.auth import agent_platform_roles as roles
from backend.auth import share_recipients as sr

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _mapping(monkeypatch):
    for name in (
        "AGENT_PLATFORM_ROLE_MAPPING_ENABLED", "AGENT_ROLE_OWNER_GROUPS", "AGENT_ROLE_EDITOR_GROUPS",
        "AGENT_ROLE_VIEWER_GROUPS", "AGENT_ROLE_BASE_USER_GROUPS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_ROLE_EDITOR_GROUPS", "Editors")
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()
    yield
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()


async def test_only_blank_logins_give_empty_profiles():
    assert await sr._fetch_recipient_profiles(["", "  ", None]) == {}


async def test_ldap_module_import_failure_gives_none_profiles(monkeypatch):
    monkeypatch.setitem(sys.modules, "backend.auth.ldap_auth", None)

    assert await sr._fetch_recipient_profiles(["Ivan", "ivan", "Petr"]) == {"ivan": None, "petr": None}


async def test_granted_role_differing_from_request_is_reported_as_capped(monkeypatch):
    async def profiles(names):
        return {"ivan": {"groups": ["Editors"], "ldap_groups": []}}

    monkeypatch.setattr(sr, "_fetch_recipient_profiles", profiles)
    # resolve_share_permission сейчас всегда возвращает запрошенную роль; ветка
    # понижения оставлена на случай возврата ранговой логики - проверяем её подменой.
    monkeypatch.setattr(sr, "resolve_share_permission", lambda req, role: "viewer")

    result = await sr.prepare_share_recipients(["Ivan"], "editor")

    assert result.to_share == [("ivan", "viewer")]
    assert len(result.capped) == 1
    assert (result.capped[0].user_id, result.capped[0].requested, result.capped[0].granted) == (
        "ivan", "editor", "viewer",
    )
