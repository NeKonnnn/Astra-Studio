"""Интеграция MCP в chat pipeline (B-40)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.agents.config import resolve_recursion_limit
from backend.agents.step_debug import describe_limit_source, log_pre_loop
from backend.agents.subagents import (
    SubagentRunContext,
    build_subagent_tools,
    load_subagent_agent_names,
    subagents_from_profile,
)
from backend.llm_providers.routing import (
    merge_sampling_request_extra,
    thinking_request_extra,
)
from backend.mcp.agent_loop import get_mcp_agent_loop
from backend.mcp.platform import get_mcp_platform
from backend.mcp.resolvers import build_chat_messages, parse_mcp_server_ids
from backend.mcp.types import AgentLoopResult, McpCallContext, McpToolInfo
from backend.settings.config import get_settings
from backend.settings.logging import get_logger

log = get_logger(__name__)

McpEventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


def _extract_provider_id(model_path: Optional[str]) -> str:
    raw = str(model_path or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("llm-svc://"):
        rest = raw[len("llm-svc://") :].strip().lstrip("/")
        if "/" in rest:
            return rest.split("/", 1)[0].strip()
        return ""
    if "/" in raw:
        return raw.split("/", 1)[0].strip()
    return raw


def is_mcp_provider_allowed(model_path: Optional[str]) -> bool:
    """MCP LLM provider allowlist (B-29)."""
    allowlist = get_settings().mcp.llm_provider_allowlist
    if not allowlist:
        return True
    allowed = [str(x).strip() for x in allowlist if str(x).strip()]
    if not allowed:
        return True
    provider_id = _extract_provider_id(model_path)
    if not provider_id:
        return True
    return provider_id in allowed


def build_mcp_context_from_user(
    user: dict, *, chat_id: Optional[str] = None, message_id: Optional[str] = None
) -> McpCallContext:
    return McpCallContext(
        user_id=str(user.get("user_id") or user.get("username") or ""),
        username=str(user.get("username") or ""),
        chat_id=chat_id,
        message_id=message_id,
        is_admin=bool(user.get("is_admin")),
        groups=list(user.get("groups") or user.get("ldap_groups") or []),
        ldap_groups=list(user.get("ldap_groups") or user.get("groups") or []),
    )


async def maybe_run_mcp_agent(
    *,
    tool_ids: Optional[List[str]],
    user_message: str,
    history: Optional[List[Dict[str, Any]]],
    system_prompt: Optional[str],
    model_path: str,
    mcp_context: McpCallContext,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    enable_thinking: bool = False,
    event_callback: Optional[McpEventCallback] = None,
    max_iterations: Optional[int] = None,
    native_tools: Optional[List[McpToolInfo]] = None,
    subagent_ctx: Optional[SubagentRunContext] = None,
    subagent_config=None,
    agent_profile: Optional[dict] = None,
) -> Optional[AgentLoopResult]:
    platform = get_mcp_platform()
    native_tools = list(native_tools or [])
    sub_cfg = subagent_config
    sub_ctx = subagent_ctx

    if agent_profile is not None and sub_cfg is None:
        sub_cfg = subagents_from_profile(agent_profile)
    if agent_profile is not None and sub_cfg.enabled and not native_tools:
        parent_id = agent_profile.get("agent_id")
        names = await load_subagent_agent_names(
            sub_cfg.agent_ids,
            user_id=mcp_context.user_id or None,
        )
        if parent_id is not None and agent_profile.get("name"):
            names[int(parent_id)] = str(agent_profile["name"])
        native_tools = build_subagent_tools(
            sub_cfg,
            parent_agent_id=int(parent_id) if parent_id is not None else None,
            agent_names=names,
        )

    has_native = bool(native_tools)
    mcp_tools: List[McpToolInfo] = []
    enabled_ids: List[str] = []

    if (
        platform.enabled
        and platform.initialized
        and is_mcp_provider_allowed(model_path)
    ):
        server_ids = parse_mcp_server_ids(tool_ids)
        if server_ids:
            enabled_ids = platform.list_enabled_server_ids(server_ids)
            for sid in enabled_ids:
                try:
                    mcp_tools.extend(
                        await platform.list_tools_for_server(sid, mcp_context)
                    )
                except Exception:
                    log.exception("MCP list_tools failed server=")
            mcp_tools = platform.filter_tools_by_context(
                mcp_tools, mcp_context, enabled_server_ids=enabled_ids
            )

    if not has_native and not mcp_tools:
        log_pre_loop(
            phase="skip_no_tools",
            chat_id=mcp_context.chat_id,
            agent_id=(agent_profile or {}).get("agent_id") if agent_profile else None,
            detail="нет MCP и встроенных инструментов — цикл агента не запускается",
        )
        return None

    if not has_native and not platform.enabled:
        log_pre_loop(
            phase="skip_mcp_disabled",
            chat_id=mcp_context.chat_id,
            agent_id=(agent_profile or {}).get("agent_id") if agent_profile else None,
            detail="платформа MCP выключена",
        )
        return None

    step_limit = max_iterations
    limit_source = "задан явно (max_iterations)"
    if step_limit is None:
        step_limit = resolve_recursion_limit(agent_profile)
        limit_source = describe_limit_source(agent_profile)

    log.debug(
        "MCP chat tool_ids=%s servers=%s native_tools=%s max_iterations=%s source=%s",
        tool_ids,
        enabled_ids,
        len(native_tools),
        step_limit,
        limit_source,
    )
    log_pre_loop(
        phase="agent_loop_enter",
        chat_id=mcp_context.chat_id,
        agent_id=(agent_profile or {}).get("agent_id") if agent_profile else None,
        recursion_limit=step_limit,
        limit_source=limit_source,
        detail=(
            f"MCP-инструментов={len(mcp_tools)} встроенных={len(native_tools)} "
            f"серверы={enabled_ids} модель={model_path}"
        ),
    )
    messages = build_chat_messages(
        user_message=user_message, history=history, system_prompt=system_prompt
    )
    request_extra = thinking_request_extra(bool(enable_thinking))
    request_extra = merge_sampling_request_extra(request_extra)
    loop = get_mcp_agent_loop()
    return await loop.run(
        messages=messages,
        model_path=model_path,
        mcp_tools=mcp_tools,
        mcp_context=mcp_context,
        enabled_server_ids=enabled_ids,
        max_iterations=step_limit,
        temperature=temperature,
        max_tokens=max_tokens,
        request_extra=request_extra,
        event_callback=event_callback,
        native_tools=native_tools,
        subagent_ctx=sub_ctx,
        subagent_config=sub_cfg,
    )


async def run_mcp_for_chat(
    *,
    tool_ids: Optional[List[str]],
    user_message: str,
    history: Optional[List[Dict[str, Any]]],
    system_prompt: Optional[str],
    model_path: str,
    user: dict,
    chat_id: Optional[str] = None,
    message_id: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    enable_thinking: bool = False,
    emit_event: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    agent_profile: Optional[dict] = None,
    subagent_executor=None,
    inline_attachments: Optional[List[Any]] = None,
) -> Optional[AgentLoopResult]:
    """
    Единая точка входа MCP для socket и REST chat (B-40).

    Multi-LLM: вызывается параллельно per-model с разным ``model_path``
    (CORSUR/… vs Phoenix/…). События ``chat_mcp_event`` — добавляйте
    ``model`` в ``emit_event`` callback на стороне handler.
    """
    mcp_ctx = build_mcp_context_from_user(user, chat_id=chat_id, message_id=message_id)
    sub_cfg = subagents_from_profile(agent_profile) if agent_profile else None
    sub_ctx = None
    if agent_profile and sub_cfg and sub_cfg.enabled:
        from backend.agents.subagent_runner import run_isolated_subagent

        parent_id = agent_profile.get("agent_id")
        step_limit = resolve_recursion_limit(agent_profile)

        async def _executor(**kwargs):
            return await run_isolated_subagent(
                target_agent_id=kwargs["target_agent_id"],
                prompt=kwargs["prompt"],
                parent_profile=kwargs["parent_profile"],
                user=user,
                user_id=mcp_ctx.user_id or None,
                depth=kwargs.get("depth", 0),
                remaining_steps=kwargs.get("remaining_steps", step_limit - 1),
                enable_thinking=enable_thinking,
                emit_event=emit_event,
                inline_attachments=kwargs.get("inline_attachments"),
            )

        sub_ctx = SubagentRunContext(
            parent_agent_id=int(parent_id) if parent_id is not None else None,
            parent_profile=dict(agent_profile),
            user=user,
            user_id=mcp_ctx.user_id or None,
            depth=0,
            remaining_steps=step_limit - 1,
            executor=subagent_executor or _executor,
            inline_attachments=list(inline_attachments or []) or None,
        )

    return await maybe_run_mcp_agent(
        tool_ids=tool_ids,
        user_message=user_message,
        history=history,
        system_prompt=system_prompt,
        model_path=model_path,
        mcp_context=mcp_ctx,
        temperature=temperature,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
        event_callback=emit_event,
        agent_profile=agent_profile,
        subagent_ctx=sub_ctx,
        subagent_config=sub_cfg,
    )
