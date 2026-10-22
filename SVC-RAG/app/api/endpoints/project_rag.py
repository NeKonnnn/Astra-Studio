# API эндпоинты для RAG-файлов проектов
from typing import Any, Dict, List, Optional

import os

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)

from pydantic import BaseModel

from app.api.rag_common import (
    RagSearchEvalBody,
    RagSearchFiltersBody,
    eval_search_kwargs_from_body,
    filters_body_to_domain,
)
from app.dependencies import get_project_rag_service
from app.services.document_duplicate import DUPLICATE_CODE, duplicate_http_detail
from app.services.hit_types import hit_cosine
from app.services.project_rag_service import ProjectRagService
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


class ProjectRagIndexResponse(BaseModel):
    ok: bool
    document_id: Optional[int] = None
    filename: Optional[str] = None
    chunks_count: Optional[int] = None
    project_id: Optional[str] = None
    error: Optional[str] = None


class ProjectRagDocumentItem(BaseModel):
    id: int
    filename: str
    project_id: str
    created_at: Optional[str] = None
    size: Optional[int] = None
    file_type: Optional[str] = None


class ProjectRagSearchRequest(RagSearchEvalBody):
    query: str
    k: int = 8
    document_id: Optional[int] = None
    use_reranking: Optional[bool] = None
    strategy: Optional[str] = None
    vector_query: Optional[str] = None
    filters: Optional[RagSearchFiltersBody] = None
    debug_trace: bool = False
    # Модель пользователя (резолвит backend). None = кластерная.
    embedding_model: Optional[str] = None
    embedding_provider: Optional[str] = None
    reranker_model: Optional[str] = None
    reranker_provider: Optional[str] = None


class ProjectRagSearchHit(BaseModel):
    content: str
    score: float
    document_id: Optional[int] = None
    chunk_index: Optional[int] = None
    cosine: Optional[float] = None


class ProjectRagSearchResponse(BaseModel):
    hits: List[ProjectRagSearchHit]
    trace: Optional[Dict[str, Any]] = None


@router.post("/projects/{project_id}/documents", response_model=ProjectRagIndexResponse)
async def project_rag_upload(
    project_id: str,
    file: UploadFile = File(...),
    minio_object: Optional[str] = Form(None),
    minio_bucket: Optional[str] = Form(None),
    chunk_size: Optional[int] = Form(None),
    chunk_overlap: Optional[int] = Form(None),
    chunking_strategy: Optional[str] = Form(None),
    owner_user_id: Optional[str] = Form(None),
    uploaded_by: Optional[str] = Form(None),
    svc: ProjectRagService = Depends(get_project_rag_service),
    embedding_model: Optional[str] = Form(None),
    embedding_provider: Optional[str] = Form(None),
):
    """Загрузить документ в RAG-хранилище проекта."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Нужно имя файла")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Файл пустой")

    result = await svc.index_document(
        data,
        file.filename,
        project_id=project_id,
        minio_object=minio_object,
        minio_bucket=minio_bucket,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunking_strategy=chunking_strategy,
        owner_user_id=owner_user_id,
        uploaded_by=uploaded_by,
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
    return ProjectRagIndexResponse(
        ok=True,
        document_id=result.get("document_id"),
        filename=result.get("filename"),
        chunks_count=result.get("chunks_count"),
        project_id=project_id,
    )


class ProjectDuplicateCheckRequest(BaseModel):
    content_hash: str
    filename: Optional[str] = None
    size: Optional[int] = None


class ProjectDuplicateCheckResponse(BaseModel):
    duplicate: bool
    document_id: Optional[int] = None
    filename: Optional[str] = None


@router.post(
    "/projects/{project_id}/documents/duplicate-check",
    response_model=ProjectDuplicateCheckResponse,
)
async def project_rag_duplicate_check(
    project_id: str,
    body: ProjectDuplicateCheckRequest,
    svc: ProjectRagService = Depends(get_project_rag_service),
):
    out = await svc.check_duplicate(
        project_id=project_id,
        content_hash=body.content_hash,
        filename=body.filename,
        size=body.size,
    )
    return ProjectDuplicateCheckResponse(
        duplicate=bool(out.get("duplicate")),
        document_id=out.get("document_id"),
        filename=out.get("filename"),
    )


@router.get(
    "/projects/{project_id}/documents", response_model=List[ProjectRagDocumentItem]
)
async def project_rag_list(
    project_id: str,
    svc: ProjectRagService = Depends(get_project_rag_service),
):
    """Список документов RAG конкретного проекта."""
    docs = await svc.list_documents(project_id)
    return [
        ProjectRagDocumentItem(
            id=d["id"],
            filename=d["filename"],
            project_id=d["project_id"],
            created_at=d.get("created_at"),
            size=d.get("size"),
            file_type=d.get("file_type"),
        )
        for d in docs
    ]


class ProjectDocumentChunkHit(BaseModel):
    content: str
    document_id: int
    chunk_index: int


class ProjectDocumentChunksResponse(BaseModel):
    chunks: List[ProjectDocumentChunkHit]


@router.get(
    "/projects/{project_id}/documents/{document_id}/chunks",
    response_model=ProjectDocumentChunksResponse,
)
async def project_rag_get_document_chunks(
    project_id: str,
    document_id: int,
    start: int = Query(0, ge=0),
    limit: int = Query(3, ge=1, le=10),
    svc: ProjectRagService = Depends(get_project_rag_service),
):
    """Первые чанки документа проекта (оглавление/структура). Не /v1/documents — там роутера нет."""
    result = await svc.get_document_chunks(
        project_id, document_id, start=start, limit=limit
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Документ не найден в проекте")
    return ProjectDocumentChunksResponse(
        chunks=[
            ProjectDocumentChunkHit(content=c, document_id=doc_id, chunk_index=idx)
            for c, doc_id, idx in result
        ]
    )


@router.delete("/projects/{project_id}/documents/{document_id}")
async def project_rag_delete_document(
    project_id: str,
    document_id: int,
    svc: ProjectRagService = Depends(get_project_rag_service),
):
    """Удалить один документ из RAG-хранилища проекта."""
    out = await svc.delete_document(document_id, project_id=project_id)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail="Документ не найден")
    return out


@router.delete("/projects/{project_id}")
async def project_rag_delete_project(
    project_id: str,
    svc: ProjectRagService = Depends(get_project_rag_service),
):
    """Удалить все RAG-документы проекта (вызывается при удалении проекта)."""
    out = await svc.delete_by_project(project_id)
    return out


@router.post("/projects/{project_id}/search", response_model=ProjectRagSearchResponse)
async def project_rag_search(
    project_id: str,
    body: ProjectRagSearchRequest,
    svc: ProjectRagService = Depends(get_project_rag_service),
):
    """Семантический поиск по RAG-документам проекта."""
    # Пересборка одного проекта чужие документы не трогает — отбиваем только
    # кластерный прогон, он перетряхивает всю таблицу.
    from app.services.reindex_queue import project_queue

    if _block_search_on_reindex() and project_queue.cluster_running():
        raise HTTPException(
            status_code=409,
            detail="Идёт полная переиндексация проектов — поиск временно недоступен",
        )
    payload = await svc.search(
        query=body.query,
        project_id=project_id,
        k=body.k,
        document_id=body.document_id,
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
    return ProjectRagSearchResponse(
        hits=[
            ProjectRagSearchHit(
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


class ProjectRagReindexRequest(BaseModel):
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    chunking_strategy: Optional[str] = None
    owner_user_id: Optional[str] = None
    project_id: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_provider: Optional[str] = None


def _block_search_on_reindex() -> bool:
    """Отбивать ли поиск во время ГЛОБАЛЬНОГО реиндекса."""
    v = os.getenv("RAG_BLOCK_SEARCH_DURING_REINDEX", "true").strip().lower()
    return v in ("1", "true", "yes", "on")


def _project_reindex_key(
    owner_user_id: Optional[str], project_id: Optional[str]
) -> str:
    """Ключ очереди. Без фильтров — кластерный прогон, он эксклюзивный."""
    from app.services.reindex_queue import GLOBAL_KEY, entity_key

    pid = (project_id or "").strip()
    if pid:
        return entity_key("project", pid)
    owner = (owner_user_id or "").strip().lower()
    if owner:
        return entity_key("owner", owner)
    return GLOBAL_KEY


async def _project_reindex_all_bg(
    svc: ProjectRagService,
    chunk_size: Optional[int],
    chunk_overlap: Optional[int],
    chunking_strategy: Optional[str],
    owner_user_id: Optional[str] = None,
    embedding_model: Optional[str] = None,
    embedding_provider: Optional[str] = None,
    project_id: Optional[str] = None,
) -> None:
    from app.services.project_rag_service import bump_project_reindex_generation
    from app.services.reindex_queue import project_queue

    pid = (project_id or "").strip() or None
    key = _project_reindex_key(owner_user_id, pid)
    # Поколение поднимаем ДО очереди: если этот же проект уже пересобирается, его
    # текущий проход должен прерваться — настройки успели поменяться.
    gen = bump_project_reindex_generation(key)

    async def _job() -> None:
        if pid:
            res = await svc.reindex_all(
                pid,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                chunking_strategy=chunking_strategy,
                generation=gen,
                generation_key=key,
                model=embedding_model,
                provider=embedding_provider,
            )
            logger.info("[REINDEX project=%s] перечанкировка завершена: %s", pid, res)
            return
        res = await svc.reindex_all_projects(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunking_strategy=chunking_strategy,
            generation=gen,
            generation_key=key,
            owner_user_id=owner_user_id,
            model=embedding_model,
            provider=embedding_provider,
        )
        logger.info("[REINDEX project ALL] перечанкировка завершена: %s", res)

    await project_queue.run(
        key,
        gen,
        _job,
        meta={
            "scope": "project" if pid else ("owner" if owner_user_id else "all"),
            "entity_id": pid,
            "owner_user_id": (owner_user_id or "").strip().lower() or None,
        },
    )


@router.post("/reindex")
async def project_rag_reindex_all(
    body: ProjectRagReindexRequest,
    background: BackgroundTasks,
    svc: ProjectRagService = Depends(get_project_rag_service),
):
    """Перечанкировать проекты В ФОНЕ.

    С ```project_id``` — только один проект.
    С ```owner_user_id``` без ```project_id``` — все проекты пользователя.
    Без фильтра — все проекты (кластерный путь после смены dim).
    """
    background.add_task(
        _project_reindex_all_bg,
        svc,
        body.chunk_size,
        body.chunk_overlap,
        body.chunking_strategy,
        body.owner_user_id,
        body.embedding_model,
        body.embedding_provider,
        body.project_id,
    )
    return {
        "ok": True,
        "status": "started",
        "owner_user_id": body.owner_user_id,
        "project_id": body.project_id,
        "queue_key": _project_reindex_key(body.owner_user_id, body.project_id),
    }


@router.get("/reindex/status")
async def project_rag_reindex_status():
    """Статус пересборки проектов.

    ``reindexing``, ``owner_user_id`` и ``project_id`` сохранены для прежней
    версии backend: первый теперь значит «занят хотя бы один слот». Что именно
    пересобирается — в ``active``.
    """
    from app.services.reindex_queue import project_queue

    status = project_queue.status()
    active = status.get("active") or []
    single = active[0] if len(active) == 1 else {}
    status["project_id"] = (
        single.get("entity_id") if single.get("scope") == "project" else None
    )
    return status
