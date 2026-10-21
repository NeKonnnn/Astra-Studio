"""realtime/handlers: аутентификация Socket.IO, остановка генерации, троттлинг потока, чистые хелперы."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.realtime import handlers as h

pytestmark = pytest.mark.unit


class FakeSio:
    """Минимальная замена socketio.AsyncServer: собирает @sio.event обработчики и события."""

    def __init__(self):
        self.handlers = {}
        self.emitted = []
        self.sessions = {}
        self.disconnected = []

    def event(self, fn):
        self.handlers[fn.__name__] = fn
        return fn

    async def emit(self, name, payload=None, room=None, **kwargs):
        self.emitted.append((name, payload, room))

    async def save_session(self, sid, session):
        self.sessions[sid] = session

    async def get_session(self, sid):
        if sid not in self.sessions:
            raise KeyError(sid)
        return self.sessions[sid]

    async def disconnect(self, sid):
        self.disconnected.append(sid)

    def names(self):
        return [e[0] for e in self.emitted]


@pytest.fixture(autouse=True)
def _clean_state():
    h._generation_seq.clear()
    h._stopped_upto.clear()
    h.stop_generation_flags.clear()
    h.stop_transcription_flags.clear()
    h._my_generation.set(0)
    h._stream_conversation_id.set(None)
    h._stream_request_id.set(None)
    yield
    h._my_generation.set(0)
    h._stream_conversation_id.set(None)
    h._stream_request_id.set(None)


@pytest.fixture
def sio():
    fake = FakeSio()
    h.register_handlers(fake)
    return fake


class TestSocketToken:
    def test_token_from_auth_payload(self):
        assert h._extract_socket_token({"token": "  tok  "}, None) == "tok"
        assert h._extract_socket_token({"access_token": "tok2"}, {}) == "tok2"

    def test_token_from_authorization_header(self):
        assert h._extract_socket_token(None, {"HTTP_AUTHORIZATION": "Bearer abc"}) == "abc"
        assert h._extract_socket_token({}, {"HTTP_AUTHORIZATION": "bearer   abc  "}) == "abc"

    def test_auth_payload_wins_over_header(self):
        assert h._extract_socket_token({"token": "a"}, {"HTTP_AUTHORIZATION": "Bearer b"}) == "a"

    @pytest.mark.parametrize(
        ("auth", "environ"),
        [(None, None), ({}, {}), ({"token": "  "}, None), ({"token": 5}, None), ("str", "str"), (None, {"HTTP_AUTHORIZATION": "Basic abc"}),
         (None, {"HTTP_AUTHORIZATION": "Bearer "}), (None, {"HTTP_AUTHORIZATION": None})],
    )
    def test_no_token(self, auth, environ):
        assert h._extract_socket_token(auth, environ) is None


class TestConnect:
    @pytest.fixture
    def decode(self, monkeypatch):
        state = SimpleNamespace(calls=[], error=None, user={"username": "alice", "user_id": "alice", "session_id": "s1"})

        def fake(token, client_binding_id=None):
            state.calls.append((token, client_binding_id))
            if state.error:
                raise state.error
            return state.user

        monkeypatch.setattr(h, "decode_token_signature_only", fake)
        monkeypatch.setattr(h, "get_client_binding_id_from_cookie_header", lambda cookie: "bind-1" if "astra_binding" in cookie else None)
        return state

    async def test_valid_token_creates_a_session(self, sio, decode):
        await sio.handlers["connect"]("sid1", {"HTTP_COOKIE": "astra_binding=1", "REMOTE_ADDR": "10.0.0.1"}, {"token": "jwt"})

        user = sio.sessions["sid1"]["user"]
        assert user == {"username": "alice", "user_id": "alice", "session_id": "s1", "token": "jwt", "client_binding_id": "bind-1"}
        assert sio.emitted == [("connected", {"data": "Connected to astrachat"}, "sid1")]
        assert h.stop_generation_flags["sid1"] is False and decode.calls == [("jwt", "bind-1")]

    async def test_missing_token_is_refused(self, sio, decode):
        with pytest.raises(ConnectionRefusedError):
            await sio.handlers["connect"]("sid1", {}, None)

        assert sio.sessions == {} and decode.calls == []

    @pytest.mark.parametrize("error", [HTTPException(status_code=401, detail="expired"), ValueError("garbage")])
    async def test_invalid_token_is_refused_without_leaking_the_reason(self, sio, decode, error):
        decode.error = error

        with pytest.raises(ConnectionRefusedError) as exc:
            await sio.handlers["connect"]("sid1", {}, {"token": "bad"})

        assert str(exc.value) == "Неверный или просроченный токен" and sio.sessions == {}

    async def test_disconnect_forgets_state(self, sio, decode):
        await sio.handlers["connect"]("sid1", {}, {"token": "jwt"})
        h._begin_generation("sid1")

        await sio.handlers["disconnect"]("sid1")

        assert "sid1" not in h.stop_generation_flags and "sid1" not in h._generation_seq


class TestSessionContext:
    @pytest.fixture
    def decode(self, monkeypatch):
        state = SimpleNamespace(error=None, calls=[])

        def fake(token, client_binding_id=None):
            state.calls.append((token, client_binding_id))
            if state.error:
                raise state.error
            return {}

        monkeypatch.setattr(h, "decode_token", fake)
        return state

    async def test_returns_the_stored_user(self, sio, decode):
        sio.sessions["s"] = {"user": {"user_id": "alice", "token": "jwt", "client_binding_id": "b"}}

        assert (await h._get_socket_user_context(sio, "s"))["user_id"] == "alice"
        assert decode.calls == [("jwt", "b")]

    async def test_revoked_session_gives_none(self, sio, decode):
        sio.sessions["s"] = {"user": {"user_id": "alice", "token": "jwt"}}
        decode.error = HTTPException(status_code=401, detail="session revoked")

        assert await h._get_socket_user_context(sio, "s") is None

    @pytest.mark.parametrize("session", [None, "x", {}, {"user": "x"}])
    async def test_broken_sessions(self, sio, decode, session):
        sio.sessions["s"] = session

        assert await h._get_socket_user_context(sio, "s") is None

    async def test_unknown_sid(self, sio, decode):
        assert await h._get_socket_user_context(sio, "ghost") is None

    async def test_session_without_token_is_trusted_as_is_known_issue(self, sio, decode):
        # Без token проверка отзыва сессии пропускается (decode не вызывается).
        sio.sessions["s"] = {"user": {"user_id": "alice"}}

        assert (await h._get_socket_user_context(sio, "s")) == {"user_id": "alice"}
        assert decode.calls == []


class TestChatMessageGuards:
    async def test_services_unavailable(self, sio, monkeypatch):
        monkeypatch.setattr(h, "ask_agent", None)

        await sio.handlers["chat_message"]("s", {"message": "hi"})

        assert sio.emitted == [("chat_error", {"error": "AI services not available"}, "s")]

    async def test_unauthenticated_message_disconnects_the_socket(self, sio, monkeypatch):
        monkeypatch.setattr(h, "ask_agent", lambda *a, **k: "x")
        monkeypatch.setattr(h, "save_dialog_entry", lambda *a, **k: None)

        await sio.handlers["chat_message"]("ghost", {"message": "hi"})

        assert sio.emitted == [("chat_error", {"error": "Не авторизован"}, "ghost")] and sio.disconnected == ["ghost"]

    async def test_revoked_session_is_reported(self, sio, monkeypatch):
        monkeypatch.setattr(h, "ask_agent", lambda *a, **k: "x")
        monkeypatch.setattr(h, "save_dialog_entry", lambda *a, **k: None)

        def revoked(token, client_binding_id=None):
            raise HTTPException(status_code=401, detail="revoked")

        monkeypatch.setattr(h, "decode_token", revoked)
        sio.sessions["s"] = {"user": {"user_id": "alice", "token": "jwt"}}

        await sio.handlers["chat_message"]("s", {"message": "hi"})

        # Сессия отозвана уже на этапе _get_socket_user_context.
        assert sio.names() == ["chat_error"] and sio.disconnected == ["s"]


class TestSimpleEvents:
    async def test_ping_returns_a_pong(self, sio):
        await sio.handlers["ping"]("s", {"timestamp": 123})

        name, payload, room = sio.emitted[0]
        assert name == "pong" and payload["timestamp"] == 123 and payload["server_time"] and room == "s"

    async def test_stop_generation_stops_only_running_generations(self, sio):
        first = h._begin_generation("s")
        await sio.handlers["stop_generation"]("s", {"conversation_id": "c1", "request_id": "r1"})
        h._my_generation.set(first)
        assert h._generation_stopped("s") is True

        second = h._begin_generation("s")  # начата ПОСЛЕ стопа
        assert second == first + 1 and h._generation_stopped("s") is False
        name, payload, _ = sio.emitted[0]
        assert name == "generation_stopped" and payload["conversation_id"] == "c1" and payload["request_id"] == "r1"
        assert h.stop_generation_flags["s"] is True

    async def test_stop_generation_without_payload_fields(self, sio):
        await sio.handlers["stop_generation"]("s", None)

        assert "conversation_id" not in sio.emitted[0][1]

    async def test_stop_transcription(self, sio):
        await sio.handlers["stop_transcription"]("s", {})

        assert h.stop_transcription_flags["s"] is True and sio.names() == ["transcription_stopped"]


class TestGenerationBookkeeping:
    def test_numbers_grow_per_socket(self):
        assert [h._begin_generation("a"), h._begin_generation("a"), h._begin_generation("b")] == [1, 2, 1]

    def test_generation_without_a_number_is_never_considered_stopped(self):
        h._stopped_upto["a"] = 99

        assert h._generation_stopped("a") is False

    def test_forget(self):
        h._begin_generation("a")
        h._forget_generation("a")
        h._forget_generation("never-seen")

        assert h._generation_seq == {} and h._stopped_upto == {}


class TestStreamIds:
    def test_ids_are_added_when_bound(self):
        tokens = h._bind_stream_ids("c1", "  r1  ")
        try:
            assert h._stream_ids_payload({"x": 1}) == {"x": 1, "conversation_id": "c1", "request_id": "r1"}
        finally:
            h._reset_stream_ids(tokens)

        assert h._stream_ids_payload({"x": 1}) == {"x": 1}

    def test_explicit_payload_values_are_not_overwritten(self):
        tokens = h._bind_stream_ids("c1", "r1")
        try:
            assert h._stream_ids_payload({"conversation_id": "other"})["conversation_id"] == "other"
        finally:
            h._reset_stream_ids(tokens)

    def test_blank_request_id_is_dropped(self):
        tokens = h._bind_stream_ids(None, "   ")
        try:
            assert h._stream_ids_payload() == {}
        finally:
            h._reset_stream_ids(tokens)

    def test_reset_is_tolerant(self):
        h._reset_stream_ids(None)
        h._reset_stream_ids(("garbage", "tokens"))


class TestThrottledStream:
    async def test_chunks_are_coalesced_and_ordered(self):
        sio = FakeSio()
        cb, flush = h._make_throttled_ui_stream_cb(sio, "s", asyncio.get_running_loop())

        for i in range(50):
            assert cb(f"c{i}", f"acc{i}") is True
        await flush()

        chunks = [p for n, p, _ in sio.emitted if n == "chat_chunk"]
        assert 1 <= len(chunks) < 50  # склейка: не по одному emit на токен
        assert chunks[-1]["accumulated"] == "acc49"  # хвост не потерян
        seqs = [c["stream_seq"] for c in chunks]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    async def test_reasoning_goes_to_the_thinking_event(self):
        sio = FakeSio()
        cb, flush = h._make_throttled_ui_stream_cb(sio, "s", asyncio.get_running_loop())

        cb("мысль", "мысль", "reasoning")
        await flush()

        name, payload, _ = sio.emitted[-1]
        assert name == "chat_thinking" and payload["thinking"] == "мысль" and payload["stream_role"] == "reasoning"

    async def test_heartbeat_emits_nothing(self):
        sio = FakeSio()
        cb, flush = h._make_throttled_ui_stream_cb(sio, "s", asyncio.get_running_loop())

        assert cb("", "", "heartbeat") is True
        await flush()

        assert sio.emitted == []

    async def test_stopped_generation_cancels_the_stream(self):
        sio = FakeSio()
        cb, flush = h._make_throttled_ui_stream_cb(sio, "s", asyncio.get_running_loop())
        h._begin_generation("s")
        h._stopped_upto["s"] = 1

        assert cb("x", "x") is False and cb("", "", "heartbeat") is False
        await flush()
        assert sio.emitted == []

    async def test_ids_are_included_in_chunks(self):
        sio = FakeSio()
        tokens = h._bind_stream_ids("c1", "r1")
        try:
            cb, flush = h._make_throttled_ui_stream_cb(sio, "s", asyncio.get_running_loop())
            cb("a", "a")
            await flush()
        finally:
            h._reset_stream_ids(tokens)

        assert sio.emitted[-1][1]["conversation_id"] == "c1"

    async def test_flush_helper_tolerates_plain_callbacks(self):
        await h._flush_ui_stream_cb(lambda *a: True)


class TestHelpers:
    def test_regen_kwargs(self):
        assert h._regen_save_kwargs(None) == {"regenerate": False}
        assert h._regen_save_kwargs({}) == {"regenerate": False, "assistant_message_id": None, "alternative_responses": None, "current_response_index": None}
        data = {"regenerate": 1, "assistant_message_id": " m1 ", "alternativeResponses": ["a"], "currentResponseIndex": 1}
        assert h._regen_save_kwargs(data) == {"regenerate": True, "assistant_message_id": "m1", "alternative_responses": ["a"], "current_response_index": 1}
        assert h._regen_save_kwargs({"alternative_responses": "x", "current_response_index": "1"})["alternative_responses"] is None

    def test_generation_duration_is_at_least_one_second(self, monkeypatch):
        monkeypatch.setattr(h.time, "perf_counter", lambda: 100.0)

        assert h._generation_duration_sec(None) == 1
        assert h._generation_duration_sec(99.9) == 1
        assert h._generation_duration_sec(90.0) == 10
        assert h._with_generation_duration({"a": 1}, 95.0) == {"a": 1, "generation_duration_sec": 5}
        assert h._with_generation_duration(None, None) == {"generation_duration_sec": 1}

    @pytest.mark.parametrize(("slug", "expected"), [("Presentation-Maker", True), ("deck_html", True), ("my-PPTX", True), ("Создать презентацию", True), ("slides", True), ("coder", False), (None, False)])
    def test_presentation_slug(self, slug, expected):
        assert h._slug_implies_presentation(slug) is expected

    def test_presentation_skill_detection(self):
        assert h._presentation_skill_active({"manual": ["pptx"]}) is True
        assert h._presentation_skill_active({"always_apply": ["slide-gen"]}) is True
        assert h._presentation_skill_active(None, ["x"], ["deck-html"]) is True
        assert h._presentation_skill_active({"manual": ["coder"]}, [], None) is False

    def test_effective_max_tokens(self, monkeypatch):
        monkeypatch.setattr(h, "_system_output_tokens_floor", lambda: 50000)

        assert h._effective_max_tokens(100, presentation=False) == 4096
        assert h._effective_max_tokens(8000, presentation=False) == 8000
        assert h._effective_max_tokens(0, presentation=False, floor=10) == 10
        assert h._effective_max_tokens(8000, presentation=True) == 50000
        assert h._effective_max_tokens(90000, presentation=True) == 90000

    def test_system_output_tokens_floor_fallback(self, monkeypatch):
        import backend.utils.llm_defaults as defaults

        monkeypatch.setattr(defaults, "llm_settings_defaults", lambda: {"output_tokens": 0})
        assert h._system_output_tokens_floor() == h._PRESENTATION_MAX_TOKENS_FLOOR

        def boom():
            raise RuntimeError("no file")

        monkeypatch.setattr(defaults, "llm_settings_defaults", boom)
        assert h._system_output_tokens_floor() == h._PRESENTATION_MAX_TOKENS_FLOOR

    def test_ctx_runner_captures_the_context_at_creation(self):
        h._stream_request_id.set("early")
        runner = h._make_ctx_runner(lambda: h._stream_request_id.get())
        h._stream_request_id.set("late")

        assert runner() == "early"

    def test_llm_svc_pool_style_paths(self):
        assert h._multi_llm_llm_svc_pool_style_path("gw/model") is True
        assert h._multi_llm_llm_svc_pool_style_path("model-only") is False
        assert h._multi_llm_llm_svc_pool_style_path("") is False


class TestInlineAttachmentsMetadata:
    def test_images_are_stored_without_base64(self):
        meta = h._build_user_inline_attachments_metadata(
            [{"name": "a.png", "contentType": "image", "minio_object": "o", "minio_bucket": "b", "size": 12.7, "base64": "AAAA", "dataUrl": "data:..."}]
        )

        assert meta == {"inline_attachments": [{"name": "a.png", "contentType": "image", "minio_object": "o", "minio_bucket": "b", "size": 12}]}

    def test_text_attachment_with_context(self):
        meta = h._build_user_inline_attachments_metadata([{"name": "n.txt", "contentType": "text", "tokenEstimate": 40}], "  содержимое  ")

        assert meta == {"inline_attachments": [{"name": "n.txt", "contentType": "text", "tokenEstimate": 40}], "inline_context": "содержимое"}

    def test_snake_case_content_type_and_default_name(self):
        meta = h._build_user_inline_attachments_metadata([{"name": " ", "content_type": "text"}])

        assert meta["inline_attachments"] == [{"name": "file", "contentType": "text"}]

    def test_unknown_types_and_bad_entries_are_dropped(self):
        assert h._build_user_inline_attachments_metadata([{"name": "x", "contentType": "video"}, "str", None, 5]) is None

    @pytest.mark.parametrize("raw", [None, "x", {}, []])
    def test_nothing_to_store(self, raw):
        assert h._build_user_inline_attachments_metadata(raw) is None

    def test_context_without_attachments(self):
        assert h._build_user_inline_attachments_metadata(None, "текст") == {"inline_context": "текст"}

    def test_non_positive_sizes_are_ignored(self):
        item = h._build_user_inline_attachments_metadata([{"name": "a", "contentType": "text", "size": 0, "tokenEstimate": -1}])["inline_attachments"][0]

        assert "size" not in item and "tokenEstimate" not in item
