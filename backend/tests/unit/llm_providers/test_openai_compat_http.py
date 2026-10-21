"""OpenAICompatProvider поверх HTTP (respx): chat/tool calls, 429-повторы, SSE-стрим, health, список моделей."""

import json

import httpx
import pytest
import respx

from backend.llm_providers import openai_compat as oc
from backend.llm_providers.base import LLMProviderConfig

pytestmark = pytest.mark.unit

BASE = "http://llm.test"
CHAT = f"{BASE}/v1/chat/completions"
MODELS = f"{BASE}/v1/models"
MSGS = [{"role": "user", "content": "hi"}]
ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("LLM_RATE_LIMIT_RETRIES", "LLM_AUTO_CONTINUE_MAX", "LLM_STREAM_ERROR_RETRIES", "TLS_CERT_PATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        monkeypatch.delenv(name, raising=False)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(oc.asyncio, "sleep", no_sleep)
    oc._FILTERED_MODELS_CACHE.clear()
    with ROUTER:
        yield
    ROUTER.reset()


def provider(**kw):
    cfg = {"id": "gw", "kind": "openai-compat", "base_url": BASE + "/", **kw}
    return oc.OpenAICompatProvider(LLMProviderConfig(**cfg))


def ok(content="Привет", **msg):
    return httpx.Response(200, json={"choices": [{"message": {"content": content, **msg}, "finish_reason": "stop"}]})


def sse(*events, done=True):
    lines = []
    for e in events:
        lines.append("data: " + (e if isinstance(e, str) else json.dumps(e)))
        lines.append("")
    if done:
        lines += ["data: [DONE]", ""]
    return httpx.Response(200, content="\n".join(lines).encode(), headers={"content-type": "text/event-stream"})


def delta(content=None, finish=None, **extra):
    d = {k: v for k, v in {"content": content, **extra}.items() if v is not None}
    return {"choices": [{"delta": d, "finish_reason": finish}]}


class TestHeaders:
    def test_api_key_goes_into_bearer_and_x_api_key(self, monkeypatch):
        monkeypatch.setenv("GW_KEY", "sk-secret")

        headers = provider(api_key_env="GW_KEY")._headers()

        assert headers["Authorization"] == "Bearer sk-secret" and headers["X-API-Key"] == "sk-secret"
        assert headers["Accept"] == "application/json"

    def test_no_key_no_auth_headers(self):
        headers = provider()._headers(accept_sse=True)

        assert "Authorization" not in headers and headers["Accept"] == "text/event-stream"

    def test_auth_diagnostics_never_contain_the_secret(self, monkeypatch):
        monkeypatch.setenv("GW_KEY", "sk-secret")

        assert "sk-secret" not in json.dumps(provider(api_key_env="GW_KEY")._auth_diag())

    def test_base_url_trailing_slash_is_trimmed(self):
        assert provider().base_url == BASE

    @pytest.mark.parametrize(("env", "expected"), [("TLS_CERT_PATH", "/a.pem"), ("SSL_CERT_FILE", "/b.pem"), ("REQUESTS_CA_BUNDLE", "/c.pem")])
    def test_tls_verify_sources(self, monkeypatch, env, expected):
        monkeypatch.setenv(env, expected)

        assert provider()._http_verify() == expected

    def test_tls_verify_defaults_to_true(self):
        assert provider()._http_verify() is True


class TestChatCompletion:
    async def test_plain_answer_and_payload(self):
        route = ROUTER.post(CHAT).mock(return_value=ok("Привет"))

        result = await provider(api_key_env=None).chat_completion(MSGS, "m1", temperature=0.2, max_tokens=50)

        assert result.content == "Привет" and result.tool_calls == []
        body = json.loads(route.calls[0].request.content)
        assert body == {"model": "m1", "messages": MSGS, "temperature": 0.2, "max_tokens": 50, "stream": False}

    async def test_tools_are_sent_with_auto_choice(self):
        route = ROUTER.post(CHAT).mock(return_value=ok())
        tools = [{"type": "function", "function": {"name": "search"}}]

        await provider().chat_completion(MSGS, "m", tools=tools)

        body = json.loads(route.calls[0].request.content)
        assert body["tools"] == tools and body["tool_choice"] == "auto"

    async def test_request_extra_skips_none_values(self):
        route = ROUTER.post(CHAT).mock(return_value=ok())

        await provider().chat_completion(MSGS, "m", request_extra={"top_p": 0.5, "seed": None})

        body = json.loads(route.calls[0].request.content)
        assert body["top_p"] == 0.5 and "seed" not in body

    async def test_tool_calls_are_parsed(self):
        ROUTER.post(CHAT).mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "search", "arguments": '{"q": "x"}'}},
                {"id": "c2", "function": {"name": "broken", "arguments": "{not json"}},
                {"function": {"name": "dict_args", "arguments": {"a": 1}}},
                {"id": "c4", "function": {"name": ""}},
                "garbage",
            ]}}]})
        )

        result = await provider().chat_completion(MSGS, "m")

        by_name = {c.name: c for c in result.tool_calls}
        assert set(by_name) == {"search", "broken", "dict_args"}
        assert by_name["search"].arguments == {"q": "x"} and by_name["search"].id == "c1"
        assert by_name["broken"].arguments == {"raw": "{not json"}  # битый JSON сохраняется как raw
        assert by_name["dict_args"].arguments == {"a": 1} and by_name["dict_args"].id

    async def test_reasoning_is_wrapped_into_think_tags(self):
        ROUTER.post(CHAT).mock(return_value=ok("Ответ", reasoning_content="Думаю"))

        result = await provider().chat_completion(MSGS, "m")

        assert result.content.startswith("<think>Думаю</think>") and result.content.endswith("Ответ")

    async def test_empty_choices_give_a_stub(self):
        ROUTER.post(CHAT).mock(return_value=httpx.Response(200, json={"choices": []}))

        assert (await provider().chat_completion(MSGS, "m")).content == "Ошибка генерации ответа"

    async def test_chat_returns_plain_text(self):
        ROUTER.post(CHAT).mock(return_value=ok("<think>x</think>Итог"))

        assert await provider().chat(MSGS, "m") == "Итог"

    async def test_chat_keeps_think_when_thinking_requested(self):
        ROUTER.post(CHAT).mock(return_value=ok("<think>x</think>Итог"))

        assert "<think>" in await provider().chat(MSGS, "m", request_extra={"enable_thinking": True})

    async def test_reasoning_only_answer_gives_an_empty_string_known_issue(self):
        # Модель отдала только рассуждение: вызывающий код получает "" (в коде это
        # отмечено warning'ом; служебные вызовы принимают пустую строку за ответ).
        ROUTER.post(CHAT).mock(return_value=ok("<think>только мысли</think>"))

        assert await provider().chat(MSGS, "m") == ""


class TestChatCompletionErrors:
    async def test_http_errors_propagate(self):
        ROUTER.post(CHAT).mock(return_value=httpx.Response(500, text="boom"))

        with pytest.raises(httpx.HTTPStatusError) as exc:
            await provider().chat_completion(MSGS, "m")

        assert exc.value.response.status_code == 500

    async def test_429_is_retried_and_succeeds(self):
        route = ROUTER.post(CHAT).mock(side_effect=[httpx.Response(429, headers={"retry-after": "1"}), httpx.Response(429), ok("после паузы")])

        assert (await provider().chat_completion(MSGS, "m")).content == "после паузы"
        assert route.call_count == 3

    async def test_429_gives_up_after_the_configured_retries(self, monkeypatch):
        monkeypatch.setenv("LLM_RATE_LIMIT_RETRIES", "1")
        route = ROUTER.post(CHAT).mock(return_value=httpx.Response(429))

        with pytest.raises(httpx.HTTPStatusError):
            await provider().chat_completion(MSGS, "m")

        assert route.call_count == 2

    async def test_context_overflow_is_retried_with_a_smaller_max_tokens(self):
        overflow = httpx.Response(400, text="maximum context length of 8192 tokens. you requested 5000 tokens from the input")
        route = ROUTER.post(CHAT).mock(side_effect=[overflow, ok("влезло")])

        result = await provider().chat_completion(MSGS, "m", max_tokens=8000)

        assert result.content == "влезло"
        assert json.loads(route.calls[1].request.content)["max_tokens"] <= 8192 - 5000

    async def test_network_errors_propagate(self):
        ROUTER.post(CHAT).mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(httpx.ConnectError):
            await provider().chat_completion(MSGS, "m")

    async def test_timeouts_propagate(self):
        ROUTER.post(CHAT).mock(side_effect=httpx.ReadTimeout("slow"))

        with pytest.raises(httpx.ReadTimeout):
            await provider().chat_completion(MSGS, "m")


class TestStreaming:
    async def run(self, p, callback=None, **kw):
        chunks = []

        def cb(chunk, accumulated, role="content"):
            chunks.append((role, chunk, accumulated))
            return callback(chunk, accumulated, role) if callback else True

        text = await p.stream_chat(MSGS, "m", cb, **kw)
        return text, chunks

    async def test_chunks_are_streamed_and_accumulated(self):
        ROUTER.post(CHAT).mock(return_value=sse(delta("При"), delta("вет"), delta(finish="stop")))

        text, chunks = await self.run(provider())

        assert text == "Привет"
        assert [(c[1], c[2]) for c in chunks if c[0] == "content"] == [("При", "При"), ("вет", "Привет")]

    async def test_request_is_a_streaming_sse_request(self):
        route = ROUTER.post(CHAT).mock(return_value=sse(delta("a", finish="stop")))

        await self.run(provider())

        request = route.calls[0].request
        assert json.loads(request.content)["stream"] is True and request.headers["accept"] == "text/event-stream"

    async def test_garbage_lines_are_skipped(self):
        ROUTER.post(CHAT).mock(return_value=sse(": comment", "not json", {"choices": []}, delta("ok"), delta(finish="stop")))

        text, _ = await self.run(provider())

        assert text == "ok"

    async def test_reasoning_goes_to_its_own_channel_only_when_requested(self):
        events = [delta(reasoning_content="думаю"), delta("ответ"), delta(finish="stop")]
        ROUTER.post(CHAT).mock(return_value=sse(*events))
        _, with_thinking = await self.run(provider(), request_extra={"enable_thinking": True})
        ROUTER.post(CHAT).mock(return_value=sse(*events))
        _, without = await self.run(provider())

        assert ("reasoning", "думаю", "думаю") in with_thinking
        assert not [c for c in without if c[0] == "reasoning"]

    async def test_callback_can_stop_the_stream(self):
        ROUTER.post(CHAT).mock(return_value=sse(delta("раз"), delta("два"), delta("три"), delta(finish="stop")))

        text, chunks = await self.run(provider(), callback=lambda c, a, r: False if r == "content" else True)

        assert text == "раз" and len([c for c in chunks if c[0] == "content"]) == 1

    async def test_chat_template_tags_cut_the_stream(self):
        ROUTER.post(CHAT).mock(return_value=sse(delta("ответ"), delta("<|im_end|>хвост"), delta("не должно попасть")))

        text, _ = await self.run(provider())

        assert "не должно попасть" not in text

    async def test_http_error_becomes_a_message_and_not_an_exception(self):
        ROUTER.post(CHAT).mock(return_value=httpx.Response(401, text="bad key"))

        text, _ = await self.run(provider())

        assert "Ошибка потока" in text

    async def test_503_not_loaded_has_a_specific_message(self):
        ROUTER.post(CHAT).mock(return_value=httpx.Response(503, json={"detail": "model not loaded"}))

        text, _ = await self.run(provider())

        assert "не загружена" in text

    async def test_503_generic(self):
        ROUTER.post(CHAT).mock(return_value=httpx.Response(503, text="busy"))

        text, _ = await self.run(provider())

        assert "недоступен (503)" in text

    async def test_429_in_a_stream_is_retried_before_any_token(self):
        route = ROUTER.post(CHAT).mock(side_effect=[httpx.Response(429), sse(delta("ok", finish="stop"))])

        text, _ = await self.run(provider())

        assert text == "ok" and route.call_count == 2

    async def test_connection_error_returns_what_was_received(self, monkeypatch):
        monkeypatch.setenv("LLM_STREAM_ERROR_RETRIES", "0")
        ROUTER.post(CHAT).mock(side_effect=httpx.ConnectError("refused"))

        text, _ = await self.run(provider())

        assert isinstance(text, str)

    async def test_stream_closed_without_finish_is_continued(self, monkeypatch):
        monkeypatch.setenv("LLM_STREAM_ERROR_RETRIES", "1")
        route = ROUTER.post(CHAT).mock(side_effect=[sse(delta("начало "), done=False), sse(delta("конец"), delta(finish="stop"))])

        text, _ = await self.run(provider())

        assert route.call_count == 2 and "начало" in text and "конец" in text

    async def test_length_finish_triggers_a_continuation_request(self, monkeypatch):
        monkeypatch.setenv("LLM_AUTO_CONTINUE_MAX", "1")
        route = ROUTER.post(CHAT).mock(side_effect=[sse(delta("часть 1 ", finish="length")), sse(delta("часть 2", finish="stop"))])

        text, _ = await self.run(provider())

        assert route.call_count == 2 and text.startswith("часть 1") and text.endswith("часть 2")

    async def test_auto_continue_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("LLM_AUTO_CONTINUE_MAX", "0")
        route = ROUTER.post(CHAT).mock(return_value=sse(delta("обрезано", finish="length")))

        text, _ = await self.run(provider())

        assert route.call_count == 1 and text == "обрезано"


class TestHealth:
    async def test_json_status_ok_with_loaded_models(self):
        ROUTER.get(f"{BASE}/v1/health").mock(return_value=httpx.Response(200, json={"status": "ok", "loaded_models": ["a", "b"]}))

        health = await provider().health()

        assert health.healthy and health.loaded_models == ["a", "b"]

    async def test_plain_text_ok_is_enough_and_models_enrich_the_result(self):
        ROUTER.get(f"{BASE}/v1/health").mock(return_value=httpx.Response(200, text="ok"))
        ROUTER.get(MODELS).mock(return_value=httpx.Response(200, json={"data": [{"id": "m1"}, {"id": ""}, "x"]}))

        health = await provider().health()

        assert health.healthy and health.loaded_models == ["m1"]

    async def test_unhealthy_status(self):
        ROUTER.get(f"{BASE}/v1/health").mock(return_value=httpx.Response(200, json={"status": "degraded"}))
        ROUTER.get(MODELS).mock(return_value=httpx.Response(500))

        assert (await provider().health()).healthy is False

    async def test_missing_health_endpoint_falls_back_to_models(self):
        for path in ("/v1/health", "/health"):
            ROUTER.get(f"{BASE}{path}").mock(return_value=httpx.Response(404))
        ROUTER.get(MODELS).mock(return_value=httpx.Response(200, json={"data": [{"id": "m1"}]}))

        health = await provider().health()

        assert health.healthy and health.loaded_models == ["m1"] and health.raw["fallback"] == "models"

    async def test_everything_down(self):
        ROUTER.route().mock(side_effect=httpx.ConnectError("refused"))

        health = await provider().health()

        assert health.healthy is False and health.error

    async def test_custom_health_path_is_tried_first(self):
        route = ROUTER.get(f"{BASE}/custom").mock(return_value=httpx.Response(200, json={"status": "up", "loaded_models": ["z"]}))

        health = await provider(extra={"health_path": "/custom"}).health()

        assert route.called and health.loaded_models == ["z"]

    async def test_auth_header_is_sent_to_health(self, monkeypatch):
        monkeypatch.setenv("GW_KEY", "sk-1")
        route = ROUTER.get(f"{BASE}/v1/health").mock(return_value=httpx.Response(200, json={"status": "ok", "loaded_models": ["a"]}))

        await provider(api_key_env="GW_KEY").health()

        assert route.calls[0].request.headers["authorization"] == "Bearer sk-1"

    def test_health_payload_interpretation(self):
        p = provider()

        assert p._interpret_health_payload({"model_loaded": True, "model_name": "m"}).loaded_models == ["m"]
        h = p._interpret_health_payload({"healthy_endpoints": [{"model": "a"}, {"model_name": "b"}, "x"], "status": "weird"})
        assert h.healthy and h.loaded_models == ["a", "b"]
        assert p._interpret_health_payload({"status": "ERROR"}).healthy is False
        assert p._interpret_health_payload({}).healthy is True


class TestListModels:
    async def test_models_from_the_gateway(self):
        ROUTER.get(MODELS).mock(return_value=httpx.Response(200, json={"data": [
            {"id": "m1", "name": "Модель 1", "context_length": 32768},
            {"id": "m2"},
            {"id": ""},
            "junk",
        ]}))

        models = await provider()._fetch_model_list_items()

        assert [m.model_id for m in models] == ["m1", "m2"]
        assert models[0].display_name == "Модель 1" and models[0].path == "gw/m1"
        assert models[0].context_size == 32768 and models[1].display_name == "m2"

    async def test_configured_allowlist_filters_the_response(self):
        ROUTER.get(MODELS).mock(return_value=httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]}))

        assert [m.model_id for m in await provider(models=["b", "ghost"])._fetch_model_list_items()] == ["b"]

    async def test_static_model_is_used_when_the_gateway_returns_nothing(self):
        ROUTER.get(MODELS).mock(return_value=httpx.Response(200, json={"data": []}))

        models = await provider(static_model="only")._fetch_model_list_items()

        assert [m.model_id for m in models] == ["only"] and models[0].extra["synthetic"] is True

    async def test_failures_fall_back_to_static_model_or_an_empty_list(self):
        ROUTER.get(MODELS).mock(return_value=httpx.Response(500))

        assert [m.model_id for m in await provider(static_model="only")._fetch_model_list_items()] == ["only"]
        assert await provider()._fetch_model_list_items() == []

    async def test_network_failure_gives_an_empty_list(self):
        ROUTER.get(MODELS).mock(side_effect=httpx.ConnectError("refused"))

        assert await provider()._fetch_model_list_items() == []

    async def test_ensure_model_loaded(self):
        ROUTER.get(MODELS).mock(return_value=httpx.Response(200, json={"data": [{"id": "m1"}]}))
        p = provider()

        assert await p.ensure_model_loaded("m1") is True
        assert await p.ensure_model_loaded("ghost") is False
        assert await p.ensure_model_loaded("  ") is False
        assert await provider(static_model="s").ensure_model_loaded("s") is True
        assert await provider(models=["cfg"]).ensure_model_loaded("cfg") is True

    async def test_ensure_model_loaded_survives_listing_errors(self):
        ROUTER.get(MODELS).mock(side_effect=httpx.ConnectError("x"))

        assert await provider(static_model="s").ensure_model_loaded("other") is False
