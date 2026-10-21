"""API поиска пользователей (LDAP directory) для шаринга агентов/скиллов."""

from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.auth.jwt_handler import get_current_user
from backend.auth.user_directory import search_directory_users

router = APIRouter(prefix="/api/users", tags=["users"])


class UserSearchHit(BaseModel):
    user_id: str = Field(..., description="Логин / gpbu")
    username: str = Field(..., description="Логин / gpbu")
    full_name: Optional[str] = Field(None, description="ФИО")
    email: Optional[str] = Field(None, description="Корпоративная почта")


class UserSearchResponse(BaseModel):
    users: List[UserSearchHit] = Field(default_factory=list)


@router.get("/search", response_model=UserSearchResponse)
async def search_users(
    current_user: Annotated[dict, Depends(get_current_user)],
    q: Annotated[
        str, Query(min_length=1, max_length=200, description="gpbu, ФИО или email")
    ],
    limit: Annotated[int, Query(ge=1, le=20, description="Максимум результатов")] = 10,
    for_share: Annotated[
        bool,
        Query(
            description="Только пользователи с платформенной ролью (viewer+) для шаринга"
        ),
    ] = False,
):
    """Поиск сотрудников в LDAP по gpbu, ФИО или корпоративной почте."""
    _ = current_user
    hits = await search_directory_users(q, limit=limit, for_share=for_share)
    return UserSearchResponse(users=[UserSearchHit(**h) for h in hits])
