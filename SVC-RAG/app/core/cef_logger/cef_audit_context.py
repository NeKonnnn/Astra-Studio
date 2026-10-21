"""Контекст CEF-аудита для SVC-RAG (request + пользователь-инициатор)."""

from __future__ import annotations

import contextvars
from typing import Any, Dict, NamedTuple, Optional

_request: contextvars.ContextVar = contextvars.ContextVar("svc_rag_cef_request", default=None)
_user: contextvars.ContextVar = contextvars.ContextVar("svc_rag_cef_user", default=None)

# Модель чата из заголовка backend (X-Astra-Model): judge/summary в той же модели.
_chat_model: contextvars.ContextVar = contextvars.ContextVar(
    "svc_rag_chat_model", default=None
)


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


def chat_model_set(model_path: Optional[str]) -> None:
    _chat_model.set(str(model_path or "").strip() or None)


def chat_model_peek() -> Optional[str]:
    return _chat_model.get()


def chat_model_set_from_request(request: Any) -> None:
    """Взять путь модели из X-Astra-Model (прокидывает backend RagClient)."""
    if request is None:
        chat_model_set(None)
        return
    headers = getattr(request, "headers", None) or {}
    raw = (
        headers.get("x-astra-model")
        or headers.get("X-Astra-Model")
        or ""
    )
    chat_model_set(raw)


# Логин для запросов, которые делает сам сервис, а не пользователь.
MODELS_SYSTEM_USER_ID = "astrachat"


def model_identity_headers() -> Dict[str, str]:
    """X-User-ID для запросов к моделям через шлюз."""
    import os

    login = ""
    try:
        _req, user = cef_audit_peek()
        if user:
            login = str(user.get("username") or user.get("user_id") or "").strip()
    except Exception:
        login = ""
    if not login or login.upper() == "SYSTEM":
        login = (
            os.getenv("MODELS_SYSTEM_USER_ID", "") or MODELS_SYSTEM_USER_ID
        ).strip() or MODELS_SYSTEM_USER_ID
    return {"X-User-ID": login}


def cef_identity_headers() -> Dict[str, str]:
    """Инициатор для внутренних вызовов backend (OCR, LLM-прокси)."""
    headers: Dict[str, str] = {}
    try:
        _req, user = cef_audit_peek()
        if not user:
            return headers
        suser = str(user.get("username") or "").strip()
        suid = str(user.get("user_id") or "").strip()
        sntdom = str(user.get("sntdom") or "").strip()
        if suser:
            headers["X-CEF-Suser"] = suser
        if suid:
            headers["X-CEF-Suid"] = suid
        if sntdom:
            headers["X-CEF-Sntdom"] = sntdom
    except Exception:
        return {}
    return headers


def user_from_request_headers(request: Any) -> Optional[Dict[str, Any]]:
    """Инициатор из заголовков, которые прокидывает backend RagClient."""
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    if headers is None:
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
    for key in ("x-real-ip", "x-forwarded-for"):
        raw = headers.get(key) or headers.get(key.title()) or ""
        if raw:
            return str(raw).split(",")[0].strip() or "127.0.0.1"
    client = getattr(request, "client", None)
    if client and getattr(client, "host", None):
        return str(client.host)
    return "127.0.0.1"
