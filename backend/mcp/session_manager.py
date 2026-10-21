"""Per-request lifecycle MCP sessions + pool integration."""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Dict, List, Optional

import httpx

from backend.mcp.client import McpClient
from backend.mcp.http_verify import resolve_httpx_verify, verify_setting_label
from backend.mcp.connection import McpServerSession
from backend.mcp.context_headers import (
    build_context_headers,
    merge_server_custom_headers,
)
from backend.mcp.credentials.base import get_credential_provider
from backend.mcp.exceptions import McpConnectionError, McpServerNotFoundError
from backend.mcp.registry import McpRegistry
from backend.mcp.session_pool import McpSessionPool
from backend.mcp.transports.stdio import connect_stdio
from backend.mcp.transports.streamable_http import (
    build_mcp_http_url,
    connect_streamable_http,
)
from backend.mcp.types import McpCallContext, McpTransport
from backend.settings.config import McpServerConfig, Settings
from backend.settings.logging import get_logger

log = get_logger(__name__)

_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
    "ALL_PROXY",
    "all_proxy",
)


def _proxy_env_snapshot() -> str:
    parts: list[str] = []
    for key in _PROXY_ENV_KEYS:
        val = os.getenv(key)
        if val and str(val).strip():
            parts.append(f"{key}={val.strip()}")
    return "; ".join(parts) if parts else "(proxy env not set)"


def _connect_error_hint(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = str(exc).lower()
    if "certificate" in msg or "ssl" in msg or "CERTIFICATE_VERIFY_FAILED" in str(exc):
        return (
            "TLS: не доверен CA сертификата Hub/HTTPS. Проверьте ca-bundle и TLS_CERT_PATH "
            "(/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem)."
        )
    if name in ("ConnectTimeout",) or "connecttimeout" in msg:
        return (
            "ConnectTimeout: TCP не установился за timeout. Проверьте BASE_URL, Service/endpoints, "
            "NetworkPolicy и HTTP(S)_PROXY в env пода."
        )
    if name in ("ReadTimeout", "WriteTimeout", "TimeoutError") or "timeout" in msg:
        return "Timeout после соединения: проверьте логи MCP-пода и init_timeout/acquire_timeout."
    if name in ("ConnectError",) or "connection refused" in msg:
        return "ConnectError/refused: порт закрыт или у Service нет endpoints."
    if "name or service not known" in msg or "getaddrinfo" in msg:
        return "DNS: имя не резолвится — неверный hostname в BASE_URL."
    return f"Неизвестная ошибка ({name}) — см. traceback."


def _server_auth_env_prefix(server_id: str) -> str:
    return f"MCP_SERVER_{server_id.upper().replace('-', '_')}_"


def _resolve_auth_token(server: McpServerConfig) -> str:
    env_val = os.getenv(f"{_server_auth_env_prefix(server.id)}AUTH_TOKEN")
    if env_val is not None:
        return env_val.strip()
    return str(server.auth_token or "").strip()


def build_server_auth_headers(server: McpServerConfig) -> Dict[str, str]:
    """Generic MCP server auth matrix (B-34)."""
    auth_type = str(server.auth_type or "none").strip().lower()
    if auth_type in ("", "none"):
        return {}
    if auth_type == "bearer":
        token = _resolve_auth_token(server)
        return {"Authorization": f"Bearer {token}"} if token else {}
    if auth_type == "headers":
        prefix = _server_auth_env_prefix(server.id)
        name = (
            os.getenv(f"{prefix}AUTH_HEADER_NAME") or server.auth_header_name or ""
        ).strip()
        value = (
            os.getenv(f"{prefix}AUTH_HEADER_VALUE") or server.auth_header_value or ""
        ).strip()
        return {name: value} if name and value else {}
    return {}


def _headers_fingerprint(headers: Dict[str, str]) -> str:
    payload = json.dumps(headers or {}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class McpSessionManager:

    def __init__(
        self,
        settings: Settings,
        registry: McpRegistry,
        pool: Optional[McpSessionPool] = None,
    ):
        self._settings = settings
        self._registry = registry
        self._pool = pool
        self._active: List[McpServerSession] = []

    async def connect_server(
        self,
        server_id: str,
        context: McpCallContext,
        *,
        use_pool: bool = True,
        ephemeral: bool = False,
    ) -> McpServerSession:
        server = self._registry.require_server(server_id)
        if not server.enabled:
            msg = f"MCP server '{server_id}' is disabled"
            raise McpServerNotFoundError(msg)
        provider = get_credential_provider(server.credential_provider)
        generic_auth = build_server_auth_headers(server)
        auth_headers = await provider.build_headers(server, context)
        ctx_headers = build_context_headers(context)
        custom_headers = merge_server_custom_headers(
            getattr(server, "custom_headers", None), context
        )
        headers = {**generic_auth, **auth_headers, **ctx_headers, **custom_headers}
        fingerprint = _headers_fingerprint(headers)
        if (
            use_pool
            and (not ephemeral)
            and (self._pool is not None)
            and (server.transport == McpTransport.STREAMABLE_HTTP.value)
        ):
            session = await self._pool.acquire(
                server_id,
                server=server,
                context=context,
                headers=headers,
                fingerprint=fingerprint,
            )
            if session.headers_fingerprint != fingerprint:
                await self._pool.discard(session)
                session = await self._create_session(
                    server, context, headers, fingerprint
                )
            session.ephemeral = ephemeral
            self._track(session)
            return session
        session = await self._create_session(server, context, headers, fingerprint)
        session.ephemeral = ephemeral
        self._track(session)
        return session

    async def connect_servers(
        self,
        server_ids: List[str],
        context: McpCallContext,
        *,
        use_pool: bool = True,
        ephemeral: bool = False,
    ) -> Dict[str, McpServerSession]:
        sessions: Dict[str, McpServerSession] = {}
        for sid in server_ids:
            try:
                sessions[sid] = await self.connect_server(
                    sid, context, use_pool=use_pool, ephemeral=ephemeral
                )
            except Exception:
                log.exception("MCP connect failed server=")
        return sessions

    async def disconnect_all(self) -> None:
        for session in reversed(self._active):
            try:
                if (
                    self._pool
                    and (not session.ephemeral)
                    and (session.config.transport == "streamable-http")
                ):
                    await self._pool.release(session)
                else:
                    await session.close()
            except Exception:
                log.exception("MCP disconnect error server=")
        self._active.clear()

    async def discard_session(self, session: McpServerSession) -> None:
        if session in self._active:
            self._active.remove(session)
        if self._pool:
            await self._pool.discard(session)
        else:
            await session.close()

    def _track(self, session: McpServerSession) -> None:
        self._active.append(session)

    async def _create_session(
        self,
        server: McpServerConfig,
        context: McpCallContext,
        headers: Dict[str, str],
        fingerprint: str,
    ) -> McpServerSession:
        client = McpClient()
        timeout = float(server.timeout_seconds or 120)
        mcp_url = ""
        started = time.perf_counter()
        try:
            if server.transport == McpTransport.STDIO.value:
                if not server.command:
                    msg = f"stdio transport requires command for server '{server.id}'"
                    raise McpConnectionError(msg)
                log.debug(
                    "MCP stdio connect start server=%s command=%s args=%s cwd=%s timeout=%ss",
                    server.id,
                    server.command,
                    server.args,
                    server.cwd,
                    timeout,
                )
                await connect_stdio(
                    client,
                    command=server.command,
                    args=server.args,
                    cwd=server.cwd,
                    timeout=timeout,
                )
                log.debug(
                    "MCP stdio connect ok server=%s elapsed_ms=%.1f",
                    server.id,
                    (time.perf_counter() - started) * 1000.0,
                )
            else:
                base = str(
                    server.base_url or ""
                ).strip() or self._settings.resolve_mcp_server_base_url(server.id)
                mcp_url = build_mcp_http_url(base, server.base_path)
                init_timeout = min(timeout, 30.0)
                verify = resolve_httpx_verify(bool(server.verify_ssl))
                log.debug(
                    "MCP session connect start server=%s base_url=%s mcp_url=%s timeout=%ss init_timeout=%ss "
                    "verify=%s use_pool=%s",
                    server.id,
                    base,
                    mcp_url,
                    timeout,
                    init_timeout,
                    verify_setting_label(verify),
                    self._pool is not None,
                )
                await connect_streamable_http(
                    client,
                    url=mcp_url,
                    headers=headers,
                    timeout=timeout,
                    verify_ssl=bool(server.verify_ssl),
                    server_id=server.id,
                )
                log.debug(
                    "MCP session connect ok server=%s mcp_url=%s elapsed_ms=%.1f",
                    server.id,
                    mcp_url,
                    (time.perf_counter() - started) * 1000.0,
                )
        except Exception as exc:
            if mcp_url:
                log.debug(
                    "MCP session connect fail server=%s mcp_url=%s error=%s: %s elapsed_ms=%.1f hint=%s proxy_env=%s",
                    server.id,
                    mcp_url,
                    type(exc).__name__,
                    exc,
                    (time.perf_counter() - started) * 1000.0,
                    _connect_error_hint(exc),
                    _proxy_env_snapshot(),
                )
            else:
                log.exception("MCP stdio connect failed server=%s", server.id)
            await client.disconnect()
            msg = f"Failed to connect MCP server '{server.id}': {exc}"
            raise McpConnectionError(msg) from exc
        return McpServerSession(
            server_id=server.id,
            config=server,
            client=client,
            context=context,
            headers_fingerprint=fingerprint,
        )

    async def create_pooled_session(
        self,
        server_id: str,
        *,
        server: McpServerConfig,
        context: McpCallContext,
        headers: Dict[str, str],
        fingerprint: str,
    ) -> McpServerSession:
        return await self._create_session(server, context, headers, fingerprint)

    async def probe_http_health(self, server: McpServerConfig) -> tuple[bool, str]:
        """Возвращает (ok, detail). detail содержит целевой URL и реальную причину сбоя."""
        if server.transport != McpTransport.STREAMABLE_HTTP.value:
            return True, ""
        base = str(
            server.base_url or ""
        ).strip() or self._settings.resolve_mcp_server_base_url(server.id)
        health_path = str(server.health_path or "/healthz").strip()
        if not health_path.startswith("/"):
            health_path = f"/{health_path}"
        url = f"{base.rstrip('/')}{health_path}"
        started = time.perf_counter()
        verify = resolve_httpx_verify(bool(server.verify_ssl))
        log.debug(
            "MCP health probe start server=%s url=%s timeout=%ss verify=%s",
            server.id,
            url,
            5.0,
            verify_setting_label(verify),
        )
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=verify) as client:
                resp = await client.get(url)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                if resp.status_code < 500:
                    log.debug(
                        "MCP health probe ok server=%s url=%s http_status=%s elapsed_ms=%.1f",
                        server.id,
                        url,
                        resp.status_code,
                        elapsed_ms,
                    )
                    return True, ""
                detail = f"{url} -> HTTP {resp.status_code}"
                log.debug(
                    "MCP health probe fail server=%s url=%s http_status=%s elapsed_ms=%.1f",
                    server.id,
                    url,
                    resp.status_code,
                    elapsed_ms,
                )
                return False, detail
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            log.debug(
                "MCP health probe fail server=%s url=%s error=%s: %s elapsed_ms=%.1f hint=%s proxy_env=%s",
                server.id,
                url,
                type(exc).__name__,
                exc,
                elapsed_ms,
                _connect_error_hint(exc),
                _proxy_env_snapshot(),
            )
            return False, f"{url} недоступен: {type(exc).__name__}: {exc}"
