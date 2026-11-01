"""Фейковые подключение/соединение к PostgreSQL для веток сбоев репозиториев."""

from __future__ import annotations

from typing import Any, Callable, List, Optional
from unittest.mock import AsyncMock, MagicMock


class FakeConn:
    """Соединение: все методы asyncpg — AsyncMock, по умолчанию «пустой» результат."""

    def __init__(self) -> None:
        self.execute = AsyncMock(return_value="OK 0")
        self.fetch = AsyncMock(return_value=[])
        self.fetchrow = AsyncMock(return_value=None)
        self.fetchval = AsyncMock(return_value=None)
        self.executemany = AsyncMock(return_value=None)
        self.transaction = MagicMock(return_value=_Tx())

    def fail_all(self, exc: Optional[BaseException] = None) -> "FakeConn":
        exc = exc or RuntimeError("db down")
        for name in ("execute", "fetch", "fetchrow", "fetchval", "executemany"):
            getattr(self, name).side_effect = exc
        return self


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _ConnCM:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *exc) -> bool:
        return False


class FakeDb:
    """Замена PostgreSQLConnection: acquire() отдаёт context manager с FakeConn."""

    host = "db.test"
    port = 5432

    def __init__(self, conn: Optional[FakeConn] = None) -> None:
        self.conn = conn or FakeConn()
        self.acquire = AsyncMock(side_effect=lambda: _ConnCM(self.conn))

    def no_pool(self, message: str = "Пул соединений не создан") -> "FakeDb":
        self.acquire = AsyncMock(side_effect=RuntimeError(message))
        return self


def rows(*items: Any) -> List[Any]:
    """Список записей-словарей (asyncpg.Record ведёт себя как dict для доступа по ключу)."""
    return list(items)


def make_factory(cls: Callable[..., Any], db: FakeDb):
    return cls(db)
