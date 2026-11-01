"""PostgreSQLConnection: подключение, диагностика ошибок, пул и обёртки запросов (без сети)."""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from backend.database.postgresql import connection as connection_module
from backend.database.postgresql.connection import (
    PostgreSQLConnection,
    _ConnectionContextManager,
)


class _AcquireCM:
    def __init__(self, conn):
        self.conn = conn
        self.exited_with = None

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        self.exited_with = exc_type
        return False


def _fake_pool(conn):
    """Пул-заглушка: acquire() даёт async context manager с соединением."""
    pool = MagicMock()
    pool.cms = []

    def _acquire():
        cm = _AcquireCM(conn)
        pool.cms.append(cm)
        return cm

    pool.acquire = MagicMock(side_effect=_acquire)
    pool.close = AsyncMock()
    pool.release = AsyncMock()
    return pool


def _conn(extension_present=True):
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="OK")
    conn.fetch = AsyncMock(return_value=["row"])
    conn.fetchrow = AsyncMock(return_value="one")
    conn.fetchval = AsyncMock(return_value=extension_present)
    return conn


@pytest.fixture
def db():
    return PostgreSQLConnection("h", 5432, "d", "u", "p")


@pytest.fixture
def create_pool(monkeypatch):
    """Подменяет asyncpg.create_pool; вернуть можно пул или задать side_effect."""
    mock = AsyncMock()
    monkeypatch.setattr(connection_module.asyncpg, "create_pool", mock)
    return mock


async def test_init_stores_parameters(db):
    assert (db.host, db.port, db.database, db.user, db.password) == (
        "h",
        5432,
        "d",
        "u",
        "p",
    )
    assert db.pool is None


async def test_context_manager_wrapper_delegates():
    cm = _AcquireCM("connection")
    wrapper = _ConnectionContextManager(cm)
    async with wrapper as value:
        assert value == "connection"
    assert cm.exited_with is None


async def test_connect_success_when_vector_extension_present(db, create_pool):
    conn = _conn(extension_present=True)
    create_pool.return_value = _fake_pool(conn)

    assert await db.connect(min_size=2, max_size=3) is True

    kwargs = create_pool.await_args.kwargs
    assert kwargs["host"] == "h" and kwargs["port"] == 5432
    assert kwargs["database"] == "d" and kwargs["user"] == "u" and kwargs["password"] == "p"
    assert kwargs["min_size"] == 2 and kwargs["max_size"] == 3
    assert kwargs["timeout"] == 5 and kwargs["command_timeout"] == 10
    assert db.pool is create_pool.return_value
    # CREATE EXTENSION не пытались выполнить: расширение уже есть
    executed = [c.args[0] for c in conn.execute.await_args_list]
    assert executed == ["SELECT 1"]


async def test_connect_installs_missing_extension(db, create_pool, caplog):
    conn = _conn()
    conn.fetchval = AsyncMock(side_effect=[False, True])
    create_pool.return_value = _fake_pool(conn)

    assert await db.connect() is True

    executed = [c.args[0] for c in conn.execute.await_args_list]
    assert "CREATE EXTENSION IF NOT EXISTS vector" in executed
    assert conn.fetchval.await_count == 2


async def test_connect_extension_still_missing_after_create_is_not_fatal(
    db, create_pool
):
    """pgvector не появился после CREATE EXTENSION: подключение всё равно успешно (RAG деградирует)."""
    conn = _conn()
    conn.fetchval = AsyncMock(side_effect=[False, False])
    create_pool.return_value = _fake_pool(conn)

    assert await db.connect() is True
    assert conn.fetchval.await_count == 2


@pytest.mark.parametrize(
    "message",
    [
        "permission denied to create extension",
        "нет права на создание",  # русское сообщение: ловится по подстроке «права»
        "extension \"vector\" does not exist",
        "не существует такого файла",
        "something else entirely",
    ],
)
async def test_connect_create_extension_failure_is_not_fatal(db, create_pool, message):
    """Любая ошибка CREATE EXTENSION логируется с подсказкой, но connect() возвращает True."""
    conn = _conn()
    conn.fetchval = AsyncMock(return_value=False)
    conn.execute = AsyncMock(side_effect=[None, RuntimeError(message)])
    create_pool.return_value = _fake_pool(conn)

    assert await db.connect() is True
    assert conn.fetchval.await_count == 1


@pytest.mark.parametrize(
    "error, hint",
    [
        (asyncpg.exceptions.InvalidPasswordError("bad"), "Неверный пароль"),
        (asyncpg.exceptions.InvalidCatalogNameError("nodb"), "не существует"),
        (
            asyncpg.exceptions.InvalidAuthorizationSpecificationError("noauth"),
            "нет прав доступа",
        ),
        (ConnectionRefusedError(), "Не удалось подключиться"),
        (OSError("No connection could be made"), "Не удалось установить соединение"),
        (OSError("не удается установить соединение"), "Не удалось установить соединение"),
        (OSError("other os error"), "ОШИБКА подключения"),
        (ValueError("boom"), "ОШИБКА при подключении к PostgreSQL (ValueError)"),
    ],
)
async def test_connect_failures_return_false_with_diagnostics(
    db, create_pool, caplog, error, hint
):
    create_pool.side_effect = error
    with caplog.at_level("DEBUG"):
        assert await db.connect() is False
    assert hint in caplog.text


async def test_connect_failure_in_probe_query_returns_false(db, create_pool):
    """Сбой первого запроса после создания пула — тоже False, а не исключение."""
    conn = _conn()
    conn.execute = AsyncMock(side_effect=RuntimeError("probe failed"))
    create_pool.return_value = _fake_pool(conn)
    assert await db.connect() is False


async def test_disconnect_closes_pool(db):
    pool = _fake_pool(_conn())
    db.pool = pool
    await db.disconnect()
    pool.close.assert_awaited_once()


async def test_disconnect_without_pool_is_noop(db):
    await db.disconnect()  # не бросает
    assert db.pool is None


async def test_health_check_ok(db):
    conn = _conn()
    db.pool = _fake_pool(conn)
    assert await db.health_check() is True
    conn.execute.assert_awaited_once_with("SELECT 1")


async def test_health_check_without_pool_is_false(db):
    assert await db.health_check() is False


async def test_health_check_query_failure_is_false(db):
    conn = _conn()
    conn.execute = AsyncMock(side_effect=RuntimeError("down"))
    db.pool = _fake_pool(conn)
    assert await db.health_check() is False


async def test_ensure_pool_creates_when_missing(db, monkeypatch):
    monkeypatch.setattr(db, "connect", AsyncMock(return_value=True))
    await db.ensure_pool()
    db.connect.assert_awaited_once()


async def test_ensure_pool_keeps_pool_from_current_loop(db, monkeypatch):
    pool = _fake_pool(_conn())
    pool._loop = asyncio.get_running_loop()
    db.pool = pool
    monkeypatch.setattr(db, "connect", AsyncMock())
    await db.ensure_pool()
    db.connect.assert_not_awaited()
    pool.close.assert_not_awaited()


async def test_ensure_pool_recreates_pool_from_other_loop(db, monkeypatch):
    old = _fake_pool(_conn())
    old._loop = object()  # пул привязан к чужому циклу
    db.pool = old
    monkeypatch.setattr(db, "connect", AsyncMock(return_value=True))
    await db.ensure_pool()
    old.close.assert_awaited_once()
    db.connect.assert_awaited_once()


async def test_ensure_pool_ignores_error_when_closing_old_pool(db, monkeypatch):
    old = _fake_pool(_conn())
    old._loop = object()
    old.close = AsyncMock(side_effect=RuntimeError("already closed"))
    db.pool = old
    monkeypatch.setattr(db, "connect", AsyncMock(return_value=True))
    await db.ensure_pool()
    db.connect.assert_awaited_once()


@pytest.fixture
def no_running_loop(monkeypatch):
    """asyncio.get_running_loop() в модуле connection бросает RuntimeError."""

    def _raise():
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(
        connection_module, "asyncio", types.SimpleNamespace(get_running_loop=_raise)
    )


async def test_ensure_pool_without_running_loop_connects_when_no_pool(
    db, monkeypatch, no_running_loop
):
    monkeypatch.setattr(db, "connect", AsyncMock(return_value=True))
    await db.ensure_pool()
    db.connect.assert_awaited_once()


async def test_ensure_pool_without_running_loop_keeps_existing_pool(
    db, monkeypatch, no_running_loop
):
    db.pool = _fake_pool(_conn())
    monkeypatch.setattr(db, "connect", AsyncMock())
    await db.ensure_pool()
    db.connect.assert_not_awaited()


async def test_acquire_returns_wrapped_pool_connection(db):
    conn = _conn()
    pool = _fake_pool(conn)
    db.pool = pool
    pool._loop = asyncio.get_running_loop()
    wrapper = await db.acquire()
    assert isinstance(wrapper, _ConnectionContextManager)
    async with wrapper as got:
        assert got is conn


async def test_acquire_raises_when_pool_cannot_be_created(db, monkeypatch):
    monkeypatch.setattr(db, "connect", AsyncMock(return_value=False))
    with pytest.raises(RuntimeError, match="Пул соединений не создан"):
        await db.acquire()


async def test_release_returns_connection_to_pool(db):
    pool = _fake_pool(_conn())
    db.pool = pool
    await db.release("conn")
    pool.release.assert_awaited_once_with("conn")


@pytest.mark.parametrize(
    "method, result",
    [
        ("execute", "OK"),
        ("fetch", ["row"]),
        ("fetchrow", "one"),
        ("fetchval", True),
    ],
)
async def test_query_helpers_pass_arguments_and_return_result(db, method, result):
    conn = _conn()
    pool = _fake_pool(conn)
    pool._loop = asyncio.get_running_loop()
    db.pool = pool

    assert await getattr(db, method)("SELECT $1", 42) == result

    getattr(conn, method).assert_awaited_once_with("SELECT $1", 42)
    assert pool.cms[0].exited_with is None
