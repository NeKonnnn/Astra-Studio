"""Сообщить бэкенду, что индексация не состоялась и исходник больше не нужен.

Зачем понадобился отдельный канал
---------------------------------
Исходник удаляет backend: PVC (или MinIO) принадлежит ему, SVC-RAG к тому
тому не подключён вовсе. Пока backend ждёт ответа на POST /documents, он
справляется сам: пришла ошибка - файл убран.

После последних коммитов он ждёт не бесконечно, и по таймауту файл НЕ удаляет. Это
правильно: был случай когда SVC-RAG ответил на 49 секунд позже порога, документ уже
лежал в базе, и удаление исходника оставило бы проиндексированный документ
без файла. Но у решения есть вторая половина: если индексация всё-таки НЕ
дошла до конца, убрать файл больше некому - backend ушёл и назад не смотрит.

Поэтому тот, кто знает исход, говорит об этом тому, кто владеет хранилищем.

---------
RAG_INDEX_FAILED_REPORT_TIMEOUT=15  сколько секунд ждать ответ backend об удалении исходника.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.http_verify import resolve_httpx_verify
from app.core.internal_auth import internal_rag_headers
from app.core.logging import get_logger

logger = get_logger(__name__)

def _report_timeout() -> httpx.Timeout:
    """Ждём недолго: это уведомление на пути ошибки, а не полезная работа."""
    try:
        read_sec = float(os.getenv("RAG_INDEX_FAILED_REPORT_TIMEOUT", "15"))
    except ValueError:
        read_sec = 15.0
    return httpx.Timeout(max(3.0, read_sec), connect=5.0)

async def report_index_failed(
    *,
    minio_object: Optional[str],
    minio_bucket: Optional[str],
    filename: str,
    reason: str,
    store: str = "kb",
) -> None:
    """Сказать бэкенду, что документ не проиндексирован и исходник можно убрать.

    minio_object / minio_bucket - ровно те, что пришли в index_document: ключ
    точный, никаких догадок по имени и размеру. Если их нет (файл не
    сохранялся), звонить не о чем.
    """
    if not minio_object or not minio_bucket:
        return
    base_url = (get_settings().backend.base_url or "").rstrip("/")
    if not base_url:
        logger.warning(
            "Индексация «%s» не состоялась, но backend.base_url не задан в конфиге "
            "SVC-RAG (urls.backend_service_*) - исходник %s останется в хранилище",
            filename,
            minio_object,
        )
        return
    url = f"{base_url}/api/internal/rag/index-failed"
    payload = {
        "minio_object": minio_object,
        "minio_bucket": minio_bucket,
        "filename": filename or "",
        # Текст ошибки нужен бэкенду только для лога - режем, чтобы не тащить
        # в запрос простыню трассировки.
        "reason": str(reason or "")[:500],
        "store": store,
    }
    try:
        async with httpx.AsyncClient(
            timeout=_report_timeout(), verify=resolve_httpx_verify()
        ) as client:
            resp = await client.post(
                url, headers=internal_rag_headers(), json=payload
            )
            resp.raise_for_status()
        logger.info(
            "Индексация «%s» не состоялась - бэкенду отправлен запрос убрать "
            "исходник %s",
            filename,
            minio_object,
        )
    except Exception as e:
        logger.warning(
            "Не удалось сообщить бэкенду об исходнике %s (%s: %s). Файл может "
            "остаться в хранилище без документа",
            minio_object,
            type(e).__name__,
            e,
        )