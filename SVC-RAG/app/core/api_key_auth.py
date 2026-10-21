"""Общий секрет backend↔svc-rag (X-API-Key / Bearer).

Пока ``SVC_RAG_API_KEY`` (или ``RAG_API_KEY``) не задан — доступ открыт
(сетевая изоляция). После задания ключа все маршруты, кроме health,
требуют совпадения заголовка.
"""

from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import HTTPException, Request, status

_ENV_NAMES = ("SVC_RAG_API_KEY", "RAG_API_KEY")


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
    """Зависимость FastAPI: при заданном ключе отклоняет чужие запросы."""
    expected = get_svc_rag_api_key()
    if not expected:
        return
    provided = _extract_provided_key(request)
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется X-API-Key / Bearer для SVC-RAG",
        )


def is_public_svc_rag_path(path: str) -> bool:
    normalized = (path or "").rstrip("/") or "/"
    return normalized in {"/v1/health", "/health", "/docs", "/redoc", "/openapi.json"}
