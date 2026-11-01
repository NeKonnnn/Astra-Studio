"""SkillRepository на фейковом соединении: чистые хелперы и ветки сбоев/пропусков."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from backend.database.postgresql.skill_models import (
    SkillCreate,
    SkillFilters,
    SkillListItem,
    SkillOut,
    SkillUpdate,
)
from backend.database.postgresql.skill_repository import (
    SkillRepository,
    _parse_meta,
    _parse_str_list,
)
from backend.tests.unit.database_pg_cov.fakes import FakeConn, FakeDb

NOW = datetime(2024, 5, 1, 12, 0, 0)


def _row(**over):
    row = {
        "id": 1,
        "slug": "s",
        "name": "Skill",
        "description": "d",
        "meta": '{"k": 1}',
        "is_active": True,
        "is_public": True,
        "author_id": " Owner ",
        "author_name": "Имя",
        "created_at": NOW,
        "updated_at": NOW,
        "content": "тело",
    }
    row.update(over)
    return row


@pytest.fixture
def conn():
    return FakeConn()


@pytest.fixture
def repo(conn):
    return SkillRepository(FakeDb(conn))


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, {}),
        ({"a": 1}, {"a": 1}),
        ('{"a": 1}', {"a": 1}),
        ("null", {}),
        ("не json", {}),
        (123, {}),
        ([1], {}),
    ],
)
def test_parse_meta(raw, expected):
    assert _parse_meta(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, []),
        ([" a ", "", "b", 3], ["a", "b", "3"]),
        ('[" x ", "", "y"]', ["x", "y"]),
        ('{"not": "list"}', []),
        ("не json", []),
        (42, []),
    ],
)
def test_parse_str_list(raw, expected):
    assert _parse_str_list(raw) == expected


def test_row_to_out_uses_defaults_for_missing_optional_columns():
    repo = SkillRepository(FakeDb())
    out = repo._row_to_out(_row())
    assert isinstance(out, SkillOut)
    assert out.meta == {"k": 1} and out.content == "тело"
    assert out.user_invocable is True and out.disable_model_invocation is False
    assert out.version == 1 and out.file_count == 0 and out.allowed_tools == []

    item = repo._row_to_out(_row(user_invocable=None, content=None), include_content=False)
    assert isinstance(item, SkillListItem)
    assert item.user_invocable is True
    assert not hasattr(item, "content")

    assert repo._row_to_out(_row(content=None)).content == ""


async def test_create_skill_query_failure_returns_none(repo, conn):
    conn.fetchrow.side_effect = RuntimeError("down")
    assert await repo.create_skill(SkillCreate(name="Skill", content="x"), "u", "U") is None


async def test_create_skill_conflict_on_insert_returns_none(repo, conn):
    """slug/name заняты: ON CONFLICT DO NOTHING не возвращает строку -> None."""
    conn.fetchrow.return_value = None
    assert await repo.create_skill(SkillCreate(name="Skill", content="x"), "u", "U") is None
    assert "ON CONFLICT DO NOTHING" in conn.fetchrow.await_args.args[0]


async def test_create_skill_with_cyrillic_name_gets_transliterated_slug(conn):
    """Кириллическое имя без slug транслитерируется (BUG-BE-19 п.5)."""
    conn.fetchrow.return_value = None
    await SkillRepository(FakeDb(conn)).create_skill(
        SkillCreate(name="Анализ отчётов", content="x"), "u", "U"
    )
    assert conn.fetchrow.await_args.args[1] == "analiz-otchetov"


async def test_create_skill_with_unslugifiable_name_is_rejected_before_db(conn):
    """Имя из одних ъ/ь/знаков даёт пустой slug -> None без обращения к БД."""
    db = FakeDb(conn)
    assert await SkillRepository(db).create_skill(
        SkillCreate(name="ъь", content="x"), "u", "U"
    ) is None
    db.acquire.assert_not_awaited()


async def test_create_skill_with_only_dots_slug_is_rejected_before_db(conn):
    """slug из одних «.» после нормализации пуст -> None (BUG-BE-68)."""
    db = FakeDb(conn)
    assert await SkillRepository(db).create_skill(
        SkillCreate(name="X", slug="..", content="x"), "u", "U"
    ) is None
    db.acquire.assert_not_awaited()


@pytest.mark.parametrize("method", ["update_skill", "toggle_skill"])
async def test_write_failures_return_none_for_admin(repo, conn, method):
    conn.execute.side_effect = RuntimeError("deadlock")
    if method == "update_skill":
        result = await repo.update_skill(1, SkillUpdate(content="новое"), "u", is_admin=True)
    else:
        result = await repo.toggle_skill(1, "u", is_admin=True)
    assert result is None


async def test_enrich_without_stats_row_keeps_zero_rating(repo, conn):
    conn.fetchrow.return_value = None
    out = await repo._enrich_skill(_row(), user_id=None, is_admin=True, include_content=True)
    assert out.average_rating == 0.0 and out.total_votes == 0
    assert out.my_permission == "owner" and out.write_access is True


async def test_enrich_survives_rating_query_failure(repo, conn):
    """Сбой запроса рейтинга/закладок не прячет навык: возвращаются нулевые значения."""
    conn.fetchrow.side_effect = RuntimeError("stats down")
    out = await repo._enrich_skill(_row(), user_id="owner", is_admin=False, include_content=False)
    assert isinstance(out, SkillListItem)
    assert out.average_rating == 0.0 and out.user_rating is None
    assert out.is_bookmarked is False
    assert out.my_permission == "owner"


async def test_enrich_private_foreign_skill_is_hidden(repo, conn):
    """Приватный чужой навык без шаринга -> None (viewer не видит)."""
    conn.fetchrow.return_value = None  # шаринга нет
    row = _row(is_public=False)
    assert await repo._enrich_skill(row, user_id="stranger", is_admin=False, include_content=True) is None
    assert await repo._enrich_skill(row, user_id=None, is_admin=False, include_content=True) is None


async def test_list_skills_drops_items_that_enrich_hides(repo, conn, monkeypatch):
    conn.fetchval.return_value = 2
    conn.fetch.return_value = [_row(id=1), _row(id=2)]
    kept = object()
    monkeypatch.setattr(repo, "_enrich_skill", AsyncMock(side_effect=[None, kept]))
    items, total = await repo.list_skills("u", SkillFilters())
    assert items == [kept] and total == 2


async def test_list_skills_filters_build_expected_sql(repo, conn):
    conn.fetchval.return_value = 0
    await repo.list_skills("U", SkillFilters(view_option="public", search_query=" q "))
    count_sql, *count_params = conn.fetchval.await_args.args
    assert "s.is_public = true" in count_sql and "ILIKE" in count_sql
    assert count_params == ["%q%"]


async def test_get_accessible_skills_paginates_and_skips_missing(repo, monkeypatch):
    first = [type("I", (), {"id": i})() for i in range(1, 3)]
    second = [type("I", (), {"id": 3})()]
    list_mock = AsyncMock(side_effect=[(first, 101), (second, 101)])
    monkeypatch.setattr(repo, "list_skills", list_mock)
    full = {1: "S1", 3: "S3"}  # skill 2 исчез между вызовами -> пропускается
    monkeypatch.setattr(
        repo,
        "get_skill_by_id",
        AsyncMock(side_effect=lambda sid, user_id, is_admin: full.get(sid)),
    )
    assert await repo.get_accessible_skills("u") == ["S1", "S3"]
    assert list_mock.await_count == 2
    assert list_mock.await_args_list[1].args[1].offset == 100


async def test_list_always_apply_skills_drops_hidden_and_uses_admin_query(repo, conn, monkeypatch):
    conn.fetch.return_value = [_row(id=1), _row(id=2)]
    keep = object()
    monkeypatch.setattr(repo, "_enrich_skill", AsyncMock(side_effect=[None, keep]))
    assert await repo.list_always_apply_skills("U", is_admin=True, limit=5) == [keep]
    sql = " ".join(conn.fetch.await_args.args[0].split())
    assert "always_apply = true AND is_active = true" in sql
    assert conn.fetch.await_args.args[1] == 5
