# Импортируем все роутеры для удобного доступа
from .health import router as health_router
from .models import router as models_router
from .chat import router as chat_router

# Список всех роутеров для автоматического импорта
__all__ = ["health_router", "models_router", "chat_router"]