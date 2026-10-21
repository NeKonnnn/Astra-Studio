"""Репозиторий Баз знаний (PostgreSQL / asyncpg)."""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from backend.database.postgresql.connection import PostgreSQLConnection
from backend.database.postgresql.knowledge_base_models import (
    KB_PERMISSION_EDITOR,
    KB_PERMISSION_OWNER,
    KB_PERMISSION_VIEWER,
    KB_SHARE_PERMISSIONS,
    KnowledgeBaseCreate,
    KnowledgeBaseFilters,
    KnowledgeBaseListItem,
    KnowledgeBaseOut,
    KnowledgeBaseShare,
    KnowledgeBaseUpdate,
    normalize_kb_permission,
)
from backend.settings.logging import get_logger

logger = get_logger(__name__)


def _parse_meta(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) or {}
        except Exception:
            return {}
    return {}


def _row_common(row) -> Dict[str, Any]:
    keys = set(row.keys())

    def _get(key, default=None):
        return row[key] if key in keys else default

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "meta": _parse_meta(row["meta"]),
        "is_public": bool(row["is_public"]),
        "document_count": int(_get("document_count") or 0),
        "views_count": int(_get("views_count") or 0),
        "usage_count": int(_get("usage_count") or 0),
        "author_id": row["author_id"],
        "author_name": row["author_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class KnowledgeBaseRepository:
    """CRUD + shares для knowledge_bases."""

    def __init__(self, db_connection: PostgreSQLConnection):
        self.db_connection = db_connection

    async def create_tables(self) -> None:
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS knowledge_bases (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        description TEXT,
                        meta JSONB DEFAULT '{}'::jsonb,
                        is_public BOOLEAN DEFAULT false,
                        document_count INTEGER DEFAULT 0,
                        views_count INTEGER DEFAULT 0,
                        usage_count INTEGER DEFAULT 0,
                        author_id VARCHAR(100) NOT NULL,
                        author_name VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                    """)
                for stmt in (
                    "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS document_count INTEGER DEFAULT 0",
                    "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS meta JSONB DEFAULT '{}'::jsonb",
                    "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT false",
                    "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS views_count INTEGER DEFAULT 0",
                    "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS usage_count INTEGER DEFAULT 0",
                ):
                    await conn.execute(stmt)

                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS knowledge_base_shares (
                        id SERIAL PRIMARY KEY,
                        knowledge_base_id INTEGER REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                        owner_id VARCHAR(100) NOT NULL,
                        shared_with_user_id VARCHAR(100) NOT NULL,
                        permission VARCHAR(20) DEFAULT 'viewer',
                        created_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(knowledge_base_id, shared_with_user_id)
                    )
                    """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS knowledge_base_ratings (
                        id SERIAL PRIMARY KEY,
                        knowledge_base_id INTEGER REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                        user_id VARCHAR(100) NOT NULL,
                        rating INTEGER CHECK (rating >= 1 AND rating <= 5),
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(knowledge_base_id, user_id)
                    )
                    """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS knowledge_base_bookmarks (
                        id SERIAL PRIMARY KEY,
                        knowledge_base_id INTEGER REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                        user_id VARCHAR(100) NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(knowledge_base_id, user_id)
                    )
                    """)
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kbases_author ON knowledge_bases(author_id)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kbases_updated ON knowledge_bases(updated_at DESC)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kbases_public ON knowledge_bases(is_public)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kbase_shares_recipient "
                    "ON knowledge_base_shares(shared_with_user_id)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kbase_shares_kb "
                    "ON knowledge_base_shares(knowledge_base_id)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kbase_ratings_kb "
                    "ON knowledge_base_ratings(knowledge_base_id)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kbase_bookmarks_user "
                    "ON knowledge_base_bookmarks(user_id)"
                )
                logger.info("Таблицы knowledge_bases созданы")
        except Exception:
            logger.exception("Ошибка при создании таблиц knowledge_bases")
            raise

    def _row_to_out(
        self,
        row,
        *,
        is_shared_with_me: bool = False,
        my_permission: Optional[str] = None,
        write_access: bool = False,
        average_rating: float = 0.0,
        total_votes: int = 0,
        user_rating: Optional[int] = None,
        is_bookmarked: bool = False,
    ) -> KnowledgeBaseOut:
        return KnowledgeBaseOut(
            **_row_common(row),
            is_shared_with_me=is_shared_with_me,
            my_permission=my_permission,
            write_access=write_access,
            average_rating=average_rating,
            total_votes=total_votes,
            user_rating=user_rating,
            is_bookmarked=is_bookmarked,
        )

    def _row_to_list_item(
        self,
        row,
        *,
        is_shared_with_me: bool = False,
        my_permission: Optional[str] = None,
        write_access: bool = False,
        average_rating: float = 0.0,
        total_votes: int = 0,
        user_rating: Optional[int] = None,
        is_bookmarked: bool = False,
    ) -> KnowledgeBaseListItem:
        return KnowledgeBaseListItem(
            **_row_common(row),
            is_shared_with_me=is_shared_with_me,
            my_permission=my_permission,
            write_access=write_access,
            average_rating=average_rating,
            total_votes=total_votes,
            user_rating=user_rating,
            is_bookmarked=is_bookmarked,
        )

    async def _enrich(
        self, row, *, user_id: Optional[str], is_admin: bool, as_list: bool = False
    ) -> Optional[Union[KnowledgeBaseOut, KnowledgeBaseListItem]]:
        uid = (user_id or "").strip().lower() if user_id else None
        author = (row["author_id"] or "").strip().lower()
        is_shared = False
        my_permission = None
        write_access = False

        if is_admin:
            my_permission = KB_PERMISSION_OWNER
            write_access = True
        elif uid and author == uid:
            my_permission = KB_PERMISSION_OWNER
            write_access = True
        elif uid:
            async with await self.db_connection.acquire() as conn:
                share = await conn.fetchrow(
                    """
                    SELECT permission FROM knowledge_base_shares
                    WHERE knowledge_base_id = $1
                      AND LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($2))
                    """,
                    row["id"],
                    uid,
                )
            if share:
                is_shared = True
                my_permission = normalize_kb_permission(share["permission"])
                write_access = my_permission == KB_PERMISSION_EDITOR
            elif row["is_public"]:
                my_permission = KB_PERMISSION_VIEWER
            else:
                return None
        elif row["is_public"]:
            my_permission = KB_PERMISSION_VIEWER
        else:
            return None

        average_rating = 0.0
        total_votes = 0
        user_rating = None
        is_bookmarked = False
        try:
            async with await self.db_connection.acquire() as conn:
                stats = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(AVG(rating), 0) AS average_rating,
                        COUNT(id) AS total_votes
                    FROM knowledge_base_ratings
                    WHERE knowledge_base_id = $1
                    """,
                    row["id"],
                )
                if stats:
                    average_rating = float(stats["average_rating"] or 0)
                    total_votes = int(stats["total_votes"] or 0)
                if uid:
                    rating_row = await conn.fetchrow(
                        """
                        SELECT rating FROM knowledge_base_ratings
                        WHERE knowledge_base_id = $1
                          AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                        """,
                        row["id"],
                        uid,
                    )
                    if rating_row:
                        user_rating = rating_row["rating"]
                    bookmark_row = await conn.fetchrow(
                        """
                        SELECT id FROM knowledge_base_bookmarks
                        WHERE knowledge_base_id = $1
                          AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                        """,
                        row["id"],
                        uid,
                    )
                    is_bookmarked = bookmark_row is not None
        except Exception:
            logger.exception("Ошибка обогащения knowledge_base рейтингом/закладками")

        kwargs = dict(
            is_shared_with_me=is_shared,
            my_permission=my_permission,
            write_access=write_access,
            average_rating=average_rating,
            total_votes=total_votes,
            user_rating=user_rating,
            is_bookmarked=is_bookmarked,
        )
        if as_list:
            return self._row_to_list_item(row, **kwargs)
        return self._row_to_out(row, **kwargs)

    async def create(
        self, data: KnowledgeBaseCreate, author_id: str, author_name: str
    ) -> Optional[KnowledgeBaseOut]:
        try:
            author_id = (author_id or "").strip().lower()
            name = (data.name or "").strip()
            if not name:
                return None
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO knowledge_bases (
                        name, description, meta, is_public, author_id, author_name
                    )
                    VALUES ($1, $2, $3::jsonb, $4, $5, $6)
                    RETURNING *
                    """,
                    name,
                    data.description,
                    json.dumps(data.meta or {}),
                    bool(data.is_public),
                    author_id,
                    author_name or "Anonymous",
                )
                return self._row_to_out(
                    row,
                    my_permission=KB_PERMISSION_OWNER,
                    write_access=True,
                )
        except Exception:
            logger.exception("Ошибка создания knowledge_base")
            return None

    async def get_by_id(
        self, kb_id: int, user_id: Optional[str] = None, is_admin: bool = False
    ) -> Optional[KnowledgeBaseOut]:
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM knowledge_bases WHERE id = $1", kb_id
                )
                if not row:
                    return None
            return await self._enrich(row, user_id=user_id, is_admin=is_admin)  # type: ignore[return-value]
        except Exception:
            logger.exception("Ошибка get knowledge_base by id")
            return None

    async def list_items(
        self, user_id: str, filters: KnowledgeBaseFilters, is_admin: bool = False
    ) -> Tuple[List[KnowledgeBaseListItem], int]:
        uid = (user_id or "").strip().lower()
        limit = filters.limit
        offset = filters.offset
        view = (filters.view_option or "").strip().lower()
        query = (filters.search_query or "").strip()

        try:
            async with await self.db_connection.acquire() as conn:
                where: List[str] = []
                params: List[Any] = []
                idx = 1

                if is_admin and not view:
                    pass
                elif view == "created":
                    where.append(f"LOWER(TRIM(k.author_id)) = LOWER(TRIM(${idx}))")
                    params.append(uid)
                    idx += 1
                elif view == "shared":
                    where.append(f"""EXISTS (
                            SELECT 1 FROM knowledge_base_shares ks
                            WHERE ks.knowledge_base_id = k.id
                              AND LOWER(TRIM(ks.shared_with_user_id)) = LOWER(TRIM(${idx}))
                        )""")
                    params.append(uid)
                    idx += 1
                elif view == "public":
                    where.append("k.is_public = true")
                else:
                    where.append(f"""(
                            LOWER(TRIM(k.author_id)) = LOWER(TRIM(${idx}))
                            OR k.is_public = true
                            OR EXISTS (
                                SELECT 1 FROM knowledge_base_shares ks
                                WHERE ks.knowledge_base_id = k.id
                                  AND LOWER(TRIM(ks.shared_with_user_id)) = LOWER(TRIM(${idx}))
                            )
                        )""")
                    params.append(uid)
                    idx += 1

                if query:
                    where.append(
                        f"(k.name ILIKE ${idx} OR COALESCE(k.description, '') ILIKE ${idx})"
                    )
                    params.append(f"%{query}%")
                    idx += 1

                where_sql = ("WHERE " + " AND ".join(where)) if where else ""
                total = await conn.fetchval(
                    f"SELECT COUNT(*) FROM knowledge_bases k {where_sql}",  # noqa: S608
                    *params,
                )
                rows = await conn.fetch(
                    f"""
                    SELECT k.* FROM knowledge_bases k
                    {where_sql}
                    ORDER BY k.updated_at DESC
                    LIMIT ${idx} OFFSET ${idx + 1}
                    """,  # noqa: S608
                    *params,
                    limit,
                    offset,
                )

            items: List[KnowledgeBaseListItem] = []
            for row in rows:
                item = await self._enrich(
                    row, user_id=uid, is_admin=is_admin, as_list=True
                )
                if item:
                    items.append(item)  # type: ignore[arg-type]
            return items, int(total or 0)
        except Exception:
            logger.exception("Ошибка list knowledge_bases")
            return [], 0

    async def get_user_permission(
        self, kb_id: int, user_id: str, *, is_admin: bool = False
    ) -> Optional[str]:
        if is_admin:
            return KB_PERMISSION_OWNER
        uid = (user_id or "").strip().lower()
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT author_id, is_public FROM knowledge_bases WHERE id = $1",
                    kb_id,
                )
                if not row:
                    return None
                if (row["author_id"] or "").strip().lower() == uid:
                    return KB_PERMISSION_OWNER
                share = await conn.fetchrow(
                    """
                    SELECT permission FROM knowledge_base_shares
                    WHERE knowledge_base_id = $1
                      AND LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($2))
                    """,
                    kb_id,
                    uid,
                )
                if share:
                    return normalize_kb_permission(share["permission"])
                if row["is_public"]:
                    return KB_PERMISSION_VIEWER
            return None
        except Exception:
            logger.exception("Ошибка get_user_permission knowledge_base")
            return None

    async def can_write(
        self, kb_id: int, user_id: str, is_admin: bool = False
    ) -> bool:
        perm = await self.get_user_permission(kb_id, user_id, is_admin=is_admin)
        return perm in (KB_PERMISSION_OWNER, KB_PERMISSION_EDITOR)

    async def can_read(
        self, kb_id: int, user_id: str, is_admin: bool = False
    ) -> bool:
        return (
            await self.get_user_permission(kb_id, user_id, is_admin=is_admin)
        ) is not None

    async def update(
        self,
        kb_id: int,
        data: KnowledgeBaseUpdate,
        user_id: str,
        is_admin: bool = False,
    ) -> Optional[KnowledgeBaseOut]:
        if not await self.can_write(kb_id, user_id, is_admin=is_admin):
            return None
        try:
            async with await self.db_connection.acquire() as conn:
                fields: List[str] = []
                params: List[Any] = []
                n = 1
                if data.name is not None:
                    fields.append(f"name = ${n}")
                    params.append(data.name.strip())
                    n += 1
                if data.description is not None:
                    fields.append(f"description = ${n}")
                    params.append(data.description)
                    n += 1
                if data.meta is not None:
                    fields.append(f"meta = ${n}::jsonb")
                    params.append(json.dumps(data.meta))
                    n += 1
                if data.is_public is not None:
                    perm = await self.get_user_permission(kb_id, user_id)
                    if is_admin or perm == KB_PERMISSION_OWNER:
                        fields.append(f"is_public = ${n}")
                        params.append(bool(data.is_public))
                        n += 1
                    else:
                        logger.info(
                            "Игнорируем смену is_public от редактора: "
                            f"user={user_id}, kb={kb_id}"
                        )
                if not fields:
                    return await self.get_by_id(
                        kb_id, user_id=user_id, is_admin=is_admin
                    )
                fields.append(f"updated_at = ${n}")
                params.append(datetime.utcnow())
                n += 1
                params.append(kb_id)
                await conn.execute(
                    f"UPDATE knowledge_bases SET {', '.join(fields)} WHERE id = ${n}",  # noqa: S608
                    *params,
                )
            return await self.get_by_id(kb_id, user_id=user_id, is_admin=is_admin)
        except Exception:
            logger.exception("Ошибка update knowledge_base")
            return None

    async def delete(
        self, kb_id: int, user_id: str, is_admin: bool = False
    ) -> bool:
        uid = (user_id or "").strip().lower()
        try:
            async with await self.db_connection.acquire() as conn:
                author = await conn.fetchval(
                    "SELECT LOWER(TRIM(author_id)) FROM knowledge_bases WHERE id = $1",
                    kb_id,
                )
                if not author:
                    return False
                if not is_admin and author != uid:
                    return False
                await conn.execute("DELETE FROM knowledge_bases WHERE id = $1", kb_id)
                return True
        except Exception:
            logger.exception("Ошибка delete knowledge_base")
            return False

    async def set_document_count(self, kb_id: int, count: int) -> None:
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE knowledge_bases
                    SET document_count = $2, updated_at = $3
                    WHERE id = $1
                    """,
                    kb_id,
                    max(0, int(count)),
                    datetime.utcnow(),
                )
        except Exception:
            logger.exception("Ошибка set_document_count knowledge_base")

    async def bump_document_count(self, kb_id: int, delta: int = 1) -> None:
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE knowledge_bases
                    SET document_count = GREATEST(0, COALESCE(document_count, 0) + $2),
                        updated_at = $3
                    WHERE id = $1
                    """,
                    kb_id,
                    int(delta),
                    datetime.utcnow(),
                )
        except Exception:
            logger.exception("Ошибка bump_document_count knowledge_base")

    async def share(
        self,
        kb_id: int,
        owner_id: str,
        usernames: List[str],
        permission: str = "viewer",
        recipient_permissions: Optional[Dict[str, str]] = None,
    ) -> Tuple[List[str], List[str]]:
        perm_default = normalize_kb_permission(permission)
        if perm_default not in KB_SHARE_PERMISSIONS:
            perm_default = KB_PERMISSION_VIEWER
        added: List[str] = []
        skipped: List[str] = []
        try:
            async with await self.db_connection.acquire() as conn:
                for raw in usernames:
                    uid = (raw or "").strip().lower()
                    if not uid:
                        continue
                    if uid == (owner_id or "").strip().lower():
                        skipped.append(uid)
                        continue
                    perm = normalize_kb_permission(
                        (recipient_permissions or {}).get(uid, perm_default)
                    )
                    if perm not in KB_SHARE_PERMISSIONS:
                        perm = KB_PERMISSION_VIEWER
                    await conn.execute(
                        """
                        INSERT INTO knowledge_base_shares (
                            knowledge_base_id, owner_id, shared_with_user_id, permission
                        )
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (knowledge_base_id, shared_with_user_id)
                        DO UPDATE SET permission = EXCLUDED.permission
                        """,
                        kb_id,
                        (owner_id or "").strip().lower(),
                        uid,
                        perm,
                    )
                    added.append(uid)
            return added, skipped
        except Exception:
            logger.exception("Ошибка share knowledge_base")
            return [], usernames

    async def list_shares(self, kb_id: int) -> List[KnowledgeBaseShare]:
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM knowledge_base_shares
                    WHERE knowledge_base_id = $1
                    ORDER BY created_at ASC
                    """,
                    kb_id,
                )
            return [
                KnowledgeBaseShare(
                    id=r["id"],
                    knowledge_base_id=r["knowledge_base_id"],
                    owner_id=r["owner_id"],
                    shared_with_user_id=r["shared_with_user_id"],
                    permission=normalize_kb_permission(r["permission"]),
                    created_at=r["created_at"],
                )
                for r in rows
            ]
        except Exception:
            logger.exception("Ошибка list_shares knowledge_base")
            return []

    async def remove_share(self, kb_id: int, shared_with_user_id: str) -> bool:
        uid = (shared_with_user_id or "").strip().lower()
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute(
                    """
                    DELETE FROM knowledge_base_shares
                    WHERE knowledge_base_id = $1
                      AND LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($2))
                    """,
                    kb_id,
                    uid,
                )
            return result.endswith("1") if isinstance(result, str) else True
        except Exception:
            logger.exception("Ошибка remove_share knowledge_base")
            return False

    async def rate_knowledge_base(
        self, kb_id: int, user_id: str, rating: int
    ) -> bool:
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            if rating < 1 or rating > 5:
                return False
            async with await self.db_connection.acquire() as conn:
                exists = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM knowledge_bases WHERE id = $1)", kb_id
                )
                if not exists:
                    return False
                existing = await conn.fetchrow(
                    """
                    SELECT id FROM knowledge_base_ratings
                    WHERE knowledge_base_id = $1
                      AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                    """,
                    kb_id,
                    user_id,
                )
                if existing:
                    await conn.execute(
                        """
                        UPDATE knowledge_base_ratings
                        SET rating = $1, updated_at = NOW()
                        WHERE knowledge_base_id = $2
                          AND LOWER(TRIM(user_id)) = LOWER(TRIM($3))
                        """,
                        rating,
                        kb_id,
                        user_id,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO knowledge_base_ratings (
                            knowledge_base_id, user_id, rating
                        )
                        VALUES ($1, $2, $3)
                        """,
                        kb_id,
                        user_id,
                        rating,
                    )
                return True
        except Exception:
            logger.exception("Ошибка rate_knowledge_base")
            return False

    async def increment_views(self, kb_id: int) -> bool:
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE knowledge_bases
                    SET views_count = COALESCE(views_count, 0) + 1
                    WHERE id = $1
                    """,
                    kb_id,
                )
                return result != "UPDATE 0"
        except Exception:
            logger.exception("Ошибка increment_views knowledge_base")
            return False

    async def increment_usage(self, kb_id: int) -> bool:
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE knowledge_bases
                    SET usage_count = COALESCE(usage_count, 0) + 1
                    WHERE id = $1
                    """,
                    kb_id,
                )
                return result != "UPDATE 0"
        except Exception:
            logger.exception("Ошибка increment_usage knowledge_base")
            return False

    async def add_bookmark(self, kb_id: int, user_id: str) -> bool:
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            async with await self.db_connection.acquire() as conn:
                exists = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM knowledge_bases WHERE id = $1)", kb_id
                )
                if not exists:
                    return False
                await conn.execute(
                    """
                    INSERT INTO knowledge_base_bookmarks (knowledge_base_id, user_id)
                    VALUES ($1, $2)
                    ON CONFLICT (knowledge_base_id, user_id) DO NOTHING
                    """,
                    kb_id,
                    user_id,
                )
                return True
        except Exception:
            logger.exception("Ошибка add_bookmark knowledge_base")
            return False

    async def remove_bookmark(self, kb_id: int, user_id: str) -> bool:
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    DELETE FROM knowledge_base_bookmarks
                    WHERE knowledge_base_id = $1
                      AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                    """,
                    kb_id,
                    user_id,
                )
                return True
        except Exception:
            logger.exception("Ошибка remove_bookmark knowledge_base")
            return False

    async def get_user_bookmarks(
        self, user_id: str, limit: int = 100, offset: int = 0
    ) -> Tuple[List[int], int]:
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            async with await self.db_connection.acquire() as conn:
                total = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM knowledge_base_bookmarks
                    WHERE LOWER(TRIM(user_id)) = LOWER(TRIM($1))
                    """,
                    user_id,
                )
                rows = await conn.fetch(
                    """
                    SELECT knowledge_base_id
                    FROM knowledge_base_bookmarks
                    WHERE LOWER(TRIM(user_id)) = LOWER(TRIM($1))
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    user_id,
                    limit,
                    offset,
                )
            return [int(r["knowledge_base_id"]) for r in rows], int(total or 0)
        except Exception:
            logger.exception("Ошибка get_user_bookmarks knowledge_base")
            return [], 0
