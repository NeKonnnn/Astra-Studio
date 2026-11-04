"""Admin API: активность платформы, usage сущностей, login/logout."""

from __future__ import annotations

import io
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.auth.jwt_handler import get_current_user
from backend.services.admin_activity import (
    build_admin_activity,
    build_admin_auth_stats,
    build_admin_usage_stats,
)
from backend.services.admin_export import build_admin_export

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(user: dict) -> None:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/activity")
async def get_admin_activity(
    current_user: Annotated[dict, Depends(get_current_user)],
    days: Annotated[int, Query(ge=1, le=90, description="Глубина ряда, дней")] = 14,
) -> Dict[str, Any]:
    """Сводка активных пользователей, логинов и дневной тренд."""
    _require_admin(current_user)
    return await build_admin_activity(days=days)


@router.get("/auth-stats")
async def get_admin_auth_stats(
    current_user: Annotated[dict, Depends(get_current_user)],
    days: Annotated[int, Query(ge=1, le=90)] = 14,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Dict[str, Any]:
    """Логины/логауты: общая стата + разбивка по пользователям."""
    _require_admin(current_user)
    return await build_admin_auth_stats(days=days, limit=limit)


@router.get("/usage")
async def get_admin_usage(
    current_user: Annotated[dict, Depends(get_current_user)],
    days: Annotated[int, Query(ge=1, le=90)] = 14,
    entity_type: Annotated[
        Optional[str],
        Query(description="agent|skill|mcp|knowledge_base|plugin или пусто = все"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Dict[str, Any]:
    """Использование агентов/skills/MCP/БЗ/плагинов: топ и кто кем пользовался."""
    _require_admin(current_user)
    return await build_admin_usage_stats(
        days=days, entity_type=entity_type, limit=limit
    )


@router.get("/export")
@router.get("/export.xlsx")
async def export_admin_stats(
    current_user: Annotated[dict, Depends(get_current_user)],
    days: Annotated[int, Query(ge=1, le=90)] = 14,
    format: Annotated[
        str,
        Query(alias="format", description="xlsx | csv | json"),
    ] = "xlsx",
    entity_type: Annotated[
        Optional[str],
        Query(description="Фильтр usage-листов: agent|skill|mcp|knowledge_base|plugin"),
    ] = None,
) -> StreamingResponse:
    """Выгрузка сводки, сессий и usage (Excel / CSV / JSON)."""
    _require_admin(current_user)
    # `format` — query-параметр; локально не переименовываем, чтобы URL был ?format=
    export_fmt = format
    try:
        payload, filename, media_type = await build_admin_export(
            fmt=export_fmt,
            days=days,
            entity_type=entity_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Export failed") from exc
    return StreamingResponse(
        io.BytesIO(payload),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
