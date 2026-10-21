"""realtime/helpers: профиль агента из карточки, KB-поиск по документам агента, мелкие утилиты."""

import logging
from types import SimpleNamespace

import pytest

from backend.realtime import helpers as h


class FakeRepo:
    def __init__(self, agent=None, access=True, boom=False):
        self.agent, self.access, self.boom = agent, access, boom
        self.access_calls = []

    async def user_can_access_agent(self, aid, user_id):
        self.access_calls.append((aid, user_id))
        return self.access

    async def get_agent(self, aid, user_id):
        if self.boom:
            raise RuntimeError("db")
        return self.agent


def agent(config=None, name=" Агент ", system_prompt="Подробная инструкция агента"):
    return SimpleNamespace(name=name, config=config, system_prompt=system_prompt)


@pytest.fixture
def repo(monkeypatch):
    holder = SimpleNamespace(r=FakeRepo(agent({})))
    monkeypatch.setattr("backend.database.init_db.get_agent_repository", lambda: holder.r)
    monkeypatch.setattr("backend.auth.agent_platform_roles.user_can_use_agents", lambda user: True)
    return holder


class TestStructureQuery:
    @pytest.mark.parametrize("text", ["Покажи оглавление", "КАКИЕ ГЛАВЫ в отчёте", "структуру работы дай", "пункт 3.2"])
    def test_yes(self, text):
        assert h._is_structure_query(text) is True

    @pytest.mark.parametrize("text", ["", "  ", "ab", "посчитай выручку", None])
    def test_no(self, text):
        assert h._is_structure_query(text) is False


class TestKbSearch:
    class Client:
        def __init__(self, hits=(), boom=False):
            self.hits, self.boom, self.calls = list(hits), boom, []

        async def kb_search(self, query, k, document_ids, strategy):
            self.calls.append((query, k, document_ids, strategy))
            if self.boom:
                raise RuntimeError("svc-rag")
            return self.hits

    async def test_empty_inputs(self):
        assert await h.kb_search_agent_documents(None, "q", [1]) == []
        assert await h.kb_search_agent_documents(self.Client(), "q", []) == []
        assert await h.kb_search_agent_documents(self.Client(), "q", ["x", None]) == []

    async def test_single_request_with_numeric_ids(self):
        c = self.Client([("a", 1.0, 1, 0)] * 5)
        out = await h.kb_search_agent_documents(c, "q", [1, "2", "bad"], k=3, strategy="deep")
        assert len(out) == 3
        assert c.calls == [("q", 3, [1, 2], "deep")]

    async def test_k_is_at_least_one(self):
        c = self.Client([("a", 1.0, 1, 0)])
        await h.kb_search_agent_documents(c, "q", [1], k=0)
        assert c.calls[0][1] == 1

    async def test_search_error_gives_empty(self):
        assert await h.kb_search_agent_documents(self.Client(boom=True), "q", [1]) == []


class TestResolveAgent:
    async def test_none_and_garbage_ids(self, repo):
        for raw in (None, "abc", object()):
            out = await h._resolve_agent_chat_params(raw)
            assert out["agent_id"] is None and out["kb_document_ids"] == []

    async def test_feature_gate_blocks(self, repo, monkeypatch):
        monkeypatch.setattr("backend.auth.agent_platform_roles.user_can_use_agents", lambda user: False)
        assert (await h._resolve_agent_chat_params(1, "u", user={"user_id": "u"}))["agent_id"] is None

    async def test_gate_error_does_not_block(self, repo, monkeypatch):
        def boom(user):
            raise RuntimeError("x")

        monkeypatch.setattr("backend.auth.agent_platform_roles.user_can_use_agents", boom)
        assert (await h._resolve_agent_chat_params(1, "u", user={"user_id": "u"}))["agent_id"] == 1

    async def test_no_repo_no_access_or_missing_agent(self, repo, monkeypatch):
        monkeypatch.setattr("backend.database.init_db.get_agent_repository", lambda: None)
        assert (await h._resolve_agent_chat_params(1))["agent_id"] is None
        monkeypatch.setattr("backend.database.init_db.get_agent_repository", lambda: repo.r)
        repo.r = FakeRepo(agent({}), access=False)
        assert (await h._resolve_agent_chat_params(1, "u"))["agent_id"] is None
        repo.r = FakeRepo(None)
        assert (await h._resolve_agent_chat_params(1, "u"))["agent_id"] is None

    async def test_skip_access_check(self, repo):
        repo.r = FakeRepo(agent({}), access=False)
        out = await h._resolve_agent_chat_params(1, "u", skip_access_check=True)
        assert out["agent_id"] == 1 and repo.r.access_calls == []

    async def test_repository_error_gives_empty(self, repo):
        repo.r = FakeRepo(boom=True)
        assert (await h._resolve_agent_chat_params(1, "u"))["agent_id"] is None

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("llm-svc://qwen", "llm-svc://qwen"),
            ("1lm-svc://qwen", "llm-svc://qwen"),
            ("org/model", "org/model"),
            ("model.GGUF", "model.GGUF"),
            ("C:\\models\\m", "C:\\models\\m"),
            ("qwen", "llm-svc://qwen"),
            ("", None),
        ],
    )
    async def test_model_path_normalization(self, repo, model, expected):
        repo.r = FakeRepo(agent({"model": model}))
        assert (await h._resolve_agent_chat_params(1))["model_path"] == expected

    async def test_model_path_key_is_alternative(self, repo):
        repo.r = FakeRepo(agent({"model_path": "a/b"}))
        assert (await h._resolve_agent_chat_params(1))["model_path"] == "a/b"

    async def test_model_settings_and_limits(self, repo):
        repo.r = FakeRepo(agent({
            "model_settings": {"output_tokens": "2048", "temperature": "0.3"},
            "max_chain_agents": "4", "max_subagents": 2, "recursion_limit": " 7 ",
        }))
        out = await h._resolve_agent_chat_params(1)
        assert out["max_tokens"] == 2048 and out["temperature"] == 0.3
        assert out["model_settings"]["output_tokens"] == "2048"
        assert out["max_chain_agents"] == 4 and out["max_subagents"] == 2 and out["recursion_limit"] == 7

    async def test_bad_numbers_are_ignored(self, repo):
        repo.r = FakeRepo(agent({
            "model_settings": {"output_tokens": "x", "temperature": "y"},
            "max_chain_agents": -1, "max_subagents": "0", "recursion_limit": 0,
        }))
        out = await h._resolve_agent_chat_params(1)
        assert out["max_tokens"] is None and out["temperature"] is None
        assert "max_chain_agents" not in out and "max_subagents" not in out and out["recursion_limit"] is None

    async def test_string_recursion_limit_zero(self, repo):
        repo.r = FakeRepo(agent({"recursion_limit": "0"}))
        assert (await h._resolve_agent_chat_params(1))["recursion_limit"] is None

    async def test_system_prompt_placeholder_is_dropped(self, repo):
        repo.r = FakeRepo(agent({}, system_prompt=h.AGENT_PROMPT_PLACEHOLDER))
        assert (await h._resolve_agent_chat_params(1))["system_prompt"] is None
        repo.r = FakeRepo(agent({}, system_prompt="  настоящий промпт  "))
        assert (await h._resolve_agent_chat_params(1))["system_prompt"] == "настоящий промпт"
        repo.r = FakeRepo(agent({}, name="  "))
        assert (await h._resolve_agent_chat_params(1))["name"] is None

    async def test_kb_skills_mcp_plugins(self, repo):
        repo.r = FakeRepo(agent({
            "file_search_enabled": 1, "kb_document_ids": ["3", 1, "x", 3],
            "skills": [" a ", "", "b"], "mcp_enabled": True, "mcp_server_ids": [" s1 ", ""],
            "plugin_ids": ["p1", " "], "artifacts_enabled": 1, "shadcn_enabled": 1,
            "user_prompt_mode": 1, "hide_sequential_outputs": 1, "shared_chain_rag": 1,
            "use_parent_shared_rag": False, "subagents": {"enabled": True},
        }))
        out = await h._resolve_agent_chat_params(1)
        assert out["file_search_enabled"] is True and out["kb_document_ids"] == [1, 3]
        assert out["skill_ids"] == ["a", "b"] and out["skills_enabled"] is True  # из наличия списка
        assert out["mcp_server_ids"] == ["s1"] and out["plugin_ids"] == ["p1"] and out["plugins_enabled"] is True
        assert out["artifacts_enabled"] and out["shadcn_enabled"] and out["user_prompt_mode"]
        assert out["hide_sequential_outputs"] and out["shared_chain_rag"]
        assert out["use_parent_shared_rag"] is False and out["subagents"] == {"enabled": True}

    async def test_explicit_flags_win_over_lists(self, repo):
        repo.r = FakeRepo(agent({"skill_ids": ["a"], "skills_enabled": False, "plugin_ids": ["p"], "plugins_enabled": False}))
        out = await h._resolve_agent_chat_params(1)
        assert out["skills_enabled"] is False and out["plugins_enabled"] is False

    async def test_defaults_for_old_cards(self, repo):
        repo.r = FakeRepo(agent(None))
        out = await h._resolve_agent_chat_params(1)
        assert out["use_parent_shared_rag"] is True and out["subagents"] is None
        assert out["kb_document_ids"] == [] and out["skills_enabled"] is False

    async def test_chain_ids_exclude_self(self, repo):
        repo.r = FakeRepo(agent({"agent_ids": [1, 2, "3"]}))
        assert 1 not in (await h._resolve_agent_chat_params(1))["agent_ids"]


class TestSmallHelpers:
    def test_merge_artifacts_settings(self):
        base = {"artifacts_enabled": False, "x": 1}
        assert h.merge_chat_artifacts_settings(base, {}) is base
        assert h.merge_chat_artifacts_settings(base, None) is base
        out = h.merge_chat_artifacts_settings(base, {"artifacts_settings": {"artifacts_enabled": 1, "shadcn_enabled": 0, "user_prompt_mode": 1, "ignored": 5}})
        assert out == {"artifacts_enabled": True, "x": 1, "shadcn_enabled": False, "user_prompt_mode": True}
        assert base["artifacts_enabled"] is False  # исходный не мутирован
        assert h.merge_chat_artifacts_settings(None, {"artifacts_settings": {"shadcn_enabled": 1}}) == {"shadcn_enabled": True}

    def test_mcp_tool_ids(self):
        assert h.agent_mcp_tool_ids(None) == []
        assert h.agent_mcp_tool_ids({"mcp_enabled": False, "mcp_server_ids": ["a"]}) == []
        assert h.agent_mcp_tool_ids({"mcp_enabled": True, "mcp_server_ids": "a"}) == []
        assert h.agent_mcp_tool_ids({"mcp_enabled": True, "mcp_server_ids": ["a", " a ", "", "b"]}) == ["server:mcp:a", "server:mcp:b"]

    def test_plugin_ids(self):
        assert h.agent_plugin_ids(None) == []
        assert h.agent_plugin_ids({"plugin_ids": ["a", " "]}) == ["a"]  # флага нет - по наличию
        assert h.agent_plugin_ids({"plugins_enabled": False, "plugin_ids": ["a"]}) == []
        assert h.agent_plugin_ids({"plugins_enabled": True, "plugin_ids": "a"}) == []


class TestBanner:
    def _state(self, monkeypatch, **over):
        st = SimpleNamespace(
            get_current_model_path=lambda: "cur/model",
            get_model_info=lambda: {"loaded": True},
            model_settings=SimpleNamespace(get_all=lambda: {"temperature": 0.7}),
            context_prompt_manager=SimpleNamespace(get_global_prompt=lambda: "г" * 600),
        )
        for k, v in over.items():
            setattr(st, k, v)
        monkeypatch.setattr("backend.app_state.get_current_model_path", st.get_current_model_path, raising=False)
        monkeypatch.setattr("backend.app_state.get_model_info", st.get_model_info, raising=False)
        monkeypatch.setattr("backend.app_state.model_settings", st.model_settings, raising=False)
        monkeypatch.setattr("backend.app_state.context_prompt_manager", st.context_prompt_manager, raising=False)

    def _call(self, caplog, **kw):
        caplog.set_level(logging.DEBUG, logger=h.logger.name)
        h._terminal_chat_inference_banner(sid="s" * 30, conversation_id="c1", user_preview="вопрос", mode_label="direct", **kw)
        return caplog.text

    def test_full_banner(self, monkeypatch, caplog):
        self._state(monkeypatch)
        text = self._call(caplog, extra_line="доп", enable_thinking=True)
        assert "Режим: direct" in text and "'cur/model'" in text
        assert "enable_thinking" in text and "True" in text
        assert '"loaded": true' in text and '"temperature": 0.7' in text
        assert "600 симв." in text and "…" in text and "доп" in text

    def test_explicit_model_and_no_thinking_flag(self, monkeypatch, caplog):
        self._state(monkeypatch)
        text = self._call(caplog, model_path_for_call="llm-svc://x")
        assert "'llm-svc://x'" in text and "не передан в баннер" in text

    def test_missing_state_parts(self, monkeypatch, caplog):
        self._state(monkeypatch, get_model_info=None, model_settings=None, context_prompt_manager=None)
        text = self._call(caplog)
        assert "модуль недоступен" in text and "менеджер недоступен" in text

    def test_failures_are_reported_in_banner(self, monkeypatch, caplog):
        def boom():
            raise RuntimeError("x")

        self._state(
            monkeypatch,
            get_model_info=boom,
            model_settings=SimpleNamespace(get_all=boom),
            context_prompt_manager=SimpleNamespace(get_global_prompt=boom),
        )
        text = self._call(caplog)
        assert "get_model_info: ошибка" in text and "Настройки модели: недоступны" in text and "Глобальный промпт: ошибка" in text

    def test_silent_when_debug_disabled(self, monkeypatch, caplog):
        self._state(monkeypatch)
        caplog.set_level(logging.INFO, logger=h.logger.name)
        h._terminal_chat_inference_banner(sid="s", conversation_id=1, user_preview="", mode_label="m")
        assert "Генерация ответа" not in caplog.text
