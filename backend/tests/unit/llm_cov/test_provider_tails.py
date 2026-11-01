"""llm_svc, ollama, vLLM, Anthropic, OpenAI-native, routing: хвосты, не покрытые базовыми тестами."""

import json

import httpx
import pytest
import respx

from backend.llm_providers import routing
from backend.llm_providers.anthropic import AnthropicProvider
from backend.llm_providers.base import LLMProviderConfig, ModelInfo, ProviderHealth
from backend.llm_providers.llm_svc import LlmSvcProvider, pool_contains_model, same_llm_svc_model_id
from backend.llm_providers.ollama import OllamaProvider
from backend.llm_providers.openai_native import OpenAIProvider, OpenRouterProvider
from backend.llm_providers.vllm import VLLMProvider

pytestmark = pytest.mark.unit

ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    for name in ("TLS_CERT_PATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "LLM_STREAM_ERROR_RETRIES"):
        monkeypatch.delenv(name, raising=False)
    with ROUTER:
        yield
    ROUTER.reset()


def make(cls, kind, base, **kw):
    return cls(LLMProviderConfig(id=kind, kind=kind, base_url=base, **kw))


def models(*ids, pid="llm-svc"):
    return [ModelInfo(provider_id=pid, model_id=i) for i in ids]


# ---------------------------------------------------------------- llm-svc


class TestLlmSvcHelpers:
    @pytest.mark.parametrize(
        ("loaded", "requested", "expected"),
        [
            (None, "a", False),
            ("a", None, False),
            ("", "a", False),
            (" Qwen ", "qwen", True),
            ("qwen", "qwen-7b-instruct", True),
            ("qwen-7b", "qwen", True),
            ("qwen", "qwen2", False),
        ],
    )
    def test_same_model_id(self, loaded, requested, expected):
        assert same_llm_svc_model_id(loaded, requested) is expected

    def test_pool_contains_model(self):
        assert pool_contains_model(["", "qwen"], "qwen-7b") is True
        assert pool_contains_model(["llama"], "qwen") is False
        assert pool_contains_model(["qwen"], "  ") is False
        assert pool_contains_model(["qwen"], None) is False


class TestLlmSvcProvider:
    BASE = "http://svc.test"

    @pytest.fixture
    def p(self):
        return make(LlmSvcProvider, "llm-svc", self.BASE)

    async def test_load_timeout_is_long(self, p):
        t = await p._load_timeout()

        assert t.read == 1200.0 and t.connect == 10.0

    async def test_post_load_model_failure_status(self, p):
        ROUTER.post(f"{self.BASE}/v1/models/load").respond(500, text="oom")

        assert await p._post_load_model("m") is False

    async def test_post_load_model_success_flag_false(self, p):
        ROUTER.post(f"{self.BASE}/v1/models/load").respond(200, json={"success": False})

        assert await p._post_load_model("m") is False

    async def test_post_load_model_network_error(self, p):
        ROUTER.post(f"{self.BASE}/v1/models/load").mock(side_effect=httpx.ConnectError("down"))

        assert await p._post_load_model("m") is False

    async def test_post_load_model_success(self, p):
        route = ROUTER.post(f"{self.BASE}/v1/models/load").respond(200, json={"success": True})

        assert await p._post_load_model("m") is True
        assert json.loads(route.calls.last.request.content) == {"model": "m"}

    async def test_ensure_model_loaded_empty_id(self, p):
        assert await p.ensure_model_loaded("  ") is False

    async def test_ensure_model_loaded_noop_when_in_pool(self, p):
        ROUTER.get(f"{self.BASE}/v1/health").respond(200, json={"loaded_models": ["qwen"]})
        load = ROUTER.post(f"{self.BASE}/v1/models/load").respond(200, json={"success": True})

        assert await p.ensure_model_loaded("qwen-7b") is True
        assert not load.called

    async def test_ensure_model_loaded_unhealthy_then_swap(self, p, monkeypatch):
        async def sick():
            return ProviderHealth(healthy=False, error="starting")

        monkeypatch.setattr(p, "health", sick)
        load = ROUTER.post(f"{self.BASE}/v1/models/load").respond(200, json={"success": True})

        assert await p.ensure_model_loaded("qwen") is True
        assert load.called

    async def test_ensure_model_loaded_rechecks_pool_under_lock(self, p, monkeypatch):
        answers = iter(
            [
                ProviderHealth(healthy=True, loaded_models=[]),
                ProviderHealth(healthy=True, loaded_models=["qwen"]),
            ]
        )

        async def fake_health():
            return next(answers)

        async def must_not_load(_):
            raise AssertionError("повторная загрузка не нужна")

        monkeypatch.setattr(p, "health", fake_health)
        monkeypatch.setattr(p, "_post_load_model", must_not_load)

        assert await p.ensure_model_loaded("qwen") is True

    async def test_filter_returns_input_when_disabled_or_empty(self, p, monkeypatch):
        monkeypatch.setattr("backend.llm_providers.llm_svc.model_health_filter_enabled", lambda cfg: False)
        items = models("a")

        assert await p._filter_models_by_health(items) is items
        assert await p._filter_models_by_health([]) == []

    @pytest.fixture
    def filter_on(self, monkeypatch):
        monkeypatch.setattr("backend.llm_providers.llm_svc.model_health_filter_enabled", lambda cfg: True)

    async def test_filter_unhealthy_returns_all(self, p, filter_on, monkeypatch):
        async def sick():
            return ProviderHealth(healthy=False, error="boom")

        monkeypatch.setattr(p, "health", sick)
        items = models("a", "b")

        assert await p._filter_models_by_health(items) == items

    async def test_filter_empty_pool_returns_all(self, p, filter_on):
        ROUTER.get(f"{self.BASE}/v1/health").respond(200, json={"loaded_models": []})
        items = models("a", "b")

        assert await p._filter_models_by_health(items) == items

    async def test_filter_healthy_keeps_full_disk_catalog(self, p, filter_on):
        """Не режем каталог до RAM-пула — иначе UI видит только 1–2 loaded модели."""
        ROUTER.get(f"{self.BASE}/v1/health").respond(200, json={"loaded_models": ["a"]})

        res = await p._filter_models_by_health(models("a-7b", "b", "c"))

        assert [m.model_id for m in res] == ["a-7b", "b", "c"]

    async def test_filter_same_size_keeps_input(self, p, filter_on):
        ROUTER.get(f"{self.BASE}/v1/health").respond(200, json={"loaded_models": ["a", "b"]})
        items = models("a", "b")

        assert [m.model_id for m in await p._filter_models_by_health(items)] == ["a", "b"]

    async def test_filter_health_exception_returns_all(self, p, filter_on, monkeypatch):
        async def boom():
            raise RuntimeError("x")

        monkeypatch.setattr(p, "health", boom)
        items = models("a")

        assert await p._filter_models_by_health(items) == items

    async def test_unload_excess_success_and_default_true(self, p):
        route = ROUTER.post(f"{self.BASE}/v1/models/unload-excess").respond(200, json={})

        assert await p.unload_excess() is True
        assert route.called

    async def test_unload_excess_reports_false_flag(self, p):
        ROUTER.post(f"{self.BASE}/v1/models/unload-excess").respond(200, json={"success": False})

        assert await p.unload_excess() is False

    async def test_unload_excess_http_error(self, p):
        ROUTER.post(f"{self.BASE}/v1/models/unload-excess").respond(500, text="bad")

        assert await p.unload_excess() is False

    async def test_unload_excess_network_error(self, p):
        ROUTER.post(f"{self.BASE}/v1/models/unload-excess").mock(side_effect=httpx.ConnectError("x"))

        assert await p.unload_excess() is False


# ------------------------------------------------------------------ ollama


class TestOllamaTails:
    BASE = "http://ollama.test"

    @pytest.fixture
    def p(self):
        return make(OllamaProvider, "ollama", self.BASE)

    async def test_health_non_json_falls_back_to_models(self, p):
        ROUTER.get(f"{self.BASE}/api/version").respond(200, text="<html>")
        ROUTER.get(f"{self.BASE}/v1/models").respond(200, json={"data": [{"id": "m1"}]})

        h = await p.health()

        assert h.healthy is True

    async def test_health_non_json_without_fallback_is_unhealthy(self, p):
        p.HEALTH_FALLBACK_TO_MODELS = False
        ROUTER.get(f"{self.BASE}/api/version").respond(200, text="<html>")

        h = await p.health()

        assert h.healthy is False and "non-JSON" in h.error

    async def test_list_models_error_falls_back_to_super(self, p, monkeypatch):
        monkeypatch.setattr("backend.llm_providers.openai_compat.model_health_filter_enabled", lambda cfg: False)
        ROUTER.get(f"{self.BASE}/api/tags").respond(200, text="not json")
        ROUTER.get(f"{self.BASE}/v1/models").respond(200, json={"data": [{"id": "fallback-m"}]})

        res = await p.list_models()

        # тело /api/tags не разбирается -> общий fallback на OpenAI-compat /v1/models
        assert [m.model_id for m in res] == ["fallback-m"]

    async def test_ensure_model_loaded_list_failure(self, p, monkeypatch):
        async def boom():
            raise RuntimeError("tags down")

        monkeypatch.setattr(p, "list_models", boom)

        assert await p.ensure_model_loaded("m") is False

    async def test_ensure_model_loaded_empty(self, p):
        assert await p.ensure_model_loaded("") is False


# -------------------------------------------------------------------- vllm


class TestVllmTails:
    BASE = "http://vllm.test"

    @pytest.fixture
    def p(self):
        return make(VLLMProvider, "vllm", self.BASE, static_model="qwen")

    async def test_loaded_ids_empty_data_uses_static_model(self, p):
        ROUTER.get(f"{self.BASE}/health").respond(200, text="")
        ROUTER.get(f"{self.BASE}/v1/models").respond(200, json={"data": []})

        h = await p.health()

        assert h.healthy is True and h.loaded_models == ["qwen"]

    async def test_loaded_ids_http_error_uses_static_fallback(self, p):
        ROUTER.get(f"{self.BASE}/health").respond(200, text="")
        ROUTER.get(f"{self.BASE}/v1/models").respond(500, text="x")

        assert (await p.health()).loaded_models == ["qwen"]

    async def test_loaded_ids_invalid_json_uses_static_fallback(self, p):
        ROUTER.get(f"{self.BASE}/health").respond(200, text="")
        ROUTER.get(f"{self.BASE}/v1/models").respond(200, text="not json")

        assert (await p.health()).loaded_models == ["qwen"]

    async def test_loaded_ids_without_static_model(self):
        p = make(VLLMProvider, "vllm", self.BASE)
        ROUTER.get(f"{self.BASE}/health").respond(200, text="")
        ROUTER.get(f"{self.BASE}/v1/models").respond(500, text="x")

        assert (await p.health()).loaded_models == []

    async def test_ensure_model_loaded_list_failure_with_static(self, p, monkeypatch):
        async def boom():
            raise RuntimeError("down")

        monkeypatch.setattr(p, "list_models", boom)

        assert await p.ensure_model_loaded("other") is False
        assert await p.ensure_model_loaded("qwen") is True

    async def test_ensure_model_loaded_list_failure_without_static(self, monkeypatch):
        p = make(VLLMProvider, "vllm", self.BASE)

        async def boom():
            raise RuntimeError("down")

        monkeypatch.setattr(p, "list_models", boom)

        assert await p.ensure_model_loaded("x") is False


# -------------------------------------------------------------- anthropic


class TestAnthropicTails:
    BASE = "http://anthropic.test"

    @pytest.fixture
    def p(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_TAIL_KEY", "ak")
        return make(AnthropicProvider, "anthropic", self.BASE, api_key_env="ANTHROPIC_TAIL_KEY")

    def test_capabilities_are_non_openai(self, p):
        caps = p.capabilities

        assert caps.native_chat_api is False
        assert caps.streaming is True

    def _stream(self, *events):
        body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    async def test_stream_timeout_helper_failure_uses_config_timeout(self, p, monkeypatch):
        import backend.llm_providers.openai_compat as oc

        def boom(_):
            raise RuntimeError("helper gone")

        monkeypatch.setattr(oc, "_stream_read_timeout_sec", boom)
        ROUTER.post(f"{self.BASE}/v1/messages").mock(
            return_value=self._stream(
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
                {"type": "message_stop"},
            )
        )
        seen = []

        res = await p.stream_chat([{"role": "user", "content": "hi"}], "m", lambda c, a: seen.append(c) or True)

        assert res == "ok" and seen == ["ok"]

    async def test_stream_ignores_blank_done_and_broken_lines(self, p):
        body = chr(10).join(
            [
                "event: ping",
                "data: ",
                "data: [DONE]",
                "data: {broken",
                'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Z"}}',
                'data: {"type": "message_stop"}',
                'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "after-stop"}}',
            ]
        )
        ROUTER.post(f"{self.BASE}/v1/messages").mock(
            return_value=httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})
        )

        res = await p.stream_chat([{"role": "user", "content": "hi"}], "m", lambda c, a: True)

        assert res == "Z"

    async def test_stream_skips_non_text_events_and_stops_on_callback_false(self, p):
        ROUTER.post(f"{self.BASE}/v1/messages").mock(
            return_value=self._stream(
                {"type": "ping"},
                {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{"}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ""}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "A"}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "B"}},
            )
        )

        res = await p.stream_chat([{"role": "user", "content": "hi"}], "m", lambda c, a: False)

        assert res == "A"


# ----------------------------------------------------------- openai native


class TestOpenAINative:
    def test_openai_capabilities_and_health_path(self):
        p = make(OpenAIProvider, "openai", "")

        assert p.base_url == "https://api.openai.com"
        assert p.HEALTH_PATH == "/v1/models"
        assert p.capabilities.vision is True and p.capabilities.hot_swap is False

    def test_openrouter_headers_with_referer_and_title(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        p = make(
            OpenRouterProvider,
            "openrouter",
            "",
            extra={"http_referer": "https://astra.test", "x_title": "AstraChat"},
        )

        headers = p._headers()

        assert headers["HTTP-Referer"] == "https://astra.test"
        assert headers["X-Title"] == "AstraChat"

    def test_openrouter_headers_without_extras(self):
        p = make(OpenRouterProvider, "openrouter", "")

        headers = p._headers(accept_sse=True)

        assert "HTTP-Referer" not in headers and "X-Title" not in headers
        assert headers["Accept"] == "text/event-stream"


# ----------------------------------------------------------------- routing


def _status_error(status, *, json_body=None, text=""):
    request = httpx.Request("POST", "http://x/v1/chat")
    if json_body is not None:
        response = httpx.Response(status, json=json_body, request=request)
    else:
        response = httpx.Response(status, text=text, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


class TestFormatLlmHttpErrorTails:
    def test_http_failures_use_user_facing_outage_text(self):
        for status, kwargs in (
            (503, {"json_body": {"detail": "model not loaded"}}),
            (503, {"text": "busy"}),
            (418, {"json_body": {"detail": "teapot"}}),
            (418, {"json_body": {"x": 1}}),
            (418, {"text": ""}),
            (418, {"text": "plain body"}),
            (404, {"text": "Not Found"}),
        ):
            msg = routing.format_llm_http_error(_status_error(status, **kwargs))
            assert msg == routing.LLM_USER_FACING_OUTAGE_TEXT
            assert "for url" not in msg
            assert "teapot" not in msg


class TestBuildChatMessages:
    def test_system_history_and_prompt(self):
        msgs = routing.build_chat_messages(
            "вопрос",
            history=[
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "tool", "content": "skip"},
                {"role": "user", "content": None},
                {"content": "no role"},
            ],
            system_prompt="  Ты помощник ",
        )

        assert msgs == [
            {"role": "system", "content": "Ты помощник"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "вопрос"},
        ]

    def test_blank_system_prompt_is_dropped(self):
        assert routing.build_chat_messages("q", system_prompt="   ") == [{"role": "user", "content": "q"}]


class TestDescribeLlmRoutes:
    async def test_lists_registry_providers(self, monkeypatch):
        class _Prov:
            id = "p1"
            kind = "vllm"
            enabled = True

            def supports_native_function_calling(self):
                return True

            def mcp_tool_calling_mode(self):
                return "auto"

        class _Reg:
            default_id = "p1"

            def all(self):
                return [_Prov()]

        async def get_registry():
            return _Reg()

        monkeypatch.setattr("backend.llm_providers.get_registry", get_registry)

        res = await routing.describe_llm_routes()

        assert res["default_provider"] == "p1"
        assert res["providers"] == [
            {"id": "p1", "kind": "vllm", "enabled": True, "function_calling": True, "mcp_mode": "auto"}
        ]
        assert res["mcp_agent_loop"] == "always ProviderRegistry"

    async def test_registry_failure_is_reported(self, monkeypatch):
        async def get_registry():
            raise RuntimeError("no registry")

        monkeypatch.setattr("backend.llm_providers.get_registry", get_registry)

        assert await routing.describe_llm_routes() == {"error": "no registry"}
