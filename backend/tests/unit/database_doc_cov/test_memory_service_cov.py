"""database/memory_service: чистые функции, ветки ошибок и «пустых» состояний — на in-memory фейковом репозитории."""

import importlib.util
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.database import init_db
from backend.database import memory_service as ms
from backend.database.mongodb.models import Conversation, Message

BOOM = Exception("db exploded")


class FakeRepo:
    """In-memory замена ConversationRepository; `fail` — метод -> исключение для имитации сбоя."""

    def __init__(self):
        self.convs: dict = {}
        self.fail: dict = {}
        self.calls: list = []

    async def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name in self.fail:
            raise self.fail[name]

    async def get_conversation(self, cid):
        await self._call("get_conversation", cid)
        return self.convs.get(cid)

    async def create_conversation(self, conv):
        await self._call("create_conversation", conv)
        self.convs[conv.conversation_id] = conv
        return conv.conversation_id

    async def add_message(self, cid, message):
        await self._call("add_message", cid, message)
        self.convs[cid].messages.append(message)
        return True

    async def update_conversation(self, cid, updates):
        await self._call("update_conversation", cid, updates)
        for key, value in updates.items():
            setattr(self.convs[cid], key, value)
        return True

    async def update_assistant_message(self, cid, mid, *, content=None, metadata=None):
        await self._call("update_assistant_message", cid, mid, content=content, metadata=metadata)
        if self.fail.get("update_returns_false"):
            return False
        for m in self.convs[cid].messages:
            if m.message_id == mid:
                m.content, m.metadata = content, metadata
                return True
        return False

    async def delete_conversation(self, cid):
        await self._call("delete_conversation", cid)
        self.convs.pop(cid, None)
        return True

    async def search_conversations(self, query, user_id, limit):
        await self._call("search_conversations", query, user_id, limit)
        return [c for c in self.convs.values() if query in (c.title or "")]

    async def remove_last_message(self, cid, role=None):
        await self._call("remove_last_message", cid, role)
        return not self.fail.get("remove_false")

    async def set_conversation_project(self, cid, project_id):
        await self._call("set_conversation_project", cid, project_id)
        self.convs[cid].project_id = project_id
        return True

    async def get_conversations_by_project(self, project_id):
        await self._call("get_conversations_by_project", project_id)
        return [c for c in self.convs.values() if c.project_id == project_id]

    async def get_global_conversations(self):
        await self._call("get_global_conversations")
        return [c for c in self.convs.values() if c.project_id is None]

    async def delete_conversations_by_project(self, project_id):
        await self._call("delete_conversations_by_project", project_id)
        return 3


def msg(mid, role, content, metadata=None, ts=None):
    return Message(
        message_id=mid, role=role, content=content, metadata=metadata or {}, timestamp=ts or datetime(2024, 1, 1)
    )


def conv(cid="c1", messages=(), **kw):
    return Conversation(conversation_id=cid, user_id=kw.pop("user_id", "alice"), messages=list(messages), **kw)


@pytest.fixture
def repo(monkeypatch):
    repository = FakeRepo()
    monkeypatch.setattr(ms, "get_conversation_repository", lambda: repository)
    monkeypatch.setattr(ms, "conversation_repo", None)
    monkeypatch.setattr(ms, "mongodb_available", False)
    ms.reset_conversation()
    yield repository
    ms.reset_conversation()


def getter_returning_none_once(repository):
    """Первый вызов (из _check_mongodb_available) отдаёт None, дальнейшие — репозиторий."""
    calls = {"n": 0}

    def getter():
        calls["n"] += 1
        return None if calls["n"] == 1 else repository

    return getter


@pytest.fixture
def unavailable(monkeypatch):
    def raise_runtime():
        raise RuntimeError("не инициализирован")

    monkeypatch.setattr(ms, "get_conversation_repository", raise_runtime)
    monkeypatch.setattr(ms, "conversation_repo", None)


class TestStripReasoning:
    @pytest.mark.parametrize("text", ["", None])
    def test_empty_values_pass_through(self, text):
        assert ms._strip_reasoning_from_history_content(text) == text

    def test_closed_block_is_removed_case_insensitively(self):
        assert ms._strip_reasoning_from_history_content("<THINK>мысли\nещё</Think> Ответ ") == "Ответ"

    def test_several_blocks(self):
        assert ms._strip_reasoning_from_history_content("<think>a</think>X<think>b</think>Y") == "XY"

    def test_unclosed_block_drops_the_tail(self):
        assert ms._strip_reasoning_from_history_content("Начало <think>обрыв рассуждения") == "Начало"

    def test_text_without_reasoning_is_only_trimmed(self):
        assert ms._strip_reasoning_from_history_content("  просто ответ \n") == "просто ответ"

    def test_lone_closing_tag_must_cut_the_reasoning(self):
        assert ms._strip_reasoning_from_history_content("рассуждение модели</think>Ответ") == "Ответ"

    def test_lone_closing_tag_cuts_multiline_reasoning(self):
        assert ms._strip_reasoning_from_history_content("шаг 1\nшаг 2\n</THINK>\nОтвет") == "Ответ"


class TestHistoryAttachment:
    def test_assistant_message_is_never_extended(self):
        assert ms._history_content_with_inline_attach("assistant", "a", {"inline_context": "ctx"}) == "a"

    def test_role_is_normalised(self):
        out = ms._history_content_with_inline_attach(" USER ", "q", {"inline_context": "ctx"})
        assert out.startswith("[Прикреплённый документ]\nctx")

    @pytest.mark.parametrize("meta", [None, "строка", {}, {"inline_context": "   "}, {"inline_context": None}])
    def test_without_context_content_is_unchanged(self, meta):
        assert ms._history_content_with_inline_attach("user", "q", meta) == "q"

    def test_role_none_is_treated_as_not_user(self):
        assert ms._history_content_with_inline_attach(None, "q", {"inline_context": "c"}) == "q"

    def test_already_attached_text_is_not_duplicated(self):
        text = "[Прикреплённый документ]\nуже тут\n\nвопрос"
        assert ms._history_content_with_inline_attach("user", text, {"inline_context": "c"}) == text

    def test_question_follows_the_document(self):
        out = ms._history_content_with_inline_attach("user", "Что тут?", {"inline_context": " текст "})
        assert out == "[Прикреплённый документ]\nтекст\n\n[Вопрос пользователя]\nЧто тут?"

    def test_document_without_question(self):
        assert (
            ms._history_content_with_inline_attach("user", "", {"inline_context": "текст"})
            == "[Прикреплённый документ]\nтекст"
        )

    def test_reasoning_only_question_counts_as_empty(self):
        out = ms._history_content_with_inline_attach("user", "<think>x</think>", {"inline_context": "t"})
        assert out == "[Прикреплённый документ]\nt"


class TestHistoryEntry:
    def test_entry_has_role_content_and_iso_timestamp(self):
        entry = ms._message_to_history_entry(msg("m", "assistant", "<think>x</think>Привет"))
        assert entry == {"role": "assistant", "content": "Привет", "timestamp": "2024-01-01T00:00:00"}

    def test_object_without_timestamp_and_with_bad_metadata(self):
        raw = SimpleNamespace(role="user", content="q", metadata="не словарь")
        assert ms._message_to_history_entry(raw) == {"role": "user", "content": "q", "timestamp": None}

    def test_image_refs_are_exposed(self):
        meta = {"inline_attachments": [{"contentType": "image", "minio_object": "o", "minio_bucket": "b", "name": "n"}]}
        entry = ms._message_to_history_entry(msg("m", "user", "q", meta))
        assert entry["image_refs"] == [{"minio_object": "o", "minio_bucket": "b", "name": "n"}]


class TestModuleImportFailure:
    def test_module_survives_missing_database_layer(self, monkeypatch):
        """init_db не импортируется: модуль загружается, репозиторий недоступен, проверка возвращает False."""
        monkeypatch.setitem(sys.modules, "backend.database.init_db", None)
        spec = importlib.util.spec_from_file_location("memory_service_noinit", ms.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert (module.get_conversation_repository, module.Conversation, module.Message) == (None, None, None)
        assert module._check_mongodb_available() is False
        assert module.mongodb_available is False


class TestAvailabilityCheck:
    def test_missing_getter(self, monkeypatch):
        monkeypatch.setattr(ms, "get_conversation_repository", None)
        assert ms._check_mongodb_available() is False and ms.mongodb_available is False

    def test_repository_not_initialised(self, unavailable):
        assert ms._check_mongodb_available() is False

    def test_unexpected_error(self, monkeypatch):
        def broken():
            raise ValueError("странная ошибка")

        monkeypatch.setattr(ms, "get_conversation_repository", broken)
        assert ms._check_mongodb_available() is False

    def test_available(self, repo):
        assert ms._check_mongodb_available() is True
        assert ms.mongodb_available is True and ms.conversation_repo is repo


class TestConversationId:
    def test_legacy_attribute_reads_context_value(self):
        ms.set_current_conversation_id("abc")
        assert ms.current_conversation_id == "abc"
        ms.set_current_conversation_id("")
        assert ms.current_conversation_id is None

    def test_unknown_module_attribute(self):
        with pytest.raises(AttributeError, match="no attribute 'nope'"):
            ms.nope


class TestVerifyOwner:
    @pytest.mark.parametrize("cid,uid", [(None, "u"), ("c", None), ("", "u"), ("c", "")])
    async def test_nothing_to_check(self, cid, uid):
        assert await ms.verify_conversation_owner(cid, uid) is True

    async def test_database_unavailable_is_fail_open(self, unavailable):
        assert await ms.verify_conversation_owner("c1", "bob") is True

    async def test_new_conversation_is_allowed(self, repo):
        assert await ms.verify_conversation_owner("c1", "bob") is True

    @pytest.mark.parametrize("owner", [None, "", "default_user"])
    async def test_legacy_owner_is_open(self, repo, owner):
        repo.convs["c1"] = conv(user_id=owner)
        assert await ms.verify_conversation_owner("c1", "bob") is True

    async def test_owner_match_and_mismatch(self, repo):
        repo.convs["c1"] = conv(user_id="alice")
        assert await ms.verify_conversation_owner("c1", "alice") is True
        assert await ms.verify_conversation_owner("c1", "bob") is False

    async def test_repository_fetched_lazily(self, monkeypatch):
        r = FakeRepo()
        r.convs["c1"] = conv(user_id="alice")
        monkeypatch.setattr(ms, "get_conversation_repository", getter_returning_none_once(r))
        monkeypatch.setattr(ms, "conversation_repo", None)
        assert await ms.verify_conversation_owner("c1", "bob") is False

    async def test_error_is_fail_open(self, repo):
        repo.fail["get_conversation"] = BOOM
        assert await ms.verify_conversation_owner("c1", "bob") is True


class TestSaveMongo:
    async def test_unavailable(self, unavailable):
        assert await ms.save_dialog_entry_mongodb("user", "x", conversation_id="c1") is False

    async def test_repository_fetched_lazily(self, monkeypatch):
        r = FakeRepo()
        monkeypatch.setattr(ms, "get_conversation_repository", getter_returning_none_once(r))
        monkeypatch.setattr(ms, "conversation_repo", None)
        assert await ms.save_dialog_entry_mongodb("user", "x", conversation_id="c1") is True
        assert "c1" in r.convs

    async def test_generated_conversation_id_is_used_when_omitted(self, repo):
        ms.set_current_conversation_id("ctx")
        await ms.save_dialog_entry_mongodb("user", "x")
        assert "ctx" in repo.convs

    async def test_runtime_error_gives_false(self, repo):
        repo.fail["get_conversation"] = RuntimeError("closed")
        assert await ms.save_dialog_entry_mongodb("user", "x", conversation_id="c1") is False

    async def test_other_error_gives_false(self, repo):
        repo.fail["create_conversation"] = BOOM
        assert await ms.save_dialog_entry_mongodb("user", "x", conversation_id="c1") is False

    @pytest.mark.parametrize(
        "meta,count,role,promoted",
        [
            ({"hidden_from_sidebar_until_user_message": True, "branch_seeded_message_count": 1}, 1, "user", True),
            ({"hidden_from_sidebar_until_user_message": True, "branch_seeded_message_count": 2}, 1, "user", False),
            ({"hidden_from_sidebar_until_user_message": True, "branch_seeded_message_count": "1"}, 1, "user", False),
            ({"hidden_from_sidebar_until_user_message": True}, 1, "user", False),
            ({"hidden_from_sidebar_until_user_message": True, "branch_seeded_message_count": 1}, 1, "assistant", False),
            ({"branch_seeded_message_count": 1}, 1, "user", False),
        ],
    )
    async def test_branch_draft_is_shown_only_after_first_user_message(self, repo, meta, count, role, promoted):
        repo.convs["c1"] = conv(messages=[msg("m0", "assistant", "seed")] * count, metadata=dict(meta))
        assert await ms.save_dialog_entry_mongodb(role, "вопрос", conversation_id="c1") is True
        stored = repo.convs["c1"].metadata
        was_hidden = "hidden_from_sidebar_until_user_message" in meta
        assert ("hidden_from_sidebar_until_user_message" in stored) is (was_hidden and not promoted)
        assert len(repo.convs["c1"].messages) == count + 1

    async def test_promotion_keeps_other_metadata(self, repo):
        meta = {"hidden_from_sidebar_until_user_message": True, "branch_seeded_message_count": 0, "keep": 1}
        repo.convs["c1"] = conv(messages=[], metadata=meta)
        await ms.save_dialog_entry_mongodb("user", "q", conversation_id="c1")
        assert repo.convs["c1"].metadata == {"branch_seeded_message_count": 0, "keep": 1}


class TestSaveEntry:
    async def test_unavailable_raises(self, unavailable):
        with pytest.raises(RuntimeError, match="MongoDB недоступен"):
            await ms.save_dialog_entry("user", "x")

    async def test_failed_save_raises(self, repo):
        repo.fail["create_conversation"] = BOOM
        with pytest.raises(RuntimeError, match="Не удалось сохранить"):
            await ms.save_dialog_entry("user", "x", conversation_id="c1")

    async def test_foreign_conversation_is_rejected(self, repo):
        repo.convs["c1"] = conv(user_id="alice")
        with pytest.raises(PermissionError):
            await ms.save_dialog_entry("user", "x", conversation_id="c1", user_id="mallory")
        assert repo.convs["c1"].messages == []

    async def test_own_conversation_is_accepted(self, repo):
        repo.convs["c1"] = conv(user_id="alice")
        await ms.save_dialog_entry("user", "x", conversation_id="c1", user_id="alice")
        assert len(repo.convs["c1"].messages) == 1

    async def test_success_returns_none(self, repo):
        assert await ms.save_dialog_entry("user", "x", conversation_id="c1") is None

    async def test_unexpected_error_is_wrapped(self, repo, monkeypatch):
        monkeypatch.setattr(ms, "save_dialog_entry_mongodb", AsyncMock(side_effect=ValueError("плохо")))
        with pytest.raises(RuntimeError, match="Ошибка при сохранении сообщения: плохо") as info:
            await ms.save_dialog_entry("user", "x")
        assert isinstance(info.value.__cause__, ValueError)

    async def test_event_loop_closed_triggers_reinit_and_retry(self, repo, monkeypatch):
        fresh = FakeRepo()
        reinit = AsyncMock(return_value=True)
        reset = []
        monkeypatch.setattr(init_db, "init_mongodb", reinit)
        monkeypatch.setattr(init_db, "reset_mongodb_globals", lambda: reset.append(1))
        monkeypatch.setattr(init_db, "get_conversation_repository", lambda: fresh)
        inner = AsyncMock(side_effect=[RuntimeWarning("Event loop is closed"), True])
        monkeypatch.setattr(ms, "save_dialog_entry_mongodb", inner)
        await ms.save_dialog_entry("user", "x", conversation_id="c1")
        assert inner.await_count == 2 and reset == [1] and reinit.await_count == 1
        assert ms.conversation_repo is fresh

    async def test_reinit_failure_is_logged_and_retry_still_happens(self, repo, monkeypatch):
        monkeypatch.setattr(init_db, "init_mongodb", AsyncMock(side_effect=BOOM))
        monkeypatch.setattr(init_db, "reset_mongodb_globals", lambda: None)
        inner = AsyncMock(side_effect=[Exception("Event loop is closed"), True])
        monkeypatch.setattr(ms, "save_dialog_entry_mongodb", inner)
        await ms.save_dialog_entry("user", "x")
        assert inner.await_count == 2

    async def test_second_event_loop_error_is_final(self, repo, monkeypatch):
        monkeypatch.setattr(init_db, "init_mongodb", AsyncMock(return_value=True))
        monkeypatch.setattr(init_db, "reset_mongodb_globals", lambda: None)
        monkeypatch.setattr(init_db, "get_conversation_repository", lambda: repo)
        inner = AsyncMock(side_effect=Exception("Event loop is closed"))
        monkeypatch.setattr(ms, "save_dialog_entry_mongodb", inner)
        with pytest.raises(RuntimeError, match="Event loop is closed"):
            await ms.save_dialog_entry("user", "x")
        assert inner.await_count == 2

    async def test_event_loop_error_inside_repository_is_retried_and_final_error_is_reported(self, repo, monkeypatch):
        """Ошибка из репозитория доходит до save_dialog_entry: одна попытка с переинициализацией, затем итоговая ошибка."""
        monkeypatch.setattr(init_db, "init_mongodb", AsyncMock(return_value=True))
        monkeypatch.setattr(init_db, "reset_mongodb_globals", lambda: None)
        monkeypatch.setattr(init_db, "get_conversation_repository", lambda: repo)
        repo.fail["get_conversation"] = RuntimeError("Event loop is closed")
        with pytest.raises(RuntimeError, match="Event loop is closed"):
            await ms.save_dialog_entry("user", "x", conversation_id="c1")
        assert len([c for c in repo.calls if c[0] == "get_conversation"]) == 2

    async def test_event_loop_closed_in_repository_must_be_retried(self, repo, monkeypatch):
        fresh = FakeRepo()
        monkeypatch.setattr(init_db, "init_mongodb", AsyncMock(return_value=True))
        monkeypatch.setattr(init_db, "reset_mongodb_globals", lambda: None)
        monkeypatch.setattr(init_db, "get_conversation_repository", lambda: fresh)
        monkeypatch.setattr(ms, "get_conversation_repository", lambda: fresh)
        repo.fail["get_conversation"] = RuntimeError("Event loop is closed")
        await ms.save_dialog_entry("user", "x", conversation_id="c1")
        assert "c1" in fresh.convs


class TestLoadHistory:
    async def test_unavailable(self, unavailable):
        assert await ms.load_dialog_history_mongodb("c1") == []

    async def test_repository_fetched_lazily_and_unknown_conversation(self, monkeypatch):
        r = FakeRepo()
        monkeypatch.setattr(ms, "get_conversation_repository", getter_returning_none_once(r))
        monkeypatch.setattr(ms, "conversation_repo", None)
        assert await ms.load_dialog_history_mongodb("nope") == []

    async def test_current_conversation_used_when_omitted(self, repo):
        repo.convs["cur"] = conv("cur", [msg("m", "user", "hi")])
        ms.set_current_conversation_id("cur")
        assert [h["content"] for h in await ms.load_dialog_history_mongodb()] == ["hi"]

    async def test_runtime_error_and_other_errors_give_empty_history(self, repo):
        repo.fail["get_conversation"] = RuntimeError("closed")
        assert await ms.load_dialog_history_mongodb("c1") == []
        repo.fail["get_conversation"] = BOOM
        assert await ms.load_dialog_history_mongodb("c1") == []

    async def test_load_dialog_history_unavailable(self, unavailable):
        assert await ms.load_dialog_history() == []

    async def test_load_dialog_history_uses_current_conversation(self, repo):
        repo.convs["cur"] = conv("cur", [msg("m", "user", "hi")])
        ms.set_current_conversation_id("cur")
        assert [h["content"] for h in await ms.load_dialog_history()] == ["hi"]

    async def test_load_dialog_history_error(self, repo, monkeypatch):
        monkeypatch.setattr(ms, "load_dialog_history_mongodb", AsyncMock(side_effect=BOOM))
        assert await ms.load_dialog_history() == []


class TestRecentHistory:
    async def test_mongodb_variant_limits(self, repo):
        repo.convs["c1"] = conv(messages=[msg(f"m{i}", "user", f"t{i}") for i in range(5)])
        assert [h["content"] for h in await ms.get_recent_dialog_history_mongodb(2, "c1")] == ["t3", "t4"]
        assert len(await ms.get_recent_dialog_history_mongodb(10, "c1")) == 5
        assert len(await ms.get_recent_dialog_history_mongodb(None, "c1")) == 5

    async def test_mongodb_variant_error(self, repo, monkeypatch):
        monkeypatch.setattr(ms, "load_dialog_history_mongodb", AsyncMock(side_effect=BOOM))
        assert await ms.get_recent_dialog_history_mongodb(3, "c1") == []

    async def test_unavailable(self, unavailable):
        assert await ms.get_recent_dialog_history(3, "c1") == []

    async def test_uses_current_conversation(self, repo):
        repo.convs["cur"] = conv("cur", [msg("m", "user", "hi")])
        ms.set_current_conversation_id("cur")
        assert [h["content"] for h in await ms.get_recent_dialog_history(5)] == ["hi"]

    async def test_other_error_gives_empty_list(self, repo, monkeypatch):
        monkeypatch.setattr(ms, "get_recent_dialog_history_mongodb", AsyncMock(side_effect=BOOM))
        assert await ms.get_recent_dialog_history(5, "c1") == []

    async def test_event_loop_closed_reinitialises_and_retries(self, repo, monkeypatch):
        fresh = FakeRepo()
        inner = AsyncMock(side_effect=[Exception("Event loop is closed"), [{"role": "user", "content": "ok"}]])
        monkeypatch.setattr(ms, "get_recent_dialog_history_mongodb", inner)
        monkeypatch.setattr(init_db, "init_mongodb", AsyncMock(return_value=True))
        monkeypatch.setattr(init_db, "reset_mongodb_globals", lambda: None)
        monkeypatch.setattr(init_db, "get_conversation_repository", lambda: fresh)
        assert await ms.get_recent_dialog_history(5, "c1") == [{"role": "user", "content": "ok"}]
        assert ms.conversation_repo is fresh

    async def test_event_loop_closed_inside_repository_is_retried(self, repo, monkeypatch):
        fresh = FakeRepo()
        fresh.convs["c1"] = conv(messages=[msg("m0", "user", "ok")])
        monkeypatch.setattr(init_db, "init_mongodb", AsyncMock(return_value=True))
        monkeypatch.setattr(init_db, "reset_mongodb_globals", lambda: None)
        monkeypatch.setattr(init_db, "get_conversation_repository", lambda: fresh)
        monkeypatch.setattr(ms, "get_conversation_repository", lambda: fresh)
        repo.fail["get_conversation"] = RuntimeError("Event loop is closed")
        assert [h["content"] for h in await ms.get_recent_dialog_history(5, "c1")] == ["ok"]

    async def test_event_loop_error_in_mongodb_variants_is_propagated(self, repo):
        repo.fail["get_conversation"] = Exception("Event loop is closed")
        with pytest.raises(Exception, match="Event loop is closed"):
            await ms.get_recent_dialog_history_mongodb(5, "c1")
        repo.fail["get_conversation"] = RuntimeError("Event loop is closed")
        with pytest.raises(RuntimeError, match="Event loop is closed"):
            await ms.load_dialog_history_mongodb("c1")
        with pytest.raises(RuntimeError, match="Event loop is closed"):
            await ms.save_dialog_entry_mongodb("user", "x", conversation_id="c1")
        repo.fail["get_conversation"] = Exception("Event loop is closed")
        with pytest.raises(Exception, match="Event loop is closed"):
            await ms.save_dialog_entry_mongodb("user", "x", conversation_id="c1")
        with pytest.raises(Exception, match="Event loop is closed"):
            await ms.load_dialog_history_mongodb("c1")

    async def test_reinit_failure_is_tolerated(self, repo, monkeypatch):
        inner = AsyncMock(side_effect=[Exception("Event loop is closed"), []])
        monkeypatch.setattr(ms, "get_recent_dialog_history_mongodb", inner)
        monkeypatch.setattr(init_db, "init_mongodb", AsyncMock(side_effect=BOOM))
        monkeypatch.setattr(init_db, "reset_mongodb_globals", lambda: None)
        assert await ms.get_recent_dialog_history(5, "c1") == []
        assert inner.await_count == 2

    async def test_second_event_loop_error_gives_empty_list(self, repo, monkeypatch):
        inner = AsyncMock(side_effect=Exception("Event loop is closed"))
        monkeypatch.setattr(ms, "get_recent_dialog_history_mongodb", inner)
        monkeypatch.setattr(init_db, "init_mongodb", AsyncMock(return_value=True))
        monkeypatch.setattr(init_db, "reset_mongodb_globals", lambda: None)
        monkeypatch.setattr(init_db, "get_conversation_repository", lambda: repo)
        assert await ms.get_recent_dialog_history(5, "c1") == []
        assert inner.await_count == 2


class TestClearHistory:
    async def test_unavailable(self, unavailable):
        assert "MongoDB не инициализирован" in await ms.clear_dialog_history_mongodb()
        assert "MongoDB недоступен" in await ms.clear_dialog_history()

    async def test_clear_deletes_current_conversation_and_resets_it(self, repo):
        repo.convs["cur"] = conv("cur")
        ms.set_current_conversation_id("cur")
        assert await ms.clear_dialog_history() == "История диалога очищена"
        assert "cur" not in repo.convs
        assert ms.current_conversation_id is None

    async def test_repository_fetched_lazily(self, monkeypatch):
        r = FakeRepo()
        monkeypatch.setattr(ms, "get_conversation_repository", getter_returning_none_once(r))
        monkeypatch.setattr(ms, "conversation_repo", None)
        assert await ms.clear_dialog_history_mongodb() == "История диалога очищена"

    async def test_runtime_error(self, repo):
        repo.fail["delete_conversation"] = RuntimeError("closed")
        assert await ms.clear_dialog_history_mongodb() == "Ошибка: MongoDB не инициализирован"

    async def test_other_error_text_is_returned(self, repo):
        repo.fail["delete_conversation"] = BOOM
        assert await ms.clear_dialog_history_mongodb() == "Ошибка при очистке истории: db exploded"

    async def test_wrapper_converts_unexpected_exception(self, repo, monkeypatch):
        monkeypatch.setattr(ms, "clear_dialog_history_mongodb", AsyncMock(side_effect=BOOM))
        assert await ms.clear_dialog_history() == "Ошибка при очистке: db exploded"


class TestSearch:
    async def test_unavailable(self, unavailable):
        assert await ms.search_conversations("q") == []

    async def test_results_are_summarised(self, repo):
        repo.convs["c1"] = conv("c1", [msg("m", "user", "x")], title="про котов")
        result = await ms.search_conversations("кот", "alice", 5)
        assert len(result) == 1 and result[0]["conversation_id"] == "c1"
        assert result[0]["title"] == "про котов" and result[0]["message_count"] == 1
        assert result[0]["created_at"] and result[0]["updated_at"]
        assert repo.calls[-1] == ("search_conversations", ("кот", "alice", 5), {})

    async def test_missing_dates_become_none(self, repo):
        c = conv("c1", title="т")
        c.created_at = c.updated_at = None
        repo.convs["c1"] = c
        result = await ms.search_conversations("т")
        assert result[0]["created_at"] is None and result[0]["updated_at"] is None

    async def test_repository_fetched_lazily(self, monkeypatch):
        r = FakeRepo()
        monkeypatch.setattr(ms, "get_conversation_repository", getter_returning_none_once(r))
        monkeypatch.setattr(ms, "conversation_repo", None)
        assert await ms.search_conversations("q") == []

    async def test_errors_give_empty_list(self, repo):
        repo.fail["search_conversations"] = RuntimeError("closed")
        assert await ms.search_conversations("q") == []
        repo.fail["search_conversations"] = BOOM
        assert await ms.search_conversations("q") == []


class TestRemoveLastUser:
    async def test_unavailable(self, unavailable):
        assert await ms.remove_last_user_message("c1") is False

    async def test_removes_by_role(self, repo):
        assert await ms.remove_last_user_message("c1") is True
        assert repo.calls[-1] == ("remove_last_message", ("c1", "user"), {})

    async def test_current_conversation_default_and_repository_fetched_lazily(self, monkeypatch):
        r = FakeRepo()
        monkeypatch.setattr(ms, "get_conversation_repository", getter_returning_none_once(r))
        monkeypatch.setattr(ms, "conversation_repo", None)
        ms.set_current_conversation_id("cur")
        assert await ms.remove_last_user_message() is True
        assert r.calls[-1][1] == ("cur", "user")

    async def test_repository_refusal_is_returned(self, repo):
        repo.fail["remove_false"] = True
        assert await ms.remove_last_user_message("c1") is False

    async def test_errors_give_false(self, repo):
        repo.fail["remove_last_message"] = RuntimeError("closed")
        assert await ms.remove_last_user_message("c1") is False
        repo.fail["remove_last_message"] = BOOM
        assert await ms.remove_last_user_message("c1") is False


class TestSaveToProject:
    async def test_unavailable(self, unavailable):
        assert await ms.save_dialog_entry_to_project("user", "x", "p1", "c1") is False

    async def test_models_missing(self, repo, monkeypatch):
        monkeypatch.setattr(ms, "Conversation", None)
        assert await ms.save_dialog_entry_to_project("user", "x", "p1", "c1") is False
        monkeypatch.undo()

    async def test_new_conversation_is_bound_to_project(self, repo):
        assert await ms.save_dialog_entry_to_project("user", "x", "p1", "c1", user_id="alice") is True
        c = repo.convs["c1"]
        assert (c.project_id, c.user_id, c.title) == ("p1", "alice", "x")

    async def test_defaults_for_owner_and_ids(self, repo):
        ms.set_current_conversation_id("cur")
        await ms.save_dialog_entry_to_project("user", "x", "p1")
        assert repo.convs["cur"].user_id == "default_user"
        assert repo.convs["cur"].messages[0].message_id.startswith("msg_")

    async def test_repository_fetched_lazily(self, monkeypatch):
        r = FakeRepo()
        monkeypatch.setattr(ms, "get_conversation_repository", getter_returning_none_once(r))
        monkeypatch.setattr(ms, "conversation_repo", None)
        assert await ms.save_dialog_entry_to_project("user", "x", "p1", "c1") is True

    async def test_existing_conversation_is_moved_to_project(self, repo):
        repo.convs["c1"] = conv("c1", project_id=None)
        await ms.save_dialog_entry_to_project("user", "x", "p1", "c1")
        assert repo.convs["c1"].project_id == "p1" and len(repo.convs["c1"].messages) == 1

    async def test_existing_conversation_of_same_project_is_not_touched(self, repo):
        repo.convs["c1"] = conv("c1", project_id="p1")
        await ms.save_dialog_entry_to_project("user", "x", "p1", "c1")
        assert all(c[0] != "set_conversation_project" for c in repo.calls)

    async def test_branch_draft_is_promoted(self, repo):
        meta = {"hidden_from_sidebar_until_user_message": True, "branch_seeded_message_count": 1}
        repo.convs["c1"] = conv("c1", [msg("m0", "assistant", "seed")], project_id="p1", metadata=meta)
        await ms.save_dialog_entry_to_project("user", "q", "p1", "c1")
        assert "hidden_from_sidebar_until_user_message" not in repo.convs["c1"].metadata

    async def test_branch_draft_with_other_message_count_is_kept(self, repo):
        meta = {"hidden_from_sidebar_until_user_message": True, "branch_seeded_message_count": 5}
        repo.convs["c1"] = conv("c1", [msg("m0", "assistant", "seed")], project_id="p1", metadata=meta)
        await ms.save_dialog_entry_to_project("user", "q", "p1", "c1")
        assert repo.convs["c1"].metadata["hidden_from_sidebar_until_user_message"] is True

    async def test_error_gives_false(self, repo):
        repo.fail["get_conversation"] = BOOM
        assert await ms.save_dialog_entry_to_project("user", "x", "p1", "c1") is False


class TestProjectAndDefaultMemory:
    @pytest.fixture
    def populated(self, repo):
        repo.convs["a"] = conv("a", [msg("1", "user", "p1"), msg("2", "assistant", "p2")], project_id="P")
        repo.convs["g"] = conv("g", [msg("3", "user", "g1"), msg("4", "assistant", "g2")])
        return repo

    async def test_project_memory_only_from_that_project(self, populated):
        assert [h["content"] for h in await ms.get_project_memory_history("P")] == ["p1", "p2"]

    async def test_default_memory_only_from_global(self, populated):
        assert [h["content"] for h in await ms.get_default_memory_history()] == ["g1", "g2"]

    async def test_limits_keep_the_latest(self, populated):
        assert [h["content"] for h in await ms.get_project_memory_history("P", 1)] == ["p2"]
        assert [h["content"] for h in await ms.get_default_memory_history(1)] == ["g2"]
        assert len(await ms.get_default_memory_history(10)) == 2

    async def test_unavailable(self, unavailable):
        assert await ms.get_project_memory_history("P") == []
        assert await ms.get_default_memory_history() == []
        assert await ms.delete_project_memory("P") == 0

    @pytest.mark.parametrize(
        "call,method",
        [
            (lambda: ms.get_project_memory_history("P"), "get_conversations_by_project"),
            (lambda: ms.get_default_memory_history(), "get_global_conversations"),
        ],
    )
    async def test_repository_fetched_lazily_and_errors(self, monkeypatch, call, method):
        r = FakeRepo()
        monkeypatch.setattr(ms, "get_conversation_repository", getter_returning_none_once(r))
        monkeypatch.setattr(ms, "conversation_repo", None)
        assert await call() == []
        r.fail[method] = BOOM
        assert await call() == []

    async def test_delete_project_memory(self, repo):
        assert await ms.delete_project_memory("P") == 3
        repo.fail["delete_conversations_by_project"] = BOOM
        assert await ms.delete_project_memory("P") == 0

    async def test_delete_project_memory_repository_fetched_lazily(self, monkeypatch):
        r = FakeRepo()
        monkeypatch.setattr(ms, "get_conversation_repository", getter_returning_none_once(r))
        monkeypatch.setattr(ms, "conversation_repo", None)
        assert await ms.delete_project_memory("P") == 3


class TestAlternativeResponses:
    def test_existing_list_replaces_current_variant(self):
        assert ms._coerce_alternative_responses(
            ["a", None, 3], fallback_content="f", new_content="N", current_index=1
        ) == (["a", "N", "3"], 1)

    def test_default_index_is_last(self):
        assert ms._coerce_alternative_responses(["a", "b"], fallback_content="", new_content="N", current_index=None)[
            1
        ] == 1

    @pytest.mark.parametrize("bad", [-1, "2", 1.5])
    def test_invalid_index_falls_back_to_last(self, bad):
        alts, idx = ms._coerce_alternative_responses(["a", "b"], fallback_content="", new_content="N", current_index=bad)
        assert (alts, idx) == (["a", "N"], 1)

    def test_index_beyond_list_pads_with_empty_variants(self):
        assert ms._coerce_alternative_responses(["a"], fallback_content="", new_content="N", current_index=3) == (
            ["a", "", "", "N"],
            3,
        )

    def test_old_text_becomes_first_variant(self):
        assert ms._coerce_alternative_responses(None, fallback_content="old", new_content="new", current_index=None) == (
            ["old", "new"],
            1,
        )

    @pytest.mark.parametrize("fallback", ["", None, "same"])
    def test_single_variant(self, fallback):
        assert ms._coerce_alternative_responses([], fallback_content=fallback, new_content="same", current_index=None) == (
            ["same"],
            0,
        ) or fallback != "same"

    def test_none_new_content(self):
        assert ms._coerce_alternative_responses(None, fallback_content="", new_content=None, current_index=None) == (
            [""],
            0,
        )


class TestSaveAssistantResponse:
    async def test_plain_save_returns_given_id(self, repo):
        mid = await ms.save_assistant_response("ответ", {"k": 1}, conversation_id="c1", message_id="m1", user_id="u")
        assert mid == "m1" and repo.convs["c1"].messages[0].metadata == {"k": 1}

    async def test_target_id_is_used_when_message_id_missing(self, repo):
        assert await ms.save_assistant_response("a", conversation_id="c1", assistant_message_id=" t1 ") == "t1"

    async def test_without_ids_result_is_none(self, repo):
        assert await ms.save_assistant_response("a", conversation_id="c1") is None

    async def test_project_answer(self, repo):
        assert await ms.save_assistant_response("a", conversation_id="c1", project_id="P", message_id="m") == "m"
        assert repo.convs["c1"].project_id == "P"

    async def test_project_answer_failure_gives_none(self, repo):
        repo.fail["get_conversation"] = BOOM
        assert await ms.save_assistant_response("a", conversation_id="c1", project_id="P", message_id="m") is None

    async def test_regenerate_without_database_raises(self, unavailable):
        with pytest.raises(RuntimeError, match="Невозможно обновить"):
            await ms.save_assistant_response("a", conversation_id="c1", regenerate=True)

    async def test_regenerate_without_conversation_id_saves_normally(self, repo):
        ms.set_current_conversation_id("cur")
        await ms.save_assistant_response("a", regenerate=True)
        assert repo.convs["cur"].messages[0].metadata == {}

    async def test_regenerate_updates_target_message_in_place(self, repo):
        repo.convs["c1"] = conv(
            "c1", [msg("u1", "user", "q"), msg("a1", "assistant", "old", {"model": "m", "alternative_responses": ["old"]})]
        )
        mid = await ms.save_assistant_response(
            "new", {"x": 1}, conversation_id="c1", regenerate=True, assistant_message_id="a1"
        )
        assert mid == "a1"
        stored = repo.convs["c1"].messages[1]
        assert stored.content == "new" and len(repo.convs["c1"].messages) == 2
        assert stored.metadata == {
            "model": "m",
            "x": 1,
            "alternative_responses": ["new"],
            "current_response_index": 0,
        }

    async def test_regenerate_with_explicit_alternatives_and_camel_case_storage(self, repo):
        repo.convs["c1"] = conv("c1", [msg("a1", "assistant", "v1", {"alternativeResponses": ["v0", "v1"]})])
        await ms.save_assistant_response("v2", conversation_id="c1", regenerate=True, assistant_message_id="a1")
        assert repo.convs["c1"].messages[0].metadata["alternative_responses"] == ["v0", "v2"]
        await ms.save_assistant_response(
            "v3",
            conversation_id="c1",
            regenerate=True,
            assistant_message_id="a1",
            alternative_responses=["x", "y"],
            current_response_index=0,
        )
        meta = repo.convs["c1"].messages[0].metadata
        assert meta["alternative_responses"] == ["v3", "y"] and meta["current_response_index"] == 0

    async def test_regenerate_adds_old_text_as_first_variant(self, repo):
        repo.convs["c1"] = conv("c1", [msg("a1", "assistant", "first")])
        await ms.save_assistant_response("second", conversation_id="c1", regenerate=True, assistant_message_id="a1")
        meta = repo.convs["c1"].messages[0].metadata
        assert meta["alternative_responses"] == ["first", "second"] and meta["current_response_index"] == 1

    async def test_unknown_target_id_falls_back_to_last_assistant_message(self, repo):
        repo.convs["c1"] = conv(
            "c1", [msg("a1", "assistant", "A1"), msg("u2", "user", "q2"), msg("a2", "assistant", "A2")]
        )
        mid = await ms.save_assistant_response(
            "A2 new", conversation_id="c1", regenerate=True, assistant_message_id="front-local-id"
        )
        assert mid == "a2" and repo.convs["c1"].messages[2].content == "A2 new"
        assert repo.convs["c1"].messages[0].content == "A1"

    async def test_target_id_must_belong_to_an_assistant_message(self, repo):
        repo.convs["c1"] = conv("c1", [msg("u1", "user", "q"), msg("a1", "assistant", "A")])
        mid = await ms.save_assistant_response(
            "new", conversation_id="c1", regenerate=True, assistant_message_id="u1"
        )
        assert mid == "a1" and repo.convs["c1"].messages[0].content == "q"

    async def test_no_assistant_message_saves_a_new_one_with_variants(self, repo):
        repo.convs["c1"] = conv("c1", [msg("u1", "user", "q")])
        mid = await ms.save_assistant_response(
            "fresh", conversation_id="c1", regenerate=True, message_id="new1"
        )
        assert mid == "new1"
        last = repo.convs["c1"].messages[-1]
        assert last.content == "fresh" and last.metadata == {
            "alternative_responses": ["fresh"],
            "current_response_index": 0,
        }

    async def test_missing_conversation_creates_it(self, repo):
        await ms.save_assistant_response("fresh", conversation_id="ghost", regenerate=True, message_id="n")
        assert repo.convs["ghost"].messages[0].metadata["alternative_responses"] == ["fresh"]

    async def test_failed_update_falls_back_to_saving_a_new_message(self, repo):
        repo.convs["c1"] = conv("c1", [msg("a1", "assistant", "old")])
        repo.fail["update_returns_false"] = True
        mid = await ms.save_assistant_response(
            "new", conversation_id="c1", regenerate=True, assistant_message_id="a1"
        )
        assert mid == "a1"
        assert [m.content for m in repo.convs["c1"].messages] == ["old", "new"]
        assert repo.convs["c1"].messages[1].metadata["alternative_responses"] == ["new"]

    async def test_repository_fetched_lazily_on_regenerate(self, monkeypatch):
        r = FakeRepo()
        r.convs["c1"] = conv("c1", [msg("a1", "assistant", "old")])
        monkeypatch.setattr(ms, "get_conversation_repository", getter_returning_none_once(r))
        monkeypatch.setattr(ms, "conversation_repo", None)
        assert await ms.save_assistant_response("n", conversation_id="c1", regenerate=True) == "a1"


class TestSaveToMemory:
    @pytest.mark.parametrize(
        "role,expected",
        [
            ("Пользователь", "user"),
            ("USER", "user"),
            ("Агент", "assistant"),
            ("Assistant", "assistant"),
            ("Система", "system"),
        ],
    )
    async def test_role_is_normalised(self, repo, role, expected):
        ms.set_current_conversation_id("cur")
        await ms.save_to_memory(role, "текст")
        assert repo.convs["cur"].messages[0].role == expected

    async def test_errors_are_swallowed(self, unavailable):
        await ms.save_to_memory("user", "текст")
