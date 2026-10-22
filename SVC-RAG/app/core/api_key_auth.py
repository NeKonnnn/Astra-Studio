"""Общий секрет backend↔svc-rag (X-API-Key / Bearer).

Fail-closed: пока ``SVC_RAG_API_KEY`` (или ``RAG_API_KEY``) не задан, все
маршруты, кроме health, отвечают 503 (сервис не настроен). Работа без ключа
возможна только по явному разрешению ``RAG_ALLOW_INSECURE_NO_AUTH=1``
(тесты / локальная разработка). После задания ключа все маршруты, кроме
health, требуют совпадения заголовка.
"""

from __future__ import annotations

import logging
import os
import secrets

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

_ENV_NAMES = ("SVC_RAG_API_KEY", "RAG_API_KEY")
_INSECURE_ENV = "RAG_ALLOW_INSECURE_NO_AUTH"
_TRUTHY = {"1", "true", "yes", "on"}


def insecure_no_auth_allowed() -> bool:
    """Явное разрешение работать без ключа (только dev/tests)."""
    return (os.getenv(_INSECURE_ENV) or "").strip().lower() in _TRUTHY


def get_svc_rag_api_key() -> str:
    for name in _ENV_NAMES:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _extract_provided_key(request: Request) -> str:
    header_key = (request.headers.get("x-api-key") or "").strip()
    if header_key:
        return header_key
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


async def require_svc_rag_api_key(request: Request) -> None:
    """Зависимость FastAPI: без ключа — отказ (fail-closed), с ключом — сверка."""
    expected = get_svc_rag_api_key()
    if not expected:
        if insecure_no_auth_allowed():
            return
        logger.error(
            "SVC-RAG: SVC_RAG_API_KEY не задан, запрос %s отклонён "
            "(для dev/tests: %s=1)",
            request.url.path,
            _INSECURE_ENV,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SVC-RAG: ключ доступа не настроен (SVC_RAG_API_KEY)",
        )
    provided = _extract_provided_key(request)
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется X-API-Key / Bearer для SVC-RAG",
        )


def is_public_svc_rag_path(path: str) -> bool:
    normalized = (path or "").rstrip("/") or "/"
    return normalized in {"/v1/health", "/health", "/docs", "/redoc", "/openapi.json"}
