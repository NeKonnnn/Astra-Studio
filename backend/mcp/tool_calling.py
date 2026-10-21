"""Выбор tier tool calling для MCP (B-25)."""

from __future__ import annotations

from typing import Literal

from backend.llm_providers.base import LLMProvider

ToolCallingMode = Literal["native_openai_tools", "prompt_json_fc", "langgraph_agent"]


def resolve_tool_calling_mode(provider: LLMProvider) -> ToolCallingMode:
    from backend.settings.config import get_settings

    global_mode = (
        str(get_settings().mcp.tool_calling_mode_default or "auto").strip().lower()
    )
    if global_mode in ("native_openai_tools", "prompt_json_fc", "langgraph_agent"):
        return global_mode  # type: ignore[return-value]

    configured = provider.mcp_tool_calling_mode()
    if configured == "native_openai_tools":
        return "native_openai_tools"
    if configured == "prompt_json_fc":
        return "prompt_json_fc"
    if provider.supports_native_function_calling():
        return "native_openai_tools"
    return "prompt_json_fc"
