"""MCP agent loop — provider-agnostic фасад (B-24, B-32)."""

from __future__ import annotations

import inspect
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.llm_providers import get_registry
from backend.mcp.connection import McpServerSession
from backend.mcp.platform import McpPlatformService, get_mcp_platform
from backend.mcp.result_parser import (
    append_download_links_to_content,
    enrich_download_links,
    format_parsed_for_llm,
    format_tool_result_for_ui,
    parse_mcp_result_to_struct,
    preview_parsed_for_ui,
)
from backend.mcp.text_tool_calls import content_has_text_tool_calls
from backend.mcp.tool_adapter import to_openai_tools
from backend.mcp.tool_calling import resolve_tool_calling_mode
from backend.agents.step_debug import (
    log_llm_call,
    log_llm_result,
    log_run_end,
    log_run_start,
    log_tool_call,
    log_tool_result,
)
from backend.agents.subagents import NATIVE_SERVER_ID, execute_native_tool
from backend.mcp.types import AgentLoopResult, McpCallContext, McpToolInfo
from backend.settings.cef_logger.cef_logger import log_cef_event
from backend.settings.config import get_settings
from backend.settings.logging import get_logger

log = get_logger(__name__)

McpEventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


async def _run_prompt_json_fc(**kwargs: Any) -> AgentLoopResult:
    """Вызов prompt_json_fc с фильтрацией kwargs под сигнатуру (устойчиво к частичному деплою)."""
    from backend.mcp.prompt_fc_handler import run_prompt_json_fc

    params = inspect.signature(run_prompt_json_fc).parameters
    filtered = {key: value for key, value in kwargs.items() if key in params}
    dropped = sorted(set(kwargs) - set(filtered))
    if dropped:
        log.debug("prompt_json_fc: skipped unsupported kwargs: %s", ", ".join(dropped))
    return await run_prompt_json_fc(**filtered)


async def emit_mcp_tool_start(
    callback: Optional[McpEventCallback],
    *,
    server_id: str,
    tool: str,
    qualified_name: str,
    call_id: Optional[str] = None,
    arguments: Optional[Dict[str, Any]] = None,
) -> None:
    if not callback:
        return
    payload: Dict[str, Any] = {
        "type": "mcp_tool_start",
        "server_id": server_id,
        "tool": tool,
        "qualified_name": qualified_name,
        "call_id": call_id or uuid.uuid4().hex,
        "timestamp": time.time(),
    }
    if arguments is not None:
        payload["arguments"] = arguments
    await callback(payload)


async def emit_mcp_tool_end(
    callback: Optional[McpEventCallback],
    *,
    server_id: str,
    tool: str,
    qualified_name: str,
    success: bool,
    duration_ms: int,
    error: Optional[str] = None,
    result_preview: Optional[str] = None,
    has_image: bool = False,
    has_audio: bool = False,
    has_resource: bool = False,
    download_urls: Optional[List[Dict[str, str]]] = None,
    call_id: Optional[str] = None,
    arguments: Optional[Dict[str, Any]] = None,
    result: Optional[str] = None,
) -> None:
    if not callback:
        return
    payload: Dict[str, Any] = {
        "type": "mcp_tool_end",
        "server_id": server_id,
        "tool": tool,
        "qualified_name": qualified_name,
        "call_id": call_id or uuid.uuid4().hex,
        "success": success,
        "duration_ms": duration_ms,
        "timestamp": time.time(),
    }
    if arguments is not None:
        payload["arguments"] = arguments
    if result:
        payload["result"] = result
    if error:
        payload["error"] = error
    if result_preview:
        payload["result_preview"] = result_preview
    if has_image:
        payload["has_image"] = True
    if has_audio:
        payload["has_audio"] = True
    if has_resource:
        payload["has_resource"] = True
    if download_urls:
        payload["download_urls"] = download_urls
    await callback(payload)


_agent_loop: Optional["McpAgentLoop"] = None


def _find_tool(name: str, tools: List[McpToolInfo]) -> Optional[McpToolInfo]:
    for tool in tools:
        if tool.qualified_name == name or tool.name == name:
            return tool
    return None


class McpAgentLoop:

    def __init__(self, platform: Optional[McpPlatformService] = None):
        self._platform = platform or get_mcp_platform()

    async def run(
        self,
        *,
        messages: List[Dict[str, Any]],
        model_path: str,
        mcp_tools: List[McpToolInfo],
        mcp_context: McpCallContext,
        enabled_server_ids: List[str],
        max_iterations: int = 10,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        request_extra: Optional[Dict[str, Any]] = None,
        event_callback: Optional[McpEventCallback] = None,
        native_tools: Optional[List[McpToolInfo]] = None,
        subagent_ctx=None,
        subagent_config=None,
    ) -> AgentLoopResult:
        all_tools = list(native_tools or []) + list(mcp_tools or [])
        native_n = len(native_tools or [])
        mcp_n = len(mcp_tools or [])
        tool_names = [t.qualified_name or t.name for t in all_tools]
        depth = (
            int(getattr(subagent_ctx, "depth", 0) or 0)
            if subagent_ctx is not None
            else 0
        )
        if not all_tools:
            registry = await get_registry()
            provider, model_id = registry.resolve(model_path)
            if not model_id:
                models = await provider.list_models()
                model_id = models[0].model_id if models else ""
            log_run_start(
                mode="plain",
                max_iterations=1,
                model=f"{getattr(provider, 'id', '')}/{model_id}".strip("/"),
                chat_id=mcp_context.chat_id,
                tool_names=[],
                mcp_servers=enabled_server_ids,
                native_tools=0,
                mcp_tools=0,
                depth=depth,
            )
            log_llm_call(
                step=1,
                limit=1,
                kind="plain",
                model=f"{getattr(provider, 'id', '')}/{model_id}".strip("/"),
                messages_count=len(messages),
            )
            t0 = time.perf_counter()
            content = await provider.chat(
                messages, model_id, temperature=temperature, max_tokens=max_tokens, request_extra=request_extra
            )
            log_llm_result(
                step=1,
                limit=1,
                has_tool_calls=False,
                content_preview=content or "",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
            log_run_end(
                mode="plain",
                steps_used=1,
                limit=1,
                tool_calls_executed=0,
                reason="no_tools",
                content_preview=content or "",
            )
            return AgentLoopResult(content=content or "", mode="plain", iterations=1)
        async with self._platform.request_sessions(enabled_server_ids, mcp_context) as sessions:
            registry = await get_registry()
            provider, model_id = registry.resolve(model_path)
            if not model_id:
                models = await provider.list_models()
                model_id = models[0].model_id if models else ""
            mode = resolve_tool_calling_mode(provider)
            fc_model = (get_settings().mcp.fc_task_model or "").strip() or None
            log_run_start(
                mode=mode,
                max_iterations=max_iterations,
                model=f"{getattr(provider, 'id', '')}/{model_id}".strip("/"),
                chat_id=mcp_context.chat_id,
                tool_names=tool_names,
                mcp_servers=enabled_server_ids,
                native_tools=native_n,
                mcp_tools=mcp_n,
                depth=depth,
            )
            if mode == "prompt_json_fc":
                return await _run_prompt_json_fc(
                    messages=messages,
                    model_path=model_path,
                    tools=all_tools,
                    context=mcp_context,
                    platform=self._platform,
                    sessions=sessions,
                    fc_model_path=fc_model,
                    max_iterations=max_iterations,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    request_extra=request_extra,
                    event_callback=event_callback,
                    subagent_ctx=subagent_ctx,
                    subagent_config=subagent_config,
                )
            return await self._run_native(
                messages=messages,
                provider=provider,
                model_id=model_id,
                tools=all_tools,
                context=mcp_context,
                sessions=sessions,
                max_iterations=max_iterations,
                temperature=temperature,
                max_tokens=max_tokens,
                request_extra=request_extra,
                event_callback=event_callback,
                subagent_ctx=subagent_ctx,
                subagent_config=subagent_config,
            )

    async def _run_native(
        self,
        *,
        messages: List[Dict[str, Any]],
        provider,
        model_id: str,
        tools: List[McpToolInfo],
        context: McpCallContext,
        sessions: Dict[str, McpServerSession],
        max_iterations: int,
        temperature: float,
        max_tokens: int,
        request_extra: Optional[Dict[str, Any]],
        event_callback: Optional[McpEventCallback] = None,
        subagent_ctx=None,
        subagent_config=None,
    ) -> AgentLoopResult:
        openai_tools = to_openai_tools(tools)
        working = list(messages)
        tool_calls_executed = 0
        attachments: List[Dict[str, str]] = []
        req_extra = dict(request_extra or {})
        model_label = f"{getattr(provider, 'id', '')}/{model_id}".strip("/")
        limit = max(1, max_iterations)
        for iteration in range(limit):
            step = iteration + 1
            log_llm_call(
                step=step,
                limit=limit,
                kind="chat_completion",
                model=model_label,
                messages_count=len(working),
                has_tools_schema=bool(openai_tools),
            )
            t_llm = time.perf_counter()
            try:
                result = await provider.chat_completion(
                    working,
                    model_id,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=openai_tools,
                    request_extra=req_extra,
                )
            except Exception:
                log.exception("Native MCP FC failed, fallback prompt_json_fc")
                return await _run_prompt_json_fc(
                    messages=messages,
                    model_path=f"{provider.id}/{model_id}",
                    tools=tools,
                    context=context,
                    platform=self._platform,
                    sessions=sessions,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    request_extra=request_extra,
                    event_callback=event_callback,
                    subagent_ctx=subagent_ctx,
                    subagent_config=subagent_config,
                )
            llm_ms = int((time.perf_counter() - t_llm) * 1000)
            if not result.tool_calls:
                if iteration == 0 and content_has_text_tool_calls(result.content or ""):
                    log.warning(
                        "Native MCP: model returned text <tool_call> instead of structured tool_calls; fallback to prompt_json_fc"
                    )
                    return await _run_prompt_json_fc(
                        messages=messages,
                        model_path=f"{provider.id}/{model_id}",
                        tools=tools,
                        context=context,
                        platform=self._platform,
                        sessions=sessions,
                        max_iterations=max_iterations,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        request_extra=request_extra,
                        event_callback=event_callback,
                        subagent_ctx=subagent_ctx,
                        subagent_config=subagent_config,
                    )
                log_llm_result(
                    step=step,
                    limit=limit,
                    has_tool_calls=False,
                    content_preview=result.content or "",
                    duration_ms=llm_ms,
                )
                log_run_end(
                    mode="native_openai_tools",
                    steps_used=step,
                    limit=limit,
                    tool_calls_executed=tool_calls_executed,
                    reason="final_answer",
                    content_preview=result.content or "",
                )
                return AgentLoopResult(
                    content=result.content or "",
                    tool_calls_executed=tool_calls_executed,
                    mode="native_openai_tools",
                    iterations=step,
                )
            tc_names = [tc.name for tc in result.tool_calls]
            log_llm_result(
                step=step,
                limit=limit,
                has_tool_calls=True,
                tool_names=tc_names,
                content_preview=result.content or "",
                duration_ms=llm_ms,
            )
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": __import__("json").dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in result.tool_calls
                ],
            }
            working.append(assistant_msg)
            for tc in result.tool_calls:
                tool_info = _find_tool(tc.name, tools)
                if not tool_info:
                    log_tool_result(
                        step=step,
                        limit=limit,
                        tool=tc.name,
                        success=False,
                        error="Unknown tool",
                    )
                    working.append({"role": "tool", "tool_call_id": tc.id, "content": f"Unknown tool: {tc.name}"})
                    continue
                session = sessions.get(tool_info.server_id)
                started = time.perf_counter()
                call_id = uuid.uuid4().hex
                tool_args = tc.arguments if isinstance(tc.arguments, dict) else {}
                tool_kind = (
                    "native" if tool_info.server_id == NATIVE_SERVER_ID else "mcp"
                )
                log_tool_call(
                    step=step,
                    limit=limit,
                    tool=tool_info.qualified_name or tool_info.name,
                    server_id=tool_info.server_id,
                    kind=tool_kind,
                    arguments=tool_args,
                )
                await emit_mcp_tool_start(
                    event_callback,
                    server_id=tool_info.server_id,
                    tool=tool_info.name,
                    qualified_name=tool_info.qualified_name,
                    call_id=call_id,
                    arguments=tool_args,
                )
                if tool_info.server_id == NATIVE_SERVER_ID:
                    try:
                        content = await execute_native_tool(
                            tool_info,
                            tool_args,
                            subagent_ctx=subagent_ctx,
                            subagent_config=subagent_config,
                        )
                        outcome = "success"
                        preview = (content or "")[:500] or None
                        has_image = has_audio = has_resource = False
                        download_links = None
                        result_ui = content
                    except Exception as exc:
                        log.exception("Native tool error")
                        content = f"Native tool error: {exc}"
                        outcome = "failure"
                        preview = None
                        has_image = has_audio = has_resource = False
                        download_links = None
                        result_ui = content
                elif not session:
                    log_tool_result(
                        step=step,
                        limit=limit,
                        tool=tool_info.qualified_name or tool_info.name,
                        success=False,
                        error=f"MCP session unavailable: {tool_info.server_id}",
                    )
                    working.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"MCP session unavailable: {tool_info.server_id}",
                        }
                    )
                    continue
                else:
                    log_cef_event(
                        "INT001",
                        extra={
                            "cs1": tool_info.qualified_name,
                            "cs1Label": "ToolName",
                            "cs2": context.chat_id or "-",
                            "cs2Label": "ChatId",
                            "cs3": tool_info.server_id,
                            "cs3Label": "McpServer",
                            "duser": context.username,
                        },
                    )
                    try:
                        raw = await self._platform.call_tool(
                            tool_info.server_id, tool_info.name, tc.arguments, context, session
                        )
                        parsed = parse_mcp_result_to_struct(raw)
                        content = format_parsed_for_llm(parsed) or str(raw)
                        outcome = "success"
                        preview = preview_parsed_for_ui(parsed)
                        has_image = bool(parsed.images)
                        has_audio = bool(parsed.audio)
                        has_resource = bool(parsed.resources)
                        download_links = enrich_download_links(parsed, tool_info.server_id)
                        result_ui = format_tool_result_for_ui(parsed, raw)
                        for link in download_links:
                            if link["url"] not in {x["url"] for x in attachments}:
                                attachments.append(link)
                    except Exception as exc:
                        log.exception("Ошибка операции")
                        content = f"MCP tool error: {exc}"
                        outcome = "failure"
                        preview = None
                        has_image = has_audio = has_resource = False
                        download_links = None
                        result_ui = content
                duration_ms = int((time.perf_counter() - started) * 1000)
                log_tool_result(
                    step=step,
                    limit=limit,
                    tool=tool_info.qualified_name or tool_info.name,
                    success=outcome == "success",
                    duration_ms=duration_ms,
                    error=None if outcome == "success" else content,
                    result_preview=preview or content or "",
                )
                log_cef_event(
                    "INT002",
                    extra={
                        "cs1": tool_info.qualified_name,
                        "cs1Label": "ToolName",
                        "cs3": tool_info.server_id,
                        "cs3Label": "McpServer",
                        "cn1": duration_ms,
                        "outcome": outcome,
                    },
                )
                await emit_mcp_tool_end(
                    event_callback,
                    server_id=tool_info.server_id,
                    tool=tool_info.name,
                    qualified_name=tool_info.qualified_name,
                    success=outcome == "success",
                    duration_ms=duration_ms,
                    error=None if outcome == "success" else content,
                    result_preview=preview if outcome == "success" else None,
                    has_image=has_image if outcome == "success" else False,
                    has_audio=has_audio if outcome == "success" else False,
                    has_resource=has_resource if outcome == "success" else False,
                    download_urls=download_links or None,
                    call_id=call_id,
                    arguments=tool_args,
                    result=result_ui if outcome == "success" else content,
                )
                tool_calls_executed += 1
                working.append({"role": "tool", "tool_call_id": tc.id, "content": content})
        log_llm_call(
            step=limit,
            limit=limit,
            kind="chat_final",
            model=model_label,
            messages_count=len(working),
        )
        t_final = time.perf_counter()
        final = await provider.chat(
            working, model_id, temperature=temperature, max_tokens=max_tokens, request_extra=req_extra
        )
        content = append_download_links_to_content(final, attachments)
        log_llm_result(
            step=limit,
            limit=limit,
            has_tool_calls=False,
            content_preview=content or "",
            duration_ms=int((time.perf_counter() - t_final) * 1000),
        )
        log_run_end(
            mode="native_openai_tools",
            steps_used=limit,
            limit=limit,
            tool_calls_executed=tool_calls_executed,
            reason="max_iterations",
            content_preview=content or "",
        )
        return AgentLoopResult(
            content=content,
            tool_calls_executed=tool_calls_executed,
            mode="native_openai_tools",
            iterations=max_iterations,
            attachments=attachments,
        )


def get_mcp_agent_loop() -> McpAgentLoop:
    global _agent_loop
    if _agent_loop is None:
        _agent_loop = McpAgentLoop()
    return _agent_loop
