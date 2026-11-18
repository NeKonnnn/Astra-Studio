"""Субагент перед вызовом модели: общий RAG, своя база, навыки, артефакты, плагин."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend.agents.shared_rag import SHARED_RAG_KEY
from backend.agents.subagents.scope import _pull_named_documents
from backend.realtime.helpers import kb_search_agent_documents
from backend.settings.logging import get_logger

log = get_logger(__name__)


async def _build_shared_rag_context(
    prompt: str,
    kb_doc_ids: List[int],
    child_profile: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Найти релевантные фрагменты общей базы знаний под запрос субагента.

    Возвращает (текст для system prompt, document_search для UI) —
    у каждого субагента своя карточка источников.

    Субагент видит общую базу так же, как главный в своём чате:
    - фрагментов столько же - rag_chat_top_k главного (снимок настроек
      запроса - его), а не 8;
    - у каждого фрагмента имя файла - иначе ребёнок сочинял названия
      («Заключение_ДЭБ.docx (предполагаемое название)»);
    - в начале перечень ВСЕХ файлов базы: по 8 фрагментам из 7 файлов
      ребёнок решал, что остальных 20 в пакете нет («документов по БГ нет»).
    """
    if not kb_doc_ids:
        return "", None
    try:
        from backend.app_state import get_rag_chat_top_k, rag_client
        from backend.rag_query.context_budget import rag_context_max_chars
        from backend.realtime.rag_evidence import (
            build_rag_id_to_filename,
            build_rag_inventory_block,
            filter_rag_hits_by_score,
            format_rag_fragments,
            hits_to_document_search_trace,
            rag_guard_env,
            rag_scoped_filenames,
        )
    except Exception:
        log.exception("shared RAG: не удалось импортировать зависимости")
        return "", None
    if not rag_client:
        return "", None
    top_k = get_rag_chat_top_k("agent")
    try:
        hits: List[Tuple[str, float, Optional[int], Optional[int]]] = list(
            await kb_search_agent_documents(rag_client, prompt, kb_doc_ids, k=top_k)
            or []
        )
    except Exception:
        # Поиск упал (таймаут SVC-RAG и т.п.) - перечень файлов ребёнок всё
        # равно получит: иначе на «какие файлы ты видишь» ответ «никаких».
        log.exception("shared RAG: ошибка поиска по общей базе знаний")
        hits = []
    try:
        min_sim, _block = rag_guard_env("agent")
        hits = filter_rag_hits_by_score(hits, min_sim)
    except Exception:
        log.exception("shared RAG: фильтр по score")
    try:
        rows = list(await rag_client.kb_list_documents() or [])
        id_to_name = build_rag_id_to_filename(rows)
    except Exception:
        log.exception("shared RAG: список документов")
        id_to_name = {}
    hits = await _pull_named_documents(
        rag_client, prompt, hits, id_to_name, kb_doc_ids, top_k, child_profile
    )
    # Перечень - только файлы общей базы главного, не вся KB: чужие имена
    # ребёнку показывать нельзя (см. rag_scoped_filenames).
    inventory = build_rag_inventory_block(
        [("Общая база знаний", rag_scoped_filenames(id_to_name, kb_doc_ids))]
    )
    trace = hits_to_document_search_trace(
        query=prompt,
        hits=hits,
        id_to_name=id_to_name,
        store="shared_kb",
        strategy="auto",
    )
    parts: List[str] = []
    if hits:
        parts, _m = format_rag_fragments(
            hits,
            id_to_name,
            max_chars=rag_context_max_chars("kb (direct)"),
            store_label="kb (direct)",
            include_chunk_meta=False,
        )
    if not inventory and not parts:
        return "", trace
    body = "\n\n".join(p for p in (inventory, "".join(parts).strip()) if p)
    ctx = (
        "Используй приведённые ниже фрагменты из общей базы знаний цепочки агентов "
        "как контекст для ответа. Перечень файлов - полный список того, что "
        "загружено; фрагменты - найденное по задаче, у каждого указан файл. "
        "Если информации недостаточно — скажи об этом.\n\n"
        f"<shared_knowledge>\n{body}\n</shared_knowledge>"
    )
    return ctx, trace


async def _apply_child_kb_context(
    child_profile: Dict[str, Any],
    prompt: str,
    system_prompt: str,
    target_agent_id: int,
) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """База знаний ребёнка: тот же поиск и та же разметка, что в чате с ним.

    Раннер изолирует ребёнка от RAG родителя - это задумано (общий RAG
    цепочки - отдельный режим, см. shared_chain_rag) Но собственную
    базу ребёнка он тоже не читал: специалист по ВНД отвечал по памяти
    модели. Возвращает (prompt, system_prompt, document_search) - с фрагментами
    и правилами CONTEXT, либо нетронутые, если базы нет.

    Даже без релевантных hits в prompt кладётся перечень доступных файлов
    (как в обычном чате) — иначе вопрос «какие документы ты видишь?»
    оставался без ответа.
    """
    kb_ids = child_profile.get("kb_document_ids") or []
    if not (
        child_profile.get("file_search_enabled")
        and isinstance(kb_ids, list)
        and len(kb_ids) > 0
    ):
        log.info(
            "[chat-#tag/subagent] agent_id=%s: KB выключена или пуста "
            "(file_search=%s kb_ids=%s)",
            target_agent_id,
            bool(child_profile.get("file_search_enabled")),
            kb_ids,
        )
        return prompt, system_prompt, None
    # Документы, которые общий RAG цепочки (shared_chain_rag) уже положил в
    # системный промпт, второй раз не ищем: при вызове self это та же база.
    shared = {str(v) for v in (child_profile.get(SHARED_RAG_KEY) or [])}
    if shared:
        kb_ids = [i for i in kb_ids if str(i) not in shared]
        if not kb_ids:
            return prompt, system_prompt, None
    # Карточка ограничила поиск файлами (см. _resolve_card_scope): ищем только
    # в них. Пустой список - указанных файлов в базе нет, искать нечего.
    scope_ids = child_profile.get("_scope_doc_ids")
    if isinstance(scope_ids, list):
        kb_ids = [
            i for i in kb_ids
            if str(i).strip().lstrip("-").isdigit() and int(i) in scope_ids
        ]
        if not kb_ids:
            return prompt, system_prompt, None
    try:
        from backend.app_state import get_rag_chat_top_k, rag_client
        from backend.rag_query.context_budget import rag_context_max_chars
        from backend.rag_query.prompts import merge_strict_rag_system_prompt
        from backend.realtime.rag_evidence import (
            build_rag_id_to_filename,
            build_rag_inventory_block,
            filter_rag_hits_by_score,
            format_rag_fragments,
            hits_to_document_search_trace,
            rag_guard_env,
            rag_scoped_filenames,
        )

        if rag_client is None:
            log.warning(
                "[chat-#tag/subagent] agent_id=%s: rag_client недоступен, KB не подмешан",
                target_agent_id,
            )
            return prompt, system_prompt, None

        rows = list(await rag_client.kb_list_documents() or [])
        id_to_name = build_rag_id_to_filename(rows)
        inventory = build_rag_inventory_block(
            [
                (
                    "База Знаний агента",
                    rag_scoped_filenames(id_to_name, kb_ids),
                )
            ]
        )

        hits = list(
            await kb_search_agent_documents(
                rag_client,
                prompt,
                kb_ids,
                k=get_rag_chat_top_k("agent"),
                strategy="auto",
            )
            or []
        )
        min_sim, _block = rag_guard_env("agent")
        hits = filter_rag_hits_by_score(hits, min_sim)
        hits = await _pull_named_documents(
            rag_client,
            prompt,
            hits,
            id_to_name,
            kb_ids,
            get_rag_chat_top_k("agent"),
            child_profile,
            strategy="auto",
        )
        log.info(
            "[chat-#tag/subagent] agent_id=%s: KB docs=%s hits=%s inventory=%s",
            target_agent_id,
            len(kb_ids),
            len(hits),
            "да" if inventory else "нет",
        )

        trace = hits_to_document_search_trace(
            query=prompt,
            hits=hits,
            id_to_name=id_to_name,
            store="kb",
            strategy="auto",
        )

        prefix_parts: List[str] = []
        if inventory:
            prefix_parts.append(inventory)
        if hits:
            parts, _m = format_rag_fragments(
                hits,
                id_to_name,
                max_chars=rag_context_max_chars("kb (direct)"),
                store_label="kb (direct)",
                include_chunk_meta=False,
            )
            prefix_parts.append(
                f"База Знаний (постоянные документы):\n{''.join(parts)}"
            )

        if not prefix_parts:
            return prompt, system_prompt, trace

        new_prompt = "\n\n".join(prefix_parts) + f"\n\n{prompt}"
        # Промпт карточки - как rag_override: так же, как главному в его чате.
        # С rag_override=None правила RAG дописывались после карточки без
        # оговорки, чья инструкция главнее, и перебивали её формат ответа.
        new_system = merge_strict_rag_system_prompt(
            None, rag_override=system_prompt or None
        )
        return new_prompt, new_system, trace
    except Exception:
        # Переиндексация базы, недоступный SVC-RAG и т.п. - ребёнок отвечает
        # без документов; ронять родителя из-за него нельзя.
        log.exception(
            "[chat-#tag/subagent] agent_id=%s: поиск по базе не удался, отвечаю без неё",
            target_agent_id,
        )
        return prompt, system_prompt, None


async def _apply_child_skills_and_artifacts(
    child_profile: Dict[str, Any],
    prompt: str,
    system_prompt: str,
    user: Optional[dict],
    target_agent_id: int,
    emit_event=None,
) -> Tuple[str, str, List[str]]:
    """Навыки и артефакты ребёнка - как в его собственном чате.

    Без этого ребёнок, который в своём чате рисует презентации и схемы,
    как субагент отвечал текстовым планом: инструкции про :::artifact у него
    не было, навыки карточки в промпт не попадали. Возвращает
    (prompt, system_prompt, доп. tool_ids из навыков).
    """
    extra_tools: List[str] = []
    lazy_ids: List[str] = []
    primed: Dict[str, Any] = {}
    try:
        from backend.services.skills import apply_skills_to_chat, strip_skill_mentions

        new_system, _stripped, lazy_ids, extra_tools, primed = await apply_skills_to_chat(
            system_prompt=system_prompt or None,
            user_message=prompt,
            data={},
            agent_profile=child_profile,
            current_user=user,
            history=None,
        )
        system_prompt = new_system or ""
        prompt = strip_skill_mentions(prompt)
        try:
            from backend.services.tag_mentions import strip_tag_mentions

            prompt = strip_tag_mentions(prompt)
        except Exception:
            pass
        if lazy_ids:
            # Отложенные навыки грузятся инструментом по __skill_ids__ из
            # контекста запроса. Контекст общий с родителем - дополняем, не
            # заменяем: у родителя от этого лишь больше разрешённых навыков.
            from backend.tools.tool_context import get_tool_context, set_tool_context

            ctx = dict(get_tool_context() or {})
            ctx["__skill_ids__"] = list(
                dict.fromkeys([*(ctx.get("__skill_ids__") or []), *lazy_ids])
            )
            if user is not None:
                ctx.setdefault("current_user", user)
            set_tool_context(ctx)
    except Exception:
        log.exception("subagent agent_id=%s: навыки не применились, иду без них", target_agent_id)
    artifacts_on = False
    try:
        from backend.prompts.artifacts import maybe_artifacts_prompt_for_agent
        from backend.services.skills import append_to_system_prompt

        block = maybe_artifacts_prompt_for_agent(child_profile)
        if block:
            system_prompt = append_to_system_prompt(system_prompt or None, block) or ""
            artifacts_on = True
    except Exception:
        log.exception("subagent agent_id=%s: артефакты не применились, иду без них", target_agent_id)
    if emit_event:
        try:
            from backend.mcp.activity_scope import emit_scoped_activity

            skill_names = list(
                dict.fromkeys(
                    [
                        *[str(x) for x in ((primed or {}).get("manual") or []) if x],
                        *[str(x) for x in ((primed or {}).get("always_apply") or []) if x],
                        *[str(x) for x in (lazy_ids or []) if x],
                    ]
                )
            )
            for skill_name in skill_names:
                await emit_scoped_activity(
                    emit_event,
                    tool="skill",
                    activity_kind="skill",
                    arguments={"skill": skill_name, "agent_id": int(target_agent_id)},
                    success=True,
                    result_preview=f"Навык «{skill_name}» подключён",
                    result=f"skill:{skill_name}",
                )
            if artifacts_on:
                await emit_scoped_activity(
                    emit_event,
                    tool="artifacts",
                    activity_kind="artifacts",
                    arguments={"enabled": True, "agent_id": int(target_agent_id)},
                    success=True,
                    result_preview="Режим артефактов включён",
                )
        except Exception:
            log.exception(
                "subagent agent_id=%s: не удалось эмитить skills/artifacts",
                target_agent_id,
            )
    log.debug(
        "subagent agent_id=%s: навыки отложенные=%s инструменты=%s артефакты=%s",
        target_agent_id,
        list(lazy_ids or []),
        list(extra_tools or []),
        "да" if artifacts_on else "нет",
    )
    return prompt, system_prompt, [str(t) for t in (extra_tools or []) if str(t).strip()]


async def _run_child_plugin(
    child_profile: Dict[str, Any],
    prompt: str,
    system_prompt: str,
    inline_attachments: Optional[List[Any]],
    target_agent_id: int,
    emit_event,
) -> Tuple[str, str, str]:
    """Плагин ребёнка на вложениях сообщения пользователя - как в его чате.

    Возвращает (prompt, system_prompt, artifact_markdown). Плагина нет -
    всё как было. Плагин есть, файла нет - в системный промпт подсказка.
    Файл есть - вердикт в промпт, подсказка в системный промпт, артефакт
    с полным вердиктом - в ответ ребёнка (его потом переносит 63).
    """
    try:
        from backend.plugins.orchestrator_bridge import resolve_agent_plugin_ids
        from backend.services.skills import append_to_system_prompt

        ids = resolve_agent_plugin_ids(child_profile)
    except Exception:
        log.exception("subagent agent_id=%s: не удалось прочитать плагины карточки", target_agent_id)
        return prompt, system_prompt, ""
    if not ids:
        return prompt, system_prompt, ""
    try:
        from backend.services.plugins_direct import (
            pick_plugin_run,
            prompt_block_for_outcome,
            run_plugin_direct,
            system_note_prerun,
        )

        run = pick_plugin_run(ids, inline_attachments, prompt, chat_mode="subagent")
        if not run:
            note = (
                "У тебя подключён плагин, но подходящего файла во вложениях сообщения "
                "пользователя нет - плагин не запускался. Отвечай по имеющимся данным и "
                "скажи, что для полного разбора нужен файл нужного формата, приложенный "
                "к сообщению."
            )
            return prompt, append_to_system_prompt(system_prompt or None, note) or "", ""

        import time
        import uuid

        from backend.agents.subagents.settings import NATIVE_SERVER_ID
        from backend.mcp.events import emit_mcp_tool_end, emit_mcp_tool_start

        tool_name = f"plugin:{run.plugin_id}"
        call_id = uuid.uuid4().hex
        started = time.perf_counter()
        args = {"file": run.file_name, "agent_id": int(target_agent_id)}
        # Аудит идёт минуты - без карточки это выглядит как зависание.
        await emit_mcp_tool_start(
            emit_event,
            server_id=NATIVE_SERVER_ID,
            tool=tool_name,
            qualified_name=tool_name,
            call_id=call_id,
            arguments=args,
        )
        outcome = await run_plugin_direct(run, chat_mode="subagent")
        duration_ms = int((time.perf_counter() - started) * 1000)
        verdict = (outcome.verdict_markdown or "").strip()
        await emit_mcp_tool_end(
            emit_event,
            server_id=NATIVE_SERVER_ID,
            tool=tool_name,
            qualified_name=tool_name,
            success=bool(outcome.ok),
            duration_ms=duration_ms,
            error=None if outcome.ok else (outcome.error or "plugin failed"),
            result_preview=verdict[:500] if outcome.ok else None,
            call_id=call_id,
            arguments=args,
            result=verdict if outcome.ok else (outcome.error or ""),
        )
        log.info(
            "[subagent] плагин %s файл=«%s» успех=%s заняло=%s мс agent_id=%s",
            run.plugin_id,
            run.file_name,
            "да" if outcome.ok else "нет",
            duration_ms,
            target_agent_id,
        )
        new_prompt = f"{prompt_block_for_outcome(run, outcome)}\n\n{prompt}"
        new_system = append_to_system_prompt(system_prompt or None, system_note_prerun(run)) or ""
        return new_prompt, new_system, (outcome.artifact_markdown or "") if outcome.ok else ""
    except Exception:
        log.exception("subagent agent_id=%s: плагин не отработал, отвечаю без него", target_agent_id)
        return prompt, system_prompt, ""


def _with_child_plugin_artifact(text: str, artifact_markdown: str) -> str:
    """Дописать артефакт плагина к ответу ребёнка (если он есть)."""
    if not artifact_markdown:
        return text
    try:
        from backend.plugins.artifact_format import append_artifacts_to_answer

        return append_artifacts_to_answer(text, artifact_markdown)
    except Exception:
        log.exception("subagent: не удалось дописать артефакт плагина")
        return text
