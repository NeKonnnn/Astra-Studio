"""Lightweight prompt+JSON function calling fallback (порт OWUI B-42)."""

from __future__ import annotations

import json
import re
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
from backend.agents.subagents.parallel import run_tool_calls
from backend.agents.subagents.parent import (
    new_subagent_call,
    run_native_tool_call,
    subagent_call_args,
)
from backend.agents.subagents.settings import NATIVE_SERVER_ID
from backend.mcp.types import AgentLoopResult, McpCallContext, McpToolInfo
from backend.settings.config import get_settings
from backend.settings.logging import get_logger

log = get_logger(__name__)

DEFAULT_PROMPT_TEMPLATE = 'Available Tools: {{TOOLS}}\n\nYour task is to choose and return the correct tool(s) from the list of available tools based on the query.\n\nRules:\n- Return only the JSON object, without any additional text.\n- If no tools match, return: {"tool_calls": []}\n- If the query needs data you cannot know from memory alone, use the available tools — do not refuse or answer from outdated knowledge.\n- After Tool results already contain enough facts to answer, return {"tool_calls": []} so a final answer can be written. Do not repeat the same tool call.\n- For multi-step tasks (presentations, documents, several edits), call ALL required tools across iterations until the task is fully complete.\n- Do NOT return empty tool_calls until every step is done (e.g. create → add slides/content → save/export).\n- You may return multiple tools in one response when they are independent.\n- Format:\n{\n  "tool_calls": [\n    {"name": "toolName1", "parameters": {"key1": "value1"}}\n  ]\n}\n'

FINAL_ANSWER_SYSTEM = (
    "Ответь на запрос пользователя, опираясь на сообщения Tool results ниже. "
    "У тебя уже есть данные из вызванных инструментов — используй их как источник фактов. "
    "Не утверждай, что нет доступа к актуальной информации, если она есть в Tool results. "
    "Не вызывай инструменты и не возвращай JSON tool_calls."
)

# Финал без tool results: в History может быть RAG, но tools могли и не вызываться.
FINAL_ANSWER_FROM_CONTEXT_SYSTEM = (
    "Ответь на вопрос пользователя по сообщениям диалога ниже. "
    "Если в истории есть фрагменты документов / база знаний / CONTEXT — опирайся на них. "
    "Не возвращай JSON tool_calls и не пиши метки [[ОТВЕТ N]]. "
    "Не рассуждай про отсутствие доступа к инструментам или JSON. "
    "Если фактов в контексте недостаточно — скажи об этом коротко."
)

_REALTIME_QUERY_RE = re.compile(
    r"(?i)\b("
    r"погод|weather|температур|прогноз|"
    r"новост|news|курс\b|валют|bitcoin|btc|"
    r"сейчас|актуальн|сегодня|real[- ]?time|на данный момент"
    r")\b"
)

_FC_NUDGE_MARKERS = (
    "Do NOT invent [[ОТВЕТ",
    "Previous tool_calls JSON",
    "return tool_calls JSON",
    "Return a SHORT complete JSON",
    "Return empty tool_calls",
    '{"tool_calls": []}',
)

# Сколько последних сообщений истории отдаём в FC-промпт (нужны tool results).
_FC_HISTORY_TAIL = 12


def _call_fingerprint(call: dict) -> str:
    name = str(call.get("name") or "")
    params = call.get("parameters") or call.get("arguments") or {}
    try:
        params_s = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        params_s = str(params)
    return f"{name}:{params_s}"


def _strip_fc_nudges(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Убирает служебные nudge FC из контекста финального ответа."""
    out: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = str(msg.get("content") or "")
        if msg.get("role") == "user" and any(m in content for m in _FC_NUDGE_MARKERS):
            continue
        out.append(msg)
    return out


def _query_needs_live_tools(text: str) -> bool:
    """Запрос про актуальное/онлайн — без tool call финал почти всегда врёт."""
    return bool(_REALTIME_QUERY_RE.search(text or ""))


def _tools_look_like_websearch(tools: List[McpToolInfo]) -> bool:
    for tool in tools or []:
        blob = f"{tool.name} {tool.qualified_name} {tool.description or ''}".lower()
        if any(
            key in blob
            for key in ("websearch", "web_search", "search", "fetchweb", "fetch_web", "weather")
        ):
            return True
    return False


async def _generate_final_answer(
    *,
    provider: Any,
    model_id: str,
    working_messages: List[Dict[str, Any]],
    tool_calls_executed: int,
    temperature: float,
    max_tokens: int,
    request_extra: Dict[str, Any],
) -> str:
    """Финальный ответ пользователю; после tools — жёстко опираемся на Tool results."""
    messages = _strip_fc_nudges(list(working_messages))
    if tool_calls_executed > 0:
        messages = [{"role": "system", "content": FINAL_ANSWER_SYSTEM}, *messages]
    else:
        messages = [
            {"role": "system", "content": FINAL_ANSWER_FROM_CONTEXT_SYSTEM},
            *messages,
        ]
    return await provider.chat(
        messages,
        model_id,
        temperature=temperature,
        max_tokens=max_tokens,
        request_extra=request_extra,
    )


def _render_tools_prompt(tools: List[McpToolInfo]) -> str:
    specs = []
    for tool in tools:
        specs.append(
            {
                "name": tool.qualified_name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
        )
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
    effective_model_path = (
        fc_model_path or model_path or settings.mcp.fc_task_model or model_path
    )
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
    # Толчки: (1) пустой первый ответ при доступных tools; (2) «ещё не готово» после tools.
    nudged_use_tools = False
    nudged_continue = False
    nudged_fc_retry = False
    nudged_realtime = False
    seen_call_fps: set[str] = set()
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
                "content": (
                    f"History:\n{json.dumps(working_messages[-_FC_HISTORY_TAIL:], ensure_ascii=False)}\n"
                    f"Query: {user_query}"
                ),
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
            fc_messages,
            model_id,
            temperature=temperature,
            max_tokens=max_tokens,
            request_extra=req_extra,
        )
        llm_ms = int((time.perf_counter() - t_llm) * 1000)
        # openai_compat уже мог разобрать OWUI JSON в result.tool_calls и очистить content.
        # Без этого ветка видит пустой content → «unparsed» и MCP не вызывается.
        payload = None
        structured_calls = list(getattr(result, "tool_calls", None) or [])
        if structured_calls:
            payload = {
                "tool_calls": [
                    {
                        "name": tc.name,
                        "parameters": tc.arguments
                        if isinstance(getattr(tc, "arguments", None), dict)
                        else {},
                    }
                    for tc in structured_calls
                    if getattr(tc, "name", None)
                ]
            }
        if not payload:
            payload = _extract_json_object(result.content)
        if not payload:
            # Fallback: OWUI/XML tool calls из content (не отдаём сырой JSON в чат).
            from backend.mcp.text_tool_calls import extract_tool_calls_from_content

            _cleaned, text_calls = extract_tool_calls_from_content(result.content or "")
            if text_calls:
                payload = {
                    "tool_calls": [
                        {
                            "name": tc.name,
                            "parameters": tc.arguments
                            if isinstance(tc.arguments, dict)
                            else {},
                        }
                        for tc in text_calls
                    ]
                }
            else:
                from backend.mcp.text_tool_calls import content_has_text_tool_calls

                raw_content = result.content or ""
                looks_fc = content_has_text_tool_calls(raw_content)
                has_orphan_marks = bool(
                    re.search(r"\[\[\s*ОТВЕТ\b", raw_content, flags=re.IGNORECASE)
                )
                # Модель пишет [[ОТВЕТ N]] без вызова subagent — просим JSON tool_calls.
                if (
                    has_orphan_marks
                    and tool_calls_executed == 0
                    and tools
                    and step < limit
                    and not nudged_use_tools
                ):
                    nudged_use_tools = True
                    log.warning(
                        "prompt_json_fc: orphan [[ОТВЕТ]] without tool call — nudge subagent"
                    )
                    working_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Do NOT invent [[ОТВЕТ N]] markers. "
                                "To call a subagent return JSON only: "
                                '{"tool_calls":[{"name":"subagent","parameters":'
                                '{"subagent_type":"...","prompt":"..."}}]}. '
                                "If no tool is needed, return {\"tool_calls\": []}."
                            ),
                        }
                    )
                    continue
                # Проза вместо JSON на realtime-запросе при websearch — форсируем tool call.
                if (
                    tool_calls_executed == 0
                    and tools
                    and step < limit
                    and not nudged_realtime
                    and _query_needs_live_tools(user_query)
                    and _tools_look_like_websearch(tools)
                ):
                    nudged_realtime = True
                    log.warning(
                        "prompt_json_fc: prose instead of tools on realtime query — force websearch"
                    )
                    working_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The user needs LIVE/current data (weather, news, rates, etc.). "
                                "You MUST call a web search/fetch tool now. "
                                'Return JSON only, e.g. {"tool_calls":[{"name":"<search_tool>",'
                                '"parameters":{"query":"..."}}]}. '
                                "Do NOT answer from memory and do NOT return empty tool_calls."
                            ),
                        }
                    )
                    continue
                # Обрезанный FC (finish_reason=length): один retry с большим max_tokens.
                if looks_fc and not nudged_fc_retry:
                    new_mt = min(4096, max(int(max_tokens) * 2, 2048))
                    log.warning(
                        "prompt_json_fc: truncated/unparsed tool_calls JSON — retry max_tokens=%s→%s",
                        max_tokens,
                        new_mt,
                    )
                    nudged_fc_retry = True
                    max_tokens = new_mt
                    working_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Previous tool_calls JSON was truncated or invalid. "
                                "Return a SHORT complete JSON only: "
                                '{"tool_calls":[{"name":"...","parameters":{...}}]} '
                                "with a concise prompt (max ~200 chars)."
                            ),
                        }
                    )
                    continue
                log_llm_result(
                    step=step,
                    limit=limit,
                    has_tool_calls=False,
                    content_preview=raw_content,
                    duration_ms=llm_ms,
                )
                log_run_end(
                    mode="prompt_json_fc",
                    steps_used=step,
                    limit=limit,
                    tool_calls_executed=tool_calls_executed,
                    reason="unparsed_fc_json",
                    content_preview=raw_content,
                )
                # Проза/метки из FC-модели не доверяем как финалу: отвечаем по History
                # (там уже RAG-контекст). Иначе модель пишет «ничего не нашёл» при hits>0.
                try:
                    answer = await _generate_final_answer(
                        provider=provider,
                        model_id=model_id,
                        working_messages=working_messages,
                        tool_calls_executed=tool_calls_executed,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        request_extra=req_extra,
                    )
                    return AgentLoopResult(
                        content=answer or "",
                        tool_calls_executed=tool_calls_executed,
                        mode="prompt_json_fc",
                        iterations=step,
                        new_messages=working_messages[len(messages) :],
                        attachments=attachments,
                    )
                except Exception:
                    log.exception(
                        "prompt_json_fc: final answer after unparsed content failed"
                    )
                    return AgentLoopResult(
                        content="",
                        tool_calls_executed=tool_calls_executed,
                        mode="prompt_json_fc",
                        iterations=step,
                        new_messages=working_messages[len(messages) :],
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
            # Пустой ответ на первом шаге при доступных tools: маленькие модели
            # часто «сдаются» («нет доступа к реальному времени») вместо поиска.
            if (
                tool_calls_executed == 0
                and tools
                and step < limit
                and not nudged_use_tools
            ):
                nudged_use_tools = True
                working_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Tools are available (including web search/fetch). "
                            "If the user asks for current/real-time information, "
                            "return tool_calls JSON now. Return empty tool_calls "
                            "only if tools are truly unnecessary."
                        ),
                    }
                )
                continue
            # Погода/новости/курс: пустой tool_calls при живом websearch — ещё один толчок.
            if (
                tool_calls_executed == 0
                and tools
                and step < limit
                and not nudged_realtime
                and _query_needs_live_tools(user_query)
                and _tools_look_like_websearch(tools)
            ):
                nudged_realtime = True
                log.warning(
                    "prompt_json_fc: realtime query without tools — force websearch tool_calls"
                )
                working_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The user needs LIVE/current data (weather, news, rates, etc.). "
                            "You MUST call a web search/fetch tool now. "
                            'Return JSON only, e.g. {"tool_calls":[{"name":"<search_tool>",'
                            '"parameters":{"query":"..."}}]}. '
                            "Do NOT answer from memory and do NOT return empty tool_calls."
                        ),
                    }
                )
                continue
            # Раньше толчок срабатывал на каждом шаге без вызовов: модель
            # говорила «готово», ей отвечали «вероятно, не закончено» - и так
            # до лимита шагов. Маленькая модель на это вызывает инструменты
            # заново: 25.09 оба проверочных субагента отработали по два раза.
            # Один толчок сохраняет смысл (экспорт после поиска и т.п.), дальше
            # слово модели «готово» - окончательное.
            if tool_calls_executed > 0 and step < limit and not nudged_continue:
                nudged_continue = True
                working_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "If Tool results already answer the user, return "
                            '{"tool_calls": []}. Only call more tools if critical '
                            "data is still missing. Do not repeat the same search."
                        ),
                    }
                )
                continue
            log_llm_call(
                step=step,
                limit=limit,
                kind="chat_final",
                model=model_label,
                messages_count=len(working_messages) + (1 if tool_calls_executed else 0),
            )
            t_final = time.perf_counter()
            final = await _generate_final_answer(
                provider=provider,
                model_id=model_id,
                working_messages=working_messages,
                tool_calls_executed=tool_calls_executed,
                temperature=temperature,
                max_tokens=max_tokens,
                request_extra=req_extra,
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
                new_messages=working_messages[len(messages) :],
            )

        # Отбрасываем точные повторы уже выполненных вызовов (модель зацикливается).
        filtered_calls: List[dict] = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            fp = _call_fingerprint(call)
            if fp in seen_call_fps:
                log.info(
                    "prompt_json_fc: skip duplicate tool call %s",
                    call.get("name"),
                )
                continue
            filtered_calls.append(call)
        if not filtered_calls and tool_calls_executed > 0:
            log_llm_result(
                step=step,
                limit=limit,
                has_tool_calls=False,
                content_preview="duplicate tool_calls skipped → final",
                duration_ms=llm_ms,
            )
            log_llm_call(
                step=step,
                limit=limit,
                kind="chat_final",
                model=model_label,
                messages_count=len(working_messages) + 1,
            )
            t_final = time.perf_counter()
            final = await _generate_final_answer(
                provider=provider,
                model_id=model_id,
                working_messages=working_messages,
                tool_calls_executed=tool_calls_executed,
                temperature=temperature,
                max_tokens=max_tokens,
                request_extra=req_extra,
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
                reason="duplicate_tools_final",
                content_preview=content or "",
            )
            return AgentLoopResult(
                content=content,
                tool_calls_executed=tool_calls_executed,
                mode="prompt_json_fc",
                iterations=step,
                attachments=attachments,
                new_messages=working_messages[len(messages) :],
            )
        calls = filtered_calls

        call_names = [str(c.get("name") or "") for c in calls if isinstance(c, dict)]
        log_llm_result(
            step=step,
            limit=limit,
            has_tool_calls=True,
            tool_names=call_names,
            content_preview=result.content or "",
            duration_ms=llm_ms,
        )
        for call in calls:
            if isinstance(call, dict):
                seen_call_fps.add(_call_fingerprint(call))

        # Один вызов шага → строка результата для модели. Вызовы subagent
        # шага идут одновременно (run_tool_calls), остальные - по очереди.
        # Как OWUI: весь батч сразу pending в UI (emit start до исполнения).
        items = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "")
            tool_info = _find_tool(name, tools)
            params = call.get("parameters") or call.get("arguments") or {}
            tool_args = params if isinstance(params, dict) else {}
            if tool_info:
                tool_args = subagent_call_args(
                    tool_info, tool_args, subagent_ctx, subagent_config
                )
            call_id = uuid.uuid4().hex
            # Номер метки [[ОТВЕТ N]] субагенту - до запуска, по порядку.
            items.append(
                (
                    call,
                    tool_info,
                    new_subagent_call(tool_info, subagent_ctx),
                    call_id,
                    tool_args,
                )
            )

        for call, tool_info, _sub, call_id, tool_args in items:
            name = str(call.get("name") or "")
            await emit_mcp_tool_start(
                event_callback,
                server_id=tool_info.server_id if tool_info else "unknown",
                tool=tool_info.name if tool_info else name,
                qualified_name=(
                    tool_info.qualified_name if tool_info else name
                ),
                call_id=call_id,
                arguments=tool_args,
            )

        async def _run_call(item) -> str:
            nonlocal tool_calls_executed
            call, tool_info, sub_call, call_id, tool_args = item
            name = str(call.get("name") or "")
            if not tool_info:
                log_tool_result(
                    step=step,
                    limit=limit,
                    tool=name,
                    success=False,
                    error="Tool not found",
                )
                await emit_mcp_tool_end(
                    event_callback,
                    server_id="unknown",
                    tool=name,
                    qualified_name=name,
                    success=False,
                    duration_ms=0,
                    error="Tool not found",
                    call_id=call_id,
                    arguments=tool_args,
                    result="Tool not found",
                )
                return f"Tool {name} not found"
            if tool_info.server_id == NATIVE_SERVER_ID:
                started = time.perf_counter()
                try:
                    from backend.mcp.activity_scope import activity_scope

                    log_tool_call(
                        step=step,
                        limit=limit,
                        tool=tool_info.qualified_name or tool_info.name,
                        server_id=tool_info.server_id,
                        kind="native",
                        arguments=tool_args,
                    )
                    scope_kwargs: Dict[str, Any] = {}
                    if tool_info.name == "subagent":
                        child_agent_id = None
                        if isinstance(tool_args, dict):
                            raw_aid = tool_args.get("agent_id")
                            try:
                                child_agent_id = (
                                    int(raw_aid) if raw_aid is not None else None
                                )
                            except (TypeError, ValueError):
                                child_agent_id = None
                        scope_kwargs = {
                            "parent_call_id": call_id,
                            "agent_id": child_agent_id,
                            "agent_name": (
                                str(tool_args.get("agent_name") or "").strip() or None
                                if isinstance(tool_args, dict)
                                else None
                            ),
                            "depth": (getattr(subagent_ctx, "depth", 0) or 0) + 1,
                            "activity_kind": "subagent",
                        }
                    # Модели - с меткой [[ОТВЕТ N]], в UI (result=content) - без.
                    with activity_scope(**scope_kwargs):
                        for_model, content, doc_search = await run_native_tool_call(
                            tool_info,
                            tool_args,
                            subagent_ctx=subagent_ctx,
                            subagent_config=subagent_config,
                            call=sub_call,
                        )
                    res = for_model
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
                        document_search=doc_search,
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
                    res = f"Native tool error: {exc}"
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
                return res
            session = sessions.get(tool_info.server_id)
            if not session:
                err = f"MCP session for {tool_info.server_id} unavailable"
                log_tool_result(
                    step=step,
                    limit=limit,
                    tool=tool_info.qualified_name or tool_info.name,
                    success=False,
                    error=err,
                )
                await emit_mcp_tool_end(
                    event_callback,
                    server_id=tool_info.server_id,
                    tool=tool_info.name,
                    qualified_name=tool_info.qualified_name,
                    success=False,
                    duration_ms=0,
                    error=err,
                    call_id=call_id,
                    arguments=tool_args,
                    result=err,
                )
                return err
            started = time.perf_counter()
            try:
                log_tool_call(
                    step=step,
                    limit=limit,
                    tool=tool_info.qualified_name or tool_info.name,
                    server_id=tool_info.server_id,
                    kind="mcp",
                    arguments=tool_args,
                )
                raw = await platform.call_tool(
                    tool_info.server_id, tool_info.name, tool_args, context, session
                )
                parsed = parse_mcp_result_to_struct(raw)
                res = format_parsed_for_llm(parsed) or str(raw)
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
                duration_ms = int((time.perf_counter() - started) * 1000)
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
                    call_id=call_id,
                    arguments=tool_args,
                    result=str(exc),
                )
                res = f"Error calling {name}: {exc}"
            return res

        tool_results: List[str] = await run_tool_calls(
            items, _run_call, parallel=lambda item: item[2] is not None, step=step
        )
        working_messages.append(
            {
                "role": "assistant",
                "content": json.dumps({"tool_calls": calls}, ensure_ascii=False),
            }
        )
        working_messages.append(
            {"role": "user", "content": "Tool results:\n" + "\n".join(tool_results)}
        )
    log_llm_call(
        step=limit,
        limit=limit,
        kind="chat_final",
        model=model_label,
        messages_count=len(working_messages) + (1 if tool_calls_executed else 0),
    )
    t_final = time.perf_counter()
    final = await _generate_final_answer(
        provider=provider,
        model_id=model_id,
        working_messages=working_messages,
        tool_calls_executed=tool_calls_executed,
        temperature=temperature,
        max_tokens=max_tokens,
        request_extra=req_extra,
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
        new_messages=working_messages[len(messages) :],
    )
