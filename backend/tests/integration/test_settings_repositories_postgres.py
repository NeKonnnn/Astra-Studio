"""Реальный PostgreSQL: user_llm_settings, entity_rag_settings, user_projects."""

import asyncio

import pytest

from backend.database.postgresql.agent_repository import AgentRepository
from backend.database.postgresql.entity_settings_repository import (
    MIGRATION_NAME,
    EntitySettingsRepository,
)
from backend.database.postgresql.project_models import ProjectCreate, ProjectUpdate
from backend.database.postgresql.project_repository import ProjectRepository
from backend.database.postgresql.user_settings_repository import UserSettingsRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def user_repo(pg_connection):
    repo = UserSettingsRepository(pg_connection)
    await repo.create_tables()
    return repo


@pytest.fixture
async def entity_repo(pg_connection):
    repo = EntitySettingsRepository(pg_connection)
    await repo.create_tables()
    return repo


@pytest.fixture
async def project_repo(pg_connection, entity_repo, monkeypatch):
    repo = ProjectRepository(pg_connection)
    await repo.create_tables()
    monkeypatch.setattr(
        "backend.database.init_db.get_entity_settings_repository",
        lambda: entity_repo,
    )
    return repo


async def _insert_agent(conn, author="owner"):
    return await conn.fetchval(
        "INSERT INTO agents (name, system_prompt, author_id, author_name) "
        "VALUES ('a', 'p', $1, 'n') RETURNING id",
        author,
    )


# ---------------------------------------------------------------- user settings


async def test_user_settings_roundtrip_lowercases_id(user_repo):
    saved = await user_repo.upsert("Alice@Corp", model_settings={"t": 0.2})
    assert saved["user_id"] == "alice@corp"
    got = await user_repo.get("  ALICE@corp ")
    assert got["model_settings"] == {"t": 0.2}
    assert got["context_prompts"] == {}


async def test_user_settings_partial_upsert_keeps_other_columns(user_repo):
    await user_repo.upsert(
        "u", model_settings={"a": 1}, context_prompts={"b": 2}, rag_settings={"c": 3}
    )
    await user_repo.upsert("u", context_prompts={"b": 20})
    got = await user_repo.get("u")
    assert got["model_settings"] == {"a": 1}
    assert got["context_prompts"] == {"b": 20}
    assert got["rag_settings"] == {"c": 3}


async def test_user_settings_explicit_empty_dict_clears_column(user_repo):
    await user_repo.upsert("u", model_settings={"a": 1})
    await user_repo.upsert("u", model_settings={})
    assert (await user_repo.get("u"))["model_settings"] == {}


async def test_user_settings_unicode_survives(user_repo):
    await user_repo.upsert("u", context_prompts={"p": "Привет, мир ✓"})
    assert (await user_repo.get("u"))["context_prompts"] == {"p": "Привет, мир ✓"}


async def test_user_settings_delete(user_repo):
    await user_repo.upsert("u", model_settings={"a": 1})
    assert await user_repo.delete("U") is True
    assert await user_repo.get("u") is None
    assert await user_repo.delete("u") is False


async def test_user_settings_blank_ids_rejected(user_repo):
    assert await user_repo.upsert("  ", model_settings={}) is None
    assert await user_repo.get("") is None
    assert await user_repo.delete(" ") is False


async def test_user_settings_concurrent_upserts_single_row(user_repo, pg_connection):
    await asyncio.gather(
        *(user_repo.upsert("u", model_settings={"n": i}) for i in range(20))
    )
    async with await pg_connection.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM user_llm_settings") == 1


async def test_user_settings_create_tables_idempotent(user_repo):
    await user_repo.create_tables()
    await user_repo.upsert("u", model_settings={"a": 1})
    await user_repo.create_tables()
    assert (await user_repo.get("u"))["model_settings"] == {"a": 1}


# -------------------------------------------------------------- entity settings


async def test_entity_settings_upsert_get_replaces_whole_document(entity_repo):
    await entity_repo.upsert("agent", "7", {"a": 1, "b": 2}, updated_by=" Bob ")
    await entity_repo.upsert("agent", "7", {"c": 3})
    assert await entity_repo.get("agent", "7") == {"c": 3}


async def test_entity_settings_scopes_are_independent(entity_repo):
    await entity_repo.upsert("agent", "1", {"x": "agent"})
    await entity_repo.upsert("project", "1", {"x": "project"})
    assert (await entity_repo.get("agent", "1"))["x"] == "agent"
    assert (await entity_repo.get("project", "1"))["x"] == "project"


async def test_entity_settings_unknown_scope_falls_back_to_project(entity_repo):
    await entity_repo.upsert("weird", "1", {"x": 1})
    assert await entity_repo.get("project", "1") == {"x": 1}
    assert await entity_repo.get("agent", "1") is None


async def test_entity_settings_updated_by_normalised(entity_repo, pg_connection):
    await entity_repo.upsert("agent", "1", {}, updated_by="  BOB ")
    async with await pg_connection.acquire() as conn:
        assert await conn.fetchval("SELECT updated_by FROM entity_rag_settings") == "bob"


async def test_entity_settings_get_missing_and_blank(entity_repo):
    assert await entity_repo.get("agent", "404") is None
    assert await entity_repo.get("agent", "  ") is None
    assert await entity_repo.upsert("agent", " ", {"a": 1}) is None


async def test_entity_settings_get_many(entity_repo):
    await entity_repo.upsert("agent", "1", {"a": 1})
    await entity_repo.upsert("agent", "2", {"a": 2})
    await entity_repo.upsert("project", "3", {"a": 3})
    got = await entity_repo.get_many("agent", ["1", "2", "3", " ", "9"])
    assert got == {"1": {"a": 1}, "2": {"a": 2}}
    assert await entity_repo.get_many("agent", []) == {}


async def test_entity_settings_delete(entity_repo):
    await entity_repo.upsert("agent", "1", {"a": 1})
    assert await entity_repo.delete("agent", "1") is True
    assert await entity_repo.delete("agent", "1") is False
    assert await entity_repo.get("agent", "1") is None


async def test_entity_settings_non_dict_payload_stored_as_empty(entity_repo):
    assert await entity_repo.upsert("agent", "1", ["not", "dict"]) == {}


async def test_insert_missing_does_not_overwrite_existing(entity_repo):
    await entity_repo.upsert("agent", "1", {"keep": True})
    inserted = await entity_repo.insert_missing(
        [
            ("agent", "1", {"keep": False}, "o"),
            ("agent", "2", {"new": True}, "O"),
            ("agent", " ", {}, "o"),
        ]
    )
    assert inserted == 1
    assert await entity_repo.get("agent", "1") == {"keep": True}
    assert await entity_repo.get("agent", "2") == {"new": True}
    assert await entity_repo.insert_missing([]) == 0


async def test_migration_marks(entity_repo):
    assert await entity_repo.migration_applied() is False
    await entity_repo.mark_migration({"moved": 3})
    assert await entity_repo.migration_applied() is True
    assert await entity_repo.migration_applied("other") is False
    await entity_repo.mark_migration({"moved": 4})
    await entity_repo.clear_migration_mark()
    assert await entity_repo.migration_applied(MIGRATION_NAME) is False


async def test_advisory_lock_released_with_transaction(entity_repo, pg_connection):
    async with await pg_connection.acquire() as c1:
        async with c1.transaction():
            await entity_repo.advisory_lock(c1)
            async with await pg_connection.acquire() as c2:
                got = await c2.fetchval(
                    "SELECT pg_try_advisory_xact_lock($1)", 774_120_001
                )
                assert got is False
        async with await pg_connection.acquire() as c3:
            async with c3.transaction():
                assert await c3.fetchval(
                    "SELECT pg_try_advisory_xact_lock($1)", 774_120_001
                )


async def test_cleanup_orphans_without_parent_tables_returns_zero(entity_repo):
    await entity_repo.upsert("agent", "1", {"a": 1})
    assert await entity_repo.cleanup_orphans() == 0
    assert await entity_repo.get("agent", "1") == {"a": 1}


async def test_cleanup_orphans_removes_only_orphans(
    pg_connection, entity_repo, project_repo
):
    await AgentRepository(pg_connection).create_tables()
    async with await pg_connection.acquire() as conn:
        live_agent = await _insert_agent(conn)
    project = await project_repo.create("owner", ProjectCreate(name="p"))
    await entity_repo.upsert("agent", str(live_agent), {"k": 1})
    await entity_repo.upsert("agent", "99999", {"k": 2})
    await entity_repo.upsert("project", project["id"], {"k": 3})
    await entity_repo.upsert("project", "ghost", {"k": 4})
    assert await entity_repo.cleanup_orphans() == 2
    assert await entity_repo.get("agent", str(live_agent)) == {"k": 1}
    assert await entity_repo.get("project", project["id"]) == {"k": 3}
    assert await entity_repo.get("agent", "99999") is None
    assert await entity_repo.get("project", "ghost") is None


async def test_owners_snapshot_lowercases_and_skips_blank(
    pg_connection, entity_repo, project_repo
):
    await AgentRepository(pg_connection).create_tables()
    async with await pg_connection.acquire() as conn:
        a1 = await _insert_agent(conn, "  Owner@X ")
        await _insert_agent(conn, "   ")
    project = await project_repo.create("Pat", ProjectCreate(name="p"))
    agents, projects = await entity_repo.owners_snapshot()
    assert agents == [(str(a1), "owner@x")]
    assert projects == [(project["id"], "pat")]


async def test_count_foreign_entity_copies_agents_only(
    pg_connection, entity_repo, project_repo, user_repo
):
    # запрос читает обе родительские таблицы — без user_projects он молча даёт 0
    await AgentRepository(pg_connection).create_tables()
    async with await pg_connection.acquire() as conn:
        agent = await _insert_agent(conn, "owner")
    body = {"entities": {"agent": {str(agent): {"k": 1}}}}
    await user_repo.upsert("owner", rag_settings=body)
    await user_repo.upsert("Reader", rag_settings=body)
    await user_repo.upsert("junk", rag_settings={"entities": "not-an-object"})
    # копия владельца не считается, копия читателя — да
    assert await entity_repo.count_foreign_entity_copies() == 1


async def test_count_foreign_entity_copies_known_issue_project_ids_break_count(
    pg_connection, entity_repo, project_repo, user_repo
):
    """Известная проблема: ``e.entity_id::int`` в условии JOIN вычисляется и для
    нечисловых id проектов — запрос падает, ошибка глотается, возвращается 0.
    Верное значение здесь — 2 (агент и проект читателя)."""
    await AgentRepository(pg_connection).create_tables()
    async with await pg_connection.acquire() as conn:
        agent = await _insert_agent(conn, "owner")
    project = await project_repo.create("owner", ProjectCreate(name="p"))
    body = {
        "entities": {"agent": {str(agent): {}}, "project": {project["id"]: {}}}
    }
    await user_repo.upsert("reader", rag_settings=body)
    assert await entity_repo.count_foreign_entity_copies() == 0


# --------------------------------------------------------------------- projects


async def test_project_create_get_list(project_repo):
    created = await project_repo.create(
        "Alice", ProjectCreate(name="  Док  ", instructions="  do it ")
    )
    assert created["name"] == "Док"
    got = await project_repo.get_by_id("alice", created["id"])
    assert got["instructions"] == "do it"
    assert [p["id"] for p in await project_repo.list_by_user("ALICE")] == [created["id"]]


async def test_project_isolation_between_users(project_repo):
    mine = await project_repo.create("alice", ProjectCreate(name="mine"))
    assert await project_repo.get_by_id("bob", mine["id"]) is None
    assert await project_repo.list_by_user("bob") == []
    assert await project_repo.update("bob", mine["id"], ProjectUpdate(name="x")) is None
    assert await project_repo.delete("bob", mine["id"]) is False
    assert (await project_repo.get_by_id("alice", mine["id"]))["name"] == "mine"


async def test_project_create_with_foreign_id_does_not_hijack(project_repo):
    mine = await project_repo.create("alice", ProjectCreate(id="fixed", name="mine"))
    assert mine["id"] == "fixed"
    assert await project_repo.create("bob", ProjectCreate(id="fixed", name="evil")) is None
    assert (await project_repo.get_by_id("alice", "fixed"))["name"] == "mine"


async def test_project_create_same_id_same_user_overwrites(project_repo):
    await project_repo.create("alice", ProjectCreate(id="p1", name="one"))
    again = await project_repo.create("alice", ProjectCreate(id="p1", name="two"))
    assert again["name"] == "two"
    assert len(await project_repo.list_by_user("alice")) == 1


async def test_project_create_rejects_blank_name_and_user(project_repo):
    assert await project_repo.create("alice", ProjectCreate.model_construct(name="  ")) is None
    assert await project_repo.create(" ", ProjectCreate(name="x")) is None


async def test_project_invalid_memory_falls_back_to_default(project_repo):
    created = await project_repo.create(
        "a", ProjectCreate.model_construct(name="x", memory="bogus", instructions="")
    )
    assert created["memory"] == "default"
    updated = await project_repo.update(
        "a", created["id"], ProjectUpdate.model_construct(memory="bogus")
    )
    assert updated["memory"] == "default"


async def test_project_update_partial_keeps_other_fields(project_repo):
    p = await project_repo.create(
        "a", ProjectCreate(name="n", instructions="i", icon="star")
    )
    u = await project_repo.update("a", p["id"], ProjectUpdate(instructions=" new "))
    assert (u["name"], u["instructions"], u["icon"]) == ("n", "new", "star")
    assert u["updatedAt"] >= p["updatedAt"]


async def test_project_update_blank_name_rejected_and_noop_returns_current(project_repo):
    p = await project_repo.create("a", ProjectCreate(name="n"))
    assert (
        await project_repo.update("a", p["id"], ProjectUpdate.model_construct(name="  "))
        is None
    )
    assert (await project_repo.update("a", p["id"], ProjectUpdate()))["name"] == "n"
    assert await project_repo.update("a", "nope", ProjectUpdate(name="x")) is None


async def test_project_list_ordered_by_recent_update(project_repo):
    first = await project_repo.create("a", ProjectCreate(name="first"))
    second = await project_repo.create("a", ProjectCreate(name="second"))
    await project_repo.update("a", first["id"], ProjectUpdate(name="first2"))
    ids = [p["id"] for p in await project_repo.list_by_user("a")]
    assert ids == [first["id"], second["id"]]


async def test_project_delete_cleans_entity_settings(project_repo, entity_repo):
    p = await project_repo.create("a", ProjectCreate(name="n"))
    await entity_repo.upsert("project", p["id"], {"k": 1})
    assert await project_repo.delete("a", p["id"]) is True
    assert await project_repo.get_by_id("a", p["id"]) is None
    assert await entity_repo.get("project", p["id"]) is None
    assert await project_repo.delete("a", p["id"]) is False


async def test_project_delete_by_id_ignores_owner(project_repo, entity_repo):
    p = await project_repo.create("a", ProjectCreate(name="n"))
    await entity_repo.upsert("project", p["id"], {"k": 1})
    assert await project_repo.delete_by_id(p["id"]) is True
    assert await entity_repo.get("project", p["id"]) is None
    assert await project_repo.delete_by_id("") is False
    assert await project_repo.delete_by_id(p["id"]) is False


async def test_project_delete_survives_settings_cleanup_failure(
    project_repo, monkeypatch
):
    p = await project_repo.create("a", ProjectCreate(name="n"))

    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr("backend.database.init_db.get_entity_settings_repository", boom)
    assert await project_repo.delete("a", p["id"]) is True
    assert await project_repo.get_by_id("a", p["id"]) is None
