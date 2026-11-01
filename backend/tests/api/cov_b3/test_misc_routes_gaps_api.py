"""Остатки routes/*: agents (MCP status), mcp_atlassian, system, mcp, llm, memory, models, plugins, context_prompts, voice."""

import asyncio
import sys
import time
import types
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.routes import agents as agents_module
from backend.routes import context_prompts as prompts_module
from backend.routes import llm as llm_module
from backend.routes import mcp as mcp_module
from backend.routes import mcp_atlassian as atlassian_module
from backend.routes import memory as memory_module
from backend.routes import models as models_module
from backend.routes import plugins as plugins_module
from backend.routes import system as system_module
from backend.routes import voice as voice_module
from backend.tests.api.conftest import ADMIN, ALICE
from backend.mcp.exceptions import McpServerNotFoundError

from ._fakes import in_group, role_caches, with_mapping  # noqa: F401

pytestmark = pytest.mark.unit


# ------------------------------------------------------------ routes/agents.py


@pytest.fixture
def mcp_platform(monkeypatch):
    import backend.mcp.platform as platform_module

    holder = SimpleNamespace(initialized=True, health_result={}, error=None, calls=[])

    async def health(ctx):
        holder.calls.append(ctx)
        if holder.error:
            raise holder.error
        return holder.health_result

    holder.health = health
    monkeypatch.setattr(platform_module, "get_mcp_platform", lambda: holder)
    return holder


async def test_agent_mcp_status_when_platform_is_not_initialized(make_client, mcp_platform):
    mcp_platform.initialized = False

    body = (await make_client(agents_module.router).get("/api/agent/mcp/status")).json()

    assert body["success"] is True
    assert body["mcp_status"] == {
        "initialized": False, "servers_connected": 0, "total_servers": 0, "tools": 0,
        "message": "MCP platform not initialized",
    }
    assert mcp_platform.calls == []


async def test_agent_mcp_status_maps_health_fields(make_client, mcp_platform):
    mcp_platform.health_result = {
        "initialized": True, "enabled": True, "servers_connected": 2, "servers_total": 3, "tools_total": 11,
        "servers": [{"id": "docs"}], "pool": {"size": 1}, "message": "ok",
    }

    body = (await make_client(agents_module.router).get("/api/agent/mcp/status")).json()

    assert body["mcp_status"] == {
        "initialized": True, "enabled": True, "servers_connected": 2, "total_servers": 3, "tools": 11,
        "servers": [{"id": "docs"}], "pool": {"size": 1}, "message": "ok",
    }
    ctx = mcp_platform.calls[0]
    assert (ctx.user_id, ctx.is_admin) == ("system", True)  # запрашивается от имени системы


async def test_agent_mcp_status_defaults_for_empty_health(make_client, mcp_platform):
    body = (await make_client(agents_module.router).get("/api/agent/mcp/status")).json()

    assert body["mcp_status"]["servers_connected"] == 0 and body["mcp_status"]["servers"] == []
    assert body["mcp_status"]["initialized"] is False and body["mcp_status"]["message"] is None


async def test_agent_mcp_status_error_is_500(make_client, mcp_platform):
    mcp_platform.error = RuntimeError("mcp сломан")

    response = await make_client(agents_module.router).get("/api/agent/mcp/status")

    assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"


# ------------------------------------------------------------ routes/mcp_atlassian.py

ATL = "/api/mcp/servers/atlassian"


@pytest.fixture
def atlassian(monkeypatch):
    env = SimpleNamespace(server=SimpleNamespace(
        display_name="Atlassian", enabled=True, transport="http", auth_mode="per_user", tool_name_prefix="atl_"
    ), allowed=True, meta={"configured": True})
    monkeypatch.setattr(
        atlassian_module, "get_settings", lambda: SimpleNamespace(get_mcp_server_config=lambda sid: env.server if sid == "atlassian" else None)
    )
    monkeypatch.setattr(atlassian_module, "user_can_use_mcp", lambda user: env.allowed)

    async def metadata(user_id, server_id):
        env.meta_args = (user_id, server_id)
        return env.meta

    monkeypatch.setattr(atlassian_module, "credentials_metadata", metadata)
    return env


async def test_atlassian_config_is_readable(make_client, atlassian):
    body = (await make_client(atlassian_module.router).get(ATL + "/config")).json()

    assert body["success"] is True and body["server_id"] == "atlassian"
    assert body["config"] == {
        "display_name": "Atlassian", "enabled": True, "transport": "http", "auth_mode": "per_user",
        "tool_name_prefix": "atl_", "read_only": False, "toolsets": {"jira": True, "confluence": True},
    }


async def test_atlassian_config_requires_login_and_configured_server(make_client, atlassian):
    assert (await make_client(atlassian_module.router, user=None).get(ATL + "/config")).status_code in (401, 403)

    atlassian.server = None
    response = await make_client(atlassian_module.router).get(ATL + "/config")
    assert response.status_code == 404 and "not configured" in response.json()["detail"]


async def test_atlassian_config_update_is_admin_only_and_not_implemented(make_client, atlassian):
    assert (await make_client(atlassian_module.router).put(ATL + "/config")).status_code == 403

    response = await make_client(atlassian_module.router, ADMIN).put(ATL + "/config")
    assert response.status_code == 501 and "not implemented" in response.json()["detail"]


async def test_atlassian_credentials_metadata(make_client, atlassian):
    body = (await make_client(atlassian_module.router).get(ATL + "/credentials")).json()

    assert body["configured"] is True and body["auth_mode"] == "per_user"
    assert body["credential_fields"] == ["jira_url", "jira_pat", "confluence_url", "confluence_pat"]
    assert atlassian.meta_args == ("alice", "atlassian")


async def test_atlassian_credentials_use_username_when_user_id_is_missing(make_client, atlassian):
    user = {"username": "carol", "is_admin": False, "groups": []}

    await make_client(atlassian_module.router, user).get(ATL + "/credentials")

    assert atlassian.meta_args == ("carol", "atlassian")


async def test_atlassian_credentials_guards(make_client, atlassian):
    atlassian.allowed = False
    forbidden = await make_client(atlassian_module.router).get(ATL + "/credentials")
    assert forbidden.status_code == 403 and "MCP" in forbidden.json()["detail"]

    atlassian.allowed = True
    atlassian.server = None
    assert (await make_client(atlassian_module.router).get(ATL + "/credentials")).status_code == 404


def test_credential_fields_fallback_chain(monkeypatch):
    import backend.mcp.credentials.atlassian as real

    assert atlassian_module._atlassian_credential_fields() == list(real.ATLASSIAN_CREDENTIAL_FIELDS)

    old = types.ModuleType("backend.mcp.credentials.atlassian")  # без константы, но с классом провайдера
    old.AtlassianCredentialProvider = SimpleNamespace(credential_field_names=("a", "b"))
    monkeypatch.setitem(sys.modules, "backend.mcp.credentials.atlassian", old)
    assert atlassian_module._atlassian_credential_fields() == ["a", "b"]

    ancient = types.ModuleType("backend.mcp.credentials.atlassian")  # ни константы, ни класса
    monkeypatch.setitem(sys.modules, "backend.mcp.credentials.atlassian", ancient)
    assert atlassian_module._atlassian_credential_fields() == list(atlassian_module._DEFAULT_ATLASSIAN_CREDENTIAL_FIELDS)


# --------------------------------------------------------------- routes/system.py


@pytest.fixture
def sys_env(monkeypatch):
    env = SimpleNamespace(rag=None)
    monkeypatch.setattr(system_module, "get_model_info", lambda: {"loaded": True})
    monkeypatch.setattr(system_module, "check_stt_available", lambda: True)
    monkeypatch.setattr(system_module, "rag_client", None)
    return env


class FakeMcpPlatform:
    def __init__(self, enabled=True, initialized=True):
        self.enabled = enabled
        self.initialized = initialized

    async def get_aggregate_status(self, ctx):
        return SimpleNamespace(servers_total=3, servers_connected=2, tools_total=9)

    def get_pool_metrics(self):
        return {"size": 4}


async def test_health_reports_mcp_aggregate_when_platform_is_running(make_client, sys_env, monkeypatch):
    import backend.mcp.platform as platform_module

    monkeypatch.setattr(platform_module, "get_mcp_platform", lambda: FakeMcpPlatform())

    body = (await make_client(system_module.router, user=None).get("/health")).json()

    assert body["services"]["mcp"] == {
        "enabled": True, "initialized": True, "servers_total": 3, "servers_connected": 2, "tools_total": 9, "pool": {"size": 4},
    }


async def test_health_mcp_status_without_aggregate_when_disabled(make_client, sys_env, monkeypatch):
    import backend.mcp.platform as platform_module

    monkeypatch.setattr(platform_module, "get_mcp_platform", lambda: FakeMcpPlatform(enabled=False))

    body = (await make_client(system_module.router, user=None).get("/health")).json()

    assert body["services"]["mcp"] == {"enabled": False, "initialized": True}


async def test_health_survives_mcp_failure(make_client, sys_env, monkeypatch):
    import backend.mcp.platform as platform_module

    def boom():
        raise RuntimeError("mcp")

    monkeypatch.setattr(platform_module, "get_mcp_platform", boom)

    body = (await make_client(system_module.router, user=None).get("/health")).json()

    assert body["status"] == "healthy" and body["services"]["mcp"] == {"enabled": False, "initialized": False}


async def test_health_without_model_and_stt_helpers(make_client, monkeypatch):
    monkeypatch.setattr(system_module, "get_model_info", None)
    monkeypatch.setattr(system_module, "check_stt_available", None)
    monkeypatch.setattr(system_module, "rag_client", None)

    body = (await make_client(system_module.router, user=None).get("/health")).json()

    assert body["services"]["llm_model"] is False and body["services"]["stt_whisperx"] is False


class FakeLlmClient:
    diarization_url = "http://diar"

    def __init__(self, fail_for=()):
        self.fail_for = set(fail_for)
        self.diar_calls = []

    async def _probe(self, name):
        if name in self.fail_for:
            raise RuntimeError(f"{name} down")
        return {"status": "ok", "probe": name}

    def health_check(self):
        return self._probe("llm")

    def get_transcription_health(self):
        return self._probe("stt")

    def get_tts_health(self):
        return self._probe("tts")

    def get_ocr_health(self):
        return self._probe("ocr")


@pytest.fixture
def status_env(monkeypatch):
    import backend.llm_client as llm_client
    import backend.llm_providers.routing as routing

    env = SimpleNamespace(client=FakeLlmClient(), enabled={"stt", "tts", "ocr", "diarization"}, routes={"chat": "gw"}, service_error=None)

    async def get_service():
        if env.service_error:
            raise env.service_error
        return SimpleNamespace(client=env.client)

    async def routes():
        return env.routes

    monkeypatch.setattr(llm_client, "get_llm_service", get_service)
    monkeypatch.setattr(routing, "describe_llm_routes", routes)
    monkeypatch.setattr(system_module, "is_service_enabled", lambda key: key in env.enabled)
    monkeypatch.setattr(system_module.state, "transcriber", object())
    return env


class FakeHttpResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _patch_httpx(monkeypatch, responses):
    """Подмена httpx.AsyncClient: responses — функция url -> ответ/исключение."""
    import httpx

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            result = responses(url)
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(httpx, "AsyncClient", Client)


async def test_system_status_reports_modules_and_microservices(make_client, status_env, monkeypatch):
    client = make_client(system_module.router)  # до подмены httpx.AsyncClient
    _patch_httpx(monkeypatch, lambda url: FakeHttpResponse(200, {"status": "ok", "url": url}))
    monkeypatch.setattr(system_module, "ask_agent", object())
    monkeypatch.setattr(system_module, "model_settings", None)
    monkeypatch.setattr(system_module, "rag_client", object())
    mcp = FakeMcpPlatform(enabled=True, initialized=False)
    import backend.mcp.platform as platform_module

    monkeypatch.setattr(platform_module, "get_mcp_platform", lambda: mcp)

    body = (await client.get("/api/system/status")).json()

    modules = body["modules"]
    assert modules["ai_agent"]["available"] is True
    assert modules["ai_agent"]["functions"]["model_settings"] is False
    assert modules["rag_service"] == {"available": True}
    assert modules["transcription"]["available"] is True
    assert modules["mcp"] == {"available": True, "enabled": True, "initialized": False}
    assert modules["llm_routing"] == {"chat": "gw"}
    services = body["microservices"]
    assert set(services) == {"llm", "stt", "tts", "ocr", "diarization"}
    assert services["llm"] == {"status": "ok", "probe": "llm"}
    assert services["diarization"] == {"status": "ok", "url": "http://diar/health"}


async def test_system_status_marks_disabled_and_failing_services(make_client, status_env, monkeypatch):
    status_env.enabled = {"stt", "diarization"}  # tts и ocr выключены
    status_env.client = FakeLlmClient(fail_for={"stt"})
    client = make_client(system_module.router)
    _patch_httpx(monkeypatch, lambda url: FakeHttpResponse(503, {}))

    body = (await client.get("/api/system/status")).json()

    services = body["microservices"]
    assert services["tts"] == {"status": "disabled", "detial": "в разработке"}  # опечатка в ключе — известная особенность
    assert services["ocr"]["status"] == "disabled"
    assert services["stt"] == {"status": "error"}
    assert services["diarization"] == {"status": "error"}
    assert services["llm"]["status"] == "ok"


async def test_system_status_diarization_unreachable(make_client, status_env, monkeypatch):
    client = make_client(system_module.router)
    _patch_httpx(monkeypatch, lambda url: ConnectionError("нет связи"))

    body = (await client.get("/api/system/status")).json()

    assert body["microservices"]["diarization"] == {"status": "unreachable"}


async def test_system_status_llm_service_failure_is_reported(make_client, status_env):
    status_env.service_error = RuntimeError("llm-svc недоступен")

    body = (await make_client(system_module.router).get("/api/system/status")).json()

    assert body["microservices"] == {"error": "Внутренняя ошибка сервера"}
    assert "modules" in body


async def test_system_status_when_mcp_import_fails(make_client, status_env, monkeypatch):
    import backend.mcp.platform as platform_module

    def boom():
        raise RuntimeError("mcp")

    monkeypatch.setattr(platform_module, "get_mcp_platform", boom)
    client = make_client(system_module.router)
    _patch_httpx(monkeypatch, lambda url: FakeHttpResponse(200, {}))

    body = (await client.get("/api/system/status")).json()

    assert body["modules"]["mcp"] == {"available": False}
    assert "llm_routing" not in body["modules"]


async def test_system_status_survives_routing_failure(make_client, status_env, monkeypatch):
    import backend.llm_providers.routing as routing

    async def boom():
        raise RuntimeError("routing")

    monkeypatch.setattr(routing, "describe_llm_routes", boom)
    client = make_client(system_module.router)
    _patch_httpx(monkeypatch, lambda url: FakeHttpResponse(200, {}))

    body = (await client.get("/api/system/status")).json()

    assert body["modules"]["mcp"]["available"] is True and "llm_routing" not in body["modules"]


# ------------------------------------------------------------------ routes/mcp.py


class McpPlatform:
    initialized = True

    def __init__(self):
        self.health_error = None
        self.resource_error = None

    async def health(self, ctx, server_ids=None, fetch_tools=False):
        if self.health_error:
            raise self.health_error
        return {"servers": [{"id": "docs"}]}

    async def read_resource_for_server(self, server_id, uri, ctx):
        if self.resource_error:
            raise self.resource_error
        return {"uri": uri}


@pytest.fixture
def mcp_env(monkeypatch):
    platform = McpPlatform()
    monkeypatch.setattr(mcp_module, "get_mcp_platform", lambda: platform)
    return platform


async def test_mcp_health_of_unknown_server_from_platform_is_404(make_client, mcp_env):
    mcp_env.health_error = McpServerNotFoundError("ghost")

    response = await make_client(mcp_module.router, ADMIN).get("/api/mcp/servers/docs/health")

    assert response.status_code == 404


async def test_mcp_health_requires_admin_and_known_server(make_client, mcp_env):
    assert (await make_client(mcp_module.router).get("/api/mcp/servers/docs/health")).status_code == 403
    assert (await make_client(mcp_module.router, ADMIN).get("/api/mcp/servers/docs/health")).json()["server"] == {"id": "docs"}
    assert (await make_client(mcp_module.router, ADMIN).get("/api/mcp/servers/ghost/health")).status_code == 404


async def test_mcp_read_resource_errors(make_client, mcp_env):
    url = "/api/mcp/servers/docs/resources/read"

    ok = await make_client(mcp_module.router).get(url, params={"uri": "file:///a"})
    assert ok.json()["resource"] == {"uri": "file:///a"}

    mcp_env.resource_error = McpServerNotFoundError("ghost")
    assert (await make_client(mcp_module.router).get(url, params={"uri": "x"})).status_code == 404

    mcp_env.resource_error = RuntimeError("upstream упал")
    bad_gateway = await make_client(mcp_module.router).get(url, params={"uri": "x"})
    assert bad_gateway.status_code == 502 and bad_gateway.json()["detail"] == "Внутренняя ошибка сервера"


@pytest.mark.parametrize("filename", ["", "  ", "../etc/passwd", "a/b.txt", "a\\b.txt"])
async def test_mcp_download_rejects_unsafe_filenames(make_client, mcp_env, filename):
    response = await make_client(mcp_module.router).get("/api/mcp/servers/docs/download", params={"filename": filename})

    assert response.status_code == 400 and response.json()["detail"] == "Invalid filename"


def test_credentials_payload_validation(monkeypatch):
    import backend.mcp.credentials.atlassian as atlassian

    monkeypatch.setattr(atlassian, "validate_atlassian_payload", lambda payload: [])
    mcp_module._validate_credentials_payload("atlassian", {"jira_url": "x"})  # корректный payload для atlassian

    monkeypatch.setattr(atlassian, "validate_atlassian_payload", lambda payload: ["a", "b"])
    with pytest.raises(HTTPException) as exc:
        mcp_module._validate_credentials_payload("atlassian", {})
    assert exc.value.status_code == 400 and exc.value.detail == "a; b"

    mcp_module._validate_credentials_payload("docs", {"token": "t"})
    with pytest.raises(HTTPException) as exc:
        mcp_module._validate_credentials_payload("docs", {})
    assert exc.value.detail == "Empty credentials payload"


# ------------------------------------------------------------------ routes/llm.py


async def test_llm_status_second_request_reuses_cache_filled_while_waiting_for_lock(make_client, monkeypatch):
    payload = {"connected": True, "providers": [], "message": None, "marker": "из кэша"}
    monkeypatch.setattr(llm_module, "_llm_status_cache_payload", None)
    monkeypatch.setattr(llm_module, "_llm_status_cache_ts", 0.0)
    monkeypatch.setattr(llm_module, "_LLM_STATUS_CACHE_SECONDS", 60)
    lock = asyncio.Lock()
    monkeypatch.setattr(llm_module, "_llm_status_cache_lock", lock)

    async def get_registry():
        raise AssertionError("реестр не должен запрашиваться: кэш уже заполнен конкурентным запросом")

    monkeypatch.setattr(llm_module, "get_registry", get_registry)
    client = make_client(llm_module.router)

    async with lock:
        task = asyncio.create_task(client.get("/api/llm/status"))
        await asyncio.sleep(0.05)  # запрос дошёл до ожидания блокировки
        monkeypatch.setattr(llm_module, "_llm_status_cache_payload", payload)
        monkeypatch.setattr(llm_module, "_llm_status_cache_ts", time.monotonic())
    response = await task

    assert response.json() == payload


# ---------------------------------------------------------------- routes/memory.py


class BrokenRepo:
    async def delete_user_conversations(self, user_id):
        raise RuntimeError("mongo down")

    async def get_user_conversations(self, user_id, limit):
        raise RuntimeError("mongo down")


@pytest.mark.parametrize(("method", "path"), [("DELETE", "/api/history"), ("GET", "/api/memory/status")])
async def test_memory_storage_errors_are_500(make_client, monkeypatch, method, path):
    monkeypatch.setattr(memory_module, "get_conversation_repository", lambda: BrokenRepo())

    response = await make_client(memory_module.router).request(method, path)

    assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"


# --------------------------------------------------------------- routes/models.py


def test_models_cache_seconds_come_from_settings(monkeypatch):
    monkeypatch.setattr(models_module, "get_settings", lambda: SimpleNamespace(model_health=SimpleNamespace(models_available_cache_seconds=17)))

    assert models_module._models_available_cache_seconds() == 17


def test_chat_model_filter_defaults_to_true_if_classifier_is_unavailable(monkeypatch):
    import backend.routes.rag as rag_routes

    monkeypatch.delattr(rag_routes, "_rag_model_kind")

    assert models_module._is_chat_model("embed/FRIDA") is True


def test_chat_provider_without_extra_config_is_visible():
    assert models_module._is_chat_provider(SimpleNamespace(config=SimpleNamespace(extra={}))) is True
    assert models_module._is_chat_provider(SimpleNamespace()) is True


async def test_current_model_without_path_is_returned_as_is(make_client, monkeypatch):
    monkeypatch.setattr(models_module, "get_model_info", lambda: {"loaded": False})
    monkeypatch.setattr(models_module, "save_app_settings", lambda d: pytest.fail("настройки не должны сохраняться"))

    response = await make_client(models_module.router).get("/api/models/current")

    assert response.json() == {"loaded": False}


async def test_available_models_cache_filled_while_waiting_for_lock(make_client, monkeypatch):
    payload = {"models": [{"path": "gw/m"}], "marker": "из кэша"}
    monkeypatch.setattr(models_module, "_models_available_cache_payload", None)
    monkeypatch.setattr(models_module, "_models_available_cache_ts", 0.0)
    monkeypatch.setattr(models_module, "_models_available_cache_seconds", lambda: 60)
    lock = asyncio.Lock()
    monkeypatch.setattr(models_module, "_models_available_cache_lock", lock)
    import backend.llm_providers as llm_providers

    async def get_registry():
        raise AssertionError("реестр не должен запрашиваться")

    monkeypatch.setattr(llm_providers, "get_registry", get_registry)
    client = make_client(models_module.router)

    async with lock:
        task = asyncio.create_task(client.get("/api/models/available"))
        await asyncio.sleep(0.05)
        monkeypatch.setattr(models_module, "_models_available_cache_payload", payload)
        monkeypatch.setattr(models_module, "_models_available_cache_ts", time.monotonic())
    response = await task

    assert response.json() == payload


class LoadRegistry:
    def __init__(self, provider, resolved_id="", contains=False):
        self.provider = provider
        self.resolved_id = resolved_id
        self._contains = contains

    def resolve(self, path):
        return self.provider, self.resolved_id

    def contains(self, path):
        return self._contains

    def get(self, path):
        return self.provider


class LoadProvider:
    id = "gw"

    def __init__(self, models=()):
        self.models = list(models)

    async def list_models(self):
        return [SimpleNamespace(model_id=m) for m in self.models]

    async def ensure_model_loaded(self, model_id):
        return True


@pytest.fixture
def load_env(monkeypatch):
    import backend.llm_providers as llm_providers

    holder = SimpleNamespace(registry=None, saved=[])

    async def get_registry():
        return holder.registry

    monkeypatch.setattr(llm_providers, "get_registry", get_registry)
    monkeypatch.setattr(models_module, "save_app_settings", lambda d: holder.saved.append(d))
    return holder


async def test_load_unknown_name_without_provider_cannot_pick_model(make_client, load_env):
    load_env.registry = LoadRegistry(LoadProvider(), resolved_id="", contains=False)

    response = await make_client(models_module.router).post("/api/models/load", json={"model_path": "ghost"})

    assert response.json() == {"message": "Не удалось определить model_id", "success": False}
    assert load_env.saved == []


async def test_load_survives_missing_agent_module(make_client, load_env, monkeypatch):
    import backend

    load_env.registry = LoadRegistry(LoadProvider(), resolved_id="m1")
    monkeypatch.delattr(backend, "agent_llm_svc", raising=False)
    monkeypatch.setitem(sys.modules, "backend.agent_llm_svc", None)

    response = await make_client(models_module.router).post("/api/models/load", json={"model_path": "gw/m1"})

    assert response.json()["success"] is True
    assert load_env.saved[0]["current_model_path"] == "gw/m1"


@pytest.fixture
def model_store(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("settings db down")

    monkeypatch.setattr(models_module, "save_user_model_settings", boom)
    monkeypatch.setattr(models_module, "reset_user_model_settings", boom)


async def test_model_settings_write_errors_are_500(make_client, model_store):
    client = make_client(models_module.router)

    update = await client.put("/api/models/settings", json={})
    reset = await client.post("/api/models/settings/reset")

    assert update.status_code == 500 and reset.status_code == 500
    assert reset.json()["detail"] == "Внутренняя ошибка сервера"


async def test_recommended_settings_error_is_500(make_client, monkeypatch):
    class Broken:
        def get_recommended_settings(self):
            raise RuntimeError("нет рекомендаций")

        def get_max_values(self):
            return {}

    monkeypatch.setattr(models_module, "model_settings", Broken())

    response = await make_client(models_module.router).get("/api/models/settings/recommended")

    assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"


# -------------------------------------------------------------- routes/plugins.py


def test_plugins_platform_is_taken_from_the_platform_module(monkeypatch):
    import backend.plugins.platform as platform_module

    sentinel = object()
    monkeypatch.setattr(platform_module, "get_plugins_platform", lambda: sentinel)

    assert plugins_module._platform() is sentinel


async def test_plugin_that_disappeared_between_listing_and_health_is_kept_as_is(make_client, monkeypatch):
    class Platform:
        enabled = True

        def list_plugins(self):
            return [SimpleNamespace(id="p1", enabled=True)]

        def list_plugins_public(self):
            return [SimpleNamespace(id="p1", enabled=True, model_dump=lambda: {"id": "p1", "enabled": True})]

        def get_plugin(self, pid):
            return None  # плагин убрали из конфигурации во время запроса

        async def health(self, pid):
            return SimpleNamespace(ok=True, error=None, detail={})

    monkeypatch.setattr(plugins_module, "_platform", lambda: Platform())

    response = await make_client(plugins_module.router).get("/api/plugins", params={"include_health": "true"})

    assert response.status_code == 200 and response.json()["plugins"][0]["id"] == "p1"


# --------------------------------------------------------- routes/context_prompts.py


async def test_global_prompt_update_passes_http_errors_through(make_client, monkeypatch):
    async def teapot(uid, prompt):
        raise HTTPException(status_code=418, detail="я чайник")

    monkeypatch.setattr(prompts_module, "set_user_global_prompt", teapot)

    response = await make_client(prompts_module.router).put("/api/context-prompts/global", json={"prompt": "x"})

    assert response.status_code == 418


async def test_model_prompt_update_passes_http_errors_through(make_client, monkeypatch):
    async def teapot(uid, model_path, prompt):
        raise HTTPException(status_code=418, detail="я чайник")

    monkeypatch.setattr(prompts_module, "set_user_model_prompt", teapot)

    response = await make_client(prompts_module.router).put("/api/context-prompts/model/gw/m1", json={"prompt": "x"})

    assert response.status_code == 418


async def test_models_with_prompts_error_is_500(make_client, monkeypatch):
    async def boom():
        raise RuntimeError("каталог моделей недоступен")

    monkeypatch.setattr(models_module, "get_available_models", boom)

    response = await make_client(prompts_module.router).get("/api/context-prompts/models")

    assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"


# ----------------------------------------------------------------- routes/voice.py


async def test_voice_background_cleanup_removes_copy_and_tolerates_missing_file(make_client, monkeypatch, tmp_path, services):
    import os
    import tempfile

    services(voice_module, "tts", "stt")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(voice_module, "minio_client", None)

    def speak(text, speaker, voice_id, speech_rate, save_to_file):
        with open(save_to_file, "wb") as f:
            f.write(b"RIFFwav")
        return True

    captured = {}

    def fake_file_response(path, **kwargs):
        captured["path"] = path
        captured["background"] = kwargs["background"]
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse("stub")

    monkeypatch.setattr(voice_module, "speak_text", speak)
    monkeypatch.setattr(voice_module, "FileResponse", fake_file_response)

    response = await make_client(voice_module.router).post("/api/voice/synthesize", json={"text": "привет"})

    assert response.text == "stub" and os.path.exists(captured["path"])
    await captured["background"]()
    assert not os.path.exists(captured["path"])
    await captured["background"]()  # повторная очистка при отсутствующем файле безопасна


async def test_transcription_settings_without_transcriber_only_store_values(make_client, monkeypatch):
    saved = []
    monkeypatch.setattr(voice_module.state, "transcriber", None)
    monkeypatch.setattr(voice_module.state, "current_transcription_engine", "whisperx")
    monkeypatch.setattr(voice_module.state, "current_transcription_language", "ru")
    monkeypatch.setattr(voice_module, "save_app_settings", lambda d: saved.append(d))

    response = await make_client(voice_module.router).put(
        "/api/transcription/settings", json={"engine": "Vosk", "language": "en"}
    )

    assert response.json()["success"] is True
    assert saved == [{"transcription_engine": "whisperx", "transcription_language": "en"}]


async def test_transcription_settings_with_transcriber_lacking_switch_methods(make_client, monkeypatch):
    monkeypatch.setattr(voice_module.state, "transcriber", object())
    monkeypatch.setattr(voice_module.state, "current_transcription_engine", "whisperx")
    monkeypatch.setattr(voice_module.state, "current_transcription_language", "ru")
    monkeypatch.setattr(voice_module, "save_app_settings", lambda d: None)

    response = await make_client(voice_module.router).put(
        "/api/transcription/settings", json={"engine": "whisperx", "language": "de"}
    )

    assert response.status_code == 200
    assert voice_module.state.current_transcription_language == "de"
