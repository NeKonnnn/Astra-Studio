"""Реальный PostgreSQL: SkillRepository (CRUD, права, шаринг, файлы, оценки, закладки)."""

import asyncio

import pytest

from backend.database.postgresql.skill_models import (
    SkillCreate,
    SkillFilters,
    SkillUpdate,
)
from backend.database.postgresql.skill_repository import SkillRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def repo(pg_connection):
    r = SkillRepository(pg_connection)
    await r.create_tables()
    return r


async def _make(repo, name="My Skill", author="Owner", **kw):
    kw.setdefault("content", "# Тело навыка\nДелай хорошо.")
    kw.setdefault("slug", None)
    skill = await repo.create_skill(SkillCreate(name=name, **kw), author, "Имя")
    assert skill is not None
    return skill


# ------------------------------------------------------------------------ CRUD


async def test_create_returns_owner_view_and_normalises(repo):
    skill = await _make(
        repo,
        name="Report Writer",
        slug="  My Report/Writer ",
        description="когда писать отчёты",
        allowed_tools=["a", "b"],
        meta={"k": [1, 2]},
    )
    assert skill.slug == "my-report-writer"
    assert skill.author_id == "owner"
    assert skill.my_permission == "owner" and skill.write_access is True
    assert skill.display_title == "Report Writer"
    assert skill.allowed_tools == ["a", "b"]
    assert skill.meta == {"k": [1, 2]}
    assert skill.version == 1


async def test_slug_derived_from_name_and_unicode_name_without_slug_rejected(repo):
    ok = await _make(repo, name="Data Analysis")
    assert ok.slug == "data-analysis"
    # чисто кириллическое имя даёт пустой slug — навык не создаётся
    assert (
        await repo.create_skill(SkillCreate(name="Анализ", content="x"), "o", "n")
        is None
    )


async def test_duplicate_slug_or_name_rejected(repo):
    await _make(repo, name="First", slug="same")
    assert (
        await repo.create_skill(
            SkillCreate(name="Other", slug="same", content="x"), "o", "n"
        )
        is None
    )
    assert (
        await repo.create_skill(
            SkillCreate(name="First", slug="different", content="x"), "o", "n"
        )
        is None
    )


async def test_concurrent_create_same_slug_single_winner(repo):
    results = await asyncio.gather(
        *(
            repo.create_skill(
                SkillCreate(name=f"Name {i}", slug="race", content="x"), "o", "n"
            )
            for i in range(8)
        )
    )
    # проверка «занято» и вставка не атомарны: проигравшие ловят UNIQUE и получают None
    assert sum(1 for r in results if r is not None) == 1


async def test_get_by_id_and_slug_visibility(repo):
    priv = await _make(repo, name="Private One", is_public=False)
    pub = await _make(repo, name="Public One", is_public=True)
    assert (await repo.get_skill_by_id(priv.id, "owner")).id == priv.id
    assert await repo.get_skill_by_id(priv.id, "stranger") is None
    assert await repo.get_skill_by_id(priv.id, None) is None
    assert (await repo.get_skill_by_id(priv.id, "stranger", is_admin=True)).id == priv.id
    got = await repo.get_skill_by_id(pub.id, "stranger")
    assert got.my_permission == "viewer" and got.write_access is False
    assert (await repo.get_skill_by_id(pub.id, None)).my_permission == "viewer"
    assert (await repo.get_skill_by_slug("PUBLIC-one", "x")).id == pub.id
    assert await repo.get_skill_by_slug("nope") is None
    assert await repo.get_skill_by_id(999999, "owner") is None


async def test_update_bumps_version_and_keeps_other_fields(repo):
    skill = await _make(repo, description="d")
    updated = await repo.update_skill(
        skill.id, SkillUpdate(content="новое тело"), "OWNER"
    )
    assert updated.content == "новое тело"
    assert updated.description == "d"
    assert updated.version == 2
    again = await repo.update_skill(skill.id, SkillUpdate(), "owner")
    assert again.version == 3


async def test_update_slug_rules(repo):
    a = await _make(repo, name="Alpha", slug="alpha")
    await _make(repo, name="Beta", slug="beta")
    assert (await repo.update_skill(a.id, SkillUpdate(slug="Gamma X"), "owner")).slug == "gamma-x"
    with pytest.raises(ValueError):
        await repo.update_skill(a.id, SkillUpdate(slug="BETA"), "owner")
    with pytest.raises(ValueError):
        await repo.update_skill(a.id, SkillUpdate(slug="!!!"), "owner")
    assert (await repo.get_skill_by_id(a.id, "owner")).slug == "gamma-x"


async def test_update_permissions_editor_cannot_publish(repo):
    skill = await _make(repo, is_public=False)
    await repo.share_skill(skill.id, "owner", ["ed", "v"], recipient_permissions={"ed": "editor"})
    got = await repo.update_skill(
        skill.id, SkillUpdate(content="правка редактора", is_public=True), "ed"
    )
    assert got.content == "правка редактора"
    assert got.is_public is False
    assert await repo.update_skill(skill.id, SkillUpdate(content="viewer"), "v") is None
    assert await repo.update_skill(skill.id, SkillUpdate(content="чужой"), "x") is None
    published = await repo.update_skill(skill.id, SkillUpdate(is_public=True), "owner")
    assert published.is_public is True


async def test_update_name_clash_returns_none(repo):
    await _make(repo, name="Taken")
    other = await _make(repo, name="Free")
    assert await repo.update_skill(other.id, SkillUpdate(name="Taken"), "owner") is None


async def test_toggle_skill(repo):
    skill = await _make(repo)
    assert (await repo.toggle_skill(skill.id, "owner")).is_active is False
    assert (await repo.toggle_skill(skill.id, "owner")).is_active is True
    assert await repo.toggle_skill(skill.id, "stranger") is None
    assert (await repo.toggle_skill(skill.id, "stranger", is_admin=True)).is_active is False


async def test_delete_skill_rules_and_cascade(repo, pg_connection):
    skill = await _make(repo, is_public=True)
    await repo.share_skill(skill.id, "owner", ["u"])
    await repo.rate_skill(skill.id, "u", 4)
    await repo.add_bookmark(skill.id, "u")
    await repo.upsert_skill_file_text(skill.id, "scripts/a.py", "print(1)")
    assert not await repo.delete_skill(skill.id, "stranger")
    assert not await repo.delete_skill(skill.id, "u")  # получатель удалить не может
    assert await repo.delete_skill(skill.id, "OWNER")
    async with await pg_connection.acquire() as conn:
        for table in ("skill_shares", "skill_ratings", "skill_bookmarks", "skill_files"):
            assert await conn.fetchval(f"SELECT COUNT(*) FROM {table}") == 0
    assert not await repo.delete_skill(skill.id, "owner")


async def test_admin_deletes_foreign_skill(repo):
    skill = await _make(repo)
    assert await repo.delete_skill(skill.id, "other", is_admin=True)


# ----------------------------------------------------------- permissions / share


async def test_permission_matrix(repo):
    priv = await _make(repo, name="Priv", is_public=False)
    pub = await _make(repo, name="Pub", is_public=True)
    assert await repo.get_user_permission(priv.id, "owner") == "owner"
    assert await repo.get_user_permission(priv.id, "x") is None
    assert await repo.get_user_permission(priv.id, None) is None
    assert await repo.get_user_permission(pub.id, "x") == "viewer"
    assert await repo.get_user_permission(999999, "owner") is None
    assert await repo.can_read(priv.id, "x") is False
    assert await repo.can_read(priv.id, "x", is_admin=True) is True
    assert await repo.can_write(pub.id, "x") is False
    assert await repo.can_write(pub.id, "OWNER") is True


async def test_share_roles_and_skips(repo):
    skill = await _make(repo, is_public=False)
    added, skipped = await repo.share_skill(
        skill.id,
        "owner",
        ["Reader", "ed", "owner", " "],
        recipient_permissions={"ed": "editor"},
    )
    assert added == ["reader", "ed"]
    assert skipped == ["owner", " "]
    assert await repo.get_user_permission(skill.id, "reader") == "viewer"
    assert await repo.get_user_permission(skill.id, "ED") == "editor"
    got = await repo.get_skill_by_id(skill.id, "reader")
    assert got.is_shared_with_me is True and got.write_access is False
    assert (await repo.get_skill_by_id(skill.id, "ed")).write_access is True
    shares = await repo.list_skill_shares(skill.id, "owner")
    assert sorted((s.shared_with_user_id, s.permission) for s in shares) == [
        ("ed", "editor"),
        ("reader", "viewer"),
    ]
    assert await repo.list_skill_shares(skill.id, "stranger") == []


async def test_share_by_non_owner_or_missing(repo):
    skill = await _make(repo)
    assert await repo.share_skill(skill.id, "stranger", ["x"]) == ([], ["x"])
    assert await repo.share_skill(999999, "owner", ["x"]) == ([], ["x"])


async def test_share_upsert_replaces_permission(repo):
    skill = await _make(repo)
    await repo.share_skill(skill.id, "owner", ["u"], permission="viewer")
    await repo.share_skill(skill.id, "owner", ["u"], permission="editor")
    assert await repo.get_user_permission(skill.id, "u") == "editor"
    assert len(await repo.list_skill_shares(skill.id, "owner")) == 1


async def test_unshare_rules(repo):
    skill = await _make(repo, is_public=False)
    await repo.share_skill(skill.id, "owner", ["a", "b"])
    assert not await repo.unshare_skill(skill.id, "b", "a")
    assert await repo.unshare_skill(skill.id, "a", "a")
    assert await repo.unshare_skill(skill.id, "OWNER", "b")
    assert not await repo.unshare_skill(skill.id, "owner", "b")
    assert not await repo.unshare_skill(999999, "owner", "b")
    assert await repo.get_skill_by_id(skill.id, "a") is None


# ---------------------------------------------------------------------- listing


async def test_list_views(repo):
    mine = await _make(repo, name="Mine", author="me", is_public=False)
    pub = await _make(repo, name="Pub", author="other", is_public=True)
    shared = await _make(repo, name="Shared", author="other", is_public=False)
    hidden = await _make(repo, name="Hidden", author="other", is_public=False)
    await repo.share_skill(shared.id, "other", ["me"])

    async def ids(view=None, user="me", **kw):
        items, _ = await repo.list_skills(user, SkillFilters(view_option=view), **kw)
        return {i.id for i in items}

    assert await ids() == {mine.id, pub.id, shared.id}
    assert await ids("created") == {mine.id}
    assert await ids("shared") == {shared.id}
    assert await ids("public") == {pub.id}
    assert await ids(user="nobody") == {pub.id}
    assert await ids(is_admin=True) == {mine.id, pub.id, shared.id, hidden.id}
    items, _ = await repo.list_skills("me", SkillFilters())
    assert all(i.my_permission for i in items)


async def test_list_search_and_pagination(repo):
    for i in range(5):
        await _make(repo, name=f"Skill {i}", description="alpha" if i == 0 else None)
    items, total = await repo.list_skills(
        "owner", SkillFilters(search_query="ALPHA")
    )
    assert total == 1 and items[0].name == "Skill 0"
    page1, total = await repo.list_skills("owner", SkillFilters(limit=2, offset=0))
    page3, _ = await repo.list_skills("owner", SkillFilters(limit=2, offset=4))
    assert total == 5 and len(page1) == 2 and len(page3) == 1


async def test_list_search_wildcards_are_escaped(repo):
    await _make(repo, name="Alpha")
    await _make(repo, name="Beta")
    await _make(repo, name="100%_done")
    _, total_percent = await repo.list_skills("owner", SkillFilters(search_query="%"))
    _, total_underscore = await repo.list_skills("owner", SkillFilters(search_query="_"))
    _, total_literal = await repo.list_skills("owner", SkillFilters(search_query="0%_d"))
    assert total_percent == 1 and total_underscore == 1 and total_literal == 1


async def test_list_items_have_no_content_but_get_has(repo):
    skill = await _make(repo, content="секретное тело")
    items, _ = await repo.list_skills("owner", SkillFilters())
    assert not hasattr(items[0], "content")
    assert (await repo.get_skill_by_id(skill.id, "owner")).content == "секретное тело"


async def test_get_accessible_skills_paginates_over_100(repo, pg_connection):
    async with await pg_connection.acquire() as conn:
        await conn.executemany(
            "INSERT INTO skills (slug, name, content, author_id, author_name, is_public) "
            "VALUES ($1, $2, 'c', 'owner', 'n', false)",
            [(f"s{i}", f"S{i}") for i in range(105)],
        )
    got = await repo.get_accessible_skills("owner")
    assert len(got) == 105
    assert len({s.id for s in got}) == 105


async def test_resolve_skills_for_user(repo):
    a = await _make(repo, name="Alpha", slug="alpha")
    b = await _make(repo, name="Beta", slug="beta")
    await repo.toggle_skill(b.id, "owner")  # выключен
    got = await repo.resolve_skills_for_user(["alpha", "beta", "ghost", "", " "], "owner")
    assert [s.id for s in got] == [a.id]
    assert await repo.resolve_skills_for_user(["alpha"], "stranger") == []
    got = await repo.resolve_skills_for_user(["beta"], "stranger", is_admin=True)
    assert got == []  # выключенные не отдаются даже админу


async def test_resolve_skills_deduplicates_across_spellings(repo):
    """id, slug и тот же slug в другом регистре — один и тот же навык: тело
    попадает в системный промпт один раз."""
    a = await _make(repo, name="Alpha", slug="alpha")
    got = await repo.resolve_skills_for_user([str(a.id), "ALPHA", "alpha"], "owner")
    assert [s.id for s in got] == [a.id]


async def test_list_always_apply_skills(repo):
    mine = await _make(repo, name="Mine", author="me", always_apply=True)
    await _make(repo, name="Off", author="me", always_apply=False)
    pub = await _make(repo, name="Pub", author="x", always_apply=True, is_public=True)
    hidden = await _make(repo, name="Hidden", author="x", always_apply=True)
    shared = await _make(repo, name="Shared", author="x", always_apply=True)
    await repo.share_skill(shared.id, "x", ["me"])
    inactive = await _make(repo, name="Inactive", author="me", always_apply=True)
    await repo.toggle_skill(inactive.id, "me")
    got = await repo.list_always_apply_skills("ME")
    assert {s.id for s in got} == {mine.id, pub.id, shared.id}
    assert all(s.content for s in got)
    got = await repo.list_always_apply_skills("me", limit=1)
    assert len(got) == 1
    admin = await repo.list_always_apply_skills("root", is_admin=True)
    assert hidden.id in {s.id for s in admin}


# ------------------------------------------------------------------------ files


async def test_skill_files_upsert_list_content_delete(repo):
    skill = await _make(repo)
    f = await repo.upsert_skill_file_text(skill.id, "\\scripts\\run.py", "print('привет')")
    assert f.relative_path == "scripts/run.py"
    again = await repo.upsert_skill_file_text(skill.id, "/scripts/run.py", "print(2)")
    assert again.id == f.id
    await repo.upsert_skill_file_text(skill.id, "references/doc.md", "# doc")
    files = await repo.list_skill_files(skill.id)
    assert [x.relative_path for x in files] == ["references/doc.md", "scripts/run.py"]
    assert await repo.get_skill_file_content(skill.id, "scripts/run.py") == "print(2)"
    assert await repo.get_skill_file_content(skill.id, "/scripts/run.py") == "print(2)"
    assert await repo.get_skill_file_content(skill.id, "nope") is None
    assert (await repo.get_skill_by_id(skill.id, "owner")).file_count == 2
    assert await repo.delete_skill_file(skill.id, f.id)
    assert not await repo.delete_skill_file(skill.id, f.id)
    assert (await repo.get_skill_by_id(skill.id, "owner")).file_count == 1


async def test_skill_file_rejects_skill_md_and_empty_path(repo):
    skill = await _make(repo)
    assert await repo.upsert_skill_file_text(skill.id, "SKILL.md", "x") is None
    assert await repo.upsert_skill_file_text(skill.id, "/skill.md", "x") is None
    assert await repo.upsert_skill_file_text(skill.id, "", "x") is None
    assert await repo.upsert_skill_file_text(skill.id, "///", "x") is None


async def test_skill_file_whitespace_path_rejected(repo):
    """Путь из одних пробелов считается пустым (проверка идёт после ``strip``)."""
    skill = await _make(repo)
    assert await repo.upsert_skill_file_text(skill.id, "  ", "x") is None
    assert await repo.list_skill_files(skill.id) == []


async def test_skill_file_for_missing_skill_fails(repo):
    assert await repo.upsert_skill_file_text(999999, "a.txt", "x") is None


async def test_skill_file_delete_wrong_skill_does_nothing(repo):
    one = await _make(repo, name="One")
    two = await _make(repo, name="Two")
    f = await repo.upsert_skill_file_text(one.id, "a.txt", "x")
    assert not await repo.delete_skill_file(two.id, f.id)
    assert len(await repo.list_skill_files(one.id)) == 1


async def test_skill_file_traversal_path_is_rejected(repo):
    """Репозиторий отсекает сегменты ``..`` в пути файла (path traversal)."""
    skill = await _make(repo)
    assert await repo.upsert_skill_file_text(skill.id, "../../etc/passwd", "x") is None
    assert await repo.upsert_skill_file_text(skill.id, "a\\..\\b.txt", "x") is None
    assert await repo.list_skill_files(skill.id) == []


async def test_skill_file_bytes_counts_utf8_octets(repo, pg_connection):
    skill = await _make(repo)
    await repo.upsert_skill_file_text(skill.id, "a.txt", "привет")
    async with await pg_connection.acquire() as conn:
        assert await conn.fetchval("SELECT bytes FROM skill_files") == 12


# ---------------------------------------------------- ratings / counters / bookmarks


async def test_rate_skill_rules(repo):
    skill = await _make(repo, is_public=True)
    assert await repo.rate_skill(skill.id, "U", 5)
    assert await repo.rate_skill(skill.id, "u", 2)
    assert await repo.rate_skill(skill.id, "v", 4)
    assert not await repo.rate_skill(skill.id, "u", 0)
    assert not await repo.rate_skill(skill.id, "u", 6)
    assert not await repo.rate_skill(999999, "u", 3)
    got = await repo.get_skill_by_id(skill.id, "u")
    assert got.total_votes == 2
    assert got.average_rating == 3
    assert got.user_rating == 2


async def test_concurrent_ratings_single_row_per_user(repo, pg_connection):
    skill = await _make(repo)
    await asyncio.gather(*(repo.rate_skill(skill.id, "same", 1 + i % 5) for i in range(10)))
    async with await pg_connection.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM skill_ratings") == 1


async def test_counters_and_concurrency(repo):
    skill = await _make(repo)
    assert await repo.increment_views(skill.id)
    assert not await repo.increment_views(999999)
    assert not await repo.increment_usage(999999)
    await asyncio.gather(*(repo.increment_usage(skill.id) for _ in range(20)))
    got = await repo.get_skill_by_id(skill.id, "owner")
    assert (got.views_count, got.usage_count) == (1, 20)


async def test_bookmarks(repo):
    a = await _make(repo, name="A", is_public=True)
    b = await _make(repo, name="B", is_public=True)
    assert await repo.add_bookmark(a.id, "U")
    assert await repo.add_bookmark(a.id, "u")
    assert await repo.add_bookmark(b.id, "u")
    assert not await repo.add_bookmark(999999, "u")
    ids, total = await repo.get_user_bookmarks("u")
    assert total == 2 and set(ids) == {a.id, b.id}
    ids, total = await repo.get_user_bookmarks("u", limit=1)
    assert len(ids) == 1 and total == 2
    assert (await repo.get_skill_by_id(a.id, "u")).is_bookmarked is True
    assert await repo.remove_bookmark(a.id, "U")
    assert (await repo.get_skill_by_id(a.id, "u")).is_bookmarked is False
    assert await repo.remove_bookmark(a.id, "u")


async def test_create_tables_idempotent(repo):
    skill = await _make(repo)
    await repo.create_tables()
    assert (await repo.get_skill_by_id(skill.id, "owner")).name == "My Skill"
