"""
routes/project_rag.py - RAG файлов проектов (MinIO/PVC + SVC-RAG) и оркестрационное удаление проекта
"""

import os
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from backend.app_state import minio_client, rag_client, get_rag_chunk_index_params, settings
from backend.auth.jwt_handler import get_current_user, get_optional_user
from backend.rag_query.semantic_cache import bump_rag_semantic_cache
from backend.services.user_rag_settings import chunk_params_from_rag_settings, get_entity_rag_settings
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress
from backend.settings.service_toggles import is_service_enabled, require_service
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
from backend.utils.http_errors import public_error_detail

logger = get_logger(__name__)

router = APIRouter(tags=["project-rag"])


def _rag_upload_username(current_user: dict) -> str:
    return current_user.get("username") or current_user.get("user_id") or "anonymous"


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
    if not object_name or not bucket:
        return
    if is_rag_pvc_bucket(bucket):
        delete_rag_pvc_file(object_name, bucket)
        return
    if minio_client:
        try:
            minio_client.delete_file(object_name, bucket_name=bucket)
        except Exception:
            logger.exception("MinIO delete project-rag object=%s bucket=%s", object_name, bucket)


async def _require_project_owner(project_id: str, current_user: dict) -> str:
    """403, если проект не принадлежит текущему пользователю."""
    user_id = str((current_user or {}).get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    from backend.database.init_db import get_project_repository

    repo = get_project_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="База данных недоступна")
    project = await repo.get_by_id(user_id, project_id)
    if not project:
        raise HTTPException(status_code=403, detail="Нет доступа к проекту")
    return user_id

async def _raise_if_project_duplicate(
    project_id: str,
    content: bytes,
    filename: str,
) -> None:
    from backend.rag_query.document_duplicate import (
        DUPLICATE_MESSAGE,
        file_content_hash,
        raise_duplicate_http,
    )

    if not rag_client:
        return
    digest = file_content_hash(content)
    try:
        out = await rag_client.project_rag_check_duplicate(
            project_id=project_id,
            content_hash=digest,
            filename=filename,
            size=len(content),
        )
    except Exception:
        logger.exception("Project RAG duplicate-check failed; продолжаем загрузку")
        return
    if isinstance(out, dict) and out.get("duplicate"):
        raise_duplicate_http(
            document_id=out.get("document_id"),
            filename=out.get("filename") or filename,
            message=DUPLICATE_MESSAGE,
        )

@router.post("/api/project-rag/projects/{project_id}/documents")
async def project_rag_upload(
    project_id: str,
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Загрузить файл в RAG-хранилище проекта: MinIO или PVC + индексация SVC-RAG."""
    require_service("rag")  # FEATURE-FLAG
    if not use_rag_pvc():
        require_service("minio")  # FEATURE-FLAG
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    await _require_project_owner(project_id, current_user)
    username = _rag_upload_username(current_user)
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Файл пустой")
        fn = file.filename or "unknown"
        ext = os.path.splitext(fn)[1] or ".bin"

        # Модели — до записи в PVC/MinIO, чтобы не оставлять «сирот» при 400.
        user_id = str(current_user.get("user_id") or "").strip()
        project_rag = await get_entity_rag_settings("project", project_id)
        from backend.services.user_rag_settings import (
            embedding_fields_from_rag_settings,
            reranker_fields_from_rag_settings,
        )

        emb_fields = embedding_fields_from_rag_settings(project_rag)
        if not emb_fields.get("embedding_model"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Сначала выберите модель эмбеддингов в настройках РАГ для проекта"
                ),
            )
        if bool(project_rag.get("rag_reranking_enabled", True)):
            rer_fields = reranker_fields_from_rag_settings(project_rag)
            if not rer_fields.get("reranker_model"):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Сначала выберите модель реранкера в настройках РАГ для проекта"
                    ),
                )

        chunk_params = (
            chunk_params_from_rag_settings(project_rag)
            if project_rag
            else get_rag_chunk_index_params()
        )

        # До PVC/MinIO и чанкинга — одинаковый файл в проекте нельзя заливать снова.
        await _raise_if_project_duplicate(project_id, content, fn)

        file_object_name = None
        project_bucket = None
        if use_rag_pvc():
            file_object_name = save_rag_bytes_to_pvc(
                content,
                fn,
                scope="project",
                username=username,
                prefix=f"proj_{project_id}_",
                content_type=file.content_type or "application/octet-stream",
            )
            if not file_object_name:
                raise HTTPException(
                    status_code=500,
                    detail=f"Не удалось сохранить файл в PVC — проверьте {RAG_PVC_DIR_ENV} и mount /ragdb",
                )
            project_bucket = RAG_PVC_BUCKET_MARKER
        else:
            project_bucket = settings.minio.project_rag_bucket_name
            if minio_client:
                try:
                    minio_client.ensure_bucket(project_bucket)
                    file_object_name = minio_client.generate_object_name(
                        prefix=f"proj_{project_id}_", extension=ext
                    )
                    minio_client.upload_file(
                        content,
                        file_object_name,
                        content_type=file.content_type or "application/octet-stream",
                        bucket_name=project_bucket,
                    )
                except Exception as e:
                    logger.exception("MinIO загрузка project-rag")
                    raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
        try:
            # Нарезка и модель — из настроек САМОГО проекта: файл должен лечь
            # теми же параметрами, которыми проект потом будет искать.
            result = await rag_client.project_rag_upload_document(
                file_bytes=content,
                filename=fn,
                project_id=project_id,
                minio_object=file_object_name,
                minio_bucket=project_bucket if file_object_name else None,
                owner_user_id=user_id or None,
                **chunk_params,
                **emb_fields,
            )
        except HTTPException:
            if file_object_name and project_bucket:
                with logged_suppress(logger):
                    _delete_rag_source_file(file_object_name, project_bucket)
            raise
        except Exception as e:
            from backend.settings.rag_client import RagDuplicateDocument

            if file_object_name and project_bucket:
                with logged_suppress(logger):
                    _delete_rag_source_file(file_object_name, project_bucket)
            if isinstance(e, RagDuplicateDocument):
                from backend.rag_query.document_duplicate import raise_duplicate_http

                raise_duplicate_http(
                    document_id=e.document_id,
                    filename=e.filename or fn,
                    message=str(e) or None,
                )
            logger.exception("SVC-RAG project-rag индексация")
            raise HTTPException(status_code=422, detail=str(e)) from e
        bump_rag_semantic_cache()
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка загрузки project-rag")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/api/project-rag/projects/{project_id}/documents")


async def project_rag_list(
    project_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Список файлов RAG конкретного проекта"""
    require_service("rag")
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    await _require_project_owner(project_id, current_user)
    try:
        docs = await rag_client.project_rag_list_documents(project_id)
        return {"documents": docs}
    except Exception as e:
        logger.exception("Ошибка списка project-rag")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.delete("/api/project-rag/projects/{project_id}/documents/{document_id}")


async def project_rag_delete_document(
    project_id: str,
    document_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Удалить один файл из RAG проекта (PVC/MinIO + Postgres)"""
    require_service("rag")
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    await _require_project_owner(project_id, current_user)
    _require_storage_for_delete()
    try:
        out = await rag_client.project_rag_delete_document(project_id, document_id)
        if not out.get("ok"):
            raise HTTPException(status_code=404, detail="Документ не найден")
        _delete_rag_source_file(out.get("minio_object"), out.get("minio_bucket"))
        bump_rag_semantic_cache()
        return {"ok": True, "document_id": document_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка удаления project-rag документа")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/api/project-rag/projects/{project_id}/search")


async def project_rag_search(
    project_id: str,
    body: dict,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Семантический поиск по RAG-файлам проекта"""
    require_service("rag")
    if not rag_client:
        raise HTTPException(status_code=503, detail="RAG service недоступен")
    await _require_project_owner(project_id, current_user)
    try:
        results = await rag_client.project_rag_search(
            query=body.get("query", ""),
            project_id=project_id,
            k=body.get("k", 8),
            document_id=body.get("document_id"),
            use_reranking=body.get("use_reranking"),
            strategy=body.get("strategy"),
        )
        return {
            "hits": [
                {
                    "content": c,
                    "score": s,
                    "document_id": doc_id,
                    "chunk_index": chunk_idx,
                }
                for c, s, doc_id, chunk_idx in results
            ]
        }
    except Exception as e:
        logger.exception("Ошибка поиска project-rag")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.delete("/api/projects/{project_id}")


async def delete_project(
    project_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """
    Оркестрационное удаление проекта:
    1. Удаляет все RAG-документы из SVC-RAG (Postgres) и PVC/MinIO
    2. Удаляет все диалоги проекта из MongoDB
    3. Удаляет метаданные проекта (инструкции и т.д.) из PostgreSQL
    """
    # Удаление проекта сносит и его файлы. Если тома нет - не начинаем: иначе
    # проект исчезнет, а файлы останутся на диске уже без всякой ссылки на них
    user_id = await _require_project_owner(project_id, current_user)
    _require_storage_for_delete()
    errors = []
    if rag_client and is_service_enabled("rag"):
        try:
            rag_out = await rag_client.project_rag_delete_project(project_id)
            minio_keys = rag_out.get("minio_keys", [])
            if minio_keys:
                for key_info in minio_keys:
                    _delete_rag_source_file(
                        key_info.get("minio_object"), key_info.get("minio_bucket")
                    )
            logger.debug(
                f"project_id={project_id}: удалено RAG-документов: {rag_out.get('deleted_count', 0)}"
            )
            bump_rag_semantic_cache()
        except Exception as e:
            logger.exception("Ошибка удаления RAG проекта")
            errors.append(f"RAG: {e}")
    else:
        errors.append("RAG service недоступен")
    try:
        from backend.database.memory_service import delete_project_memory

        deleted_convs = await delete_project_memory(project_id)
        logger.info(f"project_id={project_id}: удалено диалогов: {deleted_convs}")
    except Exception as e:
        logger.exception("Ошибка удаления MongoDB диалогов проекта")
        errors.append(f"MongoDB: {e}")
    try:
        from backend.database.init_db import get_project_repository

        repo = get_project_repository()
        deleted_meta = await repo.delete(user_id, project_id)
        if deleted_meta:
            logger.info(
                "project_id=%s: удалены метаданные проекта из PostgreSQL", project_id
            )
    except Exception as e:
        logger.exception("Ошибка удаления метаданных проекта")
        errors.append(f"PostgreSQL: {e}")
    return {"ok": len(errors) == 0, "project_id": project_id, "errors": errors}

