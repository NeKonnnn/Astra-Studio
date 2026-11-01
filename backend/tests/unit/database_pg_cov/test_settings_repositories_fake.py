"""Репозитории настроек и проектов на фейковом соединении: охранные условия и разбор JSONB."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from backend.database.postgresql import entity_settings_repository as es_module
from backend.database.postgresql import user_settings_repository as us_module
from backend.database.postgresql.entity_settings_repository import (
    EntitySettingsRepository,
    normalize_entity_id,
    normalize_scope,
)
from backend.database.postgresql.project_models import ProjectCreate, ProjectUpdate
from backend.database.postgresql.project_repository import ProjectRepository
from backend.database.postgresql.user_settings_repository import UserSettingsRepository
from backend.tests.unit.database_pg_cov.fakes import FakeConn, FakeDb

NOW = datetime(2024, 1, 2, 3, 4, 5)


@pytest.fixture
def conn():
    return FakeConn()


# --------------------------------------------------------------- _as_dict (оба модуля)


@pytest.mark.parametrize("module", [es_module, us_module])
@pytest.mark.parametrize(
    "value, expected",
    [
        ({"a": 1}, {"a": 1}),
        ('{"a": 1}', {"a": 1}),
        ("[1, 2]", {}),  # валидный JSON, но не объект
        ("не json", {}),
        ("   ", {}),
        (None, {}),
        (5, {}),
    ],
)
def test_as_dict(module, value, expected):
    assert module._as_dict(value) == expected


def test_as_dict_returns_copy_of_dict():
    source = {"a": 1}
    copy = es_module._as_dict(source)
    copy["b"] = 2
    assert source == {"a": 1}


def test_normalizers():
    assert normalize_scope(" AGENT ") == "agent"
    assert normalize_scope("garbage") == "project"
    assert normalize_scope(None) == "project"
    assert normalize_entity_id("  7 ") == "7"
    assert normalize_entity_id(0) == "0"
    assert normalize_entity_id(None) is None
    assert normalize_entity_id("   ") is None


# ------------------------------------------------------------ EntitySettingsRepository


@pytest.fixture
def entity(conn):
    return EntitySettingsRepository(FakeDb(conn))


async def test_entity_empty_ids_skip_database(conn):
    db = FakeDb(conn)
    repo = EntitySettingsRepository(db)
    assert await repo.get("agent", "  ") is None
    assert await repo.upsert("agent", "", {"a": 1}) is None
    assert await repo.delete("agent", None) is False
    assert await repo.get_many("agent", ["", None, "  "]) == {}
    assert await repo.insert_missing([]) == 0
    db.acquire.assert_not_awaited()


async def test_entity_get_parses_string_jsonb(entity, conn):
    conn.fetchrow.return_value = {"settings": '{"top_k": 5}'}
    assert await entity.get("AGENT", " 3 ") == {"top_k": 5}
    assert conn.fetchrow.await_args.args[1:] == ("agent", "3")


async def test_entity_upsert_non_dict_settings_stored_as_empty_object(entity, conn):
    conn.fetchrow.return_value = {"settings": {}}
    assert await entity.upsert("agent", "1", ["not", "dict"], updated_by=" BoB ") == {}
    args = conn.fetchrow.await_args.args
    assert args[3] == "{}" and args[4] == "bob"


async def test_entity_delete_reports_whether_row_was_removed(entity, conn):
    conn.execute.return_value = "DELETE 1"
    assert await entity.delete("agent", "1") is True
    conn.execute.return_value = "DELETE 0"
    assert await entity.delete("agent", "1") is False


async def test_entity_cleanup_orphans_counts_removed_rows(entity, conn):
    conn.execute.return_value = "DELETE 3"
    assert await entity.cleanup_orphans() == 3
    conn.execute.return_value = "DELETE 0"
    assert await entity.cleanup_orphans() == 0


async def test_entity_insert_missing_counts_new_rows_and_skips_blank_ids(entity, conn):
    conn.execute.side_effect = ["INSERT 0 1", "INSERT 0 0", "INSERT 0 1"]
    rows = [
        ("agent", "1", {"a": 1}, " Owner "),
        ("agent", "2", {}, None),
        ("bogus", "3", None, ""),
        ("agent", "  ", {"x": 1}, "u"),  # пустой id пропускается без запроса
    ]
    assert await entity.insert_missing(rows) == 2
    assert conn.execute.await_count == 3
    first = conn.execute.await_args_list[0].args
    assert first[1:] == ("agent", "1", '{"a": 1}', "owner")
    assert conn.execute.await_args_list[2].args[1] == "project"  # неизвестный scope -> project


async def test_entity_insert_missing_returns_partial_count_on_failure(entity, conn):
    """Сбой на второй вставке: уже вставленное первой строкой не теряется в счётчике."""
    conn.execute.side_effect = ["INSERT 0 1", RuntimeError("fail")]
    rows = [("agent", "1", {}, "u"), ("agent", "2", {}, "u")]
    assert await entity.insert_missing(rows) == 1


async def test_entity_advisory_lock_uses_migration_lock_id(entity, conn):
    await entity.advisory_lock(conn)
    conn.execute.assert_awaited_once_with(
        "SELECT pg_advisory_xact_lock($1)", es_module._MIGRATION_LOCK_ID
    )


async def test_entity_count_foreign_copies_returns_int(entity, conn):
    conn.fetchval.return_value = 4
    assert await entity.count_foreign_entity_copies() == 4
    conn.fetchval.return_value = None
    assert await entity.count_foreign_entity_copies() == 0


async def test_entity_migration_flags(entity, conn):
    conn.fetchrow.return_value = {"applied_at": NOW}
    assert await entity.migration_applied() is True
    conn.fetchrow.return_value = None
    assert await entity.migration_applied() is False
    await entity.mark_migration({"n": 1}, name="m1")
    assert conn.execute.await_args.args[1:] == ("m1", '{"n": 1}')
    await entity.clear_migration_mark(name="m1")
    assert "DELETE FROM backend_data_migrations" in conn.execute.await_args.args[0]


async def test_entity_owners_snapshot_skips_rows_without_owner(entity, conn):
    conn.fetch.side_effect = [
        [{"id": 1, "owner": "alice"}, {"id": 2, "owner": ""}, {"id": 3, "owner": None}],
        [{"id": "p1", "owner": "bob"}, {"id": "p2", "owner": ""}],
    ]
    assert await entity.owners_snapshot() == ([("1", "alice")], [("p1", "bob")])


async def test_entity_owners_snapshot_keeps_agents_when_projects_fail(entity, conn):
    conn.fetch.side_effect = [[{"id": 1, "owner": "alice"}], RuntimeError("no table")]
    assert await entity.owners_snapshot() == ([("1", "alice")], [])


# ------------------------------------------------------------ UserSettingsRepository


@pytest.fixture
def user_settings(conn):
    return UserSettingsRepository(FakeDb(conn))


def _user_row(**over):
    row = {
        "user_id": "u",
        "model_settings": '{"m": 1}',
        "context_prompts": {"c": 2},
        "rag_settings": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(over)
    return row


async def test_user_settings_blank_user_skips_database(conn):
    db = FakeDb(conn)
    repo = UserSettingsRepository(db)
    assert await repo.get("  ") is None
    assert await repo.upsert(None) is None
    assert await repo.delete("") is False
    db.acquire.assert_not_awaited()


async def test_user_settings_get_normalizes_row(user_settings, conn):
    conn.fetchrow.return_value = _user_row()
    result = await user_settings.get(" U ")
    assert result == {
        "user_id": "u",
        "model_settings": {"m": 1},
        "context_prompts": {"c": 2},
        "rag_settings": {},
        "created_at": NOW,
        "updated_at": NOW,
    }
    assert conn.fetchrow.await_args.args[1] == "u"


async def test_user_settings_get_missing_row(user_settings, conn):
    conn.fetchrow.return_value = None
    assert await user_settings.get("u") is None


async def test_user_settings_upsert_flags_only_provided_sections(user_settings, conn):
    conn.fetchrow.return_value = _user_row()
    result = await user_settings.upsert("U", rag_settings={"r": 1}, context_prompts="bad")
    assert result["model_settings"] == {"m": 1}
    args = conn.fetchrow.await_args.args
    assert args[1] == "u"
    # model_settings не передан; context_prompts передан, но не dict -> пишется {}; rag — dict
    assert args[2:5] == ("{}", "{}", '{"r": 1}')
    assert args[5:8] == (False, True, True)


async def test_user_settings_upsert_without_returned_row(user_settings, conn):
    conn.fetchrow.return_value = None
    assert await user_settings.upsert("u", model_settings={}) is None


async def test_user_settings_delete_by_command_tag(user_settings, conn):
    conn.execute.return_value = "DELETE 1"
    assert await user_settings.delete(" U ") is True
    conn.execute.return_value = "DELETE 0"
    assert await user_settings.delete("u") is False


# ------------------------------------------------------------------ ProjectRepository


def _project_row(**over):
    row = {
        "id": "p1",
        "user_id": "u",
        "name": "Проект",
        "instructions": "",
        "memory": "default",
        "icon": None,
        "icon_type": None,
        "icon_color": None,
        "created_at": NOW,
        "updated_at": "уже строка",
    }
    row.update(over)
    return row


@pytest.fixture
def projects(conn):
    return ProjectRepository(FakeDb(conn))


async def test_project_guards_skip_database(conn):
    db = FakeDb(conn)
    repo = ProjectRepository(db)
    assert await repo.list_by_user("  ") == []
    assert await repo.get_by_id("u", " ") is None
    assert await repo.get_by_id("", "p") is None
    assert await repo.create("", ProjectCreate(name="X")) is None
    assert await repo.create("u", ProjectCreate(name="   ")) is None
    assert await repo.update("u", " ", ProjectUpdate(name="X")) is None
    assert await repo.update("u", "p", ProjectUpdate(name="   ")) is None
    assert await repo.delete("u", " ") is False
    assert await repo.delete("", "p") is False
    assert await repo.delete_by_id("  ") is False
    db.acquire.assert_not_awaited()


async def test_project_row_mapping_formats_dates(projects, conn):
    conn.fetch.return_value = [_project_row()]
    (project,) = await projects.list_by_user(" U ")
    assert project["createdAt"] == NOW.isoformat()
    assert project["updatedAt"] == "уже строка"
    assert project["iconType"] is None and project["name"] == "Проект"
    assert conn.fetch.await_args.args[1] == "u"


async def test_project_get_by_id_trims_arguments(projects, conn):
    conn.fetchrow.return_value = None
    assert await projects.get_by_id(" U ", " p1 ") is None
    assert conn.fetchrow.await_args.args[1:] == ("u", "p1")


async def test_project_create_generates_id_and_normalizes_memory(projects, conn):
    conn.fetchrow.return_value = _project_row(id="gen")
    created = await projects.create("U", ProjectCreate(name=" Имя ", instructions=" инстр "))
    assert created["id"] == "gen"
    args = conn.fetchrow.await_args.args
    pid, uid, name, instructions, memory = args[1:6]
    assert pid.isdigit() or pid[:13].isdigit()  # миллисекунды + hex-суффикс
    assert (uid, name, instructions, memory) == ("u", "Имя", "инстр", "default")


async def test_project_create_keeps_client_id_and_returns_none_on_conflict(projects, conn):
    """ON CONFLICT ... WHERE не обновил чужую строку -> RETURNING пуст -> None."""
    conn.fetchrow.return_value = None
    data = ProjectCreate(id=" mine ", name="Имя", memory="project-only")
    assert await projects.create("u", data) is None
    assert conn.fetchrow.await_args.args[1] == "mine"
    assert conn.fetchrow.await_args.args[5] == "project-only"


async def test_project_update_without_fields_returns_current(projects, monkeypatch):
    monkeypatch.setattr(projects, "get_by_id", AsyncMock(return_value={"id": "p"}))
    assert await projects.update(" U ", " p ", ProjectUpdate()) == {"id": "p"}
    projects.get_by_id.assert_awaited_once_with("u", "p")


async def test_project_update_builds_sql_for_every_field(projects, conn):
    conn.fetchrow.return_value = _project_row(name="Новое")
    data = ProjectUpdate(
        name=" Новое ",
        instructions=" i ",
        memory="project-only",
        icon="star",
        iconType="emoji",
        iconColor="#fff",
    )
    assert (await projects.update("U", "p1", data))["name"] == "Новое"
    sql = " ".join(conn.fetchrow.await_args.args[0].split())
    for column in ("name", "instructions", "memory", "icon", "icon_type", "icon_color"):
        assert f"{column} = $" in sql
    assert "updated_at = NOW()" in sql
    assert conn.fetchrow.await_args.args[1:] == (
        "u", "p1", "Новое", "i", "project-only", "star", "emoji", "#fff",
    )


async def test_project_update_unknown_row_returns_none(projects, conn):
    conn.fetchrow.return_value = None
    assert await projects.update("u", "p", ProjectUpdate(icon="x")) is None


@pytest.mark.parametrize("method, args", [("delete", ("u", "p")), ("delete_by_id", ("p",))])
async def test_project_delete_cleans_entity_settings_only_when_row_removed(
    projects, conn, monkeypatch, method, args
):
    settings = type("S", (), {"delete": AsyncMock(return_value=True)})()
    monkeypatch.setattr(
        "backend.database.init_db.get_entity_settings_repository", lambda: settings
    )
    conn.execute.return_value = "DELETE 1"
    assert await getattr(projects, method)(*args) is True
    settings.delete.assert_awaited_once_with("project", "p")

    settings.delete.reset_mock()
    conn.execute.return_value = "DELETE 0"
    assert await getattr(projects, method)(*args) is False
    settings.delete.assert_not_awaited()


async def test_project_delete_entity_settings_branches(projects, monkeypatch):
    monkeypatch.setattr(
        "backend.database.init_db.get_entity_settings_repository", lambda: None
    )
    await projects._delete_entity_settings("p")  # репозитория настроек нет — не бросает

    failing = type("S", (), {"delete": AsyncMock(side_effect=RuntimeError("x"))})()
    monkeypatch.setattr(
        "backend.database.init_db.get_entity_settings_repository", lambda: failing
    )
    await projects._delete_entity_settings("p")  # сбой глотается
    failing.delete.assert_awaited_once_with("project", "p")
