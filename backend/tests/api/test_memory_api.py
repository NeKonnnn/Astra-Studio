"""routes/memory: история диалогов, статус и очистка памяти, глобальные настройки памяти."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.routes import memory as module
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

BOB = {**ALICE, "username": "bob", "user_id": "bob"}


def _msg(role, content, ts):
    return SimpleNamespace(role=role, content=content, timestamp=ts)


def _conv(conversation_id, messages):
    return SimpleNamespace(conversation_id=conversation_id, messages=messages)


class FakeRepo:
    def __init__(self):
        self.by_user = {}
        self.calls = []
        self.fail = False
        self.deleted = 3

    async def get_user_conversations(self, user_id, limit):
        self.calls.append(("get", user_id, limit))
        if self.fail:
            raise RuntimeError("mongo exploded: mongodb://root:pw@h")
        return self.by_user.get(user_id, [])

    async def delete_user_conversations(self, user_id):
        self.calls.append(("delete", user_id))
        if self.fail:
            raise RuntimeError("mongo exploded: mongodb://root:pw@h")
        return self.deleted


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(module, "get_conversation_repository", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _memory_state(monkeypatch):
    monkeypatch.setattr(module.state, "memory_max_messages", 20)
    monkeypatch.setattr(module.state, "memory_include_system_prompts", True)
    monkeypatch.setattr(module.state, "memory_clear_on_restart", False)


class TestHistory:
    async def test_requires_authentication(self, make_client, repo):
        response = await make_client(module.router, user=None).get("/api/history")

        assert response.status_code in (401, 403)
        assert repo.calls == []

    async def test_returns_only_the_callers_messages_sorted_by_time(self, make_client, repo):
        repo.by_user["alice"] = [
            _conv("c1", [_msg("user", "второе", datetime(2026, 1, 2)), _msg("assistant", "первое", datetime(2026, 1, 1))]),
            _conv("c2", [_msg("user", "третье", datetime(2026, 1, 3))]),
        ]
        repo.by_user["bob"] = [_conv("c3", [_msg("user", "чужое", datetime(2026, 1, 1))])]

        response = await make_client(module.router).get("/api/history")

        body = response.json()
        assert [m["content"] for m in body["history"]] == ["первое", "второе", "третье"]
        assert body["count"] == 3
        assert body["source"] == "memory_service"
        assert {m["conversation_id"] for m in body["history"]} == {"c1", "c2"}
        assert repo.calls == [("get", "alice", 200)]

    async def test_another_user_never_sees_foreign_messages(self, make_client, repo):
        repo.by_user["alice"] = [_conv("c1", [_msg("user", "секрет alice", datetime(2026, 1, 1))])]

        response = await make_client(module.router, user=BOB).get("/api/history")

        assert response.json()["history"] == []
        assert "секрет" not in response.text

    async def test_limit_keeps_the_latest_messages(self, make_client, repo):
        repo.by_user["alice"] = [_conv("c", [_msg("user", f"m{i}", datetime(2026, 1, i + 1)) for i in range(5)])]

        response = await make_client(module.router).get("/api/history", params={"limit": 2})

        assert [m["content"] for m in response.json()["history"]] == ["m3", "m4"]

    async def test_default_limit_comes_from_memory_settings(self, make_client, repo, monkeypatch):
        monkeypatch.setattr(module.state, "memory_max_messages", 1)
        repo.by_user["alice"] = [_conv("c", [_msg("user", "a", datetime(2026, 1, 1)), _msg("user", "b", datetime(2026, 1, 2))])]

        response = await make_client(module.router).get("/api/history")

        assert [m["content"] for m in response.json()["history"]] == ["b"]
        assert response.json()["max_messages"] == 1

    async def test_messages_without_timestamps_are_tolerated(self, make_client, repo):
        repo.by_user["alice"] = [_conv("c", [_msg("user", "a", None), SimpleNamespace(role="user", content="b", timestamp=None)])]
        repo.by_user["alice"].append(SimpleNamespace(conversation_id="empty", messages=None))

        response = await make_client(module.router).get("/api/history")

        assert response.json()["count"] == 2
        assert all(m["timestamp"] is None for m in response.json()["history"])

    async def test_memory_service_down(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "get_conversation_repository", lambda: None)

        response = await make_client(module.router).get("/api/history")

        assert response.status_code == 503

    async def test_repository_failure(self, make_client, repo):
        repo.fail = True

        response = await make_client(module.router).get("/api/history")

        assert response.status_code == 500


class TestClear:
    @pytest.mark.parametrize(("method", "path"), [("DELETE", "/api/history"), ("POST", "/api/memory/clear")])
    async def test_requires_authentication(self, make_client, repo, method, path):
        response = await make_client(module.router, user=None).request(method, path)

        assert response.status_code in (401, 403)
        assert repo.calls == []

    async def test_history_delete_clears_only_the_callers_conversations(self, make_client, repo):
        response = await make_client(module.router, user=BOB).delete("/api/history")

        assert response.status_code == 200
        assert response.json()["deleted"] == 3
        assert repo.calls == [("delete", "bob")]

    async def test_memory_clear(self, make_client, repo):
        response = await make_client(module.router).post("/api/memory/clear")

        assert response.json()["result"] == {"deleted": 3}
        assert repo.calls == [("delete", "alice")]

    @pytest.mark.parametrize(("method", "path"), [("DELETE", "/api/history"), ("POST", "/api/memory/clear")])
    async def test_memory_service_down(self, make_client, monkeypatch, method, path):
        monkeypatch.setattr(module, "get_conversation_repository", lambda: None)

        response = await make_client(module.router).request(method, path)

        assert response.status_code == 503

    async def test_clear_error_message_does_not_leak_the_exception(self, make_client, repo):
        repo.fail = True

        response = await make_client(module.router).post("/api/memory/clear")

        assert response.status_code == 500
        assert "root:pw" not in response.text  # CWE-209


class TestStatus:
    async def test_counts_the_callers_messages(self, make_client, repo):
        repo.by_user["alice"] = [_conv("a", [_msg("user", "1", None)] * 2), _conv("b", [_msg("user", "2", None)]), _conv("c", None)]

        response = await make_client(module.router).get("/api/memory/status")

        assert response.json() == {"message_count": 3, "max_messages": 20, "success": True}

    async def test_requires_authentication(self, make_client, repo):
        response = await make_client(module.router, user=None).get("/api/memory/status")

        assert response.status_code in (401, 403)

    async def test_memory_service_down(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "get_conversation_repository", lambda: None)

        assert (await make_client(module.router).get("/api/memory/status")).status_code == 503


class TestSettings:
    async def test_read(self, make_client):
        response = await make_client(module.router).get("/api/memory/settings")

        assert response.json() == {"max_messages": 20, "include_system_prompts": True, "clear_on_restart": False}

    async def test_update_changes_state_and_persists(self, make_client, monkeypatch):
        saved = []
        monkeypatch.setattr(module, "save_app_settings", lambda data: saved.append(data))

        response = await make_client(module.router).put(
            "/api/memory/settings",
            json={"max_messages": 5, "include_system_prompts": False, "clear_on_restart": True},
        )

        assert response.status_code == 200
        assert (module.state.memory_max_messages, module.state.memory_include_system_prompts, module.state.memory_clear_on_restart) == (5, False, True)
        assert saved == [{"memory_max_messages": 5, "memory_include_system_prompts": False, "memory_clear_on_restart": True}]

    async def test_update_uses_defaults_for_missing_fields(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "save_app_settings", lambda data: None)

        await make_client(module.router).put("/api/memory/settings", json={"max_messages": 7})

        assert module.state.memory_include_system_prompts is True
        assert module.state.memory_clear_on_restart is False

    @pytest.mark.parametrize("body", [{"max_messages": "many"}, {"clear_on_restart": "maybe"}, {"max_messages": None}])
    async def test_update_validates_the_payload(self, make_client, monkeypatch, body):
        monkeypatch.setattr(module, "save_app_settings", lambda data: None)

        response = await make_client(module.router).put("/api/memory/settings", json=body)

        assert response.status_code == 422
        assert module.state.memory_max_messages == 20

    async def test_persist_failure(self, make_client, monkeypatch):
        def boom(data):
            raise OSError("disk full")

        monkeypatch.setattr(module, "save_app_settings", boom)

        response = await make_client(module.router).put("/api/memory/settings", json={"max_messages": 5})

        assert response.status_code == 500

    async def test_anonymous_visitor_must_not_change_global_settings(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "save_app_settings", lambda data: None)

        response = await make_client(module.router, user=None).put("/api/memory/settings", json={"max_messages": 1})

        assert response.status_code in (401, 403)
        assert module.state.memory_max_messages == 20
