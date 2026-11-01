"""Лог [SUBAGENT-TRACE]: что субагент получил перед вызовом модели."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from backend.settings.logging import get_logger

log = get_logger(__name__)


def _trace_files(trace: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Файл → сколько его фрагментов ушло ребёнку (по document_search)."""
    out: Dict[str, int] = {}
    for hit in (trace or {}).get("hits") or []:
        if isinstance(hit, dict) and hit.get("file"):
            out[str(hit["file"])] = out.get(str(hit["file"]), 0) + 1
    return out


def _scope_log_part(child_profile: Dict[str, Any]) -> str:
    scope = child_profile.get("_scope_info")
    if not scope:
        return "нет"
    return (
        f"правило «{scope.get('rule')}» файлы={scope.get('files') or []} "
        f"не_найдены={scope.get('missing') or []}"
    )


def _log_subagent_trace(
    *,
    target_agent_id: int,
    child_profile: Dict[str, Any],
    parent_profile: Dict[str, Any],
    depth: int,
    task: str,
    question_added: bool,
    shared_kb_ids: List[int],
    shared_trace: Optional[Dict[str, Any]],
    child_kb_trace: Optional[Dict[str, Any]],
    system_prompt: str,
    prompt: str,
) -> None:
    """Одна строка [SUBAGENT-TRACE]: что субагент получил перед вызовом модели.

    Отвечает на вопросы «видит ли он свою карточку» и «какие документы он
    смотрел» без отладчика: промпт карточки на месте или нет, дошёл ли вопрос
    пользователя, по какой задаче искали, сколько фрагментов и из каких файлов пришло из общей базы главного
    и из своей. Только лог - в ответ и в UI ничего не уходит.
    """
    try:
        card = str(child_profile.get("system_prompt") or "").strip()
        # Навыки и артефакты дописывают в системный промпт, но карточку не
        # трогают: достаточно найти её начало.
        card_in = bool(card) and card[:200] in (system_prompt or "")
        try:
            from backend.app_state import get_rag_chat_top_k

            top_k = get_rag_chat_top_k("agent")
        except Exception:
            top_k = "?"
        shared_files = _trace_files(shared_trace)
        own_files = _trace_files(child_kb_trace)
        own_ids = child_profile.get("kb_document_ids") or []
        if shared_kb_ids:
            shared_part = (
                f"файлов в базе={len(shared_kb_ids)} top_k={top_k} "
                f"фрагментов={sum(shared_files.values())} "
                f"из {len(shared_files)} файлов {shared_files}"
            )
        elif child_profile.get("_parent_shared_rag_refused"):
            shared_part = (
                "нет (в карточке выключено «Смотреть общий RAG родителя»; "
                f"у родителя {child_profile['_parent_shared_rag_refused']} док.)"
            )
        else:
            shared_part = "нет (у главного не включён общий RAG)"
        if child_profile.get("file_search_enabled") and own_ids:
            own_part = (
                f"файлов={len(own_ids)} фрагментов={sum(own_files.values())} "
                f"из {len(own_files)} файлов {own_files}"
            )
        else:
            own_part = "нет (поиск по файлам выключен или файлов нет)"
        log.debug(
            "[SUBAGENT-TRACE] agent_id=%s «%s» родитель=%s глубина=%s модель=%s"
            " | карточка: %s симв, в системном промпте=%s"
            " | вопрос пользователя передан=%s"
            " | задача=«%s»"
            " | общий RAG: %s"
            " | своя база: %s"
            " | ограничение карточкой: %s"
            " | дотянуто по имени: %s"
            " | перечень файлов=%s"
            " | итог: системный=%s симв, сообщение=%s симв",
            target_agent_id,
            child_profile.get("name") or f"agent_{target_agent_id}",
            parent_profile.get("agent_id") if isinstance(parent_profile, dict) else None,
            depth,
            child_profile.get("model_path"),
            len(card),
            "да" if card_in else ("НЕТ" if card else "карточка пустая"),
            "да" if question_added else "нет",
            (task or "")[:300].replace("\n", " "),
            shared_part,
            own_part,
            _scope_log_part(child_profile),
            child_profile.get("_named_files") or "нет",
            "да" if "ДОСТУПНЫЕ ФАЙЛЫ" in f"{system_prompt}\n{prompt}" else "нет",
            len(system_prompt or ""),
            len(prompt or ""),
        )
    except Exception:
        log.exception("[SUBAGENT-TRACE] agent_id=%s: не удалось собрать", target_agent_id)
