"""CEF-аудит операций SVC-RAG с PostgreSQL (INT005/INT006), по аналогии с backend."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _emit(
    event_id: str, *, status_code: Optional[int] = None, extra: Optional[dict] = None
) -> None:
    try:
        from app.core.cef_logger.cef_audit_context import cef_audit_peek
        from app.core.cef_logger.cef_logger import log_cef_event

        _req, _user = cef_audit_peek()
        log_cef_event(
            event_id,
            request=_req,
            current_user=_user,
            status_code=status_code,
            extra=extra or {},
        )
    except Exception:
        logger.debug("CEF postgres emit failed", exc_info=True)


def _extra(*, method_name: str, db: Any, request_uuid: Optional[str] = None) -> dict:
    return {
        "_deviceDirection": 1,
        "dhost": getattr(db, "host", None) or "postgresql",
        "dpt": int(getattr(db, "port", None) or 5432),
        "duser": getattr(db, "user", None) or "-",
        "methodName": method_name,
        "serviceName": "AstraChat-PostgreSQL",
        "requestUuid": request_uuid or uuid.uuid4().hex,
    }


def log_postgres_success(
    method_name: str, *, db: Any, request_uuid: Optional[str] = None
) -> None:
    _emit(
        "INT005",
        status_code=200,
        extra=_extra(method_name=method_name, db=db, request_uuid=request_uuid),
    )


def log_postgres_failure(
    method_name: str,
    error: str,
    *,
    db: Any,
    request_uuid: Optional[str] = None,
) -> None:
    extra = _extra(method_name=method_name, db=db, request_uuid=request_uuid)
    extra["codeStatus"] = "EXCEPTION"
    extra["textStatus"] = str(error)[:512]
    _emit("INT006", status_code=None, extra=extra)
