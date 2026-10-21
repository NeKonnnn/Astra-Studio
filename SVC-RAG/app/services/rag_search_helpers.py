"""Общая логика поиска для KB / memory_rag / project_rag (не global RagService).

Факторы, которые обычно снижают качество RAG (для ориентира при чтении метрик):
- Слишком большой или слишком мелкий chunk / плохое перекрытие → потеря контекста или шум.
- Только вектор без BM25 (или наоборот) при несовпадении формулировки запроса и документа.
- Узкий top-k из pgvector при многих документах → «вытеснение» релевантных файлов.
- Высокий RAG_RERANK_MIN_SCORE / RAG_MIN_VECTOR_SIMILARITY на backend → пустой контекст.
- Слабый парсинг PDF/сканов → пустые или короткие чанки.
- Реранкер выключен при auto → порядок только по эмбеддингу.
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple, TypeVar

from app.database.models import DocumentVector

logger = logging.getLogger(__name__)

TVec = TypeVar("TVec", bound=DocumentVector)

# Текущий reranker в SVC-RAG-MODELS (ms-marco-MiniLM-L-6-v2) тренирован на ЧИСТО английском
# MS MARCO датасете и на кириллических запросах выдаёт случайные/шумовые скоры.
# При формуле ``final = 0.7*rerank + 0.3*cosine`` этот шум АКТИВНО ПОДАВЛЯЕТ рабочий
# векторный сигнал — классический случай, когда "включённый реранк делает хуже".
#
# Пока модель не заменена на мультиязычную (bge-reranker-v2-m3 / jina-reranker-v2 / BAAI),
# авто-отключаем реранк, если запрос преимущественно кириллический.
# Можно принудительно включить через ``RAG_FORCE_RERANK_CYRILLIC=1``.
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _is_primarily_cyrillic(text: str) -> bool:
    if not text:
        return False
    cyr = len(_CYRILLIC_RE.findall(text))
    lat = len(_LATIN_RE.findall(text))
    if cyr == 0:
        return False
    # Более половины буквенных символов — кириллица.
    return cyr >= max(3, lat)


def reranker_is_english_only() -> bool:
    """
    По умолчанию ``RAG_RERANKER_ENGLISH_ONLY=1`` (реранкер считается англоязычным);
    для многоязычного реранкера задайте ``RAG_RERANKER_ENGLISH_ONLY=0``.
    """
    return os.environ.get("RAG_RERANKER_ENGLISH_ONLY", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
        "",
    )


def should_disable_rerank_for_query(query: str) -> bool:
    """True, если для данного запроса реранк лучше выключить.

    Критерий: англоязычный reranker + запрос преимущественно на кириллице. В этом
    случае cross-encoder не только бесполезен, но и ухудшает ранжирование.
    Override через ``RAG_FORCE_RERANK_CYRILLIC=1`` — для экспериментов.
    """
    if not reranker_is_english_only():
        return False
    if os.environ.get("RAG_FORCE_RERANK_CYRILLIC", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    return _is_primarily_cyrillic(query or "")


def effective_use_reranking(
    requested: Optional[bool],
    cfg_rerank_enabled: bool,
    strategy: str,
    *,
    query_text: Optional[str] = None,
) -> bool:
    """raw_cosine / flat — без реранка всегда (это режимы «сырой выдачи»).
    Остальные — по настройке пользователя и конфигу: реранк это постобработка
    порядка, и она осмысленна для любого способа набрать кандидатов. Раньше
    vector и lexical гасили его жёстко — галочка в интерфейсе стояла, эффекта
    не было, предупреждения тоже.

    hybrid    — вектор+BM25; реранк включается только если он разрешён в конфиге.
    reranking — только реранк по конфигу.
    auto      — по конфигу и флагу запроса.

    Дополнительно: для кириллических запросов с английским-only реранкером реранк
    принудительно выключается (см. ```should_disable_rerank_for_query```). Это в несколько
    раз повышает качество на русскоязычном корпусе без замены модели.
    """
    st = (strategy or "auto").lower()
    # Сырые режимы: постобработка в них отключена по определению — стратегия
    # так и называется, и её смысл именно в том, чтобы увидеть выдачу как есть.
    if st in ("raw_cosine", "flat"):
        return False
    base = False
    if st in ("hybrid", "reranking"):
        if not cfg_rerank_enabled:
            base = False
        else:
            base = True if requested is None else bool(requested)
    else:
        base = (requested if requested is not None else cfg_rerank_enabled) and cfg_rerank_enabled

    if base and query_text is not None and should_disable_rerank_for_query(query_text):
        import logging as _logging

        _logging.getLogger(__name__).info(
            "[RAG] Реранк отключён для этого запроса: текущий reranker — английский "
            "(ms-marco-MiniLM-L-6-v2), а запрос на кириллице. Задайте "
            "RAG_RERANKER_ENGLISH_ONLY=0 после замены модели, либо "
            "RAG_FORCE_RERANK_CYRILLIC=1 чтобы форсировать."
        )
        return False
    return base


def resolve_auto_pipeline_strategy(
    query: str,
    *,
    store: str,
    document_id: Optional[int],
    hierarchical_available: bool,
    graph_available: bool,
    hybrid_available: bool,
) -> str:
    """
    Для strategy=auto выбирает фактический режим: lexical | graph | hybrid | vector.

    - RAG_AUTO_MODE=heuristic (по умолчанию): эвристики по тексту запроса + доступности.
    - RAG_AUTO_MODE=priority: первый доступный из списка приоритетов.

    Примечания:
    - Иерархический — только глобальное хранилище и без ограничения одним document_id.
    - Lexical и hybrid доступны при наличии BM25-индекса.
    """
    raw_query = (query or "").strip()
    q = raw_query.lower()
    qlen = len(q)
    words = re.findall(r"\b\w+\b", q)
    mode = (os.environ.get("RAG_AUTO_MODE") or "heuristic").strip().lower()

    can_h = bool(hierarchical_available and document_id is None and store == "global")
    can_g = bool(graph_available)
    can_hybrid_bm25 = bool(hybrid_available)
    can_lexical = can_hybrid_bm25
    if document_id is not None:
        can_h = False

    if mode == "priority":
        order = ["hybrid", "graph", "lexical", "hierarchical", "vector"]
        avail = {
            "hybrid": can_hybrid_bm25,
            "graph": can_g,
            "lexical": can_lexical,
            "hierarchical": can_h,
            "vector": True,
        }
        for choice in order:
            if avail.get(choice):
                logger.debug(
                    "[RAG-AUTO] store=%s mode=priority selected=%s query=%r available=%s",
                    store,
                    choice,
                    raw_query[:160],
                    {name: enabled for name, enabled in avail.items()},
                )
                return choice
        logger.debug(
            "[RAG-AUTO] store=%s mode=priority selected=vector query=%r reason=no_available_strategy",
            store,
            raw_query[:160],
        )
        return "vector"

    # --- heuristic ---
    # Hybrid — безопасный режим по умолчанию, когда запрос не даёт явного сигнала.
    scores: Dict[str, float] = {"vector": 1.0}
    if can_hybrid_bm25:
        scores["hybrid"] = 2.0
    if can_lexical:
        scores["lexical"] = 0.8

        # Строго лексические запросы: цитаты, коды, артикулы, номера,
        # аббревиатуры и явная просьба искать точное совпадение.
        quoted_phrase = bool(re.search(r'["«][^"»]{2,}["»]', raw_query))
        mixed_code = bool(
            re.search(
                r"\b(?=[\w./-]*[A-Za-zА-Яа-я])(?=[\w./-]*\d)[\w./-]{3,}\b",
                raw_query,
            )
        )
        uppercase_code = bool(re.search(r"\b[А-ЯA-Z]{2,}(?:[-_/][А-ЯA-Z0-9]+)*\b", raw_query))
        lexical_intent = any(
            token in q
            for token in (
                "точное совпад",
                "дослов",
                "точную фразу",
                "артикул",
                "идентификатор",
                "код ",
                "номер ",
                "инн",
                "снилс",
            )
        )
        if quoted_phrase or mixed_code or uppercase_code or lexical_intent:
            scores["lexical"] += 4.0
        elif len(words) <= 3:
            scores["lexical"] += 0.4

    if can_g:
        scores["graph"] = 1.0
        graph_intent = any(
            tok in q
            for tok in (
                "связ",
                "почему",
                "как связ",
                "отличи",
                "сравни",
                "между",
                "влияни",
                "последств",
                "цепочк",
                "граф",
                "нескольк",
            )
        )
        filename_mention = bool(re.search(r"\b[^\s/\\]+\.(?:pdf|docx?|xlsx?|txt|md|csv)\b", q))
        if graph_intent:
            scores["graph"] += 4.0
        if filename_mention:
            # Filename anchor и чтение связанных чанков реализованы в full/graph path.
            scores["graph"] += 4.5

    # Vector выбираем для явно смысловых/перефразированных вопросов, когда точное
    # совпадение слов менее важно, чем семантическая близость.
    semantic_intent = any(
        token in q
        for token in (
            "объясни",
            "что означает",
            "в чем смысл",
            "по смыслу",
            "похож",
            "перефраз",
            "другими словами",
            "о чем говорится",
        )
    )
    if semantic_intent:
        scores["vector"] += 3.0
    elif qlen > 180:
        scores["vector"] += 0.8

    if can_h:
        scores["hierarchical"] = 1.0
        if any(
            tok in q
            for tok in (
                "кратк",
                "обзор",
                "summary",
                "целиком",
                "о чем документ",
                "содержан",
                "структур",
                "раздел",
            )
        ):
            scores["hierarchical"] += 2.5
        if qlen > 220:
            scores["hierarchical"] += 0.8

    # При равенстве оценок дорогой graph — после дешёвых hybrid/vector: «нейтральный»
    # запрос (graph=vector=1.0) не должен уходить в графовый путь только из-за порядка.
    tie = ("lexical", "hybrid", "vector", "graph", "hierarchical")
    # auto = hybrid, если гибрид доступен
    # Гибрид объединяет вектор и BM25 через RRF - покрывает оба случая сразу.
    # RAG_AUTO_PREFER_HYBRID=0 возвращает старую эвристику.
    _prefer_hybrid = (
        os.environ.get("RAG_AUTO_PREFER_HYBRID", "1") or ""
    ).strip().lower() not in ("0", "false", "no", "off")
    if _prefer_hybrid and can_hybrid_bm25:
        logger.debug(
            "[RAG-AUTO] store=%s prefer_hybrid: auto -> hybrid (эвристика дала бы %s) query=%r",
            store,
            min(
                scores.keys(),
                key=lambda s: (-scores[s], tie.index(s) if s in tie else 99),
            ),
            raw_query[:160],
        )
        return "hybrid"
    best = min(
        scores.keys(), key=lambda s: (-scores[s], tie.index(s) if s in tie else 99)
    )
    logger.debug(
        "[RAG-AUTO] store=%s mode=heuristic selected=%s query=%r scores=%s "
        "available={lexical:%s, hybrid:%s, graph:%s, vector:true}",
        store,
        best,
        raw_query[:160],
        {name: round(score, 3) for name, score in scores.items()},
        can_lexical,
        can_hybrid_bm25,
        can_g,
    )
    return best


def vector_fetch_limit(
    k: int,
    *,
    graph: bool,
    document_id: Optional[int],
    use_rerank: bool,
    rerank_top_k: int,
) -> int:
    """Сколько чанков забрать из pgvector до реранка / диверсификации."""
    if graph:
        return max(k * 4, 36)
    if document_id is not None:
        return max(56, k * 5)
    if use_rerank:
        return max(rerank_top_k, k * 10, 120)
    return max(120, k * 14, 100)


def filter_by_min_vector_similarity(
    hits: List[Tuple[DocumentVector, float]],
    min_similarity: float,
    k: int,
) -> List[Tuple[DocumentVector, float]]:
    """
    Отсекает чанки с косинусным сходством ниже порога до реранка.
    Если ничего не прошло порог — спасаем top-N по скору (минимум 3, максимум 12, ориентир k),
    чтобы не обнулять recall на «трудных» корпусах (PDF/OCR). Это сознательный recall-first fallback.
    """
    if min_similarity <= 0 or not hits:
        return hits
    filtered = [h for h in hits if h[1] >= min_similarity]
    if filtered:
        if len(filtered) < len(hits):
            logger.debug(
                "[RAG] min_vector_similarity=%.3f: %d/%d чанков выше порога (отброшено %d нерелевантных)",
                min_similarity,
                len(filtered),
                len(hits),
                len(hits) - len(filtered),
            )
        return filtered
    # Не оставляем retrieval пустым из-за слишком жёсткого порога:
    # спасаем ограниченный top-N, чтобы не ломать recall на "трудных" корпусах (PDF/OCR).
    rescue_n = max(3, min(max(k, 1), 12))
    rescued = sorted(hits, key=lambda x: float(x[1]), reverse=True)[:rescue_n]
    logger.debug(
        "[RAG] min_vector_similarity=%.3f: все %d чанков ниже порога → rescue top-%d (recall-first)",
        min_similarity,
        len(hits),
        rescue_n,
    )
    return rescued


def should_diversify_hits(
    hits: List[Tuple[DocumentVector, float]],
    *,
    score_of: Optional[Any] = None,
    min_unique_docs: int = 3,
    min_top_score_for_diversify: float = 0.24,
    max_top_gap_for_diversify: float = 0.12,
) -> bool:
    """Решает, нужна ли диверсификация по документам.

    Идея: если top-1 явно лучше top-2, диверсификация часто вносит шум —
    у одного документа есть что сказать, и резать его до одного чанка нельзя.

    Пороги (0.24 / 0.12) заданы в шкале COSINE. К моменту вызова ```hits[i][1]```
    может быть уже не cosine, а weighted RRF (~0.016) — тогда ```s1 < 0.24```
    выполняется всегда, ответ всегда True, и «top-1 явно лучше» не срабатывает
    никогда. ```score_of``` позволяет передать настоящий cosine чанка; вернул
    None — считаем шкалу неизвестной и диверсифицируем, как раньше.
    """
    if len(hits) < 3:
        return False
    uniq_docs = len({h[0].document_id for h in hits if h[0].document_id is not None})
    if uniq_docs < min_unique_docs:
        return False
    if score_of is None:
        s1: Optional[float] = float(hits[0][1])
        s2: Optional[float] = float(hits[1][1])
    else:
        s1 = score_of(hits[0])
        s2 = score_of(hits[1])
        if s1 is None or s2 is None:
            return True
    if s1 < min_top_score_for_diversify:
        return True
    return (s1 - s2) <= max_top_gap_for_diversify


_KEYWORD_STOPWORDS = {
    "и",
    "в",
    "на",
    "по",
    "из",
    "с",
    "к",
    "у",
    "о",
    "а",
    "но",
    "что",
    "как",
    "это",
    "не",
    "он",
    "она",
    "они",
    "мы",
    "вы",
    "я",
    "его",
    "её",
    "их",
    "был",
    "для",
    "или",
    "при",
    "за",
    "до",
    "без",
    "то",
    "бы",
    "ли",
    "уже",
    "ещё",
    "the",
    "is",
    "in",
    "of",
    "to",
    "and",
    "for",
    "are",
    "was",
    "with",
}


def keyword_boost_hits(
    hits: List[Tuple[DocumentVector, float]],
    query: str,
    boost_factor: float = 0.30,
    min_content_len: int = 120,
) -> List[Tuple[DocumentVector, float]]:
    """Повышает скор чанков с точными вхождениями слов запроса.

    Критично для имён собственных, аббревиатур и цифр — сигналов, которые
    embedding-модель нивелирует в пользу тематических слов.
    boost_factor: макс. надбавка к скору при 100% совпадении слов (0.30 = +30%).
    """
    if not hits or not query.strip():
        return hits
    query_words = {w.lower() for w in re.findall(r"\b\w{3,}\b", query) if w.lower() not in _KEYWORD_STOPWORDS}
    if not query_words:
        return hits
    boosted = []
    for dv, score in hits:
        content_lower = (dv.content or "").lower()
        if len(content_lower.strip()) < max(20, min_content_len):
            # Заголовки/оглавления не должны выигрывать за счёт точного слова.
            boosted.append((dv, float(score)))
            continue
        matched = sum(1 for w in query_words if w in content_lower)
        ratio = matched / len(query_words)
        new_score = float(score) * (1.0 + boost_factor * ratio)
        boosted.append((dv, new_score))
    return sorted(boosted, key=lambda x: x[1], reverse=True)


def filter_low_signal_chunks(
    hits: List[Tuple[DocumentVector, float]],
    *,
    min_len: int = 40,
    rescue_keep: int = 12,
) -> List[Tuple[DocumentVector, float]]:
    """
    Отбрасывает слишком короткие чанки (оглавления/заголовки), которые часто
    искажают top-k. Если после фильтра мало кандидатов — возвращает часть исходных.

    min_len=0 полностью отключает фильтр (ничего не режет).
    По умолчанию 40, чтобы не выкидывать короткие, но осмысленные ответы:
    Q/A, определения, заголовки с числами, ячейки таблиц и т.п.
    """
    if not hits:
        return []
    if min_len <= 0:
        return list(hits)
    keep = [h for h in hits if len((h[0].content or "").strip()) >= min_len]
    if keep:
        if len(keep) < len(hits):
            logger.debug(
                "[RAG] low_signal_filter: оставлено %d/%d чанков (min_len=%d)",
                len(keep),
                len(hits),
                min_len,
            )
        return keep
    rescue_n = max(3, min(rescue_keep, len(hits)))
    logger.debug(
        "[RAG] low_signal_filter: все чанки короткие (<%d), rescue top-%d",
        min_len,
        rescue_n,
    )
    return sorted(hits, key=lambda x: float(x[1]), reverse=True)[:rescue_n]


async def fuse_seed_and_graph_hits(
    seed_hits: List[Tuple[DocumentVector, float]],
    graph_scores: Dict[Tuple[int, int], float],
    *,
    fetch_chunk: Any,
    graph_weight: float = 0.35,
    rrf_k: int = 60,
    limit: int = 40,
) -> List[Tuple[DocumentVector, float]]:
    """Сливает seed (vector) и graph-соседей через weighted RRF + подтягивает missing nodes.

    Старый ``0.7*cosine + 0.3*gscore``:
      - не добавлял соседей, которых нет в seed-пуле (graph expand почти бесполезен);
      - смешивал разные шкалы без нормализации graph по max.
    """
    if not seed_hits and not graph_scores:
        return []

    w_g = max(0.0, min(float(graph_weight), 0.95))
    w_v = 1.0 - w_g

    vec_rank: Dict[Tuple[int, int], int] = {}
    vec_obj: Dict[Tuple[int, int], DocumentVector] = {}
    for rank, (dv, _sc) in enumerate(sorted(seed_hits, key=lambda x: float(x[1]), reverse=True)):
        if dv.document_id is None:
            continue
        key = (int(dv.document_id), int(dv.chunk_index))
        if key in vec_rank:
            continue
        vec_rank[key] = rank
        vec_obj[key] = dv

    # Graph ranks по убыванию graph_score
    g_sorted = sorted(graph_scores.items(), key=lambda x: float(x[1]), reverse=True)
    g_rank: Dict[Tuple[int, int], int] = {}
    for rank, (key, _gs) in enumerate(g_sorted):
        g_rank[(int(key[0]), int(key[1]))] = rank

    all_keys = set(vec_rank.keys()) | set(g_rank.keys())
    out: List[Tuple[DocumentVector, float]] = []
    for key in all_keys:
        score = 0.0
        if key in vec_rank:
            score += w_v / float(rrf_k + vec_rank[key] + 1)
        if key in g_rank:
            score += w_g / float(rrf_k + g_rank[key] + 1)

        dv = vec_obj.get(key)
        if dv is None and fetch_chunk is not None:
            try:
                fetched = await fetch_chunk(key[0], key[1])
            except Exception:
                fetched = None
            if isinstance(fetched, tuple):
                fetched = fetched[0]
            dv = fetched
        if dv is None:
            continue
        out.append((dv, float(score)))

    out.sort(key=lambda x: x[1], reverse=True)
    return out[: max(1, limit)]


def merge_vector_and_keyword_hits(
    vector_hits: List[Tuple[DocumentVector, float]],
    keyword_hits: List[Tuple[DocumentVector, float]],
    *,
    keyword_weight: float = 0.30,
    rrf_k: int = 60,
) -> List[Tuple[DocumentVector, float]]:
    """
    Сливает dense- и keyword-кандидаты через weighted RRF по рангам.

    Нельзя смешивать max-norm cosine и FTS/BM25 линейно: шкалы несопоставимы,
    а keyword-only hit получает потолок ``keyword_weight`` и проигрывает
    любому top-1 vector-шуму. RRF работает по рангам и не зависит от абсолютных
    значений скоров.
    """
    if not vector_hits and not keyword_hits:
        return []
    if not keyword_hits:
        return sorted(vector_hits, key=lambda x: float(x[1]), reverse=True)
    if not vector_hits:
        return sorted(keyword_hits, key=lambda x: float(x[1]), reverse=True)

    w = max(0.0, min(float(keyword_weight), 0.95))
    w_vec = 1.0 - w

    vec_rank: Dict[Tuple[Optional[int], Optional[int]], int] = {}
    vec_obj: Dict[Tuple[Optional[int], Optional[int]], DocumentVector] = {}
    vec_raw: Dict[Tuple[Optional[int], Optional[int]], float] = {}
    for rank, (dv, sc) in enumerate(sorted(vector_hits, key=lambda x: float(x[1]), reverse=True)):
        key = (dv.document_id, dv.chunk_index)
        if key in vec_rank:
            continue
        vec_rank[key] = rank
        vec_obj[key] = dv
        vec_raw[key] = float(sc)

    kw_rank: Dict[Tuple[Optional[int], Optional[int]], int] = {}
    kw_raw: Dict[Tuple[Optional[int], Optional[int]], float] = {}
    for rank, (dv, sc) in enumerate(sorted(keyword_hits, key=lambda x: float(x[1]), reverse=True)):
        key = (dv.document_id, dv.chunk_index)
        if key in kw_rank:
            continue
        kw_rank[key] = rank
        kw_raw[key] = float(sc)
        if key not in vec_obj:
            vec_obj[key] = dv

    max_vec = max(vec_raw.values(), default=1.0) or 1.0
    max_kw = max(kw_raw.values(), default=1.0) or 1.0

    out: List[Tuple[DocumentVector, float]] = []
    for key, dv in vec_obj.items():
        score = 0.0
        if key in vec_rank:
            score += w_vec / float(rrf_k + vec_rank[key] + 1)
        if key in kw_rank:
            score += w / float(rrf_k + kw_rank[key] + 1)
        tie = 0.0
        if key in vec_raw:
            tie += w_vec * (vec_raw[key] / max_vec)
        if key in kw_raw:
            tie += w * (kw_raw[key] / max_kw)
        out.append((dv, score + 1e-6 * tie))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def diversify_result_rows(
    rows: List[Tuple[str, float, Optional[int], Optional[int]]],
    pool_limit: int,
) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
    """
    Post-rerank диверсификация для финального списка (content, score, doc_id, chunk_idx).
    Гарантирует хотя бы 1 строку от каждого документа в первых pool_limit результатах.
    """
    if not rows or pool_limit <= 0:
        return []
    rows_sorted = sorted(rows, key=lambda x: x[1], reverse=True)
    best_per_doc: Dict[
        Optional[int], Tuple[str, float, Optional[int], Optional[int]]
    ] = {}
    for row in rows_sorted:
        did = row[2]
        if did not in best_per_doc or row[1] > best_per_doc[did][1]:
            best_per_doc[did] = row

    merged: List[Tuple[str, float, Optional[int], Optional[int]]] = []
    seen: set = set()
    for item in sorted(best_per_doc.values(), key=lambda x: x[1], reverse=True):
        if len(merged) >= pool_limit:
            break
        key = (item[2], item[3])
        if key not in seen:
            merged.append(item)
            seen.add(key)

    for row in rows_sorted:
        if len(merged) >= pool_limit:
            break
        key = (row[2], row[3])
        if key not in seen:
            merged.append(row)
            seen.add(key)

    return merged


def diversify_hybrid_rrf_hits(
    hits: List[Tuple[DocumentVector, float]],
    *,
    candidate_limit: int,
    max_chunks_per_document: int,
    min_results: int,
) -> List[Tuple[DocumentVector, float]]:
    """Диверсификация только для hybrid после weighted RRF.

    RRF-score имеет собственную шкалу (~0.01), поэтому здесь нет cosine-порогов
    и эвристик, рассчитанных на cosine similarity. Сохраняем RRF-порядок, но
    пропускаем чанки сверх лимита на документ, пока хватает других документов.
    Если кандидатов недостаточно, возвращаем лучшие пропущенные строки, чтобы
    не отдать меньше запрошенного ``min_results``.
    """
    if not hits or candidate_limit <= 0:
        return []

    ordered = sorted(hits, key=lambda x: float(x[1]), reverse=True)
    limit = min(int(candidate_limit), len(ordered))
    per_doc_limit = max(0, int(max_chunks_per_document))
    if per_doc_limit <= 0:
        return ordered[:limit]

    unique_docs = {dv.document_id for dv, _ in ordered if dv.document_id is not None}
    if len(unique_docs) < 2:
        return ordered[:limit]

    selected: List[Tuple[DocumentVector, float]] = []
    overflow: List[Tuple[DocumentVector, float]] = []
    counts: Dict[int, int] = {}
    for item in ordered:
        dv, _score = item
        doc_id = dv.document_id
        if doc_id is None:
            selected.append(item)
        elif counts.get(doc_id, 0) < per_doc_limit:
            selected.append(item)
            counts[doc_id] = counts.get(doc_id, 0) + 1
        else:
            overflow.append(item)
        if len(selected) >= limit:
            break

    required = min(max(1, int(min_results)), limit)
    if len(selected) < required:
        selected.extend(overflow[: required - len(selected)])
    return selected[:limit]


def diversify_hits_by_document(
    hits: List[Tuple[DocumentVector, float]],
    pool_limit: int,
    keep_all_for_docs: Optional[set] = None,
) -> List[Tuple[DocumentVector, float]]:
    """Диверсификация «1 лучший чанк с каждого document_id → добор по скору».

    Параметр ``keep_all_for_docs`` — набор document_id, для которых диверсификация
    НЕ применяется (ВСЕ чанки сохраняются). Это нужно для entity-anchor документов:
    если в документе нашли имя — нельзя резать его до 1 чанка, иначе LLM получит
    только один раздел (например, «Газпромбанк»), а «МФТИ» и «Тинькофф Банк»
    из других чанков того же CV потеряются.
    """
    if not hits or pool_limit <= 0:
        return []
    keep_all_for_docs = keep_all_for_docs or set()
    hits_sorted = sorted(hits, key=lambda x: x[1], reverse=True)

    kept_entity: List[Tuple[DocumentVector, float]] = []
    diversify_pool: List[Tuple[DocumentVector, float]] = []
    for dv, sc in hits_sorted:
        if dv.document_id in keep_all_for_docs:
            kept_entity.append((dv, sc))
        else:
            diversify_pool.append((dv, sc))

    best_per_doc: Dict[int, Tuple[DocumentVector, float]] = {}
    for dv, sc in diversify_pool:
        did = dv.document_id
        if did is None:
            continue
        if did not in best_per_doc or sc > best_per_doc[did][1]:
            best_per_doc[did] = (dv, sc)

    merged: List[Tuple[DocumentVector, float]] = []
    seen: set = set()
    # 1. Entity-anchor документы — кладём ВСЕ их чанки вперёд.
    for dv, sc in kept_entity:
        key = (dv.document_id, dv.chunk_index)
        if key not in seen:
            merged.append((dv, sc))
            seen.add(key)
    # 2. Лучший чанк на каждый не-entity документ (классическая диверсификация).
    for item in sorted(best_per_doc.values(), key=lambda x: x[1], reverse=True):
        if len(merged) >= pool_limit:
            break
        dv, sc = item
        key = (dv.document_id, dv.chunk_index)
        if key not in seen:
            merged.append((dv, sc))
            seen.add(key)
    # 3. Добор по score до pool_limit.
    for dv, sc in hits_sorted:
        if len(merged) >= pool_limit:
            break
        key = (dv.document_id, dv.chunk_index)
        if key not in seen:
            merged.append((dv, sc))
            seen.add(key)

    return merged


BANNER_WIDTH = 72


def build_retrieval_metrics_lines(
    hits: List[Tuple[str, float, Optional[int], Optional[int]]],
    k_requested: int,
) -> List[str]:
    """Эвристики по финальному списку чанков (без эталонных ответов — для мониторинга в логах)."""
    if not hits:
        return [
            f"hits_в_ответе=0 k_запрошено={k_requested}",
            "подсказка: проверьте пороги релевантности, парсинг документов и запрос",
        ]

    scores = [float(h[1]) for h in hits]
    contents = [(h[0] or "") for h in hits]
    doc_ids = [h[2] for h in hits if h[2] is not None]
    uniq_docs = len(set(doc_ids)) if doc_ids else 0
    n = len(scores)
    mean_s = sum(scores) / n
    var = sum((s - mean_s) ** 2 for s in scores) / n if n else 0.0
    std_s = math.sqrt(var)

    lines = [
        f"hits_в_ответе={n} k_запрошено={k_requested}",
        f"score: top1={scores[0]:.4f} mean={mean_s:.4f} min={min(scores):.4f} max={max(scores):.4f} std={std_s:.4f}",
    ]
    if n >= 2:
        margin = scores[0] - scores[1]
        lines.append(f"margin_top1_minus_top2={margin:.4f} (мало → похожие кандидаты, выше неоднозначность)")
    lines.append(f"уникальных_document_id={uniq_docs} (мало при многих файлах в корпусе → узкий захват)")

    lens = [len(c) for c in contents]
    total_ctx = sum(lens)
    avg_len = total_ctx // n if n else 0
    lines.append(f"текст_чанков_симв: avg={avg_len} min={min(lens)} max={max(lens)} сумма_в_контекст={total_ctx}")

    short = sum(1 for L in lens if L < 80)
    if short:
        lines.append(f"очень_коротких_чанков(<80 симв)={short} → мало сигнала для LLM")

    empty_c = sum(1 for c in contents if not c.strip())
    if empty_c:
        lines.append(f"пустых_чанков={empty_c} → проверьте индексацию/парсинг")

    low_score = sum(1 for s in scores if s < 0.15)
    if low_score and n >= 2:
        lines.append(f"низкий_score(<0.15)={low_score} → риск «слабой опоры», смотрите пороги на backend")

    return lines


def log_rag_retrieval_report(
    *,
    store: str,
    strategy_resolved: str,
    pipeline: str,
    extra_lines: Optional[List[str]] = None,
    final_hits: List[Tuple[str, float, Optional[int], Optional[int]]],
    k_requested: int,
    search_seconds: Optional[float] = None,
    eval_lines: Optional[List[str]] = None,
) -> None:
    """Один блок в логах: стратегия + параметры + метрики по выдаче."""
    bar = "*" * BANNER_WIDTH
    stdout_metrics = os.environ.get("RAG_METRICS_STDOUT", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    stdout_lines: List[str] = []

    def _emit(text: str) -> None:
        logger.debug("%s", text)
        if stdout_metrics:
            stdout_lines.append(text)

    _emit(bar)
    _emit(f"[SVC-RAG] Использована стратегия поиска: {strategy_resolved}")
    _emit(f"[SVC-RAG] Хранилище: {store}")
    _emit(f"[SVC-RAG] Пайплайн: {pipeline}")
    if extra_lines:
        for line in extra_lines:
            _emit(f"[SVC-RAG] {line}")
    if search_seconds is not None:
        _emit(f"[SVC-RAG] время_поиска_с={search_seconds:.4f} (retrieval, постобработка, опционально LLM-judge)")
    _emit("[SVC-RAG] --- Метрики retrieval (финальная выдача) ---")
    for line in build_retrieval_metrics_lines(final_hits, k_requested):
        _emit(f"[SVC-RAG]   {line}")
    if eval_lines:
        _emit("[SVC-RAG] --- Метрики оценки (gold / LLM-judge) ---")
        for line in eval_lines:
            _emit(f"[SVC-RAG]   {line}")
    _emit(bar)
    if stdout_metrics and stdout_lines:
        try:
            sys.stderr.write("\n".join(stdout_lines) + "\n")
            sys.stderr.flush()
        except Exception:
            pass
