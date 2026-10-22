"""Общий retrieval-пайплайн для хранилищ KB / memory / project.

Выделен из дублирующихся ``search()``-методов. Единая логика обработки стратегий:
  - ``vector`` — чистый векторный поиск (pgvector cosine без постобработки);
  - ``lexical`` — только BM25 (sparse/keyword);
  - ``hybrid`` — cosine + BM25 с весами;
  - ``graph`` / ``raw_cosine`` / ``hierarchical`` (с грейсфул fallback на vector).

Сервис передаёт два замыкания:
  - ``search_vectors(query_embedding, limit)`` — обычно обёртка над ``vector_repo.similarity_search``
    с подставленными store-специфичными аргументами (например, ``project_id``);
  - ``search_keywords(query_text, limit)`` — аналогичная обёртка над
    ``vector_repo.keyword_search``.

Возвращает:
  - ``hits`` — финальный список (content, score, document_id, chunk_index);
  - ``trace`` — ``RetrievalTrace`` со счётчиками по шагам (для ``debug_trace`` в ответе /search).
"""

from __future__ import annotations


import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import re

from app.clients.rag_models_client import RagModelsClient
from app.database.fts import extract_filenames, extract_proper_nouns
from app.database.graph_repository import GraphRepository
from app.database.models import DocumentVector
from app.database.search_filters import DocumentVectorSearchFilters
from app.services.bm25_index import InMemoryBm25Index, hybrid_combine_vector_bm25
from app.services.hit_postprocess import apply_rerank_min_and_window
from app.services.hit_types import RagHit, cosine_similarity
from app.services.rag_search_helpers import (
    diversify_hits_by_document,
    diversify_hybrid_rrf_hits,
    effective_use_reranking,
    filter_by_min_vector_similarity,
    filter_low_signal_chunks,
    fuse_seed_and_graph_hits,
    keyword_boost_hits,
    merge_vector_and_keyword_hits,
    resolve_auto_pipeline_strategy,
    should_diversify_hits,
    vector_fetch_limit,
)
from app.services.rerank_helpers import rerank_vector_hits_ex
from app.services.retrieval_eval import log_retrieval_with_eval
from app.services.stage_timer import StageTimer
from app.core.logging import get_logger

logger = get_logger(__name__)

# Эвристика «перечислительного / меты» запроса.
# Ключевой сигнал, что пользователь хочет не ОДИН лучший ответ, а обзор по корпусу:
# «перечисли / выведи список / в каких документах / у кого / все упоминания / сколько».
# На таких запросах:
#  - расширяем пул кандидатов (widen_k),
#  - не дёргаем диверсификацию слишком агрессивно (она режет дубли по doc_id,
#    но именно эти дубли и нужны — по одному хиту на документ),
#  - entity lane (ниже) становится особенно важна.
_ENUM_PATTERNS = re.compile(
    r"(\bперечисл(и|ить)\b|\bвывед[иь]\b|\bсостав(ь|ить)\s+список\b|"
    r"\bсписок\s+документ|\bв\s+каких\s+документ|\bкакие\s+документ|"
    r"\bвсе\s+документ|\bвсе\s+упоминан|\bгде\s+упомина|\bупомина[ею]тся\b|"
    r"\bупомянут|\bсколько\s+документ|"
    # Расширение: вопросы, требующие полного перечисления по корпусу / документу.
    # «В каких местах работал X», «где учился X», «какие проекты», «какими навыками»
    # — все требуют ВЕСЬ документ (CV / биография), а не один лучший чанк.
    # ``\w+\s+`` (0–2 слов) допускает прилагательные между «какие/какими» и
    # опорным словом: «какими **хард** скиллами», «какие **мои** проекты».
    r"\bв\s+каких\s+(\w+\s+){0,2}(мест|компани|проект|вуз|университет|организаци|отдел)|"
    r"\bв\s+каком\s+(\w+\s+){0,2}(месте|университете|вузе|проекте|отделе|отделе)|"
    r"\bгде\s+(работал|училс|стажиров|преподавал|жил|трудилс|служил|практиков)|"
    r"\bкакие\s+(\w+\s+){0,2}(мест|компани|должност|проект|навык|скилл|hard|soft|язык|технолог|инструмент)|"
    r"\bкаким[иы]\s+(\w+\s+){0,2}(навык|скилл|hard|soft|технолог|язык|инструмент|проект)|"
    r"\bкакие\s+у\s+\S+\s+(есть|были|имеют|навык|скилл|проект|компетенц)|"
    r"\bчто\s+делал\b|\bчем\s+занимал|\bкаков\s+опыт|\bопыт\s+работы\b|"
    r"\bкем\s+работал|\bна\s+каких\s+должност|"
    r"\blist\s+all\b|\bwhich\s+documents?\b|\bmentions?\s+of\b|"
    r"\bwhere\s+(did|has)\s+\w+\s+work|\bwhat\s+(skills|projects)\b)"
)


def _is_enumeration_query(text: str) -> bool:
    if not text:
        return False
    return bool(_ENUM_PATTERNS.search(text.lower()))


SearchVectorsFn = Callable[
    [List[float], int],
    Awaitable[List[Tuple[DocumentVector, float]]],
]
SearchKeywordsFn = Callable[[str, int], Awaitable[List[Tuple[DocumentVector, float]]]]
# ILIKE-fallback: принимает список токенов (имена/коды из запроса) и возвращает
# чанки, где они встречаются буквально. Страховка на случай, когда FTS не
# сработал (tsvector не заполнен / OCR / редкая токенизация).
SubstringSearchFn = Callable[[List[str], int], Awaitable[List[Tuple[DocumentVector, float]]]]
# Parent-document expansion: для документа, чьи чанки попали в выдачу, можно
# вытащить соседние чанки (или весь документ). Это лечит классическую проблему
# "имя упомянуто один раз в начале документа, а подробности — в следующих
# чанках, в которых имени уже нет". Без этого CV/биография/отчёт почти всегда
# отвечают только первым чанком — не видя остального.
FetchDocumentChunksFn = Callable[[int], Awaitable[List[DocumentVector]]]
# Поиск document_id по имени файла (ILIKE). Нужен для запросов вида
# «сделай саммари по Воронин_Михаил.docx»: векторный поиск по тексту
# такого запроса не найдёт содержание документа — оно ему семантически
# перпендикулярно, но имя в запросе указано явно, и это надёжный сигнал.
FindDocsByFilenameFn = Callable[[str], Awaitable[List[int]]]


@dataclass
class RetrievalTrace:
    """Пошаговые метрики пайплайна для debug_trace в /search."""

    store: str
    requested_strategy: str
    resolved_strategy: str
    pipeline: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    used_rerank: bool = False
    warnings: List[str] = field(default_factory=list)
    seconds: float = 0.0
    stage_seconds: Dict[str, float] = field(default_factory=dict)

    def add(self, name: str, *, count: int, details: Optional[Dict[str, Any]] = None) -> None:
        entry: Dict[str, Any] = {"stage": name, "count": count}
        if details:
            entry.update(details)
        self.steps.append(entry)

    def mark_stage(self, name: str, seconds: float) -> None:
        key = (name or "unknown").strip() or "unknown"
        self.stage_seconds[key] = self.stage_seconds.get(key, 0.0) + max(0.0, float(seconds))

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        logger.warning("[%s] %s", self.store, msg)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "store": self.store,
            "requested_strategy": self.requested_strategy,
            "resolved_strategy": self.resolved_strategy,
            "pipeline": self.pipeline,
            "used_rerank": self.used_rerank,
            "warnings": list(self.warnings),
            "steps": list(self.steps),
            "seconds": round(self.seconds, 4),
            "stage_seconds": {k: round(v, 4) for k, v in self.stage_seconds.items()},
        }


async def run_retrieval_pipeline(
    *,
    store: str,
    query: str,
    vector_query: Optional[str],
    k: int,
    document_id: Optional[int],
    document_ids: Optional[List[int]] = None,
    use_reranking: Optional[bool],
    strategy: Optional[str],
    filters: Optional[DocumentVectorSearchFilters],
    rag_client: RagModelsClient,
    graph_repo: Optional[GraphRepository],
    cfg: Any,
    search_vectors: SearchVectorsFn,
    search_keywords: SearchKeywordsFn,
    substring_search: Optional[SubstringSearchFn] = None,
    fetch_document_chunks: Optional[FetchDocumentChunksFn] = None,
    find_docs_by_filename: Optional[FindDocsByFilenameFn] = None,
    vector_repo_for_window: Any = None,
    bm25_index: Optional[InMemoryBm25Index] = None,
    embed_model: Optional[str] = None,
    rerank_client: Optional[RagModelsClient] = None,
    rerank_model: Optional[str] = None,
    # Метаданные для retrieval_eval / логов — опциональны:
    eval_gold_document_ids: Optional[List[int]] = None,
    eval_gold_chunks: Optional[List[Tuple[int, int]]] = None,
    eval_llm_judge: bool = False,
    log_store_label: Optional[str] = None,
) -> Tuple[List[Tuple[str, float, Optional[int], Optional[int]]], RetrievalTrace]:
    """Единая реализация retrieval-пайплайна для нон-global хранилищ."""

    q_text = (query or "").strip()
    vq = (vector_query or "").strip()
    t0 = time.perf_counter()
    timer = StageTimer("SEARCH", store=store, k=k)

    # Сырой cosine чанка — единственная шкала, сопоставимая между запросами.
    # Заполняется сразу после pgvector, до merge/boost/rerank, и доезжает
    # до ответа отдельным полем: по ```score``` показать «релевантность 84%»
    # нельзя, там может быть RRF (~0.016) или логит cross-encoder.
    chunk_cosine: Dict[Tuple[Any, Any], float] = {}
    # Эмбеддинг запроса — чтобы досчитать cosine чанкам, которые пришли мимо
    # pgvector (BM25, entity lane, filename-anchor, parent-expansion). Иначе
    # у половины выдачи процент был бы «н/д» или, хуже, посчитан по RRF.
    query_vector: List[float] = []

    async def _ensure_query_vector() -> None:
        """Эмбеддинг запроса ради cosine там, где самому поиску он не нужен.

        Лексическая ветка эмбеддингов не считает вовсе. Без этого вызова её
        чанки остались бы единственными без релевантности, а процент должен
        считаться одинаково на всех стратегиях. Один вызов на поиск; сбой
        на выдачу не влияет — cosine просто останется неизвестным.
        """
        nonlocal query_vector
        source = vq or q_text
        if query_vector or not source or not rag_client:
            return
        try:
            with timer.stage("embed_query_for_cosine"):
                emb = await rag_client.embed([source], model=embed_model, kind="query")
        except Exception as e:
            logger.warning("[%s] cosine_embed_failed: %s", store, e)
            trace.warn(f"cosine_embed_failed: {e}")
            return
        if emb:
            query_vector = list(emb[0] or [])

    def _chunk_cosine(dv: DocumentVector) -> Optional[float]:
        key = (dv.document_id, dv.chunk_index)
        cached = chunk_cosine.get(key)
        if cached is not None:
            return cached
        if not query_vector:
            return None
        value = cosine_similarity(query_vector, getattr(dv, "embedding", None) or [])
        if value is not None:
            chunk_cosine[key] = value
        return value

    def _row(dv: DocumentVector, score: Any) -> RagHit:
        return RagHit(
            dv.content, float(score), dv.document_id, dv.chunk_index, _chunk_cosine(dv)
        )

    # Белый список документов (например, привязанные к агенту). Векторный,
    # лексический и substring-поиск фильтруются репозиторием на стороне SQL,
    # а BM25-индекс и поиск по имени файла идут мимо него - их скоупим здесь
    allowed_doc_ids: Optional[set] = None
    if document_ids:
        allowed_doc_ids = {int(d) for d in document_ids if d is not None}
    if document_id is not None:
        # document_id — сужение внутри белого списка, а не расширение.
        allowed_doc_ids = {int(document_id)}

    def _keep_doc(doc_id: Any) -> bool:
        if allowed_doc_ids is None or doc_id is None:
            return True
        try:
            return int(doc_id) in allowed_doc_ids
        except (TypeError, ValueError):
            return False

    requested = (strategy or "auto").lower()
    if requested == "flat":
        requested = "vector"
    if requested == "standard":
        # Deprecated compatibility alias для старых клиентов. Внутри пайплайна
        # стратегии standard больше нет.
        requested = "vector"
    if requested in {"keyword", "bm25"}:
        requested = "lexical"
    # BUG-RAG-15: hybrid_ok читается и при явной стратегии (filename-anchor ниже),
    # поэтому определяем его до ветвления; True только для auto.
    hybrid_ok = False
    # ``hierarchical`` в нон-global пока не поддерживается индексной стороной — graceful fallback.
    if requested == "hierarchical" and store != "global":
        logger.debug(
            "[%s] strategy=hierarchical не поддерживается этим хранилищем, fallback=vector",
            store,
        )
        resolved = "vector"
    elif requested == "auto":
        graph_ok = bool(graph_repo) and bool(getattr(cfg, "enable_graph_rag", True))
        hybrid_ok = (
            bool(getattr(cfg, "use_hybrid_search", True)) and bm25_index is not None
        )
        resolved = resolve_auto_pipeline_strategy(
            q_text,
            store=store,
            document_id=document_id,
            hierarchical_available=False,
            graph_available=graph_ok,
            hybrid_available=hybrid_ok,
        )
        # Явный выбор пользователя не трогаем: сюда попадает только auto.
        if resolved == "lexical" and hybrid_ok:
            logger.debug(
                "[%s] strategy=auto: lexical → hybrid (лексика входит в гибрид)",
                store,
            )
            resolved = "hybrid"
        logger.debug("[%s] strategy=auto → %s", store, resolved)
    else:
        resolved = requested

    # vector / lexical — без rerank; hybrid — по конфигу.
    rerank_key = "auto" if requested == "auto" else resolved
    if resolved in {"vector", "lexical", "raw_cosine"}:
        rerank_key = "vector"
    eff_rr = effective_use_reranking(
        use_reranking,
        cfg.use_reranking,
        rerank_key,
        query_text=q_text,
    )
    logger.debug(
        "[%s] retrieval: requested=%s → resolved=%s, reranking=%s, k=%s, document_id=%s",
        store,
        requested,
        resolved,
        eff_rr,
        k,
        document_id,
    )

    # Имена файлов в запросе («сделай саммари по Воронин_Михаил.docx»).
    # Резолвим их в document_id ПРЕЖДЕ чем делать поиск — потому что такие
    # запросы семантически не совпадают с содержимым файла, и нормальный
    # retrieval их проваливает. Найденные документы станут anchor'ами и будут
    # целиком вытянуты parent-expansion'ом.
    filename_mentions: List[str] = extract_filenames(q_text) if q_text else []
    filename_doc_ids: List[int] = []
    if filename_mentions and find_docs_by_filename is not None:
        for fn in filename_mentions:
            try:
                ids = await find_docs_by_filename(fn)
            except Exception as e:
                logger.warning("[%s] find_docs_by_filename(%r) failed: %s", store, fn, e)
                continue
            if ids:
                filename_doc_ids.extend(ids)
            else:
                # Пробуем по стему (без расширения) — иногда пользователь пишет
                # «по Воронин_Михаил» без ".docx".
                stem = fn.rsplit(".", 1)[0]
                if stem and stem != fn:
                    try:
                        ids2 = await find_docs_by_filename(stem)
                        if ids2:
                            filename_doc_ids.extend(ids2)
                    except Exception:
                        pass
        # Дедуп с сохранением порядка + скоуп по белому списку
        seen: set = set()
        filename_doc_ids = [d for d in filename_doc_ids if _keep_doc(d) and not (d in seen or seen.add(d))]
        if filename_doc_ids:
            logger.debug(
                "[%s] filename_anchor: query mentions %s → document_ids=%s",
                store,
                filename_mentions,
                filename_doc_ids,
            )
            if requested == "auto" and resolved == "lexical" and hybrid_ok:
                logger.debug(
                    "[%s] filename_anchor: lexical → hybrid (файл уже выбран)",
                    store,
                )
                resolved = "hybrid"

    # Энумеративные запросы расширяем пул: топ-8 на 50-документном корпусе не даёт
    # собрать все упоминания «Константина», если среди них есть хотя бы один слабый
    # по вектору чанк. widen_k влияет на vector_fetch_limit и пост-обработку.
    # Если в запросе есть имя файла — ведём себя как при enumeration (пользователь
    # по сути просит «весь документ»).
    enumeration = _is_enumeration_query(q_text) or bool(filename_doc_ids)
    effective_k_for_fetch = k
    if enumeration:
        effective_k_for_fetch = max(k * 3, 24)

    fetch_lim = vector_fetch_limit(
        effective_k_for_fetch,
        graph=(resolved == "graph"),
        document_id=document_id,
        use_rerank=eff_rr,
        rerank_top_k=int(cfg.rerank_top_k or 20),
    )

    # Entity lane: собираем собственные имена / аббревиатуры / числовые коды
    # из запроса и готовимся в отдельной ветке достать ВСЕ чанки, где они есть.
    entity_tokens: List[str] = extract_proper_nouns(q_text) if q_text else []
    pipeline_parts: List[str] = []
    if resolved == "lexical":
        pipeline_parts = ["bm25_only"]
    elif resolved == "vector":
        pipeline_parts = ["embed_query", f"pgvector_cosine(limit={fetch_lim})", "vector_only"]
    elif resolved == "hybrid":
        pipeline_parts = [
            "embed_query",
            f"pgvector_cosine(limit={fetch_lim})",
            "bm25_hybrid_merge",
        ]
        if eff_rr:
            pipeline_parts.append("cross_encoder_rerank")
    else:
        pipeline_parts = ["embed_query", f"pgvector_cosine(limit={fetch_lim})"]
        if resolved != "raw_cosine":
            # Порядок соответствует коду: косинусный порог идёт ДО слияния
            # с keyword, иначе он применялся бы к RRF-скорам.
            pipeline_parts.append(
                f"min_vector_similarity({float(getattr(cfg, 'min_vector_similarity', 0.0) or 0.0):.2f})"
            )
            pipeline_parts.append("keyword_fts_merge(OR)")
            if entity_tokens:
                pipeline_parts.append(f"entity_lane(tokens={len(entity_tokens)})")
            pipeline_parts.append(f"low_signal_filter(min_len={int(getattr(cfg, 'min_chunk_length', 40))})")
            pipeline_parts.append("keyword_boost")
        if resolved == "graph":
            pipeline_parts.append("graph_expand_neighbors")
        if document_id is None and resolved != "graph" and resolved != "raw_cosine" and not enumeration:
            pipeline_parts.append("diversify_by_document")
        if eff_rr:
            pipeline_parts.append("cross_encoder_rerank")

    trace = RetrievalTrace(
        store=store,
        requested_strategy=requested,
        resolved_strategy=resolved,
        pipeline=" -> ".join(pipeline_parts),
    )

    def _done(
        hits: List[Tuple[str, float, Optional[int], Optional[int]]],
        tr: RetrievalTrace,
    ) -> Tuple[List[Tuple[str, float, Optional[int], Optional[int]]], RetrievalTrace]:
        tr.seconds = time.perf_counter() - t0
        for name, sec in timer.items():
            tr.mark_stage(name, sec)
        timer.meta["strategy"] = tr.resolved_strategy
        timer.meta["requested"] = tr.requested_strategy
        timer.meta["hits"] = len(hits or [])
        timer.meta["rerank"] = "yes" if tr.used_rerank else "no"
        timer.log(logger)
        return hits, tr

    if not q_text and not vq:
        trace.warn("empty_query: ни query, ни vector_query не заданы")
        return _done([], trace)

    # --- lexical: только BM25 (без эмбеддингов / cosine) ---
    if resolved == "lexical":
        if bm25_index is None:
            trace.warn("lexical: BM25 индекс не передан в пайплайн")
            await log_retrieval_with_eval(
                store=log_store_label or store,
                strategy_resolved="lexical",
                pipeline="bm25_only (нет индекса)",
                extra_lines=[f"k={k}"],
                final_hits=[],
                k_requested=k,
                query_for_eval=q_text,
                search_started_perf=t0,
                gold_document_ids=eval_gold_document_ids,
                gold_chunks=eval_gold_chunks,
                llm_judge=eval_llm_judge,
            )
            trace.seconds = time.perf_counter() - t0
            return _done([], trace)
        with timer.stage("bm25"):
            bm25_rows = await bm25_index.search(q_text, max(k * 6, 48))
            # Индекс строится по всей таблице - фильтруем сами
            if allowed_doc_ids is not None:
                bm25_rows = [row for row in bm25_rows if _keep_doc(row[0])]
        if not bm25_rows or vector_repo_for_window is None:
            if not bm25_rows:
                trace.warn("lexical: пустая выдача BM25")
            else:
                trace.warn("lexical: нет vector_repo для подгрузки чанков")
            await log_retrieval_with_eval(
                store=log_store_label or store,
                strategy_resolved="lexical",
                pipeline="bm25_only (пустая выдача)",
                extra_lines=[f"k={k}", f"document_id={document_id}"],
                final_hits=[],
                k_requested=k,
                query_for_eval=q_text,
                search_started_perf=t0,
                gold_document_ids=eval_gold_document_ids,
                gold_chunks=eval_gold_chunks,
                llm_judge=eval_llm_judge,
            )
            trace.seconds = time.perf_counter() - t0
            return _done([], trace)
        max_bm25 = max((float(score) for _, _, score in bm25_rows), default=1.0) or 1.0
        lex_hits: List[Tuple[DocumentVector, float]] = []
        fetch_one = getattr(
            vector_repo_for_window, "get_vector_by_document_and_chunk", None
        )
        if fetch_one is None:
            trace.warn("lexical: get_vector_by_document_and_chunk недоступен")
            trace.seconds = time.perf_counter() - t0
            return _done([], trace)
        # Под реранк нужен пул больше k: реранжировать k из k бессмысленно.
        # Без реранка добираем ровно k, как было, — лишних чтений не делаем.
        lex_pool = max(int(cfg.rerank_top_k or 20), k) if eff_rr else k
        for doc_id_bm25, chunk_idx_bm25, score_bm25 in bm25_rows:
            vec = await fetch_one(doc_id_bm25, chunk_idx_bm25)
            if not vec:
                continue
            if isinstance(vec, tuple):
                vec = vec[0]
            lex_hits.append((vec, float(score_bm25) / max_bm25))
            if len(lex_hits) >= lex_pool:
                break
        used_rr_lex = False
        if eff_rr and len(lex_hits) > 1:
            try:
                with timer.stage("rerank"):
                    lex_hits, used_rr_lex = await rerank_vector_hits_ex(
                        q_text,
                        lex_hits,
                        rerank_client or rag_client,
                        top_k=max(int(cfg.rerank_top_k or 20), k),
                        model=rerank_model,
                    )
                if used_rr_lex:
                    trace.add("cross_encoder_rerank", count=len(lex_hits))
                else:
                    trace.warn("rerank_skipped: реранкер недоступен")
            except Exception as e:
                logger.warning("[%s] lexical rerank failed: %s", store, e)
                trace.warn(f"rerank_failed: {e}")
        # Сам поиск эмбеддингов не использовал — считаем их только ради
        # релевантности, и только для тех чанков, что реально уйдут в ответ.
        lex_out = lex_hits[:k]
        if lex_out:
            await _ensure_query_vector()
        out_lexical = [_row(dv, sc) for dv, sc in lex_out]
        trace.add("bm25_search", count=len(out_lexical))
        trace.used_rerank = used_rr_lex
        await log_retrieval_with_eval(
            store=log_store_label or store,
            strategy_resolved="lexical",
            pipeline="bm25_only" + (" -> cross_encoder_rerank" if used_rr_lex else ""),
            extra_lines=[
                f"k={k}",
                f"document_id={document_id}",
                f"реранк_cross_encoder={'да' if eff_rr else 'нет'}",
                f"реранк_применён={'да' if used_rr_lex else 'нет'}",
            ],
            final_hits=out_lexical,
            k_requested=k,
            query_for_eval=q_text,
            search_started_perf=t0,
            gold_document_ids=eval_gold_document_ids,
            gold_chunks=eval_gold_chunks,
            llm_judge=eval_llm_judge,
        )
        return _done(out_lexical, trace)

    emb_src = vq or q_text
    try:
        with timer.stage("embed_query"):
            query_emb = await rag_client.embed(
                [emb_src], model=embed_model, kind="query"
            )
    except Exception as e:
        trace.warn(f"embed_error: {e}")
        await log_retrieval_with_eval(
            store=log_store_label or store,
            strategy_resolved=resolved,
            pipeline=f"{trace.pipeline} (ошибка embed)",
            extra_lines=[f"k={k}"],
            final_hits=[],
            k_requested=k,
            query_for_eval=q_text,
            search_started_perf=t0,
            gold_document_ids=eval_gold_document_ids,
            gold_chunks=eval_gold_chunks,
            llm_judge=eval_llm_judge,
        )
        return _done([], trace)

    if not query_emb:
        trace.warn("empty_embedding: сервис rag-models вернул пустой список")
        return _done([], trace)

    # С этого момента cosine можно досчитать любому чанку, у которого есть
    # эмбеддинг, — в том числе найденному лексически.
    query_vector = list(query_emb[0] or [])

    with timer.stage("vector"):
        hits: List[Tuple[DocumentVector, float]] = await search_vectors(query_emb[0], fetch_lim)
    trace.add("vector_search", count=len(hits), details={"limit": fetch_lim})

    # Для vector/graph поиска summary-чанки (level 1/2) иерархической индексации
    # не должны быть самостоятельными ответами — только детальные level-0 чанки.
    if resolved in ("vector", "graph") and hits:
        detail_hits = [(dv, sc) for dv, sc in hits if dv.metadata.get("level", 0) == 0]
        if detail_hits:
            hits = detail_hits
            trace.add("hierarchical_detail_filter", count=len(hits))

    # Сохраняем чистые cosine-скоры ПО ДОКУМЕНТАМ ДО всех merge/boost/rerank.
    # Это «честный» сигнал семантической близости документа к запросу: вектор
    # не знает про entity-lane, filename-match и artificial score=0.5, он просто
    # меряет близость эмбеддингов.
    #
    # Зачем: потом, при сортировке anchor-чанков, использовать как ПЕРВЫЙ
    # ключ. Иначе документ, который попал в anchor_chunks только через
    # filename-entity (искусственный 0.5), оказывается «впереди» реально
    # семантически релевантного (CV с vector=0.13) — и вылетает из
    # LLM-бюджета первым.
    doc_vector_score: Dict[Any, float] = {}
    for dv, sc in hits:
        try:
            s = float(sc)
        except (TypeError, ValueError):
            continue
        chunk_cosine[(dv.document_id, dv.chunk_index)] = s
        did = dv.document_id
        if did is None:
            continue
        if s > doc_vector_score.get(did, float("-inf")):
            doc_vector_score[did] = s

    if resolved == "raw_cosine":
        rows = [_row(dv, sc) for dv, sc in hits[:k]]
        trace.add("raw_cosine_cut", count=len(rows))
        trace.seconds = time.perf_counter() - t0
        await log_retrieval_with_eval(
            store=log_store_label or store,
            strategy_resolved="raw_cosine",
            pipeline=f"embed_query -> pgvector_cosine(limit={fetch_lim}) (raw, no_postprocess)",
            extra_lines=[f"k={k}"],
            final_hits=rows,
            k_requested=k,
            query_for_eval=q_text,
            search_started_perf=t0,
            gold_document_ids=eval_gold_document_ids,
            gold_chunks=eval_gold_chunks,
            llm_judge=eval_llm_judge,
        )
        return _done(rows, trace)

    # --- vector: чистый cosine + опциональный cap «чанков на документ» ---
    if resolved == "vector":
        vec_cap = int(getattr(cfg, "vector_max_chunks_per_document", 6) or 0)
        vec_hits = hits
        diversified = False
        if vec_cap > 0 and document_id is None and hits:
            before_div = len(vec_hits)
            vec_hits = diversify_hybrid_rrf_hits(
                hits,
                candidate_limit=len(hits),
                max_chunks_per_document=vec_cap,
                min_results=k,
            )
            diversified = True
            trace.add(
                "vector_diversify",
                count=len(vec_hits),
                details={
                    "max_chunks_per_document": vec_cap,
                    "before": before_div,
                },
            )
        used_rr_vec = False
        if eff_rr and len(vec_hits) > 1:
            try:
                with timer.stage("rerank"):
                    vec_hits, used_rr_vec = await rerank_vector_hits_ex(
                        q_text,
                        vec_hits,
                        rerank_client or rag_client,
                        top_k=max(int(cfg.rerank_top_k or 20), k),
                        model=rerank_model,
                    )
                if used_rr_vec:
                    trace.add("cross_encoder_rerank", count=len(vec_hits))
                else:
                    trace.warn("rerank_skipped: реранкер недоступен")
            except Exception as e:
                logger.warning("[%s] vector rerank failed: %s", store, e)
                trace.warn(f"rerank_failed: {e}")
        rows = [_row(dv, sc) for dv, sc in vec_hits[:k]]
        trace.add("vector_cosine_only", count=len(rows))
        trace.used_rerank = used_rr_vec
        await log_retrieval_with_eval(
            store=log_store_label or store,
            strategy_resolved="vector",
            pipeline=(
                f"embed_query -> pgvector_cosine(limit={fetch_lim})"
                + (
                    f" -> diversify(max_per_doc={vec_cap})"
                    if diversified
                    else " (raw_order, no_postprocess)"
                )
            ),
            extra_lines=[
                f"k={k}",
                "порог_cosine=нет",
                (
                    f"диверсификация=да (max_chunks_per_document={vec_cap})"
                    if diversified
                    else "диверсификация=нет"
                ),
                "гибрид=нет",
                f"реранк_cross_encoder={'да' if eff_rr else 'нет'}",
                f"реранк_применён={'да' if used_rr_vec else 'нет'}",
            ],
            final_hits=rows,
            k_requested=k,
            query_for_eval=q_text,
            search_started_perf=t0,
            gold_document_ids=eval_gold_document_ids,
            gold_chunks=eval_gold_chunks,
            llm_judge=eval_llm_judge,
        )
        return _done(rows, trace)

    # --- hybrid: cosine + BM25 через weighted RRF ---
    if resolved == "hybrid":
        with timer.stage("hybrid"):
            # Cosine-порог — только по сырым vector-скорам ДО RRF merge.
            min_sim = float(getattr(cfg, "min_vector_similarity", 0.0) or 0.0)
            hits = filter_by_min_vector_similarity(hits, min_sim, k)
            hits = filter_low_signal_chunks(
                hits, min_len=int(getattr(cfg, "min_chunk_length", 40)), rescue_keep=max(k * 3, 12)
            )
            trace.add("pre_hybrid_vector_filter", count=len(hits))

            hybrid_applied = False
            if bm25_index is not None and getattr(cfg, "use_hybrid_search", True):

                async def _fetch_chunk(doc_id: int, chunk_idx: int):
                    if vector_repo_for_window is None:
                        return None
                    fetch_one = getattr(vector_repo_for_window, "get_vector_by_document_and_chunk", None)
                    if fetch_one is None:
                        return None
                    vec = await fetch_one(doc_id, chunk_idx)
                    if isinstance(vec, tuple):
                        return vec[0]
                    return vec

                hybrid_hits = await hybrid_combine_vector_bm25(
                    q_text,
                    hits,
                    k=fetch_lim,
                    bm25_index=bm25_index,
                    bm25_weight=float(getattr(cfg, "hybrid_bm25_weight", 0.3) or 0.3),
                    fetch_chunk=_fetch_chunk,
                    document_id=document_id,
                    allowed_document_ids=allowed_doc_ids,
                )
                if hybrid_hits:
                    hits = hybrid_hits
                    hybrid_applied = True
                    trace.add("bm25_rrf_merge", count=len(hits))
            else:
                trace.warn("hybrid: BM25 недоступен, остаёмся на vector-only")

            # Hybrid использует отдельную диверсификацию ПОСЛЕ weighted RRF.
            # Никаких cosine-порогов/эвристик к RRF-score не применяем: это другая шкала.
            hybrid_diversified = False
            if document_id is None and hits and bool(getattr(cfg, "hybrid_diversify_results", True)):
                pool_div = max(int(cfg.rerank_top_k or 20), k * 6, 56)
                hits = diversify_hybrid_rrf_hits(
                    hits,
                    candidate_limit=min(pool_div, len(hits)),
                    max_chunks_per_document=int(getattr(cfg, "hybrid_max_chunks_per_document", 2) or 0),
                    min_results=k,
                )
                hybrid_diversified = True
                trace.add(
                    "hybrid_rrf_diversify",
                    count=len(hits),
                    details={
                        "max_chunks_per_document": int(getattr(cfg, "hybrid_max_chunks_per_document", 2) or 0),
                        "score_scale": "RRF",
                    },
                )

        used_rr = False
        if eff_rr and len(hits) > 1:
            try:
                with timer.stage("rerank"):
                    hits, used_rr = await rerank_vector_hits_ex(
                        q_text,
                        hits,
                        rerank_client or rag_client,
                        top_k=int(cfg.rerank_top_k or 20),
                        model=rerank_model,
                    )
                if used_rr:
                    trace.add("cross_encoder_rerank", count=len(hits))
                else: 
                    trace.warn("hybrid_rerank_skipped: реранкер недоступен")
            except Exception as e:
                logger.warning("[%s] hybrid rerank failed: %s", store, e)
                trace.warn(f"hybrid_rerank_failed: {e}")

        rows = [_row(dv, sc) for dv, sc in hits[:k]]
        if used_rr and vector_repo_for_window is not None:
            rows = await apply_rerank_min_and_window(
                vector_repo_for_window,
                rows,
                rerank_min_score=float(getattr(cfg, "rerank_min_score", 0) or 0),
                sentence_window=int(getattr(cfg, "sentence_window", 0) or 0),
                used_rerank=True,
            )
        trace.used_rerank = used_rr
        trace.pipeline = (
            f"embed_query -> pgvector_cosine -> "
            f"{'bm25_rrf_merge' if hybrid_applied else 'bm25_skipped'} -> "
            f"{'hybrid_rrf_diversify' if hybrid_diversified else 'no_diversify'} -> "
            f"{'cross_encoder_rerank' if used_rr else 'no_rerank'}"
        )
        trace.seconds = time.perf_counter() - t0
        await log_retrieval_with_eval(
            store=log_store_label or store,
            strategy_resolved="hybrid",
            pipeline=trace.pipeline,
            extra_lines=[
                f"k={k}",
                f"гибрид_применён={'да' if hybrid_applied else 'нет'}",
                f"hybrid_bm25_weight={getattr(cfg, 'hybrid_bm25_weight', 0.3)}",
                "fusion=weighted_RRF",
                f"hybrid_diversify_results={getattr(cfg, 'hybrid_diversify_results', True)}",
                f"hybrid_max_chunks_per_document={getattr(cfg, 'hybrid_max_chunks_per_document', 2)}",
                f"реранк_cross_encoder={'да' if used_rr else 'нет'}",
            ],
            final_hits=rows,
            k_requested=k,
            query_for_eval=q_text,
            search_started_perf=t0,
            gold_document_ids=eval_gold_document_ids,
            gold_chunks=eval_gold_chunks,
            llm_judge=eval_llm_judge,
        )
        return _done(rows, trace)

    # --- Cosine-порог: ТОЛЬКО по сырым vector-скорам, до слияния с keyword ---
    #
    # Раньше этот фильтр стоял ниже, ПОСЛЕ merge_vector_and_keyword_hits.
    # А merge отдаёт weighted RRF: максимум ~1/61 ≈ 0.0164, то есть ЛЮБОЙ скор
    # там ниже min_vector_similarity=0.05. Порог срезал всю выдачу целиком,
    # срабатывал rescue top-N, и пул схлопывался со 120 кандидатов до 12 —
    # ещё до диверсификации и parent-expansion. Именно поэтому фрагменты,
    # которые находила либра, до ответа не доезжали.
    #
    # Hybrid-ветка выше делает ровно это же и в правильном порядке.
    # Entity-хиты фильтр не задевает: они приходят из keyword/ILIKE/filename
    # каналов и подмешиваются уже после него.
    min_sim = float(getattr(cfg, "min_vector_similarity", 0.0) or 0.0)
    before = len(hits)
    hits = filter_by_min_vector_similarity(hits, min_sim, k)
    trace.add(
        "min_similarity_filter",
        count=len(hits),
        details={
            "min_vector_similarity": min_sim,
            "before": before,
            "scale": "cosine (до RRF-слияния)",
        },
    )

    # --- Keyword FTS (OR-семантика) ---
    # Используется для auto→reranking / graph и прочих режимов с постобработкой.
    keyword_hits: List[Tuple[DocumentVector, float]] = []
    if q_text:
        try:
            keyword_hits = await search_keywords(q_text, max(k * 6, 48))
        except Exception as e:
            # Был debug — теперь warning: тихий провал keyword_search приводит
            # к чисто векторному поиску, что и ломало recall на именах/кодах.
            logger.warning("[%s] keyword_search_failed: %s", store, e)
            trace.warn(f"keyword_search_failed: {e}")
    trace.add("keyword_search", count=len(keyword_hits))

    # --- Entity lane: targeted поиск по собственным именам/кодам ---
    #
    # Фикс кейса «упоминается Константин, но RAG говорит, что нет». Три
    # параллельных канала, все объединяются в entity_hits:
    #
    #   1) FTS-tsquery по именам. Основной канал для русских имён в падежах:
    #      PostgreSQL snowball нормализует «Константина → константин»,
    #      «Петра → петр», «Михайлины → михайлин» и матчит с чанками, где
    #      имя в именительном. Работает для любой морфологии без эвристик.
    #
    #   2) ILIKE по сырым токенам. Страховка для того, что snowball принципиально
    #      не знает: коды/ID (СК0050629, 44-ФЗ), латиница (Konstantin.nf@…),
    #      редкие имена вне словаря (Лев → «льва» не нормализуется, и
    #      чтобы поймать все формы, пользователь может писать запрос
    #      как угодно — ILIKE ищет подстроку токена как есть). Для падежей
    #      русских слов этот канал уже НЕ нужен — их покрывает FTS (1).
    #
    #   3) Entity-in-filename. Имя встречается в ИМЕНИ файла (типовой паттерн:
    #      «Биохимия_Некрасов_СК0050629.pdf»). Документ автоматически
    #      релевантен, даже если вектор и FTS его пропустили (в теле —
    #      таблицы/цифры, OCR мог дать мусор). Тянем первые 4 чанка как
    #      entity-hits.
    entity_hits: List[Tuple[DocumentVector, float]] = []
    entity_keys: set = set()
    entity_fts_hits_n = 0
    entity_ilike_hits_n = 0
    entity_filename_docs: List[int] = []
    entity_filename_hits_n = 0
    if entity_tokens and q_text:
        # (1) FTS
        entity_query = " ".join(entity_tokens)
        try:
            fts_hits = await search_keywords(entity_query, max(k * 8, 64))
            entity_fts_hits_n = len(fts_hits)
            entity_hits.extend(fts_hits)
        except Exception as e:
            logger.warning("[%s] entity_lane_fts_failed: %s", store, e)
            trace.warn(f"entity_lane_fts_failed: {e}")

        # (2) ILIKE по сырым токенам — всегда, параллельно FTS.
        # Ищем СЫРОЙ токен из запроса как подстроку. FTS (1) отвечает за
        # морфологию русского, ILIKE — за то, что FTS не покроет
        # (латиница, коды, имена вне словаря snowball).
        if substring_search is not None:
            lookup_tokens = list(dict.fromkeys([t for t in entity_tokens if t]))
            if lookup_tokens:
                try:
                    ilike_hits = await substring_search(lookup_tokens, max(k * 8, 64))
                    entity_ilike_hits_n = len(ilike_hits)
                    existing = {(dv.document_id, dv.chunk_index) for dv, _ in entity_hits}
                    for dv, sc in ilike_hits:
                        if (dv.document_id, dv.chunk_index) not in existing:
                            entity_hits.append((dv, sc))
                            existing.add((dv.document_id, dv.chunk_index))
                    if ilike_hits:
                        logger.debug(
                            "[%s] entity_lane ILIKE(raw): tokens %s → %d чанков",
                            store,
                            lookup_tokens,
                            len(ilike_hits),
                        )
                except Exception as e:
                    logger.warning("[%s] entity_ilike_failed: %s", store, e)
                    trace.warn(f"entity_ilike_failed: {e}")

        # (3) Entity-in-filename: ищем имя в имени файла (на сыром токене),
        # тянем первые чанки найденных документов как entity-хиты.
        if find_docs_by_filename is not None and fetch_document_chunks is not None:
            # Отфильтруем слишком короткие / чисто числовые, чтобы не ловить
            # случайные совпадения по датам в имени. ≥4 символов достаточно.
            filename_probe = [t for t in entity_tokens if len(t) >= 4 and not t.isdigit()]
            matched_docs: set = set()
            for tok in filename_probe:
                try:
                    ids = await find_docs_by_filename(tok)
                except Exception as e:
                    logger.warning("[%s] entity_find_docs_by_filename(%r)_failed: %s", store, tok, e)
                    continue
                if ids:
                    matched_docs.update(d for d in ids if _keep_doc(d))
            # Разумный cap: не более 8 документов, чтобы не взорвать контекст.
            # Если пользователь ищет «Ivanov» и у него 50 файлов с Ivanov в имени
            # — это уже сигнал переформулировать запрос, а не RAG-магия.
            matched_docs_list = sorted(matched_docs)[:8]
            if matched_docs_list:
                entity_filename_docs = matched_docs_list
                existing = {(dv.document_id, dv.chunk_index) for dv, _ in entity_hits}
                added = 0
                for doc_id in matched_docs_list:
                    try:
                        chunks = await fetch_document_chunks(doc_id)
                    except Exception as e:
                        trace.warn(f"fetch_document_chunks_filename({doc_id})_failed: {e}")
                        continue
                    if not chunks:
                        continue
                    chunks_sorted = sorted(chunks, key=lambda d: int(getattr(d, "chunk_index", 0) or 0))
                    # Берём первые 4 чанка: обычно «обложка + введение», достаточно
                    # чтобы LLM понял, о каком документе речь. Скор — средний
                    # (0.5), чтобы они прошли min_vector_similarity и попали в пул.
                    for dv in chunks_sorted[:4]:
                        key = (dv.document_id, dv.chunk_index)
                        if key in existing:
                            continue
                        entity_hits.append((dv, 0.5))
                        existing.add(key)
                        added += 1
                entity_filename_hits_n = added
                if added:
                    logger.debug(
                        "[%s] entity_lane filename-match: tokens %s → filenames of docs %s → +%d chunks",
                        store,
                        entity_tokens,
                        matched_docs_list,
                        added,
                    )

        entity_keys = {(dv.document_id, dv.chunk_index) for dv, _ in entity_hits}
    trace.add(
        "entity_lane",
        count=len(entity_hits),
        details={
            "entities": entity_tokens,
            "fts_hits": entity_fts_hits_n,
            "ilike_stemmed_hits": entity_ilike_hits_n,
            "filename_matched_docs": entity_filename_docs,
            "filename_hits_added": entity_filename_hits_n,
        },
    )

    if keyword_hits or entity_hits:
        # Entity-хиты идут ВМЕСТЕ с keyword_hits: их вес в merge такой же, но
        # позже они переживут фильтры благодаря must_keep-механике.
        combined_kw: List[Tuple[DocumentVector, float]] = list(keyword_hits) + list(entity_hits)
        kw_weight = 0.35
        if enumeration:
            # При enumeration сильнее опираемся на keyword: «в каких документах упоминается X»
            # — чисто лексический вопрос, вектор здесь даёт шум.
            kw_weight = max(kw_weight, 0.7)
        before = len(hits)
        hits = merge_vector_and_keyword_hits(hits, combined_kw, keyword_weight=kw_weight)
        trace.add(
            "merge_vector_keyword",
            count=len(hits),
            details={
                "vector_before": before,
                "keyword_weight": kw_weight,
                "enumeration": enumeration,
            },
        )

    # --- Фильтры по тексту чанка. Entity-хиты в них «бессмертны». ---
    # Cosine-порог здесь не применяется: он работал выше, по своей шкале

    before = len(hits)
    if entity_keys:
        pinned = [h for h in hits if (h[0].document_id, h[0].chunk_index) in entity_keys]
        rest = [h for h in hits if (h[0].document_id, h[0].chunk_index) not in entity_keys]
        rest = filter_low_signal_chunks(
            rest, min_len=int(getattr(cfg, "min_chunk_length", 40)), rescue_keep=max(k * 3, 12)
        )
        hits = pinned + rest
    else:
        hits = filter_low_signal_chunks(
            hits, min_len=int(getattr(cfg, "min_chunk_length", 40)), rescue_keep=max(k * 3, 12)
        )
    trace.add(
        "low_signal_filter",
        count=len(hits),
        details={"min_len": int(getattr(cfg, "min_chunk_length", 40)), "before": before},
    )

    before = len(hits)
    hits = keyword_boost_hits(hits, q_text)
    trace.add("keyword_boost", count=len(hits))

    # На enumeration-запросах диверсификация мешает: именно дубли по doc_id
    # (разные чанки одного файла, где встречается имя) — ценность, а не шум.
    # А внизу при обрезке до k мы дадим всем документам-носителям entity шанс.
    #
    # Entity-anchor документы исключаем из диверсификации: если в CV нашли имя,
    # нельзя оставить ОДИН лучший чанк (например, с «Газпромбанк»), остальные
    # разделы документа (МФТИ, Тинькофф) потеряются.
    entity_anchor_docs_preview: set = {
        dv.document_id
        for dv, _ in hits
        if (dv.document_id, dv.chunk_index) in entity_keys and dv.document_id is not None
    }
    if (
        document_id is None 
        and hits 
        and resolved != "graph" 
        and not enumeration 
        and should_diversify_hits(
            hits,
            score_of=lambda h: chunk_cosine.get((h[0].document_id, h[0].chunk_index)),
        )
    ):
        pool = max(int(cfg.rerank_top_k or 20), k * 6, 56)
        # Решение принимаем по сырому cosine: hits здесь уже в шкале RRF,
        # а пороги внутри — косинусные (см. should_diversify_hits).
        hits = diversify_hits_by_document(
            hits,
            min(pool, len(hits)),
            keep_all_for_docs=entity_anchor_docs_preview | set(filename_doc_ids or []),
        )
        trace.add(
            "diversify_by_document",
            count=len(hits),
            details={"keep_all_for_docs": sorted(list(entity_anchor_docs_preview | set(filename_doc_ids or [])))},
        )

    if resolved == "graph" and hits:
        seed_pairs = [
            (dv.document_id, dv.chunk_index) for dv, _ in hits[: min(12, len(hits))] if dv.document_id is not None
        ]
        seed_chunk_indexes = [p[1] for p in seed_pairs]
        graph_scores: Dict[Tuple[int, int], float] = {}
        if graph_repo and seed_pairs:
            try:
                graph_scores = await graph_repo.expand_neighbors(
                    store_type=store,
                    document_id=document_id,
                    seed_chunk_indexes=seed_chunk_indexes,
                    max_hops=2,
                    max_nodes=max(k * 4, 40),
                    seed_doc_chunk_pairs=seed_pairs if document_id is None else None,
                )
            except Exception as e:
                trace.warn(f"graph_expand_failed: {e}")

        async def _fetch_graph_chunk(doc_id: int, chunk_idx: int):
            if vector_repo_for_window is None:
                return None
            fetch_one = getattr(vector_repo_for_window, "get_vector_by_document_and_chunk", None)
            if fetch_one is None:
                return None
            return await fetch_one(doc_id, chunk_idx)

        fused = await fuse_seed_and_graph_hits(
            hits,
            graph_scores,
            fetch_chunk=_fetch_graph_chunk,
            graph_weight=0.35,
            limit=max(k * 4, 40),
        )
        if fused:
            hits = fused
        trace.add(
            "graph_expand_rrf",
            count=len(hits),
            details={"graph_neighbors": len(graph_scores), "fusion": "weighted_RRF"},
        )

    used_rr = False
    if eff_rr and hits:
        try:
            with timer.stage("rerank"):
                # Не раздувать top_k до len(hits): на CPU LLM-реранкер
                # (MiniCPM) на 40–50 пассажах занимает минуты и рвёт
                # SVC_RAG_SEARCH_READ_TIMEOUT на бэкенде. Лимит — cfg.rerank_top_k.
                _rr_k = max(int(k), int(cfg.rerank_top_k or 20))
                hits, used_rr = await rerank_vector_hits_ex(
                    q_text,
                    hits,
                    rerank_client or rag_client,
                    top_k=_rr_k,
                    vector_weight=0.3,
                    model=rerank_model,
                )
            if used_rr:
                trace.add("cross_encoder_rerank", count=len(hits))
            else:
                trace.warn("rerank_skipped: реранкер недоступен")
        except Exception as e:
            trace.warn(f"rerank_failed: {e}")
    trace.used_rerank = used_rr

    sibling_added = 0
    sibling_keys: set = set()
    # Документы, которые пиним целиком (только filename-anchor!).
    pin_whole_doc_ids: set = set(filename_doc_ids or [])
    # Все документы, из которых тянем сиблинги — для трейса и учёта в финальном срезе.
    all_anchor_docs: set = set(pin_whole_doc_ids)
    entity_anchor_docs: set = set()
    if fetch_document_chunks is not None and (hits or all_anchor_docs):
        # 2) Entity-anchor — документы, где entity реально нашёлся.
        for dv, _ in hits:
            if (dv.document_id, dv.chunk_index) in entity_keys:
                entity_anchor_docs.add(dv.document_id)
        all_anchor_docs.update(entity_anchor_docs)
        # 3) Enumeration top-3 (только если entity не извлеклась — иначе entity уже
        # описывает, какие документы релевантны, и доп. top-N нам не нужен).
        enum_anchor_docs: set = set()
        if enumeration and hits and not entity_anchor_docs:
            top_docs_by_score: Dict[Any, float] = {}
            for dv, sc in hits:
                if dv.document_id not in top_docs_by_score or float(sc) > top_docs_by_score[dv.document_id]:
                    top_docs_by_score[dv.document_id] = float(sc)
            for doc_id, _ in sorted(top_docs_by_score.items(), key=lambda x: x[1], reverse=True)[:3]:
                enum_anchor_docs.add(doc_id)
        all_anchor_docs.update(enum_anchor_docs)

        if all_anchor_docs:
            existing_keys = {(dv.document_id, dv.chunk_index) for dv, _ in hits}
            base_sibling_score = min((float(sc) for _, sc in hits), default=0.01) * 0.5
            if base_sibling_score <= 0:
                base_sibling_score = 0.01
            entity_scores = [float(sc) for dv, sc in hits if (dv.document_id, dv.chunk_index) in entity_keys]
            avg_entity_score = sum(entity_scores) / len(entity_scores) if entity_scores else 0.1
            vector_support_threshold = max(
                float(getattr(cfg, "min_vector_similarity", 0.0) or 0.0),
                0.05,
            )

            def _has_vector_support(doc_id: Any) -> bool:
                return doc_vector_score.get(doc_id, 0.0) >= vector_support_threshold

            def _cap(doc_id: Any) -> int:
                if doc_id in pin_whole_doc_ids:
                    return 20
                if doc_id in entity_anchor_docs:
                    return 6 if _has_vector_support(doc_id) else 0
                if doc_id in enum_anchor_docs:
                    return 6
                return 0

            entity_sibling_score = max(avg_entity_score * 0.7, base_sibling_score)
            expanded: List[Tuple[DocumentVector, float]] = []
            for doc_id in all_anchor_docs:
                cap = _cap(doc_id)
                if cap <= 0:
                    continue
                try:
                    doc_chunks = await fetch_document_chunks(doc_id)
                except Exception as e:
                    trace.warn(f"fetch_document_chunks({doc_id})_failed: {e}")
                    continue
                if not doc_chunks:
                    continue
                entity_chunk_indices = {
                    int(getattr(dv, "chunk_index", 0) or 0)
                    for dv in doc_chunks
                    if (dv.document_id, dv.chunk_index) in entity_keys
                }

                def _priority(dv: DocumentVector) -> Tuple[int, int, int]:
                    ci = int(getattr(dv, "chunk_index", 0) or 0)
                    # (0) chunk_0 — всегда top priority.
                    if ci == 0:
                        return (0, 0, 0)
                    # (1) по близости к entity-чанкам (если есть).
                    if entity_chunk_indices:
                        prox = min(abs(ci - e) for e in entity_chunk_indices)
                    else:
                        # Для filename-anchor и enum-anchor без явных entity
                        # просто читаем документ по порядку.
                        prox = ci
                    return (1, prox, ci)

                doc_chunks_prioritized = sorted(doc_chunks, key=_priority)
                taken = 0
                sib_score = entity_sibling_score if doc_id in entity_anchor_docs else base_sibling_score
                for dv in doc_chunks_prioritized:
                    if taken >= cap:
                        break
                    key = (dv.document_id, dv.chunk_index)
                    if key in existing_keys:
                        continue
                    if doc_id in entity_anchor_docs and entity_chunk_indices:
                        ci = int(getattr(dv, "chunk_index", 0) or 0)
                        if ci != 0:
                            prox = min(abs(ci - e) for e in entity_chunk_indices)
                            if prox > 4:
                                break
                    expanded.append((dv, sib_score))
                    existing_keys.add(key)
                    sibling_keys.add(key)
                    taken += 1
            if expanded:
                hits = hits + expanded
                sibling_added = len(expanded)
    trace.add(
        "parent_document_expansion",
        count=sibling_added,
        details={
            "pin_whole_doc_ids": sorted(list(pin_whole_doc_ids)),
            "entity_anchor_docs": sorted(list(entity_anchor_docs)),
            "enum_anchor_docs": sorted(list(all_anchor_docs - pin_whole_doc_ids - entity_anchor_docs)),
        },
    )

    final_limit = k * 2 if enumeration else k
    if enumeration:
        entity_per_doc_cap = max(4, k // 2)
        entity_doc_cap = max(15, int(k * 1.5))
        entity_total_cap = entity_per_doc_cap * entity_doc_cap
    else:
        entity_per_doc_cap = 6
        entity_doc_cap = 8
        entity_total_cap = entity_per_doc_cap * entity_doc_cap
    filename_whole_cap = 20

    hits_sorted = hits
    has_pinning = bool(entity_keys or pin_whole_doc_ids)
    if has_pinning:
        anchor_chunks: List[Tuple[DocumentVector, float]] = []
        seen_keys: set = set()

        # --- 1. filename-anchor: весь указанный документ целиком (до 20 чанков) ---
        # Это override: пользователь назвал файл → отдаём его.
        filename_pool: List[Tuple[DocumentVector, float]] = []
        for dv, sc in hits_sorted:
            if dv.document_id in pin_whole_doc_ids:
                key = (dv.document_id, dv.chunk_index)
                if key not in seen_keys:
                    filename_pool.append((dv, float(sc)))
                    seen_keys.add(key)
        filename_pool.sort(
            key=lambda t: (
                int(t[0].document_id or 0),
                int(getattr(t[0], "chunk_index", 0) or 0),
            )
        )
        # Сохраняем по filename_whole_cap чанков на каждый документ, не больше.
        per_doc_count: Dict[Any, int] = {}
        for dv, sc in filename_pool:
            cnt = per_doc_count.get(dv.document_id, 0)
            if cnt < filename_whole_cap:
                anchor_chunks.append((dv, sc))
                per_doc_count[dv.document_id] = cnt + 1

        entity_and_sibling_keys = entity_keys | sibling_keys
        entity_candidates: List[Tuple[DocumentVector, float]] = []
        for dv, sc in hits_sorted:
            key = (dv.document_id, dv.chunk_index)
            if key in seen_keys:
                continue
            if key in entity_and_sibling_keys:
                entity_candidates.append((dv, float(sc)))

        def _ec_sort_key(item: Tuple[DocumentVector, float]) -> Tuple[int, float, int, int]:
            dv, sc = item
            ci = int(getattr(dv, "chunk_index", 0) or 0)
            key = (dv.document_id, dv.chunk_index)
            # chunk_0 entity-anchor/enum-anchor документа — top priority.
            # Для filename-pinned документов chunk_0 уже обрабатывается
            # в секции (1) filename-pool выше — здесь лишь entity/enum.
            is_head = (
                ci == 0 and dv.document_id is not None and dv.document_id in (entity_anchor_docs | enum_anchor_docs)
            )
            if is_head:
                group = 0
            elif key in entity_keys:
                group = 1
            else:
                group = 2
            return (group, -float(sc), int(dv.document_id or 0), ci)

        entity_candidates.sort(key=_ec_sort_key)

        entity_per_doc: Dict[Any, int] = {}
        entity_docs_used: set = set()
        entity_anchor_added = 0
        for dv, sc in entity_candidates:
            if entity_anchor_added >= entity_total_cap:
                break
            cnt = entity_per_doc.get(dv.document_id, 0)
            if cnt >= entity_per_doc_cap:
                continue
            if dv.document_id not in entity_docs_used:
                if len(entity_docs_used) >= entity_doc_cap:
                    continue
                entity_docs_used.add(dv.document_id)
            key = (dv.document_id, dv.chunk_index)
            anchor_chunks.append((dv, sc))
            seen_keys.add(key)
            entity_per_doc[dv.document_id] = cnt + 1
            entity_anchor_added += 1

        if anchor_chunks:
            doc_max_score: Dict[Any, float] = {}
            for dv, sc in anchor_chunks:
                doc_max_score[dv.document_id] = max(
                    doc_max_score.get(dv.document_id, float("-inf")),
                    float(sc),
                )
            anchor_chunks.sort(
                key=lambda t: (
                    # filename-anchor документы в топе (пользователь указал явно)
                    0 if t[0].document_id in pin_whole_doc_ids else 1,
                    # Затем — по семантической близости документа (vector cosine)
                    -doc_vector_score.get(t[0].document_id, 0.0),
                    # Tie-breaker: смешанный max score (вектор + keyword_boost)
                    -doc_max_score.get(t[0].document_id, 0.0),
                    int(t[0].document_id or 0),
                    int(getattr(t[0], "chunk_index", 0) or 0),
                )
            )

        others = [h for h in hits_sorted if (h[0].document_id, h[0].chunk_index) not in seen_keys]
        merged = anchor_chunks + others
        # Расширяем лимит ТОЛЬКО на capped pinned anchors (не на все сиблинги
        # и не на десятки ILIKE-хитов, как было раньше — там было 91 вместо 8).
        hits_sorted = merged[: max(final_limit, len(anchor_chunks))]
        trace.add(
            "entity_documents_pinned",
            count=len(anchor_chunks),
            details={
                "entity_chunks": sum(1 for dv, _ in anchor_chunks if (dv.document_id, dv.chunk_index) in entity_keys),
                "whole_doc_pin_chunks": sum(1 for dv, _ in anchor_chunks if dv.document_id in pin_whole_doc_ids),
                "documents": sorted(list({dv.document_id for dv, _ in anchor_chunks})),
                "caps": {
                    "mode": "enumeration" if enumeration else "entity",
                    "per_doc": entity_per_doc_cap,
                    "docs": entity_doc_cap,
                    "total": entity_total_cap,
                    "filename_whole": filename_whole_cap,
                },
            },
        )
    else:
        hits_sorted = hits_sorted[:final_limit]

    # hits_sorted уже ограничен выше с учётом cap'ов — не режем повторно.
    rows = [_row(dv, sc) for dv, sc in hits_sorted]
    final = await apply_rerank_min_and_window(
        vector_repo_for_window,
        rows,
        rerank_min_score=float(cfg.rerank_min_score or 0),
        sentence_window=int(cfg.sentence_window or 0),
        used_rerank=used_rr,
    )
    trace.add("post_rerank_window", count=len(final))

    await log_retrieval_with_eval(
        store=log_store_label or store,
        strategy_resolved=resolved,
        pipeline=trace.pipeline,
        extra_lines=[
            f"k={k}",
            f"document_id={'все' if document_id is None else document_id}",
            f"кандидатов_из_pgvector={fetch_lim}",
            f"реранк_cross_encoder={'да' if eff_rr else 'нет'}",
            f"реранк_применён={'да' if used_rr else 'нет'}",
        ],
        final_hits=final,
        k_requested=k,
        query_for_eval=q_text,
        search_started_perf=t0,
        gold_document_ids=eval_gold_document_ids,
        gold_chunks=eval_gold_chunks,
        llm_judge=eval_llm_judge,
    )
    trace.seconds = time.perf_counter() - t0
    return _done(final, trace)
