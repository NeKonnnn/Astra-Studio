"""Фейки соединения/пула asyncpg для юнит-проверок слоя app/database без БД."""

from contextlib import asynccontextmanager


class FakeConn:
    """Соединение, ответы которого задаются значением или функцией ``(sql, *args)``."""

    def __init__(self, *, execute="OK", fetch=None, fetchrow=None, fetchval=None):
        self.handlers = {
            "execute": execute,
            "fetch": [] if fetch is None else fetch,
            "fetchrow": fetchrow,
            "fetchval": fetchval,
        }
        self.calls = []
        self.tx_log = []

    async def _call(self, kind, sql, args):
        self.calls.append((kind, " ".join(str(sql).split()), args))
        handler = self.handlers[kind]
        return handler(sql, *args) if callable(handler) else handler

    async def execute(self, sql, *args):
        return await self._call("execute", sql, args)

    async def fetch(self, sql, *args):
        return await self._call("fetch", sql, args)

    async def fetchrow(self, sql, *args):
        return await self._call("fetchrow", sql, args)

    async def fetchval(self, sql, *args):
        return await self._call("fetchval", sql, args)

    def transaction(self):
        """Как ``asyncpg.Connection.transaction()``: begin/commit, при исключении — rollback."""
        return _Tx(self)

    def sql_of(self, kind):
        return [c[1] for c in self.calls if c[0] == kind]


class _Tx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        self._conn.tx_log.append("begin")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._conn.tx_log.append("rollback" if exc_type else "commit")
        return False


def raising(exc):
    """Обработчик, который всегда бросает ``exc``."""

    def _h(*_a, **_k):
        raise exc

    return _h


def broken_conn(exc):
    """Соединение, на любой запрос отвечающее исключением (сбой сети/пула)."""
    h = raising(exc)
    return FakeConn(execute=h, fetch=h, fetchrow=h, fetchval=h)


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakeDb:
    """Подмена ``PostgreSQLConnection``: ``await db.acquire()`` -> менеджер контекста."""

    host = "db-host"
    port = 5432
    user = "svc"

    def __init__(self, conn):
        self.conn = conn

    async def acquire(self):
        return _Acquire(self.conn)


class FakePool:
    """Подмена ``asyncpg.Pool``: ``acquire()`` возвращает менеджер контекста сразу (как в asyncpg)."""

    def __init__(self, conn, *, acquire_error=None):
        self.conn = conn
        self.acquire_error = acquire_error
        self.closed = False

    def acquire(self):
        if self.acquire_error is not None:
            raise self.acquire_error
        return _Acquire(self.conn)

    async def close(self):
        self.closed = True
