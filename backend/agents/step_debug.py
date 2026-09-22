"""Пошаговый debug-лог лимита «Максимальное количество шагов агента» (recursion_limit).

Каждый шаг графа = одно обращение к LLM (с возможным раундом инструментов после него).

Не входят в recursion_limit (логируются как подготовка):
  - RAG / KB / память
  - навыки (skills)
  - артефакты
  - плагины
Вызовы инструментов внутри цикла — как «шаг N/M · вызов инструмента».

Ищите в логах префикс ``[шаги агента]``.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence

from backend.settings.logging import get_logger

log = get_logger(__name__)

PREFIX = "[шаги агента]"
_ARGS_PREVIEW = 240
_CONTENT_PREVIEW = 160

_PHASE_RU: Mapping[str, str] = {
    "skills": "подготовка: навыки (skills)",
    "artifacts": "подготовка: артефакты",
    "plugins_prompt": "подготовка: плагины (промпт)",
    "plugin_direct": "подготовка: прямой вызов плагина",
    "rag_retrieve_done": "подготовка: поиск RAG завершён",
    "rag_before_agent_loop": "подготовка: перед циклом агента (после RAG)",
    "canned_skip_llm": "ответ без LLM (нет релевантных фрагментов RAG)",
    "agent_loop_enter": "вход в цикл агента (LLM ↔ инструменты)",
    "skip_no_tools": "цикл агента не нужен: нет инструментов",
    "skip_mcp_disabled": "цикл агента пропущен: MCP выключен",
    "skip_agent_loop": "цикл агента пропущен: нет MCP/субагентов → один вызов LLM",
    "mcp_skipped_no_tools": "MCP не дал инструментов → обычный вызов LLM",
    "subagent_start": "запуск субагента",
}

_LLM_KIND_RU: Mapping[str, str] = {
    "chat_completion": "запрос к модели (с возможностью вызвать инструменты)",
    "chat_final": "финальный запрос к модели (сбор ответа)",
    "prompt_json_fc": "запрос к модели (режим выбора инструментов через JSON)",
    "plain": "обычный запрос к модели (без инструментов)",
}

_END_REASON_RU: Mapping[str, str] = {
    "final_answer": "модель дала итоговый ответ",
    "max_iterations": "достигнут лимит шагов",
    "exhausted": "лимит шагов исчерпан",
    "no_tools": "инструментов нет, один вызов модели",
    "unparsed_fc_json": "не удалось разобрать ответ модели с вызовом инструментов",
}

_TOOL_KIND_RU: Mapping[str, str] = {
    "mcp": "MCP-инструмент",
    "native": "встроенный инструмент (субагент и т.п.)",
}

_MODE_RU: Mapping[str, str] = {
    "native_openai_tools": "нативные tool-calls",
    "prompt_json_fc": "выбор инструментов через JSON-промпт",
    "plain": "без инструментов",
}


def _trunc(text: Any, limit: int = _CONTENT_PREVIEW) -> str:
    s = str(text or "").replace("\n", " ").strip()
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "…"


def _args_preview(arguments: Any) -> str:
    try:
        raw = json.dumps(arguments, ensure_ascii=False, default=str)
    except TypeError:
        raw = str(arguments)
    return _trunc(raw, _ARGS_PREVIEW)


def _phase_label(phase: str) -> str:
    return _PHASE_RU.get(phase, phase)


def log_pre_loop(
    *,
    phase: str,
    chat_id: Optional[str] = None,
    agent_id: Any = None,
    recursion_limit: Optional[int] = None,
    limit_source: str = "",
    detail: str = "",
) -> None:
    """События до цикла агента — не расходуют лимит шагов."""
    limit_part = (
        f"лимит шагов={recursion_limit}"
        if recursion_limit is not None
        else "лимит шагов=—"
    )
    src = f", источник={limit_source}" if limit_source else ""
    extra = f" | {detail}" if detail else ""
    log.info(
        "%s %s | чат=%s агент=%s | %s%s%s",
        PREFIX,
        _phase_label(phase),
        chat_id or "—",
        agent_id if agent_id is not None else "—",
        limit_part,
        src,
        extra,
    )


def log_run_start(
    *,
    mode: str,
    max_iterations: int,
    model: str,
    chat_id: Optional[str] = None,
    tool_names: Optional[Sequence[str]] = None,
    mcp_servers: Optional[Sequence[str]] = None,
    native_tools: int = 0,
    mcp_tools: int = 0,
    limit_source: str = "",
    depth: int = 0,
) -> None:
    names = list(tool_names or [])
    preview = ", ".join(names[:12])
    if len(names) > 12:
        preview += f" …(+{len(names) - 12})"
    servers = list(mcp_servers or [])
    log.info(
        "%s Старт цикла агента | режим=%s | лимит шагов=%s (%s) | модель=%s | "
        "чат=%s | инструментов=%s (встроенных=%s, MCP=%s) | серверы=%s | "
        "глубина субагента=%s | список=[%s]",
        PREFIX,
        _MODE_RU.get(mode, mode),
        max_iterations,
        limit_source or "вычислен",
        model or "—",
        chat_id or "—",
        len(names),
        native_tools,
        mcp_tools,
        servers if servers else "—",
        depth,
        preview or "—",
    )


def log_llm_call(
    *,
    step: int,
    limit: int,
    kind: str,
    model: str,
    messages_count: int = 0,
    has_tools_schema: bool = False,
) -> None:
    tools_hint = (
        "схема инструментов передана" if has_tools_schema else "без схемы инструментов"
    )
    log.info(
        "%s Шаг %s/%s · обращение к LLM | номер шага=%s, максимум=%s | %s | "
        "модель=%s | сообщений в контексте=%s | %s",
        PREFIX,
        step,
        limit,
        step,
        limit,
        _LLM_KIND_RU.get(kind, kind),
        model or "—",
        messages_count,
        tools_hint,
    )


def log_llm_result(
    *,
    step: int,
    limit: int,
    has_tool_calls: bool,
    tool_names: Optional[Sequence[str]] = None,
    content_preview: str = "",
    duration_ms: Optional[int] = None,
) -> None:
    names = list(tool_names or [])
    timing = f" | {duration_ms} мс" if duration_ms is not None else ""
    if has_tool_calls:
        log.info(
            "%s Шаг %s/%s · ответ LLM: хочет вызвать инструменты (%s шт.): [%s]%s "
            "| номер шага=%s из %s",
            PREFIX,
            step,
            limit,
            len(names),
            ", ".join(names) or "—",
            timing,
            step,
            limit,
        )
    else:
        log.info(
            "%s Шаг %s/%s · ответ LLM: итоговый текст | номер шага=%s из %s | «%s»%s",
            PREFIX,
            step,
            limit,
            step,
            limit,
            _trunc(content_preview),
            timing,
        )


def log_tool_call(
    *,
    step: int,
    limit: int,
    tool: str,
    server_id: str = "",
    kind: str = "mcp",
    arguments: Any = None,
) -> None:
    log.info(
        "%s Шаг %s/%s · вызов инструмента | номер шага=%s из %s | тип=%s | сервер=%s | имя=%s",
        PREFIX,
        step,
        limit,
        step,
        limit,
        _TOOL_KIND_RU.get(kind, kind),
        server_id or "—",
        tool or "—",
    )
    if arguments is not None:
        log.debug(
            "%s Шаг %s/%s · аргументы инструмента %s: %s",
            PREFIX,
            step,
            limit,
            tool or "—",
            _args_preview(arguments),
        )


def log_tool_result(
    *,
    step: int,
    limit: int,
    tool: str,
    success: bool,
    duration_ms: int = 0,
    error: Optional[str] = None,
    result_preview: str = "",
) -> None:
    if success:
        log.info(
            "%s Шаг %s/%s · инструмент выполнен | номер шага=%s из %s | %s | %s мс | результат: «%s»",
            PREFIX,
            step,
            limit,
            step,
            limit,
            tool or "—",
            duration_ms,
            _trunc(result_preview),
        )
    else:
        log.info(
            "%s Шаг %s/%s · ошибка инструмента | номер шага=%s из %s | %s | %s мс | %s",
            PREFIX,
            step,
            limit,
            step,
            limit,
            tool or "—",
            duration_ms,
            _trunc(error or "неизвестная ошибка", 300),
        )


def log_run_end(
    *,
    mode: str,
    steps_used: int,
    limit: int,
    tool_calls_executed: int,
    reason: str,
    content_preview: str = "",
) -> None:
    hit_cap = steps_used >= limit and reason in ("max_iterations", "exhausted")
    limit_note = "да, упёрлись в лимит" if hit_cap else "нет"
    remaining = max(0, limit - steps_used)
    log.info(
        "%s Конец цикла | режим=%s | ИТОГО шагов: %s из %s (осталось %s) | "
        "вызовов инструментов=%s | причина: %s | упёрлись в лимит: %s | «%s»",
        PREFIX,
        _MODE_RU.get(mode, mode),
        steps_used,
        limit,
        remaining,
        tool_calls_executed,
        _END_REASON_RU.get(reason, reason),
        limit_note,
        _trunc(content_preview),
    )


def describe_limit_source(agent_profile: Optional[Mapping[str, Any]]) -> str:
    """Откуда взят recursion_limit: поле агента или глобальный AGENT_GRAPH_STEPS."""
    if isinstance(agent_profile, Mapping):
        raw = agent_profile.get("recursion_limit")
        if isinstance(raw, int) and raw > 0:
            return f"настройка агента ({raw})"
        if isinstance(raw, str) and raw.strip().isdigit() and int(raw.strip()) > 0:
            return f"настройка агента ({raw.strip()})"
    return "глобальный (AGENT_GRAPH_STEPS / по умолчанию)"


def log_chain_hop(
    *,
    index: int,
    total: int,
    agent_id: Any = None,
    agent_name: str = "",
    chat_id: Optional[str] = None,
    has_rag: bool = False,
    recursion_limit: Optional[int] = None,
) -> None:
    """Позиция агента в цепочке — это НЕ номер шага recursion_limit."""
    rag = "да" if has_rag else "нет"
    limit = recursion_limit if recursion_limit is not None else "—"
    log.info(
        "%s Цепочка: позиция агента %s/%s (не номер шага LLM) | id=%s | имя=«%s» | "
        "чат=%s | RAG=%s | у этого агента лимит шагов графа=%s "
        "(ниже смотрите «Шаг N/%s» внутри этого агента)",
        PREFIX,
        index,
        total,
        agent_id if agent_id is not None else "—",
        agent_name or "—",
        chat_id or "—",
        rag,
        limit,
        limit,
    )


def log_plain_llm(
    *,
    phase: str,
    chat_id: Optional[str] = None,
    agent_id: Any = None,
    model: str = "",
    recursion_limit: Optional[int] = None,
    duration_ms: Optional[int] = None,
    content_preview: str = "",
    detail: str = "",
) -> None:
    """Одиночный ask_agent без MCP: всегда ровно 1 шаг из лимита (1/N)."""
    limit = (
        recursion_limit
        if isinstance(recursion_limit, int) and recursion_limit > 0
        else 1
    )
    timing = f" | {duration_ms} мс" if duration_ms is not None else ""
    preview = f" | «{_trunc(content_preview)}»" if content_preview else ""
    extra = f" | {detail}" if detail else ""
    if phase == "start":
        log.info(
            "%s Шаг 1/%s · обращение к LLM | обычный запрос (без инструментов) | "
            "чат=%s агент=%s | модель=%s | "
            "номер шага=1, максимум=%s (инструментов нет — дальше шагов не будет)%s",
            PREFIX,
            limit,
            chat_id or "—",
            agent_id if agent_id is not None else "—",
            model or "—",
            limit,
            extra,
        )
    elif phase == "done":
        log.info(
            "%s Шаг 1/%s · ответ LLM получен | чат=%s агент=%s | модель=%s%s%s",
            PREFIX,
            limit,
            chat_id or "—",
            agent_id if agent_id is not None else "—",
            model or "—",
            timing,
            preview,
        )
        log.info(
            "%s Итог по шагам: использовано 1 из %s | вызовов инструментов=0 | "
            "причина: один вызов LLM без цикла инструментов | упёрлись в лимит: нет",
            PREFIX,
            limit,
        )
    else:
        log.info(
            "%s Шаг 1/%s · обращение к LLM (%s) | чат=%s агент=%s | модель=%s%s%s%s",
            PREFIX,
            limit,
            phase,
            chat_id or "—",
            agent_id if agent_id is not None else "—",
            model or "—",
            timing,
            preview,
            extra,
        )
