# Библиотека документов памяти (настройки)
from typing import Any, Dict, List, Optional

import asyncio
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
from app.dependencies import get_memory_rag_service
from app.services.document_duplicate import DUPLICATE_CODE, duplicate_http_detail
from app.services.hit_types import hit_cosine
from app.services.memory_rag_service import MemoryRagService
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


class MemoryRagIndexResponse(BaseModel):
    ok: bool
    document_id: Optional[int] = None
    filename: Optional[str] = None
    chunks_count: Optional[int] = None
    error: Optional[str] = None


class MemoryRagDocumentItem(BaseModel):
    id: int
    filename: str
    created_at: Optional[str] = None
    size: Optional[int] = None
    file_type: Optional[str] = None


class MemoryRagSearchRequest(RagSearchEvalBody):
    query: str
    k: int = 8
    document_id: Optional[int] = None
    use_reranking: Optional[bool] = None
    strategy: Optional[str] = None
    vector_query: Optional[str] = None
    filters: Optional[RagSearchFiltersBody] = None
    # Модель Библиотеки из ENV бэкенда; пусто — кластерная, как раньше.
    embedding_model: Optional[str] = None
    embedding_provider: Optional[str] = None
    reranker_model: Optional[str] = None
    reranker_provider: Optional[str] = None
    debug_trace: bool = False


class MemoryRagSearchHit(BaseModel):
    content: str
    score: float
    document_id: Optional[int] = None
    chunk_index: Optional[int] = None
    cosine: Optional[float] = None


class MemoryRagSearchResponse(BaseModel):
    hits: List[MemoryRagSearchHit]
    trace: Optional[Dict[str, Any]] = None


@router.post("/documents", response_model=MemoryRagIndexResponse)
async def index_memory_rag_document(
    file: UploadFile = File(...),
    minio_object: Optional[str] = Form(None),
    minio_bucket: Optional[str] = Form(None),
    chunk_size: Optional[int] = Form(None),
    chunk_overlap: Optional[int] = Form(None),
    chunking_strategy: Optional[str] = Form(None),
    embedding_model: Optional[str] = Form(None),
    embedding_provider: Optional[str] = Form(None),
    svc: MemoryRagService = Depends(get_memory_rag_service),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Нужно имя файла")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Файл пустой")

    result = await svc.index_document(
        data,
        file.filename,
        minio_object=minio_object,
        minio_bucket=minio_bucket,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunking_strategy=chunking_strategy or "universal",
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
    return MemoryRagIndexResponse(
        ok=True,
        document_id=result.get("document_id"),
        filename=result.get("filename"),
        chunks_count=result.get("chunks_count"),
    )


class MemoryDuplicateCheckRequest(BaseModel):
    content_hash: str
    filename: Optional[str] = None
    size: Optional[int] = None


class MemoryDuplicateCheckResponse(BaseModel):
    duplicate: bool
    document_id: Optional[int] = None
    filename: Optional[str] = None


@router.post("/documents/duplicate-check", response_model=MemoryDuplicateCheckResponse)
async def memory_rag_duplicate_check(
    body: MemoryDuplicateCheckRequest,
    svc: MemoryRagService = Depends(get_memory_rag_service),
):
    out = await svc.check_duplicate(
        content_hash=body.content_hash,
        filename=body.filename,
        size=body.size,
    )
    return MemoryDuplicateCheckResponse(
        duplicate=bool(out.get("duplicate")),
        document_id=out.get("document_id"),
        filename=out.get("filename"),
    )


@router.get("/documents", response_model=List[MemoryRagDocumentItem])
async def list_memory_rag_documents(
    svc: MemoryRagService = Depends(get_memory_rag_service),
):
    docs = await svc.list_documents()
    return [
        MemoryRagDocumentItem(
            id=d["id"],
            filename=d["filename"],
            created_at=d.get("created_at"),
            size=d.get("size"),
            file_type=d.get("file_type"),
        )
        for d in docs
    ]


@router.delete("/documents/{document_id}")
async def delete_memory_rag_document(
    document_id: int,
    svc: MemoryRagService = Depends(get_memory_rag_service),
):
    out = await svc.delete_document(document_id)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail="Документ не найден")
    return out


class MemoryReindexRequest(BaseModel):
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    chunking_strategy: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_provider: Optional[str] = None


_memory_reindex_lock = asyncio.Lock()


async def _memory_reindex_bg(
    svc: MemoryRagService,
    chunk_size: Optional[int],
    chunk_overlap: Optional[int],
    chunking_strategy: Optional[str],
    embedding_model: Optional[str],
    embedding_provider: Optional[str],
) -> None:
    from app.services.memory_rag_service import (
        bump_memory_reindex_generation,
        current_memory_reindex_generation,
    )

    gen = bump_memory_reindex_generation()
    async with _memory_reindex_lock:
        if gen != current_memory_reindex_generation():
            logger.info("[REINDEX memory] пропуск: поколение устарело до старта")
            return
        try:
            res = await svc.reindex_all(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                chunking_strategy=chunking_strategy,
                model=embedding_model,
                provider=embedding_provider,
                generation=gen,
            )
            logger.info("[REINDEX memory] фоновая переиндексация завершена: %s", res)
        except Exception:
            logger.exception("[REINDEX memory] фоновая переиндексация упала")


@router.post("/reindex")
async def memory_rag_reindex(
    body: MemoryReindexRequest,
    background: BackgroundTasks,
    svc: MemoryRagService = Depends(get_memory_rag_service),
):
    """Переиндексировать всю Библиотеку В ФОНЕ (вектора из сохранённого текста)."""
    background.add_task(
        _memory_reindex_bg,
        svc,
        body.chunk_size,
        body.chunk_overlap,
        body.chunking_strategy,
        body.embedding_model,
        body.embedding_provider,
    )
    return {"ok": True, "status": "started"}


@router.get("/reindex/status")
async def memory_rag_reindex_status():
    return {"reindexing": _memory_reindex_lock.locked()}


def _block_search_on_reindex() -> bool:
    """Отбивать ли поиск во время реиндекса (как в kb/project, BUG-RAG-19)."""
    v = os.getenv("RAG_BLOCK_SEARCH_DURING_REINDEX", "true").strip().lower()
    return v in ("1", "true", "yes", "on")


@router.post("/search", response_model=MemoryRagSearchResponse)
async def memory_rag_search(
    body: MemoryRagSearchRequest,
    svc: MemoryRagService = Depends(get_memory_rag_service),
):
    if _block_search_on_reindex() and _memory_reindex_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="Идёт переиндексация Библиотеки — поиск временно недоступен",
        )
    payload = await svc.search(
        query=body.query,
        k=body.k,
        document_id=body.document_id,
        use_reranking=body.use_reranking,
        strategy=body.strategy,
        vector_query=body.vector_query,
        filters=filters_body_to_domain(body.filters),
        model=body.embedding_model,
        provider=body.embedding_provider,
        rerank_model=body.reranker_model,
        rerank_provider=body.reranker_provider,
        return_trace=True,
        **eval_search_kwargs_from_body(body),
    )
    results, trace = payload
    return MemoryRagSearchResponse(
        hits=[
            MemoryRagSearchHit(
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
