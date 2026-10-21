"""Pydantic-модели Skills (LibreChat-like Agent Skills)."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


SKILL_PERMISSION_VIEWER = "viewer"
SKILL_PERMISSION_EDITOR = "editor"
SKILL_PERMISSION_OWNER = "owner"
SKILL_SHARE_PERMISSIONS = (SKILL_PERMISSION_VIEWER, SKILL_PERMISSION_EDITOR)

SKILL_FILE_CATEGORIES = ("script", "reference", "asset", "other")


def normalize_skill_permission(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if v in ("use", "", "read", "reader", "viewer"):
        return SKILL_PERMISSION_VIEWER
    if v in ("edit", "editor", "write", "writer"):
        return SKILL_PERMISSION_EDITOR
    return SKILL_PERMISSION_VIEWER


def slugify_skill_id(raw: str) -> str:
    """Machine name / $mention key: kebab-case."""
    import re

    s = (raw or "").strip().lower().replace(" ", "-")
    s = re.sub(r"[^a-z0-9._-]+", "-", s).strip("-")
    return s


def infer_skill_file_category(relative_path: str) -> str:
    p = (relative_path or "").replace("\\", "/").lstrip("/").lower()
    if p.startswith("scripts/"):
        return "script"
    if p.startswith("references/"):
        return "reference"
    if p.startswith("assets/"):
        return "asset"
    return "other"


class Skill(BaseModel):
    """Skill entity (LibreChat-like)."""

    id: Optional[int] = None
    slug: str = Field(..., min_length=1, max_length=100, description="Machine name (kebab-case)")
    name: str = Field(..., min_length=1, max_length=255, description="Display title")
    display_title: Optional[str] = Field(None, max_length=128, description="UI label; fallback=name")
    description: Optional[str] = Field(None, description="When to use (model trigger)")
    content: str = Field(..., description="Markdown body (= SKILL.md body)")
    meta: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    is_public: bool = False
    user_invocable: bool = Field(True, description="Visible in $ popover")
    disable_model_invocation: bool = Field(False, description="Hide from model catalog")
    always_apply: bool = Field(False, description="Prime every turn when accessible")
    allowed_tools: List[str] = Field(default_factory=list, description="Tools/MCP ids to union on prime")
    category: Optional[str] = None
    version: int = 1
    file_count: int = 0
    author_id: str
    author_name: str
    author_full_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SkillListItem(BaseModel):
    """List item without full content."""

    id: int
    slug: str
    name: str
    display_title: Optional[str] = None
    description: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    is_public: bool = False
    user_invocable: bool = True
    disable_model_invocation: bool = False
    always_apply: bool = False
    allowed_tools: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    version: int = 1
    file_count: int = 0
    author_id: str
    author_name: str
    author_full_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    views_count: int = 0
    usage_count: int = 0
    average_rating: float = 0.0
    total_votes: int = 0
    user_rating: Optional[int] = None
    is_bookmarked: bool = False
    is_shared_with_me: bool = False
    my_permission: Optional[str] = None
    write_access: bool = False


class SkillOut(Skill):
    views_count: int = 0
    usage_count: int = 0
    average_rating: float = 0.0
    total_votes: int = 0
    user_rating: Optional[int] = None
    is_bookmarked: bool = False
    is_shared_with_me: bool = False
    my_permission: Optional[str] = None
    write_access: bool = False


class SkillRatingRequest(BaseModel):
    """Запрос на оценку skill (1–5)."""

    rating: int = Field(..., ge=1, le=5)


class SkillCreate(BaseModel):
    slug: Optional[str] = Field(None, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    display_title: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = None
    content: str = Field(..., min_length=1)
    meta: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    is_public: bool = False
    user_invocable: bool = True
    disable_model_invocation: bool = False
    always_apply: bool = False
    allowed_tools: List[str] = Field(default_factory=list)
    category: Optional[str] = None


class SkillUpdate(BaseModel):
    slug: Optional[str] = Field(None, max_length=100)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    display_title: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = None
    content: Optional[str] = Field(None, min_length=1)
    meta: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    user_invocable: Optional[bool] = None
    disable_model_invocation: Optional[bool] = None
    always_apply: Optional[bool] = None
    allowed_tools: Optional[List[str]] = None
    category: Optional[str] = None


class SkillFilters(BaseModel):
    search_query: Optional[str] = None
    view_option: Optional[str] = Field(None, description="created | shared | public | ''")
    author_id: Optional[str] = None
    category: Optional[str] = None
    limit: int = Field(30, ge=1, le=1000)
    offset: int = Field(0, ge=0)


class SkillShare(BaseModel):
    id: Optional[int] = None
    skill_id: int
    owner_id: str
    shared_with_user_id: str
    permission: str = "viewer"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SkillShareRequest(BaseModel):
    usernames: List[str] = Field(..., min_length=1)
    permission: str = Field("viewer")


class SkillShareEntry(BaseModel):
    user_id: str
    full_name: Optional[str] = None
    permission: str


class SkillSharesResponse(BaseModel):
    owner: SkillShareEntry
    shares: List[SkillShareEntry] = Field(default_factory=list)


class SkillFileOut(BaseModel):
    id: int
    skill_id: int
    relative_path: str
    category: str = "other"
    mime_type: Optional[str] = None
    bytes: int = 0
    minio_bucket: Optional[str] = None
    minio_object: Optional[str] = None
    is_executable: bool = False
    created_at: datetime
    updated_at: datetime


class SkillFileCreate(BaseModel):
    relative_path: str = Field(..., min_length=1, max_length=512)
    content: Optional[str] = Field(None, description="Text content; mutually exclusive with binary upload")
    mime_type: Optional[str] = "text/plain"
    is_executable: bool = False
