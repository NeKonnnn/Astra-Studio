"""
Утилиты для конвертации сообщений и хелперы.
"""
import json
import hashlib
import asyncio
import logging
from typing import Dict, Any, List

from app.models.schemas import Message

logger = logging.getLogger(__name__)


def _role_str(msg: Message) -> str:
    """Строковое значение role из сообщения (enum или строка)."""
    r = getattr(msg, "role", None)
    if r is None:
        return "user"
    return getattr(r, "value", r) if hasattr(r, "value") else str(r)


def _content_str(msg: Message) -> str:
    """Контент сообщения как строка."""
    c = getattr(msg, "content", None)
    if c is None:
        return ""
    if isinstance(c, list):
        return " ".join(
            item.get("text", str(item)) if isinstance(item, dict) else str(item)
            for item in c
        )
    return str(c)


def convert_to_chat_completion_messages(messages: List[Message]) -> List[Dict[str, Any]]:
    """Преобразование сообщений в список словарей для create_chat_completion (llama-cpp)."""
    return [
        {"role": _role_str(msg), "content": _content_str(msg)}
        for msg in messages
    ]


def convert_to_dict_messages(messages: List[Message]) -> List[Dict[str, Any]]:
    """Преобразование сообщений в словари для обратной совместимости."""
    return [
        {"role": _role_str(msg), "content": _content_str(msg)}
        for msg in messages
    ]


def generate_response_id(messages: list) -> str:
    """Генерация уникального ID для ответа на основе сообщений."""
    messages_str = json.dumps(messages, sort_keys=True)
    return hashlib.md5(messages_str.encode()).hexdigest()


def estimate_tokens(text: str) -> int:
    """Примерная оценка количества токенов в тексте."""
    return len(text) // 4


def format_messages_for_llama(messages: list) -> str:
    """Форматирование сообщений для ввода в модель."""
    formatted = ""
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content", "") or ""
        else:
            role = _role_str(msg) if hasattr(msg, "role") else "user"
            content = _content_str(msg) if hasattr(msg, "content") else getattr(msg, "content", "")
        formatted += f"{role}: {content}\n"
    return formatted + "assistant: "


async def run_in_thread(func, *args, **kwargs):
    """Запуск блокирующей функции в отдельном потоке."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
