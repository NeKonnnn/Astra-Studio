"""routes/mcp: доступ по роли, админские операции, учётные данные per-user, скачивание файлов."""

from types import SimpleNamespace

import pytest

from backend.auth import agent_platform_roles as roles
from backend.mcp.exceptions import McpServerNotFoundError
from backend.routes import mcp as module
from backend.tests.api.conftest import ADMIN, ALICE

pytestmark = pytest.mark.unit

BASE = "/api/mcp"


class FakePlatform:
    def __init__(self):
        self.initialized = True
        self.calls = []
        self.fail = None
        self.servers = [{"id": "docs", "enabled": True}, {"id": "off", "enabled": False}]
        self.verify_ok = {"docs": True}

    def _maybe_fail(self):
        if self.fail:
            raise self.fail

    def list_servers_public(self, ctx):
        self.calls.append(("list", ctx.user_id, ctx.is_admin))
        return self.servers

    async def health(self, ctx, server_ids=None, fetch_tools=False):
        self.calls.append(("health", server_ids, fetch_tools))
        return {"servers": [{"id": "docs", "ok": True}]}

    async def get_server_status(self, server_id, ctx, fetch_tools=False):
        self._maybe_fail()
        return SimpleNamespace(server_id=server_id, display_name="Docs", enabled=True, connected=True, transport="http", tools=["a"], latency_ms=5, error=None, metadata={})

    async def list_tools_for_server(self, server_id, ctx):
        self._maybe_fail()
        return [SimpleNamespace(server_id=server_id, name="search", qualified_name=f"{server_id}__search", description="d", parameters={"type": "object"})]

    async def list_all_tools(self, ctx):
        return [SimpleNamespace(server_id="docs", name="search", qualified_name="docs__search", description="d", parameters={})]

    async def call_tool_debug(self, server_id, tool_name, arguments, ctx):
        self._maybe_fail()
        self.calls.append(("invoke", server_id, tool_name, arguments))
        return {"echo": arguments}

    async def verify_server(self, server_id, ctx):
        ok = self.verify_ok.get(server_id, False)
        return SimpleNamespace(server_id=server_id, success=ok, tools_count=2 if ok else 0, latency_ms=3, error=None if ok else "boom", tools=[f"t{i}" for i in range(80)])

    async def list_resources_for_server(self, server_id, ctx, cursor=None):
        self._maybe_fail()
        return [{"uri": "file:///a", "cursor": cursor}]

    async def read_resource_for_server(self, server_id, uri, ctx):
        self._maybe_fail()
        return {"uri": uri}

    def get_pool_metrics(self):
        return {"size": 1}


@pytest.fixture(autouse=True)
def _role_caches(monkeypatch):
    for name in ("AGENT_PLATFORM_ROLE_MAPPING_ENABLED", "AGENT_ROLE_OWNER_GROUPS", "AGENT_ROLE_EDITOR_GROUPS", "AGENT_ROLE_VIEWER_GROUPS", "AGENT_ROLE_BASE_USER_GROUPS"):
        monkeypatch.delenv(name, raising=False)
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()
    yield
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()


@pytest.fixture
def platform(monkeypatch):
    fake = FakePlatform()
    monkeypatch.setattr(module, "get_mcp_platform", lambda: fake)
    return fake


@pytest.fixture
def servers(monkeypatch):
    configs = {
        "docs": SimpleNamespace(id="docs", enabled=True, auth_mode="per_user", base_url="http://docs.local"),
        "shared": SimpleNamespace(id="shared", enabled=True, auth_mode="shared", base_url=""),
        "disabled": SimpleNamespace(id="disabled", enabled=False, auth_mode="shared", base_url=""),
    }
    settings = SimpleNamespace(get_mcp_server_config=configs.get, resolve_mcp_server_base_url=lambda sid: f"http://resolved/{sid}")
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    return configs


@pytest.fixture
def creds(monkeypatch):
    import backend.mcp.credentials.store as store

    state = SimpleNamespace(saved={}, ok=True, deleted=True, calls=[])

    async def metadata(user_id, server_id):
        return {"configured": (user_id, server_id) in state.saved}

    async def save(user_id, server_id, payload):
        state.calls.append(("save", user_id, server_id))
        if state.ok:
            state.saved[(user_id, server_id)] = payload
        return state.ok

    async def delete(user_id, server_id):
        state.calls.append(("delete", user_id, server_id))
        return state.deleted

    monkeypatch.setattr(store, "credentials_metadata", metadata)
    monkeypatch.setattr(store, "save_credentials", save)
    monkeypatch.setattr(store, "delete_credentials", delete)
    return state


def _client(make_client, user=ALICE):
    return make_client(module.router, user=user)


class TestAccess:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/servers"), ("GET", "/status"), ("GET", "/tools"), ("GET", "/servers/docs/tools"), ("POST", "/servers/verify"),
            ("PUT", "/servers/docs/credentials"), ("DELETE", "/servers/docs/credentials"), ("GET", "/servers/docs/download?filename=a.txt"),
            ("GET", "/pool/metrics"),
        ],
    )
    async def test_login_required(self, make_client, platform, method, path):
        response = await make_client(module.router, user=None).request(method, BASE + path, json={} if method in ("PUT", "POST") else None)

        assert response.status_code in (401, 403) and platform.calls == []

    async def test_role_without_mcp_access_is_rejected(self, make_client, platform, monkeypatch):
        monkeypatch.setenv("AGENT_ROLE_OWNER_GROUPS", "Owners")
        monkeypatch.setenv("AGENT_ROLE_BASE_USER_GROUPS", "Basic")
        roles.is_agent_platform_role_mapping_enabled.cache_clear()
        roles._allowed_sets.cache_clear()

        response = await _client(make_client, {**ALICE, "groups": ["Basic"]}).get(BASE + "/servers")

        assert response.status_code == 403 and platform.calls == []

    @pytest.mark.parametrize(
        ("method", "path"),
        [("GET", "/servers/docs/health"), ("POST", "/servers/docs/tools/search"), ("POST", "/servers/docs/verify"), ("POST", "/servers/verify"), ("GET", "/pool/metrics")],
    )
    async def test_diagnostics_are_admin_only(self, make_client, platform, method, path):
        response = await _client(make_client).request(method, BASE + path, json={} if method == "POST" else None)

        assert response.status_code == 403
        assert [c for c in platform.calls if c[0] in ("invoke", "health")] == []

    async def test_platform_not_initialized(self, make_client, platform):
        platform.initialized = False

        for path in ("/servers", "/status", "/tools", "/servers/docs/tools"):
            assert (await _client(make_client).get(BASE + path)).status_code == 503


class TestRead:
    async def test_server_list_uses_the_callers_context(self, make_client, platform):
        body = (await _client(make_client).get(BASE + "/servers")).json()

        assert body["success"] and len(body["servers"]) == 2
        assert platform.calls == [("list", "alice", False)]

    async def test_status_parses_the_server_filter(self, make_client, platform):
        await _client(make_client).get(BASE + "/status", params={"servers": " docs, ,wiki ", "fetch_tools": True})

        assert platform.calls[-1] == ("health", ["docs", "wiki"], True)

    async def test_status_without_filter_probes_all(self, make_client, platform):
        await _client(make_client).get(BASE + "/status", params={"servers": " , "})

        assert platform.calls[-1] == ("health", None, False)

    async def test_server_status_and_not_found(self, make_client, platform):
        ok = await _client(make_client).get(BASE + "/servers/docs/status")
        assert ok.json()["server"]["id"] == "docs"

        platform.fail = McpServerNotFoundError("nope")
        assert (await _client(make_client).get(BASE + "/servers/ghost/status")).status_code == 404

    async def test_tools_listing_and_error_mapping(self, make_client, platform):
        body = (await _client(make_client).get(BASE + "/servers/docs/tools")).json()
        assert body["count"] == 1 and body["tools"][0]["qualified_name"] == "docs__search"
        assert (await _client(make_client).get(BASE + "/tools")).json()["count"] == 1

        platform.fail = McpServerNotFoundError("nope")
        assert (await _client(make_client).get(BASE + "/servers/x/tools")).status_code == 404
        platform.fail = RuntimeError("upstream exploded at 10.0.0.5")
        response = await _client(make_client).get(BASE + "/servers/x/tools")
        assert response.status_code == 502
        # CWE-209: текст исключения не должен уходить клиенту
        assert "10.0.0.5" not in response.json()["detail"]

    async def test_resources(self, make_client, platform):
        listing = (await _client(make_client).get(BASE + "/servers/docs/resources", params={"cursor": "c1"})).json()
        assert listing["resources"][0]["cursor"] == "c1"

        read = (await _client(make_client).get(BASE + "/servers/docs/resources/read", params={"uri": "file:///a"})).json()
        assert read["resource"] == {"uri": "file:///a"}
        assert (await _client(make_client).get(BASE + "/servers/docs/resources/read")).status_code == 422

        platform.fail = McpServerNotFoundError("nope")
        assert (await _client(make_client).get(BASE + "/servers/x/resources")).status_code == 404
        assert (await _client(make_client).get(BASE + "/servers/x/resources/read", params={"uri": "u"})).status_code == 404
        platform.fail = RuntimeError("x")
        assert (await _client(make_client).get(BASE + "/servers/x/resources")).status_code == 502


class TestAdmin:
    async def test_health_of_one_server(self, make_client, platform):
        assert (await _client(make_client, ADMIN).get(BASE + "/servers/docs/health")).json()["server"] == {"id": "docs", "ok": True}
        assert (await _client(make_client, ADMIN).get(BASE + "/servers/ghost/health")).status_code == 404

    async def test_debug_tool_invoke(self, make_client, platform):
        response = await _client(make_client, ADMIN).post(BASE + "/servers/docs/tools/search", json={"arguments": {"q": "x"}})

        assert response.json()["result"] == {"echo": {"q": "x"}}
        assert platform.calls[-1] == ("invoke", "docs", "search", {"q": "x"})

        platform.fail = McpServerNotFoundError("nope")
        assert (await _client(make_client, ADMIN).post(BASE + "/servers/x/tools/t", json={})).status_code == 404
        platform.fail = RuntimeError("x")
        assert (await _client(make_client, ADMIN).post(BASE + "/servers/x/tools/t", json={})).status_code == 502

    async def test_verify_one_truncates_tools(self, make_client, platform):
        body = (await _client(make_client, ADMIN).post(BASE + "/servers/docs/verify")).json()

        assert body["success"] is True and len(body["verify"]["tools"]) == 50

    async def test_verify_all_skips_disabled_servers(self, make_client, platform):
        body = (await _client(make_client, ADMIN).post(BASE + "/servers/verify")).json()

        assert [r["server_id"] for r in body["results"]] == ["docs"] and body["success"] is True

    async def test_verify_all_reports_failure_and_vacuous_success(self, make_client, platform):
        platform.verify_ok = {}
        assert (await _client(make_client, ADMIN).post(BASE + "/servers/verify")).json()["success"] is False

        platform.servers = []
        body = (await _client(make_client, ADMIN).post(BASE + "/servers/verify")).json()
        assert body["success"] is True and body["results"] == []

    async def test_pool_metrics(self, make_client, platform):
        assert (await _client(make_client, ADMIN).get(BASE + "/pool/metrics")).json()["pool"] == {"size": 1}


class TestCredentials:
    async def test_metadata_is_per_user(self, make_client, platform, servers, creds):
        creds.saved[("alice", "docs")] = {"token": "x"}

        body = (await _client(make_client).get(BASE + "/servers/docs/credentials")).json()

        assert body["configured"] is True and body["auth_mode"] == "per_user"
        assert "token" not in str(body)
        assert (await _client(make_client, {**ALICE, "user_id": "bob", "username": "bob"}).get(BASE + "/servers/docs/credentials")).json()["configured"] is False

    async def test_unknown_server(self, make_client, platform, servers, creds):
        for method in ("GET", "PUT", "DELETE"):
            response = await _client(make_client).request(method, BASE + "/servers/ghost/credentials", json={"payload": {"a": 1}} if method == "PUT" else None)
            assert response.status_code == 404

    async def test_save_stores_under_the_callers_id(self, make_client, platform, servers, creds):
        response = await _client(make_client).put(BASE + "/servers/docs/credentials", json={"payload": {"token": "t"}})

        assert response.status_code == 200 and creds.saved == {("alice", "docs"): {"token": "t"}}

    async def test_shared_servers_do_not_accept_per_user_credentials(self, make_client, platform, servers, creds):
        response = await _client(make_client).put(BASE + "/servers/shared/credentials", json={"payload": {"token": "t"}})

        assert response.status_code == 400 and creds.calls == []

    async def test_empty_payload_is_rejected(self, make_client, platform, servers, creds):
        response = await _client(make_client).put(BASE + "/servers/docs/credentials", json={"payload": {}})

        assert response.status_code == 400 and creds.calls == []

    async def test_atlassian_payload_is_validated(self, make_client, platform, servers, creds):
        servers["atlassian"] = SimpleNamespace(id="atlassian", enabled=True, auth_mode="per_user", base_url="")

        response = await _client(make_client).put(BASE + "/servers/atlassian/credentials", json={"payload": {"junk": 1}})

        assert response.status_code == 400 and creds.calls == []

    async def test_storage_unavailable(self, make_client, platform, servers, creds):
        creds.ok = False

        assert (await _client(make_client).put(BASE + "/servers/docs/credentials", json={"payload": {"token": "t"}})).status_code == 503

    async def test_delete(self, make_client, platform, servers, creds):
        body = (await _client(make_client).delete(BASE + "/servers/docs/credentials")).json()

        assert body["deleted"] is True and creds.calls == [("delete", "alice", "docs")]

    async def test_credentials_can_be_saved_for_servers_the_user_cannot_use_known_issue(self, make_client, platform, servers, creds, monkeypatch):
        # PUT/GET/DELETE credentials не вызывают has_mcp_server_access: достаточно роли MCP.
        # Побочного ущерба нет (данные привязаны к пользователю), но проверка доступа
        # непоследовательна: скачивание её делает, учётные данные нет.
        monkeypatch.setattr(module, "has_mcp_server_access", lambda server, ctx: False)

        assert (await _client(make_client).put(BASE + "/servers/docs/credentials", json={"payload": {"t": 1}})).status_code == 200


class TestDownload:
    @pytest.fixture
    def download(self, monkeypatch, servers):
        state = SimpleNamespace(calls=[], result=(b"DATA", "application/pdf"), error=None, access=True)

        async def fetch(**kwargs):
            state.calls.append(kwargs)
            if state.error:
                raise state.error
            return state.result

        monkeypatch.setattr(module, "fetch_mcp_server_file", fetch)
        monkeypatch.setattr(module, "has_mcp_server_access", lambda server, ctx: state.access)
        monkeypatch.setattr(module, "build_server_auth_headers", lambda server: {"Authorization": "Bearer srv"})
        monkeypatch.setattr(module, "build_mcp_context_headers", lambda ctx: {"X-User": ctx.user_id})
        return state

    URL = BASE + "/servers/docs/download"

    async def test_streams_the_file_with_headers(self, make_client, download):
        response = await _client(make_client).get(self.URL, params={"filename": "deck.pptx", "storage_key": "/a/b.pptx"})

        assert response.content == b"DATA" and response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"] == 'attachment; filename="deck.pptx"'
        call = download.calls[0]
        assert call["filename"] == "deck.pptx" and call["storage_key"] == "a/b.pptx" and call["base_url"] == "http://docs.local"
        assert call["http_headers"] == {"Authorization": "Bearer srv", "X-User": "alice"}

    async def test_content_type_is_guessed_when_missing(self, make_client, download):
        download.result = (b"x", None)

        response = await _client(make_client).get(self.URL, params={"filename": "report.pdf"})

        assert response.headers["content-type"].startswith("application/pdf")

    @pytest.mark.parametrize("name", ["", "   ", "../etc/passwd", "a/b.txt", "a\\b.txt", "..", "a..b"])
    async def test_dangerous_filenames_are_rejected(self, make_client, download, name):
        response = await _client(make_client).get(self.URL, params={"filename": name})

        assert response.status_code in (400, 422) and download.calls == []

    async def test_traversal_in_storage_key(self, make_client, download):
        response = await _client(make_client).get(self.URL, params={"filename": "a.txt", "storage_key": "x/../../secret"})

        assert response.status_code == 400 and download.calls == []

    async def test_unknown_or_disabled_server(self, make_client, download):
        assert (await _client(make_client).get(BASE + "/servers/ghost/download", params={"filename": "a.txt"})).status_code == 404
        assert (await _client(make_client).get(BASE + "/servers/disabled/download", params={"filename": "a.txt"})).status_code == 404

    async def test_server_access_is_enforced(self, make_client, download):
        download.access = False

        assert (await _client(make_client).get(self.URL, params={"filename": "a.txt"})).status_code == 403
        assert download.calls == []

    async def test_base_url_falls_back_to_settings(self, make_client, download):
        await _client(make_client).get(BASE + "/servers/shared/download", params={"filename": "a.txt"})

        assert download.calls[0]["base_url"] == "http://resolved/shared"

    async def test_error_mapping(self, make_client, download):
        for error, status in ((LookupError("no file"), 404), (ValueError("bad"), 400), (RuntimeError("s3 down"), 502)):
            download.error = error
            assert (await _client(make_client).get(self.URL, params={"filename": "a.txt"})).status_code == status

    async def test_quote_in_filename_breaks_the_header_known_issue(self, make_client, download):
        # Имя с кавычкой попадает в Content-Disposition без экранирования
        # (нельзя подделать путь, но можно подсунуть дополнительные параметры заголовка).
        response = await _client(make_client).get(self.URL, params={"filename": 'a".txt; x="y'})

        assert response.headers["content-disposition"] == 'attachment; filename="a".txt; x="y"'


class TestHelpers:
    def test_context_prefers_user_id_and_mirrors_groups(self):
        ctx = module._mcp_context({"user_id": "u1", "username": "alice", "groups": ["g"], "is_admin": 1})

        assert (ctx.user_id, ctx.username, ctx.is_admin, ctx.groups, ctx.ldap_groups) == ("u1", "alice", True, ["g"], ["g"])

    def test_context_falls_back_to_username(self):
        ctx = module._mcp_context({"username": "alice", "ldap_groups": ["l"]})

        assert ctx.user_id == "alice" and ctx.groups == ["l"]
