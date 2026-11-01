import json
import time
import os
import uuid
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import logging.config

from app.core.config import settings
from app.api import router as api_router
from app.core.cef_logger import (
    CefAuditMiddleware,
    CefAuthMiddleware,
    configure_cef_logging,
    log_cef_event,
)
from app.dependencies.rag_models_handler import (
    get_rag_models_handler,
    cleanup_rag_models_handler,
    get_last_rag_models_error,
)

logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": {"format": settings.logging.format}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "level": settings.logging.level,
            }
        },
        "root": {"handlers": ["console"], "level": settings.logging.level},
    }
)
logger = logging.getLogger(__name__)

configure_cef_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        if settings.rag_models.enabled:
            logger.info("Поднимаю сервис RAG-моделей (эмбеддинг + реранкер)...")
            handler = await get_rag_models_handler()
            if handler is None:
                err = get_last_rag_models_error()
                logger.error("RAG-модели не поднялись%s", f": {err}" if err else "")
            else:
                logger.info("Сервис RAG-моделей готов")
        log_cef_event("SYS001")
    except Exception as e:
        logger.error(f"Ошибка при старте: {e}", exc_info=True)
        raise
    yield
    log_cef_event("SYS002")
    await cleanup_rag_models_handler()
    logger.info("Сервис RAG-моделей остановлен")


def create_application() -> FastAPI:
    app = FastAPI(
        title=settings.app.title,
        description=settings.app.description,
        version=settings.app.version,
        lifespan=lifespan,
        docs_url=settings.server.docs_url,
        redoc_url=settings.server.redoc_url,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.allowed_origins,
        allow_credentials=settings.cors.allow_credentials,
        allow_methods=settings.cors.allow_methods,
        allow_headers=settings.cors.allow_headers,
    )
    # CEF: auth (SEC001/SEC003) снаружи, затем OBJ001/OBJ002.
    app.add_middleware(CefAuditMiddleware)
    app.add_middleware(CefAuthMiddleware)
    app.include_router(api_router, prefix="/v1")
    return app


app = create_application()


# Пути, по которым НЕ пишем успешный запрос
# Отключить: RAG_MODELS_LOG_QUIET_PATHS=""
_DEFAULT_QUIET_PATHS = (
    "/health",
    "/healthz",
    "/ready",
    "/readyz",
    "/live",
    "/livez",
    "/metrics",
)


def _quiet_paths() -> tuple:
    """Пути проб: ENV RAG_MODELS_LOG_QUIET_PATHS через запятую, иначе дефолт."""
    raw = os.getenv("RAG_MODELS_LOG_QUIET_PATHS")
    if raw is None:
        return _DEFAULT_QUIET_PATHS
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _is_quiet(method, path, status=None) -> bool:
    """Фоновая проба, которую не нужно писать в лог.

    status=None - ответа ещё нет: так вызывается перед обработкой запроса.
    Когда код известен, прячем только успех: всё, что ответило не 2xx, в логе
    нужно. Нераспознанный код тоже остаётся - неизвестно значит не успех.

    Метод неизвестен или это запись - строку пишем: POST по тому же пути
    опросом не является.
    """
    if str(method or "").upper() not in ("GET", "HEAD"):
        return False
    if status is not None:
        try:
            code = int(status)
        except (TypeError, ValueError):
            return False
        if not 200 <= code < 300:
            return False
    clean = str(path or "").split("?", 1)[0]
    return any(q in clean for q in _quiet_paths())


# Ключи тела, которые безопасно писать значением
_SAFE_BODY_KEYS = (
    "model",
    "provider",
    "encoding_format",
    "dimensions",
    "top_n",
    "truncate",
)


def _payload_shape(payload) -> str:
    """Состав тела запроса БЕЗ содержимого: ключи, количества, длины.

    В теле POST к сервису эмбеддингов лежит текст документа. Раньше первые
    500 символов уходили в лог на уровне INFO - то есть содержимое чанков
    оказывалось в stdout пода и уезжало в сборщик логов. Для диагностики
    достаточно формы запроса: сколько строк пришло и какой длины.
    """
    if not isinstance(payload, dict):
        return type(payload).__name__
    bits = []
    for key, val in payload.items():
        if key in _SAFE_BODY_KEYS and isinstance(val, (str, int, float, bool)):
            bits.append(f"{key}={val}")
        elif isinstance(val, str):
            bits.append(f"{key}=<{len(val)} симв.>")
        elif isinstance(val, list):
            total = sum(len(v) for v in val if isinstance(v, str))
            bits.append(f"{key}=<{len(val)} шт., {total} симв.>")
        elif val is None or isinstance(val, (int, float, bool)):
            bits.append(f"{key}={val}")
        else:
            bits.append(f"{key}=<{type(val).__name__}>")
    return " ".join(bits)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start = time.time()
    quiet = _is_quiet(request.method, request.url.path)
    if not quiet:
        logger.info(f"Request {request_id}: {request.method} {request.url}")
    if request.method == "POST" and not quiet:
        try:
            body = await request.body()
            if body and "application/json" in (
                request.headers.get("content-type") or ""
            ):
                try:
                    # Только форма запроса, без содержимого: см. _payload_shape
                    logger.debug(
                        "Request %s: %s",
                        request_id,
                        _payload_shape(json.loads(body)),
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Request {request_id}: body read error {e}")
    response = await call_next(request)
    # Вторую строку решаем уже по коду ответа: неуспешная проба в логе нужна
    if not _is_quiet(request.method, request.url.path, response.status_code):
        logger.info(
            f"Request {request_id}: status={response.status_code} time={time.time() - start:.2f}s"
        )
    return response


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.server.log_level.lower(),
    )
