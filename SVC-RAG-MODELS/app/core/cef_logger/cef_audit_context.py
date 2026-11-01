"""Контекст CEF-аудита для SVC-RAG (request + пользователь-инициатор)."""

from __future__ import annotations

import contextvars
import ipaddress
import os
from typing import Any, Dict, NamedTuple, Optional

_request: contextvars.ContextVar = contextvars.ContextVar(
    "svc_rag_cef_request", default=None
)
_user: contextvars.ContextVar = contextvars.ContextVar("svc_rag_cef_user", default=None)


class _Tokens(NamedTuple):
    request: Any
    user: Any


def cef_audit_set(
    *,
    request: Any = None,
    user: Optional[Dict[str, Any]] = None,
) -> _Tokens:
    return _Tokens(
        request=_request.set(request),
        user=_user.set(user),
    )


def cef_audit_reset(tokens: _Tokens) -> None:
    _request.reset(tokens.request)
    _user.reset(tokens.user)


def cef_audit_peek() -> tuple[Optional[Any], Optional[Dict[str, Any]]]:
    return _request.get(), _user.get()


_TRUSTED_PROXIES_ENV = "RAG_MODELS_TRUSTED_PROXIES"
_INSECURE_ENV = "RAG_MODELS_ALLOW_INSECURE_NO_AUTH"
_TRUTHY = {"1", "true", "yes", "on"}


def _peer_is_trusted(request: Any) -> bool:
    """Можно ли верить заголовкам X-CEF-* / x-real-ip / x-forwarded-for этого запроса.

    Запрос, отклонённый по ключу (SEC003), не доверяется никогда: пишется адрес пира.
    Иначе доверяем, если (а) запрос предъявил валидный API-ключ (отметка
    request.state.api_key_valid ставится в CefAuthMiddleware), или (б) включён
    RAG_MODELS_ALLOW_INSECURE_NO_AUTH (dev), или (в) адрес TCP-пира входит в
    RAG_MODELS_TRUSTED_PROXIES (IP/CIDR через запятую, * - все). Иначе клиент
    подделывает src/suser в журнале аудита (CWE-117/CWE-290).
    """
    state = getattr(request, "state", None)
    if getattr(state, "api_key_rejected", False):
        return False
    if getattr(state, "api_key_valid", False):
        return True
    if (os.getenv(_INSECURE_ENV) or "").strip().lower() in _TRUTHY:
        return True
    raw = (os.getenv(_TRUSTED_PROXIES_ENV) or "").strip()
    if not raw:
        return False
    items = [x.strip() for x in raw.split(",") if x.strip()]
    if "*" in items:
        return True
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    if not host:
        return False
    try:
        peer = ipaddress.ip_address(host)
    except ValueError:
        return False
    for item in items:
        try:
            if peer in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


def caller_service_from_headers(request: Any) -> Optional[str]:
    """Сервис-инициатор (SVC-RAG, backend и т.д.) из X-CEF-Caller-Service."""
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    if headers is None or not _peer_is_trusted(request):
        return None
    raw = (
        headers.get("x-cef-caller-service") or headers.get("X-CEF-Caller-Service") or ""
    ).strip()
    return raw or None


def user_from_request_headers(request: Any) -> Optional[Dict[str, Any]]:
    """Инициатор из заголовков, которые прокидывает backend RagClient / SVC-RAG."""
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    if headers is None or not _peer_is_trusted(request):
        return None
    suser = (headers.get("x-cef-suser") or headers.get("X-CEF-Suser") or "").strip()
    suid = (headers.get("x-cef-suid") or headers.get("X-CEF-Suid") or "").strip()
    sntdom = (headers.get("x-cef-sntdom") or headers.get("X-CEF-Sntdom") or "").strip()
    if not suser and not suid:
        return None
    return {
        "username": suser or suid or "SYSTEM",
        "user_id": suid or None,
        "sntdom": sntdom or None,
    }


def resolve_client_src(request: Any) -> str:
    if request is None:
        return "127.0.0.1"
    headers = getattr(request, "headers", None) or {}
    if _peer_is_trusted(request):
        for key in ("x-real-ip", "x-forwarded-for"):
            raw = headers.get(key) or headers.get(key.title()) or ""
            if raw:
                return str(raw).split(",")[0].strip() or "127.0.0.1"
    client = getattr(request, "client", None)
    if client and getattr(client, "host", None):
        return str(client.host)
    return "127.0.0.1"
