"""Anthropic, vLLM, Ollama и llm-svc поверх HTTP (respx): формат запросов, health, список моделей, загрузка моделей."""

import json

import httpx
import pytest
import respx

from backend.llm_providers.anthropic import AnthropicProvider
from backend.llm_providers.base import LLMProviderConfig
from backend.llm_providers.llm_svc import LlmSvcProvider
from backend.llm_providers.ollama import OllamaProvider
from backend.llm_providers.vllm import VLLMProvider

pytestmark = pytest.mark.unit

ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)
MSGS = [{"role": "system", "content": "Будь краток"}, {"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    for name in ("TLS_CERT_PATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "LLM_STREAM_ERROR_RETRIES"):
        monkeypatch.delenv(name, raising=False)
    with ROUTER:
        yield
    ROUTER.reset()


def make(cls, kind, base, **kw):
    return cls(LLMProviderConfig(id=kind, kind=kind, base_url=base, **kw))


def sse(*events):
    body = "".join(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


class TestAnthropic:
    BASE = "http://anthropic.test"

    @pytest.fixture
    def p(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_TEST_KEY", "ak-1")
        return make(AnthropicProvider, "anthropic", self.BASE, api_key_env="ANTHROPIC_TEST_KEY")

    def test_system_message_is_split_out(self):
        system, rest = AnthropicProvider._split_system(MSGS + [{"role": "system", "content": "второй"}])

        assert system == "Будь краток" and [m["role"] for m in rest] == ["user", "system"]

    def test_system_message_with_blocks(self):
        system, _ = AnthropicProvider._split_system([{"role": "system", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": ""}, {"type": "text", "text": "b"}]}])

        assert system == "a\nb"

    def test_headers(self, p):
        h = p._headers()

        assert h["x-api-key"] == "ak-1" and h["anthropic-version"]

    def test_version_override_and_missing_key(self):
        p = make(AnthropicProvider, "anthropic", self.BASE, extra={"anthropic_version": "2099-01-01"})

        h = p._headers()

        assert h["anthropic-version"] == "2099-01-01" and h["x-api-key"] == ""

    async def test_chat_joins_text_blocks(self, p):
        route = ROUTER.post(f"{self.BASE}/v1/messages").mock(
            return_value=httpx.Response(200, json={"content": [{"type": "text", "text": "Привет, "}, {"type": "tool_use"}, {"type": "text", "text": "мир"}]})
        )

        assert await p.chat(MSGS, "claude-x", temperature=0.1, max_tokens=33) == "Привет, мир"
        request = route.calls[0].request
        body = json.loads(request.content)
        assert body == {"model": "claude-x", "messages": [MSGS[1]], "max_tokens": 33, "temperature": 0.1, "stream": False, "system": "Будь краток"}
        assert request.headers["x-api-key"] == "ak-1"

    async def test_chat_http_error_raises(self, p):
        ROUTER.post(f"{self.BASE}/v1/messages").mock(return_value=httpx.Response(401, json={"error": "bad key"}))

        with pytest.raises(httpx.HTTPStatusError):
            await p.chat(MSGS, "m")

    async def test_stream_collects_text_deltas(self, p):
        ROUTER.post(f"{self.BASE}/v1/messages").mock(return_value=sse(
            {"type": "message_start"},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "При"}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{}"}},
            "not json",
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "вет"}},
            {"type": "message_stop"},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "после стопа"}},
        ))
        seen = []

        text = await p.stream_chat(MSGS, "m", lambda c, a: seen.append((c, a)) or True)

        assert text == "Привет" and seen == [("При", "При"), ("вет", "Привет")]

    async def test_stream_callback_can_stop(self, p):
        ROUTER.post(f"{self.BASE}/v1/messages").mock(return_value=sse(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "раз"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "два"}},
        ))

        assert await p.stream_chat(MSGS, "m", lambda c, a: False) == "раз"

    async def test_stream_errors_become_messages(self, p):
        ROUTER.post(f"{self.BASE}/v1/messages").mock(return_value=httpx.Response(529))
        assert await p.stream_chat(MSGS, "m", lambda c, a: True) == "Ошибка Anthropic: HTTP 529"

        ROUTER.post(f"{self.BASE}/v1/messages").mock(side_effect=httpx.ConnectError("refused"))
        assert "Ошибка Anthropic" in await p.stream_chat(MSGS, "m", lambda c, a: True)

    async def test_health_needs_a_key(self):
        health = await make(AnthropicProvider, "anthropic", self.BASE).health()

        assert health.healthy is False and "ключ" in health.error

    async def test_health_via_models(self, p):
        ROUTER.get(f"{self.BASE}/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))
        assert (await p.health()).healthy is True

        ROUTER.get(f"{self.BASE}/v1/models").mock(return_value=httpx.Response(403, text="forbidden"))
        health = await p.health()
        assert health.healthy is False and "403" in health.error

        ROUTER.get(f"{self.BASE}/v1/models").mock(side_effect=httpx.ConnectError("refused"))
        assert (await p.health()).healthy is False

    async def test_list_models(self, p):
        ROUTER.get(f"{self.BASE}/v1/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "claude-a", "display_name": "Claude A"}, {"id": ""}, "x"]}))

        models = await p.list_models()

        assert [(m.model_id, m.display_name) for m in models] == [("claude-a", "Claude A")]

    async def test_list_models_without_key_does_not_call_the_api(self):
        assert await make(AnthropicProvider, "anthropic", self.BASE).list_models() == []
        assert ROUTER.calls.call_count == 0

    async def test_list_models_errors_give_an_empty_list(self, p):
        ROUTER.get(f"{self.BASE}/v1/models").mock(return_value=httpx.Response(500))

        assert await p.list_models() == []

    async def test_ensure_model_loaded_only_checks_the_key(self, p):
        assert await p.ensure_model_loaded("any") is True
        assert await p.ensure_model_loaded(" ") is False
        assert await make(AnthropicProvider, "anthropic", self.BASE).ensure_model_loaded("any") is False


class TestVllm:
    BASE = "http://vllm.test"

    def p(self, **kw):
        return make(VLLMProvider, "vllm", self.BASE, **kw)

    async def test_health_lists_served_models(self):
        ROUTER.get(f"{self.BASE}/health").mock(return_value=httpx.Response(200))
        ROUTER.get(f"{self.BASE}/v1/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "qwen"}]}))

        health = await self.p().health()

        assert health.healthy and health.loaded_models == ["qwen"]

    async def test_health_falls_back_to_the_static_model(self):
        ROUTER.get(f"{self.BASE}/health").mock(return_value=httpx.Response(200))
        ROUTER.get(f"{self.BASE}/v1/models").mock(return_value=httpx.Response(500))

        assert (await self.p(static_model="qwen").health()).loaded_models == ["qwen"]

    async def test_empty_model_list_uses_the_static_model(self):
        ROUTER.get(f"{self.BASE}/health").mock(return_value=httpx.Response(200))
        ROUTER.get(f"{self.BASE}/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))

        assert (await self.p(static_model="qwen").health()).loaded_models == ["qwen"]

    async def test_health_404_uses_models_listing(self):
        ROUTER.get(f"{self.BASE}/health").mock(return_value=httpx.Response(404))
        ROUTER.get(f"{self.BASE}/v1/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "m"}]}))

        assert (await self.p().health()).loaded_models == ["m"]

    async def test_health_server_error_and_connection_error(self):
        ROUTER.get(f"{self.BASE}/health").mock(return_value=httpx.Response(500, text="boom"))
        health = await self.p().health()
        assert health.healthy is False and "500" in health.error

        ROUTER.get(f"{self.BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
        assert (await self.p().health()).healthy is False

    async def test_ensure_model_loaded(self):
        ROUTER.get(f"{self.BASE}/v1/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "qwen"}]}))
        p = self.p(static_model="qwen")

        assert await p.ensure_model_loaded("qwen") is True  # без сети
        assert await self.p().ensure_model_loaded("qwen") is True
        assert await self.p().ensure_model_loaded("other") is False
        assert await self.p().ensure_model_loaded(" ") is False


class TestOllama:
    BASE = "http://ollama.test"

    def p(self, **kw):
        return make(OllamaProvider, "ollama", self.BASE, **kw)

    async def test_health_uses_api_version(self):
        ROUTER.get(f"{self.BASE}/api/version").mock(return_value=httpx.Response(200, json={"version": "0.5"}))

        health = await self.p().health()

        assert health.healthy and health.raw == {"version": "0.5"}

    async def test_health_404_falls_back_to_models(self):
        ROUTER.get(f"{self.BASE}/api/version").mock(return_value=httpx.Response(404))
        ROUTER.get(f"{self.BASE}/v1/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "llama"}]}))

        assert (await self.p().health()).loaded_models == ["llama"]

    async def test_health_error(self):
        ROUTER.get(f"{self.BASE}/api/version").mock(return_value=httpx.Response(500, text="x"))
        assert (await self.p().health()).healthy is False

        ROUTER.get(f"{self.BASE}/api/version").mock(side_effect=httpx.ConnectError("refused"))
        assert (await self.p().health()).healthy is False

    async def test_models_come_from_api_tags(self):
        ROUTER.get(f"{self.BASE}/api/tags").mock(return_value=httpx.Response(200, json={"models": [
            {"name": "llama3:8b", "size": 123, "details": {"family": "llama", "quantization_level": "Q4", "parameter_size": "8B"}},
            {"model": "phi3"},
            {"name": ""},
            "junk",
        ]}))

        models = await self.p().list_models()

        assert [m.model_id for m in models] == ["llama3:8b", "phi3"]
        assert models[0].extra["family"] == "llama" and models[0].extra["quantization"] == "Q4" and models[0].extra["size"] == 123

    async def test_tags_failure_falls_back_to_openai_models(self):
        ROUTER.get(f"{self.BASE}/api/tags").mock(return_value=httpx.Response(500))
        ROUTER.get(f"{self.BASE}/v1/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "fallback"}]}))

        assert [m.model_id for m in await self.p().list_models()] == ["fallback"]

    async def test_ensure_model_loaded_checks_installation(self):
        ROUTER.get(f"{self.BASE}/api/tags").mock(return_value=httpx.Response(200, json={"models": [{"name": "llama3:8b"}]}))

        assert await self.p().ensure_model_loaded("llama3:8b") is True
        assert await self.p().ensure_model_loaded("missing") is False
        assert await self.p().ensure_model_loaded("") is False


class TestLlmSvc:
    BASE = "http://svc.test"

    def p(self, **kw):
        return make(LlmSvcProvider, "llm-svc", self.BASE, **kw)

    def health(self, models):
        return ROUTER.get(f"{self.BASE}/v1/health").mock(return_value=httpx.Response(200, json={"status": "ok", "loaded_models": models}))

    async def test_loaded_model_is_a_noop(self):
        self.health(["qwen"])
        load = ROUTER.post(f"{self.BASE}/v1/models/load").mock(return_value=httpx.Response(200, json={"success": True}))

        assert await self.p().ensure_model_loaded("qwen") is True and not load.called

    async def test_missing_model_is_loaded_through_the_swap_api(self):
        self.health(["other"])
        load = ROUTER.post(f"{self.BASE}/v1/models/load").mock(return_value=httpx.Response(200, json={"success": True}))

        assert await self.p().ensure_model_loaded("qwen") is True
        assert json.loads(load.calls[0].request.content) == {"model": "qwen"}

    @pytest.mark.parametrize("response", [httpx.Response(500, text="oom"), httpx.Response(200, json={"success": False}), httpx.Response(200, json={})])
    async def test_failed_swap(self, response):
        self.health([])
        ROUTER.post(f"{self.BASE}/v1/models/load").mock(return_value=response)

        assert await self.p().ensure_model_loaded("qwen") is False

    async def test_swap_network_error(self):
        self.health([])
        ROUTER.post(f"{self.BASE}/v1/models/load").mock(side_effect=httpx.ConnectError("refused"))

        assert await self.p().ensure_model_loaded("qwen") is False

    async def test_empty_model_id(self):
        assert await self.p().ensure_model_loaded("  ") is False

    async def test_concurrent_requests_load_the_model_once(self):
        import asyncio

        state = {"loaded": []}

        def health(request):
            return httpx.Response(200, json={"status": "ok", "loaded_models": list(state["loaded"])})

        def load(request):
            state["loaded"].append(json.loads(request.content)["model"])
            return httpx.Response(200, json={"success": True})

        ROUTER.get(f"{self.BASE}/v1/health").mock(side_effect=health)
        route = ROUTER.post(f"{self.BASE}/v1/models/load").mock(side_effect=load)
        p = self.p()

        results = await asyncio.gather(*(p.ensure_model_loaded("qwen") for _ in range(4)))

        assert results == [True] * 4 and route.call_count == 1

    async def test_unload_excess(self):
        route = ROUTER.post(f"{self.BASE}/v1/models/unload-excess").mock(return_value=httpx.Response(200, json={"success": True}))
        assert await self.p().unload_excess() is True and route.called

        ROUTER.post(f"{self.BASE}/v1/models/unload-excess").mock(return_value=httpx.Response(200, json={"success": False}))
        assert await self.p().unload_excess() is False

        ROUTER.post(f"{self.BASE}/v1/models/unload-excess").mock(return_value=httpx.Response(500))
        assert await self.p().unload_excess() is False

        ROUTER.post(f"{self.BASE}/v1/models/unload-excess").mock(side_effect=httpx.ConnectError("x"))
        assert await self.p().unload_excess() is False

    async def test_health_does_not_fall_back_to_models(self):
        ROUTER.get(f"{self.BASE}/v1/health").mock(return_value=httpx.Response(404))
        ROUTER.get(f"{self.BASE}/health").mock(return_value=httpx.Response(404))

        health = await self.p().health()

        assert health.healthy is False
