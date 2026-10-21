"""Реальный PostgreSQL: AgentRepository (CRUD, теги, роли, шаринг, оценки, закладки)."""

import asyncio

import pytest

from backend.database.postgresql.agent_models import (
    AgentCreate,
    AgentFilters,
    AgentUpdate,
)
from backend.database.postgresql.agent_repository import AgentRepository
from backend.database.postgresql.entity_settings_repository import (
    EntitySettingsRepository,
)

pytestmark = pytest.mark.integration

PROMPT = "Ты помощник, отвечай коротко."


@pytest.fixture
async def repo(pg_connection, monkeypatch):
    r = AgentRepository(pg_connection)
    await r.create_tables()
    settings = EntitySettingsRepository(pg_connection)
    await settings.create_tables()
    monkeypatch.setattr(
        "backend.database.init_db.get_entity_settings_repository", lambda: settings
    )
    r.settings = settings
    return r


async def _make(repo, name="Агент один", author="Owner", **kw):
    data = AgentCreate(name=name, system_prompt=PROMPT, **kw)
    agent_id = await repo.create_agent(data, author, "Имя")
    assert agent_id is not None
    return agent_id


# ------------------------------------------------------------------- CRUD / tags


async def test_create_and_get_roundtrip(repo):
    aid = await _make(
        repo,
        description="d",
        config={"kb_document_ids": [1, 2], "x": {"y": 1}},
        tools=["search"],
        is_public=False,
    )
    agent = await repo.get_agent(aid, "owner")
    assert agent.author_id == "owner"
    assert agent.config == {"kb_document_ids": [1, 2], "x": {"y": 1}}
    assert agent.tools == ["search"]
    assert agent.is_public is False
    assert agent.my_permission == "owner"
    assert agent.average_rating == 0 and agent.total_votes == 0


async def test_get_missing_agent(repo):
    assert await repo.get_agent(424242) is None


async def test_tags_new_dedup_case_insensitive_and_short_skipped(repo):
    a = await _make(repo, new_tags=["Python", "x", "  ", "python"])
    b = await _make(repo, name="Агент два", new_tags=["PYTHON"])
    tags = await repo.list_tags()
    assert [t["name"] for t in tags] == ["Python"]
    tag_id = tags[0]["id"]
    assert await repo.list_agent_ids_by_tags([tag_id]) == sorted([a, b])
    assert await repo.list_agent_ids_by_tags(["bad", None]) == []


async def test_update_fields_partial_and_unicode(repo):
    aid = await _make(repo, description="old")
    assert await repo.update_agent(aid, AgentUpdate(name="Новое имя"), "owner")
    agent = await repo.get_agent(aid)
    assert agent.name == "Новое имя"
    assert agent.description == "old"
    assert agent.system_prompt == PROMPT


async def test_update_by_stranger_is_rejected_admin_allowed(repo):
    aid = await _make(repo, is_public=True)
    assert not await repo.update_agent(aid, AgentUpdate(name="Взлом тут"), "stranger")
    assert (await repo.get_agent(aid)).name == "Агент один"
    assert await repo.update_agent(
        aid, AgentUpdate(name="Админ правка"), "stranger", is_admin=True
    )
    assert (await repo.get_agent(aid)).name == "Админ правка"


async def test_editor_cannot_change_visibility_but_can_edit(repo):
    aid = await _make(repo, is_public=True)
    await repo.share_agent(aid, "owner", ["ed"], permission="editor")
    assert await repo.update_agent(
        aid, AgentUpdate(name="Правка ред", is_public=False), "ed"
    )
    agent = await repo.get_agent(aid)
    assert agent.name == "Правка ред"
    assert agent.is_public is True


async def test_viewer_cannot_edit(repo):
    aid = await _make(repo)
    await repo.share_agent(aid, "owner", ["v"], permission="viewer")
    assert not await repo.update_agent(aid, AgentUpdate(name="Чужая правка"), "v")


async def test_update_tags_replaces_and_removes_orphan_tags(repo):
    aid = await _make(repo, new_tags=["alpha", "beta"])
    assert await repo.update_agent(aid, AgentUpdate(new_tags=["beta", "gamma"]), "owner")
    assert sorted(t["name"] for t in await repo.list_tags()) == ["beta", "gamma"]
    assert await repo.update_agent(aid, AgentUpdate(tag_ids=[]), "owner")
    assert await repo.list_tags() == []


async def test_delete_agent_permissions_and_orphan_tags(repo):
    aid = await _make(repo, new_tags=["solo"])
    assert not await repo.delete_agent(aid, "stranger")
    assert await repo.get_agent(aid) is not None
    assert await repo.delete_agent(aid, "OWNER")
    assert await repo.get_agent(aid) is None
    assert await repo.list_tags() == []
    assert not await repo.delete_agent(aid, "owner")


async def test_admin_can_delete_foreign_agent(repo):
    aid = await _make(repo)
    assert await repo.delete_agent(aid, "someone", is_admin=True)


async def test_delete_cascades_children_and_entity_settings(repo, pg_connection):
    aid = await _make(repo)
    await repo.rate_agent(aid, "u", 4)
    await repo.add_bookmark(aid, "u")
    await repo.share_agent(aid, "owner", ["u"])
    await repo.settings.upsert("agent", str(aid), {"k": 1})
    assert await repo.delete_agent(aid, "owner")
    async with await pg_connection.acquire() as conn:
        for table in ("agent_ratings", "agent_bookmarks", "agent_shares"):
            assert await conn.fetchval(f"SELECT COUNT(*) FROM {table}") == 0
    assert await repo.settings.get("agent", str(aid)) is None


async def test_delete_cleans_chains_and_subagents_even_when_jsonb_is_a_string(repo):
    """Пул без JSON-кодека отдаёт JSONB строкой; ``remove_agent_from_chains`` /
    ``remove_agent_from_subagents`` разбирают её, поэтому id удалённого агента
    (и нечисловой мусор) вычищаются из чужих карточек."""
    victim = await _make(repo, name="Жертва агент")
    other = await _make(repo, name="Другой агент")
    chain = await _make(
        repo,
        name="Цепочка агент",
        config={
            "agent_ids": [victim, other, "junk"],
            "subagents": {"agent_ids": [other, victim], "mode": "x"},
        },
    )
    assert await repo.delete_agent(victim, "owner")
    cfg = (await repo.get_agent(chain)).config
    assert cfg["agent_ids"] == [other]
    assert cfg["subagents"]["agent_ids"] == [other]
    assert cfg["subagents"]["mode"] == "x"


async def test_chain_cleanup_leaves_untouched_when_not_referenced(repo):
    victim = await _make(repo, name="Жертва агент")
    chain = await _make(repo, name="Цепочка агент", config={"agent_ids": ["a", 5]})
    before = (await repo.get_agent(chain)).updated_at
    assert await repo.delete_agent(victim, "owner")
    agent = await repo.get_agent(chain)
    assert agent.config == {"agent_ids": ["a", 5]}
    assert agent.updated_at == before


async def test_concurrent_creates_get_unique_ids(repo):
    ids = await asyncio.gather(
        *(_make(repo, name=f"Агент №{i}") for i in range(15))
    )
    assert len(set(ids)) == 15


# ---------------------------------------------------------------------- listing


async def test_get_agents_visibility_rules(repo):
    pub = await _make(repo, name="Публичный агент", is_public=True)
    priv = await _make(repo, name="Приватный агент", is_public=False)
    agents, total = await repo.get_agents(AgentFilters(sort_by="date"))
    assert {a.id for a in agents} == {pub} and total == 1
    agents, total = await repo.get_agents(AgentFilters(), is_admin=True)
    assert {a.id for a in agents} == {pub, priv} and total == 2
    agents, total = await repo.get_agents(
        AgentFilters(author_id="owner", author_only=True), user_id="owner"
    )
    assert {a.id for a in agents} == {pub, priv}
    agents, _ = await repo.get_agents(
        AgentFilters(author_id="OWNER", author_only=True)
    )
    assert {a.id for a in agents} == {pub, priv}


async def test_get_agents_search_tags_and_pagination(repo):
    a = await _make(repo, name="Аналитик данных", new_tags=["data"])
    b = await _make(repo, name="Переводчик", description="Перевод текста")
    c = await _make(repo, name="Юрист помощник")
    found, _ = await repo.get_agents(AgentFilters(search_query="перевод"))
    assert [x.id for x in found] == [b]
    found, _ = await repo.get_agents(AgentFilters(search_query="АНАЛИТИК"))
    assert [x.id for x in found] == [a]
    tag_id = (await repo.list_tags())[0]["id"]
    found, total = await repo.get_agents(AgentFilters(tag_ids=[tag_id]))
    assert [x.id for x in found] == [a] and total == 1
    page1, total = await repo.get_agents(
        AgentFilters(sort_by="date", limit=2, offset=0)
    )
    page2, _ = await repo.get_agents(AgentFilters(sort_by="date", limit=2, offset=2))
    assert total == 3
    assert len(page1) == 2 and len(page2) == 1
    assert {x.id for x in page1 + page2} == {a, b, c}


async def test_search_pattern_wildcards_are_not_escaped_known_issue(repo):
    await _make(repo, name="Обычный агент")
    await _make(repo, name="Другой агент")
    # '%' в запросе работает как маска ILIKE и находит всё
    found, _ = await repo.get_agents(AgentFilters(search_query="%"))
    assert len(found) == 2
    found, _ = await repo.get_agents(AgentFilters(search_query="Обыч_ый"))
    assert len(found) == 1


async def test_sort_by_rating_and_min_rating(repo):
    low = await _make(repo, name="Низкий рейтинг")
    high = await _make(repo, name="Высокий рейтинг")
    await repo.rate_agent(low, "u1", 2)
    await repo.rate_agent(high, "u1", 5)
    await repo.rate_agent(high, "u2", 4)
    agents, _ = await repo.get_agents(AgentFilters(sort_by="rating"))
    assert [a.id for a in agents] == [high, low]
    assert agents[0].average_rating == 4.5 and agents[0].total_votes == 2
    agents, total = await repo.get_agents(AgentFilters(min_rating=3))
    assert [a.id for a in agents] == [high] and total == 1


async def test_unknown_sort_field_falls_back(repo):
    await _make(repo)
    agents, total = await repo.get_agents(AgentFilters(sort_by="; DROP TABLE agents"))
    assert total == 1 and len(agents) == 1


# ------------------------------------------------------------- permissions / share


async def test_permission_matrix(repo):
    pub = await _make(repo, name="Публичный агент", is_public=True)
    priv = await _make(repo, name="Приватный агент", is_public=False)
    assert await repo.get_user_permission(pub, "owner") == "owner"
    assert await repo.get_user_permission(pub, "x") == "viewer"
    assert await repo.get_user_permission(priv, "x") is None
    assert await repo.get_user_permission(priv, None) is None
    assert await repo.get_user_permission(priv, "x", is_admin=True) == "owner"
    assert await repo.get_user_permission(999999, "x", is_admin=True) is None
    assert await repo.user_can_access_agent(pub, None)
    assert not await repo.user_can_access_agent(priv, None)
    assert not await repo.user_can_access_agent(priv, "x")
    assert await repo.user_can_access_agent(priv, "OWNER")
    assert await repo.user_can_access_agent(priv, "x", is_admin=True)
    assert not await repo.user_can_access_agent(999999, "x", is_admin=True)


async def test_share_grants_access_and_roles(repo):
    priv = await _make(repo, is_public=False)
    added, skipped = await repo.share_agent(
        priv,
        "owner",
        ["Reader", "ed", "owner", " ", "reader"],
        recipient_permissions={"ed": "editor"},
    )
    assert added == ["reader", "ed", "reader"]
    assert skipped == ["owner", " "]
    assert await repo.get_user_permission(priv, "reader") == "viewer"
    assert await repo.get_user_permission(priv, "ED") == "editor"
    assert await repo.user_can_access_agent(priv, "reader")
    shares = await repo.list_agent_shares(priv, "owner")
    assert sorted((s.shared_with_user_id, s.permission) for s in shares) == [
        ("ed", "editor"),
        ("reader", "viewer"),
    ]


async def test_share_by_non_owner_or_missing_agent_adds_nothing(repo):
    aid = await _make(repo)
    assert await repo.share_agent(aid, "stranger", ["x"]) == ([], ["x"])
    assert await repo.share_agent(999999, "owner", ["x"]) == ([], ["x"])
    assert await repo.list_agent_shares(aid, "stranger") == []


async def test_share_upsert_changes_permission(repo):
    aid = await _make(repo)
    await repo.share_agent(aid, "owner", ["u"], permission="viewer")
    await repo.share_agent(aid, "owner", ["u"], permission="editor")
    assert await repo.get_user_permission(aid, "u") == "editor"
    assert len(await repo.list_agent_shares(aid, "owner")) == 1


async def test_unshare_rules(repo):
    aid = await _make(repo, is_public=False)
    await repo.share_agent(aid, "owner", ["a", "b"])
    assert not await repo.unshare_agent(aid, "b", "a")  # чужой получатель
    assert await repo.unshare_agent(aid, "a", "a")  # сам вышел
    assert await repo.unshare_agent(aid, "OWNER", "b")  # владелец убрал
    assert not await repo.unshare_agent(aid, "owner", "b")  # уже нет
    assert not await repo.unshare_agent(999999, "owner", "b")
    assert not await repo.user_can_access_agent(aid, "a")


async def test_shared_with_me_lists_agents_with_flag(repo):
    one = await _make(repo, name="Расшарен один", is_public=False)
    two = await _make(repo, name="Расшарен два", is_public=False)
    await _make(repo, name="Не расшарен")
    await repo.share_agent(one, "owner", ["me"])
    await repo.share_agent(two, "owner", ["me"], permission="editor")
    agents, total = await repo.get_shared_with_me("ME")
    assert total == 2
    assert {a.id for a in agents} == {one, two}
    assert all(a.is_shared_with_me for a in agents)
    page, total = await repo.get_shared_with_me("me", limit=1, offset=1)
    assert total == 2 and len(page) == 1


async def test_get_agent_user_context(repo):
    aid = await _make(repo, is_public=False)
    await repo.share_agent(aid, "owner", ["u"], permission="editor")
    await repo.rate_agent(aid, "u", 3)
    await repo.add_bookmark(aid, "u")
    agent = await repo.get_agent(aid, " U ")
    assert agent.user_rating == 3
    assert agent.is_bookmarked is True
    assert agent.is_shared_with_me is True
    assert agent.my_permission == "editor"
    assert (await repo.get_agent(aid, "nobody")).my_permission is None
    assert (await repo.get_agent(aid, "nobody", is_admin=True)).my_permission == "owner"


# ---------------------------------------------------- ratings / bookmarks / stats


async def test_rate_agent_update_and_validation(repo):
    aid = await _make(repo)
    assert await repo.rate_agent(aid, "U", 5)
    assert await repo.rate_agent(aid, "u", 1)
    stats = await repo.get_agent_stats(aid)
    assert stats.total_votes == 1 and stats.average_rating == 1
    assert stats.rating_distribution == {1: 1}
    assert not await repo.rate_agent(aid, "u", 9)  # CHECK-ограничение в БД
    assert not await repo.rate_agent(aid, "u", 0)
    assert not await repo.rate_agent(999999, "u", 3)


async def test_concurrent_ratings_from_one_user_keep_single_row(repo, pg_connection):
    aid = await _make(repo)
    await asyncio.gather(*(repo.rate_agent(aid, "same", 1 + i % 5) for i in range(10)))
    async with await pg_connection.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM agent_ratings") == 1


async def test_counters(repo):
    aid = await _make(repo)
    assert await repo.increment_views(aid)
    assert await repo.increment_views(aid)
    assert await repo.increment_usage(aid)
    stats = await repo.get_agent_stats(aid)
    assert (stats.views_count, stats.usage_count) == (2, 1)
    assert not await repo.increment_views(999999)
    assert await repo.get_agent_stats(999999) is None


async def test_concurrent_increments_do_not_lose_updates(repo):
    aid = await _make(repo)
    await asyncio.gather(*(repo.increment_usage(aid) for _ in range(20)))
    await asyncio.gather(*(repo.increment_views(aid) for _ in range(20)))
    stats = await repo.get_agent_stats(aid)
    assert (stats.usage_count, stats.views_count) == (20, 20)


async def test_bookmarks(repo):
    a = await _make(repo, name="Закладка один")
    b = await _make(repo, name="Закладка два")
    assert await repo.add_bookmark(a, "U")
    assert await repo.add_bookmark(a, "u")  # повтор безвреден
    assert await repo.add_bookmark(b, "u")
    assert not await repo.add_bookmark(999999, "u")
    ids, total = await repo.get_user_bookmarks("u")
    assert total == 2 and set(ids) == {a, b}
    ids, total = await repo.get_user_bookmarks("u", limit=1, offset=0)
    assert len(ids) == 1 and total == 2
    assert await repo.remove_bookmark(a, "U")
    assert (await repo.get_user_bookmarks("u"))[0] == [b]
    assert await repo.remove_bookmark(a, "u")  # удаление отсутствующей — не ошибка


# --------------------------------------------------------------- kb documents/ids


async def test_orphan_and_claimed_kb_documents(repo):
    a = await _make(repo, name="Агент владелец", config={"kb_document_ids": [1, 2, "3"]})
    await _make(repo, name="Агент второй", config={"kb_document_ids": "oops"})
    await _make(repo, name="Агент третий", config={"kb_document_ids": ["x", 7]})
    assert await repo.find_orphan_kb_document_ids([1, 2, 3, 4, 7, 8]) == [4, 8]
    assert await repo.find_orphan_kb_document_ids([]) == []
    assert await repo.find_documents_claimed_by_other_agents(a, [1, 2, 3, 7]) == [7]
    assert await repo.find_documents_claimed_by_other_agents(None, [1, 7]) == [1, 7]
    assert await repo.find_documents_claimed_by_other_agents(a, []) == []


async def test_get_agent_names_map(repo):
    a = await _make(repo, name="Имя агента А")
    b = await _make(repo, name="Имя агента Б")
    got = await repo.get_agent_names_map([a, b, a, "bad", 0, -1, 999999])
    assert got == {a: "Имя агента А", b: "Имя агента Б"}
    assert await repo.get_agent_names_map([]) == {}


async def test_create_tables_idempotent_and_keeps_data(repo):
    aid = await _make(repo, new_tags=["keep"])
    await repo.create_tables()
    assert (await repo.get_agent(aid)).name == "Агент один"
    assert [t["name"] for t in await repo.list_tags()] == ["keep"]
