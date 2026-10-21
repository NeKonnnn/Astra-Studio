"""Репозиторий метаданных пользовательских проектов (PostgreSQL)."""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.database.postgresql.connection import PostgreSQLConnection
from backend.database.postgresql.project_models import ProjectCreate, ProjectUpdate
from backend.settings.logging import get_logger

logger = get_logger(__name__)


def _normalize_user_id(user_id: str) -> str:
    return (user_id or "").strip().lower()


def _parse_kb_ids(raw: Any) -> List[int]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out: List[int] = []
    for v in raw:
        try:
            kid = int(v)
        except (TypeError, ValueError):
            continue
        if kid > 0:
            out.append(kid)
    return sorted(set(out))


def _row_to_project(row) -> Dict[str, Any]:
    created = row["created_at"]
    updated = row["updated_at"]
    keys = set(row.keys())
    return {
        "id": row["id"],
        "name": row["name"],
        "instructions": row["instructions"] or "",
        "memory": row["memory"] or "default",
        "icon": row["icon"],
        "iconType": row["icon_type"],
        "iconColor": row["icon_color"],
        "knowledgeBasesEnabled": bool(
            row["knowledge_bases_enabled"]
            if "knowledge_bases_enabled" in keys
            else False
        ),
        "knowledgeBaseIds": _parse_kb_ids(
            row["knowledge_base_ids"] if "knowledge_base_ids" in keys else []
        ),
        "createdAt": (
            created.isoformat() if isinstance(created, datetime) else str(created)
        ),
        "updatedAt": (
            updated.isoformat() if isinstance(updated, datetime) else str(updated)
        ),
    }


def _generate_project_id() -> str:
    return f"{int(datetime.utcnow().timestamp() * 1000)}{secrets.token_hex(4)}"


class ProjectRepository:
    def __init__(self, db_connection: PostgreSQLConnection):
        self.db_connection = db_connection

    async def create_tables(self) -> None:
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_projects (
                        id VARCHAR(64) PRIMARY KEY,
                        user_id VARCHAR(100) NOT NULL,
                        name VARCHAR(255) NOT NULL,
                        instructions TEXT NOT NULL DEFAULT '',
                        memory VARCHAR(32) NOT NULL DEFAULT 'default',
                        icon VARCHAR(64),
                        icon_type VARCHAR(16),
                        icon_color VARCHAR(32),
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                    """)
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_projects_user ON user_projects(user_id)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_projects_updated ON user_projects(updated_at DESC)"
                )
                for stmt in (
                    "ALTER TABLE user_projects ADD COLUMN IF NOT EXISTS "
                    "knowledge_bases_enabled BOOLEAN NOT NULL DEFAULT false",
                    "ALTER TABLE user_projects ADD COLUMN IF NOT EXISTS "
                    "knowledge_base_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
                ):
                    await conn.execute(stmt)
            logger.info("Таблица user_projects готова")
        except Exception:
            logger.exception("Ошибка при создании таблицы user_projects")
            raise

    async def list_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        uid = _normalize_user_id(user_id)
        if not uid:
            return []
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, name, instructions, memory, icon, icon_type, icon_color,
                           knowledge_bases_enabled, knowledge_base_ids,
                           created_at, updated_at
                    FROM user_projects
                    WHERE user_id = $1
                    ORDER BY updated_at DESC, created_at DESC
                    """,
                    uid,
                )
            return [_row_to_project(row) for row in rows]
        except Exception:
            logger.exception("user_projects.list_by_user failed user_id=%s", uid)
            return []

    async def get_by_id(
        self, user_id: str, project_id: str
    ) -> Optional[Dict[str, Any]]:
        uid = _normalize_user_id(user_id)
        pid = (project_id or "").strip()
        if not uid or not pid:
            return None
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, user_id, name, instructions, memory, icon, icon_type, icon_color,
                           knowledge_bases_enabled, knowledge_base_ids,
                           created_at, updated_at
                    FROM user_projects
                    WHERE user_id = $1 AND id = $2
                    """,
                    uid,
                    pid,
                )
            return _row_to_project(row) if row else None
        except Exception:
            logger.exception(
                "user_projects.get_by_id failed user_id=%s project_id=%s", uid, pid
            )
            return None

    async def create(
        self, user_id: str, data: ProjectCreate
    ) -> Optional[Dict[str, Any]]:
        uid = _normalize_user_id(user_id)
        if not uid:
            return None
        pid = (data.id or "").strip() or _generate_project_id()
        name = (data.name or "").strip()
        if not name:
            return None
        memory = (
            data.memory if data.memory in ("default", "project-only") else "default"
        )
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO user_projects (
                        id, user_id, name, instructions, memory, icon, icon_type, icon_color,
                        knowledge_bases_enabled, knowledge_base_ids, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        name = EXCLUDED.name,
                        instructions = EXCLUDED.instructions,
                        memory = EXCLUDED.memory,
                        icon = EXCLUDED.icon,
                        icon_type = EXCLUDED.icon_type,
                        icon_color = EXCLUDED.icon_color,
                        knowledge_bases_enabled = EXCLUDED.knowledge_bases_enabled,
                        knowledge_base_ids = EXCLUDED.knowledge_base_ids,
                        updated_at = NOW()
                    WHERE user_projects.user_id = EXCLUDED.user_id
                    RETURNING id, user_id, name, instructions, memory, icon, icon_type, icon_color,
                              knowledge_bases_enabled, knowledge_base_ids,
                              created_at, updated_at
                    """,
                    pid,
                    uid,
                    name,
                    (data.instructions or "").strip(),
                    memory,
                    data.icon,
                    data.icon_type,
                    data.icon_color,
                    bool(data.knowledge_bases_enabled),
                    json.dumps(_parse_kb_ids(data.knowledge_base_ids)),
                )
            return _row_to_project(row) if row else None
        except Exception:
            logger.exception(
                "user_projects.create failed user_id=%s project_id=%s", uid, pid
            )
            return None

    async def update(
        self, user_id: str, project_id: str, data: ProjectUpdate
    ) -> Optional[Dict[str, Any]]:
        uid = _normalize_user_id(user_id)
        pid = (project_id or "").strip()
        if not uid or not pid:
            return None
        fields: List[str] = []
        params: List[Any] = [uid, pid]
        idx = 3

        if data.name is not None:
            name = data.name.strip()
            if not name:
                return None
            fields.append(f"name = ${idx}")
            params.append(name)
            idx += 1
        if data.instructions is not None:
            fields.append(f"instructions = ${idx}")
            params.append(data.instructions.strip())
            idx += 1
        if data.memory is not None:
            memory = (
                data.memory if data.memory in ("default", "project-only") else "default"
            )
            fields.append(f"memory = ${idx}")
            params.append(memory)
            idx += 1
        if data.icon is not None:
            fields.append(f"icon = ${idx}")
            params.append(data.icon)
            idx += 1
        if data.icon_type is not None:
            fields.append(f"icon_type = ${idx}")
            params.append(data.icon_type)
            idx += 1
        if data.icon_color is not None:
            fields.append(f"icon_color = ${idx}")
            params.append(data.icon_color)
            idx += 1
        if data.knowledge_bases_enabled is not None:
            fields.append(f"knowledge_bases_enabled = ${idx}")
            params.append(bool(data.knowledge_bases_enabled))
            idx += 1
        if data.knowledge_base_ids is not None:
            fields.append(f"knowledge_base_ids = ${idx}::jsonb")
            params.append(json.dumps(_parse_kb_ids(data.knowledge_base_ids)))
            idx += 1

        if not fields:
            return await self.get_by_id(uid, pid)

        fields.append("updated_at = NOW()")
        sql = f"""
            UPDATE user_projects
            SET {", ".join(fields)}
            WHERE user_id = $1 AND id = $2
            RETURNING id, user_id, name, instructions, memory, icon, icon_type, icon_color,
                      knowledge_bases_enabled, knowledge_base_ids,
                      created_at, updated_at
        """
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
            return _row_to_project(row) if row else None
        except Exception:
            logger.exception(
                "user_projects.update failed user_id=%s project_id=%s", uid, pid
            )
            return None

    async def delete(self, user_id: str, project_id: str) -> bool:
        uid = _normalize_user_id(user_id)
        pid = (project_id or "").strip()
        if not uid or not pid:
            return False
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM user_projects WHERE user_id = $1 AND id = $2",
                    uid,
                    pid,
                )
            if result.endswith("1"):
                await self._delete_entity_settings(pid)
                return True
            return False
        except Exception:
            logger.exception(
                "user_projects.delete failed user_id=%s project_id=%s", uid, pid
            )
            return False

    async def delete_by_id(self, project_id: str) -> bool:
        """Удаление метаданных без проверки user_id (оркестрационное удаление проекта)."""
        pid = (project_id or "").strip()
        if not pid:
            return False
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM user_projects WHERE id = $1", pid
                )
            if result.endswith("1"):
                await self._delete_entity_settings(pid)
                return True
            return False
        except Exception:
            logger.exception("user_projects.delete_by_id failed project_id=%s", pid)
            return False

    async def _delete_entity_settings(self, project_id: str) -> None:
        """Убрать RAG-настройки удалённого проекта.

        Внешнего ключа у таблицы настроек нет: ``agents.id`` — INTEGER, а
        ``user_projects.id`` — VARCHAR, один FK на две родительские таблицы
        Postgres не даёт. Сбой здесь проект не воскрешает: осиротевшая строка
        подчистится на старте (``cleanup_orphans``).
        """
        try:
            from backend.database.init_db import get_entity_settings_repository

            repo = get_entity_settings_repository()
            if repo is not None:
                await repo.delete("project", project_id)
        except Exception:
            logger.debug(
                "Не удалось убрать настройки проекта %s — подчистятся на старте",
                project_id,
                exc_info=True,
            )
