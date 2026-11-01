"""Реальный PostgreSQL: ветки SkillRepository, не покрытые базовыми интеграционными тестами."""

import pytest

from backend.database.postgresql.skill_models import SkillCreate, SkillUpdate
from backend.database.postgresql.skill_repository import SkillRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def repo(pg_connection):
    r = SkillRepository(pg_connection)
    await r.create_tables()
    return r


async def _make(repo, name="My Skill", author="Owner", **kw):
    kw.setdefault("content", "# Тело навыка\nДелай хорошо.")
    skill = await repo.create_skill(SkillCreate(name=name, **kw), author, "Имя")
    assert skill is not None
    return skill


async def test_update_changes_every_field(repo):
    skill = await _make(repo, is_public=False, meta={"old": 1}, allowed_tools=["a"])
    update = SkillUpdate(
        name="Renamed Skill",
        display_title="Заголовок",
        description="новое описание",
        content="новое тело",
        meta={"new": [1, 2]},
        is_active=False,
        is_public=True,
        user_invocable=False,
        disable_model_invocation=True,
        always_apply=True,
        allowed_tools=["x", "y"],
        category="work",
    )
    got = await repo.update_skill(skill.id, update, "owner")
    assert got.name == "Renamed Skill"
    assert got.display_title == "Заголовок"
    assert got.description == "новое описание"
    assert got.content == "новое тело"
    assert got.meta == {"new": [1, 2]}
    assert got.is_active is False and got.is_public is True
    assert got.user_invocable is False and got.disable_model_invocation is True
    assert got.always_apply is True
    assert got.allowed_tools == ["x", "y"]
    assert got.category == "work"
    assert got.version == 2
    assert got.slug == skill.slug  # slug без явной просьбы не меняется


async def test_admin_may_publish_foreign_skill(repo):
    skill = await _make(repo, is_public=False)
    got = await repo.update_skill(skill.id, SkillUpdate(is_public=True), "boss", is_admin=True)
    assert got.is_public is True and got.my_permission == "owner"


async def test_inactive_always_apply_skill_is_not_listed_after_deactivation(repo):
    skill = await _make(repo, always_apply=True, is_public=True)
    assert [s.id for s in await repo.list_always_apply_skills("someone")] == [skill.id]
    await repo.update_skill(skill.id, SkillUpdate(is_active=False), "owner")
    assert await repo.list_always_apply_skills("someone") == []
    assert [s.id for s in await repo.list_always_apply_skills("boss", is_admin=True)] == []


async def test_slug_starting_with_dot_is_normalized(repo):
    """Slug, не подходящий под ``^[a-z0-9]...``, нормализуется: ``.hidden`` -> ``hidden``."""
    skill = await repo.create_skill(
        SkillCreate(name="Hidden skill", slug=".hidden", content="тело"), "owner", "Имя"
    )
    assert skill is not None
    assert skill.slug == "hidden"
