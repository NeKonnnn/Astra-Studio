"""realtime/handlers.chat_message: подготовка запроса, проверки доступа, диспетчеризация direct/multi-LLM."""

import sys
import types

import pytest

from backend.realtime import handlers as h

pytestmark = pytest.mark.unit

USER = {"user_id": "alice", "username": "alice"}


@pytest.fixture
def env(monkeypatch, sio, rec):
    """Все внешние зависимости chat_message заменены фейками; возвращает управляющий объект."""
    import backend.database.memory_service as mem
    import backend.agents.chain.runner as runner
    import backend.auth.agent_platform_roles as roles
    import backend.settings.cef_logger.cef_logger as cef

    st = types.SimpleNamespace(
        profile={"name": None, "model_path": None, "file_search_enabled": False, "kb_document_ids": []},
        owner_ok=True,
        project_of_conv=None,
        history=[{"role": "user", "content": "раньше"}],
        project_history=[{"role": "user", "content": "из проекта"}],
        scopes_error=None,
        save_error=None,
        runner_error=None,
        can_use_mcp=True,
        merge_images_error=None,
    )
    sio.sessions["s1"] = {"user": {"user_id": "alice", "token": "jwt", "client_binding_id": "b"}}

    async def ctx(sio_, sid):
        return (await sio_.get_session(sid)).get("user")

    monkeypatch.setattr(h, "_get_socket_user_context", ctx)
    monkeypatch.setattr(h, "decode_token", lambda token, client_binding_id=None: dict(USER))
    monkeypatch.setattr(h, "ask_agent", lambda *a, **k: "x")
    monkeypatch.setattr(h, "save_dialog_entry", None)  # переопределяется ниже

    async def save_dialog_entry(*a, **kw):
        rec.add("save_dialog_entry", *a, **kw)
        if st.save_error:
            raise st.save_error

    monkeypatch.setattr(h, "save_dialog_entry", save_dialog_entry)
    monkeypatch.setattr(h, "get_current_model_path", lambda: "global-model")

    async def resolve(agent_id, user_id, user):
        rec.add("resolve_agent", agent_id, user_id)
        return dict(st.profile)

    async def enrich(profile, user_id):
        rec.add("enrich", user_id)
        return profile

    monkeypatch.setattr(h, "_resolve_agent_chat_params", resolve)
    monkeypatch.setattr(h, "enrich_agent_profile_with_user_settings", enrich)

    async def conv_project(cid):
        rec.add("conv_project", cid)
        return st.project_of_conv

    monkeypatch.setattr(h, "get_conversation_project_id", conv_project)

    async def entity_settings(kind, eid):
        rec.add("entity_settings", kind, eid)
        if st.scopes_error:
            raise st.scopes_error
        return {"kind": kind}

    async def mem_strategy(uid):
        return "auto"

    def bind_rag(scopes, strategy=None):
        rec.add("bind_rag", scopes, strategy)
        return "rag-token"

    monkeypatch.setattr(h, "get_entity_rag_settings", entity_settings)
    monkeypatch.setattr(h, "get_user_memory_strategy", mem_strategy)
    monkeypatch.setattr(h, "bind_user_rag_runtime", bind_rag)
    monkeypatch.setattr(h, "reset_user_rag_runtime", lambda tok: rec.add("reset_rag", tok))
    monkeypatch.setattr(h, "bind_user_model_runtime", lambda ms: (rec.add("bind_model", ms), "model-token")[1])
    monkeypatch.setattr(h, "reset_user_model_runtime", lambda tok: rec.add("reset_model", tok))

    async def recent(max_entries=None, conversation_id=None):
        rec.add("recent_history", max_entries, conversation_id)
        return st.history

    monkeypatch.setattr(h, "get_recent_dialog_history", recent)

    async def owner(cid, uid):
        rec.add("owner", cid, uid)
        return st.owner_ok

    async def project_history(project_id, max_entries=None):
        rec.add("project_history", project_id, max_entries)
        return st.project_history

    async def save_project(*a, **kw):
        rec.add("save_project", *a, **kw)

    monkeypatch.setattr(mem, "verify_conversation_owner", owner)
    monkeypatch.setattr(mem, "set_current_conversation_id", lambda cid: rec.add("set_conv", cid))
    monkeypatch.setattr(mem, "get_project_memory_history", project_history)
    monkeypatch.setattr(mem, "save_dialog_entry_to_project", save_project)
    monkeypatch.setattr(roles, "user_can_use_mcp", lambda user: st.can_use_mcp)
    monkeypatch.setattr(cef, "log_cef_event", lambda *a, **kw: rec.add("cef", *a, **kw))

    import backend.services.inline_images as imgs

    def merge_images(history, current):
        rec.add("merge_images", history, current)
        if st.merge_images_error:
            raise st.merge_images_error
        return list(current) + ["из-истории"]

    monkeypatch.setattr(imgs, "merge_inline_images_from_history", merge_images)

    async def run_chain(*args, **kwargs):
        rec.add("run_chain", *args, **kwargs)
        if st.runner_error:
            raise st.runner_error

    async def multi(*args, **kwargs):
        rec.add("multi", *args, **kwargs)

    monkeypatch.setattr(runner, "run_direct_or_chain", run_chain)
    monkeypatch.setattr(h, "_handle_multi_llm", multi)
    return st


async def send(sio, data, sid="s1"):
    await sio.handlers["chat_message"](sid, data)


class TestAccessChecks:
    async def test_revoked_second_check(self, sio, env, monkeypatch):
        def revoked(token, client_binding_id=None):
            raise RuntimeError("revoked")

        monkeypatch.setattr(h, "decode_token", revoked)
        await send(sio, {"message": "hi"})
        assert sio.emitted[0][0] == "chat_error" and "Сессия завершена" in sio.emitted[0][1]["error"]
        assert sio.disconnected == ["s1"]

    async def test_foreign_conversation_is_refused(self, sio, env, rec):
        env.owner_ok = False
        await send(sio, {"message": "hi", "conversation_id": "c-other"})
        assert sio.emitted == [("chat_error", {"error": "Нет доступа к этому диалогу"}, "s1")]
        assert rec.get("owner")[0][0] == ("c-other", "alice")
        assert rec.get("run_chain") == [] and rec.get("save_dialog_entry") == []
        assert rec.get("set_conv") == []

    async def test_not_dict_payload_becomes_chat_error(self, sio, env):
        await send(sio, "не словарь")
        assert sio.names() == ["chat_error"]
        # текст исключения (AttributeError: ... get) клиенту не отдаётся - общий текст
        assert sio.emitted[0][1]["error"] == "Внутренняя ошибка сервера"
        assert "get" not in sio.emitted[0][1]["error"]


class TestDirectDispatch:
    async def test_happy_path_saves_user_message_and_runs_chain(self, sio, env, rec):
        await send(sio, {
            "message": "привет", "message_id": "m1", "conversation_id": "c1", "request_id": " r1 ",
            "inline_context": " текст ", "inline_images": ["a.png", "", None],
            "inline_attachments": [{"name": "f.txt", "contentType": "text"}],
            "rag_strategy": " HYBRID ", "use_kb_rag": 1, "use_memory_library_rag": 1,
            "project_instructions": "инструкции",
        })
        # Сообщение пользователя сохранено с метаданными вложений и привязкой к пользователю
        (args, kwargs), = rec.get("save_dialog_entry")
        assert args[0] == "user" and args[1] == "привет" and args[3] == "m1" and args[4] == "c1"
        assert args[2]["inline_context"] == "текст" and kwargs == {"user_id": "alice"}
        assert rec.get("set_conv") == [(("c1",), {})]
        (cargs, ckwargs), = rec.get("run_chain")
        assert cargs[0] is sio and cargs[1] == "s1" and cargs[3] == "привет"
        assert cargs[4] is True  # streaming по умолчанию
        assert cargs[5] == "c1" and cargs[6] == env.history
        assert cargs[7] is True and cargs[8] is True
        assert ckwargs["rag_strategy"] == "hybrid" and ckwargs["inline_context"] == "текст"
        assert ckwargs["inline_images"] == ["a.png", "из-истории"]
        assert ckwargs["project_instructions"] == "инструкции" and ckwargs["current_user"] == USER
        assert ckwargs["enable_thinking"] is False and ckwargs["generation_started_at"] > 0
        # Ids стрима сброшены после обработки (finally)
        assert h._stream_conversation_id.get() is None and h._stream_request_id.get() is None
        assert [c[0][0] for c in rec.get("reset_rag")] == ["rag-token"]
        assert h._generation_seq["s1"] == 1

    async def test_unknown_rag_strategy_is_dropped(self, sio, env, rec):
        await send(sio, {"message": "q", "rag_strategy": "magic"})
        assert rec.get("run_chain")[0][1]["rag_strategy"] is None

    @pytest.mark.parametrize("raw,expected", [
        (True, True), (False, False), (0, False), (1, True), (0.0, False),
        ("off", False), (" No ", False), ("", False), ("0", False), ("false", False),
        ("yes", True), ("1", True), (None, False), ([1], True),
    ])
    async def test_enable_thinking_is_normalized(self, sio, env, rec, raw, expected):
        await send(sio, {"message": "q", "enable_thinking": raw})
        assert rec.get("run_chain")[0][1]["enable_thinking"] is expected

    async def test_regenerate_does_not_resave_user_message(self, sio, env, rec):
        await send(sio, {"message": "q", "regenerate": True})
        assert rec.get("save_dialog_entry") == [] and len(rec.get("run_chain")) == 1

    async def test_mongo_error_on_user_save_is_reported(self, sio, env, rec):
        env.save_error = RuntimeError("MongoDB connection lost")
        await send(sio, {"message": "q", "conversation_id": "c9", "request_id": "r9"})
        assert sio.emitted == [("chat_error", {"error": "MongoDB недоступен.", "conversation_id": "c9",
                                               "request_id": "r9"}, "s1")]
        assert rec.get("run_chain") == []

    async def test_other_runtime_error_is_reraised_into_generic_handler(self, sio, env, rec):
        env.save_error = RuntimeError("что-то другое")
        await send(sio, {"message": "q"})
        assert sio.emitted[0][0] == "chat_error" and sio.emitted[0][1]["error"] == "Внутренняя ошибка сервера"
        assert "другое" not in sio.emitted[0][1]["error"]
        assert rec.get("run_chain") == []

    async def test_runner_failure_is_reported_with_ids_and_cleans_up(self, sio, env, rec):
        env.runner_error = ValueError("модель упала")
        await send(sio, {"message": "q", "conversation_id": "c1", "request_id": "r1"})
        name, payload, room = sio.emitted[-1]
        assert name == "chat_error" and room == "s1"
        assert payload == {"error": "Внутренняя ошибка сервера", "conversation_id": "c1", "request_id": "r1"}
        assert h._stream_conversation_id.get() is None
        assert len(rec.get("reset_rag")) == 1

    async def test_project_only_memory_uses_project_history(self, sio, env, rec):
        await send(sio, {"message": "q", "project_id": "p1", "project_memory": "project-only",
                         "conversation_id": "c1"})
        assert rec.get("project_history")[0][0][0] == "p1"
        assert rec.get("recent_history") == []
        assert rec.get("run_chain")[0][0][6] == env.project_history
        # Сообщение пользователя уходит в проектное хранилище
        (args, kwargs), = rec.get("save_project")
        assert args[:3] == ("user", "q", "p1") and kwargs["user_id"] == "alice"
        assert rec.get("save_dialog_entry") == []
        assert rec.get("conv_project") == []

    async def test_project_from_conversation_is_resolved(self, sio, env, rec):
        env.project_of_conv = "p-from-db"
        await send(sio, {"message": "q", "conversation_id": "c1"})
        assert rec.get("conv_project") == [(("c1",), {})]
        assert rec.get("run_chain")[0][1]["project_id"] == "p-from-db"
        assert [c[0] for c in rec.get("entity_settings")] == [("project", "p-from-db")]

    async def test_agent_and_project_settings_are_loaded(self, sio, env, rec):
        env.profile = {"name": "Юрист", "model_path": "llm-svc://m", "file_search_enabled": True,
                       "kb_document_ids": [1, 2]}
        await send(sio, {"message": "q", "project_id": "p1", "agent_id": "7", "project_name": "Проект"})
        assert [c[0] for c in rec.get("entity_settings")] == [("project", "p1"), ("agent", "7")]
        (args, _), = rec.get("bind_rag")
        assert set(args[0]) == {"project", "agent"} and args[1] == "auto"
        cargs, ckwargs = rec.get("run_chain")[0]
        assert cargs[12] is True and cargs[13] == [1, 2]  # use_agent_scoped_kb, agent_kb_doc_ids
        # INT004: вызов агента записан в CEF-аудит
        (cef_args, cef_kwargs), = rec.get("cef")
        assert cef_args == ("INT004",) and cef_kwargs["extra"]["cs1"] == "Юрист" and cef_kwargs["extra"]["cs2"] == "7"

    async def test_agent_id_not_numeric_is_not_audited(self, sio, env, rec):
        await send(sio, {"message": "q", "agent_id": "abc"})
        assert rec.get("cef") == [] and rec.get("entity_settings") == []

    async def test_agent_name_fallbacks_in_audit(self, sio, env, rec):
        await send(sio, {"message": "q", "agent_id": 5, "agent_name": "Из payload"})
        assert rec.get("cef")[0][1]["extra"]["cs1"] == "Из payload"
        await send(sio, {"message": "q", "agent_id": 6})
        assert rec.get("cef")[1][1]["extra"]["cs1"] == "agent-6"

    async def test_non_positive_agent_id_is_not_audited(self, sio, env, rec):
        await send(sio, {"message": "q", "agent_id": 0})
        assert rec.get("cef") == [] and rec.get("entity_settings") == []

    async def test_rag_settings_failure_falls_back_to_empty_scopes(self, sio, env, rec):
        env.scopes_error = RuntimeError("settings svc down")
        await send(sio, {"message": "q", "project_id": "p1"})
        assert rec.get("bind_rag")[-1][0][0] == {}
        assert len(rec.get("run_chain")) == 1

    async def test_history_image_merge_failure_is_swallowed(self, sio, env, rec):
        env.merge_images_error = RuntimeError("bad history")
        await send(sio, {"message": "q", "inline_images": ["x"]})
        assert rec.get("run_chain")[0][1]["inline_images"] == ["x"]

    async def test_users_without_mcp_lose_tool_ids(self, sio, env, rec):
        env.can_use_mcp = False
        data = {"message": "q", "tool_ids": ["t"], "mcp_tool_ids": ["m"]}
        await send(sio, data)
        assert "tool_ids" not in data and "mcp_tool_ids" not in data

    async def test_mcp_role_check_failure_is_ignored(self, sio, env, rec, monkeypatch):
        import backend.auth.agent_platform_roles as roles

        def boom(user):
            raise RuntimeError("ldap down")

        monkeypatch.setattr(roles, "user_can_use_mcp", boom)
        data = {"message": "q", "tool_ids": ["t"]}
        await send(sio, data)
        assert data["tool_ids"] == ["t"] and len(rec.get("run_chain")) == 1

    async def test_agent_card_settings(self, sio, env, rec):
        env.profile = {
            "name": "A", "model_path": "llm-svc://m", "file_search_enabled": False, "kb_document_ids": [],
            "model_settings": {"streaming": False}, "effective_model_settings": {"temperature": 0.1},
            "mcp_enabled": True, "mcp_server_ids": ["9"], "plugins_enabled": True, "plugin_ids": ["pl1"],
        }
        data = {"message": "q"}
        await send(sio, data)
        assert rec.get("bind_model")[0][0] == ({"temperature": 0.1},)
        assert rec.get("run_chain")[0][0][4] is False  # streaming переопределён карточкой
        assert data["tool_ids"] == ["server:mcp:9"]
        assert data["__plugin_ids__"] == ["pl1"]
        assert [c[0][0] for c in rec.get("reset_model")] == ["model-token"]

    async def test_explicit_tool_ids_win_over_agent_mcp(self, sio, env, rec):
        env.profile = {"name": "A", "mcp_enabled": True, "mcp_server_ids": ["9"], "model_path": None,
                       "file_search_enabled": False, "kb_document_ids": []}
        data = {"message": "q", "mcp_tool_ids": ["own"]}
        await send(sio, data)
        assert "tool_ids" not in data

    async def test_artifacts_settings_from_payload_override_profile(self, sio, env, rec):
        await send(sio, {"message": "q", "artifacts_settings": {"artifacts_enabled": True}})
        assert rec.get("run_chain")[0][0][9]["artifacts_enabled"] is True

    async def test_cef_remote_from_session_is_used(self, sio, env, rec, monkeypatch):
        import backend.settings.cef_logger.cef_audit_context as ctxmod

        seen = {}
        monkeypatch.setattr(ctxmod, "cef_audit_set", lambda **kw: seen.update(kw))
        sio.sessions["s1"]["cef_remote"] = {"src": "10.0.0.1"}
        await send(sio, {"message": "q", "conversation_id": "c1"})
        assert seen["socket_remote"] == {"src": "10.0.0.1"} and seen["conversation_id"] == "c1"


class TestMultiLlmDispatch:
    async def test_multi_llm_is_dispatched_and_chain_skipped(self, sio, env, rec):
        env.profile = {"name": "A", "model_path": None, "file_search_enabled": True, "kb_document_ids": [3]}
        await send(sio, {"message": "q", "model_comparison_enabled": True, "agent_id": 3,
                         "use_memory_library_rag": True, "conversation_id": "c1", "enable_thinking": True})
        assert rec.get("run_chain") == []
        (args, kwargs), = rec.get("multi")
        assert args[0] is sio and args[1] == "s1" and args[3] == "q" and args[4] is True
        assert args[5] == "c1" and args[7] is True
        assert args[9] is True and args[10] == [3]
        assert kwargs["models_subset"] is None and kwargs["enable_thinking"] is True
        assert kwargs["current_user"] == USER and kwargs["generation_started_at"] > 0

    async def test_slot_regeneration_limits_models(self, sio, env, rec):
        await send(sio, {"message": "q", "model_comparison_enabled": True, "regenerate": True,
                         "multi_llm_slot_regenerate": " model-b "})
        assert rec.get("multi")[0][1]["models_subset"] == ["model-b"]
        assert rec.get("save_dialog_entry") == []

    async def test_slot_without_regenerate_is_ignored(self, sio, env, rec):
        await send(sio, {"message": "q", "model_comparison_enabled": True, "multi_llm_slot_regenerate": "model-b"})
        assert rec.get("multi")[0][1]["models_subset"] is None


class TestConnectRooms:
    """События генерации идут в комнату пользователя (все вкладки), а не в один сокет."""

    async def test_non_generation_events_stay_on_the_socket(self, sio, env, rec):
        env.runner_error = ValueError("x")
        await send(sio, {"message": "q"})
        assert {room for _, _, room in sio.emitted} == {"s1"}

    async def test_generation_events_go_to_user_room_after_connect(self, sio, monkeypatch):
        monkeypatch.setattr(h, "decode_token_signature_only",
                            lambda token, client_binding_id=None: {"username": "a", "user_id": "alice", "session_id": "s"})
        monkeypatch.setattr(h, "get_client_binding_id_from_cookie_header", lambda c: None)
        await sio.handlers["connect"]("sidA", {}, {"token": "jwt"})
        await sio.handlers["stop_generation"]("sidA", {"request_id": "r1"})
        assert [(n, r) for n, _, r in sio.emitted if n == "generation_stopped"] == [("generation_stopped", "user:alice")]
        await sio.handlers["disconnect"]("sidA")
        await sio.handlers["stop_generation"]("sidA", {"request_id": "r1"})
        assert sio.emitted[-1][2] == "sidA"

    async def test_events_without_stream_ids_stay_on_the_socket(self, sio, monkeypatch):
        # Без id другие вкладки применили бы «стоп» к своей генерации
        monkeypatch.setattr(h, "decode_token_signature_only",
                            lambda token, client_binding_id=None: {"username": "a", "user_id": "alice", "session_id": "s"})
        monkeypatch.setattr(h, "get_client_binding_id_from_cookie_header", lambda c: None)
        await sio.handlers["connect"]("sidA", {}, {"token": "jwt"})
        await sio.handlers["stop_generation"]("sidA", {})
        assert sio.emitted[-1][2] == "sidA"
        assert h._gen_room("sidA") == "sidA"
        h._stream_request_id.set("r2")
        assert h._gen_room("sidA") == "user:alice"

    async def test_connect_joins_user_room(self, sio, monkeypatch):
        monkeypatch.setattr(h, "decode_token_signature_only",
                            lambda token, client_binding_id=None: {"username": "a", "user_id": "alice", "session_id": "s"})
        monkeypatch.setattr(h, "get_client_binding_id_from_cookie_header", lambda c: None)
        await sio.handlers["connect"]("sidA", {}, {"token": "jwt"})
        assert ("sidA", "user:alice") in sio.rooms


class TestConnectExtras:
    async def test_cef_remote_is_saved_in_session(self, sio, monkeypatch):
        monkeypatch.setattr(h, "decode_token_signature_only",
                            lambda token, client_binding_id=None: {"username": "a", "user_id": "alice"})
        monkeypatch.setattr(h, "get_client_binding_id_from_cookie_header", lambda c: None)
        monkeypatch.setattr(h, "cef_socket_remote_from_environ", lambda environ: {"src": "1.2.3.4"})
        await sio.handlers["connect"]("sidA", {"REMOTE_ADDR": "1.2.3.4"}, {"token": "jwt"})
        assert sio.sessions["sidA"]["cef_remote"] == {"src": "1.2.3.4"}

    async def test_ping_failure_is_logged_not_raised(self, sio):
        await sio.handlers["ping"]("s", None)  # data.get -> AttributeError внутри try
        assert sio.emitted == []
