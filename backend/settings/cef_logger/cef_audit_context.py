"""
Контекст для CEF: пользователь и диалог при вызовах LLM/памяти без явной передачи request.
Используется contextvars (корректно в async). Worker ThreadPoolExecutor и вложенный поток с asyncio.run
теряют контекст по умолчанию — вызывающий код должен пробрасывать снимок через contextvars.copy_context().run(...)
(см. realtime/handlers.py, llm_client.ask_agent_llm_svc).

Для Socket.IO без Starlette Request: при handshake сохраняется ``cef_remote`` (IP клиента) в сессию и передаётся
в ``cef_audit_set(socket_remote=...)`` — см. ``cef_socket_remote_from_environ`` и ``request_context`` в cef_logger.
"""

from __future__ import annotations

import contextvars
import ipaddress
import logging
import os
import socket
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

_UNSET = object()
_cef_dns_logger = logging.getLogger("cef.dns")
_cef_shost_logger = logging.getLogger("cef.shost")

# Как в референсной реализации audit/index.js: кэш PTR 5 минут
_RDNS_TTL_SEC = 5 * 60
_rdns_cache: Dict[str, Tuple[str, float]] = {}


def _cef_shost_debug_enabled() -> bool:
    """Явная диагностика shost/src: CEF_SHOST_DEBUG=true → INFO в логгер cef.shost."""
    return os.getenv("CEF_SHOST_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _cef_shost_log(msg: str, *args: Any) -> None:
    if _cef_shost_debug_enabled():
        _cef_shost_logger.info(msg, *args)
    else:
        _cef_shost_logger.debug(msg, *args)


@dataclass(frozen=True)
class _Tokens:
    """Пары (ContextVar, Token) для сброса."""

    pairs: Tuple[Tuple[contextvars.ContextVar, contextvars.Token], ...]


_request: contextvars.ContextVar = contextvars.ContextVar(
    "cef_audit_request", default=None
)
_user: contextvars.ContextVar = contextvars.ContextVar("cef_audit_user", default=None)
_conversation_id: contextvars.ContextVar = contextvars.ContextVar(
    "cef_audit_conversation_id", default=None
)
# Для Socket.IO: src/shost/spt/app без Starlette Request (см. request_context в cef_logger).
_socket_remote: contextvars.ContextVar = contextvars.ContextVar(
    "cef_audit_socket_remote", default=None
)

# Модель, которой отвечает текущий чат: из карточки агента, если агент
# выбран, иначе выбранная в чате. Нужна не аудиту, а RAG: judge и
# суммаризация в SVC-RAG должны идти в ту же модель, а не в дефолтную.
#
# Контекстом, а не аргументом: RAG-поиск запускается из десятка мест, и
# протаскивать модель через все сигнатуры - большая правка с большим
# шансом что-то забыть.
_chat_model: contextvars.ContextVar = contextvars.ContextVar(
    "chat_model_path", default=None
)


def chat_model_set(model_path: Optional[str]) -> None:
    _chat_model.set(str(model_path or "").strip() or None)


def chat_model_peek() -> Optional[str]:
    return _chat_model.get()


def cef_audit_set(
    *,
    request: Any = None,
    user: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    socket_remote: Any = _UNSET,
) -> _Tokens:
    """Сохранить контекст; вернуть токены для cef_audit_reset.

    ``socket_remote``: словарь ``{\"src\", \"shost\", \"spt\", \"app\"}`` с адресом клиента
    при работе через Socket.IO. Если передать ``None`` — сбросить подсказку (например REST).
    Если параметр не передавать — предыдущее значение ContextVar не меняется.
    """
    pairs: List[Tuple[contextvars.ContextVar, contextvars.Token]] = []
    if request is not None:
        pairs.append((_request, _request.set(request)))
    if user is not None:
        pairs.append((_user, _user.set(user)))
    if conversation_id is not None:
        pairs.append((_conversation_id, _conversation_id.set(conversation_id)))
    if socket_remote is not _UNSET:
        pairs.append((_socket_remote, _socket_remote.set(socket_remote)))
    return _Tokens(tuple(pairs))


def cef_audit_reset(tokens: _Tokens) -> None:
    for var, tok in reversed(tokens.pairs):
        var.reset(tok)


def cef_audit_peek() -> tuple[Optional[Any], Optional[Dict[str, Any]], Optional[str]]:
    return _request.get(), _user.get(), _conversation_id.get()


# Логин для запросов, которые делает сам сервис, а не пользователь: список
# моделей и health-check при старте пода. Согласовано с ИБ.
MODELS_SYSTEM_USER_ID = "astrachat"


def model_identity_headers() -> Dict[str, str]:
    """X-User-ID для запросов к моделям через шлюз."""
    login = ""
    try:
        _req, user, _conv = cef_audit_peek()
        if user:
            login = str(user.get("username") or user.get("user_id") or "").strip()
    except Exception:
        login = ""
    if not login or login.upper() == "SYSTEM":
        login = (
            os.getenv("MODELS_SYSTEM_USER_ID", "") or MODELS_SYSTEM_USER_ID
        ).strip() or MODELS_SYSTEM_USER_ID
    return {"X-User-ID": login}


def cef_audit_peek_socket_remote() -> Optional[Dict[str, Any]]:
    return _socket_remote.get()


def _first_forwarded_ip(value: Optional[str]) -> str:
    return (value or "").split(",")[0].strip()


def _is_loopback_ip(ip: str) -> bool:
    if not ip:
        return True
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def resolve_client_src(
    *,
    x_real_ip: Optional[str] = None,
    x_forwarded_for: Optional[str] = None,
    direct_peer: Optional[str] = None,
) -> str:
    """IPv4 конечного клиента для CEF (поле src).

    Аналог ``getSrcIp``: X-Real-IP → X-Forwarded-For → peer.
    Дополнительно: первый не-loopback в XFF (типичный K8s ingress).
    """
    xri = _first_forwarded_ip(x_real_ip)
    if xri and not _is_loopback_ip(xri):
        return xri

    for part in (x_forwarded_for or "").split(","):
        ip = part.strip()
        if ip and not _is_loopback_ip(ip):
            return ip

    if xri:
        return xri

    for part in (x_forwarded_for or "").split(","):
        ip = part.strip()
        if ip:
            return ip

    peer = (direct_peer or "").strip()
    if peer:
        return peer
    return "127.0.0.1"


def resolve_client_src_from_request(request: Any) -> str:
    """src из Starlette Request (заголовки + client.host)."""
    if request is None:
        _cef_shost_log("resolve_client_src_from_request: request=None -> 127.0.0.1")
        return "127.0.0.1"
    peer = getattr(request.client, "host", None) if request.client else None
    xri = request.headers.get("x-real-ip")
    xff = request.headers.get("x-forwarded-for")
    _cef_shost_log(
        "resolve_client_src_from_request: path=%r peer=%r x-real-ip=%r x-forwarded-for=%r x-client-hostname=%r",
        getattr(getattr(request, "url", None), "path", "-"),
        peer,
        xri,
        xff,
        request.headers.get("x-client-hostname"),
    )
    src = resolve_client_src(
        x_real_ip=xri,
        x_forwarded_for=xff,
        direct_peer=peer,
    )
    _cef_shost_log("resolve_client_src_from_request: -> src=%r", src)
    return src


def shost_override_from_headers(
    *,
    x_client_hostname: Optional[str] = None,
) -> str:
    """Явный shost (payload.shost || x-client-hostname)."""
    return (x_client_hostname or "").strip()


def _reverse_dns_enabled() -> bool:
    return os.getenv("CEF_REVERSE_DNS", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _ldap_url_for_dns() -> str:
    for key in ("LDAP_URL", "LDAP_SERVER"):
        raw = os.getenv(key, "").strip()
        if raw:
            return raw
    try:
        from backend.auth.ldap_auth import _get_ldap_settings

        url = (_get_ldap_settings().get("url") or "").strip()
        if url:
            return url
    except Exception:
        pass
    return ""


def _nameservers_from_resolv_conf() -> List[str]:
    path = os.getenv("CEF_RESOLV_CONF", "/etc/resolv.conf")
    out: List[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0].lower() == "nameserver":
                    ns = parts[1].strip()
                    if ns and ns not in ("127.0.0.1", "::1") and ns not in out:
                        out.append(ns)
    except OSError:
        pass
    return out


def _cef_ptr_nameservers() -> List[str]:
    """DNS для PTR, если системный resolver pod (kube-dns) не знает рабочие станции."""
    ordered: List[str] = []
    raw = os.getenv("CEF_DNS_SERVERS", "").strip()
    if raw:
        for part in raw.split(","):
            ns = part.strip()
            if ns and ns not in ordered:
                ordered.append(ns)
    if os.getenv("CEF_DNS_FROM_LDAP", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    ):
        ldap_raw = _ldap_url_for_dns()
        if ldap_raw:
            host = urlparse(
                ldap_raw if "://" in ldap_raw else f"ldap://{ldap_raw}"
            ).hostname
            if host:
                try:
                    for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
                        addr = info[4][0]
                        if addr and addr not in ordered:
                            ordered.append(addr)
                except OSError as exc:
                    _cef_dns_logger.debug("LDAP host %s for PTR: %s", host, exc)
    for ns in _nameservers_from_resolv_conf():
        if ns not in ordered:
            ordered.append(ns)
    return ordered


def _ipv4_ptr_qname(ip: str) -> str:
    """Имя PTR-запроса (in-addr.arpa), как при dns.reverse"""
    try:
        import dns.reversename

        return str(dns.reversename.from_address(ip)).rstrip(".")
    except Exception:
        pass
    parts = (ip or "").strip().split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return ".".join(reversed(parts)) + ".in-addr.arpa"
    return ip


def _ptr_system(ip: str) -> Optional[str]:
    """Аналог ``dns.reverse(ip)`` — системный resolver (gethostbyaddr)."""
    ptr_q = _ipv4_ptr_qname(ip)
    _cef_shost_log(
        "shost dns.reverse: ip=%s ptr=%s resolver=system (gethostbyaddr)",
        ip,
        ptr_q,
    )
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
        name = (hostname or "").strip().rstrip(".")
        if name and name != ip:
            _cef_shost_log(
                "shost dns.reverse OK: ip=%s ptr=%s -> shost=%s", ip, ptr_q, name
            )
            return name
        _cef_shost_log(
            "shost dns.reverse empty: ip=%s ptr=%s hostname=%r (ignored)",
            ip,
            ptr_q,
            hostname,
        )
    except OSError as exc:
        _cef_shost_log("shost dns.reverse FAIL: ip=%s ptr=%s error=%s", ip, ptr_q, exc)
        _cef_dns_logger.debug("gethostbyaddr(%s): %s", ip, exc)
    return None


def _ptr_dnspython(ip: str, nameservers: List[str]) -> Optional[str]:
    """Fallback PTR через dnspython, если системный dns.reverse не нашёл запись."""
    ptr_q = _ipv4_ptr_qname(ip)
    if not nameservers:
        _cef_shost_log(
            "shost dns.reverse fallback: ip=%s ptr=%s skipped (no nameservers)",
            ip,
            ptr_q,
        )
        return None
    try:
        import dns.resolver
        import dns.reversename
    except ImportError:
        _cef_shost_log(
            "shost dns.reverse fallback: ip=%s ptr=%s skipped (dnspython not installed)",
            ip,
            ptr_q,
        )
        return None

    timeout = 2.0
    try:
        timeout = max(0.5, float(os.getenv("CEF_REVERSE_DNS_TIMEOUT", "2")))
    except (TypeError, ValueError):
        pass

    rev = dns.reversename.from_address(ip)
    for ns in nameservers:
        _cef_shost_log(
            "shost dns.reverse: ip=%s ptr=%s resolver=%s timeout=%ss",
            ip,
            ptr_q,
            ns,
            timeout,
        )
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [ns]
        resolver.lifetime = timeout
        try:
            answers = resolver.resolve(rev, "PTR")
            for rdata in answers:
                name = rdata.to_text().strip().rstrip(".")
                if name and name != ip:
                    _cef_shost_log(
                        "shost dns.reverse OK: ip=%s ptr=%s resolver=%s -> shost=%s",
                        ip,
                        ptr_q,
                        ns,
                        name,
                    )
                    return name
            _cef_shost_log(
                "shost dns.reverse empty: ip=%s ptr=%s resolver=%s (no usable PTR answers)",
                ip,
                ptr_q,
                ns,
            )
        except Exception as exc:
            _cef_shost_log(
                "shost dns.reverse FAIL: ip=%s ptr=%s resolver=%s error=%s",
                ip,
                ptr_q,
                ns,
                exc,
            )
            _cef_dns_logger.debug("PTR %s via %s: %s", ip, ns, exc)
    return None


def reverse_dns(ip: str) -> str:
    """PTR для shost: системный ``dns.reverse`` (gethostbyaddr), затем корпоративный DNS."""
    if not ip or ip == "127.0.0.1":
        return "localhost"
    if not _reverse_dns_enabled():
        _cef_shost_log(
            "shost dns.reverse skipped: ip=%s (CEF_REVERSE_DNS disabled) -> shost=ip",
            ip,
        )
        return ip

    now = time.time()
    cached = _rdns_cache.get(ip)
    if cached and (now - cached[1]) < _RDNS_TTL_SEC:
        _cef_shost_log(
            "shost dns.reverse cache: ip=%s -> shost=%s (ttl=%ss)",
            ip,
            cached[0],
            _RDNS_TTL_SEC,
        )
        return cached[0]

    host = _ptr_system(ip)
    if not host:
        host = _ptr_dnspython(ip, _cef_ptr_nameservers())
    if not host:
        _cef_shost_log(
            "shost dns.reverse: ip=%s -> shost=%s (fallback: no PTR, use src)", ip, ip
        )
        host = ip

    _rdns_cache[ip] = (host, now)
    return host


def resolve_client_shost(src: str, *, shost_override: Optional[str] = None) -> str:
    """DNS-имя источника для CEF (поле shost)."""
    override = (shost_override or "").strip()
    if override:
        _cef_shost_log(
            "shost resolve: src=%s -> shost=%s (x-client-hostname override, no dns.reverse)",
            src,
            override,
        )
        return override
    ip = (src or "").strip() or "127.0.0.1"
    _cef_shost_log("shost resolve: src=%s -> dns.reverse(...)", ip)
    resolved = reverse_dns(ip)
    _cef_shost_log("shost resolve: src=%s -> shost=%s", ip, resolved)
    return resolved


def cef_socket_remote_from_environ(
    environ: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Извлекает IP/порт клиента из WSGI-подобного environ handshake Socket.IO (ASGI внутри engine.io)."""
    if not isinstance(environ, dict):
        return None

    scope = environ.get("asgi.scope")
    if not isinstance(scope, dict):
        scope = None

    def _top_hdr(key_upper: str) -> str:
        v = environ.get(key_upper)
        return str(v).strip() if v else ""

    hmap: Dict[str, str] = {}
    if scope:
        for item in scope.get("headers") or []:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            k, v = item[0], item[1]
            if isinstance(k, (bytes, bytearray)) and isinstance(v, (bytes, bytearray)):
                hmap[k.decode("latin-1").lower()] = v.decode(
                    "latin-1", errors="replace"
                )

    xff = (
        hmap.get("x-forwarded-for") or _top_hdr("HTTP_X_FORWARDED_FOR") or ""
    ).strip() or None
    xri = (hmap.get("x-real-ip") or _top_hdr("HTTP_X_REAL_IP") or "").strip() or None
    rem = _top_hdr("REMOTE_ADDR") or None
    shost_ov = shost_override_from_headers(
        x_client_hostname=hmap.get("x-client-hostname")
        or _top_hdr("HTTP_X_CLIENT_HOSTNAME")
        or None,
    )
    _cef_shost_log(
        "cef_socket_remote_from_environ: x-real-ip=%r x-forwarded-for=%r remote_addr=%r "
        "scope.client=%r x-client-hostname=%r x-forwarded-proto=%r",
        xri,
        xff,
        rem,
        scope.get("client") if scope else None,
        shost_ov or None,
        _top_hdr("HTTP_X_FORWARDED_PROTO") or None,
    )
    # shost пересчитывается в request_context по src; здесь не кэшируем устаревший PTR

    direct_peer = rem
    if scope:
        client = scope.get("client")
        if isinstance(client, (list, tuple)) and len(client) >= 1 and client[0]:
            direct_peer = str(client[0])

    src = resolve_client_src(
        x_real_ip=xri, x_forwarded_for=xff, direct_peer=direct_peer
    )
    _cef_shost_log(
        "cef_socket_remote_from_environ: resolve_client_src -> src=%r (direct_peer=%r)",
        src,
        direct_peer,
    )

    spt = 0
    if scope:
        client = scope.get("client")
        if isinstance(client, (list, tuple)) and len(client) >= 2 and client[1]:
            try:
                spt = int(client[1])
            except (TypeError, ValueError):
                spt = 0

    if not src:
        return None

    rp = environ.get("REMOTE_PORT")
    if rp and not spt:
        try:
            spt = int(rp)
        except (TypeError, ValueError):
            pass

    scheme = _top_hdr("HTTP_X_FORWARDED_PROTO").lower()
    if not scheme:
        scheme = str(environ.get("wsgi.url_scheme") or "").lower()
    if not scheme and scope:
        scheme = str(scope.get("scheme") or "https").lower()
    if not scheme:
        scheme = "https"

    app = "HTTPS" if scheme.startswith("https") else "HTTP"
    remote: Dict[str, Any] = {
        "src": src,
        "spt": int(spt or 0),
        "app": app,
    }
    if shost_ov:
        remote["shost_override"] = shost_ov
    _cef_shost_log("cef_socket_remote_from_environ: out=%r", remote)
    return remote


def log_request_context_shost(
    *,
    channel: str,
    ctx: Dict[str, Any],
    shost_override: Optional[str] = None,
    sock_hint: Optional[Dict[str, Any]] = None,
    request_path: Optional[str] = None,
) -> None:
    """Итоговая трассировка shost перед записью CEF-события."""
    _cef_shost_log(
        "request_context[%s]: path=%r sock_hint=%r shost_override=%r -> "
        "src=%r shost=%r spt=%r app=%r (DOMAIN_SERVER=%r, not used for shost)",
        channel,
        request_path,
        sock_hint,
        shost_override or None,
        ctx.get("src"),
        ctx.get("shost"),
        ctx.get("spt"),
        ctx.get("app"),
        os.getenv("DOMAIN_SERVER"),
    )
