from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.dependencies.rag_models_handler import get_rag_models_handler
from app.core.config import settings

router = APIRouter()


@router.get("/health")
async def health():
    """Процесс жив → 200 (compose healthcheck).

    Без скачанных весов возвращаем status=degraded, а не 503 — иначе свежий
    one-liner не поднимает svc-rag/backend, и UI для загрузки моделей недоступен.
    """
    if not settings.rag_models.enabled:
        return JSONResponse(
            content={
                "status": "disabled",
                "service": "rag-models",
                "embedding_loaded": False,
                "reranker_loaded": False,
            }
        )
    handler = await get_rag_models_handler()
    if handler is None:
        return JSONResponse(
            content={
                "status": "degraded",
                "service": "rag-models",
                "embedding_loaded": False,
                "reranker_loaded": False,
                "models_dir": settings.rag_models.models_dir,
                "detail": "models not loaded yet — download via UI",
            }
        )
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "rag-models",
            "embedding_loaded": True,
            "reranker_loaded": True,
            "embedding_dim": handler.get("embedding_dim"),
            "device": handler.get("device"),
            "models_dir": settings.rag_models.models_dir,
        }
    )
