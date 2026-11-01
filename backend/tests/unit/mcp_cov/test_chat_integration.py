"""mcp/chat_integration: allowlist провайдеров, контекст пользователя и точка входа MCP для чата."""

from types import SimpleNamespace

import pytest

from backend.mcp import chat_integration as ci
from backend.mcp import events, policy
from backend.mcp.types import AgentLoopResult, McpCallContext, McpToolInfo

pytestmark = pytest.mark.unit


def settings_with(allowlist):
    return lambda: SimpleNamespace(mcp=SimpleNamespace(llm_provider_allowlist=allowlist))


class TestExtractProviderId:
    @pytest.mark.parametrize(
        "path,expected",
        [
            (None, ""),
            ("", ""),
            ("   ", ""),
            ("llm-svc://corsur/qwen", "corsur"),
            ("LLM-SVC://  /corsur/qwen", "corsur"),
            ("llm-svc://model-only", ""),
            ("phoenix/gpt", "phoenix"),
            ("plain", "plain"),
        ],
    )
    def test_extract(self, path, expected):
        assert ci._extract_provider_id(path) == expected


class TestAllowlist:
    @pytest.mark.parametrize("allowlist", [None, [], ["", "  "]])
    def test_empty_allowlist_allows_everything(self, monkeypatch, allowlist):
        monkeypatch.setattr(ci, "get_settings", settings_with(allowlist))
        assert ci.is_mcp_provider_allowed("any/model") is True

    def test_listed_provider_is_allowed(self, monkeypatch):
        monkeypatch.setattr(ci, "get_settings", settings_with([" corsur ", ""]))
        assert ci.is_mcp_provider_allowed("llm-svc://corsur/m") is True
        assert ci.is_mcp_provider_allowed("corsur/m") is True

    def test_other_provider_is_denied(self, monkeypatch):
        monkeypatch.setattr(ci, "get_settings", settings_with(["corsur"]))
        assert ci.is_mcp_provider_allowed("phoenix/m") is False

    def test_unknown_provider_is_allowed(self, monkeypatch):
        monkeypatch.setattr(ci, "get_settings", settings_with(["corsur"]))
        assert ci.is_mcp_provider_allowed("") is True and ci.is_mcp_provider_allowed(None) is True

    def test_reexports(self):
        assert policy.is_mcp_provider_allowed is ci.is_mcp_provider_allowed


class TestBuildContext:
    def test_full_user(self):
        ctx = ci.build_mcp_context_from_user(
            {"user_id": 7, "username": "ivan", "is_admin": 1, "groups": ["g"], "ldap_groups": ["l"]},
            chat_id="c", message_id="m",
        )
        assert ctx == McpCallContext(user_id="7", username="ivan", chat_id="c", message_id="m", is_admin=True, groups=["g"], ldap_groups=["l"])

    def test_user_id_falls_back_to_username(self):
        assert ci.build_mcp_context_from_user({"username": "ivan"}).user_id == "ivan"

    def test_groups_fall_back_to_each_other(self):
        only_ldap = ci.build_mcp_context_from_user({"username": "a", "ldap_groups": ["l"]})
        only_groups = ci.build_mcp_context_from_user({"username": "a", "groups": ["g"]})
        assert (only_ldap.groups, only_ldap.ldap_groups) == (["l"], ["l"])
        assert (only_groups.groups, only_groups.ldap_groups) == (["g"], ["g"])

    def test_empty_user(self):
        ctx = ci.build_mcp_context_from_user({})
        assert (ctx.user_id, ctx.username, ctx.is_admin, ctx.groups) == ("", "", False, [])


TOOL = McpToolInfo("docs", "read", "mcp_docs_read", "", {})
NATIVE = McpToolInfo("__astra_native__", "subagent", "subagent", "", {})
CTX = McpCallContext(user_id="u1", username="ivan", chat_id="c1")


class FakePlatform:
    def __init__(self, enabled=True, initialized=True):
        self.enabled, self.initialized = enabled, initialized
        self.listed, self.filtered = [], []
        self.fail_for = set()

    def list_enabled_server_ids(self, ids):
        return [i for i in ids if i != "off"]

    async def list_tools_for_server(self, sid, ctx):
        self.listed.append(sid)
        if sid in self.fail_for:
            raise RuntimeError("down")
        return [McpToolInfo(sid, "t", f"mcp_{sid}_t", "", {})]

    def filter_tools_by_context(self, tools, ctx, enabled_server_ids):
        self.filtered.append((list(tools), list(enabled_server_ids)))
        return [t for t in tools if t.server_id != "hidden"]


class FakeLoop:
    def __init__(self):
        self.runs = []
        self.result = AgentLoopResult(content="итог")

    async def run(self, **kw):
        self.runs.append(kw)
        return self.result


@pytest.fixture
def env(monkeypatch):
    state = SimpleNamespace(platform=FakePlatform(), loop=FakeLoop(), phases=[], finished=[], native=[], plan=None, rules=None)
    monkeypatch.setattr(ci, "get_mcp_platform", lambda: state.platform)
    monkeypatch.setattr(ci, "get_mcp_agent_loop", lambda: state.loop)
    monkeypatch.setattr(ci, "get_settings", settings_with(None))
    monkeypatch.setattr(ci, "log_pre_loop", lambda **kw: state.phases.append(kw))
    monkeypatch.setattr(ci, "resolve_recursion_limit", lambda profile: 7)
    monkeypatch.setattr(ci, "describe_limit_source", lambda profile: "из карточки")
    monkeypatch.setattr(ci, "merge_sampling_request_extra", lambda extra: extra)

    async def prepare(**kw):
        state.prepare_kw = kw
        return list(state.native), kw["sub_cfg"]

    async def with_plan(prompt, **kw):
        state.plan = (prompt, kw)
        return f"{prompt}+plan" if prompt else "plan"

    async def finish(result, sub_ctx, rerun=None):
        state.finished.append((result, sub_ctx))
        state.rerun = rerun
        return result

    monkeypatch.setattr(ci, "prepare_parent_tools", prepare)
    monkeypatch.setattr(ci, "with_parent_rules", lambda p, n: f"{p}+rules" if n else p)
    monkeypatch.setattr(ci, "with_parent_plan", with_plan)
    monkeypatch.setattr(ci, "finish_parent_result", finish)
    return state


def run_agent(**kw):
    params = dict(
        tool_ids=["server:mcp:docs"], user_message="привет", history=None, system_prompt="sys",
        model_path="prov/m", mcp_context=CTX,
    )
    params.update(kw)
    return ci.maybe_run_mcp_agent(**params)


class TestMaybeRunMcpAgent:
    async def test_runs_loop_with_mcp_tools(self, env):
        result = await run_agent(temperature=0.2, max_tokens=50, enable_thinking=True, event_callback="cb")
        assert result.content == "итог"
        kw = env.loop.runs[0]
        assert [t.qualified_name for t in kw["mcp_tools"]] == ["mcp_docs_t"] and kw["enabled_server_ids"] == ["docs"]
        assert kw["max_iterations"] == 7 and kw["temperature"] == 0.2 and kw["max_tokens"] == 50
        assert kw["request_extra"] == {"chat_template_kwargs": {"enable_thinking": True}} and kw["event_callback"] == "cb"
        assert [m["role"] for m in kw["messages"]] == ["system", "user"] and kw["messages"][0]["content"] == "sys+plan"
        assert env.phases[-1]["phase"] == "agent_loop_enter" and env.phases[-1]["limit_source"] == "из карточки"
        assert env.finished[0][0] is result

    async def test_explicit_max_iterations(self, env):
        await run_agent(max_iterations=3)
        assert env.loop.runs[0]["max_iterations"] == 3
        assert "явно" in env.phases[-1]["limit_source"]

    async def test_disabled_servers_and_failed_listing_are_skipped(self, env, monkeypatch):
        import backend.mcp.chat_integration as ci

        records = []
        monkeypatch.setattr(ci, "log", SimpleNamespace(exception=lambda *a, **k: records.append(a), debug=lambda *a, **k: None))
        env.platform.fail_for = {"bad"}
        await run_agent(tool_ids=["docs", "bad", "off"])
        assert env.platform.listed == ["docs", "bad"]
        assert env.loop.runs[0]["enabled_server_ids"] == ["docs", "bad"]
        assert records == [("MCP list_tools failed server=%s", "bad")]

    async def test_context_filter_can_hide_every_tool(self, env):
        env.platform.filter_tools_by_context = lambda tools, ctx, enabled_server_ids: []
        assert await run_agent() is None
        assert env.phases[-1]["phase"] == "skip_no_tools" and env.loop.runs == []

    async def test_no_tools_at_all(self, env):
        assert await run_agent(tool_ids=None, agent_profile={"agent_id": "a1"}) is None
        assert env.phases[-1]["phase"] == "skip_no_tools" and env.phases[-1]["agent_id"] == "a1"

    async def test_provider_not_allowed_skips_mcp(self, env, monkeypatch):
        monkeypatch.setattr(ci, "get_settings", settings_with(["corsur"]))
        assert await run_agent(model_path="phoenix/m") is None
        assert env.platform.listed == []

    async def test_platform_not_initialized(self, env):
        env.platform.initialized = False
        assert await run_agent() is None

    async def test_native_tools_run_without_mcp(self, env):
        env.native = [NATIVE]
        env.platform.enabled = False
        result = await run_agent(subagent_ctx="ctx", subagent_config="cfg")
        assert result.content == "итог"
        kw = env.loop.runs[0]
        assert kw["native_tools"] == [NATIVE] and kw["mcp_tools"] == [] and kw["subagent_ctx"] == "ctx"
        assert kw["messages"][0]["content"] == "sys+rules+plan"
        assert env.prepare_kw["user_id"] == "u1" and env.prepare_kw["sub_ctx"] == "ctx"

    async def test_empty_user_id_is_passed_as_none(self, env):
        env.native = [NATIVE]
        await run_agent(mcp_context=McpCallContext(user_id="", username=""))
        assert env.prepare_kw["user_id"] is None

    async def test_rerun_callback_appends_messages(self, env):
        await run_agent(agent_profile={"agent_id": "a"})
        base_len = len(env.loop.runs[0]["messages"])
        await env.rerun([{"role": "user", "content": "уточни"}])
        assert len(env.loop.runs) == 2 and len(env.loop.runs[1]["messages"]) == base_len + 1
        await env.rerun()
        assert len(env.loop.runs[2]["messages"]) == base_len

    async def test_platform_disabled_while_tools_are_listed(self, env):
        """Платформу выключили (перезагрузка конфига) между проверкой enabled и итоговой проверкой."""
        reads = {"n": 0}

        class Flipping(FakePlatform):
            @property
            def enabled(self):
                reads["n"] += 1
                return reads["n"] == 1

            @enabled.setter
            def enabled(self, value):
                pass

        env.platform = Flipping()
        assert await run_agent() is None
        assert env.phases[-1]["phase"] == "skip_mcp_disabled" and env.loop.runs == []

    async def test_disabled_platform_without_tools_reports_no_tools(self, env):
        env.platform.enabled = False
        assert await run_agent() is None
        assert [p["phase"] for p in env.phases] == ["skip_no_tools"]


class TestRunMcpForChat:
    async def test_builds_context_and_delegates(self, env, monkeypatch):
        built = {}

        def build_ctx(profile, **kw):
            built.update(kw, profile=profile)
            return "cfg", "sub-ctx"

        monkeypatch.setattr(ci, "build_parent_run_context", build_ctx)
        user = {"user_id": "u9", "username": "olga"}
        result = await ci.run_mcp_for_chat(
            tool_ids=["docs"], user_message="q", history=[], system_prompt=None, model_path="p/m",
            user=user, chat_id="c", message_id="m", temperature=0.1, max_tokens=9, enable_thinking=True,
            emit_event="emit", agent_profile={"agent_id": "a"}, subagent_executor="exe",
            inline_attachments=["att"], user_question="orig",
        )
        assert result.content == "итог"
        assert built["user"] is user and built["user_id"] == "u9" and built["emit_event"] == "emit"
        assert built["inline_attachments"] == ["att"] and built["user_question"] == "orig" and built["executor"] == "exe"
        kw = env.loop.runs[0]
        assert kw["mcp_context"].user_id == "u9" and kw["mcp_context"].chat_id == "c" and kw["event_callback"] == "emit"
        assert kw["subagent_ctx"] == "sub-ctx" and env.prepare_kw["sub_cfg"] == "cfg"
        assert kw["temperature"] == 0.1 and kw["max_tokens"] == 9


class TestEventsReexport:
    def test_events_module_reexports_emitters(self):
        from backend.mcp import agent_loop

        assert events.emit_mcp_tool_start is agent_loop.emit_mcp_tool_start
        assert events.emit_mcp_tool_end is agent_loop.emit_mcp_tool_end
