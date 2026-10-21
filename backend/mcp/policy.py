"""MCP policy helpers (B-29 allowlist).

Реализация в chat_integration.py — этот модуль для обратной совместимости импортов.
"""

from backend.mcp.chat_integration import is_mcp_provider_allowed

__all__ = ["is_mcp_provider_allowed"]
