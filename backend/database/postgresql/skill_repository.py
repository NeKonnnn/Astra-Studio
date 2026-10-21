"""Репозиторий Skills (PostgreSQL / asyncpg)."""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from backend.database.postgresql.connection import PostgreSQLConnection
from backend.database.postgresql.skill_models import (
    SKILL_PERMISSION_EDITOR,
    SKILL_PERMISSION_OWNER,
    SKILL_PERMISSION_VIEWER,
    SKILL_SHARE_PERMISSIONS,
    SkillCreate,
    SkillFileOut,
    SkillFilters,
    SkillListItem,
    SkillOut,
    SkillShare,
    SkillUpdate,
    infer_skill_file_category,
    normalize_skill_permission,
    slugify_skill_id,
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


def _parse_str_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            return []
    return []


def _row_common(row) -> Dict[str, Any]:
    keys = set(row.keys())

    def _get(key, default=None):
        return row[key] if key in keys else default

    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "display_title": _get("display_title"),
        "description": row["description"],
        "meta": _parse_meta(row["meta"]),
        "is_active": bool(row["is_active"]),
        "is_public": bool(row["is_public"]),
        "user_invocable": bool(_get("user_invocable", True)) if _get("user_invocable", True) is not None else True,
        "disable_model_invocation": bool(_get("disable_model_invocation", False) or False),
        "always_apply": bool(_get("always_apply", False) or False),
        "allowed_tools": _parse_str_list(_get("allowed_tools")),
        "category": _get("category"),
        "version": int(_get("version") or 1),
        "file_count": int(_get("file_count") or 0),
        "author_id": row["author_id"],
        "author_name": row["author_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "views_count": int(_get("views_count") or 0),
        "usage_count": int(_get("usage_count") or 0),
    }


def escape_like(value: str) -> str:
    """Экранирует служебные символы LIKE/ILIKE (``\\``, ``%``, ``_``) для поиска подстроки."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

class SkillRepository:
    """CRUD + shares для skills."""

    def __init__(self, db_connection: PostgreSQLConnection):
        self.db_connection = db_connection

    async def create_tables(self) -> None:
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS skills (
                        id SERIAL PRIMARY KEY,
                        slug VARCHAR(100) NOT NULL UNIQUE,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        display_title VARCHAR(128),
                        description TEXT,
                        content TEXT NOT NULL,
                        meta JSONB DEFAULT '{}'::jsonb,
                        is_active BOOLEAN DEFAULT true,
                        is_public BOOLEAN DEFAULT false,
                        user_invocable BOOLEAN DEFAULT true,
                        disable_model_invocation BOOLEAN DEFAULT false,
                        always_apply BOOLEAN DEFAULT false,
                        allowed_tools JSONB DEFAULT '[]'::jsonb,
                        category VARCHAR(100),
                        version INTEGER DEFAULT 1,
                        file_count INTEGER DEFAULT 0,
                        views_count INTEGER DEFAULT 0,
                        usage_count INTEGER DEFAULT 0,
                        author_id VARCHAR(100) NOT NULL,
                        author_name VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
                # Soft-migrate existing OWUI-like tables
                for stmt in (
                    "ALTER TABLE skills ADD COLUMN IF NOT EXISTS display_title VARCHAR(128)",
                    "ALTER TABLE skills ADD COLUMN IF NOT EXISTS user_invocable BOOLEAN DEFAULT true",
                    "ALTER TABLE skills ADD COLUMN IF NOT EXISTS disable_model_invocation BOOLEAN DEFAULT false",
                    "ALTER TABLE skills ADD COLUMN IF NOT EXISTS always_apply BOOLEAN DEFAULT false",
                    "ALTER TABLE skills ADD COLUMN IF NOT EXISTS allowed_tools JSONB DEFAULT '[]'::jsonb",
                    "ALTER TABLE skills ADD COLUMN IF NOT EXISTS category VARCHAR(100)",
                    "ALTER TABLE skills ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1",
                    "ALTER TABLE skills ADD COLUMN IF NOT EXISTS file_count INTEGER DEFAULT 0",
                    "ALTER TABLE skills ADD COLUMN IF NOT EXISTS views_count INTEGER DEFAULT 0",
                    "ALTER TABLE skills ADD COLUMN IF NOT EXISTS usage_count INTEGER DEFAULT 0",
                ):
                    await conn.execute(stmt)

                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS skill_shares (
                        id SERIAL PRIMARY KEY,
                        skill_id INTEGER REFERENCES skills(id) ON DELETE CASCADE,
                        owner_id VARCHAR(100) NOT NULL,
                        shared_with_user_id VARCHAR(100) NOT NULL,
                        permission VARCHAR(20) DEFAULT 'viewer',
                        created_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(skill_id, shared_with_user_id)
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS skill_files (
                        id SERIAL PRIMARY KEY,
                        skill_id INTEGER REFERENCES skills(id) ON DELETE CASCADE,
                        relative_path VARCHAR(512) NOT NULL,
                        category VARCHAR(32) DEFAULT 'other',
                        mime_type VARCHAR(128),
                        bytes INTEGER DEFAULT 0,
                        minio_bucket VARCHAR(255),
                        minio_object VARCHAR(512),
                        content_text TEXT,
                        is_executable BOOLEAN DEFAULT false,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(skill_id, relative_path)
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS skill_ratings (
                        id SERIAL PRIMARY KEY,
                        skill_id INTEGER REFERENCES skills(id) ON DELETE CASCADE,
                        user_id VARCHAR(100) NOT NULL,
                        rating INTEGER CHECK (rating >= 1 AND rating <= 5),
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(skill_id, user_id)
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS skill_bookmarks (
                        id SERIAL PRIMARY KEY,
                        skill_id INTEGER REFERENCES skills(id) ON DELETE CASCADE,
                        user_id VARCHAR(100) NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(skill_id, user_id)
                    )
                    """
                )
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_author ON skills(author_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_slug ON skills(slug)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_updated ON skills(updated_at DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_public ON skills(is_public)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_always_apply ON skills(always_apply)")
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_skill_shares_recipient ON skill_shares(shared_with_user_id)"
                )
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_shares_skill ON skill_shares(skill_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_files_skill ON skill_files(skill_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_ratings_skill ON skill_ratings(skill_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_bookmarks_user ON skill_bookmarks(user_id)")
                logger.info("Таблицы skills / skill_files созданы")
        except Exception:
            logger.exception("Ошибка при создании таблиц skills")
            raise

    def _row_to_out(
        self,
        row,
        *,
        include_content: bool = True,
        is_shared_with_me: bool = False,
        my_permission: Optional[str] = None,
        write_access: bool = False,
        average_rating: float = 0.0,
        total_votes: int = 0,
        user_rating: Optional[int] = None,
        is_bookmarked: bool = False,
    ) -> Union[SkillOut, SkillListItem]:
        base = {
            **_row_common(row),
            "average_rating": float(average_rating or 0.0),
            "total_votes": int(total_votes or 0),
            "user_rating": user_rating,
            "is_bookmarked": bool(is_bookmarked),
            "is_shared_with_me": is_shared_with_me,
            "my_permission": my_permission,
            "write_access": write_access,
        }
        if include_content:
            return SkillOut(**base, content=row["content"] or "")
        return SkillListItem(**base)

    async def get_user_permission(self, skill_id: int, user_id: Optional[str]) -> Optional[str]:
        if not user_id:
            return None
        uid = user_id.strip().lower()
        try:
            async with await self.db_connection.acquire() as conn:
                author = await conn.fetchval(
                    "SELECT LOWER(TRIM(author_id)) FROM skills WHERE id = $1",
                    skill_id,
                )
                if not author:
                    return None
                if author == uid:
                    return SKILL_PERMISSION_OWNER
                share = await conn.fetchrow(
                    """
                    SELECT permission FROM skill_shares
                    WHERE skill_id = $1 AND LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($2))
                    """,
                    skill_id,
                    uid,
                )
                if share:
                    return normalize_skill_permission(share["permission"])
                is_public = await conn.fetchval("SELECT is_public FROM skills WHERE id = $1", skill_id)
                if is_public:
                    return SKILL_PERMISSION_VIEWER
                return None
        except Exception:
            logger.exception("Ошибка get_user_permission skill")
            return None

    async def can_read(self, skill_id: int, user_id: Optional[str], is_admin: bool = False) -> bool:
        if is_admin:
            return True
        return await self.get_user_permission(skill_id, user_id) is not None

    async def can_write(self, skill_id: int, user_id: Optional[str], is_admin: bool = False) -> bool:
        if is_admin:
            return True
        perm = await self.get_user_permission(skill_id, user_id)
        return perm in (SKILL_PERMISSION_OWNER, SKILL_PERMISSION_EDITOR)

    async def create_skill(self, data: SkillCreate, author_id: str, author_name: str) -> Optional[SkillOut]:
        try:
            author_id = (author_id or "").strip().lower()
            slug = slugify_skill_id(data.slug or data.name)
            if not slug:
                return None
            if not re.match(r"^[a-z0-9][a-z0-9._-]*$", slug):
                slug = re.sub(r"[^a-z0-9._-]+", "-", slug).strip("-")
            async with await self.db_connection.acquire() as conn:
                existing = await conn.fetchval("SELECT id FROM skills WHERE slug = $1 OR name = $2", slug, data.name)
                if existing:
                    logger.warning(f"Skill slug/name уже занят: {slug}/{data.name}")
                    return None
                row = await conn.fetchrow(
                    """
                    INSERT INTO skills (
                        slug, name, display_title, description, content, meta,
                        is_active, is_public, user_invocable, disable_model_invocation,
                        always_apply, allowed_tools, category, author_id, author_name
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6::jsonb,
                        $7, $8, $9, $10,
                        $11, $12::jsonb, $13, $14, $15
                    )
                    RETURNING *
                    """,
                    slug,
                    data.name,
                    data.display_title or data.name,
                    data.description,
                    data.content,
                    json.dumps(data.meta or {}),
                    data.is_active,
                    data.is_public,
                    data.user_invocable,
                    data.disable_model_invocation,
                    data.always_apply,
                    json.dumps(data.allowed_tools or []),
                    data.category,
                    author_id,
                    author_name or "Anonymous",
                )
                return self._row_to_out(
                    row,
                    include_content=True,
                    my_permission=SKILL_PERMISSION_OWNER,
                    write_access=True,
                )
        except Exception:
            logger.exception("Ошибка создания skill")
            return None

    async def get_skill_by_id(
        self, skill_id: int, user_id: Optional[str] = None, is_admin: bool = False
    ) -> Optional[SkillOut]:
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM skills WHERE id = $1", skill_id)
                if not row:
                    return None
            return await self._enrich_skill(row, user_id=user_id, is_admin=is_admin, include_content=True)
        except Exception:
            logger.exception("Ошибка get_skill_by_id")
            return None

    async def get_skill_by_slug(
        self, slug: str, user_id: Optional[str] = None, is_admin: bool = False
    ) -> Optional[SkillOut]:
        slug = slugify_skill_id(slug)
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM skills WHERE LOWER(slug) = LOWER($1)", slug)
                if not row:
                    return None
            return await self._enrich_skill(row, user_id=user_id, is_admin=is_admin, include_content=True)
        except Exception:
            logger.exception("Ошибка get_skill_by_slug")
            return None

    async def _enrich_skill(
        self, row, *, user_id: Optional[str], is_admin: bool, include_content: bool
    ) -> Optional[Union[SkillOut, SkillListItem]]:
        uid = (user_id or "").strip().lower() if user_id else None
        author = (row["author_id"] or "").strip().lower()
        is_shared = False
        my_permission = None
        write_access = False

        if is_admin:
            my_permission = SKILL_PERMISSION_OWNER
            write_access = True
        elif uid and author == uid:
            my_permission = SKILL_PERMISSION_OWNER
            write_access = True
        elif uid:
            async with await self.db_connection.acquire() as conn:
                share = await conn.fetchrow(
                    """
                    SELECT permission FROM skill_shares
                    WHERE skill_id = $1 AND LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($2))
                    """,
                    row["id"],
                    uid,
                )
            if share:
                is_shared = True
                my_permission = normalize_skill_permission(share["permission"])
                write_access = my_permission == SKILL_PERMISSION_EDITOR
            elif row["is_public"]:
                my_permission = SKILL_PERMISSION_VIEWER
            else:
                return None
        elif row["is_public"]:
            my_permission = SKILL_PERMISSION_VIEWER
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
                    FROM skill_ratings
                    WHERE skill_id = $1
                    """,
                    row["id"],
                )
                if stats:
                    average_rating = float(stats["average_rating"] or 0)
                    total_votes = int(stats["total_votes"] or 0)
                if uid:
                    rating_row = await conn.fetchrow(
                        """
                        SELECT rating FROM skill_ratings
                        WHERE skill_id = $1 AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                        """,
                        row["id"],
                        uid,
                    )
                    if rating_row:
                        user_rating = rating_row["rating"]
                    bookmark_row = await conn.fetchrow(
                        """
                        SELECT id FROM skill_bookmarks
                        WHERE skill_id = $1 AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                        """,
                        row["id"],
                        uid,
                    )
                    is_bookmarked = bookmark_row is not None
        except Exception:
            logger.exception("Ошибка обогащения skill рейтингом/закладками")

        return self._row_to_out(
            row,
            include_content=include_content,
            is_shared_with_me=is_shared,
            my_permission=my_permission,
            write_access=write_access,
            average_rating=average_rating,
            total_votes=total_votes,
            user_rating=user_rating,
            is_bookmarked=is_bookmarked,
        )

    async def list_skills(
        self, user_id: str, filters: SkillFilters, is_admin: bool = False
    ) -> Tuple[List[SkillListItem], int]:
        uid = (user_id or "").strip().lower()
        limit = filters.limit
        offset = filters.offset
        view = (filters.view_option or "").strip().lower()
        query = (filters.search_query or "").strip()

        try:
            async with await self.db_connection.acquire() as conn:
                where = []
                params: List[Any] = []
                idx = 1

                if is_admin and not view:
                    # admin sees all unless filtered
                    pass
                elif view == "created":
                    where.append(f"LOWER(TRIM(s.author_id)) = LOWER(TRIM(${idx}))")
                    params.append(uid)
                    idx += 1
                elif view == "shared":
                    where.append(f"""EXISTS (
                            SELECT 1 FROM skill_shares ss
                            WHERE ss.skill_id = s.id
                              AND LOWER(TRIM(ss.shared_with_user_id)) = LOWER(TRIM(${idx}))
                        )""")
                    params.append(uid)
                    idx += 1
                elif view == "public":
                    # Как галерея агентов: только опубликованные
                    where.append("s.is_public = true")
                else:
                    where.append(f"""(
                            LOWER(TRIM(s.author_id)) = LOWER(TRIM(${idx}))
                            OR s.is_public = true
                            OR EXISTS (
                                SELECT 1 FROM skill_shares ss
                                WHERE ss.skill_id = s.id
                                  AND LOWER(TRIM(ss.shared_with_user_id)) = LOWER(TRIM(${idx}))
                            )
                        )""")
                    params.append(uid)
                    idx += 1

                if query:
                    where.append(
                        f"(s.name ILIKE ${idx} ESCAPE '\\' "
                        f"OR s.description ILIKE ${idx} ESCAPE '\\' "
                        f"OR s.slug ILIKE ${idx} ESCAPE '\\')"
                    )
                    params.append(f"%{escape_like(query)}%")
                    idx += 1

                where_sql = ("WHERE " + " AND ".join(where)) if where else ""
                total = await conn.fetchval(
                    f"SELECT COUNT(*) FROM skills s {where_sql}",  # noqa: S608
                    *params,
                )
                rows = await conn.fetch(
                    f"""
                    SELECT s.* FROM skills s
                    {where_sql}
                    ORDER BY s.updated_at DESC
                    LIMIT ${idx} OFFSET ${idx + 1}
                    """,  # noqa: S608
                    *params,
                    limit,
                    offset,
                )

            items: List[SkillListItem] = []
            for row in rows:
                item = await self._enrich_skill(
                    row, user_id=uid, is_admin=is_admin, include_content=False
                )
                if item:
                    items.append(item)  # type: ignore[arg-type]
            return items, int(total or 0)
        except Exception:
            logger.exception("Ошибка list_skills")
            return [], 0


    async def get_accessible_skills(self, user_id: str, is_admin: bool = False) -> List[SkillOut]:
        """Все доступные skills с content (для export / inject)."""
        filters = SkillFilters(limit=100, offset=0)
        items, total = await self.list_skills(user_id, filters, is_admin=is_admin)
        result: List[SkillOut] = []
        # paginate if needed
        all_items = list(items)
        offset = filters.limit
        while offset < total:
            more, _ = await self.list_skills(
                user_id, SkillFilters(limit=100, offset=offset), is_admin=is_admin
            )
            all_items.extend(more)
            offset += 100
        for it in all_items:
            full = await self.get_skill_by_id(it.id, user_id=user_id, is_admin=is_admin)
            if full:
                result.append(full)
        return result

    async def update_skill(
        self, skill_id: int, data: SkillUpdate, user_id: str, is_admin: bool = False
    ) -> Optional[SkillOut]:
        if not await self.can_write(skill_id, user_id, is_admin=is_admin):
            return None
        try:
            async with await self.db_connection.acquire() as conn:
                fields = []
                params: List[Any] = []
                n = 1
                if data.slug is not None:
                    new_slug = slugify_skill_id(data.slug)
                    if not new_slug:
                        raise ValueError("Пустое служебное имя (slug)")
                    clash = await conn.fetchval(
                        "SELECT id FROM skills WHERE LOWER(slug) = LOWER($1) AND id <> $2",
                        new_slug,
                        skill_id,
                    )
                    if clash:
                        raise ValueError(f"Служебное имя уже занято: {new_slug}")
                    fields.append(f"slug = ${n}")
                    params.append(new_slug)
                    n += 1
                if data.name is not None:
                    fields.append(f"name = ${n}")
                    params.append(data.name)
                    n += 1
                if data.display_title is not None:
                    fields.append(f"display_title = ${n}")
                    params.append(data.display_title)
                    n += 1
                if data.description is not None:
                    fields.append(f"description = ${n}")
                    params.append(data.description)
                    n += 1
                if data.content is not None:
                    fields.append(f"content = ${n}")
                    params.append(data.content)
                    n += 1
                if data.meta is not None:
                    fields.append(f"meta = ${n}::jsonb")
                    params.append(json.dumps(data.meta))
                    n += 1
                if data.is_active is not None:
                    fields.append(f"is_active = ${n}")
                    params.append(data.is_active)
                    n += 1
                if data.is_public is not None:
                    fields.append(f"is_public = ${n}")
                    params.append(data.is_public)
                    n += 1
                if data.user_invocable is not None:
                    fields.append(f"user_invocable = ${n}")
                    params.append(data.user_invocable)
                    n += 1
                if data.disable_model_invocation is not None:
                    fields.append(f"disable_model_invocation = ${n}")
                    params.append(data.disable_model_invocation)
                    n += 1
                if data.always_apply is not None:
                    fields.append(f"always_apply = ${n}")
                    params.append(data.always_apply)
                    n += 1
                if data.allowed_tools is not None:
                    fields.append(f"allowed_tools = ${n}::jsonb")
                    params.append(json.dumps(data.allowed_tools))
                    n += 1
                if data.category is not None:
                    fields.append(f"category = ${n}")
                    params.append(data.category)
                    n += 1
                fields.append(f"version = COALESCE(version, 1) + 1")
                fields.append(f"updated_at = ${n}")
                params.append(datetime.utcnow())
                n += 1
                if len(fields) <= 2 and data.content is None and data.name is None:
                    # only version+updated — still ok if any field was set above
                    pass
                params.append(skill_id)
                await conn.execute(
                    f"UPDATE skills SET {', '.join(fields)} WHERE id = ${n}",  # noqa: S608
                    *params,
                )
            return await self.get_skill_by_id(skill_id, user_id=user_id, is_admin=is_admin)
        except ValueError:
            raise
        except Exception:
            logger.exception("Ошибка update_skill")
            return None

    async def toggle_skill(self, skill_id: int, user_id: str, is_admin: bool = False) -> Optional[SkillOut]:
        if not await self.can_write(skill_id, user_id, is_admin=is_admin):
            return None
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE skills
                    SET is_active = NOT is_active, updated_at = $2
                    WHERE id = $1
                    """,
                    skill_id,
                    datetime.utcnow(),
                )
            return await self.get_skill_by_id(skill_id, user_id=user_id, is_admin=is_admin)
        except Exception:
            logger.exception("Ошибка toggle_skill")
            return None

    async def delete_skill(self, skill_id: int, user_id: str, is_admin: bool = False) -> bool:
        uid = (user_id or "").strip().lower()
        try:
            async with await self.db_connection.acquire() as conn:
                author = await conn.fetchval(
                    "SELECT LOWER(TRIM(author_id)) FROM skills WHERE id = $1", skill_id
                )
                if not author:
                    return False
                if not is_admin and author != uid:
                    return False
                await conn.execute("DELETE FROM skills WHERE id = $1", skill_id)
                return True
        except Exception:
            logger.exception("Ошибка delete_skill")
            return False

    async def share_skill(
        self,
        skill_id: int,
        owner_id: str,
        usernames: List[str],
        permission: str = SKILL_PERMISSION_VIEWER,
        recipient_permissions: Optional[Dict[str, str]] = None,
    ) -> Tuple[List[str], List[str]]:
        owner_id = (owner_id or "").strip().lower()
        default_permission = normalize_skill_permission(permission)
        if default_permission not in SKILL_SHARE_PERMISSIONS:
            default_permission = SKILL_PERMISSION_VIEWER
        per_user = recipient_permissions or {}
        added: List[str] = []
        skipped: List[str] = []
        try:
            async with await self.db_connection.acquire() as conn:
                author = await conn.fetchval(
                    "SELECT LOWER(TRIM(author_id)) FROM skills WHERE id = $1", skill_id
                )
                if not author or author != owner_id:
                    return ([], usernames)
                for raw in usernames:
                    recipient = (raw or "").strip().lower()
                    if not recipient or recipient == owner_id:
                        skipped.append(raw or "")
                        continue
                    perm = normalize_skill_permission(
                        per_user.get(recipient, default_permission)
                    )
                    if perm not in SKILL_SHARE_PERMISSIONS:
                        perm = SKILL_PERMISSION_VIEWER
                    await conn.execute(
                        """
                        INSERT INTO skill_shares (skill_id, owner_id, shared_with_user_id, permission)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (skill_id, shared_with_user_id) DO UPDATE
                        SET permission = EXCLUDED.permission
                        """,
                        skill_id,
                        owner_id,
                        recipient,
                        perm,
                    )
                    added.append(recipient)
                return (added, skipped)
        except Exception:
            logger.exception("Ошибка share_skill")
            return ([], usernames)

    async def unshare_skill(self, skill_id: int, actor_id: str, target_user_id: str) -> bool:
        actor_id = (actor_id or "").strip().lower()
        target_user_id = (target_user_id or "").strip().lower()
        try:
            async with await self.db_connection.acquire() as conn:
                author = await conn.fetchval(
                    "SELECT LOWER(TRIM(author_id)) FROM skills WHERE id = $1", skill_id
                )
                if not author:
                    return False
                if actor_id != author and actor_id != target_user_id:
                    return False
                result = await conn.execute(
                    """
                    DELETE FROM skill_shares
                    WHERE skill_id = $1 AND LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($2))
                    """,
                    skill_id,
                    target_user_id,
                )
                return result != "DELETE 0"
        except Exception:
            logger.exception("Ошибка unshare_skill")
            return False

    async def list_skill_shares(self, skill_id: int, owner_id: str) -> List[SkillShare]:
        owner_id = (owner_id or "").strip().lower()
        try:
            async with await self.db_connection.acquire() as conn:
                author = await conn.fetchval(
                    "SELECT LOWER(TRIM(author_id)) FROM skills WHERE id = $1", skill_id
                )
                if not author or author != owner_id:
                    return []
                rows = await conn.fetch(
                    """
                    SELECT id, skill_id, owner_id, shared_with_user_id, permission, created_at
                    FROM skill_shares WHERE skill_id = $1 ORDER BY created_at DESC
                    """,
                    skill_id,
                )
                return [
                    SkillShare(
                        id=r["id"],
                        skill_id=r["skill_id"],
                        owner_id=r["owner_id"],
                        shared_with_user_id=r["shared_with_user_id"],
                        permission=r["permission"] or "viewer",
                        created_at=r["created_at"],
                    )
                    for r in rows
                ]
        except Exception:
            logger.exception("Ошибка list_skill_shares")
            return []

    async def resolve_skills_for_user(
        self,
        identifiers: List[str],
        user_id: str,
        is_admin: bool = False,
    ) -> List[SkillOut]:
        """Resolve by numeric id or slug; only active + accessible."""
        result: List[SkillOut] = []
        seen = set()
        seen_ids = set()
        for raw in identifiers:
            key = (raw or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            skill: Optional[SkillOut] = None
            if key.isdigit():
                skill = await self.get_skill_by_id(
                    int(key), user_id=user_id, is_admin=is_admin
                )
            if not skill:
                skill = await self.get_skill_by_slug(
                    key, user_id=user_id, is_admin=is_admin
                )
            # Один и тот же навык мог прийти как id, slug и slug в другом регистре:
            # дедуплицируем по id, иначе его тело дважды попадёт в системный промпт.
            if skill and skill.is_active and skill.id not in seen_ids:
                seen_ids.add(skill.id)
                result.append(skill)
        return result


    async def list_always_apply_skills(
        self, user_id: str, is_admin: bool = False, limit: int = 20
    ) -> List[SkillOut]:
        """Accessible active skills with always_apply=true."""
        uid = (user_id or "").strip().lower()
        try:
            async with await self.db_connection.acquire() as conn:
                if is_admin:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM skills
                        WHERE always_apply = true AND is_active = true
                        ORDER BY updated_at DESC
                        LIMIT $1
                        """,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT s.* FROM skills s
                        WHERE s.always_apply = true AND s.is_active = true
                          AND (
                            LOWER(TRIM(s.author_id)) = LOWER(TRIM($1))
                            OR s.is_public = true
                            OR EXISTS (
                                SELECT 1 FROM skill_shares ss
                                WHERE ss.skill_id = s.id
                                  AND LOWER(TRIM(ss.shared_with_user_id)) = LOWER(TRIM($1))
                            )
                          )
                        ORDER BY s.updated_at DESC
                        LIMIT $2
                        """,
                        uid,
                        limit,
                    )
            out: List[SkillOut] = []
            for row in rows:
                item = await self._enrich_skill(row, user_id=uid, is_admin=is_admin, include_content=True)
                if item:
                    out.append(item)  # type: ignore[arg-type]
            return out
        except Exception:
            logger.exception("Ошибка list_always_apply_skills")
            return []

    async def list_skill_files(self, skill_id: int) -> List[SkillFileOut]:
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, skill_id, relative_path, category, mime_type, bytes,
                           minio_bucket, minio_object, is_executable, created_at, updated_at
                    FROM skill_files WHERE skill_id = $1
                    ORDER BY relative_path
                    """,
                    skill_id,
                )
            return [SkillFileOut(**dict(r)) for r in rows]
        except Exception:
            logger.exception("Ошибка list_skill_files")
            return []

    async def upsert_skill_file_text(
        self,
        skill_id: int,
        relative_path: str,
        content: str,
        *,
        mime_type: str = "text/plain",
        is_executable: bool = False,
        minio_bucket: Optional[str] = None,
        minio_object: Optional[str] = None,
    ) -> Optional[SkillFileOut]:
        path = (relative_path or "").replace("\\", "/").strip().lstrip("/")
        if not path or path.lower() == "skill.md":
            return None
        # Сегменты ".." недопустимы (path traversal).
        if any(part.strip() == ".." for part in path.split("/")):
            return None
        category = infer_skill_file_category(path)
        data = content or ""
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO skill_files (
                        skill_id, relative_path, category, mime_type, bytes,
                        minio_bucket, minio_object, content_text, is_executable, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (skill_id, relative_path) DO UPDATE SET
                        category = EXCLUDED.category,
                        mime_type = EXCLUDED.mime_type,
                        bytes = EXCLUDED.bytes,
                        minio_bucket = COALESCE(EXCLUDED.minio_bucket, skill_files.minio_bucket),
                        minio_object = COALESCE(EXCLUDED.minio_object, skill_files.minio_object),
                        content_text = EXCLUDED.content_text,
                        is_executable = EXCLUDED.is_executable,
                        updated_at = EXCLUDED.updated_at
                    RETURNING id, skill_id, relative_path, category, mime_type, bytes,
                              minio_bucket, minio_object, is_executable, created_at, updated_at
                    """,
                    skill_id,
                    path,
                    category,
                    mime_type,
                    len(data.encode("utf-8")),
                    minio_bucket,
                    minio_object,
                    data,
                    is_executable,
                    datetime.utcnow(),
                )
                await conn.execute(
                    """
                    UPDATE skills SET file_count = (
                        SELECT COUNT(*) FROM skill_files WHERE skill_id = $1
                    ), updated_at = $2 WHERE id = $1
                    """,
                    skill_id,
                    datetime.utcnow(),
                )
            return SkillFileOut(**dict(row)) if row else None
        except Exception:
            logger.exception("Ошибка upsert_skill_file_text")
            return None


    async def get_skill_file_content(self, skill_id: int, relative_path: str) -> Optional[str]:
        path = (relative_path or "").replace("\\", "/").lstrip("/")
        try:
            async with await self.db_connection.acquire() as conn:
                return await conn.fetchval(
                    """
                    SELECT content_text FROM skill_files
                    WHERE skill_id = $1 AND relative_path = $2
                    """,
                    skill_id,
                    path,
                )
        except Exception:
            logger.exception("Ошибка get_skill_file_content")
            return None

    async def delete_skill_file(self, skill_id: int, file_id: int) -> bool:
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM skill_files WHERE id = $1 AND skill_id = $2",
                    file_id,
                    skill_id,
                )
                await conn.execute(
                    """
                    UPDATE skills SET file_count = (
                        SELECT COUNT(*) FROM skill_files WHERE skill_id = $1
                    ), updated_at = $2 WHERE id = $1
                    """,
                    skill_id,
                    datetime.utcnow(),
                )
                return result != "DELETE 0"
        except Exception:
            logger.exception("Ошибка delete_skill_file")
            return False

    async def rate_skill(self, skill_id: int, user_id: str, rating: int) -> bool:
        """Оценка skill пользователем (1–5)."""
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            if rating < 1 or rating > 5:
                return False
            async with await self.db_connection.acquire() as conn:
                exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM skills WHERE id = $1)", skill_id)
                if not exists:
                    logger.warning(f"Попытка оценить несуществующий skill: {skill_id}")
                    return False
                existing = await conn.fetchrow(
                    """
                    SELECT rating, id FROM skill_ratings
                    WHERE skill_id = $1 AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                    """,
                    skill_id,
                    user_id,
                )
                if existing:
                    await conn.execute(
                        """
                        UPDATE skill_ratings
                        SET rating = $1, updated_at = NOW()
                        WHERE skill_id = $2 AND LOWER(TRIM(user_id)) = LOWER(TRIM($3))
                        """,
                        rating,
                        skill_id,
                        user_id,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO skill_ratings (skill_id, user_id, rating)
                        VALUES ($1, $2, $3)
                        """,
                        skill_id,
                        user_id,
                        rating,
                    )
                return True
        except Exception:
            logger.exception("Ошибка rate_skill")
            return False

    async def increment_views(self, skill_id: int) -> bool:
        """Увеличить счётчик просмотров."""
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE skills SET views_count = COALESCE(views_count, 0) + 1
                    WHERE id = $1
                    """,
                    skill_id,
                )
                return result != "UPDATE 0"
        except Exception:
            logger.exception("Ошибка increment_views skill")
            return False

    async def increment_usage(self, skill_id: int) -> bool:
        """Увеличить счётчик использований."""
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE skills SET usage_count = COALESCE(usage_count, 0) + 1
                    WHERE id = $1
                    """,
                    skill_id,
                )
                return result != "UPDATE 0"
        except Exception:
            logger.exception("Ошибка increment_usage skill")
            return False

    async def add_bookmark(self, skill_id: int, user_id: str) -> bool:
        """Добавить skill в закладки."""
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            async with await self.db_connection.acquire() as conn:
                exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM skills WHERE id = $1)", skill_id)
                if not exists:
                    logger.warning(f"Попытка добавить в закладки несуществующий skill: {skill_id}")
                    return False
                await conn.execute(
                    """
                    INSERT INTO skill_bookmarks (skill_id, user_id)
                    VALUES ($1, $2)
                    ON CONFLICT (skill_id, user_id) DO NOTHING
                    """,
                    skill_id,
                    user_id,
                )
                return True
        except Exception:
            logger.exception("Ошибка add_bookmark skill")
            return False

    async def remove_bookmark(self, skill_id: int, user_id: str) -> bool:
        """Удалить skill из закладок."""
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    DELETE FROM skill_bookmarks
                    WHERE skill_id = $1 AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                    """,
                    skill_id,
                    user_id,
                )
                return True
        except Exception:
            logger.exception("Ошибка remove_bookmark skill")
            return False

    async def get_user_bookmarks(
        self, user_id: str, limit: int = 100, offset: int = 0
    ) -> Tuple[List[int], int]:
        """Список ID skills в закладках пользователя."""
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT skill_id
                    FROM skill_bookmarks
                    WHERE LOWER(TRIM(user_id)) = LOWER(TRIM($1))
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    user_id,
                    limit,
                    offset,
                )
                total = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM skill_bookmarks
                    WHERE LOWER(TRIM(user_id)) = LOWER(TRIM($1))
                    """,
                    user_id,
                )
                return ([row["skill_id"] for row in rows], int(total or 0))
        except Exception:
            logger.exception("Ошибка get_user_bookmarks skill")
            return ([], 0)
