"""PostgreSQL-репозиторий «Живых сессий» AstraStudio."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.database.postgresql.connection import PostgreSQLConnection
from backend.database.postgresql.live_session_models import (
    LiveCollectionCreate,
    LiveCollectionUpdate,
    LiveSessionCreate,
    LiveSessionUpdate,
)
from backend.settings.logging import get_logger

logger = get_logger(__name__)

_TRASH_DAYS = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _uid(user_id: str) -> str:
    return (user_id or "").strip().lower()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(_utcnow().timestamp() * 1000)}_{secrets.token_hex(4)}"


def _parse_json_list(raw: Any) -> List[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _parse_json_obj(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


class LiveSessionRepository:
    def __init__(self, db_connection: PostgreSQLConnection):
        self.db = db_connection

    async def create_tables(self) -> None:
        async with await self.db.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_sessions (
                    id VARCHAR(64) PRIMARY KEY,
                    user_id VARCHAR(100) NOT NULL,
                    title VARCHAR(255) NOT NULL DEFAULT '',
                    status VARCHAR(32) NOT NULL DEFAULT 'draft',
                    language VARCHAR(16) NOT NULL DEFAULT 'auto',
                    source_app VARCHAR(64),
                    participants JSONB NOT NULL DEFAULT '[]'::jsonb,
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP,
                    duration_ms BIGINT NOT NULL DEFAULT 0,
                    paused_at TIMESTAMP,
                    pause_accumulated_ms BIGINT NOT NULL DEFAULT 0,
                    brief_status VARCHAR(32) NOT NULL DEFAULT 'idle',
                    user_notes TEXT NOT NULL DEFAULT '',
                    brief_markdown TEXT NOT NULL DEFAULT '',
                    local_audio_key TEXT,
                    remote_audio_key TEXT,
                    mixed_audio_key TEXT,
                    keep_audio BOOLEAN NOT NULL DEFAULT TRUE,
                    trashed_at TIMESTAMP,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_session_segments (
                    id VARCHAR(64) PRIMARY KEY,
                    session_id VARCHAR(64) NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
                    track VARCHAR(16) NOT NULL,
                    start_ms BIGINT NOT NULL,
                    end_ms BIGINT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    speaker VARCHAR(128),
                    is_echo BOOLEAN NOT NULL DEFAULT FALSE,
                    confidence DOUBLE PRECISION,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_collections (
                    id VARCHAR(64) PRIMARY KEY,
                    user_id VARCHAR(100) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    color VARCHAR(32),
                    icon VARCHAR(64),
                    sort_order INT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_collection_items (
                    collection_id VARCHAR(64) NOT NULL REFERENCES live_collections(id) ON DELETE CASCADE,
                    session_id VARCHAR(64) NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (collection_id, session_id)
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_sessions_user ON live_sessions(user_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_sessions_status ON live_sessions(user_id, status)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_sessions_trashed ON live_sessions(user_id, trashed_at)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_segments_session ON live_session_segments(session_id, start_ms)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_collections_user ON live_collections(user_id)"
            )
            # FTS для поиска по стенограмме и брифу
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_session_search (
                    session_id VARCHAR(64) PRIMARY KEY REFERENCES live_sessions(id) ON DELETE CASCADE,
                    user_id VARCHAR(100) NOT NULL,
                    document TSVECTOR
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_session_search_tsv ON live_session_search USING GIN(document)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_live_session_search_user ON live_session_search(user_id)"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_session_prefs (
                    user_id VARCHAR(100) PRIMARY KEY,
                    prefs JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )

    def _row_card(self, row) -> Dict[str, Any]:
        keys = set(row.keys())
        preview = ""
        if "brief_markdown" in keys and row["brief_markdown"]:
            preview = str(row["brief_markdown"])[:180]
        elif "user_notes" in keys and row["user_notes"]:
            preview = str(row["user_notes"])[:180]
        return {
            "id": row["id"],
            "title": row["title"] or "Без названия",
            "status": row["status"],
            "language": row["language"] or "auto",
            "source_app": row["source_app"],
            "participants": [str(x) for x in _parse_json_list(row["participants"])],
            "started_at": _iso(row["started_at"]),
            "ended_at": _iso(row["ended_at"]),
            "duration_ms": int(row["duration_ms"] or 0),
            "brief_status": row["brief_status"] or "idle",
            "preview": preview,
            "trashed_at": _iso(row["trashed_at"]) if "trashed_at" in keys else None,
            "created_at": _iso(row["created_at"]) or "",
            "updated_at": _iso(row["updated_at"]) or "",
        }

    async def create_session(self, user_id: str, data: LiveSessionCreate) -> Dict[str, Any]:
        sid = _new_id("ls")
        title = (data.title or "").strip() or "Новая сессия"
        now = _utcnow()
        async with await self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO live_sessions (
                    id, user_id, title, status, language, source_app, participants,
                    created_at, updated_at
                ) VALUES ($1,$2,$3,'draft',$4,$5,$6::jsonb,$7,$7)
                """,
                sid,
                _uid(user_id),
                title,
                data.language or "auto",
                data.source_app,
                json.dumps(data.participants or [], ensure_ascii=False),
                now,
            )
        return await self.get_session(user_id, sid)

    async def get_session(
        self, user_id: str, session_id: str, *, include_trashed: bool = False
    ) -> Optional[Dict[str, Any]]:
        async with await self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM live_sessions
                WHERE id=$1 AND user_id=$2
                """,
                session_id,
                _uid(user_id),
            )
            if not row:
                return None
            if row["trashed_at"] is not None and not include_trashed:
                return None
            detail = self._row_card(row)
            detail.update(
                {
                    "user_notes": row["user_notes"] or "",
                    "brief_markdown": row["brief_markdown"] or "",
                    "local_audio_key": row["local_audio_key"],
                    "remote_audio_key": row["remote_audio_key"],
                    "mixed_audio_key": row["mixed_audio_key"],
                    "keep_audio": bool(row["keep_audio"]),
                    "metadata": _parse_json_obj(row["metadata"]),
                    "segments": await self._list_segments(conn, session_id),
                    "collection_ids": await self._collection_ids(conn, session_id),
                }
            )
            return detail

    async def _list_segments(self, conn, session_id: str) -> List[Dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT id, track, start_ms, end_ms, text, speaker, is_echo, confidence
            FROM live_session_segments
            WHERE session_id=$1
            ORDER BY start_ms ASC, created_at ASC
            """,
            session_id,
        )
        return [
            {
                "id": r["id"],
                "track": r["track"],
                "start_ms": int(r["start_ms"]),
                "end_ms": int(r["end_ms"]),
                "text": r["text"] or "",
                "speaker": r["speaker"],
                "is_echo": bool(r["is_echo"]),
                "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            }
            for r in rows
        ]

    async def _collection_ids(self, conn, session_id: str) -> List[str]:
        rows = await conn.fetch(
            "SELECT collection_id FROM live_collection_items WHERE session_id=$1",
            session_id,
        )
        return [r["collection_id"] for r in rows]

    async def list_sessions(
        self,
        user_id: str,
        *,
        trashed: bool = False,
        collection_id: Optional[str] = None,
        limit: int = 50,
        before: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(100, int(limit or 50)))
        params: List[Any] = [_uid(user_id)]
        where = ["user_id=$1"]
        if trashed:
            where.append("trashed_at IS NOT NULL")
        else:
            where.append("trashed_at IS NULL")
        if before:
            params.append(before)
            where.append(f"created_at < ${len(params)}::timestamp")
        join_sql = ""
        if collection_id:
            params.append(collection_id)
            join_sql = (
                f" INNER JOIN live_collection_items ci "
                f"ON ci.session_id = live_sessions.id AND ci.collection_id=${len(params)}"
            )
        params.append(limit)
        sql = f"""
            SELECT live_sessions.* FROM live_sessions
            {join_sql}
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(live_sessions.started_at, live_sessions.created_at) DESC
            LIMIT ${len(params)}
        """
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [self._row_card(r) for r in rows]

    async def search_sessions(
        self, user_id: str, query: str, *, limit: int = 30
    ) -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return await self.list_sessions(user_id, limit=limit)
        limit = max(1, min(100, int(limit or 30)))
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.*
                FROM live_sessions s
                LEFT JOIN live_session_search f ON f.session_id = s.id
                WHERE s.user_id=$1 AND s.trashed_at IS NULL
                  AND (
                    s.title ILIKE $2
                    OR COALESCE(s.brief_markdown,'') ILIKE $2
                    OR COALESCE(s.user_notes,'') ILIKE $2
                    OR f.document @@ plainto_tsquery('simple', $3)
                  )
                ORDER BY COALESCE(s.started_at, s.created_at) DESC
                LIMIT $4
                """,
                _uid(user_id),
                f"%{q}%",
                q,
                limit,
            )
            return [self._row_card(r) for r in rows]

    async def update_session(
        self, user_id: str, session_id: str, data: LiveSessionUpdate
    ) -> Optional[Dict[str, Any]]:
        fields: List[str] = []
        params: List[Any] = []
        mapping = {
            "title": data.title,
            "language": data.language,
            "source_app": data.source_app,
            "keep_audio": data.keep_audio,
            "user_notes": data.user_notes,
        }
        for col, val in mapping.items():
            if val is not None:
                params.append(val)
                fields.append(f"{col}=${len(params)}")
        if data.participants is not None:
            params.append(json.dumps(data.participants, ensure_ascii=False))
            fields.append(f"participants=${len(params)}::jsonb")
        if not fields and data.collection_ids is None:
            return await self.get_session(user_id, session_id)
        async with await self.db.acquire() as conn:
            if fields:
                params.extend([session_id, _uid(user_id)])
                fields.append("updated_at=NOW()")
                await conn.execute(
                    f"""
                    UPDATE live_sessions SET {', '.join(fields)}
                    WHERE id=${len(params)-1} AND user_id=${len(params)}
                      AND trashed_at IS NULL
                    """,
                    *params,
                )
            if data.collection_ids is not None:
                await conn.execute(
                    "DELETE FROM live_collection_items WHERE session_id=$1", session_id
                )
                for cid in data.collection_ids:
                    await conn.execute(
                        """
                        INSERT INTO live_collection_items (collection_id, session_id)
                        SELECT $1, $2 FROM live_collections
                        WHERE id=$1 AND user_id=$3
                        ON CONFLICT DO NOTHING
                        """,
                        cid,
                        session_id,
                        _uid(user_id),
                    )
        await self.refresh_search_document(user_id, session_id)
        return await self.get_session(user_id, session_id)

    async def set_status(
        self,
        user_id: str,
        session_id: str,
        status: str,
        *,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        extra = extra or {}
        fields = ["status=$3", "updated_at=NOW()"]
        params: List[Any] = [_uid(user_id), session_id, status]
        for col in (
            "started_at",
            "ended_at",
            "duration_ms",
            "paused_at",
            "pause_accumulated_ms",
            "brief_status",
            "brief_markdown",
            "user_notes",
            "local_audio_key",
            "remote_audio_key",
            "mixed_audio_key",
            "title",
        ):
            if col in extra:
                params.append(extra[col])
                fields.append(f"{col}=${len(params)}")
        if "metadata" in extra:
            params.append(json.dumps(extra["metadata"], ensure_ascii=False))
            fields.append(f"metadata=${len(params)}::jsonb")
        async with await self.db.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE live_sessions SET {', '.join(fields)}
                WHERE id=$2 AND user_id=$1
                """,
                *params,
            )
        return await self.get_session(user_id, session_id, include_trashed=True)

    async def add_segment(
        self,
        user_id: str,
        session_id: str,
        *,
        track: str,
        start_ms: int,
        end_ms: int,
        text: str,
        speaker: Optional[str] = None,
        is_echo: bool = False,
        confidence: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        owned = await self.get_session(user_id, session_id)
        if not owned:
            return None
        seg_id = _new_id("seg")
        async with await self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO live_session_segments (
                    id, session_id, track, start_ms, end_ms, text, speaker, is_echo, confidence
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                seg_id,
                session_id,
                track,
                int(start_ms),
                int(end_ms),
                text or "",
                speaker,
                bool(is_echo),
                confidence,
            )
            await conn.execute(
                "UPDATE live_sessions SET updated_at=NOW() WHERE id=$1", session_id
            )
        return {
            "id": seg_id,
            "track": track,
            "start_ms": int(start_ms),
            "end_ms": int(end_ms),
            "text": text or "",
            "speaker": speaker,
            "is_echo": bool(is_echo),
            "confidence": confidence,
        }

    async def rename_speakers(
        self, user_id: str, session_id: str, mapping: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """Переименовать speaker-лейблы сегментов и сохранить карту в metadata."""
        clean = {
            str(k).strip(): str(v).strip()
            for k, v in (mapping or {}).items()
            if str(k).strip() and str(v).strip()
        }
        session = await self.get_session(user_id, session_id, include_trashed=True)
        if not session:
            return None
        if clean:
            async with await self.db.acquire() as conn:
                for old, new in clean.items():
                    await conn.execute(
                        """
                        UPDATE live_session_segments
                        SET speaker=$3
                        WHERE session_id=$1 AND speaker=$2
                        """,
                        session_id,
                        old,
                        new[:128],
                    )
                meta = dict(session.get("metadata") or {})
                prev = dict(meta.get("speaker_map") or {})
                prev.update(clean)
                meta["speaker_map"] = prev
                await conn.execute(
                    """
                    UPDATE live_sessions
                    SET metadata=$2::jsonb, updated_at=NOW()
                    WHERE id=$1 AND user_id=$3
                    """,
                    session_id,
                    json.dumps(meta, ensure_ascii=False),
                    _uid(user_id),
                )
            await self.refresh_search_document(user_id, session_id)
        return await self.get_session(user_id, session_id, include_trashed=True)

    async def replace_segments(
        self, user_id: str, session_id: str, segments: List[Dict[str, Any]]
    ) -> bool:
        owned = await self.get_session(user_id, session_id, include_trashed=True)
        if not owned:
            return False
        async with await self.db.acquire() as conn:
            await conn.execute(
                "DELETE FROM live_session_segments WHERE session_id=$1", session_id
            )
            for seg in segments:
                await conn.execute(
                    """
                    INSERT INTO live_session_segments (
                        id, session_id, track, start_ms, end_ms, text, speaker, is_echo, confidence
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    """,
                    seg.get("id") or _new_id("seg"),
                    session_id,
                    seg.get("track") or "mixed",
                    int(seg.get("start_ms") or 0),
                    int(seg.get("end_ms") or 0),
                    seg.get("text") or "",
                    seg.get("speaker"),
                    bool(seg.get("is_echo") or False),
                    seg.get("confidence"),
                )
        await self.refresh_search_document(user_id, session_id)
        return True

    async def soft_delete(self, user_id: str, session_id: str) -> bool:
        async with await self.db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE live_sessions
                SET trashed_at=NOW(), status='archived', updated_at=NOW()
                WHERE id=$1 AND user_id=$2 AND trashed_at IS NULL
                """,
                session_id,
                _uid(user_id),
            )
            return result.endswith("1")

    async def restore(self, user_id: str, session_id: str) -> bool:
        async with await self.db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE live_sessions
                SET trashed_at=NULL, status='ready', updated_at=NOW()
                WHERE id=$1 AND user_id=$2 AND trashed_at IS NOT NULL
                """,
                session_id,
                _uid(user_id),
            )
            return result.endswith("1")

    async def hard_delete(self, user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        session = await self.get_session(user_id, session_id, include_trashed=True)
        if not session:
            return None
        async with await self.db.acquire() as conn:
            await conn.execute(
                "DELETE FROM live_sessions WHERE id=$1 AND user_id=$2",
                session_id,
                _uid(user_id),
            )
        return session

    async def purge_trash(self, user_id: str) -> List[Dict[str, Any]]:
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM live_sessions
                WHERE user_id=$1 AND trashed_at IS NOT NULL
                  AND trashed_at < NOW() - ($2 || ' days')::interval
                """,
                _uid(user_id),
                str(_TRASH_DAYS),
            )
            deleted = [self._row_card(r) for r in rows]
            if deleted:
                await conn.execute(
                    """
                    DELETE FROM live_sessions
                    WHERE user_id=$1 AND trashed_at IS NOT NULL
                      AND trashed_at < NOW() - ($2 || ' days')::interval
                    """,
                    _uid(user_id),
                    str(_TRASH_DAYS),
                )
            return deleted

    async def refresh_search_document(self, user_id: str, session_id: str) -> None:
        async with await self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT title, user_notes, brief_markdown FROM live_sessions WHERE id=$1 AND user_id=$2",
                session_id,
                _uid(user_id),
            )
            if not row:
                return
            segs = await conn.fetch(
                "SELECT text, speaker FROM live_session_segments WHERE session_id=$1 AND NOT is_echo",
                session_id,
            )
            transcript = " ".join(
                f"{(s['speaker'] or '')} {s['text'] or ''}".strip() for s in segs
            )
            blob = " ".join(
                [
                    row["title"] or "",
                    row["user_notes"] or "",
                    row["brief_markdown"] or "",
                    transcript,
                ]
            )
            await conn.execute(
                """
                INSERT INTO live_session_search (session_id, user_id, document)
                VALUES ($1, $2, to_tsvector('simple', $3))
                ON CONFLICT (session_id) DO UPDATE
                SET user_id=EXCLUDED.user_id, document=EXCLUDED.document
                """,
                session_id,
                _uid(user_id),
                blob,
            )

    # --- collections ---

    async def create_collection(
        self, user_id: str, data: LiveCollectionCreate
    ) -> Dict[str, Any]:
        cid = _new_id("lcol")
        async with await self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO live_collections (id, user_id, name, color, icon, sort_order)
                VALUES ($1,$2,$3,$4,$5,
                    COALESCE((SELECT MAX(sort_order)+1 FROM live_collections WHERE user_id=$2), 0)
                )
                """,
                cid,
                _uid(user_id),
                data.name.strip(),
                data.color,
                data.icon,
            )
        return await self.get_collection(user_id, cid)  # type: ignore[return-value]

    async def get_collection(
        self, user_id: str, collection_id: str
    ) -> Optional[Dict[str, Any]]:
        async with await self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM live_collections WHERE id=$1 AND user_id=$2",
                collection_id,
                _uid(user_id),
            )
            if not row:
                return None
            return {
                "id": row["id"],
                "name": row["name"],
                "color": row["color"],
                "icon": row["icon"],
                "sort_order": int(row["sort_order"] or 0),
                "created_at": _iso(row["created_at"]),
                "updated_at": _iso(row["updated_at"]),
            }

    async def list_collections(self, user_id: str) -> List[Dict[str, Any]]:
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM live_collections
                WHERE user_id=$1
                ORDER BY sort_order ASC, created_at ASC
                """,
                _uid(user_id),
            )
            return [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "color": r["color"],
                    "icon": r["icon"],
                    "sort_order": int(r["sort_order"] or 0),
                    "created_at": _iso(r["created_at"]),
                    "updated_at": _iso(r["updated_at"]),
                }
                for r in rows
            ]

    async def update_collection(
        self, user_id: str, collection_id: str, data: LiveCollectionUpdate
    ) -> Optional[Dict[str, Any]]:
        fields: List[str] = []
        params: List[Any] = []
        for col, val in {
            "name": data.name,
            "color": data.color,
            "icon": data.icon,
            "sort_order": data.sort_order,
        }.items():
            if val is not None:
                params.append(val)
                fields.append(f"{col}=${len(params)}")
        if not fields:
            return await self.get_collection(user_id, collection_id)
        params.extend([collection_id, _uid(user_id)])
        fields.append("updated_at=NOW()")
        async with await self.db.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE live_collections SET {', '.join(fields)}
                WHERE id=${len(params)-1} AND user_id=${len(params)}
                """,
                *params,
            )
        return await self.get_collection(user_id, collection_id)

    async def delete_collection(self, user_id: str, collection_id: str) -> bool:
        async with await self.db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM live_collections WHERE id=$1 AND user_id=$2",
                collection_id,
                _uid(user_id),
            )
            return result.endswith("1")

    async def mark_segment_echo(self, session_id: str, segment_id: str, is_echo: bool = True) -> None:
        async with await self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE live_session_segments
                SET is_echo=$3
                WHERE id=$1 AND session_id=$2
                """,
                segment_id,
                session_id,
                bool(is_echo),
            )

    DEFAULT_PREFS = {
        "echo_detect": True,
        "auto_stop_on_capture_end": True,
        "auto_stop_on_remote_silence_sec": 0,
        "jargon": [],
        "show_echo_segments": False,
    }

    async def get_prefs(self, user_id: str) -> Dict[str, Any]:
        async with await self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT prefs FROM live_session_prefs WHERE user_id=$1",
                _uid(user_id),
            )
        prefs = dict(self.DEFAULT_PREFS)
        if row:
            raw = _parse_json_obj(row["prefs"])
            prefs.update({k: v for k, v in raw.items() if k in prefs or k == "jargon"})
        if not isinstance(prefs.get("jargon"), list):
            prefs["jargon"] = []
        return prefs

    async def set_prefs(self, user_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        current = await self.get_prefs(user_id)
        for key in (
            "echo_detect",
            "auto_stop_on_capture_end",
            "auto_stop_on_remote_silence_sec",
            "show_echo_segments",
        ):
            if key in patch and patch[key] is not None:
                current[key] = patch[key]
        if "jargon" in patch and patch["jargon"] is not None:
            if isinstance(patch["jargon"], str):
                current["jargon"] = [
                    x.strip() for x in patch["jargon"].replace(";", ",").split(",") if x.strip()
                ]
            elif isinstance(patch["jargon"], list):
                current["jargon"] = [str(x).strip() for x in patch["jargon"] if str(x).strip()]
        async with await self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO live_session_prefs (user_id, prefs, updated_at)
                VALUES ($1, $2::jsonb, NOW())
                ON CONFLICT (user_id) DO UPDATE
                SET prefs=EXCLUDED.prefs, updated_at=NOW()
                """,
                _uid(user_id),
                json.dumps(current, ensure_ascii=False),
            )
        return current
