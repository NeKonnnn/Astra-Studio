"""agent_platform_roles: хвосты (автовключение по Keycloak-ENV, пустые группы, имена групп для CEF)."""

import pytest

from backend.auth import agent_platform_roles as roles

pytestmark = pytest.mark.unit

ENV_NAMES = (
    "AGENT_PLATFORM_ROLE_MAPPING_ENABLED",
    "AGENT_ROLE_OWNER_GROUPS", "AGENT_ROLE_EDITOR_GROUPS", "AGENT_ROLE_VIEWER_GROUPS",
    "AGENT_ROLE_BASE_USER_GROUPS", "AGENT_ROLE_OWNER_KEYCLOAK_GROUPS",
    "AGENT_ROLE_EDITOR_KEYCLOAK_GROUPS", "AGENT_ROLE_VIEWER_KEYCLOAK_GROUPS",
    "AGENT_ROLE_BASE_USER_KEYCLOAK_GROUPS",
)


def _clear():
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    _clear()
    yield
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    _clear()


def test_mapping_auto_enabled_by_keycloak_only_env(monkeypatch):
    monkeypatch.setenv("AGENT_ROLE_VIEWER_KEYCLOAK_GROUPS", "astra-viewers")
    _clear()

    assert roles.is_agent_platform_role_mapping_enabled() is True


def test_mapping_disabled_without_any_groups():
    assert roles.is_agent_platform_role_mapping_enabled() is False


def test_blank_ldap_groups_are_ignored():
    ids = roles._user_group_identifiers({"ldap_groups": ["", "   ", "CN=Staff,DC=x"], "groups": []})

    assert "staff" in ids and "" not in ids


def test_empty_group_set_gives_no_role(monkeypatch):
    monkeypatch.setenv("AGENT_ROLE_OWNER_GROUPS", "Owners")
    _clear()

    assert roles.get_platform_role_from_group_identifiers(set()) is None


def test_unknown_platform_role_has_empty_set(monkeypatch):
    monkeypatch.setenv("AGENT_ROLE_OWNER_GROUPS", "Owners")
    _clear()

    assert roles._role_set_for_platform_role("weird") == set()


class TestDisplayGroup:
    def test_empty_role_set_or_user(self):
        assert roles._display_group_from_match({"groups": ["a"]}, set()) == "-"
        assert roles._display_group_from_match(None, {"a"}) == "-"

    def test_plain_group_name_is_returned_as_is(self):
        assert roles._display_group_from_match({"groups": ["Owners"]}, {"owners"}) == "Owners"

    def test_cn_is_extracted_from_dn(self):
        user = {"ldap_groups": ["CN=Owners,OU=G,DC=x"]}

        assert roles._display_group_from_match(user, {"owners"}) == "Owners"

    def test_keycloak_path_gives_last_segment(self):
        assert roles._display_group_from_match({"groups": ["/team/devs"]}, {"devs"}) == "devs"

    def test_fallback_to_short_role_name_when_no_group_matches(self):
        user = {"groups": ["unrelated"]}

        assert roles._display_group_from_match(user, {"cn=a,dc=x", "owners", "admins"}) == "admins"

    def test_fallback_to_first_sorted_when_all_are_dn_like(self):
        user = {"groups": ["unrelated"]}

        assert roles._display_group_from_match(user, {"cn=b,dc=x", "cn=a,dc=x"}) == "cn=a,dc=x"


def test_share_permission_with_unknown_platform_role(monkeypatch):
    monkeypatch.setenv("AGENT_ROLE_OWNER_GROUPS", "Owners")
    _clear()

    assert roles.resolve_share_permission("editor", "mystery") is None


def test_group_identifiers_give_no_role_when_mapping_disabled():
    assert roles.get_platform_role_from_group_identifiers({"owners"}) is None
