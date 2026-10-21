"""routes/plugins: каталог HTTP-плагинов, health и multipart-прокси invoke."""

from types import SimpleNamespace

import pytest

from backend.routes import plugins as module

pytestmark = pytest.mark.unit

BASE = "/api/plugins"


class Public(SimpleNamespace):
    def model_dump(self):
        return dict(self.__dict__)


def _plugin(pid, enabled=True, **kw):
    return SimpleNamespace(id=pid, enabled=enabled, invoke_file_field=kw.get("file_field"), invoke_optional_file_field=kw.get("opt_field"), invoke_prompt_field=kw.get("prompt_field"))


class FakePlatform:
    def __init__(self):
        self.enabled = True
        self.configs = {"cash-flow": _plugin("cash-flow"), "off": _plugin("off", enabled=False)}
        self.calls = []
        self.health_ok = {"cash-flow": True}
        self.invoke_result = {"status": "ok"}
        self.invoke_error = None

    def list_plugins(self):
        return list(self.configs.values())

    def list_plugins_public(self):
        return [Public(id=c.id, enabled=c.enabled, healthy=None) for c in self.configs.values()]

    def get_plugin(self, pid):
        return self.configs.get(pid)

    def to_public(self, cfg, healthy=None, health_detail=None):
        return Public(id=cfg.id, enabled=cfg.enabled, healthy=healthy, detail=health_detail)

    async def health(self, pid):
        self.calls.append(("health", pid))
        ok = self.health_ok.get(pid, False)
        return SimpleNamespace(ok=ok, error=None if ok else "down", detail={"v": 1}, model_dump=lambda: {"ok": ok})

    async def invoke_multipart(self, pid, files, form):
        self.calls.append(("invoke", pid, files, form))
        if self.invoke_error:
            raise self.invoke_error
        return self.invoke_result


@pytest.fixture
def platform(monkeypatch):
    fake = FakePlatform()
    monkeypatch.setattr(module, "_platform", lambda: fake)
    return fake


def _client(make_client):
    return make_client(module.router)


XLSX = ("model.xlsx", b"PK\x03\x04data", "application/octet-stream")


class TestAuthentication:
    @pytest.mark.parametrize(("method", "path"), [("GET", ""), ("GET", "/cash-flow"), ("GET", "/cash-flow/health"), ("POST", "/cash-flow/invoke")])
    async def test_login_required(self, make_client, platform, method, path):
        response = await make_client(module.router, user=None).request(method, BASE + path)

        assert response.status_code in (401, 403) and platform.calls == []


class TestCatalog:
    async def test_lists_public_cards(self, make_client, platform):
        body = (await _client(make_client).get(BASE)).json()

        assert body["enabled"] is True and [p["id"] for p in body["plugins"]] == ["cash-flow", "off"]
        assert body["catalog_ids"] == ["cash-flow", "off"] and platform.calls == []

    async def test_disabled_platform_returns_an_empty_catalog(self, make_client, platform):
        platform.enabled = False

        body = (await _client(make_client).get(BASE)).json()

        assert body["enabled"] is False and body["plugins"] == [] and body["catalog_ids"] == ["cash-flow", "off"]

    async def test_health_is_probed_only_for_enabled_plugins(self, make_client, platform):
        body = (await _client(make_client).get(BASE, params={"include_health": True})).json()

        assert platform.calls == [("health", "cash-flow")]
        by_id = {p["id"]: p for p in body["plugins"]}
        assert by_id["cash-flow"]["healthy"] is True and by_id["off"]["healthy"] is None

    async def test_unhealthy_plugin_reports_the_error(self, make_client, platform):
        platform.health_ok = {}

        by_id = {p["id"]: p for p in (await _client(make_client).get(BASE, params={"include_health": True})).json()["plugins"]}

        assert by_id["cash-flow"]["healthy"] is False and by_id["cash-flow"]["detail"] == {"error": "down"}


class TestSingle:
    async def test_get(self, make_client, platform):
        body = (await _client(make_client).get(BASE + "/cash-flow")).json()

        assert body["plugin"]["id"] == "cash-flow" and body["plugin"]["healthy"] is True

    async def test_unknown_plugin(self, make_client, platform):
        for path in ("/ghost", "/ghost/health"):
            assert (await _client(make_client).get(BASE + path)).status_code == 404

    async def test_health(self, make_client, platform):
        assert (await _client(make_client).get(BASE + "/cash-flow/health")).json() == {"success": True, "health": {"ok": True}}


class TestInvoke:
    URL = BASE + "/cash-flow/invoke"

    @pytest.fixture(autouse=True)
    def _markdown(self, monkeypatch):
        import backend.plugins.artifact_format as fmt

        monkeypatch.setattr(fmt, "verdict_markdown_from_plugin_result", lambda r: "## Вердикт" if r.get("status") == "ok" else "")
        monkeypatch.setattr(fmt, "findings_fallback_markdown", lambda r: "findings")

    async def test_proxies_the_file_and_form(self, make_client, platform):
        response = await _client(make_client).post(self.URL, files={"model_file": XLSX}, data={"prompt": "проверь", "request_id": "r1"})

        body = response.json()
        assert body == {"success": True, "plugin_id": "cash-flow", "result": {"status": "ok"}, "markdown": "## Вердикт"}
        _, pid, files, form = platform.calls[-1]
        assert files == [("model_file", ("model.xlsx", b"PK\x03\x04data", "application/octet-stream"))]
        assert form == {"prompt": "проверь", "request_id": "r1"}

    async def test_form_is_none_without_optional_fields(self, make_client, platform):
        await _client(make_client).post(self.URL, files={"model_file": XLSX})

        assert platform.calls[-1][3] is None

    async def test_plugin_specific_field_names(self, make_client, platform):
        platform.configs["cash-flow"] = _plugin("cash-flow", file_field="excel", opt_field="quality", prompt_field="question")

        await _client(make_client).post(self.URL, files={"model_file": XLSX, "quality_file": ("q.xlsx", b"Q", "x/y")}, data={"prompt": "p"})

        _, _, files, form = platform.calls[-1]
        assert [f[0] for f in files] == ["excel", "quality"] and form == {"question": "p"}

    async def test_empty_optional_file_is_skipped(self, make_client, platform):
        await _client(make_client).post(self.URL, files={"model_file": XLSX, "quality_file": ("q.xlsx", b"", "x/y")})

        assert len(platform.calls[-1][2]) == 1

    async def test_empty_main_file(self, make_client, platform):
        response = await _client(make_client).post(self.URL, files={"model_file": ("m.xlsx", b"", "x/y")})

        assert response.status_code == 400 and platform.calls == []

    async def test_main_file_is_required(self, make_client, platform):
        assert (await _client(make_client).post(self.URL, data={"prompt": "p"})).status_code == 422

    async def test_unknown_and_disabled_plugins(self, make_client, platform):
        assert (await _client(make_client).post(BASE + "/ghost/invoke", files={"model_file": XLSX})).status_code == 404
        assert (await _client(make_client).post(BASE + "/off/invoke", files={"model_file": XLSX})).status_code == 503
        assert platform.calls == []

    async def test_plugin_removed_between_checks(self, make_client, platform):
        platform.invoke_error = KeyError("cash-flow")

        assert (await _client(make_client).post(self.URL, files={"model_file": XLSX})).status_code == 404

    async def test_upstream_failures_become_502_without_the_message(self, make_client, platform):
        platform.invoke_error = RuntimeError("connect to 10.1.2.3:9000 refused")

        response = await _client(make_client).post(self.URL, files={"model_file": XLSX})

        assert response.status_code == 502 and "10.1.2.3" not in response.text  # CWE-209

    async def test_markdown_is_built_only_for_cash_flow(self, make_client, platform):
        platform.configs["other"] = _plugin("other")

        body = (await _client(make_client).post(BASE + "/other/invoke", files={"model_file": XLSX})).json()

        assert body["markdown"] == ""

    async def test_findings_are_the_fallback_markdown(self, make_client, platform):
        platform.invoke_result = {"status": "failed"}

        assert (await _client(make_client).post(self.URL, files={"model_file": XLSX})).json()["markdown"] == "findings"

    async def test_oversized_upload_is_rejected_with_413(self, make_client, platform, monkeypatch):
        monkeypatch.setenv(module.PLUGIN_MAX_UPLOAD_MB_ENV, "1")
        big = b"x" * (1024 * 1024 + 1)

        response = await _client(make_client).post(self.URL, files={"model_file": ("big.xlsx", big, "application/octet-stream")})

        assert response.status_code == 413
        assert platform.calls == []  # до плагина дело не дошло

    async def test_oversized_quality_file_is_rejected_too(self, make_client, platform, monkeypatch):
        monkeypatch.setenv(module.PLUGIN_MAX_UPLOAD_MB_ENV, "1")
        big = b"x" * (1024 * 1024 + 1)

        response = await _client(make_client).post(
            self.URL, files={"model_file": XLSX, "quality_file": ("q.xlsx", big, "application/octet-stream")}
        )

        assert response.status_code == 413 and platform.calls == []

    async def test_upload_at_the_limit_is_accepted(self, make_client, platform, monkeypatch):
        monkeypatch.setenv(module.PLUGIN_MAX_UPLOAD_MB_ENV, "1")
        exact = b"x" * (1024 * 1024)

        response = await _client(make_client).post(self.URL, files={"model_file": ("ok.xlsx", exact, "application/octet-stream")})

        assert response.status_code == 200 and len(platform.calls[-1][2][0][1][1]) == len(exact)

    async def test_default_limit_is_20_mb_and_env_is_validated(self, monkeypatch):
        monkeypatch.delenv(module.PLUGIN_MAX_UPLOAD_MB_ENV, raising=False)
        assert module._max_upload_bytes() == 20 * 1024 * 1024
        for bad in ("abc", "0", "-5"):
            monkeypatch.setenv(module.PLUGIN_MAX_UPLOAD_MB_ENV, bad)
            assert module._max_upload_bytes() == 20 * 1024 * 1024
        monkeypatch.setenv(module.PLUGIN_MAX_UPLOAD_MB_ENV, "5")
        assert module._max_upload_bytes() == 5 * 1024 * 1024
