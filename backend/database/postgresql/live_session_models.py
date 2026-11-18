"""Модели «Живых сессий» AstraStudio (live dual-track запись + стенограмма).

Оригинальный домен продукта: Session / local+remote / Brief / Collection.
Не использует терминологию и схему сторонних meeting-notes клиентов.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

SessionStatus = Literal[
    "draft",
    "live",
    "paused",
    "processing",
    "ready",
    "archived",
]
TrackKind = Literal["local", "remote", "mixed"]
BriefStatus = Literal[
    "idle",
    "waiting",
    "writing",
    "ready",
    "failed",
    "empty",
]


class LiveSessionCreate(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    language: str = Field("auto", max_length=16)
    source_app: Optional[str] = Field(None, max_length=64)
    participants: List[str] = Field(default_factory=list)


class LiveSessionUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    participants: Optional[List[str]] = None
    language: Optional[str] = Field(None, max_length=16)
    source_app: Optional[str] = Field(None, max_length=64)
    keep_audio: Optional[bool] = None
    user_notes: Optional[str] = None
    collection_ids: Optional[List[str]] = None


class LiveSessionSegmentIn(BaseModel):
    track: TrackKind
    start_ms: int = Field(..., ge=0)
    end_ms: int = Field(..., ge=0)
    text: str = Field(..., min_length=0)
    speaker: Optional[str] = Field(None, max_length=128)
    is_echo: bool = False
    confidence: Optional[float] = None


class LiveSessionBriefUpdate(BaseModel):
    user_notes: Optional[str] = None
    brief_markdown: Optional[str] = None
    regenerate: bool = False
    instruction: Optional[str] = Field(None, max_length=2000)


class LiveSessionSpeakerRename(BaseModel):
    """Карта старых speaker-id → отображаемые имена."""
    mapping: Dict[str, str] = Field(default_factory=dict)


class LiveCollectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    color: Optional[str] = Field(None, max_length=32)
    icon: Optional[str] = Field(None, max_length=64)


class LiveCollectionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    color: Optional[str] = Field(None, max_length=32)
    icon: Optional[str] = Field(None, max_length=64)
    sort_order: Optional[int] = None


class LiveSessionCard(BaseModel):
    id: str
    title: str
    status: SessionStatus
    language: str
    source_app: Optional[str] = None
    participants: List[str] = Field(default_factory=list)
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_ms: int = 0
    brief_status: BriefStatus = "idle"
    preview: str = ""
    trashed_at: Optional[str] = None
    created_at: str
    updated_at: str


class LiveSessionDetail(LiveSessionCard):
    user_notes: str = ""
    brief_markdown: str = ""
    local_audio_key: Optional[str] = None
    remote_audio_key: Optional[str] = None
    mixed_audio_key: Optional[str] = None
    keep_audio: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)
    segments: List[Dict[str, Any]] = Field(default_factory=list)
    collection_ids: List[str] = Field(default_factory=list)
