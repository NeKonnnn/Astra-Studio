"""LLM-as-a-Judge: бинарная релевантность каждого чанка к запросу."""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_MAX_PASSAGE_CHARS = 480

_TRUE_STRINGS = frozenset({"true", "yes", "y", "1", "да"})


def _coerce_flag(value: object) -> bool:
    """Строгое приведение к bool: ``bool("false")`` был бы True и помечал всё релевантным."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return False


def _extract_json_object(text: str) -> Optional[Dict]:
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
        pass
    # Закрывающий ``` или фраза после JSON: берём первый корректный объект с первой «{».
    start = t.find("{")
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(t[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


async def judge_chunk_relevance(
    query: str, passages: List[str]
) -> tuple[List[bool], Optional[str]]:
    """
    Возвращает список флагов релевантности длины len(passages) и опциональное сообщение об ошибке.
    При сбое парсинга — все False.
    """
    if not passages:
        return [], None
    cfg = get_settings()
    if not getattr(cfg.rag, "eval_llm_judge_allowed", False):
        return [False] * len(
            passages
        ), "LLM-judge отключён (RAG_EVAL_LLM_JUDGE_ALLOWED=false)"

    lines: List[str] = []
    for i, p in enumerate(passages):
        snippet = (p or "").replace("\n", " ").strip()[:_MAX_PASSAGE_CHARS]
        lines.append(f"[{i}] {snippet}")
    user_prompt = (
        "Ты судья релевантности для RAG. По запросу пользователя отметь, помогает ли каждый фрагмент "
        "ответить на запрос (содержит по смыслу нужную информацию, не обязательно полный ответ).\n\n"
        f"Запрос:\n{query.strip()}\n\nФрагменты:\n"
        + "\n".join(lines)
        + '\n\nВерни ТОЛЬКО JSON вида {"relevant": [true/false, ...]} — массив ровно из '
        f"{len(passages)} булевых значений в порядке индексов 0..{len(passages) - 1}."
    )

    logger.debug(
        "[judge] запуск: passages=%s query=%s",
        len(passages),
        (query or "")[:120],
    )
    try:
        from app.services import llm_chat

        content = await llm_chat.chat(
            user_prompt,
            purpose="judge",
            temperature=0.0,
            max_tokens=512,
        )
        parsed = _extract_json_object(content)
        if not parsed or "relevant" not in parsed:
            logger.warning(
                "[SVC-RAG] LLM-judge: не удалось разобрать JSON из ответа модели"
            )
            return [False] * len(passages), "parse_error"
        rel = parsed["relevant"]
        if not isinstance(rel, list):
            return [False] * len(passages), "relevant_not_list"
        out: List[bool] = []
        for i in range(len(passages)):
            if i < len(rel):
                out.append(_coerce_flag(rel[i]))
            else:
                out.append(False)
        return out, None
    except Exception as e:
        logger.warning("[SVC-RAG] LLM-judge: запрос к LLM не удался: %s", e)
        return [False] * len(passages), str(e)
