"""Реранк векторных/лексических попаданий cross-encoder'ом и порог по скору.

Порядок выдачи определяет реранкер; prior используется только как микро-тайбрейк.
Признак успеха возвращается явно, чтобы вызывающий код не считал реранк
применённым, когда сервис моделей недоступен или вернул пустой ответ (BUG-RAG-04).
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

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
            lines.append(
                f"  {i + 1} -> выброшен реранкером  {_chunk_label(hit[0])}  {_preview(hit[0])}"
            )
    logger.debug("\n".join(lines))


async def rerank_vector_hits(
    query: str,
    hits: List[Tuple[Any, float]],
    rag_client: Any,
    top_k: int,
    vector_weight: float = 0.3,
    model: Optional[str] = None,
) -> List[Tuple[Any, float]]:
    """
    hits: список (DocumentVector, prior_score) — prior может быть cosine или RRF.

    Best practice: после fusion порядок задаёт cross-encoder. Prior-score (RRF ~0.01)
    нельзя линейно смешивать с логитом реранкера — шкалы несопоставимы.
    Поэтому:
      1) берём порядок от reranker;
      2) prior используется только как микро-тайбрейк (1e-6 * rank_norm).
    ``vector_weight`` оставлен для совместимости вызовов, на порядок не влияет.
    """
    ranked_hits, _used = await rerank_vector_hits_ex(
        query,
        hits,
        rag_client,
        top_k=top_k,
        vector_weight=vector_weight,
        model=model,
    )
    return ranked_hits


async def rerank_vector_hits_ex(
    query: str,
    hits: List[Tuple[Any, float]],
    rag_client: Any,
    top_k: int = 0,
    vector_weight: float = 0.3,
    model: Optional[str] = None,
) -> Tuple[List[Tuple[Any, float]], bool]:
    """Как ``rerank_vector_hits``, плюс флаг: удалось ли реально вызвать реранкер.

    При сбое/пустом ответе возвращаем исходные hits (обрезанные) и used=False.
    """
    _ = vector_weight  # совместимость сигнатуры
    rows = list(hits or [])
    if not rows:
        return [], False
    fallback = _trim(rows, top_k)
    if rag_client is None:
        return fallback, False
    k = int(top_k or 0)
    # Раньше pool=2*k: на CPU тяжёлый реранкер (bge-reranker MiniCPM) на
    # 40–50 текстах блокировал поиск на минуты → ReadTimeout в чате.
    # Жёсткий потолок — RAG_RERANK_INPUT_CAP (дефолт 16).
    try:
        import os

        _cap = int(os.getenv("RAG_RERANK_INPUT_CAP", "16"))
    except ValueError:
        _cap = 16
    _cap = max(4, min(_cap, 64))
    pool = min(len(rows), k if k > 0 else len(rows), _cap)
    contents = [str(getattr(h[0], "content", "") or "") for h in rows[:pool]]
    logger.debug(
        "[rerank] вход: кандидатов=%s, top_k=%s (rank-primary, prior только tiebreak)",
        len(contents),
        top_k,
    )
    try:
        ranked = await rag_client.rerank(query, contents, top_k=pool, model=model)
    except Exception as e:
        logger.warning("rerank_helpers.rerank failed: %s", e)
        return fallback, False
    if not ranked:
        return fallback, False

    prior_rank = {
        i: r
        for r, (i, _) in enumerate(
            sorted(enumerate(rows[:pool]), key=lambda x: float(x[1][1]), reverse=True)
        )
    }
    n = max(pool, 1)
    out: List[Tuple[Any, float]] = []
    used: List[int] = []
    for rr_rank, (idx, rr_sc) in enumerate(ranked):
        if not isinstance(idx, int) or not (0 <= idx < pool):
            continue
        pr = prior_rank.get(idx, n - 1)
        prior_norm = 1.0 - (pr / n)
        # Порядок — от реранкера; prior только микро-тайбрейк в значении скора.
        combined = float(rr_sc) + 1e-6 * prior_norm - 1e-9 * rr_rank
        out.append((rows[idx][0], combined))
        used.append(idx)
    if not out:
        return fallback, False
    _log_rerank_table(query, rows[:pool], out, used)
    logger.debug("[rerank] выход: отранжировано=%s (из %s)", len(out), len(rows))
    return _trim(out, k), True


def filter_by_min_score(
    rows: List[Tuple[Any, ...]],
    min_score: float,
    score_index: int = 1,
) -> List[Tuple[Any, ...]]:
    if not min_score or float(min_score) <= 0.0:
        return list(rows)
    out = []
    for row in rows:
        try:
            if float(row[score_index]) >= float(min_score):
                out.append(row)
        except (TypeError, ValueError, IndexError):
            continue
    return out
