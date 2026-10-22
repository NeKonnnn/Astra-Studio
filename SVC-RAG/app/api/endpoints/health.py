# Проверка готовности: сам сервис, провайдер моделей RAG, PostgreSQL
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.clients.rag_models_client import RagModelsClient
from app.dependencies import get_db, get_current_rag_client, get_model_choice

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    rag_models: bool
    postgresql: bool
    # Активный источник моделей по типам: native / id провайдера
    # По этим полям backend делает reconcile после рестарта svc-rag.
    embedding_provider: str = "native"
    embedding_model: Optional[str] = None
    reranker_provider: str = "native"
    reranker_model: Optional[str] = None
    embedding_routing: Optional[Dict[str, Any]] = Field(default=None)


@router.get("/health", response_model=HealthResponse)
async def health():
    """Готовность сервиса и зависимостей (модели RAG, PostgreSQL)."""
    pg_ok = False
    try:
        db = await get_db()
        pg_ok = await db.health_check()
    except Exception:
        pass
    client = None
    try:
        client = get_current_rag_client()
    except Exception:
        pass
    if client is None:
        # До первого обращения к сервисам клиент ещё не создан - проверяем native.
        client = RagModelsClient()
    rag_ok = False
    try:
        rag_ok = await client.health()
    except Exception:
        pass
    choice = {}
    try:
        choice = get_model_choice()
    except Exception:
        pass
    emb = choice.get("embedding") or {}
    rer = choice.get("reranker") or {}
    status = "healthy" if (pg_ok and rag_ok) else "degraded"
    try:
        from app.services.embed_routing import routing_overview

        routing = routing_overview()
    except Exception:
        # Не ломаем /health из-за диагностики роутинга (тест + prod).
        routing = None
    return HealthResponse(
        status=status,
        rag_models=rag_ok,
        postgresql=pg_ok,
        embedding_provider=str(emb.get("provider") or "native"),
        embedding_model=emb.get("model"),
        reranker_provider=str(rer.get("provider") or "native"),
        reranker_model=rer.get("model"),
        embedding_routing=routing,
    )
