"""
routes/memory.py - история диалогов и настройки памяти
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from backend.utils.http_errors import public_error_detail

import backend.app_state as state
from backend.app_state import get_conversation_repository, save_app_settings
from backend.auth.jwt_handler import get_current_user
from backend.schemas import MemorySettings
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["memory"])


@router.get("/api/history")
async def get_chat_history(
    current_user: Annotated[dict, Depends(get_current_user)], limit: int = None
):
    """История диалогов ТЕКУЩЕГО пользователя.

    Раньше endpoint был без авторизации и читал общий на процесс "текущий
    диалог" — из-за чего пользователи могли видеть чужие сообщения. Теперь
    выборка строго по user_id из репозитория диалогов.
    """
    if limit is None:
        limit = state.memory_max_messages
    repo = get_conversation_repository() if get_conversation_repository else None
    if repo is None:
        raise HTTPException(status_code=503, detail="Memory service недоступен")
    try:
        conversations = await repo.get_user_conversations(
            user_id=current_user["user_id"], limit=200
        )
        history = []
        for conv in conversations:
            for msg in conv.messages or []:
                history.append(
                    {
                        "role": msg.role,
                        "content": msg.content,
                        "timestamp": (
                            msg.timestamp.isoformat() if msg.timestamp else None
                        ),
                        "conversation_id": conv.conversation_id,
                    }
                )
        history.sort(key=lambda e: e.get("timestamp") or "")
        if limit and len(history) > limit:
            history = history[-limit:]
        return {
            "history": history,
            "count": len(history),
            "max_messages": state.memory_max_messages,
            "timestamp": datetime.now().isoformat(),
            "source": "memory_service",
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.delete("/api/history")
async def clear_chat_history(current_user: Annotated[dict, Depends(get_current_user)]):
    """Очистка истории ТЕКУЩЕГО пользователя (только свои диалоги)."""
    repo = get_conversation_repository() if get_conversation_repository else None
    if repo is None:
        raise HTTPException(status_code=503, detail="Memory service недоступен")
    try:
        deleted = await repo.delete_user_conversations(current_user["user_id"])
        return {
            "message": "История очищена",
            "success": True,
            "deleted": deleted,
            "source": "memory_service",
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/api/memory/settings")
async def get_memory_settings(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return {
        "max_messages": state.memory_max_messages,
        "include_system_prompts": state.memory_include_system_prompts,
        "clear_on_restart": state.memory_clear_on_restart,
    }


@router.put("/api/memory/settings")
async def update_memory_settings(
    settings_data: MemorySettings,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    try:
        state.memory_max_messages = settings_data.max_messages
        state.memory_include_system_prompts = settings_data.include_system_prompts
        state.memory_clear_on_restart = settings_data.clear_on_restart
        save_app_settings(
            {
                "memory_max_messages": state.memory_max_messages,
                "memory_include_system_prompts": state.memory_include_system_prompts,
                "memory_clear_on_restart": state.memory_clear_on_restart,
            }
        )
        return {"message": "Настройки памяти обновлены", "success": True}
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/api/memory/status")
async def get_memory_status(current_user: Annotated[dict, Depends(get_current_user)]):
    repo = get_conversation_repository() if get_conversation_repository else None
    if repo is None:
        raise HTTPException(status_code=503, detail="Memory service недоступен")
    try:
        conversations = await repo.get_user_conversations(
            user_id=current_user["user_id"], limit=200
        )
        message_count = sum(len(conv.messages or []) for conv in conversations)
        return {
            "message_count": message_count,
            "max_messages": state.memory_max_messages,
            "success": True,
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/api/memory/clear")
async def clear_memory(current_user: Annotated[dict, Depends(get_current_user)]):
    repo = get_conversation_repository() if get_conversation_repository else None
    if repo is None:
        raise HTTPException(status_code=503, detail="Memory service недоступен")
    try:
        deleted = await repo.delete_user_conversations(current_user["user_id"])
        return {
            "message": "Память успешно очищена",
            "success": True,
            "result": {"deleted": deleted},
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(
            status_code=500, detail="Ошибка очистки памяти"
        ) from e
