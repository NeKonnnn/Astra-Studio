"""
Сборка FastAPI-приложения: app, роутеры, lifespan, startup/shutdown.

Импортируется из main.py после настройки sys.path.
"""

import asyncio
import importlib
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import Request as _FFRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse as _FFJSONResponse
from fastapi.staticfiles import StaticFiles

import backend.app_state as state
from backend.app_state import (
    clear_dialog_history,
    close_databases,
    database_available,
    init_databases,
    load_app_settings,
    minio_client,
    settings,
)
from backend.realtime import register_handlers, sio, socket_app
from backend.routes.agents import router as agents_router
from backend.routes.chat import router as chat_router
from backend.routes.context_prompts import router as context_prompts_router
from backend.routes.documents import router as documents_router
from backend.routes.llm import router as llm_router
from backend.routes.llm_providers import router as llm_providers_router
from backend.routes.mcp import router as mcp_router
from backend.routes.memory import router as memory_router
from backend.routes.model_comparison import router as model_comparison_router
from backend.routes.models import router as models_router
from backend.routes.project_rag import router as project_rag_router
from backend.routes.projects import router as projects_router
from backend.routes.rag import router as rag_router
from backend.routes.system import router as system_router
from backend.routes.transcription import router as transcription_router
from backend.routes.voice import router as voice_router
from backend.routes.internal_rag import router as internal_rag_router
from backend.routes.image_generation import router as image_generation_router
from backend.routes.coding_agent import router as coding_agent_router
from backend.settings.cef_logger.cef_audit_middleware import CefAuditMiddleware
from backend.settings.cef_logger.cef_logger import configure_cef_logging, log_cef_event
from backend.settings.logging import (
    configure_logging,
    get_logger,
    get_uvicorn_log_config,
)

from backend.settings.service_toggles import ServiceDisabledError
from backend.utils.encoding_fix import fix_windows_encoding

# Plugins router — отдельно: не роняем весь backend, если модуль битый/циклический.
_plugins_import_error: BaseException | None = None
try:
    from backend.routes.plugins import router as plugins_router
except Exception as _plugins_exc:  # noqa: BLE001
    plugins_router = None
    _plugins_import_error = _plugins_exc

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

configure_logging()
configure_cef_logging()
logger = get_logger(__name__)
fix_windows_encoding()

if _plugins_import_error is not None:
    logger.error(
        "plugins router недоступен при импорте — backend стартует без /api/plugins. "
        "Проверьте backend/routes/plugins.py в образе (должен экспортировать router). err=%s",
        _plugins_import_error,
        exc_info=_plugins_import_error,
    )
elif plugins_router is not None:
    logger.debug("plugins router импортирован")

if load_dotenv is not None:
    _env = os.path.join(_ROOT_DIR, ".env")
    if os.path.exists(_env):
        load_dotenv(_env)
        logger.info(".env загружен: %s", _env)

logger.debug("Логирование настроено")

_app_cfg = settings.app


async def _restore_saved_model_after_startup() -> None:
    """
    После пересборки контейнеров пул llm-svc пуст, а в app settings остаётся
    last ``current_model_path`` (или дефолт провайдера). Подтягиваем веса
    с ретраями, пока сервис моделей прогревается.
    """
    saved = ""
    try:
        saved = str(load_app_settings().get("current_model_path") or "").strip()
    except Exception:
        logger.exception("restore model: не удалось прочитать settings")
    if not saved:
        try:
            default_provider = str(
                getattr(settings, "default_llm_provider", "") or ""
            ).strip()
            default_model = str(
                getattr(getattr(settings, "llm_service", None), "default_model", "")
                or ""
            ).strip()
            if default_provider and default_model:
                saved = f"{default_provider}/{default_model}"
        except Exception:
            logger.exception("restore model: не удалось взять default model")
    if not saved:
        logger.debug("restore model: нет сохранённой/дефолтной модели — пропуск")
        return

    delays = (2, 5, 10, 20, 30, 45)
    for attempt, delay in enumerate(delays, start=1):
        try:
            await asyncio.sleep(delay)
            from backend.llm_providers import get_registry

            registry = await get_registry()
            provider, model_id = registry.resolve(saved)
            if not model_id:
                if registry.contains(saved):
                    provider = registry.get(saved)
                    candidates = await provider.list_models()
                    model_id = str(
                        (candidates[0].model_id if candidates else "") or ""
                    ).strip()
            if not model_id:
                logger.warning(
                    "restore model: не удалось определить model_id из %r (попытка %d/%d)",
                    saved,
                    attempt,
                    len(delays),
                )
                continue
            ok = await provider.ensure_model_loaded(model_id)
            if ok:
                selected = f"{provider.id}/{model_id}"
                try:
                    from backend.app_state import save_app_settings

                    save_app_settings(
                        {
                            "current_model_path": selected,
                            "current_model_name": model_id,
                            "current_model_status": "loaded",
                        }
                    )
                except Exception:
                    logger.exception("restore model: не удалось сохранить settings")
                logger.debug("restore model: OK %s (попытка %d)", selected, attempt)
                return
            logger.warning(
                "restore model: ensure_model_loaded=False для %r (попытка %d/%d)",
                saved,
                attempt,
                len(delays),
            )
        except Exception:
            logger.exception(
                "restore model: ошибка для %r (попытка %d/%d)",
                saved,
                attempt,
                len(delays),
            )
    logger.warning(
        "restore model: не удалось восстановить %r после %d попыток", saved, len(delays)
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск приложения...")
    try:
        from backend.rag_query.prompts import prompts_overview

        # Отвечает на вопрос «промпт правили в ConfigMap, он точно доехал?»:
        # видно, какие блоки взяты из config.yml, а какие остались дефолтными.
        logger.info("[RAG-PROMPT] действующий промпт: %s", prompts_overview())
    except Exception:
        logger.exception("[RAG-PROMPT] сводка промпта недоступна")
    for _k in ("MONGODB_HOST", "MONGODB_PORT", "MONGODB_USER"):
        logger.info(f"{_k}: {os.getenv(_k, '')!r}")
    _pw = os.getenv("MONGODB_PASSWORD", "")
    logger.info(f"MONGODB_PASSWORD: {'*' * len(_pw)} (len={len(_pw)})")
    if init_databases and database_available:
        try:
            if await init_databases():
                logger.info("Базы данных инициализированы")
                try:
                    from backend.database.init_db import postgresql_connection

                    if postgresql_connection:
                        await postgresql_connection.ensure_pool()
                except Exception:
                    logger.exception("PostgreSQL pool")
                if minio_client:
                    logger.info(f"MinIO готов: {minio_client.endpoint}")
            else:
                logger.warning("Часть БД не инициализирована - файловый режим")
        except Exception:
            logger.exception("Ошибка init_databases: \n")
    if getattr(settings, "mcp", None) and settings.mcp.enabled:
        try:
            from backend.mcp.package_check import log_mcp_package_status
            from backend.mcp.platform import get_mcp_platform

            log_mcp_package_status()
            await get_mcp_platform().initialize()
            logger.info("MCP platform инициализирована")
        except Exception:
            logger.exception("Ошибка инициализации MCP platform")
    try:
        from backend.plugins.platform import get_plugins_platform

        plugins_platform = get_plugins_platform()
        plugins_platform.initialize()
        catalog = plugins_platform.list_plugins()
        logger.info(
            "Plugins platform инициализирована: enabled=%s count=%s ids=%s",
            plugins_platform.enabled,
            len(catalog),
            [p.id for p in catalog],
        )
    except Exception:
        logger.exception("Ошибка инициализации Plugins platform")
    if state.memory_clear_on_restart and clear_dialog_history:
        try:
            await clear_dialog_history()
            logger.info("Память очищена при перезапуске")
        except Exception:
            logger.exception("Не удалось очистить память")
    # После rebuild пул llm-svc пуст, но в settings лежит last current_model_path
    # (или дефолт). Подтягиваем веса асинхронно с ретраями, пока провайдер греется.
    try:
        asyncio.create_task(_restore_saved_model_after_startup())
    except Exception:
        logger.exception("Не удалось запланировать восстановление модели")

    logger.info("Приложение запущено")
    log_cef_event("SYS001")

    yield

    log_cef_event("SYS002")
    logger.info("Остановка приложения...")
    try:
        from backend.mcp.platform import get_mcp_platform

        platform = get_mcp_platform()
        if platform.initialized:
            await platform.shutdown()
    except Exception:
        logger.exception("MCP platform shutdown")
    if close_databases and database_available:
        try:
            await close_databases()
            logger.info("БД закрыты")
        except Exception:
            logger.exception("Ошибка закрытия БД")
    logger.info("Приложение остановлено")


app = FastAPI(
    title=_app_cfg.name,
    description=_app_cfg.description,
    version=_app_cfg.version,
    debug=_app_cfg.debug,
    lifespan=lifespan,
)
_cors_origins = [o for o in settings.cors.allowed_origins if o]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=settings.cors.allow_credentials,
    allow_methods=settings.cors.allow_methods,
    allow_headers=settings.cors.allow_headers,
)
app.add_middleware(CefAuditMiddleware)


@app.exception_handler(ServiceDisabledError)
async def _service_disabled_handler(request: _FFRequest, exc: ServiceDisabledError):
    return _FFJSONResponse(
        status_code=503, content={"detail": f"Микросервис '{exc.display}' в разработке"}
    )


register_handlers(sio)
app.mount("/socket.io", socket_app)

for _r in (
    system_router,
    chat_router,
    models_router,
    llm_router,
    llm_providers_router,
    memory_router,
    voice_router,
    documents_router,
    transcription_router,
    rag_router,
    internal_rag_router,
    agents_router,
    context_prompts_router,
    project_rag_router,
    projects_router,
    model_comparison_router,
    mcp_router,
    image_generation_router,
    coding_agent_router,
    *([plugins_router] if plugins_router is not None else []),
):
    app.include_router(_r)
try:
    from backend.routes.mcp_atlassian import router as mcp_atlassian_router

    app.include_router(mcp_atlassian_router)
    logger.info("mcp_atlassian router подключен")
except Exception:
    logger.exception("mcp_atlassian router недоступен")
for _name, _import_path in [
    ("auth", "backend.auth.routes"),
    ("auth_sso", "backend.auth.sso_routes"),
    ("agents", "backend.api_agents"),
    ("skills", "backend.api_skills"),
    ("knowledge_bases", "backend.api_knowledge_bases"),
    ("users", "backend.api_users"),
    ("admin", "backend.api_admin"),
    ("share", "backend.routes.share"),
]:
    try:
        _mod = importlib.import_module(_import_path)
        app.include_router(_mod.router)
        logger.info(f"{_name} router подключен")
    except Exception:
        logger.exception("router недоступен")


_is_docker = os.getenv("DOCKER_ENV", "").lower() == "true"
if not _is_docker and os.path.exists("../frontend/build"):
    app.mount(
        "/static", StaticFiles(directory="../frontend/build/static"), name="static"
    )

    @app.get("/{path:path}")
    async def serve_react(path: str):
        idx = "../frontend/build/index.html"
        return (
            FileResponse(idx)
            if os.path.exists(idx)
            else {"message": "Frontend not built"}
        )


def run_server() -> None:
    import uvicorn

    from backend.app_state import reload_model_by_path

    _urls_cfg = settings.urls
    logger.info("API docs: %s/docs", _urls_cfg.backend_port)
    try:
        _saved = load_app_settings().get("current_model_path")
        if _saved and reload_model_by_path:
            if _saved.startswith("llm-svc://"):
                logger.debug(f"Модель llm-svc уже доступна: {_saved}")
            elif os.path.exists(_saved) and (not os.path.isdir(_saved)):
                if reload_model_by_path(_saved):
                    logger.info(f"Модель восстановлена: {_saved}")
                else:
                    logger.warning(f"Не удалось восстановить: {_saved}")
    except Exception:
        logger.exception("Ошибка восстановления модели")
    uvicorn.run(
        app,
        host=os.getenv("UVICORN_HOST", settings.server.host),
        port=8000,
        reload=False,
        log_level="info",
        log_config=get_uvicorn_log_config(),
    )
