"""mcp/client, connection, transports, sdk: обёртка над SDK mcp на фейковых сессиях и контекстах."""

import sys
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from backend.mcp import client as mc
from backend.mcp import sdk
from backend.mcp.connection import McpServerSession
from backend.mcp.transports import stdio as t_stdio
from backend.mcp.transports import streamable_http as t_http
from backend.mcp.types import McpCallContext

pytestmark = pytest.mark.unit


class FakeModel:
    def __init__(self, data, is_error=False):
        self.data, self.isError = data, is_error

    def model_dump(self, mode=None):
        return self.data


class FakeSdkSession:
    """Фейк mcp.ClientSession: контекст-менеджер с initialize/list_tools/call_tool."""

    instances = []

    def __init__(self, read, write):
        self.read, self.write = read, write
        self.entered = self.exited = self.initialized = False
        self.init_error = None
        self.tools = []
        self.call_result = None
        self.resources_result = None
        self.read_result = None
        self.calls = []
        FakeSdkSession.instances.append(self)

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *exc):
        self.exited = True
        return False

    async def initialize(self):
        if self.init_error:
            raise self.init_error
        self.initialized = True

    async def list_tools(self):
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return self.call_result

    async def list_resources(self, cursor=None):
        self.calls.append(("list_resources", cursor))
        return self.resources_result

    async def read_resource(self, uri):
        self.calls.append(("read_resource", uri))
        return self.read_result


class FakeStreams:
    """Фейк контекста транспорта (stdio_client / streamablehttp_client)."""

    def __init__(self, items=("R", "W", "extra")):
        self.items = items
        self.entered = self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self.items

    async def __aexit__(self, *exc):
        self.exited = True
        return False


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch):
    FakeSdkSession.instances = []
    monkeypatch.setattr(mc, "ClientSession", FakeSdkSession)


class TestHttpxFactories:
    def test_secure_client_uses_defaults_and_passes_arguments(self, monkeypatch):
        monkeypatch.setattr(mc, "resolve_httpx_verify", lambda v: "/ca.pem" if v else False)
        seen = {}

        class FakeAsyncClient:
            def __init__(self, **kw):
                seen.update(kw)

        monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
        auth = httpx.BasicAuth("u", "p")
        mc.create_httpx_client(headers={"X": "1"}, timeout=3.0, auth=auth)
        assert seen == {"follow_redirects": True, "verify": "/ca.pem", "headers": {"X": "1"}, "timeout": 3.0, "auth": auth}

    def test_optional_arguments_are_omitted(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: seen.update(kw))
        mc.create_httpx_client()
        assert set(seen) == {"follow_redirects", "verify"}

    def test_insecure_client_disables_verification(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: seen.update(kw))
        mc.create_insecure_httpx_client(headers={})
        assert seen["verify"] is False and seen["headers"] == {}

    def test_real_client_object_is_created(self):
        client = mc.create_httpx_client(timeout=1.0)
        assert isinstance(client, httpx.AsyncClient) and client.follow_redirects is True


class TestConnect:
    async def test_enters_transport_then_session_and_initializes(self):
        streams = FakeStreams()
        c = mc.McpClient()
        await c.connect_streams(streams, init_timeout=1.0)
        sess = FakeSdkSession.instances[0]
        assert (sess.read, sess.write) == ("R", "W") and sess.entered and sess.initialized
        assert c.session is sess and c.exit_stack is not None
        assert not streams.exited and not sess.exited  # соединение остаётся открытым

    async def test_disconnect_closes_in_lifo_order_and_resets_state(self):
        streams = FakeStreams()
        c = mc.McpClient()
        await c.connect_streams(streams)
        sess = c.session
        await c.disconnect()
        assert streams.exited and sess.exited
        assert c.session is None and c.exit_stack is None
        await c.disconnect()  # повторный вызов безопасен

    async def test_initialize_failure_with_server_info_logs_debug_and_cleans_up(self, monkeypatch):
        records = []
        monkeypatch.setattr(mc, "log", SimpleNamespace(debug=lambda *a, **k: records.append(a), exception=lambda *a, **k: records.append(("EXC",) + a)))
        streams = FakeStreams()
        original = FakeSdkSession.__init__

        def failing_init(self, r, w):
            original(self, r, w)
            self.init_error = ValueError("bad init")

        FakeSdkSession.__init__ = failing_init
        try:
            c = mc.McpClient()
            with pytest.raises(ValueError, match="bad init"):
                await c.connect_streams(streams, server_id="srv", mcp_url="http://x/mcp")
        finally:
            FakeSdkSession.__init__ = original
        assert streams.exited and FakeSdkSession.instances[0].exited
        assert len(records) == 1 and "phase=initialize" in records[0][0] and records[0][1:3] == ("srv", "http://x/mcp")
        assert c.exit_stack is None  # при ошибке стек не сохраняется

    async def test_failure_without_server_info_logs_exception(self, monkeypatch):
        records = []
        monkeypatch.setattr(mc, "log", SimpleNamespace(debug=lambda *a, **k: None, exception=lambda *a, **k: records.append(a)))

        class BrokenStreams(FakeStreams):
            async def __aenter__(self):
                raise OSError("no route")

        c = mc.McpClient()
        with pytest.raises(OSError, match="no route"):
            await c.connect_streams(BrokenStreams())
        assert "connect_streams failed" in records[0][0]
        assert c.exit_stack is None

    async def test_init_timeout_is_enforced(self, monkeypatch):
        import anyio

        original = FakeSdkSession.initialize

        async def hang(self):
            await anyio.sleep(5)

        FakeSdkSession.initialize = hang
        try:
            c = mc.McpClient()
            with pytest.raises(TimeoutError):
                await c.connect_streams(FakeStreams(), init_timeout=0.01)
        finally:
            FakeSdkSession.initialize = original


class TestOperations:
    async def connected(self):
        c = mc.McpClient()
        await c.connect_streams(FakeStreams())
        return c, c.session

    @pytest.mark.parametrize(
        "method,args",
        [
            ("list_tool_specs", ()),
            ("call_tool", ("n", {})),
            ("list_resources", ()),
            ("read_resource", ("u",)),
        ],
    )
    async def test_not_connected_raises(self, method, args):
        with pytest.raises(RuntimeError, match="not connected"):
            await getattr(mc.McpClient(), method)(*args)

    async def test_list_tool_specs_normalizes_fields(self):
        c, sess = await self.connected()
        sess.tools = [
            SimpleNamespace(name="a", description="Desc", inputSchema={"type": "object"}),
            SimpleNamespace(name="b", description=None, inputSchema=None),
        ]
        assert await c.list_tool_specs() == [
            {"name": "a", "description": "Desc", "parameters": {"type": "object"}},
            {"name": "b", "description": "", "parameters": {}},
        ]

    async def test_call_tool_returns_content(self):
        c, sess = await self.connected()
        sess.call_result = FakeModel({"content": [{"type": "text", "text": "ok"}]})
        assert await c.call_tool("add", {"a": 1}) == [{"type": "text", "text": "ok"}]
        assert sess.calls == [("add", {"a": 1})]

    async def test_call_tool_without_content_key_returns_whole_dict(self):
        c, sess = await self.connected()
        sess.call_result = FakeModel({"x": 1})
        assert await c.call_tool("add", {}) == {"x": 1}

    async def test_call_tool_error_flag_raises_with_content(self):
        c, sess = await self.connected()
        sess.call_result = FakeModel({"content": "denied"}, is_error=True)
        with pytest.raises(RuntimeError, match="denied"):
            await c.call_tool("add", {})

    async def test_call_tool_empty_result_raises(self):
        c, sess = await self.connected()
        sess.call_result = None
        with pytest.raises(RuntimeError, match="No result"):
            await c.call_tool("add", {})

    async def test_list_resources(self):
        c, sess = await self.connected()
        sess.resources_result = FakeModel({"resources": [{"uri": "u"}]})
        assert await c.list_resources(cursor="c1") == [{"uri": "u"}]
        assert sess.calls == [("list_resources", "c1")]
        sess.resources_result = FakeModel({})
        assert await c.list_resources() == []

    async def test_list_resources_empty_result_raises(self):
        c, sess = await self.connected()
        with pytest.raises(RuntimeError, match="list_resources"):
            await c.list_resources()

    async def test_read_resource(self):
        c, sess = await self.connected()
        sess.read_result = FakeModel({"contents": [1]})
        assert await c.read_resource("file://x") == {"contents": [1]}
        sess.read_result = None
        with pytest.raises(RuntimeError, match="read_resource"):
            await c.read_resource("file://x")


class TestDisconnectErrors:
    def client_with_stack(self, error):
        c = mc.McpClient()

        class Stack:
            async def aclose(self):
                raise error

        c.exit_stack, c.session = Stack(), object()
        return c

    async def test_timeout_is_logged_as_warning(self, caplog):
        c = self.client_with_stack(TimeoutError())
        await c.disconnect()
        assert "timed out" in caplog.text and c.exit_stack is None and c.session is None

    async def test_runtime_error_is_suppressed(self):
        c = self.client_with_stack(RuntimeError("cancel scope"))
        await c.disconnect()
        assert c.exit_stack is None

    async def test_other_errors_are_swallowed_and_logged(self, caplog):
        c = self.client_with_stack(ValueError("weird"))
        await c.disconnect()
        assert "Ошибка операции" in caplog.text


class TestServerSession:
    async def test_delegates_to_client(self):
        calls = []

        class FakeClient:
            async def list_tool_specs(self):
                return [{"name": "t"}]

            async def call_tool(self, name, args):
                calls.append(("call", name, args))
                return "R"

            async def list_resources(self, cursor=None):
                calls.append(("res", cursor))
                return [{"uri": "u"}]

            async def read_resource(self, uri):
                return {"uri": uri}

            async def disconnect(self):
                calls.append("closed")

        s = McpServerSession("srv", SimpleNamespace(), FakeClient(), McpCallContext("u", "n"))
        assert s.from_pool is False and s.ephemeral is False and s.headers_fingerprint == ""
        assert await s.list_tools() == [{"name": "t"}]
        assert await s.call_tool("x", {"a": 1}) == "R"
        assert await s.list_resources("cur") == [{"uri": "u"}]
        assert await s.read_resource("file://a") == {"uri": "file://a"}
        await s.close()
        assert calls == [("call", "x", {"a": 1}), ("res", "cur"), "closed"]


class TestStdioTransport:
    async def test_builds_params_and_connects_with_capped_timeout(self, monkeypatch):
        seen = {}

        class Params:
            def __init__(self, **kw):
                seen["params"] = kw

        ctx = object()
        monkeypatch.setattr(t_stdio, "StdioServerParameters", Params)
        monkeypatch.setattr(t_stdio, "stdio_client", lambda p: seen.update(client_params=p) or ctx)

        class FakeClient:
            async def connect_streams(self, streams, **kw):
                seen["streams"], seen["kw"] = streams, kw

        await t_stdio.connect_stdio(FakeClient(), command="npx", args=["-y", "srv"], cwd="/w", timeout=120.0)
        assert seen["params"] == {"command": "npx", "args": ["-y", "srv"], "cwd": "/w"}
        assert seen["streams"] is ctx and seen["kw"] == {"init_timeout": 30.0}

    async def test_defaults(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(t_stdio, "StdioServerParameters", lambda **kw: seen.update(kw))
        monkeypatch.setattr(t_stdio, "stdio_client", lambda p: "ctx")

        class FakeClient:
            async def connect_streams(self, streams, **kw):
                seen["init"] = kw["init_timeout"]

        await t_stdio.connect_stdio(FakeClient(), command="srv", timeout=5.0)
        assert seen == {"command": "srv", "args": [], "cwd": None, "init": 5.0}


class TestHttpTransport:
    @pytest.mark.parametrize(
        "base,path,expected",
        [
            ("http://h:8000/", "/mcp", "http://h:8000/mcp"),
            ("http://h:8000", "mcp", "http://h:8000/mcp"),
            ("  http://h/api/  ", "", "http://h/api/mcp"),
            ("http://h/api", " /x/y ", "http://h/api/x/y"),
            (None, None, "/mcp"),
        ],
    )
    def test_build_url(self, base, path, expected):
        assert t_http.build_mcp_http_url(base, path) == expected

    @pytest.mark.parametrize("verify,factory_name", [(True, "create_httpx_client"), (False, "create_insecure_httpx_client")])
    async def test_connect_selects_httpx_factory(self, monkeypatch, verify, factory_name):
        seen = {}
        monkeypatch.setattr(
            t_http, "streamablehttp_client",
            lambda url, headers=None, httpx_client_factory=None: seen.update(url=url, headers=headers, factory=httpx_client_factory) or "ctx",
        )

        class FakeClient:
            async def connect_streams(self, streams, **kw):
                seen["streams"], seen["kw"] = streams, kw

        await t_http.connect_streamable_http(
            FakeClient(), url="http://h/mcp", headers={"A": "b"}, timeout=10.0, verify_ssl=verify, server_id="srv"
        )
        assert seen["factory"] is getattr(t_http, factory_name)
        assert seen["streams"] == "ctx" and seen["headers"] == {"A": "b"}
        assert seen["kw"] == {"init_timeout": 10.0, "server_id": "srv", "mcp_url": "http://h/mcp"}

    async def test_empty_server_id_becomes_none(self, monkeypatch):
        monkeypatch.setattr(t_http, "streamablehttp_client", lambda *a, **k: "ctx")
        seen = {}

        class FakeClient:
            async def connect_streams(self, streams, **kw):
                seen.update(kw)

        await t_http.connect_streamable_http(FakeClient(), url="u", timeout=500.0)
        assert seen["server_id"] is None and seen["init_timeout"] == 30.0


class TestSdkImport:
    def test_real_sdk_is_returned_and_cached(self):
        mod = sdk.import_mcp_sdk()
        assert hasattr(mod, "ClientSession")
        assert sdk.import_mcp_sdk() is mod

    def test_submodule_import(self):
        assert hasattr(sdk.import_mcp_sdk_submodule("mcp.client.stdio"), "stdio_client")

    def test_is_local_shadow_by_path(self):
        local = SimpleNamespace(__file__=str(sdk._BACKEND_ROOT / "mcp" / "x.py"))
        other = SimpleNamespace(__file__=str(sdk._BACKEND_ROOT.parent / "site" / "mcp" / "__init__.py"))
        assert sdk._is_local_shadow(local) is True
        assert sdk._is_local_shadow(other) is False

    def test_is_local_shadow_without_file(self):
        bare = ModuleType("mcp")
        assert sdk._is_local_shadow(bare) is True
        bare.ClientSession = object
        assert sdk._is_local_shadow(bare) is False
        assert sdk._is_local_shadow(ModuleType("other")) is False

    def test_purge_removes_only_shadowed(self, monkeypatch):
        shadow = SimpleNamespace(__file__=str(sdk._BACKEND_ROOT / "mcp" / "__init__.py"))
        good = SimpleNamespace(__file__=str(sdk._BACKEND_ROOT.parent / "site" / "mcp" / "types.py"))
        monkeypatch.setitem(sys.modules, "mcp.fake_shadow", shadow)
        monkeypatch.setitem(sys.modules, "mcp.fake_good", good)
        monkeypatch.setitem(sys.modules, "mcp.fake_none", None)
        sdk._purge_shadowed_mcp_modules()
        assert "mcp.fake_shadow" not in sys.modules and "mcp.fake_none" not in sys.modules
        assert sys.modules["mcp.fake_good"] is good

    def test_import_reloads_when_shadowed_module_is_registered(self, monkeypatch):
        real = sdk.import_mcp_sdk()
        shadow = ModuleType("mcp")
        shadow.__file__ = str(sdk._BACKEND_ROOT / "mcp" / "__init__.py")
        monkeypatch.setitem(sys.modules, "mcp", shadow)
        saved_path = list(sys.path)
        sys.path.insert(0, str(sdk._BACKEND_ROOT))
        try:
            got = sdk.import_mcp_sdk()
        finally:
            sys.path[:] = saved_path
        assert hasattr(got, "ClientSession") and got is not shadow
        assert sys.path == saved_path
        assert got.__file__ == real.__file__
