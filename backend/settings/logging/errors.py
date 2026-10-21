"""
Централизованная обработка ошибок с логированием.

Единственное место с blind except — здесь (per-file-ignores в pyproject.toml).
В прикладном коде используйте run_guarded / logged_suppress вместо try/except.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


def _log_handled(logger: logging.Logger, message: str, level: str) -> None:
    if level in ("exception", "error"):
        logger.exception(message)
    elif level == "warning":
        logger.warning("%s", message, exc_info=True)
    elif level == "debug":
        logger.debug("%s", message, exc_info=True)
    else:
        logger.exception(message)


def run_guarded(
    logger: logging.Logger,
    fn: Callable[[], T],
    *,
    message: str,
    default: Any = None,
    reraise: bool = False,
    level: str = "exception",
) -> T | Any:
    """Выполняет fn(); при ошибке логирует и возвращает default."""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        _log_handled(logger, message, level)
        if reraise:
            raise
        return default


async def run_guarded_async(
    logger: logging.Logger,
    fn: Callable[[], Awaitable[T]],
    *,
    message: str,
    default: Any = None,
    reraise: bool = False,
    level: str = "exception",
) -> T | Any:
    """Асинхронный аналог run_guarded."""
    try:
        return await fn()
    except Exception:  # noqa: BLE001
        _log_handled(logger, message, level)
        if reraise:
            raise
        return default


class logged_suppress:
    """Контекстный менеджер вместо try/except/pass с логом на DEBUG."""

    def __init__(
        self, logger: logging.Logger, message: str = "Подавлено исключение"
    ) -> None:
        self.logger = logger
        self.message = message

    def __enter__(self) -> logged_suppress:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None and issubclass(exc_type, Exception):
            self.logger.debug(self.message, exc_info=(exc_type, exc, tb))
            return True
        return False


def guarded(
    message: str | None = None,
    *,
    default: Any = None,
    reraise: bool = False,
    level: str = "exception",
) -> Callable[[Callable[P, T]], Callable[P, T | Any]]:
    """Декоратор: логирует ошибку вместо try/except в теле функции."""

    def decorator(fn: Callable[P, T]) -> Callable[P, T | Any]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | Any:
            from backend.settings.logging import get_logger

            log = get_logger(fn.__module__)
            msg = message or f"Ошибка в {fn.__qualname__}"

            def _call() -> T:
                return fn(*args, **kwargs)

            return run_guarded(
                log, _call, message=msg, default=default, reraise=reraise, level=level
            )

        return wrapper

    return decorator
