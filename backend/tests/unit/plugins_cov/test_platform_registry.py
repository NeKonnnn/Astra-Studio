"""plugins/platform и plugins/registry: каталог плагинов, health-проба и multipart-вызов (httpx.MockTransport)."""

from types import SimpleNamespace

import httpx
import pytest

from backend.plugins import platform as pp
from backend.plugins.platform import PluginInvokeTimeout, PluginsPlatformService
from backend.plugins.registry import PluginsRegistry
from backend.settings.config import PluginConfig

pytestmark = pytest.mark.unit


def plugin(pid="cash-flow", **kw):
    return PluginConfig(id=pid, **kw)


def make_settings(*plugins, enabled=True, urls=None):
    urls = urls if urls is not None else {}

    def resolve(pid):
        if pid not in urls:
            raise ValueError(f"нет URL для {pid}")
        return urls[pid]

    return SimpleNamespace(plugins=SimpleNamespace(enabled=enabled, catalog=list(plugins)), resolve_plugin_base_url=resolve)


class TestRegistry:
    def test_initialize_indexes_plugins_with_ids(self):
        a, b = plugin("a"), plugin("b", enabled=False)
        empty = SimpleNamespace(id="", enabled=True)
        reg = PluginsRegistry(make_settings(a, b, empty))
        assert reg.list_plugins() == []
        reg.initialize()
        assert reg.list_plugins() == [a, b] and reg.list_enabled_plugins() == [a]
        assert reg.enabled is True and reg.config.enabled is True

    def test_get_plugin_strips_and_tolerates_none(self):
        reg = PluginsRegistry(make_settings(plugin("a")))
        reg.initialize()
        assert reg.get_plugin("  a ").id == "a" and reg.get_plugin(None) is None and reg.get_plugin("x") is None

    def test_require_plugin(self):
        reg = PluginsRegistry(make_settings(plugin("a")))
        reg.initialize()
        assert reg.require_plugin("a").id == "a"
        with pytest.raises(KeyError, match="Plugin 'zzz' not found"):
            reg.require_plugin("zzz")

    def test_disabled_catalog(self):
        assert PluginsRegistry(make_settings(enabled=False)).enabled is False


class TestInitialization:
    def test_initialize_once_and_resolve_errors_do_not_break(self):
        svc = PluginsPlatformService(make_settings(plugin("a", base_url="http://a"), plugin("b")))
        assert svc.initialized is False and svc.enabled is True
        svc.initialize()  # у плагина b нет URL: ошибка разрешения только логируется
        assert svc.initialized is True and [p.id for p in svc.list_plugins()] == ["a", "b"]
        svc._registry.initialize = lambda: (_ for _ in ()).throw(AssertionError("повторная инициализация"))
        svc.initialize()

    def test_accessors(self):
        svc = PluginsPlatformService(make_settings(plugin("a"), plugin("b", enabled=False)))
        svc.initialize()
        assert [p.id for p in svc.list_enabled_plugins()] == ["a"] and svc.get_plugin("b").id == "b" and svc.get_plugin("q") is None

    def test_default_settings_come_from_get_settings(self, monkeypatch):
        s = make_settings()
        monkeypatch.setattr(pp, "get_settings", lambda: s)
        assert PluginsPlatformService()._settings is s


class TestBaseUrl:
    def test_explicit_url_is_stripped(self):
        svc = PluginsPlatformService(make_settings())
        assert svc.resolve_base_url(plugin(base_url=" http://x:1/ ")) == "http://x:1"

    def test_url_from_settings(self):
        svc = PluginsPlatformService(make_settings(urls={"cash-flow": "http://cf:8000"}))
        assert svc.resolve_base_url(plugin()) == "http://cf:8000"


class TestPublic:
    def test_defaults_are_filled(self):
        svc = PluginsPlatformService(make_settings())
        pub = svc.to_public(plugin("x", health_path="", invoke_path=""), healthy=True, health_detail={"v": 1})
        assert (pub.display_name, pub.kind, pub.health_path, pub.invoke_path, pub.healthy, pub.health_detail) == ("x", "http", "/health", "/audit", True, {"v": 1})

    def test_full_listing(self):
        p = plugin("a", display_name="Аудит", description="d", category="fin", tags=["t"], kind="http")
        svc = PluginsPlatformService(make_settings(p))
        svc.initialize()
        (pub,) = svc.list_plugins_public()
        assert (pub.display_name, pub.description, pub.category, pub.tags, pub.healthy) == ("Аудит", "d", "fin", ["t"], None)


@pytest.fixture
def http(monkeypatch):
    state = SimpleNamespace(handler=None, requests=[], client_kw=[])

    def handler(request):
        state.requests.append(request)
        return state.handler(request)

    real = httpx.AsyncClient

    def factory(**kw):
        state.client_kw.append(kw)
        kw["transport"] = httpx.MockTransport(handler)
        return real(**kw)

    monkeypatch.setattr(pp.httpx, "AsyncClient", factory)
    return state


def service(*plugins, urls=None):
    svc = PluginsPlatformService(make_settings(*plugins, urls=urls or {"cash-flow": "http://cf:8000"}))
    svc._registry.initialize()
    return svc


class TestHealth:
    async def test_unknown_plugin(self):
        r = await service().health("ghost")
        assert (r.ok, r.error) == (False, "Plugin not found")

    async def test_disabled_plugin(self):
        r = await service(plugin(enabled=False)).health("cash-flow")
        assert (r.ok, r.error) == (False, "Plugin disabled")

    async def test_unresolved_url(self):
        r = await service(plugin("zz"), urls={}).health("zz")
        assert r.ok is False and "нет URL для zz" in r.error

    async def test_ok_with_json_detail(self, http):
        http.handler = lambda req: httpx.Response(200, json={"status": "up"})
        r = await service(plugin(health_path="live", timeout_seconds=100)).health("cash-flow")
        assert (r.ok, r.status_code, r.detail, r.error) == (True, 200, {"status": "up"}, None)
        assert str(http.requests[0].url) == "http://cf:8000/live"
        assert http.client_kw[0]["timeout"].read == 30.0 and http.client_kw[0]["timeout"].connect == 5.0

    async def test_non_dict_json_is_wrapped(self, http):
        http.handler = lambda req: httpx.Response(200, json=[1, 2])
        r = await service(plugin()).health("cash-flow")
        assert r.detail == {"value": [1, 2]}

    async def test_non_json_body_is_truncated(self, http):
        http.handler = lambda req: httpx.Response(200, text="x" * 700)
        r = await service(plugin()).health("cash-flow")
        assert r.detail == {"raw": "x" * 500}

    async def test_http_error_status(self, http):
        http.handler = lambda req: httpx.Response(503, json={})
        r = await service(plugin()).health("cash-flow")
        assert r.ok is False and r.status_code == 503 and r.error == "HTTP 503 for http://cf:8000/health"

    async def test_transport_failure_message(self, http):
        def boom(req):
            raise httpx.ConnectError("refused")

        http.handler = boom
        r = await service(plugin()).health("cash-flow")
        assert r.ok is False and r.error == "ConnectError: refused (url=http://cf:8000/health)"

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (RuntimeError(""), "RuntimeError('') (url=u)"),
            (ValueError("ValueError: bad"), "ValueError: bad (url=u)"),
            (KeyError("k"), "KeyError: 'k' (url=u)"),
        ],
    )
    def test_format_health_error(self, exc, expected):
        assert pp._format_health_error(exc, "u") == expected


FILES = [("model_file", ("m.xlsx", b"DATA", "application/x"))]


class TestInvoke:
    async def test_unknown_plugin(self):
        with pytest.raises(KeyError):
            await service().invoke_multipart("ghost", files=FILES)

    async def test_disabled_plugin(self):
        with pytest.raises(RuntimeError, match="disabled"):
            await service(plugin(enabled=False)).invoke_multipart("cash-flow", files=FILES)

    async def test_success_sends_multipart_and_drops_none_form_values(self, http):
        http.handler = lambda req: httpx.Response(200, json={"ok": True})
        out = await service(plugin(invoke_path="run", timeout_seconds=42)).invoke_multipart(
            "cash-flow", files=FILES, form={"prompt": "проверь", "skip": None}
        )
        assert out == {"ok": True}
        req = http.requests[0]
        body = req.read()
        assert str(req.url) == "http://cf:8000/run" and b'name="prompt"' in body and "проверь".encode() in body
        assert b"skip" not in body and b"DATA" in body and b'filename="m.xlsx"' in body
        assert http.client_kw[0]["timeout"].read == 42.0 and http.client_kw[0]["timeout"].connect == 10.0

    async def test_no_form(self, http):
        http.handler = lambda req: httpx.Response(200, json={"ok": 1})
        assert await service(plugin()).invoke_multipart("cash-flow", files=FILES) == {"ok": 1}
        assert str(http.requests[0].url) == "http://cf:8000/audit"

    async def test_non_dict_result_is_wrapped(self, http):
        http.handler = lambda req: httpx.Response(200, json=[1])
        assert await service(plugin()).invoke_multipart("cash-flow", files=FILES) == {"result": [1]}

    async def test_non_json_result_is_wrapped_as_raw(self, http):
        http.handler = lambda req: httpx.Response(200, text="plain")
        assert await service(plugin()).invoke_multipart("cash-flow", files=FILES) == {"raw": "plain"}

    async def test_error_status_uses_detail(self, http):
        http.handler = lambda req: httpx.Response(422, json={"detail": " файл повреждён "})
        with pytest.raises(RuntimeError, match="^файл повреждён$"):
            await service(plugin()).invoke_multipart("cash-flow", files=FILES)

    async def test_error_status_without_detail(self, http):
        http.handler = lambda req: httpx.Response(500, text="oops")
        with pytest.raises(RuntimeError, match="вернул HTTP 500 .*http://cf:8000/audit"):
            await service(plugin()).invoke_multipart("cash-flow", files=FILES)

    async def test_timeout_has_actionable_message(self, http):
        def slow(req):
            raise httpx.ReadTimeout("slow")

        http.handler = slow
        with pytest.raises(PluginInvokeTimeout) as info:
            await service(plugin(timeout_seconds=7)).invoke_multipart("cash-flow", files=FILES)
        msg = str(info.value)
        assert "7 с" in msg and "plugins.catalog.cash-flow.timeout_seconds" in msg and "PLUGIN_CASH_FLOW_TIMEOUT_SECONDS" in msg
        assert isinstance(info.value, TimeoutError) and isinstance(info.value.__cause__, httpx.ReadTimeout)

    async def test_transport_error_is_wrapped(self, http):
        def boom(req):
            raise httpx.ConnectError("")

        http.handler = boom
        with pytest.raises(RuntimeError, match="недоступен: ConnectError .*url=http://cf:8000/audit") as info:
            await service(plugin()).invoke_multipart("cash-flow", files=FILES)
        assert isinstance(info.value.__cause__, httpx.ConnectError)

    def test_timeout_env_key(self):
        assert pp._timeout_env_key("cash-flow") == "PLUGIN_CASH_FLOW_TIMEOUT_SECONDS"
        assert pp._timeout_env_key("--a b--") == "PLUGIN_A_B_TIMEOUT_SECONDS"


class TestSingleton:
    def test_created_and_initialized_once(self, monkeypatch):
        created = []

        class Fake:
            def __init__(self):
                created.append(self)
                self.initialized = False

            def initialize(self):
                self.initialized = True

        monkeypatch.setattr(pp, "PluginsPlatformService", Fake)
        monkeypatch.setattr(pp, "_platform", None)
        first = pp.get_plugins_platform()
        assert first.initialized is True and pp.get_plugins_platform() is first and len(created) == 1
        from backend.plugins import get_plugins_platform

        assert get_plugins_platform is pp.get_plugins_platform
