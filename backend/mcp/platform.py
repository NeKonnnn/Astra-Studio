"""MCP platform service: routing, verify, health, tools cache."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from backend.mcp.access import filter_tools, has_mcp_server_access, is_server_allowed
from backend.mcp.connection import McpServerSession
from backend.mcp.registry import McpRegistry
from backend.mcp.session_manager import McpSessionManager
from backend.mcp.session_pool import McpSessionPool
from backend.mcp.tool_adapter import qualify_tool_name
from backend.mcp.transports.streamable_http import build_mcp_http_url
from backend.mcp.tools_cache import McpToolsCache
from backend.mcp.types import (
    McpAggregateStatus,
    McpCallContext,
    McpServerStatus,
    McpToolInfo,
    McpVerifyResult,
)
from backend.settings.config import Settings, get_settings
from backend.settings.logging import get_logger

log = get_logger(__name__)

_platform: Optional["McpPlatformService"] = None


class McpPlatformService:

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self._registry = McpRegistry(self._settings)
        self._session_manager = McpSessionManager(
            self._settings, self._registry, pool=None
        )
        self._pool: Optional[McpSessionPool] = None
        self._tools_cache: Optional[McpToolsCache] = None
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def enabled(self) -> bool:
        return self._registry.enabled

    def list_enabled_server_ids(self, candidates: List[str]) -> List[str]:
        enabled = {s.id for s in self._registry.list_enabled_servers()}
        return [sid for sid in candidates if sid in enabled]

    async def initialize(self) -> None:
        if self._initialized:
            return
        self._registry.initialize()
        self._tools_cache = McpToolsCache(self._registry.config.tools_cache_ttl_seconds)
        if self._registry.enabled and self._registry.config.session_pool_enabled:
            self._pool = McpSessionPool(
                self._registry.config.pool, connect_factory=self._pool_connect_factory
            )
            self._session_manager._pool = self._pool
            self._pool.start()
        elif self._registry.enabled:
            log.info("MCP session pool disabled (MCP_SESSION_POOL_ENABLED=false)")
        self._initialized = True
        try:
            from backend.mcp.credentials.store import ensure_indexes

            await ensure_indexes()
        except Exception:
            log.exception("MCP credentials indexes skipped")
        log.info("MCP platform initialized (enabled=%s)", self._registry.enabled)
        if self._registry.enabled:
            for srv in self._registry.list_servers():
                if srv.transport == "stdio":
                    log.debug(
                        "MCP startup server=%s enabled=%s transport=stdio command=%s args=%s cwd=%s",
                        srv.id,
                        srv.enabled,
                        srv.command,
                        srv.args,
                        srv.cwd,
                    )
                else:
                    base = str(
                        srv.base_url or ""
                    ).strip() or self._settings.resolve_mcp_server_base_url(srv.id)
                    health_path = str(srv.health_path or "/healthz").strip()
                    if not health_path.startswith("/"):
                        health_path = f"/{health_path}"
                    log.debug(
                        "MCP startup server=%s enabled=%s transport=%s base_url=%s mcp_url=%s health_url=%s "
                        "pool_acquire_timeout=%ss",
                        srv.id,
                        srv.enabled,
                        srv.transport,
                        base,
                        build_mcp_http_url(base, srv.base_path),
                        f"{base.rstrip('/')}{health_path}",
                        self._registry.config.pool.acquire_timeout_seconds,
                    )

    async def shutdown(self) -> None:
        if self._pool:
            await self._pool.shutdown()
            self._pool = None
        self._initialized = False
        log.info("MCP platform shutdown complete")

    async def _pool_connect_factory(self, server_id: str, **kwargs) -> McpServerSession:
        return await self._session_manager.create_pooled_session(server_id, **kwargs)

    def list_servers_public(
        self, context: Optional[McpCallContext] = None
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for srv in self._registry.list_servers():
            if context and (not has_mcp_server_access(srv, context)):
                continue
            items.append(
                {
                    "id": srv.id,
                    "display_name": srv.display_name or srv.id,
                    "enabled": srv.enabled,
                    "transport": srv.transport,
                    "auth_type": getattr(srv, "auth_type", "none"),
                    "auth_mode": srv.auth_mode,
                    "credential_provider": srv.credential_provider,
                    "tool_name_prefix": srv.tool_name_prefix,
                }
            )
        return items

    def filter_tools_by_context(
        self,
        tools: List[McpToolInfo],
        context: McpCallContext,
        *,
        enabled_server_ids: Optional[List[str]] = None,
        enabled_tool_names: Optional[set] = None,
    ) -> List[McpToolInfo]:
        allowed_servers = set(enabled_server_ids or [])
        result: List[McpToolInfo] = []
        for srv in self._registry.list_enabled_servers():
            if allowed_servers and srv.id not in allowed_servers:
                continue
            server_tools = [t for t in tools if t.server_id == srv.id]
            result.extend(
                filter_tools(
                    server_tools,
                    server=srv,
                    context=context,
                    enabled_tool_names=enabled_tool_names,
                )
            )
        return result

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict,
        context: McpCallContext,
        session: McpServerSession,
    ) -> Any:
        raw_name = self._resolve_raw_tool_name(server_id, tool_name)
        return await session.call_tool(raw_name, arguments or {})

    async def get_aggregate_status(
        self,
        context: Optional[McpCallContext] = None,
        *,
        server_ids: Optional[List[str]] = None,
        fetch_tools: bool = False,
    ) -> McpAggregateStatus:
        if not self._registry.enabled:
            return McpAggregateStatus(
                initialized=self._initialized,
                enabled=False,
                servers_total=0,
                servers_connected=0,
                tools_total=0,
                message="MCP disabled",
            )
        ctx = context or McpCallContext(
            user_id="system", username="system", is_admin=True
        )
        servers_cfg = self._registry.list_enabled_servers()
        if server_ids:
            allowed = set(server_ids)
            servers_cfg = [srv for srv in servers_cfg if srv.id in allowed]
        statuses: List[McpServerStatus] = []
        connected = 0
        tools_total = 0
        for srv in servers_cfg:
            st = await self._probe_server_status(srv, ctx, fetch_tools=fetch_tools)
            statuses.append(st)
            if st.connected:
                connected += 1
            tools_total += st.tools
        return McpAggregateStatus(
            initialized=self._initialized,
            enabled=True,
            servers_total=len(servers_cfg),
            servers_connected=connected,
            tools_total=tools_total,
            servers=statuses,
        )

    async def get_server_status(
        self, server_id: str, context: McpCallContext, *, fetch_tools: bool = False
    ) -> McpServerStatus:
        server = self._registry.require_server(server_id)
        return await self._probe_server_status(server, context, fetch_tools=fetch_tools)

    async def health(
        self,
        context: Optional[McpCallContext] = None,
        *,
        server_ids: Optional[List[str]] = None,
        fetch_tools: bool = False,
    ) -> Dict[str, Any]:
        agg = await self.get_aggregate_status(
            context, server_ids=server_ids, fetch_tools=fetch_tools
        )
        pool_metrics = self._pool.get_metrics() if self._pool else {}
        return {
            "initialized": agg.initialized,
            "enabled": agg.enabled,
            "servers_total": agg.servers_total,
            "servers_connected": agg.servers_connected,
            "tools_total": agg.tools_total,
            "pool": pool_metrics,
            "servers": [
                {
                    "id": s.server_id,
                    "display_name": s.display_name,
                    "enabled": s.enabled,
                    "connected": s.connected,
                    "transport": s.transport,
                    "tools": s.tools,
                    "latency_ms": s.latency_ms,
                    "error": s.error,
                    "metadata": s.metadata,
                }
                for s in agg.servers
            ],
            "message": agg.message,
        }

    async def verify_server(
        self, server_id: str, context: McpCallContext
    ) -> McpVerifyResult:
        self._registry.require_server(server_id)
        started = time.perf_counter()
        mgr = McpSessionManager(self._settings, self._registry, pool=None)
        try:
            session = await mgr.connect_server(
                server_id, context, use_pool=False, ephemeral=True
            )
            specs = await session.list_tools()
            latency = (time.perf_counter() - started) * 1000.0
            return McpVerifyResult(
                server_id=server_id,
                success=True,
                tools_count=len(specs),
                latency_ms=latency,
                tools=[s.get("name", "") for s in specs],
            )
        except Exception as exc:
            log.exception("Ошибка операции")
            latency = (time.perf_counter() - started) * 1000.0
            log.warning("MCP verify failed server=%s: %s", server_id, exc)
            return McpVerifyResult(
                server_id=server_id, success=False, latency_ms=latency, error=str(exc)
            )
        finally:
            await mgr.disconnect_all()

    async def list_tools_for_server(
        self,
        server_id: str,
        context: McpCallContext,
        *,
        use_cache: bool = True,
        ephemeral: bool = False,
    ) -> List[McpToolInfo]:
        server = self._registry.require_server(server_id)
        if not is_server_allowed(server, context):
            return []
        provider = None
        if server.credential_provider:
            from backend.mcp.credentials.base import get_credential_provider

            provider = get_credential_provider(server.credential_provider)
        headers = {}
        if provider:
            from backend.mcp.context_headers import build_context_headers

            auth = await provider.build_headers(server, context)
            headers = {**auth, **build_context_headers(context)}
        from backend.mcp.session_manager import _headers_fingerprint

        fp = _headers_fingerprint(headers)
        if use_cache and self._tools_cache:
            cached = await self._tools_cache.get_tools_indexed(server_id, fp)
            if cached is not None:
                return filter_tools(cached, server=server, context=context)
        mgr = McpSessionManager(self._settings, self._registry, self._pool)
        try:
            session = await mgr.connect_server(
                server_id, context, use_pool=not ephemeral, ephemeral=ephemeral
            )
            specs = await session.list_tools()
            tools = self._normalize_tool_specs(server, specs)
            tools = filter_tools(tools, server=server, context=context)
            if use_cache and self._tools_cache:
                await self._tools_cache.set_tools_indexed(server_id, tools, fp)
            return tools
        finally:
            await mgr.disconnect_all()

    async def list_all_tools(self, context: McpCallContext) -> List[McpToolInfo]:
        all_tools: List[McpToolInfo] = []
        for srv in self._registry.list_enabled_servers():
            try:
                all_tools.extend(await self.list_tools_for_server(srv.id, context))
            except Exception:
                log.exception("list_tools failed server=")
        return all_tools

    async def call_tool_debug(
        self, server_id: str, tool_name: str, arguments: dict, context: McpCallContext
    ) -> Any:
        mgr = McpSessionManager(self._settings, self._registry, self._pool)
        try:
            session = await mgr.connect_server(server_id, context, ephemeral=True)
            raw_name = self._resolve_raw_tool_name(server_id, tool_name)
            return await session.call_tool(raw_name, arguments or {})
        finally:
            await mgr.disconnect_all()

    async def list_resources_for_server(
        self, server_id: str, context: McpCallContext, *, cursor: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        server = self._registry.require_server(server_id)
        if not is_server_allowed(server, context):
            return []
        mgr = McpSessionManager(self._settings, self._registry, self._pool)
        try:
            session = await mgr.connect_server(server_id, context, ephemeral=True)
            return await session.list_resources(cursor=cursor)
        finally:
            await mgr.disconnect_all()

    async def read_resource_for_server(
        self, server_id: str, uri: str, context: McpCallContext
    ) -> Dict[str, Any]:
        server = self._registry.require_server(server_id)
        if not is_server_allowed(server, context):
            from backend.mcp.exceptions import McpServerNotFoundError

            msg = f"Access denied for MCP server '{server_id}'"
            raise McpServerNotFoundError(msg)
        mgr = McpSessionManager(self._settings, self._registry, self._pool)
        try:
            session = await mgr.connect_server(server_id, context, ephemeral=True)
            raw = await session.read_resource(uri)
            from backend.mcp.result_parser import (
                parse_mcp_result_to_struct,
                parsed_to_api_dict,
            )

            parsed = parse_mcp_result_to_struct(raw)
            return {
                "uri": uri,
                "parsed": parsed_to_api_dict(parsed),
                "raw": raw if isinstance(raw, dict) else {"content": raw},
            }
        finally:
            await mgr.disconnect_all()

    def get_pool_metrics(self) -> Dict[str, Any]:
        if not self._pool:
            return {"enabled": False}
        metrics = self._pool.get_metrics()
        metrics["enabled"] = True
        return metrics

    @asynccontextmanager
    async def request_sessions(
        self, server_ids: List[str], context: McpCallContext
    ) -> AsyncIterator[Dict[str, McpServerSession]]:
        mgr = McpSessionManager(self._settings, self._registry, self._pool)
        sessions = await mgr.connect_servers(server_ids, context)
        try:
            yield sessions
        finally:
            await mgr.disconnect_all()

    def _normalize_tool_specs(self, server, specs: List[dict]) -> List[McpToolInfo]:
        prefix = server.tool_name_prefix or ""
        tools: List[McpToolInfo] = []
        for spec in specs:
            name = str(spec.get("name") or "")
            qname = qualify_tool_name(server.id, name, prefix)
            tools.append(
                McpToolInfo(
                    server_id=server.id,
                    name=name,
                    qualified_name=qname,
                    description=str(spec.get("description") or ""),
                    parameters=spec.get("parameters")
                    or {"type": "object", "properties": {}},
                    raw=spec,
                )
            )
        return tools

    def _resolve_raw_tool_name(self, server_id: str, tool_name: str) -> str:
        server = self._registry.require_server(server_id)
        prefix = server.tool_name_prefix or ""
        q = qualify_tool_name(server_id, tool_name, prefix)
        if tool_name == q and prefix and tool_name.startswith(prefix):
            return tool_name[len(prefix) :]
        if tool_name.startswith(f"mcp_{server_id}_"):
            return tool_name[len(f"mcp_{server_id}_") :]
        if prefix and tool_name.startswith(prefix):
            return tool_name[len(prefix) :]
        return tool_name

    async def _probe_server_status(
        self, server, context: McpCallContext, *, fetch_tools: bool = False
    ) -> McpServerStatus:
        from backend.mcp.credentials.base import get_credential_provider

        provider = get_credential_provider(server.credential_provider)
        metadata = await provider.health_metadata(server, context)
        started = time.perf_counter()
        http_ok, http_detail = await self._session_manager.probe_http_health(server)
        if not http_ok:
            return McpServerStatus(
                server_id=server.id,
                display_name=server.display_name or server.id,
                enabled=server.enabled,
                transport=server.transport,
                connected=False,
                tools=0,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=(
                    f"HTTP health check failed: {http_detail}"
                    if http_detail
                    else "HTTP health check failed"
                ),
                metadata=metadata,
            )
        tools_count = 0
        if fetch_tools:
            try:
                tools = await self.list_tools_for_server(
                    server.id, context, ephemeral=True
                )
                tools_count = len(tools)
            except Exception as exc:
                log.exception("MCP status list_tools failed server=%s", server.id)
                return McpServerStatus(
                    server_id=server.id,
                    display_name=server.display_name or server.id,
                    enabled=server.enabled,
                    transport=server.transport,
                    connected=False,
                    tools=0,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    error=str(exc),
                    metadata=metadata,
                )
        elif self._tools_cache:
            tools_count = self._tools_cache.count_cached_tools(server.id)
        latency = (time.perf_counter() - started) * 1000.0
        return McpServerStatus(
            server_id=server.id,
            display_name=server.display_name or server.id,
            enabled=server.enabled,
            transport=server.transport,
            connected=True,
            tools=tools_count,
            latency_ms=latency,
            metadata=metadata,
        )


def get_mcp_platform() -> McpPlatformService:
    global _platform
    if _platform is None:
        _platform = McpPlatformService()
    return _platform
