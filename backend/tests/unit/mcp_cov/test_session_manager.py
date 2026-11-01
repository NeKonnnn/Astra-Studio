"""mcp/session_manager: заголовки авторизации, жизненный цикл сессий, пул, health-проба — на фейковых транспортах."""

from types import SimpleNamespace

import httpx
import pytest

from backend.mcp import session_manager as sm
from backend.mcp.exceptions import McpConnectionError, McpServerNotFoundError
from backend.mcp.session_pool import McpSessionPool
from backend.mcp.types import McpCallContext

pytestmark = pytest.mark.unit

CTX = McpCallContext(user_id="u1", username="ivan", chat_id="c1", message_id="m1")


def srv(sid="docs", **kw):
    base = dict(
        id=sid, enabled=True, transport="streamable-http", credential_provider=None, auth_type="none",
        auth_token=None, auth_header_name=None, auth_header_value=None, custom_headers={},
        timeout_seconds=120, command=None, args=[], cwd=None, base_url="http://mcp:8000", base_path="/mcp",
        verify_ssl=True, health_path="/healthz",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class FakeRegistry:
    def __init__(self, *servers):
        self.servers = {s.id: s for s in servers}

    def require_server(self, sid):
        if sid not in self.servers:
            raise McpServerNotFoundError(sid)
        return self.servers[sid]


class FakeSettings:
    def resolve_mcp_server_base_url(self, sid):
        return f"http://resolved-{sid}:9000"


class FakeClient:
    instances = []

    def __init__(self):
        self.disconnected = 0
        FakeClient.instances.append(self)

    async def disconnect(self):
        self.disconnected += 1


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    FakeClient.instances = []
    monkeypatch.setattr(sm, "McpClient", FakeClient)
    for key in sm._PROXY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # Переменные сервера не должны влиять на тесты
    for name in ("DOCS", "SEC", "A_B"):
        for suffix in ("AUTH_TOKEN", "AUTH_HEADER_NAME", "AUTH_HEADER_VALUE"):
            monkeypatch.delenv(f"MCP_SERVER_{name}_{suffix}", raising=False)


@pytest.fixture
def transports(monkeypatch):
    """Фейковые транспорты: фиксируют вызовы, ошибки задаются через state.error."""
    state = SimpleNamespace(http=[], stdio=[], error=None)

    async def fake_http(client, **kw):
        state.http.append(kw)
        if state.error:
            raise state.error

    async def fake_stdio(client, **kw):
        state.stdio.append(kw)
        if state.error:
            raise state.error

    monkeypatch.setattr(sm, "connect_streamable_http", fake_http)
    monkeypatch.setattr(sm, "connect_stdio", fake_stdio)
    return state


@pytest.fixture
def ctx_headers(monkeypatch):
    holder = SimpleNamespace(value={"X-User": "u1"})
    monkeypatch.setattr(sm, "build_context_headers", lambda ctx: dict(holder.value))
    return holder


def manager(*servers, pool=None):
    return sm.McpSessionManager(FakeSettings(), FakeRegistry(*servers), pool)


class TestHints:
    @pytest.mark.parametrize(
        "exc,fragment",
        [
            (RuntimeError("CERTIFICATE_VERIFY_FAILED"), "TLS"),
            (RuntimeError("ssl handshake"), "TLS"),
            (type("ConnectTimeout", (Exception,), {})("x"), "ConnectTimeout"),
            (RuntimeError("read timeout"), "Timeout после"),
            (type("ReadTimeout", (Exception,), {})("x"), "Timeout после"),
            (type("ConnectError", (Exception,), {})("x"), "ConnectError"),
            (RuntimeError("Connection refused"), "ConnectError"),
            (RuntimeError("Name or service not known"), "DNS"),
            (RuntimeError("getaddrinfo failed"), "DNS"),
            (KeyError("zzz"), "Неизвестная ошибка (KeyError)"),
        ],
    )
    def test_connect_error_hint(self, exc, fragment):
        assert fragment in sm._connect_error_hint(exc)

    def test_proxy_snapshot(self, monkeypatch):
        assert sm._proxy_env_snapshot() == "(proxy env not set)"
        monkeypatch.setenv("HTTPS_PROXY", " http://proxy:3128 ")
        monkeypatch.setenv("NO_PROXY", "   ")
        snapshot = sm._proxy_env_snapshot()
        assert snapshot.startswith("HTTPS_PROXY=http://proxy:3128") and "NO_PROXY" not in snapshot.upper().replace("HTTPS_PROXY", "")

    def test_proxy_snapshot_does_not_leak_proxy_password(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://user:S3CRET@proxy:3128")
        snapshot = sm._proxy_env_snapshot()
        assert "S3CRET" not in snapshot and "http://user:***@proxy:3128" in snapshot


class TestAuthHeaders:
    def test_env_prefix(self):
        assert sm._server_auth_env_prefix("my-srv") == "MCP_SERVER_MY_SRV_"

    @pytest.mark.parametrize("auth_type", [None, "", "none", " NONE ", "oauth2"])
    def test_no_headers(self, auth_type):
        assert sm.build_server_auth_headers(srv(auth_type=auth_type, auth_token="t")) == {}

    def test_bearer_from_config(self):
        assert sm.build_server_auth_headers(srv(auth_type="Bearer", auth_token=" tok ")) == {"Authorization": "Bearer tok"}

    def test_bearer_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv("MCP_SERVER_DOCS_AUTH_TOKEN", " envtok ")
        assert sm.build_server_auth_headers(srv(auth_type="bearer", auth_token="cfg")) == {"Authorization": "Bearer envtok"}

    def test_bearer_without_token(self):
        assert sm.build_server_auth_headers(srv(auth_type="bearer")) == {}

    def test_custom_header_from_config_and_env(self, monkeypatch):
        s = srv(auth_type="headers", auth_header_name=" X-Key ", auth_header_value=" v ")
        assert sm.build_server_auth_headers(s) == {"X-Key": "v"}
        monkeypatch.setenv("MCP_SERVER_DOCS_AUTH_HEADER_NAME", "X-Env")
        monkeypatch.setenv("MCP_SERVER_DOCS_AUTH_HEADER_VALUE", "ev")
        assert sm.build_server_auth_headers(s) == {"X-Env": "ev"}

    def test_custom_header_incomplete(self):
        assert sm.build_server_auth_headers(srv(auth_type="headers", auth_header_name="X")) == {}

    def test_server_auth_reexport(self):
        from backend.mcp import server_auth

        assert server_auth.build_server_auth_headers is sm.build_server_auth_headers


class TestFingerprint:
    def test_stable_and_order_independent(self):
        assert sm._headers_fingerprint({"a": "1", "b": "2"}) == sm._headers_fingerprint({"b": "2", "a": "1"})

    def test_differs_by_value_and_hides_value(self):
        a, b = sm._headers_fingerprint({"Authorization": "Bearer A"}), sm._headers_fingerprint({"Authorization": "Bearer B"})
        assert a != b and "Bearer" not in a and len(a) == 64

    def test_none_headers(self):
        assert sm._headers_fingerprint(None) == sm._headers_fingerprint({})


class TestConnectServer:
    async def test_unknown_server(self):
        with pytest.raises(McpServerNotFoundError):
            await manager().connect_server("ghost", CTX)

    async def test_disabled_server(self):
        with pytest.raises(McpServerNotFoundError, match="disabled"):
            await manager(srv(enabled=False)).connect_server("docs", CTX)

    async def test_headers_are_merged_in_priority_order(self, monkeypatch, transports, ctx_headers):
        class Provider:
            async def build_headers(self, server, context):
                return {"Authorization": "Token provider", "X-P": "p"}

        monkeypatch.setattr(sm, "get_credential_provider", lambda name: Provider())
        ctx_headers.value = {"X-P": "ctx", "X-C": "c"}
        server = srv(auth_type="bearer", auth_token="gen", custom_headers={"X-C": "{{USERNAME}}", "X-Chat": "{{chat_id}}"})
        mgr = manager(server)
        session = await mgr.connect_server("docs", CTX)
        sent = transports.http[0]["headers"]
        # generic < provider < context < custom
        assert sent == {"Authorization": "Token provider", "X-P": "ctx", "X-C": "ivan", "X-Chat": "c1"}
        assert session.headers_fingerprint == sm._headers_fingerprint(sent)
        assert session.server_id == "docs" and mgr._active == [session] and session.ephemeral is False

    async def test_without_pool_session_is_created_directly(self, transports, ctx_headers):
        mgr = manager(srv())
        s = await mgr.connect_server("docs", CTX, ephemeral=True)
        assert s.ephemeral is True and len(transports.http) == 1

    async def test_stdio_server_bypasses_pool(self, transports, ctx_headers):
        pool_calls = []

        class Pool:
            async def acquire(self, *a, **k):
                pool_calls.append(1)

        mgr = manager(srv(transport="stdio", command="npx", args=["-y", "x"], cwd="/w"), pool=Pool())
        await mgr.connect_server("docs", CTX)
        assert pool_calls == [] and transports.stdio == [{"command": "npx", "args": ["-y", "x"], "cwd": "/w", "timeout": 120.0}]

    async def test_pool_hit_reuses_session_with_same_fingerprint(self, transports, ctx_headers):
        mgr = manager(srv())
        fp = sm._headers_fingerprint(ctx_headers.value)
        existing = SimpleNamespace(headers_fingerprint=fp, ephemeral=True)
        acquired = []

        class Pool:
            async def acquire(self, sid, **kw):
                acquired.append((sid, kw))
                return existing

            async def discard(self, s):
                raise AssertionError("не должно вызываться")

        mgr._pool = Pool()
        s = await mgr.connect_server("docs", CTX)
        assert s is existing and existing.ephemeral is False and mgr._active == [existing]
        assert acquired[0][0] == "docs" and acquired[0][1]["fingerprint"] == fp and transports.http == []

    async def test_pool_session_with_other_fingerprint_is_discarded_and_replaced(self, transports, ctx_headers):
        stale = SimpleNamespace(headers_fingerprint="old")
        discarded = []

        class Pool:
            async def acquire(self, sid, **kw):
                return stale

            async def discard(self, s):
                discarded.append(s)

        mgr = manager(srv(), pool=Pool())
        s = await mgr.connect_server("docs", CTX)
        assert discarded == [stale] and s is not stale and len(transports.http) == 1

    async def test_pool_never_hands_one_users_session_to_another(self, transports, ctx_headers, monkeypatch):
        """Изоляция: сессия с заголовками Алисы не достаётся Бобу, т.к. отпечаток заголовков отличается."""
        created = []

        async def factory(sid, **kw):
            sess = SimpleNamespace(server_id=sid, headers_fingerprint=kw["fingerprint"], closed=0, from_pool=False, ephemeral=False, config=SimpleNamespace(transport="streamable-http"))

            async def close():
                sess.closed += 1

            sess.close = close
            created.append(sess)
            return sess

        pool = McpSessionPool(SimpleNamespace(max_size=4, idle_ttl_seconds=60, acquire_timeout_seconds=1.0), factory)
        mgr = manager(srv(), pool=pool)
        monkeypatch.setattr(sm, "build_context_headers", lambda ctx: {"X-User": ctx.user_id})

        async def fake_create(server, context, headers, fingerprint):
            return await factory(server.id, fingerprint=fingerprint)

        mgr._create_session = fake_create
        alice = await mgr.connect_server("docs", McpCallContext("alice", "a"))
        await mgr.disconnect_all()
        bob = await mgr.connect_server("docs", McpCallContext("bob", "b"))
        assert bob is not alice and alice.closed == 1  # чужая сессия закрыта, а не отдана
        assert bob.headers_fingerprint != alice.headers_fingerprint

    async def test_pool_does_not_reuse_across_messages_known_issue(self):
        """known issue: в отпечаток входят chat_id/message_id, поэтому warm-пул не срабатывает между сообщениями."""
        created = []

        async def factory(sid, **kw):
            sess = SimpleNamespace(server_id=sid, headers_fingerprint=kw["fingerprint"], from_pool=False, ephemeral=False, config=SimpleNamespace(transport="streamable-http"))

            async def close():
                pass

            sess.close = close
            created.append(sess)
            return sess

        pool = McpSessionPool(SimpleNamespace(max_size=4, idle_ttl_seconds=60, acquire_timeout_seconds=1.0), factory)
        mgr = manager(srv(), pool=pool)
        mgr._create_session = lambda server, context, headers, fp: factory(server.id, fingerprint=fp)
        first = await mgr.connect_server("docs", McpCallContext("u1", "ivan", chat_id="c", message_id="m1"))
        await mgr.disconnect_all()
        second = await mgr.connect_server("docs", McpCallContext("u1", "ivan", chat_id="c", message_id="m2"))
        # пул отдал прогретую сессию, но менеджер её выбросил из-за другого отпечатка заголовков
        assert second is not first and pool.metrics.discard_total == 1 and len(created) == 2


class TestConnectServers:
    async def test_failed_servers_are_skipped(self, transports, ctx_headers):
        mgr = manager(srv("a"), srv("b"))
        result = await mgr.connect_servers(["a", "ghost", "b"], CTX)
        assert sorted(result) == ["a", "b"]

    async def test_failure_log_names_the_server(self, monkeypatch, transports, ctx_headers):
        records = []
        monkeypatch.setattr(sm, "log", SimpleNamespace(exception=lambda *a, **k: records.append(a), debug=lambda *a, **k: None))
        await manager(srv("a")).connect_servers(["ghost"], CTX)
        assert records[0] == ("MCP connect failed server=%s", "ghost")


class TestDisconnect:
    async def test_pooled_sessions_are_released_others_closed_in_reverse_order(self):
        order = []

        class Pool:
            async def release(self, s):
                order.append(("release", s.name))

        def session(name, transport="streamable-http", ephemeral=False, fail=False):
            async def close():
                order.append(("close", name))
                if fail:
                    raise RuntimeError("boom")

            return SimpleNamespace(name=name, ephemeral=ephemeral, config=SimpleNamespace(transport=transport), close=close)

        mgr = manager(pool=Pool())
        for s in (session("a"), session("b", ephemeral=True), session("c", transport="stdio"), session("d", fail=True)):
            mgr._track(s)
        await mgr.disconnect_all()
        assert order == [("release", "d"), ("close", "c"), ("close", "b"), ("release", "a")]
        assert mgr._active == []

    async def test_close_error_does_not_stop_cleanup(self):
        closed = []

        def session(name, fail):
            async def close():
                closed.append(name)
                if fail:
                    raise RuntimeError("x")

            return SimpleNamespace(ephemeral=True, config=SimpleNamespace(transport="stdio"), close=close)

        mgr = manager()
        mgr._track(session("a", False))
        mgr._track(session("b", True))
        await mgr.disconnect_all()
        assert closed == ["b", "a"]

    async def test_close_error_log_names_the_server(self, monkeypatch):
        records = []
        monkeypatch.setattr(sm, "log", SimpleNamespace(exception=lambda *a, **k: records.append(a), debug=lambda *a, **k: None))

        async def close():
            raise RuntimeError("x")

        mgr = manager()
        mgr._track(SimpleNamespace(server_id="docs", ephemeral=True, config=SimpleNamespace(transport="stdio"), close=close))
        await mgr.disconnect_all()
        assert records == [("MCP disconnect error server=%s", "docs")]

    async def test_discard_with_pool(self):
        discarded = []

        class Pool:
            async def discard(self, s):
                discarded.append(s)

        mgr = manager(pool=Pool())
        s = SimpleNamespace()
        mgr._track(s)
        await mgr.discard_session(s)
        assert discarded == [s] and mgr._active == []
        await mgr.discard_session(SimpleNamespace())  # не отслеживаемая сессия тоже обрабатывается
        assert len(discarded) == 2

    async def test_discard_without_pool_closes(self):
        closed = []

        async def close():
            closed.append(1)

        s = SimpleNamespace(close=close)
        mgr = manager()
        mgr._track(s)
        await mgr.discard_session(s)
        assert closed == [1] and mgr._active == []


class TestCreateSession:
    async def test_stdio_requires_command(self, transports):
        mgr = manager(srv(transport="stdio"))
        with pytest.raises(McpConnectionError, match="requires command"):
            await mgr._create_session(srv(transport="stdio"), CTX, {}, "fp")
        assert FakeClient.instances[0].disconnected == 1

    async def test_stdio_success(self, transports):
        mgr = manager()
        session = await mgr._create_session(srv(transport="stdio", command="srv", timeout_seconds=0), CTX, {}, "fp")
        assert transports.stdio[0]["timeout"] == 120.0 and session.headers_fingerprint == "fp"

    async def test_http_uses_configured_base_url(self, transports):
        mgr = manager()
        await mgr._create_session(srv(base_url=" http://cfg:1 ", base_path="api", verify_ssl=False, timeout_seconds=15), CTX, {"A": "b"}, "fp")
        kw = transports.http[0]
        assert kw == {"url": "http://cfg:1/api", "headers": {"A": "b"}, "timeout": 15.0, "verify_ssl": False, "server_id": "docs"}

    async def test_http_falls_back_to_resolved_base_url(self, transports):
        await manager()._create_session(srv(base_url=""), CTX, {}, "fp")
        assert transports.http[0]["url"] == "http://resolved-docs:9000/mcp"

    async def test_http_failure_is_wrapped_and_client_disconnected(self, transports):
        transports.error = ConnectionRefusedError("refused")
        with pytest.raises(McpConnectionError, match="Failed to connect MCP server 'docs': refused") as info:
            await manager()._create_session(srv(), CTX, {}, "fp")
        assert isinstance(info.value.__cause__, ConnectionRefusedError)
        assert FakeClient.instances[0].disconnected == 1

    async def test_stdio_failure_is_wrapped(self, transports):
        transports.error = OSError("no such file")
        with pytest.raises(McpConnectionError, match="no such file"):
            await manager()._create_session(srv(transport="stdio", command="x"), CTX, {}, "fp")

    async def test_create_pooled_session_delegates(self, transports):
        s = await manager().create_pooled_session("docs", server=srv(), context=CTX, headers={}, fingerprint="f")
        assert s.headers_fingerprint == "f"


class TestProbeHealth:
    @pytest.fixture
    def http(self, monkeypatch):
        state = SimpleNamespace(handler=None, requests=[])

        def handler(request):
            state.requests.append(str(request.url))
            return state.handler(request)

        real = httpx.AsyncClient

        def factory(**kw):
            kw["transport"] = httpx.MockTransport(handler)
            return real(**kw)

        monkeypatch.setattr(sm.httpx, "AsyncClient", factory)
        return state

    async def test_stdio_is_always_healthy(self):
        assert await manager().probe_http_health(srv(transport="stdio")) == (True, "")

    async def test_ok_below_500(self, http):
        http.handler = lambda r: httpx.Response(404)
        assert await manager().probe_http_health(srv(health_path="health")) == (True, "")
        assert http.requests == ["http://mcp:8000/health"]

    async def test_server_error(self, http):
        http.handler = lambda r: httpx.Response(503)
        ok, detail = await manager().probe_http_health(srv(base_url="", health_path=""))
        assert ok is False and detail == "http://resolved-docs:9000/healthz -> HTTP 503"

    async def test_transport_error(self, http):
        def boom(request):
            raise httpx.ConnectError("refused")

        http.handler = boom
        ok, detail = await manager().probe_http_health(srv())
        assert ok is False and "недоступен: ConnectError: refused" in detail and "http://mcp:8000/healthz" in detail
