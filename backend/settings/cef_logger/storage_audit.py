"""
CEF-аудит операций с хранилищами (MongoDB, PostgreSQL, MinIO, RAG PVC)
по шаблонам INT005/INT006, FS001–FS004 и FS009–FS010.
"""

from __future__ import annotations

import os
import uuid
from typing import Optional, Tuple
from urllib.parse import urlparse

from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress

logger = get_logger(__name__)


def _mongo_endpoint() -> Tuple[str, int]:
    try:
        from settings import get_settings

        m = get_settings().mongodb
        return str(m.host or "mongodb"), int(m.port or 27017)
    except Exception:
        return os.getenv("MONGODB_HOST", "mongodb"), int(
            os.getenv("MONGODB_PORT", "27017")
        )


def _integration_extra(
    *,
    service_name: str,
    method_name: str,
    request_uuid: str,
    dhost: str,
    dpt: int,
) -> dict:
    return {
        "_deviceDirection": 1,
        "dhost": dhost,
        "dpt": dpt,
        "methodName": method_name,
        "serviceName": service_name,
        "requestUuid": request_uuid,
    }


def _emit_cef(
    event_id: str, *, status_code: Optional[int] = None, extra: Optional[dict] = None
) -> None:
    with logged_suppress(logger):
        from backend.settings.cef_logger.cef_audit_context import cef_audit_peek
        from backend.settings.cef_logger.cef_logger import log_cef_event

        _req, _user, _ = cef_audit_peek()
        log_cef_event(
            event_id,
            request=_req,
            current_user=_user,
            status_code=status_code,
            extra=extra,
        )


def log_mongo_success(method_name: str, request_uuid: Optional[str] = None) -> None:
    dhost, dpt = _mongo_endpoint()
    _emit_cef(
        "INT005",
        status_code=200,
        extra=_integration_extra(
            service_name="AstraChat-MongoDB",
            method_name=method_name,
            request_uuid=request_uuid or uuid.uuid4().hex,
            dhost=dhost,
            dpt=dpt,
        ),
    )


def log_mongo_failure(
    method_name: str, error: str, request_uuid: Optional[str] = None
) -> None:
    dhost, dpt = _mongo_endpoint()
    extra = _integration_extra(
        service_name="AstraChat-MongoDB",
        method_name=method_name,
        request_uuid=request_uuid or uuid.uuid4().hex,
        dhost=dhost,
        dpt=dpt,
    )
    extra["codeStatus"] = "EXCEPTION"
    extra["textStatus"] = str(error)[:512]
    _emit_cef("INT006", status_code=None, extra=extra)


def log_postgres_success(
    method_name: str, *, dhost: str, dpt: int, request_uuid: Optional[str] = None
) -> None:
    _emit_cef(
        "INT005",
        status_code=200,
        extra=_integration_extra(
            service_name="AstraChat-PostgreSQL",
            method_name=method_name,
            request_uuid=request_uuid or uuid.uuid4().hex,
            dhost=dhost,
            dpt=dpt,
        ),
    )


def log_postgres_failure(
    method_name: str,
    error: str,
    *,
    dhost: str,
    dpt: int,
    request_uuid: Optional[str] = None,
) -> None:
    extra = _integration_extra(
        service_name="AstraChat-PostgreSQL",
        method_name=method_name,
        request_uuid=request_uuid or uuid.uuid4().hex,
        dhost=dhost,
        dpt=dpt,
    )
    extra["codeStatus"] = "EXCEPTION"
    extra["textStatus"] = str(error)[:512]
    _emit_cef("INT006", status_code=None, extra=extra)


def _minio_bucket_uri(bucket: str, object_name: str = "") -> str:
    if object_name:
        return f"minio://{bucket}/{object_name}"
    return f"minio://{bucket}"


def log_minio_read_success(object_name: str, bucket_name: str) -> None:
    _emit_cef(
        "FS001",
        status_code=200,
        extra={
            "file": object_name,
            "bucket": _minio_bucket_uri(bucket_name, object_name),
        },
    )


def log_minio_read_failure(object_name: str, bucket_name: str, error: str) -> None:
    extra = {
        "file": object_name,
        "bucket": _minio_bucket_uri(bucket_name, object_name),
        "reason": str(error)[:300],
    }
    _emit_cef("FS002", status_code=500, extra=extra)


def log_minio_write_success(
    object_name: str, bucket_name: str, *, display_name: Optional[str] = None
) -> None:
    _emit_cef(
        "FS003",
        status_code=200,
        extra={
            "file": display_name or object_name,
            "bucket": _minio_bucket_uri(bucket_name, object_name),
        },
    )


def log_minio_write_failure(
    object_name: str,
    bucket_name: str,
    error: str,
    *,
    display_name: Optional[str] = None,
) -> None:
    extra = {
        "file": display_name or object_name,
        "bucket": _minio_bucket_uri(bucket_name),
        "reason": str(error)[:300],
    }
    _emit_cef("FS004", status_code=500, extra=extra)


def log_minio_remove_success(object_name: str, bucket_name: str) -> None:
    """Низкоуровневое удаление объекта MinIO (INT005, исходящее событие)."""
    try:
        from settings import get_settings

        endpoint = get_settings().minio.endpoint
    except Exception:
        endpoint = os.getenv("MINIO_ENDPOINT", "minio")
    parsed = urlparse(endpoint if "://" in endpoint else f"//{endpoint}")
    dhost = parsed.hostname or str(endpoint).split(":")[0] or "minio"
    dpt = int(parsed.port or os.getenv("MINIO_PORT", "9000"))
    _emit_cef(
        "INT005",
        status_code=200,
        extra=_integration_extra(
            service_name="AstraChat-MinIO",
            method_name=f"MinIO.removeObject({bucket_name}/{object_name})",
            request_uuid=uuid.uuid4().hex,
            dhost=dhost,
            dpt=dpt,
        ),
    )


def log_minio_remove_failure(object_name: str, bucket_name: str, error: str) -> None:
    try:
        from settings import get_settings

        endpoint = get_settings().minio.endpoint
    except Exception:
        endpoint = os.getenv("MINIO_ENDPOINT", "minio")
    parsed = urlparse(endpoint if "://" in endpoint else f"//{endpoint}")
    dhost = parsed.hostname or str(endpoint).split(":")[0] or "minio"
    dpt = int(parsed.port or os.getenv("MINIO_PORT", "9000"))
    extra = _integration_extra(
        service_name="AstraChat-MinIO",
        method_name=f"MinIO.removeObject({bucket_name}/{object_name})",
        request_uuid=uuid.uuid4().hex,
        dhost=dhost,
        dpt=dpt,
    )
    extra["codeStatus"] = "EXCEPTION"
    extra["textStatus"] = str(error)[:512]
    _emit_cef("INT006", status_code=None, extra=extra)


def _pvc_uri(object_key: str = "") -> str:
    root = (os.getenv("RAG_PVC_DIR") or "/ragdb").rstrip("/")
    if object_key:
        return f"file://{root}/{object_key.lstrip('/')}"
    return f"file://{root}"


def log_rag_pvc_write_success(
    filename: str,
    *,
    object_key: str,
    scope: str,
    file_size: Optional[int] = None,
) -> None:
    """Успешная запись исходника RAG в PVC (FS009) — аналог FS007 для attach / FS003 для MinIO."""
    extra: dict = {
        "fname": filename,
        "file": object_key,
        "bucket": _pvc_uri(object_key),
        "cs1": scope,
        "cs1Label": "RagPvcScope",
        "cs2": object_key,
        "cs2Label": "PvcRagObject",
    }
    if file_size is not None:
        extra["fsize"] = file_size
    _emit_cef("FS009", status_code=200, extra=extra)


def log_rag_pvc_write_failure(
    filename: str,
    reason: str,
    *,
    scope: str,
    file_size: Optional[int] = None,
) -> None:
    """Ошибка записи исходника RAG в PVC (FS010) — аналог FS008 для attach / FS004 для MinIO."""
    extra: dict = {
        "fname": filename,
        "file": filename,
        "bucket": _pvc_uri(),
        "reason": str(reason)[:300],
        "cs1": scope,
        "cs1Label": "RagPvcScope",
    }
    if file_size is not None:
        extra["fsize"] = file_size
    _emit_cef("FS010", status_code=500, extra=extra)


def log_rag_pvc_remove_success(object_name: str, *, scope: str) -> None:
    """Удаление исходника RAG из PVC

    Раньше здесь был INT005 - тот же код, что у вызовов MongoDB, PostgreSQL,
    MinIO и SVC-RAG.
    Хранилищу PVC уже принадлежат FS009/FS010 на запись — удаление логично
    туда же.
    """
    filename = str(object_name or "").rsplit("/", 1)[-1] or str(object_name or "")
    _emit_cef(
        "FS011",
        status_code=200,
        extra={
            "fname": filename,
            "file": object_name,
            "bucket": _pvc_uri(object_name),
            "cs1": scope,
            "cs1Label": "RagPvcScope",
            "cs2": object_name,
            "cs2Label": "PvcRagObject",
        },
    )


def log_rag_pvc_remove_failure(object_name: str, scope: str, error: str) -> None:
    """Ошибка удаления исходника RAG из PVC

    scope намеренно позиционный: так его передаёт
    rag_pvc.delete_rag_pvc_file, сигнатуру не меняем.
    """
    filename = str(object_name or "").rsplit("/", 1)[-1] or str(object_name or "")
    _emit_cef(
        "FS012",
        status_code=500,
        extra={
            "fname": filename,
            "file": object_name,
            "bucket": _pvc_uri(object_name),
            "reason": str(error)[:300],
            "cs1": scope,
            "cs1Label": "RagPvcScope",
            "cs2": object_name,
            "cs2Label": "PvcRagObject",
        },
    )
