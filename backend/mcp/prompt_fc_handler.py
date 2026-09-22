"""Lightweight prompt+JSON function calling fallback (порт OWUI B-42)."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from backend.llm_providers import get_registry
from backend.mcp.connection import McpServerSession
from backend.mcp.events import McpEventCallback, emit_mcp_tool_end, emit_mcp_tool_start
from backend.mcp.platform import McpPlatformService
from backend.mcp.result_parser import (
    append_download_links_to_content,
    enrich_download_links,
    format_parsed_for_llm,
    format_tool_result_for_ui,
    parse_mcp_result_to_struct,
    preview_parsed_for_ui,
)
from backend.agents.step_debug import (
    log_llm_call,
    log_llm_result,
    log_run_end,
    log_tool_call,
    log_tool_result,
)
from backend.agents.subagents import NATIVE_SERVER_ID, execute_native_tool
from backend.mcp.types import AgentLoopResult, McpCallContext, McpToolInfo
from backend.settings.config import get_settings
from backend.settings.logging import get_logger

log = get_logger(__name__)

DEFAULT_PROMPT_TEMPLATE = 'Available Tools: {{TOOLS}}\n\nYour task is to choose and return the correct tool(s) from the list of available tools based on the query.\n\nRules:\n- Return only the JSON object, without any additional text.\n- If no tools match, return: {"tool_calls": []}\n- For multi-step tasks (presentations, documents, several edits), call ALL required tools across iterations until the task is fully complete.\n- Do NOT return empty tool_calls until every step is done (e.g. create → add slides/content → save/export).\n- You may return multiple tools in one response when they are independent.\n- Format:\n{\n  "tool_calls": [\n    {"name": "toolName1", "parameters": {"key1": "value1"}}\n  ]\n}\n'


def _render_tools_prompt(tools: List[McpToolInfo]) -> str:
    specs = []
    for tool in tools:
        specs.append({"name": tool.qualified_name, "description": tool.description, "parameters": tool.parameters})
    tools_json = json.dumps(specs, ensure_ascii=False)
    return DEFAULT_PROMPT_TEMPLATE.replace("{{TOOLS}}", tools_json)


def _extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _find_tool(name: str, tools: List[McpToolInfo]) -> Optional[McpToolInfo]:
    for tool in tools:
        if tool.qualified_name == name or tool.name == name:
            return tool
    return None


async def run_prompt_json_fc(
    *,
    messages: List[Dict[str, Any]],
    model_path: str,
    tools: List[McpToolInfo],
    context: McpCallContext,
    platform: McpPlatformService,
    sessions: Dict[str, McpServerSession],
    fc_model_path: Optional[str] = None,
    max_iterations: int = 3,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    request_extra: Optional[Dict[str, Any]] = None,
    event_callback: Optional[McpEventCallback] = None,
    subagent_ctx=None,
    subagent_config=None,
) -> AgentLoopResult:
    settings = get_settings()
    effective_model_path = fc_model_path or model_path or settings.mcp.fc_task_model or model_path
    registry = await get_registry()
    provider, model_id = registry.resolve(effective_model_path)
    if not model_id:
        models = await provider.list_models()
        model_id = models[0].model_id if models else ""
    user_query = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_query = str(msg.get("content") or "")
            break
    tool_calls_executed = 0
    attachments: List[Dict[str, str]] = []
    working_messages = list(messages)
    req_extra = dict(request_extra or {})
    limit = max(1, max_iterations)
    model_label = f"{getattr(provider, 'id', '')}/{model_id}".strip("/")
    for iteration in range(limit):
        step = iteration + 1
        prompt = _render_tools_prompt(tools)
        fc_messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"History:\n{json.dumps(working_messages[-4:], ensure_ascii=False)}\nQuery: {user_query}",
            },
        ]
        log_llm_call(
            step=step,
            limit=limit,
            kind="prompt_json_fc",
            model=model_label,
            messages_count=len(fc_messages),
            has_tools_schema=True,
        )
        t_llm = time.perf_counter()
        result = await provider.chat_completion(
            fc_messages, model_id, temperature=temperature, max_tokens=max_tokens, request_extra=req_extra
        )
        llm_ms = int((time.perf_counter() - t_llm) * 1000)
        payload = _extract_json_object(result.content)
        if not payload:
            log_llm_result(
                step=step,
                limit=limit,
                has_tool_calls=False,
                content_preview=result.content or "",
                duration_ms=llm_ms,
            )
            log_run_end(
                mode="prompt_json_fc",
                steps_used=step,
                limit=limit,
                tool_calls_executed=tool_calls_executed,
                reason="unparsed_fc_json",
                content_preview=result.content or "",
            )
            return AgentLoopResult(
                content=result.content or "Не удалось распознать вызов инструмента.",
                tool_calls_executed=tool_calls_executed,
                mode="prompt_json_fc",
                iterations=step,
            )
        calls = payload.get("tool_calls") or []
        if not calls:
            log_llm_result(
                step=step,
                limit=limit,
                has_tool_calls=False,
                content_preview=result.content or "",
                duration_ms=llm_ms,
            )
            if tool_calls_executed > 0 and step < limit:
                working_messages.append(
                    {
                        "role": "user",
                        "content": "The task is likely not finished yet. If more MCP tools are required, respond with tool_calls JSON only. Return empty tool_calls only when the user request is fully completed (including export/download if applicable).",
                    }
                )
                continue
            log_llm_call(
                step=step,
                limit=limit,
                kind="chat_final",
                model=model_label,
                messages_count=len(working_messages),
            )
            t_final = time.perf_counter()
            final = await provider.chat(
                working_messages, model_id, temperature=temperature, max_tokens=max_tokens, request_extra=req_extra
            )
            content = append_download_links_to_content(final, attachments)
            log_llm_result(
                step=step,
                limit=limit,
                has_tool_calls=False,
                content_preview=content or "",
                duration_ms=int((time.perf_counter() - t_final) * 1000),
            )
            log_run_end(
                mode="prompt_json_fc",
                steps_used=step,
                limit=limit,
                tool_calls_executed=tool_calls_executed,
                reason="final_answer",
                content_preview=content or "",
            )
            return AgentLoopResult(
                content=content,
                tool_calls_executed=tool_calls_executed,
                mode="prompt_json_fc",
                iterations=step,
                attachments=attachments,
            )
        call_names = [str(c.get("name") or "") for c in calls if isinstance(c, dict)]
        log_llm_result(
            step=step,
            limit=limit,
            has_tool_calls=True,
            tool_names=call_names,
            content_preview=result.content or "",
            duration_ms=llm_ms,
        )
        tool_results: List[str] = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "")
            params = call.get("parameters") or call.get("arguments") or {}
            tool_info = _find_tool(name, tools)
            if not tool_info:
                log_tool_result(
                    step=step,
                    limit=limit,
                    tool=name,
                    success=False,
                    error="Tool not found",
                )
                tool_results.append(f"Tool {name} not found")
                continue
            if tool_info.server_id == NATIVE_SERVER_ID:
                started = time.perf_counter()
                call_id = uuid.uuid4().hex
                tool_args = params if isinstance(params, dict) else {}
                try:
                    log_tool_call(
                        step=step,
                        limit=limit,
                        tool=tool_info.qualified_name or tool_info.name,
                        server_id=tool_info.server_id,
                        kind="native",
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
                    content = await execute_native_tool(
                        tool_info,
                        tool_args,
                        subagent_ctx=subagent_ctx,
                        subagent_config=subagent_config,
                    )
                    tool_results.append(content)
                    tool_calls_executed += 1
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    log_tool_result(
                        step=step,
                        limit=limit,
                        tool=tool_info.qualified_name or tool_info.name,
                        success=True,
                        duration_ms=duration_ms,
                        result_preview=content or "",
                    )
                    await emit_mcp_tool_end(
                        event_callback,
                        server_id=tool_info.server_id,
                        tool=tool_info.name,
                        qualified_name=tool_info.qualified_name,
                        success=True,
                        duration_ms=duration_ms,
                        call_id=call_id,
                        arguments=tool_args,
                        result=content,
                    )
                except Exception as exc:
                    log.exception("Native tool error in prompt_json_fc")
                    log_tool_result(
                        step=step,
                        limit=limit,
                        tool=name,
                        success=False,
                        error=str(exc),
                    )
                    tool_results.append(f"Native tool error: {exc}")
                    await emit_mcp_tool_end(
                        event_callback,
                        server_id=tool_info.server_id,
                        tool=tool_info.name,
                        qualified_name=tool_info.qualified_name,
                        success=False,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        error=str(exc),
                        call_id=call_id,
                        arguments=tool_args,
                    )
                continue
            session = sessions.get(tool_info.server_id)
            if not session:
                log_tool_result(
                    step=step,
                    limit=limit,
                    tool=tool_info.qualified_name or tool_info.name,
                    success=False,
                    error=f"MCP session for {tool_info.server_id} unavailable",
                )
                tool_results.append(f"MCP session for {tool_info.server_id} unavailable")
                continue
            try:
                started = time.perf_counter()
                call_id = uuid.uuid4().hex
                tool_args = params if isinstance(params, dict) else {}
                log_tool_call(
                    step=step,
                    limit=limit,
                    tool=tool_info.qualified_name or tool_info.name,
                    server_id=tool_info.server_id,
                    kind="mcp",
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
                raw = await platform.call_tool(tool_info.server_id, tool_info.name, tool_args, context, session)
                parsed = parse_mcp_result_to_struct(raw)
                tool_results.append(format_parsed_for_llm(parsed) or str(raw))
                tool_calls_executed += 1
                download_links = enrich_download_links(parsed, tool_info.server_id)
                result_ui = format_tool_result_for_ui(parsed, raw)
                for link in download_links:
                    if link["url"] not in {x["url"] for x in attachments}:
                        attachments.append(link)
                duration_ms = int((time.perf_counter() - started) * 1000)
                log_tool_result(
                    step=step,
                    limit=limit,
                    tool=tool_info.qualified_name or tool_info.name,
                    success=True,
                    duration_ms=duration_ms,
                    result_preview=preview_parsed_for_ui(parsed) or "",
                )
                await emit_mcp_tool_end(
                    event_callback,
                    server_id=tool_info.server_id,
                    tool=tool_info.name,
                    qualified_name=tool_info.qualified_name,
                    success=True,
                    duration_ms=duration_ms,
                    result_preview=preview_parsed_for_ui(parsed),
                    has_image=bool(parsed.images),
                    has_audio=bool(parsed.audio),
                    has_resource=bool(parsed.resources),
                    download_urls=download_links or None,
                    call_id=call_id,
                    arguments=tool_args,
                    result=result_ui,
                )
            except Exception as exc:
                log.exception("Error calling")
                duration_ms = int((time.perf_counter() - started) * 1000) if "started" in locals() else 0
                log_tool_result(
                    step=step,
                    limit=limit,
                    tool=name,
                    success=False,
                    duration_ms=duration_ms,
                    error=str(exc),
                )
                await emit_mcp_tool_end(
                    event_callback,
                    server_id=tool_info.server_id,
                    tool=tool_info.name,
                    qualified_name=tool_info.qualified_name,
                    success=False,
                    duration_ms=duration_ms,
                    error=str(exc),
                    call_id=call_id if "call_id" in locals() else None,
                    arguments=tool_args if "tool_args" in locals() else None,
                    result=str(exc),
                )
                tool_results.append(f"Error calling {name}: {exc}")
        working_messages.append({"role": "assistant", "content": json.dumps({"tool_calls": calls}, ensure_ascii=False)})
        working_messages.append({"role": "user", "content": "Tool results:\n" + "\n".join(tool_results)})
    log_llm_call(
        step=limit,
        limit=limit,
        kind="chat_final",
        model=model_label,
        messages_count=len(working_messages),
    )
    t_final = time.perf_counter()
    final = await provider.chat(
        working_messages, model_id, temperature=temperature, max_tokens=max_tokens, request_extra=req_extra
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
        mode="prompt_json_fc",
        steps_used=limit,
        limit=limit,
        tool_calls_executed=tool_calls_executed,
        reason="max_iterations",
        content_preview=content or "",
    )
    return AgentLoopResult(
        content=content,
        tool_calls_executed=tool_calls_executed,
        mode="prompt_json_fc",
        iterations=max_iterations,
        attachments=attachments,
    )
