"""Ensure/migrate legacy project_rag и memory_rag → entity Knowledge Bases.

Документы проекта и «Библиотеки» переезжают в ``knowledge_bases`` с
``meta.source`` = ``project_library`` | ``memory_library``. Поиск в чате идёт
через per-KB retrieve, legacy store search выключен.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from backend.settings.logging import get_logger

logger = get_logger(__name__)

META_SOURCE_PROJECT = "project_library"
META_SOURCE_MEMORY = "memory_library"
META_PROJECT_ID = "project_id"

MIGRATION_PROJECT_RAG = "project_rag_to_kb_v1"
MIGRATION_MEMORY_RAG = "memory_rag_to_kb_v1"
MIGRATION_AGENT_FILESEARCH = "agent_filesearch_to_kb_v1"


def _force(env_name: str) -> bool:
    return (os.getenv(env_name, "").strip().lower()) == "force"


def _meta_source(meta: Any) -> str:
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("source") or "").strip().lower()


def _meta_project_id(meta: Any) -> str:
    if not isinstance(meta, dict):
        return ""
    return str(meta.get(META_PROJECT_ID) or "").strip()


async def find_kb_by_meta_source(
    *,
    author_id: str,
    source: str,
    project_id: Optional[str] = None,
) -> Optional[Any]:
    """Найти БЗ автора с meta.source (+ optional project_id)."""
    from backend.database.init_db import get_knowledge_base_repository
    from backend.database.postgresql.knowledge_base_models import KnowledgeBaseFilters

    repo = get_knowledge_base_repository()
    if repo is None:
        return None
    uid = (author_id or "").strip().lower()
    want_source = (source or "").strip().lower()
    want_project = str(project_id or "").strip()
    filters = KnowledgeBaseFilters(view_option="my", limit=100, offset=0)
    try:
        items, _total = await repo.list_items(uid, filters, is_admin=False)
    except Exception:
        logger.exception("find_kb_by_meta_source: list failed")
        return None
    for item in items or []:
        meta = getattr(item, "meta", None) or {}
        if _meta_source(meta) != want_source:
            continue
        if want_project and _meta_project_id(meta) != want_project:
            continue
        return item
    return None


async def ensure_personal_library_kb(
    user_id: str,
    *,
    author_name: Optional[str] = None,
) -> Optional[Any]:
    """Personal KB для тумблера «Библиотека» (одна на пользователя)."""
    from backend.database.init_db import get_knowledge_base_repository
    from backend.database.postgresql.knowledge_base_models import KnowledgeBaseCreate

    uid = (user_id or "").strip().lower()
    if not uid:
        return None
    existing = await find_kb_by_meta_source(author_id=uid, source=META_SOURCE_MEMORY)
    if existing is not None:
        return existing
    repo = get_knowledge_base_repository()
    if repo is None:
        return None
    created = await repo.create(
        KnowledgeBaseCreate(
            name="Моя библиотека",
            description="Личная база знаний (бывшая общая библиотека)",
            is_public=False,
            meta={"source": META_SOURCE_MEMORY},
        ),
        author_id=uid,
        author_name=author_name or uid,
    )
    if created:
        logger.info(
            "[legacy→kb] создана personal KB id=%s user=%s",
            getattr(created, "id", None),
            uid,
        )
    return created


async def ensure_project_library_kb(
    user_id: str,
    project_id: str,
    *,
    project_name: Optional[str] = None,
    author_name: Optional[str] = None,
) -> Optional[Any]:
    """БЗ файлов проекта; прикрепляется к project.knowledge_base_ids."""
    from backend.database.init_db import (
        get_knowledge_base_repository,
        get_project_repository,
    )
    from backend.database.postgresql.knowledge_base_models import KnowledgeBaseCreate
    from backend.database.postgresql.project_models import ProjectUpdate
    from backend.services.user_rag_settings import (
        get_entity_rag_settings,
        save_entity_rag_settings,
    )

    uid = (user_id or "").strip().lower()
    pid = str(project_id or "").strip()
    if not uid or not pid:
        return None

    prepo = get_project_repository()
    proj = await prepo.get_by_id(uid, pid) if prepo else None
    if not isinstance(proj, dict):
        logger.warning("ensure_project_library_kb: проект %s не найден", pid)
        return None

    existing_ids = proj.get("knowledgeBaseIds") or proj.get("knowledge_base_ids") or []
    if not isinstance(existing_ids, list):
        existing_ids = []
    kb_repo = get_knowledge_base_repository()
    if kb_repo is None:
        return None

    # Уже прикреплена project_library БЗ?
    for raw in existing_ids:
        try:
            kid = int(raw)
        except (TypeError, ValueError):
            continue
        item = await kb_repo.get_by_id(kid, uid, is_admin=False)
        if item is None:
            continue
        meta = getattr(item, "meta", None) or {}
        if _meta_source(meta) == META_SOURCE_PROJECT and (
            not _meta_project_id(meta) or _meta_project_id(meta) == pid
        ):
            return item

    found = await find_kb_by_meta_source(
        author_id=uid, source=META_SOURCE_PROJECT, project_id=pid
    )
    if found is not None:
        await _attach_kb_to_project(prepo, uid, pid, proj, int(found.id))
        return found

    name = (project_name or proj.get("name") or pid).strip() or pid
    created = await kb_repo.create(
        KnowledgeBaseCreate(
            name=f"Проект: {name}",
            description="Файлы проекта (бывший project RAG)",
            is_public=False,
            meta={"source": META_SOURCE_PROJECT, META_PROJECT_ID: pid},
        ),
        author_id=uid,
        author_name=author_name or uid,
    )
    if created is None:
        return None

    # Скопировать index-настройки project → kb (чтобы emb совпал с залитыми docs).
    try:
        project_settings = await get_entity_rag_settings("project", pid)
        await save_entity_rag_settings(
            "kb",
            created.id,
            {
                k: project_settings.get(k)
                for k in (
                    "rag_embedding_model_path",
                    "rag_reranker_model_path",
                    "rag_chunking_strategy",
                    "rag_chunk_size",
                    "rag_chunk_overlap",
                    "rag_chat_top_k",
                    "rag_similarity_threshold",
                    "rag_reranking_enabled",
                    "rag_rerank_top_n",
                    "rag_strategy",
                )
                if project_settings.get(k) is not None
            },
            updated_by=uid,
        )
    except Exception:
        logger.exception(
            "ensure_project_library_kb: не скопировали RAG settings project→kb"
        )

    await _attach_kb_to_project(prepo, uid, pid, proj, int(created.id))
    logger.info(
        "[legacy→kb] создана project KB id=%s project=%s user=%s",
        created.id,
        pid,
        uid,
    )
    return created


async def _attach_kb_to_project(
    prepo: Any,
    user_id: str,
    project_id: str,
    proj: Dict[str, Any],
    kb_id: int,
) -> None:
    # Импорт здесь: ensure_project_library_kb тоже импортирует ProjectUpdate
    # локально — общий модульный импорт ломал _attach (NameError → 500/Failed to fetch).
    from backend.database.postgresql.project_models import ProjectUpdate

    existing = proj.get("knowledgeBaseIds") or proj.get("knowledge_base_ids") or []
    ids: List[int] = []
    for raw in existing if isinstance(existing, list) else []:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if kb_id not in ids:
        ids.append(kb_id)
    await prepo.update(
        user_id,
        project_id,
        ProjectUpdate(
            knowledge_bases_enabled=True,
            knowledge_base_ids=ids,
        ),
    )


async def ensure_agent_library_kb(
    user_id: str,
    agent_id: int,
    *,
    agent_name: Optional[str] = None,
    author_name: Optional[str] = None,
) -> Optional[Any]:
    """БЗ для legacy file_search документов агента."""
    from backend.database.init_db import get_knowledge_base_repository
    from backend.database.postgresql.knowledge_base_models import KnowledgeBaseCreate

    uid = (user_id or "").strip().lower()
    if not uid or not agent_id:
        return None
    source = "agent_library"
    from backend.database.postgresql.knowledge_base_models import KnowledgeBaseFilters

    repo = get_knowledge_base_repository()
    if repo is None:
        return None
    filters = KnowledgeBaseFilters(view_option="my", limit=100, offset=0)
    items, _ = await repo.list_items(uid, filters, is_admin=False)
    for item in items or []:
        meta = getattr(item, "meta", None) or {}
        if _meta_source(meta) != source:
            continue
        try:
            if int(meta.get("agent_id")) == int(agent_id):
                return item
        except (TypeError, ValueError):
            continue
    name = (agent_name or str(agent_id)).strip()
    return await repo.create(
        KnowledgeBaseCreate(
            name=f"Агент: {name}",
            description="Файлы агента (бывший file_search)",
            is_public=False,
            meta={"source": source, "agent_id": int(agent_id)},
        ),
        author_id=uid,
        author_name=author_name or uid,
    )


async def run_legacy_rag_migrations() -> None:
    """Идемпотентные миграции при старте (если включены env)."""
    if (os.getenv("LEGACY_RAG_TO_KB_MIGRATE", "").strip().lower()) in (
        "1",
        "true",
        "yes",
        "force",
    ):
        try:
            await _migrate_project_rag_docs()
        except Exception:
            logger.exception("migrate project_rag→kb failed")
        try:
            await _migrate_memory_rag_docs()
        except Exception:
            logger.exception("migrate memory_rag→kb failed")
        try:
            await _migrate_agent_filesearch()
        except Exception:
            logger.exception("migrate agent file_search→kb failed")


async def _migration_done(name: str) -> bool:
    try:
        from backend.database.init_db import get_entity_settings_repository

        repo = get_entity_settings_repository()
        if repo is None:
            return False
        if hasattr(repo, "migration_applied"):
            return bool(await repo.migration_applied(name))
    except Exception:
        logger.debug("migration_done check failed for %s", name, exc_info=True)
    return False


async def _mark_migration(name: str) -> None:
    try:
        from backend.database.init_db import get_entity_settings_repository

        repo = get_entity_settings_repository()
        if repo is not None and hasattr(repo, "mark_migration"):
            await repo.mark_migration(name)
    except Exception:
        logger.debug("mark_migration failed for %s", name, exc_info=True)


async def _clone_doc_bytes_to_kb(
    *,
    rag_client: Any,
    source: str,
    document_id: int,
    filename: str,
    knowledge_base_id: int,
    owner_user_id: str,
    project_id: Optional[str] = None,
) -> bool:
    """Скачать оригинал из legacy store и залить в entity KB.

    Если у клиента нет download — логируем и пропускаем (документы остаются
    в legacy до ручного переноса).
    """
    download = getattr(rag_client, f"{source}_download_document", None) or getattr(
        rag_client, "download_document", None
    )
    if not callable(download):
        # Попробуем generic get content
        get_content = getattr(rag_client, f"{source}_get_document_content", None)
        if not callable(get_content):
            logger.warning(
                "нет download API для %s doc=%s — skip migrate",
                source,
                document_id,
            )
            return False
    try:
        if callable(download):
            if source == "project_rag" and project_id:
                blob = await download(project_id, document_id)
            else:
                blob = await download(document_id)
        else:
            blob = await get_content(document_id)
    except Exception:
        logger.exception("download %s/%s failed", source, document_id)
        return False
    if not blob:
        return False
    content = blob if isinstance(blob, (bytes, bytearray)) else None
    if content is None and isinstance(blob, dict):
        content = blob.get("content") or blob.get("data")
    if not isinstance(content, (bytes, bytearray)):
        logger.warning("unexpected download payload for %s/%s", source, document_id)
        return False
    try:
        await rag_client.kb_upload_document(
            filename=filename or f"doc_{document_id}",
            content=bytes(content),
            owner_user_id=owner_user_id,
            knowledge_base_id=knowledge_base_id,
        )
        return True
    except Exception:
        logger.exception(
            "kb_upload migrate failed source=%s doc=%s kb=%s",
            source,
            document_id,
            knowledge_base_id,
        )
        return False


async def _migrate_project_rag_docs() -> None:
    name = MIGRATION_PROJECT_RAG
    if await _migration_done(name) and not _force("LEGACY_RAG_TO_KB_MIGRATE"):
        return
    from backend.app_state import rag_client
    from backend.database.init_db import get_project_repository

    if rag_client is None:
        return
    prepo = get_project_repository()
    if prepo is None:
        return
    # list projects — depends on repo API
    list_fn = getattr(prepo, "list_all_for_migration", None) or getattr(
        prepo, "list_all", None
    )
    if not callable(list_fn):
        logger.info(
            "[legacy→kb] project migration skipped: no list_all on project repo"
        )
        await _mark_migration(name)
        return
    projects = await list_fn()
    migrated = 0
    for proj in projects or []:
        if not isinstance(proj, dict):
            continue
        pid = str(proj.get("id") or proj.get("project_id") or "").strip()
        uid = str(proj.get("user_id") or proj.get("owner_id") or "").strip().lower()
        if not pid or not uid:
            continue
        try:
            rows = list(await rag_client.project_rag_list_documents(pid) or [])
        except Exception:
            logger.exception("project_rag_list_documents %s", pid)
            continue
        if not rows:
            continue
        kb = await ensure_project_library_kb(
            uid, pid, project_name=proj.get("name"), author_name=uid
        )
        if kb is None:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                did = int(row["id"])
            except (TypeError, ValueError, KeyError):
                continue
            ok = await _clone_doc_bytes_to_kb(
                rag_client=rag_client,
                source="project_rag",
                document_id=did,
                filename=str(row.get("filename") or f"doc_{did}"),
                knowledge_base_id=int(kb.id),
                owner_user_id=uid,
                project_id=pid,
            )
            if ok:
                migrated += 1
    logger.info("[legacy→kb] project_rag migrated docs≈%s", migrated)
    await _mark_migration(name)


async def _migrate_memory_rag_docs() -> None:
    name = MIGRATION_MEMORY_RAG
    if await _migration_done(name) and not _force("LEGACY_RAG_TO_KB_MIGRATE"):
        return
    from backend.app_state import rag_client

    if rag_client is None:
        await _mark_migration(name)
        return
    # Memory docs are global/shared — без per-user owner в list. Мигрируем
    # только если есть uploaded_by / owner в metadata.
    try:
        rows = list(await rag_client.memory_rag_list_documents() or [])
    except Exception:
        logger.exception("memory_rag_list_documents failed")
        await _mark_migration(name)
        return
    migrated = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        uid = str(
            meta.get("uploaded_by")
            or meta.get("owner_user_id")
            or row.get("uploaded_by")
            or ""
        ).strip().lower()
        if not uid:
            continue
        try:
            did = int(row["id"])
        except (TypeError, ValueError, KeyError):
            continue
        kb = await ensure_personal_library_kb(uid, author_name=uid)
        if kb is None:
            continue
        ok = await _clone_doc_bytes_to_kb(
            rag_client=rag_client,
            source="memory_rag",
            document_id=did,
            filename=str(row.get("filename") or f"doc_{did}"),
            knowledge_base_id=int(kb.id),
            owner_user_id=uid,
        )
        if ok:
            migrated += 1
    logger.info("[legacy→kb] memory_rag migrated docs≈%s", migrated)
    await _mark_migration(name)


async def _migrate_agent_filesearch() -> None:
    """Переписать agent config: legacy docs → knowledge_base_ids."""
    name = MIGRATION_AGENT_FILESEARCH
    if await _migration_done(name) and not _force("LEGACY_RAG_TO_KB_MIGRATE"):
        return
    from backend.database.init_db import get_agent_repository

    arepo = get_agent_repository()
    if arepo is None:
        await _mark_migration(name)
        return
    list_fn = getattr(arepo, "list_all_for_migration", None) or getattr(
        arepo, "list_all_agents", None
    )
    if not callable(list_fn):
        logger.info("[legacy→kb] agent migration skipped: no list_all")
        await _mark_migration(name)
        return
    agents = await list_fn()
    updated = 0
    for agent in agents or []:
        cfg = getattr(agent, "config", None)
        if isinstance(agent, dict):
            cfg = agent.get("config") or agent
            agent_id = agent.get("id") or agent.get("agent_id")
            author = agent.get("author_id") or agent.get("user_id")
            aname = agent.get("name")
        else:
            agent_id = getattr(agent, "id", None) or getattr(agent, "agent_id", None)
            author = getattr(agent, "author_id", None)
            aname = getattr(agent, "name", None)
        if not isinstance(cfg, dict):
            continue
        if not cfg.get("file_search_enabled"):
            continue
        raw_docs = cfg.get("kb_document_ids") or []
        if not isinstance(raw_docs, list) or not raw_docs:
            continue
        try:
            aid = int(agent_id)
        except (TypeError, ValueError):
            continue
        uid = str(author or "").strip().lower()
        if not uid:
            continue
        kb = await ensure_agent_library_kb(
            uid, aid, agent_name=str(aname or aid), author_name=uid
        )
        if kb is None:
            continue
        # Tag legacy docs with knowledge_base_id if SVC supports update metadata
        from backend.app_state import rag_client

        if rag_client is not None:
            tag = getattr(rag_client, "kb_tag_documents_knowledge_base", None)
            if callable(tag):
                try:
                    await tag(
                        [int(x) for x in raw_docs if str(x).strip().lstrip("-").isdigit()],
                        int(kb.id),
                    )
                except Exception:
                    logger.exception("tag agent docs kb failed agent=%s", aid)
        kb_ids = list(cfg.get("knowledge_base_ids") or [])
        try:
            kb_ids = [int(x) for x in kb_ids]
        except (TypeError, ValueError):
            kb_ids = []
        if int(kb.id) not in kb_ids:
            kb_ids.append(int(kb.id))
        cfg["knowledge_bases_enabled"] = True
        cfg["knowledge_base_ids"] = kb_ids
        # keep file_search for dual-read until cleanup; mark migrated
        cfg["_legacy_filesearch_migrated"] = True
        update_fn = getattr(arepo, "update_agent_config", None) or getattr(
            arepo, "update_config", None
        )
        if callable(update_fn):
            try:
                await update_fn(aid, cfg, user_id=uid)
                updated += 1
            except Exception:
                logger.exception("update agent config failed id=%s", aid)
    logger.info("[legacy→kb] agents updated≈%s", updated)
    await _mark_migration(name)
