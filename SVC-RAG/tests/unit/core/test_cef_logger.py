"""core.cef_logger: формирование CEF-строк, syslog UDP, контекст аудита, storage_audit и middleware OBJ001/OBJ002."""

from __future__ import annotations

import logging
import re
import socket
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.core import cef_logger as cef_pkg
from app.core.cef_logger import cef_audit_context as ctx_mod
from app.core.cef_logger import cef_audit_middleware as mw
from app.core.cef_logger import cef_logger as cef
from app.core.cef_logger import storage_audit

pytestmark = pytest.mark.unit

CEF_ENV = (
    "CEF_DEVICE_VERSION", "CEF_DVCHOST", "MY_POD_NAME", "POD_NAME", "HOSTNAME", "AUDIT_SYSLOG_FACILITY",
    "AUDIT_CEF_ENABLED", "AUDIT_SYSLOG_TARGET", "AUDIT_SYSLOG_PORT", "CEF_DEVICE_PROCESS", "LDAP_USER_SEARCH_BASE",
    "DOMAIN_SERVER", "CEF_DPT", "CEF_DEVICE_VENDOR", "CEF_DEVICE_PRODUCT", "CEF_AUDIT_POLLING",
    "CEF_AUDIT_POLLING_INTERVAL", "MODELS_SYSTEM_USER_ID",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in CEF_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DOMAIN_SERVER", "rag.example.local")
    monkeypatch.setenv("CEF_DVCHOST", "pod-1")


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelno, record.getMessage()))


@pytest.fixture
def cef_lines():
    """Перехват логгера cef напрямую (его propagate может быть выключен configure_cef_logging)."""
    handler = ListHandler()
    old_level = cef.logger.level
    cef.logger.addHandler(handler)
    cef.logger.setLevel(logging.DEBUG)
    yield handler
    cef.logger.removeHandler(handler)
    cef.logger.setLevel(old_level)


def parse(line: str):
    """CEF-строка -> (заголовок из 7 полей, словарь расширения)."""
    parts = line.split("|", 7)
    ext = {}
    for m in re.finditer(r"(\w+)=((?:\\.|[^ ])*(?: (?!\w+=)(?:\\.|[^ ])*)*)", parts[7]):
        ext[m.group(1)] = m.group(2)
    return parts[:7], ext


def events(handler):
    return [msg for lvl, msg in handler.lines if msg.startswith("CEF:0|")]


def request_stub(headers=None, host="10.0.0.5", port=4444, scheme="https"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host, port=port),
                           url=SimpleNamespace(scheme=scheme))


# ───────────────────────────── cef_logger: мелкие функции ─────────────────────────────


class TestSmallHelpers:
    @pytest.mark.parametrize("sev,expected", [(0, 6), (3, 6), (4, 5), (5, 5), (6, 4), (7, 4), (8, 3), (10, 3)])
    def test_syslog_severity_mapping(self, sev, expected):
        assert cef._cef_severity_to_syslog_pri_severity(sev) == expected

    def test_device_version_default_and_env(self, monkeypatch):
        assert cef.resolve_cef_device_version() == "0.0.0"
        monkeypatch.setenv("CEF_DEVICE_VERSION", " 1.2.3 ")
        assert cef.resolve_cef_device_version() == "1.2.3"

    def test_dvchost_priority(self, monkeypatch):
        monkeypatch.delenv("CEF_DVCHOST")
        monkeypatch.setenv("HOSTNAME", "h")
        assert cef.resolve_cef_dvchost() == "h"
        monkeypatch.setenv("POD_NAME", "p")
        assert cef.resolve_cef_dvchost() == "p"
        monkeypatch.setenv("MY_POD_NAME", "m")
        assert cef.resolve_cef_dvchost() == "m"
        monkeypatch.setenv("CEF_DVCHOST", "explicit")
        assert cef.resolve_cef_dvchost() == "explicit"

    def test_dvchost_falls_back_to_fqdn_then_dash(self, monkeypatch):
        monkeypatch.delenv("CEF_DVCHOST")
        monkeypatch.setattr(cef.socket, "getfqdn", lambda: "fqdn.local")
        assert cef.resolve_cef_dvchost() == "fqdn.local"
        monkeypatch.setattr(cef.socket, "getfqdn", lambda: "")
        monkeypatch.setattr(cef.socket, "gethostname", lambda: "")
        assert cef.resolve_cef_dvchost() == "-"

    def test_escape(self):
        assert cef._esc(None) == "-"
        assert cef._esc("a=b\\c\nd") == "a\\=b\\\\c\\nd"
        assert cef._esc(5) == "5"

    def test_escape_does_not_neutralize_carriage_return_known_issue(self):
        # Низкая: \n экранируется, а \r - нет. Текст исключения (textStatus) попадает в CEF-строку,
        # и \r в нём разрывает строку лога для сборщика, читающего stdout по CR.
        assert "\r" in cef._esc("evil\rCEF:0|fake")

    @pytest.mark.parametrize("code,expected", [
        (None, "success"), (200, "success"), (299, "success"), (302, "success"),
        (404, "failure"), (499, "failure"), (500, "error"), (503, "error"),
    ])
    def test_outcome_from_status(self, code, expected):
        assert cef._outcome_from_status(code) == expected

    def test_extension_items_skip_none_values_in_both_passes(self):
        items = cef._extension_ordered_items({"cat": None, "suser": "u", "zzz": None, "_private": "x", "aaa": "1"})

        assert items == [("suser", "u"), ("aaa", "1")]

    def test_configure_keeps_existing_handler(self, monkeypatch):
        target = logging.getLogger("cef")
        saved = (list(target.handlers), target.level, target.propagate)
        monkeypatch.setattr(cef, "_cef_logging_configured", False)
        existing = logging.NullHandler()
        target.handlers[:] = [existing]
        try:
            cef.configure_cef_logging()

            assert target.handlers == [existing] and target.propagate is False
        finally:
            target.handlers[:] = saved[0]
            target.setLevel(saved[1])
            target.propagate = saved[2]

    def test_domain_from_ldap_base_dn(self):
        assert cef.domain_from_ldap_base_dn("OU=Users, DC=corp , dc=example,DC=ru") == "corp.example.ru"
        assert cef.domain_from_ldap_base_dn("") == ""
        assert cef.domain_from_ldap_base_dn(None) == ""

    def test_configure_cef_logging_installs_single_raw_stdout_handler(self, monkeypatch):
        target = logging.getLogger("cef")
        saved = (list(target.handlers), target.level, target.propagate)
        monkeypatch.setattr(cef, "_cef_logging_configured", False)
        target.handlers.clear()
        try:
            cef.configure_cef_logging()
            cef.configure_cef_logging()

            assert len(target.handlers) == 1 and target.propagate is False and target.level == logging.INFO
            assert target.handlers[0].formatter._fmt == "%(message)s"
        finally:
            target.handlers[:] = saved[0]
            target.setLevel(saved[1])
            target.propagate = saved[2]


# ───────────────────────────── syslog ─────────────────────────────


class FakeSock:
    created = []

    def __init__(self, family, kind):
        self.family, self.kind, self.sent, self.error = family, kind, [], None
        FakeSock.created.append(self)

    def sendto(self, payload, addr):
        if self.error:
            raise self.error
        self.sent.append((payload, addr))


@pytest.fixture
def udp(monkeypatch):
    FakeSock.created = []
    monkeypatch.setattr(cef, "_SYSLOG_UDP_SOCK", None)
    monkeypatch.setattr(cef.socket, "socket", FakeSock)
    return FakeSock


class TestSyslog:
    def test_payload_format_rfc5424(self, monkeypatch):
        monkeypatch.setenv("AUDIT_SYSLOG_FACILITY", "local0")
        monkeypatch.setenv("CEF_DEVICE_PROCESS", "rag-x")

        payload = cef._build_rfc5424_syslog_payload("CEF:0|a", 4).decode()

        # LOCAL0 = 16, severity 4 -> syslog 5: PRI = 16*8+5
        assert re.fullmatch(r"<133>1 \d{4}-\d\d-\d\dT[\d:.]+Z pod-1 rag-x \d+ - - CEF:0\|a", payload)

    def test_unknown_facility_falls_back_to_local4(self, monkeypatch):
        monkeypatch.setenv("AUDIT_SYSLOG_FACILITY", "bogus")

        assert cef._build_rfc5424_syslog_payload("x", 0).startswith(b"<166>1 ")  # 20*8+6

    def test_disabled_without_flag_or_target(self, udp, monkeypatch):
        cef._emit_cef_syslog_udp("x", 3)
        monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
        cef._emit_cef_syslog_udp("x", 3)

        assert udp.created == []

    def test_sends_datagram_and_reuses_socket(self, udp, monkeypatch):
        monkeypatch.setenv("AUDIT_CEF_ENABLED", "TRUE")
        monkeypatch.setenv("AUDIT_SYSLOG_TARGET", " siem.local ")
        monkeypatch.setenv("AUDIT_SYSLOG_PORT", "1514")

        cef._emit_cef_syslog_udp("CEF:0|one", 3)
        cef._emit_cef_syslog_udp("CEF:0|two", 3)

        assert len(udp.created) == 1
        assert udp.created[0].family == socket.AF_INET and udp.created[0].kind == socket.SOCK_DGRAM
        assert [addr for _, addr in udp.created[0].sent] == [("siem.local", 1514)] * 2
        assert udp.created[0].sent[0][0].endswith(b"CEF:0|one")

    def test_bad_port_falls_back_to_514(self, udp, monkeypatch):
        monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
        monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "h")
        monkeypatch.setenv("AUDIT_SYSLOG_PORT", "abc")

        cef._emit_cef_syslog_udp("x", 3)

        assert udp.created[0].sent[0][1] == ("h", 514)

    def test_send_error_is_logged_not_raised(self, udp, monkeypatch, cef_lines):
        monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
        monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "h")
        cef._emit_cef_syslog_udp("x", 3)
        udp.created[0].error = OSError("сеть недоступна")

        cef._emit_cef_syslog_udp("y", 3)

        assert any(lvl == logging.WARNING and "send to h:514 failed" in msg for lvl, msg in cef_lines.lines)


# ───────────────────────────── контекст запроса ─────────────────────────────


class TestRequestContext:
    def test_system_defaults_without_request(self):
        c = cef.request_context(None, None)

        assert c == {"src": "127.0.0.1", "shost": "127.0.0.1", "spt": 0, "app": "HTTPS", "suser": "SYSTEM", "sntdom": "-"}

    def test_user_request_proto_and_domain(self, monkeypatch):
        monkeypatch.setenv("LDAP_USER_SEARCH_BASE", "DC=corp,DC=ru")
        req = request_stub({"x-real-ip": "1.1.1.1", "x-forwarded-proto": "HTTP"}, port=777)

        c = cef.request_context(req, {"username": "ivan", "sntdom": "CORP"})

        assert (c["src"], c["spt"], c["app"], c["suser"], c["sntdom"]) == ("1.1.1.1", 777, "HTTP", "ivan", "CORP")

    def test_user_id_fallback_and_scheme_and_domain_from_ldap(self, monkeypatch):
        monkeypatch.setenv("LDAP_USER_SEARCH_BASE", "DC=corp,DC=ru")
        req = request_stub({}, scheme="http")

        c = cef.request_context(req, {"user_id": "u-5"})

        assert c["suser"] == "u-5" and c["app"] == "HTTP" and c["sntdom"] == "corp.ru" and c["src"] == "10.0.0.5"

    def test_invalid_port_and_unknown_scheme_keep_defaults(self):
        req = request_stub({}, port="x", scheme="ws")

        c = cef.request_context(req, None)

        assert c["spt"] == 0 and c["app"] == "HTTPS"

    def test_request_without_client_or_url(self):
        req = SimpleNamespace(headers=None, client=None, url=None)

        assert cef.request_context(req, None)["app"] == "HTTPS"


class TestClientSrc:
    def test_priority_real_ip_then_forwarded_then_client(self):
        assert ctx_mod.resolve_client_src(None) == "127.0.0.1"
        assert ctx_mod.resolve_client_src(request_stub({"x-forwarded-for": "2.2.2.2, 3.3.3.3"})) == "2.2.2.2"
        assert ctx_mod.resolve_client_src(request_stub({"x-real-ip": "1.1.1.1", "x-forwarded-for": "2.2.2.2"})) == "1.1.1.1"
        assert ctx_mod.resolve_client_src(request_stub({})) == "10.0.0.5"

    def test_empty_forwarded_and_no_client_gives_localhost(self):
        assert ctx_mod.resolve_client_src(SimpleNamespace(headers={"x-forwarded-for": " , "}, client=None)) == "127.0.0.1"
        assert ctx_mod.resolve_client_src(SimpleNamespace(headers=None, client=SimpleNamespace(host=None))) == "127.0.0.1"


class TestOutboundExtra:
    def test_http_default_port_and_labels(self):
        extra = cef.cef_outbound_extra(base_url="http://models.local/", method_name="/v1/embeddings", service_name="s",
                                       request_uuid="r", model="m", provider="p")

        assert extra["dhost"] == "models.local" and extra["dpt"] == 80 and extra["_deviceDirection"] == 1
        assert extra["request"] == "http://models.local/v1/embeddings"
        assert (extra["cs1"], extra["cs1Label"], extra["cs3"], extra["cs3Label"]) == ("m", "LLMModel", "p", "LLMProvider")

    def test_https_and_explicit_port_without_model(self):
        https = cef.cef_outbound_extra(base_url="https://h", method_name="m", service_name="s", request_uuid="r")
        explicit = cef.cef_outbound_extra(base_url="http://h:8080", method_name="m", service_name="s", request_uuid="r")

        assert https["dpt"] == 443 and "cs1" not in https and "cs3" not in https
        assert explicit["dpt"] == 8080

    def test_host_without_scheme_keeps_raw_value(self):
        extra = cef.cef_outbound_extra(base_url="models", method_name="m", service_name="s", request_uuid="r")

        assert extra["dhost"] == "models" and extra["dpt"] == 80


# ───────────────────────────── log_cef_event ─────────────────────────────


class TestLogCefEvent:
    def test_unknown_event_warns_and_writes_nothing(self, cef_lines):
        cef.log_cef_event("NOPE")

        assert events(cef_lines) == []
        assert any(lvl == logging.WARNING and "'NOPE' is not configured" in msg for lvl, msg in cef_lines.lines)

    def test_obj001_header_and_extension(self, cef_lines, monkeypatch):
        monkeypatch.setenv("CEF_DEVICE_VERSION", "9.9")
        req = request_stub({"x-real-ip": "7.7.7.7"})

        cef.log_cef_event("OBJ001", request=req, current_user={"username": "ivan", "user_id": "42", "sntdom": "CORP"},
                          status_code=200, extra={"methodName": "GET /v1/x", "serviceName": "SVC-RAG", "requestUuid": "u1"})

        header, ext = parse(events(cef_lines)[0])
        assert header[:7] == ["CEF:0", "CORSUR", "SVC-RAG", "9.9", "OBJ001", "API Call Success", "0"]
        assert ext["src"] == "7.7.7.7" and ext["suser"] == "ivan" and ext["suid"] == "42" and ext["sntdom"] == "CORP"
        assert ext["outcome"] == "success" and ext["act"] == "request" and ext["deviceDirection"] == "0"
        assert ext["dvchost"] == "pod-1" and ext["dhost"] == "rag.example.local" and ext["dpt"] == "443"
        assert "GET /v1/x" in ext["msg"] or "GET /v1/x" in events(cef_lines)[0]
        assert ext["externalId"] and ext["start"] == ext["end"]

    def test_context_from_audit_peek_when_request_not_given(self, cef_lines):
        tokens = ctx_mod.cef_audit_set(request=request_stub({"x-real-ip": "5.5.5.5"}), user={"username": "ctx-user", "user_id": "7"})
        try:
            cef.log_cef_event("OBJ001", status_code=200)
        finally:
            ctx_mod.cef_audit_reset(tokens)

        _, ext = parse(events(cef_lines)[0])
        assert ext["src"] == "5.5.5.5" and ext["suser"] == "ctx-user" and ext["suid"] == "7"

    def test_system_events_ignore_request_and_user(self, cef_lines):
        cef.log_cef_event("SYS001", request=request_stub({"x-real-ip": "9.9.9.9"}),
                          current_user={"username": "ivan", "user_id": "1"})

        _, ext = parse(events(cef_lines)[0])
        assert ext["src"] == "127.0.0.1" and ext["shost"] == "localhost" and ext["suser"] == "SYSTEM"
        assert "suid" not in ext

    def test_outbound_event_uses_destination_overrides(self, cef_lines):
        extra = cef.cef_outbound_extra(base_url="http://models:9000", method_name="POST /v1/embeddings",
                                       service_name="phoenix", request_uuid="r1", model="frida", provider="PHOENIX")

        cef.log_cef_event("INT003", status_code=200, extra=extra)

        header, ext = parse(events(cef_lines)[0])
        assert header[4] == "INT003" and ext["deviceDirection"] == "1"
        assert ext["dhost"] == "models" and ext["dpt"] == "9000"
        assert ext["cs1"] == "frida" and ext["cs3"] == "PHOENIX" and ext["cs1Label"] == "LLMModel"
        # Приватные ключи (_deviceDirection) в строку не попадают
        assert "_deviceDirection" not in events(cef_lines)[0]

    def test_extension_order_known_keys_first_then_alphabetical_extras(self, cef_lines):
        cef.log_cef_event("OBJ001", status_code=200, extra={"zzz": "1", "aaa": "2", "none": None})

        line = events(cef_lines)[0]
        assert line.index("externalId=") < line.index("deviceProcessName=") < line.index("msg=")
        assert line.index("aaa=2") < line.index("zzz=1") and "none=" not in line

    def test_values_are_escaped(self, cef_lines):
        cef.log_cef_event("OBJ002", status_code=500, extra={"codeStatus": "500", "textStatus": "a=b\nc"})

        assert "textStatus=a\\=b\\nc" in events(cef_lines)[0]

    def test_message_template_uses_dash_for_missing_fields(self, cef_lines):
        cef.log_cef_event("OBJ001", status_code=200)

        _, ext = parse(events(cef_lines)[0])
        assert ext["msg"].startswith("Обработка запроса '-' сервиса -")

    def test_syslog_copy_is_emitted(self, cef_lines, udp, monkeypatch):
        monkeypatch.setenv("AUDIT_CEF_ENABLED", "true")
        monkeypatch.setenv("AUDIT_SYSLOG_TARGET", "siem")

        cef.log_cef_event("OBJ001", status_code=200)

        assert udp.created[0].sent[0][0].endswith(events(cef_lines)[0].encode())

    def test_audit_context_failure_does_not_stop_event(self, cef_lines, monkeypatch):
        def boom():
            raise RuntimeError("контекст сломан")

        monkeypatch.setattr(ctx_mod, "cef_audit_peek", boom)

        cef.log_cef_event("OBJ001", status_code=200)

        assert len(events(cef_lines)) == 1

    def test_failure_event_without_status_must_be_failure(self, cef_lines):
        # BUG-RAG-22: без status_code (сбой вне HTTP) outcome берётся из spec события.
        cef.log_cef_event("INT006", status_code=None, extra={"codeStatus": "EXCEPTION"})

        _, ext = parse(events(cef_lines)[0])
        assert ext["outcome"] == "failure"

    def test_explicit_outcome_wins(self, cef_lines):
        cef.log_cef_event("INT006", status_code=None, outcome="failure")

        assert parse(events(cef_lines)[0])[1]["outcome"] == "failure"


class TestIntegrationHelpers:
    def test_int003_model_request(self, cef_lines):
        cef.log_cef_int003_model_request(base_url="http://models:8000", model="frida", provider="PHOENIX")

        header, ext = parse(events(cef_lines)[0])
        assert header[4] == "INT003" and ext["methodName"] == "POST /v1/embeddings" and ext["serviceName"] == "PHOENIX"
        assert ext["requestUuid"] and ext["outcome"] == "success"

    def test_int003_without_provider_uses_default_service(self, cef_lines):
        cef.log_cef_int003_model_request(base_url="http://m", model="x", provider="", method_name="POST /v1/rerank")

        _, ext = parse(events(cef_lines)[0])
        assert ext["serviceName"] == "rag-models" and ext["methodName"] == "POST /v1/rerank"

    def test_int006_failure_with_status(self, cef_lines):
        cef.log_cef_int006_outbound_failure(base_url="http://m", model="x", provider="p", method_name="POST /v1/embeddings",
                                            status_code=503, text_status="t" * 600)

        header, ext = parse(events(cef_lines)[0])
        assert header[4] == "INT006" and ext["codeStatus"] == "503" and ext["outcome"] == "error"
        assert ext["textStatus"] == "t" * 512

    def test_int006_failure_without_status_is_exception(self, cef_lines):
        cef.log_cef_int006_outbound_failure(base_url="http://m", model="x", provider="p", method_name="m",
                                            status_code=None, text_status="")

        _, ext = parse(events(cef_lines)[0])
        assert ext["codeStatus"] == "EXCEPTION"


# ───────────────────────────── storage_audit ─────────────────────────────


class TestStorageAudit:
    def test_success_event(self, cef_lines):
        db = SimpleNamespace(host="pg.local", port=5433, user="rag")

        storage_audit.log_postgres_success("insert_chunks", db=db, request_uuid="req-1")

        header, ext = parse(events(cef_lines)[0])
        assert header[4] == "INT005" and ext["dhost"] == "pg.local" and ext["dpt"] == "5433" and ext["duser"] == "rag"
        assert ext["methodName"] == "insert_chunks" and ext["serviceName"] == "AstraChat-PostgreSQL"
        assert ext["requestUuid"] == "req-1" and ext["deviceDirection"] == "1" and ext["outcome"] == "success"

    def test_defaults_for_db_without_attributes(self):
        extra = storage_audit._extra(method_name="m", db=object())

        assert extra["dhost"] == "postgresql" and extra["dpt"] == 5432 and extra["duser"] == "-"
        assert len(extra["requestUuid"]) == 32

    def test_failure_event_truncates_error(self, cef_lines):
        storage_audit.log_postgres_failure("delete", "e" * 700, db=SimpleNamespace(), request_uuid="r")

        header, ext = parse(events(cef_lines)[0])
        assert header[4] == "INT006" and ext["codeStatus"] == "EXCEPTION" and ext["textStatus"] == "e" * 512

    def test_emit_swallows_logger_errors(self, monkeypatch, caplog):
        def boom(*a, **k):
            raise RuntimeError("cef сломан")

        monkeypatch.setattr(cef, "log_cef_event", boom)
        caplog.set_level(logging.DEBUG, logger=storage_audit.logger.name)

        storage_audit.log_postgres_success("m", db=SimpleNamespace())

        assert any("CEF postgres emit failed" in r.getMessage() for r in caplog.records)

    def test_emit_uses_audit_context_request_and_user(self, cef_lines):
        tokens = ctx_mod.cef_audit_set(request=request_stub({"x-real-ip": "6.6.6.6"}), user={"username": "u", "user_id": "9"})
        try:
            storage_audit.log_postgres_success("m", db=SimpleNamespace())
        finally:
            ctx_mod.cef_audit_reset(tokens)

        _, ext = parse(events(cef_lines)[0])
        assert ext["src"] == "6.6.6.6" and ext["suser"] == "u"


# ───────────────────────────── cef_audit_context ─────────────────────────────


class TestAuditContext:
    def test_set_peek_reset(self):
        assert ctx_mod.cef_audit_peek() == (None, None)
        tokens = ctx_mod.cef_audit_set(request="req", user={"username": "x"})

        assert ctx_mod.cef_audit_peek() == ("req", {"username": "x"})
        ctx_mod.cef_audit_reset(tokens)
        assert ctx_mod.cef_audit_peek() == (None, None)

    def test_chat_model_from_headers(self):
        ctx_mod.chat_model_set_from_request(SimpleNamespace(headers={"x-astra-model": " gpt-x "}))
        assert ctx_mod.chat_model_peek() == "gpt-x"

        ctx_mod.chat_model_set_from_request(SimpleNamespace(headers={"X-Astra-Model": "Upper"}))
        assert ctx_mod.chat_model_peek() == "Upper"

        ctx_mod.chat_model_set_from_request(SimpleNamespace(headers={}))
        assert ctx_mod.chat_model_peek() is None

    def test_chat_model_without_headers_or_on_error(self):
        ctx_mod.chat_model_set_from_request(SimpleNamespace(headers=None))
        assert ctx_mod.chat_model_peek() is None

        class Broken:
            @property
            def headers(self):
                raise RuntimeError("x")

        ctx_mod._chat_model.set("old")
        ctx_mod.chat_model_set_from_request(Broken())
        assert ctx_mod.chat_model_peek() is None

    def test_model_identity_defaults_to_system_login(self):
        assert ctx_mod.model_identity_headers() == {"X-User-ID": "astrachat"}

    def test_model_identity_from_user_and_env_override(self, monkeypatch):
        tokens = ctx_mod.cef_audit_set(user={"username": "ivan"})
        try:
            assert ctx_mod.model_identity_headers() == {"X-User-ID": "ivan"}
        finally:
            ctx_mod.cef_audit_reset(tokens)

        tokens = ctx_mod.cef_audit_set(user={"user_id": "u-7"})
        try:
            assert ctx_mod.model_identity_headers() == {"X-User-ID": "u-7"}
        finally:
            ctx_mod.cef_audit_reset(tokens)

        monkeypatch.setenv("MODELS_SYSTEM_USER_ID", "svc-login")
        tokens = ctx_mod.cef_audit_set(user={"username": "system"})
        try:
            assert ctx_mod.model_identity_headers() == {"X-User-ID": "svc-login"}
        finally:
            ctx_mod.cef_audit_reset(tokens)

    def test_model_identity_blank_env_falls_back_to_constant(self, monkeypatch):
        monkeypatch.setenv("MODELS_SYSTEM_USER_ID", "   ")

        assert ctx_mod.model_identity_headers() == {"X-User-ID": ctx_mod.MODELS_SYSTEM_USER_ID}

    def test_model_identity_survives_peek_failure(self, monkeypatch):
        def boom():
            raise RuntimeError

        monkeypatch.setattr(ctx_mod, "cef_audit_peek", boom)

        assert ctx_mod.model_identity_headers() == {"X-User-ID": "astrachat"}

    def test_identity_headers_for_backend(self):
        assert ctx_mod.cef_identity_headers() == {}
        tokens = ctx_mod.cef_audit_set(user={"username": "ivan", "user_id": "42", "sntdom": "CORP"})
        try:
            assert ctx_mod.cef_identity_headers() == {"X-CEF-Suser": "ivan", "X-CEF-Suid": "42", "X-CEF-Sntdom": "CORP"}
        finally:
            ctx_mod.cef_audit_reset(tokens)

        tokens = ctx_mod.cef_audit_set(user={"username": " ", "user_id": None})
        try:
            assert ctx_mod.cef_identity_headers() == {}
        finally:
            ctx_mod.cef_audit_reset(tokens)

    def test_identity_headers_survive_errors(self, monkeypatch):
        class BadUser(dict):
            def get(self, *a):
                raise RuntimeError

        tokens = ctx_mod.cef_audit_set(user=BadUser(a=1))
        try:
            assert ctx_mod.cef_identity_headers() == {}
        finally:
            ctx_mod.cef_audit_reset(tokens)

    def test_user_from_headers(self):
        assert ctx_mod.user_from_request_headers(None) is None
        assert ctx_mod.user_from_request_headers(SimpleNamespace(headers=None)) is None
        assert ctx_mod.user_from_request_headers(SimpleNamespace(headers={})) is None

        user = ctx_mod.user_from_request_headers(SimpleNamespace(headers={"x-cef-suser": " ivan ", "x-cef-suid": "42", "x-cef-sntdom": "C"}))
        assert user == {"username": "ivan", "user_id": "42", "sntdom": "C"}

    def test_user_from_headers_with_only_id_or_only_name(self):
        only_id = ctx_mod.user_from_request_headers(SimpleNamespace(headers={"X-CEF-Suid": "7"}))
        only_name = ctx_mod.user_from_request_headers(SimpleNamespace(headers={"X-CEF-Suser": "bob"}))

        assert only_id == {"username": "7", "user_id": "7", "sntdom": None}
        assert only_name == {"username": "bob", "user_id": None, "sntdom": None}

    def test_package_reexports(self):
        assert cef_pkg.log_cef_event is cef.log_cef_event
        assert cef_pkg.CefAuditMiddleware is mw.CefAuditMiddleware
        assert cef_pkg.log_postgres_failure is storage_audit.log_postgres_failure


# ───────────────────────────── middleware ─────────────────────────────


@pytest.fixture
def emitted(monkeypatch):
    out = []
    monkeypatch.setattr(mw, "log_cef_event", lambda event_id, **kw: out.append((event_id, kw)))
    monkeypatch.setattr(mw, "_POLL_LAST_EMIT", {})
    monkeypatch.setattr(mw, "_POLL_SUPPRESSED", {})
    monkeypatch.setattr(mw, "_audit_setting", lambda env, yml, default: default)
    return out


@pytest.fixture
def app_client(emitted):
    application = FastAPI()
    application.add_middleware(mw.CefAuditMiddleware)
    seen = {}

    @application.get("/v1/ok")
    async def ok():
        seen["ctx"] = ctx_mod.cef_audit_peek()
        seen["model"] = ctx_mod.chat_model_peek()
        return {"ok": True}

    @application.get("/v1/missing")
    async def missing():
        raise HTTPException(status_code=404, detail="нет")

    @application.get("/v1/crash")
    async def crash():
        raise RuntimeError("взрыв " + "x" * 600)

    @application.get("/v1/kb/documents")
    async def docs():
        return []

    @application.post("/v1/kb/documents")
    async def upload():
        return {"ok": True}

    @application.get("/v1/health")
    async def health():
        return {"status": "ok"}

    @application.get("/v1/crash/health")
    async def health_crash():
        raise RuntimeError("проба упала")

    @application.get("/v1/redirect")
    async def redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/v1/ok", status_code=302)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url="http://rag")
    client.seen = seen
    return client


class TestMiddleware:
    async def test_success_emits_obj001_with_context(self, app_client, emitted):
        r = await app_client.get("/v1/ok", headers={"X-CEF-Suser": "ivan", "X-CEF-Suid": "42", "X-Astra-Model": "gpt"})

        assert r.status_code == 200
        event_id, kw = emitted[0]
        assert event_id == "OBJ001" and kw["status_code"] == 200
        assert kw["current_user"]["username"] == "ivan"
        assert kw["extra"]["methodName"] == "GET /v1/ok" and kw["extra"]["serviceName"] == "SVC-RAG"
        assert len(kw["extra"]["requestUuid"]) == 32
        # Контекст был виден обработчику и убран после запроса
        assert app_client.seen["ctx"][1]["user_id"] == "42" and app_client.seen["model"] == "gpt"
        assert ctx_mod.cef_audit_peek() == (None, None)

    async def test_http_error_emits_obj002_with_status(self, app_client, emitted):
        r = await app_client.get("/v1/missing")

        assert r.status_code == 404
        event_id, kw = emitted[0]
        assert event_id == "OBJ002" and kw["status_code"] == 404
        assert kw["extra"]["codeStatus"] == "404" and kw["extra"]["textStatus"] == "-"

    async def test_unhandled_exception_emits_obj002_500_and_truncated_text(self, app_client, emitted):
        r = await app_client.get("/v1/crash")

        assert r.status_code == 500
        event_id, kw = emitted[0]
        assert event_id == "OBJ002" and kw["status_code"] == 500
        assert kw["extra"]["codeStatus"] == "500" and kw["extra"]["textStatus"].startswith("взрыв") and len(kw["extra"]["textStatus"]) == 512
        assert ctx_mod.cef_audit_peek() == (None, None)

    async def test_health_is_not_audited(self, app_client, emitted):
        await app_client.get("/v1/health")

        assert emitted == []

    async def test_exception_on_skipped_path_is_not_audited_but_context_is_reset(self, app_client, emitted):
        r = await app_client.get("/v1/crash/health")

        assert r.status_code == 500 and emitted == []
        assert ctx_mod.cef_audit_peek() == (None, None)

    async def test_redirect_is_success(self, app_client, emitted):
        r = await app_client.get("/v1/redirect")

        assert r.status_code == 302 and emitted[0][0] == "OBJ001"

    async def test_polling_read_is_thinned_to_one_event_per_window(self, app_client, emitted, monkeypatch):
        clock = SimpleNamespace(now=100.0)
        monkeypatch.setattr(mw.time, "monotonic", lambda: clock.now)

        for _ in range(3):
            await app_client.get("/v1/kb/documents", headers={"X-CEF-Suser": "ivan"})
        assert [e[0] for e in emitted] == ["OBJ001"]

        clock.now += 61
        await app_client.get("/v1/kb/documents", headers={"X-CEF-Suser": "ivan"})

        assert len(emitted) == 2
        # Событие после окна представляет 3 пропущенных + само обращение
        assert emitted[1][1]["extra"]["cn1"] == 3 and emitted[1][1]["extra"]["cn1Label"] == "RequestCount"

    async def test_write_on_polling_path_is_always_audited(self, app_client, emitted):
        await app_client.post("/v1/kb/documents")
        await app_client.post("/v1/kb/documents")

        assert [e[0] for e in emitted] == ["OBJ001", "OBJ001"]

    async def test_polling_audit_disabled_by_setting(self, app_client, emitted, monkeypatch):
        monkeypatch.setattr(mw, "_audit_setting", lambda env, yml, default: False if yml == "cef_polling" else default)

        await app_client.get("/v1/kb/documents")

        assert emitted == []


class TestMiddlewareHelpers:
    @pytest.mark.parametrize("path,expected", [
        ("/v1/health", True), ("/v1/health/", True), ("/docs", True), ("/redoc", True), ("/openapi.json", True),
        ("/v1/kb/search", False), ("", False), (None, False),
    ])
    def test_should_skip(self, path, expected):
        assert mw._should_skip(path) is expected

    @pytest.mark.parametrize("method,path,status,expected", [
        ("GET", "/v1/kb/reindex/status", 200, True),
        ("HEAD", "/v1/kb/documents/", 200, True),
        ("GET", "/v1/kb/documents", 302, True),
        ("GET", "/v1/kb/documents/42/chunks", 200, False),
        ("POST", "/v1/kb/documents", 200, False),
        ("GET", "/v1/kb/documents", 404, False),
        ("GET", "/v1/kb/documents", "x", False),
        (None, "/v1/kb/documents", 200, False),
    ])
    def test_is_polling_read(self, method, path, status, expected):
        assert mw._is_polling_read(method, path, status) is expected

    def test_poll_decision_disabled_polling(self, monkeypatch):
        monkeypatch.setattr(mw, "_audit_setting", lambda env, yml, default: False if yml == "cef_polling" else default)

        assert mw._poll_audit_decision(None, "GET", "/p") == (False, 0)

    @pytest.mark.parametrize("interval", [0, -5])
    def test_poll_decision_zero_interval_audits_everything(self, monkeypatch, interval):
        monkeypatch.setattr(mw, "_audit_setting", lambda env, yml, default: interval if yml.endswith("seconds") else True)

        assert mw._poll_audit_decision(None, "GET", "/p") == (True, 1)
        assert mw._poll_audit_decision(None, "GET", "/p") == (True, 1)

    def test_poll_decision_bad_interval_falls_back_to_60(self, monkeypatch):
        monkeypatch.setattr(mw, "_POLL_LAST_EMIT", {})
        monkeypatch.setattr(mw, "_POLL_SUPPRESSED", {})
        monkeypatch.setattr(mw, "_audit_setting", lambda env, yml, default: "abc" if yml.endswith("seconds") else True)

        assert mw._poll_audit_decision(None, "GET", "/p") == (True, 1)
        assert mw._poll_audit_decision(None, "GET", "/p") == (False, 0)

    def test_poll_decision_is_per_user_and_path(self, monkeypatch):
        monkeypatch.setattr(mw, "_POLL_LAST_EMIT", {})
        monkeypatch.setattr(mw, "_POLL_SUPPRESSED", {})
        monkeypatch.setattr(mw, "_audit_setting", lambda env, yml, default: default)

        assert mw._poll_audit_decision({"username": "a"}, "GET", "/p") == (True, 1)
        assert mw._poll_audit_decision({"username": "b"}, "GET", "/p") == (True, 1)
        # Ключ нормализуется: логин берётся из username или user_id, метод - в верхний регистр, хвостовой "/" срезается.
        # Поэтому {"user_id": "a"} + "get" + "/p/" - тот же ключ, что у первого вызова, и подавляется (корректная дедупликация).
        assert mw._poll_audit_decision({"user_id": "a"}, "get", "/p/") == (False, 0)
        assert mw._poll_audit_decision({"user_id": "c"}, "get", "/p/") == (True, 1)
        assert mw._poll_audit_decision({"username": "a"}, "GET", "/p") == (False, 0)
        # Пользователь без данных попадает под общий ключ SYSTEM
        assert mw._poll_audit_decision(None, "GET", "/p") == (True, 1)
        assert mw._poll_audit_decision({}, "GET", "/p") == (False, 0)

    def test_poll_table_is_pruned_over_limit(self, monkeypatch):
        clock = SimpleNamespace(now=1000.0)
        monkeypatch.setattr(mw.time, "monotonic", lambda: clock.now)
        monkeypatch.setattr(mw, "_POLL_KEYS_MAX", 2)
        monkeypatch.setattr(mw, "_POLL_LAST_EMIT", {})
        monkeypatch.setattr(mw, "_POLL_SUPPRESSED", {})
        monkeypatch.setattr(mw, "_audit_setting", lambda env, yml, default: default)

        mw._poll_audit_decision({"username": "old1"}, "GET", "/p")
        mw._poll_audit_decision({"username": "old2"}, "GET", "/p")
        mw._POLL_SUPPRESSED[("old1", "GET", "/p")] = 4
        clock.now += 60 * 10 + 1
        mw._poll_audit_decision({"username": "new"}, "GET", "/p")

        assert list(mw._POLL_LAST_EMIT) == [("new", "GET", "/p")]
        assert mw._POLL_SUPPRESSED == {}


class TestAuditSetting:
    def test_env_bool_and_int(self, monkeypatch):
        monkeypatch.setenv("X_FLAG", "Yes")
        monkeypatch.setenv("X_NUM", "30")
        monkeypatch.setenv("X_BAD", "много")

        assert mw._audit_setting("X_FLAG", "f", False) is True
        assert mw._audit_setting("X_NUM", "n", 60) == 30
        assert mw._audit_setting("X_BAD", "n", 60) == 60

    def test_env_false_literal_is_false(self, monkeypatch):
        monkeypatch.setenv("X_FLAG", "off")

        assert mw._audit_setting("X_FLAG", "f", True) is False

    def test_yaml_value_then_default(self, monkeypatch):
        import app.core.config as config

        monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(audit=SimpleNamespace(cef_polling=False, other=None)))

        assert mw._audit_setting("UNSET_ENV", "cef_polling", True) is False
        assert mw._audit_setting("UNSET_ENV", "other", 5) == 5
        assert mw._audit_setting("UNSET_ENV", "missing", 7) == 7

    def test_settings_failure_gives_default(self, monkeypatch):
        import app.core.config as config

        def boom():
            raise RuntimeError("нет конфига")

        monkeypatch.setattr(config, "get_settings", boom)

        assert mw._audit_setting("UNSET_ENV", "cef_polling", True) is True
