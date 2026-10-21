"""ConversationRepository без реальной MongoDB: контракт возвращаемых значений и форма update-запросов."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.database.mongodb.models import Conversation, Message
from backend.database.mongodb.repository import ConversationRepository

pytestmark = pytest.mark.unit

T0 = datetime(2026, 1, 1, 12, 0, 0)


def _msg(mid, role="user"):
    return Message(message_id=mid, role=role, content="x", timestamp=T0, metadata={})


def _repo(collection):
    connection = MagicMock()
    connection.get_collection.return_value = collection
    return ConversationRepository(connection)


def _collection(matched=1, deleted=1):
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=SimpleNamespace(matched_count=matched, modified_count=matched))
    collection.delete_one = AsyncMock(return_value=SimpleNamespace(deleted_count=deleted))
    return collection


def test_add_message_to_missing_conversation_returns_false():
    repo = _repo(_collection(matched=0))
    assert asyncio.run(repo.add_message("ghost", _msg("m1"))) is False


def test_add_message_to_existing_conversation_returns_true():
    repo = _repo(_collection(matched=1))
    assert asyncio.run(repo.add_message("c1", _msg("m1"))) is True


def test_update_conversation_missing_returns_false():
    repo = _repo(_collection(matched=0))
    assert asyncio.run(repo.update_conversation("ghost", {"title": "x"})) is False


def test_update_conversation_does_not_mutate_callers_dict():
    collection = _collection()
    repo = _repo(collection)
    updates = {"title": "x"}

    assert asyncio.run(repo.update_conversation("c1", updates)) is True

    assert updates == {"title": "x"}
    sent = collection.update_one.await_args.args[1]["$set"]
    assert sent["title"] == "x" and "updated_at" in sent


def test_delete_missing_conversation_returns_false():
    repo = _repo(_collection(deleted=0))
    assert asyncio.run(repo.delete_conversation("ghost")) is False


def test_delete_existing_conversation_returns_true():
    repo = _repo(_collection(deleted=1))
    assert asyncio.run(repo.delete_conversation("c1")) is True


def test_set_project_on_missing_conversation_returns_false():
    repo = _repo(_collection(matched=0))
    assert asyncio.run(repo.set_conversation_project("ghost", "p")) is False


def test_remove_last_by_role_removes_by_index_not_by_message_id():
    collection = _collection()
    repo = _repo(collection)
    conversation = Conversation(
        conversation_id="c1",
        user_id="alice",
        title="t",
        messages=[_msg("dup", "assistant"), _msg("m2", "user"), _msg("dup", "assistant")],
        created_at=T0,
        updated_at=T0,
    )
    repo.get_conversation = AsyncMock(return_value=conversation)

    assert asyncio.run(repo.remove_last_message("c1", role="assistant")) is True

    flt, update = collection.update_one.await_args.args[:2]
    assert flt == {"conversation_id": "c1", "messages.2.message_id": "dup"}
    assert isinstance(update, list), "ожидался pipeline-апдейт, а не $pull по message_id"
    assert "$pull" not in str(update)


def test_remove_last_by_role_reports_failure_when_conversation_changed_concurrently():
    repo = _repo(_collection(matched=0))
    conversation = Conversation(
        conversation_id="c1", user_id="alice", title="t", messages=[_msg("m1", "assistant")], created_at=T0, updated_at=T0
    )
    repo.get_conversation = AsyncMock(return_value=conversation)

    assert asyncio.run(repo.remove_last_message("c1", role="assistant")) is False
