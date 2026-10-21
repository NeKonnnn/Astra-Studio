"""auth/ldap_auth: построение LDAP-фильтров, группы, статус учётной записи, TLS-настройки."""

import ssl

import pytest

from backend.auth import ldap_auth as ldap

pytestmark = pytest.mark.unit

SETTINGS = {
    "username_attr": "sAMAccountName",
    "id_attr": "sAMAccountName",
    "email_attr": "mail",
    "full_name_attr": "displayName",
    "search_filter": "(&(objectClass=user)(sAMAccountName={{username}})(memberOf=CN=Staff,OU=G,DC=corp,DC=local))",
}

INJECTIONS = ["*", "*)(uid=*", "admin)(|(objectClass=*", "a\\", "x\x00y", ")(cn=*))(|(cn=*"]


def _balanced(text: str) -> bool:
    """Скобки фильтра сбалансированы (экранированные символы не открывают новых условий)."""
    depth = 0
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if depth < 0:
            return False
    return depth == 0


class TestFilterEscaping:
    @pytest.mark.parametrize("payload", INJECTIONS)
    def test_login_filter_cannot_be_broken_out_of(self, payload):
        result = ldap._build_filter("(sAMAccountName={username})", payload)

        assert _balanced(result)
        assert result.count("(") == 1
        assert "*" not in result

    def test_both_placeholders_are_supported(self):
        assert ldap._build_filter("(a={{username}})(b={username})", "bob") == "(a=bob)(b=bob)"

    @pytest.mark.parametrize("payload", INJECTIONS)
    def test_identity_filter_escapes_the_name(self, payload):
        result = ldap._build_identity_search_filter(SETTINGS, payload)

        assert _balanced(result)
        assert result.count("(") == 5  # внешняя (| и четыре условия — ничего лишнего

    @pytest.mark.parametrize("payload", INJECTIONS)
    @pytest.mark.parametrize("query", ["plain", "ivan petrov", "user@corp"])
    def test_directory_search_escapes_every_query_shape(self, payload, query):
        result = ldap._build_directory_text_filter(SETTINGS, query + payload)

        assert _balanced(result)
        assert ")(uid=*" not in result and "(|(objectClass=*" not in result

    def test_wildcards_in_user_input_are_literal(self):
        result = ldap._build_directory_text_filter(SETTINGS, "ivan*")

        assert "ivan\\2a" in result

    def test_filter_special_characters_are_hex_escaped(self):
        assert ldap._build_filter("{username}", "a(b)c*d\\e") == "a\\28b\\29c\\2ad\\5ce"

    def test_default_attribute_names(self):
        result = ldap._build_identity_search_filter({}, "bob")

        assert result == "(|(sAMAccountName=bob)(sAMAccountName=bob)(userPrincipalName=bob)(cn=bob))"


class TestDirectoryTextFilter:
    def test_email_queries_use_mail_attributes(self):
        result = ldap._build_directory_text_filter(SETTINGS, "ivan@corp.ru")

        assert result == "(|(mail=ivan@corp.ru*)(mail=ivan@corp.ru*)(userPrincipalName=ivan@corp.ru*))"

    def test_login_like_queries_are_prefix_searches(self):
        result = ldap._build_directory_text_filter(SETTINGS, "ivan.p")

        assert "(sAMAccountName=ivan.p*)" in result and "*ivan.p*" not in result

    def test_free_text_queries_search_substrings_of_the_name(self):
        result = ldap._build_directory_text_filter(SETTINGS, "Иван Петров")

        assert "(displayName=*Иван Петров*)" in result and "(cn=*Иван Петров*)" in result

    def test_full_filter_combines_scope_and_text(self):
        result = ldap._build_directory_search_filter(SETTINGS, "ivan")

        assert result.startswith("(&(&(objectCategory=person)(objectClass=user)(memberOf=CN=Staff,OU=G,DC=corp,DC=local))")
        assert _balanced(result)


class TestScopeAndGroups:
    def test_member_of_dns_are_extracted_and_normalized(self):
        dns = ldap.extract_member_of_dns_from_search_filter("(&(a=b)(memberOf=CN=Staff, OU=G ,DC=corp)(MEMBEROF=CN=Admins,DC=corp))")

        assert dns == frozenset({"cn=staff,ou=g,dc=corp", "cn=admins,dc=corp"})

    def test_no_filter_means_no_groups(self):
        assert ldap.extract_member_of_dns_from_search_filter("") == frozenset()
        assert ldap.extract_member_of_dns_from_search_filter("(a=b)") == frozenset()

    def test_raw_dns_keep_their_original_form(self):
        assert ldap._extract_member_of_dns_raw("(memberOf=CN=A,DC=x)(memberOf=CN=B,DC=x)") == ["CN=A,DC=x", "CN=B,DC=x"]
        assert ldap._extract_member_of_dns_raw("") == []

    def test_user_groups_are_limited_to_the_configured_ones(self):
        allowed = "(memberOf=CN=Staff,OU=G,DC=corp)"

        kept = ldap._filter_member_of_by_search_filter(["cn=staff, ou=g, dc=corp", "CN=Other,DC=corp", "", None], allowed)

        assert kept == ["cn=staff, ou=g, dc=corp"]

    def test_without_a_group_filter_no_groups_are_exposed(self):
        assert ldap._filter_member_of_by_search_filter(["CN=Any,DC=corp"], None) == []

    def test_scope_filter_with_one_or_many_groups(self):
        one = ldap._build_directory_scope_filter({"search_filter": "(memberOf=CN=A,DC=x)"})
        many = ldap._build_directory_scope_filter({"search_filter": "(memberOf=CN=A,DC=x)(memberOf=CN=B,DC=x)"})
        none = ldap._build_directory_scope_filter({"search_filter": ""})

        assert one == "(&(objectCategory=person)(objectClass=user)(memberOf=CN=A,DC=x))"
        assert many.endswith("(|(memberOf=CN=A,DC=x)(memberOf=CN=B,DC=x)))")
        assert none == "(&(objectCategory=person)(objectClass=user))"

    def test_group_names_and_labels(self):
        assert ldap._extract_group_cn("CN=Finance Team,OU=Groups,DC=corp") == "Finance Team"
        assert ldap._extract_group_cn("cn=lower,dc=x") == "lower"
        assert ldap._extract_group_cn("PlainName") == "PlainName"
        assert ldap._extract_group_cn("") == ""
        assert ldap._dn_to_label("CN=A,DC=x") == "A" and ldap._dn_to_label(None) == ""

    def test_dn_normalization_for_comparison(self):
        assert ldap._normalize_ldap_dn(" CN=A , OU=B ,,DC=C ") == "cn=a,ou=b,dc=c"
        assert ldap._normalize_ldap_dn(None) == ""

    def test_csv_group_lists(self):
        assert ldap._split_groups(" a, b ,, c ") == ["a", "b", "c"]
        assert ldap._split_groups("") == []


class TestAccountStatus:
    @pytest.mark.parametrize(
        ("uac", "disabled"),
        [(512, False), (514, True), ("514", True), (66050, True), ([514], True), (None, False), ("garbage", False)],
    )
    def test_disabled_flag(self, uac, disabled):
        attrs = {} if uac is None else {"userAccountControl": uac}

        assert ldap._is_account_disabled(attrs) is disabled

    @pytest.mark.parametrize(
        ("lockout", "locked"),
        [(0, False), ("0", False), (133000000000000000, True), ("133000000000000000", True), (None, False), ("x", False)],
    )
    def test_locked_flag(self, lockout, locked):
        attrs = {} if lockout is None else {"lockoutTime": lockout}

        assert ldap._is_account_locked(attrs) is locked

    def test_status_messages(self):
        assert "отключена" in ldap._ldap_account_status_error({"userAccountControl": 514})
        assert "заблокирована" in ldap._ldap_account_status_error({"lockoutTime": 5})
        assert ldap._ldap_account_status_error({"userAccountControl": 512}) is None

    def test_disabled_wins_over_locked(self):
        assert "отключена" in ldap._ldap_account_status_error({"userAccountControl": 514, "lockoutTime": 5})

    def test_integer_attribute_parsing(self):
        assert ldap._parse_int_attr({"a": ["12"]}, "a") == 12
        assert ldap._parse_int_attr({"a": "12.7"}, "a") == 12
        assert ldap._parse_int_attr({"a": []}, "a") is None
        assert ldap._parse_int_attr({}, "a") is None


class TestAttributeHelpers:
    def test_first_value(self):
        assert ldap._extract_first({"a": ["x", "y"]}, "a") == "x"
        assert ldap._extract_first({"a": "z"}, "a") == "z"
        assert ldap._extract_first({"a": []}, "a") is None
        assert ldap._extract_first({"a": None}, "a") is None
        assert ldap._extract_first({}, "a") is None
        assert ldap._extract_first({"a": 5}, "") is None

    def test_binary_first_value(self):
        assert ldap._extract_binary_first({"p": [b"\x01\x02"]}, "p") == b"\x01\x02"
        assert ldap._extract_binary_first({}, "p") is None

    def test_photo_attribute_names(self, monkeypatch):
        monkeypatch.delenv("LDAP_PHOTO_ATTRS", raising=False)
        assert ldap._ldap_photo_attribute_names() == ["thumbnailPhoto", "jpegPhoto"]
        monkeypatch.setenv("LDAP_PHOTO_ATTRS", " a , ,b ")
        assert ldap._ldap_photo_attribute_names() == ["a", "b"]
        monkeypatch.setenv("LDAP_PHOTO_ATTRS", " , ")
        assert ldap._ldap_photo_attribute_names() == ["thumbnailPhoto", "jpegPhoto"]

    def test_domain_from_base_dn(self):
        assert ldap._domain_from_base_dn("OU=Users,DC=corp,DC=example,DC=com") == "corp.example.com"
        assert ldap._domain_from_base_dn("") == ""


class TestSettings:
    def test_defaults_disable_ldap(self, monkeypatch):
        monkeypatch.delenv("LDAP_ENABLED", raising=False)

        assert ldap.is_ldap_enabled() is False

    @pytest.mark.parametrize(("raw", "expected"), [("true", True), ("1", True), ("YES", True), ("on", True), ("false", False), ("0", False), ("", False)])
    def test_enabled_flag_parsing(self, monkeypatch, raw, expected):
        monkeypatch.setenv("LDAP_ENABLED", raw)

        assert ldap.is_ldap_enabled() is expected

    def test_env_bool_default(self, monkeypatch):
        monkeypatch.delenv("X_FLAG", raising=False)
        assert ldap._env_bool("X_FLAG", True) is True and ldap._env_bool("X_FLAG") is False

    def test_settings_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("LDAP_URL", "ldaps://ad.corp:636")
        monkeypatch.setenv("LDAP_ADMIN_GROUPS", "Admins, Ops")
        monkeypatch.setenv("LDAP_LOGIN_USES_USERNAME", "false")

        settings = ldap._get_ldap_settings()

        assert settings["url"] == "ldaps://ad.corp:636"
        assert settings["admin_groups"] == ["Admins", "Ops"]
        assert settings["login_uses_username"] is False


class TestServerTls:
    def test_plain_ldap_has_no_tls(self):
        server = ldap._build_server("ldap://ad.corp:389", 5, None)

        assert server.ssl is False and server.tls is None
        assert (server.host, server.port) == ("ad.corp", 389)

    def test_default_ports(self):
        assert ldap._build_server("ldaps://ad.corp", 5, None).port == 636
        assert ldap._build_server("ad.corp", 5, None).port == 389

    def test_ldaps_with_a_ca_file_validates_the_certificate(self, tmp_path):
        ca = tmp_path / "ca.pem"
        ca.write_text("x")

        server = ldap._build_server("ldaps://ad.corp", 5, str(ca))

        assert server.tls.validate == ssl.CERT_REQUIRED

    @pytest.mark.parametrize("ca", [None, "", "/no/such/ca.pem"])
    def test_ldaps_without_a_usable_ca_still_validates_with_system_cas(self, ca, monkeypatch):
        monkeypatch.delenv("LDAP_TLS_INSECURE", raising=False)

        server = ldap._build_server("ldaps://ad.corp", 5, ca)

        assert server.tls.validate == ssl.CERT_REQUIRED

    @pytest.mark.parametrize("ca", [None, "/no/such/ca.pem"])
    def test_ldaps_certificate_validation_is_off_only_with_explicit_insecure_flag(self, ca, monkeypatch):
        monkeypatch.setenv("LDAP_TLS_INSECURE", "1")

        server = ldap._build_server("ldaps://ad.corp", 5, ca)

        assert server.tls.validate == ssl.CERT_NONE

    def test_insecure_flag_does_not_override_a_valid_ca_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LDAP_TLS_INSECURE", "1")
        ca = tmp_path / "ca.pem"
        ca.write_text("x")

        assert ldap._build_server("ldaps://ad.corp", 5, str(ca)).tls.validate == ssl.CERT_REQUIRED
