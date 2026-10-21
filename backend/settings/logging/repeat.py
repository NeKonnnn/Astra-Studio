"""Повторяющиеся строки: INFO при изменении, DEBUG на повторах.

Часть строк сообщает не событие, а состояние: «эти модели не берём в выбор
чата», «batch /health недоступен, идём chat-probe». Само по себе это полезно,
но пишется на каждую пересборку кэша каталога моделей — то есть раз в
```MODELS_AVAILABLE_CACHE_SECONDS``` (по умолчанию 60 секунд), бесконечно и с
одним и тем же текстом.

Понизить их до DEBUG нельзя: первое появление и любое изменение состояния
эксплуатации нужны. Поэтому строка пишется на INFO, только когда её содержимое
ОТЛИЧАЕТСЯ от прошлого раза, а повтор уходит в DEBUG.

Состояние живёт в памяти процесса: после рестарта пода каждая строка снова
напечатается один раз — это и нужно, иначе после передеплоя нельзя понять
текущую конфигурацию.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict

# Ключ -> последнее записанное состояние. Ключей единицы (по одному на строку
# лога), чистка не нужна.
_last_state: Dict[str, Any] = {}
_lock = threading.Lock()


def log_on_change(
    logger: logging.Logger,
    key: str,
    state: Any,
    msg: str,
    *args: Any,
    level: int = logging.INFO,
) -> bool:
    """Записать ```msg``` на ```level```, если ```state``` изменился, иначе на DEBUG.

    :param key: идентификатор строки лога, стабильный между вызовами
        (например ```"models.not_for_chat.CORSUR"```).
    :param state: то, что сравниваем. Должно быть хешируемым или сравнимым
        через ```==```: кортеж, строка, число.
    :returns: True, если писали на ```level``` (состояние изменилось).

    Пример::

        log_on_change(
            logger,
            f"models.not_for_chat.{provider.id}",
            tuple(not_for_chat),
            "[MODELS] %s: в выбор модели чата не берём эмбеддеры и реранкеры: %s",
            provider.id,
            not_for_chat,
        )
    """
    with _lock:
        changed = _last_state.get(key, _MISSING) != state
        if changed:
            _last_state[key] = state
    if changed:
        logger.log(level, msg, *args)
    else:
        logger.debug(msg, *args)
    return changed


def reset_log_state(key: str | None = None) -> None:
    """Забыть запомненное состояние (для тестов и ручной диагностики)."""
    with _lock:
        if key is None:
            _last_state.clear()
        else:
            _last_state.pop(key, None)


class _Missing:
    """Отличает «ещё не писали» от «писали значение None»."""

    def __repr__(self) -> str:
        return "<не записывалось>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Missing)

    def __hash__(self) -> int:
        return hash("_Missing")


_MISSING = _Missing()

__all__ = ["log_on_change", "reset_log_state"]
