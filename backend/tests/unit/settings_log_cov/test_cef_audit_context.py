"""Тесты backend/settings/cef_logger/cef_audit_context.py: контекст аудита, src/shost, DNS."""

import contextvars
import socket
import sys
import time
from types import SimpleNamespace

import pytest

from backend.settings.cef_logger import cef_audit_context as ctx


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    """Изолируем кэш PTR и env, влияющие на DNS/диагностику."""
    monkeypatch.setattr(ctx, "_rdns_cache", {})
    for var in (
        "CEF_SHOST_DEBUG",
        "CEF_REVERSE_DNS",
        "CEF_DNS_SERVERS",
        "CEF_DNS_FROM_LDAP",
        "CEF_RESOLV_CONF",
        "CEF_REVERSE_DNS_TIMEOUT",
        "LDAP_URL",
        "LDAP_SERVER",
        "MODELS_SYSTEM_USER_ID",
    ):
        monkeypatch.delenv(var, raising=False)


def _req(host="10.0.0.5", port=4321, headers=None, path="/api/x", scheme="https", client=True):
    return SimpleNamespace(
        client=SimpleNamespace(host=host, port=port) if client else None,
        headers=headers or {},
        url=SimpleNamespace(path=path, scheme=scheme),
    )


# ---------------- диагностика shost ----------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("TRUE", True), (" yes ", True), ("on", True), ("", False), ("0", False), ("off", False)],
)
def test_shost_debug_flag(monkeypatch, raw, expected):
    monkeypatch.setenv("CEF_SHOST_DEBUG", raw)
    assert ctx._cef_shost_debug_enabled() is expected


def test_shost_log_goes_to_debug_by_default(capture_logger):
    h = capture_logger("cef.shost")
    ctx._cef_shost_log("значение %s", 1)
    assert [(r.levelname, r.getMessage()) for r in h.records] == [("DEBUG", "значение 1")]


def test_shost_log_goes_to_info_when_debug_enabled(monkeypatch, capture_logger):
    monkeypatch.setenv("CEF_SHOST_DEBUG", "true")
    h = capture_logger("cef.shost")
    ctx._cef_shost_log("значение %s", 2)
    assert [(r.levelname, r.getMessage()) for r in h.records] == [("INFO", "значение 2")]


# ---------------- contextvars ----------------


def _in_fresh_context(fn):
    return contextvars.copy_context().run(fn)


def test_chat_model_set_and_peek_normalizes():
    def body():
        assert ctx.chat_model_peek() is None
        ctx.chat_model_set("  org/model  ")
        assert ctx.chat_model_peek() == "org/model"
        ctx.chat_model_set("   ")
        assert ctx.chat_model_peek() is None
        ctx.chat_model_set(None)
        assert ctx.chat_model_peek() is None
        ctx.chat_model_set(123)
        assert ctx.chat_model_peek() == "123"

    _in_fresh_context(body)


def test_audit_set_peek_reset_roundtrip():
    def body():
        req, user = object(), {"username": "u"}
        tokens = ctx.cef_audit_set(request=req, user=user, conversation_id="c1")
        assert ctx.cef_audit_peek() == (req, user, "c1")
        assert len(tokens.pairs) == 3
        ctx.cef_audit_reset(tokens)
        assert ctx.cef_audit_peek() == (None, None, None)

    _in_fresh_context(body)


def test_audit_set_without_arguments_changes_nothing():
    def body():
        tokens = ctx.cef_audit_set()
        assert tokens.pairs == ()
        assert ctx.cef_audit_peek() == (None, None, None)
        assert ctx.cef_audit_peek_socket_remote() is None

    _in_fresh_context(body)


def test_audit_set_socket_remote_semantics():
    def body():
        remote = {"src": "1.2.3.4"}
        t1 = ctx.cef_audit_set(socket_remote=remote)
        assert ctx.cef_audit_peek_socket_remote() == remote
        # вызов без socket_remote не меняет подсказку
        t2 = ctx.cef_audit_set(user={"username": "x"})
        assert ctx.cef_audit_peek_socket_remote() == remote
        # явный None сбрасывает подсказку (REST поверх сокета)
        t3 = ctx.cef_audit_set(socket_remote=None)
        assert ctx.cef_audit_peek_socket_remote() is None
        ctx.cef_audit_reset(t3)
        assert ctx.cef_audit_peek_socket_remote() == remote
        ctx.cef_audit_reset(t2)
        ctx.cef_audit_reset(t1)
        assert ctx.cef_audit_peek_socket_remote() is None

    _in_fresh_context(body)


# ---------------- model_identity_headers ----------------


def test_model_identity_uses_username_then_user_id():
    def body():
        t = ctx.cef_audit_set(user={"username": " ivanov ", "user_id": "7"})
        assert ctx.model_identity_headers() == {"X-User-ID": "ivanov"}
        ctx.cef_audit_reset(t)
        t = ctx.cef_audit_set(user={"user_id": "u-7"})
        assert ctx.model_identity_headers() == {"X-User-ID": "u-7"}
        ctx.cef_audit_reset(t)

    _in_fresh_context(body)


def test_model_identity_system_and_missing_user_use_service_login(monkeypatch):
    def body():
        assert ctx.model_identity_headers() == {"X-User-ID": "astrachat"}
        t = ctx.cef_audit_set(user={"username": "system"})
        assert ctx.model_identity_headers() == {"X-User-ID": "astrachat"}
        ctx.cef_audit_reset(t)
        monkeypatch.setenv("MODELS_SYSTEM_USER_ID", " svc-login ")
        assert ctx.model_identity_headers() == {"X-User-ID": "svc-login"}
        monkeypatch.setenv("MODELS_SYSTEM_USER_ID", "   ")
        assert ctx.model_identity_headers() == {"X-User-ID": ctx.MODELS_SYSTEM_USER_ID}

    _in_fresh_context(body)


def test_model_identity_survives_peek_failure(monkeypatch):
    def boom():
        raise RuntimeError("нет контекста")

    monkeypatch.setattr(ctx, "cef_audit_peek", boom)
    assert ctx.model_identity_headers() == {"X-User-ID": "astrachat"}


# ---------------- разбор IP ----------------


def test_first_forwarded_ip():
    assert ctx._first_forwarded_ip(None) == ""
    assert ctx._first_forwarded_ip(" 1.1.1.1 , 2.2.2.2") == "1.1.1.1"


@pytest.mark.parametrize(
    ("ip", "expected"),
    [("", True), ("127.0.0.1", True), ("::1", True), ("10.0.0.1", False), ("не-ip", False)],
)
def test_is_loopback_ip(ip, expected):
    assert ctx._is_loopback_ip(ip) is expected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"x_real_ip": "5.5.5.5, 6.6.6.6", "x_forwarded_for": "1.1.1.1"}, "5.5.5.5"),
        ({"x_real_ip": "127.0.0.1", "x_forwarded_for": "127.0.0.1, 8.8.8.8"}, "8.8.8.8"),
        ({"x_real_ip": "127.0.0.1", "x_forwarded_for": "127.0.0.2"}, "127.0.0.1"),
        ({"x_forwarded_for": "127.0.0.1, ,127.0.0.2"}, "127.0.0.1"),
        ({"x_forwarded_for": " , "}, "127.0.0.1"),
        ({"x_forwarded_for": "", "direct_peer": " 9.9.9.9 "}, "9.9.9.9"),
        ({}, "127.0.0.1"),
    ],
)
def test_resolve_client_src(kwargs, expected):
    assert ctx.resolve_client_src(**kwargs) == expected


def test_resolve_client_src_from_request_none():
    assert ctx.resolve_client_src_from_request(None) == "127.0.0.1"


def test_resolve_client_src_from_request_uses_headers_then_peer():
    r = _req(host="10.9.9.9", headers={"x-forwarded-for": "7.7.7.7, 10.0.0.1"})
    assert ctx.resolve_client_src_from_request(r) == "7.7.7.7"
    r2 = _req(host="10.9.9.9")
    assert ctx.resolve_client_src_from_request(r2) == "10.9.9.9"
    r3 = _req(client=False)
    assert ctx.resolve_client_src_from_request(r3) == "127.0.0.1"


def test_shost_override_from_headers():
    assert ctx.shost_override_from_headers() == ""
    assert ctx.shost_override_from_headers(x_client_hostname="  pc-01 ") == "pc-01"


# ---------------- настройки DNS ----------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, True), ("true", True), ("0", False), ("FALSE", False), (" no ", False), ("off", False)],
)
def test_reverse_dns_enabled(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("CEF_REVERSE_DNS", raw)
    assert ctx._reverse_dns_enabled() is expected


def test_ldap_url_for_dns_prefers_env(monkeypatch):
    monkeypatch.setenv("LDAP_SERVER", "ldap://second")
    assert ctx._ldap_url_for_dns() == "ldap://second"
    monkeypatch.setenv("LDAP_URL", " ldap://first ")
    assert ctx._ldap_url_for_dns() == "ldap://first"


def test_ldap_url_for_dns_falls_back_to_settings(monkeypatch):
    monkeypatch.setattr("backend.auth.ldap_auth._get_ldap_settings", lambda: {"url": " ldap://cfg "})
    assert ctx._ldap_url_for_dns() == "ldap://cfg"


def test_ldap_url_for_dns_empty_when_settings_fail_or_empty(monkeypatch):
    monkeypatch.setattr("backend.auth.ldap_auth._get_ldap_settings", lambda: {"url": ""})
    assert ctx._ldap_url_for_dns() == ""

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr("backend.auth.ldap_auth._get_ldap_settings", boom)
    assert ctx._ldap_url_for_dns() == ""


def test_nameservers_from_resolv_conf(monkeypatch, tmp_path):
    f = tmp_path / "resolv.conf"
    f.write_text(
        "# comment\nnameserver 127.0.0.1\nnameserver ::1\nnameserver 10.0.0.2\n"
        "NameServer 10.0.0.3\nnameserver 10.0.0.2\nsearch local\nnameserver\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CEF_RESOLV_CONF", str(f))
    assert ctx._nameservers_from_resolv_conf() == ["10.0.0.2", "10.0.0.3"]


def test_nameservers_from_resolv_conf_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CEF_RESOLV_CONF", str(tmp_path / "нет.conf"))
    assert ctx._nameservers_from_resolv_conf() == []


def test_cef_ptr_nameservers_combines_sources_without_duplicates(monkeypatch, tmp_path):
    resolv = tmp_path / "r.conf"
    resolv.write_text("nameserver 10.0.0.9\nnameserver 8.8.8.8\n", encoding="utf-8")
    monkeypatch.setenv("CEF_RESOLV_CONF", str(resolv))
    monkeypatch.setenv("CEF_DNS_SERVERS", "8.8.8.8, ,1.1.1.1,8.8.8.8")
    monkeypatch.setenv("LDAP_URL", "ldaps://dc.corp.local:636")
    seen = {}

    def fake_getaddrinfo(host, port, type=0):
        seen["host"] = host
        return [(None, None, None, "", ("10.0.0.1", 0)), (None, None, None, "", ("1.1.1.1", 0)),
                (None, None, None, "", ("", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert ctx._cef_ptr_nameservers() == ["8.8.8.8", "1.1.1.1", "10.0.0.1", "10.0.0.9"]
    assert seen["host"] == "dc.corp.local"


def test_cef_ptr_nameservers_ldap_without_scheme_and_resolution_error(monkeypatch, tmp_path):
    monkeypatch.setenv("CEF_RESOLV_CONF", str(tmp_path / "нет"))
    monkeypatch.setenv("LDAP_URL", "dc.corp.local")

    def fail(host, port, type=0):
        raise OSError("не резолвится")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert ctx._cef_ptr_nameservers() == []


def test_cef_ptr_nameservers_ldap_disabled_or_no_host(monkeypatch, tmp_path):
    monkeypatch.setenv("CEF_RESOLV_CONF", str(tmp_path / "нет"))
    monkeypatch.setenv("LDAP_URL", "ldap://dc")
    monkeypatch.setenv("CEF_DNS_FROM_LDAP", "false")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("не должно вызываться"))
    assert ctx._cef_ptr_nameservers() == []
    # LDAP-адрес без хоста: getaddrinfo не вызывается
    monkeypatch.setenv("CEF_DNS_FROM_LDAP", "true")
    monkeypatch.setenv("LDAP_URL", "ldap://")
    assert ctx._cef_ptr_nameservers() == []


# ---------------- PTR ----------------


def test_ipv4_ptr_qname_with_dnspython():
    assert ctx._ipv4_ptr_qname("10.1.2.3") == "3.2.1.10.in-addr.arpa"


def test_ipv4_ptr_qname_manual_fallback_without_dnspython(monkeypatch):
    monkeypatch.setitem(sys.modules, "dns.reversename", None)  # import -> ImportError
    assert ctx._ipv4_ptr_qname("10.1.2.3") == "3.2.1.10.in-addr.arpa"
    assert ctx._ipv4_ptr_qname("не-ip") == "не-ip"
    assert ctx._ipv4_ptr_qname("1.2.3.999") == "1.2.3.999"
    assert ctx._ipv4_ptr_qname("") == ""


def test_ptr_system_success(monkeypatch):
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("pc-1.corp.", [], [ip]))
    assert ctx._ptr_system("10.0.0.1") == "pc-1.corp"


def test_ptr_system_ignores_ip_or_empty_answer(monkeypatch):
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: (ip, [], [ip]))
    assert ctx._ptr_system("10.0.0.1") is None
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("", [], [ip]))
    assert ctx._ptr_system("10.0.0.1") is None


def test_ptr_system_oserror(monkeypatch, capture_logger):
    h = capture_logger("cef.dns")

    def fail(ip):
        raise socket.herror("нет записи")

    monkeypatch.setattr(socket, "gethostbyaddr", fail)
    assert ctx._ptr_system("10.0.0.1") is None
    assert any("gethostbyaddr(10.0.0.1)" in m for m in h.messages)


class _FakeResolver:
    """Подмена dns.resolver.Resolver: поведение задаётся по адресу DNS-сервера."""

    behaviours: dict = {}

    def __init__(self, configure=True):
        assert configure is False
        self.nameservers = []
        self.lifetime = None

    def resolve(self, rev, rtype):
        assert rtype == "PTR"
        result = self.behaviours[self.nameservers[0]]
        if isinstance(result, Exception):
            raise result
        return [SimpleNamespace(to_text=lambda t=t: t) for t in result]


@pytest.fixture
def fake_dns(monkeypatch):
    import dns.resolver

    monkeypatch.setattr(dns.resolver, "Resolver", _FakeResolver)
    _FakeResolver.behaviours = {}
    return _FakeResolver


def test_ptr_dnspython_without_nameservers():
    assert ctx._ptr_dnspython("10.0.0.1", []) is None


def test_ptr_dnspython_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "dns.resolver", None)
    assert ctx._ptr_dnspython("10.0.0.1", ["8.8.8.8"]) is None


def test_ptr_dnspython_first_good_answer(fake_dns, monkeypatch):
    monkeypatch.setenv("CEF_REVERSE_DNS_TIMEOUT", "5")
    fake_dns.behaviours = {
        "1.1.1.1": RuntimeError("таймаут"),
        "2.2.2.2": ["10.0.0.1.", "host.corp."],
    }
    assert ctx._ptr_dnspython("10.0.0.1", ["1.1.1.1", "2.2.2.2"]) == "host.corp"


def test_ptr_dnspython_no_usable_answers(fake_dns):
    fake_dns.behaviours = {"1.1.1.1": ["", "10.0.0.1."]}
    assert ctx._ptr_dnspython("10.0.0.1", ["1.1.1.1"]) is None


@pytest.mark.parametrize("raw", ["abc", "0.1"])
def test_ptr_dnspython_timeout_env_is_sanitized(fake_dns, monkeypatch, raw):
    monkeypatch.setenv("CEF_REVERSE_DNS_TIMEOUT", raw)
    created = []
    orig_init = fake_dns.__init__

    def spy(self, configure=True):
        orig_init(self, configure)
        created.append(self)

    monkeypatch.setattr(fake_dns, "__init__", spy)
    fake_dns.behaviours = {"1.1.1.1": ["h.corp."]}
    assert ctx._ptr_dnspython("10.0.0.1", ["1.1.1.1"]) == "h.corp"
    # не число -> 2 с по умолчанию; слишком малое -> минимум 0.5 с
    assert created[0].lifetime == (2.0 if raw == "abc" else 0.5)


# ---------------- reverse_dns / resolve_client_shost ----------------


def test_reverse_dns_trivial_cases():
    assert ctx.reverse_dns("") == "localhost"
    assert ctx.reverse_dns("127.0.0.1") == "localhost"


def test_reverse_dns_disabled_returns_ip(monkeypatch):
    monkeypatch.setenv("CEF_REVERSE_DNS", "false")
    monkeypatch.setattr(ctx, "_ptr_system", lambda ip: pytest.fail("DNS не должен вызываться"))
    assert ctx.reverse_dns("10.0.0.7") == "10.0.0.7"


def test_reverse_dns_system_ptr_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(ctx, "_ptr_system", lambda ip: calls.append(ip) or "pc.corp")
    assert ctx.reverse_dns("10.0.0.7") == "pc.corp"
    assert ctx.reverse_dns("10.0.0.7") == "pc.corp"
    assert calls == ["10.0.0.7"]  # второй раз - из кэша


def test_reverse_dns_cache_expires(monkeypatch):
    answers = iter(["old.corp", "new.corp"])
    monkeypatch.setattr(ctx, "_ptr_system", lambda ip: next(answers))
    assert ctx.reverse_dns("10.0.0.7") == "old.corp"
    host, ts = ctx._rdns_cache["10.0.0.7"]
    ctx._rdns_cache["10.0.0.7"] = (host, ts - ctx._RDNS_TTL_SEC - 1)
    assert ctx.reverse_dns("10.0.0.7") == "new.corp"


def test_reverse_dns_falls_back_to_dnspython_then_to_ip(monkeypatch):
    monkeypatch.setattr(ctx, "_ptr_system", lambda ip: None)
    monkeypatch.setattr(ctx, "_cef_ptr_nameservers", lambda: ["8.8.8.8"])
    monkeypatch.setattr(ctx, "_ptr_dnspython", lambda ip, ns: "via-dns.corp")
    assert ctx.reverse_dns("10.0.0.8") == "via-dns.corp"
    monkeypatch.setattr(ctx, "_ptr_dnspython", lambda ip, ns: None)
    assert ctx.reverse_dns("10.0.0.9") == "10.0.0.9"
    assert ctx._rdns_cache["10.0.0.9"][0] == "10.0.0.9"


def test_resolve_client_shost_override_wins(monkeypatch):
    monkeypatch.setattr(ctx, "reverse_dns", lambda ip: pytest.fail("override не должен ходить в DNS"))
    assert ctx.resolve_client_shost("10.0.0.1", shost_override="  my-pc ") == "my-pc"


def test_resolve_client_shost_uses_reverse_dns(monkeypatch):
    seen = []
    monkeypatch.setattr(ctx, "reverse_dns", lambda ip: seen.append(ip) or "r.corp")
    assert ctx.resolve_client_shost(" 10.0.0.1 ") == "r.corp"
    assert ctx.resolve_client_shost("") == "r.corp"
    assert seen == ["10.0.0.1", "127.0.0.1"]


# ---------------- cef_socket_remote_from_environ ----------------


def test_socket_remote_non_dict_environ():
    assert ctx.cef_socket_remote_from_environ(None) is None
    assert ctx.cef_socket_remote_from_environ("x") is None


def test_socket_remote_from_asgi_scope_headers():
    environ = {
        "asgi.scope": {
            "headers": [
                (b"X-Real-IP", b"5.5.5.5"),
                (b"x-client-hostname", b"pc-77"),
                ("не-байты", "пропуск"),
                (b"one",),
                [b"x-forwarded-for", b"6.6.6.6"],
            ],
            "client": ("10.1.1.1", 5555),
            "scheme": "http",
        }
    }
    out = ctx.cef_socket_remote_from_environ(environ)
    assert out == {"src": "5.5.5.5", "spt": 5555, "app": "HTTP", "shost_override": "pc-77"}


def test_socket_remote_from_wsgi_environ_with_forwarded_proto():
    environ = {
        "HTTP_X_FORWARDED_FOR": "127.0.0.1, 8.8.4.4",
        "REMOTE_ADDR": "10.0.0.2",
        "REMOTE_PORT": "4040",
        "HTTP_X_FORWARDED_PROTO": "HTTPS",
        "HTTP_X_CLIENT_HOSTNAME": "ws-1",
    }
    out = ctx.cef_socket_remote_from_environ(environ)
    assert out == {"src": "8.8.4.4", "spt": 4040, "app": "HTTPS", "shost_override": "ws-1"}


def test_socket_remote_peer_and_scheme_fallbacks():
    # без заголовков: адрес из REMOTE_ADDR, схема из wsgi.url_scheme
    out = ctx.cef_socket_remote_from_environ(
        {"REMOTE_ADDR": "10.0.0.2", "REMOTE_PORT": "abc", "wsgi.url_scheme": "http"}
    )
    assert out == {"src": "10.0.0.2", "spt": 0, "app": "HTTP"}
    # без схемы вообще -> https
    out = ctx.cef_socket_remote_from_environ({"REMOTE_ADDR": "10.0.0.3"})
    assert out["app"] == "HTTPS"
    # схема берётся из ASGI scope
    out = ctx.cef_socket_remote_from_environ(
        {"asgi.scope": {"client": ["10.0.0.4", "x"], "scheme": "http"}}
    )
    assert out == {"src": "10.0.0.4", "spt": 0, "app": "HTTP"}
    # scope без scheme и без прочих данных -> https по умолчанию
    out = ctx.cef_socket_remote_from_environ({"asgi.scope": {"client": ["10.0.0.5"]}})
    assert out["app"] == "HTTPS"


def test_socket_remote_scope_not_dict_is_ignored():
    out = ctx.cef_socket_remote_from_environ({"asgi.scope": "bad", "REMOTE_ADDR": "10.0.0.2"})
    assert out["src"] == "10.0.0.2"


def test_socket_remote_without_any_address_returns_127():
    # resolve_client_src всегда отдаёт адрес (127.0.0.1 по умолчанию)
    out = ctx.cef_socket_remote_from_environ({})
    assert out["src"] == "127.0.0.1"


def test_socket_remote_returns_none_when_src_empty(monkeypatch):
    monkeypatch.setattr(ctx, "resolve_client_src", lambda **kw: "")
    assert ctx.cef_socket_remote_from_environ({"REMOTE_ADDR": "1.1.1.1"}) is None


# ---------------- log_request_context_shost ----------------


def test_log_request_context_shost_writes_trace(capture_logger):
    h = capture_logger("cef.shost")
    ctx.log_request_context_shost(
        channel="http",
        ctx={"src": "1.2.3.4", "shost": "pc", "spt": 1, "app": "HTTPS"},
        shost_override="pc",
        sock_hint=None,
        request_path="/api/x",
    )
    (msg,) = h.messages
    assert "request_context[http]" in msg and "src='1.2.3.4'" in msg and "/api/x" in msg


def test_time_based_cache_uses_wall_clock(monkeypatch):
    """Свежая запись кэша PTR не пересчитывается (контроль границы TTL)."""
    monkeypatch.setattr(ctx, "_ptr_system", lambda ip: pytest.fail("кэш должен сработать"))
    ctx._rdns_cache["10.9.9.9"] = ("cached.corp", time.time())
    assert ctx.reverse_dns("10.9.9.9") == "cached.corp"


def test_cef_ptr_nameservers_ldap_enabled_but_url_unknown(monkeypatch, tmp_path):
    monkeypatch.setenv("CEF_RESOLV_CONF", str(tmp_path / "нет"))
    monkeypatch.setattr(ctx, "_ldap_url_for_dns", lambda: "")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("не должно вызываться"))
    assert ctx._cef_ptr_nameservers() == []


def test_socket_remote_scope_with_empty_client_uses_remote_addr():
    out = ctx.cef_socket_remote_from_environ({"asgi.scope": {"client": []}, "REMOTE_ADDR": "10.0.0.6"})
    assert out["src"] == "10.0.0.6" and out["spt"] == 0
