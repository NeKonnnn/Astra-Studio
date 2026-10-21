"""database/memory_service на реальной MongoDB: сохранение и чтение истории, regenerate, память проектов, изоляция."""

import asyncio

import pytest

from backend.database import memory_service as ms
from backend.database.mongodb.repository import ConversationRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def repo(mongo_connection, monkeypatch):
    repository = ConversationRepository(mongo_connection)
    await repository.create_indexes()
    monkeypatch.setattr(ms, "get_conversation_repository", lambda: repository)
    monkeypatch.setattr(ms, "conversation_repo", None)
    ms.reset_conversation()
    yield repository
    ms.reset_conversation()


async def texts(cid):
    return [(m["role"], m["content"]) for m in await ms.load_dialog_history_mongodb(cid)]


class TestSaveAndLoad:
    async def test_first_message_creates_the_conversation(self, repo):
        await ms.save_dialog_entry("user", "Привет, мир", conversation_id="c1", user_id="alice")
        conv = await repo.get_conversation("c1")
        assert conv.user_id == "alice" and conv.title == "Привет, мир" and len(conv.messages) == 1

    async def test_following_messages_are_appended_in_order(self, repo):
        for role, text in (("user", "q1"), ("assistant", "a1"), ("user", "q2")):
            await ms.save_dialog_entry(role, text, conversation_id="c1", user_id="alice")
        assert await texts("c1") == [("user", "q1"), ("assistant", "a1"), ("user", "q2")]

    async def test_title_is_cut_to_sixty_characters(self, repo):
        await ms.save_dialog_entry("user", "я" * 200, conversation_id="c1")
        assert len((await repo.get_conversation("c1")).title) == 60

    async def test_default_owner_and_generated_ids(self, repo):
        await ms.save_dialog_entry("user", "text", conversation_id="c1")
        conv = await repo.get_conversation("c1")
        assert conv.user_id == "default_user" and conv.messages[0].message_id.startswith("msg_")

    async def test_current_conversation_is_used_when_id_is_omitted(self, repo):
        ms.set_current_conversation_id("ctx-conv")
        await ms.save_dialog_entry("user", "в текущий диалог")
        assert await texts("ctx-conv") == [("user", "в текущий диалог")]
        assert ms.get_or_create_conversation_id() == "ctx-conv"

    async def test_new_id_is_generated_once_per_context(self, repo):
        first = ms.get_or_create_conversation_id()
        assert first.startswith("conv_") and ms.get_or_create_conversation_id() == first
        ms.reset_conversation()
        assert ms.get_or_create_conversation_id() != first

    async def test_conversation_id_is_isolated_between_tasks(self, repo):
        seen = {}

        async def worker(name):
            ms.set_current_conversation_id(name)
            await asyncio.sleep(0.01)
            seen[name] = ms.get_or_create_conversation_id()

        await asyncio.gather(*(asyncio.create_task(worker(f"u{i}")) for i in range(10)))
        assert seen == {f"u{i}": f"u{i}" for i in range(10)}

    async def test_metadata_is_stored_and_attachments_are_merged_into_history(self, repo):
        await ms.save_dialog_entry(
            "user", "О чём файл?", {"inline_context": "Содержимое файла"}, conversation_id="c1", user_id="alice"
        )
        history = await ms.load_dialog_history_mongodb("c1")
        assert history[0]["content"] == "[Прикреплённый документ]\nСодержимое файла\n\n[Вопрос пользователя]\nО чём файл?"
        stored = (await repo.get_conversation("c1")).messages[0]
        assert stored.content == "О чём файл?" and stored.metadata["inline_context"] == "Содержимое файла"

    async def test_reasoning_is_removed_from_history_for_the_model(self, repo):
        await ms.save_dialog_entry("assistant", "<think>мысли</think>Ответ", conversation_id="c1")
        assert await texts("c1") == [("assistant", "Ответ")]

    async def test_unknown_conversation_has_empty_history(self, repo):
        assert await ms.load_dialog_history_mongodb("ghost") == []

    async def test_recent_history_returns_the_tail(self, repo):
        for i in range(6):
            await ms.save_dialog_entry("user", f"m{i}", conversation_id="c1")
        assert [m["content"] for m in await ms.get_recent_dialog_history(3, "c1")] == ["m3", "m4", "m5"]
        assert len(await ms.get_recent_dialog_history(None, "c1")) == 6
        assert len(await ms.get_recent_dialog_history(100, "c1")) == 6

    async def test_history_uses_the_current_conversation_by_default(self, repo):
        ms.set_current_conversation_id("cur")
        await ms.save_dialog_entry("user", "x")
        assert [m["content"] for m in await ms.load_dialog_history()] == ["x"]
        assert [m["content"] for m in await ms.get_recent_dialog_history(5)] == ["x"]

    async def test_save_to_memory_maps_russian_and_english_roles(self, repo):
        ms.set_current_conversation_id("legacy")
        await ms.save_to_memory("Пользователь", "вопрос")
        await ms.save_to_memory("Агент", "ответ")
        await ms.save_to_memory("Система", "служебное")
        await ms.save_to_memory("USER", "ещё")
        assert [r for r, _ in await texts("legacy")] == ["user", "assistant", "system", "user"]

    async def test_clear_deletes_the_current_conversation_and_starts_a_new_one(self, repo):
        ms.set_current_conversation_id("to-clear")
        await ms.save_dialog_entry("user", "x")
        assert await ms.clear_dialog_history() == "История диалога очищена"
        assert await repo.get_conversation("to-clear") is None
        assert ms.get_or_create_conversation_id() != "to-clear"

    async def test_remove_last_user_message(self, repo):
        await ms.save_dialog_entry("user", "q1", conversation_id="c1")
        await ms.save_dialog_entry("assistant", "a1", conversation_id="c1")
        await ms.save_dialog_entry("user", "q2", conversation_id="c1")
        assert await ms.remove_last_user_message("c1") is True
        assert await texts("c1") == [("user", "q1"), ("assistant", "a1")]

    async def test_concurrent_saves_lose_no_messages(self, repo):
        await ms.save_dialog_entry("user", "first", conversation_id="c1")
        await asyncio.gather(*(ms.save_dialog_entry("user", f"m{i}", conversation_id="c1") for i in range(20)))
        assert len(await texts("c1")) == 21


class TestOwnership:
    async def test_foreign_conversation_is_rejected(self, repo):
        await ms.save_dialog_entry("user", "секрет", conversation_id="c1", user_id="alice")
        assert await ms.verify_conversation_owner("c1", "alice") is True
        assert await ms.verify_conversation_owner("c1", "mallory") is False

    async def test_new_missing_or_legacy_conversations_are_allowed(self, repo):
        assert await ms.verify_conversation_owner("new", "bob") is True
        assert await ms.verify_conversation_owner(None, "bob") is True
        assert await ms.verify_conversation_owner("c", None) is True
        await ms.save_dialog_entry("user", "x", conversation_id="legacy")
        assert await ms.verify_conversation_owner("legacy", "anyone") is True

    async def test_verification_fails_open_when_the_database_breaks(self, repo, monkeypatch):
        async def broken(_):
            raise RuntimeError("mongo down")

        monkeypatch.setattr(repo, "get_conversation", broken)
        assert await ms.verify_conversation_owner("c1", "alice") is True

    async def test_save_does_not_check_ownership_itself_known_issue(self, repo):
        """Известная особенность: ``save_dialog_entry`` допишет сообщение в ЧУЖОЙ диалог, если вызывающий
        код не вызвал ``verify_conversation_owner`` заранее — защита целиком на вызывающей стороне."""
        await ms.save_dialog_entry("user", "alice", conversation_id="c1", user_id="alice")
        await ms.save_dialog_entry("user", "mallory пишет в чужой диалог", conversation_id="c1", user_id="mallory")
        conv = await repo.get_conversation("c1")
        assert conv.user_id == "alice" and len(conv.messages) == 2


class TestAssistantResponses:
    async def test_plain_answer_is_saved(self, repo):
        await ms.save_dialog_entry("user", "q", conversation_id="c1", user_id="alice")
        mid = await ms.save_assistant_response(
            "ответ", {"model": "m"}, conversation_id="c1", user_id="alice", message_id="a-1"
        )
        assert mid == "a-1"
        last = (await repo.get_conversation("c1")).messages[-1]
        assert (last.role, last.content, last.metadata) == ("assistant", "ответ", {"model": "m"})

    async def test_regenerate_updates_the_same_message_and_keeps_alternatives(self, repo):
        await ms.save_dialog_entry("user", "q", conversation_id="c1", user_id="alice")
        await ms.save_dialog_entry("assistant", "первый ответ", conversation_id="c1", message_id="a-1")

        mid = await ms.save_assistant_response(
            "второй ответ", conversation_id="c1", regenerate=True, assistant_message_id="a-1"
        )

        assert mid == "a-1"
        conv = await repo.get_conversation("c1")
        assert len(conv.messages) == 2
        answer = conv.messages[-1]
        assert answer.content == "второй ответ"
        assert answer.metadata["alternative_responses"] == ["первый ответ", "второй ответ"]
        assert answer.metadata["current_response_index"] == 1

    async def test_regenerate_without_known_id_updates_the_last_assistant_reply(self, repo):
        await ms.save_dialog_entry("assistant", "старый", conversation_id="c1", message_id="a-1")
        await ms.save_dialog_entry("user", "q2", conversation_id="c1")
        mid = await ms.save_assistant_response(
            "новый", conversation_id="c1", regenerate=True, assistant_message_id="front-local-id"
        )
        assert mid == "a-1" and (await repo.get_conversation("c1")).messages[0].content == "новый"

    async def test_repeated_regeneration_extends_the_alternatives(self, repo):
        await ms.save_dialog_entry("assistant", "v1", conversation_id="c1", message_id="a-1")
        await ms.save_assistant_response("v2", conversation_id="c1", regenerate=True, assistant_message_id="a-1")
        await ms.save_assistant_response("v3", conversation_id="c1", regenerate=True, assistant_message_id="a-1")
        meta = (await repo.get_conversation("c1")).messages[0].metadata
        assert meta["alternative_responses"][-1] == "v3"
        assert meta["current_response_index"] == len(meta["alternative_responses"]) - 1

    async def test_regenerate_in_a_missing_conversation_saves_a_new_message(self, repo):
        mid = await ms.save_assistant_response("ответ", conversation_id="ghost", regenerate=True, message_id="m-1")
        assert mid == "m-1" and (await repo.get_conversation("ghost")) is not None

    async def test_explicit_alternatives_and_index_are_respected(self, repo):
        await ms.save_dialog_entry("assistant", "b", conversation_id="c1", message_id="a-1")
        await ms.save_assistant_response(
            "c",
            conversation_id="c1",
            regenerate=True,
            assistant_message_id="a-1",
            alternative_responses=["a", "b"],
            current_response_index=1,
        )
        meta = (await repo.get_conversation("c1")).messages[0].metadata
        assert meta["alternative_responses"] == ["a", "c"] and meta["current_response_index"] == 1

    async def test_project_answers_are_bound_to_the_project(self, repo):
        mid = await ms.save_assistant_response(
            "ответ", conversation_id="c1", project_id="p1", user_id="alice", message_id="a-1"
        )
        assert mid == "a-1" and (await repo.get_conversation("c1")).project_id == "p1"


class TestProjectMemory:
    async def test_messages_are_bound_to_the_project_and_move_with_it(self, repo):
        await ms.save_dialog_entry_to_project("user", "в проект", "p1", "c1", user_id="alice")
        assert (await repo.get_conversation("c1")).project_id == "p1"
        await ms.save_dialog_entry_to_project("assistant", "ответ", "p2", "c1")
        conv = await repo.get_conversation("c1")
        assert conv.project_id == "p2" and len(conv.messages) == 2

    async def test_project_and_default_memory_do_not_mix(self, repo):
        await ms.save_dialog_entry_to_project("user", "проектное", "p1", "c1", user_id="alice")
        await ms.save_dialog_entry_to_project("user", "другой проект", "p2", "c2", user_id="alice")
        await ms.save_dialog_entry("user", "глобальное", conversation_id="c3", user_id="alice")
        assert [m["content"] for m in await ms.get_project_memory_history("p1")] == ["проектное"]
        assert [m["content"] for m in await ms.get_default_memory_history()] == ["глобальное"]

    async def test_history_limits(self, repo):
        for i in range(5):
            await ms.save_dialog_entry_to_project("user", f"m{i}", "p1", "c1")
        assert [m["content"] for m in await ms.get_project_memory_history("p1", max_entries=2)] == ["m3", "m4"]
        for i in range(5):
            await ms.save_dialog_entry("user", f"g{i}", conversation_id="g1")
        assert len(await ms.get_default_memory_history(max_entries=2)) == 2

    async def test_deleting_a_project_removes_only_its_conversations(self, repo):
        await ms.save_dialog_entry_to_project("user", "a", "p1", "c1")
        await ms.save_dialog_entry_to_project("user", "b", "p1", "c2")
        await ms.save_dialog_entry_to_project("user", "c", "p2", "c3")
        assert await ms.delete_project_memory("p1") == 2
        assert await repo.get_conversation("c1") is None and await repo.get_conversation("c3") is not None
        assert await ms.delete_project_memory("never") == 0


class TestSearch:
    async def test_search_returns_summaries_for_the_owner_only(self, repo):
        await ms.save_dialog_entry("user", "договор поставки и штрафы", conversation_id="c1", user_id="alice")
        await ms.save_dialog_entry("assistant", "ответ про штрафы", conversation_id="c1", user_id="alice")
        await ms.save_dialog_entry("user", "штрафы у соседа", conversation_id="c2", user_id="bob")
        found = await ms.search_conversations("штрафы", user_id="alice")
        assert [f["conversation_id"] for f in found] == ["c1"]
        assert found[0]["message_count"] == 2 and found[0]["title"] and found[0]["created_at"]
        assert await ms.search_conversations("несуществующее", user_id="alice") == []


class TestAvailability:
    async def test_everything_degrades_gracefully_without_a_repository(self, monkeypatch):
        def not_ready():
            raise RuntimeError("Mongo not initialised")

        monkeypatch.setattr(ms, "get_conversation_repository", not_ready)
        monkeypatch.setattr(ms, "conversation_repo", None)
        assert ms._check_mongodb_available() is False
        assert await ms.save_dialog_entry_mongodb("user", "x") is False
        with pytest.raises(RuntimeError, match="MongoDB недоступен"):
            await ms.save_dialog_entry("user", "x")
        assert await ms.load_dialog_history() == [] and await ms.load_dialog_history_mongodb("c") == []
        assert await ms.get_recent_dialog_history(3, "c") == []
        assert "Ошибка" in await ms.clear_dialog_history()
        assert await ms.search_conversations("q") == []
        assert await ms.remove_last_user_message("c") is False
        assert await ms.save_dialog_entry_to_project("user", "x", "p") is False
        assert await ms.get_project_memory_history("p") == [] and await ms.get_default_memory_history() == []
        assert await ms.delete_project_memory("p") == 0
        with pytest.raises(RuntimeError):
            await ms.save_assistant_response("x", conversation_id="c", regenerate=True)

    async def test_missing_module_import_is_reported(self, monkeypatch):
        monkeypatch.setattr(ms, "get_conversation_repository", None)
        assert ms._check_mongodb_available() is False

    async def test_failed_save_is_a_runtime_error(self, repo, monkeypatch):
        async def broken(*a, **k):
            raise OSError("write failed")

        monkeypatch.setattr(repo, "create_conversation", broken)
        with pytest.raises(RuntimeError, match="Не удалось сохранить"):
            await ms.save_dialog_entry("user", "x", conversation_id="new")
