"""Мост LangGraph orchestrator ↔ MCP platform / ProviderRegistry (B-27)."""

from __future__ import annotations

import asyncio
import concurrent.futures

import httpx
from typing import Any, Callable, Dict, List, Optional

from backend.llm_providers.routing import build_chat_messages
from backend.settings.logging import get_logger

log = get_logger(__name__)


def _invoke_stream_callback(
    cb: Callable[..., Any], chunk: str, acc: str, stream_role: str = "content"
) -> bool:
    try:
        result = cb(chunk, acc, stream_role)
    except TypeError:
        if stream_role == "heartbeat":
            return True
        result = cb(chunk, acc)
    return result is not False


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# Имена моделей, которые чат обслуживать не могут. Совпадают с подсказками
# RAG-каталога (backend/routes/rag.py::_rag_model_kind) — если менять, менять там же.
_NON_CHAT_NAME_HINTS = (
    "embed",
    "rerank",
    "frida",
    "cross-encoder",
    "crossencoder",
    "bge-",
    "e5-",
    "gte-",
    "labse",
    "minilm",
)


def _pick_chat_model(models: Any, *, provider_id: str) -> str:
    """Первая ЧАТОВАЯ модель провайдера.

    Раньше здесь стояло ```models[0]``` — берём что первое отдал шлюз. На CORSUR
    первым в каталоге идёт ```embed/FRIDA```, поэтому все служебные вызовы без
    явной модели (иерархическая суммаризация при индексации, judge) уходили
    чат-запросом на эмбеддер и получали 422 «provider API error». Хуже того,
    текст этой ошибки попадал в индекс как summary документа (01.08).
    """
    ids = [str(getattr(m, "model_id", "") or "").strip() for m in (models or [])]
    ids = [m for m in ids if m]
    if not ids:
        return ""
    for model_id in ids:
        low = model_id.lower()
        if not any(hint in low for hint in _NON_CHAT_NAME_HINTS):
            return model_id
    # Все имена похожи на не-чатовые: берём первое, но громко предупреждаем —
    # иначе вызов молча уйдёт в эмбеддер, как это и было.
    log.warning(
        "[LLM] у провайдера %s нет модели, похожей на чатовую (каталог: %s) — "
        "беру %r, вероятен отказ провайдера",
        provider_id,
        ids[:8],
        ids[0],
    )
    return ids[0]


def sync_chat_via_registry(
    prompt: str,
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    model_path: Optional[str] = None,
    streaming: bool = False,
    stream_callback: Optional[Callable] = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    system_prompt: Optional[str] = None,
    enable_thinking: bool = False,
    service_call: bool = False,
) -> Optional[str]:
    """Синхронный LLM-вызов через ProviderRegistry (planner / aggregator).

    ```service_call=True``` — вызов служебный (опечатки, multi-query, HyDE,
    суммаризация, judge): его ответ читает код, а не человек. Для таких
    выключается мышление и поднимается нижняя граница ```max_tokens```.
    """

    async def _call():
        from backend.llm_providers import get_registry

        try:
            from backend.app_state import get_current_model_path
        except Exception:
            log.exception("Ошибка операции")

            def get_current_model_path():
                return None

        registry = await get_registry()
        effective_path = (model_path or get_current_model_path() or "").strip()
        provider, model_id = registry.resolve(effective_path)
        if not model_id:
            models = await provider.list_models()
            model_id = _pick_chat_model(
                models, provider_id=getattr(provider, "id", "?")
            )
        if not model_id:
            return None
        messages = build_chat_messages(
            prompt, history=history, system_prompt=system_prompt
        )
        from backend.llm_providers.routing import (
            apply_no_think_tag,
            service_call_request_extra,
            service_min_max_tokens,
            service_no_think_tag_enabled,
            thinking_request_extra,
        )

        # UI «Быстрый»/«Мышление»: явно шлём chat_template_kwargs.enable_thinking.
        # Раньше при False extra не передавался — reasoning-модель думала по умолчанию.
        req_extra = thinking_request_extra(bool(enable_thinking))
        eff_max_tokens = max_tokens
        no_think_tag = False
        if service_call and not enable_thinking:
            extra = service_call_request_extra()
            # LLM_SERVICE_DISABLE_THINKING=0 — не принудительно гасить мышление.
            req_extra = extra if extra else None
            eff_max_tokens = max(int(max_tokens or 0), service_min_max_tokens())
            # chat_template_kwargs шлюз может не пробросить до vLLM, и тогда
            # модель всё равно уходит в рассуждение. Тег в тексте промпта едет
            # в messages — этого поля шлюз не касается.
            if extra and service_no_think_tag_enabled():
                messages = apply_no_think_tag(messages)
                no_think_tag = True
            log.debug(
                "[LLM] служебный вызов: мышление выключено=%s, /np_think=%s, max_tokens %s -> %s",
                bool(extra),
                no_think_tag,
                max_tokens,
                eff_max_tokens,
            )
        # Поля выборки добавляем ПОСЛЕ ветки служебного вызова: там req_extra
        # перезаписывается целиком, и слияние выше было бы затёрто.
        from backend.llm_providers.routing import merge_sampling_request_extra

        req_extra = merge_sampling_request_extra(req_extra)
        if streaming and stream_callback:

            def _cb(chunk: str, acc: str, stream_role: str = "content") -> bool:
                return _invoke_stream_callback(stream_callback, chunk, acc, stream_role)

            return await provider.stream_chat(
                messages,
                model_id,
                callback=_cb,
                temperature=temperature,
                # eff_max_tokens, а не max_tokens: иначе слежубный вызов в
                # стриминге остался бы без нижней границы бюджета.
                max_tokens=eff_max_tokens,
                request_extra=req_extra,
            )
        return await provider.chat(
            messages,
            model_id,
            temperature=temperature,
            max_tokens=eff_max_tokens,
            request_extra=req_extra,
        )

    try:
        return _run_async(_call())
    except httpx.HTTPStatusError:
        raise
    except Exception:
        log.exception("sync_chat_via_registry failed")
        return None


async def attach_mcp_tools_to_orchestrator(
    orchestrator, context: Dict[str, Any]
) -> int:
    """Подключает MCP tools из tool_ids к orchestrator (tier-3, generic)."""
    from backend.mcp.langgraph_tools import load_mcp_langgraph_tools
    from backend.mcp.resolvers import parse_mcp_server_ids

    tool_ids = context.get("tool_ids") or context.get("mcp_tool_ids") or []
    server_ids = parse_mcp_server_ids(
        tool_ids if isinstance(tool_ids, list) else [tool_ids]
    )
    if not server_ids:
        return 0
    dynamic: set = getattr(orchestrator, "_dynamic_mcp_tool_names", set())
    for name in list(dynamic):
        orchestrator.tools_by_name.pop(name, None)
        orchestrator.tool_status.pop(name, None)
    orchestrator.tools = [t for t in orchestrator.tools if t.name not in dynamic]
    dynamic.clear()
    mcp_tools = await load_mcp_langgraph_tools(server_ids=server_ids, context=context)
    for tool in mcp_tools:
        name = tool.name
        orchestrator.tools.append(tool)
        orchestrator.tools_by_name[name] = tool
        orchestrator.tool_status[name] = True
        dynamic.add(name)
    orchestrator._dynamic_mcp_tool_names = dynamic
    if mcp_tools:
        log.info(
            "Orchestrator: attached %s MCP tools from servers=%s",
            len(mcp_tools),
            server_ids,
        )
    return len(mcp_tools)
