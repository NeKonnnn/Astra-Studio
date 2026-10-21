"""auth/agent_platform_roles: платформенные роли из групп LDAP/Keycloak и права на их основе."""

import pytest

from backend.auth import agent_platform_roles as roles

pytestmark = pytest.mark.unit

ENV_NAMES = (
    "AGENT_PLATFORM_ROLE_MAPPING_ENABLED",
    "AGENT_ROLE_OWNER_GROUPS",
    "AGENT_ROLE_EDITOR_GROUPS",
    "AGENT_ROLE_VIEWER_GROUPS",
    "AGENT_ROLE_BASE_USER_GROUPS",
    "AGENT_ROLE_OWNER_KEYCLOAK_GROUPS",
    "AGENT_ROLE_EDITOR_KEYCLOAK_GROUPS",
    "AGENT_ROLE_VIEWER_KEYCLOAK_GROUPS",
    "AGENT_ROLE_BASE_USER_KEYCLOAK_GROUPS",
)


def _clear_caches():
    roles.clear_role_cache()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Кэши lru_cache завязаны на ENV: сбрасываем до и после каждого теста."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    _clear_caches()
    yield
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    _clear_caches()


@pytest.fixture
def mapping(monkeypatch):
    """Включает маппинг: owner=Owners, editor=Editors, viewer=Viewers, base=Basic."""
    monkeypatch.setenv("AGENT_ROLE_OWNER_GROUPS", "Owners")
    monkeypatch.setenv("AGENT_ROLE_EDITOR_GROUPS", "Editors")
    monkeypatch.setenv("AGENT_ROLE_VIEWER_GROUPS", "Viewers")
    monkeypatch.setenv("AGENT_ROLE_BASE_USER_GROUPS", "Basic")
    _clear_caches()


def user(*groups, ldap=(), admin=False):
    return {"username": "u", "user_id": "u", "groups": list(groups), "ldap_groups": list(ldap), "is_admin": admin}


class TestParsing:
    def test_keycloak_section_splits_the_value(self):
        text = "(memberOf=CN=A,DC=x)\n---KEYCLOAK---\nrole-a, role-b"

        assert roles._split_keycloak_section(text) == ("(memberOf=CN=A,DC=x)", "role-a, role-b")

    def test_marker_is_case_insensitive_and_optional(self):
        assert roles._split_keycloak_section("a\n---keycloak---\nb") == ("a", "b")
        assert roles._split_keycloak_section("  just a list ") == ("just a list", "")

    @pytest.mark.parametrize(("raw", "expected"), [("a, b ;c,, ", ["a", "b", "c"]), ("", []), (None, []), ("  ", [])])
    def test_simple_lists(self, raw, expected):
        assert roles._parse_simple_role_list(raw) == expected

    def test_ldap_filter_gives_dns_and_cns(self):
        result = roles._parse_env_role_groups("(&(a=b)(memberOf=CN=Finance,OU=G,DC=corp))")

        assert "cn=finance,ou=g,dc=corp" in result
        assert "finance" in [item.casefold() for item in result]

    def test_plain_values_are_taken_as_they_are(self):
        assert roles._parse_env_role_groups("Admins, Ops") == ["Admins", "Ops"]

    def test_companion_env_name(self):
        assert roles._companion_keycloak_env_name("AGENT_ROLE_OWNER_GROUPS") == "AGENT_ROLE_OWNER_KEYCLOAK_GROUPS"
        assert roles._companion_keycloak_env_name("SOMETHING") == "SOMETHING_KEYCLOAK_GROUPS"

    def test_role_groups_merge_ldap_inline_and_companion_sources(self, monkeypatch):
        monkeypatch.setenv("AGENT_ROLE_OWNER_GROUPS", "LdapGroup\n---KEYCLOAK---\nkc-inline")
        monkeypatch.setenv("AGENT_ROLE_OWNER_KEYCLOAK_GROUPS", "kc-companion")

        assert roles._env_role_groups("AGENT_ROLE_OWNER_GROUPS") == ["LdapGroup", "kc-inline", "kc-companion"]

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("CN=Finance,OU=Groups,DC=corp", {"cn=finance,ou=groups,dc=corp", "finance"}),
            ("Finance", {"finance"}),
            ("", set()),
            (None, set()),
        ],
    )
    def test_group_identifiers(self, value, expected):
        result = roles._normalize_group_identifier(value)

        assert expected <= result

    def test_keycloak_paths_are_kept_whole(self):
        result = roles._normalize_group_identifier("/org/Finance")

        assert "org/finance" in result and "finance" not in result

    @pytest.mark.parametrize(
        ("value", "cn"),
        [("CN=Team,OU=X", "Team"), ("memberOf=CN=Team Two,DC=a", "Team Two"), ("cn = lower , x", "lower"), ("NoCn", ""), ("", "")],
    )
    def test_extract_cn(self, value, cn):
        assert roles._extract_cn(value) == cn

    def test_env_bool(self, monkeypatch):
        monkeypatch.setenv("X_B", " YES ")
        assert roles._env_bool("X_B", False) is True
        monkeypatch.setenv("X_B", "0")
        assert roles._env_bool("X_B", True) is False
        monkeypatch.delenv("X_B")
        assert roles._env_bool("X_B", True) is True


class TestMappingSwitch:
    def test_disabled_without_any_configuration(self):
        assert roles.is_agent_platform_role_mapping_enabled() is False

    def test_enabled_automatically_when_groups_are_configured(self, monkeypatch):
        monkeypatch.setenv("AGENT_ROLE_VIEWER_GROUPS", "Viewers")
        _clear_caches()

        assert roles.is_agent_platform_role_mapping_enabled() is True

    def test_keycloak_only_configuration_enables_it_too(self, monkeypatch):
        monkeypatch.setenv("AGENT_ROLE_OWNER_KEYCLOAK_GROUPS", "kc-owner")
        _clear_caches()

        assert roles.is_agent_platform_role_mapping_enabled() is True

    def test_explicit_switch_wins(self, monkeypatch):
        monkeypatch.setenv("AGENT_ROLE_VIEWER_GROUPS", "Viewers")
        monkeypatch.setenv("AGENT_PLATFORM_ROLE_MAPPING_ENABLED", "false")
        _clear_caches()

        assert roles.is_agent_platform_role_mapping_enabled() is False

    def test_the_result_is_cached_until_clear_role_cache(self, monkeypatch):
        assert roles.is_agent_platform_role_mapping_enabled() is False
        monkeypatch.setenv("AGENT_ROLE_VIEWER_GROUPS", "Viewers")

        assert roles.is_agent_platform_role_mapping_enabled() is False
        roles.clear_role_cache()
        assert roles.is_agent_platform_role_mapping_enabled() is True

    def test_reset_settings_clears_the_role_cache(self, monkeypatch):
        from backend.settings import config as settings_config

        assert roles.is_agent_platform_role_mapping_enabled() is False
        monkeypatch.setenv("AGENT_ROLE_VIEWER_GROUPS", "Viewers")
        monkeypatch.setattr(settings_config.Settings, "from_yaml", classmethod(lambda cls, path=None: object()))
        monkeypatch.setattr(settings_config, "_settings", None)

        settings_config.reset_settings()

        assert roles.is_agent_platform_role_mapping_enabled() is True


class TestRoleResolution:
    @pytest.mark.parametrize(
        ("groups", "expected"),
        [
            (["Owners"], "owner"),
            (["Editors"], "editor"),
            (["Viewers"], "viewer"),
            (["Basic"], "base"),
            (["Viewers", "Owners"], "owner"),  # старшая роль побеждает
            (["Basic", "Editors"], "editor"),
            (["owners"], "owner"),  # без учёта регистра
            (["Strangers"], None),
            ([], None),
        ],
    )
    def test_role_from_groups(self, mapping, groups, expected):
        assert roles.get_agent_platform_role(user(*groups)) == expected

    def test_ldap_distinguished_names_are_matched_by_cn(self, mapping):
        assert roles.get_agent_platform_role(user(ldap=["CN=Owners,OU=Groups,DC=corp"])) == "owner"

    def test_keycloak_group_paths_are_matched_by_the_full_path(self, monkeypatch):
        monkeypatch.setenv("AGENT_ROLE_EDITOR_GROUPS", "/company/Editors")
        _clear_caches()

        assert roles.get_agent_platform_role(user("/company/Editors")) == "editor"
        assert roles.get_agent_platform_role(user("/other/Editors")) is None
        assert roles.get_agent_platform_role(user("Editors")) is None

    def test_top_level_keycloak_group_matches_the_bare_configured_name(self, mapping):
        assert roles.get_agent_platform_role(user("/Owners")) == "owner"

    def test_without_mapping_nobody_has_a_role(self):
        assert roles.get_agent_platform_role(user("Owners")) is None

    @pytest.mark.parametrize("bad", [None, {}, [], "text"])
    def test_garbage_users(self, mapping, bad):
        assert roles.get_agent_platform_role(bad) is None

    @pytest.mark.parametrize("group", ["/attackers/Owners", "/attackers/CN=Owners,OU=x", "attackers/Owners"])
    def test_a_group_named_like_a_role_under_a_different_path_does_not_match(self, mapping, group):
        assert roles.get_agent_platform_role(user(group)) is None


class TestCapabilities:
    def test_everything_is_allowed_without_mapping(self):
        u = user("Nobody")

        assert roles.user_can_use_agents(u) and roles.user_can_use_mcp(u)
        assert roles.user_can_create_agents(u) and roles.user_can_publish_to_library(u)

    @pytest.mark.parametrize(
        ("group", "use", "create", "publish"),
        [
            ("Owners", True, True, True),
            ("Editors", True, True, False),
            ("Viewers", True, False, False),
            ("Basic", False, False, False),
            ("Strangers", False, False, False),
        ],
    )
    def test_capabilities_by_role(self, mapping, group, use, create, publish):
        u = user(group)

        assert roles.user_can_use_agents(u) is use
        assert roles.user_can_use_mcp(u) is use
        assert roles.user_can_create_agents(u) is create
        assert roles.user_can_publish_to_library(u) is publish

    def test_admins_can_do_everything_regardless_of_groups(self, mapping):
        u = user("Basic", admin=True)

        assert roles.user_can_use_agents(u) and roles.user_can_create_agents(u) and roles.user_can_publish_to_library(u)

    @pytest.mark.parametrize("bad", [None, {}, "x"])
    def test_garbage_users_get_nothing(self, mapping, bad):
        assert not roles.user_can_use_agents(bad)
        assert not roles.user_can_create_agents(bad)
        assert not roles.user_can_publish_to_library(bad)


class TestPermissionCapping:
    @pytest.mark.parametrize(
        ("db", "platform", "expected"),
        [
            ("owner", "owner", "owner"),
            ("owner", "editor", "editor"),
            ("owner", "viewer", "viewer"),
            ("editor", "owner", "editor"),
            ("editor", "viewer", "viewer"),
            ("viewer", "owner", "viewer"),
            ("owner", "base", None),
            ("owner", None, None),
            ("owner", "weird", None),
            (None, "owner", None),
            ("custom", "viewer", "custom"),
        ],
    )
    def test_cap(self, db, platform, expected):
        assert roles.cap_agent_permission(db, platform) == expected

    @pytest.mark.parametrize(
        ("requested", "recipient", "expected"),
        [
            ("viewer", "viewer", "viewer"),
            ("editor", "viewer", "editor"),
            ("edit", "editor", "editor"),
            ("write", "owner", "editor"),
            ("use", "viewer", "viewer"),
            ("garbage", "viewer", "viewer"),
            ("", "editor", "viewer"),
            ("editor", "base", None),
            ("editor", None, None),
        ],
    )
    def test_share_permission_with_mapping(self, mapping, requested, recipient, expected):
        assert roles.resolve_share_permission(requested, recipient) == expected

    def test_share_permission_without_mapping_keeps_the_request(self):
        assert roles.resolve_share_permission("edit", None) == "editor"
        assert roles.resolve_share_permission("zzz", "base") == "viewer"


class TestCefRoleModel:
    def test_admin(self, mapping):
        assert roles.resolve_cef_role_model(user(admin=True)) == ("ADMIN", "")

    def test_without_mapping_everyone_is_a_plain_user(self):
        assert roles.resolve_cef_role_model(user("Owners")) == ("USER", "-")
        assert roles.resolve_cef_role_model(None) == ("USER", "-")

    @pytest.mark.parametrize(("group", "role"), [("Owners", "OWNER"), ("Editors", "EDITOR"), ("Viewers", "VIEWER"), ("Basic", "BASE")])
    def test_role_and_matched_group(self, mapping, group, role):
        assert roles.resolve_cef_role_model(user(group)) == (role, group)

    def test_unmatched_users_are_reported_as_base(self, mapping):
        assert roles.resolve_cef_role_model(user("Strangers")) == ("BASE", "-")

    def test_matched_group_is_shown_by_its_common_name(self, mapping):
        assert roles.resolve_cef_role_model(user(ldap=["CN=Owners,OU=Groups,DC=corp"])) == ("OWNER", "Owners")


class TestFeatureFlags:
    def test_flags_are_added_to_the_profile(self, mapping):
        profile = roles.enrich_user_feature_flags(user("Editors"))

        assert profile["platform_role"] == "editor"
        assert profile["can_use_agents"] and profile["can_create_agents"]
        assert profile["can_publish_to_library"] is False
        assert profile["features"] == {"agents": True, "mcp": True, "create_agents": True, "publish_to_library": False}

    def test_unmatched_users_see_no_agents_and_the_ui_label_is_base(self, mapping):
        profile = roles.enrich_user_feature_flags(user("Strangers"))

        assert profile["platform_role"] == "base" and profile["can_use_agents"] is False

    def test_admin_without_a_role_keeps_a_none_label(self, mapping):
        profile = roles.enrich_user_feature_flags(user("Strangers", admin=True))

        assert profile["platform_role"] is None and profile["can_use_agents"] is True

    @pytest.mark.parametrize("bad", [None, {}, "x"])
    def test_garbage_is_returned_unchanged(self, bad):
        assert roles.enrich_user_feature_flags(bad) == bad
