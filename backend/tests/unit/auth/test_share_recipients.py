"""share_recipients: проверка получателей шаринга (LDAP + платформенная роль) и ответ API."""

import pytest

from backend.auth import agent_platform_roles as roles
from backend.auth import share_recipients as sr

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _role_caches(monkeypatch):
    for name in ("AGENT_PLATFORM_ROLE_MAPPING_ENABLED", "AGENT_ROLE_OWNER_GROUPS", "AGENT_ROLE_EDITOR_GROUPS", "AGENT_ROLE_VIEWER_GROUPS", "AGENT_ROLE_BASE_USER_GROUPS"):
        monkeypatch.delenv(name, raising=False)
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()
    yield
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()


@pytest.fixture
def mapping(monkeypatch):
    monkeypatch.setenv("AGENT_ROLE_OWNER_GROUPS", "Owners")
    monkeypatch.setenv("AGENT_ROLE_EDITOR_GROUPS", "Editors")
    monkeypatch.setenv("AGENT_ROLE_VIEWER_GROUPS", "Viewers")
    monkeypatch.setenv("AGENT_ROLE_BASE_USER_GROUPS", "Basic")
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()


@pytest.fixture
def ldap(monkeypatch):
    import backend.auth.ldap_auth as ldap_auth

    state = type("S", (), {})()
    state.enabled = True
    state.profiles = {}
    state.calls = []
    state.error = None

    def bulk(names):
        state.calls.append(list(names))
        if state.error:
            raise state.error
        return {n.lower(): state.profiles[n.lower()] for n in names if n.lower() in state.profiles}

    monkeypatch.setattr(ldap_auth, "is_ldap_enabled", lambda: state.enabled)
    monkeypatch.setattr(ldap_auth, "fetch_ldap_user_group_profiles_bulk", bulk)
    return state


class TestPermissionNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("use", "viewer"), ("", "viewer"), (None, "viewer"), (" READ ", "viewer"), ("reader", "viewer"), ("edit", "editor"), ("Write", "editor"),
         ("writer", "editor"), ("editor", "editor"), ("viewer", "viewer"), ("owner", "viewer"), ("admin", "viewer")],
    )
    def test_mapping(self, raw, expected):
        assert sr._normalize_requested_permission(raw) == expected

    def test_group_identifiers_merge_both_lists(self):
        ids = sr._group_identifiers_from_profile({"groups": ["Team"], "ldap_groups": ["AD-Group"]})

        assert any("team" in i.lower() for i in ids) and any("ad-group" in i.lower() for i in ids)
        assert sr._group_identifiers_from_profile({}) == set()


class TestWithoutRoleMapping:
    async def test_everyone_gets_the_requested_role_without_ldap(self, ldap):
        result = await sr.prepare_share_recipients(["Bob", " carol ", "BOB"], "edit")

        assert result.to_share == [("bob", "editor"), ("carol", "editor"), ("bob", "editor")]
        assert result.skipped == [] and ldap.calls == []

    async def test_empty_logins_are_skipped(self, ldap):
        result = await sr.prepare_share_recipients(["", "  ", "bob"], "viewer")

        assert result.to_share == [("bob", "viewer")] and len(result.skipped) == 2
        assert {d.reason for d in result.skipped_details} == {"Пустой логин"}


class TestWithRoleMapping:
    async def test_unknown_users_are_skipped(self, mapping, ldap):
        result = await sr.prepare_share_recipients(["ghost"], "viewer")

        assert result.to_share == [] and result.skipped == ["ghost"]
        assert "не найден" in result.skipped_details[0].reason

    async def test_users_without_platform_role_cannot_receive_shares(self, mapping, ldap):
        ldap.profiles = {"basic": {"groups": ["Basic"]}, "nogroup": {"groups": []}}

        result = await sr.prepare_share_recipients(["basic", "nogroup"], "viewer")

        assert result.to_share == [] and result.skipped == ["basic", "nogroup"]
        assert "Нет платформенной роли" in result.skipped_details[0].reason

    async def test_requested_role_is_kept_for_valid_recipients(self, mapping, ldap):
        # Права получателя не «режут» запрошенный editor при шаринге (cap делается при чтении).
        ldap.profiles = {"vera": {"groups": ["Viewers"]}, "owen": {"ldap_groups": ["Owners"]}}

        result = await sr.prepare_share_recipients(["Vera", "owen"], "editor")

        assert result.to_share == [("vera", "editor"), ("owen", "editor")] and result.capped == []

    async def test_ldap_is_queried_once_with_unique_names(self, mapping, ldap):
        ldap.profiles = {"vera": {"groups": ["Viewers"]}}

        await sr.prepare_share_recipients(["Vera", "vera", " ", "VERA"], "viewer")

        assert ldap.calls == [["Vera"]]

    async def test_disabled_ldap_means_nobody_is_found(self, mapping, ldap):
        ldap.enabled = False

        result = await sr.prepare_share_recipients(["vera"], "viewer")

        assert result.to_share == [] and ldap.calls == []

    async def test_ldap_failure_skips_everyone_instead_of_sharing(self, mapping, ldap):
        ldap.error = RuntimeError("ldap down")
        ldap.profiles = {"vera": {"groups": ["Viewers"]}}

        result = await sr.prepare_share_recipients(["vera"], "viewer")

        assert result.to_share == [] and result.skipped == ["vera"]


class TestApiResponse:
    def prepared(self, **kw):
        return sr.ShareRecipientsPrepareResult(**kw)

    def test_success_message(self):
        body = sr.build_share_api_response(self.prepared(), ["bob", "carol"], [], resource_label="Агент")

        assert body["success"] is True and body["shared_with"] == ["bob", "carol"]
        assert body["message"] == "Агент доступен для: bob, carol" and body["warnings"] == []

    def test_nobody_added(self):
        body = sr.build_share_api_response(self.prepared(), [], [], resource_label="Агент")

        assert body["success"] is False and body["message"] == "Никого не добавлено"

    def test_skip_reasons_explain_the_failure_when_nobody_was_added(self):
        prepared = self.prepared(skipped=["ghost"], skipped_details=[sr.ShareRecipientSkip("ghost", "не найден")])

        body = sr.build_share_api_response(prepared, [], [], resource_label="Skill")

        assert body["message"] == "ghost: не найден" and body["skipped"] == ["ghost"]

    def test_capped_roles_are_reported_as_a_warning(self):
        prepared = self.prepared(capped=[sr.ShareRecipientCap("bob", "editor", "viewer")])

        body = sr.build_share_api_response(prepared, ["bob"], [], resource_label="Агент")

        assert body["capped"] == [{"user_id": "bob", "requested": "editor", "granted": "viewer"}]
        assert body["warnings"] == ["Роль понижена для: bob (viewer)"]

    def test_repository_skips_are_merged_without_duplicates(self):
        prepared = self.prepared(skipped=["ghost"], skipped_details=[sr.ShareRecipientSkip("ghost", "r")])

        body = sr.build_share_api_response(prepared, ["bob"], ["ghost", "dave", ""], resource_label="Агент")

        assert body["skipped"] == ["ghost", "dave", ""]
        assert [d["user_id"] for d in body["skipped_details"]] == ["ghost", "dave"]
        assert body["skipped_details"][1]["reason"] == "Не удалось выдать доступ"

    def test_only_three_reasons_are_shown(self):
        details = [sr.ShareRecipientSkip(f"u{i}", "r") for i in range(5)]

        body = sr.build_share_api_response(self.prepared(skipped_details=details), [], [], resource_label="X")

        assert body["message"].count("; ") == 2
