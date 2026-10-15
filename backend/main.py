"""
main.py - точка входа
Здесь только:
  - настройка кодировки / логирования
  - создание app и sio
  - монтирование роутеров
  - startup / shutdown хуки
Вся логика работы приложения - в backend/routes/*.py, backend/socket_handlers.py и т.д.
"""
# кодировка Windowsimport sys, os
import sys
import os
try:
    from utils.encoding_fix import fix_windows_encoding
    fix_windows_encoding()
except ImportError:
    if sys.platform == "win32":
        os.system("chcp 65001 >nul 2>&1")
        for _s in (sys.stdout, sys.stderr):
            if hasattr(_s, "reconfigure"):
                _s.reconfigure(encoding="utf-8")
# -- пути
_current_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(_current_dir)
try:
    from dotenv import load_dotenv
    _env = os.path.join(_root_dir, ".env")
    if os.path.exists(_env):
        load_dotenv(_env)
        print(f".env загружен: {_env}")
except ImportError:
    pass
if _current_dir == "/app" and not os.path.exists("/app/backend"):
    os.system("ln -sf /app /app/backend")
sys.path.insert(0, _current_dir)
sys.path.insert(0, _root_dir)
# -- логирование
import logging
from backend.settings.cef_logger.cef_logger import log_cef_event
from backend.settings.logging import configure_logging, get_logger, get_uvicorn_log_config

configure_logging()
# CEF: отдельный логгер «cef» — пишем сырую строку CEF в stdout.
_cef_logger = logging.getLogger("cef")
_cef_logger.setLevel(logging.INFO)
_cef_logger.propagate = False
if not _cef_logger.handlers:
    _cef_handler = logging.StreamHandler(sys.stdout)
    _cef_handler.setLevel(logging.INFO)
    _cef_handler.setFormatter(logging.Formatter("%(message)s"))
    _cef_logger.addHandler(_cef_handler)
logger = get_logger(__name__)
logger.info("Логирование настроено")
# -- импорт app_state (загружает все сервисы)
import backend.app_state as state
from backend.app_state import (
    settings, load_app_settings, save_app_settings,
    init_databases, close_databases, database_available,
    clear_dialog_history,
    memory_clear_on_restart, minio_client,
)
# -- FastAPI
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
_app_cfg = settings.app
app = FastAPI(
    title=_app_cfg.name,
    description=_app_cfg.description,
    version=_app_cfg.version,
    debug=_app_cfg.debug,
)
_cors_origins = [o for o in settings.cors.allowed_origins if o]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=settings.cors.allow_credentials,
    allow_methods=settings.cors.allow_methods,
    allow_headers=settings.cors.allow_headers,
)
from backend.settings.cef_logger.cef_audit_middleware import CefAuditMiddleware

app.add_middleware(CefAuditMiddleware)
# ServiceDisabledError (выключенный фича-флагом микросервис) -> 503 «в разработке», а не 500
from backend.settings.service_toggles import register_service_disabled_handler  # FEATURE-FLAG
register_service_disabled_handler(app)  # FEATURE-FLAG
# -- Socket.IO
from backend.realtime import sio, socket_app
from backend.realtime import register_handlers
register_handlers(sio)
app.mount("/socket.io", socket_app)
# -- роутеры
from backend.routes.system import router as system_router
from backend.routes.chat import router as chat_router
from backend.routes.models import router as models_router
from backend.routes.llm import router as llm_router
from backend.routes.llm_providers import router as llm_providers_router
from backend.routes.memory import router as memory_router
from backend.routes.voice import router as voice_router
from backend.routes.documents import router as documents_router
from backend.routes.transcription import router as transcription_router
from backend.routes.rag import router as rag_router
from backend.routes.internal_rag import router as internal_rag_router
from backend.routes.agents import router as agents_router
from backend.routes.context_prompts import router as context_prompts_router
from backend.routes.project_rag import router as project_rag_router
from backend.routes.projects import router as projects_router
from backend.routes.model_comparison import router as model_comparison_router
from backend.routes.mcp import router as mcp_router
from backend.routes.coding_agent import router as coding_agent_router
from backend.routes.image_generation import router as image_generation_router
_plugins_import_error: BaseException | None = None
try:
    from backend.routes.plugins import router as plugins_router
except Exception as _plugins_exc:  # noqa: BLE001
    plugins_router = None
    _plugins_import_error = _plugins_exc
if _plugins_import_error is not None:
    logger.warning(
        "plugins router недоступен при импорте — backend стартует без /api/plugins. err=%s",
        _plugins_import_error,
        exc_info=_plugins_import_error,
    )
elif plugins_router is not None:
    logger.info("plugins router импортирован")
for _r in (
    system_router, chat_router, models_router, llm_router, llm_providers_router,
    memory_router, voice_router, documents_router, transcription_router,
    rag_router, internal_rag_router, agents_router, context_prompts_router,
    project_rag_router, projects_router, model_comparison_router,
    mcp_router, coding_agent_router, image_generation_router,
    *([plugins_router] if plugins_router is not None else []),
):
    app.include_router(_r)
try:
    from backend.routes.mcp_atlassian import router as mcp_atlassian_router

    app.include_router(mcp_atlassian_router)
    logger.info("mcp_atlassian router подключен")
except Exception as _e:
    logger.warning("mcp_atlassian router недоступен: %s", _e)
# -- внешние роутеры (auth, agents gallery, skills, share)
for _name, _import_path in [
    ("auth",    "backend.auth.routes"),
    ("agents",  "backend.api_agents"),
    ("skills",  "backend.api_skills"),
    ("users",   "backend.api_users"),
    ("share",   "backend.routes.share"),
]:
    try:
        import importlib
        _mod = importlib.import_module(_import_path)
        app.include_router(_mod.router)
        logger.info(f"{_name} router подключен")
    except Exception as _e:
        logger.warning(f"{_name} router недоступен: {_e}")
# -- startup / shutdown
@app.on_event("startup")
async def startup_event():
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
                except Exception as e:
                    logger.warning(f"PostgreSQL pool: {e}")
                if minio_client:
                    logger.info(f"MinIO готов: {minio_client.endpoint}")
            else:
                logger.warning("Часть БД не инициализирована - файловый режим")
        except Exception:
            logger.exception("Ошибка init_databases")
    if getattr(settings, "mcp", None) and settings.mcp.enabled:
        try:
            from backend.mcp.platform import get_mcp_platform
            await get_mcp_platform().initialize()
            logger.info("MCP platform инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации MCP platform: {e}")
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
    except Exception as e:
        logger.error("Ошибка инициализации Plugins platform: %s", e)
    # очистка памяти при рестарте
    if state.memory_clear_on_restart and clear_dialog_history:
        try:
            await clear_dialog_history()
            logger.info("Память очищена при перезапуске")
        except Exception as e:
            logger.warning(f"Не удалось очистить память: {e}")
    logger.info("Приложение запущено")
    log_cef_event("SYS001")
@app.on_event("shutdown")
async def shutdown_event():
    log_cef_event("SYS002")
    logger.info("Остановка приложения...")
    try:
        from backend.mcp.platform import get_mcp_platform
        platform = get_mcp_platform()
        if platform.initialized:
            await platform.shutdown()
    except Exception as e:
        logger.warning(f"MCP platform shutdown: {e}")
    if close_databases and database_available:
        try:
            await close_databases()
            logger.info("БД закрыты")
        except Exception as e:
            logger.error(f"Ошибка закрытия БД: {e}")
    logger.info("Приложение остановлено")
# -- статика 
_is_docker = os.getenv("DOCKER_ENV", "").lower() == "true"
if not _is_docker and os.path.exists("../frontend/build"):
    app.mount("/static", StaticFiles(directory="../frontend/build/static"), name="static")
    @app.get("/{path:path}")
    async def serve_react(path: str):
        idx = "../frontend/build/index.html"
        return FileResponse(idx) if os.path.exists(idx) else {"message": "Frontend not built"}
# -- точка запуска
if __name__ == "__main__":
    import uvicorn
    from backend.app_state import get_current_model_path, reload_model_by_path
    _urls_cfg = settings.urls
    print(f"API docs: {_urls_cfg.backend_port}/docs")
    # восстанавливаем сохраненную модель
    try:
        _saved = load_app_settings().get("current_model_path")
        if _saved and reload_model_by_path:
            if _saved.startswith("llm-svc://"):
                logger.info(f"Модель llm-svc уже доступна: {_saved}")
            elif os.path.exists(_saved) and not os.path.isdir(_saved):
                if reload_model_by_path(_saved):
                    logger.info(f"Модель восстановлена: {_saved}")
                else:
                    logger.warning(f"Не удалось восстановить: {_saved}")
    except Exception as e:
        logger.error(f"Ошибка восстановления модели: {e}")
    uvicorn.run(
        app,
        host=os.getenv("UVICORN_HOST", settings.server.host),
        port=8000,
        reload=False,
        log_level="info",
        log_config=get_uvicorn_log_config(),
    )