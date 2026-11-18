"""Запуск изолированного субагента.

По умолчанию субагент изолирован и не наследует RAG родителя. Исключение —
режим «общий RAG для цепочки/субагентов» (config.shared_chain_rag головного
агента): тогда база знаний головного агента подмешивается в контекст субагента,
и этот режим наследуется вниз по всей ветке субагентов.

Что ребёнок получает перед вызовом - child_context.py, в каких файлах ищет -
scope.py, лог об этом - trace.py.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import os
from typing import Any, Dict, List, Optional

from backend.agents.config import resolve_recursion_limit
from backend.agents.shared_rag import (
    SHARED_RAG_KEY,
    accepts_parent_shared_rag,
    shared_kb_ids_for_child,
)
from backend.agents.step_debug import describe_limit_source, log_pre_loop
from backend.agents.subagents.answers import (
    SUBAGENT_CHILD_NOTE,
    subagent_question_for_child,
)
from backend.agents.subagents.child_context import (
    _apply_child_kb_context,
    _apply_child_skills_and_artifacts,
    _build_shared_rag_context,
    _run_child_plugin,
    _with_child_plugin_artifact,
)
from backend.agents.subagents.names import load_subagent_agent_names
from backend.agents.subagents.scope import _card_scope_note, _resolve_card_scope
from backend.agents.subagents.settings import subagents_from_profile
from backend.agents.subagents.tool import (
    SubagentOutcome,
    SubagentRunContext,
    build_subagent_tools,
)
from backend.agents.subagents.trace import _log_subagent_trace
from backend.realtime.helpers import _resolve_agent_chat_params, agent_mcp_tool_ids
from backend.settings.logging import get_logger

log = get_logger(__name__)


def _parent_declares_subagent(
    parent_profile: Dict[str, Any], target_agent_id: int
) -> bool:
    """Субагент явно в карточке родителя (agent_ids) или self-spawn."""
    try:
        tid = int(target_agent_id)
    except (TypeError, ValueError):
        return False
    cfg = subagents_from_profile(parent_profile)
    if tid in cfg.agent_ids:
        return True
    if cfg.allow_self:
        parent_id = parent_profile.get("agent_id")
        try:
            if parent_id is not None and int(parent_id) == tid:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _subagent_max_tokens_floor() -> int:
    """Пол max_tokens ребёнка - как у родителя в handlers (_run_ask).

    Длинные ответы (презентация, код) иначе рвутся на лимите карточки,
    :::artifact-блок остаётся незакрытым и до родителя не доезжает.
    """
    try:
        return max(int(os.getenv("SUBAGENT_MAX_TOKENS_FLOOR", "4096")), 256)
    except (TypeError, ValueError):
        return 4096


async def run_isolated_subagent(
    *,
    target_agent_id: int,
    prompt: str,
    parent_profile: Dict[str, Any],
    user: Optional[dict],
    user_id: Optional[str],
    depth: int,
    remaining_steps: int,
    history: Optional[List[Dict[str, Any]]] = None,
    enable_thinking: bool = False,
    emit_event=None,
    inline_attachments: Optional[List[Any]] = None,
    user_question: Optional[str] = None,
    prior_answers: Optional[str] = None,
) -> SubagentOutcome:
    """Выполнить дочернего агента в изолированном контексте и вернуть итог."""
    # Если родитель объявил этого субагента в карточке — грузим профиль
    # без отдельного ACL на ребёнка (иначе шаринг/галерея родителя ломаются:
    # имя видно, а запуск пишет «not available»).
    trusted = _parent_declares_subagent(parent_profile, int(target_agent_id))
    child_profile = await _resolve_agent_chat_params(
        target_agent_id, user_id, user=user, skip_access_check=trusted
    )
    # agent_id проставляется только найденному агенту. Без него профиль пустой:
    # либо нет доступа (и родитель его не объявлял), либо агент удалён.
    if child_profile.get("agent_id") is None:
        log.warning(
            "subagent: agent_id=%s недоступен для user=%s trusted=%s",
            target_agent_id,
            user_id,
            trusted,
        )
        return SubagentOutcome(
            text=(
                f"Subagent {target_agent_id} is not available for this user "
                "(no access or deleted)."
            )
        )
    if not child_profile.get("model_path"):
        return SubagentOutcome(
            text=f"Subagent {target_agent_id}: model is not configured."
        )
    model_path = str(child_profile["model_path"])
    system_prompt = child_profile.get("system_prompt") or ""
    doc_traces: List[Optional[Dict[str, Any]]] = []
    # Вопрос пользователя дословно - рядом с заданием родителя. До поиска по
    # базам: искать тоже по обоим.
    task_with_question = subagent_question_for_child(user_question, prompt)
    question_added = task_with_question != prompt
    prompt = task_with_question
    task_text = prompt
    shared_trace: Optional[Dict[str, Any]] = None

    # Общий RAG цепочки/субагентов: если у головного агента включён общий RAG,
    # подмешиваем его базу знаний в контекст субагента и протаскиваем список
    # документов вниз по всей ветке дочерних субагентов.
    shared_kb_ids = shared_kb_ids_for_child(parent_profile)
    # В карточке ребёнка выключено «Смотреть общий RAG родителя» - базу
    # родителя не берём и вниз по ветке не передаём: субагенты этого ребёнка
    # увидят только то, чем делится он сам. Своя база ребёнка - как обычно.
    if shared_kb_ids and not accepts_parent_shared_rag(child_profile):
        log.info(
            "[subagent] agent_id=%s «%s»: в карточке выключено «Смотреть общий RAG "
            "родителя» - общая база родителя (%s док.) не подмешана",
            target_agent_id,
            child_profile.get("name") or f"agent_{target_agent_id}",
            len(shared_kb_ids),
        )
        child_profile["_parent_shared_rag_refused"] = len(shared_kb_ids)
        shared_kb_ids = []
    # Файлы, которыми карточка ребёнка ограничила поиск («смотри только …»).
    # Вниз по ветке (SHARED_RAG_KEY) уходит полный список: у внука своя
    # карточка и свои указания.
    scope_ids = await _resolve_card_scope(
        child_profile, shared_kb_ids, int(target_agent_id)
    )
    if shared_kb_ids:
        child_profile[SHARED_RAG_KEY] = list(shared_kb_ids)
        shared_search_ids = (
            shared_kb_ids
            if scope_ids is None
            else [i for i in shared_kb_ids if i in scope_ids]
        )
        shared_ctx, shared_trace = (
            await _build_shared_rag_context(prompt, shared_search_ids, child_profile)
            if shared_search_ids
            else ("", None)
        )
        if shared_trace:
            doc_traces.append(shared_trace)
        if shared_ctx:
            system_prompt = (
                f"{system_prompt}\n\n{shared_ctx}" if system_prompt else shared_ctx
            )
            log.info(
                "[subagent] общий RAG цепочки: agent_id=%s документов=%s hits=%s",
                target_agent_id,
                len(shared_kb_ids),
                len((shared_trace or {}).get("hits") or []),
            )

    prompt, system_prompt, child_kb_trace = await _apply_child_kb_context(
        child_profile, prompt, system_prompt, int(target_agent_id)
    )
    if emit_event and child_kb_trace:
        try:
            from backend.mcp.activity_scope import emit_scoped_activity

            await emit_scoped_activity(
                emit_event,
                tool="document_search",
                activity_kind="knowledge_base",
                arguments={
                    "query": child_kb_trace.get("query"),
                    "agent_id": int(target_agent_id),
                    "files": len(child_kb_trace.get("sourceFiles") or []),
                    "hits": len(child_kb_trace.get("hits") or []),
                },
                success=True,
                result_preview=(
                    f"KB субагента: файлов {len(child_kb_trace.get('sourceFiles') or [])}, "
                    f"фрагментов {len(child_kb_trace.get('hits') or [])}"
                ),
                document_search=child_kb_trace,
            )
        except Exception:
            log.exception(
                "subagent agent_id=%s: не удалось эмитить KB activity",
                target_agent_id,
            )
    if emit_event and shared_trace:
        try:
            from backend.mcp.activity_scope import emit_scoped_activity

            await emit_scoped_activity(
                emit_event,
                tool="document_search",
                activity_kind="knowledge_base",
                arguments={
                    "query": shared_trace.get("query"),
                    "agent_id": int(target_agent_id),
                    "shared": True,
                    "files": len(shared_trace.get("sourceFiles") or []),
                    "hits": len(shared_trace.get("hits") or []),
                },
                success=True,
                result_preview=(
                    f"Общий RAG: файлов {len(shared_trace.get('sourceFiles') or [])}, "
                    f"фрагментов {len(shared_trace.get('hits') or [])}"
                ),
                document_search=shared_trace,
            )
        except Exception:
            log.exception(
                "subagent agent_id=%s: не удалось эмитить shared RAG activity",
                target_agent_id,
            )
    # После поиска: подсказка не должна менять поисковый запрос.
    scope_note = _card_scope_note(child_profile)
    if scope_note:
        prompt = f"{scope_note}\n\n{prompt}"
    # Ответы других субагентов ([[ОТВЕТ N]] в задании) - после поиска: иском
    # по многостраничному тексту SVC-RAG нашёл бы не то и считал бы минуты.
    if prior_answers:
        prompt = f"{prompt}\n\n{prior_answers}"
    if child_kb_trace:
        doc_traces.append(child_kb_trace)

    from backend.realtime.rag_evidence import merge_document_search_traces

    document_search = merge_document_search_traces(*doc_traces)

    sub_cfg = subagents_from_profile(child_profile)
    child_limit = min(remaining_steps, resolve_recursion_limit(child_profile))
    child_limit = max(1, child_limit)
    log_pre_loop(
        phase="subagent_start",
        agent_id=target_agent_id,
        recursion_limit=child_limit,
        limit_source=(
            f"мин(осталось шагов={remaining_steps}, {describe_limit_source(child_profile)})"
        ),
        detail=(
            f"родитель={parent_profile.get('agent_id')} глубина={depth} "
            f"модель={model_path} запрос=«{prompt[:120]}»"
        ),
    )

    prompt, system_prompt, _skill_tool_ids = await _apply_child_skills_and_artifacts(
        child_profile,
        prompt,
        system_prompt,
        user,
        int(target_agent_id),
        emit_event=emit_event,
    )
    # Инструменты навыков - в цикл ребёнка, как у родителя в _handle_direct.
    tool_ids = list(dict.fromkeys([*agent_mcp_tool_ids(child_profile), *_skill_tool_ids]))
    # Лимит карточки снизу поднимаем до пола - как родителю в своём чате.
    card_max_tokens = int(child_profile.get("max_tokens") or 1024)
    child_max_tokens = max(card_max_tokens, _subagent_max_tokens_floor())
    log.debug(
        "[subagent] agent_id=%s max_tokens: карточка=%s → эффективно=%s thinking=%s",
        target_agent_id,
        card_max_tokens,
        child_max_tokens,
        enable_thinking,
    )
    prompt, system_prompt, child_plugin_artifact = await _run_child_plugin(
        child_profile, prompt, system_prompt, inline_attachments, int(target_agent_id), emit_event
    )
    # Ответ уйдёт агенту-родителю, а не человеку: без приветствий и меню из
    # карточки. #тег без выбранного агента (родителя нет) отвечает человеку
    # напрямую - там приписка не нужна.
    if isinstance(parent_profile, dict) and parent_profile.get("agent_id"):
        system_prompt = (
            f"{system_prompt}\n\n{SUBAGENT_CHILD_NOTE}" if system_prompt else SUBAGENT_CHILD_NOTE
        )
    names = await load_subagent_agent_names(
        sub_cfg.agent_ids,
        user_id=user_id,
        snapshot_names=sub_cfg.agent_names,
    )
    if child_profile.get("name"):
        names[int(target_agent_id)] = str(child_profile["name"])
    native_tools = build_subagent_tools(
        sub_cfg,
        parent_agent_id=child_profile.get("agent_id"),
        agent_names=names,
    )

    messages: List[Dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        for item in history[-6:]:
            role = str(item.get("role") or "user")
            content = str(item.get("content") or "")
            if content:
                messages.append({"role": role, "content": content})
    # История ребёнка: то, что уже собрано выше из history[-6:], без
    # системного. Раньше messages никуда не уходили - история терялась.
    child_history: List[Dict[str, Any]] = [
        m for m in messages if m.get("role") in ("user", "assistant")
    ]
    messages.append({"role": "user", "content": prompt})
    _log_subagent_trace(
        target_agent_id=int(target_agent_id),
        child_profile=child_profile,
        parent_profile=parent_profile,
        depth=depth,
        task=task_text,
        question_added=question_added,
        shared_kb_ids=shared_kb_ids,
        shared_trace=shared_trace,
        child_kb_trace=child_kb_trace,
        system_prompt=system_prompt,
        prompt=prompt,
    )

    def _finish(text: str) -> SubagentOutcome:
        return SubagentOutcome(
            text=_with_child_plugin_artifact(text, child_plugin_artifact),
            document_search=document_search,
        )

    if tool_ids or native_tools:
        from backend.mcp.chat_integration import maybe_run_mcp_agent
        from backend.mcp.chat_integration import build_mcp_context_from_user

        mcp_ctx = build_mcp_context_from_user(user or {}, chat_id=None, message_id=None)

        async def _child_executor(**kwargs):
            return await run_isolated_subagent(
                target_agent_id=kwargs["target_agent_id"],
                prompt=kwargs["prompt"],
                parent_profile=kwargs["parent_profile"],
                user=kwargs.get("user"),
                user_id=kwargs.get("user_id"),
                depth=kwargs.get("depth", depth),
                remaining_steps=kwargs.get("remaining_steps", child_limit - 1),
                enable_thinking=enable_thinking,
                emit_event=emit_event,
                inline_attachments=kwargs.get("inline_attachments", inline_attachments),
                user_question=kwargs.get("user_question", user_question),
                prior_answers=kwargs.get("prior_answers"),
            )

        subagent_ctx = SubagentRunContext(
            parent_agent_id=child_profile.get("agent_id"),
            parent_profile=child_profile,
            user=user,
            user_id=user_id,
            depth=depth,
            remaining_steps=child_limit - 1,
            executor=_child_executor,
            inline_attachments=inline_attachments,
            agent_names=dict(names),
            user_question=user_question,
        )
        result = await maybe_run_mcp_agent(
            tool_ids=tool_ids or None,
            user_message=prompt,
            history=child_history,
            system_prompt=system_prompt or None,
            model_path=model_path,
            mcp_context=mcp_ctx,
            temperature=float(child_profile.get("temperature") or 0.7),
            max_tokens=child_max_tokens,
            enable_thinking=enable_thinking,
            event_callback=emit_event,
            max_iterations=child_limit,
            native_tools=native_tools,
            subagent_ctx=subagent_ctx,
            subagent_config=sub_cfg,
        )
        if result is not None:
            if result.content:
                return _finish(result.content.strip())
            return _finish("Subagent finished without a response.")
        # None - цикл инструментов не запускался вовсе: MCP-сервер ребёнка
        # выключен или недоступен, нативных инструментов нет. Раньше здесь
        # отвечали «finished without a response», хотя модель ребёнка даже
        # не вызывалась. Проваливаемся в обычный вызов ниже.
        log.warning(
            "subagent agent_id=%s: инструменты недоступны (tool_ids=%s), отвечаю без них",
            target_agent_id,
            tool_ids,
        )

    from backend.app_state import ask_agent

    _ctx = contextvars.copy_context()
    _call = functools.partial(
        ask_agent,
        prompt,
        history=child_history,
        max_tokens=child_max_tokens,
        streaming=True,
        stream_callback=None,
        model_path=model_path,
        custom_prompt_id=None,
        images=None,
        system_prompt=system_prompt or None,
        temperature=child_profile.get("temperature"),
        enable_thinking=enable_thinking,
    )
    response = await asyncio.get_running_loop().run_in_executor(
        None, functools.partial(_ctx.run, _call)
    )
    return _finish(
        str(response or "").strip() or "Subagent finished without a response."
    )
