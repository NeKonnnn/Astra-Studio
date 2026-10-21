# API эндпоинты для постоянной Базы Знаний (Knowledge Base)
from app.core.logging import get_logger
from typing import Any, Dict, List, Optional

import os

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from pydantic import BaseModel

from app.api.rag_common import (
    RagSearchEvalBody,
    RagSearchFiltersBody,
    eval_search_kwargs_from_body,
    filters_body_to_domain,
)
from app.dependencies import get_kb_service
from app.services.document_duplicate import DUPLICATE_CODE, duplicate_http_detail
from app.services.hit_types import hit_cosine
from app.services.kb_service import KbService

logger = get_logger(__name__)
router = APIRouter()


class KbIndexResponse(BaseModel):
    ok: bool
    document_id: Optional[int] = None
    filename: Optional[str] = None
    chunks_count: Optional[int] = None
    error: Optional[str] = None


class KbDocumentItem(BaseModel):
    id: int
    filename: str
    created_at: Optional[str] = None
    size: Optional[int] = None
    file_type: Optional[str] = None
    # Только {"agent_id": N} и только если он есть. Поле называется metadata,
    # потому что фронт читает agent_id именно оттуда; блоб целиком сюда не
    # кладём - см. list_documents_brief. Без этого поля FastAPI вырезал бы
    # agent_id из ответа по response_model, даже если сервис его вернул.
    metadata: Optional[Dict[str, Any]] = None

class KbSearchRequest(RagSearchEvalBody):
    query: str
    k: int = 8
    document_id: Optional[int] = None
    # Несколько документов одним запросом (KB агента). Приоритетнее document_id.
    document_ids: Optional[List[int]] = None
    use_reranking: Optional[bool] = None
    strategy: Optional[str] = None
    vector_query: Optional[str] = None
    filters: Optional[RagSearchFiltersBody] = None
    debug_trace: bool = False  # отладочные метрики по шагам пайплайна
    embedding_model: Optional[str] = None
    embedding_provider: Optional[str] = None
    reranker_model: Optional[str] = None
    reranker_provider: Optional[str] = None


class KbSearchHit(BaseModel):
    content: str
    score: float
    document_id: Optional[int] = None
    chunk_index: Optional[int] = None
    cosine: Optional[float] = None


class KbSearchResponse(BaseModel):
    hits: List[KbSearchHit]
    trace: Optional[Dict[str, Any]] = None


@router.post("/documents", response_model=KbIndexResponse)
async def kb_index_document(
    file: UploadFile = File(...),
    chunk_size: Optional[int] = Form(None),
    chunk_overlap: Optional[int] = Form(None),
    chunking_strategy: Optional[str] = Form(None),
    minio_object: Optional[str] = Form(None),
    minio_bucket: Optional[str] = Form(None),
    owner_user_id: Optional[str] = Form(None),
    agent_id: Optional[int] = Form(None),
    knowledge_base_id: Optional[int] = Form(None),
    uploaded_by: Optional[str] = Form(None),
    relative_path: Optional[str] = Form(None),
    embedding_model: Optional[str] = Form(None),
    embedding_provider: Optional[str] = Form(None),
    kb: KbService = Depends(get_kb_service),
):
    """Загрузить документ в постоянную Базу Знаний (PDF, DOCX, XLSX, TXT)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Нужно имя файла")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Файл пустой")

    result = await kb.index_document(
        data,
        file.filename,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunking_strategy=chunking_strategy or "universal",
        minio_object=minio_object,
        minio_bucket=minio_bucket,
        owner_user_id=owner_user_id,
        agent_id=agent_id,
        knowledge_base_id=knowledge_base_id,
        uploaded_by=uploaded_by,
        relative_path=relative_path,
        model=embedding_model,
        provider=embedding_provider,
    )
    if not result.get("ok"):
        if result.get("code") == DUPLICATE_CODE:
            raise HTTPException(
                status_code=409,
                detail=duplicate_http_detail(
                    document_id=result.get("document_id"),
                    filename=result.get("filename") or file.filename,
                    message=result.get("error"),
                ),
            )
        raise HTTPException(
            status_code=422, detail=result.get("error", "Ошибка индексации")
        )
    return KbIndexResponse(
        ok=True,
        document_id=result.get("document_id"),
        filename=result.get("filename"),
        chunks_count=result.get("chunks_count"),
    )


class KbDuplicateCheckRequest(BaseModel):
    content_hash: str
    filename: Optional[str] = None
    size: Optional[int] = None
    agent_id: Optional[int] = None
    knowledge_base_id: Optional[int] = None


class KbDuplicateCheckResponse(BaseModel):
    duplicate: bool
    document_id: Optional[int] = None
    filename: Optional[str] = None


@router.post("/documents/duplicate-check", response_model=KbDuplicateCheckResponse)
async def kb_duplicate_check(
    body: KbDuplicateCheckRequest,
    kb: KbService = Depends(get_kb_service),
):
    """Проверка дубликата до записи исходника в MinIO/PVC."""
    out = await kb.check_duplicate(
        content_hash=body.content_hash,
        filename=body.filename,
        size=body.size,
        agent_id=body.agent_id,
        knowledge_base_id=body.knowledge_base_id,
    )
    return KbDuplicateCheckResponse(
        duplicate=bool(out.get("duplicate")),
        document_id=out.get("document_id"),
        filename=out.get("filename"),
    )


@router.get("/documents", response_model=List[KbDocumentItem])
async def kb_list_documents(kb: KbService = Depends(get_kb_service)):
    """Список документов в Базе Знаний."""
    docs = await kb.list_documents()
    return [
        KbDocumentItem(
            id=d["id"],
            filename=d["filename"],
            created_at=d.get("created_at"),
            size=d.get("size"),
            file_type=d.get("file_type"),
            metadata=d.get("metadata"),
        )
        for d in docs
    ]


@router.delete("/documents/{document_id}")
async def kb_delete_document(
    document_id: int,
    kb: KbService = Depends(get_kb_service),
):
    """Удалить документ из Базы Знаний."""
    out = await kb.delete_document(document_id)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail="Документ не найден в Базе Знаний")
    return out


@router.get("/documents/{document_id}")
async def kb_get_document(
    document_id: int,
    kb: KbService = Depends(get_kb_service),
):
    """Метаданные одного KB-документа (для клонирования агента и т.п.)."""
    info = await kb.get_document_info(document_id)
    if not info:
        raise HTTPException(status_code=404, detail="Документ не найден в Базе Знаний")
    return info


class KbCloneDocumentRequest(BaseModel):
    agent_id: Optional[int] = None
    knowledge_base_id: Optional[int] = None
    owner_user_id: Optional[str] = None
    uploaded_by: Optional[str] = None
    minio_object: Optional[str] = None
    minio_bucket: Optional[str] = None


@router.post("/documents/{document_id}/clone")
async def kb_clone_document(
    document_id: int,
    body: KbCloneDocumentRequest,
    kb: KbService = Depends(get_kb_service),
):
    """Клон KB-документа с векторами (агент или база знаний, без повторной индексации)."""
    out = await kb.clone_document(
        document_id,
        agent_id=body.agent_id,
        knowledge_base_id=body.knowledge_base_id,
        owner_user_id=body.owner_user_id,
        uploaded_by=body.uploaded_by,
        minio_object=body.minio_object,
        minio_bucket=body.minio_bucket,
    )
    if not out.get("ok"):
        err = out.get("error") or "clone_failed"
        if err == "not_found":
            raise HTTPException(
                status_code=404, detail="Документ не найден в Базе Знаний"
            )
        raise HTTPException(status_code=500, detail=f"Не удалось клонировать документ: {err}")
    return out


@router.post("/search", response_model=KbSearchResponse)
async def kb_search(
    body: KbSearchRequest,
    kb: KbService = Depends(get_kb_service),
):
    """Поиск по Базе Знаний."""
    # Скоупленный реиндекс чужие документы не трогает — блокировать всех незачем.
    # Отбиваем только кластерный прогон: он перетряхивает всю таблицу.
    from app.services.reindex_queue import kb_queue

    if _block_search_on_reindex() and kb_queue.cluster_running():
        raise HTTPException(
            status_code=409,
            detail="Идёт полная переиндексация Базы Знаний — поиск временно недоступен",
        )
    payload = await kb.search(
        query=body.query,
        k=body.k,
        document_id=body.document_id,
        document_ids=body.document_ids,
        use_reranking=body.use_reranking,
        strategy=body.strategy,
        vector_query=body.vector_query,
        filters=filters_body_to_domain(body.filters),
        return_trace=True,
        model=body.embedding_model,
        provider=body.embedding_provider,
        rerank_model=body.reranker_model,
        rerank_provider=body.reranker_provider,
        **eval_search_kwargs_from_body(body),
    )
    results, trace = payload
    return KbSearchResponse(
        hits=[
            KbSearchHit(
                content=h[0],
                score=h[1],
                document_id=h[2],
                chunk_index=h[3],
                cosine=hit_cosine(h),
            )
            for h in results
        ],
        trace=trace.to_dict() if body.debug_trace else None,
    )


class KbReindexRequest(BaseModel):
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    chunking_strategy: Optional[str] = None
    owner_user_id: Optional[str] = None
    # Чью именно БЗ пересобираем. Ключ очереди: у двух агентов одного владельца
    # пересборки идут независимо и друг друга не прерывают.
    agent_id: Optional[int] = None
    document_ids: Optional[List[int]] = None
    embedding_model: Optional[str] = None
    embedding_provider: Optional[str] = None


def _block_search_on_reindex() -> bool:
    """Отбивать ли поиск во время ГЛОБАЛЬНОГО реиндекса."""
    v = os.getenv("RAG_BLOCK_SEARCH_DURING_REINDEX", "true").strip().lower()
    return v in ("1", "true", "yes", "on")


def _kb_reindex_key(owner_user_id: Optional[str], agent_id: Optional[int]) -> str:
    """Ключ очереди. Без фильтров — кластерный прогон, он эксклюзивный."""
    from app.services.reindex_queue import GLOBAL_KEY, entity_key

    if agent_id is not None:
        return entity_key("agent", agent_id)
    owner = (owner_user_id or "").strip().lower()
    if owner:
        return entity_key("owner", owner)
    return GLOBAL_KEY


async def _kb_reindex_bg(
    kb: KbService,
    chunk_size: Optional[int],
    chunk_overlap: Optional[int],
    chunking_strategy: Optional[str],
    owner_user_id: Optional[str] = None,
    document_ids: Optional[List[int]] = None,
    embedding_model: Optional[str] = None,
    embedding_provider: Optional[str] = None,
    agent_id: Optional[int] = None,
) -> None:
    from app.services.kb_service import bump_kb_reindex_generation
    from app.services.reindex_queue import kb_queue

    key = _kb_reindex_key(owner_user_id, agent_id)
    # Поколение поднимаем ДО очереди: если этот же агент уже пересобирается, его
    # текущий проход должен прерваться — настройки успели поменяться.
    gen = bump_kb_reindex_generation(key)

    async def _job() -> None:
        res = await kb.reindex_all(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunking_strategy=chunking_strategy,
            generation=gen,
            generation_key=key,
            owner_user_id=owner_user_id,
            document_ids=document_ids,
            model=embedding_model,
            provider=embedding_provider,
        )
        logger.info("[REINDEX kb] %s: перечанкировка завершена: %s", key, res)

    await kb_queue.run(
        key,
        gen,
        _job,
        meta={
            "scope": (
                "agent"
                if agent_id is not None
                else ("owner" if owner_user_id else "all")
            ),
            "entity_id": str(agent_id) if agent_id is not None else None,
            "owner_user_id": (owner_user_id or "").strip().lower() or None,
        },
    )


@router.post("/reindex")
async def kb_reindex(
    body: KbReindexRequest,
    background: BackgroundTasks,
    kb: KbService = Depends(get_kb_service),
):
    """Запустить перечанкировку БЗ В ФОНЕ.

    С owner_user_id / document_ids — scoped.
    Без фильтра — вся KB (кластерный путь после смены dim).
    """
    background.add_task(
        _kb_reindex_bg,
        kb,
        body.chunk_size,
        body.chunk_overlap,
        body.chunking_strategy,
        body.owner_user_id,
        body.document_ids,
        body.embedding_model,
        body.embedding_provider,
        body.agent_id,
    )
    return {
        "ok": True,
        "status": "started",
        "owner_user_id": body.owner_user_id,
        "agent_id": body.agent_id,
        "document_ids": body.document_ids,
        "queue_key": _kb_reindex_key(body.owner_user_id, body.agent_id),
    }


@router.get("/reindex/status")
async def kb_reindex_status():
    """Статус пересборки БЗ.

    ``reindexing`` и ``owner_user_id`` сохранены для прежней версии backend:
    первый теперь значит «занят хотя бы один слот». Что именно пересобирается —
    в ``active``.
    """
    from app.services.reindex_queue import kb_queue

    return kb_queue.status()
