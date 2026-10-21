"""Общий секрет SVC-RAG: при заданном ключе нужен X-API-Key / Bearer."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.api_key_auth import (
    get_svc_rag_api_key,
    is_public_svc_rag_path,
    require_svc_rag_api_key,
)

pytestmark = pytest.mark.unit


def _request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/kb/documents",
            "headers": headers or [],
        }
    )


async def test_without_env_key_auth_is_noop(monkeypatch):
    monkeypatch.delenv("SVC_RAG_API_KEY", raising=False)
    monkeypatch.delenv("RAG_API_KEY", raising=False)
    assert get_svc_rag_api_key() == ""
    await require_svc_rag_api_key(_request())


async def test_missing_header_is_401(monkeypatch):
    monkeypatch.setenv("SVC_RAG_API_KEY", "secret-rag")
    with pytest.raises(HTTPException) as exc:
        await require_svc_rag_api_key(_request())
    assert exc.value.status_code == 401


async def test_wrong_header_is_401(monkeypatch):
    monkeypatch.setenv("SVC_RAG_API_KEY", "secret-rag")
    with pytest.raises(HTTPException) as exc:
        await require_svc_rag_api_key(_request([(b"x-api-key", b"wrong")]))
    assert exc.value.status_code == 401


async def test_matching_x_api_key_passes(monkeypatch):
    monkeypatch.setenv("SVC_RAG_API_KEY", "secret-rag")
    await require_svc_rag_api_key(_request([(b"x-api-key", b"secret-rag")]))


async def test_matching_bearer_passes(monkeypatch):
    monkeypatch.setenv("RAG_API_KEY", "secret-rag")
    await require_svc_rag_api_key(_request([(b"authorization", b"Bearer secret-rag")]))


def test_health_paths_are_public():
    assert is_public_svc_rag_path("/v1/health")
    assert is_public_svc_rag_path("/health")
    assert not is_public_svc_rag_path("/v1/kb/documents")
