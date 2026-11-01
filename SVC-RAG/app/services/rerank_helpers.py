"""Реранк векторных/лексических попаданий cross-encoder'ом и порог по скору.

Порядок выдачи определяет реранкер; признак успеха возвращается явно, чтобы
вызывающий код не считал реранк применённым, когда сервис моделей недоступен
или вернул пустой ответ (см. BUG-RAG-04).
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_PREVIEW_LIMIT = 80


def _chunk_label(dv: Any) -> str:
    return f"doc{getattr(dv, 'document_id', None)}:чанк{getattr(dv, 'chunk_index', None)}"


def _preview(dv: Any, limit: int = _PREVIEW_LIMIT) -> str:
    text = " ".join(str(getattr(dv, "content", None) or "").split())
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _trim(hits: Sequence[Any], top_k: int) -> List[Any]:
    rows = list(hits)
    return rows[:top_k] if top_k and top_k > 0 else rows


def _log_rerank_table(query: str, hits: Sequence[Any], out: Sequence[Any], used: Sequence[int]) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return
    q = " ".join(str(query or "").split())
    lines = [f"[RERANK] запрос={q[:_PREVIEW_LIMIT]!r}: позиция до -> после"]
    for new_pos, src in enumerate(used):
        dv = hits[src][0]
        lines.append(
            f"  {src + 1} -> {new_pos + 1}  {_chunk_label(dv)}  "
            f"score={float(out[new_pos][1]):.4f}  {_preview(dv)}"
        )
    used_set = set(used)
    for i, hit in enumerate(hits):
        if i not in used_set:
            lines.append(f"  {i + 1} -> выброшен реранкером  {_chunk_label(hit[0])}  {_preview(hit[0])}")
    logger.debug("\n".join(lines))


async def rerank_vector_hits_ex(
    query: str,
    hits: Sequence[Tuple[Any, float]],
    client: Any,
    top_k: int = 0,
    model: Optional[str] = None,
    vector_weight: float = 0.0,
) -> Tuple[List[Tuple[Any, float]], bool]:
    """Реранк пар ``(DocumentVector, score)``. Возвращает ``(строки, реранк_применён)``.

    ``vector_weight`` оставлен для совместимости вызовов (порядок целиком от реранкера).

    При любой неудаче реранкера (исключение, пустой ответ) порядок исходный,
    а признак успеха — ``False``.
    """
    rows = list(hits or [])
    if not rows:
        return [], False
    fallback = _trim(rows, top_k)
    if client is None:
        return fallback, False
    k = int(top_k or 0)
    pool = min(len(rows), max(k * 2, k)) if k > 0 else len(rows)
    contents = [str(getattr(h[0], "content", "") or "") for h in rows[:pool]]
    try:
        ranked = await client.rerank(query, contents, top_k=pool, model=model)
    except Exception as e:
        logger.warning("rerank failed, keeping vector order: %s", e)
        return fallback, False
    if not ranked:
        return fallback, False

    scored: List[Tuple[int, float]] = []
    for idx, score in ranked:
        if isinstance(idx, int) and 0 <= idx < pool:
            scored.append((idx, float(score)))
    if not scored:
        return fallback, False
    scored.sort(key=lambda p: -p[1])  # стабильно: равные скоры сохраняют порядок реранкера
    out = [(rows[i][0], s) for i, s in scored]
    _log_rerank_table(query, rows[:pool], out, [i for i, _ in scored])
    return _trim(out, k), True


async def rerank_vector_hits(
    query: str,
    hits: Sequence[Tuple[Any, float]],
    client: Any,
    top_k: int = 0,
    vector_weight: float = 0.0,
    model: Optional[str] = None,
) -> List[Tuple[Any, float]]:
    """Совместимая обёртка: только порядок (``vector_weight`` игнорируется)."""
    out, _ = await rerank_vector_hits_ex(query, hits, client, top_k=top_k, model=model)
    return out


def filter_by_min_score(rows: Sequence[Any], min_score: float, score_index: int = 1) -> List[Any]:
    """Оставляет строки со скором ``>= min_score``; строки без числового скора отбрасываются."""
    if not min_score or float(min_score) <= 0:
        return list(rows)
    kept: List[Any] = []
    for row in rows:
        try:
            if float(row[score_index]) >= float(min_score):
                kept.append(row)
        except (TypeError, ValueError, IndexError):
            continue
    return kept
