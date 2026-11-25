import logging
import os
from fastapi import APIRouter, Depends, HTTPException
from app.models.schemas import (
    ModelsListResponse,
    ModelInfoResponse,
    ModelLoadRequest,
    ModelLoadResponse,
    ModelPoolTrimResponse,
)
from app.api.dependencies import get_llm_handler_without_loaded_gate
from app.services.base_llm_handler import BaseLLMHandler
from app.services.llama_handler import LlamaHandler
from app.core.config import settings
from app.utils.gguf_paths import list_gguf_models

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/models", response_model=ModelsListResponse)
async def list_models(
    llama_service: BaseLLMHandler = Depends(get_llm_handler_without_loaded_gate),
):
    """Список доступных моделей. Не требует загруженной модели."""
    logger.info("Models list requested")
    models_list = []
    try:
        models_list = list_gguf_models()
        logger.info(f"Found {len(models_list)} GGUF models under models root")
    except Exception as e:
        logger.error(f"Error scanning models tree: {e}")
    if not models_list and llama_service.is_loaded() and llama_service.model_name:
        models_list.append({
            "id": llama_service.model_name,
            "object": "model",
            "owned_by": "local",
            "permissions": []
        })
    return ModelsListResponse(data=models_list)

@router.get("/models/current", response_model=ModelInfoResponse)
async def get_current_model_info(
    llama_service: BaseLLMHandler = Depends(get_llm_handler_without_loaded_gate),
):
    """Получить детальную информацию о текущей загруженной модели."""
    logger.info("Current model info requested")
    model_path = llama_service.model_path
    file_exists = os.path.exists(model_path) if model_path else False
    file_size = os.path.getsize(model_path) if file_exists else None
    file_size_mb = round(file_size / (1024 * 1024), 2) if file_size else None
    loaded = llama_service.is_loaded()
    return ModelInfoResponse(
        model_name=llama_service.model_name if loaded else None,
        model_path=model_path,
        is_loaded=loaded,
        context_size=llama_service.n_ctx if loaded else None,
        gpu_layers=llama_service.n_gpu_layers if loaded else None,
        file_size=file_size,
        file_size_mb=file_size_mb,
        file_exists=file_exists,
        config_name=settings.model.name
    )

@router.post("/models/load", response_model=ModelLoadResponse)
async def load_model(
    request: ModelLoadRequest,
    llama_service: BaseLLMHandler = Depends(get_llm_handler_without_loaded_gate),
):
    """Загрузить/переключить модель по имени (имя файла без .gguf) или по полному пути."""
    model_name = (request.model or "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model name is required")
    try:
        success = await llama_service.load_model(model_name)
        if success:
            rid = (
                LlamaHandler.normalize_model_id(model_name)
                if isinstance(llama_service, LlamaHandler)
                else model_name
            )
            return ModelLoadResponse(
                success=True,
                message=f"Model '{rid}' loaded successfully",
                model_name=rid,
            )
        return ModelLoadResponse(
            success=False,
            message=f"Failed to load model '{model_name}' (file not found or load error)"
        )
    except Exception as e:
        logger.exception(f"Error loading model {model_name}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/unload-excess", response_model=ModelPoolTrimResponse)
async def unload_excess_models(
    llama_service: BaseLLMHandler = Depends(get_llm_handler_without_loaded_gate),
):
    """
    Оставить в пуле только модель из конфига сервиса; остальные выгрузить
    Вызывается бэкендом при выходе из режима multi-LLM
    """
    if not isinstance(llama_service, LlamaHandler):
        return ModelPoolTrimResponse(
            success=True,
            message="Pool trim applies only to llama.cpp handler; skipped",
            remaining_models=None,
        )
    try:
        ok = await llama_service.trim_pool_to_config_default()
        remaining = llama_service.get_loaded_model_ids()
        return ModelPoolTrimResponse(
            success=ok,
            message="Pool trimmed to config default model" if ok else "Trim completed with warnings",
            remaining_models=remaining or None,
        )
    except Exception as e:
        logger.exception("unload-excess failed")
        raise HTTPException(status_code=500, detail=str(e))
