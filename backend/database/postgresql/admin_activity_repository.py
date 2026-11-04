"""Репозиторий админ-метрик: auth (login/logout) + usage сущностей."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.database.postgresql.connection import PostgreSQLConnection
from backend.settings.logging import get_logger

logger = get_logger(__name__)

ENTITY_TYPES = ("agent", "skill", "mcp", "knowledge_base", "plugin")
AUTH_EVENT_TYPES = ("login", "logout")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class AdminActivityRepository:
    """Логины/логауты и события использования агентов/skills/MCP/БЗ/плагинов."""

    def __init__(self, db_connection: PostgreSQLConnection):
        self.db_connection = db_connection

    async def create_tables(self) -> None:
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS admin_login_events (
                        id BIGSERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        event_type TEXT NOT NULL DEFAULT 'login'
                    )
                    """
                )
                await conn.execute(
                    """
                    ALTER TABLE admin_login_events
                    ADD COLUMN IF NOT EXISTS event_type TEXT NOT NULL DEFAULT 'login'
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_admin_login_events_logged_at
                    ON admin_login_events (logged_at DESC)
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_admin_login_events_user_logged
                    ON admin_login_events (user_id, logged_at DESC)
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_admin_login_events_type_at
                    ON admin_login_events (event_type, logged_at DESC)
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_presence (
                        user_id TEXT PRIMARY KEY,
                        last_login_at TIMESTAMPTZ,
                        last_seen_at TIMESTAMPTZ,
                        last_logout_at TIMESTAMPTZ
                    )
                    """
                )
                await conn.execute(
                    """
                    ALTER TABLE user_presence
                    ADD COLUMN IF NOT EXISTS last_logout_at TIMESTAMPTZ
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS admin_usage_events (
                        id BIGSERIAL PRIMARY KEY,
                        entity_type TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        entity_name TEXT,
                        user_id TEXT NOT NULL,
                        action TEXT NOT NULL DEFAULT 'used',
                        occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        meta JSONB NOT NULL DEFAULT '{}'::jsonb
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_admin_usage_occurred
                    ON admin_usage_events (occurred_at DESC)
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_admin_usage_type_at
                    ON admin_usage_events (entity_type, occurred_at DESC)
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_admin_usage_entity
                    ON admin_usage_events (entity_type, entity_id, occurred_at DESC)
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_admin_usage_user
                    ON admin_usage_events (user_id, occurred_at DESC)
                    """
                )
            logger.info("Таблицы admin_login_events / user_presence / admin_usage_events готовы")
        except Exception:
            logger.exception("Ошибка при создании таблиц admin activity")
            raise

    async def record_auth_event(self, user_id: str, event_type: str = "login") -> None:
        uid = (user_id or "").strip()
        et = (event_type or "login").strip().lower()
        if not uid or et not in AUTH_EVENT_TYPES:
            return
        now = _utc_now()
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO admin_login_events (user_id, logged_at, event_type)
                    VALUES ($1, $2, $3)
                    """,
                    uid,
                    now,
                    et,
                )
                if et == "login":
                    await conn.execute(
                        """
                        INSERT INTO user_presence (user_id, last_login_at, last_seen_at)
                        VALUES ($1, $2, $2)
                        ON CONFLICT (user_id) DO UPDATE SET
                            last_login_at = EXCLUDED.last_login_at,
                            last_seen_at = EXCLUDED.last_seen_at
                        """,
                        uid,
                        now,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO user_presence (user_id, last_logout_at, last_seen_at)
                        VALUES ($1, $2, $2)
                        ON CONFLICT (user_id) DO UPDATE SET
                            last_logout_at = EXCLUDED.last_logout_at,
                            last_seen_at = EXCLUDED.last_seen_at
                        """,
                        uid,
                        now,
                    )
        except Exception:
            logger.exception("admin auth event failed user_id=%s type=%s", uid, et)

    async def record_login(self, user_id: str) -> None:
        await self.record_auth_event(user_id, "login")

    async def record_logout(self, user_id: str) -> None:
        await self.record_auth_event(user_id, "logout")

    async def record_usage_event(
        self,
        *,
        entity_type: str,
        entity_id: str,
        user_id: str,
        action: str = "used",
        entity_name: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        et = (entity_type or "").strip().lower()
        eid = str(entity_id or "").strip()
        uid = (user_id or "").strip()
        if not et or not eid or not uid:
            return
        if et not in ENTITY_TYPES:
            return
        now = _utc_now()
        payload = meta if isinstance(meta, dict) else {}
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO admin_usage_events
                        (entity_type, entity_id, entity_name, user_id, action, occurred_at, meta)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                    """,
                    et,
                    eid,
                    (entity_name or "")[:255] or None,
                    uid,
                    (action or "used")[:64],
                    now,
                    json.dumps(payload, ensure_ascii=False, default=str),
                )
        except Exception:
            logger.exception(
                "admin_usage_events insert failed type=%s id=%s user=%s", et, eid, uid
            )

    async def count_auth_since(self, since: datetime, event_type: str = "login") -> int:
        since = _as_utc(since) or _utc_now()
        et = (event_type or "login").strip().lower()
        try:
            async with await self.db_connection.acquire() as conn:
                val = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM admin_login_events
                    WHERE logged_at >= $1 AND event_type = $2
                    """,
                    since,
                    et,
                )
            return int(val or 0)
        except Exception:
            logger.exception("count_auth_since failed type=%s", et)
            return 0

    async def count_logins_since(self, since: datetime) -> int:
        return await self.count_auth_since(since, "login")

    async def count_unique_logins_since(self, since: datetime) -> int:
        since = _as_utc(since) or _utc_now()
        try:
            async with await self.db_connection.acquire() as conn:
                val = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT user_id) FROM admin_login_events
                    WHERE logged_at >= $1 AND event_type = 'login'
                    """,
                    since,
                )
            return int(val or 0)
        except Exception:
            logger.exception("count_unique_logins_since failed")
            return 0

    async def auth_by_day(self, since: datetime) -> Dict[str, Dict[str, int]]:
        """{date: {login: N, logout: M}}."""
        since = _as_utc(since) or _utc_now()
        out: Dict[str, Dict[str, int]] = {}
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT (logged_at AT TIME ZONE 'UTC')::date AS d,
                           event_type,
                           COUNT(*) AS cnt
                    FROM admin_login_events
                    WHERE logged_at >= $1
                    GROUP BY 1, 2
                    ORDER BY 1
                    """,
                    since,
                )
            for row in rows:
                day = row["d"].isoformat()
                bucket = out.setdefault(day, {"login": 0, "logout": 0})
                et = str(row["event_type"] or "login")
                if et in bucket:
                    bucket[et] = int(row["cnt"] or 0)
        except Exception:
            logger.exception("auth_by_day failed")
        return out

    async def logins_by_day(self, since: datetime) -> Dict[str, int]:
        by = await self.auth_by_day(since)
        return {d: int(v.get("login") or 0) for d, v in by.items()}

    async def auth_by_user(self, since: datetime, limit: int = 100) -> List[Dict[str, Any]]:
        since = _as_utc(since) or _utc_now()
        limit = max(1, min(int(limit or 100), 500))
        out: List[Dict[str, Any]] = []
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        e.user_id,
                        COUNT(*) FILTER (WHERE e.event_type = 'login') AS logins,
                        COUNT(*) FILTER (WHERE e.event_type = 'logout') AS logouts,
                        MAX(e.logged_at) FILTER (WHERE e.event_type = 'login') AS last_login_at,
                        MAX(e.logged_at) FILTER (WHERE e.event_type = 'logout') AS last_logout_at
                    FROM admin_login_events e
                    WHERE e.logged_at >= $1
                    GROUP BY e.user_id
                    ORDER BY logins DESC, logouts DESC
                    LIMIT $2
                    """,
                    since,
                    limit,
                )
            for row in rows:
                out.append(
                    {
                        "user_id": row["user_id"],
                        "logins": int(row["logins"] or 0),
                        "logouts": int(row["logouts"] or 0),
                        "last_login_at": row["last_login_at"].isoformat()
                        if row["last_login_at"]
                        else None,
                        "last_logout_at": row["last_logout_at"].isoformat()
                        if row["last_logout_at"]
                        else None,
                    }
                )
        except Exception:
            logger.exception("auth_by_user failed")
        return out

    async def entities_created_by_day(
        self, table: str, since: datetime
    ) -> Dict[str, int]:
        if table not in ("agents", "skills"):
            return {}
        since = _as_utc(since) or _utc_now()
        out: Dict[str, int] = {}
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT (created_at AT TIME ZONE 'UTC')::date AS d, COUNT(*) AS cnt
                    FROM {table}
                    WHERE created_at >= $1
                    GROUP BY 1
                    ORDER BY 1
                    """,
                    since.replace(tzinfo=None) if since.tzinfo else since,
                )
            for row in rows:
                out[row["d"].isoformat()] = int(row["cnt"] or 0)
        except Exception:
            logger.exception("entities_created_by_day failed table=%s", table)
        return out

    async def entities_created_since(self, table: str, since: datetime) -> int:
        if table not in ("agents", "skills"):
            return 0
        since = _as_utc(since) or _utc_now()
        try:
            async with await self.db_connection.acquire() as conn:
                val = await conn.fetchval(
                    f"""
                    SELECT COUNT(*) FROM {table}
                    WHERE created_at >= $1
                    """,
                    since.replace(tzinfo=None) if since.tzinfo else since,
                )
            return int(val or 0)
        except Exception:
            logger.exception("entities_created_since failed table=%s", table)
            return 0

    async def usage_summary_since(self, since: datetime) -> Dict[str, int]:
        since = _as_utc(since) or _utc_now()
        out = {t: 0 for t in ENTITY_TYPES}
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT entity_type, COUNT(*) AS cnt
                    FROM admin_usage_events
                    WHERE occurred_at >= $1
                    GROUP BY entity_type
                    """,
                    since,
                )
            for row in rows:
                et = str(row["entity_type"] or "")
                if et in out:
                    out[et] = int(row["cnt"] or 0)
        except Exception:
            logger.exception("usage_summary_since failed")
        return out

    async def usage_by_day(
        self, since: datetime, entity_type: Optional[str] = None
    ) -> Dict[str, int]:
        since = _as_utc(since) or _utc_now()
        et = (entity_type or "").strip().lower() or None
        if et and et not in ENTITY_TYPES:
            return {}
        out: Dict[str, int] = {}
        try:
            async with await self.db_connection.acquire() as conn:
                if et:
                    rows = await conn.fetch(
                        """
                        SELECT (occurred_at AT TIME ZONE 'UTC')::date AS d, COUNT(*) AS cnt
                        FROM admin_usage_events
                        WHERE occurred_at >= $1 AND entity_type = $2
                        GROUP BY 1 ORDER BY 1
                        """,
                        since,
                        et,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT (occurred_at AT TIME ZONE 'UTC')::date AS d, COUNT(*) AS cnt
                        FROM admin_usage_events
                        WHERE occurred_at >= $1
                        GROUP BY 1 ORDER BY 1
                        """,
                        since,
                    )
            for row in rows:
                out[row["d"].isoformat()] = int(row["cnt"] or 0)
        except Exception:
            logger.exception("usage_by_day failed")
        return out

    async def top_entities(
        self,
        since: datetime,
        entity_type: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        since = _as_utc(since) or _utc_now()
        et = (entity_type or "").strip().lower()
        if et not in ENTITY_TYPES:
            return []
        limit = max(1, min(int(limit or 20), 100))
        out: List[Dict[str, Any]] = []
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        entity_id,
                        COALESCE(MAX(entity_name), '') AS entity_name,
                        COUNT(*) AS uses,
                        COUNT(DISTINCT user_id) AS unique_users
                    FROM admin_usage_events
                    WHERE occurred_at >= $1 AND entity_type = $2
                    GROUP BY entity_id
                    ORDER BY uses DESC
                    LIMIT $3
                    """,
                    since,
                    et,
                    limit,
                )
            for row in rows:
                out.append(
                    {
                        "entity_type": et,
                        "entity_id": row["entity_id"],
                        "entity_name": row["entity_name"] or row["entity_id"],
                        "uses": int(row["uses"] or 0),
                        "unique_users": int(row["unique_users"] or 0),
                    }
                )
        except Exception:
            logger.exception("top_entities failed type=%s", et)
        return out

    async def usage_by_user_entity(
        self,
        since: datetime,
        entity_type: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Кто каким entity пользовался."""
        since = _as_utc(since) or _utc_now()
        et = (entity_type or "").strip().lower()
        if et not in ENTITY_TYPES:
            return []
        limit = max(1, min(int(limit or 100), 500))
        out: List[Dict[str, Any]] = []
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        user_id,
                        entity_id,
                        COALESCE(MAX(entity_name), '') AS entity_name,
                        COUNT(*) AS uses,
                        MAX(occurred_at) AS last_used_at
                    FROM admin_usage_events
                    WHERE occurred_at >= $1 AND entity_type = $2
                    GROUP BY user_id, entity_id
                    ORDER BY uses DESC, last_used_at DESC
                    LIMIT $3
                    """,
                    since,
                    et,
                    limit,
                )
            for row in rows:
                out.append(
                    {
                        "user_id": row["user_id"],
                        "entity_type": et,
                        "entity_id": row["entity_id"],
                        "entity_name": row["entity_name"] or row["entity_id"],
                        "uses": int(row["uses"] or 0),
                        "last_used_at": row["last_used_at"].isoformat()
                        if row["last_used_at"]
                        else None,
                    }
                )
        except Exception:
            logger.exception("usage_by_user_entity failed type=%s", et)
        return out

    async def list_auth_events(
        self, since: datetime, limit: int = 5000
    ) -> List[Dict[str, Any]]:
        since = _as_utc(since) or _utc_now()
        limit = max(1, min(int(limit or 5000), 20000))
        out: List[Dict[str, Any]] = []
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT user_id, event_type, logged_at
                    FROM admin_login_events
                    WHERE logged_at >= $1
                    ORDER BY logged_at DESC
                    LIMIT $2
                    """,
                    since,
                    limit,
                )
            for row in rows:
                out.append(
                    {
                        "user_id": row["user_id"],
                        "event_type": row["event_type"],
                        "logged_at": row["logged_at"].isoformat()
                        if row["logged_at"]
                        else None,
                    }
                )
        except Exception:
            logger.exception("list_auth_events failed")
        return out

    async def list_usage_events(
        self,
        since: datetime,
        entity_type: Optional[str] = None,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        since = _as_utc(since) or _utc_now()
        limit = max(1, min(int(limit or 5000), 20000))
        et = (entity_type or "").strip().lower() or None
        if et and et not in ENTITY_TYPES:
            return []
        out: List[Dict[str, Any]] = []
        try:
            async with await self.db_connection.acquire() as conn:
                if et:
                    rows = await conn.fetch(
                        """
                        SELECT entity_type, entity_id, entity_name, user_id,
                               action, occurred_at
                        FROM admin_usage_events
                        WHERE occurred_at >= $1 AND entity_type = $2
                        ORDER BY occurred_at DESC
                        LIMIT $3
                        """,
                        since,
                        et,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT entity_type, entity_id, entity_name, user_id,
                               action, occurred_at
                        FROM admin_usage_events
                        WHERE occurred_at >= $1
                        ORDER BY occurred_at DESC
                        LIMIT $2
                        """,
                        since,
                        limit,
                    )
            for row in rows:
                out.append(
                    {
                        "entity_type": row["entity_type"],
                        "entity_id": row["entity_id"],
                        "entity_name": row["entity_name"] or "",
                        "user_id": row["user_id"],
                        "action": row["action"],
                        "occurred_at": row["occurred_at"].isoformat()
                        if row["occurred_at"]
                        else None,
                    }
                )
        except Exception:
            logger.exception("list_usage_events failed")
        return out


def empty_day_series(days: int, end: Optional[datetime] = None) -> List[str]:
    end_dt = _as_utc(end) or _utc_now()
    end_day = end_dt.date()
    return [(end_day - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def merge_activity_series(
    days: int,
    *,
    active_users: Dict[str, int],
    logins: Dict[str, int],
    logouts: Optional[Dict[str, int]] = None,
    conversation_updates: Dict[str, int],
    agents_created: Dict[str, int],
    skills_created: Dict[str, int],
    usage_total: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    logouts = logouts or {}
    usage_total = usage_total or {}
    series: List[Dict[str, Any]] = []
    for day in empty_day_series(days):
        series.append(
            {
                "date": day,
                "active_users": int(active_users.get(day, 0)),
                "logins": int(logins.get(day, 0)),
                "logouts": int(logouts.get(day, 0)),
                "conversation_updates": int(conversation_updates.get(day, 0)),
                "agents_created": int(agents_created.get(day, 0)),
                "skills_created": int(skills_created.get(day, 0)),
                "usage_total": int(usage_total.get(day, 0)),
            }
        )
    return series
