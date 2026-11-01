"""mcp/*: реестр серверов, проверка пакета, tier tool calling, плагины серверов, ленивые импорты, кэш tools и пр."""

import asyncio
import builtins
import importlib
import logging
from types import SimpleNamespace

import pytest

import backend.mcp as mcp_pkg
from backend.mcp import file_download as fd
from backend.mcp import langgraph_tools as lt
from backend.mcp import package_check as pc
from backend.mcp import platform as plat
from backend.mcp import prompt_fc_handler as pfh
from backend.mcp import registry as reg
from backend.mcp import tool_calling as tcm
from backend.mcp.exceptions import McpServerNotFoundError
from backend.mcp.plugins import get_server_plugin
from backend.mcp.text_tool_calls import extract_tool_calls_from_content
from backend.mcp.tools_cache import McpToolsCache
from backend.mcp.types import McpToolInfo

pytestmark = pytest.mark.unit


class TestRegistry:
    def settings(self, enabled=True, servers=()):
        return SimpleNamespace(mcp=SimpleNamespace(enabled=enabled, servers=list(servers)))

    def test_initialize_indexes_servers_with_ids(self):
        a, b, nameless = SimpleNamespace(id="a", enabled=True), SimpleNamespace(id="b", enabled=False), SimpleNamespace(id="", enabled=True)
        r = reg.McpRegistry(self.settings(True, [a, b, nameless]))
        assert r.list_servers() == []  # до initialize реестр пуст
        r.initialize()
        assert r.list_servers() == [a, b] and r.list_enabled_servers() == [a]
        assert r.get_server("b") is b and r.get_server("zzz") is None
        assert r.enabled is True and r.config is r._settings.mcp

    def test_initialize_picks_up_new_config(self):
        s = self.settings(False, [])
        r = reg.McpRegistry(s)
        s.mcp = SimpleNamespace(enabled=True, servers=[SimpleNamespace(id="x", enabled=True)])
        r.initialize()
        assert r.enabled is True and [x.id for x in r.list_servers()] == ["x"]

    def test_require_server(self):
        r = reg.McpRegistry(self.settings(True, [SimpleNamespace(id="a", enabled=True)]))
        r.initialize()
        assert r.require_server("a").id == "a"
        with pytest.raises(McpServerNotFoundError, match="'ghost' not found"):
            r.require_server("ghost")


class TestPackageCheck:
    def test_real_package_is_complete(self):
        assert pc.verify_mcp_package() == []

    def test_reports_missing_modules_only(self, monkeypatch):
        real = importlib.import_module

        def fake(name, *a):
            if name in ("backend.mcp.events", "backend.mcp.sdk"):
                raise ModuleNotFoundError(name)
            return real(name, *a)

        monkeypatch.setattr(pc.importlib, "import_module", fake)
        assert pc.verify_mcp_package() == ["events", "sdk"]

    def test_current_prompt_fc_handler_is_fine(self):
        assert pc.verify_prompt_fc_handler() == []

    def test_old_prompt_fc_handler_is_reported(self, monkeypatch):
        async def old(messages):
            return None

        monkeypatch.setattr(pfh, "run_prompt_json_fc", old)
        issues = pc.verify_prompt_fc_handler()
        assert len(issues) == 1 and "event_callback" in issues[0]

    def test_import_failure_is_reported(self, monkeypatch):
        monkeypatch.setattr(pc.inspect, "signature", lambda f: (_ for _ in ()).throw(ValueError("нет сигнатуры")))
        issues = pc.verify_prompt_fc_handler()
        assert issues == ["prompt_fc_handler: нет сигнатуры"]

    @pytest.fixture
    def records(self, monkeypatch):
        rec = []
        fake = SimpleNamespace(
            error=lambda *a, **k: rec.append(("error", a)),
            warning=lambda *a, **k: rec.append(("warning", a)),
            info=lambda *a, **k: rec.append(("info", a)),
            exception=lambda *a, **k: rec.append(("exception", a)),
        )
        monkeypatch.setattr(pc, "log", fake)
        return rec

    def test_status_error_when_modules_missing(self, monkeypatch, records):
        monkeypatch.setattr(pc, "verify_mcp_package", lambda: ["client", "sdk"])
        monkeypatch.setattr(pc, "verify_prompt_fc_handler", lambda: [])
        pc.log_mcp_package_status()
        level, args = records[0]
        assert level == "error" and args[1] == "client, sdk" and str(args[2]).endswith("mcp")

    def test_status_warning_when_stale(self, monkeypatch, records):
        monkeypatch.setattr(pc, "verify_mcp_package", lambda: [])
        monkeypatch.setattr(pc, "verify_prompt_fc_handler", lambda: ["a", "b"])
        pc.log_mcp_package_status()
        level, args = records[0]
        assert level == "warning" and args[1] == len(pc.REQUIRED_MODULES) and args[2] == "a; b"

    def test_status_ok(self, monkeypatch, records):
        monkeypatch.setattr(pc, "verify_mcp_package", lambda: [])
        monkeypatch.setattr(pc, "verify_prompt_fc_handler", lambda: [])
        pc.log_mcp_package_status()
        assert records == [("info", ("MCP package check OK (%s modules)", len(pc.REQUIRED_MODULES)))]


class FakeProvider:
    def __init__(self, configured, native):
        self.configured, self.native = configured, native

    def mcp_tool_calling_mode(self):
        return self.configured

    def supports_native_function_calling(self):
        return self.native


class TestToolCallingMode:
    @pytest.mark.parametrize(
        "configured,native,expected",
        [
            ("native_openai_tools", False, "native_openai_tools"),
            ("prompt_json_fc", True, "prompt_json_fc"),
            ("auto", True, "native_openai_tools"),
            ("auto", False, "prompt_json_fc"),
            ("langgraph_agent", False, "prompt_json_fc"),
        ],
    )
    def test_resolve(self, configured, native, expected):
        assert tcm.resolve_tool_calling_mode(FakeProvider(configured, native)) == expected


class TestServerPlugins:
    def test_atlassian(self):
        plugin = get_server_plugin("atlassian")
        assert plugin["id"] == "atlassian" and plugin["supports_credentials_api"] is False and plugin["supports_config_api"] is True

    def test_unknown(self):
        assert get_server_plugin("other") is None


class TestLazyPackageExports:
    def test_platform_exports(self):
        assert mcp_pkg.McpPlatformService is plat.McpPlatformService
        assert mcp_pkg.get_mcp_platform is plat.get_mcp_platform

    def test_agent_loop_exports(self):
        from backend.mcp import agent_loop

        assert mcp_pkg.McpAgentLoop is agent_loop.McpAgentLoop
        assert mcp_pkg.get_mcp_agent_loop is agent_loop.get_mcp_agent_loop

    def test_unknown_attribute(self):
        with pytest.raises(AttributeError, match="has no attribute 'nope'"):
            mcp_pkg.nope


class TestPlatformConstructor:
    def test_builds_registry_and_manager_without_pool(self):
        settings = SimpleNamespace(mcp=SimpleNamespace(enabled=True, servers=[]))
        svc = plat.McpPlatformService(settings)
        assert svc.initialized is False and svc.enabled is True
        assert svc._pool is None and svc._tools_cache is None and svc._session_manager._pool is None

    def test_disabled_config(self):
        svc = plat.McpPlatformService(SimpleNamespace(mcp=SimpleNamespace(enabled=False, servers=[])))
        assert svc.enabled is False and svc.list_enabled_server_ids(["a"]) == []

    def test_raw_name_never_strips_a_non_empty_prefix_known_dead_branch(self):
        """platform.py:429 недостижима: qualify_tool_name при непустом prefix возвращает prefix+name != name."""
        settings = SimpleNamespace(mcp=SimpleNamespace(enabled=True, servers=[SimpleNamespace(id="docs", enabled=True, tool_name_prefix="p_")]))
        svc = plat.McpPlatformService(settings)
        svc._registry.initialize()
        assert svc._resolve_raw_tool_name("docs", "p_search") == "search"  # снимается веткой startswith(prefix), а не строкой 429


class TestToolsCache:
    async def test_expired_entries_are_not_counted(self, monkeypatch):
        now = {"t": 100.0}
        monkeypatch.setattr("backend.mcp.tools_cache.time.monotonic", lambda: now["t"])
        cache = McpToolsCache(ttl_seconds=10)
        tool = McpToolInfo("s", "t", "mcp_s_t", "", {})
        await cache.set_tools_indexed("s", [tool], "fp1")
        now["t"] = 105.0
        await cache.set_tools_indexed("s", [tool, tool], "fp2")
        assert cache.count_cached_tools("s") == 2
        now["t"] = 112.0  # fp1 просрочен (12с), fp2 свеж (7с)
        assert cache.count_cached_tools("s") == 2
        now["t"] = 120.0
        assert cache.count_cached_tools("s") == 0

    async def test_index_entry_without_store_is_skipped(self):
        cache = McpToolsCache(ttl_seconds=10)
        await cache.set_tools_indexed("s", [], "fp")
        cache._store.clear()
        assert cache.count_cached_tools("s") == 0
        assert cache.count_cached_tools("unknown") == 0


class TestTextToolCallEdgeCases:
    def test_function_tag_with_empty_body(self):
        cleaned, calls = extract_tool_calls_from_content("до <function=ping></function> после")
        assert [(c.name, c.arguments) for c in calls] == [("ping", {})] and cleaned == "до  после"

    def test_function_tag_with_non_json_body(self):
        _, calls = extract_tool_calls_from_content("<function=ping>просто текст</function>")
        assert calls[0].arguments == {}

    def test_function_tag_with_broken_or_non_dict_json(self):
        _, broken = extract_tool_calls_from_content("<function=a>{oops</function>")
        _, listy = extract_tool_calls_from_content("<function=a>[1]</function>")
        assert broken[0].arguments == {} and listy[0].arguments == {}


class TestPromptJsonExtraction:
    def test_extracts_embedded_object(self):
        assert pfh._extract_json_object('ответ: {"a": 1} конец') == {"a": 1}

    def test_invalid_json_gives_none(self):
        assert pfh._extract_json_object("{не json}") is None
        assert pfh._extract_json_object("") is None
        assert pfh._extract_json_object("нет скобок") is None


class TestFileDownloadTails:
    def test_missing_minio_library(self, monkeypatch):
        real = builtins.__import__

        def fake(name, *a, **k):
            if name == "minio":
                raise ImportError("no minio")
            return real(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake)
        with pytest.raises(RuntimeError, match="MinIO library not installed"):
            fd._build_s3_client({"endpoint": "s3"})

    def test_no_candidate_keys_gives_explanatory_error(self, monkeypatch):
        monkeypatch.setattr(fd, "_build_s3_client", lambda s: object())
        monkeypatch.setattr(fd, "_s3_object_key_candidates", lambda *a, **k: [])
        assert fd.fetch_from_s3("a.pptx", {"bucket": "b"}) == (None, "no object found for keys: ")

    def test_s3_download_miss_log_names_the_object(self, monkeypatch):
        records = []
        monkeypatch.setattr(fd, "logger", SimpleNamespace(exception=lambda *a, **k: records.append(a), info=lambda *a, **k: None, warning=lambda *a, **k: None))

        class Client:
            def get_object(self, bucket, key):
                raise OSError("NoSuchKey")

        monkeypatch.setattr(fd, "_build_s3_client", lambda s: Client())
        fd.fetch_from_s3("a.pptx", {"bucket": "bucket-x", "prefix": "pres", "server_id": "srv"})
        assert records and records[0] == ("MCP S3 download miss server=%s bucket=%s key=%s", "srv", "bucket-x", "pres/a.pptx")


class TestLangGraphSyncWithIdleLoop:
    """Синхронные обёртки при установленном, но не запущенном event loop: run_until_complete."""

    @pytest.fixture
    def platform(self, monkeypatch):
        from contextlib import asynccontextmanager

        spec = McpToolInfo("math", "add", "mcp_math_add", "d", {})

        class P:
            enabled = initialized = True

            async def list_tools_for_server(self, sid, ctx):
                return [spec]

            def filter_tools_by_context(self, specs, ctx, enabled_server_ids=None):
                return specs

            @asynccontextmanager
            async def request_sessions(self, ids, ctx):
                yield {ids[0]: "S"}

            async def call_tool(self, *a):
                return {"content": [{"type": "text", "text": "ok"}]}

        monkeypatch.setattr("backend.mcp.platform.get_mcp_platform", lambda: P())
        monkeypatch.setattr("backend.mcp.chat_integration.build_mcp_context_from_user", lambda user, chat_id=None, message_id=None: SimpleNamespace())

    def _with_idle_loop(self, fn):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return fn(loop)
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    def test_create_compat_uses_idle_loop(self, platform):
        tools = self._with_idle_loop(lambda loop: lt.create_mcp_langgraph_tools(server_ids=["math"], context_factory=dict))
        assert [t.name for t in tools] == ["mcp_math_add"]

    def test_tool_sync_wrapper_uses_idle_loop(self, platform):
        tools = asyncio.run(lt.load_mcp_langgraph_tools(server_ids=["math"], context={}))
        out = self._with_idle_loop(lambda loop: tools[0].func())
        assert out == "ok"
