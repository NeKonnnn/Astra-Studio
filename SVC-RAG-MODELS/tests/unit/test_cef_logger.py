"""app/core/cef_logger: форматирование CEF, экранирование, контекст аудита, storage_audit, syslog."""

import logging
import re
import socket
import sys
from types import SimpleNamespace

import pytest

from app.core.cef_logger import cef_audit_context as ctx
from app.core.cef_logger import cef_logger as cl
from app.core.cef_logger import storage_audit as sa
from tests.cef_helpers import make_request, parse_cef

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- configure
class TestConfigureCefLogging:
    @pytest.fixture
    def pristine_logger(self, monkeypatch):
        lg = logging.getLogger("cef")
        monkeypatch.setattr(lg, "handlers", [])
        monkeypatch.setattr(lg, "level", logging.NOTSET)
        monkeypatch.setattr(lg, "propagate", True)
        return lg

    def test_noop_when_already_configured(self, monkeypatch, pristine_logger):
        monkeypatch.setattr(cl, "_cef_logging_configured", True)
        cl.configure_cef_logging()
        assert pristine_logger.handlers == []
        assert pristine_logger.propagate is True

    def test_configures_stdout_raw_message_handler(self, monkeypatch, pristine_logger):
        monkeypatch.setattr(cl, "_cef_logging_configured", False)
        cl.configure_cef_logging()
        assert pristine_logger.level == logging.INFO
        assert pristine_logger.propagate is False
        (handler,) = pristine_logger.handlers
        assert handler.stream is sys.stdout
        rec = logging.LogRecord("cef", logging.INFO, "f", 1, "CEF:0|raw", None, None)
        assert handler.format(rec) == "CEF:0|raw"  # без префикса времени/уровня
        assert cl._cef_logging_configured is True

    def test_existing_handler_is_not_duplicated(self, monkeypatch, pristine_logger):
        existing = logging.NullHandler()
        pristine_logger.handlers.append(existing)
        monkeypatch.setattr(cl, "_cef_logging_configured", False)
        cl.configure_cef_logging()
        assert pristine_logger.handlers == [existing]
        assert pristine_logger.propagate is False


# --------------------------------------------------------------------------- env helpers
class TestSeverityAndEnvResolvers:
    @pytest.mark.parametrize("cef,pri", [(0, 6), (3, 6), (4, 5), (5, 5), (6, 4), (7, 4), (8, 3), (10, 3)])
    def test_cef_severity_to_syslog(self, cef, pri):
        assert cl._cef_severity_to_syslog_pri_severity(cef) == pri

    def test_device_version(self, monkeypatch):
        assert cl.resolve_cef_device_version() == "0.0.0"
        monkeypatch.setenv("CEF_DEVICE_VERSION", "  1.2.3 ")
        assert cl.resolve_cef_device_version() == "1.2.3"

    def test_dvchost_priority(self, monkeypatch):
        monkeypatch.setenv("HOSTNAME", "h")
        assert cl.resolve_cef_dvchost() == "h"
        monkeypatch.setenv("POD_NAME", "pod")
        assert cl.resolve_cef_dvchost() == "pod"
        monkeypatch.setenv("MY_POD_NAME", "mypod")
        assert cl.resolve_cef_dvchost() == "mypod"
        monkeypatch.setenv("CEF_DVCHOST", "explicit")
        assert cl.resolve_cef_dvchost() == "explicit"

    def test_dvchost_falls_back_to_socket_names(self, monkeypatch):
        monkeypatch.setattr(cl.socket, "getfqdn", lambda: "fq.example")
        assert cl.resolve_cef_dvchost() == "fq.example"
        monkeypatch.setattr(cl.socket, "getfqdn", lambda: "")
        monkeypatch.setattr(cl.socket, "gethostname", lambda: "plain-host")
        assert cl.resolve_cef_dvchost() == "plain-host"
        monkeypatch.setattr(cl.socket, "gethostname", lambda: "")
        assert cl.resolve_cef_dvchost() == "-"


# --------------------------------------------------------------------------- syslog
class _FakeSock:
    instances = []

    def __init__(self, family, kind):
        self.family, self.kind = family, kind
        self.sent = []
        self.fail = None
        _FakeSock.instances.append(self)

    def sendto(self, payload, addr):
        if self.fail:
            raise self.fail
        self.sent.append((payload, addr))


@pytest.fixture
def fake_udp(monkeypatch):
    _FakeSock.instances = []
    monkeypatch.setattr(cl, "_SYSLOG_UDP_SOCK", None)
    monkeypatch.setattr(cl.socket, "socket", _FakeSock)
    return _FakeSock


class TestSyslog:
    def test_payload_rfc5424_format(self, monkeypatch):
        monkeypatch.setenv("CEF_DVCHOST", "node1")
        monkeypatch.setenv("CEF_DEVICE_PROCESS", "proc")
        raw = cl._build_rfc5424_syslog_payload("CEF:0|x", 4)
        text = raw.decode("utf-8")
        # LOCAL4=20 -> 20*8 + syslog severity 5
        assert re.fullmatch(r"<165>1 \d{4}-\d\d-\d\dT[\d:.]+Z node1 proc \d+ - - CEF:0\|x", text), text

    def test_facility_from_env_and_unknown_fallback(self, monkeypatch):
        monkeypatch.setenv("AUDIT_SYSLOG_FACILITY", "auth")
        assert cl._build_rfc5424_syslog_payload("c", 3).startswith(b"<38>1 ")  # 4*8+6
        monkeypatch.setenv("AUDIT_SYSLOG_FACILITY", "bogus")
        assert cl._build_rfc5424_syslog_payload("c", 9).startswith(b"<163>1 ")  # 20*8+3

    def test_disabled_without_flag(self, fake_udp, monkeypatch):
        monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "10.1.1.1")
        cl._emit_cef_syslog_udp("CEF:0|x", 3)
        assert fake_udp.instances == []

    def test_disabled_without_target(self, fake_udp, monkeypatch):
        monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
        cl._emit_cef_syslog_udp("CEF:0|x", 3)
        assert fake_udp.instances == []

    def test_sends_datagram_and_reuses_socket(self, fake_udp, monkeypatch):
        monkeypatch.setenv("AUDIT_CEF_ENABLED", "TRUE")
        monkeypatch.setenv("AUDIT_SYSLOG_TARGET", " 10.1.1.1 ")
        monkeypatch.setenv("AUDIT_SYSLOG_PORT", "5514")
        cl._emit_cef_syslog_udp("CEF:0|one", 3)
        cl._emit_cef_syslog_udp("CEF:0|two", 3)
        assert len(fake_udp.instances) == 1
        sock = fake_udp.instances[0]
        assert (sock.family, sock.kind) == (socket.AF_INET, socket.SOCK_DGRAM)
        assert [addr for _, addr in sock.sent] == [("10.1.1.1", 5514)] * 2
        assert sock.sent[0][0].endswith(b"CEF:0|one")

    def test_bad_port_falls_back_to_514(self, fake_udp, monkeypatch):
        monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
        monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "h")
        monkeypatch.setenv("AUDIT_SYSLOG_PORT", "abc")
        cl._emit_cef_syslog_udp("c", 3)
        assert fake_udp.instances[0].sent[0][1] == ("h", 514)

    def test_send_error_is_logged_not_raised(self, fake_udp, monkeypatch, cef_lines):
        monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
        monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "h")
        cl._emit_cef_syslog_udp("c", 3)
        fake_udp.instances[0].fail = OSError("network down")
        cl._emit_cef_syslog_udp("c2", 3)
        assert any("syslog UDP send to h:514 failed: network down" in line for line in cef_lines)


# --------------------------------------------------------------------------- small helpers
class TestSmallHelpers:
    def test_api_key_extra(self):
        assert cl.api_key_audit_cef_extra("svc-rag") == {
            "cs1": "svc-rag", "cs1Label": "CallerService",
            "cs2": "api_key", "cs2Label": "AuthMethod",
            "cs3": "-", "cs3Label": "MatchedADGroup",
            "cs4": "SERVICE", "cs4Label": "RoleModel",
        }
        assert cl.api_key_audit_cef_extra("")["cs1"] == "-"

    @pytest.mark.parametrize(
        "extra,user,expected",
        [
            (None, None, "ldap"),
            ({"auth_method": " API_KEY "}, None, "api_key"),
            (None, {"auth_method": "Kerberos"}, "kerberos"),
            ({"auth_method": "api_key"}, {"auth_method": "ldap"}, "api_key"),
            ({"auth_method": "  "}, None, "ldap"),
        ],
    )
    def test_resolve_auth_method(self, extra, user, expected):
        assert cl._resolve_auth_method(extra=extra, current_user=user) == expected

    def test_login_template_only_swapped_for_sec001_api_key(self):
        assert cl._login_audit_msg_template("SEC001", "api_key", "dflt") == cl._SEC001_API_KEY_MSG
        assert cl._login_audit_msg_template("SEC001", "ldap", "dflt") == "dflt"
        assert cl._login_audit_msg_template("SEC003", "api_key", "dflt") == "dflt"

    def test_safe_dict_missing_key_is_dash(self):
        assert "{a}-{b}".format_map(cl._SafeDict(a=1)) == "1--"

    @pytest.mark.parametrize(
        "code,expected",
        [(None, "success"), (200, "success"), (299, "success"), (302, "success"),
         (199, "success"), (400, "failure"), (404, "failure"), (499, "failure"),
         (500, "error"), (503, "error")],
    )
    def test_outcome_from_status(self, code, expected):
        assert cl._outcome_from_status(code) == expected

    @pytest.mark.parametrize(
        "dn,expected",
        [("DC=corp,DC=example,DC=com", "corp.example.com"), ("ou=u, dc=a , dc=b", "a.b"),
         ("ou=users", ""), ("", ""), (None, "")],
    )
    def test_domain_from_ldap_base_dn(self, dn, expected):
        assert cl.domain_from_ldap_base_dn(dn) == expected


class TestEscaping:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("plain text", "plain text"),
            ("a=b", r"a\=b"),
            ("back\\slash", "back\\\\slash"),
            ("line1\nline2", r"line1\nline2"),
            (None, "-"),
            (0, "0"),
            ("a\\=b", "a\\\\\\=b"),
        ],
    )
    def test_esc(self, raw, expected):
        assert cl._esc(raw) == expected

    def test_carriage_return_is_escaped(self):
        assert "\r" not in cl._esc("a\rCEF:0|fake")

    def test_extension_injection_cannot_forge_new_keys(self, cef_lines):
        cl.log_cef_event(
            "OBJ001",
            current_user={"username": "evil act=forged outcome=failure\nCEF:0|x|y|1|SEC001|n|0|"},
            extra={"methodName": "m", "serviceName": "s", "requestUuid": "u"},
        )
        (line,) = cef_lines
        assert "\n" not in line and "\r" not in line
        _, ext = parse_cef(line)
        assert ext["act"] == "request"          # не подменён
        assert ext["outcome"] == "success"
        assert ext["suser"].startswith(r"evil act\=forged outcome\=failure\n")


# --------------------------------------------------------------------------- request_context
class TestRequestContext:
    def test_none_request_and_user(self):
        assert cl.request_context(None, None) == {
            "src": "127.0.0.1", "shost": "127.0.0.1", "spt": 0,
            "app": "HTTPS", "suser": "SYSTEM", "sntdom": "-",
        }

    def test_user_fields_and_ldap_domain(self, monkeypatch):
        monkeypatch.setenv("LDAP_USER_SEARCH_BASE", "DC=corp,DC=local")
        got = cl.request_context(None, {"username": "ivanov", "sntdom": "OTHER"})
        assert got["suser"] == "ivanov" and got["sntdom"] == "OTHER"
        got = cl.request_context(None, {"user_id": 7})
        assert got["suser"] == "7" and got["sntdom"] == "corp.local"
        assert cl.request_context(None, {"username": "", "user_id": None})["suser"] == "SYSTEM"

    def test_real_request_gives_src_port_and_scheme(self):
        req = make_request(headers={"x-real-ip": "203.0.113.9"}, scheme="http")
        got = cl.request_context(req, None)
        assert (got["src"], got["shost"], got["spt"], got["app"]) == ("203.0.113.9", "203.0.113.9", 4321, "HTTP")

    def test_forwarded_proto_wins_over_scheme(self):
        req = make_request(headers={"X-Forwarded-Proto": " HTTPS "}, scheme="http")
        assert cl.request_context(req, None)["app"] == "HTTPS"

    def test_unknown_forwarded_proto_falls_back_to_scheme(self):
        req = make_request(headers={"x-forwarded-proto": "ftp"}, scheme="http")
        assert cl.request_context(req, None)["app"] == "HTTP"

    def test_unknown_scheme_keeps_default_https(self):
        assert cl.request_context(make_request(scheme="ws"), None)["app"] == "HTTPS"

    def test_request_without_client(self):
        got = cl.request_context(make_request(client=None), None)
        assert got["spt"] == 0 and got["src"] == "127.0.0.1"

    def test_non_numeric_port_gives_zero(self):
        fake = SimpleNamespace(headers={}, client=SimpleNamespace(host="h", port="abc"), url=None)
        got = cl.request_context(fake, None)
        assert got["spt"] == 0 and got["src"] == "h"

    def test_request_without_url_attribute(self):
        fake = SimpleNamespace(headers={}, client=None, url=None)
        assert cl.request_context(fake, None)["app"] == "HTTPS"


# --------------------------------------------------------------------------- outbound helpers
class TestOutbound:
    def test_explicit_port_and_model_provider(self):
        extra = cl.cef_outbound_extra(
            base_url="http://rag:8010/", method_name="/v1/embeddings", service_name="svc",
            request_uuid="u1", model="FRIDA", provider="svc-rag-models",
        )
        assert extra == {
            "_deviceDirection": 1, "dhost": "rag", "dpt": 8010,
            "methodName": "/v1/embeddings", "serviceName": "svc", "requestUuid": "u1",
            "request": "http://rag:8010/v1/embeddings",
            "cs1": "FRIDA", "cs1Label": "LLMModel", "cs3": "svc-rag-models", "cs3Label": "LLMProvider",
        }

    def test_default_ports_by_scheme(self):
        https = cl.cef_outbound_extra(base_url="HTTPS://h", method_name="m", service_name="s", request_uuid="u")
        http = cl.cef_outbound_extra(base_url="http://h", method_name="m", service_name="s", request_uuid="u")
        bare = cl.cef_outbound_extra(base_url="no-scheme", method_name="m", service_name="s", request_uuid="u")
        assert https["dpt"] == 443 and http["dpt"] == 80
        assert bare["dpt"] == 80 and bare["dhost"] == "no-scheme"
        assert "cs1" not in http and "cs3" not in http

    def test_int003_event_is_logged_with_outbound_direction(self, cef_lines):
        cl.log_cef_int003_model_request(base_url="https://llm.example:9443", model="FRIDA", provider="")
        header, ext = parse_cef(cef_lines[0])
        assert header["event_id"] == "INT003"
        assert ext["deviceDirection"] == "1"
        assert ext["dhost"] == "llm.example" and ext["dpt"] == "9443"
        assert ext["serviceName"] == "rag-models"       # провайдер пуст -> имя по умолчанию
        assert ext["methodName"] == "POST /v1/embeddings"
        assert ext["cs1"] == "FRIDA"
        assert "Пользователь SYSTEM" in ext["msg"] and "модели FRIDA" in ext["msg"]
        assert "provider" not in cef_lines[0].lower().replace("llmprovider", "")

    def test_int006_failure_with_status(self, cef_lines):
        cl.log_cef_int006_outbound_failure(
            base_url="http://h", model="m", provider="p", method_name="POST /x",
            status_code=502, text_status="x" * 600,
        )
        header, ext = parse_cef(cef_lines[0])
        assert header["event_id"] == "INT006"
        assert ext["codeStatus"] == "502"
        assert ext["textStatus"] == "x" * 512
        assert ext["outcome"] == "error"
        assert "Ошибка '502 " in ext["msg"]

    def test_int006_exception_without_status(self, cef_lines):
        cl.log_cef_int006_outbound_failure(
            base_url="http://h", model="m", provider="", method_name="POST /x",
            status_code=None, text_status="",
        )
        _, ext = parse_cef(cef_lines[0])
        assert ext["codeStatus"] == "EXCEPTION"
        assert ext["serviceName"] == "rag-models"

    def test_failure_event_without_status_has_failure_outcome(self, cef_lines):
        cl.log_cef_int006_outbound_failure(
            base_url="http://h", model="m", provider="p", method_name="POST /x",
            status_code=None, text_status="timeout",
        )
        assert parse_cef(cef_lines[0])[1]["outcome"] == "failure"


# --------------------------------------------------------------------------- extension ordering
class TestExtensionOrderedItems:
    def test_known_keys_first_in_spec_order_then_sorted_rest(self):
        items = cl._extension_ordered_items(
            {"zzz": 1, "aaa": 2, "msg": "m", "externalId": "e", "_private": 3, "cat": None, "suser": "u"}
        )
        assert [k for k, _ in items] == ["externalId", "suser", "msg", "aaa", "zzz"]

    def test_none_values_skipped_in_both_passes(self):
        assert cl._extension_ordered_items({"cat": None, "zzz": None, "src": "s"}) == [("src", "s")]


# --------------------------------------------------------------------------- log_cef_event
class TestLogCefEvent:
    def test_unknown_event_only_warns(self, cef_lines):
        cl.log_cef_event("NOPE999")
        assert cef_lines == ["CEF event id 'NOPE999' is not configured"]

    def test_system_event_uses_system_identity(self, cef_lines, monkeypatch):
        monkeypatch.setenv("CEF_DEVICE_VERSION", "9.9")
        monkeypatch.setenv("DOMAIN_SERVER", "rag.example")
        cl.log_cef_event(
            "SYS001", request=make_request(headers={"x-real-ip": "1.2.3.4"}),
            current_user={"username": "mallory", "user_id": 5},
        )
        header, ext = parse_cef(cef_lines[0])
        assert header == {"vendor": "CORSUR", "product": "SVC-RAG-MODELS", "version": "9.9",
                          "event_id": "SYS001", "name": "Application Started", "severity": "3"}
        assert (ext["src"], ext["shost"], ext["suser"], ext["spt"]) == ("127.0.0.1", "localhost", "SYSTEM", "0")
        assert "suid" not in ext
        assert ext["dhost"] == "rag.example" and ext["dpt"] == "443"
        assert ext["cat"] == "/System/Startup" and ext["act"] == "start" and ext["outcome"] == "success"
        assert ext["msg"] == "Сервис SVC-RAG-MODELS стартовал успешно, API доступен [/System/Startup]"
        assert ext["deviceDirection"] == "0" and ext["deviceProcessName"] == "svc-rag-models"
        assert re.fullmatch(r"[0-9a-f]{32}", ext["externalId"])
        assert ext["start"] == ext["end"] and ext["start"].isdigit()
        assert ext["rt"].endswith("Z")

    def test_sys_event_ldap_domain(self, cef_lines, monkeypatch):
        monkeypatch.setenv("LDAP_USER_SEARCH_BASE", "DC=corp,DC=local")
        cl.log_cef_event("SYS002")
        assert parse_cef(cef_lines[0])[1]["sntdom"] == "corp.local"

    def test_request_event_takes_identity_from_request_and_user(self, cef_lines):
        req = make_request(headers={"x-forwarded-for": "198.51.100.7, 10.0.0.1"})
        cl.log_cef_event(
            "OBJ001", request=req, current_user={"username": "ivanov", "user_id": 42, "sntdom": "CORP"},
            status_code=200, extra={"methodName": "GET /v1/models", "serviceName": "SVC", "requestUuid": "r1"},
        )
        header, ext = parse_cef(cef_lines[0])
        assert header["severity"] == "0"
        assert (ext["src"], ext["suser"], ext["suid"], ext["sntdom"]) == ("198.51.100.7", "ivanov", "42", "CORP")
        assert ext["spt"] == "4321" and ext["app"] == "HTTP"
        assert ext["msg"] == "Обработка запроса 'GET /v1/models' сервиса SVC выполнена успешно (r1)"
        assert ext["methodName"] == "GET /v1/models" and ext["requestUuid"] == "r1"

    def test_user_without_id_has_no_suid(self, cef_lines):
        cl.log_cef_event("OBJ001", current_user={"username": "u"}, extra={"methodName": "m"})
        assert "suid" not in parse_cef(cef_lines[0])[1]

    def test_missing_template_fields_render_as_dash(self, cef_lines):
        cl.log_cef_event("OBJ002", status_code=500)
        msg = parse_cef(cef_lines[0])[1]["msg"]
        assert msg == "Ошибка '- -' обработки запроса '-' сервиса - (-)"

    @pytest.mark.parametrize("status,outcome", [(200, "success"), (404, "failure"), (503, "error")])
    def test_outcome_from_status(self, cef_lines, status, outcome):
        cl.log_cef_event("OBJ002", status_code=status)
        assert parse_cef(cef_lines[0])[1]["outcome"] == outcome

    def test_explicit_outcome_wins(self, cef_lines):
        cl.log_cef_event("OBJ001", status_code=500, outcome="custom")
        assert parse_cef(cef_lines[0])[1]["outcome"] == "custom"

    def test_context_vars_fill_missing_request_and_user(self, cef_lines):
        req = make_request(headers={"x-real-ip": "192.0.2.55"})
        tokens = ctx.cef_audit_set(request=req, user={"username": "ctx-user", "user_id": "u9"})
        try:
            cl.log_cef_event("OBJ001")
        finally:
            ctx.cef_audit_reset(tokens)
        _, ext = parse_cef(cef_lines[0])
        assert (ext["src"], ext["suser"], ext["suid"]) == ("192.0.2.55", "ctx-user", "u9")

    def test_explicit_arguments_beat_context(self, cef_lines):
        tokens = ctx.cef_audit_set(request=make_request(headers={"x-real-ip": "1.1.1.1"}), user={"username": "ctx"})
        try:
            cl.log_cef_event(
                "OBJ001", request=make_request(headers={"x-real-ip": "2.2.2.2"}), current_user={"username": "explicit"}
            )
        finally:
            ctx.cef_audit_reset(tokens)
        _, ext = parse_cef(cef_lines[0])
        assert (ext["src"], ext["suser"]) == ("2.2.2.2", "explicit")

    def test_broken_context_is_ignored(self, cef_lines, monkeypatch):
        def boom():
            raise RuntimeError("context lost")

        monkeypatch.setattr(ctx, "cef_audit_peek", boom)
        cl.log_cef_event("OBJ001")
        assert parse_cef(cef_lines[0])[1]["suser"] == "SYSTEM"

    def test_outbound_direction_takes_destination_from_extra(self, cef_lines):
        cl.log_cef_event(
            "INT005", status_code=200,
            extra={"_deviceDirection": 1, "dhost": "db.local", "dpt": "5433", "dst": "10.9.9.9",
                   "duser": "app", "dntdom": "CORP", "methodName": "SELECT", "serviceName": "pg",
                   "requestUuid": "q1", "ignored": None},
        )
        _, ext = parse_cef(cef_lines[0])
        assert ext["deviceDirection"] == "1"
        assert (ext["dhost"], ext["dpt"], ext["dst"], ext["duser"], ext["dntdom"]) == (
            "db.local", "5433", "10.9.9.9", "app", "CORP")
        assert "ignored" not in ext and "_deviceDirection" not in ext

    def test_outbound_without_dpt_keeps_default_port(self, cef_lines):
        cl.log_cef_event("INT005", extra={"_deviceDirection": 1, "dhost": "db.local"})
        _, ext = parse_cef(cef_lines[0])
        assert (ext["dhost"], ext["dpt"]) == ("db.local", "443")

    def test_inbound_direction_keeps_own_host(self, cef_lines, monkeypatch):
        monkeypatch.setenv("DOMAIN_SERVER", "me.example")
        cl.log_cef_event("OBJ001", extra={"dst": "should-not-be-used"})
        _, ext = parse_cef(cef_lines[0])
        assert ext["dhost"] == "me.example"
        assert ext["dst"] == "should-not-be-used"  # попадает через общий extra.update

    def test_sec001_api_key_message_template(self, cef_lines):
        cl.log_cef_event(
            "SEC001", current_user={"username": "caller-svc", "auth_method": "api_key"}, status_code=200,
            extra={"auth_method": "api_key", **cl.api_key_audit_cef_extra("backend")},
        )
        msg = parse_cef(cef_lines[0])[1]["msg"]
        assert msg.startswith("Сервис backend аутентифицирован с адреса 127.0.0.1 по API-ключу (инициатор caller-svc)")

    def test_sec001_ldap_message_template(self, cef_lines):
        cl.log_cef_event("SEC001", current_user={"username": "u"}, extra={"cs1": "ldap1", "cs2": "tuz", "cs3": "g", "cs4": "R"})
        msg = parse_cef(cef_lines[0])[1]["msg"]
        assert "через LDAP-сервер ldap1, ТУЗ tuz. Ролевая модель: R (g)" in msg

    def test_values_are_escaped_in_final_line(self, cef_lines):
        cl.log_cef_event("OBJ001", extra={"methodName": "a=b\\c", "requestUuid": "x"})
        line = cef_lines[0]
        assert "methodName=a\\=b\\\\c" in line

    def test_line_is_forwarded_to_syslog(self, cef_lines, fake_udp, monkeypatch):
        monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
        monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "syslog.local")
        cl.log_cef_event("OBJ002", status_code=500)
        (payload, addr), = fake_udp.instances[0].sent
        assert addr == ("syslog.local", 514)
        assert payload.decode("utf-8").endswith(cef_lines[0])
        assert payload.startswith(b"<165>1 ")  # LOCAL4*8 + syslog-severity 5 (CEF severity 4)


# --------------------------------------------------------------------------- audit context
class TestAuditContext:
    def test_set_peek_reset(self):
        assert ctx.cef_audit_peek() == (None, None)
        tokens = ctx.cef_audit_set(request="req", user={"username": "u"})
        assert ctx.cef_audit_peek() == ("req", {"username": "u"})
        ctx.cef_audit_reset(tokens)
        assert ctx.cef_audit_peek() == (None, None)

    def test_nested_reset_restores_outer(self):
        outer = ctx.cef_audit_set(request="outer", user=None)
        inner = ctx.cef_audit_set(request="inner", user={"username": "x"})
        ctx.cef_audit_reset(inner)
        assert ctx.cef_audit_peek() == ("outer", None)
        ctx.cef_audit_reset(outer)

    def test_caller_service_header(self):
        assert ctx.caller_service_from_headers(None) is None
        assert ctx.caller_service_from_headers(SimpleNamespace()) is None
        assert ctx.caller_service_from_headers(SimpleNamespace(headers=None)) is None
        assert ctx.caller_service_from_headers(make_request()) is None
        assert ctx.caller_service_from_headers(make_request({"X-CEF-Caller-Service": " SVC-RAG "})) == "SVC-RAG"
        assert ctx.caller_service_from_headers(make_request({"x-cef-caller-service": "  "})) is None

    def test_user_from_headers(self):
        assert ctx.user_from_request_headers(None) is None
        assert ctx.user_from_request_headers(SimpleNamespace(headers=None)) is None
        assert ctx.user_from_request_headers(make_request()) is None
        assert ctx.user_from_request_headers(make_request({"x-cef-sntdom": "CORP"})) is None
        full = ctx.user_from_request_headers(
            make_request({"X-CEF-Suser": " ivanov ", "x-cef-suid": "17", "x-cef-sntdom": "CORP"}))
        assert full == {"username": "ivanov", "user_id": "17", "sntdom": "CORP"}
        assert ctx.user_from_request_headers(make_request({"x-cef-suser": "only"})) == {
            "username": "only", "user_id": None, "sntdom": None}
        assert ctx.user_from_request_headers(make_request({"x-cef-suid": "9"})) == {
            "username": "9", "user_id": "9", "sntdom": None}

    def test_resolve_client_src(self):
        assert ctx.resolve_client_src(None) == "127.0.0.1"
        assert ctx.resolve_client_src(make_request({"x-real-ip": "1.1.1.1", "x-forwarded-for": "2.2.2.2"})) == "1.1.1.1"
        assert ctx.resolve_client_src(make_request({"x-forwarded-for": "3.3.3.3, 4.4.4.4"})) == "3.3.3.3"
        assert ctx.resolve_client_src(make_request({"x-forwarded-for": " , 5.5.5.5"})) == "127.0.0.1"
        assert ctx.resolve_client_src(make_request()) == "10.0.0.5"
        assert ctx.resolve_client_src(make_request(client=None)) == "127.0.0.1"
        assert ctx.resolve_client_src(SimpleNamespace(headers=None, client=None)) == "127.0.0.1"
        assert ctx.resolve_client_src(SimpleNamespace(headers={}, client=SimpleNamespace(host=""))) == "127.0.0.1"
        assert ctx.resolve_client_src(SimpleNamespace(headers={"X-Real-Ip": "6.6.6.6"}, client=None)) == "6.6.6.6"

    def test_untrusted_peer_headers_are_ignored_by_default(self, monkeypatch):
        """BUG-RM-06: без RAG_MODELS_TRUSTED_PROXIES заголовки клиента не влияют на аудит."""
        monkeypatch.delenv("RAG_MODELS_TRUSTED_PROXIES", raising=False)
        monkeypatch.delenv("RAG_MODELS_ALLOW_INSECURE_NO_AUTH", raising=False)
        req = make_request(
            {"x-real-ip": "8.8.8.8", "x-forwarded-for": "9.9.9.9", "x-cef-suser": "evil",
             "x-cef-suid": "1", "x-cef-caller-service": "SVC-FAKE"},
            client=("10.0.0.99", 1),
        )
        assert ctx.resolve_client_src(req) == "10.0.0.99"
        assert ctx.user_from_request_headers(req) is None
        assert ctx.caller_service_from_headers(req) is None

    @pytest.mark.parametrize(
        "setting,peer,trusted",
        [
            ("10.0.0.99", "10.0.0.99", True),
            ("10.0.0.0/24, 192.168.1.1", "10.0.0.99", True),
            ("192.168.1.1,10.0.0.0/24", "10.0.0.7", True),
            ("10.0.0.0/24", "10.0.1.7", False),
            ("*", "203.0.113.5", True),
            ("not-an-ip, 10.0.0.0/24", "10.0.0.8", True),
            ("not-an-ip", "10.0.0.8", False),
            ("  ", "10.0.0.8", False),
        ],
    )
    def test_trusted_proxies_setting(self, monkeypatch, setting, peer, trusted):
        monkeypatch.delenv("RAG_MODELS_ALLOW_INSECURE_NO_AUTH", raising=False)
        monkeypatch.setenv("RAG_MODELS_TRUSTED_PROXIES", setting)
        req = make_request({"x-real-ip": "8.8.8.8", "x-cef-suser": "ivanov"}, client=(peer, 1))
        assert ctx.resolve_client_src(req) == ("8.8.8.8" if trusted else peer)
        assert (ctx.user_from_request_headers(req) is not None) is trusted

    def test_valid_api_key_mark_trusts_headers_rejected_mark_never(self, monkeypatch):
        monkeypatch.delenv("RAG_MODELS_ALLOW_INSECURE_NO_AUTH", raising=False)
        monkeypatch.delenv("RAG_MODELS_TRUSTED_PROXIES", raising=False)
        req = make_request({"x-real-ip": "8.8.8.8", "x-cef-suser": "ivanov"}, client=("10.0.0.99", 1))
        assert ctx.resolve_client_src(req) == "10.0.0.99"
        req.state.api_key_valid = True
        assert ctx.resolve_client_src(req) == "8.8.8.8"
        assert ctx.user_from_request_headers(req)["username"] == "ivanov"
        monkeypatch.setenv("RAG_MODELS_TRUSTED_PROXIES", "*")
        req.state.api_key_rejected = True  # SEC003: даже доверенному пиру не верим
        assert ctx.resolve_client_src(req) == "10.0.0.99"
        assert ctx.user_from_request_headers(req) is None

    def test_trusted_list_without_peer_or_with_non_ip_peer_is_untrusted(self, monkeypatch):
        monkeypatch.delenv("RAG_MODELS_ALLOW_INSECURE_NO_AUTH", raising=False)
        monkeypatch.setenv("RAG_MODELS_TRUSTED_PROXIES", "10.0.0.0/24")
        assert ctx.resolve_client_src(make_request({"x-real-ip": "8.8.8.8"}, client=None)) == "127.0.0.1"
        req = make_request({"x-real-ip": "8.8.8.8"}, client=("localhost", 1))
        assert ctx.resolve_client_src(req) == "localhost"


# --------------------------------------------------------------------------- storage_audit
class TestStorageAudit:
    DB = SimpleNamespace(host="pg.local", port=6432, user="astra")

    def test_extra_with_db_attributes(self):
        extra = sa._extra(method_name="SELECT 1", db=self.DB, request_uuid="rid")
        assert extra == {
            "_deviceDirection": 1, "dhost": "pg.local", "dpt": 6432, "duser": "astra",
            "methodName": "SELECT 1", "serviceName": "AstraChat-PostgreSQL", "requestUuid": "rid",
        }

    def test_extra_defaults_and_generated_uuid(self):
        extra = sa._extra(method_name="m", db=object())
        assert (extra["dhost"], extra["dpt"], extra["duser"]) == ("postgresql", 5432, "-")
        assert re.fullmatch(r"[0-9a-f]{32}", extra["requestUuid"])

    def test_success_event_emitted_as_int005(self, cef_lines):
        sa.log_postgres_success("INSERT kb", db=self.DB, request_uuid="r1")
        header, ext = parse_cef(cef_lines[0])
        assert header["event_id"] == "INT005"
        assert ext["outcome"] == "success" and ext["deviceDirection"] == "1"
        assert (ext["dhost"], ext["dpt"], ext["duser"]) == ("pg.local", "6432", "astra")
        assert ext["msg"] == "Обработка запроса 'INSERT kb' сервиса AstraChat-PostgreSQL выполнена успешно (r1)"

    def test_failure_event_emitted_as_int006_with_truncated_text(self, cef_lines):
        sa.log_postgres_failure("UPDATE kb", "e" * 900, db=self.DB, request_uuid="r2")
        header, ext = parse_cef(cef_lines[0])
        assert header["event_id"] == "INT006"
        assert ext["codeStatus"] == "EXCEPTION"
        assert ext["textStatus"] == "e" * 512
        assert ext["requestUuid"] == "r2"

    def test_db_failure_outcome_is_failure(self, cef_lines):
        sa.log_postgres_failure("UPDATE kb", "boom", db=self.DB)
        assert parse_cef(cef_lines[0])[1]["outcome"] == "failure"

    def test_emit_picks_up_audit_context(self, cef_lines):
        tokens = ctx.cef_audit_set(request=make_request({"x-real-ip": "7.7.7.7"}), user={"username": "ctx-user"})
        try:
            sa.log_postgres_success("SELECT", db=self.DB)
        finally:
            ctx.cef_audit_reset(tokens)
        _, ext = parse_cef(cef_lines[0])
        assert (ext["suser"], ext["src"]) == ("ctx-user", "7.7.7.7")

    def test_emit_never_raises(self, monkeypatch, caplog):
        def boom(*a, **k):
            raise RuntimeError("cef down")

        monkeypatch.setattr(cl, "log_cef_event", boom)
        with caplog.at_level(logging.DEBUG, logger=sa.logger.name):
            sa.log_postgres_success("SELECT", db=self.DB)  # не бросает
        assert "CEF postgres emit failed" in caplog.text
