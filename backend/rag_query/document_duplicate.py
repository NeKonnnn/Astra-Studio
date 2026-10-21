"""Общие константы и хелперы для дубликатов RAG-документов."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from fastapi import HTTPException

DUPLICATE_CODE = "duplicate_document"
DUPLICATE_MESSAGE = "Упс, такой документ уже подгружен в базу"


def file_content_hash(file_data: bytes) -> str:
    return hashlib.sha256(file_data or b"").hexdigest()


def duplicate_http_detail(
    *,
    document_id: Optional[int] = None,
    filename: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "code": DUPLICATE_CODE,
        "message": message or DUPLICATE_MESSAGE,
        "document_id": document_id,
        "filename": filename,
    }


def raise_duplicate_http(
    *,
    document_id: Optional[int] = None,
    filename: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    raise HTTPException(
        status_code=409,
        detail=duplicate_http_detail(
            document_id=document_id,
            filename=filename,
            message=message,
        ),
    )


def is_duplicate_detail(detail: Any) -> bool:
    if isinstance(detail, dict):
        return str(detail.get("code") or "").strip() == DUPLICATE_CODE
    if isinstance(detail, str):
        return DUPLICATE_CODE in detail or "уже подгружен в базу" in detail.lower()
    return False
