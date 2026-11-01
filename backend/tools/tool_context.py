"""Контекст инструментов (ContextVar) без зависимостей от агентов.

Глобального fallback нет: иначе контекст одного запроса (current_user, токены,
__skill_ids__) виден другому. Если инструменту нужен контекст в рабочем потоке,
вызывающий код копирует его явно (contextvars.copy_context().run).
"""

from contextvars import ContextVar
from typing import Any, Dict, Optional

from backend.settings.logging import get_logger

logger = get_logger(__name__)

_tool_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar("tool_context")


def set_tool_context(context: Dict[str, Any]) -> None:
    """Установка контекста для инструментов (вызывается из orchestrator)."""
    _tool_context.set(context)
    logger.debug(
        "[set_tool_context] Установлен контекст: streaming=%s, has_callback=%s",
        context.get("streaming", False),
        context.get("stream_callback") is not None,
    )


def get_tool_context() -> Dict[str, Any]:
    """Получение контекста для инструментов (пустой dict, если не установлен)."""
    result = _tool_context.get(None)
    if result is None:
        logger.debug("[get_tool_context] Контекст не установлен в текущем потоке")
        return {}
    logger.debug(
        "[get_tool_context] Получен из ContextVar: streaming=%s",
        result.get("streaming", False),
    )
    return result
