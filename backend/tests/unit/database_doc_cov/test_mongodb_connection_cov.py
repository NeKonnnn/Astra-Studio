"""MongoDBConnection: подключение, ошибки подключения, health-check, доступ к БД — с фейковым motor-клиентом."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.errors import ConfigurationError, ConnectionFailure

from backend.database.mongodb import connection as conn_mod
from backend.database.mongodb.connection import MongoDBConnection


class FakeClient:
    """Фейк AsyncIOMotorClient: ping настраивается, базы выдаются по ключу."""

    last = None

    def __init__(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        self.admin = MagicMock()
        self.admin.command = AsyncMock(return_value={"ok": 1})
        self.closed = False
        FakeClient.last = self

    def __getitem__(self, name):
        return {"db_name": name}

    def close(self):
        self.closed = True


@pytest.fixture
def debug_lines(monkeypatch):
    """Перехват debug-сообщений логгера модуля (иерархия логгеров проекта не пропускает записи в caplog)."""
    lines: list = []
    monkeypatch.setattr(conn_mod.logger, "debug", lambda msg, *a, **k: lines.append(str(msg)))
    return lines


@pytest.fixture(autouse=True)
def fake_motor(monkeypatch):
    FakeClient.last = None
    monkeypatch.setattr(conn_mod, "AsyncIOMotorClient", FakeClient)


class TestConnect:
    async def test_successful_connect_pings_and_selects_database(self):
        c = MongoDBConnection("mongodb://h:27017", "mydb")
        assert await c.connect() is True
        assert c.db == {"db_name": "mydb"}
        FakeClient.last.admin.command.assert_awaited_once_with("ping")
        assert FakeClient.last.kwargs == {
            "serverSelectionTimeoutMS": 5000,
            "connectTimeoutMS": 5000,
            "socketTimeoutMS": 10000,
        }

    async def test_default_database_name(self):
        assert MongoDBConnection("mongodb://h").database_name == "astrachat"

    async def test_credentials_are_masked_in_the_safe_log_line(self, debug_lines):
        c = MongoDBConnection("mongodb://user:pa55@host:27017/db", "d")
        assert await c.connect() is True
        masked = [m for m in debug_lines if "Создание клиента" in m]
        assert masked and "user:***@host:27017/db" in masked[0] and "pa55" not in masked[0]

    async def test_url_with_at_but_without_password_is_not_masked(self, debug_lines):
        c = MongoDBConnection("mongodb://user@host", "d")
        assert await c.connect() is True
        masked = [m for m in debug_lines if "Создание клиента" in m]
        assert "mongodb://user@host" in masked[0]

    async def test_url_with_several_at_signs_is_masked(self, debug_lines):
        c = MongoDBConnection("mongodb://u:p@w@host", "d")
        assert await c.connect() is True
        masked = [m for m in debug_lines if "Создание клиента" in m]
        assert "u:***@host" in masked[0] and "p@w" not in masked[0]

    async def test_password_must_not_be_logged(self, debug_lines):
        c = MongoDBConnection("mongodb://user:s3cret@host/db", "d")
        await c.connect()
        assert not any("s3cret" in m for m in debug_lines)

    @pytest.mark.parametrize(
        "error", [ConnectionFailure("no server"), ConfigurationError("bad uri"), ValueError("boom")]
    )
    async def test_errors_give_false_and_no_database(self, monkeypatch, error):
        class Failing(FakeClient):
            def __init__(self, url, **kwargs):
                super().__init__(url, **kwargs)
                self.admin.command = AsyncMock(side_effect=error)

        monkeypatch.setattr(conn_mod, "AsyncIOMotorClient", Failing)
        c = MongoDBConnection("mongodb://h", "d")
        assert await c.connect() is False
        assert c.db is None

    @pytest.mark.parametrize("error", [ConnectionFailure("x"), ConfigurationError("y")])
    async def test_password_is_not_logged_on_errors(self, monkeypatch, error):
        errors: list = []
        monkeypatch.setattr(conn_mod.logger, "exception", lambda msg, *a, **k: errors.append(str(msg)))

        class Failing(FakeClient):
            def __init__(self, url, **kwargs):
                super().__init__(url, **kwargs)
                self.admin.command = AsyncMock(side_effect=error)

        monkeypatch.setattr(conn_mod, "AsyncIOMotorClient", Failing)
        assert await MongoDBConnection("mongodb://user:s3cret@host/db", "d").connect() is False
        assert errors and not any("s3cret" in m for m in errors)

    async def test_client_construction_error_gives_false(self, monkeypatch):
        def broken(*a, **k):
            raise ConfigurationError("invalid scheme")

        monkeypatch.setattr(conn_mod, "AsyncIOMotorClient", broken)
        assert await MongoDBConnection("bad://x").connect() is False


class TestDisconnectAndHealth:
    async def test_disconnect_closes_client(self):
        c = MongoDBConnection("mongodb://h")
        await c.connect()
        await c.disconnect()
        assert FakeClient.last.closed is True

    async def test_disconnect_without_client_is_noop(self):
        await MongoDBConnection("mongodb://h").disconnect()

    async def test_health_is_true_when_ping_works(self):
        c = MongoDBConnection("mongodb://h")
        await c.connect()
        assert await c.health_check() is True

    async def test_health_is_false_without_client(self):
        assert await MongoDBConnection("mongodb://h").health_check() is False

    async def test_health_is_false_when_ping_fails(self):
        c = MongoDBConnection("mongodb://h")
        await c.connect()
        c.client.admin.command = AsyncMock(side_effect=ConnectionFailure("lost"))
        assert await c.health_check() is False


class TestAccessors:
    async def test_get_database_before_connect_raises(self):
        with pytest.raises(RuntimeError, match="не подключена"):
            MongoDBConnection("mongodb://h").get_database()

    async def test_get_collection_before_connect_raises(self):
        with pytest.raises(RuntimeError):
            MongoDBConnection("mongodb://h").get_collection("x")

    async def test_get_database_and_collection_after_connect(self):
        c = MongoDBConnection("mongodb://h", "d")
        c.db = {"coll": "COLL"}
        assert c.get_database() is c.db
        assert c.get_collection("coll") == "COLL"
