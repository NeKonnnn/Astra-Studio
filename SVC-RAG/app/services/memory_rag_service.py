# RAG по документам из настроек «библиотека памяти»: MinIO (оригинал) + memory_rag_* в Postgres
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from app.clients.rag_models_client import RagModelsClient
from app.core.config import get_settings
from app.core.logging import get_logger
from app.database.search_filters import DocumentVectorSearchFilters
from app.database.memory_rag_repository import (
    MemoryRagDocumentRepository,
    MemoryRagVectorRepository,
)
from app.database.models import Document, DocumentVector
from app.database.graph_repository import GraphRepository
from app.services.bm25_index import Bm25IndexCache, InMemoryBm25Index
from app.services.chunker import (
    describe_embed_client,
    normalize_chunking_strategy,
    resolve_chunk_params,
    split_into_chunks_with_meta,
)
from app.services.document_parser import empty_document_index_error, parse_document
from app.services.retrieval_pipeline import RetrievalTrace, run_retrieval_pipeline
from app.services.stage_timer import StageTimer
from app.text_sanitize import strip_null_bytes
from app.services.hierarchical_indexing import index_document_hierarchically
from app.clients.openai_models_compat_client import set_embed_log_context

logger = get_logger(__name__)

_memory_reindex_generation = 0

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

def bump_memory_reindex_generation() -> int:
    global _memory_reindex_generation
    _memory_reindex_generation += 1
    return _memory_reindex_generation

def current_memory_reindex_generation() -> int:
    return _memory_reindex_generation

def _memory_chunk_params() -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Единые настройки чанкования Библиотеки: env RAG_MEMORY** или дефолты
    конфига. Настройки из UI сюда сознательно НЕ доходят — иначе документы
    Библиотеки получают разную нарезку (перенарезка память не трогает)."""
    import os

    def _int_env(name: str) -> Optional[int]:
        v = (os.getenv(name) or "").strip()
        if not v:
            return None
        try:
            return int(v)
        except ValueError:
            logger.warning("%s=%r не число — используем дефолт конфига", name, v)
            return None

    strategy = (os.getenv("RAG_MEMORY_CHUNKING_STRATEGY") or "").strip() or None
    return (
        _int_env("RAG_MEMORY_CHUNK_SIZE"),
        _int_env("RAG_MEMORY_CHUNK_OVERLAP"),
        strategy,
    )

def memory_embedding_choice() -> Tuple[Optional[str], Optional[str]]:
    """(provider, model) Библиотеки из RAG_MEMORY_EMBEDDING_MODEL.

    Тот же формат пути, что в UI: ```local/<имя>```, ```phoenix/<id>``` или ```corsur/<id>```.
    Разбор продублирован с бэкендом (```memory_rag_env.get_memory_embedding_fields```),
    потому что переменные RAG_MEMORY_* лежат в ConfigMap обоих сервисов — здесь они
    нужны, чтобы на старте заметить смену модели и переиндексировать Библиотеку.

    (None, None) — переменная не задана, работает кластерная модель.
    """
    import os

    raw = (os.getenv("RAG_MEMORY_EMBEDDING_MODEL") or "").strip()
    if not raw:
        return None, None
    lower = raw.lower()
    if lower.startswith("phoenix_embeddings/"):
        provider = (
            os.getenv("RAG_PHOENIX_EMBEDDINGS_PROVIDER_ID") or "PHOENIX_Embeddings"
        ).strip() or "PHOENIX_Embeddings"
        model = raw.split("/", 1)[1].strip()
    elif lower.startswith("phoenix/"):
        provider = (os.getenv("RAG_PHOENIX_PROVIDER_ID") or "PHOENIX").strip() or "PHOENIX"
        model = raw.split("/", 1)[1].strip()
    elif lower.startswith("corsur/"):
        provider = (os.getenv("RAG_CORSUR_PROVIDER_ID") or "CORSUR").strip() or "CORSUR"
        model = raw.split("/", 1)[1].strip()
    else:
        provider = "native"
        model = raw.split("/", 1)[1].strip() if "/" in raw else raw
    return (provider, model) if model else (None, None)

class MemoryRagService:
    def __init__(
        self,
        doc_repo: MemoryRagDocumentRepository,
        vector_repo: MemoryRagVectorRepository,
        rag_models_client: RagModelsClient,
        graph_repo: Optional[GraphRepository] = None,
    ):
        self.doc_repo = doc_repo
        self.vector_repo = vector_repo
        self.rag_client = rag_models_client
        self.graph_repo = graph_repo
        # Индекс на каждую размерность: BM25 строится по конкретной таблице.
        self._bm25_by_dim = Bm25IndexCache(get_settings().rag.bm25_retain_seconds)

    async def _route(self, model: Optional[str] = None, provider: Optional[str] = None):
        """Профиль эмбеддинга (клиент + имя модели + dim) и репозиторий нужной таблицы.

        Модель Библиотеки задаётся не пользователем, а из ENV бэкенда
        (RAG_MEMORY_EMBEDDING_MODEL) — но механика та же, что у KB и проектов.
        Без model/provider вернёт ровно то, что было до фазы B3.
        """
        from app.services.embed_routing import resolve_for

        return await resolve_for(self.rag_client, self.vector_repo, provider, model)

    def _bm25_for(self, repo) -> InMemoryBm25Index:
        dim = int(getattr(repo, "embedding_dim", 0) or 0)
        def _make() -> InMemoryBm25Index:
            if not get_settings().rag.bm25_store_enabled:
                return InMemoryBm25Index(repo.get_all_contents_for_bm25)

            from app.database.bm25_store import cache_key

            store_key = cache_key("memory", "all", dim)

            async def _load():
                return await repo.bm25_store_load(store_key)

            async def _save(fingerprint, payload, chunk_count):
                await repo.bm25_store_save(
                    store_key, fingerprint, payload, chunk_count
                )

            return InMemoryBm25Index(
                repo.get_all_contents_for_bm25,
                load_from_store=_load,
                save_to_store=_save,
            )

        return self._bm25_by_dim.get_or_create(dim, _make)

    def _mark_bm25_dirty(self) -> None:
        for idx in self._bm25_by_dim.values():
            idx.mark_dirty()

    async def _rebuild_graph_for_document(self, document_id: int) -> None:
        if not self.graph_repo:
            return
        try:
            chunks = await self.vector_repo.get_vectors_by_document(document_id)
            if chunks:
                await self.graph_repo.rebuild_document_graph(
                    store_type="memory",
                    document_id=document_id,
                    chunks=[(v.chunk_index, v.content) for v in chunks],
                )
        except Exception as e:
            logger.warning(
                "memory graph индекс не пересобран для документа %s: %s", document_id, e
            )

    async def index_document(
        self,
        file_data: bytes,
        filename: str,
        minio_object: Optional[str] = None,
        minio_bucket: Optional[str] = None,
        *,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        timer = StageTimer("INDEX", store="memory", file=filename)
        chunk_size, chunk_overlap, chunking_strategy = _memory_chunk_params()
        with timer.stage("route_embed_model"):
            prof, repo = await self._route(model, provider)
        with timer.stage("parse"):
            parsed = await parse_document(file_data, filename)
        if not parsed:
            timer.log(logger)
            return {
                "ok": False,
                "error": "Не удалось извлечь текст или формат не поддерживается",
                "document_id": None,
            }

        filename = strip_null_bytes(filename)
        text = strip_null_bytes(parsed.get("text", "") or "")
        if not text.strip():
            timer.log(logger)
            return {"ok": False, "error": empty_document_index_error(parsed, filename), "document_id": None}

        meta: Dict[str, Any] = {
            "file_type": parsed.get("file_type", ""),
            "pages": parsed.get("pages", 0),
            "size": len(file_data),
            "source": "memory_library",
        }
        if minio_object:
            meta["minio_object"] = minio_object
        if minio_bucket:
            meta["minio_bucket"] = minio_bucket

        doc = Document(
            filename=filename,
            content=text,
            metadata=meta,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        with timer.stage("insert_document"):
            doc_id = await self.doc_repo.create_document(doc)
        if doc_id is None:
            timer.log(logger)
            return {
                "ok": False,
                "error": "Ошибка сохранения документа в БД",
                "document_id": None,
            }
        
        # Пометка для логов гардрейла: клиент видит только список строк, имя
        # файла и id документа до него не доезжают. Ставим до ветвления, чтобы
        # покрыть и иерархическую индексацию, и обычную.
        set_embed_log_context(
            store="memory",
            operation="upload",
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
                    )
            except Exception as e:
                logger.error("memory-rag иерархическая индексация не удалась: %s", e)
                await self.doc_repo.delete_document(doc_id)
                timer.log(logger)
                return {
                    "ok": False,
                    "error": f"Иерархическая индексация: {e}",
                    "document_id": None,
                }
            with timer.stage("bm25"):
                self._mark_bm25_dirty()
            with timer.stage("graph"):
                await self._rebuild_graph_for_document(doc_id)
            eff_size, eff_overlap = resolve_chunk_params(chunk_size, chunk_overlap)
            logger.info(
                "[INDEX memory] '%s' (id=%s): strategy=hierarchical size=%s overlap=%s "
                "символов=%s чанков=%s embed=%s",
                filename,
                doc_id,
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
            }

        with timer.stage("chunk"):
            chunks_with_meta = split_into_chunks_with_meta(
                text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                chunking_strategy=chunking_strategy or "universal",
            )
        if not chunks_with_meta:
            await self.doc_repo.delete_document(doc_id)
            timer.log(logger)
            return {
                "ok": False,
                "error": "Не удалось нарезать чанки",
                "document_id": None,
            }
        chunks = [c for c, _m in chunks_with_meta]

        try:
            with timer.stage("embed"):
                embeddings = await prof.embed(chunks, kind="document")
        except Exception as e:
            logger.error("Ошибка эмбеддингов memory_rag: %s", e)
            await self.doc_repo.delete_document(doc_id)
            timer.log(logger)
            return {
                "ok": False,
                "error": f"Ошибка эмбеддингов: {e}",
                "document_id": None,
            }

        vectors = []
        for idx, ((chunk, cmeta), embedding) in enumerate(
            zip(chunks_with_meta, embeddings)
        ):
            meta = {"chunk_index": idx, "document_filename": filename}
            meta.update(cmeta)
            vectors.append(
                DocumentVector(
                    document_id=doc_id,
                    chunk_index=idx,
                    embedding=embedding,
                    content=chunk,
                    metadata=meta,
                )
            )

        with timer.stage("insert"):
            created = await repo.create_vectors_batch(vectors)
        with timer.stage("bm25"):
            self._mark_bm25_dirty()
        if self.graph_repo:
            try:
                with timer.stage("graph"):
                    await self.graph_repo.rebuild_document_graph(
                        store_type="memory",
                        document_id=doc_id,
                        chunks=[(v.chunk_index, v.content) for v in vectors],
                    )
            except Exception as e:
                logger.warning(
                    "memory graph индекс не собран для документа %s: %s", doc_id, e
                )
        eff_size, eff_overlap = resolve_chunk_params(chunk_size, chunk_overlap)
        logger.info(
            "[INDEX memory] '%s' (id=%s): strategy=%s size=%s overlap=%s "
            "символов=%s чанков=%s embed=%s",
            filename,
            doc_id,
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
        }

    async def reindex_document(
        self,
        document_id: int,
        *,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> int:
        """Заново нарезать документ Библиотеки из сохранённого текста и заменить вектора."""
        chunk_size, chunk_overlap, chunking_strategy = _memory_chunk_params()
        prof, repo = await self._route(model, provider)
        doc = await self.doc_repo.get_document(document_id)
        if doc is None:
            return 0
        text = doc.content or ""
        if not text.strip():
            return 0
        # Пометка для логов гардрейла: клиент видит только список строк, имя
        # файла и id документа до него не доезжают. Ставим до ветвления, чтобы
        # покрыть и иерархическую индексацию, и обычную.
        set_embed_log_context(
            store="memory",
            operation="reindex",
            document_id=document_id,
            filename=getattr(doc, "filename", None),
        )
        await self.vector_repo.delete_vectors_by_document(document_id)
        strategy = (chunking_strategy or "universal").strip().lower()
        if strategy == "hierarchical":
            count = await index_document_hierarchically(
                text,
                document_id,
                filename=doc.filename,
                vector_repo=repo,
                rag_client=prof.client,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            self._mark_bm25_dirty()
            await self._rebuild_graph_for_document(document_id)
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
        for idx, ((chunk, cmeta), embedding) in enumerate(
            zip(chunks_with_meta, embeddings)
        ):
            meta = {"chunk_index": idx, "document_filename": doc.filename}
            meta.update(cmeta)
            vectors.append(
                DocumentVector(
                    document_id=document_id,
                    chunk_index=idx,
                    embedding=embedding,
                    content=chunk,
                    metadata=meta,
                )
            )
        created = await repo.create_vectors_batch(vectors)
        self._mark_bm25_dirty()
        await self._rebuild_graph_for_document(document_id)
        return created

    async def reindex_all(
        self,
        *,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        generation: Optional[int] = None,
    ) -> Dict[str, int]:
        """Переиндексировать все документы Библиотеки. Возвращает {documents, chunks}."""
        docs = await self.doc_repo.get_all_documents()
        _cs_env, _co_env, _strat_env = _memory_chunk_params()
        _eff_size, _eff_overlap = resolve_chunk_params(_cs_env, _co_env)
        logger.info(
            "[REINDEX memory] старт: документов=%s strategy=%s size=%s overlap=%s embed=%s",
            len(docs),
            normalize_chunking_strategy(_strat_env),
            _eff_size,
            _eff_overlap,
            describe_embed_client(self.rag_client),
        )
        n_docs = 0
        n_chunks = 0
        n_errors = 0
        for doc in docs:
            if generation is not None and generation != _memory_reindex_generation:
                logger.info(
                    "[REINDEX memory] прерван: начат новый реиндекс (gen %s→%s)",
                    generation,
                    _memory_reindex_generation,
                )
                break
            try:
                c = await self.reindex_document(
                    doc.id,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    chunking_strategy=chunking_strategy,
                    model=model,
                    provider=provider,
                )
                n_docs += 1
                n_chunks += c
                _own, _up, _name = _doc_actors(doc)
                logger.debug(
                    "[REINDEX memory] '%s' (id=%s, owner=%s, uploader=%s) чанков=%s",
                    _name,
                    doc.id,
                    _own,
                    _up,
                    c,
                )
            except Exception as e:
                n_errors += 1
                _own, _up, _name = _doc_actors(doc)
                logger.error(
                    "[REINDEX memory] '%s' (id=%s, owner=%s, uploader=%s) ошибка: %s",
                    _name,
                    getattr(doc, "id", "?"),
                    _own,
                    _up,
                    e,
                )
        logger.info(
            "[REINDEX memory] готово: документов=%s чанков=%s ошибок=%s",
            n_docs,
            n_chunks,
            n_errors,
        )
        return {"documents": n_docs, "chunks": n_chunks}

    async def search(
        self,
        query: str,
        k: int = 8,
        document_id: Optional[int] = None,
        use_reranking: Optional[bool] = None,
        strategy: Optional[str] = None,
        vector_query: Optional[str] = None,
        filters: Optional[DocumentVectorSearchFilters] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        rerank_model: Optional[str] = None,
        rerank_provider: Optional[str] = None,
        eval_gold_document_ids: Optional[List[int]] = None,
        eval_gold_chunks: Optional[List[Tuple[int, int]]] = None,
        eval_llm_judge: bool = False,
        return_trace: bool = False,
    ) -> Union[
        List[Tuple[str, float, Optional[int], Optional[int]]],
        Tuple[List[Tuple[str, float, Optional[int], Optional[int]]], RetrievalTrace],
    ]:
        cfg = get_settings().rag
        # Запрос считаем моделью Библиотеки и ищем в таблице ЕЁ размерности.
        prof, repo = await self._route(model, provider)
        # Реранкер выбирается независимо от эмбеддера: хранилища у него нет.
        from app.services.embed_routing import rerank_client_for

        rr_client = rerank_client_for(self.rag_client, rerank_provider, rerank_model)

        async def _vectors(emb, lim):
            return await repo.similarity_search(
                query_embedding=emb,
                limit=lim,
                document_id=document_id,
                filters=filters,
            )

        async def _keywords(text, lim):
            return await repo.keyword_search(
                text,
                limit=lim,
                document_id=document_id,
                filters=filters,
            )

        async def _substring(tokens, lim):
            return await repo.substring_search(
                tokens, limit=lim, document_id=document_id
            )

        async def _fetch_doc(doc_id: int):
            return await repo.get_vectors_by_document(doc_id)

        async def _find_docs_by_filename(name: str):
            return await self.doc_repo.find_document_ids_by_filename(name)

        hits, trace = await run_retrieval_pipeline(
            store="memory",
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
            bm25_index=self._bm25_for(repo),
            eval_gold_document_ids=eval_gold_document_ids,
            eval_gold_chunks=eval_gold_chunks,
            eval_llm_judge=eval_llm_judge,
            log_store_label="memory (библиотека памяти)",
        )
        return (hits, trace) if return_trace else hits

    async def list_documents(self) -> List[Dict[str, Any]]:
        docs = await self.doc_repo.get_all_documents()
        return [
            {
                "id": d.id,
                "filename": d.filename,
                "metadata": d.metadata,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "size": (d.metadata or {}).get("size", 0),
                "file_type": (d.metadata or {}).get("file_type", ""),
            }
            for d in docs
        ]

    async def delete_document(self, document_id: int) -> Dict[str, Any]:
        """Удаляет из БД; возвращает minio-ключи для очистки в backend."""
        doc = await self.doc_repo.get_document(document_id)
        if not doc:
            return {"ok": False, "error": "not_found"}
        meta = doc.metadata or {}
        if self.graph_repo:
            try:
                await self.graph_repo.delete_document_graph("memory", document_id)
            except Exception:
                pass
        # Для CEF INT005/INT006 по чанкам ставим контекст ДО удаления векторов.
        set_embed_log_context(
            store="memory",
            operation="delete",
            document_id=document_id,
            filename=doc.filename,
        )
        await self.vector_repo.delete_vectors_by_document(document_id)
        await self.doc_repo.delete_document(document_id)
        self._mark_bm25_dirty()
        logger.info("memory_rag: удалён документ id=%s", document_id)
        return {
            "ok": True,
            "document_id": document_id,
            "minio_object": meta.get("minio_object"),
            "minio_bucket": meta.get("minio_bucket"),
        }