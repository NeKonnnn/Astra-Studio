from fastapi import APIRouter, Depends

from app.core.api_key_auth import require_svc_rag_api_key

from .endpoints import diag, health, kb, memdiag, memory_rag, project_rag, schema

router = APIRouter()
# Health без ключа — для probe'ов k8s.
router.include_router(health.router, tags=["Здоровье"])

_auth = [Depends(require_svc_rag_api_key)]
router.include_router(kb.router, prefix="/kb", tags=["База Знаний"], dependencies=_auth)
router.include_router(
    memory_rag.router,
    prefix="/memory-rag",
    tags=["Библиотека памяти RAG"],
    dependencies=_auth,
)
router.include_router(
    project_rag.router, prefix="/project-rag", tags=["RAG проектов"], dependencies=_auth
)
router.include_router(
    diag.router, prefix="/diag", tags=["Диагностика RAG"], dependencies=_auth
)
router.include_router(
    schema.router, prefix="/schema", tags=["Схема векторов"], dependencies=_auth
)
router.include_router(
    memdiag.router,
    prefix="/memory-diag",
    tags=["Диагностика памяти"],
    dependencies=_auth,
)
