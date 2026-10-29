"""
routes/rag.py - настройки RAG, База Знаний (KB), библиотека памяти (memory-rag)
"""

import os
import time
from typing import Annotated, Optional
import asyncio
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

import backend.app_state as state
from backend.app_state import (
    minio_client,
    rag_client,
    rag_models_client,
    settings,
    get_library_chunk_index_params,
    get_rag_chunk_index_params,
)
from backend.auth.jwt_handler import get_current_user
from backend.rag_query.semantic_cache import bump_rag_semantic_cache
from backend.schemas import RAGSettings, RagModelSelectRequest
from backend.services.user_rag_settings import (
    INDEX_AFFECTING_KEYS,
    chunk_params_from_rag_settings,
    default_rag_settings_snapshot,
    get_entity_rag_settings,
    get_user_memory_strategy,
    index_params_changed,
    normalize_entity_id,
    normalize_scope,
    reset_entity_rag_settings,
    save_entity_rag_settings,
    save_user_memory_strategy,
    settings_response_dict,
)
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress
from backend.settings.service_toggles import require_service
from backend.realtime.rag_evidence import build_reindex_status_message
from backend.storage.rag_pvc import (
    RAG_PVC_BUCKET_MARKER,
    RAG_PVC_DIR_ENV,
    RagPvcUnavailable,
    delete_rag_pvc_file,
    is_rag_pvc_bucket,
    require_rag_pvc_available,
    save_rag_bytes_to_pvc,
    use_rag_pvc,
)
from backend.utils.http_errors import UPSTREAM_ERROR_DETAIL, public_error_detail

logger = get_logger(__name__)


def _model_row_from_path(model_path: str, kind: str) -> dict:
    path = str(model_path or "").strip()
    name = path.split("/")[-1] if path else ""
    source = path.split("/")[0] if "/" in path else "local"
    return {
        "path": path,
        "name": name,
        "display_name": name,
        "source": source,
        "kind": kind,
    }


def _overlay_user_model_current(data: dict, user_rag: dict) -> dict:
    """В UI current — модель выбранной сущности, не runtime кластера.

    Если у сущности путь пуст — показываем cluster_default (ConfigMap на старте
    svc-rag-models), а не live current после чужого /models/select.
    """
    out = dict(data or {})
    cluster = dict(out.get("cluster_default") or out.get("current") or {})
    current = dict(cluster)
    saved_emb = str(user_rag.get("rag_embedding_model_path") or "").strip()
    saved_rer = str(user_rag.get("rag_reranker_model_path") or "").strip()
    if saved_emb:
        current["embedding"] = _model_row_from_path(saved_emb, "embedding")
    if saved_rer:
        current["reranker"] = _model_row_from_path(saved_rer, "reranker")
    out["current"] = current
    out["cluster_default"] = cluster
    return out


def _allowed_embedding_models() -> list:
    """Белый список эмбеддеров из ENV. Пусто = разрешены все (поведение по умолчанию)."""
    raw = (os.getenv("RAG_EMBEDDING_MODELS_ALLOWED", "") or "").replace(";", ",")
    return [m.strip().lower() for m in raw.split(",") if m.strip()]

async def _validate_local_model_path(model_type: str, model_path: str) -> None:
    data = await rag_models_client.list_models(model_type)
    rows = (data.get("models") or {}).get(model_type) or []
    paths = {str((r or {}).get("path") or "").strip() for r in rows}
    if model_path not in paths:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Модель {model_path!r} не найдена в каталоге. "
                "Доступны только папки из RAG_MODELS_DIR / ConfigMap RAG**_MODEL*."
            ),
        )
    # Гард по памяти: модель есть на диске, но её загрузка положит под моделей.
    # Проверяем ДО выбора — иначе падение случится в фоне, без сообщения пользователю.
    allowed = _allowed_embedding_models()
    if model_type == "embedding" and allowed:
        name = model_path.split("/")[-1].strip()
        if name.lower() not in allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Модель {name!r} сейчас недоступна для выбора: она не помещается "
                    f"в память сервиса моделей. Доступны: {', '.join(sorted(allowed))}."
                ),
            )

def _rag_upload_username(current_user: dict) -> str:
    return current_user.get("username") or current_user.get("user_id") or "anonymous"


async def _raise_if_kb_duplicate(
    content: bytes,
    filename: str,
    *,
    agent_id: Optional[int] = None,
    knowledge_base_id: Optional[int] = None,
) -> None:
    """Блок до PVC/MinIO: одинаковый файл в KB агента / базы знаний."""
    from backend.rag_query.document_duplicate import (
        DUPLICATE_MESSAGE,
        file_content_hash,
        raise_duplicate_http,
    )

    if not rag_client:
        return
    digest = file_content_hash(content)
    try:
        out = await rag_client.kb_check_duplicate(
            content_hash=digest,
            filename=filename,
            size=len(content),
            agent_id=agent_id,
            knowledge_base_id=knowledge_base_id,
        )
    except Exception:
        logger.exception("KB duplicate-check failed; продолжаем загрузку")
        return
    if isinstance(out, dict) and out.get("duplicate"):
        raise_duplicate_http(
            document_id=out.get("document_id"),
            filename=out.get("filename") or filename,
            message=DUPLICATE_MESSAGE,
        )


async def _raise_if_memory_duplicate(content: bytes, filename: str) -> None:
    from backend.rag_query.document_duplicate import (
        DUPLICATE_MESSAGE,
        file_content_hash,
        raise_duplicate_http,
    )

    if not rag_client:
        return
    digest = file_content_hash(content)
    try:
        out = await rag_client.memory_rag_check_duplicate(
            content_hash=digest,
            filename=filename,
            size=len(content),
        )
    except Exception:
        logger.exception("Memory RAG duplicate-check failed; продолжаем загрузку")
        return
    if isinstance(out, dict) and out.get("duplicate"):
        raise_duplicate_http(
            document_id=out.get("document_id"),
            filename=out.get("filename") or filename,
            message=DUPLICATE_MESSAGE,
        )


def _require_storage_for_delete() -> None:
    """503, если исходники в PVC, а том недоступен.

    Зовётся ДО обращения к SVC-RAG: удаление записи о документе и удаление
    самого файла — две разные системы.
    """
    try:
        require_rag_pvc_available()
    except RagPvcUnavailable as exc:
        logger.error("Удаление отменено: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _delete_rag_source_file(object_name: Optional[str], bucket: Optional[str]) -> None:
    """Удаляет исходник из PVC или MinIO по metadata документа."""
    if not object_name or not bucket:
        return
    if is_rag_pvc_bucket(bucket):
        delete_rag_pvc_file(object_name, bucket)
        return
    if minio_client:
        try:
            minio_client.delete_file(object_name, bucket_name=bucket)
        except Exception:
            logger.exception("MinIO delete RAG source object=%s bucket=%s", object_name, bucket)


router = APIRouter(tags=["rag"])
_VALID_STRATEGIES = {"auto", "hierarchical", "hybrid", "vector", "lexical", "raw_cosine", "graph"}
_VALID_CHUNKING_STRATEGIES = {"hierarchical", "fixed", "markdown", "separators", "semantic"}

# Что сейчас пересобирается: {"agent:17": {"scope","entity_id","name",...}}.
# Пересборка идёт для ВСЕХ, кому сущность расшарена, поэтому и плашка не
# пер-юзерная, как раньше, а пер-сущностная — с именем агента или проекта.
_active_reindex: dict[str, dict] = {}
_cluster_reindex_active: bool = False
# Когда флаги подняли. svc-rag отвечает "started" ДО того, как возьмёт слот, —
# без форы статус погасил бы плашку в первую же секунду.
_reindex_flags_started_at: float = 0.0
REINDEX_FLAG_GRACE_SECONDS = 60.0

def _mark_reindex_started() -> None:
    global _reindex_flags_started_at
    import time

    _reindex_flags_started_at = time.monotonic()

def _reindex_grace_active() -> bool:
    import time

    return (time.monotonic() - _reindex_flags_started_at) < REINDEX_FLAG_GRACE_SECONDS


def _norm_uid(user_id: Optional[str]) -> str:
    return str(user_id or "").strip().lower()


def _reindex_key(scope: Optional[str], entity_id: Optional[str]) -> str:
    sc = normalize_scope(scope)
    eid = str(entity_id or "").strip()
    return f"{sc}:{eid}" if eid else sc


async def _entity_display_name(scope: str, entity_id: str) -> Optional[str]:
    """Имя агента, проекта или базы знаний для плашки. Не нашли — покажем id."""
    sc = normalize_scope(scope)
    eid = str(entity_id or "").strip()
    if not eid:
        return None
    try:
        if sc == "agent":
            from backend.database.init_db import get_agent_repository

            repo = get_agent_repository()
            agent = await repo.get_agent(int(eid)) if repo else None
            return (getattr(agent, "name", None) or "").strip() or None
        if sc == "kb":
            from backend.database.init_db import get_knowledge_base_repository

            repo = get_knowledge_base_repository()
            kb = await repo.get_by_id(int(eid), is_admin=True) if repo else None
            return (getattr(kb, "name", None) or "").strip() or None
        from backend.database.init_db import get_project_repository

        repo = get_project_repository()
        if repo is None:
            return None
        async with await repo.db_connection.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name FROM user_projects WHERE id = $1", eid
            )
        return (row["name"] or "").strip() if row else None
    except Exception:
        logger.debug("Имя сущности %s:%s недоступно", sc, eid, exc_info=True)
        return None


def _mark_entity_reindex(
    scope: Optional[str],
    entity_id: Optional[str],
    *,
    active: bool,
    name: Optional[str] = None,
) -> None:
    """Отметить сущность как пересобираемую. Без entity_id ничего не отмечаем:
    пересборку «всего» показывает кластерный флаг."""
    eid = str(entity_id or "").strip()
    if not eid:
        return
    key = _reindex_key(scope, eid)
    if active:
        _active_reindex[key] = {
            "scope": normalize_scope(scope),
            "entity_id": eid,
            "name": name,
        }
    else:
        _active_reindex.pop(key, None)


def _is_upstream_httpx_timeout(exc: BaseException) -> bool:
    seen = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, httpx.TimeoutException):
            return True
        cur = cur.__cause__
    return False


# Текст для пользователя, когда SVC-RAG не ответил за отведённое время.
# Важное слово здесь - «продолжается»: индексация не отменяется оттого, что мы
# перестали ждать, и документ обычно появляется в списке через минуту-другую.
_INDEX_TIMEOUT_DETAIL = (
    "SVC-RAG не ответил за отведённое время, но индексация продолжается. "
    "Файл сохранён; документ обычно появляется в списке в течение нескольких "
    "минут — обновите список. Если не появился, загрузите файл заново. "
    "Порог ожидания: SVC_RAG_INDEX_READ_TIMEOUT (секунды), по умолчанию 900."
)

def _cleanup_rag_source_after_failure(
    exc: BaseException, object_name: Optional[str], bucket: Optional[str]
) -> bool:
    """Убрать исходник после неудачной индексации. Возвращает True, если это таймаут.

    На таймауте исходник НЕ удаляем. Мы перестали ждать - это не значит, что
    SVC-RAG перестал работать: 23.09 ответ пришёл на 49 секунд позже нашего
    порога, и к тому моменту документ с 61 вектором уже лежал в базе. Удалив
    файл, мы получили бы проиндексированный документ без исходника - его не
    открыть и не перечанковать, а в списке он есть.

    Цена решения: если SVC-RAG всё-таки упадёт после нашего таймаута, в
    хранилище останется файл без документа. Это лечится повторной загрузкой и
    видно снаружи, в отличие от документа без файла.
    """
    if _is_upstream_httpx_timeout(exc):
        logger.warning(
            "SVC-RAG не ответил за таймаут индексации: исходник %s оставлен в "
            "хранилище - индексация могла дойти до конца уже после того, как мы "
            "перестали ждать",
            object_name or "(не сохранялся)",
        )
        return True
    if object_name and bucket:
        with logged_suppress(logger):
            _delete_rag_source_file(object_name, bucket)
    return False


def _rag_settings_response_dict() -> dict:
    """Дефолты кластера (settings.json / env) — seed для новых пользователей."""
    return settings_response_dict(default_rag_settings_snapshot())


def _entity_id_from_params(
    scope: Optional[str],
    project_id: Optional[str] = None,
    agent_id: Optional[int] = None,
    knowledge_base_id: Optional[int] = None,
) -> Optional[str]:
    sc = normalize_scope(scope)
    if sc == "project":
        pid = str(project_id or "").strip()
        return pid or None
    if sc == "agent" and agent_id is not None:
        try:
            aid = int(agent_id)
        except (TypeError, ValueError):
            return None
        return str(aid) if aid > 0 else None
    if sc == "kb" and knowledge_base_id is not None:
        try:
            kid = int(knowledge_base_id)
        except (TypeError, ValueError):
            return None
        return str(kid) if kid > 0 else None
    return None


def _entity_label(scope: str, entity_name: Optional[str], entity_id: Optional[str]) -> str:
    name = str(entity_name or "").strip()
    if name:
        return f"«{name}»"
    eid = str(entity_id or "").strip()
    return eid or "?"


async def _entity_permission(
    scope: str,
    entity_id: str,
    user_id: str,
    *,
    is_admin: bool = False,
) -> Optional[str]:
    """Роль пользователя для сущности: owner | editor | viewer | None.

    Агент: владелец, получатель шаринга, LDAP/Keycloak admin или (для публичного) читатель.
    Проект: своими проектами владеет один человек, чужих он не видит.
    """
    sc = normalize_scope(scope)
    ek = normalize_entity_id(entity_id)
    uid = (user_id or "").strip().lower()
    if not ek or not uid:
        return None
    if sc == "agent":
        try:
            from backend.database.init_db import get_agent_repository

            repo = get_agent_repository()
            if repo is None:
                return None
            return await repo.get_user_permission(int(ek), uid, is_admin=is_admin)
        except (TypeError, ValueError):
            return None
        except Exception:
            logger.exception("Не удалось определить роль для агента %s", ek)
            return None
    if sc == "kb":
        try:
            from backend.database.init_db import get_knowledge_base_repository

            repo = get_knowledge_base_repository()
            if repo is None:
                return None
            return await repo.get_user_permission(int(ek), uid, is_admin=is_admin)
        except (TypeError, ValueError):
            return None
        except Exception:
            logger.exception("Не удалось определить роль для базы знаний %s", ek)
            return None
    try:
        from backend.database.init_db import get_project_repository

        repo = get_project_repository()
        if repo is None:
            return None
        project = await repo.get_by_id(uid, ek)
        return "owner" if project else None
    except Exception:
        logger.exception("Не удалось определить роль для проекта %s", ek)
        return None


def _can_edit(permission: Optional[str]) -> bool:
    """Менять настройки могут владелец и редактор — у обоих правка видна всем."""
    return (permission or "").strip().lower() in ("owner", "editor")


async def _require_entity_editor(
    scope: str,
    entity_id: str,
    user_id: str,
    *,
    is_admin: bool = False,
) -> str:
    """403, если у пользователя нет права менять настройки этой сущности."""
    permission = await _entity_permission(
        scope, entity_id, user_id, is_admin=is_admin
    )
    if not _can_edit(permission):
        kind = "агента" if normalize_scope(scope) == "agent" else "проекта"
        raise HTTPException(
            status_code=403,
            detail=f"Недостаточно прав для изменения настроек {kind}",
        )
    return permission or "owner"


@router.get("/api/rag/settings")
async def get_rag_settings(
    current_user: Annotated[dict, Depends(get_current_user)],
    scope: Optional[str] = None,
    project_id: Optional[str] = None,
    agent_id: Optional[int] = None,
    knowledge_base_id: Optional[int] = None,
):
    """RAG-настройки конкретного агента, проекта или базы знаний.

    ```scope``` — project (по умолчанию), agent или kb.
    ```project_id``` / ```agent_id``` / ```knowledge_base_id``` — чьи именно настройки.
    Без них отдаём дефолты кластера: настройки принадлежат сущности,
    «настроек вообще» больше нет.

    ```can_edit``` — может ли текущий пользователь их менять. Читателю
    расшаренного агента настройки показываем (иначе непонятно, почему поиск ведёт
    себя так), но правку отбиваем.

    Memory RAG (кроме стратегии поиска) — только ConfigMap/env SVC-RAG / SVC-RAG-MODELS.
    """
    user_id = str(current_user.get("user_id") or "").strip()
    admin = bool(current_user.get("is_admin"))
    sc = normalize_scope(scope)
    entity_id = _entity_id_from_params(
        sc, project_id, agent_id, knowledge_base_id
    )
    if entity_id:
        settings_data = await get_entity_rag_settings(sc, entity_id)
        permission = await _entity_permission(
            sc, entity_id, user_id, is_admin=admin
        )
    else:
        settings_data = default_rag_settings_snapshot()
        # Новая сущность ещё не создана: показываем дефолты и даём их править —
        # сохранятся они уже под конкретным id.
        permission = "owner"
    return {
        **settings_response_dict(settings_data),
        "scope": sc,
        "project_id": project_id if sc == "project" else None,
        "agent_id": agent_id if sc == "agent" else None,
        "knowledge_base_id": knowledge_base_id if sc == "kb" else None,
        "rag_memory_strategy": await get_user_memory_strategy(user_id),
        "permission": permission,
        "can_edit": _can_edit(permission),
    }


def _build_reindex_status_message(
    *,
    memory_reindexing: bool,
    project_reindexing: bool,
    kb_reindexing: bool,
    active: Optional[list] = None,
) -> str:
    return build_reindex_status_message(
        memory_reindexing=memory_reindexing,
        project_reindexing=project_reindexing,
        kb_reindexing=kb_reindexing,
        active=active,
    )


async def _resolve_agent_has_kb_documents(agent_id: int, user_id: Optional[str]) -> bool:
    """Агент реально использует KB-RAG: file_search + id документов, существующих в сторе."""
    from backend.realtime.helpers import _resolve_agent_chat_params

    profile = await _resolve_agent_chat_params(agent_id, user_id)
    if not bool(profile.get("file_search_enabled")):
        return False
    kb_ids = profile.get("kb_document_ids") or []
    if not isinstance(kb_ids, list) or len(kb_ids) == 0:
        return False
    if not rag_client:
        return False
    try:
        rows = list(await rag_client.kb_list_documents() or [])
    except Exception:
        logger.warning("[RAG] kb_list_documents failed for reindex-status", exc_info=True)
        return len(kb_ids) > 0
    existing = {int(d.get("id")) for d in rows if d.get("id") is not None}
    return any(int(doc_id) in existing for doc_id in kb_ids)


async def _resolve_project_has_rag_documents(project_id: str) -> bool:
    if not rag_client or not project_id:
        return False
    try:
        docs = list(await rag_client.project_rag_list_documents(project_id) or [])
    except Exception:
        logger.warning("[RAG] project_rag_list_documents failed for reindex-status", exc_info=True)
        return False
    return len(docs) > 0


@router.get("/api/rag/reindex-status")
async def get_rag_reindex_status(
    current_user: Annotated[dict, Depends(get_current_user)],
    agent_id: Optional[int] = None,
    project_id: Optional[str] = None,
):
    """Агрегированный статус перечанковки RAG для UI (плашка и стоппер).

    Здесь же уезжает 'memory_rag_enabled': по нему
    интерфейс гасит тумблер Библиотеки, когда стор выключен в ConfigMap
    """
    from backend.realtime.rag_evidence import memory_rag_enabled

    memory_enabled = memory_rag_enabled()
    empty_payload = {
        "memory": {"reindexing": False},
        "project": {"reindexing": False},
        "kb": {"reindexing": False},
        "any_reindexing": False,
        "active": [],
        "agent_has_kb": False,
        "project_has_documents": False,
        "memory_rag_enabled": memory_enabled,
        "message": "",
    }
    if not rag_client:
        return empty_payload
    global _cluster_reindex_active
    try:
        status = await rag_client.get_reindex_status()
    except Exception as exc:
        logger.warning("[RAG] reindex-status poll failed: %s", exc)
        return empty_payload

    # Источник истины о том, идёт ли работа, — svc-rag, а не наш реестр: backend
    # узнаёт только момент ПОСТАНОВКИ задачи, не её завершение.
    svc_busy = any(
        bool((status.get(key) or {}).get("reindexing"))
        for key in ("kb", "project", "memory")
    )
    if not svc_busy and not _reindex_grace_active():
        if _cluster_reindex_active:
            logger.info("[REINDEX] svc-rag освободился — снимаю кластерный флаг")
            _cluster_reindex_active = False
        if _active_reindex:
            logger.info("[REINDEX] svc-rag освободился — чищу реестр пересборок")
            _active_reindex.clear()

    user_id = _norm_uid(current_user.get("user_id") if current_user else None)
    agent_has_kb = False
    if agent_id is not None:
        agent_has_kb = await _resolve_agent_has_kb_documents(agent_id, user_id or None)

    project_has_documents = False
    if project_id:
        project_has_documents = await _resolve_project_has_rag_documents(str(project_id).strip())

    # Memory — общий стор: флаг виден всем (ConfigMap / cluster).
    memory_flag = bool(status.get("memory", {}).get("reindexing")) or _cluster_reindex_active
    active = [dict(v) for v in _active_reindex.values()]
    if _cluster_reindex_active:
        # Кластерный прогон перетряхивает всё — показываем его всем.
        project_flag = bool(status.get("project", {}).get("reindexing"))
        kb_flag = bool(status.get("kb", {}).get("reindexing"))
    else:
        # Пересобирается сущность, а не «мои документы»: плашка нужна всем, кто с
        # ней работает, включая читателей расшаренного агента.
        project_flag = any(a["scope"] == "project" for a in active)
        kb_flag = any(a["scope"] == "agent" for a in active)
    message = _build_reindex_status_message(
        memory_reindexing=memory_flag,
        project_reindexing=project_flag,
        kb_reindexing=kb_flag,
        active=active,
    )
    return {
        "memory": {"reindexing": memory_flag},
        "project": {"reindexing": project_flag},
        "kb": {"reindexing": kb_flag},
        "any_reindexing": memory_flag or project_flag or kb_flag,
        "active": active,
        "agent_has_kb": agent_has_kb,
        "project_has_documents": project_has_documents,
        "memory_rag_enabled": memory_enabled,
        "message": message,
    }


@router.post("/api/rag/settings/reset")
async def reset_rag_settings(
    current_user: Annotated[dict, Depends(get_current_user)],
    scope: Optional[str] = None,
    project_id: Optional[str] = None,
    agent_id: Optional[int] = None,
    knowledge_base_id: Optional[int] = None,
):
    """Сброс настроек агента, проекта или базы знаний к дефолтам кластера.

    Сбрасывает не «мои настройки», а настройки СУЩНОСТИ — то есть у всех, кому
    её расшарили. Поэтому нужны права редактора и обязателен
    ```project_id``` / ```agent_id``` / ```knowledge_base_id```.
    """
    try:
        user_id = str(current_user.get("user_id") or "").strip()
        if not user_id:
            raise HTTPException(status_code=401, detail="Требуется авторизация")
        sc = normalize_scope(scope)
        entity_id = _entity_id_from_params(
            sc, project_id, agent_id, knowledge_base_id
        )
        if not entity_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Укажите project_id, agent_id или knowledge_base_id: "
                    "настройки принадлежат сущности"
                ),
            )
        await _require_entity_editor(
            sc, entity_id, user_id, is_admin=bool(current_user.get("is_admin"))
        )
        logger.info(
            "[RAG-CFG] сброс настроек %s %s пользователем %s",
            "агента" if sc == "agent" else "проекта",
            entity_id,
            user_id,
        )
        merged = await reset_entity_rag_settings(sc, entity_id)
        bump_rag_semantic_cache()
        return {
            "message": "Настройки RAG сброшены",
            "success": True,
            **settings_response_dict(merged),
            "scope": sc,
            "can_edit": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=str(e)) from e


def _rechunk_on_settings_change() -> bool:
    v = os.getenv("RAG_RECHUNK_ON_SETTINGS_CHANGE", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _reindex_on_model_change() -> bool:
    # Дефолт true: после смены embedding-модели вектора стёрты миграцией,
    # без реиндекса поиск мёртв до ручного перезалива
    v = os.getenv("RAG_REINDEX_ON_MODEL_CHANGE", "true").strip().lower()
    return v in ("1", "true", "yes", "on")


async def _collect_owner_agent_kb_document_ids(owner_user_id: str) -> list:
    """document_id из config агентов, где user — author (владелец)."""
    oid = (owner_user_id or "").strip().lower()
    if not oid:
        return []
    try:
        from backend.database.init_db import get_agent_repository
        from backend.database.postgresql.agent_models import AgentFilters

        repo = get_agent_repository()
        agents, _total = await repo.get_agents(
            AgentFilters(author_id=oid, author_only=True, limit=100, offset=0),
            user_id=oid,
        )
        out: list = []
        seen = set()
        for agent in agents or []:
            cfg = getattr(agent, "config", None) or {}
            if isinstance(cfg, str):
                import json

                try:
                    cfg = json.loads(cfg)
                except Exception:
                    cfg = {}
            if not isinstance(cfg, dict):
                continue
            for raw in cfg.get("kb_document_ids") or []:
                try:
                    doc_id = int(raw)
                except (TypeError, ValueError):
                    continue
                if doc_id not in seen:
                    seen.add(doc_id)
                    out.append(doc_id)
        return out
    except Exception:
        logger.exception("Не удалось собрать kb_document_ids владельца %s", oid)
        return []


async def _collect_agent_kb_document_ids(agent_id: int) -> list:
    """document_id из config одного агента."""
    try:
        aid = int(agent_id)
    except (TypeError, ValueError):
        return []
    if aid <= 0:
        return []
    try:
        from backend.database.init_db import get_agent_repository

        repo = get_agent_repository()
        if repo is None:
            return []
        agent = await repo.get_agent(aid)
        if not agent:
            return []
        cfg = getattr(agent, "config", None) or {}
        if isinstance(cfg, str):
            import json

            try:
                cfg = json.loads(cfg)
            except Exception:
                cfg = {}
        if not isinstance(cfg, dict):
            return []
        out: list = []
        for raw in cfg.get("kb_document_ids") or []:
            try:
                out.append(int(raw))
            except (TypeError, ValueError):
                continue
        return out
    except Exception:
        logger.exception("Не удалось собрать kb_document_ids агента %s", agent_id)
        return []


async def _collect_knowledge_base_document_ids(knowledge_base_id: int) -> list:
    """Документы user KB по metadata.knowledge_base_id."""
    if not rag_client:
        return []
    try:
        kid = int(knowledge_base_id)
    except (TypeError, ValueError):
        return []
    if kid <= 0:
        return []
    try:
        rows = list(await rag_client.kb_list_documents() or [])
    except Exception:
        logger.exception(
            "Не удалось собрать документы базы знаний %s", knowledge_base_id
        )
        return []
    out: list = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        meta = row.get("metadata") or {}
        if not isinstance(meta, dict):
            continue
        try:
            if int(meta.get("knowledge_base_id")) != kid:
                continue
        except (TypeError, ValueError):
            continue
        try:
            out.append(int(row["id"]))
        except (TypeError, ValueError, KeyError):
            continue
    return out


async def _run_background_rechunk_for_user(
    user_id: str,
    chunk_params: dict,
    scope: Optional[str] = None,
    *,
    project_id: Optional[str] = None,
    agent_id: Optional[int] = None,
    knowledge_base_id: Optional[int] = None,
) -> None:
    """Перечанковка документов в пределах ОДНОГО стора.

    ```scope="agent"``` — только agent KB (документы, где owner_user_id = пользователь),
    ```scope="project"``` — только документы проектов.
    ```scope="kb"``` — документы пользовательской базы знаний.
    Без scope трогает agent+project, как было до разделения настроек.

    С ```agent_id``` — только KB выбранного агента.
    С ```project_id``` — только документы указанного проекта.
    С ```knowledge_base_id``` — только документы указанной базы знаний.

    Memory RAG не трогаем — нарезка Memory только из env SVC-RAG.
    """
    if not rag_client or not user_id:
        return
    cs = chunk_params.get("chunk_size")
    co = chunk_params.get("chunk_overlap")
    strat = chunk_params.get("chunking_strategy")
    oid = _norm_uid(user_id)
    agent_doc_ids = await _collect_owner_agent_kb_document_ids(oid)
    if agent_id is not None:
        try:
            aid = int(agent_id)
        except (TypeError, ValueError):
            aid = 0
        if aid > 0:
            agent_doc_ids = await _collect_agent_kb_document_ids(aid)
    kb_doc_ids: list = []
    if knowledge_base_id is not None:
        kb_doc_ids = await _collect_knowledge_base_document_ids(knowledge_base_id)
    # Фон живёт вне запроса — ContextVar пуст, модель берём у самой сущности.
    # Взять её у пользователя нельзя: перечанковку мог запустить редактор, а
    # корпус должен лечь моделью агента, той же, которой по нему потом ищут.
    from backend.services.user_rag_settings import embedding_fields_for_entity

    emb = await embedding_fields_for_entity(
        scope,
        _entity_id_from_params(
            scope or "", project_id, agent_id, knowledge_base_id
        ),
    )
    logger.info(
        "[RECHUNK-USER] старт user=%s scope=%s project=%s agent=%s kb=%s "
        "provider=%s model=%s strategy=%s chunk_size=%s "
        "overlap=%s agent_docs=%s kb_docs=%s",
        oid,
        scope or "both",
        project_id or "*",
        agent_id or "*",
        knowledge_base_id or "*",
        emb.get("embedding_provider") or "cluster",
        emb.get("embedding_model"),
        strat,
        cs,
        co,
        len(agent_doc_ids),
        len(kb_doc_ids),
    )
    sc = (scope or "").strip().lower()
    do_agent = sc in ("", "agent")
    do_project = sc in ("", "project")
    do_user_kb = sc == "kb"
    pid = str(project_id or "").strip() or None

    # Пустой список ≠ «все документы»: раньше `[] or None` уводил в полный
    # reindex владельца и поднимал плашку даже у нового агента без файлов.
    if do_agent and not agent_doc_ids:
        logger.info(
            "[RECHUNK-USER] kb пропуск: нет документов "
            "(user=%s agent=%s) — плашку не поднимаем",
            oid,
            agent_id or "*",
        )
        do_agent = False

    if do_user_kb and not kb_doc_ids:
        logger.info(
            "[RECHUNK-USER] knowledge_base пропуск: нет документов "
            "(user=%s kb=%s) — плашку не поднимаем",
            oid,
            knowledge_base_id or "*",
        )
        do_user_kb = False

    # Новый проект без файлов: смена эмбеддера/нарезки не должна стартовать
    # перечанковку и оранжевую плашку — пересобирать нечего.
    if do_project and pid:
        has_project_docs = await _resolve_project_has_rag_documents(pid)
        if not has_project_docs:
            logger.info(
                "[RECHUNK-USER] project пропуск: нет документов "
                "(user=%s project=%s) — плашку не поднимаем",
                oid,
                pid,
            )
            do_project = False

    if not do_agent and not do_project and not do_user_kb:
        logger.info(
            "[RECHUNK-USER] нечего пересобирать user=%s scope=%s — выход",
            oid,
            scope or "both",
        )
        return

    if sc == "agent" and agent_id is not None:
        marked_entity_id = str(agent_id)
    elif sc == "kb" and knowledge_base_id is not None:
        marked_entity_id = str(knowledge_base_id)
    else:
        marked_entity_id = pid
    _mark_entity_reindex(
        scope,
        marked_entity_id,
        active=True,
        name=await _entity_display_name(scope or "", marked_entity_id or ""),
    )
    _mark_reindex_started()
    try:
        if do_agent:
            try:
                kb_res = await rag_client.kb_reindex(
                    chunk_size=cs,
                    chunk_overlap=co,
                    chunking_strategy=strat,
                    # Пересобираем КОНКРЕТНОГО агента - значит только его документы
                    # svc-rag складывает owner_user_id и document_ids ОБЪЕДИНЕНИЕМ,
                    # поэтому вместе с owner сюда приезжали все документы этого
                    # пользователя и перенарезались настройками текущего агента
                    owner_user_id=None if agent_id is not None else oid,
                    # Ключ очереди в svc-rag: пересборка этого агента не должна
                    # прерывать пересборку соседнего у того же владельца
                    agent_id=int(agent_id) if agent_id is not None else None,
                    document_ids=list(agent_doc_ids),
                    **emb,
                )
                logger.info("[RECHUNK-USER] kb готово: %s", kb_res)
            except Exception:
                logger.exception("[RECHUNK-USER] kb ошибка")
        if do_user_kb:
            try:
                kb_res = await rag_client.kb_reindex(
                    chunk_size=cs,
                    chunk_overlap=co,
                    chunking_strategy=strat,
                    owner_user_id=None,
                    knowledge_base_id=(
                        int(knowledge_base_id)
                        if knowledge_base_id is not None
                        else None
                    ),
                    document_ids=list(kb_doc_ids),
                    **emb,
                )
                logger.info("[RECHUNK-USER] knowledge_base готово: %s", kb_res)
            except Exception:
                logger.exception("[RECHUNK-USER] knowledge_base ошибка")
        if do_project:
            try:
                proj_res = await rag_client.project_rag_reindex_all(
                    chunk_size=cs,
                    chunk_overlap=co,
                    chunking_strategy=strat,
                    owner_user_id=oid if not pid else None,
                    project_id=pid,
                    **emb,
                )
                logger.info(
                    "[RECHUNK-USER] project готово scope=%s project_id=%s: %s",
                    scope or "both",
                    pid or "*",
                    proj_res,
                )
            except Exception:
                logger.exception("[RECHUNK-USER] projects ошибка")
        logger.info(
            "[RECHUNK-USER] задачи поставлены в svc-rag user=%s project=%s; "
            "флаг снимется по факту завершения",
            oid,
            pid or "*",
        )
    except Exception:
        # Ставить задачи не удалось — снимаем отметку сразу, ждать нечего.
        _mark_entity_reindex(scope, marked_entity_id, active=False)
        raise


async def _run_background_reindex_after_model_change() -> None:
    """Кластерное восстановление после миграции dim (ConfigMap / reconcile).

    Memory тоже переиндексируется — вектора очищены TRUNCATE всей таблицы.
    """
    global _cluster_reindex_active
    if not rag_client:
        return
    params = get_rag_chunk_index_params()
    cs = params.get("chunk_size")
    co = params.get("chunk_overlap")
    strat = params.get("chunking_strategy")
    logger.info(
        "[REINDEX-CLUSTER] старт после миграции dim: strategy=%s size=%s overlap=%s",
        strat,
        cs,
        co,
    )
    _cluster_reindex_active = True
    _mark_reindex_started()
    try:
        from backend.services.memory_rag_env import get_memory_chunk_index_params

        mem = get_memory_chunk_index_params()
    except Exception:
        mem = {"chunk_size": cs, "chunk_overlap": co, "chunking_strategy": "universal"}
    for name, call, kwargs in (
        ("kb", rag_client.kb_reindex, {"chunk_size": cs, "chunk_overlap": co, "chunking_strategy": strat}),
        ("projects", rag_client.project_rag_reindex_all, {"chunk_size": cs, "chunk_overlap": co, "chunking_strategy": strat}),
        (
            "memory",
            rag_client.memory_rag_reindex,
            {
                "chunk_size": mem.get("chunk_size"),
                "chunk_overlap": mem.get("chunk_overlap"),
                "chunking_strategy": mem.get("chunking_strategy"),
            },
        ),
    ):
        try:
            res = await call(**kwargs)
            logger.info("[REINDEX-CLUSTER] %s готово: %s", name, res)
        except Exception:
            logger.exception("[REINDEX-CLUSTER] %s ошибка", name)
    logger.info(
        "[REINDEX-CLUSTER] задачи поставлены в svc-rag; "
        "флаг снимется по факту завершения"
    )


async def _run_background_reindex_for_user(
    user_id: str,
    chunk_params: dict,
    scope: Optional[str] = None,
    *,
    project_id: Optional[str] = None,
    agent_id: Optional[int] = None,
    knowledge_base_id: Optional[int] = None,
) -> None:
    """Переиндексация документов пользователя в пределах стора (без Memory).

    ```project_id``` / ```agent_id``` / ```knowledge_base_id``` сужают до одной
    сущности — без них смена модели у одного агента перетряхнула бы KB всех
    агентов пользователя.
    """
    await _run_background_rechunk_for_user(
        user_id,
        chunk_params,
        scope,
        project_id=project_id,
        agent_id=agent_id,
        knowledge_base_id=knowledge_base_id,
    )


@router.put("/api/rag/settings")
async def update_rag_settings(
    settings_data: RAGSettings,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Обновить RAG-настройки конкретного агента или проекта.

    Правка видна ВСЕМ, кому сущность расшарена, — поэтому нужны права владельца
    или редактора. При смене полей, влияющих на индекс (нарезка, эмбеддер),
    перечанковываются документы только этой сущности.

    Memory RAG настраивается из env; из UI у него меняется одна стратегия поиска
    (```rag_memory_strategy```) — она общая и остаётся у пользователя.
    """
    user_id = str(current_user.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Требуется авторизация")

    strat = settings_data.strategy
    if strat is not None and strat == "reranking":
        strat = "hybrid"
    if strat is not None and strat == "standard":
        strat = "vector"
    if strat is not None and strat == "lexical":
        strat = "lexical"
    if strat is not None and strat not in _VALID_STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимая стратегия. Допустимые: {_VALID_STRATEGIES}",
        )
    chunking = settings_data.rag_chunking_strategy
    if chunking is not None:
        chunking = str(chunking).strip().lower()
        if chunking not in _VALID_CHUNKING_STRATEGIES:
            raise HTTPException(
                status_code=400,
                detail=f"Недопустимая стратегия чанкования. Допустимые: {_VALID_CHUNKING_STRATEGIES}",
            )
    # Стратегия Библиотеки — единственная настройка, оставшаяся у пользователя:
    # это общий стор, сущности к нему отношения не имеют. Обрабатываем ДО
    # проверки «нет полей»: со страницы настроек она приходит одна, без сущности.
    memory_strategy_saved: Optional[str] = None
    if settings_data.rag_memory_strategy is not None:
        mem_strat = str(settings_data.rag_memory_strategy).strip().lower()
        if mem_strat and mem_strat not in _VALID_STRATEGIES:
            raise HTTPException(
                status_code=400,
                detail=f"Недопустимая стратегия Библиотеки. Допустимые: {_VALID_STRATEGIES}",
            )
        if mem_strat:
            memory_strategy_saved = await save_user_memory_strategy(user_id, mem_strat)
            logger.info(
                "[RAG-CFG] стратегия поиска по Библиотеке у %s: %s", user_id, mem_strat
            )
    entity_fields_present = any(
        value is not None
        for value in (
            settings_data.strategy,
            settings_data.agentic_rag_enabled,
            settings_data.agentic_max_iterations,
            settings_data.rag_query_fix_typos,
            settings_data.rag_multi_query_enabled,
            settings_data.rag_hyde_enabled,
            settings_data.rag_chat_top_k,
            settings_data.rag_chunking_strategy,
            settings_data.rag_chunk_size,
            settings_data.rag_chunk_overlap,
            settings_data.rag_similarity_threshold,
            settings_data.rag_reranking_enabled,
            settings_data.rag_rerank_top_n,
            settings_data.rag_system_prompt,
            settings_data.rag_embedding_model_path,
            settings_data.rag_reranker_model_path,
        )
    )
    if not entity_fields_present:
        if memory_strategy_saved:
            return {
                "message": "Стратегия поиска по Библиотеке обновлена",
                "success": True,
                "rag_memory_strategy": memory_strategy_saved,
            }
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    scope = normalize_scope(settings_data.scope)
    entity_id = _entity_id_from_params(
        scope,
        settings_data.project_id,
        settings_data.agent_id,
        settings_data.knowledge_base_id,
    )
    entity_label = _entity_label(scope, settings_data.entity_name, entity_id)
    if not entity_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Укажите project_id, agent_id или knowledge_base_id: "
                "настройки принадлежат сущности"
            ),
        )
    permission = await _require_entity_editor(
        scope, entity_id, user_id, is_admin=bool(current_user.get("is_admin"))
    )
    try:
        before = await get_entity_rag_settings(scope, entity_id)
        updates: dict = {}
        if strat is not None:
            updates["rag_strategy"] = strat
        if settings_data.agentic_rag_enabled is not None:
            updates["agentic_rag_enabled"] = bool(settings_data.agentic_rag_enabled)
        if settings_data.agentic_max_iterations is not None:
            ami = int(settings_data.agentic_max_iterations)
            updates["agentic_max_iterations"] = max(1, min(ami, 5))
        if settings_data.rag_query_fix_typos is not None:
            updates["rag_query_fix_typos"] = bool(settings_data.rag_query_fix_typos)
        if settings_data.rag_multi_query_enabled is not None:
            updates["rag_multi_query_enabled"] = bool(
                settings_data.rag_multi_query_enabled
            )
        if settings_data.rag_hyde_enabled is not None:
            updates["rag_hyde_enabled"] = bool(settings_data.rag_hyde_enabled)
        if settings_data.rag_chat_top_k is not None:
            try:
                rk = int(settings_data.rag_chat_top_k)
                updates["rag_chat_top_k"] = max(1, min(rk, 64))
            except (TypeError, ValueError) as e:
                raise HTTPException(
                    status_code=400, detail="rag_chat_top_k: ожидается целое число 1–64"
                ) from e
        if chunking is not None:
            updates["rag_chunking_strategy"] = chunking
        if settings_data.rag_chunk_size is not None:
            try:
                size = int(settings_data.rag_chunk_size)
                updates["rag_chunk_size"] = max(200, min(size, 8000))
            except (TypeError, ValueError) as e:
                raise HTTPException(
                    status_code=400,
                    detail="rag_chunk_size: ожидается целое число 200–8000",
                ) from e
        if settings_data.rag_chunk_overlap is not None:
            try:
                overlap = int(settings_data.rag_chunk_overlap)
                updates["rag_chunk_overlap"] = max(0, min(overlap, 2000))
            except (TypeError, ValueError) as e:
                raise HTTPException(
                    status_code=400,
                    detail="rag_chunk_overlap: ожидается целое число 0–2000",
                ) from e
        if settings_data.rag_similarity_threshold is not None:
            try:
                threshold = float(settings_data.rag_similarity_threshold)
                updates["rag_similarity_threshold"] = max(0.0, min(threshold, 1.0))
            except (TypeError, ValueError) as e:
                raise HTTPException(
                    status_code=400,
                    detail="rag_similarity_threshold: ожидается число 0..1",
                ) from e
        if settings_data.rag_reranking_enabled is not None:
            updates["rag_reranking_enabled"] = bool(settings_data.rag_reranking_enabled)
        if settings_data.rag_rerank_top_n is not None:
            try:
                top_n = int(settings_data.rag_rerank_top_n)
                updates["rag_rerank_top_n"] = max(1, min(top_n, 64))
            except (TypeError, ValueError) as e:
                raise HTTPException(
                    status_code=400,
                    detail="rag_rerank_top_n: ожидается целое число 1–64",
                ) from e
        if settings_data.rag_system_prompt is not None:
            # Пустая строка допустима: тогда soft-rules; иначе — текст из UI как есть.
            updates["rag_system_prompt"] = str(
                settings_data.rag_system_prompt or ""
            ).strip()
        if settings_data.rag_embedding_model_path is not None:
            updates["rag_embedding_model_path"] = str(
                settings_data.rag_embedding_model_path or ""
            ).strip()
        if settings_data.rag_reranker_model_path is not None:
            updates["rag_reranker_model_path"] = str(
                settings_data.rag_reranker_model_path or ""
            ).strip()

        if not updates:
            raise HTTPException(status_code=400, detail="Нет полей для обновления")

        kind = (
            "проекта"
            if scope == "project"
            else "базы знаний"
            if scope == "kb"
            else "агента"
        )
        logger.debug(
            "[RAG-CFG] настройки %s %s (user=%s): %s", kind, entity_id, user_id, updates
        )
        merged = await save_entity_rag_settings(scope, entity_id, updates, user_id)
        logger.info(
            "[RAG-CFG] настройки %s %s изменены пользователем %s (роль %s)",
            kind,
            entity_label,
            user_id,
            permission,
        )

        if "rag_chat_top_k" in updates or "rag_rerank_top_n" in updates:
            bump_rag_semantic_cache()

        # Пересобираем только когда изменилось то, что лежит в индексе: нарезка
        # и эмбеддер. top_k, порог и тумблеры препроцесса применяются на лету.
        reindex_started = False
        if _rechunk_on_settings_change() and index_params_changed(before, merged):
            logger.info(
                # Печатаем "было -> стало" по каждому индексному ключу
                "[RECHUNK-USER] индексные настройки %s %s изменились → перечанковка: %s",
                kind,
                entity_label,
                {
                    k: f"{before.get(k)!r} → {merged.get(k)!r}"
                    for k in INDEX_AFFECTING_KEYS
                    if before.get(k) != merged.get(k)
                },
            )
            reindex_started = True
            asyncio.create_task(
                _run_background_rechunk_for_user(
                    user_id,
                    chunk_params_from_rag_settings(merged),
                    scope,
                    project_id=settings_data.project_id,
                    agent_id=settings_data.agent_id,
                    knowledge_base_id=settings_data.knowledge_base_id,
                )
            )
        return {
            "message": "Настройки RAG обновлены",
            "success": True,
            # Фронт по этому полю учащает опрос статуса: задача фоновая, и
            # плашка иначе всплывёт только на следующем тике опроса. Здесь
            # именно "поставили задачу": пересобирать может оказаться нечего,
            # тогда фон выйдет молча и флаг так и не поднимется.
            "reindexed": reindex_started,
            **settings_response_dict(merged),
            "scope": scope,
            "project_id": settings_data.project_id if scope == "project" else None,
            "agent_id": settings_data.agent_id if scope == "agent" else None,
            "knowledge_base_id": (
                settings_data.knowledge_base_id if scope == "kb" else None
            ),
            "permission": permission,
            "can_edit": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _run_global_reindex(
    agent_params: dict, project_params: dict, include_memory: bool
) -> None:
    """Полный реиндекс KB + проектов (все документы всех пользователей)."""
    global _cluster_reindex_active
    if not rag_client:
        return
    a_cs = agent_params.get("chunk_size")
    a_co = agent_params.get("chunk_overlap")
    a_strat = agent_params.get("chunking_strategy")
    p_cs = project_params.get("chunk_size")
    p_co = project_params.get("chunk_overlap")
    p_strat = project_params.get("chunking_strategy")
    # Для fallback-а на старые версии без scope:
    cs, co = a_cs, a_co
    logger.info(
        "[REINDEX-ALL] старт: agent strategy=%s chunk_size=%s overlap=%s project strategy=%s chunk_size=%s overlap=%s memory=%s",
        a_strat, 
        a_cs, 
        a_co, 
        p_strat, 
        p_cs, 
        p_co, 
        include_memory,
    )
    # Флаг снимает /api/rag/reindex-status по факту освобождения локов svc-rag:
    # здесь мы узнаём только момент ПОСТАНОВКИ задачи (svc-rag отвечает "started"
    # сразу), а работа идёт ещё десятки минут.
    _cluster_reindex_active = True
    _mark_reindex_started()
    try:
        kb_res = await rag_client.kb_reindex(
            chunk_size=a_cs, chunk_overlap=a_co, chunking_strategy=a_strat
        )
        logger.info("[REINDEX-ALL] kb готово: %s", kb_res)
    except Exception:
        logger.exception("[REINDEX-ALL] kb ошибка")
    try:
        proj_res = await rag_client.project_rag_reindex_all(
            chunk_size=p_cs, chunk_overlap=p_co, chunking_strategy=p_strat
        )
        logger.info("[REINDEX-ALL] projects готово: %s", proj_res)
    except Exception:
        logger.exception("[REINDEX-ALL] projects ошибка")
    if include_memory:
        try:
            from backend.services.memory_rag_env import get_memory_chunk_index_params
            
            mem = get_memory_chunk_index_params()
        except Exception:
            mem = {
                "chunk_size": cs,
                "chunk_overlap": co,
                "chunking_strategy": "universal",
            }
        try:
            mem_res = await rag_client.memory_rag_reindex(
                chunk_size=mem.get("chunk_size"),
                chunk_overlap=mem.get("chunk_overlap"),
                chunking_strategy=mem.get("chunking_strategy"),
            )
            logger.info("[REINDEX-ALL] memory готово: %s", mem_res)
        except Exception:
            logger.exception("[REINDEX-ALL] memory ошибка")
    logger.info("[REINDEX-ALL] задачи поставлены в svc-rag")
        

@router.post("/api/rag/reindex-all")
async def reindex_all_documents(
    current_user: Annotated[dict, Depends(get_current_user)],
    include_memory: bool = False,
):
    """ПОЛНЫЙ реиндекс: KB + проекты всех пользователей текущей моделью.

    Memory — только при include_memory=true (по умолчанию не трогаем: её нарезка из ENV).
    Возвращает сразу, работа идёт в фоне; следить по логам [REINDEX-ALL] и [REINDEX kb|project].
    """
    require_service("rag")
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    user_id = str(current_user.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    logger.warning(
        "[REINDEX-ALL] запущен пользователем %s (memory=%s)", user_id, include_memory
    )
    # Известное ограничение: прогон режет ВСЁ одним набором параметров и
    # индивидуальные настройки агентов не учитывает — после него они разойдутся
    # с фактом, и агента придётся пересобрать из его карточки. Эндпоинт ручной,
    # кнопки в интерфейсе нет; переделка на цикл по сущностям — отдельная работа.
    cluster_defaults = default_rag_settings_snapshot()
    asyncio.create_task(
        _run_global_reindex(
            chunk_params_from_rag_settings(cluster_defaults),
            chunk_params_from_rag_settings(cluster_defaults),
            bool(include_memory),
        )
    )
    return {
        "ok": True,
        "status": "started",
        "scope": "kb+projects" + ("+memory" if include_memory else ""),
    }


PHOENIX_PROVIDER_ID = os.getenv("RAG_PHOENIX_PROVIDER_ID", "PHOENIX")
CORSUR_PROVIDER_ID = os.getenv("RAG_CORSUR_PROVIDER_ID", "CORSUR")
# Phoenix под ключом эмбеддингов: /v1/models отдаёт только эмбеддеры и реранкеры.
PHOENIX_EMBEDDINGS_PROVIDER_ID = os.getenv(
    "RAG_PHOENIX_EMBEDDINGS_PROVIDER_ID", "PHOENIX_Embeddings"
)

_dangling_default_warned: set = set()


async def _rag_gateway_catalog() -> list:
    """Записи каталога по провайдерам, объявившим себя шлюзом RAG-моделей.

    ```path_prefix``` = id провайдера в нижнем регистре. По нему SVC-RAG ищет
    провайдера в своём ```rag_models.providers``` (сравнение регистронезависимое),
    поэтому имя должно совпадать по обе стороны — это и есть весь контракт.

    ```rag_only=True``` для всех: сюда попадают только выделенные шлюзы моделей,
    у них незнакомое по имени — эмбеддер, а не чат-модель.
    """
    from backend.llm_providers.registry import get_registry

    registry = await get_registry()
    entries: list = []
    for provider in registry.all():
        extra = getattr(getattr(provider, "config", None), "extra", None) or {}
        if not bool(extra.get("rag_gateway")):
            continue
        pid = str(getattr(provider, "id", "") or "").strip()
        if not pid:
            continue
        entries.append(
            {
                "provider_id": pid,
                "path_prefix": pid.lower(),
                "source": pid.lower(),
                "description": f"Модель {pid}",
                "rag_only": True,
            }
        )
    if not entries:
        logger.debug(
            "[RAG-CATALOG] ни один провайдер не объявил extra.rag_gateway: true — "
            "используем встроенный каталог CORSUR/PHOENIX"
        )
    return entries


def _warn_if_dangling_cluster_default(catalog_rows: list) -> None:
    """Кластерный дефолт указывает на шлюз, которого нет в каталоге.

    Путь модели лежит в settings.json обычной строкой и переживает любое
    переименование провайдера. Сущность без своей записи наследует его целиком —
    то есть КАЖДЫЙ новый проект и агент. Префикс уходит в SVC-RAG как есть, там
    он не находится, и первая же загрузка документа падает с 422 «провайдер не
    найден», хотя пользователь модель не выбирал.
    """
    if not catalog_rows:
        return
    known = {str(r.get("source") or "").strip().lower() for r in catalog_rows}
    known |= {"local", "native", ""}
    for field, label in (
        ("rag_embedding_model_path", "эмбеддинга"),
        ("rag_reranker_model_path", "реранкера"),
    ):
        path = str(getattr(state, field, "") or "").strip()
        prefix = path.split("/", 1)[0].strip().lower() if "/" in path else ""
        if not path or prefix in known or path in _dangling_default_warned:
            continue
        _dangling_default_warned.add(path)
        logger.warning(
            "[RAG-CATALOG] кластерный дефолт модели %s — %r, но провайдера %r "
            "в каталоге нет (есть: %s). Этот путь наследует каждая новая "
            "сущность, и индексация в ней упадёт с 422 «провайдер не найден». "
            "Поправьте %s в settings.json бэкенда либо верните запись провайдера "
            "в rag_models.providers у SVC-RAG",
            label,
            path,
            prefix,
            ", ".join(sorted(k for k in known if k)) or "-",
            field,
        )


# Внешние OpenAI-совместимые источники эмбеддингов/реранка в UI.
# Вкладки (порядок и написание): CORSUR → PHOENIX → PHOENIX_Embeddings.
_EXTERNAL_RAG_CATALOG = (
    {
        "provider_id": CORSUR_PROVIDER_ID,
        "path_prefix": "corsur",
        "source": "corsur",
        "description": "Модель CORSUR (llm.corsur)",
        "rag_only": True,
    },
    {
        "provider_id": PHOENIX_PROVIDER_ID,
        "path_prefix": "phoenix",
        "source": "phoenix",
        "description": "Модель Phoenix (LiteLLM)",
        "rag_only": False,
    },
    {
        "provider_id": PHOENIX_EMBEDDINGS_PROVIDER_ID,
        "path_prefix": "phoenix_embeddings",
        "source": "phoenix_embeddings",
        "description": "Модель Phoenix Embeddings (LiteLLM, emb-ключ)",
        "rag_only": True,
    },
)

async def _effective_rag_gateway_catalog() -> list:
    """Динамический каталог из config.yml; при пустом — legacy CORSUR/PHOENIX."""
    dynamic = await _rag_gateway_catalog()
    if dynamic:
        return dynamic
    return list(_EXTERNAL_RAG_CATALOG)

# Подсказки к типу модели. Это СЕМЕЙСТВА, а не конкретные id: список моделей
# приходит из /v1/models и нигде не хардкодится. Дополняются переменными
# окружения через запятую — новое семейство не требует правки кода:
#   RAG_RERANKER_NAME_HINTS=colbert,monot5
#   RAG_EMBEDDING_NAME_HINTS=jina,nomic
# Реранкерные проверяются ПЕРВЫМИ: 'bge/bge-rerank' → reranker, 'bge/bge-m3' → embedding.
_RERANKER_NAME_HINTS_DEFAULT = ("rerank", "cross-encoder", "crossencoder", "marco")
_EMBEDDING_NAME_HINTS_DEFAULT = (
    "embed",
    "bge",
    "frida",
    "giga",
    "e5-",
    "gte-",
    "labse",
    "minilm",
)

def _name_hints(env_name: str, defaults: tuple) -> tuple:
    extra = (os.getenv(env_name, "") or "").replace(";", ",")
    parts = [p.strip().lower() for p in extra.split(",") if p.strip()]
    return tuple(defaults) + tuple(p for p in parts if p not in defaults)

_RERANKER_NAME_HINTS = _name_hints(
    "RAG_RERANKER_NAME_HINTS", _RERANKER_NAME_HINTS_DEFAULT
)
_EMBEDDING_NAME_HINTS = _name_hints(
    "RAG_EMBEDDING_NAME_HINTS", _EMBEDDING_NAME_HINTS_DEFAULT
)


def _rag_model_kind(model_id: str, *, rag_only: bool) -> Optional[str]:
    """Тип RAG-модели по её id. None — модель не для RAG-селектора.

    ```rag_only=True``` — шлюз отдаёт только эмбеддеры и реранкеры, поэтому
    неизвестное имя считаем эмбеддером, а не выбрасываем: иначе ```bge-m3```,
    ```FRIDA```, ```multilingual-e5-large``` молча исчезают из селектора.
    ```rag_only=False``` — на шлюзе есть и чат-модели, тип обязан быть виден
    по имени, иначе в RAG-селектор попадут LLM.
    """
    n = (model_id or "").lower()
    if any(h in n for h in _RERANKER_NAME_HINTS):
        return "reranker"
    if any(h in n for h in _EMBEDDING_NAME_HINTS):
        return "embedding"
    return "embedding" if rag_only else None

# --- Проверка, что объявленная моделью строка каталога реально работает -----
# Шлюзы объявляют модели, которые вызвать нельзя: у PHOENIX это битые алиасы
# вроде "QWEN3 EMBEDDING 8B" (400 Invalid model name), у CORSUR — чат-модели,
# попадающие в embedding-список из-за rag_only.
#
# Но «не ответила» бывает двух совершенно разных сортов, и путать их нельзя:
#   * шлюз ВНЯТНО отверг модель (400/422, или 200 без вектора) — прячем;
#   * шлюз недоступен, тормозит, отдаёт 5xx или таймаут — НЕ прячем.
# Второе — состояние шлюза, а не модели. 31.07 из-за этого селектор опустел
# целиком: у PHOENIX рабочая qwen3-embedding-06b-f16 не уложилась в таймаут,
# и единственный живой эмбеддер исчез из UI.
_PROBE_TTL_SECONDS = float(os.getenv("RAG_MODEL_PROBE_TTL", "600") or 600)
# Неопределённый результат кешируем ненадолго: шлюз мог просто моргнуть.
_PROBE_TTL_UNKNOWN = float(os.getenv("RAG_MODEL_PROBE_TTL_UNKNOWN", "120") or 120)
_PROBE_TIMEOUT = float(os.getenv("RAG_MODEL_PROBE_TIMEOUT", "15") or 15)
_PROBE_MAX_PARALLEL = 8

_PROBE_OK = "ok"
_PROBE_BAD = "bad"          # модель заведомо не работает — прятать
_PROBE_UNKNOWN = "unknown"  # шлюз не в форме — оставить как есть

# (provider_id, model_id) -> (статус, dim, причина, monotonic-время проверки)
_embedding_probe_cache: dict = {}

# Кэш полного RAG-каталога (как MODELS_AVAILABLE_CACHE у LLM).
# current из user settings поверх кэша на каждом запросе — не кэшируется.
_rag_models_catalog_cache: Optional[list] = None
_rag_models_catalog_cache_ts: float = 0.0
_rag_models_catalog_cache_lock = asyncio.Lock()


def _rag_models_cache_seconds() -> int:
    """TTL каталога /api/rag/models. ENV: RAG_MODELS_CACHE_SECONDS, иначе как у LLM."""
    raw = (os.getenv("RAG_MODELS_CACHE_SECONDS", "") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    try:
        from backend.settings import get_settings

        return max(1, int(get_settings().model_health.models_available_cache_seconds))
    except Exception:
        return 60


def _invalidate_rag_models_catalog_cache() -> None:
    global _rag_models_catalog_cache, _rag_models_catalog_cache_ts
    _rag_models_catalog_cache = None
    _rag_models_catalog_cache_ts = 0.0


def _probe_enabled() -> bool:
    return str(os.getenv("RAG_MODEL_PROBE", "1")).strip().lower() not in {
        "0",
        "false",
        "no",
    }

def _hide_unavailable() -> bool:
    """Прятать неработающие модели из селектора (по умолчанию да).

    RAG_MODEL_PROBE_HIDE_UNAVAILABLE=0 — показывать их с available=false и
    причиной, если фронт умеет рисовать недоступную строку.
    """
    return str(os.getenv("RAG_MODEL_PROBE_HIDE_UNAVAILABLE", "1")).strip().lower() not in {
        "0",
        "false",
        "no",
    }

def _first_vector_len(value, depth: int = 0) -> int:
    """Длина первого списка чисел в ответе. 0 — вектора нет.

    Рекурсивно, а не по известным ключам: через bifrost-passthrough вектор
    приходит завёрнутым в chat-ответ, и точную форму снаружи знать нельзя.
    """
    if depth > 6:
        return 0
    if isinstance(value, list):
        if len(value) >= 8 and all(isinstance(x, (int, float)) for x in value[:8]):
            return len(value)
        for item in value[:3]:
            found = _first_vector_len(item, depth + 1)
            if found:
                return found
        return 0
    if isinstance(value, dict):
        for item in value.values():
            found = _first_vector_len(item, depth + 1)
            if found:
                return found
    return 0

def _classify_probe_response(resp) -> tuple:
    """(статус, dim, причина) по одному ответу шлюза."""
    if resp.status_code == 200:
        try:
            dim = _first_vector_len(resp.json())
        except Exception:
            return _PROBE_BAD, 0, "200, но тело не JSON"
        if dim:
            return _PROBE_OK, dim, ""
        # 200 без вектора — это чат-модель, попавшая в embedding-список.
        return _PROBE_BAD, 0, "200, но вектора в ответе нет (это не эмбеддер)"
    body = (resp.text or "")[:160]
    if resp.status_code in (400, 401, 403, 422):
        return _PROBE_BAD, 0, f"HTTP {resp.status_code}: {body}"
    # 404/405 — маршрута нет; 5xx — шлюзу плохо. И то и другое про шлюз.
    return _PROBE_UNKNOWN, 0, f"HTTP {resp.status_code}: {body}"

async def _probe_embedding_model(provider, model_id: str) -> tuple:
    """Пробный вектор. Возвращает (статус, dim, причина).

    Два захода, потому что эмбеддер может быть опубликован по-разному:
      1. штатный POST /v1/embeddings;
      2. если там нет маршрута (404/405) — тоннель через /v1/chat/completions
         с ```x-bf-passthrough-extra-params```. Так эмбеддинги ходят на CORSUR,
         где отдельного embeddings-маршрута на шлюзе нет. Без этого захода
         рабочий embed/FRIDA помечался недоступным и пропадал из UI.

    Кеш: успех и внятный отказ — на RAG_MODEL_PROBE_TTL, неопределённость —
    на RAG_MODEL_PROBE_TTL_UNKNOWN (шлюз мог просто моргнуть).
    """
    import time

    provider_id = str(getattr(provider, "id", "") or "")
    key = (provider_id, model_id)
    now = time.monotonic()
    cached = _embedding_probe_cache.get(key)
    if cached:
        ttl = _PROBE_TTL_UNKNOWN if cached[0] == _PROBE_UNKNOWN else _PROBE_TTL_SECONDS
        if (now - cached[3]) < ttl:
            return cached[0], cached[1], cached[2]

    base_url = str(getattr(provider, "base_url", "") or "").rstrip("/")
    if not base_url:
        # проверить нечем — не прячем модель на всякий случай
        return _PROBE_UNKNOWN, 0, "base_url не задан"

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        api_key = str(provider.get_api_key() or "")
    except Exception:
        api_key = ""
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    verify_fn = getattr(provider, "_http_verify", None)
    verify = verify_fn() if callable(verify_fn) else True

    status, dim, reason = _PROBE_UNKNOWN, 0, ""
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT, verify=verify) as client:
            resp = await client.post(
                f"{base_url}/v1/embeddings",
                headers=headers,
                json={"model": model_id, "input": ["ping"]},
            )
            status, dim, reason = _classify_probe_response(resp)

            if status != _PROBE_OK and resp.status_code in (404, 405):
                tunnel_headers = dict(headers)
                tunnel_headers["x-bf-passthrough-extra-params"] = "true"
                resp = await client.post(
                    f"{base_url}/v1/chat/completions",
                    headers=tunnel_headers,
                    json={
                        # Только texts, без input: тело с обоими непустыми
                        # полями шлюз отвергает с 400 (проверено 31.07 20:10).
                        "model": model_id,
                        "messages": [{"role": "user", "content": "Send request"}],
                        "texts": ["ping"],
                        "kind": "query",
                    },
                )
                status, dim, reason = _classify_probe_response(resp)
                if status == _PROBE_OK:
                    reason = ""
    except Exception as e:
        status, dim, reason = _PROBE_UNKNOWN, 0, f"{type(e).__name__}: {e}"

    _embedding_probe_cache[key] = (status, dim, reason, now)
    return status, dim, reason

async def _annotate_availability(provider, rows: list, source: str) -> list:
    """Проставить строкам каталога реальную доступность (и dim).

    Прячем ТОЛЬКО заведомо нерабочие модели. Если шлюз не в форме (таймаут,
    5xx), модель остаётся в селекторе: пустой список хуже, чем список с
    моделью, которая, возможно, заработает через минуту.
    """
    targets = [r for r in rows if r.get("kind") == "embedding"]
    if not targets or not _probe_enabled():
        return rows

    semaphore = asyncio.Semaphore(_PROBE_MAX_PARALLEL)

    async def _one(row: dict) -> None:
        async with semaphore:
            status, dim, reason = await _probe_embedding_model(provider, row["name"])
        row["probe_status"] = status
        row["available"] = status != _PROBE_BAD
        if dim:
            row["dim"] = dim
        if reason:
            row["probe_note"] = reason

    await asyncio.gather(*(_one(row) for row in targets))

    bad = [(r["path"], r.get("probe_note", "")) for r in targets if r.get("probe_status") == _PROBE_BAD]
    unknown = [(r["path"], r.get("probe_note", "")) for r in targets if r.get("probe_status") == _PROBE_UNKNOWN]
    if bad:
        # Молча не выбрасываем: иначе непонятно, почему селектор поредел.
        logger.warning(
            "[RAG-CATALOG] %s: скрыты — шлюз внятно отверг модель: %s", source, bad
        )
    if unknown:
        logger.warning(
            "[RAG-CATALOG] %s: оставлены в селекторе, но проверка не удалась "
            "(состояние шлюза, не модели): %s",
            source,
            unknown,
        )
    if _hide_unavailable():
        return [r for r in rows if r.get("kind") != "embedding" or r.get("available")]
    return rows


async def _openai_compat_rag_models(
    *,
    provider_id: str,
    path_prefix: str,
    source: str,
    description: str,
    model_type: Optional[str] = None,
    rag_only: bool = False,
) -> list:
    """Модели OpenAI-совместимого провайдера (GET /v1/models) для селектора RAG.

    Список берётся из живого ```GET /v1/models``` шлюза — ничего не захардкожено.
    Эмбеддеры дополнительно проверяются пробным вектором: в селектор попадают
    только те, что реально отвечают (см. ```_probe_embedding_model```).
    """
    from backend.llm_providers.registry import get_registry

    registry = await get_registry()
    if not registry.contains(provider_id):
        logger.warning(
            "[RAG-CATALOG] провайдер %r не найден в реестре LLM — модели %s не появятся",
            provider_id,
            source,
        )
        return []
    provider = registry.get(provider_id)
    infos = await provider.list_models()
    rows: list = []
    logger.info(
        "[RAG-CATALOG] %s сырые модели: %s",
        source,
        [getattr(i, "model_id", "") for i in (infos or [])],
    )
    skipped: list = []
    for info in infos or []:
        model_id = str(getattr(info, "model_id", "") or "").strip()
        if not model_id:
            continue
        kind = _rag_model_kind(model_id, rag_only=rag_only)
        if kind is None:
            # Раньше отбрасывалось молча — и было не понять, почему селектор пуст.
            skipped.append(model_id)
            continue
        if model_type and kind != model_type:
            continue
        rows.append(
            {
                "path": f"{path_prefix}/{model_id}",
                "name": model_id,
                "display_name": model_id,
                "source": source,
                "kind": kind,
                "description": description,
                "available": True,
            }
        )
    if skipped:
        logger.info(
            "[RAG-CATALOG] %s не распознаны как RAG-модели (тип не виден по имени): %s",
            source,
            skipped,
        )
    rows = await _annotate_availability(provider, rows, source)
    logger.info("[RAG-CATALOG] %s RAG-каталог: %s", source, [r["path"] for r in rows])
    return rows


async def _phoenix_rag_models(model_type: Optional[str] = None) -> list:
    """Обратная совместимость: только Phoenix."""
    return await _openai_compat_rag_models(
        provider_id=PHOENIX_PROVIDER_ID,
        path_prefix="phoenix",
        source="phoenix",
        description="Модель Phoenix (LiteLLM)",
        model_type=model_type,
    )


async def _external_rag_models(model_type: Optional[str] = None) -> list:
    """Все шлюзы RAG-моделей из конфига. Опрашиваются параллельно."""

    async def _one(entry: dict) -> list:
        try:
            return await _openai_compat_rag_models(
                provider_id=entry["provider_id"],
                path_prefix=entry["path_prefix"],
                source=entry["source"],
                description=entry["description"],
                model_type=model_type,
                rag_only=bool(entry.get("rag_only", False)),
            )
        except Exception:
            logger.exception(
                "Каталог %s недоступен — пропускаю", entry.get("source")
            )
            return []

    catalog = await _effective_rag_gateway_catalog()
    parts = await asyncio.gather(*(_one(entry) for entry in catalog))
    rows: list = []
    for part in parts:
        rows.extend(part)
    return rows


async def _external_rag_models_cached(model_type: Optional[str] = None) -> list:
    """Полный каталог с TTL (как /api/models/available), затем фильтр по type."""
    global _rag_models_catalog_cache, _rag_models_catalog_cache_ts

    cache_seconds = _rag_models_cache_seconds()
    now = time.monotonic()
    if (
        _rag_models_catalog_cache is not None
        and (now - _rag_models_catalog_cache_ts) < cache_seconds
        and _rag_models_catalog_cache
    ):
        rows = _rag_models_catalog_cache
    else:
        async with _rag_models_catalog_cache_lock:
            now = time.monotonic()
            if (
                _rag_models_catalog_cache is not None
                and (now - _rag_models_catalog_cache_ts) < cache_seconds
                and _rag_models_catalog_cache
            ):
                rows = _rag_models_catalog_cache
            else:
                rows = await _external_rag_models(None)
                if rows:
                    _rag_models_catalog_cache = rows
                    _rag_models_catalog_cache_ts = time.monotonic()

    if not model_type:
        return list(rows)
    return [r for r in rows if (r.get("kind") or "embedding") == model_type]


@router.get("/api/rag/models")
async def list_rag_models(
    current_user: Annotated[dict, Depends(get_current_user)],
    type: str | None = None,
    scope: Optional[str] = None,
    project_id: Optional[str] = None,
    agent_id: Optional[int] = None,
):
    """Каталог embedding/reranker для UI: local (models/rag) + CORSUR/PHOENIX.

    ```scope``` — чей выбор подсвечивать как current: project (по умолчанию) или agent.
    ```project_id``` / ```agent_id``` — настройки конкретного проекта/агента.
    """
    try:
        data: dict = {
            "models": {"embedding": [], "reranker": []},
            "offline": True,
            "cluster_default": {},
        }
        # 1) Локальные папки из SVC-RAG-MODELS (./models/rag/*) — основной каталог.
        if rag_models_client:
            try:
                local = await rag_models_client.list_models(type)
                if isinstance(local, dict):
                    data["offline"] = bool(local.get("offline", True))
                    if isinstance(local.get("cluster_default"), dict):
                        data["cluster_default"] = local["cluster_default"]
                    elif isinstance(local.get("current"), dict):
                        data["cluster_default"] = local["current"]
                    if isinstance(local.get("models_dir"), str):
                        data["models_dir"] = local["models_dir"]
                    local_models = local.get("models") or {}
                    for kind_key in ("embedding", "reranker"):
                        if type and kind_key != type:
                            continue
                        for row in local_models.get(kind_key) or []:
                            if not isinstance(row, dict) or not row.get("path"):
                                continue
                            row = dict(row)
                            row.setdefault("source", "local")
                            row.setdefault("kind", kind_key)
                            data["models"].setdefault(kind_key, []).append(row)
            except Exception:
                logger.exception(
                    "Каталог локальных RAG-моделей (svc-rag-models) недоступен"
                )
        # 2) Внешние шлюзы (CORSUR / PHOENIX) — опционально, если настроены.
        try:
            external_rows = await _external_rag_models_cached(type)
            for row in external_rows or []:
                kind_key = row.get("kind") or "embedding"
                data["models"].setdefault(kind_key, []).append(row)
            catalog_for_warn: list = []
            for kind_key in ("embedding", "reranker"):
                catalog_for_warn.extend(data["models"].get(kind_key) or [])
            _warn_if_dangling_cluster_default(catalog_for_warn)
        except Exception:
            logger.exception("Каталог внешних RAG-моделей недоступен")
        sc = normalize_scope(scope)
        entity_id = _entity_id_from_params(sc, project_id, agent_id)
        entity_rag = await get_entity_rag_settings(sc, entity_id)
        return _overlay_user_model_current(data, entity_rag)
    except Exception as e:
        logger.exception("list_rag_models error")
        raise HTTPException(status_code=502, detail=public_error_detail(e))


@router.get("/api/rag/models/current")
async def get_rag_models_current(
    current_user: Annotated[dict, Depends(get_current_user)],
    scope: Optional[str] = None,
    project_id: Optional[str] = None,
    agent_id: Optional[int] = None,
):
    try:
        # Кластерная «текущая модель» есть только у svc-rag-models. Для шлюзовых
        # провайдеров источник истины — настройки самой сущности.
        data: dict = {}
        if rag_models_client:
            try:
                data = await rag_models_client.get_current()
            except Exception:
                logger.warning(
                    "[RAG-CATALOG] /models/current недоступен — отдаю только выбор пользователя",
                    exc_info=True,
                )
                data = {}
        if not isinstance(data, dict):
            data = {}
        sc = normalize_scope(scope)
        entity_id = _entity_id_from_params(sc, project_id, agent_id)
        entity_rag = await get_entity_rag_settings(sc, entity_id)
        return _overlay_user_model_current(data, entity_rag)
    except Exception as e:
        logger.exception("get_rag_models_current error")
        raise HTTPException(status_code=502, detail=public_error_detail(e))


async def _current_cluster_embedding_dim() -> Optional[int]:
    """Текущая dim загруженной модели / health (до миграции схемы)."""
    if not rag_models_client:
        return None
    try:
        health = await rag_models_client.health()
        if isinstance(health, dict) and health.get("embedding_dim"):
            return int(health["embedding_dim"])
    except Exception:
        logger.debug("health embedding_dim недоступен", exc_info=True)
    return None


async def _select_external_rag_model(
    model_type: str,
    model_path: str,
    user_id: str,
    *,
    path_prefix: str,
    source_label: str,
    scope: Optional[str] = None,
    entity_id: Optional[str] = None,
    project_id: Optional[str] = None,
    agent_id: Optional[int] = None,
):
    """Персональный выбор внешней OpenAI-совместимой модели (phoenix/…, corsur/…).

    Кластер НЕ переключаем: после B2/B3 модель едет в теле каждого запроса.
    """
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    prefix = f"{path_prefix}/"
    if not model_path.lower().startswith(prefix):
        raise HTTPException(status_code=400, detail=f"Ожидался путь {prefix}<id>")
    model_id = model_path.split("/", 1)[1].strip()
    if not model_id:
        raise HTTPException(
            status_code=400, detail=f"Пустой id модели {source_label}"
        )

    try:
        catalog = await _external_rag_models(model_type)
    except Exception as e:
        logger.exception("%s catalog error", source_label)
        raise HTTPException(status_code=502, detail=public_error_detail(e))
    paths = {
        str((r or {}).get("path") or "").strip()
        for r in (catalog or [])
        if str((r or {}).get("path") or "").lower().startswith(prefix)
    }
    if paths and model_path not in paths:
        raise HTTPException(
            status_code=400,
            detail=f"Модель {source_label} {model_path!r} не найдена в каталоге",
        )
    result: dict = {
        "success": True,
        "model_type": model_type,
        "model_path": model_path,
        "cluster_changed": False,
    }

    if model_type == "embedding":
        merged = await save_entity_rag_settings(
            scope, entity_id, {"rag_embedding_model_path": model_path}, user_id
        )
        if _reindex_on_model_change():
            asyncio.create_task(
                _run_background_reindex_for_user(
                    user_id,
                    chunk_params_from_rag_settings(merged),
                    scope,
                    project_id=project_id,
                    agent_id=agent_id,
                )
            )
    else:
        await save_entity_rag_settings(
            scope, entity_id, {"rag_reranker_model_path": model_path}, user_id
        )
    bump_rag_semantic_cache()
    logger.info(
        "[RAG-CFG] выбор %s для %s %s пользователем %s: type=%s path=%s "
        "(кластер и Библиотека не меняются)",
        source_label,
        "агента" if scope == "agent" else "проекта",
        entity_id,
        user_id,
        model_type,
        model_path,
    )
    result["message"] = "Модель сохранена в настройках этого агента или проекта"
    result["reindexed"] = model_type == "embedding" and _reindex_on_model_change()
    return result


async def _select_phoenix_rag_model(
    model_type: str, model_path: str, user_id: str, 
    scope: Optional[str] = None,
):
    return await _select_external_rag_model(
        model_type,
        model_path,
        user_id,
        path_prefix="phoenix",
        source_label="Phoenix",
        scope=scope,
    )


@router.post("/api/rag/models/select")
async def select_rag_model(
    body: RagModelSelectRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Выбор embedding/reranker: загружает модель и сохраняет путь пользователю.

    - Модель принадлежит агенту или проекту: у других сущностей не меняется,
      но меняется у всех, кому эта расшарена. Нужны права редактора.
    - Memory RAG не переиндексируется.
    - Смена dim из UI запрещена.
    - При той же dim — reindex только документов этой сущности.
    """
    user_id = str(current_user.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    model_type = str(body.model_type or "").strip().lower()
    if model_type not in ("embedding", "reranker"):
        raise HTTPException(status_code=400, detail="model_type должен быть embedding или reranker")
    model_path = str(body.model_path or "").strip()
    if not model_path:
        raise HTTPException(status_code=400, detail="model_path обязателен")
    scope = normalize_scope(body.scope)
    entity_id = _entity_id_from_params(scope, body.project_id, body.agent_id)
    if not entity_id:
        raise HTTPException(
            status_code=400,
            detail="Укажите project_id или agent_id: модель принадлежит сущности",
        )
    await _require_entity_editor(scope, entity_id, user_id)
    logger.debug(
        "[RAG-CFG] Выбор RAG-модели user=%s scope=%s entity=%s: type=%s, path=%s",
        user_id,
        scope,
        entity_id,
        model_type,
        model_path,
    )
    if model_path.lower().startswith("huggingface/"):
        raise HTTPException(
            status_code=400,
            detail="Источник huggingface отключён: выберите модель из каталога RAG",
        )
    prefix = model_path.split("/", 1)[0].strip().lower() if "/" in model_path else ""
    gateway = next(
        (
            e
            for e in await _effective_rag_gateway_catalog()
            if str(e.get("path_prefix") or "").lower() == prefix
        ),
        None,
    )
    if gateway:
        return await _select_external_rag_model(
            model_type,
            model_path,
            user_id,
            path_prefix=str(gateway["path_prefix"]),
            source_label=str(gateway["provider_id"]),
            scope=scope,
            entity_id=entity_id,
            project_id=body.project_id,
            agent_id=body.agent_id,
        )
    # local/* — нужен svc-rag-models
    require_service("rag_models")
    if not rag_models_client:
        raise HTTPException(status_code=503, detail="RAG-models service недоступен")
    try:
        await _validate_local_model_path(model_type, model_path)
        # Кластер не трогаем: модель едет в теле запроса, svc-rag-models поднимет
        # её сам по имени (LRU из фазы A), вектора лягут в таблицу своей
        # размерности. Прежний select_model менял модель ВСЕМ, а 409-гард
        # запрещал чужую dim — с раздельными таблицами это больше не нужно.
        result: dict = {
            "success": True,
            "model_type": model_type,
            "model_path": model_path,
            "cluster_changed": False,
        }

        if model_type == "embedding":
            merged = await save_entity_rag_settings(
                scope, entity_id, {"rag_embedding_model_path": model_path}, user_id
            )
            if _reindex_on_model_change():
                logger.info(
                    "[REINDEX-USER] смена embedding user=%s scope=%s entity=%s → scoped reindex (без Memory)",
                    user_id,
                    scope,
                    entity_id,
                )
                asyncio.create_task(
                    _run_background_reindex_for_user(
                        user_id,
                        chunk_params_from_rag_settings(merged),
                        scope,
                        project_id=body.project_id,
                        agent_id=body.agent_id,
                    )
                )
        else:
            await save_entity_rag_settings(
                scope, entity_id, {"rag_reranker_model_path": model_path}, user_id
            )
            bump_rag_semantic_cache()
        result["message"] = "Модель сохранена в настройках этого агента или проекта"
        result["reindexed"] = (
            model_type == "embedding" and _reindex_on_model_change()
        )
        logger.info(
            "[RAG-CFG] выбор модели для %s %s пользователем %s: type=%s path=%s "
            "(кластер и Библиотека не меняются)",
            "агента" if scope == "agent" else "проекта",
            entity_id,
            user_id,
            model_type,
            model_path,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("select_rag_model error")
        raise HTTPException(status_code=502, detail=public_error_detail(e))


@router.post("/api/kb/documents")
async def kb_upload_document(
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[dict, Depends(get_current_user)],
    chunking_strategy: Annotated[Optional[str], Form()] = None,
    agent_id: Annotated[Optional[int], Form()] = None,
    knowledge_base_id: Annotated[Optional[int], Form()] = None,
    relative_path: Annotated[Optional[str], Form()] = None,
):
    """Загрузка в KB: база знаний (knowledge_base_id) или legacy agent KB.

    owner_user_id = автор сущности (не uploader): файлы редактора принадлежат
    владельцу. Параметры чанкинга — из entity RAG settings сущности.
    ```relative_path``` — путь внутри каталога (webkitRelativePath) для UI-папок.
    """
    require_service("rag")
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    username = _rag_upload_username(current_user)
    uploader_id = str(current_user.get("user_id") or "").strip().lower()
    file_object_name = None
    file_bucket = None
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Файл пустой")
        fn = file.filename or "unknown"
        rel_path = (relative_path or "").replace("\\", "/").strip().lstrip("/") or None

        owner_user_id = uploader_id
        resolved_agent_id: Optional[int] = None
        resolved_kb_id: Optional[int] = None
        entity_rag: dict = {}
        entity_scope_label = "агента"
        pvc_scope = "agent"

        if knowledge_base_id is not None:
            from backend.database.init_db import get_knowledge_base_repository
            from backend.database.postgresql.knowledge_base_models import (
                KB_PERMISSION_EDITOR,
                KB_PERMISSION_OWNER,
            )

            kb_repo = get_knowledge_base_repository()
            admin = bool(current_user.get("is_admin"))
            permission = await kb_repo.get_user_permission(
                int(knowledge_base_id), uploader_id, is_admin=admin
            )
            if permission not in (KB_PERMISSION_OWNER, KB_PERMISSION_EDITOR):
                raise HTTPException(
                    status_code=403,
                    detail="Недостаточно прав для загрузки файлов в базу знаний",
                )
            kb = await kb_repo.get_by_id(
                int(knowledge_base_id), uploader_id, is_admin=admin
            )
            if not kb:
                raise HTTPException(status_code=404, detail="База знаний не найдена")
            owner_user_id = str(kb.author_id or uploader_id).strip().lower()
            resolved_kb_id = int(knowledge_base_id)
            entity_rag = await get_entity_rag_settings("kb", resolved_kb_id)
            entity_scope_label = "базы знаний"
            pvc_scope = "kb"
        elif agent_id is not None:
            from backend.database.init_db import get_agent_repository
            from backend.database.postgresql.agent_models import (
                AGENT_PERMISSION_EDITOR,
                AGENT_PERMISSION_OWNER,
            )

            agent_repo = get_agent_repository()
            admin = bool(current_user.get("is_admin"))
            permission = await agent_repo.get_user_permission(
                int(agent_id), uploader_id, is_admin=admin
            )
            if permission not in (AGENT_PERMISSION_OWNER, AGENT_PERMISSION_EDITOR):
                raise HTTPException(
                    status_code=403,
                    detail="Недостаточно прав для загрузки файлов в KB агента",
                )
            agent = await agent_repo.get_agent(
                int(agent_id), uploader_id, is_admin=admin
            )
            if not agent:
                raise HTTPException(status_code=404, detail="Агент не найден")
            owner_user_id = str(agent.author_id or uploader_id).strip().lower()
            resolved_agent_id = int(agent_id)
            entity_rag = await get_entity_rag_settings("agent", resolved_agent_id)

        # До PVC/индексации: не пишем исходник и не чанкуем повторно.
        await _raise_if_kb_duplicate(
            content,
            fn,
            agent_id=resolved_agent_id,
            knowledge_base_id=resolved_kb_id,
        )

        from backend.services.user_rag_settings import (
            embedding_fields_from_rag_settings,
            reranker_fields_from_rag_settings,
        )

        emb_fields = embedding_fields_from_rag_settings(entity_rag)
        has_entity = resolved_agent_id is not None or resolved_kb_id is not None
        if has_entity and not emb_fields.get("embedding_model"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Сначала выберите модель эмбеддингов в настройках РАГ для {entity_scope_label}"
                ),
            )
        from backend.services.entity_rag_env import get_entity_reranking_enabled

        if has_entity and bool(
            entity_rag.get("rag_reranking_enabled", get_entity_reranking_enabled())
        ):
            rer_fields = reranker_fields_from_rag_settings(entity_rag)
            if not rer_fields.get("reranker_model"):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Сначала выберите модель реранкера в настройках РАГ для {entity_scope_label}"
                    ),
                )

        chunk_params = (
            chunk_params_from_rag_settings(entity_rag)
            if entity_rag
            else get_rag_chunk_index_params()
        )
        strategy = (chunking_strategy or "").strip().lower()
        if strategy and strategy in _VALID_CHUNKING_STRATEGIES | {"universal"}:
            chunk_params["chunking_strategy"] = strategy
        elif not strategy:
            if not has_entity:
                chunk_params = get_library_chunk_index_params()

        if use_rag_pvc():
            file_object_name = save_rag_bytes_to_pvc(
                content,
                fn,
                scope=pvc_scope,  # type: ignore[arg-type]
                username=username,
                prefix="kb_",
                content_type=file.content_type or "application/octet-stream",
            )
            if not file_object_name:
                raise HTTPException(
                    status_code=500,
                    detail=f"Не удалось сохранить файл в PVC — проверьте {RAG_PVC_DIR_ENV} и mount /ragdb",
                )
            file_bucket = RAG_PVC_BUCKET_MARKER
        try:
            out = await rag_client.kb_upload_document(
                file_bytes=content,
                filename=fn,
                minio_object=file_object_name,
                minio_bucket=file_bucket,
                owner_user_id=owner_user_id or None,
                agent_id=resolved_agent_id,
                knowledge_base_id=resolved_kb_id,
                uploaded_by=username or None,
                relative_path=rel_path,
                **chunk_params,
                **emb_fields,
            )
        except Exception as e:
            is_index_timeout = _cleanup_rag_source_after_failure(
                e, file_object_name, file_bucket
            )
            if is_index_timeout:
                raise HTTPException(
                    status_code=504, detail=_INDEX_TIMEOUT_DETAIL
                ) from e
            raise
        if resolved_kb_id is not None and isinstance(out, dict) and out.get("ok"):
            try:
                from backend.database.init_db import get_knowledge_base_repository

                await get_knowledge_base_repository().bump_document_count(
                    resolved_kb_id, 1
                )
            except Exception:
                logger.exception(
                    "Не удалось обновить document_count для kb=%s", resolved_kb_id
                )
        bump_rag_semantic_cache()
        return out
    except HTTPException:
        raise
    except Exception as e:
        from backend.settings.rag_client import RagDuplicateDocument

        if isinstance(e, RagDuplicateDocument):
            from backend.rag_query.document_duplicate import raise_duplicate_http

            raise_duplicate_http(
                document_id=e.document_id,
                filename=e.filename or (file.filename if file else None),
                message=str(e) or None,
            )
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/kb/documents")
async def kb_list_documents(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    require_service("rag")
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    try:
        docs = await rag_client.kb_list_documents()
        return {"documents": docs}
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.delete("/api/kb/documents/{document_id}")
async def kb_delete_document(
    document_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    agent_id: Annotated[Optional[int], Query()] = None,
):
    """Удалить или отвязать KB-документ.

    ``agent_id`` — карточка агента, из которой удаляют:
    * файл ещё у другого агента → только отвязка у текущего (хранилище целое);
    * больше никто не ссылается → физическое удаление из SVC-RAG/MinIO.

    Без ``agent_id`` (панель БЗ) — всегда физическое удаление.
    """
    require_service("rag")  # FEATURE-FLAG
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    _require_storage_for_delete()
    try:
        doc_id = int(document_id)
        if agent_id is not None:
            try:
                from backend.database.init_db import get_agent_repository

                agent_repo = get_agent_repository()
                if agent_repo is not None:
                    aid = int(agent_id)
                    foreign = await agent_repo.find_documents_claimed_by_other_agents(
                        aid, [doc_id]
                    )
                    if foreign:
                        # Отвязка только у этого агента — чужие карточки не трогаем.
                        removed = await agent_repo.remove_kb_document_ids(aid, [doc_id])
                        logger.info(
                            "KB-документ %s отвязан от агента %s "
                            "(ещё используется другими, physical delete пропущен), "
                            "config_updated=%s",
                            doc_id,
                            aid,
                            removed,
                        )
                        return {
                            "ok": True,
                            "document_id": doc_id,
                            "unbound_only": True,
                        }
            except Exception:
                logger.exception(
                    "Не удалось проверить/отвязать KB-документ %s у агента %s",
                    document_id,
                    agent_id,
                )

        out = await rag_client.kb_delete_document(doc_id)
        if isinstance(out, dict) and out.get("ok") is False:
            raise HTTPException(status_code=404, detail="Документ не найден")
        if isinstance(out, dict):
            _delete_rag_source_file(out.get("minio_object"), out.get("minio_bucket"))
        # На всякий случай убрать id из config текущего агента после физического delete.
        if agent_id is not None:
            try:
                from backend.database.init_db import get_agent_repository

                agent_repo = get_agent_repository()
                if agent_repo is not None:
                    await agent_repo.remove_kb_document_ids(int(agent_id), [doc_id])
            except Exception:
                logger.exception(
                    "Не удалось убрать kb_document_id=%s из config агента %s",
                    doc_id,
                    agent_id,
                )
        bump_rag_semantic_cache()
        return {"ok": True, "document_id": doc_id, "unbound_only": False}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/api/memory-rag/documents")
async def memory_rag_upload(
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Глобальный / memory RAG (Настройки → RAG). MinIO или PVC — по флагу RAG_USE_PVC."""
    require_service("rag")  # FEATURE-FLAG
    if not use_rag_pvc():
        require_service("minio")  # FEATURE-FLAG
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    _require_storage_for_delete()
    username = _rag_upload_username(current_user)
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Файл пустой")
        fn = file.filename or "unknown"
        await _raise_if_memory_duplicate(content, fn)
        ext = os.path.splitext(fn)[1] or ".bin"
        file_object_name = None
        memory_bucket = None
        if use_rag_pvc():
            file_object_name = save_rag_bytes_to_pvc(
                content,
                fn,
                scope="memory",
                username=username,
                prefix="memrag*",
                content_type=file.content_type or "application/octet-stream",
            )
            if not file_object_name:
                raise HTTPException(
                    status_code=500,
                    detail=f"Не удалось сохранить файл в PVC — проверьте {RAG_PVC_DIR_ENV} и mount /ragdb",
                )
            memory_bucket = RAG_PVC_BUCKET_MARKER
        else:
            memory_bucket = settings.minio.memory_rag_bucket_name
            if minio_client:
                try:
                    minio_client.ensure_bucket(memory_bucket)
                    file_object_name = minio_client.generate_object_name(prefix="memrag*", extension=ext)
                    minio_client.upload_file(
                        content,
                        file_object_name,
                        content_type=file.content_type or "application/octet-stream",
                        bucket_name=memory_bucket,
                    )
                except Exception as e:
                    logger.exception("MinIO memory-rag upload")
                    raise HTTPException(status_code=500, detail=f"MinIO: {e}") from e
        try:
            # Библиотека памяти: всегда universal chunking
            chunk_params = get_library_chunk_index_params()
            result = await rag_client.memory_rag_index_document(
                file_bytes=content,
                filename=fn,
                minio_object=file_object_name,
                minio_bucket=memory_bucket if file_object_name else None,
                **chunk_params,
            )
        except Exception as e:
            from backend.settings.rag_client import RagDuplicateDocument

            logger.exception("Ошибка операции")
            is_index_timeout = _cleanup_rag_source_after_failure(
                e, file_object_name, memory_bucket
            )
            if isinstance(e, RagDuplicateDocument):
                from backend.rag_query.document_duplicate import raise_duplicate_http

                raise_duplicate_http(
                    document_id=e.document_id,
                    filename=e.filename or fn,
                    message=str(e) or None,
                )
            if is_index_timeout:
                raise HTTPException(
                    status_code=504,
                    detail=_INDEX_TIMEOUT_DETAIL,
                ) from e
            raise HTTPException(
                status_code=502, detail=public_error_detail(e, UPSTREAM_ERROR_DETAIL)
            ) from e
        bump_rag_semantic_cache()
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/api/memory-rag/documents")
async def memory_rag_list(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    require_service("rag")
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    try:
        docs = await rag_client.memory_rag_list_documents()
        return {"documents": docs}
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.delete("/api/memory-rag/documents/{document_id}")
async def memory_rag_delete(
    document_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    require_service("rag")
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    _require_storage_for_delete()
    try:
        out = await rag_client.memory_rag_delete_document(document_id)
        if not out.get("ok"):
            raise HTTPException(status_code=404, detail="Документ не найден")
        _delete_rag_source_file(out.get("minio_object"), out.get("minio_bucket"))
        bump_rag_semantic_cache()
        return {"ok": True, "document_id": document_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
