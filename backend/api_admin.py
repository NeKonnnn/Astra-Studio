"""Admin API: активность платформы, usage сущностей, login/logout, пользователи."""

from __future__ import annotations

import io
import os
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.auth.jwt_handler import get_current_user, revoke_user_session
from backend.auth.local_auth import (
    hash_password,
    register_local_user,
    validate_password,
    validate_username,
)
from backend.auth.registration_policy import get_registration_policy
from backend.services.admin_activity import (
    build_admin_activity,
    build_admin_auth_stats,
    build_admin_usage_stats,
)
from backend.services.admin_export import build_admin_export
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(user: dict) -> None:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")


def _serialize_user(row: Dict[str, Any]) -> Dict[str, Any]:
    created = row.get("created_at")
    updated = row.get("updated_at")
    return {
        "username": row["username"],
        "is_admin": bool(row.get("is_admin")),
        "is_active": bool(row.get("is_active", True)),
        "auth_source": "local",
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
        "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated,
    }


class AdminCreateUserRequest(BaseModel):
    username: str
    password: str
    is_admin: bool = False
    is_active: bool = True


class AdminUpdateUserRequest(BaseModel):
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=1)


class AdminRegistrationPolicyUpdate(BaseModel):
    """Только для отображения: фактический toggle — через ENV/рестарт.

    Для runtime без рестарта пишем в process env текущего воркера
    (достаточно для single-worker; в multi-worker нужен общий ConfigMap).
    """

    registration_enabled: Optional[bool] = None
    default_is_admin: Optional[bool] = None
    bootstrap_admin: Optional[bool] = None
    min_password_length: Optional[int] = Field(default=None, ge=4, le=72)


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


@router.get("/users")
async def list_admin_users(
    current_user: Annotated[dict, Depends(get_current_user)],
    search: Annotated[Optional[str], Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Dict[str, Any]:
    """Список локальных пользователей (PostgreSQL local_users)."""
    _require_admin(current_user)
    try:
        from backend.database.init_db import get_local_user_repository

        repo = get_local_user_repository()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="База локальных пользователей недоступна"
        ) from exc
    rows = await repo.list_users(search=search, limit=limit, offset=offset)
    total = await repo.count_users(search=search)
    return {
        "users": [_serialize_user(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "registration_policy": get_registration_policy(),
    }


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_admin_user(
    payload: AdminCreateUserRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Dict[str, Any]:
    """Создать локального пользователя от имени админа."""
    _require_admin(current_user)
    err = validate_username(payload.username)
    if err:
        raise HTTPException(status_code=400, detail=err)
    err = validate_password(payload.password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        profile = await register_local_user(
            payload.username,
            payload.password,
            is_admin=bool(payload.is_admin),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if payload.is_active is False:
        from backend.database.init_db import get_local_user_repository

        repo = get_local_user_repository()
        row = await repo.update_user(payload.username.strip(), is_active=False)
        if row:
            return _serialize_user(row)

    return {
        "username": profile["username"],
        "is_admin": bool(profile.get("is_admin")),
        "is_active": bool(profile.get("is_active", True)),
        "auth_source": "local",
        "created_at": None,
        "updated_at": None,
    }


@router.patch("/users/{username}")
async def update_admin_user(
    username: str,
    payload: AdminUpdateUserRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Dict[str, Any]:
    """Изменить роль/активность/пароль локального пользователя."""
    _require_admin(current_user)
    uname = (username or "").strip()
    if not uname:
        raise HTTPException(status_code=400, detail="Укажите username")

    me = (current_user.get("username") or current_user.get("user_id") or "").strip()
    if payload.is_admin is False and me.lower() == uname.lower():
        raise HTTPException(
            status_code=400, detail="Нельзя снять права администратора с самого себя"
        )
    if payload.is_active is False and me.lower() == uname.lower():
        raise HTTPException(status_code=400, detail="Нельзя заблокировать самого себя")

    password_hash = None
    if payload.password is not None:
        err = validate_password(payload.password)
        if err:
            raise HTTPException(status_code=400, detail=err)
        password_hash = hash_password(payload.password)

    try:
        from backend.database.init_db import get_local_user_repository

        repo = get_local_user_repository()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="База локальных пользователей недоступна"
        ) from exc

    existing = await repo.get_by_username(uname)
    if not existing:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # Нельзя снять последнего активного админа.
    if payload.is_admin is False and existing.get("is_admin"):
        admins = await repo.count_admins()
        if admins <= 1:
            raise HTTPException(
                status_code=400,
                detail="Нельзя снять права у последнего активного администратора",
            )
    if payload.is_active is False and existing.get("is_admin"):
        # если блокируем админа — убедиться, что останутся другие
        others = await repo.count_admins()
        if others <= 1:
            raise HTTPException(
                status_code=400,
                detail="Нельзя заблокировать последнего активного администратора",
            )

    row = await repo.update_user(
        uname,
        is_admin=payload.is_admin,
        is_active=payload.is_active,
        password_hash=password_hash,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if payload.is_active is False:
        try:
            revoke_user_session(uname, None)
        except Exception:
            logger.exception("failed to revoke sessions for blocked user=%s", uname)

    return _serialize_user(row)


@router.delete("/users/{username}")
async def delete_admin_user(
    username: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Dict[str, Any]:
    """Удалить локального пользователя."""
    _require_admin(current_user)
    uname = (username or "").strip()
    me = (current_user.get("username") or current_user.get("user_id") or "").strip()
    if me.lower() == uname.lower():
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")

    try:
        from backend.database.init_db import get_local_user_repository

        repo = get_local_user_repository()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="База локальных пользователей недоступна"
        ) from exc

    existing = await repo.get_by_username(uname)
    if not existing:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if existing.get("is_admin"):
        admins = await repo.count_admins()
        if admins <= 1 and existing.get("is_active"):
            raise HTTPException(
                status_code=400,
                detail="Нельзя удалить последнего активного администратора",
            )

    ok = await repo.delete_user(uname)
    if not ok:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    try:
        revoke_user_session(uname, None)
    except Exception:
        logger.exception("failed to revoke sessions for deleted user=%s", uname)
    return {"ok": True, "username": uname}


@router.get("/registration-policy")
async def admin_get_registration_policy(
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Dict[str, Any]:
    _require_admin(current_user)
    return get_registration_policy()


@router.put("/registration-policy")
async def admin_put_registration_policy(
    payload: AdminRegistrationPolicyUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Dict[str, Any]:
    """Включить/выключить регистрацию и связанные дефолты (runtime ENV процесса)."""
    _require_admin(current_user)
    if payload.registration_enabled is not None:
        os.environ["AUTH_REGISTRATION_ENABLED"] = (
            "true" if payload.registration_enabled else "false"
        )
    if payload.default_is_admin is not None:
        os.environ["AUTH_REGISTER_DEFAULT_IS_ADMIN"] = (
            "true" if payload.default_is_admin else "false"
        )
    if payload.bootstrap_admin is not None:
        os.environ["AUTH_REGISTER_BOOTSTRAP_ADMIN"] = (
            "true" if payload.bootstrap_admin else "false"
        )
    if payload.min_password_length is not None:
        os.environ["AUTH_MIN_PASSWORD_LENGTH"] = str(int(payload.min_password_length))
    return get_registration_policy()

