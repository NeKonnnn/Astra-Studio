# RAG-сервис для файлов проектов: project_rag_documents + project_rag_vectors
# Каждый документ привязан к project_id; при удалении проекта всё чистится через delete_by_project.
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
import json

from app.clients.openai_models_compat_client import set_embed_log_context
from app.clients.rag_models_client import RagModelsClient
from app.core.config import get_settings
from app.core.logging import get_logger
from app.database.search_filters import DocumentVectorSearchFilters
from app.database.project_rag_repository import (
    ProjectRagDocumentRepository,
    ProjectRagVectorRepository,
)
from app.database.models import Document, DocumentVector
from app.database.graph_repository import GraphRepository
from app.services.bm25_index import InMemoryBm25Index, Bm25IndexCache
from app.services.chunker import (
    normalize_chunking_strategy,
    resolve_chunk_params,
    split_into_chunks_with_meta,
)
from app.services.document_parser import empty_document_index_error, parse_document
from app.services.retrieval_pipeline import RetrievalTrace, run_retrieval_pipeline
from app.services.hierarchical_indexing import index_document_hierarchically
from app.services.stage_timer import StageTimer

logger = get_logger(__name__)


def _doc_actors(doc):
    """(owner, uploader, filename) из документа. Принимает и объект, и dict."""
    if isinstance(doc, dict):
        meta = doc.get("metadata")
        name = doc.get("filename") or doc.get("name") or "?"
    else:
        meta = getattr(doc, "metadata", None)
        name = getattr(doc, "filename", None) or "?"
    if isinstance(meta, str):
        try:
            meta = json.loads(meta or "{}")
        except (TypeError, ValueError):
            meta = {}
    meta = meta or {}
    owner = str(meta.get("owner_user_id") or "-").strip() or "-"
    uploader = str(meta.get("uploaded_by") or "-").strip() or "-"
    return owner, uploader, name


def bump_project_reindex_generation(key: str = "*") -> int:
    """Новое поколение реиндекса проектов — сигнал проходу ЭТОГО ключа прерваться.

    Ключ — конкретный проект (``project:<pid>``, ``owner:<uid>``) либо ``*`` для
    кластерного прогона. Раньше счётчик был один на сервис, и пересборка одного
    проекта прерывала пересборку другого на полпути.
    """
    from app.services.reindex_queue import project_queue

    return project_queue.bump(key)

def current_project_reindex_generation(key: str = "*") -> int:
    from app.services.reindex_queue import project_queue

    return project_queue.current(key)

def project_generation_is_current(key: str, generation: Optional[int]) -> bool:
    """Актуален ли проход. ``generation=None`` — прерывание не запрашивалось."""
    from app.services.reindex_queue import project_queue

    return project_queue.is_current(key, generation)


class ProjectRagService:
    def __init__(
        self,
        doc_repo: ProjectRagDocumentRepository,
        vector_repo: ProjectRagVectorRepository,
        rag_models_client: RagModelsClient,
        graph_repo: Optional[GraphRepository] = None,
    ):
        self.doc_repo = doc_repo
        self.vector_repo = vector_repo
        self.rag_client = rag_models_client
        self.graph_repo = graph_repo
        # Кэш с вытеснением, а не обычный словарь: сервис - синглтон на процесс
        # Размер - RAG_BM25_INDEX_CACHE_SIZE.
        self._bm25_by_key = Bm25IndexCache(get_settings().rag.bm25_retain_seconds)

    async def _route(self, model=None, provider=None):
        """Профиль эмбеддинга (клиент + имя модели + dim) и репозиторий нужной таблицы.

        Без model/provider вернутся ровно то, что было до фазы B3.
        """
        from app.services.embed_routing import resolve_for

        return await resolve_for(self.rag_client, self.vector_repo, provider, model)

    async def _rebuild_graph_for_document(self, document_id: int, vector_repo=None) -> None:
        repo = vector_repo or self.vector_repo
        if not self.graph_repo:
            return
        try:
            chunks = await repo.get_vectors_by_document(document_id)
            if chunks:
                await self.graph_repo.rebuild_document_graph(
                    store_type="project",
                    document_id=document_id,
                    chunks=[(v.chunk_index, v.content) for v in chunks],
                )
        except Exception as e:
            logger.warning("project graph индекс не пересобран для документа %s: %s", document_id, e)

    def _bm25_for_project(self, project_id: str, repo=None) -> InMemoryBm25Index:
        repo = repo or self.vector_repo
        dim = int(getattr(repo, "embedding_dim", 0) or 0)
        key = (project_id, dim)
        
        def _make() -> InMemoryBm25Index:
            async def _fetch():
                return await repo.get_all_contents_for_bm25(project_id=project_id)

            if not get_settings().rag.bm25_store_enabled:
                return InMemoryBm25Index(_fetch)

            from app.database.bm25_store import cache_key

            store_key = cache_key("project", str(project_id), dim)

            async def _load():
                return await repo.bm25_store_load(store_key, project_id=project_id)

            async def _save(fingerprint, payload, chunk_count):
                await repo.bm25_store_save(
                    store_key, fingerprint, payload, chunk_count
                )

            return InMemoryBm25Index(
                _fetch, load_from_store=_load, save_to_store=_save
            )

        return self._bm25_by_key.get_or_create(key, _make)

    def _mark_bm25_dirty(self, project_id: Optional[str] = None) -> None:
        if project_id:
            for (pid, _dim), idx in self._bm25_by_key.items():
                if pid == project_id:
                    idx.mark_dirty()
            return
        for idx in self._bm25_by_key.values():
            idx.mark_dirty()

    async def index_document(
        self,
        file_data: bytes,
        filename: str,
        project_id: str,
        minio_object: Optional[str] = None,
        minio_bucket: Optional[str] = None,
        *,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        uploaded_by: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        timer = StageTimer("INDEX", store="project", file=filename, project_id=project_id)
        with timer.stage("parse"):
            parsed = await parse_document(file_data, filename)
        if not parsed:
            timer.log(logger)
            return {
                "ok": False,
                "error": "Не удалось извлечь текст или формат не поддерживается",
                "document_id": None,
            }

        text = parsed.get("text", "")
        if not text.strip():
            timer.log(logger)
            return {"ok": False, "error": empty_document_index_error(parsed, filename), "document_id": None}

        # Модель выбираем ДО создания документа: иначе при ошибке сохранения
        # в БД останется документ без единого вектора.
        try:
            with timer.stage("route_embed_model"):
                prof, repo = await self._route(model, provider)
        except Exception as e:
            logger.error(
                "[INDEX project] не удалось выбрать модель (provider=%s model=%s): %s",
                provider,
                model,
                e,
            )
            timer.log(logger)
            return {
                "ok": False,
                "error": f"Модель эмбеддинга: {e}",
                "document_id": None,
            }

        meta: Dict[str, Any] = {
            "file_type": parsed.get("file_type", ""),
            "pages": parsed.get("pages", 0),
            "size": len(file_data),
            "source": "project",
            "project_id": project_id,
        }
        if minio_object:
            meta["minio_object"] = minio_object
        if minio_bucket:
            meta["minio_bucket"] = minio_bucket
        if owner_user_id:
            meta["owner_user_id"] = str(owner_user_id).strip().lower()
        if uploaded_by:
            meta["uploaded_by"] = str(uploaded_by).strip().lower()

        doc = Document(
            filename=filename,
            content=text,
            metadata=meta,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        with timer.stage("insert_document"):
            doc_id = await self.doc_repo.create_document(project_id, doc)
        if doc_id is None:
            timer.log(logger)
            return {"ok": False, "error": "Ошибка сохранения документа в БД", "document_id": None}
        
        # Пометка для логов гардрейла: клиент видит только список строк, имя
        # файла и id документа до него не доезжают. Ставим до ветвления, чтобы
        # покрыть и иерархическую индексацию, и обычную.
        set_embed_log_context(
            store="project",
            operation="upload",
            project_id=project_id,
            document_id=doc_id,
            filename=filename,
        )
        if (chunking_strategy or "").strip().lower() == "hierarchical":
            try:
                with timer.stage("hierarchical_embed_insert"):
                    count = await index_document_hierarchically(
                        text,
                        doc_id,
                        filename=filename,
                        vector_repo=repo,
                        rag_client=prof.client,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        model=prof.model,
                    )
            except Exception as e:
                logger.error("project_rag иерархическая индексация не удалась: %s", e)
                await self.doc_repo.delete_document(doc_id)
                timer.log(logger)
                return {
                    "ok": False,
                    "error": f"Иерархическая индексация: {e}",
                    "document_id": None,
                }
            with timer.stage("bm25"):
                self._mark_bm25_dirty(project_id)
            with timer.stage("graph"):
                await self._rebuild_graph_for_document(doc_id, vector_repo=repo)
            eff_size, eff_overlap = resolve_chunk_params(chunk_size, chunk_overlap)
            logger.info(
                "[INDEX project] '%s' (project=%s, id=%s, owner=%s, uploader=%s): strategy=hierarchical size=%s overlap=%s "
                "символов=%s чанков=%s embed=%s",
                filename,
                project_id,
                doc_id,
                owner_user_id or "-",
                uploaded_by or "-",
                eff_size,
                eff_overlap,
                len(text),
                count,
                prof.label,
            )
            timer.meta["doc_id"] = doc_id
            timer.meta["chunks"] = count
            timer.log(logger)
            return {
                "ok": True,
                "document_id": doc_id,
                "filename": filename,
                "chunks_count": count,
                "project_id": project_id,
            }

        with timer.stage("chunk"):
            chunks_with_meta = split_into_chunks_with_meta(
                text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                chunking_strategy=chunking_strategy,
            )
        if not chunks_with_meta:
            await self.doc_repo.delete_document(doc_id)
            timer.log(logger)
            return {"ok": False, "error": "Не удалось нарезать чанки", "document_id": None}
        chunks = [c for c, _m in chunks_with_meta]

        try:
            with timer.stage("embed"):
                embeddings = await prof.embed(chunks, kind="document")
        except Exception as e:
            logger.error("Ошибка эмбеддингов project_rag: %s", e)
            await self.doc_repo.delete_document(doc_id)
            timer.log(logger)
            return {"ok": False, "error": f"Ошибка эмбеддингов: {e}", "document_id": None}

        vectors = []
        for idx, ((chunk, cmeta), embedding) in enumerate(zip(chunks_with_meta, embeddings)):
            vmeta = {"chunk_index": idx, "document_filename": filename}
            vmeta.update(cmeta)
            vectors.append(
                DocumentVector(
                    document_id=doc_id,
                    chunk_index=idx,
                    embedding=embedding,
                    content=chunk,
                    metadata=vmeta,
                )
            )

        with timer.stage("insert"):
            created = await repo.create_vectors_batch(vectors)
        with timer.stage("bm25"):
            self._mark_bm25_dirty(project_id)
        if self.graph_repo:
            try:
                with timer.stage("graph"):
                    await self.graph_repo.rebuild_document_graph(
                        store_type="project",
                        document_id=doc_id,
                        chunks=[(v.chunk_index, v.content) for v in vectors],
                    )
            except Exception as e:
                logger.warning("project graph индекс не собран для документа %s: %s", doc_id, e)
        eff_size, eff_overlap = resolve_chunk_params(chunk_size, chunk_overlap)
        logger.info(
            "[INDEX project] '%s' (project=%s, id=%s, owner=%s, uploader=%s): strategy=%s size=%s overlap=%s "
            "символов=%s чанков=%s embed=%s",
            filename,
            project_id,
            doc_id,
            owner_user_id or "-",
            uploaded_by or "-",
            normalize_chunking_strategy(chunking_strategy),
            eff_size,
            eff_overlap,
            len(text),
            created,
            prof.label,
        )
        timer.meta["doc_id"] = doc_id
        timer.meta["chunks"] = created
        timer.log(logger)
        return {
            "ok": True,
            "document_id": doc_id,
            "filename": filename,
            "chunks_count": created,
            "project_id": project_id,
        }

    async def reindex_document(
        self,
        document: dict,
        *,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        route=None,
    ) -> int:
        """Заново нарезать один документ проекта (dict с content) и заменить вектора.

        route — уже разрешённая пара (профиль, репозиторий): reindex_all передаёт её,
        чтобы не резолвить модель на каждый документ.
        """
        document_id = document["id"]
        project_id = document.get("project_id")
        filename = document.get("filename") or "unknown"
        text = document.get("content") or ""
        if not text.strip():
            return 0
        prof, repo = route if route else await self._route(model, provider)
        # Удаление обходит ВСЕ таблицы (B2b) — при смене модели старые вектора
        # лежат в таблице прежней размерности и должны уйти.
        # Пометка для логов гардрейла: клиент видит только список строк, имя
        # файла и id документа до него не доезжают. Ставим до ветвления, чтобы
        # покрыть и иерархическую индексацию, и обычную.
        set_embed_log_context(
            store="project",
            operation="reindex",
            project_id=project_id,
            document_id=document_id,
            filename=filename,
        )
        await repo.delete_vectors_by_document(document_id)
        strategy = (chunking_strategy or "universal").strip().lower()
        if strategy == "hierarchical":
            from app.services.hierarchical_indexing import index_document_hierarchically

            count = await index_document_hierarchically(
                text,
                document_id,
                filename=filename,
                vector_repo=repo,
                rag_client=prof.client,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                model=prof.model,
            )
            self._mark_bm25_dirty(project_id)
            await self._rebuild_graph_for_document(document_id, vector_repo=repo)
            return count
        chunks_with_meta = split_into_chunks_with_meta(
            text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunking_strategy=strategy,
        )
        chunks = [c for c, _m in chunks_with_meta]
        if not chunks:
            return 0
        embeddings = await prof.embed(chunks, kind="document")
        vectors = []
        for idx, ((chunk, cmeta), embedding) in enumerate(zip(chunks_with_meta, embeddings)):
            vmeta = {"chunk_index": idx, "document_filename": filename}
            vmeta.update(cmeta)
            vectors.append(
                DocumentVector(
                    document_id=document_id,
                    chunk_index=idx,
                    embedding=embedding,
                    content=chunk,
                    metadata=vmeta,
                )
            )
        created = await repo.create_vectors_batch(vectors)
        self._mark_bm25_dirty(project_id)
        if self.graph_repo:
            try:
                await self.graph_repo.rebuild_document_graph(
                    store_type="project",
                    document_id=document_id,
                    chunks=[(v.chunk_index, v.content) for v in vectors],
                )
            except Exception as e:
                logger.warning("project graph индекс не собран для документа %s: %s", document_id, e)
        return created

    async def reindex_all(
        self,
        project_id: str,
        *,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        generation: Optional[int] = None,
        generation_key: str = "*",
        model: Optional[str] = None,
        provider: Optional[str] = None,
        route=None,
    ) -> Dict[str, Any]:
        """Перечанкировать все документы одного проекта."""
        docs = await self.doc_repo.get_documents_by_project(project_id)
        prof, repo = route if route else await self._route(model, provider)
        _eff_size, _eff_overlap = resolve_chunk_params(chunk_size, chunk_overlap)
        logger.info(
            "[REINDEX project=%s] старт: документов=%s strategy=%s size=%s overlap=%s embed=%s",
            project_id,
            len(docs),
            normalize_chunking_strategy(chunking_strategy),
            _eff_size,
            _eff_overlap,
            prof.label,
        )
        n_docs = 0
        n_chunks = 0
        n_errors = 0
        for d in docs:
            if not project_generation_is_current(generation_key, generation):
                logger.info(
                    "[REINDEX project] %s прерван: начат новый реиндекс этой же сущности (gen %s→%s)",
                    generation_key,
                    generation,
                    current_project_reindex_generation(generation_key),
                )
                break
            try:
                c = await self.reindex_document(
                    d,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    chunking_strategy=chunking_strategy,
                    route=(prof, repo),
                )
                n_docs += 1
                n_chunks += c
                logger.debug(
                    "[REINDEX project=%s] '%s' (id=%s, owner=%s, uploader=%s) чанков=%s",
                    project_id,
                    _doc_actors(d)[2],
                    d.get("id"),
                    _doc_actors(d)[0],
                    _doc_actors(d)[1],
                    c,
                )
            except Exception as e:
                n_errors += 1
                logger.error(
                    "[REINDEX project=%s] '%s' (id=%s, owner=%s, uploader=%s) ошибка: %s",
                    project_id,
                    _doc_actors(d)[2],
                    d.get("id"),
                    _doc_actors(d)[0],
                    _doc_actors(d)[1],
                    e,
                )
        logger.info(
            "[REINDEX project=%s] готово: документов=%s чанков=%s ошибок=%s",
            project_id,
            n_docs,
            n_chunks,
            n_errors,
        )
        return {"project_id": project_id, "documents": n_docs, "chunks": n_chunks, "errors": n_errors}

    async def reindex_all_projects(
        self,
        *,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        generation: Optional[int] = None,
        generation_key: str = "*",
        owner_user_id: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Перечанкировать проекты.

        С owner_user_id — только документы этого пользователя (metadata.owner_user_id).
        Без фильтра — все проекты (админский/кластерный путь после смены dim).
        """
        # Модель резолвим один раз на весь проход — иначе проба на каждый документ.
        prof, repo = await self._route(model, provider)
        if owner_user_id:
            docs = await self.doc_repo.get_documents_by_owner(owner_user_id)
            _eff_size, _eff_overlap = resolve_chunk_params(chunk_size, chunk_overlap)
            logger.info(
                "[REINDEX project OWNER] старт: owner=%s документов=%s strategy=%s size=%s overlap=%s embed=%s",
                owner_user_id,
                len(docs),
                normalize_chunking_strategy(chunking_strategy),
                _eff_size,
                _eff_overlap,
                prof.label,
            )
            total_docs = 0
            total_chunks = 0
            total_errors = 0
            seen_projects: set = set()
            for d in docs:
                if not project_generation_is_current(generation_key, generation):
                    logger.info(
                        "[REINDEX project OWNER] %s прерван: начат новый реиндекс этой же сущности (gen %s→%s)",
                        generation_key,
                        generation,
                        current_project_reindex_generation(generation_key),
                    )
                    break
                try:
                    c = await self.reindex_document(
                        d,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        chunking_strategy=chunking_strategy,
                        route=(prof, repo),
                    )
                    total_docs += 1
                    total_chunks += c
                    pid = d.get("project_id")
                    if pid:
                        seen_projects.add(pid)
                    logger.debug(
                        "[REINDEX project owner=%s] '%s' doc=%s чанков=%s",
                        owner_user_id,
                        _doc_actors(d)[2],
                        d.get("id"),
                        c,
                    )
                except Exception as e:
                    total_errors += 1
                    logger.error(
                        "[REINDEX project owner=%s] '%s' doc=%s ошибка: %s",
                        owner_user_id,
                        _doc_actors(d)[2],
                        d.get("id"),
                        e,
                    )
            logger.info(
                "[REINDEX project OWNER] owner=%s проектов=%s документов=%s чанков=%s ошибок=%s",
                owner_user_id,
                len(seen_projects),
                total_docs,
                total_chunks,
                total_errors,
            )
            return {
                "projects": len(seen_projects),
                "documents": total_docs,
                "chunks": total_chunks,
                "owner_user_id": owner_user_id,
            }

        project_ids = await self.doc_repo.get_all_project_ids()
        total_docs = 0
        total_chunks = 0
        for pid in project_ids:
            if not project_generation_is_current(generation_key, generation):
                logger.info(
                    "[REINDEX project ALL] %s прерван: начат новый реиндекс этой же сущности (gen %s→%s)",
                    generation_key,
                    generation,
                    current_project_reindex_generation(generation_key),
                )
                break
            try:
                r = await self.reindex_all(
                    pid,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    chunking_strategy=chunking_strategy,
                    generation=generation,
                    generation_key=generation_key,
                    route=(prof, repo),
                )
                total_docs += r["documents"]
                total_chunks += r["chunks"]
            except Exception as e:
                logger.error("[REINDEX project ALL] проект %s упал, пропускаем: %s", pid, e)
        logger.info(
            "[REINDEX project ALL] проектов=%s документов=%s чанков=%s",
            len(project_ids),
            total_docs,
            total_chunks,
        )
        return {
            "projects": len(project_ids),
            "documents": total_docs,
            "chunks": total_chunks,
        }

    async def search(
        self,
        query: str,
        project_id: str,
        k: int = 8,
        document_id: Optional[int] = None,
        use_reranking: Optional[bool] = None,
        strategy: Optional[str] = None,
        vector_query: Optional[str] = None,
        filters: Optional[DocumentVectorSearchFilters] = None,
        eval_gold_document_ids: Optional[List[int]] = None,
        eval_gold_chunks: Optional[List[Tuple[int, int]]] = None,
        eval_llm_judge: bool = False,
        return_trace: bool = False,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        rerank_model: Optional[str] = None,
        rerank_provider: Optional[str] = None,
    ) -> Union[
        List[Tuple[str, float, Optional[int], Optional[int]]],
        Tuple[List[Tuple[str, float, Optional[int], Optional[int]]], RetrievalTrace],
    ]:
        cfg = get_settings().rag
        # Запрос считаем моделью пользователя и ищем в таблице ЕЁ размерности.
        prof, repo = await self._route(model, provider)
        from app.services.embed_routing import rerank_client_for

        rr_client = rerank_client_for(self.rag_client, rerank_provider, rerank_model)

        async def _vectors(emb, lim):
            return await repo.similarity_search(
                query_embedding=emb,
                limit=lim,
                project_id=project_id,
                document_id=document_id,
                filters=filters,
            )

        async def _keywords(text, lim):
            return await repo.keyword_search(
                text,
                limit=lim,
                project_id=project_id,
                document_id=document_id,
                filters=filters,
            )

        async def _substring(tokens, lim):
            return await repo.substring_search(
                tokens,
                limit=lim,
                project_id=project_id,
                document_id=document_id,
            )

        async def _fetch_doc(doc_id: int):
            return await repo.get_vectors_by_document(doc_id)

        async def _find_docs_by_filename(name: str):
            # В проектных RAG запросы всегда скоупятся к project_id —
            # иначе саммари по одному файлу могло бы «склеиваться» с тем же
            # именем из другого проекта.
            return await self.doc_repo.find_document_ids_by_filename(name, project_id=project_id)

        hits, trace = await run_retrieval_pipeline(
            store="project",
            query=query,
            vector_query=vector_query,
            k=k,
            document_id=document_id,
            document_ids=None,
            use_reranking=use_reranking,
            strategy=strategy,
            filters=filters,
            rag_client=prof.client,
            embed_model=prof.model,
            rerank_client=rr_client,
            rerank_model=rerank_model,
            graph_repo=self.graph_repo,
            cfg=cfg,
            search_vectors=_vectors,
            search_keywords=_keywords,
            substring_search=_substring,
            fetch_document_chunks=_fetch_doc,
            find_docs_by_filename=_find_docs_by_filename,
            vector_repo_for_window=repo,
            bm25_index=self._bm25_for_project(project_id, repo),
            eval_gold_document_ids=eval_gold_document_ids,
            eval_gold_chunks=eval_gold_chunks,
            eval_llm_judge=eval_llm_judge,
            log_store_label=f"project_rag (project_id={project_id})",
        )
        return (hits, trace) if return_trace else hits

    async def list_documents(self, project_id: str) -> List[Dict[str, Any]]:
        docs = await self.doc_repo.get_documents_by_project(project_id)
        return [
            {
                "id": d["id"],
                "filename": d["filename"],
                "metadata": d["metadata"],
                "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
                "size": (d["metadata"] or {}).get("size", 0),
                "file_type": (d["metadata"] or {}).get("file_type", ""),
                "project_id": d["project_id"],
            }
            for d in docs
        ]

    async def get_document_chunks(
        self,
        project_id: str,
        document_id: int,
        start: int = 0,
        limit: int = 3,
    ) -> Optional[List[Tuple[str, int, int]]]:
        """Чанки документа проекта по порядку (начало — для оглавления/структуры).

        Возвращает None, если документа нет или он чужого проекта;
        иначе список (content, document_id, chunk_index).
        """
        doc = await self.doc_repo.get_document(document_id)
        if not doc:
            return None
        if str(doc.get("project_id") or "") != str(project_id or ""):
            return None
        vectors = await self.vector_repo.get_vectors_by_document(document_id)
        if not vectors:
            return []
        vectors = sorted(
            vectors, key=lambda v: (1 if v.chunk_index < 0 else 0, v.chunk_index)
        )
        selected = vectors[start : start + limit]
        return [(v.content, v.document_id, v.chunk_index) for v in selected]

    async def delete_document(self, document_id: int) -> Dict[str, Any]:
        """Удаляет документ; возвращает minio-ключи для очистки бэкендом."""
        doc = await self.doc_repo.get_document(document_id)
        if not doc:
            return {"ok": False, "error": "not_found"}
        meta = doc["metadata"] or {}
        if self.graph_repo:
            try:
                await self.graph_repo.delete_document_graph("project", document_id)
            except Exception:
                pass
        # Для CEF INT005/INT006 по чанкам ставим контекст ДО удаления векторов.
        set_embed_log_context(
            store="project",
            operation="delete",
            project_id=meta.get("project_id") or doc.get("project_id"),
            document_id=document_id,
            filename=doc.get("filename"),
        )
        await self.vector_repo.delete_vectors_by_document(document_id)
        await self.doc_repo.delete_document(document_id)
        self._mark_bm25_dirty(str(meta.get("project_id") or "") or None)
        logger.info("project_rag: удалён документ id=%s", document_id)
        return {
            "ok": True,
            "document_id": document_id,
            "minio_object": meta.get("minio_object"),
            "minio_bucket": meta.get("minio_bucket"),
        }

    async def delete_by_project(self, project_id: str) -> Dict[str, Any]:
        """
        Удаляет все документы и векторы проекта.
        Перед вызовом нужно получить список minio-ключей, чтобы удалить файлы из MinIO.
        """
        docs = await self.doc_repo.get_documents_by_project(project_id)
        minio_keys = [
            {
                "minio_object": (d["metadata"] or {}).get("minio_object"),
                "minio_bucket": (d["metadata"] or {}).get("minio_bucket"),
            }
            for d in docs
            if (d["metadata"] or {}).get("minio_object")
        ]
        deleted_count = await self.doc_repo.delete_documents_by_project(project_id)
        # Проекта больше нет - индексы не помечаем грязными, а убираем совсем
        self._bm25_by_key.drop(lambda k: k[0] == project_id)
        if get_settings().rag.bm25_store_enabled:
            try:
                await self.vector_repo.bm25_store_drop(project_id)
            except Exception as e:
                logger.warning("BM25-хранилище: записи проекта не убраны (%s)", e)
        logger.info(
            "project_rag: удалено %s документов для project_id=%s",
            deleted_count,
            project_id,
        )
        return {
            "ok": True,
            "project_id": project_id,
            "deleted_count": deleted_count,
            "minio_keys": minio_keys,
        }
