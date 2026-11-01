"""Проверка API-ключа и CEF SEC001/SEC003 для SVC-RAG-MODELS.

Fail-closed: пока ключ (``RAG_MODELS_API_KEY`` / ``RAG_MODELS_API_KEY_ENV``) не
задан, все маршруты, кроме health/docs, отвечают 503 (сервис не настроен).
Работа без ключа возможна только по явному ``RAG_MODELS_ALLOW_INSECURE_NO_AUTH=1``
(тесты / локальная разработка).
"""

from __future__ import annotations

import logging
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.cef_logger.cef_audit_context import (
    caller_service_from_headers,
    user_from_request_headers,
)
from app.core.cef_logger.cef_logger import api_key_audit_cef_extra, log_cef_event

logger = logging.getLogger(__name__)

_INSECURE_ENV = "RAG_MODELS_ALLOW_INSECURE_NO_AUTH"
_TRUTHY = {"1", "true", "yes", "on"}

_SKIP_PATH_SUFFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _should_skip(path: str) -> bool:
    p = (path or "").rstrip("/")
    return any(
        p.endswith(s.rstrip("/")) or p == s.rstrip("/") for s in _SKIP_PATH_SUFFIXES
    )


def insecure_no_auth_allowed() -> bool:
    """Явное разрешение работать без ключа (только dev/tests)."""
    return (os.getenv(_INSECURE_ENV) or "").strip().lower() in _TRUTHY


def _configured_api_key() -> str:
    direct = (os.getenv("RAG_MODELS_API_KEY") or "").strip()
    if direct:
        return direct
    env_name = (os.getenv("RAG_MODELS_API_KEY_ENV") or "").strip()
    if env_name:
        return (os.getenv(env_name) or "").strip()
    return ""


def _extract_api_key(request: Request) -> str:
    auth = (
        request.headers.get("authorization")
        or request.headers.get("Authorization")
        or ""
    ).strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (
        request.headers.get("x-api-key") or request.headers.get("X-API-Key") or ""
    ).strip()


class CefAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        expected = _configured_api_key()
        if _should_skip(request.url.path):
            return await call_next(request)
        if not expected:
            if insecure_no_auth_allowed():
                return await call_next(request)
            logger.error(
                "SVC-RAG-MODELS: RAG_MODELS_API_KEY не задан, запрос %s отклонён "
                "(для dev/tests: %s=1)",
                request.url.path,
                _INSECURE_ENV,
            )
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "SVC-RAG-MODELS: ключ доступа не настроен (RAG_MODELS_API_KEY)"
                },
            )

        presented = _extract_api_key(request)
        valid = bool(presented) and secrets.compare_digest(presented, expected)
        # Отметка читается аудитом (cef_audit_context): заголовкам X-CEF-* / x-real-ip
        # верим только при валидном ключе; при отказе (SEC003) - не верим.
        request.state.api_key_valid = valid
        request.state.api_key_rejected = not valid
        user = user_from_request_headers(request)
        caller = caller_service_from_headers(request) or "unknown"

        if not valid:
            reason = "missing API key" if not presented else "invalid API key"
            log_cef_event(
                "SEC003",
                request=request,
                current_user=user or {"username": caller},
                status_code=401,
                extra={
                    "reason": reason,
                    "auth_method": "api_key",
                    **api_key_audit_cef_extra(caller),
                },
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
            )

        log_cef_event(
            "SEC001",
            request=request,
            current_user=user or {"username": caller, "auth_method": "api_key"},
            status_code=200,
            extra={"auth_method": "api_key", **api_key_audit_cef_extra(caller)},
        )
        return await call_next(request)
