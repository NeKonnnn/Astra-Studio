"""mcp/platform: McpPlatformService — маршрутизация имён инструментов, фильтрация, статусы, verify, кеш инструментов."""

from types import SimpleNamespace

import pytest

from backend.mcp import platform as plat
from backend.mcp.exceptions import McpServerNotFoundError
from backend.mcp.types import McpCallContext, McpToolInfo

pytestmark = pytest.mark.unit

CTX = McpCallContext(user_id="u1", username="ivan")


def srv(sid="docs", prefix="", **kw):
    base = dict(
        id=sid, display_name=sid.upper(), enabled=True, transport="http", auth_type="none", auth_mode="shared",
        credential_provider=None, tool_name_prefix=prefix, base_url="http://x", health_path="healthz",
        base_path="/mcp", command=None, args=[], cwd=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class FakeRegistry:
    def __init__(self, servers, enabled=True):
        self.servers = {s.id: s for s in servers}
        self.enabled = enabled
        self.initialized = False
        self.config = SimpleNamespace(
            tools_cache_ttl_seconds=60, session_pool_enabled=False,
            pool=SimpleNamespace(acquire_timeout_seconds=5),
        )

    def initialize(self):
        self.initialized = True

    def list_servers(self):
        return list(self.servers.values())

    def list_enabled_servers(self):
        return [s for s in self.servers.values() if s.enabled]

    def require_server(self, sid):
        if sid not in self.servers:
            raise McpServerNotFoundError(sid)
        return self.servers[sid]


class FakeSession:
    def __init__(self, specs=None, error=None):
        self.specs, self.error, self.calls = specs if specs is not None else [], error, []

    async def list_tools(self):
        if self.error:
            raise self.error
        return self.specs

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return {"ok": name}

    async def list_resources(self, cursor=None):
        return [{"uri": "r", "cursor": cursor}]

    async def read_resource(self, uri):
        return {"contents": [{"uri": uri, "text": "hello"}]}


class FakeManager:
    instances = []
    session = None
    probe = (True, "")
    broken_servers = set()

    def __init__(self, settings, registry, pool=None):
        self.pool, self.connects, self.disconnected = pool, [], 0
        self.session = FakeManager.session
        self.probe = FakeManager.probe
        FakeManager.instances.append(self)

    async def connect_server(self, sid, context, **kw):
        self.connects.append((sid, kw))
        if sid in FakeManager.broken_servers:
            raise RuntimeError(f"{sid} down")
        if isinstance(self.session, Exception):
            raise self.session
        return self.session

    async def connect_servers(self, ids, context):
        return {sid: self.session for sid in ids}

    async def disconnect_all(self):
        self.disconnected += 1

    async def probe_http_health(self, server):
        return self.probe

    async def create_pooled_session(self, sid, **kw):
        return ("pooled", sid, kw)


class FakeCache:
    def __init__(self):
        self.store, self.counts = {}, {}

    async def get_tools_indexed(self, sid, fp):
        return self.store.get((sid, fp))

    async def set_tools_indexed(self, sid, tools, fp):
        self.store[(sid, fp)] = tools

    def count_cached_tools(self, sid):
        return self.counts.get(sid, 0)


class Pool:
    def __init__(self, *a, **k):
        self.started = self.stopped = False
        self.connect_factory = k.get("connect_factory")

    def start(self):
        self.started = True

    async def shutdown(self):
        self.stopped = True

    def get_metrics(self):
        return {"size": 2}


@pytest.fixture
def service(monkeypatch):
    FakeManager.instances, FakeManager.session = [], FakeSession()
    FakeManager.probe, FakeManager.broken_servers = (True, ""), set()
    monkeypatch.setattr(plat, "McpSessionManager", FakeManager)
    monkeypatch.setattr(plat, "McpSessionPool", Pool)
    monkeypatch.setattr(plat, "has_mcp_server_access", lambda s, c: s.id != "secret")
    monkeypatch.setattr(plat, "is_server_allowed", lambda s, c: s.id != "secret")
    monkeypatch.setattr(plat, "filter_tools", lambda tools, server, context, enabled_tool_names=None: [
        t for t in tools if enabled_tool_names is None or t.name in enabled_tool_names
    ])
    s = plat.McpPlatformService.__new__(plat.McpPlatformService)
    s._settings = SimpleNamespace(resolve_mcp_server_base_url=lambda sid: f"http://resolved-{sid}")
    s._registry = FakeRegistry([srv("docs", prefix="docs_"), srv("math"), srv("secret"), srv("off", enabled=False)])
    s._session_manager = FakeManager(s._settings, s._registry)
    s._pool, s._tools_cache, s._initialized = None, FakeCache(), False
    return s


class FakeProvider:
    async def health_metadata(self, server, context):
        return {"mode": "none"}


@pytest.fixture
def provider(monkeypatch):
    import backend.mcp.credentials.base as base

    monkeypatch.setattr(base, "get_credential_provider", lambda name: FakeProvider())


class TestToolNameRouting:
    @pytest.mark.parametrize(
        "server,tool,raw",
        [
            ("docs", "docs_search", "search"),
            ("docs", "mcp_docs_search", "search"),
            ("math", "mcp_math_add", "add"),
            ("math", "add", "add"),
            ("math", "mcp_other_add", "mcp_other_add"),
        ],
    )
    def test_resolve_raw_name(self, service, server, tool, raw):
        assert service._resolve_raw_tool_name(server, tool) == raw

    def test_unknown_server(self, service):
        with pytest.raises(McpServerNotFoundError):
            service._resolve_raw_tool_name("ghost", "x")

    async def test_call_tool_uses_the_raw_name(self, service):
        session = FakeSession()
        assert await service.call_tool("math", "mcp_math_add", {"a": 1}, CTX, session) == {"ok": "add"}
        await service.call_tool("math", "mcp_math_add", None, CTX, session)
        assert session.calls == [("add", {"a": 1}), ("add", {})]

    def test_normalize_specs(self, service):
        tools = service._normalize_tool_specs(service._registry.servers["docs"], [{"name": "search", "description": "d"}, {"name": None}])
        assert tools[0].qualified_name == "docs_search" and tools[0].parameters == {"type": "object", "properties": {}}
        assert tools[1].name == "" and tools[0].raw == {"name": "search", "description": "d"}


class TestListing:
    def test_enabled_ids_keep_the_candidate_order(self, service):
        assert service.list_enabled_server_ids(["math", "off", "ghost", "docs"]) == ["math", "docs"]

    def test_public_listing_hides_inaccessible_servers(self, service):
        assert {s["id"] for s in service.list_servers_public()} == {"docs", "math", "secret", "off"}
        listed = service.list_servers_public(CTX)
        assert {s["id"] for s in listed} == {"docs", "math", "off"}
        assert listed[0]["display_name"] == "DOCS" and listed[0]["tool_name_prefix"] == "docs_"

    def test_filter_by_context_respects_enabled_servers_and_tools(self, service):
        tools = [
            McpToolInfo("docs", "a", "docs_a", "", {}), McpToolInfo("math", "b", "mcp_math_b", "", {}),
            McpToolInfo("math", "c", "mcp_math_c", "", {}), McpToolInfo("off", "d", "mcp_off_d", "", {}),
        ]
        assert [t.name for t in service.filter_tools_by_context(tools, CTX)] == ["a", "b", "c"]
        assert [t.name for t in service.filter_tools_by_context(tools, CTX, enabled_server_ids=["math"])] == ["b", "c"]
        assert [t.name for t in service.filter_tools_by_context(tools, CTX, enabled_tool_names={"c", "a"})] == ["a", "c"]


class TestLifecycle:
    async def test_initialize_builds_cache_and_pool_once(self, service, monkeypatch):
        import backend.mcp.credentials.store as store

        indexes = []

        async def ensure():
            indexes.append(1)

        monkeypatch.setattr(store, "ensure_indexes", ensure)
        monkeypatch.setattr(plat, "McpToolsCache", lambda ttl: ("cache", ttl))
        service._registry.config.session_pool_enabled = True
        service._registry.servers["cmd"] = srv("cmd", transport="stdio", command="python", args=["-m", "x"], cwd="/tmp")
        assert service.initialized is False and service.enabled is True
        await service.initialize()
        await service.initialize()
        assert service.initialized is True and service._registry.initialized and indexes == [1]
        assert service._tools_cache == ("cache", 60) and service._pool.started and service._session_manager._pool is service._pool
        assert await service._pool_connect_factory("docs", x=1) == ("pooled", "docs", {"x": 1})

    async def test_initialize_without_pool_and_with_failing_indexes(self, service, monkeypatch):
        import backend.mcp.credentials.store as store

        async def broken():
            raise RuntimeError("mongo down")

        monkeypatch.setattr(store, "ensure_indexes", broken)
        monkeypatch.setattr(plat, "McpToolsCache", lambda ttl: FakeCache())
        await service.initialize()
        assert service.initialized and service._pool is None

    async def test_disabled_registry_skips_pool_and_logging(self, service, monkeypatch):
        import backend.mcp.credentials.store as store

        async def ok():
            pass

        monkeypatch.setattr(store, "ensure_indexes", ok)
        monkeypatch.setattr(plat, "McpToolsCache", lambda ttl: FakeCache())
        service._registry.enabled = False
        await service.initialize()
        assert service._pool is None and service.enabled is False

    async def test_shutdown(self, service):
        pool = Pool()
        service._pool, service._initialized = pool, True
        await service.shutdown()
        assert pool.stopped and service._pool is None and service.initialized is False
        await service.shutdown()

    def test_pool_metrics(self, service):
        assert service.get_pool_metrics() == {"enabled": False}
        service._pool = Pool()
        assert service.get_pool_metrics() == {"size": 2, "enabled": True}


class TestStatus:
    async def test_disabled_platform(self, service):
        service._registry.enabled = False
        status = await service.get_aggregate_status()
        assert status.enabled is False and status.message == "MCP disabled" and status.servers_total == 0

    async def test_http_health_failure_marks_the_server_disconnected(self, service, provider):
        service._session_manager.probe = (False, "503")
        st = await service.get_server_status("docs", CTX)
        assert st.connected is False and st.error == "HTTP health check failed: 503" and st.metadata == {"mode": "none"}
        service._session_manager.probe = (False, "")
        assert (await service.get_server_status("docs", CTX)).error == "HTTP health check failed"

    async def test_connected_server_reports_cached_tool_count(self, service, provider):
        service._tools_cache.counts["docs"] = 4
        st = await service.get_server_status("docs", CTX)
        assert st.connected is True and st.tools == 4 and st.latency_ms >= 0 and st.display_name == "DOCS"

    async def test_fetch_tools_counts_live_tools_and_reports_failures(self, service, provider):
        FakeManager.session = FakeSession([{"name": "a"}, {"name": "b"}])
        assert (await service.get_server_status("math", CTX, fetch_tools=True)).tools == 2
        # fetch_tools читает кеш инструментов (use_cache=True): повторный «живой» опрос кеш не обходит
        assert (await service.get_server_status("math", CTX, fetch_tools=True)).tools == 2
        service._tools_cache.store.clear()
        FakeManager.session = RuntimeError("tool listing failed")
        st = await service.get_server_status("math", CTX, fetch_tools=True)
        assert st.connected is False and st.error == "tool listing failed"

    async def test_aggregate_status_counts_enabled_servers(self, service, provider):
        service._tools_cache.counts.update({"docs": 3, "math": 1})
        agg = await service.get_aggregate_status()
        assert (agg.servers_total, agg.servers_connected, agg.tools_total) == (3, 3, 4)
        only = await service.get_aggregate_status(server_ids=["math"])
        assert [s.server_id for s in only.servers] == ["math"]

    async def test_health_summary(self, service, provider):
        service._pool = Pool()
        health = await service.health(CTX, server_ids=["docs"])
        assert health["enabled"] and health["pool"] == {"size": 2} and health["servers"][0]["id"] == "docs"
        service._pool = None
        assert (await service.health())["pool"] == {}


class TestVerify:
    async def test_successful_verification(self, service):
        FakeManager.session = FakeSession([{"name": "a"}, {"name": "b"}])
        result = await service.verify_server("docs", CTX)
        assert result.success and result.tools_count == 2 and result.tools == ["a", "b"]
        mgr = FakeManager.instances[-1]
        assert mgr.connects[0][1] == {"use_pool": False, "ephemeral": True} and mgr.disconnected == 1

    async def test_failure_is_returned_not_raised(self, service):
        FakeManager.session = FakeSession(error=ConnectionError("refused"))
        result = await service.verify_server("docs", CTX)
        assert result.success is False and result.error == "refused" and FakeManager.instances[-1].disconnected == 1

    async def test_unknown_server_raises(self, service):
        with pytest.raises(McpServerNotFoundError):
            await service.verify_server("ghost", CTX)


class TestToolsListing:
    async def test_lists_filters_and_caches(self, service):
        FakeManager.session = FakeSession([{"name": "add", "description": "sum"}])
        tools = await service.list_tools_for_server("math", CTX)
        assert [t.qualified_name for t in tools] == ["mcp_math_add"]
        FakeManager.session = RuntimeError("must come from cache")
        assert [t.name for t in await service.list_tools_for_server("math", CTX)] == ["add"]

    async def test_cache_can_be_bypassed(self, service):
        FakeManager.session = FakeSession([{"name": "one"}])
        await service.list_tools_for_server("math", CTX)
        FakeManager.session = FakeSession([{"name": "two"}])
        assert [t.name for t in await service.list_tools_for_server("math", CTX, use_cache=False)] == ["two"]

    async def test_ephemeral_sessions_do_not_use_the_pool(self, service):
        await service.list_tools_for_server("math", CTX, ephemeral=True)
        mgr = FakeManager.instances[-1]
        assert mgr.connects[0][1] == {"use_pool": False, "ephemeral": True}

    async def test_forbidden_server_returns_nothing_without_connecting(self, service):
        assert await service.list_tools_for_server("secret", CTX) == []
        assert len(FakeManager.instances) == 1  # только менеджер из фикстуры

    async def test_credential_headers_are_part_of_the_cache_key(self, service, monkeypatch):
        import backend.mcp.credentials.base as base

        class Provider:
            async def build_headers(self, server, context):
                return {"Authorization": f"Bearer {context.user_id}"}

        monkeypatch.setattr(base, "get_credential_provider", lambda name: Provider())
        service._registry.servers["math"].credential_provider = "oauth"
        FakeManager.session = FakeSession([{"name": "x"}])
        await service.list_tools_for_server("math", McpCallContext("a", "a"))
        await service.list_tools_for_server("math", McpCallContext("b", "b"))
        assert len(service._tools_cache.store) == 2

    async def test_all_tools_skips_failing_servers(self, service):
        FakeManager.session = FakeSession([{"name": "t"}])
        FakeManager.broken_servers = {"docs"}
        tools = await service.list_all_tools(CTX)
        assert sorted(t.server_id for t in tools) == ["math"]


class TestDebugResourcesAndSessions:
    async def test_call_tool_debug(self, service):
        session = FakeSession()
        FakeManager.session = session
        assert await service.call_tool_debug("docs", "docs_search", None, CTX) == {"ok": "search"}
        assert session.calls == [("search", {})] and FakeManager.instances[-1].disconnected == 1

    async def test_resources_listing_checks_access(self, service):
        assert await service.list_resources_for_server("secret", CTX) == []
        assert await service.list_resources_for_server("docs", CTX, cursor="c1") == [{"uri": "r", "cursor": "c1"}]

    async def test_reading_a_resource(self, service):
        out = await service.read_resource_for_server("docs", "file:///a", CTX)
        assert out["uri"] == "file:///a" and out["raw"]["contents"][0]["text"] == "hello" and "parsed" in out

    async def test_reading_from_a_forbidden_server_is_denied(self, service):
        with pytest.raises(McpServerNotFoundError, match="Access denied"):
            await service.read_resource_for_server("secret", "file:///a", CTX)

    async def test_request_sessions_are_always_released(self, service):
        FakeManager.session = FakeSession()
        with pytest.raises(RuntimeError):
            async with service.request_sessions(["docs", "math"], CTX) as sessions:
                assert set(sessions) == {"docs", "math"}
                raise RuntimeError("generation failed")
        assert FakeManager.instances[-1].disconnected == 1


class TestSingleton:
    def test_platform_is_created_once(self, monkeypatch):
        created = []
        monkeypatch.setattr(plat, "_platform", None)
        monkeypatch.setattr(plat, "McpPlatformService", lambda: created.append(1) or object())
        assert plat.get_mcp_platform() is plat.get_mcp_platform() and created == [1]
