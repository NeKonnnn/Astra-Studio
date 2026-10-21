"""API endpoints для пользовательских Баз знаний."""

from typing import Annotated, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.auth.agent_platform_roles import (
    cap_agent_permission,
    get_agent_platform_role,
    is_agent_platform_role_mapping_enabled,
    user_can_create_agents,
    user_can_publish_to_library,
)
from backend.auth.jwt_handler import get_current_user
from backend.auth.share_recipients import (
    build_share_api_response,
    prepare_share_recipients,
)
from backend.auth.user_directory import enrich_items_author_full_names
from backend.database.init_db import get_knowledge_base_repository
from backend.database.postgresql.knowledge_base_models import (
    KnowledgeBaseCreate,
    KnowledgeBaseFilters,
    KnowledgeBaseListItem,
    KnowledgeBaseOut,
    KnowledgeBaseRatingRequest,
    KnowledgeBaseShareEntry,
    KnowledgeBaseShareRequest,
    KnowledgeBaseSharesResponse,
    KnowledgeBaseUpdate,
)
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])


def _is_admin(user: dict) -> bool:
    return bool(user.get("is_admin"))


def _norm_uid(user_id: Optional[str]) -> str:
    return str(user_id or "").strip().lower()


def _is_kb_author(item, user: Optional[dict]) -> bool:
    if not item or not user:
        return False
    if _is_admin(user):
        return True
    return _norm_uid(getattr(item, "author_id", None)) == _norm_uid(
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


def _sync_write_access(item: Union[KnowledgeBaseListItem, KnowledgeBaseOut]) -> None:
    perm = getattr(item, "my_permission", None)
    item.write_access = perm in ("owner", "editor")


def _apply_platform_role(
    item: Union[KnowledgeBaseListItem, KnowledgeBaseOut],
    platform_role: Optional[str],
    *,
    user: Optional[dict] = None,
    is_admin: bool = False,
) -> None:
    if is_admin or not is_agent_platform_role_mapping_enabled():
        _sync_write_access(item)
        return
    if user is not None and _is_kb_author(item, user):
        _sync_write_access(item)
        return
    item.my_permission = cap_agent_permission(item.my_permission, platform_role)
    _sync_write_access(item)


def _apply_platform_role_to_items(
    items: List[Union[KnowledgeBaseListItem, KnowledgeBaseOut]], user: dict
) -> None:
    admin = _is_admin(user)
    if admin:
        for item in items:
            _sync_write_access(item)
        return
    if not is_agent_platform_role_mapping_enabled():
        return
    platform_role = get_agent_platform_role(user)
    for item in items:
        _apply_platform_role(item, platform_role, user=user)


class KnowledgeBasesListResponse(BaseModel):
    items: List[KnowledgeBaseListItem] = []
    total: int = 0
    page: int = 1
    pages: int = 0


@router.get("/", response_model=List[KnowledgeBaseListItem])
async def get_knowledge_bases(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Все доступные базы знаний."""
    try:
        repo = get_knowledge_base_repository()
        admin = _is_admin(current_user)
        page_size = 200
        items: List[KnowledgeBaseListItem] = []
        offset = 0
        while True:
            chunk, total = await repo.list_items(
                current_user["user_id"],
                KnowledgeBaseFilters(limit=page_size, offset=offset),
                is_admin=admin,
            )
            items.extend(chunk)
            offset += page_size
            if not chunk or offset >= total or not admin:
                break
        _apply_platform_role_to_items(items, current_user)
        await enrich_items_author_full_names(items)
        return items
    except Exception as e:
        logger.exception("Ошибка get_knowledge_bases")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/list", response_model=KnowledgeBasesListResponse)
async def get_knowledge_base_list(
    current_user: Annotated[dict, Depends(get_current_user)],
    query: Annotated[Optional[str], Query()] = None,
    view_option: Annotated[Optional[str], Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=1000)] = 30,
):
    """Пагинированный список баз знаний."""
    try:
        repo = get_knowledge_base_repository()
        filters = KnowledgeBaseFilters(
            search_query=query,
            view_option=view_option,
            limit=limit,
            offset=(page - 1) * limit,
        )
        items, total = await repo.list_items(
            current_user["user_id"], filters, is_admin=_is_admin(current_user)
        )
        _apply_platform_role_to_items(items, current_user)
        await enrich_items_author_full_names(items)
        pages = (total + limit - 1) // limit if total else 0
        return KnowledgeBasesListResponse(
            items=items, total=total, page=page, pages=pages
        )
    except Exception as e:
        logger.exception("Ошибка get_knowledge_base_list")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/my/bookmarks", response_model=KnowledgeBasesListResponse)
async def get_my_knowledge_base_bookmarks(
    current_user: Annotated[dict, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Закладки баз знаний текущего пользователя."""
    try:
        repo = get_knowledge_base_repository()
        bookmark_ids, total = await repo.get_user_bookmarks(
            current_user["user_id"], limit=limit, offset=(page - 1) * limit
        )
        if not bookmark_ids:
            return KnowledgeBasesListResponse(items=[], total=0, page=page, pages=0)
        items: List[KnowledgeBaseListItem] = []
        admin = _is_admin(current_user)
        for kid in bookmark_ids:
            kb = await repo.get_by_id(kid, current_user["user_id"], is_admin=admin)
            if kb:
                items.append(KnowledgeBaseListItem(**kb.model_dump()))
        _apply_platform_role_to_items(items, current_user)
        await enrich_items_author_full_names(items)
        pages = (total + limit - 1) // limit if total else 0
        return KnowledgeBasesListResponse(
            items=items, total=total, page=page, pages=pages
        )
    except Exception as e:
        logger.exception("Ошибка get_my_knowledge_base_bookmarks")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/create", response_model=KnowledgeBaseOut, status_code=201)
@router.post("/", response_model=KnowledgeBaseOut, status_code=201)
async def create_knowledge_base(
    data: KnowledgeBaseCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Создание базы знаний."""
    try:
        if not user_can_create_agents(current_user):
            raise HTTPException(
                status_code=403, detail="Недостаточно прав для создания базы знаний"
            )
        if data.is_public and not user_can_publish_to_library(current_user):
            data.is_public = False
        repo = get_knowledge_base_repository()
        item = await repo.create(
            data,
            author_id=current_user["user_id"],
            author_name=current_user.get("username")
            or current_user.get("full_name")
            or "Anonymous",
        )
        if not item:
            raise HTTPException(status_code=400, detail="Не удалось создать базу знаний")
        return item
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка create_knowledge_base")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/ensure-personal-library", response_model=KnowledgeBaseOut)
async def ensure_personal_library(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Personal KB для тумблера «Библиотека» (meta.source=memory_library)."""
    from backend.services.legacy_rag_to_kb import ensure_personal_library_kb

    item = await ensure_personal_library_kb(
        current_user["user_id"],
        author_name=current_user.get("username")
        or current_user.get("full_name")
        or "Anonymous",
    )
    if not item:
        raise HTTPException(status_code=400, detail="Не удалось создать личную БЗ")
    return item


@router.post("/ensure-project-library", response_model=KnowledgeBaseOut)
async def ensure_project_library(
    current_user: Annotated[dict, Depends(get_current_user)],
    project_id: str = Query(..., min_length=1),
    project_name: Optional[str] = Query(None),
):
    """БЗ файлов проекта; прикрепляется к project.knowledgeBaseIds."""
    from backend.services.legacy_rag_to_kb import ensure_project_library_kb

    item = await ensure_project_library_kb(
        current_user["user_id"],
        project_id,
        project_name=project_name,
        author_name=current_user.get("username")
        or current_user.get("full_name")
        or "Anonymous",
    )
    if not item:
        raise HTTPException(
            status_code=400, detail="Не удалось создать БЗ проекта"
        )
    return item


@router.post("/ensure-agent-library", response_model=KnowledgeBaseOut)
async def ensure_agent_library(
    current_user: Annotated[dict, Depends(get_current_user)],
    agent_id: int = Query(..., gt=0),
    agent_name: Optional[str] = Query(None),
):
    """БЗ для legacy file_search документов агента."""
    from backend.services.legacy_rag_to_kb import ensure_agent_library_kb

    item = await ensure_agent_library_kb(
        current_user["user_id"],
        agent_id,
        agent_name=agent_name,
        author_name=current_user.get("username")
        or current_user.get("full_name")
        or "Anonymous",
    )
    if not item:
        raise HTTPException(
            status_code=400, detail="Не удалось создать БЗ агента"
        )
    return item


@router.get("/{kb_id}", response_model=KnowledgeBaseOut)
async def get_knowledge_base(
    kb_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Получение базы знаний по id."""
    try:
        repo = get_knowledge_base_repository()
        item = await repo.get_by_id(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not item:
            raise HTTPException(status_code=404, detail="База знаний не найдена")
        _apply_platform_role(
            item,
            _platform_role(current_user),
            user=current_user,
            is_admin=_is_admin(current_user),
        )
        return item
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка get_knowledge_base")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/{kb_id}", response_model=KnowledgeBaseOut)
async def update_knowledge_base(
    kb_id: int,
    data: KnowledgeBaseUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Обновление базы знаний (в т.ч. публикация в галерею)."""
    try:
        repo = get_knowledge_base_repository()
        existing = await repo.get_by_id(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="База знаний не найдена")
        if not _is_kb_author(existing, current_user):
            _require_platform_roles(
                current_user,
                "owner",
                "editor",
                detail="Недостаточно прав для редактирования базы знаний",
            )
        if (
            data.is_public is not None
            and bool(data.is_public) != bool(getattr(existing, "is_public", False))
            and not user_can_publish_to_library(current_user)
        ):
            raise HTTPException(
                status_code=403,
                detail="Недостаточно прав для публикации базы знаний в галерее",
            )
        item = await repo.update(
            kb_id,
            data,
            current_user["user_id"],
            is_admin=_is_admin(current_user),
        )
        if not item:
            raise HTTPException(
                status_code=403, detail="Нет прав или база знаний не найдена"
            )
        return item
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка update_knowledge_base")
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _cleanup_knowledge_base_documents(kb_id: int) -> int:
    """Удалить документы SVC-RAG и исходники PVC/MinIO для базы знаний.

    Порядок как у одиночного DELETE /api/kb/documents/{id}:
    storage check → SVC-RAG delete → файл из PVC/MinIO.
    """
    from backend.rag_query.semantic_cache import bump_rag_semantic_cache
    from backend.routes.rag import _delete_rag_source_file, _require_storage_for_delete
    from backend.settings.rag_client import get_rag_client

    _require_storage_for_delete()
    rag = get_rag_client()
    if rag is None:
        raise HTTPException(status_code=503, detail="RAG-сервис недоступен")

    try:
        rows = list(await rag.kb_list_documents() or [])
    except Exception as e:
        logger.exception("Не удалось получить документы для удаления kb=%s", kb_id)
        raise HTTPException(
            status_code=502, detail="Не удалось получить список документов базы знаний"
        ) from e

    doc_ids: List[int] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        meta = row.get("metadata") or {}
        if not isinstance(meta, dict):
            continue
        try:
            if int(meta.get("knowledge_base_id")) != int(kb_id):
                continue
            doc_ids.append(int(row["id"]))
        except (TypeError, ValueError, KeyError):
            continue

    deleted = 0
    for doc_id in doc_ids:
        try:
            out = await rag.kb_delete_document(int(doc_id))
            if isinstance(out, dict):
                try:
                    _delete_rag_source_file(
                        out.get("minio_object"), out.get("minio_bucket")
                    )
                except Exception:
                    logger.exception(
                        "Не удалось удалить файл документа %s базы знаний %s",
                        doc_id,
                        kb_id,
                    )
            deleted += 1
        except Exception:
            logger.exception(
                "Не удалось удалить документ %s базы знаний %s", doc_id, kb_id
            )
    if deleted:
        bump_rag_semantic_cache()
    return deleted


@router.delete("/{kb_id}", response_model=dict)
async def delete_knowledge_base(
    kb_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Удаление базы знаний (автор или platform owner/admin) вместе с документами."""
    try:
        repo = get_knowledge_base_repository()
        existing = await repo.get_by_id(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="База знаний не найдена")
        if not _is_kb_author(existing, current_user):
            _require_platform_roles(
                current_user,
                "owner",
                detail="Недостаточно прав для удаления базы знаний",
            )
        deleted_docs = await _cleanup_knowledge_base_documents(int(kb_id))
        ok = await repo.delete(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not ok:
            raise HTTPException(
                status_code=403, detail="Нет прав или база знаний не найдена"
            )
        try:
            from backend.database.init_db import get_entity_settings_repository

            entity_repo = get_entity_settings_repository()
            if entity_repo is not None:
                await entity_repo.delete("kb", str(kb_id))
        except Exception:
            logger.exception(
                "Не удалось удалить entity_rag_settings для kb=%s", kb_id
            )
        return {
            "success": True,
            "message": "База знаний удалена",
            "deleted_documents": deleted_docs,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка delete_knowledge_base")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{kb_id}/rate", response_model=dict)
async def rate_knowledge_base(
    kb_id: int,
    rating_request: KnowledgeBaseRatingRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Оценка базы знаний пользователем."""
    try:
        repo = get_knowledge_base_repository()
        if not await repo.can_read(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        ):
            raise HTTPException(status_code=404, detail="База знаний не найдена")
        success = await repo.rate_knowledge_base(
            kb_id=kb_id,
            user_id=current_user["user_id"],
            rating=rating_request.rating,
        )
        if success:
            return {"success": True, "message": "Оценка сохранена"}
        raise HTTPException(status_code=404, detail="База знаний не найдена")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка rate_knowledge_base")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{kb_id}/view", response_model=dict)
async def view_knowledge_base(
    kb_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Увеличить счётчик просмотров базы знаний."""
    try:
        repo = get_knowledge_base_repository()
        if not await repo.can_read(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        ):
            raise HTTPException(status_code=404, detail="База знаний не найдена")
        success = await repo.increment_views(kb_id)
        if success:
            return {"success": True, "message": "Просмотр учтён"}
        raise HTTPException(status_code=404, detail="База знаний не найдена")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка view_knowledge_base")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{kb_id}/use", response_model=dict)
async def use_knowledge_base(
    kb_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Увеличить счётчик использований базы знаний."""
    try:
        repo = get_knowledge_base_repository()
        if not await repo.can_read(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        ):
            raise HTTPException(status_code=404, detail="База знаний не найдена")
        success = await repo.increment_usage(kb_id)
        if success:
            return {"success": True, "message": "Использование учтено"}
        raise HTTPException(status_code=404, detail="База знаний не найдена")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка use_knowledge_base")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{kb_id}/bookmark", response_model=dict)
async def add_knowledge_base_bookmark(
    kb_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Добавить базу знаний в закладки."""
    try:
        repo = get_knowledge_base_repository()
        if not await repo.can_read(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        ):
            raise HTTPException(status_code=404, detail="База знаний не найдена")
        success = await repo.add_bookmark(kb_id, current_user["user_id"])
        if success:
            return {"success": True, "message": "Добавлено в закладки"}
        raise HTTPException(status_code=404, detail="База знаний не найдена")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка add_knowledge_base_bookmark")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{kb_id}/bookmark", response_model=dict)
async def remove_knowledge_base_bookmark(
    kb_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Удалить базу знаний из закладок."""
    try:
        repo = get_knowledge_base_repository()
        success = await repo.remove_bookmark(kb_id, current_user["user_id"])
        if success:
            return {"success": True, "message": "Удалено из закладок"}
        raise HTTPException(status_code=404, detail="База знаний не найдена")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка remove_knowledge_base_bookmark")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{kb_id}/share", response_model=dict)
async def share_knowledge_base(
    kb_id: int,
    payload: KnowledgeBaseShareRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Поделиться базой знаний."""
    try:
        repo = get_knowledge_base_repository()
        existing = await repo.get_by_id(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="База знаний не найдена")
        if not _is_kb_author(existing, current_user):
            _require_platform_roles(
                current_user,
                "owner",
                detail="Недостаточно прав для шаринга базы знаний",
            )
        prepared = await prepare_share_recipients(payload.usernames, payload.permission)
        usernames_to_share = [uid for uid, _ in prepared.to_share]
        recipient_permissions = {uid: perm for uid, perm in prepared.to_share}
        added, skipped = await repo.share(
            kb_id=kb_id,
            owner_id=current_user["user_id"],
            usernames=usernames_to_share,
            permission=payload.permission,
            recipient_permissions=recipient_permissions,
        )
        response = build_share_api_response(
            prepared,
            added,
            skipped,
            resource_label="База знаний",
        )
        if not added and (prepared.skipped or skipped):
            raise HTTPException(
                status_code=403,
                detail=response.get("message") or "Не удалось поделиться базой знаний",
            )
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка share_knowledge_base")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{kb_id}/shares", response_model=KnowledgeBaseSharesResponse)
async def get_knowledge_base_shares(
    kb_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Список шарингов базы знаний."""
    try:
        repo = get_knowledge_base_repository()
        existing = await repo.get_by_id(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="База знаний не найдена")
        if not existing.write_access and not _is_kb_author(existing, current_user):
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        shares = await repo.list_shares(kb_id)
        return KnowledgeBaseSharesResponse(
            owner=KnowledgeBaseShareEntry(
                user_id=existing.author_id,
                full_name=existing.author_full_name or existing.author_name,
                permission="owner",
            ),
            shares=[
                KnowledgeBaseShareEntry(
                    user_id=s.shared_with_user_id,
                    permission=s.permission,
                )
                for s in shares
            ],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка get_knowledge_base_shares")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{kb_id}/share/{username}", response_model=dict)
async def unshare_knowledge_base(
    kb_id: int,
    username: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Снять шаринг базы знаний."""
    try:
        repo = get_knowledge_base_repository()
        existing = await repo.get_by_id(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="База знаний не найдена")
        if not _is_kb_author(existing, current_user):
            _require_platform_roles(
                current_user,
                "owner",
                detail="Недостаточно прав для изменения шаринга",
            )
        ok = await repo.remove_share(kb_id, username)
        if not ok:
            raise HTTPException(status_code=404, detail="Шаринг не найден")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка unshare_knowledge_base")
        raise HTTPException(status_code=500, detail=str(e)) from e


def _read_kb_source_bytes(object_name: Optional[str], bucket: Optional[str]):
    """Прочитать исходник документа из PVC или MinIO."""
    if not object_name or not bucket:
        return None
    try:
        from backend.storage.rag_pvc import is_rag_pvc_bucket, rag_pvc_file_path

        if is_rag_pvc_bucket(bucket):
            path = rag_pvc_file_path(object_name)
            if not path:
                return None
            with open(path, "rb") as fh:
                return fh.read()
    except Exception:
        logger.exception("Не удалось прочитать PVC-исходник KB object=%s", object_name)
        return None
    try:
        import backend.app_state as state

        client = getattr(state, "minio_client", None)
        if not client:
            return None
        return client.download_file(object_name, bucket_name=bucket)
    except Exception:
        logger.exception(
            "Не удалось прочитать MinIO-исходник KB object=%s bucket=%s",
            object_name,
            bucket,
        )
        return None


async def _clone_kb_documents_into(
    *,
    source_kb_id: int,
    new_kb_id: int,
    owner_user_id: str,
    username: str,
) -> list:
    """Физически клонировать документы БЗ (исходник + векторы) под new_kb_id."""
    from backend.settings.rag_client import get_rag_client
    from backend.storage.rag_pvc import (
        RAG_PVC_BUCKET_MARKER,
        save_rag_bytes_to_pvc,
        use_rag_pvc,
    )

    rag = get_rag_client()
    if rag is None:
        logger.warning("Клон БЗ %s: RAG недоступен — документы не скопированы", new_kb_id)
        return []

    try:
        rows = list(await rag.kb_list_documents() or [])
    except Exception:
        logger.exception("Клон БЗ: не удалось получить список документов")
        return []

    source_ids: list = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        meta = row.get("metadata") or {}
        if not isinstance(meta, dict):
            continue
        try:
            if int(meta.get("knowledge_base_id")) != int(source_kb_id):
                continue
            source_ids.append(int(row["id"]))
        except (TypeError, ValueError, KeyError):
            continue

    new_ids: list = []
    for src_id in source_ids:
        try:
            info = await rag.kb_get_document(int(src_id))
            if not isinstance(info, dict):
                continue
            meta = info.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            filename = str(info.get("filename") or f"document-{src_id}")
            src_object = meta.get("minio_object")
            src_bucket = meta.get("minio_bucket")

            new_object: Optional[str] = None
            new_bucket: Optional[str] = None
            raw = _read_kb_source_bytes(
                str(src_object) if src_object else None,
                str(src_bucket) if src_bucket else None,
            )
            if raw and use_rag_pvc():
                new_object = save_rag_bytes_to_pvc(
                    raw,
                    filename,
                    scope="kb",
                    username=username,
                    prefix="kb_",
                )
                if new_object:
                    new_bucket = RAG_PVC_BUCKET_MARKER
            elif raw and src_bucket and str(src_bucket).strip() != RAG_PVC_BUCKET_MARKER:
                try:
                    import uuid as _uuid

                    import backend.app_state as state

                    client = getattr(state, "minio_client", None)
                    if client:
                        ext = ""
                        if "." in filename:
                            ext = "." + filename.rsplit(".", 1)[-1]
                        new_object = f"kb_clone/{new_kb_id}/{_uuid.uuid4().hex}{ext}"
                        client.upload_file(
                            raw, new_object, bucket_name=str(src_bucket)
                        )
                        new_bucket = str(src_bucket)
                except Exception:
                    logger.exception(
                        "Клон БЗ: не удалось скопировать MinIO-файл doc=%s", src_id
                    )
                    new_object = None
                    new_bucket = None

            out = await rag.kb_clone_document(
                int(src_id),
                knowledge_base_id=int(new_kb_id),
                owner_user_id=owner_user_id,
                uploaded_by=username,
                minio_object=new_object or "",
                minio_bucket=new_bucket or "",
            )
            if isinstance(out, dict) and out.get("ok") and out.get("document_id"):
                new_ids.append(int(out["document_id"]))
            else:
                logger.warning("Клон БЗ: svc-rag не склонировал doc=%s → %s", src_id, out)
        except Exception:
            logger.exception(
                "Клон БЗ: ошибка копирования документа %s для kb=%s",
                src_id,
                new_kb_id,
            )
    return new_ids


async def _clone_kb_rag_settings(
    source_kb_id: int, new_kb_id: int, updated_by: str
) -> None:
    try:
        from backend.database.init_db import get_entity_settings_repository

        entity_repo = get_entity_settings_repository()
        if entity_repo is None:
            return
        stored = await entity_repo.get("kb", str(source_kb_id))
        if isinstance(stored, dict) and stored:
            await entity_repo.upsert("kb", str(new_kb_id), dict(stored), updated_by)
    except Exception:
        logger.exception(
            "Не удалось скопировать RAG-настройки kb %s → %s",
            source_kb_id,
            new_kb_id,
        )


@router.post("/{kb_id}/duplicate", response_model=dict)
async def duplicate_knowledge_base(
    kb_id: int, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Полное клонирование базы знаний: карточка + документы + RAG-настройки."""
    try:
        if not user_can_create_agents(current_user):
            raise HTTPException(
                status_code=403, detail="Недостаточно прав для создания базы знаний"
            )
        repo = get_knowledge_base_repository()
        source = await repo.get_by_id(
            kb_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not source:
            raise HTTPException(status_code=404, detail="База знаний не найдена")

        owner_uid = str(current_user["user_id"]).strip().lower()
        username = (
            current_user.get("username")
            or current_user.get("full_name")
            or owner_uid
            or "Anonymous"
        )
        base_name = (source.name or "База знаний").strip() or "База знаний"
        copy_name = f"{base_name} (копия)"
        if len(copy_name) > 255:
            copy_name = copy_name[:255]

        meta = dict(source.meta or {}) if isinstance(source.meta, dict) else {}
        meta["cloned_from"] = int(kb_id)

        created = await repo.create(
            KnowledgeBaseCreate(
                name=copy_name,
                description=source.description,
                meta=meta,
                is_public=False,
            ),
            author_id=owner_uid,
            author_name=str(username),
        )
        if not created:
            raise HTTPException(status_code=400, detail="Не удалось создать копию базы знаний")

        new_kb_id = int(created.id)
        new_doc_ids = await _clone_kb_documents_into(
            source_kb_id=int(kb_id),
            new_kb_id=new_kb_id,
            owner_user_id=owner_uid,
            username=str(username).strip().lower(),
        )
        try:
            await repo.set_document_count(new_kb_id, len(new_doc_ids))
        except Exception:
            logger.exception("Не удалось обновить document_count для kb=%s", new_kb_id)

        await _clone_kb_rag_settings(int(kb_id), new_kb_id, owner_uid)

        item = await repo.get_by_id(new_kb_id, owner_uid, is_admin=True)
        payload = item or created
        return {
            "success": True,
            "knowledge_base_id": new_kb_id,
            "cloned_documents": len(new_doc_ids),
            "item": payload.model_dump(mode="json"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка duplicate_knowledge_base")
        raise HTTPException(status_code=500, detail=str(e)) from e
