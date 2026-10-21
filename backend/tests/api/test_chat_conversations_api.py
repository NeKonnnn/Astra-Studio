"""routes/chat: CRUD диалогов, архив, копия, ветка и проверка владельца диалога."""

from datetime import datetime

import pytest

from backend.database.mongodb.models import Conversation, Message
from backend.routes import chat as module
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

BOB = {**ALICE, "username": "bob", "user_id": "bob"}
T0 = datetime(2026, 1, 1, 12, 0, 0)


def _msg(mid, role, content, **meta):
    return Message(message_id=mid, role=role, content=content, timestamp=T0, metadata=meta)


def _conv(cid, user_id, title="Диалог", messages=None, **kw):
    return Conversation(
        conversation_id=cid,
        user_id=user_id,
        title=title,
        messages=messages or [],
        created_at=T0,
        updated_at=T0,
        **kw,
    )


class FakeRepo:
    def __init__(self):
        self.convs = {}
        self.calls = []
        self.create_ok = True

    def add(self, conv):
        self.convs[conv.conversation_id] = conv
        return conv

    async def get_user_conversations(self, user_id, limit):
        self.calls.append(("list", user_id, limit))
        return [c for c in self.convs.values() if c.user_id == user_id][:limit]

    async def get_conversation(self, cid):
        return self.convs.get(cid)

    async def delete_conversation(self, cid):
        self.calls.append(("delete", cid))
        self.convs.pop(cid, None)

    async def delete_user_conversations(self, user_id):
        self.calls.append(("delete_all", user_id))
        mine = [cid for cid, c in self.convs.items() if c.user_id == user_id]
        for cid in mine:
            del self.convs[cid]
        return len(mine)

    async def update_conversation(self, cid, patch):
        self.calls.append(("update", cid, patch))
        for key, value in patch.items():
            setattr(self.convs[cid], key, value)

    async def create_conversation(self, conv):
        self.calls.append(("create", conv.conversation_id))
        if not self.create_ok:
            return None
        self.convs[conv.conversation_id] = conv
        return conv.conversation_id


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(module, "get_conversation_repository", lambda: fake)
    return fake


@pytest.fixture
def events(cef_events):
    return cef_events(module)


@pytest.fixture
def client(make_client, repo, events):
    return make_client(module.router)


def _as_bob(make_client):
    return make_client(module.router, user=BOB)


class TestAuthenticationAndAvailability:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/conversations"),
            ("GET", "/api/conversations/c1"),
            ("DELETE", "/api/conversations"),
            ("DELETE", "/api/conversations/c1"),
            ("PUT", "/api/conversations/c1/title"),
            ("POST", "/api/conversations/c1/archive"),
            ("POST", "/api/conversations/c1/unarchive"),
            ("POST", "/api/conversations/c1/duplicate"),
            ("POST", "/api/conversations/c1/branch"),
            ("PUT", "/api/messages/c1/m1/feedback"),
        ],
    )
    async def test_requires_login(self, make_client, repo, method, path):
        response = await make_client(module.router, user=None).request(method, path, json={})

        assert response.status_code in (401, 403)
        assert repo.calls == []

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/conversations"),
            ("GET", "/api/conversations/c1"),
            ("DELETE", "/api/conversations"),
            ("DELETE", "/api/conversations/c1"),
            ("POST", "/api/conversations/c1/archive"),
            ("POST", "/api/conversations/c1/duplicate"),
        ],
    )
    async def test_storage_unavailable(self, make_client, monkeypatch, method, path):
        monkeypatch.setattr(module, "get_conversation_repository", lambda: None)

        response = await make_client(module.router).request(method, path)

        assert response.status_code == 503


class TestListAndGet:
    async def test_list_returns_only_own_conversations(self, client, repo):
        repo.add(_conv("a", "alice", messages=[_msg("m1", "user", "привет")]))
        repo.add(_conv("b", "bob"))

        response = await client.get("/api/conversations")

        body = response.json()
        assert body["count"] == 1
        conv = body["conversations"][0]
        assert conv["conversation_id"] == "a"
        assert conv["messages"][0]["content"] == "привет"
        assert conv["created_at"] == "2026-01-01T12:00:00"
        assert repo.calls == [("list", "alice", 200)]

    async def test_list_limit(self, client, repo):
        for i in range(5):
            repo.add(_conv(f"c{i}", "alice"))

        body = (await client.get("/api/conversations", params={"limit": 2})).json()

        assert body["count"] == 2

    async def test_get_own_conversation(self, client, repo):
        repo.add(_conv("a", "alice", messages=[_msg("m1", "assistant", "ответ", model="qwen")]))

        response = await client.get("/api/conversations/a")

        message = response.json()["conversation"]["messages"][0]
        assert (message["message_id"], message["content"], message["metadata"]) == ("m1", "ответ", {"model": "qwen"})

    async def test_foreign_conversation_is_forbidden_and_not_leaked(self, make_client, repo, events):
        repo.add(_conv("a", "alice", title="Секретный план", messages=[_msg("m1", "user", "пароль 123")]))

        response = await _as_bob(make_client).get("/api/conversations/a")

        assert response.status_code == 403
        assert "Секретный" not in response.text and "пароль" not in response.text

    async def test_missing_conversation(self, client):
        assert (await client.get("/api/conversations/ghost")).status_code == 404


class TestDelete:
    async def test_owner_deletes_and_it_is_audited(self, client, repo, events):
        repo.add(_conv("a", "alice"))

        response = await client.delete("/api/conversations/a")

        assert response.json() == {"success": True, "conversation_id": "a"}
        assert "a" not in repo.convs
        assert [(c, kw["extra"]["cs2"]) for c, kw in events] == [("CNV004", "a")]

    async def test_foreign_conversation_survives(self, make_client, repo, events):
        repo.add(_conv("a", "alice"))

        response = await _as_bob(make_client).delete("/api/conversations/a")

        assert response.status_code == 403
        assert "a" in repo.convs
        assert events == []

    async def test_unknown_conversation(self, client, events):
        assert (await client.delete("/api/conversations/ghost")).status_code == 404
        assert events == []

    async def test_delete_all_touches_only_the_callers_conversations(self, make_client, repo, events):
        repo.add(_conv("a", "alice"))
        repo.add(_conv("a2", "alice"))
        repo.add(_conv("b", "bob"))

        response = await make_client(module.router).delete("/api/conversations")

        assert response.json() == {"success": True, "deleted": 2}
        assert list(repo.convs) == ["b"]
        assert [c for c, _ in events] == ["CNV005"]


class TestTitle:
    async def test_rename_trims_the_title(self, client, repo, events):
        repo.add(_conv("a", "alice"))

        response = await client.put("/api/conversations/a/title", json={"title": "  Новое имя  "})

        assert response.json()["title"] == "Новое имя"
        assert repo.convs["a"].title == "Новое имя"
        assert [c for c, _ in events] == ["CNV001"]

    @pytest.mark.parametrize("body", [{}, {"title": ""}, {"title": "   "}])
    async def test_title_is_required(self, client, repo, body):
        repo.add(_conv("a", "alice"))

        response = await client.put("/api/conversations/a/title", json=body)

        assert response.status_code == 400
        assert repo.convs["a"].title == "Диалог"

    async def test_foreign_conversation_cannot_be_renamed(self, make_client, repo):
        repo.add(_conv("a", "alice"))

        response = await _as_bob(make_client).put("/api/conversations/a/title", json={"title": "Взлом"})

        assert response.status_code == 403
        assert repo.convs["a"].title == "Диалог"

    async def test_unknown_conversation(self, client):
        assert (await client.put("/api/conversations/ghost/title", json={"title": "x"})).status_code == 404


class TestArchive:
    async def test_archive_and_unarchive_toggle_the_flag_and_keep_other_metadata(self, client, repo, events):
        repo.add(_conv("a", "alice", metadata={"keep": 1}))

        archived = await client.post("/api/conversations/a/archive")
        assert archived.json() == {"success": True, "conversation_id": "a", "archived": True}
        assert repo.convs["a"].metadata == {"keep": 1, "archived": True}

        unarchived = await client.post("/api/conversations/a/unarchive")
        assert unarchived.json()["archived"] is False
        assert repo.convs["a"].metadata == {"keep": 1, "archived": False}
        assert [c for c, _ in events] == ["CNV002", "CNV002"]

    @pytest.mark.parametrize("action", ["archive", "unarchive"])
    async def test_foreign_conversation_is_protected(self, make_client, repo, action):
        repo.add(_conv("a", "alice"))

        response = await _as_bob(make_client).post(f"/api/conversations/a/{action}")

        assert response.status_code == 403
        assert repo.convs["a"].metadata == {}

    @pytest.mark.parametrize("action", ["archive", "unarchive"])
    async def test_unknown_conversation(self, client, action):
        assert (await client.post(f"/api/conversations/ghost/{action}")).status_code == 404


class TestDuplicate:
    async def test_copy_belongs_to_the_caller_and_keeps_content(self, client, repo, events):
        repo.add(_conv("a", "alice", title="Отчёт", messages=[_msg("m1", "user", "q")], project_id="p1", metadata={"x": 1}))

        response = await client.post("/api/conversations/a/duplicate")

        new_id = response.json()["conversation_id"]
        assert new_id != "a"
        clone = repo.convs[new_id]
        assert (clone.user_id, clone.title, clone.project_id, clone.metadata) == ("alice", "Отчёт (copy)", "p1", {"x": 1})
        assert [m.content for m in clone.messages] == ["q"]
        assert [c for c, _ in events] == ["CNV003"]

    async def test_foreign_conversation_cannot_be_copied(self, make_client, repo):
        repo.add(_conv("a", "alice", messages=[_msg("m1", "user", "секрет")]))

        response = await _as_bob(make_client).post("/api/conversations/a/duplicate")

        assert response.status_code == 403
        assert list(repo.convs) == ["a"]

    async def test_untitled_conversation_gets_a_default_name(self, client, repo):
        repo.add(_conv("a", "alice", title=None))

        new_id = (await client.post("/api/conversations/a/duplicate")).json()["conversation_id"]

        assert repo.convs[new_id].title == "Диалог (copy)"

    async def test_storage_failure(self, client, repo):
        repo.add(_conv("a", "alice"))
        repo.create_ok = False

        assert (await client.post("/api/conversations/a/duplicate")).status_code == 500


class TestBranch:
    @staticmethod
    def _chat():
        return _conv(
            "a",
            "alice",
            title="Планирование",
            project_id="p1",
            messages=[
                _msg("m1", "user", "вопрос"),
                _msg("m2", "assistant", "основной", alternative_responses=["вариант 0", "вариант 1"], current_response_index=1, model="qwen"),
                _msg("m3", "user", "следующий вопрос"),
            ],
        )

    async def test_branch_copies_messages_up_to_the_point(self, client, repo, events):
        repo.add(self._chat())

        response = await client.post("/api/conversations/a/branch", json={"message_id": "m2"})

        body = response.json()
        clone = repo.convs[body["conversation_id"]]
        assert [m.role for m in clone.messages] == ["user", "assistant"]
        assert clone.title == "Ветка · Планирование"
        assert clone.project_id == "p1"
        assert clone.user_id == "alice"
        assert clone.metadata["branched_from"] == "a"
        assert clone.metadata["branch_message_id"] == "m2"
        assert clone.metadata["hidden_from_sidebar_until_user_message"] is True
        assert clone.metadata["branch_seeded_message_count"] == 2
        assert body["conversation"]["conversation_id"] == clone.conversation_id

    async def test_messages_get_new_ids(self, client, repo):
        repo.add(self._chat())

        clone = repo.convs[(await client.post("/api/conversations/a/branch", json={"message_id": "m2"})).json()["conversation_id"]]

        assert {m.message_id for m in clone.messages}.isdisjoint({"m1", "m2", "m3"})

    async def test_selected_alternative_becomes_the_branch_content(self, client, repo):
        repo.add(self._chat())

        clone = repo.convs[(await client.post("/api/conversations/a/branch", json={"message_id": "m2"})).json()["conversation_id"]]

        branch_point = clone.messages[1]
        assert branch_point.content == "вариант 1"
        assert "alternative_responses" not in branch_point.metadata
        assert branch_point.metadata["model"] == "qwen"

    async def test_multi_llm_slot_is_selected_by_index(self, client, repo):
        chat = _conv(
            "a",
            "alice",
            messages=[
                _msg("m1", "user", "q"),
                _msg(
                    "m2",
                    "assistant",
                    "",
                    multi_llm_responses=[{"model": "A", "content": "ответ A"}, {"model": "B", "content": "ответ B"}],
                ),
            ],
        )
        repo.add(chat)

        response = await client.post("/api/conversations/a/branch", json={"message_id": "m2", "multi_llm_slot_index": 1})

        point = repo.convs[response.json()["conversation_id"]].messages[1]
        assert point.content == "ответ B"
        assert point.metadata["model"] == "B"
        assert "multi_llm_responses" not in point.metadata

    async def test_original_conversation_is_not_modified(self, client, repo):
        repo.add(self._chat())

        await client.post("/api/conversations/a/branch", json={"message_id": "m2"})

        original = repo.convs["a"]
        assert [m.message_id for m in original.messages] == ["m1", "m2", "m3"]
        assert original.messages[1].content == "основной"

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({}, 400),
            ({"message_id": "  "}, 400),
            ({"message_id": "m2", "multi_llm_slot_index": "x"}, 400),
            ({"message_id": "ghost"}, 404),
            ({"message_id": "m1"}, 400),
        ],
    )
    async def test_bad_requests(self, client, repo, body, status):
        repo.add(self._chat())

        response = await client.post("/api/conversations/a/branch", json=body)

        assert response.status_code == status
        assert list(repo.convs) == ["a"]

    async def test_invalid_json_body(self, client, repo):
        repo.add(self._chat())

        response = await client.post("/api/conversations/a/branch", content=b"not json", headers={"content-type": "application/json"})

        assert response.status_code == 400

    async def test_foreign_conversation_cannot_be_branched(self, make_client, repo):
        repo.add(self._chat())

        response = await _as_bob(make_client).post("/api/conversations/a/branch", json={"message_id": "m2"})

        assert response.status_code == 403
        assert list(repo.convs) == ["a"]

    async def test_unknown_conversation(self, client):
        assert (await client.post("/api/conversations/ghost/branch", json={"message_id": "m"})).status_code == 404

    async def test_branch_is_audited_with_the_delete_event_code_known_issue(self, client, repo, events):
        # См. TESTING_KNOWN_BUGS.md: ветка пишется кодом CNV004, тем же, что и удаление диалога.
        repo.add(self._chat())

        await client.post("/api/conversations/a/branch", json={"message_id": "m2"})

        assert [c for c, _ in events] == ["CNV004"]
        assert events[0][1]["extra"]["cs3"] == "a"

    async def test_storage_failure(self, client, repo):
        repo.add(self._chat())
        repo.create_ok = False

        assert (await client.post("/api/conversations/a/branch", json={"message_id": "m2"})).status_code == 500


class TestBranchHelpers:
    @pytest.mark.parametrize(
        ("slot", "expected"),
        [
            ({"alternative_responses": ["a", "b"], "current_response_index": 1}, "b"),
            ({"alternativeResponses": ["a", "b"], "currentResponseIndex": 0}, "a"),
            ({"alternative_responses": ["a"], "current_response_index": 9}, "a"),
            ({"alternative_responses": ["a"], "current_response_index": "x"}, "a"),
            ({"alternative_responses": [], "content": "c"}, "c"),
            ({"content": "c"}, "c"),
            ({}, ""),
        ],
    )
    def test_slot_content(self, slot, expected):
        assert module._resolve_multi_llm_slot_content(slot) == expected

    def test_new_message_ids_are_unique_and_prefixed(self):
        ids = {module._new_branch_message_id() for _ in range(50)}

        assert len(ids) == 50 and all(i.startswith("msg_") and len(i) == 16 for i in ids)

    def test_non_branch_point_messages_keep_their_alternatives(self):
        msg = _msg("m", "assistant", "основной", alternative_responses=["x"], current_response_index=0)

        clone = module._clone_message_for_branch(msg, is_branch_point=False)

        assert clone.content == "основной"
        assert clone.metadata["alternative_responses"] == ["x"]
        assert clone.message_id != "m"

    def test_user_messages_are_never_rewritten(self):
        msg = _msg("m", "user", "вопрос", alternative_responses=["x"])

        assert module._clone_message_for_branch(msg, is_branch_point=True).content == "вопрос"


class TestFeedback:
    URL = "/api/messages/a/m1/feedback"

    @pytest.fixture(autouse=True)
    def _repo(self, repo, monkeypatch):
        import backend.app_state as app_state

        # Обработчик берёт репозиторий локальным импортом из app_state.
        monkeypatch.setattr(app_state, "get_conversation_repository", lambda: repo)
        repo.feedback_calls = []
        repo.feedback_ok = True

        async def update_message_feedback(cid, mid, payload, multi_llm_slot_index=None):
            repo.feedback_calls.append((cid, mid, payload, multi_llm_slot_index))
            return repo.feedback_ok

        repo.update_message_feedback = update_message_feedback
        repo.add(_conv("a", "alice", messages=[_msg("m1", "assistant", "ответ"), _msg("m0", "user", "вопрос")]))

    async def test_like_is_saved_for_the_caller(self, client, repo):
        response = await client.put(self.URL, json={"rating": "like"})

        assert response.status_code == 200
        (cid, mid, payload, slot) = repo.feedback_calls[0]
        assert (cid, mid, slot) == ("a", "m1", None)
        assert payload["rating"] == "like" and payload["user_id"] == "alice"
        assert payload["tags"] == []

    async def test_dislike_needs_a_reason(self, client, repo):
        response = await client.put(self.URL, json={"rating": "dislike"})

        assert response.status_code == 400
        assert repo.feedback_calls == []

    async def test_dislike_with_known_tags_and_comment(self, client, repo):
        response = await client.put(
            self.URL, json={"rating": "dislike", "tags": ["inaccurate", "made_up_tag", "too_short"], "comment": "  неверно  "}
        )

        payload = repo.feedback_calls[0][2]
        assert response.status_code == 200
        assert payload["tags"] == ["inaccurate", "too_short"]
        assert payload["comment"] == "неверно"

    async def test_unknown_tags_alone_are_not_a_reason(self, client, repo):
        response = await client.put(self.URL, json={"rating": "dislike", "tags": ["made_up_tag"]})

        assert response.status_code == 400

    async def test_comment_alone_is_a_reason_and_is_capped(self, client, repo):
        await client.put(self.URL, json={"rating": "dislike", "comment": "х" * 3000})

        assert len(repo.feedback_calls[0][2]["comment"]) == 2000

    async def test_tags_are_dropped_for_likes(self, client, repo):
        await client.put(self.URL, json={"rating": "like", "tags": ["inaccurate"]})

        assert repo.feedback_calls[0][2]["tags"] == []

    async def test_rating_null_clears_the_feedback(self, client, repo):
        response = await client.put(self.URL, json={"rating": None})

        assert response.json()["feedback"] is None
        assert repo.feedback_calls[0][2] is None

    async def test_slot_index_is_forwarded(self, client, repo):
        await client.put(self.URL, json={"rating": "like", "multi_llm_slot_index": 2})

        assert repo.feedback_calls[0][3] == 2

    async def test_only_assistant_messages_can_be_rated(self, client, repo):
        response = await client.put("/api/messages/a/m0/feedback", json={"rating": "like"})

        assert response.status_code == 400

    async def test_unknown_message_and_conversation(self, client, repo):
        assert (await client.put("/api/messages/a/ghost/feedback", json={"rating": "like"})).status_code == 404
        assert (await client.put("/api/messages/ghost/m1/feedback", json={"rating": "like"})).status_code == 404

    async def test_foreign_conversation_cannot_be_rated(self, make_client, repo):
        response = await _as_bob(make_client).put(self.URL, json={"rating": "like"})

        assert response.status_code == 403
        assert repo.feedback_calls == []

    async def test_conversation_without_an_owner_is_open_to_anyone_known_issue(self, make_client, repo):
        # Проверка владельца пропускается, если у диалога нет user_id (старые записи):
        # любой вошедший пользователь может оценивать такие сообщения.
        repo.add(_conv("legacy", None, messages=[_msg("m1", "assistant", "ответ")]))

        response = await _as_bob(make_client).put("/api/messages/legacy/m1/feedback", json={"rating": "like"})

        assert response.status_code == 200

    async def test_storage_refusal(self, client, repo):
        repo.feedback_ok = False

        assert (await client.put(self.URL, json={"rating": "like"})).status_code == 404

    async def test_invalid_rating_value(self, client, repo):
        assert (await client.put(self.URL, json={"rating": "meh"})).status_code == 422


class TestMessageEdit:
    """PUT /api/messages/{cid}/{mid} — правка текста сообщения."""

    @pytest.fixture(autouse=True)
    def _repo(self, repo, monkeypatch):
        import backend.app_state as app_state

        monkeypatch.setattr(app_state, "get_conversation_repository", lambda: repo)
        repo.edits = []

        async def update_message(cid, mid, content, old_content=None):
            repo.edits.append((cid, mid, content, old_content))
            return True

        repo.update_message = update_message
        repo.add(_conv("a", "alice", messages=[_msg("m1", "user", "исходный текст")]))

    async def test_owner_can_edit(self, client, repo):
        response = await client.put("/api/messages/a/m1", json={"content": "новый текст", "old_content": "исходный текст"})

        assert response.status_code == 200
        assert repo.edits == [("a", "m1", "новый текст", "исходный текст")]

    async def test_content_is_required(self, client, repo):
        response = await client.put("/api/messages/a/m1", json={"content": ""})

        assert response.status_code == 400
        assert repo.edits == []

    async def test_anonymous_visitor_must_not_edit_messages(self, make_client, repo):
        response = await make_client(module.router, user=None).put("/api/messages/a/m1", json={"content": "подмена"})

        assert response.status_code in (401, 403)
        assert repo.edits == []

    async def test_another_user_must_not_edit_someone_elses_message(self, make_client, repo):
        response = await _as_bob(make_client).put("/api/messages/a/m1", json={"content": "подмена"})

        assert response.status_code in (401, 403, 404)
        assert repo.edits == []
