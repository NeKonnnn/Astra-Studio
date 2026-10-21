"""auth/ldap_auth: сценарии входа, service bind, профили, поиск в каталоге — на фейковом ldap3-соединении."""

import ssl

import pytest
from fastapi import HTTPException
from ldap3.core.exceptions import LDAPBindError, LDAPException

from backend.auth import ldap_auth as ldap

pytestmark = pytest.mark.unit

GROUP = "CN=Staff,OU=Groups,DC=corp,DC=local"
ADMINS = "CN=Admins,OU=Groups,DC=corp,DC=local"
SEARCH_FILTER = f"(&(objectClass=user)(sAMAccountName={{{{username}}}})(memberOf={GROUP}))"

ENV = {
    "LDAP_ENABLED": "true",
    "LDAP_URL": "ldap://dc.corp.local",
    "LDAP_USER_SEARCH_BASE": "DC=corp,DC=local",
    "LDAP_SEARCH_FILTER": SEARCH_FILTER,
    "LDAP_ID": "sAMAccountName",
    "LDAP_EMAIL": "mail",
    "LDAP_FULL_NAME": "displayName",
    "LDAP_USERNAME": "sAMAccountName",
    "LDAP_ADMIN_GROUPS": "Admins",
}
SERVICE = {"LDAP_BIND_DN": "CN=svc,DC=corp,DC=local", "LDAP_BIND_CREDENTIALS": "svc-pass"}


def attrs_of(login="ivan", **extra):
    base = {
        "sAMAccountName": [login],
        "mail": [f"{login}@corp.local"],
        "displayName": ["Иван Петров"],
        "memberOf": [GROUP, "CN=Other,DC=x"],
    }
    base.update(extra)
    return base


class Entry:
    def __init__(self, attrs, dn="CN=ivan,DC=corp,DC=local"):
        self.entry_attributes_as_dict = attrs
        self.entry_dn = dn


class FakeConn:
    """Подмена ldap3.Connection: поведение задаётся классовыми атрибутами."""

    instances = []
    bind_errors = {}  # user -> исключение
    entries_by_call = []  # очередь списков Entry для search
    search_error = None

    def __init__(self, server, user=None, password=None, **kw):
        err = FakeConn.bind_errors.get(user)
        if err:
            raise err
        self.user, self.password, self.kw = user, password, kw
        self.entries, self.searches, self.unbound = [], [], False
        FakeConn.instances.append(self)

    def search(self, **kw):
        self.searches.append(kw)
        if FakeConn.search_error:
            raise FakeConn.search_error
        self.entries = FakeConn.entries_by_call.pop(0) if FakeConn.entries_by_call else []
        return bool(self.entries)

    def unbind(self):
        self.unbound = True


@pytest.fixture
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    for k in SERVICE:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    FakeConn.instances, FakeConn.bind_errors = [], {}
    FakeConn.entries_by_call, FakeConn.search_error = [], None
    monkeypatch.setattr(ldap, "Connection", FakeConn)
    monkeypatch.setattr(ldap, "_build_server", lambda *a, **k: object())
    return monkeypatch


@pytest.fixture
def service(env):
    for k, v in SERVICE.items():
        env.setenv(k, v)
    return env


class TestSettingsAndServer:
    def test_settings_come_from_environment(self, env):
        s = ldap._get_ldap_settings()
        assert s["enabled"] is True and s["url"] == "ldap://dc.corp.local"
        assert s["admin_groups"] == ["Admins"] and s["login_uses_username"] is True

    def test_is_ldap_enabled_follows_flag(self, env):
        assert ldap.is_ldap_enabled() is True
        env.setenv("LDAP_ENABLED", "no")
        assert ldap.is_ldap_enabled() is False

    @pytest.mark.parametrize(
        "url,host,port,ssl_on",
        [("ldap://dc.local", "dc.local", 389, False), ("ldaps://dc.local", "dc.local", 636, True), ("ldap://dc.local:3389", "dc.local", 3389, False), ("dc.local", "dc.local", 389, False)],
    )
    def test_build_server_picks_port_and_tls(self, monkeypatch, url, host, port, ssl_on):
        seen = {}
        monkeypatch.setattr(ldap, "Server", lambda **kw: seen.update(kw) or "srv")
        assert ldap._build_server(url, 7, None) == "srv"
        assert (seen["host"], seen["port"], seen["use_ssl"], seen["connect_timeout"]) == (host, port, ssl_on, 7)
        assert (seen["tls"] is not None) is ssl_on

    def test_ldaps_with_a_ca_file_requires_certificate_validation(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"
        ca.write_text("x")
        tls_args = []
        monkeypatch.setattr(ldap, "Tls", lambda **kw: tls_args.append(kw) or "tls")
        monkeypatch.setattr(ldap, "Server", lambda **kw: kw)
        monkeypatch.delenv("LDAP_TLS_INSECURE", raising=False)
        ldap._build_server("ldaps://dc", 5, str(ca))
        ldap._build_server("ldaps://dc", 5, str(tmp_path / "missing.pem"))
        assert tls_args[0]["ca_certs_file"] == str(ca)
        assert tls_args[0]["validate"] == tls_args[1]["validate"] == ssl.CERT_REQUIRED
        assert "ca_certs_file" not in tls_args[1]


class TestAttributeHelpers:
    def test_extract_first(self):
        assert ldap._extract_first({"a": ["x", "y"]}, "a") == "x"
        assert ldap._extract_first({"a": 5}, "a") == "5"
        assert ldap._extract_first({"a": []}, "a") is None
        assert ldap._extract_first({"a": None}, "a") is None
        assert ldap._extract_first({}, "a") is None and ldap._extract_first({"a": 1}, "") is None

    def test_binary_and_photo(self, env):
        assert ldap._extract_binary_first({"p": [b"img"]}, "p") == b"img"
        assert ldap._extract_binary_first({"p": [bytearray(b"i")]}, "p") == b"i"
        assert ldap._extract_binary_first({"p": [b""]}, "p") is None
        assert ldap._extract_binary_first({"p": ["text"]}, "p") is None
        assert ldap._extract_binary_first({"p": []}, "p") is None
        assert ldap._extract_ldap_photo_bytes({"jpegPhoto": [b"j"]}) == b"j"
        assert ldap._extract_ldap_photo_bytes({"thumbnailPhoto": [b"t"], "jpegPhoto": [b"j"]}) == b"t"
        assert ldap._extract_ldap_photo_bytes({}) is None

    def test_photo_attribute_names_are_configurable(self, env):
        env.setenv("LDAP_PHOTO_ATTRS", " a , ,b ")
        assert ldap._ldap_photo_attribute_names() == ["a", "b"]
        env.setenv("LDAP_PHOTO_ATTRS", " , ")
        assert ldap._ldap_photo_attribute_names() == ["thumbnailPhoto", "jpegPhoto"]

    def test_lookup_attributes_include_status_and_photo(self, env):
        names = set(ldap._ldap_lookup_attributes(ldap._get_ldap_settings()))
        assert {"memberOf", "userAccountControl", "lockoutTime", "thumbnailPhoto", "mail"} <= names

    @pytest.mark.parametrize("raw,expected", [("512", 512), ("3.0", 3), ("x", None), (None, None)])
    def test_parse_int(self, raw, expected):
        attrs = {} if raw is None else {"n": [raw]}
        assert ldap._parse_int_attr(attrs, "n") == expected

    @pytest.mark.parametrize(
        "attrs,message",
        [
            ({"userAccountControl": ["514"]}, "отключена"),
            ({"lockoutTime": ["133000000"]}, "заблокирована"),
            ({"userAccountControl": ["514"], "lockoutTime": ["1"]}, "отключена"),
            ({"userAccountControl": ["512"], "lockoutTime": ["0"]}, None),
            ({}, None),
        ],
    )
    def test_account_status(self, attrs, message):
        result = ldap._ldap_account_status_error(attrs)
        assert (message in result) if message else result is None

    def test_domain_from_base_dn(self):
        assert ldap._domain_from_base_dn("OU=Users, DC=corp, DC=local") == "corp.local"
        assert ldap._domain_from_base_dn("") == ""


class TestProfile:
    def test_only_groups_from_search_filter_are_exposed(self, env):
        profile = ldap._build_user_profile_from_attrs(attrs_of(), ldap._get_ldap_settings(), fallback_username="x")
        assert profile["user_id"] == "ivan" and profile["email"] == "ivan@corp.local"
        # groups — CN из LDAP_SEARCH_FILTER; ldap_groups — полный memberOf (для AGENT_ROLE_*).
        assert profile["groups"] == ["Staff"] and profile["ldap_groups"] == [GROUP, "CN=Other,DC=x"]
        assert profile["is_admin"] is False and "has_avatar" not in profile

    def test_admin_group_membership(self, env):
        env.setenv("LDAP_SEARCH_FILTER", f"(&(memberOf={GROUP})(memberOf={ADMINS}))")
        profile = ldap._build_user_profile_from_attrs(
            attrs_of(memberOf=[ADMINS]), ldap._get_ldap_settings(), fallback_username="x"
        )
        assert profile["is_admin"] is True

    def test_fallbacks_and_avatar(self, env):
        attrs = {"cn": ["Пётр"], "userPrincipalName": ["p@corp"], "thumbnailPhoto": [b"img"]}
        profile = ldap._build_user_profile_from_attrs(attrs, ldap._get_ldap_settings(), fallback_username="petr")
        assert profile["user_id"] == "petr" and profile["full_name"] == "Пётр" and profile["email"] == "p@corp"
        assert profile["has_avatar"] is True and profile["avatar_url"] == "/api/auth/me/avatar"


class TestAuthenticateWithUserBind:
    def test_success_binds_as_the_user_and_returns_the_profile(self, env):
        FakeConn.entries_by_call = [[Entry(attrs_of())]]
        profile = ldap.authenticate_ldap("ivan", "secret")
        assert profile["user_id"] == "ivan" and profile["groups"] == ["Staff"]
        conn = FakeConn.instances[0]
        assert conn.user == "ivan" and conn.password == "secret" and conn.unbound
        assert "sAMAccountName=ivan" in conn.searches[0]["search_filter"]

    def test_second_candidate_user_principal_is_tried_after_rejection(self, env):
        FakeConn.bind_errors = {"ivan": LDAPBindError("bad")}
        FakeConn.entries_by_call = [[Entry(attrs_of())]]
        assert ldap.authenticate_ldap("ivan", "secret")["user_id"] == "ivan"
        assert FakeConn.instances[0].user == "ivan@corp.local"

    def test_wrong_password_returns_none(self, env):
        FakeConn.bind_errors = {"ivan": LDAPBindError("bad"), "ivan@corp.local": LDAPException("down")}
        assert ldap.authenticate_ldap("ivan", "wrong") is None

    def test_login_uses_only_the_principal_when_username_login_is_disabled(self, env):
        env.setenv("LDAP_LOGIN_USES_USERNAME", "false")
        FakeConn.entries_by_call = [[Entry(attrs_of())]]
        ldap.authenticate_ldap("ivan", "secret")
        assert [c.user for c in FakeConn.instances] == ["ivan@corp.local"]

    def test_user_outside_the_required_group_is_rejected(self, env):
        FakeConn.entries_by_call = [[], []]
        assert ldap.authenticate_ldap("ivan", "secret") is None
        assert FakeConn.instances[0].unbound

    @pytest.mark.parametrize("extra", [{"userAccountControl": ["514"]}, {"lockoutTime": ["123"]}])
    def test_disabled_or_locked_accounts_are_rejected(self, env, extra):
        FakeConn.entries_by_call = [[Entry(attrs_of(**extra))]]
        assert ldap.authenticate_ldap("ivan", "secret") is None


class TestAuthenticateGuards:
    def test_disabled_ldap_and_empty_password_skip_the_server(self, env):
        assert ldap.authenticate_ldap("ivan", "") is None
        env.setenv("LDAP_ENABLED", "false")
        assert ldap.authenticate_ldap("ivan", "x") is None
        assert FakeConn.instances == []

    def test_missing_library_is_a_503(self, env):
        env.setattr(ldap, "LDAP_AVAILABLE", False)
        with pytest.raises(HTTPException) as err:
            ldap.authenticate_ldap("ivan", "x")
        assert err.value.status_code == 503

    def test_incomplete_configuration_names_missing_keys(self, env):
        env.delenv("LDAP_URL")
        env.delenv("LDAP_EMAIL")
        with pytest.raises(HTTPException) as err:
            ldap.authenticate_ldap("ivan", "x")
        # Известная проблема: HTTPException конфигурации ловится общим ``except Exception`` и
        # подменяется на безликое «Ошибка аутентификации» — администратор не видит, чего не хватает.
        assert err.value.status_code == 500 and err.value.detail == "Ошибка аутентификации"

    def test_bind_dn_without_credentials_is_a_config_error(self, env):
        env.setenv("LDAP_BIND_DN", "CN=svc")
        with pytest.raises(HTTPException) as err:
            ldap.authenticate_ldap("ivan", "x")
        assert err.value.status_code == 500 and err.value.detail == "Ошибка аутентификации"  # см. выше

    def test_ldap_errors_are_a_503_and_hide_details_unless_debug(self, env):
        FakeConn.entries_by_call = [[Entry(attrs_of())]]
        FakeConn.search_error = LDAPException("secret-host-details")
        with pytest.raises(HTTPException) as err:
            ldap.authenticate_ldap("ivan", "x")
        assert err.value.status_code == 503 and "secret-host" not in err.value.detail
        env.setenv("DEBUG", "true")
        with pytest.raises(HTTPException) as err:
            ldap.authenticate_ldap("ivan", "x")
        assert err.value.status_code == 500 and "secret-host-details" in err.value.detail

    def test_unexpected_errors_are_a_generic_500(self, env):
        FakeConn.entries_by_call = [[Entry(attrs_of())]]
        FakeConn.search_error = ValueError("boom-internal")
        with pytest.raises(HTTPException) as err:
            ldap.authenticate_ldap("ivan", "x")
        assert err.value.status_code == 500 and err.value.detail == "Ошибка аутентификации"
        env.setenv("DEBUG", "true")
        with pytest.raises(HTTPException) as err:
            ldap.authenticate_ldap("ivan", "x")
        assert "boom-internal" in err.value.detail


class TestAuthenticateWithServiceBind:
    def test_search_is_done_by_service_account_and_password_checked_by_dn(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of(), dn="CN=Ivan,OU=U,DC=corp,DC=local")]]
        profile = ldap.authenticate_ldap("ivan", "secret")
        assert profile["user_id"] == "ivan"
        svc, user = FakeConn.instances
        assert svc.user == SERVICE["LDAP_BIND_DN"] and user.user == "CN=Ivan,OU=U,DC=corp,DC=local"
        assert user.password == "secret" and user.unbound

    def test_wrong_password_returns_none(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of(), dn="CN=Ivan")]]
        FakeConn.bind_errors = {"CN=Ivan": LDAPBindError("invalid credentials")}
        assert ldap.authenticate_ldap("ivan", "wrong") is None

    def test_other_ldap_failure_while_checking_password_also_denies(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of(), dn="CN=Ivan")]]
        FakeConn.bind_errors = {"CN=Ivan": LDAPException("timeout")}
        assert ldap.authenticate_ldap("ivan", "x") is None

    def test_unknown_user_is_diagnosed_without_leaking_and_denied(self, service):
        FakeConn.entries_by_call = [[], [Entry(attrs_of(memberOf=["CN=Other,DC=x"]))]]
        assert ldap.authenticate_ldap("ivan", "x") is None
        assert "sAMAccountName=ivan" in FakeConn.instances[1].searches[0]["search_filter"]

    def test_disabled_account_is_rejected_after_password_check(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of(userAccountControl=["514"]), dn="CN=Ivan")]]
        assert ldap.authenticate_ldap("ivan", "ok") is None

    def test_diagnostics_never_break_login_denial(self, service):
        FakeConn.entries_by_call = [[]]
        original = FakeConn.search
        calls = {"n": 0}

        def flaky(self, **kw):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("diag failed")
            return original(self, **kw)

        service.setattr(FakeConn, "search", flaky)
        assert ldap.authenticate_ldap("ivan", "x") is None


class TestRoleDiagnostics:
    def test_user_bind_diagnostic_handles_all_branches(self, env):
        settings = ldap._get_ldap_settings()
        conn = FakeConn(None)
        FakeConn.entries_by_call = [[], [Entry(attrs_of())]]
        ldap._diagnose_role_mismatch_with_user_bind(conn, settings, "ivan", [])
        ldap._diagnose_role_mismatch_with_user_bind(conn, settings, "ivan", [])
        FakeConn.search_error = RuntimeError("x")
        ldap._diagnose_role_mismatch_with_user_bind(conn, settings, "ivan", [])
        ldap._diagnose_role_mismatch_with_user_bind(conn, {**settings, "search_filter": "(a=b)"}, "ivan", [])
        assert len(conn.searches) == 3

    def test_missing_roles_logging_covers_all_inputs(self, env):
        settings = ldap._get_ldap_settings()
        ldap._log_missing_required_roles("ivan", settings, None)
        ldap._log_missing_required_roles("ivan", settings, {"memberOf": ["CN=A,DC=x"]})
        ldap._log_missing_required_roles("ivan", {"search_filter": ""}, {})


class TestValidateAccount:
    def test_noop_when_ldap_or_service_bind_is_off(self, env):
        ldap.validate_ldap_user_account("ivan")  # без service bind
        env.setenv("LDAP_ENABLED", "0")
        ldap.validate_ldap_user_account("ivan")
        assert FakeConn.instances == []

    def test_active_account_passes(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of())]]
        ldap.validate_ldap_user_account("ivan")

    def test_second_name_is_tried_when_first_is_unknown(self, service):
        FakeConn.entries_by_call = [[], [Entry(attrs_of())]]
        ldap.validate_ldap_user_account("ivan", "ivan-id")
        assert len(FakeConn.instances) == 2

    def test_unknown_account_is_401(self, service):
        with pytest.raises(HTTPException) as err:
            ldap.validate_ldap_user_account("ivan", "ivan")
        assert err.value.status_code == 401 and len(FakeConn.instances) == 1

    @pytest.mark.parametrize("extra,word", [({"userAccountControl": ["514"]}, "отключена"), ({"lockoutTime": ["9"]}, "заблокирована")])
    def test_disabled_or_locked_is_401(self, service, extra, word):
        FakeConn.entries_by_call = [[Entry(attrs_of(**extra))]]
        with pytest.raises(HTTPException) as err:
            ldap.validate_ldap_user_account("ivan")
        assert err.value.status_code == 401 and word in err.value.detail

    @pytest.mark.parametrize("exc", [LDAPBindError("x"), LDAPException("x")])
    def test_directory_outage_is_503_not_401(self, service, exc):
        FakeConn.bind_errors = {SERVICE["LDAP_BIND_DN"]: exc}
        with pytest.raises(HTTPException) as err:
            ldap.validate_ldap_user_account("ivan")
        assert err.value.status_code == 503

    def test_incomplete_config_with_service_bind_is_500(self, service):
        service.delenv("LDAP_SEARCH_FILTER")
        with pytest.raises(HTTPException) as err:
            ldap.validate_ldap_user_account("ivan")
        assert err.value.status_code == 500


class TestPhotoAndProfileFetch:
    def test_photo_found(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of(thumbnailPhoto=[b"img"]))]]
        assert ldap.fetch_ldap_user_photo("ivan") == b"img"

    def test_photo_absent_or_unavailable(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of())], []]
        assert ldap.fetch_ldap_user_photo("ivan", "other") is None
        FakeConn.bind_errors = {SERVICE["LDAP_BIND_DN"]: LDAPException("x")}
        assert ldap.fetch_ldap_user_photo("ivan") is None

    def test_photo_and_profile_need_service_bind_and_ldap(self, env):
        assert ldap.fetch_ldap_user_photo("ivan") is None and ldap.fetch_ldap_user_profile("ivan") is None
        env.setenv("LDAP_ENABLED", "0")
        assert ldap.fetch_ldap_user_profile("ivan") is None

    def test_profile_of_an_active_user(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of())]]
        assert ldap.fetch_ldap_user_profile("ivan")["full_name"] == "Иван Петров"

    def test_profile_of_a_disabled_user_is_hidden(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of(userAccountControl=["514"]))]]
        assert ldap.fetch_ldap_user_profile("ivan") is None

    def test_profile_of_unknown_user_or_during_outage(self, service):
        assert ldap.fetch_ldap_user_profile("ghost") is None
        FakeConn.bind_errors = {SERVICE["LDAP_BIND_DN"]: LDAPBindError("x")}
        assert ldap.fetch_ldap_user_profile("ivan") is None


class TestDirectorySearch:
    def test_scope_filter_variants(self, env):
        s = ldap._get_ldap_settings()
        assert ldap._build_directory_scope_filter(s) == f"(&(objectCategory=person)(objectClass=user)(memberOf={GROUP}))"
        two = {**s, "search_filter": f"(memberOf={GROUP})(memberOf={ADMINS})"}
        assert f"(|(memberOf={GROUP})(memberOf={ADMINS}))" in ldap._build_directory_scope_filter(two)
        assert ldap._build_directory_scope_filter({"search_filter": ""}) == "(&(objectCategory=person)(objectClass=user))"
        assert ldap._extract_member_of_dns_raw("") == []

    def test_search_requires_service_bind_and_two_characters(self, env):
        assert ldap.search_ldap_users("iv") == []
        env.setenv("LDAP_BIND_DN", "x")
        env.setenv("LDAP_BIND_CREDENTIALS", "y")
        assert ldap.search_ldap_users("i") == [] and ldap.search_ldap_users(None) == []

    def test_search_without_url_or_base_is_empty(self, service):
        service.delenv("LDAP_URL")
        assert ldap.search_ldap_users("ivan") == []

    def test_search_returns_deduplicated_hits_without_inactive_accounts(self, service):
        FakeConn.entries_by_call = [
            [
                Entry(attrs_of("ivan")),
                Entry(attrs_of("IVAN")),
                Entry(attrs_of("anna", userAccountControl=["514"])),
                Entry(attrs_of("petr")),
                Entry({"cn": ["no-login"]}),
            ]
        ]
        hits = ldap.search_ldap_users("iv", limit=10)
        assert [h["user_id"] for h in hits] == ["ivan", "petr"]
        assert hits[0] == {"user_id": "ivan", "username": "ivan", "full_name": "Иван Петров", "email": "ivan@corp.local"}

    def test_limit_is_clamped_and_applied(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of(f"u{i}")) for i in range(30)]]
        assert len(ldap.search_ldap_users("uu", limit=500)) == 20
        FakeConn.entries_by_call = [[Entry(attrs_of(f"u{i}")) for i in range(30)]]
        assert len(ldap.search_ldap_users("uu", limit=0)) == 10
        assert FakeConn.instances[0].searches[0]["size_limit"] == 40

    def test_service_search_survives_size_limit_notice(self, service):
        class Notice(FakeConn):
            def search(self, **kw):
                self.entries = [Entry(attrs_of())]
                raise LDAPException("sizeLimitExceeded")

        service.setattr(ldap, "Connection", Notice)
        assert [h["user_id"] for h in ldap.search_ldap_users("iv")] == ["ivan"]

    def test_service_search_swallows_bind_and_search_failures(self, service):
        FakeConn.bind_errors = {SERVICE["LDAP_BIND_DN"]: LDAPException("x")}
        assert ldap.search_ldap_users("iv") == []
        FakeConn.bind_errors = {}

        class Broken(FakeConn):
            def search(self, **kw):
                raise LDAPException("a")

            @property
            def entries(self):
                raise LDAPException("b")

            @entries.setter
            def entries(self, value):
                pass

        service.setattr(ldap, "Connection", Broken)
        assert ldap.search_ldap_users("iv") == []

    def test_for_share_filters_by_platform_role(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of("ivan")), Entry(attrs_of("anna"))]]
        service.setattr(ldap, "_ldap_user_has_share_platform_role", lambda attrs, s: attrs["sAMAccountName"] == ["anna"])
        hits = ldap.search_ldap_users("an", for_share=True)
        assert [h["user_id"] for h in hits] == ["anna"]
        assert "memberOf" in FakeConn.instances[0].searches[0]["attributes"]

    def test_platform_role_check_is_permissive_when_mapping_is_disabled(self, env):
        from backend.auth import agent_platform_roles as roles

        env.setattr(roles, "is_agent_platform_role_mapping_enabled", lambda: False)
        assert ldap._ldap_user_has_share_platform_role(attrs_of(), ldap._get_ldap_settings()) is True

    def test_platform_role_check_uses_group_identifiers(self, env):
        from backend.auth import agent_platform_roles as roles

        env.setattr(roles, "is_agent_platform_role_mapping_enabled", lambda: True)
        env.setattr(roles, "get_platform_role_from_group_identifiers", lambda ids: "viewer" if "staff" in {i.lower() for i in ids} else None)
        settings = ldap._get_ldap_settings()
        assert ldap._ldap_user_has_share_platform_role(attrs_of(), settings) is True
        assert ldap._ldap_user_has_share_platform_role(attrs_of(memberOf=["CN=Other,DC=x"]), settings) is False


class TestBulkProfiles:
    def test_returns_nothing_when_not_applicable(self, env):
        assert ldap.fetch_ldap_user_profiles_bulk(["a"]) == {}  # нет service bind
        env.setenv("LDAP_ENABLED", "0")
        assert ldap.fetch_ldap_user_profiles_bulk(["a"]) == {}
        assert ldap.fetch_ldap_user_group_profiles_bulk(["a"]) == {}

    def test_empty_and_duplicate_names_are_dropped(self, service):
        assert ldap.fetch_ldap_user_profiles_bulk(["", None, "  "]) == {}
        FakeConn.entries_by_call = [[Entry(attrs_of("ivan"))]]
        result = ldap.fetch_ldap_user_profiles_bulk(["Ivan", "ivan", " IVAN "])
        assert list(result) == ["ivan"] and result["ivan"]["full_name"] == "Иван Петров"
        assert FakeConn.instances[0].searches[0]["search_filter"].count("(userPrincipalName=") == 1

    def test_names_that_were_not_found_are_absent(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of("ivan"))]]
        assert set(ldap.fetch_ldap_user_profiles_bulk(["ivan", "ghost"])) == {"ivan"}

    def test_injection_in_names_is_escaped(self, service):
        ldap.fetch_ldap_user_profiles_bulk(["a)(uid=*"])
        flt = FakeConn.instances[0].searches[0]["search_filter"]
        assert "a)(uid=*" not in flt and "\\28" in flt

    def test_group_profiles_include_groups_and_skip_inactive(self, service):
        FakeConn.entries_by_call = [[Entry(attrs_of("ivan")), Entry(attrs_of("anna", lockoutTime=["5"])), Entry({"cn": ["x"]})]]
        result = ldap.fetch_ldap_user_group_profiles_bulk(["ivan", "anna", "x"])
        assert set(result) == {"ivan"} and result["ivan"]["groups"] == ["Staff"]
        assert "memberOf" in FakeConn.instances[0].searches[0]["attributes"]
        assert ldap.fetch_ldap_user_group_profiles_bulk([]) == {}
