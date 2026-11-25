"""
API endpoints module.
"""

from fastapi import APIRouter
from .endpoints import chat, health, models

# Создаем основной роутер API
router = APIRouter()

# Включаем все эндпоинты из модулей
router.include_router(health.router, tags=["Health"])
router.include_router(models.router, tags=["Models"])
router.include_router(chat.router, tags=["Chat"])