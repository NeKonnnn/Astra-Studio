"""MCP streaming events для Socket.IO (B-22).

Реализация живёт в agent_loop.py, чтобы agent loop не падал при неполном деплое
отдельного файла events.py в образе.
"""

from backend.mcp.agent_loop import (
    McpEventCallback,
    emit_mcp_tool_end,
    emit_mcp_tool_start,
)

__all__ = ["McpEventCallback", "emit_mcp_tool_start", "emit_mcp_tool_end"]
