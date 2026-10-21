"""User/session context headers для MCP (B-38)."""

from __future__ import annotations

import os
import re
from typing import Dict, Optional

from backend.mcp.types import McpCallContext
from backend.settings.config import get_settings

_TEMPLATE_RE = re.compile(r"\{\{(\w+)\}\}")


def _render_template(value: str, context: McpCallContext) -> str:
    def _repl(match: re.Match) -> str:
        key = match.group(1).upper()
        mapping = {
            "USER_ID": context.user_id,
            "USERNAME": context.username,
            "EMAIL": getattr(context, "email", None) or "",
            "CHAT_ID": context.chat_id or "",
            "MESSAGE_ID": context.message_id or "",
        }
        return str(mapping.get(key, ""))

    return _TEMPLATE_RE.sub(_repl, value)


def build_mcp_context_headers(context: McpCallContext) -> Dict[str, str]:
    """LDAP user + OWUI session headers (GPB naming из config)."""
    settings = get_settings()
    fh = settings.mcp.forward_headers
    headers: Dict[str, str] = {}

    if fh.enabled:
        user_map = fh.user
        if user_map.username and context.username:
            headers[user_map.username] = context.username
        if user_map.user_id and context.user_id:
            headers[user_map.user_id] = context.user_id
        email_hdr = getattr(user_map, "email", None)
        email_val = getattr(context, "email", None)
        if email_hdr and email_val:
            headers[email_hdr] = str(email_val)

    chat_hdr = fh.chat_id_header or os.getenv(
        "MCP_FORWARD_HEADER_CHAT_ID", "X-OpenWebUI-Chat-Id"
    )
    msg_hdr = fh.message_id_header or os.getenv(
        "MCP_FORWARD_HEADER_MESSAGE_ID", "X-OpenWebUI-Message-Id"
    )
    if context.chat_id and chat_hdr:
        headers[chat_hdr] = context.chat_id
    if context.message_id and msg_hdr:
        headers[msg_hdr] = context.message_id

    if context.extra_headers:
        headers.update(context.extra_headers)
    return headers


def build_context_headers(context: McpCallContext) -> Dict[str, str]:
    """Alias для session_manager (backward compat)."""
    return build_mcp_context_headers(context)


def merge_server_custom_headers(
    server_custom: Optional[Dict[str, str]],
    context: McpCallContext,
) -> Dict[str, str]:
    if not server_custom:
        return {}
    return {k: _render_template(str(v), context) for k, v in server_custom.items() if v}
