"""
Репозиторий локальных пользователей (username + password_hash) в PostgreSQL.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
                        is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
                # Безопасный дефолт на существующих инсталляциях (колонка уже есть).
                await conn.execute(
                    """
                    ALTER TABLE local_users
                    ALTER COLUMN is_admin SET DEFAULT FALSE
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
            return self._row_to_dict(row)
        except Exception:
            logger.exception("local_users.get_by_username failed username=%s", uname)
            return None

    async def list_users(
        self,
        *,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        lim = max(1, min(int(limit or 100), 500))
        off = max(0, int(offset or 0))
        q = (search or "").strip()
        try:
            async with await self.db_connection.acquire() as conn:
                if q:
                    rows = await conn.fetch(
                        """
                        SELECT username, password_hash, is_admin, is_active, created_at, updated_at
                        FROM local_users
                        WHERE username ILIKE $1
                        ORDER BY created_at DESC NULLS LAST, username ASC
                        LIMIT $2 OFFSET $3
                        """,
                        f"%{q}%",
                        lim,
                        off,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT username, password_hash, is_admin, is_active, created_at, updated_at
                        FROM local_users
                        ORDER BY created_at DESC NULLS LAST, username ASC
                        LIMIT $1 OFFSET $2
                        """,
                        lim,
                        off,
                    )
            return [self._row_to_dict(r) for r in rows]
        except Exception:
            logger.exception("local_users.list_users failed")
            return []

    async def count_users(self, *, search: Optional[str] = None) -> int:
        q = (search or "").strip()
        try:
            async with await self.db_connection.acquire() as conn:
                if q:
                    val = await conn.fetchval(
                        "SELECT COUNT(*) FROM local_users WHERE username ILIKE $1",
                        f"%{q}%",
                    )
                else:
                    val = await conn.fetchval("SELECT COUNT(*) FROM local_users")
            return int(val or 0)
        except Exception:
            logger.exception("local_users.count_users failed")
            return 0

    async def count_admins(self) -> int:
        try:
            async with await self.db_connection.acquire() as conn:
                val = await conn.fetchval(
                    "SELECT COUNT(*) FROM local_users WHERE is_admin = TRUE AND is_active = TRUE"
                )
            return int(val or 0)
        except Exception:
            logger.exception("local_users.count_admins failed")
            return 0

    async def create_user(
        self,
        username: str,
        password_hash: str,
        *,
        is_admin: bool = False,
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
            return self._row_to_dict(row)
        except Exception as e:
            sqlstate = getattr(e, "sqlstate", None)
            if sqlstate == "23505" or "unique" in str(e).lower() or "Duplicate" in type(e).__name__:
                return None
            logger.exception("local_users.create_user failed username=%s", uname)
            raise

    async def update_user(
        self,
        username: str,
        *,
        is_admin: Optional[bool] = None,
        is_active: Optional[bool] = None,
        password_hash: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        uname = (username or "").strip()
        if not uname:
            return None
        sets: List[str] = ["updated_at = NOW()"]
        args: List[Any] = []
        idx = 1
        if is_admin is not None:
            sets.append(f"is_admin = ${idx}")
            args.append(bool(is_admin))
            idx += 1
        if is_active is not None:
            sets.append(f"is_active = ${idx}")
            args.append(bool(is_active))
            idx += 1
        if password_hash is not None:
            sets.append(f"password_hash = ${idx}")
            args.append(password_hash)
            idx += 1
        if len(args) == 0:
            return await self.get_by_username(uname)
        args.append(uname)
        sql = f"""
            UPDATE local_users
            SET {', '.join(sets)}
            WHERE lower(username) = lower(${idx})
            RETURNING username, password_hash, is_admin, is_active, created_at, updated_at
        """
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(sql, *args)
            if not row:
                return None
            return self._row_to_dict(row)
        except Exception:
            logger.exception("local_users.update_user failed username=%s", uname)
            raise

    async def delete_user(self, username: str) -> bool:
        uname = (username or "").strip()
        if not uname:
            return False
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM local_users WHERE lower(username) = lower($1)",
                    uname,
                )
            # asyncpg: "DELETE N"
            return str(result).endswith("1") or str(result).endswith(" 1")
        except Exception:
            logger.exception("local_users.delete_user failed username=%s", uname)
            raise

    @staticmethod
    def _row_to_dict(row: Any) -> Dict[str, Any]:
        return {
            "username": row["username"],
            "password_hash": row["password_hash"],
            "is_admin": bool(row["is_admin"]),
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
