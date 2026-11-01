"""HTTP middleware: CEF audit context + OBJ001/OBJ002 на входящие API SVC-RAG-MODELS."""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.cef_logger.cef_audit_context import (
    caller_service_from_headers,
    cef_audit_reset,
    cef_audit_set,
    user_from_request_headers,
)
from app.core.cef_logger.cef_logger import log_cef_event

_SERVICE_NAME = "SVC-RAG-MODELS"

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


def _caller_extra(request: Request) -> dict:
    caller = caller_service_from_headers(request)
    if not caller:
        return {}
    return {"cs2": caller, "cs2Label": "CallerService"}


class CefAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        user = user_from_request_headers(request)
        tokens = cef_audit_set(request=request, user=user)
        request_uuid = uuid.uuid4().hex
        path = request.url.path
        method_name = f"{request.method} {path}"
        caller_extra = _caller_extra(request)
        try:
            response = await call_next(request)
        except Exception as exc:
            if not _should_skip(path):
                log_cef_event(
                    "OBJ002",
                    request=request,
                    current_user=user,
                    status_code=500,
                    extra={
                        "methodName": method_name,
                        "serviceName": _SERVICE_NAME,
                        "requestUuid": request_uuid,
                        "codeStatus": "500",
                        "textStatus": str(exc)[:512],
                        **caller_extra,
                    },
                )
            cef_audit_reset(tokens)
            raise

        try:
            if not _should_skip(path):
                status = int(getattr(response, "status_code", 500) or 500)
                if 200 <= status < 400:
                    log_cef_event(
                        "OBJ001",
                        request=request,
                        current_user=user,
                        status_code=status,
                        extra={
                            "methodName": method_name,
                            "serviceName": _SERVICE_NAME,
                            "requestUuid": request_uuid,
                            **caller_extra,
                        },
                    )
                else:
                    log_cef_event(
                        "OBJ002",
                        request=request,
                        current_user=user,
                        status_code=status,
                        extra={
                            "methodName": method_name,
                            "serviceName": _SERVICE_NAME,
                            "requestUuid": request_uuid,
                            "codeStatus": str(status),
                            "textStatus": "-",
                            **caller_extra,
                        },
                    )
        finally:
            cef_audit_reset(tokens)
        return response
