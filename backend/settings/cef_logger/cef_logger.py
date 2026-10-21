import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


logger = logging.getLogger("cef")

# Отправка в АС СВОИ по UDP: syslog RFC5424, полезная нагрузка MSG — сырая строка CEF.
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
    """Сопоставление CEF Severity → syslog severity внутри PRI (типовая шкала для аудита)."""
    if cef_severity <= 3:
        return 6
    if cef_severity <= 5:
        return 5
    if cef_severity <= 7:
        return 4
    return 3


def _build_rfc5424_syslog_payload(cef_line: str, cef_severity: int) -> bytes:
    fac_name = (os.getenv("AUDIT_SYSLOG_FACILITY") or "LOCAL4").upper()
    facility_code = _SYSLOG_FACILITY_CODES.get(fac_name, _SYSLOG_FACILITY_CODES["LOCAL4"])
    pri = facility_code * 8 + _cef_severity_to_syslog_pri_severity(int(cef_severity))
    ts = datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    hostname = resolve_cef_dvchost()
    app_name = os.getenv("CEF_DEVICE_PROCESS", "astrachat")
    proc_id = os.getpid()
    line = f"<{pri}>1 {ts} {hostname} {app_name} {proc_id} - - {cef_line}"
    return line.encode("utf-8")


def resolve_cef_device_version() -> str:
    """Версия в заголовке CEF — только ``CEF_DEVICE_VERSION`` из env/ConfigMap."""
    return (os.getenv("CEF_DEVICE_VERSION") or "").strip() or "0.0.0"


def resolve_cef_dvchost() -> str:
    """Имя хоста/pod, генерирующего событие (extension ``dvchost``, как в примерах NDP/ArcSight в CEF.md)."""
    explicit = (os.getenv("CEF_DVCHOST") or "").strip()
    if explicit:
        return explicit
    for key in ("MY_POD_NAME", "POD_NAME", "HOSTNAME"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return socket.getfqdn() or socket.gethostname() or "-"


def _emit_cef_syslog_udp(cef_line: str, cef_severity: int) -> None:
    """UDP на коллектор: AUDIT_CEF_ENABLED=true и задан AUDIT_SYSLOG_TARGET."""
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
        "Приложение AstraChat запущено, API доступен по адресу https://{dhost} [{cat}]",
    ),
    "SYS002": CEFEventSpec(
        "SYS002",
        "Application Stopped",
        3,
        "/System/Shutdown",
        "stop",
        "success",
        "Приложение AstraChat остановлено (завершение работы экземпляра) [{cat}]",
    ),
    "FS001": CEFEventSpec(
        "FS001",
        "MinIO File Read",
        1,
        "/Storage/MinIO/Read",
        "read",
        "success",
        "Чтение файла '{file}' из бакета '{bucket}'",
    ),
    "FS002": CEFEventSpec(
        "FS002",
        "MinIO File Read Failed",
        4,
        "/Storage/MinIO/Read",
        "read",
        "failure",
        "Ошибка чтения файла '{file}' из бакета '{bucket}'",
    ),
    "FS003": CEFEventSpec(
        "FS003",
        "MinIO File Write",
        1,
        "/Storage/MinIO/Write",
        "write",
        "success",
        "Сохранение файла '{file}' в бакет '{bucket}'",
    ),
    "FS004": CEFEventSpec(
        "FS004",
        "MinIO File Write Failed",
        4,
        "/Storage/MinIO/Write",
        "write",
        "failure",
        "Ошибка сохранения файла '{file}' в бакет '{bucket}'",
    ),
    "FS005": CEFEventSpec(
        "FS005",
        "File Uploaded",
        3,
        "/Object/File/Upload",
        "upload",
        "success",
        "Файл {fname} ({fsize} байт) загружен пользователем {suser} [{cat}]",
    ),
    "FS006": CEFEventSpec(
        "FS006",
        "File Deleted",
        4,
        "/Object/File/Delete",
        "delete",
        "success",
        "Файл {fname} ({fsize} байт) удалён пользователем {suser} [{cat}]",
    ),
    "FS007": CEFEventSpec(
        "FS007",
        "File Inline Extract",
        2,
        "/Object/File/InlineExtract",
        "read",
        "success",
        "Файл {fname} ({fsize} байт, тип: {cs1}) извлечён напрямую (без эмбединга) пользователем {suser} [{cat}]",
    ),
    "FS008": CEFEventSpec(
        "FS008",
        "File Inline Attach Rejected",
        4,
        "/Object/File/InlineAttach",
        "upload",
        "failure",
        "Попытка прикрепить файл {fname} ({fsize} байт) к сообщению отклонена пользователем {suser}. "
        "Причина: {reason} [{cat}]",
    ),
    "FS009": CEFEventSpec(
        "FS009",
        "Rag Pvc File Write",
        2,
        "/Storage/Pvc/Rag/Write",
        "write",
        "success",
        "Файл {fname} ({fsize} байт, scope: {cs1}) сохранён в PVC RAG "
        "({cs2}) пользователем {suser} [{cat}]",
    ),
    "FS010": CEFEventSpec(
        "FS010",
        "Rag Pvc File Write Failed",
        4,
        "/Storage/Pvc/Rag/Write",
        "write",
        "failure",
        "Ошибка сохранения файла {fname} ({fsize} байт, scope: {cs1}) в PVC RAG "
        "пользователем {suser}. Причина: {reason} [{cat}]",
    ),
    "FS011": CEFEventSpec(
        "FS011",
        "Rag Pvc File Delete",
        3,
        "/Storage/Pvc/Rag/Delete",
        "delete",
        "success",
        "Файл {fname} (scope: {cs1}) удалён из PVC RAG ({cs2}) "
        "пользователем {suser} [{cat}]",
    ),
    "FS012": CEFEventSpec(
        "FS012",
        "Rag Pvc File Delete Failed",
        4,
        "/Storage/Pvc/Rag/Delete",
        "delete",
        "failure",
        "Ошибка удаления файла {fname} (scope: {cs1}) из PVC RAG "
        "пользователем {suser}. Причина: {reason} [{cat}]",
    ),
    "SEC001": CEFEventSpec(
        "SEC001",
        "User Login Success",
        3,
        "/Authentication/Login",
        "login",
        "success",
        "Пользователь {suser} аутентифицирован с адреса {src} через LDAP-сервер {cs1}, ТУЗ {cs2}. "
        "Ролевая модель: {cs4} ({cs3}) [{cat}]",
    ),
    "SEC002": CEFEventSpec(
        "SEC002",
        "User Logout",
        3,
        "/Authentication/Logout",
        "logout",
        "success",
        "Пользователь {suser} завершил сеанс [{cat}]",
    ),
    "SEC003": CEFEventSpec(
        "SEC003",
        "User Login Failed",
        5,
        "/Authentication/Login",
        "login",
        "failure",
        "Неудачная попытка аутентификации УЗ {suser} с адреса {src}. Причина: {reason} [{cat}]",
    ),
    "SEC004": CEFEventSpec(
        "SEC004",
        "Agent Access Changed",
        5,
        "/Authorization/Agent/AccessChange",
        "modify",
        "success",
        "Пользователь {suser} изменил права доступа к агенту {cs1} (ID: {cs2}): пользователю {duser} назначена роль «{cs3}» [{cat}]",
    ),
    "SEC005": CEFEventSpec(
        "SEC005",
        "Admin Settings Changed",
        5,
        "/Authorization/AdminSettings/Modify",
        "modify",
        "success",
        "Администратор {suser} изменил глобальные разрешения раздела «{cs1}» для роли {cs2}: {cs3} [{cat}]",
    ),
    "SEC006": CEFEventSpec(
        "SEC006",
        "Session Invalidated",
        5,
        "/Authentication/SessionInvalidated",
        "invalidate",
        "success",
        "Принудительная инвалидация {cn1} предыдущих сессий пользователя {suser}. "
        "Причина: вход из другого браузера/устройства (single-session enforcement) [{cat}]",
    ),
    "SEC007": CEFEventSpec(
        "SEC007",
        "Token Validation Failed",
        5,
        "/Authentication/TokenValidate",
        "validate",
        "failure",
        "Неуспешная проверка JWT ({cs1}): {reason} [{cat}]",
    ),
    "USR001": CEFEventSpec(
        "USR001",
        "User Account Created",
        4,
        "/UserManagement/Create",
        "create",
        "success",
        "УЗ пользователя {duser} создана при первом входе через LDAP-сервер {cs1}. Ролевая модель: {cs4} ({cs3}) [{cat}]",
    ),
    "USR002": CEFEventSpec(
        "USR002",
        "User Account Deleted",
        7,
        "/UserManagement/Delete",
        "delete",
        "success",
        "УЗ пользователя {duser} ({dmail}) удалена пользователем {suser} [{cat}]",
    ),
    "AGT001": CEFEventSpec(
        "AGT001",
        "Agent Created",
        3,
        "/Object/Agent/Create",
        "create",
        "success",
        "Агент {cs1} (ID: {cs2}) создан пользователем {suser} [{cat}]",
    ),
    "AGT002": CEFEventSpec(
        "AGT002",
        "Agent Config Changed",
        4,
        "/Object/Agent/Modify",
        "modify",
        "success",
        "Конфигурация агента {cs1} (ID: {cs2}) изменена пользователем {suser} [{cat}]",
    ),
    "AGT003": CEFEventSpec(
        "AGT003",
        "Agent Duplicated",
        3,
        "/Object/Agent/Copy",
        "copy",
        "success",
        "Агент {cs1} скопирован в {agt_copy_target} пользователем {suser} [{cat}]",
    ),
    "AGT004": CEFEventSpec(
        "AGT004",
        "Agent Version Rollback",
        5,
        "/Object/Agent/Rollback",
        "rollback",
        "success",
        "Агент {cs1} (ID: {cs2}) откачен к версии {cn1} пользователем {suser} [{cat}]",
    ),
    "AGT005": CEFEventSpec(
        "AGT005",
        "Agent Deleted",
        5,
        "/Object/Agent/Delete",
        "delete",
        "success",
        "Агент {cs1} (ID: {cs2}) удалён пользователем {suser} [{cat}]",
    ),
    "CNV001": CEFEventSpec(
        "CNV001",
        "Conversation Renamed",
        1,
        "/Object/Conversation/Rename",
        "rename",
        "success",
        "Диалог {cs2} переименован пользователем {suser} [{cat}]",
    ),
    "CNV002": CEFEventSpec(
        "CNV002",
        "Conversation Archived",
        1,
        "/Object/Conversation/Archive",
        "archive",
        "success",
        "Диалог {cs2} архивирован пользователем {suser} [{cat}]",
    ),
    "CNV003": CEFEventSpec(
        "CNV003",
        "Conversation Duplicated",
        1,
        "/Object/Conversation/Copy",
        "copy",
        "success",
        "Диалог {cs2} скопирован пользователем {suser} [{cat}]",
    ),
    "CNV004": CEFEventSpec(
        "CNV004",
        "Conversation Deleted",
        3,
        "/Object/Conversation/Delete",
        "delete",
        "success",
        "Диалог {cs2} удалён пользователем {suser} [{cat}]",
    ),
    "CNV005": CEFEventSpec(
        "CNV005",
        "All Conversations Deleted",
        7,
        "/Object/Conversation/Purge",
        "purge",
        "success",
        "Все диалоги пользователя {suser} удалены [{cat}]",
    ),
    "INT001": CEFEventSpec(
        "INT001",
        "MCP Tool Invoked",
        3,
        "/Integration/MCP/Invoke",
        "execute",
        "success",
        "Пользователь {suser} вызвал MCP-сервер {cs3}, инструмент {cs1}, диалог {cs2}{cef_tail} [{cat}]",
        1,
    ),
    "INT002": CEFEventSpec(
        "INT002",
        "MCP Tool Completed",
        3,
        "/Integration/MCP/Complete",
        "complete",
        "success",
        "MCP-сервер {cs3}, инструмент {cs1} завершил работу. Длительность: {cn1} мс. Статус: {outcome}{cef_tail} [{cat}]",
        1,
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
    "INT004": CEFEventSpec(
        "INT004",
        "Agent REST API Call",
        3,
        "/Integration/Agent/Request",
        "request",
        "success",
        "Пользователь {suser} обратился к агенту {cs1} (ID: {cs2}) с адреса {src} [{cat}]",
    ),
    "INT005": CEFEventSpec(
        "INT005",
        "API Call Success",
        1,
        "/Object/API/Request",
        "request",
        "success",
        "Обработка запроса '{methodName}' сервиса {serviceName} выполнена успешно ({requestUuid})",
    ),
    "INT006": CEFEventSpec(
        "INT006",
        "API Call Failed",
        4,
        "/Object/API/Request",
        "request",
        "failure",
        "Ошибка '{codeStatus} {textStatus}' обработки запроса '{methodName}' сервиса {serviceName} ({requestUuid})",
    ),
    "INT007": CEFEventSpec(
        "INT007",
        "SVC-RAG Model Error",
        4,
        "/Object/API/Request",
        "request",
        "failure",
        "Сервис {serviceName} вернул ошибку моделей: '{codeStatus} {textStatus}' при обработке запроса '{methodName}' ({requestUuid})",
    ),
}


# Порядок полей расширения как в разделе 7 CEF.md (примеры полных сообщений)
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
    "cn2",
    "cn2Label",
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


def ldap_audit_cs_fields(user: Optional[dict]) -> Dict[str, Any]:
    """cs1–cs4 для SEC001/USR001 по CEF.md (LDAP-сервер, bind ТУЗ, группа AD, роль)."""
    if not user:
        return {}
    try:
        from urllib.parse import urlparse

        from backend.auth.ldap_auth import _get_ldap_settings

        settings = _get_ldap_settings()
        url = settings.get("url") or ""
        host = "-"
        if url:
            p = urlparse(url)
            host = p.hostname or p.netloc or "-"
        bind_dn = settings.get("bind_dn") or "-"
        admin_groups = settings.get("admin_groups") or []
        matched = admin_groups[0] if user.get("is_admin") and admin_groups else "-"
        role = "ADMIN" if user.get("is_admin") else "USER"
        return {
            "cs1": host,
            "cs1Label": "LDAPServer",
            "cs2": bind_dn,
            "cs2Label": "LDAPBindAccount",
            "cs3": matched,
            "cs3Label": "MatchedADGroup",
            "cs4": role,
            "cs4Label": "RoleModel",
        }
    except Exception:
        return {
            "cs1": "-",
            "cs1Label": "LDAPServer",
            "cs2": "-",
            "cs2Label": "LDAPBindAccount",
            "cs3": "-",
            "cs3Label": "MatchedADGroup",
            "cs4": "USER",
            "cs4Label": "RoleModel",
        }


def ldap_reason_suffix() -> str:
    """Суффикс к причине отказа SEC003, как в примере CEF.md."""
    try:
        from urllib.parse import urlparse

        from backend.auth.ldap_auth import _get_ldap_settings

        settings = _get_ldap_settings()
        url = settings.get("url") or ""
        host = "-"
        if url:
            p = urlparse(url)
            host = p.hostname or p.netloc or "-"
        bind_dn = settings.get("bind_dn") or "-"
        return f" (сервер {host}, ТУЗ {bind_dn})"
    except Exception:
        return ""


def _resolve_cef_current_user(
    request: Any,
    current_user: Optional[dict],
) -> Optional[dict]:
    if current_user:
        return current_user
    try:
        from backend.settings.cef_logger.cef_audit_context import cef_audit_peek

        _, audit_user, _ = cef_audit_peek()
        if audit_user:
            return audit_user
    except Exception:
        pass
    if request is not None:
        try:
            from backend.auth.jwt_handler import try_user_from_request

            return try_user_from_request(request)
        except Exception:
            pass
    return None


def request_context(request: Any, current_user: Optional[dict] = None) -> Dict[str, Any]:
    base_dn = os.getenv("LDAP_USER_SEARCH_BASE", "")
    sntdom = domain_from_ldap_base_dn(base_dn) or (base_dn or "-")
    current_user = _resolve_cef_current_user(request, current_user)

    sock_hint: Optional[Dict[str, Any]] = None
    resolve_client_src_from_request_fn = None
    resolve_client_shost_fn = None
    shost_override_from_headers_fn = None
    try:
        from backend.settings.cef_logger.cef_audit_context import (
            cef_audit_peek_socket_remote,
            resolve_client_shost,
            resolve_client_src_from_request,
            shost_override_from_headers,
        )

        resolve_client_src_from_request_fn = resolve_client_src_from_request
        resolve_client_shost_fn = resolve_client_shost
        shost_override_from_headers_fn = shost_override_from_headers
        sock_hint = cef_audit_peek_socket_remote()
    except Exception:
        pass

    if request is None:
        if isinstance(sock_hint, dict) and sock_hint.get("src"):
            src = str(sock_hint["src"]).strip()
            shost_ov = str(sock_hint.get("shost_override") or "").strip()
            shost = (
                resolve_client_shost_fn(src, shost_override=shost_ov or None)
                if resolve_client_shost_fn
                else (shost_ov or src)
            )
            try:
                spt = int(sock_hint.get("spt") or 0)
            except (TypeError, ValueError):
                spt = 0
            app_raw = sock_hint.get("app") or "HTTPS"
            app = "HTTPS" if str(app_raw).upper().startswith("HTTPS") else "HTTP"
            channel = "websocket"
        else:
            src = "127.0.0.1"
            shost = "localhost"
            spt = 0
            app = "HTTPS"
            shost_ov = ""
            channel = "websocket-empty"
    else:
        if resolve_client_src_from_request_fn:
            src = resolve_client_src_from_request_fn(request)
        else:
            peer = getattr(request.client, "host", None) if request.client else None
            src = (peer or "127.0.0.1").split(",")[0].strip()
        shost_ov = (
            shost_override_from_headers_fn(
                x_client_hostname=request.headers.get("x-client-hostname"),
            )
            if shost_override_from_headers_fn
            else ""
        )
        shost = (
            resolve_client_shost_fn(src, shost_override=shost_ov or None)
            if resolve_client_shost_fn
            else (shost_ov or src)
        )
        spt = int(getattr(request.client, "port", 0) or 0)
        proto = request.headers.get("x-forwarded-proto") or getattr(request.url, "scheme", "http")
        app = "HTTPS" if str(proto).lower().startswith("https") else "HTTP"
        channel = "http"

    ctx = {
        "src": src,
        "shost": shost,
        "spt": spt,
        "app": app,
        "suser": (current_user or {}).get("username", "anonymous"),
        "sntdom": sntdom,
    }
    try:
        from backend.settings.cef_logger.cef_audit_context import log_request_context_shost

        req_path = getattr(getattr(request, "url", None), "path", None) if request is not None else None
        log_request_context_shost(
            channel=channel,
            ctx=ctx,
            shost_override=shost_ov or None,
            sock_hint=sock_hint if isinstance(sock_hint, dict) else None,
            request_path=req_path,
        )
    except Exception:
        pass
    return ctx


def cef_llm_int003_extra(
    *,
    base_url: str,
    provider_id: str,
    model: str,
    request_uuid: str,
) -> Dict[str, Any]:
    """Поля ``extra`` для INT003 (запрос к LLM по OpenAI-compatible ``/v1/chat/completions``)."""
    root = (base_url or "").rstrip("/")
    extra: Dict[str, Any] = {
        "cs1": model,
        "cs1Label": "LLMModel",
        "cs3": provider_id,
        "cs3Label": "LLMProvider",
        "request": f"{root}/v1/chat/completions",
        "duser": os.getenv("CEF_LLM_DUSER", "API-KEY"),
        "requestUuid": request_uuid,
    }
    try:
        pu = urlparse(base_url)
        if pu.hostname:
            extra["dhost"] = pu.hostname
        if pu.port:
            extra["dpt"] = int(pu.port)
        elif (pu.scheme or "").lower() == "https":
            extra["dpt"] = 443
    except Exception:
        pass
    dnt = os.getenv("CEF_LLM_DNTDOM")
    if dnt:
        extra["dntdom"] = dnt
    return extra


def log_cef_int003_llm_request(
    *,
    base_url: str,
    provider_id: str,
    model: str,
    request_uuid: str,
) -> None:
    """CEF INT003: исходящий вызов LLM (до HTTP-запроса)."""
    log_cef_event(
        "INT003",
        current_user=None,
        status_code=200,
        extra=cef_llm_int003_extra(
            base_url=base_url,
            provider_id=provider_id,
            model=model,
            request_uuid=request_uuid,
        ),
    )


def log_cef_int006_llm_api_failure(
    *,
    request_uuid: str,
    code_status: Any,
    text_status: str,
    service_name: str,
    method_name: str = "POST /v1/chat/completions",
    status_code: Optional[int] = None,
) -> None:
    """CEF INT006: сбой вызова LLM API (HTTP, формат ответа, исключение)."""
    from backend.settings.cef_logger.cef_audit_context import cef_audit_peek

    req, user, _ = cef_audit_peek()
    log_cef_event(
        "INT006",
        request=req,
        current_user=user,
        status_code=status_code,
        extra={
            "methodName": method_name,
            "serviceName": service_name,
            "requestUuid": request_uuid,
            "codeStatus": str(code_status),
            "textStatus": (text_status or "")[:512],
        },
    )


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

    _cef_ac: Optional[str] = None
    try:
        from backend.settings.cef_logger.cef_audit_context import cef_audit_peek

        _ar, _au, _cef_ac = cef_audit_peek()
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
            "shost": socket.getfqdn() or socket.gethostname() or "localhost",
            "spt": 0,
            "app": "HTTPS",
            "suser": "SYSTEM",
            "sntdom": domain_from_ldap_base_dn(os.getenv("LDAP_USER_SEARCH_BASE", "")) or "-",
        }
        current_user_effective = {"username": "SYSTEM", "user_id": None}
    else:
        current_user = _resolve_cef_current_user(request, current_user)
        ctx = request_context(request, current_user)
        current_user_effective = current_user

    extra = dict(extra) if extra else {}
    if event_id == "INT003" and _cef_ac and extra.get("cs2") in (None, "", "-"):
        extra["cs2"] = _cef_ac
        extra.setdefault("cs2Label", "ConversationId")
    cef_tail = extra.pop("cef_tail", "")
    if cef_tail is None:
        cef_tail = ""

    # Разрешаем переопределять deviceDirection через extra (для исходящих вызовов по событиям
    # с device_direction=0 по умолчанию, напр. INT005/INT006 при вызовах к SVC-RAG/MongoDB).
    _extra_dir = extra.pop("_deviceDirection", None)
    effective_dir = int(_extra_dir) if _extra_dir is not None else spec.device_direction

    extension: Dict[str, Any] = {
        "externalId": uuid.uuid4().hex,
        "deviceProcessName": os.getenv("CEF_DEVICE_PROCESS", "astrachat"),
        "cat": spec.cat,
        "shost": ctx["shost"],
        "src": ctx["src"],
        "sntdom": ctx["sntdom"],
        "spt": ctx["spt"],
        "dhost": dhost_fqdn,
        "dpt": int(os.getenv("CEF_DPT", "443")),
        "app": ctx["app"],
        "act": spec.act,
        "outcome": outcome or _outcome_from_status(status_code) or spec.outcome,
        "suser": ctx["suser"],
        "start": now_ms,
        "end": now_ms,
        "rt": datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "dvchost": resolve_cef_dvchost(),
        "deviceDirection": effective_dir,
    }

    uid = (current_user_effective or {}).get("user_id")
    if uid and event_id != "SYS001":
        extension["suid"] = str(uid)

    if effective_dir == 1:
        if extra.get("dst"):
            extension["dst"] = extra.get("dst")
        if extra.get("duser"):
            extension["duser"] = extra.get("duser")
        if extra.get("dntdom"):
            extension["dntdom"] = extra.get("dntdom")
        if extra.get("dhost"):
            extension["dhost"] = extra.get("dhost")
        if extra.get("dpt") is not None:
            extension["dpt"] = int(extra["dpt"])

    extension.update({k: v for k, v in extra.items() if v is not None and not str(k).startswith("_")})

    if event_id in ("INT001", "INT002"):
        extension.pop("src", None)
        extension["shost"] = os.getenv("CEF_INT_SHOST", "localhost")
        extension["spt"] = 0

    fmt_map = _SafeDict({**extension, "cat": spec.cat, "cef_tail": cef_tail or ""})
    if "dmail" not in fmt_map or fmt_map["dmail"] in ("", "-"):
        fmt_map["dmail"] = (current_user_effective or {}).get("email") or "-"
    if event_id == "AGT003" and (fmt_map.get("agt_copy_target") in (None, "", "-")):
        fmt_map["agt_copy_target"] = fmt_map.get("cs2") or "-"

    extension["msg"] = spec.msg_template.format_map(fmt_map)

    for _msg_only in ("agt_copy_target", "dmail"):
        extension.pop(_msg_only, None)

    # Заголовок CEF: поля через "|", далее расширение — пары key=value через пробел (как в CEF.md §7).
    prefix = "CEF:0|{vendor}|{product}|{version}|{event_id}|{name}|{severity}|".format(
        vendor=os.getenv("CEF_DEVICE_VENDOR", "CORSUR"),
        product=os.getenv("CEF_DEVICE_PRODUCT", "AstraChat"),
        version=resolve_cef_device_version(),
        event_id=spec.event_id,
        name=spec.name,
        severity=spec.severity,
    )
    ext_str = " ".join(f"{k}={_esc(v)}" for k, v in _extension_ordered_items(extension))
    full_cef = f"{prefix}{ext_str}"
    logger.info("%s", full_cef)
    _emit_cef_syslog_udp(full_cef, spec.severity)

def login_audit_cef_extra(user: Optional[dict], auth_method: str) -> Dict[str, Any]:
    """Общие cs-поля и auth_method для SEC001/USR001 (LDAP или Keycloak)."""
    method = str(auth_method or "ldap").strip().lower() or "ldap"
    extra: Dict[str, Any] = {"auth_method": method}
    if method == "keycloak":
        extra.update(keycloak_audit_cs_fields(user))
    else:
        extra.update(ldap_audit_cs_fields(user))
    return extra

