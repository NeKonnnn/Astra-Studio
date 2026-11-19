"""API: проверка и применение обновлений AstraStudio (для любого авторизованного пользователя)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.auth.jwt_handler import get_current_user
from backend.services import system_updates
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/updates", tags=["updates"])


@router.get("/check")
async def check_updates(current_user: Annotated[dict, Depends(get_current_user)]):
    _ = current_user
    try:
        return await system_updates.fetch_remote_version()
    except Exception as exc:
        logger.exception("update check failed")
        raise HTTPException(status_code=502, detail=f"Update check failed: {exc}") from exc


@router.get("/status")
async def update_status(current_user: Annotated[dict, Depends(get_current_user)]):
    _ = current_user
    return system_updates.get_job_snapshot()


@router.post("/apply")
async def apply_updates(current_user: Annotated[dict, Depends(get_current_user)]):
    _ = current_user
    try:
        return system_updates.start_apply()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("update apply failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
