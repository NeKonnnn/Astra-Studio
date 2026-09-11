# Индексация документов и поиск: парсинг → чанки → эмбеддинги (RAG-MODELS) → pgvector, опционально BM25 и реранк
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.clients.rag_models_client import RagModelsClient
from app.clients.openai_models_compat_client import set_embed_log_context
from app.core.config import get_settings
from app.core.http_verify import resolve_httpx_verify
from app.database.models import Document, DocumentVector
from app.database.repository import DocumentRepository, VectorRepository
from app.database.search_filters import DocumentVectorSearchFilters
from app.services.bm25_index import InMemoryBm25Index, hybrid_combine_vector_bm25
from app.services.hit_postprocess import apply_rerank_min_and_window
from app.services.retrieval_eval import log_retrieval_with_eval
from app.services.rag_search_helpers import (
    diversify_hits_by_document,
    effective_use_reranking,
    filter_low_signal_chunks,
    filter_by_min_vector_similarity,
    fuse_seed_and_graph_hits,
    keyword_boost_hits,
    merge_vector_and_keyword_hits,
    resolve_auto_pipeline_strategy,
    should_diversify_hits,
    vector_fetch_limit,
)
from app.database.fts import extract_filenames, extract_proper_nouns
from app.database.graph_repository import GraphRepository
from app.services.chunker import split_into_chunks, split_into_chunks_with_meta
from app.services.document_parser import empty_document_index_error, parse_document
from app.services.hierarchical import DocumentSummarizer, OptimizedDocumentIndex
from app.services.retrieval_pipeline import _is_enumeration_query
from app.services.stage_timer import StageTimer
from app.core.logging import get_logger

logger = get_logger(__name__)


class RagService:
    def __init__(
        self,
        document_repo: DocumentRepository,
        vector_repo: VectorRepository,
        rag_models_client: RagModelsClient,
        graph_repo: Optional[GraphRepository] = None,
    ):
        self.document_repo = document_repo
        self.vector_repo = vector_repo
        self.rag_client = rag_models_client
        cfg = get_settings().rag
        self._cfg = cfg
        self.graph_repo = graph_repo
        self.graph_enabled: bool = bool(getattr(cfg, "enable_graph_rag", True))

        # BM25 / гибридный поиск (индекс нужен и для lexical, и для hybrid)
        self.use_hybrid_search: bool = cfg.use_hybrid_search
        self.hybrid_bm25_weight: float = cfg.hybrid_bm25_weight
        self._bm25 = InMemoryBm25Index(self.vector_repo.get_all_contents_for_bm25)

        # Иерархия: суммаризатор и оптимизированный индекс (при включённой настройке)
        self._summarizer: Optional[DocumentSummarizer] = None
        self._optimized_index: Optional[OptimizedDocumentIndex] = None
        if cfg.use_hierarchical_indexing:
            llm_cfg = get_settings().llm_service

            async def _llm_summarize(prompt: str) -> str:
                try:
                    logger.debug(
                        "[SVC-RAG→LLM] summarize: url=%s model=%s prompt_len=%s",
                        f"{llm_cfg.base_url.rstrip("/")}/v1/chat/completions",
                        llm_cfg.default_model,
                        len(prompt or ""),
                    )
                    async with httpx.AsyncClient(
                        timeout=llm_cfg.timeout, verify=resolve_httpx_verify()
                    ) as client:
                        r = await client.post(
                            f"{llm_cfg.base_url.rstrip('/')}/v1/chat/completions",
                            json={
                                "model": llm_cfg.default_model,
                                "messages": [{"role": "user", "content": prompt}],
                                "temperature": 0.3,
                                "max_tokens": 2000,
                                "stream": False,
                            },
                        )
                        r.raise_for_status()
                        data = r.json()
                        _content = (data.get("choices") or [{}])[0].get(
                            "message", {}
                        ).get("content", "") or ""
                        logger.debug(
                            "[SVC-RAG->LLM] summarize: ответ=%s символов", len(_content)
                        )
                        return _content
                except Exception as e:
                    logger.warning("LLM суммаризация не удалась: %s", e)
                    return ""

            self._summarizer = DocumentSummarizer(
                llm_function=_llm_summarize,
                max_chunk_size=cfg.hierarchical_chunk_size,
                chunk_overlap=cfg.hierarchical_chunk_overlap,
                intermediate_summary_chunks=cfg.intermediate_summary_chunks,
            )
            self._optimized_index = OptimizedDocumentIndex(self.rag_client, self.vector_repo)

    @property
    def bm25_index(self):
        """Совместимость с кодом, проверяющим наличие BM25-индекса."""
        return self._bm25.index

    @property
    def bm25_chunk_count(self) -> int:
        """Сколько чанков в индексе. Раньше отдавался их полный текст."""
        return self._bm25.chunk_count

    async def warm_up(self) -> None:
        """Прогрев сервиса на старте SVC-RAG.

        Собирает BM25-индекс один раз, чтобы первый lexical/hybrid/auto-запрос
        не висел на горячем построении индекса (особенно после рестарта).
        """
        try:
            await self._bm25.ensure_built()
            logger.info("[SVC-RAG] warm_up: BM25-индекс готов (chunks=%d)", len(self._bm25.chunk_count))
        except Exception as e:
            logger.warning("[SVC-RAG] warm_up: BM25 не построен: %s", e)

    async def _finalize_hit_rows(
        self,
        rows: List[Tuple[str, float, Optional[int], Optional[int]]],
        *,
        used_rerank: bool,
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        return await apply_rerank_min_and_window(
            self.vector_repo,
            rows,
            rerank_min_score=float(getattr(self._cfg, "rerank_min_score", 0) or 0),
            sentence_window=int(getattr(self._cfg, "sentence_window", 0) or 0),
            used_rerank=used_rerank,
        )

    async def index_document(
        self,
        file_data: bytes,
        filename: str,
        image_meta: Optional[Dict[str, Any]] = None,
        *,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Парсим файл, режем на чанки, получаем эмбеддинги из SVC-RAG-MODELS, пишем в БД.

        image_meta: опциональные данные о файле/MinIO:
            {
                "path": str | None,
                "minio_object": str | None,
                "minio_bucket": str | None,
            }
        Эти данные сохраняются в metadata документа (ключ image_info), чтобы backend
        мог восстановить информацию об изображении/объекте MinIO.
        """
        timer = StageTimer("INDEX", store="global", file=filename)
        with timer.stage("parse"):
            parsed = await parse_document(file_data, filename)
        if not parsed:
            timer.log(logger)
            return {"ok": False, "error": "Не удалось извлечь текст или формат не поддерживается", "document_id": None}

        text = (parsed.get("text") or "").strip()
        confidence_info = parsed.get("confidence_info")

        if not text:
            timer.log(logger)
            return {
                "ok": False,
                "error": empty_document_index_error(parsed, filename),
                "document_id": None,
            }

        use_hierarchical = (
            self._cfg.use_hierarchical_indexing
            and len(text) > self._cfg.hierarchical_threshold
            and self._summarizer is not None
            and self._optimized_index is not None
        )

        if use_hierarchical:
            summarizer_restore = None
            if self._summarizer is not None and (chunk_size is not None or chunk_overlap is not None):
                summarizer_restore = self._summarizer.apply_chunk_params(chunk_size, chunk_overlap)
            try:
                hierarchical_doc = await self._summarizer.create_hierarchical_summary_async(
                    text,
                    filename,
                    create_full_summary=self._cfg.create_full_summary_via_llm,
                )
            finally:
                if summarizer_restore is not None and self._summarizer is not None:
                    self._summarizer.restore_chunk_params(summarizer_restore)
            meta: Dict[str, Any] = {
                "chunks_count": hierarchical_doc["metadata"]["total_chunks"],
                "source": "svc-rag",
                "hierarchical": True,
            }
            if confidence_info:
                meta["confidence_data"] = confidence_info
            if image_meta:
                meta["image_info"] = {
                    "path": image_meta.get("path"),
                    "minio_object": image_meta.get("minio_object"),
                    "minio_bucket": image_meta.get("minio_bucket"),
                }
            doc = Document(
                filename=filename,
                content=text,
                metadata=meta,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            doc_id = await self.document_repo.create_document(doc)
            if not doc_id:
                return {"ok": False, "error": "Не удалось сохранить документ в БД", "document_id": None}
            # Контекст операции для CEF-логов чанков в PostgreSQL.
            set_embed_log_context(
                store="global",
                operation="upload",
                document_id=doc_id,
                filename=filename,
            )
            ok = await self._optimized_index.index_document_hierarchical_async(hierarchical_doc, doc_id)
            if not ok:
                await self.document_repo.delete_document(doc_id)
                return {"ok": False, "error": "Ошибка иерархической индексации", "document_id": None}
            self._bm25.mark_dirty()
            timer.meta["doc_id"] = doc_id
            timer.meta["chunks"] = hierarchical_doc["metadata"]["total_chunks"]
            timer.meta["mode"] = "hierarchical"
            timer.log(logger)
            return {
                "ok": True,
                "document_id": doc_id,
                "filename": filename,
                "chunks_count": hierarchical_doc["metadata"]["total_chunks"],
            }

        with timer.stage("chunk"):
            chunks_with_meta = split_into_chunks_with_meta(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if not chunks_with_meta:
            timer.log(logger)
            return {"ok": False, "error": "После разбиения чанков не осталось", "document_id": None}
        chunks = [c for c, _m in chunks_with_meta]

        metadata: Dict[str, Any] = {"chunks_count": len(chunks), "source": "svc-rag"}
        if confidence_info:
            metadata["confidence_data"] = confidence_info
        if image_meta:
            metadata["image_info"] = {
                "path": image_meta.get("path"),
                "minio_object": image_meta.get("minio_object"),
                "minio_bucket": image_meta.get("minio_bucket"),
            }

        doc = Document(
            filename=filename,
            content=text,
            metadata=metadata,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        with timer.stage("insert_document"):
            doc_id = await self.document_repo.create_document(doc)
        if not doc_id:
            timer.log(logger)
            return {"ok": False, "error": "Не удалось сохранить документ в БД", "document_id": None}
        # Контекст операции для CEF-логов чанков в PostgreSQL.
        set_embed_log_context(
            store="global",
            operation="upload",
            document_id=doc_id,
            filename=filename,
        )

        try:
            with timer.stage("embed"):
                embeddings = await self.rag_client.embed(chunks)
        except Exception as e:
            await self.document_repo.delete_document(doc_id)
            timer.log(logger)
            return {"ok": False, "error": f"Ошибка эмбеддингов: {e}", "document_id": None}

        if len(embeddings) != len(chunks):
            await self.document_repo.delete_document(doc_id)
            timer.log(logger)
            return {"ok": False, "error": "Число эмбеддингов не совпадает с числом чанков", "document_id": None}

        vectors = [
            DocumentVector(
                document_id=doc_id,
                chunk_index=i,
                embedding=emb,
                content=chunks_with_meta[i][0],
                metadata=dict(chunks_with_meta[i][1]),
            )
            for i, emb in enumerate(embeddings)
        ]
        with timer.stage("insert"):
            created = await self.vector_repo.create_vectors_batch(vectors)
        if self.graph_repo and self.graph_enabled:
            try:
                with timer.stage("graph"):
                    await self.graph_repo.rebuild_document_graph(
                        store_type="global",
                        document_id=doc_id,
                        chunks=[(v.chunk_index, v.content) for v in vectors],
                    )
            except Exception as e:
                logger.warning("Graph индекс не собран для документа %s: %s", doc_id, e)

        # После добавления документа помечаем BM25 индекс на пересборку
        with timer.stage("bm25"):
            self._bm25.mark_dirty()

        timer.meta["doc_id"] = doc_id
        timer.meta["chunks"] = created
        timer.log(logger)
        return {
            "ok": True,
            "document_id": doc_id,
            "filename": filename,
            "chunks_count": created,
        }

    async def search(
        self,
        query: str,
        k: int = 10,
        document_id: Optional[int] = None,
        use_reranking: Optional[bool] = None,
        strategy: Optional[str] = None,
        vector_query: Optional[str] = None,
        filters: Optional[DocumentVectorSearchFilters] = None,
        eval_gold_document_ids: Optional[List[int]] = None,
        eval_gold_chunks: Optional[List[Tuple[int, int]]] = None,
        eval_llm_judge: bool = False,
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        """
        Поиск: эмбеддинг запроса → векторный поиск → опционально гибрид с BM25 → опционально rerank.

        strategy:
        - "auto" (по умолчанию) - умный выбор пайплайна среди hierarchical / graph / hybrid / standard
          по доступности (BM25, граф, OptimizedDocumentIndex) и эвристикам запроса; RAG_AUTO_MODE=heuristic|priority.
          Реранк для auto остаётся с семантикой режима auto (как в effective_use_reranking).
        - "reranking" - векторный/гибридный/иерархический поиск + rerank.
        - "hierarchical" - умный поиск по иерархии (OptimizedDocumentIndex).
        - "hybrid" - векторный (cosine) + BM25 через weighted RRF по рангам; опционально cross-encoder rerank.
        - "standard" - чистый векторный поиск (pgvector cosine similarity), без BM25/FTS merge.
        - "raw_cosine" - сырой векторный поиск (pgvector cosine) без постобработки.
        - "graph" - seed retrieval + расширение по графу связей чанков.
        - "lexical" - только BM25 (sparse/keyword retrieval), без векторного поиска и без rerank.
        Также поддерживается "flat" как синоним "standard".

        Возвращает список (content, score, document_id, chunk_index).
        При rerank порядок задаёт cross-encoder; prior (cosine/RRF) — только tiebreak.
        """
        q_text = (query or "").strip()
        vq_text = (vector_query or "").strip()
        if not q_text and not vq_text:
            return []
        embed_source = vq_text or q_text
        t_search_start = time.perf_counter()

        user_strategy = (strategy or "auto").lower()
        logger.debug(
            "[SVC-RAG] search вход: strategy=%s, k=%s, document_id=%s, use_reranking=%s",
            user_strategy,
            k,
            document_id,
            use_reranking,
        )

        if user_strategy == "flat":
            user_strategy = "standard"
        if user_strategy in {"keyword", "bm25"}:
            user_strategy = "lexical"

        rerank_key = user_strategy
        if user_strategy == "auto":
            # Индекс может ещё не быть прогрет — hybrid всё равно доступен (построение lazy).
            hybrid_ok = bool(self.use_hybrid_search)
            graph_ok = bool(self.graph_repo and self.graph_enabled)
            hier_ok = self._optimized_index is not None
            picked = resolve_auto_pipeline_strategy(
                q_text,
                store="global",
                document_id=document_id,
                hierarchical_available=hier_ok,
                graph_available=graph_ok,
                hybrid_available=hybrid_ok,
            )
            user_strategy = picked
            rerank_key = "auto"
            logger.debug(
                "[SVC-RAG] global strategy=auto → %s (hybrid_ready=%s graph=%s hierarchical=%s)",
                picked,
                hybrid_ok,
                graph_ok,
                hier_ok,
            )

        # Явный иерархический поиск - отдельная ветка
        if user_strategy == "hierarchical" and self._optimized_index is None:
            logger.debug(
                "[SVC-RAG] search store=global strategy=hierarchical недоступен (optimized_index выключен/не создан) → обычный pgvector/BM25/rerank путь"
            )
        if user_strategy == "hierarchical" and self._optimized_index is not None:
            try:
                hits_h = await self._optimized_index.smart_search_async(q_text, k=k, search_strategy="auto")
                final_h = await self._finalize_hit_rows(list(hits_h), used_rerank=False)
                await log_retrieval_with_eval(
                    store="global (глобальные документы)",
                    strategy_resolved="hierarchical",
                    pipeline="OptimizedDocumentIndex.smart_search_async",
                    extra_lines=[f"k={k}", f"document_id={document_id}"],
                    final_hits=final_h,
                    k_requested=k,
                    query_for_eval=q_text,
                    search_started_perf=t_search_start,
                    gold_document_ids=eval_gold_document_ids,
                    gold_chunks=eval_gold_chunks,
                    llm_judge=eval_llm_judge,
                )
                return final_h
            except Exception as e:
                logger.warning("Иерархический поиск не удался, fallback на плоский: %s", e)
                user_strategy = "hybrid" if (self.use_hybrid_search and self._bm25.ready) else "standard"
                logger.debug("[SVC-RAG] после сбоя hierarchical используем pipeline=%s", user_strategy)

        if user_strategy == "graph":
            return await self._graph_search(
                q_text,
                k=k,
                document_id=document_id,
                use_reranking=use_reranking,
                vector_query=vq_text or None,
                filters=filters,
                search_started_perf=t_search_start,
                eval_gold_document_ids=eval_gold_document_ids,
                eval_gold_chunks=eval_gold_chunks,
                eval_llm_judge=eval_llm_judge,
            )

        if user_strategy == "lexical":
            bm25_rows = await self._bm25.search(q_text, max(k * 6, 48))
            if document_id is not None:
                bm25_rows = [row for row in bm25_rows if int(row[0]) == int(document_id)]
            if not bm25_rows:
                await log_retrieval_with_eval(
                    store="global (глобальные документы)",
                    strategy_resolved="lexical",
                    pipeline="bm25_only (пустая выдача)",
                    extra_lines=[f"k={k}", f"document_id={document_id}"],
                    final_hits=[],
                    k_requested=k,
                    query_for_eval=q_text,
                    search_started_perf=t_search_start,
                    gold_document_ids=eval_gold_document_ids,
                    gold_chunks=eval_gold_chunks,
                    llm_judge=eval_llm_judge,
                )
                return []
            max_bm25 = max((float(score) for _, _, score in bm25_rows), default=1.0) or 1.0
            out_lexical: List[Tuple[str, float, Optional[int], Optional[int]]] = []
            for doc_id_bm25, chunk_idx_bm25, score_bm25 in bm25_rows:
                vec = await self.vector_repo.get_vector_by_document_and_chunk(doc_id_bm25, chunk_idx_bm25)
                if not vec:
                    continue
                out_lexical.append(
                    (
                        vec.content,
                        float(score_bm25) / max_bm25,
                        vec.document_id,
                        vec.chunk_index,
                    )
                )
                if len(out_lexical) >= max(k * 3, 48):
                    break
            out_lexical = out_lexical[:k]
            final_lexical = await self._finalize_hit_rows(out_lexical, used_rerank=False)
            await log_retrieval_with_eval(
                store="global (глобальные документы)",
                strategy_resolved="lexical",
                pipeline="bm25_only",
                extra_lines=[f"k={k}", f"document_id={document_id}", "реранк_cross_encoder=нет"],
                final_hits=final_lexical,
                k_requested=k,
                query_for_eval=q_text,
                search_started_perf=t_search_start,
                gold_document_ids=eval_gold_document_ids,
                gold_chunks=eval_gold_chunks,
                llm_judge=eval_llm_judge,
            )
            return final_lexical

        cfg_rerank_enabled = self._cfg.use_reranking
        # standard = чистый cosine: без rerank. hybrid — rerank по конфигу.
        use_rerank = effective_use_reranking(
            use_reranking,
            cfg_rerank_enabled,
            "standard" if user_strategy == "standard" else rerank_key,
            query_text=q_text,
        )

        # Как в KB/project/memory: широкий пул из pgvector + диверсификация по document_id.
        # Иначе при rerank_top_k=20 весь пул забивают чанки 1–2 больших DOCX, а PDF (напр. CV) не попадает в реранк.
        # Для перечислительных запросов расширяем k×3, чтобы успеть собрать
        # кандидатов от всех документов, где есть entity.
        _fetch_k_seed = k * 3 if _is_enumeration_query(q_text) else k
        fetch_lim = vector_fetch_limit(
            _fetch_k_seed,
            graph=False,
            document_id=document_id,
            use_rerank=use_rerank,
            rerank_top_k=int(self._cfg.rerank_top_k or 20),
        )

        try:
            query_embedding = await self.rag_client.embed_single(embed_source)
        except Exception as e:
            logger.warning("Embed query failed: %s", e)
            await log_retrieval_with_eval(
                store="global (глобальные документы)",
                strategy_resolved=user_strategy,
                pipeline="(ошибка эмбеддинга запроса)",
                extra_lines=[f"k={k}"],
                final_hits=[],
                k_requested=k,
                query_for_eval=q_text,
                search_started_perf=t_search_start,
                gold_document_ids=eval_gold_document_ids,
                gold_chunks=eval_gold_chunks,
                llm_judge=eval_llm_judge,
            )
            return []

        pairs = await self.vector_repo.similarity_search(
            query_embedding,
            limit=fetch_lim,
            document_id=document_id,
            filters=filters,
        )
        # Честный cosine-скор ПО ДОКУМЕНТУ (до entity-lane / merge / boost).
        # Используется ниже как первый ключ сортировки anchor_chunks, чтобы
        # семантически близкие документы не вытеснялись «искусственно
        # накрученными» entity-filename попаданиями (где скор 0.5 задан
        # вручную). См. также retrieval_pipeline.py — аналогичная логика.
        doc_vector_score: Dict[Any, float] = {}
        for dv, sc in pairs:
            try:
                s = float(sc)
            except (TypeError, ValueError):
                continue
            did = dv.document_id
            if did is None:
                continue
            if s > doc_vector_score.get(did, float("-inf")):
                doc_vector_score[did] = s
        if not pairs:
            await log_retrieval_with_eval(
                store="global (глобальные документы)",
                strategy_resolved=user_strategy,
                pipeline="pgvector_cosine → нет кандидатов",
                extra_lines=[f"k={k}", f"лимит_pgvector={fetch_lim}", f"document_id={document_id}"],
                final_hits=[],
                k_requested=k,
                query_for_eval=q_text,
                search_started_perf=t_search_start,
                gold_document_ids=eval_gold_document_ids,
                gold_chunks=eval_gold_chunks,
                llm_judge=eval_llm_judge,
            )
            return []

        raw_mode = user_strategy == "raw_cosine"
        if raw_mode:
            out_raw = [(v.content, score, v.document_id, v.chunk_index) for v, score in pairs[:k]]
            await log_retrieval_with_eval(
                store="global (глобальные документы)",
                strategy_resolved="raw_cosine",
                pipeline="pgvector_cosine_similarity (raw, no_postprocess)",
                extra_lines=[f"k={k}", f"лимит_pgvector={fetch_lim}", f"document_id={document_id}"],
                final_hits=out_raw,
                k_requested=k,
                query_for_eval=q_text,
                search_started_perf=t_search_start,
                gold_document_ids=eval_gold_document_ids,
                gold_chunks=eval_gold_chunks,
                llm_judge=eval_llm_judge,
            )
            return out_raw

        # --- standard: чистый семантический поиск (cosine), без FTS/BM25 ---
        if user_strategy == "standard":
            min_sim_std = float(getattr(self._cfg, "min_vector_similarity", 0.0) or 0.0)
            pairs = filter_by_min_vector_similarity(pairs, min_sim_std, k)
            pairs = filter_low_signal_chunks(
                pairs,
                min_len=int(getattr(self._cfg, "min_chunk_length", 40)),
                rescue_keep=max(k * 3, 12),
            )
            if document_id is None and pairs and should_diversify_hits(pairs):
                pool_div = max(int(self._cfg.rerank_top_k or 20), k * 6, 56)
                pairs = diversify_hits_by_document(pairs, min(pool_div, len(pairs)))
            out_std = [(v.content, float(score), v.document_id, v.chunk_index) for v, score in pairs[:k]]
            final_std = await self._finalize_hit_rows(out_std, used_rerank=False)
            await log_retrieval_with_eval(
                store="global (глобальные документы)",
                strategy_resolved="standard",
                pipeline="pgvector_cosine_similarity (vector_only, no_fts_no_bm25)",
                extra_lines=[
                    f"k={k}",
                    f"лимит_векторов_pgvector={fetch_lim}",
                    f"document_id={document_id}",
                    "гибрид=выключен_для_этой_стратегии",
                    "реранк_cross_encoder=нет",
                ],
                final_hits=final_std,
                k_requested=k,
                query_for_eval=q_text,
                search_started_perf=t_search_start,
                gold_document_ids=eval_gold_document_ids,
                gold_chunks=eval_gold_chunks,
                llm_judge=eval_llm_judge,
            )
            return final_std

        # --- hybrid: vector cosine + BM25 через weighted RRF (без FTS merge) ---
        if user_strategy == "hybrid":
            # Cosine-порог применяем ТОЛЬКО к сырым vector-скорам ДО merge.
            # После RRF шкала уже не cosine — filter_by_min_vector_similarity
            # там некорректен и выкидывает BM25-only хиты.
            min_sim_h = float(getattr(self._cfg, "min_vector_similarity", 0.0) or 0.0)
            pairs = filter_by_min_vector_similarity(pairs, min_sim_h, k)
            pairs = filter_low_signal_chunks(
                pairs,
                min_len=int(getattr(self._cfg, "min_chunk_length", 40)),
                rescue_keep=max(k * 3, 12),
            )

            hybrid_applied = False
            if self.use_hybrid_search:
                hybrid_results = await hybrid_combine_vector_bm25(
                    q_text,
                    pairs,
                    k=fetch_lim,
                    bm25_index=self._bm25,
                    bm25_weight=self.hybrid_bm25_weight,
                    fetch_chunk=self.vector_repo.get_vector_by_document_and_chunk,
                    document_id=document_id,
                )
                if hybrid_results:
                    pairs = hybrid_results
                    hybrid_applied = True

            # diversify по RRF-скорам ок (сравниваем порядок, не абсолютную шкалу)
            if document_id is None and pairs and should_diversify_hits(pairs):
                pool_div = max(int(self._cfg.rerank_top_k or 20), k * 6, 56)
                pairs = diversify_hits_by_document(pairs, min(pool_div, len(pairs)))

            pipeline_desc = "pgvector_cosine_similarity + bm25_rrf_merge"
            if not hybrid_applied:
                pipeline_desc = "pgvector_cosine_similarity + bm25_skipped(no_index_or_disabled)"
            if use_rerank:
                pipeline_desc += " + cross_encoder_rerank"
            else:
                pipeline_desc += " (no_rerank)"

            used_rerank_h = False
            if use_rerank and len(pairs) > 1:
                passages = [v.content for v, _ in pairs]
                try:
                    reranked = await self.rag_client.rerank(q_text, passages, top_k=len(pairs))
                    out_h: List[Tuple[str, float, Optional[int], Optional[int]]] = []
                    # Порядок задаёт cross-encoder; prior (RRF) — только tiebreak.
                    prior_order = {
                        i: r
                        for r, (i, _) in enumerate(
                            sorted(enumerate(pairs), key=lambda x: float(x[1][1]), reverse=True)
                        )
                    }
                    n_p = max(len(pairs), 1)
                    for rr_rank, (idx, sc) in enumerate(reranked):
                        if idx < len(pairs):
                            v, _orig = pairs[idx]
                            prior_norm = 1.0 - (prior_order.get(idx, n_p - 1) / n_p)
                            final_score = float(sc) + 1e-6 * prior_norm - 1e-9 * rr_rank
                            out_h.append((v.content, final_score, v.document_id, v.chunk_index))
                    used_rerank_h = True
                except Exception as e:
                    logger.warning("[global] hybrid rerank failed: %s", e)
                    out_h = [(v.content, float(score), v.document_id, v.chunk_index) for v, score in pairs]
            else:
                out_h = [(v.content, float(score), v.document_id, v.chunk_index) for v, score in pairs]
            out_h = out_h[:k]
            final_h = await self._finalize_hit_rows(out_h, used_rerank=used_rerank_h)
            await log_retrieval_with_eval(
                store="global (глобальные документы)",
                strategy_resolved="hybrid",
                pipeline=pipeline_desc,
                extra_lines=[
                    f"k={k}",
                    f"лимит_векторов_pgvector={fetch_lim}",
                    f"гибрид_BM25_в_конфиге={self.use_hybrid_search}",
                    f"гибрид_применён={'да' if hybrid_applied else 'нет'}",
                    f"hybrid_bm25_weight={self.hybrid_bm25_weight}",
                    "fusion=weighted_RRF",
                    f"реранк_cross_encoder={'да' if used_rerank_h else 'нет'}",
                ],
                final_hits=final_h,
                k_requested=k,
                query_for_eval=q_text,
                search_started_perf=t_search_start,
                gold_document_ids=eval_gold_document_ids,
                gold_chunks=eval_gold_chunks,
                llm_judge=eval_llm_judge,
            )
            return final_h

        # --- Keyword FTS (OR) + Entity lane ---
        # Используется для auto / reranking / прочих режимов с постобработкой.
        # Те же принципы, что и в retrieval_pipeline.py для нон-global хранилищ:
        #  1. OR-семантика FTS уже внутри VectorRepository.keyword_search (fts.py).
        #  2. Entity lane — targeted FTS только по собственным именам/кодам,
        #     найденные чанки переживают фильтры (pinned).
        #  3. Enumeration-запросы получают более высокий keyword_weight и не диверсифицируются.
        # Filename anchor: запросы вида «саммари по X.docx» семантически не
        # совпадают с содержанием файла — резолвим имя в document_id и принудительно
        # поднимаем весь документ (см. _fetch_all_chunks ниже).
        filename_mentions: List[str] = extract_filenames(q_text) if q_text else []
        filename_doc_ids: List[int] = []
        if filename_mentions and hasattr(self.doc_repo, "find_document_ids_by_filename"):
            for fn in filename_mentions:
                try:
                    ids = await self.doc_repo.find_document_ids_by_filename(fn)
                except Exception as e:
                    logger.warning("[global] find_docs_by_filename(%r) failed: %s", fn, e)
                    continue
                if ids:
                    filename_doc_ids.extend(ids)
                else:
                    stem = fn.rsplit(".", 1)[0]
                    if stem and stem != fn:
                        try:
                            ids2 = await self.doc_repo.find_document_ids_by_filename(stem)
                            if ids2:
                                filename_doc_ids.extend(ids2)
                        except Exception:
                            pass
            seen_fid: set = set()
            filename_doc_ids = [d for d in filename_doc_ids if not (d in seen_fid or seen_fid.add(d))]
            if filename_doc_ids:
                logger.debug(
                    "[global] filename_anchor: %s → document_ids=%s",
                    filename_mentions,
                    filename_doc_ids,
                )

        # Имена файлов в запросе → включаем режим enumeration (широкий пул, без диверсификации).
        enumeration = _is_enumeration_query(q_text) or bool(filename_doc_ids)
        entity_tokens: List[str] = extract_proper_nouns(q_text) if q_text else []

        keyword_hits: List[Tuple[DocumentVector, float]] = []
        if q_text:
            try:
                keyword_hits = await self.vector_repo.keyword_search(
                    q_text,
                    limit=max(k * 6, 48),
                    document_id=document_id,
                    filters=filters,
                )
            except Exception as e:
                # Warning, чтобы тихие провалы keyword-поиска были видны в логах.
                logger.warning("keyword_search(global) failed: %s", e)

        entity_hits: List[Tuple[DocumentVector, float]] = []
        entity_keys: set = set()
        if entity_tokens and q_text:
            # (1) FTS по именам.
            try:
                fts_hits = await self.vector_repo.keyword_search(
                    " ".join(entity_tokens),
                    limit=max(k * 8, 64),
                    document_id=document_id,
                    filters=filters,
                )
                entity_hits.extend(fts_hits)
            except Exception as e:
                logger.warning("entity_lane(global) FTS failed: %s", e)

            # (2) ILIKE по сырым токенам — всегда, параллельно FTS. Подробный
            # разбор см. в retrieval_pipeline.py; коротко: морфологию русских
            # имён покрывает FTS (Postgres snowball), а ILIKE нужен только
            # для того, что snowball не знает — коды/латиница/редкие токены.
            lookup_tokens = list(dict.fromkeys([t for t in entity_tokens if t]))
            if lookup_tokens:
                try:
                    ilike_hits = await self.vector_repo.substring_search(
                        lookup_tokens, limit=max(k * 8, 64), document_id=document_id
                    )
                    existing = {(dv.document_id, dv.chunk_index) for dv, _ in entity_hits}
                    for dv, sc in ilike_hits:
                        if (dv.document_id, dv.chunk_index) not in existing:
                            entity_hits.append((dv, sc))
                            existing.add((dv.document_id, dv.chunk_index))
                    if ilike_hits:
                        logger.debug(
                            "[global] entity_lane ILIKE(raw): %s → %d чанков",
                            lookup_tokens,
                            len(ilike_hits),
                        )
                except Exception as e:
                    logger.warning("entity_ilike_raw(global) failed: %s", e)

            # (3) Entity-in-filename: имена героев часто зашиты в имя файла
            # («..._Некрасов_СК0050629.pdf»). Если у документа имя совпадает
            # с extracted entity — тянем первые 4 чанка как entity-хиты.
            if hasattr(self.doc_repo, "find_document_ids_by_filename") and hasattr(
                self.vector_repo, "get_vectors_by_document"
            ):
                filename_probe = [t for t in entity_tokens if len(t) >= 4 and not t.isdigit()]
                matched_docs: set = set()
                for tok in filename_probe:
                    try:
                        ids = await self.doc_repo.find_document_ids_by_filename(tok)
                        if ids:
                            matched_docs.update(ids)
                    except Exception as e:
                        logger.warning("entity_find_docs_by_filename(%r, global) failed: %s", tok, e)
                matched_docs_list = sorted(matched_docs)[:8]
                if matched_docs_list:
                    existing = {(dv.document_id, dv.chunk_index) for dv, _ in entity_hits}
                    added = 0
                    for doc_id in matched_docs_list:
                        try:
                            chunks = await self.vector_repo.get_vectors_by_document(doc_id)
                        except Exception as e:
                            logger.warning("get_vectors_by_document(%d, global) failed: %s", doc_id, e)
                            continue
                        if not chunks:
                            continue
                        chunks_sorted = sorted(chunks, key=lambda d: int(getattr(d, "chunk_index", 0) or 0))
                        for dv in chunks_sorted[:4]:
                            key = (dv.document_id, dv.chunk_index)
                            if key in existing:
                                continue
                            entity_hits.append((dv, 0.5))
                            existing.add(key)
                            added += 1
                    if added:
                        logger.debug(
                            "[global] entity_lane filename-match: %s → docs %s → +%d chunks",
                            entity_tokens,
                            matched_docs_list,
                            added,
                        )

            entity_keys = {(dv.document_id, dv.chunk_index) for dv, _ in entity_hits}

        if keyword_hits or entity_hits:
            combined_kw = list(keyword_hits) + list(entity_hits)
            kw_weight = 0.65 if (user_strategy == "standard" and not use_rerank) else 0.35
            if enumeration:
                kw_weight = max(kw_weight, 0.7)
            pairs = merge_vector_and_keyword_hits(pairs, combined_kw, keyword_weight=kw_weight)

        # Фильтрация явно нерелевантных чанков до реранка/гибрида.
        # Entity-хиты обходят min_sim / low_signal — без этого именно «Константина»
        # в глобальном хранилище тоже теряется.
        min_sim = float(getattr(self._cfg, "min_vector_similarity", 0.0) or 0.0)
        if entity_keys:
            pinned = [h for h in pairs if (h[0].document_id, h[0].chunk_index) in entity_keys]
            rest = [h for h in pairs if (h[0].document_id, h[0].chunk_index) not in entity_keys]
            rest = filter_by_min_vector_similarity(rest, min_sim, k)
            rest = filter_low_signal_chunks(
                rest,
                min_len=int(getattr(self._cfg, "min_chunk_length", 40)),
                rescue_keep=max(k * 3, 12),
            )
            pairs = pinned + rest
        else:
            pairs = filter_by_min_vector_similarity(pairs, min_sim, k)
            pairs = filter_low_signal_chunks(
                pairs,
                min_len=int(getattr(self._cfg, "min_chunk_length", 40)),
                rescue_keep=max(k * 3, 12),
            )
        pairs = keyword_boost_hits(pairs, q_text)

        # Entity-anchor документы исключаем из diversify, иначе при cap=1/doc
        # у CV останется только чанк с «Газпромбанк», а «МФТИ» и «Тинькофф»
        # из соседних чанков потеряются, даже если документ попал в entity-hits.
        entity_anchor_docs_preview = {
            v.document_id
            for v, _ in pairs
            if (v.document_id, v.chunk_index) in entity_keys and v.document_id is not None
        }
        if document_id is None and pairs and not enumeration and should_diversify_hits(pairs):
            pool_div = max(int(self._cfg.rerank_top_k or 20), k * 6, 56)
            pairs = diversify_hits_by_document(
                pairs,
                min(pool_div, len(pairs)),
                keep_all_for_docs=entity_anchor_docs_preview | set(filename_doc_ids or []),
            )

        pipeline_desc = "pgvector_cosine_similarity + keyword_fts_merge"
        if document_id is not None:
            pipeline_desc += " (doc_scoped)"
        if use_rerank:
            pipeline_desc += " + cross_encoder_rerank"
        else:
            pipeline_desc += " (no_rerank)"

        report_extras = [
            f"k={k}",
            f"лимит_векторов_pgvector={fetch_lim}",
            "гибрид_BM25=не_в_этой_ветке(auto/reranking_fts)",
            f"реранк_cross_encoder={'да' if use_rerank else 'нет'}",
        ]

        final_limit = k * 2 if enumeration else k

        # Parent-document expansion — см. подробный комментарий в retrieval_pipeline.py.
        # Короткая версия:
        #   * filename-anchor (пользователь назвал файл) — override: 20 чанков документа целиком, пиним.
        #   * entity-anchor (имя встретилось в документе) — мягкий сигнал: 2 соседних
        #     с entity чанка, НЕ пиним весь документ (иначе «magnet»-эффект: CV
        #     сосёт все запросы, где упомянут Константин, даже если вопрос про другой файл).
        #   * enumeration без entity — top-3 документа, по 4 чанка, без пининга всего.
        pin_whole_doc_ids: set = set(filename_doc_ids or [])
        entity_anchor_doc_ids: set = set()
        enum_anchor_doc_ids: set = set()
        sibling_rows_by_key: Dict[Tuple[Any, Any], Tuple[str, float, Optional[int], Optional[int]]] = {}
        if entity_keys:
            for v, _ in pairs:
                if (v.document_id, v.chunk_index) in entity_keys:
                    entity_anchor_doc_ids.add(v.document_id)
        if enumeration and pairs and not entity_anchor_doc_ids:
            seen_doc_best: Dict[Any, float] = {}
            for v, sc in pairs:
                if v.document_id not in seen_doc_best or float(sc) > seen_doc_best[v.document_id]:
                    seen_doc_best[v.document_id] = float(sc)
            for doc_id, _ in sorted(seen_doc_best.items(), key=lambda x: x[1], reverse=True)[:3]:
                enum_anchor_doc_ids.add(doc_id)
        all_anchor_doc_ids: set = pin_whole_doc_ids | entity_anchor_doc_ids | enum_anchor_doc_ids

        if all_anchor_doc_ids:
            existing = {(v.document_id, v.chunk_index) for v, _ in pairs}
            base_sib_score = min((float(sc) for _, sc in pairs), default=0.01) * 0.5
            if base_sib_score <= 0:
                base_sib_score = 0.01
            # Средний score entity-чанков — используем как базу для siblings
            # entity-anchor документов (чтобы сиблинги разделов типа «Тинькофф»
            # не тонули в финальной сортировке).
            entity_scores_global = [float(sc) for v, sc in pairs if (v.document_id, v.chunk_index) in entity_keys]
            avg_entity_score_global = (
                sum(entity_scores_global) / len(entity_scores_global) if entity_scores_global else 0.1
            )
            entity_sib_score = max(avg_entity_score_global * 0.7, base_sib_score)

            # Порог «документ реально семантически похож на запрос».
            # Если документ попал в entity_anchor_doc_ids только через FTS/ILIKE
            # совпадение имени в библиографии/сноске/мусорной строке, его
            # реальный vector_score низкий. Расширять такой документ сиблингами
            # — значит тянуть шум, который выдавит релевантные документы из
            # LLM-бюджета. Подробный разбор см. retrieval_pipeline.py.
            vector_support_threshold_g = max(
                float(getattr(self._cfg, "min_vector_similarity", 0.0) or 0.0),
                0.05,
            )

            def _has_vector_support_g(doc_id: Any) -> bool:
                return doc_vector_score.get(doc_id, 0.0) >= vector_support_threshold_g

            # Универсальные капы:
            #   filename: до 20 чанков (пользователь явно назвал файл, vector
            #     support не требуется).
            #   entity-anchor С vector-supp: 6 чанков (chunk_0 + entity + ±4).
            #   entity-anchor БЕЗ vector-supp: 0 сиблингов (шумовой документ —
            #     имя встретилось в библиографии, а сам документ семантически
            #     не про запрос).
            #   enum-anchor: 6 чанков.
            def _cap_global(doc_id: Any) -> int:
                if doc_id in pin_whole_doc_ids:
                    return 20
                if doc_id in entity_anchor_doc_ids:
                    return 6 if _has_vector_support_g(doc_id) else 0
                if doc_id in enum_anchor_doc_ids:
                    return 6
                return 0

            for doc_id in all_anchor_doc_ids:
                cap = _cap_global(doc_id)
                if cap <= 0:
                    continue
                try:
                    doc_chunks = await self.vector_repo.get_vectors_by_document(doc_id)
                except Exception as e:
                    logger.warning("[global] parent_expansion get_vectors_by_document(%s): %s", doc_id, e)
                    continue
                if not doc_chunks:
                    continue
                entity_chunk_indices = {
                    int(getattr(v, "chunk_index", 0) or 0)
                    for v in doc_chunks
                    if (v.document_id, v.chunk_index) in entity_keys
                }

                # Универсальный приоритет чанков внутри документа:
                #   (0) chunk_index == 0 — всегда первым (заголовочный якорь).
                #   (1) близость к entity-чанкам (entity → proximity).
                #   (2) тай-брейк по chunk_index.
                def _priority(dv: Any) -> Tuple[int, int, int]:
                    ci = int(getattr(dv, "chunk_index", 0) or 0)
                    if ci == 0:
                        return (0, 0, 0)
                    if entity_chunk_indices:
                        prox = min(abs(ci - e) for e in entity_chunk_indices)
                    else:
                        prox = ci
                    return (1, prox, ci)

                doc_chunks_prioritized = sorted(doc_chunks, key=_priority)
                taken = 0
                sib_score = entity_sib_score if doc_id in entity_anchor_doc_ids else base_sib_score
                for dv in doc_chunks_prioritized:
                    if taken >= cap:
                        break
                    key = (dv.document_id, dv.chunk_index)
                    if key in existing:
                        continue
                    # Для entity-anchor: обрыв за пределами proximity±4
                    # (chunk_0 проходит мимо этой проверки — его prox=0).
                    if doc_id in entity_anchor_doc_ids and entity_chunk_indices:
                        ci = int(getattr(dv, "chunk_index", 0) or 0)
                        if ci != 0:
                            prox = min(abs(ci - e) for e in entity_chunk_indices)
                            if prox > 4:
                                break
                    sibling_rows_by_key[key] = (dv.content, sib_score, dv.document_id, dv.chunk_index)
                    pairs.append((dv, sib_score))
                    existing.add(key)
                    taken += 1
            if sibling_rows_by_key:
                logger.debug(
                    "[global] parent-expansion: +%s sibling-чанков " "(pin_whole=%s, entity_anchor=%s, enum_anchor=%s)",
                    len(sibling_rows_by_key),
                    sorted(list(pin_whole_doc_ids)),
                    sorted(list(entity_anchor_doc_ids)),
                    sorted(list(enum_anchor_doc_ids)),
                )

        # Cap'ы на pinned anchors — защита от «magnet»-эффекта, когда один
        # популярный документ (xlsx со ста упоминаниями имени) забивает промпт
        # LLM десятками чанков и вытесняет другие файлы. Подробный разбор см.
        # в retrieval_pipeline.py, секция «Финальный срез».
        if enumeration:
            entity_per_doc_cap = max(4, k // 2)
            entity_doc_cap = max(15, int(k * 1.5))
        else:
            # 6 чанков/doc: для коротких entity-документов (CV, справка)
            # это покрывает весь файл. Было 4 — отрезало заголовок
            # «АО «Газпромбанк»» в CV, см. retrieval_pipeline.py.
            entity_per_doc_cap = 6
            entity_doc_cap = 8
        entity_total_cap = entity_per_doc_cap * entity_doc_cap
        filename_whole_cap = 20
        sibling_keys_global = set(sibling_rows_by_key.keys())
        entity_and_sibling_keys = entity_keys | sibling_keys_global

        def _cut_with_entity_pin(
            rows: List[Tuple[str, float, Optional[int], Optional[int]]],
        ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
            """Срезает до ``final_limit`` и пиннит anchor-чанки с учётом cap'ов:

              * filename-anchor документ — до ``filename_whole_cap`` чанков целиком;
              * entity-anchor — до ``entity_per_doc_cap`` чанков × ``entity_doc_cap``
                документов (итого не более ``entity_total_cap`` pinned), выбор по
                убыванию score — так в top уходят самые релевантные чанки, а не
                «все подряд по порядку документов».

            Entity-anchor siblings и enum siblings в пининг НЕ попадают — они просто
            лежат в общей куче с пониженным score и пробиваются по общему ранжированию.
            """
            if not rows:
                return []
            if not entity_keys and not pin_whole_doc_ids:
                return rows[:final_limit]

            anchor_rows: List[Tuple[str, float, Optional[int], Optional[int]]] = []
            seen: set = set()

            # 1. filename-anchor: весь явно названный документ (до filename_whole_cap).
            filename_pool: List[Tuple[str, float, Optional[int], Optional[int]]] = []
            for row in rows:
                _, _, doc_id, chunk_idx = row
                if doc_id in pin_whole_doc_ids:
                    key = (doc_id, chunk_idx)
                    if key not in seen:
                        filename_pool.append(row)
                        seen.add(key)
            filename_pool.sort(key=lambda r: (int(r[2] or 0), int(r[3] or 0)))
            per_doc_cnt: Dict[Any, int] = {}
            for row in filename_pool:
                cnt = per_doc_cnt.get(row[2], 0)
                if cnt < filename_whole_cap:
                    anchor_rows.append(row)
                    per_doc_cnt[row[2]] = cnt + 1

            # 2. entity-anchor: отбор с приоритетом структурной значимости.
            # Включаем entity-чанки И siblings (chunk_0 + соседи). Сортировка:
            #   группа 0: chunk_0 entity/enum-anchor документов (заголовок);
            #   группа 1: entity-чанки (прямые попадания имени);
            #   группа 2: siblings (добор по proximity).
            # Такая сортировка универсальна для любого документа — не нужны
            # пороги по длине. См. retrieval_pipeline.py.
            entity_cands: List[Tuple[str, float, Optional[int], Optional[int]]] = []
            for row in rows:
                _, _, doc_id, chunk_idx = row
                key = (doc_id, chunk_idx)
                if key in seen:
                    continue
                if key in entity_and_sibling_keys:
                    entity_cands.append(row)

            def _ec_key(row: Tuple[str, float, Optional[int], Optional[int]]) -> Tuple[int, float, int, int]:
                _, sc, d_id, c_idx = row
                ci = int(c_idx or 0)
                key = (d_id, c_idx)
                is_head = ci == 0 and d_id is not None and d_id in (entity_anchor_doc_ids | enum_anchor_doc_ids)
                if is_head:
                    group = 0
                elif key in entity_keys:
                    group = 1
                else:
                    group = 2
                return (group, -float(sc), int(d_id or 0), ci)

            entity_cands.sort(key=_ec_key)

            entity_per_doc: Dict[Any, int] = {}
            entity_docs_used: set = set()
            entity_added = 0
            for row in entity_cands:
                if entity_added >= entity_total_cap:
                    break
                doc_id = row[2]
                cnt = entity_per_doc.get(doc_id, 0)
                if cnt >= entity_per_doc_cap:
                    continue
                if doc_id not in entity_docs_used:
                    if len(entity_docs_used) >= entity_doc_cap:
                        continue
                    entity_docs_used.add(doc_id)
                anchor_rows.append(row)
                seen.add((doc_id, row[3]))
                entity_per_doc[doc_id] = cnt + 1
                entity_added += 1

            # Порядок anchor-чанков.
            #
            # ФИКС: первый ключ сортировки — doc_vector_score (чистый cosine
            # документа из исходного pgvector-поиска, до entity/filename magic).
            # Это единственный честный сигнал семантической близости документа
            # к запросу. Документ с artificial entity-filename score=0.5
            # больше не выдавливает вперёд реально релевантный документ с
            # vector_score=0.13.
            if anchor_rows:
                doc_max: Dict[Any, float] = {}
                for row in anchor_rows:
                    doc_max[row[2]] = max(doc_max.get(row[2], float("-inf")), float(row[1]))
                anchor_rows.sort(
                    key=lambda r: (
                        0 if r[2] in pin_whole_doc_ids else 1,
                        -doc_vector_score.get(r[2], 0.0),
                        -doc_max.get(r[2], 0.0),
                        int(r[2] or 0),
                        int(r[3] or 0),
                    )
                )

            others = [r for r in rows if (r[2], r[3]) not in seen]
            merged = anchor_rows + others
            return merged[: max(final_limit, len(anchor_rows))]

        if use_rerank and len(pairs) > 1:
            passages = [v.content for v, _ in pairs]
            try:
                # top_k=len(pairs) — полный ранжированный список; порядок = cross-encoder.
                reranked = await self.rag_client.rerank(q_text, passages, top_k=len(pairs))
                prior_order = {
                    i: r
                    for r, (i, _) in enumerate(
                        sorted(enumerate(pairs), key=lambda x: float(x[1][1]), reverse=True)
                    )
                }
                n_p = max(len(pairs), 1)
                out = []
                for rr_rank, (idx, sc) in enumerate(reranked):
                    if idx < len(pairs):
                        v, _orig = pairs[idx]
                        prior_norm = 1.0 - (prior_order.get(idx, n_p - 1) / n_p)
                        final_score = float(sc) + 1e-6 * prior_norm - 1e-9 * rr_rank
                        out.append((v.content, final_score, v.document_id, v.chunk_index))
                logger.debug("[SVC-RAG] search store=global rerank=ok hits=%s", len(out))
                final_out = await self._finalize_hit_rows(_cut_with_entity_pin(out), used_rerank=True)
                await log_retrieval_with_eval(
                    store="global (глобальные документы)",
                    strategy_resolved=user_strategy,
                    pipeline=pipeline_desc,
                    extra_lines=report_extras + ["реранк_статус=успех"],
                    final_hits=final_out,
                    k_requested=k,
                    query_for_eval=q_text,
                    search_started_perf=t_search_start,
                    gold_document_ids=eval_gold_document_ids,
                    gold_chunks=eval_gold_chunks,
                    llm_judge=eval_llm_judge,
                )
                return final_out
            except Exception as e:
                logger.warning("Rerank failed, using vector order: %s", e)

        out_pairs_all = [(v.content, score, v.document_id, v.chunk_index) for v, score in pairs]
        out_pairs = _cut_with_entity_pin(out_pairs_all)
        logger.debug(
            "[SVC-RAG] search store=global done hits=%s (final order без rerank или после сбоя rerank)", len(out_pairs)
        )
        final_pairs = await self._finalize_hit_rows(out_pairs, used_rerank=False)
        await log_retrieval_with_eval(
            store="global (глобальные документы)",
            strategy_resolved=user_strategy,
            pipeline=pipeline_desc,
            extra_lines=report_extras + ["реранк_статус=нет_или_сбой"],
            final_hits=final_pairs,
            k_requested=k,
            query_for_eval=q_text,
            search_started_perf=t_search_start,
            gold_document_ids=eval_gold_document_ids,
            gold_chunks=eval_gold_chunks,
            llm_judge=eval_llm_judge,
        )
        return final_pairs

    async def _graph_search(
        self,
        query: str,
        k: int = 10,
        document_id: Optional[int] = None,
        use_reranking: Optional[bool] = None,
        vector_query: Optional[str] = None,
        filters: Optional[DocumentVectorSearchFilters] = None,
        *,
        search_started_perf: float,
        eval_gold_document_ids: Optional[List[int]] = None,
        eval_gold_chunks: Optional[List[Tuple[int, int]]] = None,
        eval_llm_judge: bool = False,
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        """Graph RAG: seed retrieval -> expansion по графу -> optional rerank."""
        q_text = (query or "").strip()
        embed_source = (vector_query or "").strip() or q_text
        pipeline_base = "pgvector_cosine_seeds → graph_expand_neighbors → (опционально) cross_encoder_rerank"
        graph_extras = [
            f"k={k}",
            f"document_id={document_id}",
            f"seed_limit={max(k * 4, 36)}",
        ]
        try:
            query_embedding = await self.rag_client.embed_single(embed_source)
        except Exception as e:
            logger.warning("Embed query failed for graph search: %s", e)
            await log_retrieval_with_eval(
                store="global (глобальные документы)",
                strategy_resolved="graph",
                pipeline=f"{pipeline_base} (ошибка эмбеддинга запроса)",
                extra_lines=graph_extras,
                final_hits=[],
                k_requested=k,
                query_for_eval=q_text,
                search_started_perf=search_started_perf,
                gold_document_ids=eval_gold_document_ids,
                gold_chunks=eval_gold_chunks,
                llm_judge=eval_llm_judge,
            )
            return []

        seed_limit = max(k * 4, 36)
        pairs = await self.vector_repo.similarity_search(
            query_embedding,
            limit=seed_limit,
            document_id=document_id,
            filters=filters,
        )
        if not pairs:
            await log_retrieval_with_eval(
                store="global (глобальные документы)",
                strategy_resolved="graph",
                pipeline=f"{pipeline_base} → нет seed-кандидатов",
                extra_lines=graph_extras,
                final_hits=[],
                k_requested=k,
                query_for_eval=q_text,
                search_started_perf=search_started_perf,
                gold_document_ids=eval_gold_document_ids,
                gold_chunks=eval_gold_chunks,
                llm_judge=eval_llm_judge,
            )
            return []

        seed_pairs = [
            (v.document_id, v.chunk_index) for v, _ in pairs[: min(12, len(pairs))] if v.document_id is not None
        ]
        seed_chunk_indexes = [p[1] for p in seed_pairs]
        graph_scores: Dict[Tuple[int, int], float] = {}
        if self.graph_repo and self.graph_enabled:
            try:
                graph_scores = await self.graph_repo.expand_neighbors(
                    store_type="global",
                    document_id=document_id,
                    seed_chunk_indexes=seed_chunk_indexes,
                    max_hops=2,
                    max_nodes=max(k * 4, 40),
                    seed_doc_chunk_pairs=seed_pairs if document_id is None else None,
                )
            except Exception as e:
                logger.warning("Graph expand failed, fallback to seeds: %s", e)

        # Weighted RRF seed↔graph + подтягивание соседей, которых нет в seed-пуле.
        scored = await fuse_seed_and_graph_hits(
            pairs,
            graph_scores,
            fetch_chunk=self.vector_repo.get_vector_by_document_and_chunk,
            graph_weight=0.35,
            limit=max(k * 4, 40),
        )
        if not scored:
            scored = list(pairs)

        if (use_reranking if use_reranking is not None else self._cfg.use_reranking) and len(scored) > 1:
            n_take = min(len(scored), max(k * 2, 12))
            subset = scored[:n_take]
            passages = [v.content for v, _ in subset]
            try:
                reranked = await self.rag_client.rerank(q_text, passages, top_k=k)
                out: List[Tuple[str, float, Optional[int], Optional[int]]] = []
                prior_order = {
                    i: r
                    for r, (i, _) in enumerate(
                        sorted(enumerate(subset), key=lambda x: float(x[1][1]), reverse=True)
                    )
                }
                n_p = max(len(subset), 1)
                for rr_rank, (idx, sc) in enumerate(reranked):
                    if idx < len(subset):
                        v, _gm = subset[idx]
                        prior_norm = 1.0 - (prior_order.get(idx, n_p - 1) / n_p)
                        final_score = float(sc) + 1e-6 * prior_norm - 1e-9 * rr_rank
                        out.append((v.content, final_score, v.document_id, v.chunk_index))
                if out:
                    logger.debug(
                        "[SVC-RAG] search store=global strategy=graph done hits=%s rerank=ok seeds=%s graph_nodes=%s",
                        len(out[:k]),
                        len(seed_chunk_indexes),
                        len(graph_scores),
                    )
                    final_g = await self._finalize_hit_rows(out[:k], used_rerank=True)
                    await log_retrieval_with_eval(
                        store="global (глобальные документы)",
                        strategy_resolved="graph",
                        pipeline=pipeline_base,
                        extra_lines=graph_extras
                        + [
                            f"seeds={len(seed_chunk_indexes)} graph_nodes={len(graph_scores)}",
                            "реранк_статус=успех",
                        ],
                        final_hits=final_g,
                        k_requested=k,
                        query_for_eval=q_text,
                        search_started_perf=search_started_perf,
                        gold_document_ids=eval_gold_document_ids,
                        gold_chunks=eval_gold_chunks,
                        llm_judge=eval_llm_judge,
                    )
                    return final_g
            except Exception as e:
                logger.warning("Graph rerank failed: %s", e)

        out_final = [(v.content, score, v.document_id, v.chunk_index) for v, score in scored[:k]]
        logger.debug(
            "[SVC-RAG] search store=global strategy=graph done hits=%s rerank=no seeds=%s graph_nodes=%s",
            len(out_final),
            len(seed_chunk_indexes),
            len(graph_scores),
        )
        final_nf = await self._finalize_hit_rows(out_final, used_rerank=False)
        await log_retrieval_with_eval(
            store="global (глобальные документы)",
            strategy_resolved="graph",
            pipeline=pipeline_base,
            extra_lines=graph_extras
            + [
                f"seeds={len(seed_chunk_indexes)} graph_nodes={len(graph_scores)}",
                "реранк_статус=нет_или_сбой",
            ],
            final_hits=final_nf,
            k_requested=k,
            query_for_eval=q_text,
            search_started_perf=search_started_perf,
            gold_document_ids=eval_gold_document_ids,
            gold_chunks=eval_gold_chunks,
            llm_judge=eval_llm_judge,
        )
        return final_nf

    async def delete_document(self, document_id: int) -> bool:
        if self.graph_repo and self.graph_enabled:
            try:
                await self.graph_repo.delete_document_graph("global", document_id)
            except Exception:
                pass
        # Контекст операции для CEF-логов чанков в PostgreSQL.
        set_embed_log_context(store="global", operation="delete", document_id=document_id)
        await self.vector_repo.delete_vectors_by_document(document_id)
        await self.document_repo.delete_document(document_id)
        self._bm25.mark_dirty()
        return True

    async def list_documents(self) -> List[Dict[str, Any]]:
        docs = await self.document_repo.get_all_documents()
        return [
            {"id": d.id, "filename": d.filename, "created_at": d.created_at.isoformat() if d.created_at else None}
            for d in docs
        ]

    async def delete_document_by_filename(self, filename: str) -> bool:
        """Удаление документа по имени файла (аналог remove_document в backend, но без MinIO)."""
        doc = await self.document_repo.get_document_by_filename(filename)
        if not doc or doc.id is None:
            return False
        await self.delete_document(doc.id)
        return True

    async def get_document_chunks(
        self,
        document_id: int,
        start: int = 0,
        limit: int = 3,
    ) -> List[Tuple[str, int, int]]:
        """
        Получить чанки документа по порядку (например, начало документа с оглавлением).
        Возвращает список (content, document_id, chunk_index).
        """
        vectors = await self.vector_repo.get_vectors_by_document(document_id)
        if not vectors:
            return []
        # Сначала чанки с неотрицательным индексом (начало документа), затем специальные (-2, -1)
        vectors = sorted(vectors, key=lambda v: (1 if v.chunk_index < 0 else 0, v.chunk_index))
        selected = vectors[start : start + limit]
        return [(v.content, v.document_id, v.chunk_index) for v in selected]

    async def get_image_minio_info(self, filename: str) -> Optional[Dict[str, Any]]:
        """Вернуть информацию о MinIO/пути для изображения по имени файла."""
        doc = await self.document_repo.get_document_by_filename(filename)
        if not doc:
            return None
        meta = doc.metadata or {}
        image_info = meta.get("image_info") or {}
        if not isinstance(image_info, dict):
            return None
        return {
            "minio_object": image_info.get("minio_object"),
            "minio_bucket": image_info.get("minio_bucket"),
            "path": image_info.get("path"),
        }

    async def get_confidence_report(self) -> Dict[str, Any]:
        """Агрегированный отчёт об уверенности (аналог get_confidence_report_data в backend)."""
        docs = await self.document_repo.get_all_documents()
        confidence_map: Dict[str, Dict[str, Any]] = {}
        for d in docs:
            meta = d.metadata or {}
            cd = meta.get("confidence_data")
            if cd:
                confidence_map[d.filename] = cd

        if not confidence_map:
            return {
                "total_documents": 0,
                "documents": [],
                "average_confidence": 0.0,
                "overall_confidence": 0.0,
                "total_words": 0,
                "formatted_texts": [],
            }

        documents_info: List[Dict[str, Any]] = []
        formatted_texts: List[Dict[str, Any]] = []
        total_confidence = 0.0
        total_weighted_confidence = 0.0
        total_words = 0

        for filename, info in confidence_map.items():
            words = info.get("words", []) or []

            # Форматируем текст с процентами над словами
            formatted_lines: List[str] = []
            current_line: List[str] = []

            for word_info in words:
                word = word_info.get("word", "")
                conf = float(word_info.get("confidence", 0.0))
                if not word:
                    continue
                formatted_word = f"{conf:.0f}%\n{word}"
                current_line.append(formatted_word)
                if len(current_line) >= 10:
                    formatted_lines.append("  ".join(current_line))
                    current_line = []

            if current_line:
                formatted_lines.append("  ".join(current_line))

            formatted_text = "\n".join(formatted_lines)

            # Средняя уверенность по документу
            doc_avg_confidence = float(info.get("confidence", 0.0))
            if words:
                doc_avg_confidence = sum(float(w.get("confidence", 0.0)) for w in words) / len(words)

            documents_info.append(
                {
                    "filename": filename,
                    "confidence": doc_avg_confidence,
                    "text_length": int(info.get("text_length", 0)),
                    "file_type": info.get("file_type", "unknown"),
                    "words_count": len(words),
                }
            )

            formatted_texts.append(
                {
                    "filename": filename,
                    "formatted_text": formatted_text,
                    "words": words,
                }
            )

            total_confidence += doc_avg_confidence
            if words:
                total_weighted_confidence += sum(float(w.get("confidence", 0.0)) for w in words)
                total_words += len(words)

        avg_confidence = total_confidence / len(documents_info) if documents_info else 0.0
        overall_confidence = total_weighted_confidence / total_words if total_words > 0 else avg_confidence

        return {
            "total_documents": len(documents_info),
            "documents": documents_info,
            "average_confidence": avg_confidence,
            "overall_confidence": overall_confidence,
            "total_words": total_words,
            "formatted_texts": formatted_texts,
        }
