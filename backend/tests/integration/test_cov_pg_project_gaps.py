"""Реальный PostgreSQL: ProjectRepository.update по всем полям и границы владения."""

import pytest

from backend.database.postgresql.project_models import ProjectCreate, ProjectUpdate
from backend.database.postgresql.project_repository import ProjectRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def repo(pg_connection):
    r = ProjectRepository(pg_connection)
    await r.create_tables()
    return r


async def test_update_changes_every_field_and_moves_updated_at(repo):
    created = await repo.create("Alice", ProjectCreate(name="Старое", instructions="i"))
    updated = await repo.update(
        " alice ",
        created["id"],
        ProjectUpdate(
            name=" Новое ",
            instructions=" инструкции ",
            memory="project-only",
            icon="star",
            iconType="emoji",
            iconColor="#112233",
        ),
    )
    assert updated["name"] == "Новое"
    assert updated["instructions"] == "инструкции"
    assert updated["memory"] == "project-only"
    assert (updated["icon"], updated["iconType"], updated["iconColor"]) == (
        "star",
        "emoji",
        "#112233",
    )
    assert updated["updatedAt"] >= created["updatedAt"]
    assert (await repo.get_by_id("alice", created["id"])) == updated


async def test_update_of_foreign_project_changes_nothing(repo):
    created = await repo.create("alice", ProjectCreate(name="Мой"))
    assert await repo.update("bob", created["id"], ProjectUpdate(name="Взлом")) is None
    assert (await repo.get_by_id("alice", created["id"]))["name"] == "Мой"


async def test_update_without_fields_returns_current_state(repo):
    created = await repo.create("alice", ProjectCreate(name="Мой"))
    assert await repo.update("alice", created["id"], ProjectUpdate()) == created
    assert await repo.update("alice", "missing", ProjectUpdate()) is None


async def test_create_with_existing_id_of_other_user_does_not_steal_project(repo):
    first = await repo.create("alice", ProjectCreate(id="shared-id", name="Алисин"))
    assert first["id"] == "shared-id"
    assert await repo.create("bob", ProjectCreate(id="shared-id", name="Бобов")) is None
    assert (await repo.get_by_id("alice", "shared-id"))["name"] == "Алисин"
