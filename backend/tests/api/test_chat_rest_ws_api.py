"""routes/chat: REST /api/chat, context-breakdown, follow-up подсказки и аутентификация WebSocket (/ws/chat, /ws/voice)."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.routes import chat as module
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

BOB = {**ALICE, "username": "bob", "user_id": "bob"}


@pytest.fixture
def agent(monkeypatch):
    """Фейковый ask_agent/сохранение диалога и владелец диалога."""
    state = SimpleNamespace(asks=[], saved=[], owner_ok=True, answer="Ответ модели", ask_error=None, history=[], mcp=None, mcp_error=None)

    def ask_agent(message, **kwargs):
        state.asks.append((message, kwargs))
        if state.ask_error:
            raise state.ask_error
        return state.answer

    async def save(role, content, **kwargs):
        state.saved.append((role, content, kwargs))

    async def history(max_entries=None, conversation_id=None):
        state.asks_history = (max_entries, conversation_id)
        return state.history

    async def owner(conversation_id, user_id):
        state.owner_args = (conversation_id, user_id)
        return state.owner_ok

    async def feedback(user_id, conversation_id=None):
        return ""

    async def mcp(**kwargs):
        state.mcp_args = kwargs
        if state.mcp_error:
            raise state.mcp_error
        return state.mcp

    monkeypatch.setattr(module, "ask_agent", ask_agent)
    monkeypatch.setattr(module, "save_dialog_entry", save)
    monkeypatch.setattr(module, "get_recent_dialog_history", history)
    monkeypatch.setattr(module, "verify_conversation_owner", owner)
    monkeypatch.setattr(module, "get_current_model_path", lambda: "gw/default")
    import backend.services.user_feedback_context as fb
    import backend.mcp.chat_integration as integration

    monkeypatch.setattr(fb, "build_user_feedback_system_block", feedback)
    monkeypatch.setattr(integration, "run_mcp_for_chat", mcp)
    return state


class TestRestChat:
    URL = "/api/chat"

    async def test_login_required(self, make_client, agent):
        assert (await make_client(module.router, user=None).post(self.URL, json={"message": "hi"})).status_code in (401, 403)
        assert agent.asks == []

    async def test_answer_is_saved_under_a_personal_conversation(self, make_client, agent):
        response = await make_client(module.router).post(self.URL, json={"message": "привет"})

        body = response.json()
        assert response.status_code == 200 and body["response"] == "Ответ модели" and body["success"] is True
        assert [(r, c) for r, c, _ in agent.saved] == [("user", "привет"), ("assistant", "Ответ модели")]
        assert all(kw["conversation_id"] == "voice_alice" and kw["user_id"] == "alice" for _, _, kw in agent.saved)
        message, kwargs = agent.asks[0]
        assert kwargs["streaming"] is False and kwargs["model_path"] == "gw/default"

    async def test_requested_model_and_history_are_used(self, make_client, agent):
        agent.history = [{"role": "user", "content": "раньше"}]

        await make_client(module.router).post(self.URL, json={"message": "m", "model": "gw/special", "conversation_id": "c1"})

        _, kwargs = agent.asks[0]
        assert kwargs["model_path"] == "gw/special" and kwargs["history"] == agent.history
        assert agent.owner_args == ("c1", "alice") and agent.asks_history[1] == "c1"

    async def test_ai_agent_unavailable(self, make_client, agent, monkeypatch):
        monkeypatch.setattr(module, "ask_agent", None)

        assert (await make_client(module.router).post(self.URL, json={"message": "m"})).status_code == 503

    async def test_memory_service_unavailable(self, make_client, agent, monkeypatch):
        monkeypatch.setattr(module, "save_dialog_entry", None)

        assert (await make_client(module.router).post(self.URL, json={"message": "m"})).status_code == 503

    async def test_message_is_required(self, make_client, agent):
        assert (await make_client(module.router).post(self.URL, json={})).status_code == 422

    async def test_mcp_result_replaces_the_plain_llm_call(self, make_client, agent):
        agent.mcp = SimpleNamespace(content="ответ через MCP", mode="native", tool_calls_executed=2)

        body = (await make_client(module.router).post(self.URL, json={"message": "m", "tool_ids": ["docs"], "conversation_id": "c1", "message_id": "m1"})).json()

        assert body["response"] == "ответ через MCP" and agent.asks == []
        assert agent.mcp_args["tool_ids"] == ["docs"] and agent.mcp_args["chat_id"] == "c1" and agent.mcp_args["user"]["user_id"] == "alice"

    async def test_legacy_mcp_tool_ids_are_accepted(self, make_client, agent):
        agent.mcp = SimpleNamespace(content="x", mode="m", tool_calls_executed=0)

        await make_client(module.router).post(self.URL, json={"message": "m", "mcp_tool_ids": ["legacy"]})

        assert agent.mcp_args["tool_ids"] == ["legacy"]

    async def test_mcp_failure_falls_back_to_the_plain_llm(self, make_client, agent):
        agent.mcp_error = RuntimeError("mcp down")

        body = (await make_client(module.router).post(self.URL, json={"message": "m", "tool_ids": ["docs"]})).json()

        assert body["response"] == "Ответ модели"

    async def test_model_errors_become_500_without_the_message(self, make_client, agent):
        agent.ask_error = RuntimeError("upstream 10.0.0.9 refused")

        response = await make_client(module.router).post(self.URL, json={"message": "m"})

        assert response.status_code == 500 and "10.0.0.9" not in response.text  # CWE-209
        assert agent.saved == []

    async def test_foreign_conversation_is_forbidden(self, make_client, agent):
        agent.owner_ok = False

        response = await make_client(module.router).post(self.URL, json={"message": "m", "conversation_id": "alice-private"})

        assert response.status_code == 403

    async def test_foreign_conversation_is_not_answered_or_saved(self, make_client, agent):
        # Доступ закрыт (ничего не вызвано и не сохранено), код ответа — 403, а не 500.
        agent.owner_ok = False

        response = await make_client(module.router, user=BOB).post(self.URL, json={"message": "m", "conversation_id": "alice-private"})

        assert response.status_code == 403 and response.json()["detail"] == "Нет доступа к этому диалогу"
        assert agent.asks == [] and agent.saved == []


class TestContextBreakdown:
    URL = "/api/chat/context-breakdown"

    async def test_requires_login(self, make_client):
        assert (await make_client(module.router, user=None).post(self.URL, json={})).status_code in (401, 403)

    async def test_arguments_are_forwarded_and_success_is_added(self, make_client, monkeypatch):
        seen = {}

        async def build(**kwargs):
            seen.update(kwargs)
            return {"segments": [1, 2]}

        monkeypatch.setattr(module, "build_context_overhead", build)

        response = await make_client(module.router).post(self.URL, json={"model_path": "gw/m", "agent_id": 3, "use_kb_rag": True, "tool_ids": ["t"], "project_instructions": "ИНСТР"})

        assert response.json() == {"segments": [1, 2], "success": True}
        assert seen["model_path"] == "gw/m" and seen["agent_id"] == 3 and seen["use_kb_rag"] is True
        assert seen["tool_ids"] == ["t"] and seen["project_instructions"] == "ИНСТР" and seen["user"]["user_id"] == "alice"

    async def test_errors_become_500(self, make_client, monkeypatch):
        async def build(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(module, "build_context_overhead", build)

        assert (await make_client(module.router).post(self.URL, json={})).status_code == 500


class TestFollowUps:
    URL = "/api/chat/follow-up-suggestions"

    @pytest.fixture
    def gen(self, monkeypatch, agent):
        state = SimpleNamespace(calls=[], result=["Расскажи подробнее", "Приведи пример"])

        async def generate(*, ask_agent, messages, model_path):
            state.calls.append((messages, model_path))
            return state.result

        monkeypatch.setattr(module, "generate_follow_up_suggestions", generate)
        return state

    async def test_requires_login(self, make_client, gen):
        assert (await make_client(module.router, user=None).post(self.URL, json={"messages": []})).status_code in (401, 403)

    async def test_suggestions(self, make_client, gen):
        body = {"messages": [{"role": "user", "content": "Что такое RAG?"}, {"role": "assistant", "content": "Поиск + генерация"}]}

        response = await make_client(module.router).post(self.URL, json=body)

        assert response.json() == {"success": True, "suggestions": ["Расскажи подробнее", "Приведи пример"]}
        assert gen.calls == [([{"role": "user", "content": "Что такое RAG?"}, {"role": "assistant", "content": "Поиск + генерация"}], "gw/default")]

    async def test_explicit_model_path(self, make_client, gen):
        await make_client(module.router).post(self.URL, json={"messages": [{"role": "user", "content": "x"}], "model_path": "gw/other"})

        assert gen.calls[0][1] == "gw/other"

    async def test_empty_history_skips_generation(self, make_client, gen):
        assert (await make_client(module.router).post(self.URL, json={"messages": []})).json() == {"success": True, "suggestions": []}
        assert gen.calls == []

    async def test_agent_unavailable(self, make_client, gen, monkeypatch):
        monkeypatch.setattr(module, "ask_agent", None)

        assert (await make_client(module.router).post(self.URL, json={"messages": [{"role": "user", "content": "x"}]})).status_code == 503

    async def test_invalid_roles_are_rejected(self, make_client, gen):
        assert (await make_client(module.router).post(self.URL, json={"messages": [{"role": "system", "content": "x"}]})).status_code == 422


# --- WebSocket ---------------------------------------------------------------


@pytest.fixture
def ws_client(agent, monkeypatch):
    app = FastAPI()
    app.include_router(module.router)
    users = {"good-token": {"user_id": "alice", "username": "alice"}, "bob-token": {"user_id": "bob", "username": "bob"}}

    def decode(token):
        if token not in users:
            raise ValueError("bad token")
        return users[token]

    monkeypatch.setattr(module, "decode_token_signature_only", decode)
    module.manager.active_connections.clear()
    return TestClient(app)


class TestWebSocketAuth:
    @pytest.mark.parametrize("path", ["/ws/chat", "/ws/chat?token=forged", "/ws/voice", "/ws/voice?access_token=forged"])
    def test_unauthenticated_connections_are_closed_with_1008(self, ws_client, path):
        with pytest.raises(WebSocketDisconnect) as exc:
            with ws_client.websocket_connect(path):
                pass

        assert exc.value.code == 1008

    @pytest.mark.parametrize("path", ["/ws/chat?token=good-token", "/ws/chat?access_token=good-token"])
    def test_token_in_the_query_is_accepted(self, ws_client, agent, path):
        with ws_client.websocket_connect(path) as ws:
            ws.send_text(json.dumps({"message": "привет", "streaming": False}))
            reply = json.loads(ws.receive_text())

        assert reply["type"] == "complete" and reply["response"] == "Ответ модели"

    def test_bearer_header_is_accepted(self, ws_client, agent):
        with ws_client.websocket_connect("/ws/chat", headers={"Authorization": "Bearer good-token"}) as ws:
            ws.send_text(json.dumps({"message": "привет", "streaming": False}))
            assert json.loads(ws.receive_text())["type"] == "complete"

    def test_non_bearer_header_is_not_enough(self, ws_client):
        with pytest.raises(WebSocketDisconnect):
            with ws_client.websocket_connect("/ws/chat", headers={"Authorization": "Basic good-token"}):
                pass

    def test_chat_is_closed_when_ai_services_are_down(self, ws_client, agent, monkeypatch):
        monkeypatch.setattr(module, "ask_agent", None)

        with pytest.raises(WebSocketDisconnect) as exc:
            with ws_client.websocket_connect("/ws/chat?token=good-token"):
                pass

        assert exc.value.code == 1008


class TestWebSocketChat:
    def test_dialog_is_bound_to_the_authenticated_user(self, ws_client, agent):
        with ws_client.websocket_connect("/ws/chat?token=bob-token") as ws:
            ws.send_text(json.dumps({"message": "я Боб", "streaming": False}))
            ws.receive_text()

        assert agent.asks_history[1] == "wschat_bob"
        assert [(r, kw["conversation_id"], kw["user_id"]) for r, _, kw in agent.saved] == [("user", "wschat_bob", "bob"), ("assistant", "wschat_bob", "bob")]

    def test_client_cannot_choose_the_conversation(self, ws_client, agent):
        with ws_client.websocket_connect("/ws/chat?token=bob-token") as ws:
            ws.send_text(json.dumps({"message": "x", "streaming": False, "conversation_id": "wschat_alice", "user_id": "alice"}))
            ws.receive_text()

        assert {kw["conversation_id"] for _, _, kw in agent.saved} == {"wschat_bob"}

    def test_errors_are_reported_and_the_socket_stays_open(self, ws_client, agent):
        agent.ask_error = RuntimeError("модель упала")

        with ws_client.websocket_connect("/ws/chat?token=good-token") as ws:
            ws.send_text(json.dumps({"message": "x", "streaming": False}))
            error_frame = json.loads(ws.receive_text())
            assert error_frame["type"] == "error" and "модель упала" not in error_frame["error"]
            agent.ask_error = None
            ws.send_text(json.dumps({"message": "ещё", "streaming": False}))
            assert json.loads(ws.receive_text())["type"] == "complete"

    def test_multi_llm_without_selected_models(self, ws_client, agent, monkeypatch):
        monkeypatch.setattr(module, "get_model_comparison_models", lambda: [])

        with ws_client.websocket_connect("/ws/chat?token=good-token") as ws:
            ws.send_text(json.dumps({"message": "x", "model_comparison_enabled": True}))
            assert json.loads(ws.receive_text()) == {"type": "error", "error": "Модели не выбраны"}

    def test_connection_is_unregistered_after_disconnect(self, ws_client, agent):
        with ws_client.websocket_connect("/ws/chat?token=good-token"):
            assert len(module.manager.active_connections) == 1

        assert module.manager.active_connections == []


class TestWebSocketVoice:
    def test_control_commands(self, ws_client, agent):
        with ws_client.websocket_connect("/ws/voice?token=good-token") as ws:
            ws.send_text(json.dumps({"type": "start_listening"}))
            assert json.loads(ws.receive_text())["type"] == "listening_started"
            ws.send_text(json.dumps({"type": "stop_processing"}))
            assert json.loads(ws.receive_text())["type"] == "processing_stopped"
            ws.send_text(json.dumps({"type": "reset_processing"}))
            assert json.loads(ws.receive_text())["type"] == "processing_reset"
            ws.send_text("not json")  # молча игнорируется
            ws.send_text(json.dumps({"type": "start_listening"}))
            assert json.loads(ws.receive_text())["type"] == "listening_started"

    def test_invalid_audio_is_reported_as_an_error(self, ws_client, agent):
        with ws_client.websocket_connect("/ws/voice?token=good-token") as ws:
            ws.send_bytes(b"tiny")
            reply = json.loads(ws.receive_text())

        assert reply["type"] == "error"


class TestAudioHelper:
    def test_short_data_is_rejected(self):
        with pytest.raises(ValueError):
            module._write_audio_bytes_to_file(b"x" * 99, upload_to_minio=False)

    @pytest.mark.parametrize(
        ("header", "ext"),
        [(b"RIFF\x00\x00\x00\x00WAVE", ".wav"), (b"\x1aE\xdf\xa3" + b"\x00" * 8, ".webm"), (b"OggS" + b"\x00" * 8, ".ogg"), (b"????" + b"\x00" * 8, ".webm")],
    )
    def test_container_is_detected_from_magic_bytes(self, header, ext):
        import os

        path = module._write_audio_bytes_to_file(header + b"\x00" * 200, upload_to_minio=False)
        try:
            assert path.endswith(ext)
        finally:
            os.unlink(path)
