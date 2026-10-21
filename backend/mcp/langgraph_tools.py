"""Generic LangGraph tools from MCP platform (B-17, B-27)."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from backend.settings.logging import get_logger

log = get_logger(__name__)


async def load_mcp_langgraph_tools(
    *, server_ids: List[str], context: Dict[str, Any]
) -> List[Any]:
    """Загружает LangChain StructuredTool для каждого MCP tool (async-safe)."""
    try:
        from langchain_core.tools import StructuredTool
    except ImportError:
        log.warning("langchain_core not available for MCP langgraph tools")
        return []
    from backend.mcp.chat_integration import build_mcp_context_from_user
    from backend.mcp.platform import get_mcp_platform
    from backend.mcp.result_parser import (
        format_parsed_for_llm,
        parse_mcp_result_to_struct,
        preview_parsed_for_ui,
    )

    platform = get_mcp_platform()
    if not platform.enabled or not platform.initialized:
        return []
    user = context.get("current_user") or {}
    mcp_ctx = build_mcp_context_from_user(
        user,
        chat_id=context.get("conversation_id"),
        message_id=context.get("message_id"),
    )
    specs = []
    for sid in server_ids:
        try:
            specs.extend(await platform.list_tools_for_server(sid, mcp_ctx))
        except Exception:
            log.exception("langgraph MCP list_tools failed server=")
    specs = platform.filter_tools_by_context(
        specs, mcp_ctx, enabled_server_ids=server_ids
    )
    if not specs:
        return []
    sio = context.get("sio")
    socket_id = context.get("socket_id")

    async def _emit(event: Dict[str, Any]) -> None:
        if sio and socket_id:
            try:
                await sio.emit("chat_mcp_event", event, room=socket_id)
            except Exception:
                log.exception("MCP event emit failed")

    tools: List[Any] = []
    for spec in specs:

        def _build_tool(tool_spec=spec):

            async def _arun(**kwargs):
                started = time.perf_counter()
                await _emit(
                    {
                        "type": "mcp_tool_start",
                        "server_id": tool_spec.server_id,
                        "tool": tool_spec.name,
                        "qualified_name": tool_spec.qualified_name,
                        "timestamp": time.time(),
                    }
                )
                try:
                    async with platform.request_sessions(
                        [tool_spec.server_id], mcp_ctx
                    ) as sessions:
                        session = sessions.get(tool_spec.server_id)
                        if not session:
                            msg = f"MCP session unavailable: {tool_spec.server_id}"
                            raise RuntimeError(msg)
                        raw = await platform.call_tool(
                            tool_spec.server_id,
                            tool_spec.name,
                            kwargs,
                            mcp_ctx,
                            session,
                        )
                        parsed = parse_mcp_result_to_struct(raw)
                        result = format_parsed_for_llm(parsed) or str(raw)
                        success = True
                        error = None
                        preview = preview_parsed_for_ui(parsed)
                        has_image = bool(parsed.images)
                        has_audio = bool(parsed.audio)
                        has_resource = bool(parsed.resources)
                except Exception as exc:
                    log.exception("Ошибка операции")
                    result = f"MCP tool error: {exc}"
                    success = False
                    error = str(exc)
                    preview = None
                    has_image = has_audio = has_resource = False
                duration_ms = int((time.perf_counter() - started) * 1000)
                payload: Dict[str, Any] = {
                    "type": "mcp_tool_end",
                    "server_id": tool_spec.server_id,
                    "tool": tool_spec.name,
                    "qualified_name": tool_spec.qualified_name,
                    "success": success,
                    "duration_ms": duration_ms,
                    "timestamp": time.time(),
                }
                if error:
                    payload["error"] = error
                if preview:
                    payload["result_preview"] = preview
                if has_image:
                    payload["has_image"] = True
                if has_audio:
                    payload["has_audio"] = True
                if has_resource:
                    payload["has_resource"] = True
                await _emit(payload)
                return result

            def _make_sync(**kwargs):
                import asyncio

                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures

                        with concurrent.futures.ThreadPoolExecutor(
                            max_workers=1
                        ) as pool:
                            return pool.submit(asyncio.run, _arun(**kwargs)).result()
                    return loop.run_until_complete(_arun(**kwargs))
                except RuntimeError:
                    return asyncio.run(_arun(**kwargs))

            return StructuredTool.from_function(
                func=_make_sync,
                coroutine=_arun,
                name=tool_spec.qualified_name,
                description=tool_spec.description or tool_spec.name,
            )

        tools.append(_build_tool())
    return tools


def create_mcp_langgraph_tools(*, server_ids: List[str], context_factory) -> List[Any]:
    """Sync compat wrapper (deprecated — используйте load_mcp_langgraph_tools)."""
    import asyncio

    ctx = context_factory() or {}
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run,
                    load_mcp_langgraph_tools(server_ids=server_ids, context=ctx),
                ).result()
        return loop.run_until_complete(
            load_mcp_langgraph_tools(server_ids=server_ids, context=ctx)
        )
    except RuntimeError:
        return asyncio.run(load_mcp_langgraph_tools(server_ids=server_ids, context=ctx))
