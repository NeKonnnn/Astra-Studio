"""Stdio transport (spawn subprocess via MCP SDK)."""

from __future__ import annotations

from typing import List, Optional

from backend.mcp.sdk import import_mcp_sdk_submodule

_stdio = import_mcp_sdk_submodule("mcp.client.stdio")
StdioServerParameters = _stdio.StdioServerParameters
stdio_client = _stdio.stdio_client


async def connect_stdio(
    client,
    *,
    command: str,
    args: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    timeout: float = 120.0,
) -> None:
    params = StdioServerParameters(
        command=command,
        args=list(args or []),
        cwd=cwd,
    )
    streams_context = stdio_client(params)
    await client.connect_streams(streams_context, init_timeout=min(timeout, 30.0))
