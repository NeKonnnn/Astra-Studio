"""HTTP middleware: CEF audit context + OBJ001/OBJ002 на входящие API SVC-RAG."""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Dict, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.cef_logger.cef_audit_context import (
    cef_audit_reset,
    cef_audit_set,
    chat_model_set,
    chat_model_set_from_request,
    user_from_request_headers,
)
from app.core.cef_logger.cef_logger import log_cef_event


_SKIP_PATH_SUFFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _should_skip(path: str) -> bool:
    p = (path or "").rstrip("/")
    return any(p.endswith(s.rstrip("/")) or p == s.rstrip("/") for s in _SKIP_PATH_SUFFIXES)

# Пути, которые фронт опрашивает по таймеру. Один опрос плашки переиндексации =
# ПЯТЬ запросов сюда: бэкенд в обработчике /api/rag/reindex-status зовёт три
# /reindex/status плюс kb_list_documents() и project_rag_list_documents()
# (backend/settings/rag_client.py). Это давало пять событий OBJ001 каждые
# несколько секунд с каждой открытой вкладки.
#
# endswith, а не вхождение: "/documents" - это СПИСОК (его и опрашивает
# плашка), а "/documents/42/chunks" - просмотр чанков пользователем, и его
# аудировать надо.
_POLL_PATH_MARKERS = (
    "/reindex/status",
    "/documents",
)

# Окно прореживания: (пользователь, метод, путь) -> когда писали последний раз
# и сколько обращений с тех пор пропустили.
_POLL_LAST_EMIT: Dict[Tuple[str, str, str], float] = {}
_POLL_SUPPRESSED: Dict[Tuple[str, str, str], int] = {}
_POLL_LOCK = threading.Lock()
# Ключей столько же, сколько (активных пользователей x путей). Предел на всякий
# случай, чтобы словарь не рос бесконечно.
_POLL_KEYS_MAX = 512

def _audit_setting(env_name: str, yml_field: str, default):
    """Настройка аудита: ENV -> config.yml -> дефолт.

    Порядок как у уровней логирования app/core/logging/setup._level_from
    ENV главнее ямла, чтобы в контуре переключалось без пересборки образа.

    Импорт конфига внутри функции намеренно: middleware не должен падать, если
    конфиг не прочитался. get_settings() кэширует результат в глобальной,
    так что обращение на каждый запрос стоит один поиск по словарю.
    """
    raw = (os.getenv(env_name) or "").strip()
    if raw:
        if isinstance(default, bool):
            return raw.lower() in ("1", "true", "yes", "on")
        try:
            return int(raw)
        except ValueError:
            return default
    try:
        from app.core.config import get_settings

        value = getattr(getattr(get_settings(), "audit", None), yml_field, None)
        return default if value is None else value
    except Exception:
        return default

def _is_polling_read(method: str, path: str, status: int) -> bool:
    """Успешное ЧТЕНИЕ по поллинговому пути - фоновый опрос интерфейса.

    Условия узкие, чтобы не потерять ничего значимого:

    * только GET/HEAD - POST/PUT/DELETE по тем же путям (загрузка, удаление)
      аудируются как раньше;
    * только 2xx/3xx - ошибки и отказы доступа аудируются как раньше;
    * только перечисленные пути.
    """
    if str(method or "").upper() not in ("GET", "HEAD"):
        return False
    try:
        code = int(status)
    except (TypeError, ValueError):
        # Статус не разобрали - не рискуем, пусть аудируется.
        return False
    if not 200 <= code < 400:
        return False
    p = (path or "").rstrip("/")
    return any(p.endswith(marker.rstrip("/")) for marker in _POLL_PATH_MARKERS)

def _poll_audit_decision(user, method: str, path: str) -> Tuple[bool, int]:
    """Писать ли событие поллинга и сколько обращений оно представляет.

    Возвращает (emit, count). count уходит в CEF как cn1
    (RequestCount) - по одному событию видно реальный объём.

    Пользователь входит в ключ намеренно: аудит должен показывать, КТО
    обращался. Без него периодическое событие приписывалось бы тому, кто попал
    в окно первым, а остальные исчезли бы из аудита совсем.
    """
    if not _audit_setting("CEF_AUDIT_POLLING", "cef_polling", True):
        return (False, 0)
    interval = _audit_setting(
        "CEF_AUDIT_POLLING_INTERVAL", "cef_polling_interval_seconds", 60
    )
    try:
        interval = float(interval)
    except (TypeError, ValueError):
        interval = 60.0
    if interval <= 0:
        return (True, 1)

    suser = ""
    if user:
        suser = str(user.get("username") or user.get("user_id") or "")
    key = (suser or "SYSTEM", str(method or "").upper(), (path or "").rstrip("/"))
    now = time.monotonic()
    with _POLL_LOCK:
        last = _POLL_LAST_EMIT.get(key)
        if last is not None and (now - last) < interval:
            _POLL_SUPPRESSED[key] = _POLL_SUPPRESSED.get(key, 0) + 1
            return (False, 0)
        count = _POLL_SUPPRESSED.pop(key, 0) + 1
        _POLL_LAST_EMIT[key] = now
        if len(_POLL_LAST_EMIT) > _POLL_KEYS_MAX:
            stale = [k for k, t in _POLL_LAST_EMIT.items() if (now - t) > interval * 10]
            for k in stale:
                _POLL_LAST_EMIT.pop(k, None)
                _POLL_SUPPRESSED.pop(k, None)
        return (True, count)


class CefAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        user = user_from_request_headers(request)
        tokens = cef_audit_set(request=request, user=user)
        chat_model_set_from_request(request)
        request_uuid = uuid.uuid4().hex
        path = request.url.path
        method_name = f"{request.method} {path}"
        try:
            response = await call_next(request)
        except Exception as exc:
            if not _should_skip(path):
                log_cef_event(
                    "OBJ002",
                    request=request,
                    current_user=user,
                    status_code=500,
                    extra={
                        "methodName": method_name,
                        "serviceName": "SVC-RAG",
                        "requestUuid": request_uuid,
                        "codeStatus": "500",
                        "textStatus": str(exc)[:512],
                    },
                )
            chat_model_set(None)
            cef_audit_reset(tokens)
            raise

        try:
            if not _should_skip(path):
                status = int(getattr(response, "status_code", 500) or 500)
                if 200 <= status < 400:
                    emit, poll_count = True, 0
                    if _is_polling_read(request.method, path, status):
                        # Фоновый опрос UI: одно событие в окно вместо потока.
                        emit, poll_count = _poll_audit_decision(user, request.method, path)
                    if emit:
                        poll_extra = {}
                        if poll_count > 1:
                            # Сколько обращений представляет это событие.
                            poll_extra = {"cn1": poll_count, "cn1Label": "RequestCount"}
                        log_cef_event(
                            "OBJ001",
                            request=request,
                            current_user=user,
                            status_code=status,
                            extra={
                                "methodName": method_name,
                                "serviceName": "SVC-RAG",
                                "requestUuid": request_uuid,
                                **poll_extra,
                            },
                        )
                else:
                    log_cef_event(
                        "OBJ002",
                        request=request,
                        current_user=user,
                        status_code=status,
                        extra={
                            "methodName": method_name,
                            "serviceName": "SVC-RAG",
                            "requestUuid": request_uuid,
                            "codeStatus": str(status),
                            "textStatus": "-",
                        },
                    )
        finally:
            chat_model_set(None)
            cef_audit_reset(tokens)
        return response
