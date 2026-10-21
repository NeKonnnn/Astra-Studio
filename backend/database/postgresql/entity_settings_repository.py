"""
Репозиторий RAG-настроек агентов и проектов.

Настройки принадлежат СУЩНОСТИ, а не пользователю: агента расшаривают, и
читатель должен искать по тем же параметрам, которыми залит его корпус. Раньше
запись лежала в ``user_llm_settings.rag_settings`` по ``user_id``, из-за чего у
читателя применялись его собственные настройки, а правка редактора не доезжала
до владельца.

Одна таблица на оба скоупа. Внешнего ключа нет намеренно: ``agents.id`` —
INTEGER, ``user_projects.id`` — VARCHAR, один FK на две родительские таблицы
Postgres не даёт. Уборка явная (см. ``delete``) плюс чистка сирот на старте.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.database.postgresql.connection import PostgreSQLConnection
from backend.settings.logging import get_logger

logger = get_logger(__name__)

SCOPE_AGENT = "agent"
SCOPE_PROJECT = "project"
SCOPE_KB = "kb"
SCOPES: Tuple[str, ...] = (SCOPE_AGENT, SCOPE_PROJECT, SCOPE_KB)

# Отметка о разовом переносе настроек из user_llm_settings. Менять только вместе
# с новой миграцией: по этому имени определяется, что перенос уже был.
MIGRATION_NAME = "entity_rag_settings_v1"

# Своё число для pg_advisory_xact_lock. Реплик backend может быть несколько, и
# стартуют они одновременно — без лока перенос пойдёт параллельно в несколько рук.
_MIGRATION_LOCK_ID = 774_120_001


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            logger.exception("entity_rag_settings: не удалось распарсить JSONB")
    return {}


def normalize_scope(raw: Optional[str]) -> str:
    s = (raw or "").strip().lower()
    return s if s in SCOPES else SCOPE_PROJECT


def normalize_entity_id(raw: Any) -> Optional[str]:
    s = str(raw if raw is not None else "").strip()
    return s or None


class EntitySettingsRepository:
    """CRUD для таблицы entity_rag_settings."""

    def __init__(self, db_connection: PostgreSQLConnection):
        self.db_connection = db_connection

    async def create_tables(self) -> None:
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS entity_rag_settings (
                        scope      VARCHAR(16)  NOT NULL,
                        entity_id  VARCHAR(64)  NOT NULL,
                        settings   JSONB        NOT NULL DEFAULT '{}'::jsonb,
                        updated_by VARCHAR(100),
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW(),
                        PRIMARY KEY (scope, entity_id)
                    )
                    """
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_entity_rag_settings_updated "
                    "ON entity_rag_settings(updated_at DESC)"
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS backend_data_migrations (
                        name       VARCHAR(128) PRIMARY KEY,
                        applied_at TIMESTAMP DEFAULT NOW(),
                        details    JSONB DEFAULT '{}'::jsonb
                    )
                    """
                )
            logger.info("Таблица entity_rag_settings готова")
        except Exception:
            logger.exception("Ошибка при создании таблицы entity_rag_settings")
            raise

    # --- чтение -------------------------------------------------------------

    async def get(self, scope: str, entity_id: str) -> Optional[Dict[str, Any]]:
        """Сырые настройки сущности. None — записи нет (значит дефолты кластера)."""
        sc = normalize_scope(scope)
        ek = normalize_entity_id(entity_id)
        if not ek:
            return None
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT settings FROM entity_rag_settings WHERE scope = $1 AND entity_id = $2",
                    sc,
                    ek,
                )
            return _as_dict(row["settings"]) if row else None
        except Exception:
            logger.exception("entity_rag_settings.get failed scope=%s entity=%s", sc, ek)
            return None

    async def get_many(self, scope: str, entity_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """Настройки пачки сущностей одним запросом — для списков в UI."""
        sc = normalize_scope(scope)
        keys = [k for k in (normalize_entity_id(e) for e in entity_ids) if k]
        if not keys:
            return {}
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT entity_id, settings FROM entity_rag_settings "
                    "WHERE scope = $1 AND entity_id = ANY($2::varchar[])",
                    sc,
                    keys,
                )
            return {r["entity_id"]: _as_dict(r["settings"]) for r in rows}
        except Exception:
            logger.exception("entity_rag_settings.get_many failed scope=%s", sc)
            return {}

    # --- запись -------------------------------------------------------------

    async def upsert(
        self,
        scope: str,
        entity_id: str,
        settings: Dict[str, Any],
        updated_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Записать настройки целиком. Слияние с прежними — на стороне сервиса."""
        sc = normalize_scope(scope)
        ek = normalize_entity_id(entity_id)
        if not ek:
            return None
        payload = settings if isinstance(settings, dict) else {}
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO entity_rag_settings (scope, entity_id, settings, updated_by, updated_at)
                    VALUES ($1, $2, $3::jsonb, $4, NOW())
                    ON CONFLICT (scope, entity_id) DO UPDATE SET
                        settings   = EXCLUDED.settings,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW()
                    RETURNING settings
                    """,
                    sc,
                    ek,
                    json.dumps(payload, ensure_ascii=False),
                    (updated_by or "").strip().lower() or None,
                )
            return _as_dict(row["settings"]) if row else None
        except Exception:
            logger.exception("entity_rag_settings.upsert failed scope=%s entity=%s", sc, ek)
            return None

    async def delete(self, scope: str, entity_id: str) -> bool:
        """Убрать настройки сущности — сброс на дефолты кластера и уборка при удалении."""
        sc = normalize_scope(scope)
        ek = normalize_entity_id(entity_id)
        if not ek:
            return False
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM entity_rag_settings WHERE scope = $1 AND entity_id = $2",
                    sc,
                    ek,
                )
            return not str(result).endswith(" 0")
        except Exception:
            logger.exception("entity_rag_settings.delete failed scope=%s entity=%s", sc, ek)
            return False

    # --- уборка и перенос ---------------------------------------------------

    async def cleanup_orphans(self) -> int:
        """Строки без агента и без проекта. Бывают после удаления мимо репозитория."""
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute(
                    """
                    DELETE FROM entity_rag_settings s
                    WHERE (s.scope = 'agent'
                           AND s.entity_id ~ '^[0-9]+$'
                           AND NOT EXISTS (SELECT 1 FROM agents a WHERE a.id = s.entity_id::int))
                       OR (s.scope = 'project'
                           AND NOT EXISTS (SELECT 1 FROM user_projects p WHERE p.id = s.entity_id))
                       OR (s.scope = 'kb'
                           AND s.entity_id ~ '^[0-9]+$'
                           AND NOT EXISTS (
                               SELECT 1 FROM knowledge_bases k WHERE k.id = s.entity_id::int
                           ))
                    """
                )
            removed = int(str(result).rsplit(" ", 1)[-1] or 0)
            if removed:
                logger.info("entity_rag_settings: убрано осиротевших записей: %s", removed)
            return removed
        except Exception:
            # Таблиц agents / user_projects может не быть в момент первого старта.
            logger.debug("entity_rag_settings: чистка сирот пропущена", exc_info=True)
            return 0

    async def migration_applied(self, name: str = MIGRATION_NAME) -> bool:
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT applied_at FROM backend_data_migrations WHERE name = $1", name
                )
            return row is not None
        except Exception:
            logger.exception("backend_data_migrations: проверка отметки не удалась")
            # Считаем «уже применена»: лучше не перенести, чем перенести дважды
            # и воскресить настройки, которые пользователь сбросил.
            return True

    async def mark_migration(self, details: Dict[str, Any], name: str = MIGRATION_NAME) -> None:
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO backend_data_migrations (name, details)
                    VALUES ($1, $2::jsonb)
                    ON CONFLICT (name) DO UPDATE SET
                        details = EXCLUDED.details,
                        applied_at = NOW()
                    """,
                    name,
                    json.dumps(details or {}, ensure_ascii=False),
                )
        except Exception:
            logger.exception("backend_data_migrations: не удалось поставить отметку")

    async def clear_migration_mark(self, name: str = MIGRATION_NAME) -> None:
        """Для RAG_ENTITY_SETTINGS_MIGRATE=force."""
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute("DELETE FROM backend_data_migrations WHERE name = $1", name)
            logger.warning("backend_data_migrations: отметка %s снята — перенос пойдёт заново", name)
        except Exception:
            logger.exception("backend_data_migrations: не удалось снять отметку")

    async def insert_missing(self, rows: List[Tuple[str, str, Dict[str, Any], Optional[str]]]) -> int:
        """Вставить настройки, не трогая существующие. Возвращает число вставленных."""
        if not rows:
            return 0
        inserted = 0
        try:
            async with await self.db_connection.acquire() as conn:
                for scope, entity_id, settings, owner in rows:
                    ek = normalize_entity_id(entity_id)
                    if not ek:
                        continue
                    result = await conn.execute(
                        """
                        INSERT INTO entity_rag_settings (scope, entity_id, settings, updated_by)
                        VALUES ($1, $2, $3::jsonb, $4)
                        ON CONFLICT (scope, entity_id) DO NOTHING
                        """,
                        normalize_scope(scope),
                        ek,
                        json.dumps(settings or {}, ensure_ascii=False),
                        (owner or "").strip().lower() or None,
                    )
                    if not str(result).endswith(" 0"):
                        inserted += 1
            return inserted
        except Exception:
            logger.exception("entity_rag_settings: вставка перенесённых настроек не удалась")
            return inserted

    async def advisory_lock(self, conn) -> None:
        """Лок на время транзакции переноса — снимается сам при её завершении."""
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _MIGRATION_LOCK_ID)

    async def count_foreign_entity_copies(self) -> int:
        """Сколько записей ``entities.*`` принадлежит НЕ владельцу сущности.

        Это настройки, накрученные читателем или редактором под себя для чужого
        агента: при переносе выигрывает владелец, а их копии отбрасываются.
        Число нужно, чтобы понимать масштаб — у этих людей поиск по расшаренным
        агентам изменится. Считаем в базе: строки чужаков в кэш переноса не
        попадают, там только владельцы.
        """
        try:
            async with await self.db_connection.acquire() as conn:
                value = await conn.fetchval(
                    """
                    WITH e AS (
                        SELECT LOWER(TRIM(u.user_id)) AS user_id,
                               s.key   AS scope,
                               x.key   AS entity_id
                        FROM user_llm_settings u,
                             LATERAL jsonb_each(
                                 CASE WHEN jsonb_typeof(u.rag_settings->'entities') = 'object'
                                      THEN u.rag_settings->'entities'
                                      ELSE '{}'::jsonb END
                             ) AS s(key, value),
                             LATERAL jsonb_each(
                                 CASE WHEN jsonb_typeof(s.value) = 'object'
                                      THEN s.value
                                      ELSE '{}'::jsonb END
                             ) AS x(key, value)
                    )
                    SELECT COUNT(*)
                    FROM e
                    LEFT JOIN agents a
                           ON e.scope = 'agent'
                          AND e.entity_id ~ '^[0-9]+$'
                          AND a.id = e.entity_id::int
                    LEFT JOIN user_projects p
                           ON e.scope = 'project'
                          AND p.id = e.entity_id
                    WHERE (a.id IS NOT NULL AND LOWER(TRIM(a.author_id)) <> e.user_id)
                       OR (p.id IS NOT NULL AND LOWER(TRIM(p.user_id)) <> e.user_id)
                    """
                )
            return int(value or 0)
        except Exception:
            logger.debug("entity_rag_settings: подсчёт чужих копий не удался", exc_info=True)
            return 0

    async def owners_snapshot(self) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
        """(агенты, проекты) как пары (entity_id, владелец) — вход для переноса."""
        agents: List[Tuple[str, str]] = []
        projects: List[Tuple[str, str]] = []
        try:
            async with await self.db_connection.acquire() as conn:
                for row in await conn.fetch(
                    "SELECT id, LOWER(TRIM(author_id)) AS owner FROM agents"
                ):
                    if row["owner"]:
                        agents.append((str(row["id"]), row["owner"]))
                for row in await conn.fetch(
                    "SELECT id, LOWER(TRIM(user_id)) AS owner FROM user_projects"
                ):
                    if row["owner"]:
                        projects.append((str(row["id"]), row["owner"]))
        except Exception:
            logger.exception("entity_rag_settings: не удалось прочитать владельцев сущностей")
        return agents, projects
