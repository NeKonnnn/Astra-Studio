"""Online RAG-метрики на каждый запрос чата.

Считает по факту одного ответа (без разметки / gold):

  * ``reciprocal_rank`` (RR, он же per-query MRR) — 1/позиция первого чанка,
    признанного релевантным LLM-судьёй; агрегат MRR по многим запросам
    собирается в offline-пайплайне;
  * ``context_precision`` — доля релевантных чанков среди попавших в промпт;
  * ``faithfulness`` — доля утверждений ответа, подтверждённых контекстом
    (LLM-as-judge, RAGAS-подобно).

Все вычисления fail-safe: при любой ошибке метрика = ``None`` и НИКОГДА не
ломают основной ответ пользователю. Судья использует текущую UI-модель через
``backend.agent_llm_svc.ask_agent`` (как остальной RAG-препроцесс).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.settings.logging import get_logger

logger = get_logger(__name__)

# Целевые пороги (из спецификации метрик RAG).
MRR_TARGET = float(os.getenv("RAG_METRIC_MRR_TARGET", "0.75"))
MRR_WARN = float(os.getenv("RAG_METRIC_MRR_WARN", "0.60"))
FAITHFULNESS_TARGET = float(os.getenv("RAG_METRIC_FAITHFULNESS_TARGET", "0.85"))
FAITHFULNESS_WARN = float(os.getenv("RAG_METRIC_FAITHFULNESS_WARN", "0.70"))
CONTEXT_PRECISION_TARGET = float(os.getenv("RAG_METRIC_CP_TARGET", "0.70"))
CONTEXT_PRECISION_WARN = float(os.getenv("RAG_METRIC_CP_WARN", "0.50"))

_MAX_PASSAGE_CHARS = 480
_MAX_CTX_CHARS = 12000
_MAX_ANSWER_CHARS = 8000


def online_metrics_enabled() -> bool:
    """По умолчанию включено. Отключается RAG_ONLINE_METRICS=0."""
    v = os.getenv("RAG_ONLINE_METRICS", "").strip().lower()
    if not v:
        return True
    return v not in ("0", "false", "no", "off")


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


async def _ask_judge(prompt: str, *, system: str, max_tokens: int = 512) -> str:
    """Один вызов текущей UI-модели в отдельном потоке (ask_agent — sync)."""
    import contextvars

    from backend.agent_llm_svc import ask_agent

    loop = asyncio.get_running_loop()
    # Модель этого чата, см. llm_judge.py: читаем до потока.
    from backend.settings.cef_logger.cef_audit_context import chat_model_peek

    _model_path = chat_model_peek()
    # ThreadPoolExecutor не копирует ContextVar — переносим явно.
    _ctx = contextvars.copy_context()

    def _call() -> str:
        return (
            ask_agent(
                prompt,
                history=[],
                streaming=False,
                system_prompt=system,
                model_path=_model_path,
                max_tokens=max_tokens,
                temperature=0.0,
            )
            or ""
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return await loop.run_in_executor(ex, lambda: _ctx.run(_call))


def _threshold_status(
    value: Optional[float], target: float, warn: float
) -> Optional[str]:
    if value is None:
        return None
    if value >= target:
        return "OK"
    if value >= warn:
        return "WARNING"
    return "CRITICAL"


def reciprocal_rank(relevance_flags: Sequence[bool]) -> float:
    """RR = 1/rank первого релевантного чанка (0, если релевантных нет)."""
    for i, ok in enumerate(relevance_flags):
        if ok:
            return 1.0 / (i + 1)
    return 0.0


def context_precision(relevance_flags: Sequence[bool]) -> Optional[float]:
    """Доля релевантных чанков среди попавших в промпт."""
    n = len(relevance_flags)
    if n == 0:
        return None
    return sum(1 for f in relevance_flags if f) / n


async def judge_chunk_relevance(
    query: str, passages: List[str]
) -> Tuple[Optional[List[bool]], Optional[str]]:
    """Бинарная релевантность каждого чанка. Возвращает (flags|None, error|None).

    В отличие от фильтрующего судьи, для метрик мы НЕ подменяем провал на
    all-True (иначе precision раздувается). При ошибке возвращаем (None, err).
    """
    if not passages:
        return [], None
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
        content = await _ask_judge(
            user_prompt, system="Отвечай только компактным JSON."
        )
        parsed = _extract_json_object(content)
        if (
            not parsed
            or "relevant" not in parsed
            or not isinstance(parsed["relevant"], list)
        ):
            return None, "parse_error"
        rel = parsed["relevant"]
        return [
            bool(rel[i]) if i < len(rel) else False for i in range(len(passages))
        ], None
    except Exception as e:  # noqa: BLE001
        logger.warning("[metrics] judge_chunk_relevance failed: %s", e)
        return None, str(e)


async def faithfulness_score(
    context_text: str, answer: str
) -> Tuple[Optional[float], Dict[str, Any]]:
    """Доля утверждений ответа, подтверждённых контекстом (RAGAS-подобно).

    LLM разбивает ответ на атомарные утверждения и помечает каждое как
    supported/unsupported по предоставленному контексту. Возвращает
    (faithfulness|None, details).
    """
    ctx = (context_text or "").strip()[:_MAX_CTX_CHARS]
    ans = (answer or "").strip()[:_MAX_ANSWER_CHARS]
    if not ans or not ctx:
        return None, {"reason": "empty_context_or_answer"}
    user_prompt = (
        "Ты аудитор фактологичности RAG-ответа. Разбей ОТВЕТ на отдельные атомарные "
        "утверждения (факты). Для каждого утверждения определи, подтверждается ли оно "
        "напрямую предоставленным КОНТЕКСТОМ (перефраз допустим; общие вводные фразы, "
        "не несущие фактов, игнорируй).\n\n"
        f"КОНТЕКСТ:\n{ctx}\n\n"
        f"ОТВЕТ:\n{ans}\n\n"
        'Верни ТОЛЬКО JSON вида {"claims": [{"text": "...", "supported": true/false}, ...]}. '
        "Если фактических утверждений нет — верни пустой список claims."
    )
    try:
        content = await _ask_judge(
            user_prompt,
            system="Ты строгий аудитор фактов. Отвечай только компактным JSON.",
            max_tokens=800,
        )
        parsed = _extract_json_object(content)
        if not parsed or not isinstance(parsed.get("claims"), list):
            return None, {"reason": "parse_error"}
        claims = parsed["claims"]
        total = len(claims)
        if total == 0:
            # Нет фактических утверждений (напр. «Не знаю») — не штрафуем.
            return None, {"reason": "no_factual_claims", "total": 0}
        supported = sum(
            1 for c in claims if isinstance(c, dict) and bool(c.get("supported"))
        )
        score = supported / total
        return score, {"supported": supported, "total": total}
    except Exception as e:  # noqa: BLE001
        logger.warning("[metrics] faithfulness_score failed: %s", e)
        return None, {"reason": str(e)}


def _passages_from_hits(hits: Sequence[Any]) -> List[str]:
    """Достаёт тексты чанков из hits (list[dict] трейса или list[tuple])."""
    out: List[str] = []
    for h in hits or []:
        if isinstance(h, dict):
            out.append(str(h.get("content") or ""))
        elif isinstance(h, (list, tuple)) and h:
            out.append(str(h[0] or ""))
    return out


async def compute_online_rag_metrics(
    *,
    query: str,
    hits: Sequence[Any],
    answer: str,
    context_text: str,
) -> Optional[Dict[str, Any]]:
    """Считает online-метрики для одного ответа. Никогда не бросает исключений."""
    try:
        if not online_metrics_enabled():
            return None
        passages = _passages_from_hits(hits)
        if not passages:
            return None

        flags, rel_err = await judge_chunk_relevance(query, passages)
        faith, faith_details = await faithfulness_score(context_text, answer)

        rr: Optional[float] = None
        cp: Optional[float] = None
        first_rel_rank: Optional[int] = None
        if flags is not None:
            rr = reciprocal_rank(flags)
            cp = context_precision(flags)
            for i, ok in enumerate(flags):
                if ok:
                    first_rel_rank = i + 1
                    break

        metrics: Dict[str, Any] = {
            "reciprocal_rank": None if rr is None else round(rr, 4),
            "context_precision": None if cp is None else round(cp, 4),
            "faithfulness": None if faith is None else round(faith, 4),
            "first_relevant_rank": first_rel_rank,
            "relevant_chunks": None if flags is None else sum(1 for f in flags if f),
            "retrieved_chunks": len(passages),
            "status": {
                "mrr": _threshold_status(rr, MRR_TARGET, MRR_WARN),
                "faithfulness": _threshold_status(
                    faith, FAITHFULNESS_TARGET, FAITHFULNESS_WARN
                ),
                "context_precision": _threshold_status(
                    cp, CONTEXT_PRECISION_TARGET, CONTEXT_PRECISION_WARN
                ),
            },
            "faithfulness_details": faith_details,
            "judge_error": rel_err,
        }

        crit = [k for k, v in metrics["status"].items() if v == "CRITICAL"]
        warn = [k for k, v in metrics["status"].items() if v == "WARNING"]
        if crit:
            logger.warning("[RAG-METRICS] CRITICAL по %s: %s", crit, metrics)
        elif warn:
            logger.info("[RAG-METRICS] WARNING по %s: %s", warn, metrics)
        else:
            logger.debug(
                "[RAG-METRICS] RR=%s CP=%s Faithfulness=%s (чанков=%s, релевантных=%s)",
                metrics["reciprocal_rank"],
                metrics["context_precision"],
                metrics["faithfulness"],
                metrics["retrieved_chunks"],
                metrics["relevant_chunks"],
            )
        return metrics
    except Exception as e:  # noqa: BLE001
        logger.warning("[metrics] compute_online_rag_metrics failed: %s", e)
        return None
