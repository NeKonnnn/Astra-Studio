"""Хелперы разбора tool_ids и сборки сообщений для MCP agent loop."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def mcp_server_tool_id(server_id: str) -> str:
    return f"server:mcp:{server_id}"


def resolve_chat_tool_ids(payload_tool_ids: Optional[List[str]] = None) -> List[str]:
    """Только явные tool_ids с клиента (переключатель MCP в UI). Без серверного fallback."""
    explicit: List[str] = []
    for raw in payload_tool_ids or []:
        tid = str(raw or "").strip()
        if tid:
            explicit.append(tid)
    return explicit


def parse_mcp_server_ids(tool_ids: Optional[List[str]]) -> List[str]:
    """Форматы id: ``server:mcp:{id}`` | ``mcp:{id}`` | plain ``{id}``."""
    result: List[str] = []
    for raw in tool_ids or []:
        tid = str(raw or "").strip()
        if not tid:
            continue
        if tid.startswith("server:mcp:"):
            sid = tid.split(":", 2)[2].strip()
        elif tid.startswith("mcp:"):
            sid = tid.split(":", 1)[1].strip()
        else:
            sid = tid
        if sid and sid not in result:
            result.append(sid)
    return result


def build_chat_messages(
    *,
    user_message: str,
    history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    if system_prompt and str(system_prompt).strip():
        messages.append({"role": "system", "content": str(system_prompt).strip()})
    for entry in history or []:
        role = str(entry.get("role") or "").strip()
        content = entry.get("content")
        if role in ("user", "assistant", "system") and content is not None:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages
