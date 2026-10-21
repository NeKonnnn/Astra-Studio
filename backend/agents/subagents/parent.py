
"""Субагенты в цикле инструментов родителя.

Всё, что цикл агента делает ради субагентов, - здесь, по одной функции на
место вызова:

  chat_integration.run_mcp_for_chat   → build_parent_run_context
  chat_integration.maybe_run_mcp_agent → prepare_parent_tools, with_parent_rules,
                                          finish_parent_result
  agent_loop (native tools) и
  prompt_fc_handler (prompt_json_fc)  → subagent_call_args, new_subagent_call,
                                          run_native_tool_call
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend.agents.config import resolve_recursion_limit
from backend.agents.subagents.answers import (
    append_subagent_rules,
    drop_answer_marks,
    expand_subagent_answers,
    orphan_answer_marks,
    subagent_result_for_model,
)
from backend.agents.subagents.names import (
    load_subagent_agent_names,
    with_subagent_display_name,
)
from backend.agents.subagents.settings import (
    NATIVE_SERVER_ID,
    SUBAGENT_TOOL_NAME,
    AgentSubagentsConfig,
    subagents_from_profile,
)
from backend.agents.subagents.tool import (
    SubagentCall,
    SubagentRunContext,
    build_subagent_tools,
    execute_native_tool,
)
from backend.mcp.types import McpToolInfo
from backend.agents.subagents.plan import (
    log_plan_report,
    make_subagent_plan,
    plan_enabled,
    plan_missing_note,
    plan_progress_line,
    plan_system_block,
)
from backend.llm_providers.routing import (
    merge_sampling_request_extra,
    thinking_request_extra,
)
from backend.mcp.resolvers import build_chat_messages
from backend.settings.logging import get_logger

log = get_logger(__name__)

# Модель поставила метку, не вызвав субагента: одно сообщение - и цикл идёт дальше
ORPHAN_MARK_NOTE = (
    "Ты поставил в ответ метку ответа субагента ({marks}), но такого ответа нет: "
    "метки [[ОТВЕТ N]] появляются только после вызова инструмента subagent. "
    "Если для задачи есть подходящий субагент - вызови его сейчас. Если нет - "
    "ответь сам, без меток."
)
ORPHAN_MARK_FALLBACK = (
    "Агент сослался на ответ субагента, но субагента не вызвал. Повторите запрос."
)


def build_parent_run_context(
    agent_profile: Optional[dict],
    *,
    user: dict,
    user_id: Optional[str],
    enable_thinking: bool,
    emit_event=None,
    inline_attachments: Optional[List[Any]] = None,
    user_question: Optional[str] = None,
    executor=None,
) -> Tuple[Optional[AgentSubagentsConfig], Optional[SubagentRunContext]]:
    """Настройки субагентов карточки и контекст хода (для run_mcp_for_chat).

    Контекст - только если субагенты у агента включены. Исполнитель по
    умолчанию - run_isolated_subagent (subagents/runner.py).
    """
    sub_cfg = subagents_from_profile(agent_profile) if agent_profile else None
    sub_ctx = None
    if agent_profile and sub_cfg and sub_cfg.enabled:
        from backend.agents.subagents.runner import run_isolated_subagent

        parent_id = agent_profile.get("agent_id")
        step_limit = resolve_recursion_limit(agent_profile)

        async def _executor(**kwargs):
            return await run_isolated_subagent(
                target_agent_id=kwargs["target_agent_id"],
                prompt=kwargs["prompt"],
                parent_profile=kwargs["parent_profile"],
                user=user,
                user_id=user_id,
                depth=kwargs.get("depth", 0),
                remaining_steps=kwargs.get("remaining_steps", step_limit - 1),
                enable_thinking=enable_thinking,
                emit_event=emit_event,
                inline_attachments=kwargs.get("inline_attachments"),
                user_question=kwargs.get("user_question"),
                prior_answers=kwargs.get("prior_answers"),
            )

        sub_ctx = SubagentRunContext(
            parent_agent_id=int(parent_id) if parent_id is not None else None,
            parent_profile=dict(agent_profile),
            user=user,
            user_id=user_id,
            depth=0,
            remaining_steps=step_limit - 1,
            executor=executor or _executor,
            inline_attachments=list(inline_attachments or []) or None,
            user_question=user_question,
        )
    return sub_cfg, sub_ctx


async def prepare_parent_tools(
    *,
    agent_profile: Optional[dict],
    sub_cfg: Optional[AgentSubagentsConfig],
    sub_ctx: Optional[SubagentRunContext],
    native_tools: Optional[List[McpToolInfo]],
    user_id: Optional[str],
) -> Tuple[List[McpToolInfo], Optional[AgentSubagentsConfig]]:
    """Инструмент subagent для цикла родителя: (встроенные инструменты, настройки).

    Встроенные уже переданы (ребёнок со своими субагентами) - как есть.
    Иначе по карточке: имена субагентов - из БД, со снимком на случай шаринга.
    """
    native_tools = list(native_tools or [])
    if agent_profile is not None and sub_cfg is None:
        sub_cfg = subagents_from_profile(agent_profile)
    if agent_profile is not None and sub_cfg.enabled and not native_tools:
        parent_id = agent_profile.get("agent_id")
        names = await load_subagent_agent_names(
            sub_cfg.agent_ids,
            user_id=user_id,
            snapshot_names=sub_cfg.agent_names,
        )
        if parent_id is not None and agent_profile.get("name"):
            names[int(parent_id)] = str(agent_profile["name"])
        native_tools = build_subagent_tools(
            sub_cfg,
            parent_agent_id=int(parent_id) if parent_id is not None else None,
            agent_names=names,
        )
        if sub_ctx is not None:
            sub_ctx.agent_names = {**dict(sub_ctx.agent_names or {}), **names}
    return native_tools, sub_cfg


def with_parent_rules(
    system_prompt: Optional[str], native_tools: List[McpToolInfo]
) -> Optional[str]:
    """Есть инструмент subagent - родителю правила работы с ответами детей.

    Код, а не карточка: одинаково для всех агентов, копировать некуда.
    """
    if any(t.name == SUBAGENT_TOOL_NAME for t in native_tools):
        return append_subagent_rules(system_prompt)
    return system_prompt


async def _rerun_with_note(rerun, result, note: str):
    """Повтор цикла родителя: ход до сих пор + его итог + пояснение.

    С тем, что цикл уже сделал (вызовы субагентов и их ответы), а не с начала.
    """
    extra = list(getattr(result, "new_messages", None) or []) + [
        {"role": "assistant", "content": result.content},
        {"role": "user", "content": note},
    ]
    retry = await rerun(extra)
    if retry is None:
        return result
    retry.tool_calls_executed += result.tool_calls_executed
    retry.iterations += result.iterations
    # Следующий повтор продолжит с обоих отрезков хода.
    retry.new_messages = extra + list(getattr(retry, "new_messages", None) or [])
    return retry


async def finish_parent_result(result, sub_ctx: Optional[SubagentRunContext], rerun=None):
    """[[ОТВЕТ N]] в итоге родителя -> ответ ребёнка как есть.

    До отправки в чат: итог уходит в UI одним куском после цикла.
    Метка, для которой ответа нет (субагент не вызывался), - один раз
    возвращаем модели с пояснением (rerun - повтор цикла с доп. сообщениями);
    что осталось без ответа и после этого, в чат не уходит.
    """
    if result is None:
        return result
    orphans = orphan_answer_marks(result.content, sub_ctx)
    if orphans and rerun is not None:
        log.warning(
            "[subagent] метки %s без ответов субагентов (ответов в ходе: %s) - "
            "прошу модель вызвать субагента или ответить без меток",
            orphans,
            len(getattr(sub_ctx, "answers", None) or {}),
        )
        result = await _rerun_with_note(
            rerun, 
            result, 
            ORPHAN_MARK_NOTE.format(marks=", ".join(dict.fromkeys(orphans)))
        )
    # Модель заканчивает, а шаги с субагентами из её же плана не сделаны
    # - один раз список невыполненных.
    missing = (
        plan_missing_note(sub_ctx) if rerun is not None and sub_ctx is not None else ""
    )
    if missing:
        result = await _rerun_with_note(rerun, result, missing)
    # В лог: какие шаги планы сделаны, какие нет (только если план был).
    log_plan_report(sub_ctx)
    if sub_ctx is not None and sub_ctx.answers:
        result.content = expand_subagent_answers(result.content, sub_ctx)
    left = orphan_answer_marks(result.content, sub_ctx)
    if left:
        log.warning("[subagent] метки %s так и остались без ответов - убраны из ответа", left)
        result.content = drop_answer_marks(result.content) or ORPHAN_MARK_FALLBACK
    return result


async def plan_for_parent(
    *,
    messages: List[Dict[str, Any]],
    model_path: str,
    native_tools: List[McpToolInfo],
    sub_ctx: Optional[SubagentRunContext],
    sub_cfg: Optional[AgentSubagentsConfig],
    request_extra: Optional[Dict[str, Any]] = None,
) -> str:
    """План хода по инструкции главного (subagents/plan.py) → блок для его системного промпта.

    Только главный (глубина 0) с инструментом subagent и SUBAGENT_PLAN не 0.
    """
    if (
        sub_ctx is None
        or sub_cfg is None
        or sub_ctx.depth != 0
        or not plan_enabled()
        or not any(t.name == SUBAGENT_TOOL_NAME for t in native_tools)
    ):
        return ""
    sub_ctx.plan = await make_subagent_plan(
        messages=messages,
        model_path=model_path,
        config=sub_cfg,
        parent_agent_id=sub_ctx.parent_agent_id,
        agent_names=sub_ctx.agent_names,
        request_extra=request_extra,
    )
    return plan_system_block(sub_ctx.plan)

async def with_parent_plan(
    system_prompt: Optional[str],
    *,
    user_message: str,
    history: Optional[List[Dict[str, Any]]],
    model_path: str,
    native_tools: List[McpToolInfo],
    sub_ctx: Optional[SubagentRunContext],
    sub_cfg: Optional[AgentSubagentsConfig],
) -> Optional[str]:
    """План хода главного (subagents/plan.py) - в конец его системного промпта.

    Модель главного составляет план сама по своей инструкции, дальше код
    сверяет с ним вызовы. Служебный вызов - без размышлений. Плана нет (не
    главный, нет субагентов, SUBAGENT_PLAN=0, сбой) - промпт как был.
    """
    plan_block = await plan_for_parent(
        messages=build_chat_messages(
            user_message=user_message, history=history, system_prompt=system_prompt
        ),
        model_path=model_path,
        native_tools=native_tools,
        sub_ctx=sub_ctx,
        sub_cfg=sub_cfg,
        request_extra=merge_sampling_request_extra(thinking_request_extra(False)),
    )
    if not plan_block:
        return system_prompt
    return f"{system_prompt}\n\n{plan_block}" if system_prompt else plan_block


def subagent_call_args(
    tool_info: McpToolInfo,
    tool_args: Dict[str, Any],
    subagent_ctx: Optional[SubagentRunContext],
    subagent_config: Optional[AgentSubagentsConfig],
) -> Dict[str, Any]:
    """Аргументы вызова для карточки в чате: у subagent - имя ребёнка (agent_name)."""
    if tool_info.server_id != NATIVE_SERVER_ID or tool_info.name != SUBAGENT_TOOL_NAME:
        return tool_args
    return with_subagent_display_name(
        tool_args,
        parent_agent_id=subagent_ctx.parent_agent_id if subagent_ctx else None,
        config=subagent_config,
        agent_names=subagent_ctx.agent_names if subagent_ctx else None,
    )


def new_subagent_call(
    tool_info: Optional[McpToolInfo], subagent_ctx: Optional[SubagentRunContext]
) -> Optional[SubagentCall]:
    """Слот вызова subagent (None - не субагент): номер метки занят сразу.

    Цикл берёт слоты всех вызовов шага до их запуска - номера [[ОТВЕТ N]]
    идут в порядке вызовов, хотя ответы приходят в любом.
    """
    if (
        subagent_ctx is None
        or tool_info is None
        or tool_info.server_id != NATIVE_SERVER_ID
        or tool_info.name != SUBAGENT_TOOL_NAME
    ):
        return None
    return SubagentCall(reserved_no=subagent_ctx.reserve_answer_no())


async def run_native_tool_call(
    tool_info: McpToolInfo,
    tool_args: Dict[str, Any],
    *,
    subagent_ctx: Optional[SubagentRunContext],
    subagent_config: Optional[AgentSubagentsConfig],
    call: Optional[SubagentCall] = None,
) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """Встроенный инструмент: (результат для модели, для UI, document_search).

    Модели - с меткой [[ОТВЕТ N]], в карточку в чате - без неё; источники
    ребёнка - в карточку. Ошибка уходит наружу: цикл сам ставит failure.
    """
    call = call if call is not None else SubagentCall()
    content = await execute_native_tool(
        tool_info,
        tool_args,
        subagent_ctx=subagent_ctx,
        subagent_config=subagent_config,
        call=call,
    )
    doc_search = call.document_search
    for_model = subagent_result_for_model(subagent_ctx, content, call.answer_no)
    # Прогресс по плану хода - только модели главного, в чат не уходит.
    if subagent_ctx is not None and tool_info.name == SUBAGENT_TOOL_NAME:
        progress = plan_progress_line(subagent_ctx)
        if progress:
            for_model = f"{for_model}\n\n{progress}"
    return for_model, content, doc_search