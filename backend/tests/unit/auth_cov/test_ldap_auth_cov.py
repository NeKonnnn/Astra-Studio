"""ldap_auth: хвосты (без ldap3, неполная конфигурация, пакетные запросы, ошибки unbind)."""

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from ldap3.core.exceptions import LDAPBindError, LDAPException

from backend.auth import ldap_auth as ldap

pytestmark = pytest.mark.unit

GROUP = "CN=Staff,OU=Groups,DC=corp,DC=local"
ENV = {
    "LDAP_ENABLED": "true",
    "LDAP_URL": "ldap://dc.corp.local",
    "LDAP_USER_SEARCH_BASE": "DC=corp,DC=local",
    "LDAP_SEARCH_FILTER": f"(&(objectClass=user)(sAMAccountName={{{{username}}}})(memberOf={GROUP}))",
    "LDAP_ID": "sAMAccountName",
    "LDAP_EMAIL": "mail",
    "LDAP_FULL_NAME": "displayName",
    "LDAP_USERNAME": "sAMAccountName",
    "LDAP_BIND_DN": "CN=svc,DC=corp,DC=local",
    "LDAP_BIND_CREDENTIALS": "svc-pass",
}


def attrs_of(login="ivan", **extra):
    base = {
        "sAMAccountName": [login], "mail": [f"{login}@corp.local"],
        "displayName": [f"Имя {login}"], "memberOf": [GROUP],
    }
    base.update(extra)
    return base


class Entry:
    def __init__(self, attrs, dn="CN=x,DC=corp,DC=local"):
        self.entry_attributes_as_dict = attrs
        self.entry_dn = dn


class FakeConn:
    instances = []
    bind_errors = {}
    entries_by_call = []
    search_error = None
    unbind_error = None

    def __init__(self, server, user=None, password=None, **kw):
        err = FakeConn.bind_errors.get(user)
        if err:
            raise err
        self.entries, self.searches = [], []
        FakeConn.instances.append(self)

    def search(self, **kw):
        self.searches.append(kw)
        if FakeConn.search_error:
            raise FakeConn.search_error
        self.entries = FakeConn.entries_by_call.pop(0) if FakeConn.entries_by_call else []
        return bool(self.entries)

    def unbind(self):
        if FakeConn.unbind_error:
            raise FakeConn.unbind_error


@pytest.fixture
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    for k in ("LDAP_LOGIN_USES_USERNAME", "LDAP_ADMIN_GROUPS", "DEBUG"):
        monkeypatch.delenv(k, raising=False)
    FakeConn.instances, FakeConn.bind_errors = [], {}
    FakeConn.entries_by_call, FakeConn.search_error, FakeConn.unbind_error = [], None, None
    monkeypatch.setattr(ldap, "Connection", FakeConn)
    monkeypatch.setattr(ldap, "_build_server", lambda *a, **k: object())
    return monkeypatch


class TestWithoutLdap3:
    def test_module_loads_without_ldap3_and_marks_unavailable(self, monkeypatch):
        for name in ("ldap3", "ldap3.core.exceptions", "ldap3.utils.conv"):
            monkeypatch.setitem(sys.modules, name, None)
        spec = importlib.util.spec_from_file_location("ldap_auth_without_ldap3", ldap.__file__)
        module = importlib.util.module_from_spec(spec)

        spec.loader.exec_module(module)

        assert module.LDAP_AVAILABLE is False

    def test_functions_return_empty_when_flag_is_off(self, env):
        env.setattr(ldap, "LDAP_AVAILABLE", False)

        assert ldap.fetch_ldap_user_photo("ivan") is None
        assert ldap.fetch_ldap_user_profile("ivan") is None
        assert ldap.fetch_ldap_user_profiles_bulk(["ivan"]) == {}
        assert ldap.fetch_ldap_user_group_profiles_bulk(["ivan"]) == {}
        assert ldap.search_ldap_users("iv") == []
        assert ldap.is_ldap_enabled() is False

    def test_authenticate_without_library_is_503(self, env):
        env.setattr(ldap, "LDAP_AVAILABLE", False)

        with pytest.raises(HTTPException) as ei:
            ldap.authenticate_ldap("ivan", "pass")

        assert ei.value.status_code == 503


class TestExtractBinary:
    @pytest.mark.parametrize("attrs", [{"a": [None]}, {"a": None}, {"a": []}, {"a": "text"}, {"a": [b""]}, {}])
    def test_non_binary_values_give_none(self, attrs):
        assert ldap._extract_binary_first(attrs, "a") is None

    def test_first_bytes_value(self):
        assert ldap._extract_binary_first({"a": [bytearray(b"\xff\xd8")]}, "a") == b"\xff\xd8"


class TestDiagnoseWithoutRequiredGroups:
    def test_no_member_of_in_filter_skips_diagnostics(self, env):
        env.setenv("LDAP_SEARCH_FILTER", "(&(objectClass=user)(sAMAccountName={{username}}))")
        settings = ldap._get_ldap_settings()

        ldap._diagnose_role_mismatch_with_service_bind(object(), settings, "ivan", ["mail"])

        assert FakeConn.instances == []  # service bind даже не открывался


class TestServiceBindLookup:
    def test_without_service_account_returns_none(self, env):
        env.delenv("LDAP_BIND_DN")

        assert ldap._service_bind_lookup_user("ivan") is None

    def test_incomplete_config_is_500(self, env):
        env.delenv("LDAP_URL")

        with pytest.raises(HTTPException) as ei:
            ldap._service_bind_lookup_user("ivan")

        assert ei.value.status_code == 500 and "url" in ei.value.detail


class TestPhotoAndProfile:
    def test_disabled_ldap(self, env):
        env.setenv("LDAP_ENABLED", "false")

        assert ldap.fetch_ldap_user_photo("ivan") is None
        assert ldap.fetch_ldap_user_profile("ivan") is None

    def test_incomplete_config_propagates_http_error(self, env):
        env.delenv("LDAP_SEARCH_FILTER")

        with pytest.raises(HTTPException):
            ldap.fetch_ldap_user_photo("ivan")
        with pytest.raises(HTTPException):
            ldap.fetch_ldap_user_profile("ivan")

    def test_service_bind_failure_gives_none(self, env):
        FakeConn.bind_errors[ENV["LDAP_BIND_DN"]] = LDAPBindError("bad creds")

        assert ldap.fetch_ldap_user_photo("ivan") is None
        assert ldap.fetch_ldap_user_profile("ivan") is None

    def test_second_lookup_name_is_tried(self, env):
        FakeConn.entries_by_call = [[], [Entry(attrs_of("ivan", thumbnailPhoto=[b"\xff\xd8photo"]))]]

        photo = ldap.fetch_ldap_user_photo("ghost", user_id="ivan")

        assert photo == b"\xff\xd8photo"

    def test_profile_from_second_name(self, env):
        FakeConn.entries_by_call = [[], [Entry(attrs_of("ivan"))]]

        profile = ldap.fetch_ldap_user_profile("ghost", user_id="ivan")

        assert profile["username"] == "ivan" and profile["email"] == "ivan@corp.local"

    def test_no_name_found_gives_none(self, env):
        assert ldap.fetch_ldap_user_profile("ghost") is None
        assert ldap.fetch_ldap_user_photo("ghost") is None


class TestAuthenticateServiceBindError:
    def test_service_account_bind_error_returns_none(self, env):
        FakeConn.bind_errors[ENV["LDAP_BIND_DN"]] = LDAPBindError("invalid service creds")

        assert ldap.authenticate_ldap("ivan", "pass") is None


class TestServiceSearch:
    def test_unbind_failure_is_swallowed(self, env):
        FakeConn.entries_by_call = [[Entry(attrs_of("ivan"))]]
        FakeConn.unbind_error = RuntimeError("socket closed")
        settings = ldap._get_ldap_settings()

        result = ldap._ldap_service_search(settings, "(cn=*)", ["mail"], size_limit=5)

        assert result == [attrs_of("ivan")]

    def test_service_bind_error_gives_empty(self, env):
        FakeConn.bind_errors[ENV["LDAP_BIND_DN"]] = LDAPException("down")

        assert ldap._ldap_service_search(ldap._get_ldap_settings(), "(cn=*)", [], size_limit=5) == []


class TestBulkProfiles:
    def test_missing_service_account_or_endpoint(self, env):
        env.delenv("LDAP_BIND_CREDENTIALS")
        assert ldap.fetch_ldap_user_profiles_bulk(["ivan"]) == {}
        assert ldap.fetch_ldap_user_group_profiles_bulk(["ivan"]) == {}
        env.setenv("LDAP_BIND_CREDENTIALS", "x")
        env.delenv("LDAP_URL")
        assert ldap.fetch_ldap_user_profiles_bulk(["ivan"]) == {}
        assert ldap.fetch_ldap_user_group_profiles_bulk(["ivan"]) == {}

    def test_blank_and_duplicate_logins_are_skipped(self, env):
        assert ldap.fetch_ldap_user_group_profiles_bulk(["", "  ", None]) == {}
        FakeConn.entries_by_call = [[Entry(attrs_of("ivan"))]]

        result = ldap.fetch_ldap_user_group_profiles_bulk(["Ivan", "ivan", " IVAN "])

        assert list(result) == ["ivan"]
        assert len(FakeConn.instances[0].searches) == 1
        assert FakeConn.instances[0].searches[0]["search_filter"].count("(sAMAccountName=Ivan)") >= 1

    def test_disabled_accounts_are_dropped(self, env):
        FakeConn.entries_by_call = [[
            Entry(attrs_of("ivan", userAccountControl=["514"])), Entry(attrs_of("petr")),
        ]]

        result = ldap.fetch_ldap_user_profiles_bulk(["ivan", "petr"])

        assert list(result) == ["petr"] and result["petr"]["full_name"] == "Имя petr"

    def test_group_bulk_drops_disabled_and_empty_login_entries(self, env):
        FakeConn.entries_by_call = [[
            Entry(attrs_of("ivan", lockoutTime=["5"])), Entry({"mail": ["x@y"]}), Entry(attrs_of("petr")),
        ]]

        result = ldap.fetch_ldap_user_group_profiles_bulk(["ivan", "petr"])

        assert list(result) == ["petr"]
        assert GROUP in result["petr"]["groups"] or result["petr"]["groups"]


class TestSharePlatformRole:
    def test_import_failure_allows_everyone(self, env, monkeypatch):
        monkeypatch.setitem(sys.modules, "backend.auth.agent_platform_roles", None)

        assert ldap._ldap_user_has_share_platform_role(attrs_of("ivan"), ldap._get_ldap_settings()) is True
