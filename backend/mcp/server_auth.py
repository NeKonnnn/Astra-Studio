"""Generic MCP server auth matrix (B-34).

Реализация в session_manager.py — этот модуль для обратной совместимости импортов.
"""

from backend.mcp.session_manager import build_server_auth_headers

__all__ = ["build_server_auth_headers"]
