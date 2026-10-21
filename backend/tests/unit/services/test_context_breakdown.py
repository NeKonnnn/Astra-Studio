"""context_breakdown: оценка токенов и сегменты скрытого контекста чата."""

from types import SimpleNamespace

import pytest

from backend.services import context_breakdown as cb


@pytest.fixture(autouse=True)
def _clear_cache():
    cb._MCP_TOKENS_CACHE.clear()
    yield
    cb._MCP_TOKENS_CACHE.clear()


class TestEstimateTokens:
    def test_empty(self):
        assert cb.estimate_tokens("") == 0
        assert cb.estimate_tokens(None) == 0

    def test_plain_text_is_len_over_four(self):
        assert cb.estimate_tokens("abcdefgh") == 2

    def test_specials_and_newlines_add_weight(self):
        # 8 символов -> 2; 2 спецсимвола -> 1; 1 перевод строки -> 1
        assert cb.estimate_tokens("ab{}cd\nef") == 3 + 1 + 1
        assert cb.estimate_tokens("привет мир") == 3  # кириллица и пробел - не спецсимволы


class TestEntityContextSize:
    async def test_effective_settings_win(self, monkeypatch):
        async def boom(uid):
            raise AssertionError("не должен вызываться")

        monkeypatch.setattr(cb, "get_user_model_settings", boom)
        assert await cb._entity_context_size("u", {"effective_model_settings": {"context_size": "4096"}}) == 4096

    async def test_effective_without_size_is_none(self):
        assert await cb._entity_context_size("u", {"effective_model_settings": {"context_size": 0}}) is None

    async def test_falls_back_to_user_settings(self, monkeypatch):
        async def user_ms(uid):
            return {"context_size": 2048}

        monkeypatch.setattr(cb, "get_user_model_settings", user_ms)
        assert await cb._entity_context_size("u", None) == 2048

    async def test_garbage_and_errors_give_none(self, monkeypatch):
        assert await cb._entity_context_size("u", {"effective_model_settings": {"context_size": "abc"}}) is None

        async def boom(uid):
            raise RuntimeError("db")

        monkeypatch.setattr(cb, "get_user_model_settings", boom)
        assert await cb._entity_context_size("u", None) is None


class _Tool:
    def __init__(self, name):
        self.qualified_name = name
        self.description = f"описание {name}"
        self.parameters = {"type": "object"}


class FakePlatform:
    def __init__(self, tools=None, enabled=True, initialized=True, fail_for=()):
        self.enabled, self.initialized = enabled, initialized
        self.tools, self.fail_for = tools or {}, set(fail_for)
        self.list_calls = []

    async def list_tools_for_server(self, sid, ctx):
        self.list_calls.append(sid)
        if sid in self.fail_for:
            raise RuntimeError("mcp down")
        return list(self.tools.get(sid, []))

    def filter_tools_by_context(self, tools, ctx, enabled_server_ids=None):
        return tools


@pytest.fixture
def platform(monkeypatch):
    holder = {"p": FakePlatform()}
    monkeypatch.setattr("backend.mcp.platform.get_mcp_platform", lambda: holder["p"])
    monkeypatch.setattr("backend.mcp.chat_integration.build_mcp_context_from_user", lambda user: SimpleNamespace(user=user))
    return holder


class TestMcpTokens:
    async def test_no_tools_or_user(self, platform):
        assert await cb._estimate_mcp_tools_tokens(None, {"user_id": "u"}) == 0
        assert await cb._estimate_mcp_tools_tokens(["mcp:a"], None) == 0

    async def test_platform_disabled_or_not_ready(self, platform):
        platform["p"] = FakePlatform(enabled=False)
        assert await cb._estimate_mcp_tools_tokens(["mcp:a"], {"u": 1}) == 0
        platform["p"] = FakePlatform(initialized=False)
        assert await cb._estimate_mcp_tools_tokens(["mcp:a"], {"u": 1}) == 0

    async def test_no_server_ids(self, platform):
        assert await cb._estimate_mcp_tools_tokens(["  "], {"u": 1}) == 0

    async def test_counts_tools_skips_failing_server_and_caches(self, platform, monkeypatch):
        platform["p"] = FakePlatform({"a": [_Tool("t1")], "b": [_Tool("t2")]}, fail_for={"c"})
        monkeypatch.setattr("backend.mcp.prompt_fc_handler._render_tools_prompt", lambda tools: "x" * 40)
        n = await cb._estimate_mcp_tools_tokens(["mcp:a", "server:mcp:b", "mcp:c"], {"u": 1})
        assert n == 10
        calls = len(platform["p"].list_calls)
        # второй вызов с теми же id (в другом порядке) берётся из кэша
        assert await cb._estimate_mcp_tools_tokens(["server:mcp:b", "mcp:c", "mcp:a"], {"u": 1}) == 10
        assert len(platform["p"].list_calls) == calls

    async def test_cache_expires(self, platform, monkeypatch):
        platform["p"] = FakePlatform({"a": [_Tool("t")]})
        monkeypatch.setattr("backend.mcp.prompt_fc_handler._render_tools_prompt", lambda tools: "x" * 8)
        assert await cb._estimate_mcp_tools_tokens(["mcp:a"], {"u": 1}) == 2
        key = next(iter(cb._MCP_TOKENS_CACHE))
        cb._MCP_TOKENS_CACHE[key] = (99, cb._MCP_TOKENS_CACHE[key][1] - cb._MCP_CACHE_TTL_SEC - 1)
        assert await cb._estimate_mcp_tools_tokens(["mcp:a"], {"u": 1}) == 2

    async def test_empty_tool_list_is_cached_as_zero(self, platform):
        assert await cb._estimate_mcp_tools_tokens(["mcp:a"], {"u": 1}) == 0
        assert list(cb._MCP_TOKENS_CACHE.values())[0][0] == 0

    async def test_render_failure_falls_back_to_json(self, platform, monkeypatch):
        platform["p"] = FakePlatform({"a": [_Tool("t1")]})

        def boom(tools):
            raise RuntimeError("render")

        monkeypatch.setattr("backend.mcp.prompt_fc_handler._render_tools_prompt", boom)
        assert await cb._estimate_mcp_tools_tokens(["mcp:a"], {"u": 1}) > 0


class FakePromptManager:
    def __init__(self, effective="", global_prompt="", boom=False):
        self.effective, self.global_prompt, self.boom = effective, global_prompt, boom
        self.paths = []

    def get_effective_prompt(self, model_path):
        if self.boom:
            raise RuntimeError("x")
        self.paths.append(model_path)
        return self.effective

    def get_global_prompt(self):
        return self.global_prompt


@pytest.fixture
def overhead(monkeypatch):
    state = {
        "profile": {}, "pm": FakePromptManager(), "mcp": 0, "ctx_size": None,
        "skills": None, "artifacts": "",
    }

    async def resolve(agent_id, user_id, user=None):
        state["resolve_args"] = (agent_id, user_id)
        return dict(state["profile"])

    async def enrich(profile, user_id):
        out = dict(profile)
        if state["ctx_size"]:
            out["effective_model_settings"] = {"context_size": state["ctx_size"]}
        return out

    async def get_pm(user_id):
        return state["pm"]

    async def mcp(tool_ids, user):
        return state["mcp"]

    async def inject(**kw):
        state["inject_kw"] = kw
        return state["skills"], None, None, None

    async def user_ms(user_id):
        return {}

    monkeypatch.setattr(cb, "get_user_model_settings", user_ms)
    monkeypatch.setattr(cb, "_resolve_agent_chat_params", resolve)
    monkeypatch.setattr(cb, "enrich_agent_profile_with_user_settings", enrich)
    monkeypatch.setattr(cb, "get_user_prompt_manager", get_pm)
    monkeypatch.setattr(cb, "_estimate_mcp_tools_tokens", mcp)
    monkeypatch.setattr(cb, "rag_system_prompt", "RAG-ПРОМПТ")
    monkeypatch.setattr("backend.services.skills.resolve_and_build_skill_injection", inject)
    monkeypatch.setattr("backend.prompts.artifacts.maybe_artifacts_prompt_for_agent", lambda p: state["artifacts"])
    return state


def _ids(result):
    return [s["id"] for s in result["segments"]]


class TestBuildContextOverhead:
    async def test_empty_context_defaults(self, overhead):
        out = await cb.build_context_overhead(user={"user_id": "u"})
        assert out["segments"] == [] and out["max_tokens"] == 8192
        assert out["entity_context_size"] is None
        assert out["overhead_tokens_active"] == 0 == out["overhead_tokens_configured"]
        assert out["skills_enabled"] is False and out["artifacts_enabled"] is False
        assert overhead["resolve_args"] == (None, "u")

    async def test_project_agent_and_context_segments(self, overhead):
        overhead["profile"] = {"system_prompt": " агент " * 4}
        overhead["pm"] = FakePromptManager(effective="общий промпт", global_prompt="глобальный")
        out = await cb.build_context_overhead(model_path="m/x", project_instructions=" проект ", agent_id=3, user={"user_id": "u"})
        assert _ids(out) == ["project", "agent", "context_instructions"]
        ctx = out["segments"][2]
        # промпт сущности перекрывает общий - сегмент настроен, но не активен
        assert ctx["active"] is False
        assert overhead["pm"].paths == ["m/x"]
        assert out["overhead_tokens_configured"] > out["overhead_tokens_active"]

    async def test_context_prompt_is_active_without_entity_prompts(self, overhead):
        overhead["pm"] = FakePromptManager(global_prompt="глобальный")
        out = await cb.build_context_overhead(user=None)
        (seg,) = out["segments"]
        assert seg["id"] == "context_instructions" and seg["active"] is True
        assert overhead["pm"].paths == []  # без model_path берётся глобальный

    async def test_prompt_manager_failure_is_ignored(self, overhead):
        overhead["pm"] = FakePromptManager(boom=True)
        out = await cb.build_context_overhead(model_path="m")
        assert out["segments"] == []

    async def test_rag_rules_and_mcp(self, overhead):
        overhead["mcp"] = 77
        out = await cb.build_context_overhead(use_kb_rag=True, tool_ids=["mcp:a"], user={"user_id": "u"})
        assert _ids(out) == ["rag_rules", "mcp_tools"]
        assert out["segments"][1]["tokens"] == 77

    async def test_skills_segment(self, overhead):
        overhead["profile"] = {"skills_enabled": True, "skill_ids": ["s1"]}
        overhead["skills"] = "блок навыков " * 5
        out = await cb.build_context_overhead(user={"user_id": "u", "is_admin": True})
        seg = next(s for s in out["segments"] if s["id"] == "skills")
        assert seg["active"] is True and seg["tokens"] > 0
        assert overhead["inject_kw"]["is_admin"] is True and overhead["inject_kw"]["user_id"] == "u"
        assert out["skills_enabled"] is True

    async def test_enabled_skills_without_text_are_inactive(self, overhead):
        overhead["profile"] = {"skills_enabled": True}
        overhead["skills"] = ""
        out = await cb.build_context_overhead(user={"user_id": "u"})
        seg = next(s for s in out["segments"] if s["id"] == "skills")
        assert seg == {"id": "skills", "label": "Скиллы", "tokens": 0, "active": False}

    async def test_skills_failure_is_ignored(self, overhead, monkeypatch):
        overhead["profile"] = {"skills_enabled": True}

        async def boom(**kw):
            raise RuntimeError("x")

        monkeypatch.setattr("backend.services.skills.resolve_and_build_skill_injection", boom)
        out = await cb.build_context_overhead()
        assert "skills" not in _ids(out)

    async def test_artifacts_segment(self, overhead):
        overhead["profile"] = {"artifacts_enabled": True}
        overhead["artifacts"] = "правила артефактов"
        out = await cb.build_context_overhead()
        seg = next(s for s in out["segments"] if s["id"] == "artifacts")
        assert seg["active"] is True and out["artifacts_enabled"] is True

    async def test_artifacts_enabled_but_empty_block(self, overhead):
        overhead["profile"] = {"artifacts_enabled": True}
        out = await cb.build_context_overhead()
        seg = next(s for s in out["segments"] if s["id"] == "artifacts")
        assert seg["tokens"] == 0 and seg["active"] is False

    async def test_entity_context_size_sets_max_tokens(self, overhead):
        overhead["ctx_size"] = 32000
        out = await cb.build_context_overhead()
        assert out["max_tokens"] == 32000 and out["entity_context_size"] == 32000
