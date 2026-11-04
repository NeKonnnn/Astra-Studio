"""Сервис записи и агрегации активности для admin-дашборда."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from backend.settings.logging import get_logger

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _schedule(coro_factory, *, label: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run() -> None:
        try:
            await coro_factory()
        except Exception:
            logger.exception("%s failed", label)

    try:
        loop.create_task(_run())
    except Exception:
        logger.exception("%s: create_task failed", label)


def schedule_record_login(user_id: str) -> None:
    uid = (user_id or "").strip()
    if not uid:
        return

    async def _go() -> None:
        from backend.database.init_db import get_admin_activity_repository

        await get_admin_activity_repository().record_login(uid)

    _schedule(_go, label=f"schedule_record_login:{uid}")


def schedule_record_logout(user_id: str) -> None:
    uid = (user_id or "").strip()
    if not uid:
        return

    async def _go() -> None:
        from backend.database.init_db import get_admin_activity_repository

        await get_admin_activity_repository().record_logout(uid)

    _schedule(_go, label=f"schedule_record_logout:{uid}")


def schedule_record_usage(
    *,
    entity_type: str,
    entity_id: str,
    user_id: str,
    action: str = "used",
    entity_name: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    uid = (user_id or "").strip()
    eid = str(entity_id or "").strip()
    et = (entity_type or "").strip().lower()
    if not uid or not eid or not et:
        return

    async def _go() -> None:
        from backend.database.init_db import get_admin_activity_repository

        await get_admin_activity_repository().record_usage_event(
            entity_type=et,
            entity_id=eid,
            user_id=uid,
            action=action,
            entity_name=entity_name,
            meta=meta,
        )

    _schedule(_go, label=f"schedule_record_usage:{et}:{eid}")


async def build_admin_activity(days: int = 14) -> Dict[str, Any]:
    """Сводка + дневной ряд для GET /api/admin/activity."""
    days = max(1, min(int(days or 14), 90))
    now = _utc_now()
    since = now - timedelta(days=days)
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)

    mongo_active_24h = 0
    mongo_active_7d = 0
    conv_touched_24h = 0
    mongo_by_day: Dict[str, Dict[str, int]] = {}

    try:
        from backend.database.init_db import conversation_repo

        if conversation_repo is not None:
            mongo_active_24h = await conversation_repo.count_active_users_since(since_24h)
            mongo_active_7d = await conversation_repo.count_active_users_since(since_7d)
            conv_touched_24h = await conversation_repo.count_conversations_touched_since(
                since_24h
            )
            mongo_by_day = await conversation_repo.activity_by_day_since(since)
    except Exception:
        logger.exception("admin activity: mongo aggregates failed")

    logins_24h = 0
    logins_7d = 0
    logouts_24h = 0
    logouts_7d = 0
    unique_logins_7d = 0
    auth_by_day: Dict[str, Dict[str, int]] = {}
    agents_7d = 0
    skills_7d = 0
    agents_by_day: Dict[str, int] = {}
    skills_by_day: Dict[str, int] = {}
    usage_24h: Dict[str, int] = {}
    usage_7d: Dict[str, int] = {}
    usage_by_day: Dict[str, int] = {}
    top_agents: list = []

    try:
        from backend.database.init_db import get_admin_activity_repository

        repo = get_admin_activity_repository()
        logins_24h = await repo.count_auth_since(since_24h, "login")
        logins_7d = await repo.count_auth_since(since_7d, "login")
        logouts_24h = await repo.count_auth_since(since_24h, "logout")
        logouts_7d = await repo.count_auth_since(since_7d, "logout")
        unique_logins_7d = await repo.count_unique_logins_since(since_7d)
        auth_by_day = await repo.auth_by_day(since)
        agents_7d = await repo.entities_created_since("agents", since_7d)
        skills_7d = await repo.entities_created_since("skills", since_7d)
        agents_by_day = await repo.entities_created_by_day("agents", since)
        skills_by_day = await repo.entities_created_by_day("skills", since)
        usage_24h = await repo.usage_summary_since(since_24h)
        usage_7d = await repo.usage_summary_since(since_7d)
        usage_by_day = await repo.usage_by_day(since)
        top_agents = await repo.top_entities(since_7d, "agent", limit=8)
    except Exception:
        logger.exception("admin activity: pg aggregates failed")

    from backend.database.postgresql.admin_activity_repository import merge_activity_series

    active_users_by_day = {
        day: int((vals or {}).get("active_users") or 0)
        for day, vals in mongo_by_day.items()
    }
    conv_updates_by_day = {
        day: int((vals or {}).get("conversation_updates") or 0)
        for day, vals in mongo_by_day.items()
    }
    logins_series = {d: int(v.get("login") or 0) for d, v in auth_by_day.items()}
    logouts_series = {d: int(v.get("logout") or 0) for d, v in auth_by_day.items()}

    series = merge_activity_series(
        days,
        active_users=active_users_by_day,
        logins=logins_series,
        logouts=logouts_series,
        conversation_updates=conv_updates_by_day,
        agents_created=agents_by_day,
        skills_created=skills_by_day,
        usage_total=usage_by_day,
    )

    return {
        "generated_at": now.isoformat(),
        "days": days,
        "summary": {
            "active_users_24h": mongo_active_24h,
            "active_users_7d": mongo_active_7d,
            "logins_24h": logins_24h,
            "logins_7d": logins_7d,
            "logouts_24h": logouts_24h,
            "logouts_7d": logouts_7d,
            "unique_logins_7d": unique_logins_7d,
            "conversations_touched_24h": conv_touched_24h,
            "agents_created_7d": agents_7d,
            "skills_created_7d": skills_7d,
            "usage_24h": usage_24h,
            "usage_7d": usage_7d,
        },
        "top_agents_7d": top_agents,
        "series": series,
    }


async def build_admin_auth_stats(days: int = 14, limit: int = 100) -> Dict[str, Any]:
    days = max(1, min(int(days or 14), 90))
    limit = max(1, min(int(limit or 100), 500))
    now = _utc_now()
    since = now - timedelta(days=days)
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)

    summary = {
        "logins_24h": 0,
        "logouts_24h": 0,
        "logins_7d": 0,
        "logouts_7d": 0,
        "unique_logins_7d": 0,
    }
    series = []
    by_user = []

    try:
        from backend.database.init_db import get_admin_activity_repository
        from backend.database.postgresql.admin_activity_repository import empty_day_series

        repo = get_admin_activity_repository()
        summary["logins_24h"] = await repo.count_auth_since(since_24h, "login")
        summary["logouts_24h"] = await repo.count_auth_since(since_24h, "logout")
        summary["logins_7d"] = await repo.count_auth_since(since_7d, "login")
        summary["logouts_7d"] = await repo.count_auth_since(since_7d, "logout")
        summary["unique_logins_7d"] = await repo.count_unique_logins_since(since_7d)
        auth_days = await repo.auth_by_day(since)
        by_user = await repo.auth_by_user(since, limit=limit)
        for day in empty_day_series(days):
            bucket = auth_days.get(day) or {}
            series.append(
                {
                    "date": day,
                    "logins": int(bucket.get("login") or 0),
                    "logouts": int(bucket.get("logout") or 0),
                }
            )
    except Exception:
        logger.exception("build_admin_auth_stats failed")

    return {
        "generated_at": now.isoformat(),
        "days": days,
        "summary": summary,
        "series": series,
        "by_user": by_user,
    }


async def build_admin_usage_stats(
    days: int = 14,
    entity_type: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    days = max(1, min(int(days or 14), 90))
    limit = max(1, min(int(limit or 50), 200))
    now = _utc_now()
    since = now - timedelta(days=days)
    since_7d = now - timedelta(days=7)

    from backend.database.postgresql.admin_activity_repository import (
        ENTITY_TYPES,
        empty_day_series,
    )

    et = (entity_type or "").strip().lower() or None
    if et and et not in ENTITY_TYPES:
        et = None

    summary_7d: Dict[str, int] = {t: 0 for t in ENTITY_TYPES}
    series = []
    top = []
    by_user = []
    by_type_day: Dict[str, Dict[str, int]] = {t: {} for t in ENTITY_TYPES}

    try:
        from backend.database.init_db import get_admin_activity_repository

        repo = get_admin_activity_repository()
        summary_7d = await repo.usage_summary_since(since_7d)
        for t in ENTITY_TYPES:
            by_type_day[t] = await repo.usage_by_day(since, t)

        if et:
            top = await repo.top_entities(since, et, limit=limit)
            by_user = await repo.usage_by_user_entity(since, et, limit=limit)
            day_map = by_type_day.get(et) or {}
            for day in empty_day_series(days):
                series.append({"date": day, "uses": int(day_map.get(day, 0))})
        else:
            # сводный ряд + топ по каждому типу
            for day in empty_day_series(days):
                row: Dict[str, Any] = {"date": day, "uses": 0}
                for t in ENTITY_TYPES:
                    n = int((by_type_day.get(t) or {}).get(day, 0))
                    row[t] = n
                    row["uses"] += n
                series.append(row)
            top = []
            for t in ENTITY_TYPES:
                top.extend(await repo.top_entities(since, t, limit=min(10, limit)))
            top.sort(key=lambda x: int(x.get("uses") or 0), reverse=True)
            top = top[:limit]
            by_user = []
            for t in ENTITY_TYPES:
                by_user.extend(
                    await repo.usage_by_user_entity(since, t, limit=min(20, limit))
                )
            by_user.sort(key=lambda x: int(x.get("uses") or 0), reverse=True)
            by_user = by_user[:limit]
    except Exception:
        logger.exception("build_admin_usage_stats failed")

    return {
        "generated_at": now.isoformat(),
        "days": days,
        "entity_type": et,
        "summary_7d": summary_7d,
        "series": series,
        "top": top,
        "by_user": by_user,
    }
