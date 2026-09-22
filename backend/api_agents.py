"""
API endpoints для галереи агентов
"""

import asyncio
from typing import Annotated, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from backend.auth.jwt_handler import get_current_user, get_optional_user
from backend.auth.user_directory import enrich_items_author_full_names
from backend.database.init_db import get_agent_repository
from backend.database.postgresql.agent_models import (
    AgentCreate,
    AgentFilters,
    AgentShareEntry,
    AgentShareRequest,
    AgentSharesResponse,
    AgentStats,
    AgentUpdate,
    AgentWithTags,
)
from backend.settings.cef_logger.cef_logger import log_cef_event
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

# Кэш ФИО по gpbu (логин один и тот же для LDAP и SSO), чтобы не дёргать LDAP на каждый запрос
_full_name_cache: Dict[str, Optional[str]] = {}


@router.get("/chain-config")
async def get_agent_chain_config():
    """Лимиты цепочки и шагов графа из ConfigMap (AGENT_CHAIN_MAX_AGENTS, AGENT_GRAPH_STEPS)."""
    from backend.agents.chain import (
        MAX_CHAIN_AGENTS_CAP,
        get_agent_graph_steps,
        get_max_chain_agents,
    )
    from backend.agents.config import DEFAULT_RECURSION_LIMIT, MAX_RECURSION_LIMIT_CAP
    from backend.agents.subagents import MAX_SUBAGENTS_CAP, get_max_subagents

    return {
        "max_agents": get_max_chain_agents(),
        "max_agents_cap": MAX_CHAIN_AGENTS_CAP,
        "max_subagents": get_max_subagents(),
        "max_subagents_cap": MAX_SUBAGENTS_CAP,
        "graph_steps": get_agent_graph_steps(),
        "default_recursion_limit": get_agent_graph_steps() or DEFAULT_RECURSION_LIMIT,
        "max_recursion_limit": MAX_RECURSION_LIMIT_CAP,
    }


@router.get("/tags")
async def list_agent_tags(current_user: Annotated[dict, Depends(get_current_user)]):
    """Справочник тегов: поле «Теги» в карточке и «Обязательные теги» у субагентов.

    Стоит до маршрута /{agent_id}, иначе слово tags ушло бы туда как id.
    """
    agent_repo = get_agent_repository()
    if agent_repo is None:
        return {"tags": []}
    return {"tags": await agent_repo.list_tags()}


async def _resolve_full_names(user_ids: List[str]) -> Dict[str, Optional[str]]:
    """ФИО по списку gpbu из LDAP (service bind). Логин одинаков для LDAP и SSO."""
    result: Dict[str, Optional[str]] = {}
    to_lookup: List[str] = []
    seen: set = set()
    for raw in user_ids:
        uid = (raw or "").strip()
        if not uid:
            continue
        key = uid.lower()
        if key in seen:
            continue
        seen.add(key)
        if key in _full_name_cache:
            result[uid] = _full_name_cache[key]
        else:
            to_lookup.append(uid)

    if not to_lookup:
        return result

    try:
        from backend.auth.ldap_auth import fetch_ldap_user_profile, is_ldap_enabled
    except Exception:  # pragma: no cover - LDAP модуль недоступен
        for uid in to_lookup:
            result[uid] = None
        return result

    if not is_ldap_enabled():
        for uid in to_lookup:
            result[uid] = None
        return result

    def _bulk() -> Dict[str, Optional[str]]:
        out: Dict[str, Optional[str]] = {}
        for uid in to_lookup:
            name: Optional[str] = None
            try:
                profile = fetch_ldap_user_profile(uid)
                if profile:
                    name = profile.get("full_name") or None
            except Exception:
                name = None
            out[uid] = name
        return out

    try:
        looked = await asyncio.to_thread(_bulk)
    except Exception:
        logger.exception("Ошибка резолва ФИО по gpbu")
        looked = {uid: None for uid in to_lookup}

    for uid, name in looked.items():
        _full_name_cache[uid.lower()] = name
        result[uid] = name
    return result


async def _reject_foreign_kb_documents(
    agent_repo, agent_id: Optional[int], config: Optional[dict]
) -> None:
    """Не дать привязать файл, который уже числится за другим агентом.

    Файл принадлежит ровно одному агенту: перечанковка идёт по настройкам
    агента, и общий файл один из них молча перекроил бы под себя.
    """
    if not isinstance(config, dict):
        return
    raw = config.get("kb_document_ids")
    if not isinstance(raw, list) or not raw:
        return
    ids: List[int] = []
    for value in raw:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return
    foreign = await agent_repo.find_documents_claimed_by_other_agents(agent_id, ids)
    if foreign:
        raise HTTPException(
            status_code=400,
            detail=(
                "Файлы уже привязаны к другому агенту и не могут использоваться "
                f"двумя сразу: {', '.join(str(i) for i in foreign)}. "
                "Загрузите их в этого агента отдельно."
            ),
        )


@router.post("", response_model=dict, status_code=201)
@router.post("/", response_model=dict, status_code=201, include_in_schema=False)
async def create_agent(
    request: Request, agent_data: AgentCreate, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Создание нового агента"""
    try:
        agent_repo = get_agent_repository()
        await _reject_foreign_kb_documents(agent_repo, None, agent_data.config)
        agent_id = await agent_repo.create_agent(
            agent_data=agent_data,
            author_id=current_user["user_id"],
            author_name=current_user.get("username", "Anonymous"),
        )
        if agent_id:
            log_cef_event(
                "AGT001",
                request=request,
                current_user=current_user,
                status_code=201,
                extra={"cs1": agent_data.name, "cs2": str(agent_id), "cs1Label": "AgentName", "cs2Label": "AgentId"},
            )
            return {"success": True, "agent_id": agent_id, "message": "Агент успешно создан"}
        else:
            raise HTTPException(status_code=500, detail="Ошибка при создании агента")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка создания агента")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{agent_id}/view", response_model=dict)
async def view_agent(agent_id: int, current_user: Annotated[Optional[dict], Depends(get_optional_user)]):
    """Увеличить счетчик просмотров агента (публичный доступ)"""
    try:
        agent_repo = get_agent_repository()
        success = await agent_repo.increment_views(agent_id)
        if success:
            return {"success": True, "message": "Просмотр учтён"}
        else:
            raise HTTPException(status_code=404, detail="Агент не найден")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка учёта просмотра")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{agent_id}", response_model=AgentWithTags)
async def get_agent(agent_id: int, current_user: Annotated[Optional[dict], Depends(get_optional_user)]):
    """Получение агента по ID (публичный / автор / получатель шаринга)"""
    try:
        agent_repo = get_agent_repository()
        user_id = current_user["user_id"] if current_user else None
        agent = await agent_repo.get_agent(agent_id, user_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Агент не найден")
        can_access = await agent_repo.user_can_access_agent(agent_id, user_id)
        if not can_access:
            raise HTTPException(status_code=403, detail="Нет доступа к этому агенту")
        await agent_repo.increment_views(agent_id)
        await enrich_items_author_full_names([agent])
        return agent
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка получения агента")
        raise HTTPException(status_code=500, detail=str(e)) from e


class AgentsResponse(BaseModel):
    """Ответ со списком агентов"""

    agents: List[AgentWithTags]
    total: int
    page: int
    pages: int


@router.get("", response_model=AgentsResponse)
@router.get("/", response_model=AgentsResponse, include_in_schema=False)
async def get_agents(
    current_user: Annotated[Optional[dict], Depends(get_optional_user)],
    search: Annotated[Optional[str], Query(description="Поисковый запрос")] = None,
    tags: Annotated[Optional[str], Query(description="ID тегов через запятую")] = None,
    author_id: Annotated[Optional[str], Query(description="ID автора")] = None,
    min_rating: Annotated[Optional[float], Query(ge=0, le=5, description="Минимальный рейтинг")] = None,
    sort_by: Annotated[str, Query(description="Поле сортировки (rating, date, views, usage, votes)")] = "rating",
    sort_order: Annotated[str, Query(description="Порядок сортировки (asc/desc)")] = "desc",
    page: Annotated[int, Query(ge=1, description="Номер страницы")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Количество на странице")] = 20,
):
    """Получение списка агентов с фильтрацией (публичный доступ)"""
    try:
        logger.debug(
            "Запрос списка агентов: page=%s, limit=%s, sort_by=%s, sort_order=%s",
            page,
            limit,
            sort_by,
            sort_order,
        )
        agent_repo = get_agent_repository()
        tag_ids = None
        if tags:
            try:
                tag_ids = [int(t.strip()) for t in tags.split(",") if t.strip()]
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Неверный формат тегов") from e
        filters = AgentFilters(
            search_query=search,
            tag_ids=tag_ids,
            author_id=author_id,
            min_rating=min_rating,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=(page - 1) * limit,
        )
        user_id = current_user["user_id"] if current_user else None
        agents, total = await agent_repo.get_agents(filters, user_id)
        await enrich_items_author_full_names(agents)
        logger.debug("Получено агентов: %s, всего: %s", len(agents), total)
        pages = (total + limit - 1) // limit
        return AgentsResponse(agents=agents, total=total, page=page, pages=pages)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        logger.error(f"Ошибка получения списка агентов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/{agent_id}", response_model=dict)
async def update_agent(
    request: Request, agent_id: int, agent_data: AgentUpdate, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Обновление агента (только автор)"""
    try:
        agent_repo = get_agent_repository()
        await _reject_foreign_kb_documents(agent_repo, agent_id, agent_data.config)
        success = await agent_repo.update_agent(
            agent_id=agent_id, agent_data=agent_data, author_id=current_user["user_id"]
        )
        if success:
            log_cef_event(
                "AGT002",
                request=request,
                current_user=current_user,
                status_code=200,
                extra={
                    "cs1": getattr(agent_data, "name", None) or f"agent-{agent_id}",
                    "cs2": str(agent_id),
                    "cs1Label": "AgentName",
                    "cs2Label": "AgentId",
                },
            )
            return {"success": True, "message": "Агент успешно обновлён"}
        else:
            raise HTTPException(status_code=403, detail="Недостаточно прав для редактирования этого агента")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка обновления агента")
        raise HTTPException(status_code=500, detail=str(e)) from e


def _extract_kb_document_ids(agent) -> list:
    """kb_document_ids из config агента (config может прийти строкой)."""
    cfg = getattr(agent, "config", None) or {}
    if isinstance(cfg, str):
        import json

        try:
            cfg = json.loads(cfg)
        except (TypeError, ValueError):
            cfg = {}
    if not isinstance(cfg, dict):
        return []
    out = []
    for raw in cfg.get("kb_document_ids") or []:
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            continue
    return out


@router.delete("/{agent_id}", response_model=dict)
async def delete_agent(request: Request, agent_id: int, current_user: Annotated[dict, Depends(get_current_user)]):
    """Удаление агента (только автор)"""
    try:
        agent_repo = get_agent_repository()
        existing = await agent_repo.get_agent(agent_id, current_user["user_id"])
        success = await agent_repo.delete_agent(agent_id=agent_id, author_id=current_user["user_id"])
        if success:
            _name = (existing.name if existing else None) or f"agent-{agent_id}"
            log_cef_event(
                "AGT005",
                request=request,
                current_user=current_user,
                status_code=200,
                extra={"cs1": _name, "cs2": str(agent_id), "cs1Label": "AgentName", "cs2Label": "AgentId"},
            )
            # Чистим KB-документы агента, на которые больше никто не ссылается.
            # Считаем ПОСЛЕ удаления агента: его собственные ссылки уже не учитываются.
            # Ошибки здесь не должны ломать удаление агента — он уже удалён.
            doc_ids = _extract_kb_document_ids(existing)
            if doc_ids:
                try:
                    orphans = await agent_repo.find_orphan_kb_document_ids(doc_ids)
                    if orphans:
                        from backend.settings.rag_client import get_rag_client
                        from backend.routes.rag import _delete_rag_source_file

                        rag = get_rag_client()
                        for doc_id in orphans:
                            try:
                                out = await rag.kb_delete_document(int(doc_id))
                                logger.info(
                                    "Удалён осиротевший KB-документ %s (агент %s)",
                                    doc_id,
                                    agent_id,
                                )
                                if isinstance(out, dict):
                                    try:
                                        _delete_rag_source_file(
                                            out.get("minio_object"),
                                            out.get("minio_bucket"),
                                        )
                                    except Exception:
                                        logger.exception(
                                            "Не удалось удалить файл KB-документа %s агента %s",
                                            doc_id,
                                            agent_id,
                                        )
                            except Exception:
                                logger.exception(
                                    "Не удалось удалить KB-документ %s агента %s",
                                    doc_id,
                                    agent_id,
                                )
                    kept = [d for d in doc_ids if d not in set(orphans or [])]
                    if kept:
                        logger.info(
                            "KB-документы агента %s оставлены (на них ссылаются другие агенты): %s",
                            agent_id,
                            kept,
                        )
                except Exception:
                    logger.exception("Ошибка чистки KB-документов агента %s", agent_id)
            return {"success": True, "message": "Агент успешно удалён"}
        else:
            raise HTTPException(status_code=403, detail="Недостаточно прав для удаления этого агента")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка удаления агента")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{agent_id}/duplicate", response_model=dict)
async def duplicate_agent(request: Request, agent_id: int, current_user: Annotated[dict, Depends(get_current_user)]):
    """Дублирование агента"""
    try:
        agent_repo = get_agent_repository()
        source = await agent_repo.get_agent(agent_id, current_user["user_id"])
        if not source:
            raise HTTPException(status_code=404, detail="Агент не найден")
        create_data = AgentCreate(
            name=f"{source.name} (copy)",
            description=source.description,
            system_prompt=source.system_prompt,
            config=source.config or {},
            tools=source.tools or [],
            is_public=source.is_public,
            tag_ids=[int(t["id"]) for t in source.tags or [] if isinstance(t, dict) and t.get("id")],
            new_tags=[],
        )
        new_agent_id = await agent_repo.create_agent(
            agent_data=create_data,
            author_id=current_user["user_id"],
            author_name=current_user.get("username", "Anonymous"),
        )
        if not new_agent_id:
            raise HTTPException(status_code=500, detail="Ошибка при дублировании агента")
        log_cef_event(
            "AGT003",
            request=request,
            current_user=current_user,
            status_code=201,
            extra={
                "cs1": source.name,
                "cs2": str(new_agent_id),
                "agt_copy_target": create_data.name,
                "cs3": f"duplicated from {agent_id}",
                "cs3Label": "Context",
                "cs1Label": "AgentName",
                "cs2Label": "AgentId",
            },
        )
        return {"success": True, "agent_id": new_agent_id, "message": "Агент скопирован"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка дублирования агента")
        raise HTTPException(status_code=500, detail=str(e)) from e


class RatingRequest(BaseModel):
    """Запрос на оценку агента"""

    rating: int


class RollbackRequest(BaseModel):
    version: int = 0


@router.post("/{agent_id}/rate", response_model=dict)
async def rate_agent(
    agent_id: int, rating_request: RatingRequest, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Оценка агента пользователем"""
    try:
        if rating_request.rating < 1 or rating_request.rating > 5:
            raise HTTPException(status_code=400, detail="Рейтинг должен быть от 1 до 5")
        agent_repo = get_agent_repository()
        success = await agent_repo.rate_agent(
            agent_id=agent_id, user_id=current_user["user_id"], rating=rating_request.rating
        )
        if success:
            return {"success": True, "message": "Оценка сохранена"}
        else:
            raise HTTPException(status_code=404, detail="Агент не найден")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка оценки агента")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{agent_id}/use", response_model=dict)
async def use_agent(
    request: Request, agent_id: int, current_user: Annotated[Optional[dict], Depends(get_optional_user)]
):
    """Отметить использование агента"""
    try:
        agent_repo = get_agent_repository()
        usage_success = await agent_repo.increment_usage(agent_id)
        if usage_success:
            ag = await agent_repo.get_agent(agent_id, (current_user or {}).get("user_id"))
            _disp = ag.name if ag else f"agent-{agent_id}"
            log_cef_event(
                "INT004",
                request=request,
                current_user=current_user,
                status_code=200,
                extra={"cs1": _disp, "cs2": str(agent_id), "cs2Label": "AgentId"},
            )
            return {"success": True, "message": "Использование учтено"}
        else:
            raise HTTPException(status_code=404, detail="Агент не найден")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка учёта использования")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{agent_id}/stats", response_model=AgentStats)
async def get_agent_stats(agent_id: int, current_user: Annotated[Optional[dict], Depends(get_optional_user)]):
    """Получение статистики агента (публичный доступ)"""
    try:
        agent_repo = get_agent_repository()
        stats = await agent_repo.get_agent_stats(agent_id)
        if not stats:
            raise HTTPException(status_code=404, detail="Агент не найден")
        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка получения статистики")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{agent_id}/rollback", response_model=dict)
async def rollback_agent(
    request: Request, agent_id: int, payload: RollbackRequest, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Логическая операция rollback (пока без хранения ревизий)."""
    try:
        agent_repo = get_agent_repository()
        source = await agent_repo.get_agent(agent_id, current_user["user_id"])
        if not source:
            raise HTTPException(status_code=404, detail="Агент не найден")
        ok = await agent_repo.update_agent(agent_id, AgentUpdate(), current_user["user_id"])
        if not ok:
            raise HTTPException(status_code=403, detail="Недостаточно прав для rollback")
        log_cef_event(
            "AGT004",
            request=request,
            current_user=current_user,
            status_code=200,
            extra={
                "cs1": source.name,
                "cs2": str(agent_id),
                "cn1": payload.version,
                "cn1Label": "VersionNumber",
                "cs1Label": "AgentName",
                "cs2Label": "AgentId",
            },
        )
        return {"success": True, "message": f"Rollback агента выполнен к версии {payload.version}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка rollback агента")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{agent_id}/bookmark", response_model=dict)
async def add_bookmark(agent_id: int, current_user: Annotated[dict, Depends(get_current_user)]):
    """Добавить агента в закладки"""
    try:
        agent_repo = get_agent_repository()
        success = await agent_repo.add_bookmark(agent_id, current_user["user_id"])
        if success:
            return {"success": True, "message": "Добавлено в закладки"}
        else:
            raise HTTPException(status_code=404, detail="Агент не найден")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка добавления в закладки")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{agent_id}/bookmark", response_model=dict)
async def remove_bookmark(agent_id: int, current_user: Annotated[dict, Depends(get_current_user)]):
    """Удалить агента из закладок"""
    try:
        agent_repo = get_agent_repository()
        success = await agent_repo.remove_bookmark(agent_id, current_user["user_id"])
        if success:
            return {"success": True, "message": "Удалено из закладок"}
        else:
            raise HTTPException(status_code=404, detail="Агент не найден")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка удаления из закладок")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/my/bookmarks", response_model=AgentsResponse)
async def get_my_bookmarks(
    current_user: Annotated[dict, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Получение закладок текущего пользователя"""
    try:
        logger.info(
            f"Запрос закладок пользователя: {current_user.get('user_id', 'unknown')}, page={page}, limit={limit}"
        )
        agent_repo = get_agent_repository()
        bookmark_ids, total = await agent_repo.get_user_bookmarks(
            current_user["user_id"], limit=limit, offset=(page - 1) * limit
        )
        logger.info(f"Найдено закладок: {total}, IDs: {bookmark_ids}")
        if not bookmark_ids:
            logger.info("У пользователя нет закладок")
            return AgentsResponse(agents=[], total=0, page=page, pages=0)
        agents = []
        for agent_id in bookmark_ids:
            agent = await agent_repo.get_agent(agent_id, current_user["user_id"])
            if agent:
                agents.append(agent)
        await enrich_items_author_full_names(agents)
        pages = (total + limit - 1) // limit
        logger.info(f"Возвращаем {len(agents)} агентов из закладок")
        return AgentsResponse(agents=agents, total=total, page=page, pages=pages)
    except Exception as e:
        logger.exception("Ошибка получения закладок")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/my/agents", response_model=AgentsResponse)
async def get_my_agents(
    current_user: Annotated[dict, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Получение агентов текущего пользователя"""
    try:
        agent_repo = get_agent_repository()
        filters = AgentFilters(
            author_id=current_user["user_id"],
            author_only=True,
            sort_by="date",
            sort_order="desc",
            limit=limit,
            offset=(page - 1) * limit,
        )
        agents, total = await agent_repo.get_agents(filters, current_user["user_id"])
        await enrich_items_author_full_names(agents)
        pages = (total + limit - 1) // limit
        return AgentsResponse(agents=agents, total=total, page=page, pages=pages)
    except Exception as e:
        logger.exception("Ошибка получения моих агентов")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/my/shared", response_model=AgentsResponse)
async def get_shared_with_me(
    current_user: Annotated[dict, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Агенты, которыми поделились с текущим пользователем"""
    try:
        agent_repo = get_agent_repository()
        agents, total = await agent_repo.get_shared_with_me(
            current_user["user_id"], limit=limit, offset=(page - 1) * limit
        )
        await enrich_items_author_full_names(agents)
        pages = (total + limit - 1) // limit if total else 0
        return AgentsResponse(agents=agents, total=total, page=page, pages=pages)
    except Exception as e:
        logger.exception("Ошибка получения расшаренных агентов")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{agent_id}/share", response_model=dict)
async def share_agent(
    agent_id: int,
    payload: AgentShareRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Поделиться агентом с пользователями (только автор)"""
    try:
        agent_repo = get_agent_repository()
        existing = await agent_repo.get_agent(agent_id, current_user["user_id"])
        if not existing:
            raise HTTPException(status_code=404, detail="Агент не найден")
        added, skipped = await agent_repo.share_agent(
            agent_id=agent_id,
            owner_id=current_user["user_id"],
            usernames=payload.usernames,
            permission=payload.permission,
        )
        if not added and skipped:
            raise HTTPException(
                status_code=403,
                detail="Не удалось поделиться агентом (нет прав или некорректные получатели)",
            )
        return {
            "success": True,
            "shared_with": added,
            "skipped": skipped,
            "message": f"Агент доступен для: {', '.join(added)}" if added else "Никого не добавлено",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка шаринга агента")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{agent_id}/shares", response_model=AgentSharesResponse)
async def list_agent_shares(agent_id: int, current_user: Annotated[dict, Depends(get_current_user)]):
    """Список доступа к агенту: владелец (создатель) + получатели, с ФИО."""
    try:
        agent_repo = get_agent_repository()
        existing = await agent_repo.get_agent(agent_id, current_user["user_id"])
        if not existing:
            raise HTTPException(status_code=404, detail="Агент не найден")
        author = (existing.author_id or "").strip().lower()
        me = (current_user["user_id"] or "").strip().lower()
        if author != me:
            raise HTTPException(status_code=403, detail="Только автор может просматривать список шарингов")

        raw_shares = await agent_repo.list_agent_shares(agent_id, current_user["user_id"])

        owner_id = existing.author_id or current_user["user_id"]
        name_map = await _resolve_full_names(
            [owner_id, *[s.shared_with_user_id for s in raw_shares]]
        )

        owner_name = name_map.get(owner_id)
        # ФИО текущего пользователя из токена — как запасной вариант для владельца
        if not owner_name and (existing.author_id or "").strip().lower() == me:
            owner_name = current_user.get("full_name") or current_user.get("name")

        owner_entry = AgentShareEntry(
            user_id=owner_id,
            full_name=owner_name,
            permission="owner",
        )
        share_entries = [
            AgentShareEntry(
                user_id=s.shared_with_user_id,
                full_name=name_map.get(s.shared_with_user_id),
                permission="editor" if (s.permission or "").strip().lower() == "editor" else "viewer",
            )
            for s in raw_shares
        ]
        return AgentSharesResponse(owner=owner_entry, shares=share_entries)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка списка шарингов")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{agent_id}/share/{username}", response_model=dict)
async def unshare_agent(
    agent_id: int, username: str, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Снять доступ: автор отзывает или получатель выходит"""
    try:
        agent_repo = get_agent_repository()
        success = await agent_repo.unshare_agent(
            agent_id=agent_id,
            actor_id=current_user["user_id"],
            target_user_id=username,
        )
        if success:
            return {"success": True, "message": f"Доступ для {username} снят"}
        raise HTTPException(status_code=403, detail="Не удалось снять доступ")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка снятия шаринга")
        raise HTTPException(status_code=500, detail=str(e)) from e
