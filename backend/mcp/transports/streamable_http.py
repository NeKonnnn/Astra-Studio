"""Streamable HTTP transport (MCP SDK streamable_http_client)."""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Optional, Tuple
from urllib.parse import urljoin

from backend.mcp.sdk import import_mcp_sdk_submodule

_mod = import_mcp_sdk_submodule("mcp.client.streamable_http")
_streamable_http_client = getattr(_mod, "streamable_http_client", None)
_legacy_streamablehttp_client = getattr(_mod, "streamablehttp_client", None)
if _streamable_http_client is None and _legacy_streamablehttp_client is None:
    raise AttributeError(
        "mcp.client.streamable_http: нет streamable_http_client / streamablehttp_client"
    )

from backend.mcp.client import create_httpx_client, create_insecure_httpx_client


def build_mcp_http_url(base_url: str, base_path: str) -> str:
    root = str(base_url or "").strip().rstrip("/")
    path = str(base_path or "/mcp").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return urljoin(f"{root}/", path.lstrip("/"))


def _uses_new_streamable_http_api() -> bool:
    if _streamable_http_client is None:
        return False
    params = inspect.signature(_streamable_http_client).parameters
    return "http_client" in params


@asynccontextmanager
async def _connect_new_streamable_http(
    url: str,
    *,
    headers: Optional[Dict[str, str]],
    timeout: float,
    verify_ssl: bool,
) -> AsyncIterator[Tuple]:
    """MCP SDK >=1.8: headers передаются через httpx.AsyncClient, а не kwargs клиента."""
    factory = create_httpx_client if verify_ssl else create_insecure_httpx_client
    http_client = factory(headers=headers, timeout=timeout)
    async with http_client:
        async with _streamable_http_client(url, http_client=http_client) as streams:
            yield streams


async def connect_streamable_http(
    client,
    *,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 120.0,
    verify_ssl: bool = True,
    server_id: Optional[str] = None,
) -> None:
    if _uses_new_streamable_http_api():
        streams_context = _connect_new_streamable_http(
            url,
            headers=headers,
            timeout=timeout,
            verify_ssl=verify_ssl,
        )
    else:
        factory = create_httpx_client if verify_ssl else create_insecure_httpx_client
        streams_context = _legacy_streamablehttp_client(
            url,
            headers=headers,
            httpx_client_factory=factory,
            timeout=timeout,
        )
    await client.connect_streams(
        streams_context,
        init_timeout=min(timeout, 30.0),
        server_id=server_id,
        mcp_url=url,
    )
