"""LLM-as-a-Judge: backend судит релевантность RAG-чанков ТЕКУЩЕЙ UI-моделью.
Единый вход: judge_and_filter_hits(query, hits). Включается env RAG_LLM_JUDGE_ENABLED
(по умолчанию выкл). Использует backend.agent_llm_svc.ask_agent БЕЗ model_path - та же
модель, что выбрана в UI (как _llm_short для multi-query/HyDE). Логика судьи — в backend, без round-trip в svc-rag.
"""

from __future__ import annotations
import asyncio
import concurrent.futures
import json
import os
import re
from typing import List, Optional, Tuple

from backend.settings.logging import get_logger

logger = get_logger(__name__)

_MAX_PASSAGE_CHARS = 480

Hit = Tuple[str, float, Optional[int], Optional[int]]


def llm_judge_enabled() -> bool:
    v = (os.getenv("RAG_LLM_JUDGE_ENABLED", "") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    t = text.strip()
    m = re.search(r"\{[\s\S]*\}\s*$", t)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


async def _judge_llm(prompt: str) -> str:
    """Один вызов выбранной backend-модели (sync ask_agent в отдельном потоке)."""
    from backend.agent_llm_svc import ask_agent

    loop = asyncio.get_running_loop()
    # Модель этого чата: карточка агента, иначе выбранная в чате. Читаем
    # ЗДЕСЬ, а не внутри _call: в поток executor contextvars не копируются,
    # там значение было бы пустым.
    from backend.settings.cef_logger.cef_audit_context import chat_model_peek

    _model_path = chat_model_peek()

    def _call() -> str:
        return (
            ask_agent(
                prompt,
                history=[],
                streaming=False,
                model_path=_model_path,
                max_tokens=512,
                temperature=0.0,
                # Судья должен вернуть JSON, а не рассуждения.
                service_call=True,
            )
            or ""
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return await loop.run_in_executor(ex, _call)


async def judge_chunk_relevance(query: str, passages: List[str]) -> List[bool]:
    """Бинарная релевантность каждого фрагмента к запросу. При сбое - все True (не режем)."""
    if not passages:
        return []
    lines: List[str] = []
    for i, p in enumerate(passages):
        snippet = (p or "").replace("\n", " ").strip()[:_MAX_PASSAGE_CHARS]
        lines.append(f"[{i}] {snippet}")
    user_prompt = (
        "Ты судья релевантности для RAG. По запросу пользователя отметь, помогает ли каждый "
        "фрагмент ответить на запрос (содержит по смыслу нужную информацию, не обязательно "
        "полный ответ).\n\n"
        f"Запрос:\n{query.strip()}\n\nФрагменты:\n"
        + "\n".join(lines)
        + '\n\nВерни ТОЛЬКО JSON вида {"relevant": [true/false, ...]} — массив ровно из '
        f"{len(passages)} булевых значений в порядке индексов 0..{len(passages) - 1}."
    )
    try:
        content = await _judge_llm(user_prompt)
        parsed = _extract_json_object(content)
        if (
            not parsed
            or "relevant" not in parsed
            or not isinstance(parsed["relevant"], list)
        ):
            logger.warning(
                "[judge] не разобрал JSON (chars=%s) — не режем", len(content or "")
            )
            return [True] * len(passages)
        rel = parsed["relevant"]
        out = [bool(rel[i]) if i < len(rel) else True for i in range(len(passages))]
        logger.debug("[judge] релевантных %s из %s", sum(out), len(passages))
        return out
    except Exception as e:
        logger.warning("[judge] LLM не удался: %s — не режем", e)
        return [True] * len(passages)


async def judge_and_filter_hits(query: str, hits: List[Hit]) -> List[Hit]:
    """Если judge включён — отфильтровать нерелевантные хиты. Иначе вернуть как есть."""
    if not hits or not llm_judge_enabled():
        return hits
    passages = [h[0] for h in hits]
    flags = await judge_chunk_relevance(query, passages)
    filtered = [h for h, keep in zip(hits, flags) if keep]
    # Если judge зарезал всё — возвращаем исходные (лучше показать, чем пусто).
    if not filtered:
        logger.info("[judge] все хиты отсеяны — оставляем исходные %s", len(hits))
        return hits
    return filtered
