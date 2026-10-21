"""MCP SDK client wrapper (паттерн Open WebUI, LIFO disconnect без shield)."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Callable, Dict, List, Optional

import anyio

from backend.mcp.http_verify import resolve_httpx_verify
from backend.mcp.sdk import import_mcp_sdk
from backend.settings.logging import get_logger

ClientSession = import_mcp_sdk().ClientSession
HttpxClientFactory = Callable[..., Any]

log = get_logger(__name__)


def _build_httpx_client(headers=None, timeout=None, auth=None, verify: bool = True):
    import httpx

    verify_setting = resolve_httpx_verify(verify)
    kwargs: Dict[str, Any] = {"follow_redirects": True, "verify": verify_setting}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


def create_httpx_client(headers=None, timeout=None, auth=None):
    return _build_httpx_client(headers=headers, timeout=timeout, auth=auth, verify=True)


def create_insecure_httpx_client(headers=None, timeout=None, auth=None):
    return _build_httpx_client(
        headers=headers, timeout=timeout, auth=auth, verify=False
    )


class McpClient:
    """Обёртка над MCP SDK ClientSession."""

    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.exit_stack: Optional[AsyncExitStack] = None

    async def connect_streams(
        self,
        streams_context,
        *,
        init_timeout: float = 10.0,
        server_id: Optional[str] = None,
        mcp_url: Optional[str] = None,
    ) -> None:
        import time

        started = time.perf_counter()
        async with AsyncExitStack() as exit_stack:
            try:
                transport = await exit_stack.enter_async_context(streams_context)
                read_stream, write_stream = (transport[0], transport[1])
                session_context = ClientSession(read_stream, write_stream)
                self.session = await exit_stack.enter_async_context(session_context)
                with anyio.fail_after(init_timeout):
                    await self.session.initialize()
                self.exit_stack = exit_stack.pop_all()
            except Exception as exc:
                if server_id and mcp_url:
                    log.debug(
                        "MCP session connect fail server=%s phase=initialize mcp_url=%s error=%s: %s elapsed_ms=%.1f",
                        server_id,
                        mcp_url,
                        type(exc).__name__,
                        exc,
                        (time.perf_counter() - started) * 1000.0,
                    )
                else:
                    log.exception(
                        "MCP connect_streams failed init_timeout=%ss", init_timeout
                    )
                await self.disconnect()
                raise

    async def list_tool_specs(self) -> List[Dict[str, Any]]:
        if not self.session:
            msg = "MCP client is not connected."
            raise RuntimeError(msg)
        result = await self.session.list_tools()
        specs: List[Dict[str, Any]] = []
        for tool in result.tools:
            specs.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema or {},
                }
            )
        return specs

    async def call_tool(self, function_name: str, function_args: dict) -> Any:
        if not self.session:
            msg = "MCP client is not connected."
            raise RuntimeError(msg)
        result = await self.session.call_tool(function_name, function_args)
        if not result:
            msg = "No result returned from MCP tool call."
            raise RuntimeError(msg)
        result_dict = result.model_dump(mode="json")
        content = result_dict.get("content", result_dict)
        if result.isError:
            msg = str(content)
            raise RuntimeError(msg)
        return content

    async def list_resources(
        self, cursor: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        if not self.session:
            msg = "MCP client is not connected."
            raise RuntimeError(msg)
        result = await self.session.list_resources(cursor=cursor)
        if not result:
            msg = "No result returned from MCP list_resources call."
            raise RuntimeError(msg)
        return result.model_dump().get("resources", [])

    async def read_resource(self, uri: str) -> Dict[str, Any]:
        if not self.session:
            msg = "MCP client is not connected."
            raise RuntimeError(msg)
        result = await self.session.read_resource(uri)
        if not result:
            msg = "No result returned from MCP read_resource call."
            raise RuntimeError(msg)
        return result.model_dump()

    async def disconnect(self) -> None:
        """LIFO cleanup без asyncio.shield (требование MCP SDK)."""
        exit_stack = self.exit_stack
        if exit_stack is None:
            return
        self.exit_stack = None
        self.session = None
        try:
            await exit_stack.aclose()
        except TimeoutError:
            log.warning("McpClient.disconnect() timed out")
        except RuntimeError as exc:
            log.debug("McpClient.disconnect() suppressed RuntimeError: %s", exc)
        except Exception as exc:
            log.exception("Ошибка операции")
            log.debug("McpClient.disconnect() error: %s", exc)
