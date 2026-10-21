"""
API endpoints для галереи агентов
"""

from typing import Annotated, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from backend.utils.http_errors import public_error_detail
from pydantic import BaseModel

from backend.auth.jwt_handler import get_current_user, get_optional_user
from backend.auth.agent_platform_roles import (
    cap_agent_permission,
    get_agent_platform_role,
    is_agent_platform_role_mapping_enabled,
    user_can_create_agents,
    user_can_publish_to_library,
    user_can_use_agents,
)
from backend.auth.user_directory import (
    enrich_items_author_full_names,
    resolve_full_names,
)
from backend.auth.share_recipients import (
    build_share_api_response,
    prepare_share_recipients,
)
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


def _is_admin(user: Optional[dict]) -> bool:
    return bool(user and user.get("is_admin"))


def _require_agents_feature(user: Optional[dict]) -> None:
    """Базовый пользователь (base) и прочие без owner/editor/viewer — без агентов."""
    if user is not None and user_can_use_agents(user):
        return
    if user is None and not is_agent_platform_role_mapping_enabled():
        return
    raise HTTPException(
        status_code=403,
        detail="Агенты недоступны для вашей роли",
    )


async def get_agents_user(
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    _require_agents_feature(current_user)
    return current_user


def _require_platform_roles(user: dict, *allowed: str, detail: str) -> None:
    """Проверка AGENT_ROLE_* (пропускает LDAP/Keycloak admin)."""
    if _is_admin(user):
        return
    if not is_agent_platform_role_mapping_enabled():
        return
    role = get_agent_platform_role(user)
    if role not in allowed:
        raise HTTPException(status_code=403, detail=detail)


def _norm_uid(user_id: Optional[str]) -> str:
    return str(user_id or "").strip().lower()


def _is_agent_author(agent, user: Optional[dict]) -> bool:
    """Автор карточки (в т.ч. своего клона) — полный контроль над ней."""
    if not agent or not user:
        return False
    if _is_admin(user):
        return True
    return _norm_uid(getattr(agent, "author_id", None)) == _norm_uid(
        user.get("user_id")
    )


def _apply_platform_permission_cap(
    agent, user: Optional[dict], platform_role: Optional[str]
) -> None:
    """Сжать my_permission platform-ролью, кроме автора своей карточки.

    Иначе viewer, склонировавший агента из галереи, видел бы свой клон
    только на чтение — хотя он его автор и должен править/удалять клон.
    Исходный (чужой) публичный агент по-прежнему капится.

    Явный шаринг (is_shared_with_me) не капим: ACL editor/viewer из
    agent_shares должен дойти до UI/API получателя как есть. Иначе выдача
    «Редактор» человеку с platform viewer снова показывается как «Зритель».
    """
    if not agent or not user or _is_admin(user):
        return
    if not is_agent_platform_role_mapping_enabled():
        return
    if _is_agent_author(agent, user):
        return
    if getattr(agent, "is_shared_with_me", False):
        return
    if platform_role is None:
        return
    agent.my_permission = cap_agent_permission(agent.my_permission, platform_role)


async def _enrich_agents_for_response(agents: List[AgentWithTags]) -> None:
    """ФИО авторов + имена субагентов в config (для шаринга/галереи)."""
    if not agents:
        return
    await enrich_items_author_full_names(agents)
    try:
        from backend.agents.subagents.names import enrich_agents_subagent_names

        await enrich_agents_subagent_names(agents)
    except Exception:
        logger.exception("Не удалось обогатить имена субагентов")



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

@router.get("/chain-config")
async def get_agent_chain_config(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Лимиты цепочки и шагов графа из ConfigMap (AGENT_CHAIN_MAX_AGENTS, AGENT_GRAPH_STEPS)."""
    from backend.agents.chain.settings import (
        MAX_CHAIN_AGENTS_CAP,
        get_agent_graph_steps,
        get_max_chain_agents,
    )
    from backend.agents.config import DEFAULT_RECURSION_LIMIT, MAX_RECURSION_LIMIT_CAP
    from backend.agents.subagents.settings import MAX_SUBAGENTS_CAP, get_max_subagents

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
async def list_agent_tags(current_user: Annotated[dict, Depends(get_agents_user)]):
    """Справочник тегов: поле «Теги» в карточке и «Обязательные теги» у субагентов.

    Стоит до маршрута /{agent_id}, иначе слово tags ушло бы туда как id.
    """
    agent_repo = get_agent_repository()
    if agent_repo is None:
        return {"tags": []}
    return {"tags": await agent_repo.list_tags()}

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
    request: Request,
    agent_data: AgentCreate,
    current_user: Annotated[dict, Depends(get_agents_user)],
):
    """Создание нового агента"""
    try:
        if not user_can_create_agents(current_user):
            role = get_agent_platform_role(current_user)
            log_cef_event(
                "AGT006",
                request=request,
                current_user=current_user,
                status_code=403,
                outcome="failure",
                extra={
                    "cs1": agent_data.name,
                    "cs1Label": "AgentName",
                    "cs2": "-",
                    "cs2Label": "AgentId",
                    "cs3": role or "none",
                    "cs3Label": "PlatformRole",
                    "reason": "platform role cannot create agents",
                },
            )
            raise HTTPException(
                status_code=403, detail="Недостаточно прав для создания агента"
            )
        # Редактор создаёт только приватных агентов; публикация — у owner.
        if agent_data.is_public and not user_can_publish_to_library(current_user):
            agent_data.is_public = False

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
                extra={
                    "cs1": agent_data.name,
                    "cs2": str(agent_id),
                    "cs1Label": "AgentName",
                    "cs2Label": "AgentId",
                },
            )
            return {
                "success": True,
                "agent_id": agent_id,
                "message": "Агент успешно создан",
            }
        else:
            raise HTTPException(status_code=500, detail="Ошибка при создании агента")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка создания агента")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{agent_id}/view", response_model=dict)
async def view_agent(
    agent_id: int, current_user: Annotated[Optional[dict], Depends(get_optional_user)]
):
    """Увеличить счетчик просмотров агента (публичный доступ)"""
    try:
        _require_agents_feature(current_user)
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
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/{agent_id}", response_model=AgentWithTags)
async def get_agent(
    agent_id: int, current_user: Annotated[Optional[dict], Depends(get_optional_user)]
):
    """Получение агента по ID (публичный / автор / получатель шаринга / admin)"""
    try:
        _require_agents_feature(current_user)
        agent_repo = get_agent_repository()
        user_id = current_user["user_id"] if current_user else None
        admin = _is_admin(current_user)
        platform_role: Optional[str] = None
        agent = await agent_repo.get_agent(agent_id, user_id, is_admin=admin)
        if not agent:
            raise HTTPException(status_code=404, detail="Агент не найден")
        if is_agent_platform_role_mapping_enabled():
            if current_user and not admin:
                platform_role = get_agent_platform_role(current_user)
        can_access = await agent_repo.user_can_access_agent(
            agent_id, user_id, is_admin=admin
        )
        if not can_access:
            raise HTTPException(status_code=403, detail="Нет доступа к этому агенту")
        await agent_repo.increment_views(agent_id)
        _apply_platform_permission_cap(agent, current_user, platform_role)
        await _enrich_agents_for_response([agent])
        return agent
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка получения агента")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


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
    min_rating: Annotated[
        Optional[float], Query(ge=0, le=5, description="Минимальный рейтинг")
    ] = None,
    sort_by: Annotated[
        str, Query(description="Поле сортировки (rating, date, views, usage, votes)")
    ] = "rating",
    sort_order: Annotated[
        str, Query(description="Порядок сортировки (asc/desc)")
    ] = "desc",
    page: Annotated[int, Query(ge=1, description="Номер страницы")] = 1,
    limit: Annotated[
        int, Query(ge=1, le=100, description="Количество на странице")
    ] = 20,
):
    """Получение списка агентов с фильтрацией (публичный доступ)"""
    try:
        # Base-пользователь видит вкладку галереи, но не список агентов
        if current_user is not None and not user_can_use_agents(current_user):
            return AgentsResponse(agents=[], total=0, page=page, pages=0)
        logger.debug(
            f"Запрос списка агентов: page={page}, limit={limit}, sort_by={sort_by}, sort_order={sort_order}"
        )
        agent_repo = get_agent_repository()
        tag_ids = None
        if tags:
            try:
                tag_ids = [int(t.strip()) for t in tags.split(",") if t.strip()]
            except ValueError as e:
                raise HTTPException(
                    status_code=400, detail="Неверный формат тегов"
                ) from e
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
        platform_role: Optional[str] = None
        user_id = current_user["user_id"] if current_user else None
        if is_agent_platform_role_mapping_enabled() and current_user:
            platform_role = get_agent_platform_role(current_user)

        agents, total = await agent_repo.get_agents(filters, user_id)
        if is_agent_platform_role_mapping_enabled() and current_user:
            for a in agents:
                _apply_platform_permission_cap(a, current_user, platform_role)
        await _enrich_agents_for_response(agents)
        logger.debug(f"Получено агентов: {len(agents)}, всего: {total}")
        pages = (total + limit - 1) // limit
        return AgentsResponse(agents=agents, total=total, page=page, pages=pages)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        logger.error(f"Ошибка получения списка агентов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.put("/{agent_id}", response_model=dict)
async def update_agent(
    request: Request,
    agent_id: int,
    agent_data: AgentUpdate,
    current_user: Annotated[dict, Depends(get_agents_user)],
):
    """Обновление агента (автор / редактор / admin)"""
    try:
        agent_repo = get_agent_repository()
        existing = await agent_repo.get_agent(
            agent_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        # Автор своей карточки (клон) — полный контроль; чужие — по platform-роли.
        if not _is_agent_author(existing, current_user):
            _require_platform_roles(
                current_user,
                "owner",
                "editor",
                detail="Недостаточно прав для редактирования агента",
            )
        # Публикация/снятие с галереи — только platform owner (не editor).
        if (
            agent_data.is_public is not None
            and bool(agent_data.is_public) != bool(getattr(existing, "is_public", False))
            and not user_can_publish_to_library(current_user)
        ):
            raise HTTPException(
                status_code=403,
                detail="Недостаточно прав для публикации агента в галерее",
            )

        await _reject_foreign_kb_documents(agent_repo, agent_id, agent_data.config)
        success = await agent_repo.update_agent(
            agent_id=agent_id,
            agent_data=agent_data,
            author_id=current_user["user_id"],
            is_admin=_is_admin(current_user),
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
            raise HTTPException(
                status_code=403,
                detail="Недостаточно прав для редактирования этого агента",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка обновления агента")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


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


async def _cleanup_agent_kb_documents(agent_repo, agent_id: int, doc_ids: list) -> None:
    """Удалить осиротевшие KB-документы после удаления агента."""
    if not doc_ids:
        return
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


async def _collect_owned_linked_agent_ids(
    agent_repo, root_id: int, owner_user_id: str
) -> List[int]:
    """Свои агенты из цепочки/субагентов родителя (транзитивно).

    Чужие (галерея/шаринг) не трогаем — только author_id текущего пользователя.
    """
    owner = _norm_uid(owner_user_id)
    ordered: List[int] = []
    seen = {int(root_id)}
    queue: List[int] = [int(root_id)]
    while queue:
        cur = queue.pop(0)
        agent = await agent_repo.get_agent(cur, user_id=None)
        if not agent:
            continue
        cfg = agent.config if isinstance(getattr(agent, "config", None), dict) else {}
        for linked in _extract_linked_agent_ids(cfg):
            if linked in seen or linked <= 0:
                continue
            seen.add(linked)
            linked_agent = await agent_repo.get_agent(linked, user_id=None)
            if not linked_agent:
                continue
            if _norm_uid(getattr(linked_agent, "author_id", None)) != owner:
                continue
            ordered.append(linked)
            queue.append(linked)
            if len(ordered) >= _CLONE_GRAPH_MAX_AGENTS:
                return ordered
    return ordered


@router.delete("/{agent_id}", response_model=dict)
async def delete_agent(
    request: Request,
    agent_id: int,
    current_user: Annotated[dict, Depends(get_agents_user)],
    cascade_linked: Annotated[
        bool,
        Query(
            description=(
                "Удалить также своих агентов из цепочки/субагентов "
                "(типично клоны). Чужие карточки не трогаем."
            )
        ),
    ] = False,
):
    """Удаление агента (автор или admin)."""
    try:
        agent_repo = get_agent_repository()
        admin = _is_admin(current_user)
        existing = await agent_repo.get_agent(
            agent_id, current_user["user_id"], is_admin=admin
        )
        # Удалить можно только свою карточку; platform-owner — ещё и чужие
        # по правилам репозитория. Viewer/editor удаляют лишь свои клоны.
        if not _is_agent_author(existing, current_user):
            _require_platform_roles(
                current_user,
                "owner",
                detail="Недостаточно прав для удаления агента",
            )

        linked_to_delete: List[int] = []
        if cascade_linked and existing:
            linked_to_delete = await _collect_owned_linked_agent_ids(
                agent_repo, int(agent_id), current_user["user_id"]
            )

        # KB storage: проверяем ДО удаления (родитель + связанные).
        agents_for_kb = [existing] if existing else []
        for lid in linked_to_delete:
            la = await agent_repo.get_agent(lid, user_id=None)
            if la:
                agents_for_kb.append(la)
        if any(_extract_kb_document_ids(a) for a in agents_for_kb if a):
            from backend.routes.rag import _require_storage_for_delete

            _require_storage_for_delete()

        deleted_linked = 0
        for lid in linked_to_delete:
            linked_agent = await agent_repo.get_agent(lid, user_id=None)
            linked_docs = _extract_kb_document_ids(linked_agent) if linked_agent else []
            ok_linked = await agent_repo.delete_agent(
                agent_id=lid,
                author_id=current_user["user_id"],
                is_admin=admin,
            )
            if ok_linked:
                deleted_linked += 1
                await _cleanup_agent_kb_documents(agent_repo, lid, linked_docs)
            else:
                logger.warning(
                    "Каскадное удаление: не удалось удалить связанного агента %s",
                    lid,
                )

        success = await agent_repo.delete_agent(
            agent_id=agent_id,
            author_id=current_user["user_id"],
            is_admin=admin,
        )
        if success:
            _name = (existing.name if existing else None) or f"agent-{agent_id}"
            log_cef_event(
                "AGT005",
                request=request,
                current_user=current_user,
                status_code=200,
                extra={
                    "cs1": _name,
                    "cs2": str(agent_id),
                    "cs1Label": "AgentName",
                    "cs2Label": "AgentId",
                    "cs3": (
                        f"cascade_linked={deleted_linked}"
                        if cascade_linked
                        else "cascade_linked=0"
                    ),
                    "cs3Label": "Context",
                },
            )
            await _cleanup_agent_kb_documents(
                agent_repo, agent_id, _extract_kb_document_ids(existing)
            )
            msg = "Агент успешно удалён"
            if deleted_linked:
                msg = (
                    f"Агент удалён вместе с {deleted_linked} "
                    f"связанными агентами цепочки/субагентов"
                )
            return {
                "success": True,
                "message": msg,
                "deleted_linked": deleted_linked,
            }
        else:
            raise HTTPException(
                status_code=403, detail="Недостаточно прав для удаления этого агента"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка удаления агента")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


# Сколько агентов можно утянуть вместе с родителем (цепочка + субагенты, транзитивно).
_CLONE_GRAPH_MAX_AGENTS = 64


def _clone_agent_config(source_config) -> dict:
    """Глубокая копия config без aliasing на исходный dict агента.

    Через json round-trip, чтобы гарантированно не задеть source.config
    (иначе очистка kb_document_ids у копии могла бы стереть файлы у оригинала
    в памяти, а при ошибочном UPDATE — и в БД).
    """
    import json

    cfg = source_config
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except (TypeError, ValueError):
            return {}
    if not isinstance(cfg, dict):
        return {}
    try:
        return json.loads(json.dumps(cfg, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        import copy

        return copy.deepcopy(cfg)


def _parse_int_ids(raw_list) -> List[int]:
    out: List[int] = []
    if not isinstance(raw_list, list):
        return out
    for raw in raw_list:
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            continue
    return out


def _extract_linked_agent_ids(config: Optional[dict]) -> List[int]:
    """id из config.agent_ids (цепочка) и config.subagents.agent_ids."""
    if not isinstance(config, dict):
        return []
    ids = _parse_int_ids(config.get("agent_ids"))
    sub = config.get("subagents")
    if isinstance(sub, dict):
        ids.extend(_parse_int_ids(sub.get("agent_ids")))
    # Уникальные, порядок сохранения
    seen = set()
    ordered: List[int] = []
    for i in ids:
        if i in seen or i <= 0:
            continue
        seen.add(i)
        ordered.append(i)
    return ordered


def _remap_config_agent_refs(config: dict, id_map: dict) -> dict:
    """Переписать ссылки цепочки/субагентов на id клонов. Без маппинга — выкидываем."""
    if not isinstance(config, dict) or not id_map:
        return config

    def _map_list(raw_list) -> List[int]:
        mapped: List[int] = []
        seen = set()
        for raw in _parse_int_ids(raw_list):
            new_id = id_map.get(raw)
            if new_id is None or new_id in seen:
                continue
            seen.add(new_id)
            mapped.append(int(new_id))
        return mapped

    if "agent_ids" in config:
        config["agent_ids"] = _map_list(config.get("agent_ids"))

    sub = config.get("subagents")
    if isinstance(sub, dict):
        sub = dict(sub)
        sub["agent_ids"] = _map_list(sub.get("agent_ids"))
        names = sub.get("agent_names")
        if isinstance(names, dict):
            new_names = {}
            for key, label in names.items():
                try:
                    old_id = int(key)
                except (TypeError, ValueError):
                    continue
                new_id = id_map.get(old_id)
                if new_id is None:
                    continue
                new_names[str(int(new_id))] = label
            sub["agent_names"] = new_names
        config["subagents"] = sub
    return config


async def _collect_clone_graph_ids(agent_repo, root_id: int) -> List[int]:
    """Транзитивное замыкание: родитель + цепочка + субагенты (с защитой от циклов).

    Связанных агентов читаем из БД без ACL: пользователь мог не иметь
    publish/share на них, но клон родителя должен получить полные копии.
    """
    ordered: List[int] = []
    seen = set()
    queue: List[int] = [int(root_id)]
    while queue:
        cur = queue.pop(0)
        if cur in seen or cur <= 0:
            continue
        if len(ordered) >= _CLONE_GRAPH_MAX_AGENTS:
            logger.warning(
                "Клон агента %s: граф обрезан на %s агентах",
                root_id,
                _CLONE_GRAPH_MAX_AGENTS,
            )
            break
        seen.add(cur)
        # Читаем без ACL: get_agent отдаёт строку из БД всегда.
        agent = await agent_repo.get_agent(cur, user_id=None)
        if not agent:
            logger.warning(
                "Клон графа agent=%s: узел %s не найден в БД, пропускаем",
                root_id,
                cur,
            )
            continue
        ordered.append(cur)
        # Всегда через _clone_agent_config: config может прийти строкой JSON.
        cfg = _clone_agent_config(getattr(agent, "config", None))
        linked_ids = _extract_linked_agent_ids(cfg)
        if cur == int(root_id) and not linked_ids:
            logger.info(
                "Клон графа agent=%s: у корня нет agent_ids/subagents.agent_ids",
                root_id,
            )
        for linked in linked_ids:
            if linked not in seen:
                queue.append(linked)
    return ordered


async def _ensure_source_kb_ids_intact(
    agent_repo, source_id: int, expected_kb_ids: List[int]
) -> None:
    """Если у исходника после клона пропали kb_document_ids — вернуть их.

    Защита от регрессий: клон никогда не должен менять файлы оригинала.
    """
    if not expected_kb_ids:
        return
    try:
        fresh = await agent_repo.get_agent(int(source_id), user_id=None)
        if not fresh:
            return
        cfg = _clone_agent_config(getattr(fresh, "config", None))
        current = _parse_int_ids(cfg.get("kb_document_ids"))
        if set(current) >= set(expected_kb_ids):
            return
        logger.error(
            "Клон затронул KB исходника agent=%s: было %s, стало %s — восстанавливаем",
            source_id,
            expected_kb_ids,
            current,
        )
        # Мержим: не затираем новые id, если они уже появились легитимно.
        cfg["kb_document_ids"] = sorted(set(current) | set(expected_kb_ids))
        await agent_repo.update_agent(
            agent_id=int(source_id),
            agent_data=AgentUpdate(config=cfg),
            author_id=str(getattr(fresh, "author_id", "") or ""),
            is_admin=True,
        )
    except Exception:
        logger.exception(
            "Не удалось проверить/восстановить kb_document_ids исходника %s",
            source_id,
        )


async def _materialize_single_agent_clone(
    *,
    agent_repo,
    source,
    current_user: dict,
    owner_uid: str,
    username: str,
) -> Optional[int]:
    """Создать одну карточку-клон (без финального remap цепочки)."""
    source_id = int(source.id)
    # Снимок KB id ДО любых правок config — только из копии.
    config = _clone_agent_config(getattr(source, "config", None))
    source_kb_ids = _parse_int_ids(config.get("kb_document_ids"))
    config["kb_document_ids"] = []
    config["cloned_from"] = source_id

    # AgentCreate требует system_prompt min_length=10 — короткие промпты
    # у субагентов/цепочки не должны ронять весь deep-clone.
    src_prompt = str(getattr(source, "system_prompt", None) or "").strip()
    if len(src_prompt) < 10:
        src_prompt = (src_prompt + " (копия агента)").strip()
        if len(src_prompt) < 10:
            src_prompt = "Склонированный агент"
    src_name = str(getattr(source, "name", None) or "Агент").strip() or "Агент"
    if len(src_name) < 3:
        src_name = f"Агент {source_id}"
    src_desc = getattr(source, "description", None)
    if isinstance(src_desc, str) and len(src_desc) > 1000:
        src_desc = src_desc[:1000]

    create_data = AgentCreate(
        name=f"{src_name} (копия)",
        description=src_desc,
        system_prompt=src_prompt,
        config=config,
        tools=list(source.tools or []),
        is_public=False,
        tag_ids=[
            int(t["id"])
            for t in source.tags or []
            if isinstance(t, dict) and t.get("id")
        ],
        new_tags=[],
    )
    new_agent_id = await agent_repo.create_agent(
        agent_data=create_data,
        author_id=current_user["user_id"],
        author_name=current_user.get("username", "Anonymous"),
    )
    if not new_agent_id:
        return None
    new_agent_id = int(new_agent_id)
    # Никогда не пишем в карточку-источник.
    if new_agent_id == source_id:
        logger.error(
            "Клон агента %s вернул тот же id — прерываем, чтобы не затереть оригинал",
            source_id,
        )
        return None

    await _clone_agent_rag_settings(source_id, new_agent_id, owner_uid)
    new_kb_ids = await _clone_agent_kb_documents(
        source_doc_ids=source_kb_ids,
        new_agent_id=new_agent_id,
        owner_user_id=owner_uid,
        username=str(username),
    )
    config["kb_document_ids"] = new_kb_ids
    # Пока со старыми ссылками chain/subagents — remap сделаем вторым проходом.
    await agent_repo.update_agent(
        agent_id=new_agent_id,
        agent_data=AgentUpdate(config=config),
        author_id=owner_uid,
        is_admin=True,
    )
    await _ensure_source_kb_ids_intact(agent_repo, source_id, source_kb_ids)
    return new_agent_id


def _read_rag_source_bytes(
    object_name: Optional[str], bucket: Optional[str]
) -> Optional[bytes]:
    """Прочитать исходник KB из PVC или MinIO. None — файла нет / не удалось."""
    if not object_name or not bucket:
        return None
    try:
        from backend.storage.rag_pvc import (
            is_rag_pvc_bucket,
            rag_pvc_file_path,
        )

        if is_rag_pvc_bucket(bucket):
            path = rag_pvc_file_path(object_name)
            if not path:
                return None
            with open(path, "rb") as fh:
                return fh.read()
    except Exception:
        logger.exception(
            "Не удалось прочитать PVC-исходник KB object=%s", object_name
        )
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


async def _clone_agent_kb_documents(
    *,
    source_doc_ids: List[int],
    new_agent_id: int,
    owner_user_id: str,
    username: str,
) -> List[int]:
    """Физически клонировать KB-файлы агента (исходник + векторы) под new_agent_id."""
    if not source_doc_ids:
        return []
    from backend.settings.rag_client import get_rag_client
    from backend.storage.rag_pvc import (
        RAG_PVC_BUCKET_MARKER,
        save_rag_bytes_to_pvc,
        use_rag_pvc,
    )

    rag = get_rag_client()
    if rag is None:
        logger.warning(
            "Клон агента %s: RAG недоступен — kb_document_ids очищены",
            new_agent_id,
        )
        return []

    new_ids: List[int] = []
    for src_id in source_doc_ids:
        try:
            info = await rag.kb_get_document(int(src_id))
            if not isinstance(info, dict):
                logger.warning(
                    "Клон KB: документ %s не найден, пропускаем", src_id
                )
                continue
            meta = info.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            filename = str(info.get("filename") or f"document-{src_id}")
            src_object = meta.get("minio_object")
            src_bucket = meta.get("minio_bucket")

            new_object: Optional[str] = None
            new_bucket: Optional[str] = None
            raw = _read_rag_source_bytes(
                str(src_object) if src_object else None,
                str(src_bucket) if src_bucket else None,
            )
            if raw and use_rag_pvc():
                new_object = save_rag_bytes_to_pvc(
                    raw,
                    filename,
                    scope="agent",
                    username=username,
                    prefix="kb_",
                )
                if new_object:
                    new_bucket = RAG_PVC_BUCKET_MARKER
            elif raw and src_bucket and not (
                str(src_bucket).strip() == RAG_PVC_BUCKET_MARKER
            ):
                # MinIO: кладём копию рядом со старым объектом, новый ключ.
                try:
                    import backend.app_state as state
                    import uuid as _uuid

                    client = getattr(state, "minio_client", None)
                    if client:
                        ext = ""
                        if "." in filename:
                            ext = "." + filename.rsplit(".", 1)[-1]
                        new_object = f"kb_clone/{new_agent_id}/{_uuid.uuid4().hex}{ext}"
                        client.upload_file(
                            raw, new_object, bucket_name=str(src_bucket)
                        )
                        new_bucket = str(src_bucket)
                except Exception:
                    logger.exception(
                        "Клон KB: не удалось скопировать MinIO-файл doc=%s",
                        src_id,
                    )
                    new_object = None
                    new_bucket = None

            # Пустые строки = сбросить shared-пути у клона (не делить исходник).
            out = await rag.kb_clone_document(
                int(src_id),
                agent_id=int(new_agent_id),
                owner_user_id=owner_user_id,
                uploaded_by=username,
                minio_object=new_object or "",
                minio_bucket=new_bucket or "",
            )
            if isinstance(out, dict) and out.get("ok") and out.get("document_id"):
                new_ids.append(int(out["document_id"]))
            else:
                logger.warning(
                    "Клон KB: svc-rag не склонировал doc=%s → %s", src_id, out
                )
        except Exception:
            logger.exception(
                "Клон KB: ошибка копирования документа %s для агента %s",
                src_id,
                new_agent_id,
            )
    return new_ids


async def _clone_agent_rag_settings(
    source_agent_id: int, new_agent_id: int, updated_by: str
) -> None:
    """Скопировать entity_rag_settings агента (сырой payload, не дефолты)."""
    try:
        from backend.database.init_db import get_entity_settings_repository

        repo = get_entity_settings_repository()
        if repo is None:
            return
        stored = await repo.get("agent", str(source_agent_id))
        if isinstance(stored, dict) and stored:
            await repo.upsert(
                "agent", str(new_agent_id), dict(stored), updated_by
            )
    except Exception:
        logger.exception(
            "Не удалось скопировать RAG-настройки агента %s → %s",
            source_agent_id,
            new_agent_id,
        )


@router.post("/{agent_id}/duplicate", response_model=dict)
async def duplicate_agent(
    request: Request,
    agent_id: int,
    current_user: Annotated[dict, Depends(get_agents_user)],
):
    """Полное клонирование агента вместе с цепочкой и субагентами.

    Доступно owner/editor/viewer. Клонируется граф: родитель + транзитивно
    все agent_ids / subagents.agent_ids. Связанные агенты копируются даже
    без publish/share на пользователя. Оригиналы не меняются; у клонов
    ссылки переписываются на новые id — пользователь правит только копии.
    """
    try:
        agent_repo = get_agent_repository()
        admin = _is_admin(current_user)
        # Корень — только если пользователь его видит (галерея / share / свой).
        root = await agent_repo.get_agent(agent_id, current_user["user_id"])
        if not root:
            raise HTTPException(status_code=404, detail="Агент не найден")
        can_access = await agent_repo.user_can_access_agent(
            agent_id, current_user["user_id"], is_admin=admin
        )
        if not can_access:
            raise HTTPException(status_code=403, detail="Нет доступа к этому агенту")

        graph_ids = await _collect_clone_graph_ids(agent_repo, int(agent_id))
        if int(agent_id) not in graph_ids:
            graph_ids.insert(0, int(agent_id))

        owner_uid = str(current_user.get("user_id") or "").strip().lower()
        username = (
            current_user.get("username")
            or current_user.get("user_id")
            or "anonymous"
        )

        id_map: dict = {}
        # 1) Материализуем клоны всех узлов графа (KB/RAG/промпт/настройки).
        for src_id in graph_ids:
            src = await agent_repo.get_agent(int(src_id), user_id=None)
            if not src:
                logger.warning(
                    "Клон графа: агент %s не найден, пропускаем", src_id
                )
                continue
            try:
                new_id = await _materialize_single_agent_clone(
                    agent_repo=agent_repo,
                    source=src,
                    current_user=current_user,
                    owner_uid=owner_uid,
                    username=str(username),
                )
            except Exception:
                logger.exception(
                    "Клон графа: ошибка создания копии агента %s", src_id
                )
                new_id = None
            if new_id is None:
                logger.error(
                    "Клон графа: не удалось создать копию агента %s", src_id
                )
                continue
            id_map[int(src_id)] = int(new_id)

        root_new_id = id_map.get(int(agent_id))
        if not root_new_id:
            raise HTTPException(
                status_code=500, detail="Ошибка при дублировании агента"
            )

        # 2) Переписать ссылки цепочки/субагентов на id клонов у каждой копии.
        source_ids = set(id_map.keys())
        for src_id, new_id in id_map.items():
            if int(new_id) in source_ids:
                logger.error(
                    "Клон: new_id=%s совпал с исходником графа — UPDATE пропущен",
                    new_id,
                )
                continue
            cloned = await agent_repo.get_agent(int(new_id), owner_uid, is_admin=True)
            if not cloned:
                continue
            cfg = _clone_agent_config(cloned.config)
            # Сохраняем уже проставленные kb id клона — remap их не трогает.
            kb_keep = _parse_int_ids(cfg.get("kb_document_ids"))
            cfg = _remap_config_agent_refs(cfg, id_map)
            cfg["kb_document_ids"] = kb_keep
            cfg["cloned_from"] = int(src_id)
            await agent_repo.update_agent(
                agent_id=int(new_id),
                agent_data=AgentUpdate(config=cfg),
                author_id=owner_uid,
                is_admin=True,
            )

        log_cef_event(
            "AGT003",
            request=request,
            current_user=current_user,
            status_code=201,
            extra={
                "cs1": root.name,
                "cs2": str(root_new_id),
                "agt_copy_target": f"{root.name} (копия)",
                "cs3": (
                    f"deep-duplicated from {agent_id}; "
                    f"graph={len(id_map)}/{len(graph_ids)}"
                ),
                "cs3Label": "Context",
                "cs1Label": "AgentName",
                "cs2Label": "AgentId",
            },
        )
        return {
            "success": True,
            "agent_id": root_new_id,
            "message": "Агент и связанные агенты цепочки/субагентов склонированы",
            "cloned_agents": len(id_map),
            "cloned_agent_ids": sorted(id_map.values()),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка дублирования агента")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


class RatingRequest(BaseModel):
    """Запрос на оценку агента"""

    rating: int


class RollbackRequest(BaseModel):
    version: int = 0


@router.post("/{agent_id}/rate", response_model=dict)
async def rate_agent(
    agent_id: int,
    rating_request: RatingRequest,
    current_user: Annotated[dict, Depends(get_agents_user)],
):
    """Оценка агента пользователем"""
    try:
        if rating_request.rating < 1 or rating_request.rating > 5:
            raise HTTPException(status_code=400, detail="Рейтинг должен быть от 1 до 5")
        agent_repo = get_agent_repository()
        success = await agent_repo.rate_agent(
            agent_id=agent_id,
            user_id=current_user["user_id"],
            rating=rating_request.rating,
        )
        if success:
            return {"success": True, "message": "Оценка сохранена"}
        else:
            raise HTTPException(status_code=404, detail="Агент не найден")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка оценки агента")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{agent_id}/use", response_model=dict)
async def use_agent(
    request: Request,
    agent_id: int,
    current_user: Annotated[Optional[dict], Depends(get_optional_user)],
):
    """Отметить использование агента"""
    try:
        _require_agents_feature(current_user)
        agent_repo = get_agent_repository()
        usage_success = await agent_repo.increment_usage(agent_id)
        if usage_success:
            ag = await agent_repo.get_agent(
                agent_id, (current_user or {}).get("user_id")
            )
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
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/{agent_id}/stats", response_model=AgentStats)
async def get_agent_stats(
    agent_id: int, current_user: Annotated[Optional[dict], Depends(get_optional_user)]
):
    """Получение статистики агента (публичный доступ)"""
    try:
        _require_agents_feature(current_user)
        agent_repo = get_agent_repository()
        stats = await agent_repo.get_agent_stats(agent_id)
        if not stats:
            raise HTTPException(status_code=404, detail="Агент не найден")
        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка получения статистики")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{agent_id}/rollback", response_model=dict)
async def rollback_agent(
    request: Request,
    agent_id: int,
    payload: RollbackRequest,
    current_user: Annotated[dict, Depends(get_agents_user)],
):
    """Логическая операция rollback (пока без хранения ревизий)."""
    try:
        agent_repo = get_agent_repository()
        source = await agent_repo.get_agent(agent_id, current_user["user_id"])
        if not source:
            raise HTTPException(status_code=404, detail="Агент не найден")
        ok = await agent_repo.update_agent(
            agent_id, AgentUpdate(), current_user["user_id"]
        )
        if not ok:
            raise HTTPException(
                status_code=403, detail="Недостаточно прав для rollback"
            )
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
        return {
            "success": True,
            "message": f"Rollback агента выполнен к версии {payload.version}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка rollback агента")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{agent_id}/bookmark", response_model=dict)
async def add_bookmark(
    agent_id: int, current_user: Annotated[dict, Depends(get_agents_user)]
):
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
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.delete("/{agent_id}/bookmark", response_model=dict)
async def remove_bookmark(
    agent_id: int, current_user: Annotated[dict, Depends(get_agents_user)]
):
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
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/my/bookmarks", response_model=AgentsResponse)
async def get_my_bookmarks(
    current_user: Annotated[dict, Depends(get_agents_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Получение закладок текущего пользователя"""
    try:
        role: Optional[str] = None
        if is_agent_platform_role_mapping_enabled():
            role = get_agent_platform_role(current_user)

        logger.debug(
            f"Запрос закладок пользователя: {current_user.get('user_id', 'unknown')}, page={page}, limit={limit}"
        )
        agent_repo = get_agent_repository()
        bookmark_ids, total = await agent_repo.get_user_bookmarks(
            current_user["user_id"], limit=limit, offset=(page - 1) * limit
        )
        logger.debug(f"Найдено закладок: {total}, IDs: {bookmark_ids}")
        if not bookmark_ids:
            logger.debug("У пользователя нет закладок")
            return AgentsResponse(agents=[], total=0, page=page, pages=0)
        agents = []
        for agent_id in bookmark_ids:
            agent = await agent_repo.get_agent(agent_id, current_user["user_id"])
            # Снятые с публикации не показываем в «Агенты из галереи».
            if agent and getattr(agent, "is_public", False):
                agents.append(agent)
        pages = (total + limit - 1) // limit
        logger.debug(f"Возвращаем {len(agents)} агентов из закладок")
        if is_agent_platform_role_mapping_enabled():
            for a in agents:
                _apply_platform_permission_cap(a, current_user, role)
        await _enrich_agents_for_response(agents)
        return AgentsResponse(agents=agents, total=total, page=page, pages=pages)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка получения закладок")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/admin/all", response_model=AgentsResponse)
async def get_all_agents_for_admin(
    current_user: Annotated[dict, Depends(get_agents_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    search: Annotated[Optional[str], Query(description="Поисковый запрос")] = None,
):
    """Все агенты платформы (включая приватные) — только LDAP/Keycloak admin.

    Клиент может листать page=1..N; жёсткого потолка по общему числу агентов нет.
    """
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    try:
        agent_repo = get_agent_repository()
        filters = AgentFilters(
            search_query=search,
            sort_by="date",
            sort_order="desc",
            limit=limit,
            offset=(page - 1) * limit,
        )
        agents, total = await agent_repo.get_agents(
            filters, current_user["user_id"], is_admin=True
        )
        for a in agents:
            a.my_permission = "owner"
        await _enrich_agents_for_response(agents)
        pages = (total + limit - 1) // limit if total else 0
        return AgentsResponse(agents=agents, total=total, page=page, pages=pages)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка получения всех агентов (admin)")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/my/agents", response_model=AgentsResponse)
async def get_my_agents(
    current_user: Annotated[dict, Depends(get_agents_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Получение агентов текущего пользователя"""
    try:
        role: Optional[str] = None
        if is_agent_platform_role_mapping_enabled():
            role = get_agent_platform_role(current_user)

        agent_repo = get_agent_repository()
        filters = AgentFilters(
            author_id=(current_user.get("user_id") or "").strip().lower(),
            author_only=True,
            sort_by="date",
            sort_order="desc",
            limit=limit,
            offset=(page - 1) * limit,
        )
        agents, total = await agent_repo.get_agents(filters, current_user["user_id"])
        # Свои карточки (в т.ч. клоны) — без platform-cap: автор = owner.
        if is_agent_platform_role_mapping_enabled():
            for a in agents:
                _apply_platform_permission_cap(a, current_user, role)
        await _enrich_agents_for_response(agents)
        pages = (total + limit - 1) // limit
        return AgentsResponse(agents=agents, total=total, page=page, pages=pages)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка получения моих агентов")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/my/shared", response_model=AgentsResponse)
async def get_shared_with_me(
    current_user: Annotated[dict, Depends(get_agents_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Агенты, которыми поделились с текущим пользователем"""
    try:
        role: Optional[str] = None
        if is_agent_platform_role_mapping_enabled():
            role = get_agent_platform_role(current_user)

        agent_repo = get_agent_repository()
        agents, total = await agent_repo.get_shared_with_me(
            current_user["user_id"], limit=limit, offset=(page - 1) * limit
        )
        pages = (total + limit - 1) // limit if total else 0
        if is_agent_platform_role_mapping_enabled():
            for a in agents:
                _apply_platform_permission_cap(a, current_user, role)
        await _enrich_agents_for_response(agents)
        return AgentsResponse(agents=agents, total=total, page=page, pages=pages)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка получения расшаренных агентов")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{agent_id}/share", response_model=dict)
async def share_agent(
    request: Request,
    agent_id: int,
    payload: AgentShareRequest,
    current_user: Annotated[dict, Depends(get_agents_user)],
):
    """Поделиться агентом с пользователями (только автор)"""
    try:
        agent_repo = get_agent_repository()
        existing = await agent_repo.get_agent(agent_id, current_user["user_id"])
        if not existing:
            raise HTTPException(status_code=404, detail="Агент не найден")
        # Автор своего клона может шарить его; чужие — только platform-owner.
        if not _is_agent_author(existing, current_user):
            # Без mapping только автор/admin; иначе _require_platform_roles — no-op.
            if not is_agent_platform_role_mapping_enabled():
                raise HTTPException(
                    status_code=403, detail="Недостаточно прав для шаринга агента"
                )
            _require_platform_roles(
                current_user,
                "owner",
                detail="Недостаточно прав для шаринга агента",
            )

        prepared = await prepare_share_recipients(payload.usernames, payload.permission)
        usernames_to_share = [uid for uid, _ in prepared.to_share]
        recipient_permissions = {uid: perm for uid, perm in prepared.to_share}

        # owner_id в agent_shares — автор карточки; админ/platform-owner
        # шарит от имени автора, иначе репозиторий отклоняет чужой ресурс.
        share_owner_id = existing.author_id or current_user["user_id"]
        added, skipped = await agent_repo.share_agent(
            agent_id=agent_id,
            owner_id=share_owner_id,
            usernames=usernames_to_share,
            permission=payload.permission,
            recipient_permissions=recipient_permissions,
        )
        response = build_share_api_response(
            prepared,
            added,
            skipped,
            resource_label="Агент",
        )
        if not added and (prepared.skipped or skipped):
            raise HTTPException(
                status_code=403,
                detail=response.get("message") or "Не удалось поделиться агентом",
            )
        _name = (existing.name if existing else None) or f"agent-{agent_id}"
        for _duser in added:
            _perm = recipient_permissions.get(_duser, "viewer")
            _cef_role = "agent_editor" if _perm == "editor" else "agent_viewer"
            log_cef_event(
                "SEC004",
                request=request,
                current_user=current_user,
                status_code=200,
                extra={
                    "cs1": _name,
                    "cs1Label": "AgentName",
                    "cs2": str(agent_id),
                    "cs2Label": "AgentId",
                    "cs3": _cef_role,
                    "cs3Label": "AccessRole",
                    "duser": _duser,
                },
            )
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка шаринга агента")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/{agent_id}/shares", response_model=AgentSharesResponse)
async def list_agent_shares(
    agent_id: int, current_user: Annotated[dict, Depends(get_agents_user)]
):
    """Список доступа к агенту: владелец (создатель) + получатели, с ФИО."""
    try:
        agent_repo = get_agent_repository()
        admin = _is_admin(current_user)
        existing = await agent_repo.get_agent(
            agent_id, current_user["user_id"], is_admin=admin
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Агент не найден")
        if not _is_agent_author(existing, current_user):
            raise HTTPException(
                status_code=403,
                detail="Только автор может просматривать список шарингов",
            )
        me = (current_user["user_id"] or "").strip().lower()

        raw_shares = await agent_repo.list_agent_shares(
            agent_id, existing.author_id or current_user["user_id"]
        )

        owner_id = existing.author_id or current_user["user_id"]
        name_map = await resolve_full_names(
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
                permission=(
                    "editor"
                    if (s.permission or "").strip().lower() == "editor"
                    else "viewer"
                ),
            )
            for s in raw_shares
        ]
        return AgentSharesResponse(owner=owner_entry, shares=share_entries)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка списка шарингов")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.delete("/{agent_id}/share/{username}", response_model=dict)
async def unshare_agent(
    request: Request,
    agent_id: int,
    username: str,
    current_user: Annotated[dict, Depends(get_agents_user)],
):
    """Снять доступ: автор/админ/platform-owner отзывает или получатель выходит."""
    try:
        agent_repo = get_agent_repository()
        existing = await agent_repo.get_agent(
            agent_id, current_user["user_id"], is_admin=_is_admin(current_user)
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Агент не найден")

        me = _norm_uid(current_user.get("user_id"))
        target = _norm_uid(username)
        # Получатель может выйти сам; иначе — автор / админ / platform-owner.
        # actor_id для репозитория — author_id карточки (админ не автор в БД).
        if me != target:
            if not _is_agent_author(existing, current_user):
                _require_platform_roles(
                    current_user,
                    "owner",
                    detail="Недостаточно прав для снятия шаринга",
                )
            actor_id = existing.author_id or current_user["user_id"]
        else:
            actor_id = current_user["user_id"]

        success = await agent_repo.unshare_agent(
            agent_id=agent_id,
            actor_id=actor_id,
            target_user_id=username,
        )
        if success:
            _name = (existing.name if existing else None) or f"agent-{agent_id}"
            log_cef_event(
                "SEC004",
                request=request,
                current_user=current_user,
                status_code=200,
                extra={
                    "cs1": _name,
                    "cs1Label": "AgentName",
                    "cs2": str(agent_id),
                    "cs2Label": "AgentId",
                    "cs3": "revoked",
                    "cs3Label": "AccessRole",
                    "duser": username,
                },
            )
            return {"success": True, "message": f"Доступ для {username} снят"}
        raise HTTPException(status_code=403, detail="Не удалось снять доступ")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка снятия шаринга")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
