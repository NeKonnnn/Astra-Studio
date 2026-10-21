"""
routes/context_prompts.py - персональные контекстные промпты пользователя
"""

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.auth.jwt_handler import get_current_user
from backend.services.user_llm_settings import (
    delete_user_custom_prompt,
    get_user_context_prompts,
    get_user_prompt_manager,
    set_user_custom_prompt,
    set_user_global_prompt,
    set_user_model_prompt,
)
from backend.settings.cef_logger.cef_logger import log_cef_event
from backend.settings.logging import get_logger
from backend.utils.http_errors import public_error_detail

logger = get_logger(__name__)

router = APIRouter(prefix="/api/context-prompts", tags=["context-prompts"])


def _uid(current_user: dict) -> str:
    return str(current_user.get("user_id") or current_user.get("username") or "").strip()


@router.get("/global")
async def get_global_prompt(current_user: Annotated[dict, Depends(get_current_user)]):
    try:
        data = await get_user_context_prompts(_uid(current_user))
        return {
            "prompt": data.get("global_prompt", ""),
            "success": True,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e))


@router.put("/global")
async def update_global_prompt(
    payload: dict, http_request: Request, current_user: Annotated[dict, Depends(get_current_user)]
):
    try:
        await set_user_global_prompt(_uid(current_user), payload.get("prompt", ""))
        log_cef_event(
            "SEC005",
            request=http_request,
            current_user=current_user,
            status_code=200,
            extra={
                "cs1": "user-context-prompts",
                "cs1Label": "SettingsSection",
                "cs2": "USER",
                "cs2Label": "TargetRole",
                "cs3": "user global prompt changed",
                "cs3Label": "ChangedPermissions",
            },
        )
        return {"message": "Глобальный промпт обновлен", "success": True, "timestamp": datetime.now().isoformat()}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e))


@router.get("/models")
async def get_models_with_prompts(current_user: Annotated[dict, Depends(get_current_user)]):
    try:
        from backend.routes.models import get_available_models

        data = await get_available_models()
        mgr = await get_user_prompt_manager(_uid(current_user))
        models = mgr.enrich_models_with_prompts(data.get("models") or [])
        return {
            "models": models,
            "success": True,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e))


@router.get("/model/{model_path:path}")
async def get_model_prompt(model_path: str, current_user: Annotated[dict, Depends(get_current_user)]):
    try:
        mgr = await get_user_prompt_manager(_uid(current_user))
        return {
            "model_path": model_path,
            "prompt": mgr.get_model_prompt(model_path),
            "success": True,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e))


@router.put("/model/{model_path:path}")
async def update_model_prompt(
    model_path: str, request: dict, current_user: Annotated[dict, Depends(get_current_user)]
):
    try:
        await set_user_model_prompt(_uid(current_user), model_path, request.get("prompt", ""))
        return {
            "message": f"Промпт для модели {model_path} обновлен",
            "success": True,
            "timestamp": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e))


@router.get("/custom")
async def get_custom_prompts(current_user: Annotated[dict, Depends(get_current_user)]):
    try:
        data = await get_user_context_prompts(_uid(current_user))
        return {
            "prompts": data.get("custom_prompts") or {},
            "success": True,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e))


@router.post("/custom")
async def create_custom_prompt(request: dict, current_user: Annotated[dict, Depends(get_current_user)]):
    try:
        prompt_id = request.get("id", "").strip()
        prompt = request.get("prompt", "").strip()
        if not prompt_id or not prompt:
            raise HTTPException(status_code=400, detail="ID и промпт обязательны")
        await set_user_custom_prompt(_uid(current_user), prompt_id, prompt, request.get("description", ""))
        return {"message": f"Промпт '{prompt_id}' создан", "success": True, "timestamp": datetime.now().isoformat()}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e))


@router.delete("/custom/{prompt_id}")
async def delete_custom_prompt(prompt_id: str, current_user: Annotated[dict, Depends(get_current_user)]):
    try:
        if not await delete_user_custom_prompt(_uid(current_user), prompt_id):
            raise HTTPException(status_code=404, detail="Промпт не найден")
        return {"message": f"Промпт '{prompt_id}' удален", "success": True, "timestamp": datetime.now().isoformat()}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e))


@router.get("/effective/{model_path:path}")
async def get_effective_prompt(
    model_path: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    custom_prompt_id: Optional[str] = None,
):
    try:
        mgr = await get_user_prompt_manager(_uid(current_user))
        prompt = mgr.get_effective_prompt(model_path, custom_prompt_id)
        return {
            "model_path": model_path,
            "custom_prompt_id": custom_prompt_id,
            "prompt": prompt,
            "success": True,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e))
