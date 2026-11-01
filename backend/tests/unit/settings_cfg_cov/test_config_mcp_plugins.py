"""settings/config: McpPlatformConfig и PluginsPlatformConfig — нормализация и ENV-переопределения."""

import logging

import pytest

from backend.settings import config as cfg

pytestmark = pytest.mark.unit

Mcp = cfg.McpPlatformConfig


class TestNormalizeHubs:
    def test_none_and_garbage_give_empty(self):
        assert Mcp._normalize_hubs(None) == {}
        assert Mcp._normalize_hubs(42) == {}

    def test_dict_of_strings_and_dicts(self):
        raw = {
            " a ": " http://a/ ",
            "b": {"base_url": "http://b//"},
            "c": {"url": "http://c"},
            "": "http://noname",
            "d": "",
            "e": None,
        }

        assert Mcp._normalize_hubs(raw) == {
            "a": "http://a",
            "b": "http://b",
            "c": "http://c",
        }

    def test_list_form(self):
        raw = [
            {"id": "h1", "base_url": "http://h1/"},
            {"name": "h2", "url": "http://h2"},
            {"id": "h3"},
            "skip-me",
        ]

        assert Mcp._normalize_hubs(raw) == {"h1": "http://h1", "h2": "http://h2"}


class TestNormalizeServers:
    def test_none_and_garbage(self):
        assert Mcp._normalize_servers(None) == []
        assert Mcp._normalize_servers("x") == []

    def test_list_keeps_only_dicts_and_copies(self):
        src = {"id": "a"}

        out = Mcp._normalize_servers([src, "x", 3])

        assert out == [{"id": "a"}]
        assert out[0] is not src

    def test_mapping_form(self):
        out = Mcp._normalize_servers(
            {"one": {"base_url": "http://1"}, "two": None, "three": "http://3", "four": {"id": "custom"}}
        )

        assert out == [
            {"base_url": "http://1", "id": "one"},
            {"id": "two"},
            {"base_url": "http://3", "id": "three"},
            {"id": "custom"},
        ]


class TestNormalizeStringList:
    def test_none_blank_and_list(self):
        assert Mcp._normalize_string_list(None) == []
        assert Mcp._normalize_string_list("   ") == []
        assert Mcp._normalize_string_list([" a ", "", "b"]) == ["a", "b"]

    def test_none_item_in_list_is_dropped(self):
        assert Mcp._normalize_string_list(["a", None, "b"]) == ["a", "b"]

    def test_null_item_in_yaml_list_string_is_dropped(self):
        assert Mcp._normalize_string_list("[a, null, b]") == ["a", "b"]

    def test_yaml_list_string(self):
        assert Mcp._normalize_string_list("[a, b, ' ']") == ["a", "b"]

    def test_comma_separated_string(self):
        assert Mcp._normalize_string_list("a, b,,c") == ["a", "b", "c"]

    def test_unparsable_yaml_falls_back_to_split(self):
        # "{" ломает yaml.safe_load, остаётся разбиение по запятой
        assert Mcp._normalize_string_list("{a, b") == ["{a", "b"]


class TestBoolEnv:
    @pytest.mark.parametrize(("raw", "expected"), [("1", True), (" TRUE ", True), ("off", False), ("", False)])
    def test_parse(self, raw, expected):
        assert Mcp._bool_env(raw) is expected


class TestApplyServerDefaults:
    def _apply(self, srv, defaults=None, hubs=None, allowed=("streamable-http",)):
        return Mcp._apply_server_defaults(
            srv, defaults or {}, hubs=hubs or {}, allowed_transports=set(allowed)
        )

    def test_fills_defaults_prefix_and_display_name(self):
        out = self._apply({"id": "my-server_x"})

        assert out["display_name"] == "My Server X"
        assert out["tool_name_prefix"] == "mcp_my_server_x_"
        assert out["transport"] == "streamable-http"
        assert out["health_path"] == "/mcp"
        assert out["enabled"] is True
        assert out["args"] == []

    def test_explicit_values_beat_defaults(self):
        out = self._apply(
            {"id": "s", "timeout_seconds": 5, "tool_name_prefix": "p_", "args": "a,b"},
            defaults={"timeout_seconds": 99, "auth_type": "bearer"},
        )

        assert out["timeout_seconds"] == 5
        assert out["auth_type"] == "bearer"
        assert out["tool_name_prefix"] == "p_"
        assert out["args"] == ["a", "b"]

    def test_hub_path_with_known_hub(self):
        out = self._apply(
            {"id": "s", "hub": "main", "hub_path": "tools"}, hubs={"main": "http://hub"}
        )

        assert out["base_url"] == "http://hub"
        assert out["base_path"] == "/tools"
        assert out["health_path"] == "/tools"
        assert out["enabled"] is True
        assert "hub_path" not in out and "hub" not in out

    def test_explicit_health_path_is_kept_with_hub(self):
        out = self._apply(
            {"id": "s", "hub": "main", "hub_path": "/t", "health_path": "/hz"},
            hubs={"main": "http://hub"},
        )

        assert out["health_path"] == "/hz"
        assert out["base_path"] == "/t"

    def test_hub_path_without_hub_disables_server(self, caplog):
        with caplog.at_level(logging.WARNING):
            out = self._apply({"id": "s", "hub_path": "/t"})

        assert out["enabled"] is False
        assert out["base_path"] == "/t"

    def test_unknown_hub_disables_server(self):
        out = self._apply({"id": "s", "hub": "nope", "hub_path": "/t"}, hubs={"main": "u"})

        assert out["enabled"] is False
        assert "base_url" not in out

    def test_disallowed_transport_disables_server(self):
        out = self._apply({"id": "s", "transport": " stdio "})

        assert out["transport"] == "stdio"
        assert out["enabled"] is False


class TestMcpLoadFromEnv:
    def test_defaults(self):
        m = Mcp()

        assert m.enabled is False
        assert m.servers == []
        assert m.allowed_transports == ["streamable-http"]
        assert m.pool.max_size == 4

    def test_non_dict_input(self):
        assert Mcp.load_from_env(None)["servers"] == []

    def test_scalar_env_overrides(self, monkeypatch):
        monkeypatch.setenv("MCP_ENABLED", "TRUE")
        monkeypatch.setenv("MCP_TOOLS_CACHE_TTL_SECONDS", "15")
        monkeypatch.setenv("MCP_FC_TASK_MODEL", "fc-model")
        monkeypatch.setenv("MCP_TOOL_CALLING_MODE_DEFAULT", " native ")
        monkeypatch.setenv("MCP_CHAT_DEFAULT", " all ")
        monkeypatch.setenv("MCP_LLM_PROVIDER_ALLOWLIST", "a, b,,c")
        monkeypatch.setenv("MCP_SESSION_POOL_ENABLED", "no")

        m = Mcp(enabled=False)

        assert m.enabled is True
        assert m.tools_cache_ttl_seconds == 15
        assert m.fc_task_model == "fc-model"
        assert m.tool_calling_mode_default == "native"
        assert m.chat_default == "all"
        assert m.llm_provider_allowlist == ["a", "b", "c"]
        assert m.session_pool_enabled is False

    def test_bad_ttl_env_is_ignored(self, monkeypatch):
        monkeypatch.setenv("MCP_TOOLS_CACHE_TTL_SECONDS", "x")

        assert Mcp(tools_cache_ttl_seconds=7).tools_cache_ttl_seconds == 7

    def test_allowed_transports_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_ALLOWED_TRANSPORTS", "streamable-http, sse")

        m = Mcp(allowed_transports=["stdio"])

        assert m.allowed_transports == ["streamable-http", "sse"]

    def test_allowed_transports_empty_falls_back(self):
        assert Mcp(allowed_transports=[]).allowed_transports == ["streamable-http"]

    def test_server_defaults_env(self, monkeypatch):
        monkeypatch.setenv("MCP_SERVER_DEFAULT_TRANSPORT", " sse ")
        monkeypatch.setenv("MCP_SERVER_DEFAULT_BASE_PATH", "/b")
        monkeypatch.setenv("MCP_SERVER_DEFAULT_AUTH_TYPE", "bearer")
        monkeypatch.setenv("MCP_SERVER_DEFAULT_ENABLED", "false")
        monkeypatch.setenv("MCP_SERVER_DEFAULT_VERIFY_SSL", "0")
        monkeypatch.setenv("MCP_SERVER_DEFAULT_STATELESS", "1")
        monkeypatch.setenv("MCP_SERVER_DEFAULT_TIMEOUT_SECONDS", "77")
        monkeypatch.setenv("MCP_ALLOWED_TRANSPORTS", "sse")

        m = Mcp(servers=[{"id": "s"}])

        defaults = m.server_defaults
        assert defaults["transport"] == "sse"
        assert defaults["base_path"] == "/b"
        assert defaults["enabled"] is False
        assert defaults["verify_ssl"] is False
        assert defaults["stateless"] is True
        assert defaults["timeout_seconds"] == 77
        srv = m.servers[0]
        assert srv.transport == "sse"
        assert srv.timeout_seconds == 77
        assert srv.enabled is False
        assert srv.auth_type == "bearer"

    def test_per_server_env_overrides_yaml_transport(self, monkeypatch):
        """Docker: MCP_SERVER_WEBSEARCH_TRANSPORT перекрывает stdio из YAML."""
        monkeypatch.setenv("MCP_SERVER_WEBSEARCH_ENABLED", "true")
        monkeypatch.setenv("MCP_SERVER_WEBSEARCH_TRANSPORT", "streamable-http")
        monkeypatch.setenv("MCP_SERVER_WEBSEARCH_BASE_PATH", "/mcp")
        monkeypatch.setenv("MCP_SERVER_WEBSEARCH_HEALTH_PATH", "/mcp")
        monkeypatch.setenv("MCP_ALLOWED_TRANSPORTS", "streamable-http")

        m = Mcp(
            servers=[
                {
                    "id": "websearch",
                    "enabled": True,
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "open-websearch@latest"],
                }
            ]
        )

        srv = m.servers[0]
        assert srv.enabled is True
        assert srv.transport == "streamable-http"
        assert srv.base_path == "/mcp"
        assert srv.health_path == "/mcp"

    def test_mcp_servers_json_merges_by_id(self, monkeypatch):
        monkeypatch.setenv(
            "MCP_SERVERS_JSON",
            '[{"id": "websearch", "transport": "streamable-http", "enabled": true}]',
        )
        monkeypatch.setenv("MCP_ALLOWED_TRANSPORTS", "streamable-http")

        m = Mcp(
            servers=[
                {"id": "websearch", "transport": "stdio", "enabled": True},
                {"id": "atlassian", "transport": "streamable-http", "enabled": False},
            ]
        )

        by_id = {s.id: s for s in m.servers}
        assert by_id["websearch"].transport == "streamable-http"
        assert by_id["websearch"].enabled is True
        assert by_id["atlassian"].enabled is False

    def test_bad_default_timeout_env_is_ignored(self, monkeypatch):
        monkeypatch.setenv("MCP_SERVER_DEFAULT_TIMEOUT_SECONDS", "NaN")

        assert "timeout_seconds" not in Mcp().server_defaults

    def test_non_dict_server_defaults_replaced(self):
        assert Mcp(server_defaults="junk").server_defaults == {}

    def test_pool_env(self, monkeypatch):
        monkeypatch.setenv("MCP_SESSION_POOL_SIZE", "2")
        monkeypatch.setenv("MCP_POOL_IDLE_TTL_SECONDS", "30")
        monkeypatch.setenv("MCP_POOL_ACQUIRE_TIMEOUT_SECONDS", "1.5")

        m = Mcp(pool="junk")

        assert (m.pool.max_size, m.pool.idle_ttl_seconds, m.pool.acquire_timeout_seconds) == (2, 30, 1.5)

    def test_pool_max_size_env_later_key_wins(self, monkeypatch):
        monkeypatch.setenv("MCP_SESSION_POOL_SIZE", "2")
        monkeypatch.setenv("MCP_POOL_MAX_SIZE", "8")

        assert Mcp().pool.max_size == 8

    def test_bad_pool_env_is_ignored(self, monkeypatch):
        monkeypatch.setenv("MCP_POOL_MAX_SIZE", "big")
        monkeypatch.setenv("MCP_POOL_ACQUIRE_TIMEOUT_SECONDS", "slow")

        m = Mcp(pool={"max_size": 3})

        assert m.pool.max_size == 3
        assert m.pool.acquire_timeout_seconds == 5.0

    def test_forward_headers_env(self, monkeypatch):
        monkeypatch.setenv("MCP_FORWARD_USER_INFO_HEADERS", "false")
        monkeypatch.setenv("MCP_FORWARD_HEADER_CHAT_ID", "X-Chat")
        monkeypatch.setenv("MCP_FORWARD_HEADER_MESSAGE_ID", "X-Msg")

        m = Mcp(forward_headers="junk")

        assert m.forward_headers.enabled is False
        assert m.forward_headers.chat_id_header == "X-Chat"
        assert m.forward_headers.message_id_header == "X-Msg"

    def test_forward_headers_only_chat_header(self, monkeypatch):
        monkeypatch.setenv("MCP_FORWARD_HEADER_CHAT_ID", "X-Chat")

        m = Mcp(forward_headers={"enabled": False})

        assert m.forward_headers.enabled is False
        assert m.forward_headers.chat_id_header == "X-Chat"
        assert m.forward_headers.message_id_header == "X-OpenWebUI-Message-Id"

    def test_forward_headers_non_dict_with_header_env(self, monkeypatch):
        monkeypatch.setenv("MCP_FORWARD_HEADER_MESSAGE_ID", "X-M")

        assert Mcp(forward_headers=5).forward_headers.message_id_header == "X-M"

    def test_hubs_list_and_servers_map_end_to_end(self):
        m = Mcp(
            hubs=[{"id": "main", "url": "http://hub/"}],
            servers={"alpha": {"hub": "main", "hub_path": "/alpha"}},
        )

        assert m.hubs == {"main": "http://hub"}
        assert m.servers[0].id == "alpha"
        assert m.servers[0].base_url == "http://hub"

    def test_non_collection_hubs_ignored(self):
        assert Mcp(hubs="oops").hubs == {}


Plugins = cfg.PluginsPlatformConfig


class TestPluginsNormalize:
    def test_catalog_none_and_garbage(self):
        assert Plugins._normalize_catalog(None) == []
        assert Plugins._normalize_catalog(5) == []

    def test_catalog_list(self):
        assert Plugins._normalize_catalog([{"id": "a"}, "x"]) == [{"id": "a"}]

    def test_catalog_mapping(self):
        out = Plugins._normalize_catalog(
            {"a": {"base_url": "u"}, "b": None, "c": "http://c", "d": {"id": "own"}}
        )

        assert out == [
            {"base_url": "u", "id": "a"},
            {"id": "b"},
            {"base_url": "http://c", "id": "c"},
            {"id": "own"},
        ]


class TestPluginEnvTimeout:
    def test_specific_beats_default(self, monkeypatch):
        monkeypatch.setenv("PLUGIN_CASH_FLOW_TIMEOUT_SECONDS", "11")
        monkeypatch.setenv("PLUGINS_DEFAULT_TIMEOUT_SECONDS", "22")

        assert Plugins._env_timeout_seconds("cash-flow") == 11

    def test_default_used_when_specific_missing(self, monkeypatch):
        monkeypatch.setenv("PLUGINS_DEFAULT_TIMEOUT_SECONDS", "22")

        assert Plugins._env_timeout_seconds("cash-flow") == 22

    def test_garbage_and_non_positive_values_are_skipped(self, monkeypatch):
        monkeypatch.setenv("PLUGIN_P_TIMEOUT_SECONDS", "abc")
        monkeypatch.setenv("PLUGINS_DEFAULT_TIMEOUT_SECONDS", "0")

        assert Plugins._env_timeout_seconds("p") is None

    def test_empty_id_uses_only_default(self, monkeypatch):
        monkeypatch.setenv("PLUGINS_DEFAULT_TIMEOUT_SECONDS", "5")

        assert Plugins._env_timeout_seconds("---") == 5

    def test_nothing_set(self):
        assert Plugins._env_timeout_seconds("p") is None


class TestPluginsLoadFromEnv:
    def test_defaults(self):
        p = Plugins()

        assert p.enabled is True
        assert p.catalog == []

    def test_non_dict_input(self):
        assert Plugins.load_from_env(None)["catalog"] == []

    def test_enabled_env(self, monkeypatch):
        monkeypatch.setenv("PLUGINS_ENABLED", "no")

        assert Plugins(enabled=True).enabled is False

    @pytest.mark.parametrize("key", ["catalog", "plugins", "items"])
    def test_catalog_aliases(self, key):
        p = Plugins(**{key: [{"id": "cash-flow"}]})

        assert [x.id for x in p.catalog] == ["cash-flow"]
        assert p.catalog[0].display_name == "Cash Flow"

    def test_tags_normalisation(self):
        p = Plugins(
            catalog=[
                {"id": "a", "tags": "x, y,,z"},
                {"id": "b", "tags": 5},
                {"id": "c", "tags": ["k"]},
            ]
        )

        assert [x.tags for x in p.catalog] == [["x", "y", "z"], [], ["k"]]

    def test_timeout_from_env_overrides_yaml(self, monkeypatch):
        monkeypatch.setenv("PLUGIN_A_TIMEOUT_SECONDS", "9")

        p = Plugins(catalog=[{"id": "a", "timeout_seconds": 1}, {"id": "b", "timeout_seconds": 1}])

        assert [x.timeout_seconds for x in p.catalog] == [9, 1]
