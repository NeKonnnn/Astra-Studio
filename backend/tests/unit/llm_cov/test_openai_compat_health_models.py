"""openai_compat: health, batch/chat-probe фильтр моделей, кэш каталога, диагностика (хвосты)."""

import json

import httpx
import pytest
import respx

from backend.llm_providers import openai_compat as oc
from backend.llm_providers.base import LLMProviderConfig, ModelInfo

pytestmark = pytest.mark.unit

BASE = "http://llm.test"
ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("TLS_CERT_PATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        monkeypatch.delenv(name, raising=False)
    oc._FILTERED_MODELS_CACHE.clear()
    with ROUTER:
        yield
    ROUTER.reset()
    oc._FILTERED_MODELS_CACHE.clear()


def provider(**kw):
    cfg = {"id": "gw", "kind": "openai-compat", "base_url": BASE + "/", **kw}
    return oc.OpenAICompatProvider(LLMProviderConfig(**cfg))


def models(*ids):
    return [ModelInfo(provider_id="gw", model_id=i) for i in ids]


class TestInitAndDiagnostics:
    def test_function_calling_extra_overrides_capabilities(self):
        p = provider(extra={"function_calling": False, "prompt_json_fc": False, "langgraph_agent": False})

        caps = p.capabilities
        assert caps.function_calling is False
        assert caps.prompt_json_fc is False
        assert caps.langgraph_agent is False
        assert caps.vision is True  # остальное наследуется от класса

    def test_default_capabilities_support_function_calling(self):
        assert provider().capabilities.function_calling is True

    def test_safe_auth_diag_retries_without_headers_on_type_error(self, monkeypatch):
        p = provider()
        calls = []

        def old_style(*args):
            calls.append(args)
            if args:
                raise TypeError("старая сигнатура")
            return {"ok": True}

        monkeypatch.setattr(p, "_auth_diag", old_style)

        assert p._safe_auth_diag({"h": "v"}) == {"ok": True}
        assert calls == [({"h": "v"},), ()]

    def test_safe_auth_diag_degrades_when_both_attempts_fail(self, monkeypatch):
        monkeypatch.setenv("GW_KEY", "secret")
        p = provider(api_key_env="GW_KEY")

        def broken(*_args):
            raise TypeError("всегда")

        monkeypatch.setattr(p, "_auth_diag", broken)

        assert p._safe_auth_diag() == {"api_key_env": "GW_KEY", "api_key_set": True}

    def test_safe_auth_diag_degrades_on_unexpected_error(self, monkeypatch):
        p = provider()

        def broken(*_args):
            raise RuntimeError("boom")

        monkeypatch.setattr(p, "_auth_diag", broken)

        assert p._safe_auth_diag() == {"api_key_env": None, "api_key_set": False}

    def test_http_error_details_survives_unreadable_body(self):
        request = httpx.Request("GET", BASE)

        async def chunks():
            yield b"x"

        # у потокового ответа .text бросает ResponseNotRead
        response = httpx.Response(502, request=request, content=chunks())
        err = httpx.HTTPStatusError("bad gateway", request=request, response=response)

        details = provider()._http_error_details(err)

        assert details["status_code"] == 502
        assert details["reason"] == "bad gateway"

    def test_http_error_details_for_plain_exception(self):
        details = provider()._http_error_details(ValueError("oops"))

        assert details == {"error_type": "ValueError", "status_code": None, "reason": "oops"}


class TestDecodeHealth:
    def _resp(self, **kw):
        return httpx.Response(200, request=httpx.Request("GET", BASE), **kw)

    def test_empty_body_is_an_empty_payload(self):
        assert provider()._decode_health_json(self._resp(text="  "), url="u") == {}

    def test_json_list_is_wrapped(self):
        assert provider()._decode_health_json(self._resp(json=[1, 2]), url="u") == {"value": [1, 2]}

    def test_plain_ok_word(self):
        assert provider()._decode_health_json(self._resp(text="OK"), url="u") == {"status": "ok"}

    def test_garbage_gives_none(self):
        assert provider()._decode_health_json(self._resp(text="<html>nope</html>"), url="u") is None

    def test_interpret_skips_non_dict_endpoints_and_duplicates(self):
        payload = {
            "loaded_models": ["a"],
            "healthy_endpoints": ["junk", {"model": "a"}, {"model_name": " b "}, {"model_id": ""}],
        }

        res = provider()._interpret_health_payload(payload)

        assert res.loaded_models == ["a", "b"]
        assert res.healthy is True

    def test_interpret_model_loaded_fields(self):
        res = provider()._interpret_health_payload({"model_loaded": True, "model_name": "m", "status": "down"})

        assert res.loaded_models == ["m"] and res.healthy is False

    def test_litellm_payload_detection(self):
        p = provider()

        assert p._litellm_health_payload(None) is False
        assert p._litellm_health_payload({}) is False
        assert p._litellm_health_payload({"healthy_endpoints": []}) is True


class TestHealthViaModels:
    async def test_non_200_is_unhealthy(self):
        ROUTER.get(f"{BASE}/v1/models").respond(500, text="x")
        async with httpx.AsyncClient() as client:
            res = await provider()._health_via_models(client)

        assert res.healthy is False and res.error == "/v1/models HTTP 500"

    async def test_transport_error_is_unhealthy(self):
        ROUTER.get(f"{BASE}/v1/models").mock(side_effect=httpx.ConnectError("refused"))
        async with httpx.AsyncClient() as client:
            res = await provider()._health_via_models(client)

        assert res.healthy is False and "refused" in res.error


class TestConnectionHealth:
    async def test_non_json_health_falls_back_to_models(self):
        ROUTER.get(f"{BASE}/v1/health").respond(200, text="<html>")
        ROUTER.get(f"{BASE}/v1/models").respond(200, json={"data": [{"id": "m1"}]})

        res = await provider().health()

        assert res.healthy is True and res.loaded_models == ["m1"]

    async def test_non_json_everywhere_without_fallback_reports_error(self):
        p = provider()
        p.HEALTH_FALLBACK_TO_MODELS = False
        for path in ("/v1/health", "/health"):
            ROUTER.get(f"{BASE}{path}").respond(200, text="<html>")

        res = await p.health()

        assert res.healthy is False and "non-JSON" in res.error

    async def test_non_json_with_failed_fallback_tries_next_path(self):
        ROUTER.get(f"{BASE}/v1/health").respond(200, text="<html>")
        ROUTER.get(f"{BASE}/v1/models").respond(500, text="x")
        ROUTER.get(f"{BASE}/health").respond(200, json={"status": "ok", "loaded_models": ["z"]})

        res = await provider().health()

        assert res.healthy is True and res.loaded_models == ["z"]

    async def test_litellm_style_payload_is_not_enriched_from_models(self):
        ROUTER.get(f"{BASE}/v1/health").respond(200, json={"healthy_endpoints": [], "unhealthy_endpoints": []})
        models_route = ROUTER.get(f"{BASE}/v1/models").respond(200, json={"data": [{"id": "x"}]})

        res = await provider().health()

        assert res.healthy is True and res.loaded_models == []
        assert not models_route.called

    async def test_enrichment_without_models_returns_plain_result(self):
        ROUTER.get(f"{BASE}/v1/health").respond(200, json={"status": "ok"})
        ROUTER.get(f"{BASE}/v1/models").respond(200, json={"data": []})

        res = await provider().health()

        assert res.healthy is True and res.loaded_models == []

    async def test_non_200_statuses_are_remembered_as_last_error(self):
        p = provider()
        p.HEALTH_FALLBACK_TO_MODELS = False
        ROUTER.get(f"{BASE}/v1/health").respond(500, text="broken")
        ROUTER.get(f"{BASE}/health").respond(404)

        res = await p.health()

        assert res.healthy is False and res.error.startswith("HTTP 500: broken")

    async def test_no_endpoints_at_all(self):
        p = provider()
        p.HEALTH_FALLBACK_TO_MODELS = False
        ROUTER.get(f"{BASE}/v1/health").respond(404)
        ROUTER.get(f"{BASE}/health").respond(404)

        assert (await p.health()).error == "health endpoints unavailable"

    async def test_transport_error_on_every_path(self):
        p = provider()
        p.HEALTH_FALLBACK_TO_MODELS = False
        ROUTER.get(f"{BASE}/v1/health").mock(side_effect=httpx.ConnectError("refused"))
        ROUTER.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("refused"))

        res = await p.health()

        assert res.healthy is False and "refused" in res.error

    async def test_client_construction_error_is_reported(self, monkeypatch):
        p = provider()

        def boom():
            raise OSError("bad CA bundle")

        monkeypatch.setattr(p, "_http_verify", boom)

        res = await p.health()

        assert res.healthy is False and res.error == "bad CA bundle"

    def test_health_paths_are_deduplicated_and_ordered(self):
        p = provider(extra={"health_path": "/custom"})

        assert p._resolve_health_paths() == ["/custom", "/v1/health", "/health"]
        assert p._listing_health_paths() == ["/custom", "/health", "/v1/health"]

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (None, False),
            ("HTTP 403: forbidden for /health", True),
            ("key not allowed to call route /health", True),
            ("not allowed: health", True),
            ("HTTP 500 on /health", False),
            ("403 on /models", False),
        ],
    )
    def test_forbidden_detection(self, error, expected):
        assert oc.OpenAICompatProvider._health_error_is_forbidden(error) is expected

    def test_timeouts(self):
        p = provider(timeout=240)

        assert p._chat_probe_timeout().read == 30.0
        assert p._health_probe_timeout().read == 120.0
        assert provider(timeout=1)._chat_probe_timeout().read == 15.0
        assert provider(timeout=1)._health_probe_timeout().read == 30.0


class TestBatchHealth:
    async def test_success_returns_payload(self):
        ROUTER.get(f"{BASE}/health").respond(200, json={"unhealthy_endpoints": []})

        payload, err = await provider()._try_litellm_batch_health(timeout=httpx.Timeout(5))

        assert payload == {"unhealthy_endpoints": []} and err is None

    async def test_collects_errors_over_paths(self):
        ROUTER.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
        ROUTER.get(f"{BASE}/v1/health").respond(404)

        payload, err = await provider()._try_litellm_batch_health(timeout=httpx.Timeout(5))

        assert payload is None and err == "refused"

    async def test_http_error_status_and_non_json(self):
        ROUTER.get(f"{BASE}/health").respond(403, text="forbidden")
        ROUTER.get(f"{BASE}/v1/health").respond(200, text="<html>")

        payload, err = await provider()._try_litellm_batch_health(timeout=httpx.Timeout(5))

        assert payload is None and "non-JSON" in err

    async def test_http_error_status_is_the_last_error(self):
        ROUTER.get(f"{BASE}/health").respond(403, text="forbidden")
        ROUTER.get(f"{BASE}/v1/health").respond(404)

        payload, err = await provider()._try_litellm_batch_health(timeout=httpx.Timeout(5))

        assert payload is None and err == "HTTP 403: forbidden"

    async def test_nothing_available(self):
        ROUTER.get(f"{BASE}/health").respond(404)
        ROUTER.get(f"{BASE}/v1/health").respond(404)

        assert await provider()._try_litellm_batch_health(timeout=httpx.Timeout(5)) == (
            None,
            "health endpoints unavailable",
        )

    async def test_client_error_is_returned(self, monkeypatch):
        p = provider()

        def boom():
            raise OSError("no certs")

        monkeypatch.setattr(p, "_http_verify", boom)

        assert await p._try_litellm_batch_health(timeout=httpx.Timeout(5)) == (None, "no certs")


class TestChatProbe:
    CHAT = f"{BASE}/v1/chat/completions"

    async def _probe(self, mid="m"):
        async with httpx.AsyncClient() as client:
            return await provider()._probe_single_model_via_chat(client, mid)

    async def test_blank_model(self):
        assert await self._probe("  ") == ("", False)

    async def test_choices_mean_healthy_and_payload_is_minimal(self):
        route = ROUTER.post(self.CHAT).respond(200, json={"choices": [{}]})

        assert await self._probe() == ("m", True)
        assert json.loads(route.calls.last.request.content) == {
            "model": "m",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }

    async def test_no_choices_is_unhealthy(self):
        ROUTER.post(self.CHAT).respond(200, json={"choices": []})

        assert await self._probe() == ("m", False)

    async def test_non_json_200_counts_as_healthy(self):
        ROUTER.post(self.CHAT).respond(200, text="not json")

        assert await self._probe() == ("m", True)

    async def test_error_status_and_transport_error(self):
        ROUTER.post(self.CHAT).respond(500, text="x")
        assert await self._probe() == ("m", False)
        ROUTER.post(self.CHAT).mock(side_effect=httpx.ConnectError("x"))
        assert await self._probe() == ("m", False)

    async def test_map_runs_all_non_blank_ids(self):
        def handler(request):
            mid = json.loads(request.content)["model"]
            return httpx.Response(200, json={"choices": [{}]}) if mid != "bad" else httpx.Response(500)

        ROUTER.post(self.CHAT).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            res = await provider()._probe_models_via_chat_map(client, ["ok1", " ", "bad", "ok2", None])

        assert res == {"ok1": True, "bad": False, "ok2": True}

    async def test_map_without_ids(self):
        async with httpx.AsyncClient() as client:
            assert await provider()._probe_models_via_chat_map(client, ["", "  "]) == {}


@pytest.fixture
def filter_on(monkeypatch):
    monkeypatch.setattr(oc, "model_health_filter_enabled", lambda cfg: True)
    monkeypatch.setattr(oc, "model_health_probe_max", lambda cfg: 2)


class TestFilterByHealth:
    CHAT = f"{BASE}/v1/chat/completions"

    async def test_disabled_filter_returns_input(self, monkeypatch):
        monkeypatch.setattr(oc, "model_health_filter_enabled", lambda cfg: False)
        items = models("a")

        assert await provider()._filter_models_by_health(items) is items
        assert await provider()._filter_models_by_health([]) == []

    async def test_models_without_ids_are_returned_as_is(self, filter_on):
        items = models("", " ")

        assert await provider()._filter_models_by_health(items) is items

    async def test_litellm_batch_health_excludes_unhealthy(self, filter_on):
        ROUTER.get(f"{BASE}/health").respond(200, json={"unhealthy_endpoints": [{"model": "b"}]})

        res = await provider()._filter_models_by_health(models("a", "b", "c"))

        assert [m.model_id for m in res] == ["a", "c"]

    async def test_litellm_batch_health_all_good(self, filter_on):
        ROUTER.get(f"{BASE}/health").respond(200, json={"unhealthy_endpoints": []})
        items = models("a", "b")

        assert await provider()._filter_models_by_health(items) is items

    async def test_forbidden_health_switches_to_chat_probe(self, filter_on):
        ROUTER.get(f"{BASE}/health").respond(403, text="route /health not allowed")
        ROUTER.get(f"{BASE}/v1/health").respond(403, text="route /health not allowed")
        probed = []

        def handler(request):
            mid = json.loads(request.content)["model"]
            probed.append(mid)
            return httpx.Response(200 if mid == "a" else 500, json={"choices": [{}]})

        ROUTER.post(self.CHAT).mock(side_effect=handler)

        # лимит probe = 2: c не проверяется и остаётся в списке
        res = await provider()._filter_models_by_health(models("a", "b", "c"))

        assert sorted(probed) == ["a", "b"]
        assert [m.model_id for m in res] == ["a", "c"]

    async def test_unavailable_batch_health_switches_to_chat_probe(self, filter_on):
        ROUTER.get(f"{BASE}/health").respond(404)
        ROUTER.get(f"{BASE}/v1/health").respond(404)
        ROUTER.post(self.CHAT).respond(500)
        items = models("a", "b")

        # все пробы упали -> каталог не прячем целиком
        assert await provider()._filter_models_by_health(items) is items

    async def test_unexpected_error_returns_unfiltered(self, filter_on, monkeypatch):
        p = provider()

        async def boom(**_):
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(p, "_try_litellm_batch_health", boom)
        items = models("a")

        assert await p._filter_models_by_health(items) is items


class TestFilteredCache:
    async def test_disabled_returns_input(self, monkeypatch):
        monkeypatch.setattr(oc, "model_health_filter_enabled", lambda cfg: False)
        items = models("a")

        assert await provider()._filter_models_by_health_cached(items) is items

    async def test_second_call_is_served_from_cache(self, filter_on, monkeypatch):
        p = provider()
        calls = []

        async def fake_filter(items):
            calls.append(len(items))
            return items[:1]

        monkeypatch.setattr(p, "_filter_models_by_health", fake_filter)
        items = models("a", "b")

        first = await p._filter_models_by_health_cached(items)
        second = await p._filter_models_by_health_cached(items)

        assert [m.model_id for m in first] == ["a"] and [m.model_id for m in second] == ["a"]
        assert calls == [2]

    async def test_changed_catalog_invalidates_cache(self, filter_on, monkeypatch):
        p = provider()
        calls = []

        async def fake_filter(items):
            calls.append([m.model_id for m in items])
            return items

        monkeypatch.setattr(p, "_filter_models_by_health", fake_filter)

        await p._filter_models_by_health_cached(models("a"))
        await p._filter_models_by_health_cached(models("a", "b"))

        assert calls == [["a"], ["a", "b"]]

    async def test_empty_result_caches_full_list(self, filter_on, monkeypatch):
        p = provider()

        async def nothing(items):
            return []

        monkeypatch.setattr(p, "_filter_models_by_health", nothing)
        items = models("a", "b")

        assert await p._filter_models_by_health_cached(items) == items
        assert [m.model_id for m in oc._FILTERED_MODELS_CACHE["gw"][2]] == ["a", "b"]


class TestEnsureModelLoadedEdges:
    async def test_configured_models_short_circuit(self):
        p = provider(models=["cfg-model"])

        assert await p.ensure_model_loaded("cfg-model") is True

    async def test_listing_failure_without_static_model(self, monkeypatch):
        p = provider()

        async def boom():
            raise RuntimeError("down")

        monkeypatch.setattr(p, "_fetch_model_list_items", boom)

        assert await p.ensure_model_loaded("x") is False

    async def test_listing_failure_with_static_model_for_other_id(self, monkeypatch):
        p = provider(static_model="stat")

        async def boom():
            raise RuntimeError("down")

        monkeypatch.setattr(p, "_fetch_model_list_items", boom)

        assert await p.ensure_model_loaded("other") is False


class TestLogFinishReason:
    def test_broken_choices_are_ignored(self, caplog):
        p = provider()

        # data не dict -> AttributeError внутри, функция молча выходит
        assert p._log_finish_reason(None, model="m") is None
        assert p._log_finish_reason({"choices": None}, model="m") is None
        assert p._log_finish_reason({}, model="m") is None

    def test_normal_reasons_are_silent(self, caplog):
        p = provider()
        with caplog.at_level("WARNING"):
            p._log_finish_reason({"choices": [{"finish_reason": "stop"}]}, model="m")
            p._log_finish_reason({"choices": [{"finish_reason": "tool_calls"}]}, model="m")
            p._log_finish_reason({"choices": [{}]}, model="m")

        assert "finish_reason" not in caplog.text

    def test_abnormal_reason_is_logged_with_extras(self, monkeypatch):
        p = provider()
        warnings = []
        monkeypatch.setattr(oc.logger, "warning", lambda fmt, *a, **k: warnings.append(fmt % a))

        p._log_finish_reason(
            {
                "choices": [{"finish_reason": "content_filter", "message": {"content": "abc"}}],
                "guardrail": {"rule": "pii"},
            },
            model="m",
        )

        assert len(warnings) == 1
        assert "content_filter" in warnings[0] and "pii" in warnings[0] and "длина content=3" in warnings[0]

    def test_abnormal_reason_without_extras(self, monkeypatch):
        p = provider()
        warnings = []
        monkeypatch.setattr(oc.logger, "warning", lambda fmt, *a, **k: warnings.append(fmt % a))

        p._log_finish_reason({"choices": [{"stop_reason": "blocked"}]}, model="m")

        assert "blocked" in warnings[0] and "доп. поля" not in warnings[0]


class TestModuleHelpers:
    def test_env_driven_limits_and_fallbacks(self, monkeypatch):
        monkeypatch.setenv("LLM_AUTO_CONTINUE_MAX", "50")
        assert oc._auto_continue_max() == 20
        monkeypatch.setenv("LLM_AUTO_CONTINUE_MAX", "abc")
        assert oc._auto_continue_max() == 5
        monkeypatch.delenv("LLM_AUTO_CONTINUE_MAX")
        assert oc._auto_continue_max() == 5

        monkeypatch.setenv("LLM_STREAM_READ_TIMEOUT", "10")
        assert oc._stream_read_timeout_sec(3600) == 60.0
        monkeypatch.setenv("LLM_STREAM_READ_TIMEOUT", "bad")
        assert oc._stream_read_timeout_sec(100) == 100.0
        monkeypatch.delenv("LLM_STREAM_READ_TIMEOUT")
        assert oc._stream_read_timeout_sec("oops") == 3600.0

        monkeypatch.setenv("LLM_STREAM_IDLE_TIMEOUT", "5")
        assert oc._stream_idle_timeout_sec() == 30.0
        monkeypatch.setenv("LLM_STREAM_IDLE_TIMEOUT", "x")
        assert oc._stream_idle_timeout_sec() == 300.0
        monkeypatch.delenv("LLM_STREAM_IDLE_TIMEOUT")
        assert oc._stream_idle_timeout_sec() == 300.0

        monkeypatch.setenv("LLM_STREAM_ERROR_RETRIES", "9")
        assert oc._stream_error_retries() == 5
        monkeypatch.setenv("LLM_STREAM_ERROR_RETRIES", "x")
        assert oc._stream_error_retries() == 2

        monkeypatch.setenv("LLM_RATE_LIMIT_RETRIES", "99")
        assert oc._rate_limit_retries() == 10
        monkeypatch.setenv("LLM_RATE_LIMIT_RETRIES", "x")
        assert oc._rate_limit_retries() == 3

    def test_rate_limit_delay(self):
        retry_after = httpx.Response(429, headers={"retry-after": "500"})
        assert oc._rate_limit_delay(retry_after, 0) == 60.0
        assert oc._rate_limit_delay(httpx.Response(429, headers={"retry-after": "0.1"}), 0) == 1.0
        assert oc._rate_limit_delay(httpx.Response(429, headers={"retry-after": "soon"}), 1) == 8.0
        assert oc._rate_limit_delay(httpx.Response(429), 0) == 3.0
        assert oc._rate_limit_delay(httpx.Response(429), 9) == 20.0
        assert oc._rate_limit_delay(object(), 1) == 8.0

    def test_context_fit(self):
        body = "maximum context length of 262144 tokens. you requested 71140 tokens from the input"
        assert oc._context_fit_max_tokens("p", "m", 400, body, 200000) == 262144 - 71140 - 512
        assert oc._context_fit_max_tokens("p", "m", 500, body, 200000) is None
        assert oc._context_fit_max_tokens("p", "m", 400, "", 200000) is None
        assert oc._context_fit_max_tokens("p", "m", 400, "other error", 1000) is None
        assert oc._context_fit_max_tokens("p", "m", 400, body, "many") is None
        # места осталось меньше минимума / max_tokens и так меньше
        assert oc._context_fit_max_tokens("p", "m", 400, body, 100) is None
        small = "maximum context length is 1000 tokens. (900 in the messages, 50 in the completion)"
        assert oc._context_fit_max_tokens("p", "m", 400, small, 500) is None
        openai = "maximum context length is 8192 tokens. However, you requested (1000 in the messages"
        assert oc._context_fit_max_tokens("p", "m", 400, openai, 9000) == 8192 - 1000 - 512

    def test_invoke_stream_callback_variants(self):
        seen = []

        assert oc._invoke_stream_callback(lambda c, a, r: seen.append(r), "c", "a", "reasoning") is True
        assert seen == ["reasoning"]
        # двухаргументный колбэк
        assert oc._invoke_stream_callback(lambda c, a: False, "c", "a") is False
        # heartbeat двухаргументному колбэку не отправляется
        assert oc._invoke_stream_callback(lambda c, a: False, "", "a", "heartbeat") is True
        assert oc._invoke_stream_callback(lambda c, a, r: None, "c", "a") is True

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (None, None),
            ({}, None),
            ({"context_length": 4096}, 4096),
            ({"max_model_len": 8192.0}, 8192),
            ({"n_ctx": True}, None),
            ({"context_window": "32,768"}, 32768),
            ({"context_size": "128k"}, 128000),
            ({"context_size": "1.5M"}, 1500000),
            ({"context_size": "0"}, None),
            ({"context_size": "big"}, None),
            ({"context_size": 0, "max_context_tokens": 5}, 5),
        ],
    )
    def test_parse_context_size(self, data, expected):
        assert oc._parse_context_size_from_mapping(data) == expected

    def test_trace_outbound_probe_counts_calls(self):
        oc._TRACE_CALL_STATE.clear()

        oc._trace_outbound_probe("pp", "op", "http://x")
        oc._trace_outbound_probe("pp", "op", "http://x")

        assert oc._TRACE_CALL_STATE["pp:op"]["count"] == 2.0

    def test_clean_llm_response_unescapes_nested_entities(self):
        assert oc.clean_llm_response("a &amp;amp;amp;amp; b") == "a &amp; b"

    def test_model_in_allowlist(self):
        m = ModelInfo(provider_id="p", model_id="embed/FRIDA", display_name="Frida")

        assert oc._model_in_allowlist(m, ["embed/frida"]) is True
        assert oc._model_in_allowlist(m, ["FRIDA"]) is True
        assert oc._model_in_allowlist(m, ["  frida "]) is True
        assert oc._model_in_allowlist(m, ["other", None]) is False

    def test_log_helpers(self, monkeypatch):
        monkeypatch.setenv("LLM_ERROR_BODY_LOG_CHARS", "bad")
        assert oc._llm_error_body_limit() == 2000
        monkeypatch.setenv("LLM_ERROR_BODY_LOG_CHARS", "99999")
        assert oc._llm_error_body_limit() == 20000
        monkeypatch.setenv("LLM_ERROR_BODY_LOG_CHARS", "10")
        assert oc._cut_for_log("a  b\nc " + "x" * 50).endswith("симв.)")
        assert oc._cut_for_log(None) == ""
        assert oc._cut_for_log("short text", limit=100) == "short text"

    def test_safe_response_text(self):
        async def chunks():
            yield b"x"

        stream_resp = httpx.Response(500, content=chunks())

        assert "тело ответа недоступно" in oc._safe_response_text(stream_resp)
        assert oc._safe_response_text(httpx.Response(500, text="body")) == "body"
        assert oc._safe_response_text(httpx.Response(500)) == ""

    @pytest.mark.parametrize(
        ("body", "expected"),
        [("Request blocked by Guardrail", True), ("content_filter triggered", True), ("заблокирован модерацией", True), ("plain failure", False), (None, False)],
    )
    def test_guardrail_detection(self, body, expected):
        assert oc._looks_like_guardrail(body) is expected

    def test_http_failure_logging_mentions_guardrail(self, monkeypatch):
        warnings = []
        monkeypatch.setattr(oc.logger, "warning", lambda fmt, *a, **k: warnings.append(fmt % a))

        oc._log_llm_http_failure(
            provider_id="gw",
            status_code=400,
            body="blocked by guardrail",
            model="m",
            messages=[{"role": "user", "content": "hello"}, None],
            max_tokens=10,
            has_tools=True,
            payload_keys=["model"],
        )

        assert len(warnings) == 3
        assert "символов в промпте=5" in warnings[1]
        assert "guardrail/модерация" in warnings[2]

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("", ""),
            ("plain", "plain"),
            ("<think>a</think>answer", "answer"),
            ("<THINK>a</THINK>answer", "answer"),
            ("answer<think>unfinished", "answer"),
            ("reasoning only</think>answer", "answer"),
            ("<think>a</think>x</think>y", "y"),
        ],
    )
    def test_strip_think_tags(self, text, expected):
        assert oc._strip_think_tags(text) == expected

    def test_has_think_markers(self):
        assert oc._has_think_markers("") is False
        assert oc._has_think_markers("x </THINK>") is True
        assert oc._has_think_markers("plain") is False

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, ""),
            ("s", "s"),
            (["a", {"text": "b"}, {"content": "c"}, {"reasoning": "d"}, {"x": 1}, 5], "abcd"),
            ({"text": "t"}, "t"),
            ({"reasoning_text": "rt"}, "rt"),
            ({"thinking": "th"}, "th"),
            ({"content": [{"text": "x"}, "y"]}, "xy"),
            ({"my_reason": "r1", "deep_thought": {"text": "r2"}, "other": "no"}, "r1r2"),
            (42, "42"),
        ],
    )
    def test_normalize_reasoning(self, value, expected):
        assert oc._normalize_reasoning_payload(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, ""),
            ("s", "s"),
            (["a", {"type": "text", "text": "b"}, {"type": "output_text", "content": "c"}, {"type": "image", "content": "d"}, {"type": "x", "text": "e"}, {"type": "text", "text": 5}], "abcde"),
            ({"text": "t"}, "t"),
            ({"content": "c"}, "c"),
            ({"content": 5}, ""),
            (7, "7"),
        ],
    )
    def test_normalize_content(self, value, expected):
        assert oc._normalize_content_payload(value) == expected


async def test_next_line_helper_returns_next_item():
    async def gen():
        yield "line-1"

    assert await oc._next_line(gen().__aiter__()) == "line-1"
