"""
routes/model_comparison.py - независимые настройки сравнения моделей
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from backend.app_state import get_model_comparison_models, set_model_comparison_models
from backend.auth.jwt_handler import get_current_user
from backend.schemas import ModelComparisonModelsRequest

router = APIRouter(
    prefix="/api/model-comparison",
    tags=["model-comparison"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/models")
async def get_models_for_comparison():
    return {"models": get_model_comparison_models(), "success": True}


@router.post("/models")
async def set_models_for_comparison(request: ModelComparisonModelsRequest):
    models = request.models or []
    if len(models) == 0:
        raise HTTPException(status_code=400, detail="Нужно выбрать хотя бы одну модель")
    if len(models) > 4:
        raise HTTPException(status_code=400, detail="Максимум 4 модели для сравнения")
    saved = set_model_comparison_models(models)
    return {
        "message": f"Модели для сравнения установлены: {', '.join(saved)}",
        "success": True,
        "models": saved,
        "timestamp": datetime.now().isoformat(),
    }
