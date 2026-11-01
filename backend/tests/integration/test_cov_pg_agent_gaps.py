"""Реальный PostgreSQL: ветки AgentRepository, не покрытые базовыми интеграционными тестами.

Дополняет ``test_agent_repository_postgres.py``: контекст пользователя в списке,
фильтр по автору без ``author_only``, обновление всех полей, теги при обновлении
и «битые» теги в справочнике.
"""

import pytest

from backend.database.postgresql.agent_models import (
    AgentCreate,
    AgentFilters,
    AgentUpdate,
)
from backend.database.postgresql.agent_repository import AgentRepository

pytestmark = pytest.mark.integration

PROMPT = "Ты помощник, отвечай коротко."


@pytest.fixture
async def repo(pg_connection):
    r = AgentRepository(pg_connection)
    await r.create_tables()
    return r


async def _make(repo, name="Агент один", author="Owner", **kw):
    agent_id = await repo.create_agent(
        AgentCreate(name=name, system_prompt=PROMPT, **kw), author, "Имя"
    )
    assert agent_id is not None
    return agent_id


async def test_get_agents_fills_user_context_for_each_agent(repo):
    """Оценка, закладка, шаринг и роль считаются для каждой карточки списка."""
    own = await _make(repo, name="Мой агент", author="Me")
    shared = await _make(repo, name="Расшаренный", author="Other", is_public=False)
    public = await _make(repo, name="Публичный", author="Other")
    private = await _make(repo, name="Чужой приватный", author="Other", is_public=False)
    assert await repo.share_agent(shared, "other", ["me"], "editor") == (["me"], [])
    assert await repo.rate_agent(public, "me", 4)
    assert await repo.add_bookmark(public, "me")

    agents, total = await repo.get_agents(
        AgentFilters(sort_by="date", limit=50), user_id="  ME ", is_admin=False
    )
    by_id = {a.id: a for a in agents}
    # приватный чужой и нерасшаренный в выдачу не попадает
    assert private not in by_id
    assert total == len(agents)
    assert by_id[own].my_permission == "owner"
    assert by_id[public].my_permission == "viewer"
    assert by_id[public].user_rating == 4 and by_id[public].is_bookmarked is True
    assert by_id[public].is_shared_with_me is False
    assert by_id[own].user_rating is None and by_id[own].is_bookmarked is False

    # админ видит приватные и получает owner независимо от автора
    admin_agents, _ = await repo.get_agents(AgentFilters(limit=50), "me", is_admin=True)
    assert {a.my_permission for a in admin_agents} == {"owner"}
    assert private in {a.id for a in admin_agents}


async def test_get_agents_shared_agent_gets_share_permission(repo):
    """Роль из шаринга побеждает «публичный → viewer» (editor остаётся editor)."""
    shared = await _make(repo, name="Расшаренный", author="Other", is_public=True)
    await repo.share_agent(shared, "other", ["me"], "editor")
    agents, _ = await repo.get_agents(AgentFilters(), user_id="me", is_admin=False)
    only = [a for a in agents if a.id == shared][0]
    assert only.my_permission == "editor"
    assert only.is_shared_with_me is True


async def test_get_agent_public_for_stranger_is_viewer_private_has_no_role(repo):
    public = await _make(repo, name="Публичный", author="Other")
    private = await _make(repo, name="Приватный", author="Other", is_public=False)
    assert (await repo.get_agent(public, "stranger")).my_permission == "viewer"
    assert (await repo.get_agent(private, "stranger")).my_permission is None


async def test_get_agents_author_filter_without_author_only(repo):
    """author_id без author_only работает как точное сравнение среди публичных."""
    mine = await _make(repo, name="Агент автора", author="alice")
    await _make(repo, name="Агент чужой", author="bob")
    await _make(repo, name="Приватный автора", author="alice", is_public=False)
    agents, total = await repo.get_agents(AgentFilters(author_id="alice"))
    assert [a.id for a in agents] == [mine] and total == 1


async def test_update_agent_changes_every_field(repo):
    aid = await _make(repo, description="старое", config={"a": 1}, tools=["t1"])
    update = AgentUpdate(
        name="Другое имя",
        description="новое описание",
        system_prompt="Новый системный промпт агента.",
        config={"temperature": 0.2},
        tools=["t2", "t3"],
        is_public=False,
    )
    assert await repo.update_agent(aid, update, "OWNER") is True
    agent = await repo.get_agent(aid, "owner")
    assert agent.name == "Другое имя"
    assert agent.description == "новое описание"
    assert agent.system_prompt == "Новый системный промпт агента."
    assert agent.config == {"temperature": 0.2}
    assert agent.tools == ["t2", "t3"]
    assert agent.is_public is False
    assert agent.updated_at >= agent.created_at


async def test_update_agent_tags_reuse_existing_create_new_and_skip_short(repo):
    first = await _make(repo, name="Первый агент", new_tags=["Alpha"])
    second = await _make(repo, name="Второй агент")
    alpha = (await repo.list_tags())[0]["id"]

    update = AgentUpdate(tag_ids=[alpha], new_tags=["alpha", "Beta", "b", "  "])
    assert await repo.update_agent(second, update, "owner")

    names = sorted(t["name"] for t in (await repo.get_agent(second)).tags)
    assert names == ["Alpha", "Beta"]  # «alpha» нашёлся без учёта регистра, «b» и пробелы пропущены
    assert [t["name"] for t in (await repo.get_agent(first)).tags] == ["Alpha"]
    assert sorted(t["name"] for t in await repo.list_tags()) == ["Alpha", "Beta"]


async def test_update_agent_with_empty_tags_clears_and_drops_orphans(repo):
    aid = await _make(repo, new_tags=["Solo"])
    assert await repo.update_agent(aid, AgentUpdate(tag_ids=[]), "owner")
    assert (await repo.get_agent(aid)).tags == []
    assert await repo.list_tags() == []


async def test_invalid_tags_in_catalog_are_skipped_in_card_and_list(repo, pg_connection):
    """Тег, не проходящий модель Tag (цвет не hex), не роняет карточку и список."""
    aid = await _make(repo, new_tags=["Good"])
    async with await pg_connection.acquire() as conn:
        bad = await conn.fetchval(
            "INSERT INTO tags (name, color) VALUES ('Bad', 'red') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO agent_tags (agent_id, tag_id) VALUES ($1, $2)", aid, bad
        )
    agent = await repo.get_agent(aid)
    assert [t["name"] for t in agent.tags] == ["Good"]
    agents, _ = await repo.get_agents(AgentFilters())
    assert [t["name"] for t in agents[0].tags] == ["Good"]


async def test_get_agent_names_map_skips_blank_names(repo):
    named = await _make(repo, name="Видимое имя")
    blank = await _make(repo, name="   ")
    assert await repo.get_agent_names_map([named, blank]) == {named: "Видимое имя"}


async def test_get_agents_private_foreign_agent_via_author_only_has_no_role(repo):
    """Приватная карточка чужого автора: не владелец, не расшарена, не публична -> роли нет."""
    private = await _make(repo, name="Чужой приватный", author="Other", is_public=False)
    agents, _ = await repo.get_agents(
        AgentFilters(author_id="other", author_only=True), user_id="me"
    )
    assert [a.id for a in agents] == [private]
    assert agents[0].my_permission is None
    assert agents[0].is_shared_with_me is False
