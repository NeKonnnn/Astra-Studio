"""ACL и фильтрация MCP tools (B-35, B-36)."""

from __future__ import annotations

from typing import Iterable, List, Optional, Set

from backend.mcp.types import McpCallContext, McpToolInfo
from backend.settings.config import McpServerConfig


def _user_groups(context: McpCallContext) -> Set[str]:
    groups: Set[str] = set()
    for key in ("groups", "ldap_groups"):
        raw = getattr(context, key, None)
        if isinstance(raw, list):
            groups.update(str(g) for g in raw if g)
    # extra_headers намеренно не учитываются: это произвольные заголовки
    # (могут прийти из запроса) и не являются доверенным источником групп.
    return groups


def has_mcp_server_access(server: McpServerConfig, context: McpCallContext) -> bool:
    if not server.enabled:
        return False
    if context.is_admin:
        return True
    grants = server.access_grants or []
    if not grants:
        return True
    user_groups = _user_groups(context)
    return bool(user_groups & set(grants))


def is_server_allowed(server: McpServerConfig, context: McpCallContext) -> bool:
    return has_mcp_server_access(server, context)


def filter_tools(
    tools: Iterable[McpToolInfo],
    *,
    server: McpServerConfig,
    context: McpCallContext,
    enabled_tool_names: Optional[Set[str]] = None,
) -> List[McpToolInfo]:
    if not is_server_allowed(server, context):
        return []
    native_whitelist = set(server.enabled_tools or [])
    qualified_whitelist = set(server.function_name_filter_list or [])
    result: List[McpToolInfo] = []
    for tool in tools:
        if native_whitelist and tool.name not in native_whitelist:
            continue
        if qualified_whitelist and tool.qualified_name not in qualified_whitelist:
            continue
        if (
            enabled_tool_names is not None
            and tool.qualified_name not in enabled_tool_names
        ):
            continue
        result.append(tool)
    return result
