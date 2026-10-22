"""Детекция дубликатов документов RAG по content hash (и legacy filename+size)."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

DUPLICATE_CODE = "duplicate_document"
DUPLICATE_MESSAGE = "Упс, такой документ уже подгружен в базу"


def file_content_hash(file_data: bytes) -> str:
    return hashlib.sha256(file_data or b"").hexdigest()


def duplicate_index_result(
    *,
    document_id: Optional[int] = None,
    filename: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": message or DUPLICATE_MESSAGE,
        "code": DUPLICATE_CODE,
        "document_id": document_id,
        "filename": filename,
    }


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
