"""
Репозиторий локальных пользователей (username + password_hash) в PostgreSQL.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend.database.postgresql.connection import PostgreSQLConnection
from backend.settings.logging import get_logger

logger = get_logger(__name__)


class LocalUserRepository:
    """CRUD для таблицы local_users."""

    def __init__(self, db_connection: PostgreSQLConnection):
        self.db_connection = db_connection

    async def create_tables(self) -> None:
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS local_users (
                        username VARCHAR(100) PRIMARY KEY,
                        password_hash TEXT NOT NULL,
                        is_admin BOOLEAN NOT NULL DEFAULT TRUE,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_local_users_created ON local_users(created_at DESC)"
                )
            logger.info("Таблица local_users готова")
        except Exception:
            logger.exception("Ошибка при создании таблицы local_users")
            raise

    async def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        uname = (username or "").strip()
        if not uname:
            return None
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT username, password_hash, is_admin, is_active, created_at, updated_at
                    FROM local_users
                    WHERE lower(username) = lower($1)
                    """,
                    uname,
                )
            if not row:
                return None
            return {
                "username": row["username"],
                "password_hash": row["password_hash"],
                "is_admin": bool(row["is_admin"]),
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        except Exception:
            logger.exception("local_users.get_by_username failed username=%s", uname)
            return None

    async def create_user(
        self,
        username: str,
        password_hash: str,
        *,
        is_admin: bool = True,
        is_active: bool = True,
    ) -> Optional[Dict[str, Any]]:
        uname = (username or "").strip()
        if not uname or not password_hash:
            return None
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO local_users (username, password_hash, is_admin, is_active)
                    VALUES ($1, $2, $3, $4)
                    RETURNING username, password_hash, is_admin, is_active, created_at, updated_at
                    """,
                    uname,
                    password_hash,
                    bool(is_admin),
                    bool(is_active),
                )
            if not row:
                return None
            return {
                "username": row["username"],
                "password_hash": row["password_hash"],
                "is_admin": bool(row["is_admin"]),
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        except Exception as e:
            # UniqueViolation — логин уже занят (asyncpg: sqlstate 23505)
            sqlstate = getattr(e, "sqlstate", None)
            if sqlstate == "23505" or "unique" in str(e).lower() or "Duplicate" in type(e).__name__:
                return None
            logger.exception("local_users.create_user failed username=%s", uname)
            raise
