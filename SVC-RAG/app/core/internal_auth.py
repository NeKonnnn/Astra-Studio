"""Заголовки для вызовов SVC-RAG → backend ``/api/internal/rag/*`` (BUG-RAG-07 / BUG-X-01).

Если задан общий секрет ``INTERNAL_RAG_SECRET``, backend требует его в заголовке
``X-Internal-Rag-Secret`` (иначе 401). Секрет не задан — заголовок не добавляется
(режим сетевой изоляции, как раньше). Значение никогда не логируется.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

INTERNAL_SECRET_ENV = "INTERNAL_RAG_SECRET"
INTERNAL_SECRET_HEADER = "X-Internal-Rag-Secret"


def internal_rag_secret() -> str:
    return (os.getenv(INTERNAL_SECRET_ENV) or "").strip()


def internal_rag_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Новый словарь заголовков: ``extra`` + секрет (если задан)."""
    headers: Dict[str, str] = dict(extra or {})
    secret = internal_rag_secret()
    if secret:
        headers[INTERNAL_SECRET_HEADER] = secret
    return headers
