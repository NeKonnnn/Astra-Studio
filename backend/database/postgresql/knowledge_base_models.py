"""Pydantic-модели пользовательских Баз знаний (Knowledge Bases)."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

KB_PERMISSION_VIEWER = "viewer"
KB_PERMISSION_EDITOR = "editor"
KB_PERMISSION_OWNER = "owner"
KB_SHARE_PERMISSIONS = (KB_PERMISSION_VIEWER, KB_PERMISSION_EDITOR)


def normalize_kb_permission(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if v in ("use", "", "read", "reader", "viewer"):
        return KB_PERMISSION_VIEWER
    if v in ("edit", "editor", "write", "writer"):
        return KB_PERMISSION_EDITOR
    return KB_PERMISSION_VIEWER


class KnowledgeBase(BaseModel):
    """База знаний как первоклассная сущность."""

    id: Optional[int] = None
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    is_public: bool = False
    document_count: int = 0
    views_count: int = 0
    usage_count: int = 0
    author_id: str
    author_name: str
    author_full_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class KnowledgeBaseListItem(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    is_public: bool = False
    document_count: int = 0
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


class KnowledgeBaseOut(KnowledgeBase):
    views_count: int = 0
    usage_count: int = 0
    average_rating: float = 0.0
    total_votes: int = 0
    user_rating: Optional[int] = None
    is_bookmarked: bool = False
    is_shared_with_me: bool = False
    my_permission: Optional[str] = None
    write_access: bool = False


class KnowledgeBaseRatingRequest(BaseModel):
    """Запрос на оценку базы знаний (1–5)."""

    rating: int = Field(..., ge=1, le=5)


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    is_public: bool = False


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
    is_public: Optional[bool] = None


class KnowledgeBaseFilters(BaseModel):
    search_query: Optional[str] = None
    view_option: Optional[str] = Field(
        None, description="created | shared | public | ''"
    )
    author_id: Optional[str] = None
    limit: int = Field(30, ge=1, le=1000)
    offset: int = Field(0, ge=0)


class KnowledgeBaseShare(BaseModel):
    id: Optional[int] = None
    knowledge_base_id: int
    owner_id: str
    shared_with_user_id: str
    permission: str = "viewer"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class KnowledgeBaseShareRequest(BaseModel):
    usernames: List[str] = Field(..., min_length=1)
    permission: str = Field("viewer")


class KnowledgeBaseShareEntry(BaseModel):
    user_id: str
    full_name: Optional[str] = None
    permission: str


class KnowledgeBaseSharesResponse(BaseModel):
    owner: KnowledgeBaseShareEntry
    shares: List[KnowledgeBaseShareEntry] = Field(default_factory=list)
