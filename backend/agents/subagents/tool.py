"""Инструмент subagent: описание для модели родителя и запуск ребёнка по вызову."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Union

from backend.agents.subagents.answers import (
    _remember_subagent_answer,
    answers_for_child,
)
from backend.agents.subagents.settings import (
    MAX_SUBAGENT_DEPTH,
    NATIVE_SERVER_ID,
    SELF_SUBAGENT_TYPE,
    SUBAGENT_TOOL_NAME,
    AgentSubagentsConfig,
    resolve_subagent_target,
    subagent_type_by_name,
)
from backend.mcp.types import McpToolInfo
from backend.settings.logging import get_logger

log = get_logger(__name__)

@dataclass
class SubagentOutcome:
    """Результат изолированного субагента: текст + опциональный RAG-трейс для UI."""

    text: str
    document_search: Optional[Dict[str, Any]] = None

SubagentExecutor = Callable[..., Awaitable[Union[str, SubagentOutcome]]]

@dataclass
class SubagentCall:
    """Один вызов subagent в шаге родителя: номер метки и RAG-трейс ответа.

    Свой у каждого вызова, а не общий в SubagentRunContext: вызовы одного
    шага идут параллельно (subagents/parallel.py) и перетирали бы друг друга.
    """

    # Номер [[ОТВЕТ N]], занятый до запуска - в порядке вызовов шага.
    reserved_no: Optional[int] = None
    # Номер, с которым ответ ушёл модели (None - ответа нет).
    answer_no: Optional[int] = None
    document_search: Optional[Dict[str, Any]] = None

@dataclass
class SubagentRunContext:
    parent_agent_id: Optional[int]
    parent_profile: Dict[str, Any]
    user: Optional[dict]
    user_id: Optional[str]
    depth: int = 0
    remaining_steps: int = 50
    executor: Optional[SubagentExecutor] = None
    # Вложения сообщения пользователя - для
    # плагинов ребёнка. Явно, а не через tool_context: у того глобальный
    # fallback, и файл мог бы прийти из чужого запроса.
    inline_attachments: Optional[List[Any]] = None
    # Имена субагентов (id → подпись) для UI-карточек tool subagent.
    agent_names: Dict[int, str] = field(default_factory=dict)
    # Вопрос пользователя дословно: ребёнок получает его рядом с заданием
    # родителя (subagent_question_for_child).
    user_question: Optional[str] = None
    # Ответы детей этого хода: номер метки [[ОТВЕТ N]] → {"name", "text"}.
    answers: Dict[int, Dict[str, str]] = field(default_factory=dict)
    # Следующий свободный номер метки (reserve_answer_no).
    next_answer_no: int = 1
    # Уже выполненные вызовы этого хода: «agent_id + задание» → номер ответа.
    # Тот же субагент с тем же заданием второй раз не запускается.
    done_calls: Dict[str, int] = field(default_factory=dict)
    # Идущие сейчас вызовы: тот же ключ, что у done_calls. Второй такой же
    # вызов того же шага ждёт первый, а не запускает ребёнка ещё раз.
    running_calls: Dict[str, "asyncio.Future[None]"] = field(default_factory=dict)
    # План хода (subagents/plan.py): [{"do", "subagent", "target_id"}]. Только у главного.
    plan: List[Dict[str, Any]] = field(default_factory=list)

    def reserve_answer_no(self) -> int:
        """Номер метки для нового ответа. Неиспользованный не возвращается."""
        n = max(self.next_answer_no, len(self.answers) + 1)
        self.next_answer_no = n + 1
        return n

def build_subagent_tools(
    config: AgentSubagentsConfig,
    *,
    parent_agent_id: Optional[int],
    agent_names: Mapping[int, str],
) -> List[McpToolInfo]:
    if not config.enabled:
        return []
    enum_values: List[str] = []
    descriptions: List[str] = []
    if config.allow_self and parent_agent_id is not None:
        enum_values.append(SELF_SUBAGENT_TYPE)
        name = agent_names.get(parent_agent_id) or "self"
        descriptions.append(
            f"- {SELF_SUBAGENT_TYPE}: spawn {name} in an isolated context"
        )
    types_by_id = subagent_type_by_name(config, agent_names)
    for aid in config.agent_ids:
        st = types_by_id[aid]
        enum_values.append(st)
        name = agent_names.get(aid) or f"Agent {aid}"
        descriptions.append(f"- {st}: {name}" if st != name else f"- {st}")
    if not enum_values:
        return []
    desc = (
        "Spawn an isolated subagent to handle a focused subtask. "
        "Choose the subagent by its name and description: each task goes to the "
        "subagent whose name/description matches it, never by list order.\n"
        "Its full answer returns with a marker like [[ОТВЕТ 1]]: put the marker "
        "on its own line in your answer to show that answer to the user verbatim.\n"
        "If the result contains :::artifact blocks (diagrams, charts, presentations, "
        "HTML) or download links, reproduce them in your final answer VERBATIM, "
        "unchanged - they are rendered for the user; do not retell them as text.\n"
        + "\n".join(descriptions)
    )
    return [
        McpToolInfo(
            server_id=NATIVE_SERVER_ID,
            name=SUBAGENT_TOOL_NAME,
            qualified_name=SUBAGENT_TOOL_NAME,
            description=desc,
            parameters={
                "type": "object",
                "properties": {
                    "subagent_type": {
                        "type": "string",
                        "enum": enum_values,
                        "description": "Which subagent to spawn",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Focused task for the subagent",
                    },
                },
                "required": ["subagent_type", "prompt"],
            },
        )
    ]

async def execute_subagent_tool(
    arguments: Dict[str, Any],
    *,
    ctx: SubagentRunContext,
    config: AgentSubagentsConfig,
    call: Optional[SubagentCall] = None,
) -> str:
    """Запуск ребёнка. Номер метки и RAG-трейс ответа - в call."""
    call = call if call is not None else SubagentCall()
    if ctx.depth >= MAX_SUBAGENT_DEPTH:
        return f"Subagent depth limit ({MAX_SUBAGENT_DEPTH}) reached."
    if ctx.remaining_steps <= 0:
        return "Subagent step budget exhausted."
    subagent_type = str(arguments.get("subagent_type") or "").strip()
    prompt = str(arguments.get("prompt") or arguments.get("task") or "").strip()
    if not prompt:
        return "Subagent prompt is required."
    target_id = resolve_subagent_target(
        subagent_type,
        parent_agent_id=ctx.parent_agent_id,
        config=config,
        agent_names=ctx.agent_names,
    )
    if target_id is None:
        allowed = list(subagent_type_by_name(config, ctx.agent_names).values())
        log.debug(
            "[subagent] неизвестный subagent_type=%r, допустимые: %s",
            subagent_type,
            allowed,
        )
        return (
            f"Unknown or disallowed subagent_type: {subagent_type!r}. "
            f"Allowed: {', '.join(allowed)}"
        )
    if ctx.executor is None:
        return "Subagent executor is not configured."
    # Тот же субагент с тем же заданием в этом ходе уже отвечал - второй раз
    # не запускаем. 25.09 главный после «задача, вероятно, не закончена»
    # вызвал обоих проверочных повторно с теми же словами: +6 минут и те же
    # поиски. Готовый ответ уходит с той же меткой [[ОТВЕТ N]]; упавший
    # вызов (исключение) не запоминается - его повторить можно.
    call_key = f"{int(target_id)}\n{' '.join(prompt.split()).lower()}"
    running = ctx.running_calls.get(call_key)
    if running is not None:
        # Такой же вызов этого шага ещё идёт (вызовы шага параллельны) - ждём
        # его ответ. Упал - запускаем сами, как после упавшего вызова.
        log.info(
            "[subagent] такой же вызов agent_id=%s уже идёт - жду его ответ",
            target_id,
        )
        await asyncio.shield(running)
    prev_no = ctx.done_calls.get(call_key)
    if prev_no is not None and prev_no in ctx.answers:
        log.info(
            "[subagent] повторный вызов agent_id=%s с тем же заданием - не запускаю, "
            "отдаю ответ [[ОТВЕТ %s]]",
            target_id,
            prev_no,
        )
        call.answer_no = prev_no
        return (
            "(Этот субагент уже ответил на это же задание в этом ходе - "
            "повторно не запускался. Ответ тот же:)\n\n" + ctx.answers[prev_no]["text"]
        )
    # Через tool subagent ходят только вызовы по решению модели: обязательные
    # (по тегам) идут мимо него, в subagents/required.py.
    log.debug(
        "[subagent] ВЫЗОВ ПО ВЫБОРУ РОДИТЕЛЯ: %s → agent_id=%s глубина=%s prompt=«%s»",
        subagent_type,
        target_id,
        ctx.depth + 1,
        prompt[:120].replace("\n", " "),
    )
    done = asyncio.get_running_loop().create_future()
    ctx.running_calls[call_key] = done
    try:
        # [[ОТВЕТ N]] в задании - готовый ответ другого субагента: ребёнку
        # уходит его полный текст, а не пересказ родителя
        child_task, prior_answers = answers_for_child(prompt, ctx)
        raw = await ctx.executor(
            target_agent_id=target_id,
            prompt=child_task,
            prior_answers=prior_answers or None,
            parent_profile=ctx.parent_profile,
            user=ctx.user,
            user_id=ctx.user_id,
            depth=ctx.depth + 1,
            remaining_steps=ctx.remaining_steps,
            inline_attachments=ctx.inline_attachments,
            user_question=ctx.user_question,
        )
        if isinstance(raw, SubagentOutcome):
            call.document_search = raw.document_search
            text = raw.text
        else:
            text = str(raw or "")
        call.answer_no = _remember_subagent_answer(
            ctx, target_id, text, n=call.reserved_no
        )
        if call.answer_no:
            ctx.done_calls[call_key] = call.answer_no
        return text
    except Exception as exc:
        # Строка «Subagent error» уезжала в UI с success=true. Исключение
        # ловит agent_loop: он проставит failure и напечатает traceback,
        # здесь - только адрес сбоя.
        call.document_search = None
        log.error("Subagent execution failed target=%s: %s", target_id, exc)
        raise
    finally:
        ctx.running_calls.pop(call_key, None)
        if not done.done():
            done.set_result(None)

async def execute_native_tool(
    tool_info: McpToolInfo,
    arguments: Dict[str, Any],
    *,
    subagent_ctx: Optional[SubagentRunContext] = None,
    subagent_config: Optional[AgentSubagentsConfig] = None,
    call: Optional[SubagentCall] = None,
) -> str:
    if tool_info.server_id != NATIVE_SERVER_ID:
        return f"Unknown native tool server: {tool_info.server_id}"
    if tool_info.name == SUBAGENT_TOOL_NAME:
        if subagent_ctx is None or subagent_config is None:
            return "Subagents are not enabled for this run."
        args = arguments if isinstance(arguments, dict) else {}
        return await execute_subagent_tool(
            args, ctx=subagent_ctx, config=subagent_config, call=call
        )
    return f"Unknown native tool: {tool_info.name}"