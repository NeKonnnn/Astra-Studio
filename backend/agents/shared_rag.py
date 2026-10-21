
"""Общий RAG цепочки и субагентов: база знаний головного агента - остальным.

Переключатель «Общий RAG для цепочки и субагентов» в карточке головного
агента (config.shared_chain_rag): его базы знаний (entity knowledge_base_ids
и legacy kb_document_ids) доступны агентам цепочки (chain/runner.py) и всей
ветке его субагентов (subagents/runner.py). Кому и чью базу давать -
решается здесь, для обоих.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Ключ, под которым общий RAG цепочки протаскивается вниз по субагентам
# независимо от того, чей профиль сейчас является «родительским».
SHARED_RAG_KEY = "_shared_chain_rag_ids"


def head_shared_kb_ids(head_profile: Dict[str, Any]) -> List[Any]:
    """Legacy document_id головного агента для shared RAG.

    Пусто, если общий RAG выключен или legacy file_search выключен.
    Entity knowledge bases разворачиваются асинхронно в chain/runner.
    """
    if not isinstance(head_profile, dict):
        return []
    if not head_profile.get("shared_chain_rag"):
        return []
    if not head_profile.get("file_search_enabled"):
        return []
    raw_ids = head_profile.get("kb_document_ids") or []
    if not isinstance(raw_ids, list):
        return []
    return list(raw_ids)


def head_shared_knowledge_base_ids(head_profile: Dict[str, Any]) -> List[int]:
    """Entity knowledge_base_ids головного для shared RAG."""
    if not isinstance(head_profile, dict):
        return []
    if not head_profile.get("shared_chain_rag"):
        return []
    if not head_profile.get("knowledge_bases_enabled"):
        return []
    out: List[int] = []
    for raw in head_profile.get("knowledge_base_ids") or []:
        try:
            kid = int(raw)
        except (TypeError, ValueError):
            continue
        if kid > 0 and kid not in out:
            out.append(kid)
    return out


def head_shares_chain_rag(head_profile: Dict[str, Any]) -> bool:
    """Включён ли общий RAG цепочки у головного агента."""
    return isinstance(head_profile, dict) and bool(head_profile.get("shared_chain_rag"))


def accepts_parent_shared_rag(profile: Dict[str, Any]) -> bool:
    """Берёт ли агент общий RAG родителя - переключатель в его карточке.

    «Смотреть общий RAG родителя» (config.use_parent_shared_rag), по
    умолчанию включён: у карточек без этого поля всё как раньше. Действует,
    когда агента вызывают субагентом или он шаг цепочки, и только если
    родитель общим RAG делится. Своя база агента от него не зависит.
    """
    if not isinstance(profile, dict):
        return True
    return profile.get("use_parent_shared_rag") is not False


def shared_kb_ids_for_child(parent_profile: Dict[str, Any]) -> List[int]:
    """Список document_id общего RAG, если режим включён у головного агента.

    Значение сначала ищется в уже протянутом вниз ключе ``SHARED_RAG_KEY``
    (чтобы наследоваться на всю глубину субагентов), затем — в самом профиле
    родителя (первый уровень субагентов от головного агента цепочки)."""
    if not isinstance(parent_profile, dict):
        return []
    inherited = parent_profile.get(SHARED_RAG_KEY)
    if isinstance(inherited, list):
        return [
            int(v)
            for v in inherited
            if isinstance(v, (int, str)) and str(v).strip().lstrip("-").isdigit()
        ]
    out: List[int] = []
    for v in head_shared_kb_ids(parent_profile):
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out
