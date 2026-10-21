"""Безопасные тексты ошибок для HTTP-ответов (CWE-209)."""

from __future__ import annotations

from typing import Optional

GENERIC_INTERNAL_ERROR_DETAIL = "Внутренняя ошибка сервера"
UPSTREAM_ERROR_DETAIL = "Ошибка при обращении к внешнему сервису"


def public_error_detail(
    _exc: Optional[BaseException] = None,
    default: str = GENERIC_INTERNAL_ERROR_DETAIL,
) -> str:
    """Текст ``detail`` для клиента при внутренней ошибке (HTTP 5xx).

    Текст исключения (хосты, пути, фрагменты запросов, креды) наружу не отдаётся —
    его нужно писать только в серверный лог (``logger.exception``). Аргумент
    ``_exc`` принимается для удобства подстановки вместо ``str(e)`` и
    намеренно игнорируется.
    """
    return default
