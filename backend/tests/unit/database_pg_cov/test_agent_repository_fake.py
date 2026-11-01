"""AgentRepository на фейковом соединении: ветки, которые на реальной БД недостижимы или хрупки.

Пул asyncpg без JSON-кодека отдаёт JSONB строкой (см. BUG-BE-18), поэтому логика
чистки цепочек, рассчитанная на ``dict``, на реальном PostgreSQL не срабатывает.
Здесь конфиг подаётся уже словарём — так проверяется сама логика чистки.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from backend.database.postgresql.agent_models import AgentUpdate
from backend.database.postgresql.agent_repository import AgentRepository
from backend.tests.unit.database_pg_cov.fakes import FakeConn, FakeDb


@pytest.fixture
def conn():
    return FakeConn()


@pytest.fixture
def repo(conn):
    return AgentRepository(FakeDb(conn))


def _updates(conn):
    """Список (id карточки, config) из вызовов UPDATE agents."""
    out = []
    for call in conn.execute.await_args_list:
        sql, cfg_json, row_id = call.args
        assert "UPDATE agents SET config" in " ".join(sql.split())
        out.append((row_id, json.loads(cfg_json)))
    return out


async def test_remove_agent_from_chains_cleans_references(repo, conn):
    conn.fetch.return_value = [
        {"id": 10, "config": {"agent_ids": [1, "2", "junk", None, 3], "keep": True}},
        {"id": 11, "config": {"agent_ids": [3, 4]}},  # без удаляемого id
        {"id": 12, "config": {"agent_ids": "not-a-list"}},
        {"id": 13, "config": "строка вместо словаря"},
    ]
    await repo.remove_agent_from_chains(2)
    assert _updates(conn) == [(10, {"agent_ids": [1, 3], "keep": True})]


async def test_remove_agent_from_chains_handles_update_failure(repo, conn):
    conn.fetch.return_value = [{"id": 10, "config": {"agent_ids": [2]}}]
    conn.execute.side_effect = RuntimeError("update failed")
    await repo.remove_agent_from_chains(2)  # исключение не уходит наружу
    conn.execute.assert_awaited_once()


async def test_remove_agent_from_subagents_cleans_references(repo, conn):
    conn.fetch.return_value = [
        {
            "id": 20,
            "config": {"subagents": {"agent_ids": [5, "7", "x", 9], "mode": "m"}, "a": 1},
        },
        {"id": 21, "config": {"subagents": {"agent_ids": [1, 2]}}},  # id нет
        {"id": 22, "config": {"subagents": "oops"}},
        {"id": 23, "config": {"subagents": {"agent_ids": "oops"}}},
        {"id": 24, "config": {}},
        {"id": 25, "config": "{}"},  # строка (как отдаёт пул без кодека) пропускается
    ]
    await repo.remove_agent_from_subagents(7)
    assert _updates(conn) == [
        (20, {"subagents": {"agent_ids": [5, 9], "mode": "m"}, "a": 1})
    ]


async def test_delete_orphan_tags_failure_returns_zero(repo, conn):
    conn.fetchval.side_effect = RuntimeError("catalog error")
    assert await repo._delete_orphan_tags(conn) == 0
    conn.fetch.assert_not_awaited()


async def test_delete_orphan_tags_protects_prompt_tags_when_table_exists(repo, conn):
    conn.fetchval.return_value = True
    conn.fetch.return_value = [{"name": "a"}, {"name": "b"}]
    assert await repo._delete_orphan_tags(conn) == 2
    sql = conn.fetch.await_args.args[0]
    assert "prompt_tags" in sql and "RETURNING t.name" in sql


async def test_delete_orphan_tags_without_prompt_tags_table(repo, conn):
    conn.fetchval.return_value = False
    conn.fetch.return_value = []
    assert await repo._delete_orphan_tags(conn) == 0
    assert "prompt_tags" not in conn.fetch.await_args.args[0]


async def test_update_agent_query_failure_returns_false_for_admin(repo, conn):
    """Права проверены (admin), но UPDATE упал -> False, а не исключение."""
    conn.execute.side_effect = RuntimeError("deadlock")
    assert await repo.update_agent(1, AgentUpdate(name="Новое имя"), "u", is_admin=True) is False


async def test_get_shared_with_me_skips_shares_of_missing_agents(repo, conn, monkeypatch):
    """Шаринг без агента (рассинхрон) пропускается, а total берётся из COUNT."""
    conn.fetchval.return_value = 2
    conn.fetch.return_value = [{"agent_id": 1}, {"agent_id": 2}]
    alive = type("A", (), {"is_shared_with_me": False})()
    monkeypatch.setattr(
        repo, "get_agent", AsyncMock(side_effect=[None, alive]), raising=True
    )
    agents, total = await repo.get_shared_with_me(" User ")
    assert agents == [alive] and alive.is_shared_with_me is True
    assert total == 2
    assert repo.get_agent.await_args_list[0].args == (1, "user")


async def test_delete_entity_settings_removes_agent_settings(repo, monkeypatch):
    settings = type("S", (), {"delete": AsyncMock(return_value=True)})()
    monkeypatch.setattr(
        "backend.database.init_db.get_entity_settings_repository", lambda: settings
    )
    await repo._delete_entity_settings(5)
    settings.delete.assert_awaited_once_with("agent", "5")


async def test_delete_entity_settings_without_repository_is_noop(repo, monkeypatch):
    monkeypatch.setattr(
        "backend.database.init_db.get_entity_settings_repository", lambda: None
    )
    await repo._delete_entity_settings(5)  # не бросает


async def test_delete_entity_settings_swallows_errors(repo, monkeypatch):
    """Сбой чистки настроек не должен воскрешать удалённого агента."""
    settings = type("S", (), {"delete": AsyncMock(side_effect=RuntimeError("x"))})()
    monkeypatch.setattr(
        "backend.database.init_db.get_entity_settings_repository", lambda: settings
    )
    await repo._delete_entity_settings(5)
    settings.delete.assert_awaited_once()


async def test_get_agent_names_map_filters_bad_ids_and_blank_names(repo, conn):
    conn.fetch.return_value = [
        {"id": 3, "name": " Имя "},
        {"id": 4, "name": "   "},
        {"id": 5, "name": None},
    ]
    result = await repo.get_agent_names_map(["3", 3, 4, 5, "x", None, 0, -2])
    assert result == {3: "Имя"}
    assert conn.fetch.await_args.args[1] == [3, 4, 5]


async def test_get_agent_names_map_without_valid_ids_skips_db(conn):
    db = FakeDb(conn)
    assert await AgentRepository(db).get_agent_names_map(["a", 0, None]) == {}
    db.acquire.assert_not_awaited()
