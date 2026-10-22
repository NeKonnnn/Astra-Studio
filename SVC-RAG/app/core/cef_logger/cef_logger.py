"""CEF-аудит SVC-RAG (stdout + UDP syslog) — по аналогии с backend.settings.cef_logger."""

from __future__ import annotations

import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("cef")

_cef_logging_configured = False


def configure_cef_logging() -> None:
    """Stdout-логгер CEF: только сырая строка ``CEF:0|...`` без формата app."""
    global _cef_logging_configured
    if _cef_logging_configured:
        return
    import sys

    _cef_logger = logging.getLogger("cef")
    _cef_logger.setLevel(logging.INFO)
    _cef_logger.propagate = False
    if not _cef_logger.handlers:
        _cef_handler = logging.StreamHandler(sys.stdout)
        _cef_handler.setLevel(logging.INFO)
        _cef_handler.setFormatter(logging.Formatter("%(message)s"))
        _cef_logger.addHandler(_cef_handler)
    _cef_logging_configured = True


_SYSLOG_UDP_SOCK: Optional[socket.socket] = None
_SYSLOG_FACILITY_CODES = {
    "KERN": 0,
    "USER": 1,
    "MAIL": 2,
    "DAEMON": 3,
    "AUTH": 4,
    "SYSLOG": 5,
    "LPR": 6,
    "NEWS": 7,
    "UUCP": 8,
    "CRON": 9,
    "AUTHPRIV": 10,
    "FTP": 11,
    "LOCAL0": 16,
    "LOCAL1": 17,
    "LOCAL2": 18,
    "LOCAL3": 19,
    "LOCAL4": 20,
    "LOCAL5": 21,
    "LOCAL6": 22,
    "LOCAL7": 23,
}


def _cef_severity_to_syslog_pri_severity(cef_severity: int) -> int:
    if cef_severity <= 3:
        return 6
    if cef_severity <= 5:
        return 5
    if cef_severity <= 7:
        return 4
    return 3


def resolve_cef_device_version() -> str:
    return (os.getenv("CEF_DEVICE_VERSION") or "").strip() or "0.0.0"


def resolve_cef_dvchost() -> str:
    explicit = (os.getenv("CEF_DVCHOST") or "").strip()
    if explicit:
        return explicit
    for key in ("MY_POD_NAME", "POD_NAME", "HOSTNAME"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return socket.getfqdn() or socket.gethostname() or "-"


def _build_rfc5424_syslog_payload(cef_line: str, cef_severity: int) -> bytes:
    fac_name = (os.getenv("AUDIT_SYSLOG_FACILITY") or "LOCAL4").upper()
    facility_code = _SYSLOG_FACILITY_CODES.get(
        fac_name, _SYSLOG_FACILITY_CODES["LOCAL4"]
    )
    pri = facility_code * 8 + _cef_severity_to_syslog_pri_severity(int(cef_severity))
    ts = (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    hostname = resolve_cef_dvchost()
    app_name = os.getenv("CEF_DEVICE_PROCESS", "svc-rag")
    proc_id = os.getpid()
    line = f"<{pri}>1 {ts} {hostname} {app_name} {proc_id} - - {cef_line}"
    return line.encode("utf-8")


def _emit_cef_syslog_udp(cef_line: str, cef_severity: int) -> None:
    if os.getenv("AUDIT_CEF_ENABLED", "").lower() != "true":
        return
    target = (os.getenv("AUDIT_SYSLOG_TARGET") or "").strip()
    if not target:
        return
    try:
        port = int(os.getenv("AUDIT_SYSLOG_PORT") or "514")
    except ValueError:
        port = 514
    payload = _build_rfc5424_syslog_payload(cef_line, cef_severity)
    global _SYSLOG_UDP_SOCK
    try:
        if _SYSLOG_UDP_SOCK is None:
            _SYSLOG_UDP_SOCK = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _SYSLOG_UDP_SOCK.sendto(payload, (target, port))
    except OSError as exc:
        logger.warning("CEF syslog UDP send to %s:%s failed: %s", target, port, exc)


@dataclass(frozen=True)
class CEFEventSpec:
    event_id: str
    name: str
    severity: int
    cat: str
    act: str
    outcome: str
    msg_template: str
    device_direction: int = 0


EVENTS: Dict[str, CEFEventSpec] = {
    "SYS001": CEFEventSpec(
        "SYS001",
        "Application Started",
        3,
        "/System/Startup",
        "start",
        "success",
        "Сервис SVC-RAG стартовал успешно, API доступен [{cat}]",
    ),
    "SYS002": CEFEventSpec(
        "SYS002",
        "Application Stopped",
        3,
        "/System/Shutdown",
        "stop",
        "success",
        "Сервис SVC-RAG остановлен (завершение работы экземпляра) [{cat}]",
    ),
    "OBJ001": CEFEventSpec(
        "OBJ001",
        "API Call Success",
        0,
        "/Object/API/Request",
        "request",
        "success",
        "Обработка запроса '{methodName}' сервиса {serviceName} выполнена успешно ({requestUuid})",
    ),
    "OBJ002": CEFEventSpec(
        "OBJ002",
        "API Call Failed",
        4,
        "/Object/API/Request",
        "request",
        "failure",
        "Ошибка '{codeStatus} {textStatus}' обработки запроса '{methodName}' сервиса {serviceName} ({requestUuid})",
    ),
    "FS001": CEFEventSpec(
        "FS001",
        "File Read",
        1,
        "/Storage/File/Read",
        "read",
        "success",
        "Чтение файла '{file}' из бакета '{bucket}'",
    ),
    "FS002": CEFEventSpec(
        "FS002",
        "File Read Failed",
        4,
        "/Storage/File/Read",
        "read",
        "failure",
        "Ошибка чтения файла '{file}' из бакета '{bucket}'",
    ),
    "INT003": CEFEventSpec(
        "INT003",
        "LLM Model Called",
        3,
        "/Integration/LLM/Request",
        "request",
        "success",
        "Пользователь {suser} инициировал запрос к модели {cs1} с помощью провайдера {cs3} [{cat}]",
        1,
    ),
    "INT005": CEFEventSpec(
        "INT005",
        "API Call Success",
        1,
        "/Object/API/Request",
        "request",
        "success",
        "Обработка запроса '{methodName}' сервиса {serviceName} выполнена успешно ({requestUuid})",
        1,
    ),
    "INT006": CEFEventSpec(
        "INT006",
        "API Call Failed",
        4,
        "/Object/API/Request",
        "request",
        "failure",
        "Ошибка '{codeStatus} {textStatus}' обработки запроса '{methodName}' сервиса {serviceName} ({requestUuid})",
        1,
    ),
}


_EXTENSION_KEY_ORDER: List[str] = [
    "externalId",
    "deviceProcessName",
    "cat",
    "shost",
    "src",
    "sntdom",
    "spt",
    "dhost",
    "dst",
    "dpt",
    "duser",
    "dntdom",
    "app",
    "act",
    "outcome",
    "suser",
    "suid",
    "start",
    "end",
    "rt",
    "dvchost",
    "msg",
    "deviceDirection",
    "cs1",
    "cs1Label",
    "cs2",
    "cs2Label",
    "cs3",
    "cs3Label",
    "cs4",
    "cs4Label",
    "cn1",
    "cn1Label",
    "fname",
    "fsize",
    "request",
    "reason",
    "methodName",
    "serviceName",
    "requestUuid",
    "codeStatus",
    "textStatus",
    "file",
    "bucket",
]


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "-"


def _esc(val: Any) -> str:
    text = str(val if val is not None else "-")
    return text.replace("\\", "\\\\").replace("=", r"\=").replace("\n", r"\n")


def _outcome_from_status(status_code: Optional[int]) -> str:
    if status_code is None:
        return "success"
    if 200 <= status_code < 300:
        return "success"
    if 400 <= status_code < 500:
        return "failure"
    if status_code >= 500:
        return "error"
    return "success"


def domain_from_ldap_base_dn(base_dn: str) -> str:
    parts = []
    for chunk in (base_dn or "").split(","):
        piece = chunk.strip()
        if piece.lower().startswith("dc="):
            parts.append(piece[3:])
    return ".".join(parts)


def request_context(request: Any, current_user: Optional[dict]) -> Dict[str, Any]:
    from app.core.cef_logger.cef_audit_context import resolve_client_src

    src = resolve_client_src(request)
    suser = "SYSTEM"
    sntdom = domain_from_ldap_base_dn(os.getenv("LDAP_USER_SEARCH_BASE", "")) or "-"
    spt = 0
    app = "HTTPS"
    if current_user:
        suser = str(
            current_user.get("username") or current_user.get("user_id") or "SYSTEM"
        )
        if current_user.get("sntdom"):
            sntdom = str(current_user["sntdom"])
    if request is not None:
        client = getattr(request, "client", None)
        if client and getattr(client, "port", None) is not None:
            try:
                spt = int(client.port)
            except (TypeError, ValueError):
                spt = 0
        headers = getattr(request, "headers", None) or {}
        xf_proto = (headers.get("x-forwarded-proto") or "").strip().lower()
        if xf_proto in ("http", "https"):
            app = xf_proto.upper()
        elif getattr(request, "url", None) is not None:
            scheme = str(getattr(request.url, "scheme", "") or "").lower()
            if scheme in ("http", "https"):
                app = scheme.upper()
    return {
        "src": src,
        "shost": src,
        "spt": spt,
        "app": app,
        "suser": suser,
        "sntdom": sntdom or "-",
    }


def cef_outbound_extra(
    *,
    base_url: str,
    method_name: str,
    service_name: str,
    request_uuid: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    parsed = urlparse(base_url)
    dhost = parsed.hostname or base_url
    if parsed.port:
        dpt = int(parsed.port)
    elif (parsed.scheme or "").lower() == "https":
        dpt = 443
    else:
        dpt = 80
    extra: Dict[str, Any] = {
        "_deviceDirection": 1,
        "dhost": dhost,
        "dpt": dpt,
        "methodName": method_name,
        "serviceName": service_name,
        "requestUuid": request_uuid,
        "request": f"{base_url.rstrip('/')}/{method_name.lstrip('/')}",
    }
    if model:
        extra["cs1"] = model
        extra["cs1Label"] = "LLMModel"
    if provider:
        extra["cs3"] = provider
        extra["cs3Label"] = "LLMProvider"
    return extra


def log_cef_int003_model_request(
    *,
    base_url: str,
    model: str,
    provider: str,
    method_name: str = "POST /v1/embeddings",
) -> None:
    rid = uuid.uuid4().hex
    log_cef_event(
        "INT003",
        status_code=200,
        extra=cef_outbound_extra(
            base_url=base_url,
            method_name=method_name,
            service_name=provider or "rag-models",
            request_uuid=rid,
            model=model,
            provider=provider,
        ),
    )


def log_cef_int006_outbound_failure(
    *,
    base_url: str,
    model: str,
    provider: str,
    method_name: str,
    status_code: Optional[int],
    text_status: str,
) -> None:
    rid = uuid.uuid4().hex
    extra = cef_outbound_extra(
        base_url=base_url,
        method_name=method_name,
        service_name=provider or "rag-models",
        request_uuid=rid,
        model=model,
        provider=provider,
    )
    extra["codeStatus"] = str(status_code if status_code is not None else "EXCEPTION")
    extra["textStatus"] = (text_status or "")[:512]
    log_cef_event("INT006", status_code=status_code, extra=extra)


def _extension_ordered_items(extension: Dict[str, Any]) -> List[Tuple[str, Any]]:
    seen: set[str] = set()
    out: List[Tuple[str, Any]] = []
    for key in _EXTENSION_KEY_ORDER:
        if key not in extension:
            continue
        val = extension[key]
        if val is None:
            continue
        out.append((key, val))
        seen.add(key)
    for key in sorted(extension.keys()):
        if key in seen or key.startswith("_"):
            continue
        val = extension[key]
        if val is None:
            continue
        out.append((key, val))
    return out


def log_cef_event(
    event_id: str,
    *,
    request: Any = None,
    current_user: Optional[dict] = None,
    status_code: Optional[int] = None,
    outcome: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    spec = EVENTS.get(event_id)
    if not spec:
        logger.warning("CEF event id '%s' is not configured", event_id)
        return

    try:
        from app.core.cef_logger.cef_audit_context import cef_audit_peek

        _ar, _au = cef_audit_peek()
        if request is None and _ar is not None:
            request = _ar
        if current_user is None and _au is not None:
            current_user = _au
    except Exception:
        pass

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    dhost_fqdn = os.getenv("DOMAIN_SERVER", socket.getfqdn())

    if event_id in ("SYS001", "SYS002"):
        ctx = {
            "src": "127.0.0.1",
            "shost": "localhost",
            "spt": 0,
            "app": "HTTPS",
            "suser": "SYSTEM",
            "sntdom": domain_from_ldap_base_dn(os.getenv("LDAP_USER_SEARCH_BASE", ""))
            or "-",
        }
        current_user_effective = {"username": "SYSTEM", "user_id": None}
    else:
        ctx = request_context(request, current_user)
        current_user_effective = current_user or {
            "username": ctx["suser"],
            "user_id": None,
        }

    extra = dict(extra) if extra else {}
    _extra_dir = extra.pop("_deviceDirection", None)
    effective_dir = int(_extra_dir) if _extra_dir is not None else spec.device_direction

    extension: Dict[str, Any] = {
        "externalId": uuid.uuid4().hex,
        "deviceProcessName": os.getenv("CEF_DEVICE_PROCESS", "svc-rag"),
        "cat": spec.cat,
        "shost": ctx["shost"],
        "src": ctx["src"],
        "sntdom": ctx["sntdom"],
        "spt": ctx["spt"],
        "dhost": dhost_fqdn,
        "dpt": int(os.getenv("CEF_DPT", "443")),
        "app": ctx["app"],
        "act": spec.act,
        # Без status_code (сбой вне HTTP) берём outcome из спецификации события,
        # иначе INT006/OBJ002 уходили в SIEM как success (BUG-RAG-22).
        "outcome": outcome
        or (_outcome_from_status(status_code) if status_code is not None else None)
        or spec.outcome,
        "suser": ctx["suser"],
        "start": now_ms,
        "end": now_ms,
        "rt": datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "dvchost": resolve_cef_dvchost(),
        "deviceDirection": effective_dir,
    }

    uid = (current_user_effective or {}).get("user_id")
    if uid and event_id not in ("SYS001", "SYS002"):
        extension["suid"] = str(uid)

    if effective_dir == 1:
        for key in ("dst", "duser", "dntdom", "dhost"):
            if extra.get(key):
                extension[key] = extra.get(key)
        if extra.get("dpt") is not None:
            extension["dpt"] = int(extra["dpt"])

    extension.update(
        {k: v for k, v in extra.items() if v is not None and not str(k).startswith("_")}
    )

    fmt_map = _SafeDict({**extension, "cat": spec.cat})
    extension["msg"] = spec.msg_template.format_map(fmt_map)

    prefix = "CEF:0|{vendor}|{product}|{version}|{event_id}|{name}|{severity}|".format(
        vendor=os.getenv("CEF_DEVICE_VENDOR", "CORSUR"),
        product=os.getenv("CEF_DEVICE_PRODUCT", "SVC-RAG"),
        version=resolve_cef_device_version(),
        event_id=spec.event_id,
        name=spec.name,
        severity=spec.severity,
    )
    ext_str = " ".join(f"{k}={_esc(v)}" for k, v in _extension_ordered_items(extension))
    full_cef = f"{prefix}{ext_str}"
    logger.info("%s", full_cef)
    _emit_cef_syslog_udp(full_cef, spec.severity)
