# Сервис постоянной базы знаний (Knowledge Base)
# Логика аналогична RagService, но работает с таблицами kb_documents/kb_vectors
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
import json

from app.services.hierarchical_indexing import index_document_hierarchically
from app.clients.openai_models_compat_client import set_embed_log_context
from app.clients.rag_models_client import RagModelsClient
from app.core.config import get_settings
from app.core.logging import get_logger
from app.database.search_filters import DocumentVectorSearchFilters
from app.database.kb_repository import KbDocumentRepository, KbVectorRepository
from app.database.models import Document
from app.database.graph_repository import GraphRepository
from app.services.bm25_index import InMemoryBm25Index, Bm25IndexCache
from app.services.chunker import (
    normalize_chunking_strategy,
    resolve_chunk_params,
    split_into_chunks_with_meta,
)
from app.services.document_parser import (
    empty_document_index_error,
    parse_document,
    parsed_file_type,
)
from app.services.retrieval_pipeline import RetrievalTrace, run_retrieval_pipeline
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


def bump_kb_reindex_generation(key: str = "*") -> int:
    """Новое поколение реиндекса KB — сигнал текущему проходу ЭТОГО ключа прерваться.

    Ключ — конкретная сущность (``agent:<id>``, ``owner:<uid>``) либо ``*`` для
    кластерного прогона. Раньше счётчик был один на сервис, и старт пересборки
    одного агента прерывал пересборку другого на полпути.
    """
    from app.services.reindex_queue import kb_queue

    return kb_queue.bump(key)


def current_kb_reindex_generation(key: str = "*") -> int:
    from app.services.reindex_queue import kb_queue

    return kb_queue.current(key)


def kb_generation_is_current(key: str, generation: Optional[int]) -> bool:
    """Актуален ли проход. ``generation=None`` — прерывание не запрашивалось."""
    from app.services.reindex_queue import kb_queue

    return kb_queue.is_current(key, generation)


MAX_KB_CONTEXT_CHARS = 12000


class KbService:
    def __init__(
        self,
        doc_repo: KbDocumentRepository,
        vector_repo: KbVectorRepository,
        rag_models_client: RagModelsClient,
        graph_repo: Optional[GraphRepository] = None,
    ):
        self.doc_repo = doc_repo
        self.vector_repo = vector_repo
        self.rag_client = rag_models_client
        self.graph_repo = graph_repo
        # Индексы BM25 по (размерность, набор документов).
        self._bm25_by_key = Bm25IndexCache(get_settings().rag.bm25_retain_seconds)

    async def _route(self, model=None, provider=None):
        """Профиль эмбеддинга (клиент + имя модели + dim) и репозиторий нужной таблицы.

        Без model/provider вернутся ровно то, что было до фазы B3.
        """
        from app.services.embed_routing import resolve_for

        return await resolve_for(self.rag_client, self.vector_repo, provider, model)

    def _bm25_for(
        self, repo, document_ids: Optional[List[int]] = None
    ) -> InMemoryBm25Index:
        """Индекс по документам белого списка; без него - по всей таблице.

        document_ids - тот же белый список, которым потом фильтруется
        выдача (см. allowed_doc_ids в retrieval_pipeline)
        """
        dim = int(getattr(repo, "embedding_dim", 0) or 0)
        scope = (
            frozenset(int(d) for d in document_ids if d is not None)
            if document_ids
            else None
        )
        key = (dim, scope)

        def _make() -> InMemoryBm25Index:
            ids = sorted(scope) if scope else None

            async def _fetch():
                return await repo.get_all_contents_for_bm25(document_ids=ids)

            if not get_settings().rag.bm25_store_enabled:
                # Выключатель: ведём себя как до появления хранилища.
                return InMemoryBm25Index(_fetch)

            from app.database.bm25_store import cache_key, documents_entity

            store_key = cache_key("kb", documents_entity(ids), dim)

            async def _load():
                return await repo.bm25_store_load(store_key, document_ids=ids)

            async def _save(fingerprint, payload, chunk_count):
                await repo.bm25_store_save(store_key, fingerprint, payload, chunk_count)

            return InMemoryBm25Index(_fetch, load_from_store=_load, save_to_store=_save)

        return self._bm25_by_key.get_or_create(key, _make)

    def _mark_bm25_dirty(self) -> None:
        for idx in self._bm25_by_key.values():
            idx.mark_dirty()

    async def _rebuild_graph_for_document(self, document_id: int) -> None:
        if not self.graph_repo:
            return
        try:
            chunks = await self.vector_repo.get_vectors_by_document(document_id)
            if chunks:
                await self.graph_repo.rebuild_document_graph(
                    store_type="kb",
                    document_id=document_id,
                    chunks=[(v.chunk_index, v.content) for v in chunks],
                )
        except Exception as e:
            logger.warning(
                "KB graph индекс не пересобран для документа %s: %s", document_id, e
            )

    # ─── Индексация ───────────────────────────────────────────────────────────

    async def index_document(
        self,
        file_data: bytes,
        filename: str,
        *,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        minio_object: Optional[str] = None,
        minio_bucket: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        agent_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
        uploaded_by: Optional[str] = None,
        relative_path: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Парсим файл, режем на чанки, получаем эмбеддинги и сохраняем в kb_documents/kb_vectors."""
        from app.services.document_duplicate import (
            duplicate_index_result,
            file_content_hash,
        )
        from app.services.text_duplicate import (
            find_text_duplicate,
            remember_text,
            text_duplicate_index_result,
        )

        content_hash = file_content_hash(file_data)
        existing = await self.doc_repo.find_duplicate(
            content_hash=content_hash,
            filename=filename,
            size=len(file_data),
            agent_id=int(agent_id) if agent_id is not None else None,
            knowledge_base_id=(
                int(knowledge_base_id) if knowledge_base_id is not None else None
            ),
        )
        if existing:
            return duplicate_index_result(
                document_id=existing.get("id"),
                filename=existing.get("filename") or filename,
            )

        timer = StageTimer("INDEX", store="kb", file=filename)
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
            return {
                "ok": False,
                "error": empty_document_index_error(parsed, filename),
                "document_id": None,
            }

        # Тот же текст под другим именем или в других байтах - до эмбеддингов.
        # Область та же, что у побайтной проверки: база знаний / агент / общая.
        scope_agent = int(agent_id) if agent_id is not None else None
        scope_kb = int(knowledge_base_id) if knowledge_base_id is not None else None

        async def _kb_doc_text(doc_id: int) -> Optional[str]:
            doc = await self.doc_repo.get_document(doc_id)
            return doc.content if doc else None

        async def _same_text(older_than: Optional[int] = None):
            def _in_scope(meta: dict) -> bool:
                if scope_kb is not None:
                    return meta.get("knowledge_base_id") == scope_kb
                return meta.get("agent_id") == scope_agent

            return await find_text_duplicate(
                store="kb",
                text=text,
                candidates=[
                    (d["id"], d["filename"])
                    for d in await self.doc_repo.list_documents_brief()
                    if _in_scope(d.get("metadata") or {})
                ],
                load_text=_kb_doc_text,
                older_than=older_than,
                upload_name=filename,
            )

        with timer.stage("text_duplicate"):
            same = await _same_text()
        if same:
            timer.log(logger)
            return text_duplicate_index_result("kb", filename, same)

        # Модель выбираем ДО создания документа: иначе при ошибке сохранения
        # в БД останется документ без единого вектора.
        try:
            with timer.stage("route_embed_model"):
                prof, repo = await self._route(model, provider)
        except Exception as e:
            logger.error(
                "[INDEX kb] не удалось выбрать модель (provider=%s model=%s): %s",
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
            "file_type": parsed_file_type(parsed),
            "pages": parsed.get("pages", 0),
            "size": len(file_data),
            "source": "knowledge_base" if knowledge_base_id is not None else "agent",
            "content_hash": content_hash,
        }
        if minio_object:
            meta["minio_object"] = minio_object
        if minio_bucket:
            meta["minio_bucket"] = minio_bucket
        if owner_user_id:
            meta["owner_user_id"] = str(owner_user_id).strip().lower()
        if uploaded_by:
            meta["uploaded_by"] = str(uploaded_by).strip().lower()
        if agent_id is not None:
            try:
                meta["agent_id"] = int(agent_id)
            except (TypeError, ValueError):
                pass
        if knowledge_base_id is not None:
            try:
                meta["knowledge_base_id"] = int(knowledge_base_id)
            except (TypeError, ValueError):
                pass
        if relative_path:
            rel = str(relative_path).replace("\\", "/").strip().lstrip("/")
            if rel:
                meta["relative_path"] = rel

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
        # Одновременная загрузка того же текста (несколько файлов разом) тоже
        # прошла проверку выше: после вставки поздний документ уступает раннему.
        same = await _same_text(older_than=doc_id)
        if same:
            await self.doc_repo.delete_document(doc_id)
            timer.log(logger)
            return text_duplicate_index_result("kb", filename, same)
        remember_text("kb", doc_id, text)
        # Пометка для логов гардрейла: клиент видит только список строк, имя
        # файла и id документа до него не доезжают. Ставим до ветвления, чтобы
        # покрыть и иерархическую индексацию, и обычную.
        set_embed_log_context(
            store="kb",
            operation="upload",
            document_id=doc_id,
            filename=filename,
            owner_user_id=owner_user_id,
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
                logger.error("KB иерархическая индексация не удалась: %s", e)
                await self.doc_repo.delete_document(doc_id)
                # Документа не будет - исходник больше не нужен. Удалить его
                # может только backend (PVC его), и только если знает об исходе:
                # по таймауту он ждать уже перестал. См. index_failed_report.
                from app.services.index_failed_report import report_index_failed

                await report_index_failed(
                    minio_object=minio_object,
                    minio_bucket=minio_bucket,
                    filename=filename,
                    reason=f"Иерархическая индексация: {e}",
                )
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
                "[INDEX kb] '%s' (id=%s, owner=%s, uploader=%s): strategy=hierarchical size=%s overlap=%s "
                "символов=%s чанков=%s embed=%s",
                filename,
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

        from app.services.index_batching import embed_and_store

        def _kb_vector_meta(idx: int, cmeta: dict) -> dict:
            meta = {"start": idx}
            meta.update(cmeta)
            return meta

        try:
            # Волнами по RAG_INDEX_CHUNKS_PER_BATCH чанков: эмбеддинги,
            # вставка, пачка отпущена.
            # vectors возвращаются без эмбеддингов: ниже они нужны только
            # графу, а ему от чанка требуются chunk_index и content.
            created, vectors = await embed_and_store(
                chunks_with_meta,
                document_id=doc_id,
                repo=repo,
                embed=lambda texts: prof.embed(texts, kind="document"),
                make_metadata=_kb_vector_meta,
                timer=timer,
            )
        except Exception as e:
            logger.error("Ошибка индексации KB: %s", e)
            await self.doc_repo.delete_document(doc_id)
            # То же, что в иерархической ветке: документа не будет, и сказать
            # об этом бэкенду больше некому.
            from app.services.index_failed_report import report_index_failed

            await report_index_failed(
                minio_object=minio_object,
                minio_bucket=minio_bucket,
                filename=filename,
                reason=f"Ошибка эмбеддингов: {e}",
            )
            timer.log(logger)
            return {
                "ok": False,
                "error": f"Ошибка эмбеддингов: {e}",
                "document_id": None,
            }
        with timer.stage("bm25"):
            self._mark_bm25_dirty()
        if self.graph_repo:
            try:
                with timer.stage("graph"):
                    await self.graph_repo.rebuild_document_graph(
                        store_type="kb",
                        document_id=doc_id,
                        chunks=[(v.chunk_index, v.content) for v in vectors],
                    )
            except Exception as e:
                logger.warning(
                    "KB graph индекс не собран для документа %s: %s", doc_id, e
                )
        eff_size, eff_overlap = resolve_chunk_params(chunk_size, chunk_overlap)
        logger.info(
            "[INDEX kb] '%s' (id=%s, owner=%s, uploader=%s): strategy=%s size=%s overlap=%s "
            "символов=%s чанков=%s embed=%s",
            filename,
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
        route=None,
    ) -> int:
        """Заново нарезать один документ KB из сохранённого текста и заменить его вектора.

        route — уже разрешённая пара (профиль, репозиторий): reindex_all передаёт её,
        чтобы не резолвить модель на каждый документ.
        """
        doc = await self.doc_repo.get_document(document_id)
        if doc is None:
            return 0
        text = doc.content or ""
        if not text.strip():
            return 0
        prof, repo = route if route else await self._route(model, provider)
        # Удаление обходит ВСЕ таблицы (B2b) — при смене модели старые вектора
        # лежат в таблице прежней размерности и должны уйти.
        # Пометка для логов гардрейла: клиент видит только список строк, имя
        # файла и id документа до него не доезжают. Ставим до ветвления, чтобы
        # покрыть и иерархическую индексацию, и обычную.
        set_embed_log_context(
            store="kb",
            operation="reindex",
            document_id=document_id,
            filename=getattr(doc, "filename", None),
        )
        await repo.delete_vectors_by_document(document_id)
        strategy = (chunking_strategy or "universal").strip().lower()
        if strategy == "hierarchical":
            from app.services.hierarchical_indexing import index_document_hierarchically

            count = await index_document_hierarchically(
                text,
                document_id,
                filename=doc.filename,
                vector_repo=repo,
                rag_client=prof.client,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                model=prof.model,
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
        from app.services.index_batching import embed_and_store

        def _kb_vector_meta(idx: int, cmeta: dict) -> dict:
            meta = {"start": idx}
            meta.update(cmeta)
            return meta

        # Волнами, см. index_document.
        created, vectors = await embed_and_store(
            chunks_with_meta,
            document_id=document_id,
            repo=repo,
            embed=lambda texts: prof.embed(texts, kind="document"),
            make_metadata=_kb_vector_meta,
        )
        self._mark_bm25_dirty()
        if self.graph_repo:
            try:
                await self.graph_repo.rebuild_document_graph(
                    store_type="kb",
                    document_id=document_id,
                    chunks=[(v.chunk_index, v.content) for v in vectors],
                )
            except Exception as e:
                logger.warning(
                    "KB graph индекс не собран для документа %s: %s", document_id, e
                )
        return created

    async def reindex_all(
        self,
        *,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        generation: Optional[int] = None,
        generation_key: str = "*",
        owner_user_id: Optional[str] = None,
        document_ids: Optional[List[int]] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, int]:
        """Переиндексировать документы KB.

        Если передан owner_user_id — документы владельца (+ опционально document_ids
        из config агентов без metadata — для legacy).
        """
        by_id: Dict[int, Any] = {}
        if owner_user_id:
            for doc in await self.doc_repo.get_documents_by_owner(owner_user_id):
                if doc.id is not None:
                    by_id[int(doc.id)] = doc
        if document_ids:
            for doc in await self.doc_repo.get_documents_by_ids(list(document_ids)):
                if doc.id is not None:
                    by_id[int(doc.id)] = doc
        if owner_user_id or document_ids:
            docs = list(by_id.values())
        else:
            docs = await self.doc_repo.get_all_documents()
        # Модель резолвим один раз на весь проход — иначе проба на каждый документ.
        prof, repo = await self._route(model, provider)
        eff_size, eff_overlap = resolve_chunk_params(chunk_size, chunk_overlap)
        logger.info(
            "[REINDEX kb] старт: owner=%s документов=%s strategy=%s size=%s overlap=%s embed=%s",
            owner_user_id or "*",
            len(docs),
            normalize_chunking_strategy(chunking_strategy),
            eff_size,
            eff_overlap,
            prof.label,
        )
        n_docs = 0
        n_chunks = 0
        n_errors = 0
        for doc in docs:
            # Прерываемся, только если заново запустили ЭТУ ЖЕ сущность: значит
            # её настройки изменились и текущий проход уже неактуален. Пересборка
            # соседнего агента нас не касается.
            if not kb_generation_is_current(generation_key, generation):
                logger.info(
                    "[REINDEX kb] %s прерван: начат новый реиндекс этой же сущности (gen %s→%s)",
                    generation_key,
                    generation,
                    current_kb_reindex_generation(generation_key),
                )
                break
            try:
                c = await self.reindex_document(
                    doc.id,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    chunking_strategy=chunking_strategy,
                    route=(prof, repo),
                )
                n_docs += 1
                n_chunks += c
                # Документ переиндексирован - его текст, чанки и векторы
                # больше не нужны. Отдаём память сразу, а не в конце
                # прохода: иначе она копится на весь реиндекс.
                #
                # Реиндекс идёт фоновой задачей и через middleware не
                # проходит, поэтому вызов стоит здесь. Импорт локальный -
                # одна строка на сервис, тащить её в шапку незачем.
                from app.core import mem_release

                mem_release.release("реиндекс kb")
                _own, _up, _name = _doc_actors(doc)
                logger.debug(
                    "[REINDEX kb] '%s' (id=%s, owner=%s, uploader=%s) чанков=%s",
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
                    "[REINDEX kb] '%s' (id=%s, owner=%s, uploader=%s) ошибка: %s",
                    _name,
                    getattr(doc, "id", "?"),
                    _own,
                    _up,
                    e,
                )
        logger.info(
            "[REINDEX kb] итого: owner=%s docs_filter=%s документов=%s чанков=%s ошибок=%s",
            owner_user_id or "*",
            len(document_ids or []),
            n_docs,
            n_chunks,
            n_errors,
        )
        return {"documents": n_docs, "chunks": n_chunks}

    # ─── Поиск ────────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        k: int = 8,
        document_id: Optional[int] = None,
        document_ids: Optional[List[int]] = None,
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
        """Векторный поиск по базе знаний.

        Возвращает список (content, score, document_id, chunk_index).
        При ```return_trace=True``` — кортеж (hits, RetrievalTrace).
        """
        cfg = get_settings().rag
        # Запрос считаем моделью пользователя и ищем в таблице ЕЁ размерности.
        prof, repo = await self._route(model, provider)
        # Реранкер выбирается независимо от эмбеддера: хранилища у него нет.
        from app.services.embed_routing import rerank_client_for

        rr_client = rerank_client_for(self.rag_client, rerank_provider, rerank_model)

        async def _vectors(emb, lim):
            return await repo.similarity_search(
                query_embedding=emb,
                limit=lim,
                document_id=document_id,
                document_ids=document_ids,
                filters=filters,
            )

        async def _keywords(text, lim):
            return await repo.keyword_search(
                text,
                limit=lim,
                document_id=document_id,
                document_ids=document_ids,
                filters=filters,
            )

        async def _substring(tokens, lim):
            return await repo.substring_search(
                tokens,
                limit=lim,
                document_id=document_id,
                document_ids=document_ids,
            )

        async def _fetch_doc(doc_id: int):
            return await repo.get_vectors_by_document(doc_id)

        async def _find_docs_by_filename(name: str):
            return await self.doc_repo.find_document_ids_by_filename(name)

        hits, trace = await run_retrieval_pipeline(
            store="kb",
            query=query,
            vector_query=vector_query,
            k=k,
            document_id=document_id,
            document_ids=document_ids,
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
            bm25_index=self._bm25_for(
                repo, [document_id] if document_id is not None else document_ids
            ),
            eval_gold_document_ids=eval_gold_document_ids,
            eval_gold_chunks=eval_gold_chunks,
            eval_llm_judge=eval_llm_judge,
            log_store_label="kb (база знаний)",
        )
        return (hits, trace) if return_trace else hits

    # ─── Управление документами ───────────────────────────────────────────────

    async def check_duplicate(
        self,
        *,
        content_hash: Optional[str] = None,
        filename: Optional[str] = None,
        size: Optional[int] = None,
        agent_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        existing = await self.doc_repo.find_duplicate(
            content_hash=content_hash,
            filename=filename,
            size=size,
            agent_id=agent_id,
            knowledge_base_id=knowledge_base_id,
        )
        if not existing:
            return {"duplicate": False}
        return {
            "duplicate": True,
            "document_id": existing.get("id"),
            "filename": existing.get("filename"),
        }

    async def list_documents(self) -> List[Dict[str, Any]]:
        # size и file_type достаются в SQL, метаданные целиком не
        # разбираются: наверху из них всё равно брали только эти два поля.
        # См. KbDocumentItem и list_documents_brief.
        return await self.doc_repo.list_documents_brief()

    async def delete_document(self, document_id: int) -> Dict[str, Any]:
        doc = await self.doc_repo.get_document(document_id)
        if not doc:
            return {"ok": False, "document_id": document_id}
        meta = doc.metadata or {}
        if self.graph_repo:
            try:
                await self.graph_repo.delete_document_graph("kb", document_id)
            except Exception:
                pass
        # Для CEF INT005/INT006 по чанкам ставим контекст ДО удаления векторов.
        set_embed_log_context(
            store="kb",
            operation="delete",
            document_id=document_id,
            filename=doc.filename,
        )
        await self.vector_repo.delete_vectors_by_document(document_id)
        await self.doc_repo.delete_document(document_id)
        self._mark_bm25_dirty()
        logger.info("KB: удалён документ id=%s ('%s')", document_id, doc.filename)
        return {
            "ok": True,
            "document_id": document_id,
            "minio_object": meta.get("minio_object"),
            "minio_bucket": meta.get("minio_bucket"),
        }

    async def get_document_info(self, document_id: int) -> Optional[Dict[str, Any]]:
        doc = await self.doc_repo.get_document(document_id)
        if not doc:
            return None
        return {
            "id": doc.id,
            "filename": doc.filename,
            "metadata": doc.metadata,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
        }

    async def clone_document(
        self,
        document_id: int,
        *,
        agent_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
        owner_user_id: Optional[str] = None,
        uploaded_by: Optional[str] = None,
        minio_object: Optional[str] = None,
        minio_bucket: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Полный клон KB-документа: строка + векторы во всех dim-таблицах.

        Исходник на диске/MinIO не трогаем — пути передаёт вызывающий
        (обычно уже скопированный файл под нового агента / БЗ).
        """
        src = await self.doc_repo.get_document(document_id)
        if not src:
            return {"ok": False, "document_id": document_id, "error": "not_found"}

        meta = dict(src.metadata or {})
        meta["cloned_from"] = int(document_id)
        if agent_id is not None:
            meta["agent_id"] = int(agent_id)
        if knowledge_base_id is not None:
            meta["knowledge_base_id"] = int(knowledge_base_id)
            # Клон в сущность БЗ — не оставляем привязку к чужому агенту.
            if agent_id is None:
                meta.pop("agent_id", None)
        if owner_user_id:
            meta["owner_user_id"] = str(owner_user_id).strip().lower()
        if uploaded_by:
            meta["uploaded_by"] = str(uploaded_by).strip().lower()
        if minio_object is not None:
            if minio_object:
                meta["minio_object"] = str(minio_object)
            else:
                meta.pop("minio_object", None)
        if minio_bucket is not None:
            if minio_bucket:
                meta["minio_bucket"] = str(minio_bucket)
            else:
                meta.pop("minio_bucket", None)

        new_id = await self.doc_repo.clone_document_row(document_id, metadata=meta)
        if new_id is None:
            return {"ok": False, "document_id": document_id, "error": "clone_failed"}

        try:
            n_vec = await self.vector_repo.clone_vectors_to_document(document_id, new_id)
        except Exception:
            logger.exception(
                "KB clone: векторы не скопированы src=%s → %s, откатываем документ",
                document_id,
                new_id,
            )
            try:
                await self.doc_repo.delete_document(new_id)
            except Exception:
                logger.exception("KB clone: не удалось откатить документ %s", new_id)
            return {"ok": False, "document_id": document_id, "error": "vectors_failed"}

        self._mark_bm25_dirty()
        logger.info(
            "KB: клон документа %s → %s ('%s'), vectors=%s, agent_id=%s",
            document_id,
            new_id,
            src.filename,
            n_vec,
            agent_id,
        )
        return {
            "ok": True,
            "document_id": new_id,
            "source_document_id": int(document_id),
            "filename": src.filename,
            "vectors_copied": n_vec,
            "source_minio_object": (src.metadata or {}).get("minio_object"),
            "source_minio_bucket": (src.metadata or {}).get("minio_bucket"),
        }
