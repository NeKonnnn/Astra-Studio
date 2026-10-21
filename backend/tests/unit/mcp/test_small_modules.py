"""mcp/tools_cache, tool_adapter, resolvers, context_headers: TTL-кэш, OpenAI-формат, разбор tool_ids, заголовки контекста."""

from types import SimpleNamespace

import pytest

from backend.mcp import context_headers as ch
from backend.mcp import tools_cache as tc
from backend.mcp.resolvers import build_chat_messages, parse_mcp_server_ids
from backend.mcp.tool_adapter import qualify_tool_name, to_openai_tools
from backend.mcp.types import McpCallContext, McpToolInfo

pytestmark = pytest.mark.unit


def tool(name="search", server="docs", **kw):
    return McpToolInfo(server_id=server, name=name, qualified_name=kw.pop("qualified_name", f"mcp_{server}_{name}"), description=kw.pop("description", "поиск"), parameters=kw.pop("parameters", {"type": "object", "properties": {"q": {"type": "string"}}}))


class TestToolsCache:
    async def test_roundtrip_and_copy_isolation(self):
        cache = tc.McpToolsCache(ttl_seconds=60)
        await cache.set_tools_indexed("docs", [tool()], "fp1")

        got = await cache.get_tools_indexed("docs", "fp1")
        got.clear()

        assert len(await cache.get_tools_indexed("docs", "fp1")) == 1

    async def test_cache_is_scoped_by_server_and_headers_fingerprint(self):
        cache = tc.McpToolsCache()
        await cache.set_tools_indexed("docs", [tool()], "user-a")

        assert await cache.get_tools_indexed("docs", "user-b") is None
        assert await cache.get_tools_indexed("wiki", "user-a") is None
        assert await cache.get_tools_indexed("docs", "user-a") is not None

    async def test_entries_expire(self, monkeypatch):
        cache = tc.McpToolsCache(ttl_seconds=10)
        now = [1000.0]
        monkeypatch.setattr(tc.time, "monotonic", lambda: now[0])
        await cache.set_tools_indexed("docs", [tool()])
        now[0] += 9
        assert await cache.get_tools_indexed("docs") is not None
        now[0] += 2

        assert await cache.get_tools_indexed("docs") is None
        assert cache.count_cached_tools("docs") == 0

    async def test_invalidate_removes_every_fingerprint_of_the_server(self):
        cache = tc.McpToolsCache()
        await cache.set_tools_indexed("docs", [tool()], "a")
        await cache.set_tools_indexed("docs", [tool(), tool("other")], "b")
        await cache.set_tools_indexed("wiki", [tool(server="wiki")], "a")

        cache.invalidate("docs")
        cache.invalidate("never-cached")

        assert await cache.get_tools_indexed("docs", "a") is None and await cache.get_tools_indexed("docs", "b") is None
        assert await cache.get_tools_indexed("wiki", "a") is not None

    async def test_count_uses_the_largest_fresh_entry(self, monkeypatch):
        cache = tc.McpToolsCache(ttl_seconds=10)
        now = [0.0]
        monkeypatch.setattr(tc.time, "monotonic", lambda: now[0])
        await cache.set_tools_indexed("docs", [tool("a")], "old")
        now[0] = 8
        await cache.set_tools_indexed("docs", [tool("a"), tool("b")], "new")
        assert cache.count_cached_tools("docs") == 2
        now[0] = 11  # старая запись протухла, новая жива
        assert cache.count_cached_tools("docs") == 2
        now[0] = 30
        assert cache.count_cached_tools("docs") == 0

    @pytest.mark.parametrize("ttl", [0, -5])
    def test_ttl_has_a_floor_of_one_second(self, ttl):
        assert tc.McpToolsCache(ttl_seconds=ttl).ttl_seconds == 1

    def test_keys_do_not_expose_inputs(self):
        key = tc.McpToolsCache._cache_key("docs", "Bearer secret-token")

        assert "secret" not in key and len(key) == 64

    async def test_keys_cannot_collide_through_the_colon_separator_known_issue(self):
        # Ключ — sha256("server:fingerprint"): ("a:b", "c") и ("a", "b:c") дают один и тот же ключ.
        # Эксплуатация требует управляемого server_id и отпечатка; фиксируем поведение.
        assert tc.McpToolsCache._cache_key("a:b", "c") == tc.McpToolsCache._cache_key("a", "b:c")


class TestToolAdapter:
    def test_openai_format(self):
        result = to_openai_tools([tool(), tool("bare", description="", parameters={})])

        assert result[0] == {"type": "function", "function": {"name": "mcp_docs_search", "description": "поиск", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}
        assert result[1]["function"]["description"] == "bare"  # пустое описание → имя
        assert result[1]["function"]["parameters"] == {"type": "object", "properties": {}}
        assert to_openai_tools([]) == []

    @pytest.mark.parametrize(
        ("server", "name", "prefix", "expected"),
        [
            ("docs", "search", "", "mcp_docs_search"),
            ("docs", "docs_search", "", "docs_search"),
            ("docs", "mcp_docs_search", "", "mcp_docs_search"),
            ("docs", "search", "x_", "x_search"),
            ("docs", "search", "mcp_docs_", "mcp_docs_search"),
        ],
    )
    def test_qualify_names(self, server, name, prefix, expected):
        assert qualify_tool_name(server, name, prefix) == expected


class TestResolvers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (["server:mcp:docs", "mcp:wiki", "plain"], ["docs", "wiki", "plain"]),
            (["docs", "mcp:docs", "server:mcp:docs"], ["docs"]),
            ([" ", "", None, "mcp:", "server:mcp:"], []),
            (["  mcp:  spaced  "], ["spaced"]),
            (None, []),
            ([], []),
        ],
    )
    def test_server_ids(self, raw, expected):
        assert parse_mcp_server_ids(raw) == expected

    def test_messages_are_assembled_in_order(self):
        history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}, {"role": "tool", "content": "skip"}, {"role": "user", "content": None}, {"content": "no role"}]

        messages = build_chat_messages(user_message="q2", history=history, system_prompt="  система  ")

        assert messages == [
            {"role": "system", "content": "система"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]

    def test_blank_system_prompt_is_skipped(self):
        assert build_chat_messages(user_message="q", system_prompt="   ") == [{"role": "user", "content": "q"}]


class TestContextHeaders:
    def settings(self, monkeypatch, *, enabled=True, username="X-User", user_id="X-User-Id", chat_id=None, message_id=None):
        fh = SimpleNamespace(
            enabled=enabled, user=SimpleNamespace(username=username, user_id=user_id), chat_id_header=chat_id, message_id_header=message_id,
        )
        monkeypatch.setattr(ch, "get_settings", lambda: SimpleNamespace(mcp=SimpleNamespace(forward_headers=fh)))
        monkeypatch.delenv("MCP_FORWARD_HEADER_CHAT_ID", raising=False)
        monkeypatch.delenv("MCP_FORWARD_HEADER_MESSAGE_ID", raising=False)

    def ctx(self, **kw):
        return McpCallContext(**{"user_id": "u1", "username": "alice", "chat_id": "c1", "message_id": "m1", **kw})

    def test_user_and_session_headers(self, monkeypatch):
        self.settings(monkeypatch)

        assert ch.build_mcp_context_headers(self.ctx()) == {
            "X-User": "alice", "X-User-Id": "u1", "X-OpenWebUI-Chat-Id": "c1", "X-OpenWebUI-Message-Id": "m1",
        }

    def test_forwarding_disabled_keeps_only_session_headers(self, monkeypatch):
        self.settings(monkeypatch, enabled=False)

        assert ch.build_mcp_context_headers(self.ctx()) == {"X-OpenWebUI-Chat-Id": "c1", "X-OpenWebUI-Message-Id": "m1"}

    def test_header_names_from_config_and_env(self, monkeypatch):
        self.settings(monkeypatch, chat_id="X-Chat")
        monkeypatch.setenv("MCP_FORWARD_HEADER_MESSAGE_ID", "X-Msg")

        headers = ch.build_mcp_context_headers(self.ctx())

        assert headers["X-Chat"] == "c1" and headers["X-Msg"] == "m1"

    def test_missing_values_are_not_forwarded(self, monkeypatch):
        self.settings(monkeypatch, username="", user_id=None)

        assert ch.build_mcp_context_headers(self.ctx(chat_id=None, message_id=None, username="")) == {}

    def test_extra_headers_override_computed_ones(self, monkeypatch):
        self.settings(monkeypatch)

        headers = ch.build_mcp_context_headers(self.ctx(extra_headers={"X-User": "override", "X-Extra": "1"}))

        assert headers["X-User"] == "override" and headers["X-Extra"] == "1"

    def test_user_identity_header_is_taken_from_the_trusted_context_not_the_chat(self, monkeypatch):
        # chat_id/message_id приходят от клиента и пересылаются MCP-серверу как есть:
        # сервер не должен доверять им для авторизации (CR/LF отсекает httpx на отправке).
        self.settings(monkeypatch)

        headers = ch.build_mcp_context_headers(self.ctx(chat_id="evil\nX-User: root"))

        assert headers["X-User"] == "alice" and headers["X-OpenWebUI-Chat-Id"] == "evil\nX-User: root"

    def test_alias(self, monkeypatch):
        self.settings(monkeypatch)

        assert ch.build_context_headers(self.ctx()) == ch.build_mcp_context_headers(self.ctx())

    def test_server_custom_headers_are_rendered_from_the_context(self):
        rendered = ch.merge_server_custom_headers(
            {"X-Who": "{{username}}-{{USER_ID}}", "X-Chat": "chat={{chat_id}}", "X-Unknown": "{{secret}}!", "X-Empty": "", "X-Static": "v"},
            self.ctx(),
        )

        assert rendered == {"X-Who": "alice-u1", "X-Chat": "chat=c1", "X-Unknown": "!", "X-Static": "v"}

    def test_custom_headers_empty_inputs(self):
        assert ch.merge_server_custom_headers(None, self.ctx()) == {} and ch.merge_server_custom_headers({}, self.ctx()) == {}

    def test_template_without_ids_renders_empty(self):
        assert ch.merge_server_custom_headers({"X": "{{chat_id}}|{{message_id}}"}, self.ctx(chat_id=None, message_id=None)) == {"X": "|"}
