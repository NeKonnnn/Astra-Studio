import json
import time
import uuid
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging.config
from app.core.config import settings
from app.api import router as api_router
from app.llm_dependencies import get_llm_handler, cleanup_llm_handler as cleanup_llama_service
from app.middleware.logging_middleware import LoggingMiddleware
from app.services.nexus_client import download_model_from_nexus_if_needed
from fastapi import Request
# Настройка логирования
def setup_logging():
    """Настройка централизованного логирования для всего приложения"""
    logging.config.dictConfig({
        'version': 1,
        'disable_existing_loggers': False,  # Важно: не отключаем существующие логгеры
        'formatters': {
            'default': {
                'format': settings.logging.format
            }
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'default',
                'level': settings.logging.level,
                'stream': 'ext://sys.stdout'
            }
        },
        'root': {
            'handlers': ['console'],
            'level': settings.logging.level
        },
        'loggers': {
            'app': {
                'handlers': ['console'],
                'level': settings.logging.level,
                'propagate': False
            },
            'app.api': {
                'handlers': ['console'],
                'level': settings.logging.level,
                'propagate': False
            },
            'app.api.endpoints': {
                'handlers': ['console'],
                'level': settings.logging.level,
                'propagate': False
            },
            'app.core': {
                'handlers': ['console'],
                'level': settings.logging.level,
                'propagate': False
            },
            'app.middleware': {
                'handlers': ['console'],
                'level': settings.logging.level,
                'propagate': False
            },
            'app.models': {
                'handlers': ['console'],
                'level': settings.logging.level,
                'propagate': False
            },
            'app.services': {
                'handlers': ['console'],
                'level': settings.logging.level,
                'propagate': False
            },
            'app.services.generators': {
                'handlers': ['console'],
                'level': settings.logging.level,
                'propagate': False
            },
            'app.services.llama_handler': {  # Явно настраиваем логгер для llama_handler
                'handlers': ['console'],
                'level': settings.logging.level,
                'propagate': False
            },
            '__main__': {
                'handlers': ['console'],
                'level': settings.logging.level,
                'propagate': False
            }
        }
    })
# Настраиваем логирование при импорте модуля
setup_logging()
logger = logging.getLogger(__name__)
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan менеджер для управления жизненным циклом приложения.
    Отделяет инициализацию ресурсов от их использования в зависимостях.
    """
    # Инициализация при запуске
    try:
        logger.info("Starting application initialization...")
        # Загружаем модель из Nexus при необходимости
        if not download_model_from_nexus_if_needed():
            logger.error("Failed to download model from Nexus")
            raise RuntimeError("Failed to download model from Nexus")
        # Предварительная инициализация LLM (LlamaHandler; загрузка модели — по возможности)
        await get_llm_handler()
        logger.info("Application started successfully")
    except Exception as e:
        logger.error(f"Failed to initialize application: {str(e)}")
        # Гарантируем очистку ресурсов при ошибке инициализации
        await cleanup_llama_service()
        raise
    yield  # Приложение работает
    # Очистка при завершении
    try:
        await cleanup_llama_service()
        logger.info("Application shut down gracefully")
    except Exception as e:
        logger.error(f"Error during application shutdown: {str(e)}")
def create_application() -> FastAPI:
    """Создание и настройка FastAPI приложения"""
    application = FastAPI(
        title=settings.app.title,
        description=settings.app.description,
        version=settings.app.version,
        lifespan=lifespan,
        docs_url=settings.server.docs_url,
        redoc_url=settings.server.redoc_url,
    )
    # Настройка CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.allowed_origins,
        allow_credentials=settings.cors.allow_credentials,
        allow_methods=settings.cors.allow_methods,
        allow_headers=settings.cors.allow_headers,
    )
    # Подключение роутеров
    application.include_router(api_router, prefix="/v1")
    return application
app = create_application()
app.add_middleware(LoggingMiddleware)
if __name__ == "__main__":
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.server.log_level.lower(),
    )

