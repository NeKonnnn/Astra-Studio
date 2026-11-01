"""ConversationRepository: ветки ошибок и редкие пути — на фейковой коллекции, бросающей исключения."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.database.mongodb.models import Conversation, Message
from backend.database.mongodb.repository import ConversationRepository
from backend.settings.cef_logger import storage_audit

BOOM = RuntimeError("db down")


def make_collection(**overrides):
    coll = MagicMock()
    coll.insert_one = AsyncMock(return_value=SimpleNamespace(inserted_id="oid"))
    coll.find_one = AsyncMock(return_value=None)
    coll.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1, matched_count=1))
    coll.delete_one = AsyncMock()
    coll.delete_many = AsyncMock(return_value=SimpleNamespace(deleted_count=2))
    coll.create_index = AsyncMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])
    coll.find.return_value = cursor
    coll.cursor = cursor
    for name, value in overrides.items():
        setattr(coll, name, value)
    return coll


@pytest.fixture
def audit(monkeypatch):
    events: list = []
    monkeypatch.setattr(
        storage_audit, "log_mongo_success", lambda method, **kw: events.append(("ok", method, kw))
    )
    monkeypatch.setattr(
        storage_audit, "log_mongo_failure", lambda method, err, **kw: events.append(("fail", method, err, kw))
    )
    return events


def repo_with(coll):
    conn = MagicMock()
    conn.get_collection.return_value = coll
    return ConversationRepository(conn)


def conv_doc(cid="c1", messages=None, **extra):
    doc = {
        "_id": "x",
        "conversation_id": cid,
        "user_id": "u",
        "messages": messages
        if messages is not None
        else [
            {"message_id": "m1", "role": "user", "content": "q", "timestamp": datetime(2024, 1, 1)},
            {
                "message_id": "m2",
                "role": "assistant",
                "content": "a",
                "timestamp": datetime(2024, 1, 2),
                "metadata": {"multi_llm_responses": [{"text": "s0"}], "feedback": {"v": 1}},
            },
        ],
    }
    doc.update(extra)
    return doc


class TestIndexes:
    async def test_ttl_index_is_created_only_when_some_conversation_expires(self):
        coll = make_collection(find_one=AsyncMock(return_value={"expires_at": 1}))
        await repo_with(coll).create_indexes()
        ttl = [c for c in coll.create_index.await_args_list if c.args[0] == "expires_at"]
        assert len(ttl) == 1 and ttl[0].kwargs == {"expireAfterSeconds": 0}

    async def test_no_ttl_index_without_expiring_conversations(self):
        coll = make_collection()
        await repo_with(coll).create_indexes()
        assert all(c.args[0] != "expires_at" for c in coll.create_index.await_args_list)
        assert coll.create_index.await_count == 5


class TestWriteErrors:
    """Любая ошибка БД превращается в безопасное значение и событие аудита; исключение наружу не уходит."""

    async def test_create_conversation_success_and_failure(self, audit):
        coll = make_collection()
        repo = repo_with(coll)
        assert await repo.create_conversation(Conversation(conversation_id="c1")) == "oid"
        assert audit[-1][:2] == ("ok", "MongoDB.insertOne(conversations)")
        coll.insert_one.side_effect = BOOM
        assert await repo.create_conversation(Conversation(conversation_id="c2")) is None
        assert audit[-1][0] == "fail" and audit[-1][3] == {"request_uuid": "c2"} and "db down" in audit[-1][2]

    async def test_get_conversation_error_gives_none(self):
        coll = make_collection(find_one=AsyncMock(side_effect=BOOM))
        assert await repo_with(coll).get_conversation("c1") is None

    async def test_add_message_failure(self, audit):
        coll = make_collection(update_one=AsyncMock(side_effect=BOOM))
        msg = Message(message_id="m", role="user", content="x")
        assert await repo_with(coll).add_message("c1", msg) is False
        assert audit[-1][:2] == ("fail", "MongoDB.updateOne(conversations.$push)")

    async def test_update_message_failure(self, audit):
        coll = make_collection(update_one=AsyncMock(side_effect=BOOM))
        assert await repo_with(coll).update_message("c1", "m", "new") is False
        assert audit[-1][:2] == ("fail", "MongoDB.updateOne(conversations.messages)")

    async def test_update_message_by_old_content_when_id_is_not_found(self, audit):
        coll = make_collection(
            update_one=AsyncMock(
                side_effect=[SimpleNamespace(modified_count=0), SimpleNamespace(modified_count=1)]
            )
        )
        assert await repo_with(coll).update_message("c1", "front-id", "new", old_content="old") is True
        second_filter = coll.update_one.await_args_list[1].args[0]
        assert second_filter == {"conversation_id": "c1", "messages.content": "old"}
        assert audit[-1][0] == "ok"

    async def test_update_message_old_content_not_found_either(self):
        coll = make_collection(update_one=AsyncMock(return_value=SimpleNamespace(modified_count=0)))
        assert await repo_with(coll).update_message("c1", "m", "new", old_content="old") is False
        assert coll.update_one.await_count == 2

    async def test_update_conversation_failure(self, audit):
        coll = make_collection(update_one=AsyncMock(side_effect=BOOM))
        assert await repo_with(coll).update_conversation("c1", {"title": "t"}) is False
        assert audit[-1][:2] == ("fail", "MongoDB.updateOne(conversations)")

    async def test_update_conversation_skips_owner_and_project(self):
        coll = make_collection()
        updates = {"title": "t", "user_id": "mallory", "project_id": "p"}
        assert await repo_with(coll).update_conversation("c1", updates) is True
        sets = coll.update_one.await_args.args[1]["$set"]
        assert set(sets) == {"title", "updated_at"} and updates["user_id"] == "mallory"

    async def test_delete_conversation_failure(self, audit):
        coll = make_collection(delete_one=AsyncMock(side_effect=BOOM))
        assert await repo_with(coll).delete_conversation("c1") is False
        assert audit[-1][:2] == ("fail", "MongoDB.deleteOne(conversations)")

    async def test_delete_user_conversations_success_and_failure(self, audit):
        coll = make_collection()
        repo = repo_with(coll)
        assert await repo.delete_user_conversations("u") == 2
        assert audit[-1][2] == {"request_uuid": "u:2"}
        coll.delete_many.side_effect = BOOM
        assert await repo.delete_user_conversations("u") == 0
        assert audit[-1][0] == "fail" and audit[-1][3] == {"request_uuid": "u"}

    async def test_delete_by_project_failure(self, audit):
        coll = make_collection(delete_many=AsyncMock(side_effect=BOOM))
        assert await repo_with(coll).delete_conversations_by_project("p1") == 0
        assert audit[-1][0] == "fail" and "project_id=p1" in audit[-1][1]

    async def test_set_project_failure(self, audit):
        coll = make_collection(update_one=AsyncMock(side_effect=BOOM))
        assert await repo_with(coll).set_conversation_project("c1", "p") is False
        assert audit[-1][:2] == ("fail", "MongoDB.updateOne(conversations.project_id)")

    async def test_ttl_with_unrepresentable_period_returns_false(self):
        """days больше предела timedelta — OverflowError ловится и даёт False, запись в БД не делается."""
        coll = make_collection()
        assert await repo_with(coll).set_conversation_ttl("c1", 10**12) is False
        coll.update_one.assert_not_awaited()

    async def test_ttl_failure_in_update_gives_false(self):
        coll = make_collection(update_one=AsyncMock(side_effect=BOOM))
        assert await repo_with(coll).set_conversation_ttl("c1", 3) is False


class TestReadErrors:
    @pytest.mark.parametrize(
        "call",
        [
            lambda r: r.get_user_conversations("u"),
            lambda r: r.search_conversations("q", "u"),
            lambda r: r.get_conversations_by_project("p"),
            lambda r: r.get_global_conversations(),
        ],
    )
    async def test_list_queries_return_empty_list_on_error(self, call):
        coll = make_collection()
        coll.cursor.to_list.side_effect = BOOM
        assert await call(repo_with(coll)) == []

    @pytest.mark.parametrize(
        "call",
        [
            lambda r: r.get_user_conversations("u"),
            lambda r: r.search_conversations("q"),
            lambda r: r.get_conversations_by_project("p"),
            lambda r: r.get_global_conversations(),
        ],
    )
    async def test_list_queries_strip_object_ids(self, call):
        coll = make_collection()
        coll.cursor.to_list.return_value = [conv_doc("a"), conv_doc("b")]
        result = await call(repo_with(coll))
        assert [c.conversation_id for c in result] == ["a", "b"]


class TestFeedbackBranches:
    async def test_slot_feedback_is_removed_when_cleared(self):
        coll = make_collection(find_one=AsyncMock(return_value=conv_doc()))
        ok = await repo_with(coll).update_message_feedback(
            "c1", "m2", None, multi_llm_slot_index=0
        )
        assert ok is True
        flt, update = coll.update_one.await_args.args[:2]
        assert update["$unset"] == {"messages.$[m].metadata.multi_llm_responses.0.feedback": ""}
        assert flt["messages"]["$elemMatch"] == {
            "message_id": "m2",
            "metadata.multi_llm_responses.0": {"$exists": True},
        }
        assert coll.update_one.await_args.kwargs["array_filters"] == [{"m.message_id": "m2"}]

    async def test_slot_feedback_is_set(self):
        coll = make_collection(find_one=AsyncMock(return_value=conv_doc()))
        await repo_with(coll).update_message_feedback("c1", "m2", {"v": -1}, multi_llm_slot_index=0)
        sets = coll.update_one.await_args.args[1]["$set"]
        assert sets["messages.$[m].metadata.multi_llm_responses.0.feedback"] == {"v": -1}

    async def test_slot_feedback_camel_case_key(self):
        messages = [{"message_id": "m1", "role": "assistant", "content": "a", "timestamp": datetime(2024, 1, 1),
                     "metadata": {"multiLLMResponses": [{"t": 1}]}}]
        coll = make_collection(find_one=AsyncMock(return_value=conv_doc(messages=messages)))
        await repo_with(coll).update_message_feedback("c1", "m1", {"v": 1}, multi_llm_slot_index=0)
        sets = coll.update_one.await_args.args[1]["$set"]
        assert "messages.$[m].metadata.multiLLMResponses.0.feedback" in sets

    @pytest.mark.parametrize("slot", [-1, 1])
    async def test_invalid_slot_does_not_write(self, slot):
        coll = make_collection(find_one=AsyncMock(return_value=conv_doc()))
        assert await repo_with(coll).update_message_feedback("c1", "m2", {"v": 1}, multi_llm_slot_index=slot) is False
        coll.update_one.assert_not_awaited()

    async def test_slot_feedback_unknown_message_does_not_write(self):
        coll = make_collection(find_one=AsyncMock(return_value=conv_doc()))
        assert await repo_with(coll).update_message_feedback("c1", "ghost", {"v": 1}, multi_llm_slot_index=0) is False
        coll.update_one.assert_not_awaited()

    async def test_message_level_feedback_cleared(self):
        coll = make_collection()
        await repo_with(coll).update_message_feedback("c1", "m2", None)
        update = coll.update_one.await_args.args[1]
        assert update["$unset"] == {"messages.$[m].metadata.feedback": ""}
        coll.find_one.assert_not_awaited()

    async def test_message_level_feedback_is_one_atomic_update(self):
        coll = make_collection()
        await repo_with(coll).update_message_feedback("c1", "m2", {"v": 2})
        update = coll.update_one.await_args.args[1]
        assert update["$set"]["messages.$[m].metadata.feedback"] == {"v": 2} and "$unset" not in update

    async def test_conversation_vanished_between_read_and_write(self, audit):
        coll = make_collection(
            find_one=AsyncMock(return_value=conv_doc()),
            update_one=AsyncMock(return_value=SimpleNamespace(matched_count=0)),
        )
        assert await repo_with(coll).update_message_feedback("c1", "m2", {"v": 1}) is False
        assert audit == []

    async def test_write_error_is_audited(self, audit):
        coll = make_collection(
            find_one=AsyncMock(return_value=conv_doc()), update_one=AsyncMock(side_effect=BOOM)
        )
        assert await repo_with(coll).update_message_feedback("c1", "m2", {"v": 1}) is False
        assert audit[-1][:2] == ("fail", "MongoDB.updateOne(conversations.messages.feedback)")

    async def test_success_is_audited(self, audit):
        coll = make_collection(find_one=AsyncMock(return_value=conv_doc()))
        assert await repo_with(coll).update_message_feedback("c1", "m1", {"v": 1}) is True
        assert audit[-1][:2] == ("ok", "MongoDB.updateOne(conversations.messages.feedback)")


class TestAssistantMessageUpdate:
    async def test_nothing_to_update(self):
        coll = make_collection()
        assert await repo_with(coll).update_assistant_message("c1", "m") is False
        coll.update_one.assert_not_awaited()

    async def test_only_metadata(self):
        coll = make_collection()
        assert await repo_with(coll).update_assistant_message("c1", "m", metadata={"k": 1}) is True
        sets = coll.update_one.await_args.args[1]["$set"]
        assert sets["messages.$.metadata"] == {"k": 1} and "messages.$.content" not in sets

    async def test_only_content(self):
        coll = make_collection()
        await repo_with(coll).update_assistant_message("c1", "m", content="x")
        sets = coll.update_one.await_args.args[1]["$set"]
        assert sets["messages.$.content"] == "x" and "messages.$.metadata" not in sets

    async def test_unchanged_message_gives_false(self):
        coll = make_collection(update_one=AsyncMock(return_value=SimpleNamespace(modified_count=0)))
        assert await repo_with(coll).update_assistant_message("c1", "m", content="x") is False

    async def test_error_gives_false(self):
        coll = make_collection(update_one=AsyncMock(side_effect=BOOM))
        assert await repo_with(coll).update_assistant_message("c1", "m", content="x") is False


class TestRemoveLastMessage:
    async def test_error_is_audited(self, audit):
        coll = make_collection(find_one=AsyncMock(side_effect=[conv_doc()]), update_one=AsyncMock(side_effect=BOOM))
        assert await repo_with(coll).remove_last_message("c1", role="user") is False
        assert audit[-1][:2] == ("fail", "MongoDB.updateOne(conversations.messages)")

    async def test_pop_variant_without_role(self, audit):
        coll = make_collection(find_one=AsyncMock(return_value=conv_doc()))
        assert await repo_with(coll).remove_last_message("c1") is True
        assert coll.update_one.await_args.args[1]["$pop"] == {"messages": 1}
        assert audit[-1][1] == "MongoDB.updateOne(conversations.$pop)"

    async def test_pull_variant_with_role(self, audit):
        coll = make_collection(find_one=AsyncMock(return_value=conv_doc()))
        assert await repo_with(coll).remove_last_message("c1", role="assistant") is True
        flt, pipeline = coll.update_one.await_args.args[:2]
        # удаляется ровно один элемент по индексу (а не $pull по message_id - он уносил дубликаты)
        assert flt == {"conversation_id": "c1", "messages.1.message_id": "m2"}
        concat = pipeline[0]["$set"]["messages"]["$concatArrays"]
        assert concat[0] == {"$slice": ["$messages", 1]}
        assert concat[1]["$slice"][1] == 2
        assert audit[-1][1] == "MongoDB.updateOne(conversations.messages.$slice)"

    async def test_concurrent_edit_is_not_reported_as_removed(self, audit):
        coll = make_collection(find_one=AsyncMock(return_value=conv_doc()))
        coll.update_one = AsyncMock(return_value=MagicMock(matched_count=0))
        assert await repo_with(coll).remove_last_message("c1", role="assistant") is False

    async def test_unknown_role_and_empty_conversation(self):
        coll = make_collection(find_one=AsyncMock(return_value=conv_doc()))
        assert await repo_with(coll).remove_last_message("c1", role="system") is False
        coll = make_collection(find_one=AsyncMock(return_value=conv_doc(messages=[])))
        assert await repo_with(coll).remove_last_message("c1") is False
