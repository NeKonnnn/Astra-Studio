"""API endpoints для Skills (аналог Open WebUI /api/v1/skills)."""

from typing import Annotated, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from backend.utils.http_errors import public_error_detail
from pydantic import BaseModel

from backend.auth.agent_platform_roles import (
    cap_agent_permission,
    get_agent_platform_role,
    is_agent_platform_role_mapping_enabled,
    user_can_create_agents,
    user_can_publish_to_library,
)
from backend.auth.jwt_handler import get_current_user
from backend.auth.user_directory import (
    enrich_items_author_full_names,
    resolve_full_names,
)
from backend.auth.share_recipients import (
    build_share_api_response,
    prepare_share_recipients,
)
from backend.database.init_db import get_skill_repository
from backend.database.postgresql.skill_models import (
    SkillCreate,
    SkillFileCreate,
    SkillFileOut,
    SkillFilters,
    SkillListItem,
    SkillOut,
    SkillRatingRequest,
    SkillShareEntry,
    SkillShareRequest,
    SkillSharesResponse,
    SkillUpdate,
)
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _is_admin(user: dict) -> bool:
    return bool(user.get("is_admin"))


def _norm_uid(user_id: Optional[str]) -> str:
    return str(user_id or "").strip().lower()


def _is_skill_author(skill, user: Optional[dict]) -> bool:
    """Автор skill — полный контроль над своей карточкой (как у агентов)."""
    if not skill or not user:
        return False
    if _is_admin(user):
        return True
    return _norm_uid(getattr(skill, "author_id", None)) == _norm_uid(
        user.get("user_id")
    )


def _platform_role(user: dict) -> Optional[str]:
    if is_agent_platform_role_mapping_enabled():
        return get_agent_platform_role(user)
    return None


def _require_platform_roles(user: dict, *allowed: str, detail: str) -> None:
    if _is_admin(user):
        return
    if not is_agent_platform_role_mapping_enabled():
        return
    role = get_agent_platform_role(user)
    if role not in allowed:
        raise HTTPException(status_code=403, detail=detail)


def _sync_skill_write_access(item: Union[SkillListItem, SkillOut]) -> None:
    perm = getattr(item, "my_permission", None)
    item.write_access = perm in ("owner", "editor")


def _apply_platform_role_to_skill(
    item: Union[SkillListItem, SkillOut],
    platform_role: Optional[str],
    *,
    user: Optional[dict] = None,
    is_admin: bool = False,
) -> None:
    """Сжать my_permission platform-ролью, кроме автора своей карточки.

    Явный шаринг (is_shared_with_me) не капим — ACL из skill_shares
    должен сохраняться (editor не сжимать до viewer по LDAP/KC роли).
    """
    if is_admin or not is_agent_platform_role_mapping_enabled():
        _sync_skill_write_access(item)
        return
    if user is not None and _is_skill_author(item, user):
        _sync_skill_write_access(item)
        return
    if getattr(item, "is_shared_with_me", False):
        _sync_skill_write_access(item)
        return
    item.my_permission = cap_agent_permission(item.my_permission, platform_role)
    _sync_skill_write_access(item)


def _apply_platform_role_to_skills(
    items: List[Union[SkillListItem, SkillOut]], user: dict
) -> None:
    admin = _is_admin(user)
    if admin:
        for item in items:
            _sync_skill_write_access(item)
        return
    if not is_agent_platform_role_mapping_enabled():
        return
    platform_role = get_agent_platform_role(user)
    for item in items:
        _apply_platform_role_to_skill(item, platform_role, user=user)


class SkillsListResponse(BaseModel):
    items: List[SkillListItem] = []
    total: int = 0
    page: int = 1
    pages: int = 0


@router.get("/", response_model=List[SkillListItem])
async def get_skills(current_user: Annotated[dict, Depends(get_current_user)]):
    """Все доступные skills (без content)."""
    try:
        repo = get_skill_repository()
        admin = _is_admin(current_user)
        # Админ: все skills страницами; иначе — доступные (лимит на страницу).
        page_size = 200
        items: List[SkillListItem] = []
        offset = 0
        while True:
            chunk, total = await repo.list_skills(
                current_user["user_id"],
                SkillFilters(limit=page_size, offset=offset),
                is_admin=admin,
            )
            items.extend(chunk)
            offset += page_size
            if not chunk or offset >= total or not admin:
                break
        _apply_platform_role_to_skills(items, current_user)
        await enrich_items_author_full_names(items)
        return items
    except Exception as e:
        logger.exception("Ошибка get_skills")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/list", response_model=SkillsListResponse)
async def get_skill_list(
    current_user: Annotated[dict, Depends(get_current_user)],
    query: Annotated[Optional[str], Query()] = None,
    view_option: Annotated[Optional[str], Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=1000)] = 30,
):
    """Пагинированный список skills.

    Для admin без view_option — все skills платформы (включая приватные).
    """
    try:
        repo = get_skill_repository()
        filters = SkillFilters(
            search_query=query,
            view_option=view_option,
            limit=limit,
            offset=(page - 1) * limit,
        )
        items, total = await repo.list_skills(
            current_user["user_id"], filters, is_admin=_is_admin(current_user)
        )
        _apply_platform_role_to_skills(items, current_user)
        await enrich_items_author_full_names(items)
        pages = (total + limit - 1) // limit if total else 0
        return SkillsListResponse(items=items, total=total, page=page, pages=pages)
    except Exception as e:
        logger.exception("Ошибка get_skill_list")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/export", response_model=List[SkillOut])
async def export_skills(current_user: Annotated[dict, Depends(get_current_user)]):
    """Export доступных skills с content."""
    try:
        repo = get_skill_repository()
        skills = await repo.get_accessible_skills(
            current_user["user_id"], is_admin=_is_admin(current_user)
        )
        _apply_platform_role_to_skills(skills, current_user)
        return skills
    except Exception as e:
        logger.exception("Ошибка export_skills")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/create", response_model=SkillOut, status_code=201)
@router.post("/", response_model=SkillOut, status_code=201)
async def create_skill(
    skill_data: SkillCreate, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Создание skill."""
    try:
        if not user_can_create_agents(current_user):
            raise HTTPException(
                status_code=403, detail="Недостаточно прав для создания skill"
            )
        # Редактор создаёт только приватные skills; публикация — у owner.
        if skill_data.is_public and not user_can_publish_to_library(current_user):
            skill_data.is_public = False
        repo = get_skill_repository()
        skill = await repo.create_skill(
            skill_data,
            author_id=current_user["user_id"],
            author_name=current_user.get("username")
            or current_user.get("full_name")
            or "Anonymous",
        )
        if not skill:
            raise HTTPException(
                status_code=400, detail="Не удалось создать skill (slug/name заняты?)"
            )
        return skill
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка create_skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/my/bookmarks", response_model=SkillsListResponse)
async def get_my_bookmarks(
    current_user: Annotated[dict, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Закладки текущего пользователя (пагинация)."""
    try:
        repo = get_skill_repository()
        bookmark_ids, total = await repo.get_user_bookmarks(
            current_user["user_id"], limit=limit, offset=(page - 1) * limit
        )
        if not bookmark_ids:
            return SkillsListResponse(items=[], total=0, page=page, pages=0)
        items: List[SkillListItem] = []
        admin = _is_admin(current_user)
        for sid in bookmark_ids:
            skill = await repo.get_skill_by_id(
                sid, current_user["user_id"], is_admin=admin
            )
            # Галерея: публичные; свои карточки оставляем (в т.ч. ещё не опубликованные).
            if skill and (
                getattr(skill, "is_public", False)
                or _is_skill_author(skill, current_user)
            ):
                items.append(SkillListItem(**skill.model_dump(exclude={"content"})))
        _apply_platform_role_to_skills(items, current_user)
        await enrich_items_author_full_names(items)
        pages = (total + limit - 1) // limit if total else 0
        return SkillsListResponse(items=items, total=total, page=page, pages=pages)
    except Exception as e:
        logger.exception("Ошибка get_my_bookmarks skills")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/id/{skill_id}", response_model=SkillOut)
@router.get("/{skill_id}", response_model=SkillOut)
async def get_skill(
    skill_id: str, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Получение skill по id или slug."""
    try:
        repo = get_skill_repository()
        admin = _is_admin(current_user)
        skill = None
        if skill_id.isdigit():
            skill = await repo.get_skill_by_id(
                int(skill_id), current_user["user_id"], is_admin=admin
            )
        if not skill:
            skill = await repo.get_skill_by_slug(
                skill_id, current_user["user_id"], is_admin=admin
            )
        if not skill:
            raise HTTPException(status_code=404, detail="Skill не найден")
        _apply_platform_role_to_skill(
            skill,
            _platform_role(current_user),
            user=current_user,
            is_admin=_is_admin(current_user),
        )
        await repo.increment_views(skill.id)
        return skill
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка get_skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{skill_id}/rate", response_model=dict)
async def rate_skill(
    skill_id: int,
    rating_request: SkillRatingRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Оценка skill пользователем."""
    try:
        repo = get_skill_repository()
        if not await repo.can_read(
            skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
        ):
            raise HTTPException(status_code=404, detail="Skill не найден")
        success = await repo.rate_skill(
            skill_id=skill_id,
            user_id=current_user["user_id"],
            rating=rating_request.rating,
        )
        if success:
            return {"success": True, "message": "Оценка сохранена"}
        raise HTTPException(status_code=404, detail="Skill не найден")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка rate_skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{skill_id}/view", response_model=dict)
async def view_skill(
    skill_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Увеличить счётчик просмотров skill."""
    try:
        repo = get_skill_repository()
        if not await repo.can_read(
            skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
        ):
            raise HTTPException(status_code=404, detail="Skill не найден")
        success = await repo.increment_views(skill_id)
        if success:
            return {"success": True, "message": "Просмотр учтён"}
        raise HTTPException(status_code=404, detail="Skill не найден")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка view_skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{skill_id}/use", response_model=dict)
async def use_skill(
    skill_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Увеличить счётчик использований skill."""
    try:
        repo = get_skill_repository()
        if not await repo.can_read(
            skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
        ):
            raise HTTPException(status_code=404, detail="Skill не найден")
        success = await repo.increment_usage(skill_id)
        if success:
            return {"success": True, "message": "Использование учтено"}
        raise HTTPException(status_code=404, detail="Skill не найден")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка use_skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{skill_id}/bookmark", response_model=dict)
async def add_bookmark(
    skill_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Добавить skill в закладки."""
    try:
        repo = get_skill_repository()
        if not await repo.can_read(
            skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
        ):
            raise HTTPException(status_code=404, detail="Skill не найден")
        success = await repo.add_bookmark(skill_id, current_user["user_id"])
        if success:
            return {"success": True, "message": "Добавлено в закладки"}
        raise HTTPException(status_code=404, detail="Skill не найден")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка add_bookmark skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.delete("/{skill_id}/bookmark", response_model=dict)
async def remove_bookmark(
    skill_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Удалить skill из закладок."""
    try:
        repo = get_skill_repository()
        success = await repo.remove_bookmark(skill_id, current_user["user_id"])
        if success:
            return {"success": True, "message": "Удалено из закладок"}
        raise HTTPException(status_code=404, detail="Skill не найден")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка remove_bookmark skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/id/{skill_id}/update", response_model=SkillOut)
@router.put("/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: int,
    skill_data: SkillUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Обновление skill."""
    try:
        repo = get_skill_repository()
        existing = await repo.get_skill_by_id(
            skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Skill не найден")
        # Автор своей карточки — полный контроль; чужие — по platform-роли.
        if not _is_skill_author(existing, current_user):
            _require_platform_roles(
                current_user,
                "owner",
                "editor",
                detail="Недостаточно прав для редактирования skill",
            )
        # Публикация/снятие с галереи — только platform owner (не editor).
        if (
            skill_data.is_public is not None
            and bool(skill_data.is_public) != bool(getattr(existing, "is_public", False))
            and not user_can_publish_to_library(current_user)
        ):
            raise HTTPException(
                status_code=403,
                detail="Недостаточно прав для публикации skill в галерее",
            )
        skill = await repo.update_skill(
            skill_id,
            skill_data,
            current_user["user_id"],
            is_admin=_is_admin(current_user),
        )
        if not skill:
            raise HTTPException(status_code=403, detail="Нет прав или skill не найден")
        return skill
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка update_skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/id/{skill_id}/toggle", response_model=SkillOut)
@router.post("/{skill_id}/toggle", response_model=SkillOut)
async def toggle_skill(
    skill_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Переключить is_active."""
    try:
        _require_platform_roles(
            current_user,
            "owner",
            "editor",
            detail="Недостаточно прав для изменения skill",
        )
        repo = get_skill_repository()
        skill = await repo.toggle_skill(
            skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not skill:
            raise HTTPException(status_code=403, detail="Нет прав или skill не найден")
        return skill
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка toggle_skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.delete("/id/{skill_id}/delete", response_model=dict)
@router.delete("/{skill_id}", response_model=dict)
async def delete_skill(
    skill_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Удаление skill (автор своей карточки или platform owner/admin)."""
    try:
        repo = get_skill_repository()
        existing = await repo.get_skill_by_id(
            skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Skill не найден")
        if not _is_skill_author(existing, current_user):
            _require_platform_roles(
                current_user,
                "owner",
                detail="Недостаточно прав для удаления skill",
            )
        ok = await repo.delete_skill(
            skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not ok:
            raise HTTPException(status_code=403, detail="Нет прав или skill не найден")
        return {"success": True, "message": "Skill удалён"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка delete_skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{skill_id}/share", response_model=dict)
async def share_skill(
    skill_id: int,
    payload: SkillShareRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Поделиться skill (автор своей карточки; чужие — только platform owner)."""
    try:
        repo = get_skill_repository()
        existing = await repo.get_skill_by_id(
            skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Skill не найден")
        if not _is_skill_author(existing, current_user):
            # Без mapping только автор (и admin через _is_skill_author) может шарить;
            # иначе _require_platform_roles — no-op и чужой share пройдёт с owner_id автора.
            if not is_agent_platform_role_mapping_enabled():
                raise HTTPException(
                    status_code=403, detail="Недостаточно прав для шаринга skill"
                )
            _require_platform_roles(
                current_user,
                "owner",
                detail="Недостаточно прав для шаринга skill",
            )

        prepared = await prepare_share_recipients(payload.usernames, payload.permission)
        usernames_to_share = [uid for uid, _ in prepared.to_share]
        recipient_permissions = {uid: perm for uid, perm in prepared.to_share}

        # owner_id в skill_shares — автор карточки; админ/platform-owner
        # шарит от имени автора, иначе репозиторий отклоняет чужой ресурс.
        share_owner_id = existing.author_id or current_user["user_id"]
        added, skipped = await repo.share_skill(
            skill_id=skill_id,
            owner_id=share_owner_id,
            usernames=usernames_to_share,
            permission=payload.permission,
            recipient_permissions=recipient_permissions,
        )
        response = build_share_api_response(
            prepared,
            added,
            skipped,
            resource_label="Skill",
        )
        if not added and (prepared.skipped or skipped):
            raise HTTPException(
                status_code=403,
                detail=response.get("message") or "Не удалось поделиться skill",
            )
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка share_skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/{skill_id}/shares", response_model=SkillSharesResponse)
async def list_skill_shares(
    skill_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Список доступа к skill."""
    try:
        repo = get_skill_repository()
        existing = await repo.get_skill_by_id(
            skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Skill не найден")
        if not _is_skill_author(existing, current_user):
            raise HTTPException(
                status_code=403, detail="Только автор может просматривать шаринги"
            )
        author = (existing.author_id or "").strip().lower()
        me = (current_user["user_id"] or "").strip().lower()
        raw_shares = await repo.list_skill_shares(skill_id, existing.author_id)
        owner_id = existing.author_id or current_user["user_id"]
        name_map = await resolve_full_names(
            [owner_id, *[s.shared_with_user_id for s in raw_shares]]
        )
        owner_name = name_map.get(owner_id)
        if not owner_name and author == me:
            owner_name = current_user.get("full_name") or current_user.get("name")
        return SkillSharesResponse(
            owner=SkillShareEntry(
                user_id=owner_id, full_name=owner_name, permission="owner"
            ),
            shares=[
                SkillShareEntry(
                    user_id=s.shared_with_user_id,
                    full_name=name_map.get(s.shared_with_user_id),
                    permission=(
                        "editor"
                        if (s.permission or "").lower() == "editor"
                        else "viewer"
                    ),
                )
                for s in raw_shares
            ],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка list_skill_shares")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.delete("/{skill_id}/share/{username}", response_model=dict)
async def unshare_skill(
    skill_id: int,
    username: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Снять доступ: автор/админ/platform-owner отзывает или получатель выходит."""
    try:
        repo = get_skill_repository()
        existing = await repo.get_skill_by_id(
            skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Skill не найден")

        me = _norm_uid(current_user.get("user_id"))
        target = _norm_uid(username)
        # actor_id для репозитория — author_id карточки (админ не автор в БД).
        if me != target:
            if not _is_skill_author(existing, current_user):
                _require_platform_roles(
                    current_user,
                    "owner",
                    detail="Недостаточно прав для снятия шаринга",
                )
            actor_id = existing.author_id or current_user["user_id"]
        else:
            actor_id = current_user["user_id"]

        ok = await repo.unshare_skill(skill_id, actor_id, username)
        if ok:
            return {"success": True, "message": f"Доступ для {username} снят"}
        raise HTTPException(status_code=403, detail="Не удалось снять доступ")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка unshare_skill")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/{skill_id}/files", response_model=List[SkillFileOut])
async def list_skill_files(
    skill_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    repo = get_skill_repository()
    skill = await repo.get_skill_by_id(
        skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
    )
    if not skill:
        raise HTTPException(status_code=404, detail="Skill не найден")
    return await repo.list_skill_files(skill_id)


@router.post("/{skill_id}/files", response_model=SkillFileOut, status_code=201)
async def upsert_skill_file(
    skill_id: int,
    payload: SkillFileCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    repo = get_skill_repository()
    if not await repo.can_write(
        skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
    ):
        raise HTTPException(status_code=403, detail="Нет прав")
    if is_agent_platform_role_mapping_enabled():
        role = get_agent_platform_role(current_user)
        if role not in ("owner", "editor"):
            raise HTTPException(status_code=403, detail="Недостаточно прав")
    if payload.content is None:
        raise HTTPException(status_code=400, detail="Укажите content")
    file_row = await repo.upsert_skill_file_text(
        skill_id,
        payload.relative_path,
        payload.content,
        mime_type=payload.mime_type or "text/plain",
        is_executable=payload.is_executable,
    )
    if not file_row:
        raise HTTPException(
            status_code=400,
            detail="Не удалось сохранить файл (SKILL.md запрещён как relative_path)",
        )
    return file_row


@router.get("/{skill_id}/files/content")
async def get_skill_file_content(
    skill_id: int,
    path: Annotated[str, Query()],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    repo = get_skill_repository()
    skill = await repo.get_skill_by_id(
        skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
    )
    if not skill:
        raise HTTPException(status_code=404, detail="Skill не найден")
    text = await repo.get_skill_file_content(skill_id, path)
    if text is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return {"relative_path": path, "content": text}


@router.delete("/{skill_id}/files/{file_id}", response_model=dict)
async def delete_skill_file(
    skill_id: int,
    file_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    repo = get_skill_repository()
    if not await repo.can_write(
        skill_id, current_user["user_id"], is_admin=_is_admin(current_user)
    ):
        raise HTTPException(status_code=403, detail="Нет прав")
    if is_agent_platform_role_mapping_enabled():
        role = get_agent_platform_role(current_user)
        if role not in ("owner", "editor"):
            raise HTTPException(status_code=403, detail="Недостаточно прав")
    ok = await repo.delete_skill_file(skill_id, file_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return {"success": True}
