"""Дополнительные проверки репозитория и подключения на реальной MongoDB: индексы TTL, правки ассистента, feedback по слотам, health-check."""

from datetime import datetime, timedelta

import pytest

from backend.database.mongodb.models import Conversation, Message
from backend.database.mongodb.repository import ConversationRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def repo(mongo_connection):
    repository = ConversationRepository(mongo_connection)
    await repository.create_indexes()
    return repository


def message(mid, role="assistant", content="text", metadata=None):
    return Message(message_id=mid, role=role, content=content, metadata=metadata or {})


class TestConnection:
    async def test_health_check_and_accessors(self, mongo_connection):
        assert await mongo_connection.health_check() is True
        assert mongo_connection.get_database().name == mongo_connection.database_name
        assert mongo_connection.get_collection("x").name == "x"

    async def test_health_check_without_client_is_false(self):
        from backend.database.mongodb.connection import MongoDBConnection

        connection = MongoDBConnection("mongodb://127.0.0.1:27117", "astra_itest_unused")
        assert await connection.health_check() is False  # клиента ещё нет
        assert connection.db is None


class TestTtlIndex:
    async def test_expiring_conversation_gets_a_ttl_index_on_next_index_creation(self, repo, mongo_connection):
        await repo.create_conversation(Conversation(conversation_id="c1", user_id="u"))
        await repo.set_conversation_ttl("c1", 1)
        stored = await repo.get_conversation("c1")
        assert stored.expires_at > datetime.utcnow() + timedelta(hours=23)
        await repo.create_indexes()
        indexes = await mongo_connection.get_collection("conversations").index_information()
        ttl = [i for i in indexes.values() if i["key"] == [("expires_at", 1)]]
        assert ttl and ttl[0]["expireAfterSeconds"] == 0

    async def test_no_ttl_index_when_nothing_expires(self, repo, mongo_connection):
        await repo.create_conversation(Conversation(conversation_id="c1", user_id="u"))
        indexes = await mongo_connection.get_collection("conversations").index_information()
        assert not [i for i in indexes.values() if i["key"] == [("expires_at", 1)]]


class TestAssistantMessageUpdate:
    async def test_content_and_metadata_are_replaced(self, repo):
        await repo.create_conversation(
            Conversation(conversation_id="c1", messages=[message("a1", content="old", metadata={"k": 1})])
        )
        assert await repo.update_assistant_message("c1", "a1", content="new", metadata={"k": 2}) is True
        stored = (await repo.get_conversation("c1")).messages[0]
        assert (stored.content, stored.metadata) == ("new", {"k": 2})

    async def test_metadata_only_keeps_content(self, repo):
        await repo.create_conversation(Conversation(conversation_id="c1", messages=[message("a1", content="same")]))
        assert await repo.update_assistant_message("c1", "a1", metadata={"x": True}) is True
        stored = (await repo.get_conversation("c1")).messages[0]
        assert (stored.content, stored.metadata) == ("same", {"x": True})

    async def test_unknown_message_or_nothing_to_change(self, repo):
        await repo.create_conversation(Conversation(conversation_id="c1", messages=[message("a1")]))
        assert await repo.update_assistant_message("c1", "zzz", content="x") is False
        assert await repo.update_assistant_message("c1", "a1") is False


class TestSlotFeedback:
    async def test_slot_feedback_can_be_set_and_cleared(self, repo):
        slots = [{"text": "s0"}, {"text": "s1"}]
        await repo.create_conversation(
            Conversation(conversation_id="c1", messages=[message("a1", metadata={"multi_llm_responses": slots})])
        )
        assert await repo.update_message_feedback("c1", "a1", {"v": 1}, multi_llm_slot_index=1) is True
        meta = (await repo.get_conversation("c1")).messages[0].metadata
        assert meta["multi_llm_responses"][1]["feedback"] == {"v": 1} and "feedback" not in meta["multi_llm_responses"][0]
        assert await repo.update_message_feedback("c1", "a1", None, multi_llm_slot_index=1) is True
        meta = (await repo.get_conversation("c1")).messages[0].metadata
        assert meta["multi_llm_responses"] == slots
