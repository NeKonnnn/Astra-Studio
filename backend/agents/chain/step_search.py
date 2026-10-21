"""Свой поиск по документам на каждом шаге цепочки.

Все агенты цепочки получали одни и те же фрагменты: 
В отличие от субагентов, в цепочке задание никто не пишет - его пишет сам шаг: 
короткий служебный вызов его модели «по своей инструкции и запросу напиши, что искать». 
По этому запросу идёт поиск; что шаг получает в сообщении - как было.

Служебный вызов - без размышлений, ответ до 256 токенов. Сбой или пустой
ответ - поиск по вопросу пользователя, как раньше.
AGENT_CHAIN_STEP_QUERY=0 (ConfigMap) - выключить.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, Optional

from backend.agents.chain.settings import chain_step_query_enabled
from backend.settings.logging import get_logger

log = get_logger(__name__)

STEP_QUERY_REQUEST = """Сейчас не выполняй запрос. Сначала нужно найти в базе документов материалы для твоей части работы.

Запрос пользователя: «{question}»

По своей инструкции (системный промпт выше) и этому запросу напиши поисковый запрос к документам: какие сведения, показатели, разделы документов нужны именно тебе. Включи названия компаний, людей и документов из запроса пользователя.
Верни ТОЛЬКО поисковый запрос - одну строку до 300 символов, без пояснений."""

STEP_QUERY_MAX_CHARS = 500
# Вопрос пользователя в служебном запросе - не простыня вставленного текста.
_QUESTION_MAX_CHARS = 4000
_THINK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.IGNORECASE | re.DOTALL)
_LABEL_RE = re.compile(r"^(?:поисковый\s+)?запрос\s*:\s*", re.IGNORECASE)

def clean_step_query(text: Any) -> str:
    """Ответ модели → одна строка запроса: без размышлений, подписи и кавычек."""
    raw = _THINK_RE.sub(" ", str(text or ""))
    query = " ".join(line.strip() for line in raw.splitlines() if line.strip())
    query = _LABEL_RE.sub("", query).strip(" \"'«»`*")
    return query[:STEP_QUERY_MAX_CHARS].strip()

async def chain_step_search_query(
    profile: Optional[Dict[str, Any]],
    question: str,
    *,
    step_label: str,
) -> str:
    """Поисковый запрос шага по его инструкции. Не вышло - вопрос пользователя."""
    system_prompt = str((profile or {}).get("system_prompt") or "").strip()
    if not chain_step_query_enabled() or not system_prompt:
        return question
    q = (question or "").strip()
    if len(q) > _QUESTION_MAX_CHARS:
        q = q[:_QUESTION_MAX_CHARS] + " …"
    try:
        from backend.app_state import get_current_model_path
        from backend.llm_providers import get_registry
        from backend.llm_providers.routing import (
            merge_sampling_request_extra,
            thinking_request_extra,
        )

        registry = await get_registry()
        provider, model_id = registry.resolve(
            (profile or {}).get("model_path") or get_current_model_path()
        )
        if not model_id:
            models = await provider.list_models()
            model_id = models[0].model_id if models else ""
        text = await provider.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": STEP_QUERY_REQUEST.format(question=q)},
            ],
            model_id,
            temperature=0.1,
            max_tokens=256,
            request_extra=merge_sampling_request_extra(thinking_request_extra(False)),
        )
    except Exception:
        log.exception(
            "[agent-chain] %s: поисковый запрос не составлен - поиск по вопросу "
            "пользователя",
            step_label,
        )
        return question
    query = clean_step_query(text)
    if not query:
        log.warning(
            "[agent-chain] %s: пустой поисковый запрос (ответ модели: %r) - поиск "
            "по вопросу пользователя",
            step_label,
            str(text or "")[:500],
        )
        return question
    log.debug("[agent-chain] %s: поиск по документам - %r", step_label, query)
    return query

def step_sources_text(document_search: Any) -> str:
    """Файлы, из которых шаг получил фрагменты, и сколько из каждого."""
    hits = (
        document_search.get("hits") if isinstance(document_search, dict) else None
    ) or []
    counts = Counter(str(h.get("file") or "?") for h in hits if isinstance(h, dict))
    if not counts:
        return "не найдено"
    return ", ".join(f"{name}: {n}" for name, n in counts.most_common())

def log_step_sources(step_label: str, document_search: Any) -> None:
    """Лог: какие файлы получил шаг (в чате при «скрывать промежуточные» не видно)."""
    log.debug(
        "[agent-chain] %s: документы - %s",
        step_label,
        step_sources_text(document_search),
    )