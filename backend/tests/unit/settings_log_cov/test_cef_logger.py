"""Тесты backend/settings/cef_logger/cef_logger.py: формат CEF, экранирование, контекст запроса."""

import logging
import re
from types import SimpleNamespace

import pytest

from backend.settings.cef_logger import cef_audit_context as actx
from backend.settings.cef_logger import cef_logger as cl


# ---------------- общие фикстуры ----------------


class _FakeUdpSocket:
    instances: list = []

    def __init__(self, family, kind):
        self.family, self.kind = family, kind
        self.sent: list = []
        self.fail_with: Exception | None = None
        _FakeUdpSocket.instances.append(self)

    def sendto(self, data, addr):
        if self.fail_with:
            raise self.fail_with
        self.sent.append((data, addr))


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Детерминированное окружение: без DNS, без реальных сокетов, чистые env."""
    for var in (
        "CEF_DEVICE_VENDOR",
        "CEF_DEVICE_PRODUCT",
        "CEF_DEVICE_VERSION",
        "CEF_DEVICE_PROCESS",
        "CEF_DVCHOST",
        "MY_POD_NAME",
        "POD_NAME",
        "HOSTNAME",
        "DOMAIN_SERVER",
        "CEF_DPT",
        "CEF_INT_SHOST",
        "CEF_LLM_DUSER",
        "CEF_LLM_DNTDOM",
        "LDAP_USER_SEARCH_BASE",
        "AUDIT_CEF_ENABLED",
        "AUDIT_SYSLOG_TARGET",
        "AUDIT_SYSLOG_PORT",
        "AUDIT_SYSLOG_FACILITY",
    ):
        monkeypatch.delenv(var, raising=False)
    _FakeUdpSocket.instances = []
    fake_socket = SimpleNamespace(
        socket=_FakeUdpSocket,
        AF_INET=2,
        SOCK_DGRAM=2,
        getfqdn=lambda: "host.fqdn.test",
        gethostname=lambda: "hostname-test",
    )
    monkeypatch.setattr(cl, "socket", fake_socket)
    monkeypatch.setattr(cl, "_SYSLOG_UDP_SOCK", None)
    monkeypatch.setattr(actx, "reverse_dns", lambda ip: f"{ip}.rdns.test")


@pytest.fixture
def cef_lines(capture_logger):
    """Список сообщений логгера "cef" (логгер сохраняется и восстанавливается)."""
    handler = capture_logger("cef", logging.INFO)
    return handler


def _req(host="10.0.0.5", port=4321, headers=None, path="/api/x", scheme="https", client=True):
    return SimpleNamespace(
        client=SimpleNamespace(host=host, port=port) if client else None,
        headers=headers or {},
        url=SimpleNamespace(path=path, scheme=scheme),
    )


def parse_cef(line: str):
    """Разбор строки CEF: (поля заголовка без префикса, словарь расширения)."""
    parts = re.split(r"(?<!\\)\|", line, maxsplit=7)
    assert len(parts) == 8, line
    ext = {}
    for chunk in re.split(r" (?=[A-Za-z0-9]+=)", parts[7]):
        key, _, value = chunk.partition("=")
        ext[key] = value
    return parts[:7], ext


def emit(cef_lines, event_id, **kwargs):
    cl.log_cef_event(event_id, **kwargs)
    assert cef_lines.messages, "событие не записано"
    return parse_cef(cef_lines.messages[-1])


# ---------------- configure_cef_logging ----------------


def test_configure_cef_logging_sets_up_raw_stdout_logger(monkeypatch):
    lg = logging.getLogger("cef")
    saved = (list(lg.handlers), lg.level, lg.propagate)
    try:
        lg.handlers[:] = []
        monkeypatch.setattr(cl, "_cef_logging_configured", False)
        cl.configure_cef_logging()
        assert lg.level == logging.INFO and lg.propagate is False
        (handler,) = lg.handlers
        assert handler.formatter._fmt == "%(message)s"
        cl.configure_cef_logging()  # повтор - без изменений
        assert lg.handlers == [handler]
    finally:
        lg.handlers[:] = saved[0]
        lg.setLevel(saved[1])
        lg.propagate = saved[2]


def test_configure_cef_logging_keeps_existing_handler(monkeypatch):
    lg = logging.getLogger("cef")
    saved = (list(lg.handlers), lg.level, lg.propagate)
    try:
        marker = logging.NullHandler()
        lg.handlers[:] = [marker]
        monkeypatch.setattr(cl, "_cef_logging_configured", False)
        cl.configure_cef_logging()
        assert lg.handlers == [marker]
    finally:
        lg.handlers[:] = saved[0]
        lg.setLevel(saved[1])
        lg.propagate = saved[2]


# ---------------- syslog / RFC5424 ----------------


@pytest.mark.parametrize(
    ("cef_sev", "syslog_sev"),
    [(0, 6), (3, 6), (4, 5), (5, 5), (6, 4), (7, 4), (8, 3), (10, 3)],
)
def test_cef_severity_to_syslog(cef_sev, syslog_sev):
    assert cl._cef_severity_to_syslog_pri_severity(cef_sev) == syslog_sev


def test_build_rfc5424_payload_default_facility(monkeypatch):
    monkeypatch.setenv("MY_POD_NAME", "pod-1")
    monkeypatch.setattr(cl.os, "getpid", lambda: 4242)
    payload = cl._build_rfc5424_syslog_payload("CEF:0|x", 5)
    text = payload.decode("utf-8")
    # LOCAL4 = 20 -> PRI = 20*8 + 5 (syslog severity для CEF 5)
    m = re.fullmatch(r"<165>1 (\S+Z) pod-1 astrachat 4242 - - CEF:0\|x", text)
    assert m, text
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", m.group(1))


def test_build_rfc5424_payload_custom_facility_and_unknown(monkeypatch):
    monkeypatch.setenv("AUDIT_SYSLOG_FACILITY", "auth")
    monkeypatch.setenv("CEF_DEVICE_PROCESS", "proc")
    text = cl._build_rfc5424_syslog_payload("L", 9).decode()
    assert text.startswith("<35>1 ")  # AUTH(4)*8 + 3
    assert " proc " in text
    monkeypatch.setenv("AUDIT_SYSLOG_FACILITY", "НЕТ-ТАКОГО")
    assert cl._build_rfc5424_syslog_payload("L", 0).decode().startswith("<166>1 ")  # LOCAL4*8+6


def test_build_payload_is_utf8():
    assert "Привет".encode("utf-8") in cl._build_rfc5424_syslog_payload("Привет", 1)


def test_resolve_cef_device_version(monkeypatch):
    assert cl.resolve_cef_device_version() == "0.0.0"
    monkeypatch.setenv("CEF_DEVICE_VERSION", "  ")
    assert cl.resolve_cef_device_version() == "0.0.0"
    monkeypatch.setenv("CEF_DEVICE_VERSION", " 1.2.3 ")
    assert cl.resolve_cef_device_version() == "1.2.3"


def test_resolve_cef_dvchost_priority(monkeypatch):
    assert cl.resolve_cef_dvchost() == "host.fqdn.test"
    monkeypatch.setattr(cl.socket, "getfqdn", lambda: "")
    assert cl.resolve_cef_dvchost() == "hostname-test"
    monkeypatch.setattr(cl.socket, "gethostname", lambda: "")
    assert cl.resolve_cef_dvchost() == "-"
    monkeypatch.setenv("HOSTNAME", "h-env")
    assert cl.resolve_cef_dvchost() == "h-env"
    monkeypatch.setenv("POD_NAME", "pod-env")
    assert cl.resolve_cef_dvchost() == "pod-env"
    monkeypatch.setenv("MY_POD_NAME", " my-pod ")
    assert cl.resolve_cef_dvchost() == "my-pod"
    monkeypatch.setenv("CEF_DVCHOST", " explicit ")
    assert cl.resolve_cef_dvchost() == "explicit"


def test_emit_syslog_disabled_or_no_target_sends_nothing(monkeypatch):
    cl._emit_cef_syslog_udp("CEF:0", 3)
    monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
    cl._emit_cef_syslog_udp("CEF:0", 3)
    monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "  ")
    cl._emit_cef_syslog_udp("CEF:0", 3)
    assert _FakeUdpSocket.instances == []


def test_emit_syslog_sends_datagram_and_reuses_socket(monkeypatch):
    monkeypatch.setenv("AUDIT_CEF_ENABLED", "TRUE")
    monkeypatch.setenv("AUDIT_SYSLOG_TARGET", " siem.local ")
    monkeypatch.setenv("AUDIT_SYSLOG_PORT", "1514")
    cl._emit_cef_syslog_udp("CEF:0|one", 3)
    cl._emit_cef_syslog_udp("CEF:0|two", 3)
    (sock,) = _FakeUdpSocket.instances
    assert [addr for _, addr in sock.sent] == [("siem.local", 1514)] * 2
    assert sock.sent[0][0].endswith(b"CEF:0|one") and sock.sent[1][0].endswith(b"CEF:0|two")


def test_emit_syslog_bad_port_falls_back_to_514(monkeypatch):
    monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
    monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "siem")
    monkeypatch.setenv("AUDIT_SYSLOG_PORT", "abc")
    cl._emit_cef_syslog_udp("L", 1)
    assert _FakeUdpSocket.instances[0].sent[0][1] == ("siem", 514)


def test_emit_syslog_oserror_is_logged_not_raised(monkeypatch, cef_lines):
    monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
    monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "siem")
    cl._emit_cef_syslog_udp("L", 1)
    _FakeUdpSocket.instances[0].fail_with = OSError("сеть недоступна")
    cl._emit_cef_syslog_udp("L", 1)  # не должно бросать исключение
    assert any("CEF syslog UDP send to siem:514 failed" in m for m in cef_lines.messages)


# ---------------- каталог событий ----------------


def test_events_catalog_consistent():
    assert cl.EVENTS
    for key, spec in cl.EVENTS.items():
        assert spec.event_id == key
        assert 0 <= spec.severity <= 10
        assert spec.outcome in ("success", "failure")
        assert spec.device_direction in (0, 1)
        assert spec.cat.startswith("/")


def test_event_spec_is_frozen():
    with pytest.raises(Exception):
        cl.EVENTS["SYS001"].severity = 10


# ---------------- метод аутентификации ----------------


@pytest.mark.parametrize(
    ("extra", "user", "expected"),
    [
        (None, None, "ldap"),
        ({"auth_method": " Keycloak "}, None, "keycloak"),
        (None, {"auth_method": "KEYCLOAK"}, "keycloak"),
        ({"auth_method": "   "}, None, "ldap"),
        ({"auth_method": "ldap"}, {"auth_method": "keycloak"}, "ldap"),
    ],
)
def test_resolve_auth_method(extra, user, expected):
    assert cl._resolve_auth_method(extra=extra, current_user=user) == expected


def test_login_msg_template_selection():
    d = "по умолчанию"
    assert cl._login_audit_msg_template("SEC001", "keycloak", d) == cl._SEC001_KEYCLOAK_MSG
    assert cl._login_audit_msg_template("USR001", "keycloak", d) == cl._USR001_KEYCLOAK_MSG
    assert cl._login_audit_msg_template("SEC003", "keycloak", d) == d
    assert cl._login_audit_msg_template("SEC001", "ldap", d) == d


# ---------------- экранирование и исход ----------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("простой", "простой"),
        (None, "-"),
        (0, "0"),
        ("a=b", r"a\=b"),
        ("back\\slash", "back\\\\slash"),
        ("line1\nline2", r"line1\nline2"),
        ("a\\=b", "a\\\\\\=b"),
        ("pipe|inside", "pipe|inside"),  # '|' в значениях расширения экранировать не нужно
    ],
)
def test_esc(raw, expected):
    assert cl._esc(raw) == expected


def test_esc_neutralizes_key_injection():
    """Значение не может добавить новый ключ расширения: '=' экранируется."""
    out = cl._esc("x outcome=success\nCEF:0|fake")
    assert "\n" not in out
    assert "outcome=" not in out.replace(r"\=", "")


def test_esc_escapes_carriage_return():
    assert "\r" not in cl._esc("a\rb")


def test_esc_carriage_return_becomes_literal_escape():
    assert cl._esc("a\rb") == "a\\rb"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(None, "success"), (200, "success"), (299, "success"), (404, "failure"), (499, "failure"),
     (500, "error"), (503, "error"), (302, "success"), (100, "success")],
)
def test_outcome_from_status(status, expected):
    assert cl._outcome_from_status(status) == expected


def test_domain_from_ldap_base_dn():
    assert cl.domain_from_ldap_base_dn("ou=Users, DC=corp ,dc=local") == "corp.local"
    assert cl.domain_from_ldap_base_dn("") == ""
    assert cl.domain_from_ldap_base_dn(None) == ""
    assert cl.domain_from_ldap_base_dn("cn=x") == ""


# ---------------- cs-поля LDAP/Keycloak ----------------


def _patch_roles(monkeypatch, role, matched):
    monkeypatch.setattr(
        "backend.auth.agent_platform_roles.resolve_cef_role_model",
        lambda user: (role, matched),
    )


def test_ldap_cs_fields_empty_user():
    assert cl.ldap_audit_cs_fields(None) == {}
    assert cl.ldap_audit_cs_fields({}) == {}


@pytest.mark.parametrize(
    ("role", "matched", "admin_groups", "expected_cs3"),
    [
        ("ADMIN", "", ["CN=admins", "CN=other"], "CN=admins"),
        ("ADMIN", "", [], "-"),
        ("EDITOR", "grp", ["x"], "grp"),
        ("BASE", "", ["x"], "-"),
    ],
)
def test_ldap_cs_fields(monkeypatch, role, matched, admin_groups, expected_cs3):
    monkeypatch.setattr(
        "backend.auth.ldap_auth._get_ldap_settings",
        lambda: {"url": "ldaps://dc.corp.local:636", "bind_dn": "CN=svc", "admin_groups": admin_groups},
    )
    _patch_roles(monkeypatch, role, matched)
    out = cl.ldap_audit_cs_fields({"username": "u"})
    assert out == {
        "cs1": "dc.corp.local",
        "cs1Label": "LDAPServer",
        "cs2": "CN=svc",
        "cs2Label": "LDAPBindAccount",
        "cs3": expected_cs3,
        "cs3Label": "MatchedADGroup",
        "cs4": role,
        "cs4Label": "RoleModel",
    }


def test_ldap_cs_fields_without_url_and_bind(monkeypatch):
    monkeypatch.setattr("backend.auth.ldap_auth._get_ldap_settings", lambda: {})
    _patch_roles(monkeypatch, "USER", "-")
    out = cl.ldap_audit_cs_fields({"username": "u"})
    assert (out["cs1"], out["cs2"], out["cs3"]) == ("-", "-", "-")


def test_ldap_cs_fields_url_without_hostname_uses_netloc(monkeypatch):
    monkeypatch.setattr("backend.auth.ldap_auth._get_ldap_settings", lambda: {"url": "dc-only"})
    _patch_roles(monkeypatch, "USER", "-")
    # urlparse("dc-only") -> hostname None, netloc "" -> "-"
    assert cl.ldap_audit_cs_fields({"username": "u"})["cs1"] == "-"


def test_ldap_cs_fields_fallback_on_error(monkeypatch):
    def boom():
        raise RuntimeError("нет настроек")

    monkeypatch.setattr("backend.auth.ldap_auth._get_ldap_settings", boom)
    out = cl.ldap_audit_cs_fields({"username": "u"})
    assert out["cs1"] == "-" and out["cs4"] == "USER" and out["cs2Label"] == "LDAPBindAccount"


def _kc(monkeypatch, **settings):
    monkeypatch.setattr("backend.auth.keycloak_auth.get_keycloak_settings", lambda: settings)


def test_keycloak_cs_fields_admin_matched_group(monkeypatch):
    _kc(monkeypatch, issuer="https://kc.corp/realms/main", client_id="astra", admin_groups=["g1", "g2"])
    _patch_roles(monkeypatch, "ADMIN", "")
    out = cl.keycloak_audit_cs_fields({"groups": ["x", "g2"]})
    assert out["cs1"] == "kc.corp" and out["cs2"] == "astra"
    assert out["cs3"] == "g2" and out["cs4"] == "ADMIN"
    assert out["cs1Label"] == "KeycloakIssuer" and out["cs3Label"] == "MatchedKeycloakRole"


def test_keycloak_cs_fields_admin_without_match_uses_first_admin_group(monkeypatch):
    _kc(monkeypatch, issuer="kc.corp/realms/main", admin_groups=["g1", "g2"])
    _patch_roles(monkeypatch, "ADMIN", "")
    out = cl.keycloak_audit_cs_fields({"groups": ["zzz"]})
    assert out["cs3"] == "g1"
    # issuer без схемы: хост не выделяется, берётся последний сегмент пути
    assert out["cs1"] == "main"
    assert out["cs2"] == "-"


def test_keycloak_cs_fields_admin_without_configured_groups(monkeypatch):
    _kc(monkeypatch, issuer="", client_id="c")
    _patch_roles(monkeypatch, "ADMIN", "")
    out = cl.keycloak_audit_cs_fields(None)
    assert (out["cs1"], out["cs3"]) == ("-", "-")


def test_keycloak_cs_fields_user_role_takes_first_token_group(monkeypatch):
    _kc(monkeypatch, issuer="https://kc/x")
    _patch_roles(monkeypatch, "USER", "-")
    assert cl.keycloak_audit_cs_fields({"groups": ["a", "b"]})["cs3"] == "a"
    assert cl.keycloak_audit_cs_fields({"groups": []})["cs3"] == "-"
    assert cl.keycloak_audit_cs_fields(None)["cs3"] == "-"


def test_keycloak_cs_fields_platform_roles(monkeypatch):
    _kc(monkeypatch, issuer="https://kc/x")
    _patch_roles(monkeypatch, "EDITOR", "grp-editors")
    assert cl.keycloak_audit_cs_fields({"groups": ["a"]})["cs3"] == "grp-editors"
    _patch_roles(monkeypatch, "BASE", "")
    assert cl.keycloak_audit_cs_fields({"groups": ["a"]})["cs3"] == "-"


def test_keycloak_cs_fields_defaults_on_error(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr("backend.auth.keycloak_auth.get_keycloak_settings", boom)
    out = cl.keycloak_audit_cs_fields({"groups": ["a"]})
    assert out["cs1"] == "-" and out["cs4"] == "USER" and out["cs1Label"] == "KeycloakIssuer"


def test_login_audit_cef_extra_dispatch(monkeypatch):
    monkeypatch.setattr(cl, "keycloak_audit_cs_fields", lambda u: {"cs1": "kc"})
    monkeypatch.setattr(cl, "ldap_audit_cs_fields", lambda u: {"cs1": "ld"})
    assert cl.login_audit_cef_extra({}, " Keycloak ") == {"auth_method": "keycloak", "cs1": "kc"}
    assert cl.login_audit_cef_extra({}, "") == {"auth_method": "ldap", "cs1": "ld"}
    assert cl.login_audit_cef_extra({}, None)["auth_method"] == "ldap"


def test_ldap_reason_suffix(monkeypatch):
    monkeypatch.setattr(
        "backend.auth.ldap_auth._get_ldap_settings",
        lambda: {"url": "ldap://dc.corp:389", "bind_dn": "CN=svc"},
    )
    assert cl.ldap_reason_suffix() == " (сервер dc.corp, ТУЗ CN=svc)"
    monkeypatch.setattr("backend.auth.ldap_auth._get_ldap_settings", lambda: {})
    assert cl.ldap_reason_suffix() == " (сервер -, ТУЗ -)"


def test_ldap_reason_suffix_error_gives_empty(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr("backend.auth.ldap_auth._get_ldap_settings", boom)
    assert cl.ldap_reason_suffix() == ""


# ---------------- _resolve_cef_current_user ----------------


def test_resolve_current_user_explicit_wins():
    assert cl._resolve_cef_current_user(_req(), {"username": "a"}) == {"username": "a"}


def test_resolve_current_user_from_audit_context():
    def body():
        tokens = actx.cef_audit_set(user={"username": "from-ctx"})
        try:
            assert cl._resolve_cef_current_user(None, None) == {"username": "from-ctx"}
        finally:
            actx.cef_audit_reset(tokens)

    import contextvars

    contextvars.copy_context().run(body)


def test_resolve_current_user_from_request_token(monkeypatch):
    monkeypatch.setattr(
        "backend.auth.jwt_handler.try_user_from_request", lambda r: {"username": "jwt-user"}
    )
    assert cl._resolve_cef_current_user(_req(), None) == {"username": "jwt-user"}


def test_resolve_current_user_failures_give_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(actx, "cef_audit_peek", boom)
    monkeypatch.setattr("backend.auth.jwt_handler.try_user_from_request", boom)
    assert cl._resolve_cef_current_user(_req(), None) is None
    assert cl._resolve_cef_current_user(None, None) is None


# ---------------- request_context ----------------


def test_request_context_without_request_and_socket_hint():
    import contextvars

    def body():
        return cl.request_context(None, {"username": "bob"})

    ctx = contextvars.copy_context().run(body)
    assert ctx == {
        "src": "127.0.0.1",
        "shost": "localhost",
        "spt": 0,
        "app": "HTTPS",
        "suser": "bob",
        "sntdom": "-",
    }


def test_request_context_from_socket_hint(monkeypatch):
    import contextvars

    monkeypatch.setenv("LDAP_USER_SEARCH_BASE", "ou=u,dc=corp,dc=local")

    def body():
        actx.cef_audit_set(
            socket_remote={"src": " 10.1.1.1 ", "spt": "5555", "app": "http", "shost_override": "ws-9"}
        )
        return cl.request_context(None, None)

    ctx = contextvars.copy_context().run(body)
    assert ctx == {
        "src": "10.1.1.1",
        "shost": "ws-9",
        "spt": 5555,
        "app": "HTTP",
        "suser": "anonymous",
        "sntdom": "corp.local",
    }


def test_request_context_socket_hint_bad_port_and_default_app():
    import contextvars

    def body():
        actx.cef_audit_set(socket_remote={"src": "10.1.1.2", "spt": "abc", "app": None})
        return cl.request_context(None, {"username": "u"})

    ctx = contextvars.copy_context().run(body)
    assert ctx["spt"] == 0 and ctx["app"] == "HTTPS"
    assert ctx["shost"] == "10.1.1.2.rdns.test"


def test_request_context_from_http_request():
    r = _req(
        host="10.2.2.2",
        port=7777,
        headers={"x-forwarded-for": "8.8.8.8", "x-client-hostname": " laptop ", "x-forwarded-proto": "HTTP"},
    )
    ctx = cl.request_context(r, {"username": "alice"})
    assert ctx["src"] == "8.8.8.8" and ctx["shost"] == "laptop"
    assert ctx["spt"] == 7777 and ctx["app"] == "HTTP" and ctx["suser"] == "alice"


def test_request_context_http_scheme_taken_from_url():
    ctx = cl.request_context(_req(scheme="https"), {"username": "a"})
    assert ctx["app"] == "HTTPS"
    ctx = cl.request_context(_req(scheme="http", client=False), {"username": "a"})
    assert ctx["app"] == "HTTP" and ctx["spt"] == 0 and ctx["src"] == "127.0.0.1"


def test_request_context_without_context_module_helpers(monkeypatch):
    """Если вспомогательные функции контекста недоступны, используются запасные ветки."""
    monkeypatch.delattr(actx, "resolve_client_shost")
    r = _req(host="10.3.3.3, 10.4.4.4")
    ctx = cl.request_context(r, {"username": "u"})
    assert ctx["src"] == "10.3.3.3" and ctx["shost"] == "10.3.3.3"
    r2 = _req(client=False)
    assert cl.request_context(r2, {"username": "u"})["src"] == "127.0.0.1"
    assert cl.request_context(None, {"username": "u"})["shost"] == "localhost"


def test_request_context_survives_trace_failure(monkeypatch):
    def boom(**kw):
        raise RuntimeError("trace")

    monkeypatch.setattr(actx, "log_request_context_shost", boom)
    assert cl.request_context(None, {"username": "u"})["suser"] == "u"


# ---------------- LLM helpers ----------------


def test_llm_int003_extra_basic(monkeypatch):
    extra = cl.cef_llm_int003_extra(
        base_url="https://llm.corp:8443/", provider_id="prov", model="gpt", request_uuid="uid"
    )
    assert extra == {
        "cs1": "gpt",
        "cs1Label": "LLMModel",
        "cs3": "prov",
        "cs3Label": "LLMProvider",
        "request": "https://llm.corp:8443/v1/chat/completions",
        "duser": "API-KEY",
        "requestUuid": "uid",
        "dhost": "llm.corp",
        "dpt": 8443,
    }


def test_llm_int003_extra_default_https_port_env_overrides(monkeypatch):
    monkeypatch.setenv("CEF_LLM_DUSER", "svc-llm")
    monkeypatch.setenv("CEF_LLM_DNTDOM", "corp.local")
    extra = cl.cef_llm_int003_extra(base_url="https://llm.corp", provider_id="p", model="m", request_uuid="u")
    assert extra["dpt"] == 443 and extra["duser"] == "svc-llm" and extra["dntdom"] == "corp.local"


def test_llm_int003_extra_http_without_port_has_no_dpt():
    extra = cl.cef_llm_int003_extra(base_url="http://llm", provider_id="p", model="m", request_uuid="u")
    assert "dpt" not in extra and extra["dhost"] == "llm"


def test_llm_int003_extra_empty_and_invalid_url():
    extra = cl.cef_llm_int003_extra(base_url="", provider_id="p", model="m", request_uuid="u")
    assert extra["request"] == "/v1/chat/completions" and "dhost" not in extra
    # некорректный порт: urlparse(...).port бросает ValueError - это не должно ломать аудит
    bad = cl.cef_llm_int003_extra(base_url="http://h:abc", provider_id="p", model="m", request_uuid="u")
    assert bad["dhost"] == "h" and "dpt" not in bad


def test_log_cef_int003_llm_request_delegates(monkeypatch):
    calls = []
    monkeypatch.setattr(cl, "log_cef_event", lambda *a, **k: calls.append((a, k)))
    cl.log_cef_int003_llm_request(base_url="http://h:1", provider_id="p", model="m", request_uuid="u")
    (args, kwargs), = calls
    assert args == ("INT003",)
    assert kwargs["current_user"] is None and kwargs["status_code"] == 200
    assert kwargs["extra"]["cs1"] == "m" and kwargs["extra"]["dpt"] == 1


def test_log_cef_int006_llm_api_failure_uses_audit_context_and_truncates(monkeypatch):
    import contextvars

    calls = []
    monkeypatch.setattr(cl, "log_cef_event", lambda *a, **k: calls.append((a, k)))

    def body():
        req, user = object(), {"username": "u"}
        actx.cef_audit_set(request=req, user=user)
        cl.log_cef_int006_llm_api_failure(
            request_uuid="uid", code_status=502, text_status="x" * 600, service_name="LLM"
        )
        cl.log_cef_int006_llm_api_failure(
            request_uuid="uid", code_status="E", text_status=None, service_name="LLM",
            method_name="GET /m", status_code=500,
        )
        return req, user

    req, user = contextvars.copy_context().run(body)
    (a1, k1), (a2, k2) = calls
    assert a1 == ("INT006",) and k1["request"] is req and k1["current_user"] is user
    assert k1["status_code"] is None
    assert k1["extra"]["codeStatus"] == "502" and len(k1["extra"]["textStatus"]) == 512
    assert k1["extra"]["methodName"] == "POST /v1/chat/completions"
    assert k2["extra"]["textStatus"] == "" and k2["extra"]["methodName"] == "GET /m"
    assert k2["status_code"] == 500


# ---------------- порядок полей расширения ----------------


def test_extension_ordered_items():
    items = cl._extension_ordered_items(
        {"zzz": 1, "act": "a", "externalId": "e", "none": None, "_hidden": 1, "aaa": 2, "cat": None}
    )
    assert items == [("externalId", "e"), ("act", "a"), ("aaa", 2), ("zzz", 1)]


# ---------------- log_cef_event ----------------


def test_log_cef_event_unknown_id_warns(cef_lines):
    cl.log_cef_event("NOPE999")
    assert cef_lines.messages == ["CEF event id 'NOPE999' is not configured"]


def test_log_cef_event_header_and_core_fields(monkeypatch, cef_lines):
    monkeypatch.setenv("CEF_DEVICE_VENDOR", "ACME")
    monkeypatch.setenv("CEF_DEVICE_PRODUCT", "Chat")
    monkeypatch.setenv("CEF_DEVICE_VERSION", "9.9")
    monkeypatch.setenv("DOMAIN_SERVER", "chat.corp")
    monkeypatch.setenv("CEF_DPT", "8443")
    line_user = {"username": "alice", "user_id": "u-1", "email": "a@corp"}
    req = _req(host="10.0.0.9", port=1111, headers={"x-client-hostname": "pc-1"})
    header, ext = emit(cef_lines, "AGT001", request=req, current_user=line_user, status_code=201,
                       extra={"cs1": "Агент", "cs2": "id-5"})
    assert cef_lines.messages[-1].startswith("CEF:0|ACME|Chat|9.9|AGT001|Agent Created|3|")
    assert header[0] == "CEF:0" and header[5] == "Agent Created" and header[6] == "3"
    assert ext["cat"] == "/Object/Agent/Create" and ext["act"] == "create"
    assert ext["outcome"] == "success"
    assert (ext["suser"], ext["suid"]) == ("alice", "u-1")
    assert (ext["src"], ext["shost"], ext["spt"], ext["app"]) == ("10.0.0.9", "pc-1", "1111", "HTTPS")
    assert ext["dhost"] == "chat.corp" and ext["dpt"] == "8443"
    assert ext["deviceDirection"] == "0"
    assert re.fullmatch(r"[0-9a-f]{32}", ext["externalId"])
    assert ext["start"] == ext["end"] and ext["rt"].endswith("Z")
    assert ext["msg"].startswith("Агент Агент (ID: id-5) создан пользователем alice")
    # порядок полей: externalId идёт первым, cs1/cs2 - после msg
    keys = list(ext)
    assert keys[0] == "externalId" and keys.index("msg") < keys.index("cs1")


def test_log_cef_event_outcome_from_status_and_explicit(cef_lines):
    u = {"username": "u"}
    assert emit(cef_lines, "SEC003", current_user=u, status_code=401)[1]["outcome"] == "failure"
    assert emit(cef_lines, "SEC003", current_user=u, status_code=503)[1]["outcome"] == "error"
    assert emit(cef_lines, "SEC003", current_user=u, status_code=200, outcome="denied")[1]["outcome"] == "denied"


def test_failure_event_without_status_must_be_failure(cef_lines):
    _, ext = emit(cef_lines, "SEC003", current_user={"username": "u"}, extra={"reason": "bad"})
    assert ext["outcome"] == "failure"


def test_failure_event_without_status_uses_event_outcome(cef_lines):
    _, ext = emit(cef_lines, "INT006", current_user={"username": "u"}, extra={"codeStatus": "EXCEPTION"})
    assert ext["outcome"] == "failure"


def test_log_cef_event_sys_events_use_system_identity(monkeypatch, cef_lines):
    monkeypatch.setenv("DOMAIN_SERVER", "chat.corp")
    monkeypatch.setenv("LDAP_USER_SEARCH_BASE", "dc=corp,dc=local")
    _, ext = emit(cef_lines, "SYS001")
    assert ext["suser"] == "SYSTEM" and ext["src"] == "127.0.0.1" and ext["shost"] == "host.fqdn.test"
    assert ext["sntdom"] == "corp.local" and "suid" not in ext
    assert "https://chat.corp" in ext["msg"]
    _, ext2 = emit(cef_lines, "SYS002")
    assert ext2["suser"] == "SYSTEM" and ext2["sntdom"] == "corp.local"
    assert "остановлено" in ext2["msg"]


def test_log_cef_event_sys_sntdom_dash_without_ldap_base(cef_lines):
    assert emit(cef_lines, "SYS002")[1]["sntdom"] == "-"


def test_log_cef_event_sys_shost_falls_back_to_hostname_and_localhost(monkeypatch, cef_lines):
    monkeypatch.setattr(cl.socket, "getfqdn", lambda: "")
    assert emit(cef_lines, "SYS002")[1]["shost"] == "hostname-test"
    monkeypatch.setattr(cl.socket, "gethostname", lambda: "")
    assert emit(cef_lines, "SYS002")[1]["shost"] == "localhost"


def test_log_cef_event_uses_audit_context_when_arguments_missing(cef_lines):
    import contextvars

    def body():
        actx.cef_audit_set(request=_req(host="10.7.7.7"), user={"username": "ctx-user", "user_id": 5})
        return emit(cef_lines, "CNV001", extra={"cs2": "chat-1"})

    _, ext = contextvars.copy_context().run(body)
    assert ext["suser"] == "ctx-user" and ext["suid"] == "5" and ext["src"] == "10.7.7.7"


def test_log_cef_event_tolerates_audit_context_failure(monkeypatch, cef_lines):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(actx, "cef_audit_peek", boom)
    _, ext = emit(cef_lines, "CNV001", current_user={"username": "u"}, extra={"cs2": "c"})
    assert ext["suser"] == "u"


def test_log_cef_event_int003_takes_conversation_from_context(cef_lines):
    import contextvars

    def body():
        actx.cef_audit_set(conversation_id="conv-42", user={"username": "u"})
        a = emit(cef_lines, "INT003", extra={"cs1": "m", "cs3": "p"})[1]
        b = emit(cef_lines, "INT003", extra={"cs1": "m", "cs2": "own"})[1]
        c = emit(cef_lines, "INT003", extra={"cs1": "m", "cs2": "-", "cs2Label": "Mine"})[1]
        return a, b, c

    a, b, c = contextvars.copy_context().run(body)
    assert a["cs2"] == "conv-42" and a["cs2Label"] == "ConversationId"
    assert b["cs2"] == "own"
    assert c["cs2"] == "conv-42" and c["cs2Label"] == "Mine"


def test_log_cef_event_cef_tail_and_inbound_overrides(cef_lines):
    u = {"username": "u"}
    _, ext = emit(cef_lines, "INT001", current_user=u,
                  extra={"cs1": "tool", "cs2": "chat", "cs3": "srv", "cef_tail": " (хвост)"})
    assert "(хвост)" in ext["msg"]
    _, ext = emit(cef_lines, "INT001", current_user=u, extra={"cs1": "t", "cs2": "c", "cs3": "s", "cef_tail": None})
    assert ext["msg"].endswith("[/Integration/MCP/Invoke]")


def test_log_cef_event_int001_int002_use_local_source(monkeypatch, cef_lines):
    monkeypatch.setenv("CEF_INT_SHOST", "svc-host")
    for event in ("INT001", "INT002"):
        _, ext = emit(cef_lines, event, current_user={"username": "u"},
                      extra={"cs1": "t", "cs2": "c", "cs3": "s", "cn1": 12, "outcome": "ok"})
        assert "src" not in ext and ext["shost"] == "svc-host" and ext["spt"] == "0"
        assert ext["deviceDirection"] == "1"


def test_log_cef_event_outgoing_direction_copies_destination(cef_lines):
    _, ext = emit(
        cef_lines, "INT003", current_user={"username": "u"},
        extra={"cs1": "m", "cs3": "p", "dst": "10.9.9.9", "duser": "API-KEY", "dntdom": "corp",
               "dhost": "llm.corp", "dpt": "8443"},
    )
    assert (ext["dst"], ext["duser"], ext["dntdom"], ext["dhost"], ext["dpt"]) == (
        "10.9.9.9", "API-KEY", "corp", "llm.corp", "8443")
    assert ext["deviceDirection"] == "1"


def test_log_cef_event_direction_override_via_extra(cef_lines):
    _, ext = emit(cef_lines, "INT005", current_user={"username": "u"},
                  extra={"_deviceDirection": "1", "dhost": "mongo", "dpt": 27017,
                         "methodName": "find", "serviceName": "Mongo", "requestUuid": "r1"})
    assert ext["deviceDirection"] == "1" and ext["dhost"] == "mongo" and ext["dpt"] == "27017"
    assert "_deviceDirection" not in ext


def test_log_cef_event_inbound_ignores_destination_extras_for_dir_zero(cef_lines):
    _, ext = emit(cef_lines, "CNV001", current_user={"username": "u"}, extra={"cs2": "c", "dst": "1.1.1.1"})
    # для направления 0 dst из extra всё равно попадает в расширение общим update,
    # но dhost/dpt остаются собственными значениями сервиса
    assert ext["dhost"] == "host.fqdn.test" and ext["dpt"] == "443"


def test_log_cef_event_dmail_and_copy_target_are_message_only(cef_lines):
    _, ext = emit(cef_lines, "USR002", current_user={"username": "admin", "email": "adm@corp"},
                  extra={"duser": "victim", "dmail": "victim@corp"})
    assert "victim@corp" in ext["msg"] and "dmail" not in ext
    _, ext = emit(cef_lines, "USR002", current_user={"username": "admin", "email": "adm@corp"},
                  extra={"duser": "victim", "dmail": "-"})
    assert "(adm@corp)" in ext["msg"]  # запасной адрес - почта текущего пользователя
    _, ext = emit(cef_lines, "USR002", current_user={"username": "admin"}, extra={"duser": "v"})
    assert "(-)" in ext["msg"]
    _, ext = emit(cef_lines, "AGT003", current_user={"username": "u"}, extra={"cs1": "A", "cs2": "copy-id"})
    assert "скопирован в copy-id" in ext["msg"] and "agt_copy_target" not in ext
    _, ext = emit(cef_lines, "AGT003", current_user={"username": "u"},
                  extra={"cs1": "A", "cs2": "copy-id", "agt_copy_target": "Копия"})
    assert "скопирован в Копия" in ext["msg"]
    _, ext = emit(cef_lines, "AGT003", current_user={"username": "u"}, extra={"cs1": "A"})
    assert "скопирован в -" in ext["msg"]


def test_log_cef_event_missing_template_fields_become_dash(cef_lines):
    _, ext = emit(cef_lines, "SEC003", current_user={"username": "u"}, status_code=401)
    assert "Причина: -" in ext["msg"]


def test_log_cef_event_keycloak_template(cef_lines):
    _, ext = emit(
        cef_lines, "SEC001", current_user={"username": "kc-user"}, status_code=200,
        extra={"auth_method": "keycloak", "cs1": "kc.corp", "cs2": "client", "cs3": "grp", "cs4": "USER"},
    )
    assert "Keycloak SSO" in ext["msg"] and "kc.corp" in ext["msg"]
    _, ext = emit(
        cef_lines, "SEC001", current_user={"username": "ld-user"}, status_code=200,
        extra={"cs1": "dc", "cs2": "bind", "cs3": "g", "cs4": "USER"},
    )
    assert "LDAP-сервер dc" in ext["msg"]


def test_log_cef_event_pops_internal_extra_without_mutating_input(cef_lines):
    extra = {"cs1": "a", "cef_tail": "t", "_deviceDirection": 1, "_private": "x"}
    emit(cef_lines, "INT001", current_user={"username": "u"}, extra=extra)
    assert extra == {"cs1": "a", "cef_tail": "t", "_deviceDirection": 1, "_private": "x"}
    assert "_private" not in cef_lines.messages[-1]


def test_log_cef_event_none_extra_values_are_dropped(cef_lines):
    _, ext = emit(cef_lines, "CNV001", current_user={"username": "u"}, extra={"cs2": "c", "fname": None})
    assert "fname" not in ext


def test_log_cef_event_sends_syslog_copy(monkeypatch, cef_lines):
    monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
    monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "siem")
    emit(cef_lines, "CNV001", current_user={"username": "u"}, extra={"cs2": "c"})
    (sock,) = _FakeUdpSocket.instances
    payload = sock.sent[0][0].decode()
    assert payload.endswith(cef_lines.messages[-1])
    assert payload.startswith("<166>1 ")  # CNV001 severity 1 -> syslog 6; LOCAL4*8+6


def test_log_cef_event_syslog_pri_matches_severity(monkeypatch, cef_lines):
    monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
    monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "siem")
    emit(cef_lines, "USR002", current_user={"username": "u"}, extra={"duser": "x"})  # severity 7
    payload = _FakeUdpSocket.instances[0].sent[0][0].decode()
    assert payload.startswith("<164>1 ")  # 20*8 + 4


# ---------------- безопасность журнала: инъекции ----------------

_EVIL = "evil\nCEF:0|FAKE|FAKE|0|SEC002|x|3| outcome=success suser=admin"


def test_cef_line_is_single_line_and_cannot_forge_keys(cef_lines):
    _, ext = emit(
        cef_lines, "SEC003", current_user={"username": _EVIL}, status_code=401,
        extra={"reason": _EVIL, "cs1": _EVIL},
    )
    line = cef_lines.messages[-1]
    assert "\n" not in line
    # подделать ключ через '=' нельзя: все '=' из значений экранированы
    assert len(re.findall(r"(?<!\\)(?<![A-Za-z0-9])outcome=", line)) == 1
    assert ext["outcome"] == "failure"
    assert ext["suser"].startswith("evil")


def test_cef_value_with_backslash_cannot_unescape_next_char(cef_lines):
    _, ext = emit(cef_lines, "SEC003", current_user={"username": "x\\"}, status_code=401,
                  extra={"reason": "r"})
    line = cef_lines.messages[-1]
    assert "suser=x\\\\ " in line  # одиночный '\' удваивается и не «съедает» пробел-разделитель


def test_cef_line_has_no_carriage_return(cef_lines):
    emit(cef_lines, "SEC003", current_user={"username": "a\rb"}, status_code=401, extra={"reason": "x\ry"})
    assert "\r" not in cef_lines.messages[-1]


def test_cef_line_carriage_return_is_escaped(cef_lines):
    emit(cef_lines, "SEC003", current_user={"username": "a\rb"}, status_code=401, extra={"reason": "r"})
    assert "a\\rb" in cef_lines.messages[-1]


def test_header_pipe_in_vendor_is_escaped(monkeypatch, cef_lines):
    monkeypatch.setenv("CEF_DEVICE_VENDOR", "A|B")
    cl.log_cef_event("CNV001", current_user={"username": "u"}, extra={"cs2": "c"})
    unescaped_pipes = re.findall(r"(?<!\\)\|", cef_lines.messages[-1].split(" externalId=")[0])
    assert len(unescaped_pipes) == 7


def test_header_pipe_in_vendor_is_kept_in_one_field(monkeypatch, cef_lines):
    monkeypatch.setenv("CEF_DEVICE_VENDOR", "A|B")
    cl.log_cef_event("CNV001", current_user={"username": "u"}, extra={"cs2": "c"})
    head = cef_lines.messages[-1].split(" externalId=")[0]
    assert head.startswith("CEF:0|A\\|B|AstraChat|")


def test_pipe_in_extension_value_is_kept(cef_lines):
    _, ext = emit(cef_lines, "CNV001", current_user={"username": "a|b"}, extra={"cs2": "c"})
    assert ext["suser"] == "a|b"  # в расширении '|' допустим по спецификации CEF
