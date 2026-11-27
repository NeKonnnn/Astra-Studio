"""
Репозиторий персональных LLM-настроек и контекстных промптов пользователя.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from backend.database.postgresql.connection import PostgreSQLConnection
from backend.settings.logging import get_logger

logger = get_logger(__name__)


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            logger.exception("user_llm_settings: не удалось распарсить JSONB")
    return {}


class UserSettingsRepository:
    """CRUD для таблицы user_llm_settings."""

    def __init__(self, db_connection: PostgreSQLConnection):
        self.db_connection = db_connection

    async def create_tables(self) -> None:
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_llm_settings (
                        user_id VARCHAR(100) PRIMARY KEY,
                        model_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                        context_prompts JSONB NOT NULL DEFAULT '{}'::jsonb,
                        rag_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                    """
                )
                # Миграция для существующих инсталляций без колонки rag_settings
                await conn.execute(
                    """
                    ALTER TABLE user_llm_settings
                    ADD COLUMN IF NOT EXISTS rag_settings JSONB NOT NULL DEFAULT '{}'::jsonb
                    """
                )
                await conn.execute(
                    """
                    ALTER TABLE user_llm_settings
                    ADD COLUMN IF NOT EXISTS selected_model_path TEXT NOT NULL DEFAULT ''
                    """
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_llm_settings_updated ON user_llm_settings(updated_at DESC)"
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_user_llm_settings_selected_model
                    ON user_llm_settings(updated_at DESC)
                    WHERE selected_model_path <> ''
                    """
                )
            logger.info("Таблица user_llm_settings готова")
        except Exception:
            logger.exception("Ошибка при создании таблицы user_llm_settings")
            raise

    async def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        uid = (user_id or "").strip().lower()
        if not uid:
            return None
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT user_id, model_settings, context_prompts, rag_settings,
                           selected_model_path, created_at, updated_at
                    FROM user_llm_settings
                    WHERE user_id = $1
                    """,
                    uid,
                )
            if not row:
                return None
            return {
                "user_id": row["user_id"],
                "model_settings": _as_dict(row["model_settings"]),
                "context_prompts": _as_dict(row["context_prompts"]),
                "rag_settings": _as_dict(row["rag_settings"]),
                "selected_model_path": str(row["selected_model_path"] or "").strip(),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        except Exception:
            logger.exception("user_llm_settings.get failed user_id=%s", uid)
            return None

    async def upsert(
        self,
        user_id: str,
        *,
        model_settings: Optional[Dict[str, Any]] = None,
        context_prompts: Optional[Dict[str, Any]] = None,
        rag_settings: Optional[Dict[str, Any]] = None,
        selected_model_path: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        uid = (user_id or "").strip().lower()
        if not uid:
            return None
        ms = model_settings if isinstance(model_settings, dict) else {}
        cp = context_prompts if isinstance(context_prompts, dict) else {}
        rs = rag_settings if isinstance(rag_settings, dict) else {}
        smp = (
            str(selected_model_path).strip()
            if selected_model_path is not None
            else ""
        )
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO user_llm_settings (
                        user_id, model_settings, context_prompts, rag_settings,
                        selected_model_path, updated_at
                    )
                    VALUES ($1, $2::jsonb, $3::jsonb, $4::jsonb, $5, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        model_settings = CASE
                            WHEN $6::boolean THEN EXCLUDED.model_settings
                            ELSE user_llm_settings.model_settings
                        END,
                        context_prompts = CASE
                            WHEN $7::boolean THEN EXCLUDED.context_prompts
                            ELSE user_llm_settings.context_prompts
                        END,
                        rag_settings = CASE
                            WHEN $8::boolean THEN EXCLUDED.rag_settings
                            ELSE user_llm_settings.rag_settings
                        END,
                        selected_model_path = CASE
                            WHEN $9::boolean THEN EXCLUDED.selected_model_path
                            ELSE user_llm_settings.selected_model_path
                        END,
                        updated_at = NOW()
                    RETURNING user_id, model_settings, context_prompts, rag_settings,
                              selected_model_path, created_at, updated_at
                    """,
                    uid,
                    json.dumps(ms, ensure_ascii=False),
                    json.dumps(cp, ensure_ascii=False),
                    json.dumps(rs, ensure_ascii=False),
                    smp,
                    model_settings is not None,
                    context_prompts is not None,
                    rag_settings is not None,
                    selected_model_path is not None,
                )
            if not row:
                return None
            return {
                "user_id": row["user_id"],
                "model_settings": _as_dict(row["model_settings"]),
                "context_prompts": _as_dict(row["context_prompts"]),
                "rag_settings": _as_dict(row["rag_settings"]),
                "selected_model_path": str(row["selected_model_path"] or "").strip(),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        except Exception:
            logger.exception("user_llm_settings.upsert failed user_id=%s", uid)
            return None

    async def get_latest_selected_model_path(self) -> Optional[str]:
        """Самая свежая выбранная модель среди пользователей (для прогрева при старте)."""
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT selected_model_path
                    FROM user_llm_settings
                    WHERE selected_model_path IS NOT NULL
                      AND btrim(selected_model_path) <> ''
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                )
            if not row:
                return None
            path = str(row["selected_model_path"] or "").strip()
            return path or None
        except Exception:
            logger.exception("user_llm_settings.get_latest_selected_model_path failed")
            return None

    async def delete(self, user_id: str) -> bool:
        uid = (user_id or "").strip().lower()
        if not uid:
            return False
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.execute("DELETE FROM user_llm_settings WHERE user_id = $1", uid)
            return result.endswith("1")
        except Exception:
            logger.exception("user_llm_settings.delete failed user_id=%s", uid)
            return False
