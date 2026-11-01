"""cleanup_invalid_tags: скрипт очистки тегов с фейковым подключением к PostgreSQL."""

import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend import cleanup_invalid_tags as cit

pytestmark = pytest.mark.unit


class FakeConn:
    def __init__(self, rows, fail_on_fetch=False):
        self.rows = rows
        self.executed = []
        self.fail_on_fetch = fail_on_fetch

    async def fetch(self, sql):
        if self.fail_on_fetch:
            raise RuntimeError("БД недоступна")
        return self.rows

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))


class FakeDb:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.connected = False
        self.closed = False
        self.conn = FakeDb.next_conn
        FakeDb.instances.append(self)

    async def connect(self):
        self.connected = True

    async def close(self):
        self.closed = True

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pytest.fixture
def db(monkeypatch):
    FakeDb.instances = []
    pg = SimpleNamespace(host="h", port=5432, database="d", user="u", password="p")
    monkeypatch.setattr(cit, "get_settings", lambda: SimpleNamespace(postgresql=pg))
    monkeypatch.setattr(cit, "PostgreSQLConnection", FakeDb)
    return FakeDb


TAGS = [
    {"id": 1, "name": "a", "usage_count": 3},
    {"id": 2, "name": "\n", "usage_count": 0},
]


async def test_no_invalid_tags_returns_without_changes(db):
    db.next_conn = FakeConn([])

    await cit.cleanup_invalid_tags()

    inst = db.instances[0]
    assert inst.kwargs == {"host": "h", "port": 5432, "database": "d", "user": "u", "password": "p"}
    assert inst.connected and inst.closed
    assert inst.conn.executed == []


async def test_default_action_renames_tags(db):
    db.next_conn = FakeConn(TAGS)

    await cit.cleanup_invalid_tags()

    assert db.instances[0].conn.executed == [
        ("UPDATE tags SET name = $1 WHERE id = $2", ("tag-1", 1)),
        ("UPDATE tags SET name = $1 WHERE id = $2", ("tag-2", 2)),
    ]


async def test_delete_action_removes_tags(db):
    db.next_conn = FakeConn(TAGS)

    await cit.cleanup_invalid_tags("delete")

    assert db.instances[0].conn.executed == [
        ("DELETE FROM tags WHERE id = $1", (1,)),
        ("DELETE FROM tags WHERE id = $1", (2,)),
    ]


async def test_error_is_reraised_and_connection_closed(db):
    db.next_conn = FakeConn([], fail_on_fetch=True)

    with pytest.raises(RuntimeError, match="БД недоступна"):
        await cit.cleanup_invalid_tags()

    assert db.instances[0].closed


class TestMain:
    async def test_rename_runs_without_confirmation(self, monkeypatch):
        calls = []

        async def fake_cleanup(action):
            calls.append(action)

        monkeypatch.setattr(cit, "cleanup_invalid_tags", fake_cleanup)
        monkeypatch.setattr(sys, "argv", ["cleanup"])
        monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("не должен спрашивать"))

        await cit.main()

        assert calls == ["rename"]

    async def test_delete_confirmed_runs_cleanup(self, monkeypatch):
        calls = []

        async def fake_cleanup(action):
            calls.append(action)

        monkeypatch.setattr(cit, "cleanup_invalid_tags", fake_cleanup)
        monkeypatch.setattr(sys, "argv", ["cleanup", "--action", "delete"])
        monkeypatch.setattr("builtins.input", lambda *_: "YES")

        await cit.main()

        assert calls == ["delete"]

    async def test_delete_declined_does_nothing(self, monkeypatch):
        async def fake_cleanup(action):
            pytest.fail("очистка не должна запускаться")

        monkeypatch.setattr(cit, "cleanup_invalid_tags", fake_cleanup)
        monkeypatch.setattr(sys, "argv", ["cleanup", "--action", "delete"])
        monkeypatch.setattr("builtins.input", lambda *_: "no")

        await cit.main()
