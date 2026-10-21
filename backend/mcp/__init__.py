"""Универсальная MCP-платформа AstraChat."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "McpPlatformService",
    "get_mcp_platform",
    "McpAgentLoop",
    "get_mcp_agent_loop",
]

if TYPE_CHECKING:
    from backend.mcp.agent_loop import McpAgentLoop, get_mcp_agent_loop
    from backend.mcp.platform import McpPlatformService, get_mcp_platform


def __getattr__(name: str):
    if name in ("McpPlatformService", "get_mcp_platform"):
        from backend.mcp.platform import McpPlatformService, get_mcp_platform

        return McpPlatformService if name == "McpPlatformService" else get_mcp_platform
    if name in ("McpAgentLoop", "get_mcp_agent_loop"):
        from backend.mcp.agent_loop import McpAgentLoop, get_mcp_agent_loop

        return McpAgentLoop if name == "McpAgentLoop" else get_mcp_agent_loop
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
