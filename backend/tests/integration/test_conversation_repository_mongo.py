"""ConversationRepository на реальной MongoDB: CRUD, изоляция по пользователям, поиск, TTL, проекты, правка сообщений."""

import asyncio
from datetime import datetime, timedelta

import pytest

from backend.database.mongodb.models import Conversation, Message
from backend.database.mongodb.repository import ConversationRepository

pytestmark = pytest.mark.integration

T0 = datetime(2026, 1, 1, 12, 0, 0)


def msg(mid, role="user", content="привет", **kw):
    return Message(message_id=mid, role=role, content=content, timestamp=kw.pop("timestamp", T0), metadata=kw.pop("metadata", {}))


def conv(cid, user="alice", title="Диалог", messages=None, **kw):
    return Conversation(conversation_id=cid, user_id=user, title=title, messages=messages or [], created_at=kw.pop("created_at", T0), updated_at=kw.pop("updated_at", T0), **kw)


@pytest.fixture
async def repo(mongo_connection):
    repository = ConversationRepository(mongo_connection)
    await repository.create_indexes()
    return repository


class TestCreateAndRead:
    async def test_roundtrip(self, repo):
        assert await repo.create_conversation(conv("c1", messages=[msg("m1", content="Привет, мир")], metadata={"model": "qwen"}))

        loaded = await repo.get_conversation("c1")

        assert loaded.user_id == "alice" and loaded.title == "Диалог" and loaded.metadata == {"model": "qwen"}
        assert [(m.message_id, m.content) for m in loaded.messages] == [("m1", "Привет, мир")]

    async def test_missing_conversation(self, repo):
        assert await repo.get_conversation("ghost") is None

    async def test_conversation_id_is_unique(self, repo):
        assert await repo.create_conversation(conv("c1"))

        assert await repo.create_conversation(conv("c1", title="Дубликат")) is None
        assert (await repo.get_conversation("c1")).title == "Диалог"

    async def test_unicode_and_large_content_survive(self, repo):
        text = "Ёжик 🦔 " + "я" * 200_000
        await repo.create_conversation(conv("c1", messages=[msg("m1", content=text)]))

        assert (await repo.get_conversation("c1")).messages[0].content == text


class TestMessages:
    async def test_messages_are_appended_in_order(self, repo):
        await repo.create_conversation(conv("c1"))

        for i in range(3):
            assert await repo.add_message("c1", msg(f"m{i}", content=f"текст {i}"))

        assert [m.message_id for m in (await repo.get_conversation("c1")).messages] == ["m0", "m1", "m2"]

    async def test_concurrent_appends_do_not_lose_messages(self, repo):
        await repo.create_conversation(conv("c1"))

        await asyncio.gather(*[repo.add_message("c1", msg(f"m{i}")) for i in range(25)])

        assert len((await repo.get_conversation("c1")).messages) == 25

    async def test_appending_bumps_updated_at(self, repo):
        await repo.create_conversation(conv("c1"))

        await repo.add_message("c1", msg("m1"))

        assert (await repo.get_conversation("c1")).updated_at > T0

    async def test_appending_to_a_missing_conversation_reports_failure(self, repo):
        # update_one без upsert ничего не находит: метод честно отвечает False.
        assert await repo.add_message("ghost", msg("m1")) is False
        assert await repo.get_conversation("ghost") is None

    async def test_edit_by_message_id(self, repo):
        await repo.create_conversation(conv("c1", messages=[msg("m1", content="старое"), msg("m2", content="другое")]))

        assert await repo.update_message("c1", "m1", "новое") is True

        assert [m.content for m in (await repo.get_conversation("c1")).messages] == ["новое", "другое"]

    async def test_edit_falls_back_to_the_old_content(self, repo):
        await repo.create_conversation(conv("c1", messages=[msg("server-id", content="старое")]))

        assert await repo.update_message("c1", "frontend-id", "новое", old_content="старое") is True
        assert (await repo.get_conversation("c1")).messages[0].content == "новое"

    async def test_edit_of_an_unknown_message(self, repo):
        await repo.create_conversation(conv("c1", messages=[msg("m1")]))

        assert await repo.update_message("c1", "ghost", "x") is False
        assert await repo.update_message("c1", "ghost", "x", old_content="нет такого") is False

    async def test_saving_identical_text_still_counts_as_success(self, repo):
        # updated_at меняется вместе с содержимым, поэтому modified_count > 0 и без реальной правки текста.
        await repo.create_conversation(conv("c1", messages=[msg("m1", content="то же")]))

        assert await repo.update_message("c1", "m1", "то же") is True
        assert (await repo.get_conversation("c1")).updated_at > T0

    async def test_edit_does_not_cross_conversations(self, repo):
        await repo.create_conversation(conv("a", messages=[msg("m1", content="A")]))
        await repo.create_conversation(conv("b", messages=[msg("m1", content="B")]))

        await repo.update_message("a", "m1", "A2")

        assert (await repo.get_conversation("b")).messages[0].content == "B"

    async def test_assistant_message_content_and_metadata(self, repo):
        await repo.create_conversation(conv("c1", messages=[msg("m1", "assistant", "ответ")]))

        assert await repo.update_assistant_message("c1", "m1", content="правка", metadata={"model": "qwen", "tokens": 5}) is True
        assert await repo.update_assistant_message("c1", "m1") is False
        assert await repo.update_assistant_message("c1", "ghost", content="x") is False

        message = (await repo.get_conversation("c1")).messages[0]
        assert message.content == "правка" and message.metadata == {"model": "qwen", "tokens": 5}

    async def test_last_message_removal(self, repo):
        await repo.create_conversation(conv("c1", messages=[msg("m1", "user"), msg("m2", "assistant"), msg("m3", "user")]))

        assert await repo.remove_last_message("c1") is True
        assert [m.message_id for m in (await repo.get_conversation("c1")).messages] == ["m1", "m2"]

        assert await repo.remove_last_message("c1", role="user") is True
        assert [m.message_id for m in (await repo.get_conversation("c1")).messages] == ["m2"]

    async def test_last_message_removal_failures(self, repo):
        await repo.create_conversation(conv("empty"))
        await repo.create_conversation(conv("c1", messages=[msg("m1", "user")]))

        assert await repo.remove_last_message("empty") is False
        assert await repo.remove_last_message("ghost") is False
        assert await repo.remove_last_message("c1", role="assistant") is False

    async def test_role_removal_drops_only_the_last_message_even_with_duplicate_ids(self, repo):
        # Удаление идёт по индексу: повторяющийся id (например, от регенерации) не уносит лишние сообщения.
        await repo.create_conversation(conv("c1", messages=[msg("dup", "assistant", "первый"), msg("m2", "user"), msg("dup", "assistant", "второй")]))

        assert await repo.remove_last_message("c1", role="assistant") is True

        remaining = (await repo.get_conversation("c1")).messages
        assert [(m.message_id, m.content) for m in remaining] == [("dup", "первый"), ("m2", "привет")]


class TestFeedback:
    async def test_feedback_is_stored_and_cleared(self, repo):
        await repo.create_conversation(conv("c1", messages=[msg("m1", "assistant", metadata={"model": "qwen"})]))

        assert await repo.update_message_feedback("c1", "m1", {"rating": "like", "tags": []}) is True
        assert (await repo.get_conversation("c1")).messages[0].metadata == {"model": "qwen", "feedback": {"rating": "like", "tags": []}}

        assert await repo.update_message_feedback("c1", "m1", None) is True
        assert (await repo.get_conversation("c1")).messages[0].metadata == {"model": "qwen"}

    async def test_feedback_per_multi_llm_slot(self, repo):
        slots = [{"model": "a", "content": "1"}, {"model": "b", "content": "2"}]
        await repo.create_conversation(conv("c1", messages=[msg("m1", "assistant", metadata={"multi_llm_responses": slots})]))

        assert await repo.update_message_feedback("c1", "m1", {"rating": "dislike"}, multi_llm_slot_index=1) is True

        stored = (await repo.get_conversation("c1")).messages[0].metadata["multi_llm_responses"]
        assert stored[0] == {"model": "a", "content": "1"} and stored[1]["feedback"] == {"rating": "dislike"}

    @pytest.mark.parametrize("slot", [-1, 2, 99])
    async def test_invalid_slot_indexes(self, repo, slot):
        await repo.create_conversation(conv("c1", messages=[msg("m1", "assistant", metadata={"multi_llm_responses": [{"model": "a"}, {"model": "b"}]})]))

        assert await repo.update_message_feedback("c1", "m1", {"rating": "like"}, multi_llm_slot_index=slot) is False

    async def test_unknown_conversation_or_message(self, repo):
        await repo.create_conversation(conv("c1", messages=[msg("m1")]))

        assert await repo.update_message_feedback("ghost", "m1", {"rating": "like"}) is False
        assert await repo.update_message_feedback("c1", "ghost", {"rating": "like"}) is False

    async def test_feedback_on_different_messages_does_not_interfere(self, repo):
        await repo.create_conversation(conv("c1", messages=[msg("m1", "assistant"), msg("m2", "assistant")]))

        await repo.update_message_feedback("c1", "m1", {"rating": "like"})
        await repo.update_message_feedback("c1", "m2", {"rating": "dislike"})

        messages = (await repo.get_conversation("c1")).messages
        assert [m.metadata["feedback"]["rating"] for m in messages] == ["like", "dislike"]


class TestListsAndSearch:
    async def test_users_see_only_their_own_conversations_newest_first(self, repo):
        await repo.create_conversation(conv("a1", "alice", updated_at=T0))
        await repo.create_conversation(conv("a2", "alice", updated_at=T0 + timedelta(hours=1)))
        await repo.create_conversation(conv("b1", "bob", updated_at=T0 + timedelta(hours=2)))

        assert [c.conversation_id for c in await repo.get_user_conversations("alice")] == ["a2", "a1"]
        assert [c.conversation_id for c in await repo.get_user_conversations("bob")] == ["b1"]
        assert await repo.get_user_conversations("carol") == []

    async def test_paging(self, repo):
        for i in range(5):
            await repo.create_conversation(conv(f"c{i}", updated_at=T0 + timedelta(minutes=i)))

        page = await repo.get_user_conversations("alice", limit=2, skip=1)

        assert [c.conversation_id for c in page] == ["c3", "c2"]

    async def test_full_text_search_respects_the_owner(self, repo):
        await repo.create_conversation(conv("a1", "alice", title="Налоговая отчётность", messages=[msg("m1", content="квартальный отчёт")]))
        await repo.create_conversation(conv("b1", "bob", title="Налоговая проверка"))

        found_all = await repo.search_conversations("налоговая")
        found_alice = await repo.search_conversations("налоговая", user_id="alice")

        assert {c.conversation_id for c in found_all} == {"a1", "b1"}
        assert [c.conversation_id for c in found_alice] == ["a1"]
        assert await repo.search_conversations("квартальный", user_id="bob") == []

    async def test_search_inside_messages(self, repo):
        await repo.create_conversation(conv("a1", messages=[msg("m1", content="договор поставки оборудования")]))

        assert [c.conversation_id for c in await repo.search_conversations("поставки")] == ["a1"]
        assert await repo.search_conversations("несуществующее") == []

    async def test_search_treats_operators_as_text(self, repo):
        await repo.create_conversation(conv("a1", title="обычный диалог"))

        assert await repo.search_conversations('{"$where": "1"}') == []


class TestUpdatesAndDeletion:
    async def test_update_sets_fields_and_bumps_updated_at(self, repo):
        await repo.create_conversation(conv("c1"))

        assert await repo.update_conversation("c1", {"title": "Новое имя", "metadata": {"archived": True}}) is True

        loaded = await repo.get_conversation("c1")
        assert loaded.title == "Новое имя" and loaded.metadata == {"archived": True} and loaded.updated_at > T0

    async def test_update_can_reassign_the_owner_known_issue(self, repo):
        # Репозиторий записывает любые поля «как есть», включая user_id: защита владельца — на слое API.
        await repo.create_conversation(conv("c1", "alice"))

        await repo.update_conversation("c1", {"user_id": "mallory"})

        assert (await repo.get_conversation("c1")).user_id == "mallory"

    async def test_update_does_not_mutate_the_callers_dict(self, repo):
        await repo.create_conversation(conv("c1"))
        updates = {"title": "x"}

        await repo.update_conversation("c1", updates)

        assert updates == {"title": "x"}

    async def test_updating_a_missing_conversation_reports_failure(self, repo):
        assert await repo.update_conversation("ghost", {"title": "x"}) is False

    async def test_delete_one(self, repo):
        await repo.create_conversation(conv("c1"))
        await repo.create_conversation(conv("c2"))

        assert await repo.delete_conversation("c1") is True

        assert await repo.get_conversation("c1") is None and await repo.get_conversation("c2") is not None
        assert await repo.delete_conversation("ghost") is False  # несуществующий диалог не «удаляется»

    async def test_delete_all_of_a_user_only(self, repo):
        for cid, user in (("a1", "alice"), ("a2", "alice"), ("b1", "bob")):
            await repo.create_conversation(conv(cid, user))

        assert await repo.delete_user_conversations("alice") == 2

        assert await repo.get_user_conversations("alice") == [] and len(await repo.get_user_conversations("bob")) == 1
        assert await repo.delete_user_conversations("nobody") == 0

    async def test_ttl_marks_the_expiry_in_the_future(self, repo):
        await repo.create_conversation(conv("c1"))

        assert await repo.set_conversation_ttl("c1", 7) is True

        expires = (await repo.get_conversation("c1")).expires_at
        assert timedelta(days=6, hours=23) < expires - datetime.utcnow() < timedelta(days=7, minutes=1)


class TestProjects:
    async def test_conversations_of_a_project_and_global_ones(self, repo):
        await repo.create_conversation(conv("g1"))
        await repo.create_conversation(conv("p1", project_id="proj-1", updated_at=T0 + timedelta(hours=1)))
        await repo.create_conversation(conv("p2", project_id="proj-1"))
        await repo.create_conversation(conv("q1", project_id="proj-2"))

        assert [c.conversation_id for c in await repo.get_conversations_by_project("proj-1")] == ["p1", "p2"]
        assert [c.conversation_id for c in await repo.get_global_conversations()] == ["g1"]
        assert await repo.get_conversations_by_project("ghost") == []

    async def test_attaching_and_detaching(self, repo):
        await repo.create_conversation(conv("c1"))

        await repo.set_conversation_project("c1", "proj-1")
        assert (await repo.get_conversation("c1")).project_id == "proj-1"

        await repo.set_conversation_project("c1", None)
        assert (await repo.get_conversation("c1")).project_id is None
        assert [c.conversation_id for c in await repo.get_global_conversations()] == ["c1"]

    async def test_deleting_a_project_removes_its_conversations_of_all_users(self, repo):
        await repo.create_conversation(conv("p1", "alice", project_id="proj-1"))
        await repo.create_conversation(conv("p2", "bob", project_id="proj-1"))
        await repo.create_conversation(conv("keep", "alice", project_id="proj-2"))

        assert await repo.delete_conversations_by_project("proj-1") == 2

        assert await repo.get_conversation("p2") is None and await repo.get_conversation("keep") is not None

    async def test_listing_limit(self, repo):
        for i in range(5):
            await repo.create_conversation(conv(f"g{i}", updated_at=T0 + timedelta(minutes=i)))

        assert len(await repo.get_global_conversations(limit=3)) == 3
