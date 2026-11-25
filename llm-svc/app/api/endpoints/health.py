import logging
from fastapi import APIRouter, Depends
from app.models.schemas import HealthResponse
from app.services.llama_handler import LlamaHandler
from app.api.dependencies import get_llm_handler_without_loaded_gate


logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/health", response_model=HealthResponse)
async def health_check(
    llama_service: LlamaHandler = Depends(get_llm_handler_without_loaded_gate),
):
    """Проверка статуса сервиса и загрузки модели."""
    logger.info("Health requested")
    model_name = llama_service.model_name if llama_service.is_loaded() else None
    loaded_models = None
    if hasattr(llama_service, "get_loaded_model_ids"):
        loaded_models = llama_service.get_loaded_model_ids() or None
    if model_name:
        logger.info(f"Health check: Model '{model_name}' is {'loaded' if llama_service.is_loaded() else 'not loaded'}")
    return HealthResponse(
        status="healthy",
        model_loaded=llama_service.is_loaded(),
        model_name=model_name,
        loaded_models=loaded_models,
    )