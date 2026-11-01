from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Agent(BaseModel):
    """Модель агента"""

    id: Optional[int] = Field(None, description="ID агента (автоинкремент)")
    name: str = Field(..., description="Название агента", min_length=3, max_length=255)
    description: Optional[str] = Field(None, description="Описание агента", max_length=1000)
    system_prompt: str = Field(..., description="Системный промпт агента", min_length=10)
    config: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Настройки агента (JSON)")
    tools: Optional[List[str]] = Field(default_factory=list, description="Список инструментов агента")
    author_id: str = Field(..., description="ID пользователя-автора")
    author_name: str = Field(..., description="Имя автора")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Дата создания")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Дата обновления")
    is_public: bool = Field(True, description="Публичный/приватный")
    usage_count: int = Field(0, description="Количество использований")
    views_count: int = Field(0, description="Количество просмотров")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Аналитик данных",
                "description": "Агент для анализа данных и создания отчетов",
                "system_prompt": "Ты - эксперт по анализу данных. Помогай пользователям анализировать данные...",
                "config": {"temperature": 0.7, "max_tokens": 2000},
                "tools": ["search_documents"],
                "author_id": "user123",
                "author_name": "Иван Иванов",
                "is_public": True,
            }
        }


class AgentWithTags(Agent):
    """Модель агента с тегами и рейтингом"""

    tags: List[Dict] = Field(default_factory=list, description="Теги агента")
    average_rating: float = Field(0.0, description="Средний рейтинг (1-5)")
    total_votes: int = Field(0, description="Общее количество голосов")
    user_rating: Optional[int] = Field(None, description="Оценка текущего пользователя")
    is_bookmarked: bool = Field(False, description="Добавлен ли в закладки текущим пользователем")
    is_shared_with_me: bool = Field(False, description="Агент расшарен текущему пользователю")
    my_permission: Optional[str] = Field(
        None, description="Роль текущего пользователя: owner | editor | viewer | None"
    )


class Tag(BaseModel):
    """Тег для галереи агентов (общая таблица tags)"""

    id: Optional[int] = Field(None, description="ID тега")
    name: str = Field(..., description="Название тега", min_length=2, max_length=100)
    description: Optional[str] = Field(None, description="Описание тега", max_length=500)
    color: Optional[str] = Field(None, description="Цвет для UI (hex)", pattern="^#[0-9A-Fa-f]{6}$")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Дата создания")


# Роли доступа к агенту
AGENT_PERMISSION_VIEWER = "viewer"
AGENT_PERMISSION_EDITOR = "editor"
AGENT_PERMISSION_OWNER = "owner"
AGENT_SHARE_PERMISSIONS = (AGENT_PERMISSION_VIEWER, AGENT_PERMISSION_EDITOR)


def normalize_permission(value: Optional[str]) -> str:
    """Нормализация роли шаринга. Старое значение 'use' → 'viewer'."""
    v = (value or "").strip().lower()
    if v in ("use", "", "read", "reader"):
        return AGENT_PERMISSION_VIEWER
    if v in ("edit", "editor", "write", "writer"):
        return AGENT_PERMISSION_EDITOR
    if v == AGENT_PERMISSION_EDITOR:
        return AGENT_PERMISSION_EDITOR
    return AGENT_PERMISSION_VIEWER


class AgentShare(BaseModel):
    """Запись о шаринге агента с пользователем"""

    id: Optional[int] = Field(None, description="ID записи")
    agent_id: int = Field(..., description="ID агента")
    owner_id: str = Field(..., description="ID владельца (автора)")
    shared_with_user_id: str = Field(..., description="ID получателя")
    permission: str = Field("viewer", description="Роль: viewer | editor")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentShareRequest(BaseModel):
    """Запрос на шаринг агента"""

    usernames: List[str] = Field(..., min_length=1, description="Логины / user_id получателей")
    permission: str = Field("viewer", description="Роль: viewer | editor")


class AgentShareEntry(BaseModel):
    """Участник доступа к агенту (владелец или получатель) с ФИО."""

    user_id: str = Field(..., description="gpbu / логин пользователя")
    full_name: Optional[str] = Field(None, description="ФИО (подтягивается по gpbu из LDAP)")
    permission: str = Field(..., description="Роль: owner | editor | viewer")


class AgentSharesResponse(BaseModel):
    """Список доступа к агенту: владелец + получатели."""

    owner: AgentShareEntry = Field(..., description="Владелец (создатель агента)")
    shares: List[AgentShareEntry] = Field(default_factory=list, description="Получатели доступа")


class AgentRating(BaseModel):
    """Модель рейтинга агента"""

    id: Optional[int] = Field(None, description="ID рейтинга (автоинкремент)")
    agent_id: int = Field(..., description="ID агента")
    user_id: str = Field(..., description="ID пользователя")
    rating: int = Field(..., description="Оценка (1-5)", ge=1, le=5)
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Дата создания")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Дата обновления")

    class Config:
        json_schema_extra = {"example": {"agent_id": 1, "user_id": "user123", "rating": 5}}


class AgentCreate(BaseModel):
    """Модель для создания агента"""

    name: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    system_prompt: str = Field(..., min_length=10)
    config: Optional[Dict[str, Any]] = Field(default_factory=dict)
    tools: Optional[List[str]] = Field(default_factory=list)
    is_public: bool = Field(True)
    tag_ids: List[int] = Field(default_factory=list, description="ID существующих тегов")
    new_tags: List[str] = Field(default_factory=list, description="Новые теги для создания")


class AgentUpdate(BaseModel):
    """Модель для обновления агента"""

    name: Optional[str] = Field(None, min_length=3, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    system_prompt: Optional[str] = Field(None, min_length=10)
    config: Optional[Dict[str, Any]] = Field(None)
    tools: Optional[List[str]] = Field(None)
    is_public: Optional[bool] = Field(None)
    tag_ids: Optional[List[int]] = Field(None, description="ID существующих тегов")
    new_tags: Optional[List[str]] = Field(None, description="Новые теги для создания")


class AgentFilters(BaseModel):
    """Фильтры для поиска агентов"""

    search_query: Optional[str] = Field(None, description="Поисковый запрос")
    tag_ids: Optional[List[int]] = Field(None, description="Фильтр по тегам")
    author_id: Optional[str] = Field(None, description="Фильтр по автору")
    author_only: bool = Field(False, description="Только агенты автора (игнорировать is_public)")
    min_rating: Optional[float] = Field(None, description="Минимальный рейтинг", ge=0, le=5)
    sort_by: str = Field("rating", description="Поле сортировки")
    sort_order: str = Field("desc", description="Порядок сортировки (asc/desc)")
    # le=1000: совпадает с GET /api/agents/admin/all (Query le=1000, default 200).
    # Раньше le=100 ломало admin-каталог в конструкторе (ValidationError → пустой список).
    limit: int = Field(20, description="Количество результатов", ge=1, le=1000)
    offset: int = Field(0, description="Смещение для пагинации", ge=0)


class AgentStats(BaseModel):
    """Статистика агента"""

    agent_id: int
    views_count: int
    usage_count: int
    average_rating: float
    total_votes: int
    rating_distribution: Dict[int, int] = Field(
        default_factory=dict, description="Распределение оценок {1: count, 2: count, ...}"
    )
