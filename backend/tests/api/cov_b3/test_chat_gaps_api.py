"""routes/chat: недоступное хранилище, 404-ветки, правка сообщений, multi-LLM по WebSocket, голос и диктовка."""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from backend.routes import chat as module
from backend.tests.api.conftest import ALICE
from backend.tests.api.test_chat_conversations_api import FakeRepo, _conv, _msg

pytestmark = pytest.mark.unit


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(module, "get_conversation_repository", lambda: fake)
    import backend.app_state as app_state

    monkeypatch.setattr(app_state, "get_conversation_repository", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _no_cef(cef_events):
    cef_events(module)


# ------------------------------------------------- хранилище недоступно / диалога нет


ALL_CONVERSATION_ENDPOINTS = [
    ("PUT", "/api/conversations/c1/title", {"title": "Новое"}),
    ("POST", "/api/conversations/c1/unarchive", None),
    ("POST", "/api/conversations/c1/branch", {"message_id": "m1"}),
    ("PUT", "/api/messages/c1/m1", {"content": "x"}),
    ("PUT", "/api/messages/c1/m1/feedback", {"rating": "like"}),
]


@pytest.mark.parametrize(("method", "path", "body"), ALL_CONVERSATION_ENDPOINTS)
async def test_storage_unavailable_for_the_rest_of_endpoints(make_client, monkeypatch, method, path, body):
    import backend.app_state as app_state

    monkeypatch.setattr(module, "get_conversation_repository", lambda: None)
    monkeypatch.setattr(app_state, "get_conversation_repository", lambda: None)

    response = await make_client(module.router).request(method, path, json=body)

    assert response.status_code == 503


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("DELETE", "/api/conversations/zzz", None),
        ("PUT", "/api/conversations/zzz/title", {"title": "Новое"}),
        ("POST", "/api/conversations/zzz/archive", None),
        ("POST", "/api/conversations/zzz/unarchive", None),
        ("POST", "/api/conversations/zzz/duplicate", None),
        ("POST", "/api/conversations/zzz/branch", {"message_id": "m1"}),
        ("GET", "/api/conversations/zzz", None),
        ("PUT", "/api/messages/zzz/m1/feedback", {"rating": "like"}),
    ],
)
async def test_unknown_conversation_is_404_everywhere(make_client, repo, method, path, body):
    response = await make_client(module.router).request(method, path, json=body)

    assert response.status_code == 404
    assert not [c for c in repo.calls if c[0] in ("delete", "update", "create")]


async def test_foreign_conversation_cannot_be_unarchived(make_client, repo):
    repo.add(_conv("a", "bob"))

    response = await make_client(module.router).post("/api/conversations/a/unarchive")

    assert response.status_code == 403 and repo.calls == []


async def test_branch_only_from_assistant_messages(make_client, repo):
    repo.add(_conv("a", "alice", messages=[_msg("m1", "user", "вопрос")]))

    response = await make_client(module.router).post("/api/conversations/a/branch", json={"message_id": "m1"})

    assert response.status_code == 400 and "ответа ассистента" in response.json()["detail"]
    assert not [c for c in repo.calls if c[0] == "create"]


async def test_branch_from_unknown_message_is_404(make_client, repo):
    repo.add(_conv("a", "alice", messages=[_msg("m1", "assistant", "ответ")]))

    response = await make_client(module.router).post("/api/conversations/a/branch", json={"message_id": "nope"})

    assert response.status_code == 404 and response.json()["detail"] == "Сообщение не найдено"


def test_branch_point_with_out_of_range_alternative_index_uses_first_alternative():
    msg = _msg("m1", "assistant", "исходный", alternative_responses=["первый", "второй"], current_response_index=9)

    cloned = module._clone_message_for_branch(msg, is_branch_point=True)

    assert cloned.content == "первый"
    assert "alternative_responses" not in cloned.metadata and "current_response_index" not in cloned.metadata


def test_branch_point_with_negative_index_and_camel_case_keys():
    msg = _msg("m1", "assistant", "исходный", alternativeResponses=["a", "b"], currentResponseIndex=-1)

    cloned = module._clone_message_for_branch(msg, is_branch_point=True)

    assert cloned.content == "a" and "alternativeResponses" not in cloned.metadata


# ----------------------------------------------------------- правка сообщения и отзыв


async def test_update_message_outcomes(make_client, repo):
    async def update(cid, mid, content, old=None):
        repo.edits = getattr(repo, "edits", []) + [(cid, mid, content, old)]
        return mid == "m1"

    repo.update_message = update
    repo.add(_conv("a", "alice"))
    client = make_client(module.router)

    assert (await client.put("/api/messages/a/m1", json={"content": "x"})).status_code == 200
    missing = await client.put("/api/messages/a/m2", json={"content": "x"})
    assert missing.status_code == 404 and missing.json()["detail"] == "Сообщение не найдено"
    # BUG-BE-03: диалог проверяется на владельца до правки.
    repo.add(_conv("b", "bob"))
    assert (await client.put("/api/messages/b/m1", json={"content": "x"})).status_code == 403
    # BUG-BE-25: диалог без владельца тоже нельзя править.
    repo.add(_conv("n", None))
    assert (await client.put("/api/messages/n/m1", json={"content": "x"})).status_code == 403
    assert (await client.put("/api/messages/zzz/m1", json={"content": "x"})).status_code == 404


async def test_update_message_storage_error_is_500(make_client, repo):
    async def update(*a, **k):
        raise RuntimeError("mongo down")

    repo.update_message = update
    repo.add(_conv("a", "alice"))

    response = await make_client(module.router).put("/api/messages/a/m1", json={"content": "x"})

    assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"


async def test_feedback_storage_error_is_500(make_client, repo):
    repo.add(_conv("a", "alice", messages=[_msg("m1", "assistant", "ответ")]))

    async def boom(*a, **k):
        raise RuntimeError("mongo down")

    repo.update_message_feedback = boom

    response = await make_client(module.router).put("/api/messages/a/m1/feedback", json={"rating": "like"})

    assert response.status_code == 500


# ------------------------------------------------------------------ REST /api/chat


async def test_mcp_without_result_falls_back_to_llm(make_client, monkeypatch):
    import backend.mcp.chat_integration as integration
    import backend.services.user_feedback_context as fb

    asked = []

    async def feedback(user_id, conversation_id=None):
        return ""

    async def mcp(**kw):
        return None

    async def save(*a, **k):
        return None

    async def history(**k):
        return []

    monkeypatch.setattr(fb, "build_user_feedback_system_block", feedback)
    monkeypatch.setattr(integration, "run_mcp_for_chat", mcp)
    monkeypatch.setattr(module, "ask_agent", lambda m, **k: asked.append(m) or "прямой ответ")
    monkeypatch.setattr(module, "save_dialog_entry", save)
    monkeypatch.setattr(module, "get_recent_dialog_history", history)
    monkeypatch.setattr(module, "get_current_model_path", lambda: "gw/m")

    response = await make_client(module.router).post("/api/chat", json={"message": "вопрос", "tool_ids": ["t"]})

    assert response.json()["response"] == "прямой ответ" and asked == ["вопрос"]


# ------------------------------------------------------------------ WebSocket


@pytest.fixture
def ws_env(monkeypatch):
    env = SimpleNamespace(
        asks=[], saved=[], answer="ответ", ask_hook=None, models=[], registry=None, service=None,
    )

    def ask(message, **kw):
        env.asks.append((message, kw))
        if env.ask_hook:
            return env.ask_hook(message, **kw)
        return env.answer

    async def save(role, content, **kw):
        env.saved.append((role, content, kw))

    async def history(max_entries=None, conversation_id=None):
        return []

    async def get_registry():
        return env.registry

    monkeypatch.setattr(module, "ask_agent", ask)
    monkeypatch.setattr(module, "save_dialog_entry", save)
    monkeypatch.setattr(module, "get_recent_dialog_history", history)
    monkeypatch.setattr(module, "get_current_model_path", lambda: "gw/m")
    monkeypatch.setattr(module, "get_model_comparison_models", lambda: env.models)
    monkeypatch.setattr(module, "get_registry", get_registry)
    monkeypatch.setattr(module, "decode_token_signature_only", lambda token: {"user_id": "alice", "username": "alice"})

    import backend.llm_client as llm_client

    async def get_service():
        return env.service

    monkeypatch.setattr(llm_client, "get_llm_service", get_service)
    env.service = SimpleNamespace(prepare_messages=lambda prompt, history, system_prompt: [{"role": "user", "content": prompt}])

    app = FastAPI()
    app.include_router(module.router)
    module.manager.active_connections.clear()
    env.client = TestClient(app)
    return env


class FakeProvider:
    id = "gw"

    def __init__(self, loaded=True):
        self.loaded = loaded
        self.chat_calls = []
        self.stream_calls = []

    async def ensure_model_loaded(self, model_id):
        return self.loaded

    async def chat(self, messages, model, temperature, max_tokens):
        self.chat_calls.append((messages, model))
        return f"ответ {model}"

    async def stream_chat(self, messages, model, callback, temperature, max_tokens):
        self.stream_calls.append((messages, model))
        callback("часть", "часть")
        callback("часть2", "часть часть2")
        return f"итог {model}"


class FakeRegistry:
    def __init__(self, provider):
        self.provider = provider

    def resolve(self, name):
        if name.startswith("bad"):
            return self.provider, ""
        if name == "explode":
            raise RuntimeError("реестр сломан")
        return self.provider, name.split("/")[-1]


def _ws_messages(ws, count):
    return [json.loads(ws.receive_text()) for _ in range(count)]


def test_multi_llm_non_streaming_returns_one_answer_per_model(ws_env):
    provider = FakeProvider()
    ws_env.registry = FakeRegistry(provider)
    ws_env.models = ["gw/a", "gw/b"]

    with ws_env.client.websocket_connect("/ws/chat?token=t") as ws:
        ws.send_text(json.dumps({"message": "сравни", "model_comparison_enabled": True, "streaming": False}))
        received = _ws_messages(ws, 4)

    starts = [m for m in received if m["type"] == "multi_llm_start"]
    done = {m["model"]: m for m in received if m["type"] == "multi_llm_complete"}
    assert [m["total_models"] for m in starts] == [2, 2]
    assert done["gw/a"]["response"] == "ответ a" and done["gw/a"]["error"] is False
    assert done["gw/b"]["response"] == "ответ b"
    assert ws_env.asks == []  # обычный ask_agent не вызывался
    assert [(r, c) for r, c, _ in ws_env.saved] == [("user", "сравни")]


def test_multi_llm_streaming_sends_chunks(ws_env):
    provider = FakeProvider()
    ws_env.registry = FakeRegistry(provider)
    ws_env.models = ["gw/a"]

    with ws_env.client.websocket_connect("/ws/chat?token=t") as ws:
        ws.send_text(json.dumps({"message": "q", "model_comparison_enabled": True}))
        received = _ws_messages(ws, 4)

    chunks = [m for m in received if m["type"] == "multi_llm_chunk"]
    assert [c["chunk"] for c in chunks] == ["часть", "часть2"] and chunks[1]["accumulated"] == "часть часть2"
    complete = [m for m in received if m["type"] == "multi_llm_complete"][0]
    assert complete["response"] == "итог a" and provider.stream_calls


def test_multi_llm_reports_model_level_errors(ws_env):
    ws_env.registry = FakeRegistry(FakeProvider(loaded=False))
    ws_env.models = ["bad-path", "gw/unloaded", "explode"]

    with ws_env.client.websocket_connect("/ws/chat?token=t") as ws:
        ws.send_text(json.dumps({"message": "q", "model_comparison_enabled": True, "streaming": False}))
        received = _ws_messages(ws, 6)

    done = {m["model"]: m for m in received if m["type"] == "multi_llm_complete"}
    assert done["bad-path"]["error"] is True and "Некорректный путь модели" in done["bad-path"]["response"]
    assert done["gw/unloaded"]["error"] is True and "недоступна на провайдере" in done["gw/unloaded"]["response"]
    assert done["explode"]["error"] is True and "реестр сломан" not in done["explode"]["response"]


def test_multi_llm_gather_exception_is_reported_as_unknown_model(ws_env):
    ws_env.registry = FakeRegistry(FakeProvider())
    ws_env.models = [object()]  # json.dumps в multi_llm_start упадёт вне try внутри _gen_one

    with ws_env.client.websocket_connect("/ws/chat?token=t") as ws:
        ws.send_text(json.dumps({"message": "q", "model_comparison_enabled": True}))
        reply = json.loads(ws.receive_text())

    assert reply["type"] == "multi_llm_complete" and reply["model"] == "unknown" and reply["error"] is True


def test_streaming_answer_sends_chunks_and_completion(ws_env):
    def hook(message, stream_callback=None, **kw):
        assert stream_callback("п1", "п1") is True
        return "итог"

    ws_env.ask_hook = hook

    with ws_env.client.websocket_connect("/ws/chat?token=t") as ws:
        ws.send_text(json.dumps({"message": "q"}))
        received = {m["type"]: m for m in _ws_messages(ws, 2)}  # чанк уходит отдельной задачей, порядок не гарантирован

    assert received["chunk"] == {"type": "chunk", "chunk": "п1", "accumulated": "п1"}
    assert received["complete"]["response"] == "итог"


def test_streaming_callback_reports_failure_instead_of_raising(ws_env):
    results = []

    def hook(message, stream_callback=None, **kw):
        results.append(stream_callback(b"bytes-are-not-json", "acc"))
        return "итог"

    ws_env.ask_hook = hook

    with ws_env.client.websocket_connect("/ws/chat?token=t") as ws:
        ws.send_text(json.dumps({"message": "q"}))
        assert json.loads(ws.receive_text())["type"] == "complete"

    assert results == [False]


@pytest.mark.parametrize("frame", ["это не json", "[1, 2]", "5"])
def test_chat_socket_reports_garbage_frame_and_keeps_connection(ws_env, frame):
    """Невалидный JSON (или не объект) даёт сообщение об ошибке, сокет остаётся открытым."""
    with ws_env.client.websocket_connect("/ws/chat?token=t") as ws:
        ws.send_text(frame)
        assert json.loads(ws.receive_text()) == {"type": "error", "error": "Некорректный JSON"}

    assert module.manager.active_connections == []
    assert ws_env.saved == []


def test_chat_socket_handles_disconnect_raised_by_receive(ws_env, monkeypatch):
    async def receive_text(self):
        raise WebSocketDisconnect(1001)

    monkeypatch.setattr(WebSocket, "receive_text", receive_text)

    with ws_env.client.websocket_connect("/ws/chat?token=t"):
        pass

    assert module.manager.active_connections == []


def test_chat_socket_unexpected_error_unregisters_connection(ws_env, monkeypatch):
    async def receive_text(self):
        raise RuntimeError("сбой транспорта")

    monkeypatch.setattr(WebSocket, "receive_text", receive_text)

    with ws_env.client.websocket_connect("/ws/chat?token=t"):
        pass

    assert module.manager.active_connections == []


# ----------------------------------------------------------------- /ws/voice


def test_voice_socket_announces_missing_ai_services(ws_env, monkeypatch):
    monkeypatch.setattr(module, "ask_agent", None)

    with ws_env.client.websocket_connect("/ws/voice?token=t") as ws:
        assert json.loads(ws.receive_text()) == {"type": "error", "error": "AI сервисы недоступны."}


def test_voice_socket_ignores_unknown_commands_and_empty_frames(ws_env):
    with ws_env.client.websocket_connect("/ws/voice?token=t") as ws:
        ws.send_text(json.dumps({"type": "something-else"}))
        ws.send({"type": "websocket.receive"})  # ни text, ни bytes
        ws.send_text(json.dumps({"type": "start_listening"}))
        assert json.loads(ws.receive_text())["type"] == "listening_started"


def test_voice_socket_passes_audio_to_processing_with_user_context(ws_env, monkeypatch):
    calls = []

    async def process(websocket, data, *, user_id=None, conversation_id=None):
        calls.append((data, user_id, conversation_id))
        await websocket.send_text(json.dumps({"type": "ok"}))

    monkeypatch.setattr(module, "_process_audio", process)

    with ws_env.client.websocket_connect("/ws/voice?token=t") as ws:
        ws.send_bytes(b"audio")
        assert json.loads(ws.receive_text()) == {"type": "ok"}

    assert calls == [(b"audio", "alice", "voice_alice")]


def test_voice_socket_reports_processing_errors_and_stays_open(ws_env, monkeypatch):
    async def process(websocket, data, **kw):
        raise RuntimeError("stt упал")

    monkeypatch.setattr(module, "_process_audio", process)

    with ws_env.client.websocket_connect("/ws/voice?token=t") as ws:
        ws.send_bytes(b"audio")
        assert json.loads(ws.receive_text()) == {"type": "error", "error": "Внутренняя ошибка сервера"}
        ws.send_text(json.dumps({"type": "start_listening"}))
        assert json.loads(ws.receive_text())["type"] == "listening_started"


@pytest.mark.parametrize("exc", [WebSocketDisconnect(1001), RuntimeError("receive сломан")])
def test_voice_and_dictation_sockets_survive_receive_failures(ws_env, monkeypatch, exc):
    original = WebSocket.receive

    async def receive(self):
        if self.client_state == WebSocketState.CONNECTING:  # рукопожатие accept() идёт через receive()
            return await original(self)
        raise exc

    monkeypatch.setattr(WebSocket, "receive", receive)

    for path in ("/ws/voice?token=t", "/ws/dictation"):
        with ws_env.client.websocket_connect(path):
            pass

    assert module.manager.active_connections == []


# --------------------------------------------------------------- запись аудио


class FakeMinio:
    bucket_name = "audio"

    def __init__(self, fail=False):
        self.fail = fail
        self.uploads = []

    def generate_object_name(self, prefix, extension):
        return f"{prefix}1{extension}"

    def upload_file(self, data, obj, content_type=None, cef_display_name=None):
        if self.fail:
            raise RuntimeError("minio down")
        self.uploads.append((data, obj, content_type, cef_display_name))

    def get_file_path(self, obj):
        return f"/minio/{obj}"


@pytest.fixture
def tmp_audio_dir(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


WAV = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 200


def test_audio_is_uploaded_to_minio_when_available(monkeypatch, tmp_audio_dir):
    minio = FakeMinio()
    monkeypatch.setattr(module, "minio_client", minio)

    path = module._write_audio_bytes_to_file(WAV)

    assert path == "/minio/voice_1.wav"
    assert minio.uploads == [(WAV, "voice_1.wav", "audio/wav", "voice_upload")]
    assert list(tmp_audio_dir.iterdir()) == []


def test_audio_falls_back_to_local_file_when_minio_fails(monkeypatch, tmp_audio_dir):
    monkeypatch.setattr(module, "minio_client", FakeMinio(fail=True))

    path = module._write_audio_bytes_to_file(WAV)

    assert path.startswith(str(tmp_audio_dir)) and path.endswith(".wav")
    with open(path, "rb") as f:
        assert f.read() == WAV


def test_audio_is_written_locally_when_minio_upload_is_disabled(monkeypatch, tmp_audio_dir):
    minio = FakeMinio()
    monkeypatch.setattr(module, "minio_client", minio)

    path = module._write_audio_bytes_to_file(WAV, upload_to_minio=False)

    assert os.path.exists(path) and minio.uploads == []


# ----------------------------------------------------------------- диктовка


class FakeWs:
    """Минимальный WebSocket: собирает отправленное."""

    def __init__(self):
        self.texts = []
        self.bytes_sent = []

    async def send_text(self, text):
        self.texts.append(json.loads(text))

    async def send_bytes(self, data):
        self.bytes_sent.append(data)


@pytest.fixture
def dictation(monkeypatch, tmp_audio_dir):
    env = SimpleNamespace(text=" привет мир ", error=None, files=[])

    def recognize(path):
        env.files.append(path)
        if env.error:
            raise env.error
        return env.text

    monkeypatch.setattr(module, "recognize_speech_from_file", recognize)
    monkeypatch.setattr(module, "minio_client", None)
    return env


async def test_dictation_recognizes_text_and_removes_the_temp_file(dictation):
    ws = FakeWs()

    await module._process_dictation_audio(ws, WAV)

    assert ws.texts == [{"type": "speech_recognized", "text": "привет мир"}]
    assert not os.path.exists(dictation.files[0])


async def test_dictation_without_stt_service():
    ws = FakeWs()
    module.recognize_speech_from_file, original = None, module.recognize_speech_from_file
    try:
        await module._process_dictation_audio(ws, WAV)
    finally:
        module.recognize_speech_from_file = original

    assert ws.texts == [{"type": "error", "error": "STT недоступен"}]


async def test_dictation_rejects_short_fragments(dictation):
    ws = FakeWs()

    await module._process_dictation_audio(ws, b"x" * 99)

    assert ws.texts == [{"type": "speech_error", "error": "Слишком короткий аудиофрагмент"}] and dictation.files == []


async def test_dictation_empty_recognition(dictation):
    dictation.text = "   "
    ws = FakeWs()

    await module._process_dictation_audio(ws, WAV)

    assert ws.texts == [{"type": "speech_error", "error": "Речь не распознана"}]
    assert not os.path.exists(dictation.files[0])


async def test_dictation_value_error_is_reported_as_error(dictation):
    dictation.error = ValueError("плохой формат")
    ws = FakeWs()

    await module._process_dictation_audio(ws, WAV)

    assert ws.texts == [{"type": "error", "error": "Некорректные аудио данные"}]
    assert "плохой формат" not in json.dumps(ws.texts, ensure_ascii=False)


async def test_dictation_unexpected_error_hides_details(dictation):
    dictation.error = RuntimeError("секретный путь /srv/models")
    ws = FakeWs()

    await module._process_dictation_audio(ws, WAV)

    assert ws.texts == [{"type": "speech_error", "error": "Ошибка распознавания"}]


def test_dictation_socket_flow(ws_env, dictation):
    with ws_env.client.websocket_connect("/ws/dictation") as ws:
        ws.send_text("не json")  # игнорируется
        ws.send_text(json.dumps({"type": "start"}))
        assert json.loads(ws.receive_text()) == {"type": "ready", "message": "Готов к диктовке"}
        ws.send_text(json.dumps({"type": "other"}))
        ws.send({"type": "websocket.receive"})
        ws.send_bytes(WAV)
        assert json.loads(ws.receive_text())["type"] == "speech_recognized"


def test_dictation_socket_without_stt_announces_it(ws_env, monkeypatch):
    monkeypatch.setattr(module, "recognize_speech_from_file", None)

    with ws_env.client.websocket_connect("/ws/dictation") as ws:
        assert json.loads(ws.receive_text()) == {"type": "error", "error": "STT недоступен"}
        ws.send_bytes(WAV)
        assert json.loads(ws.receive_text()) == {"type": "error", "error": "STT недоступен"}


def test_dictation_socket_reports_unexpected_processing_errors(ws_env, monkeypatch):
    async def process(websocket, data):
        raise RuntimeError("упало")

    monkeypatch.setattr(module, "_process_dictation_audio", process)

    with ws_env.client.websocket_connect("/ws/dictation") as ws:
        ws.send_bytes(WAV)
        assert json.loads(ws.receive_text()) == {"type": "error", "error": "Внутренняя ошибка сервера"}


# ------------------------------------------------------- голосовой диалог (_process_audio)


@pytest.fixture
def voice(monkeypatch, tmp_audio_dir):
    env = SimpleNamespace(
        text="что по кредиту", ai="Ответ ассистента", saved=[], asks=[], speak_ok=True, speak_error=None,
        speak_bytes=b"R" * 100, files=[], histories=[],
    )
    monkeypatch.setattr(module.state, "voice_chat_stop_flag", False)
    monkeypatch.setattr(module, "minio_client", None)

    def recognize(path):
        env.files.append(path)
        return env.text

    def ask(text, **kw):
        env.asks.append((text, kw))
        return env.ai

    def speak(text, speaker, voice_id, save_to_file):
        env.speak_args = (text, speaker, voice_id)
        if env.speak_error:
            raise env.speak_error
        if env.speak_ok:
            with open(save_to_file, "wb") as f:
                f.write(env.speak_bytes)
        return env.speak_ok

    async def save(role, content, **kw):
        env.saved.append((role, content, kw))

    async def history(max_entries=None, conversation_id=None):
        env.histories.append(conversation_id)
        return [{"role": "user", "content": "раньше"}]

    monkeypatch.setattr(module, "recognize_speech_from_file", recognize)
    monkeypatch.setattr(module, "ask_agent", ask)
    monkeypatch.setattr(module, "speak_text", speak)
    monkeypatch.setattr(module, "save_dialog_entry", save)
    monkeypatch.setattr(module, "get_recent_dialog_history", history)
    monkeypatch.setattr(module, "get_current_model_path", lambda: "gw/m")
    env.tmp = tmp_audio_dir
    return env


async def _voice(ws, data=WAV):
    await module._process_audio(ws, data, user_id="alice", conversation_id="voice_alice")


async def test_voice_dialog_full_cycle(voice):
    ws = FakeWs()

    await _voice(ws)

    assert [m["type"] for m in ws.texts] == ["speech_recognized", "ai_response"]
    assert ws.texts[1]["text"] == "Ответ ассистента" and ws.bytes_sent == [voice.speak_bytes]
    assert [(r, c, kw["user_id"], kw["conversation_id"]) for r, c, kw in voice.saved] == [
        ("user", "что по кредиту", "alice", "voice_alice"),
        ("assistant", "Ответ ассистента", "alice", "voice_alice"),
    ]
    text, kwargs = voice.asks[0]
    assert kwargs["history"] == [{"role": "user", "content": "раньше"}] and "голосовой" in kwargs["system_prompt"]
    assert voice.speak_args == ("Ответ ассистента", "baya", "ru")
    assert list(voice.tmp.iterdir()) == []  # временные файлы убраны


async def test_voice_dialog_uses_minio_path_and_works_without_history_service(voice, monkeypatch):
    minio = FakeMinio()
    monkeypatch.setattr(module, "minio_client", minio)
    monkeypatch.setattr(module, "get_recent_dialog_history", None)
    ws = FakeWs()

    await _voice(ws, b"OggS" + b"\x00" * 200)

    assert voice.files == ["/minio/voice_1.ogg"] and minio.uploads[0][2] == "audio/ogg"
    assert voice.asks[0][1]["history"] == []


async def test_voice_dialog_falls_back_to_local_file_when_minio_fails(voice, monkeypatch):
    monkeypatch.setattr(module, "minio_client", FakeMinio(fail=True))
    ws = FakeWs()

    await _voice(ws, b"\x1aE\xdf\xa3" + b"\x00" * 200)

    assert voice.files[0].endswith(".webm") and voice.files[0].startswith(str(voice.tmp))
    assert ws.texts[0]["type"] == "speech_recognized"


@pytest.mark.parametrize(
    ("header", "ext"),
    [(b"RIFF\x00\x00\x00\x00WAVE", ".wav"), (b"OggS" + b"\x00" * 8, ".ogg"), (b"????" + b"\x00" * 8, ".webm")],
)
async def test_voice_dialog_picks_extension_from_magic_bytes(voice, header, ext):
    await _voice(FakeWs(), header + b"\x00" * 200)

    assert voice.files[0].endswith(ext)


async def test_voice_dialog_is_skipped_after_stop(voice, monkeypatch):
    monkeypatch.setattr(module.state, "voice_chat_stop_flag", True)
    ws = FakeWs()

    await _voice(ws)

    assert ws.texts == [] and voice.files == []


async def test_voice_dialog_rejects_short_audio(voice):
    ws = FakeWs()

    await _voice(ws, b"x" * 50)

    assert ws.texts == [{"type": "error", "error": "Некорректные аудио данные"}]


async def test_voice_dialog_without_stt(voice, monkeypatch):
    monkeypatch.setattr(module, "recognize_speech_from_file", None)
    ws = FakeWs()

    await _voice(ws)

    assert ws.texts == [{"type": "error", "error": "STT недоступен"}]
    assert list(voice.tmp.iterdir()) == []


async def test_voice_dialog_silence(voice):
    voice.text = "  "
    ws = FakeWs()

    await _voice(ws)

    assert ws.texts == [{"type": "speech_error", "error": "Речь не распознана"}]
    assert voice.asks == [] and voice.saved == []


@pytest.mark.parametrize("variant", ["not_ok", "tiny_file"])
async def test_voice_dialog_tts_failure_is_reported(voice, variant):
    if variant == "not_ok":
        voice.speak_ok = False
    else:
        voice.speak_bytes = b"x" * 44  # только заголовок WAV
    ws = FakeWs()

    await _voice(ws)

    assert ws.texts[-1] == {"type": "tts_error", "error": "Ошибка TTS"} and ws.bytes_sent == []


async def test_voice_dialog_tts_exception_is_reported(voice):
    voice.speak_error = RuntimeError("tts упал")
    ws = FakeWs()

    await _voice(ws)

    assert ws.texts[-1] == {"type": "tts_error", "error": "Внутренняя ошибка сервера"}


def test_connection_manager_tracks_sockets():
    manager = module.ConnectionManager()
    ws = FakeWs()

    async def connect():
        ws.accept = lambda: asyncio.sleep(0)
        await manager.connect(ws)

    asyncio.run(connect())
    assert manager.active_connections == [ws]
    manager.disconnect(ws)
    assert manager.active_connections == []


# ------------------------------------------------------------ хвосты ветвлений


def test_multi_llm_chunk_callback_survives_unserializable_chunk(ws_env):
    class BytesProvider(FakeProvider):
        async def stream_chat(self, messages, model, callback, temperature, max_tokens):
            assert callback(b"not-json", "acc") is True  # ошибка отправки только логируется
            return "итог"

    ws_env.registry = FakeRegistry(BytesProvider())
    ws_env.models = ["gw/a"]

    with ws_env.client.websocket_connect("/ws/chat?token=t") as ws:
        ws.send_text(json.dumps({"message": "q", "model_comparison_enabled": True}))
        reply = _ws_messages(ws, 2)[1]

    assert reply["type"] == "multi_llm_complete" and reply["response"] == "итог"


@pytest.mark.parametrize(
    "slots",
    [
        None,
        [],
        ["не словарь"],
    ],
)
def test_branch_point_with_unusable_multi_llm_slot_keeps_the_message(slots):
    meta = {"multi_llm_responses": slots} if slots is not None else {}
    msg = _msg("m1", "assistant", "исходный", **meta)

    cloned = module._clone_message_for_branch(msg, is_branch_point=True, multi_llm_slot_index=0)

    assert cloned.content == "исходный"


def test_branch_point_slot_without_model_keeps_other_metadata():
    msg = _msg("m1", "assistant", "исходный", multi_llm_responses=[{"content": "из слота"}], keep="да")

    cloned = module._clone_message_for_branch(msg, is_branch_point=True, multi_llm_slot_index=0)

    assert cloned.content == "из слота" and cloned.metadata == {"keep": "да"}


def test_branch_point_without_alternatives_keeps_content():
    msg = _msg("m1", "assistant", "исходный", alternative_responses=[])

    assert module._clone_message_for_branch(msg, is_branch_point=True).content == "исходный"


async def test_dictation_cleanup_tolerates_file_removed_by_recognizer(dictation, monkeypatch):
    def recognize(path):
        os.remove(path)
        return "текст"

    monkeypatch.setattr(module, "recognize_speech_from_file", recognize)
    ws = FakeWs()

    await module._process_dictation_audio(ws, WAV)

    assert ws.texts == [{"type": "speech_recognized", "text": "текст"}]


async def test_dictation_failure_before_file_is_written_needs_no_cleanup(dictation, monkeypatch):
    def broken_writer(data, upload_to_minio=True):
        raise OSError("диск переполнен")

    monkeypatch.setattr(module, "_write_audio_bytes_to_file", broken_writer)
    ws = FakeWs()

    await module._process_dictation_audio(ws, WAV)

    assert ws.texts == [{"type": "speech_error", "error": "Ошибка распознавания"}] and dictation.files == []
