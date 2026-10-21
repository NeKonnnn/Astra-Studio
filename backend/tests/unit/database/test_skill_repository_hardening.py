"""SkillRepository: дедупликация resolve и валидация путей файлов (без БД)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.database.postgresql.skill_repository import SkillRepository, escape_like

pytestmark = pytest.mark.unit


def _repo():
    return SkillRepository(MagicMock())


@pytest.mark.parametrize("path", ["  ", "", "///", "../x", "a/../../b", "a\\..\\b", "SKILL.md"])
def test_upsert_skill_file_rejects_bad_paths_before_touching_db(path):
    repo = _repo()
    repo.db_connection.acquire = AsyncMock(side_effect=AssertionError("DB must not be touched"))

    assert asyncio.run(repo.upsert_skill_file_text(1, path, "x")) is None


def test_resolve_skills_deduplicates_by_skill_id():
    repo = _repo()
    alpha = SimpleNamespace(id=5, is_active=True)
    repo.get_skill_by_id = AsyncMock(return_value=alpha)
    repo.get_skill_by_slug = AsyncMock(return_value=alpha)

    got = asyncio.run(repo.resolve_skills_for_user(["5", "ALPHA", "alpha"], "owner"))

    assert got == [alpha]


def test_resolve_skills_skips_inactive_and_keeps_distinct():
    repo = _repo()
    a = SimpleNamespace(id=1, is_active=True)
    b = SimpleNamespace(id=2, is_active=False)
    c = SimpleNamespace(id=3, is_active=True)
    by_slug = {"a": a, "b": b, "c": c}
    repo.get_skill_by_id = AsyncMock(return_value=None)
    repo.get_skill_by_slug = AsyncMock(side_effect=lambda slug, **kw: by_slug.get(slug))

    got = asyncio.run(repo.resolve_skills_for_user(["a", "b", "c"], "owner"))

    assert got == [a, c]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("abc", "abc"), ("%", "\\%"), ("_", "\\_"), ("a\\b", "a\\\\b"), ("100%_x", "100\\%\\_x")],
)
def test_escape_like_neutralises_wildcards(raw, expected):
    assert escape_like(raw) == expected
