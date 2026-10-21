"""llm_client: маршрутизация llm-svc, нормализация ответов, HTTP-методы LLMClient, STT/TTS/диаризация, OCR."""

import io
import json

import httpx
import pytest
import respx
from PIL import Image

from backend import llm_client as lc
from backend.settings.service_toggles import ServiceDisabledError

pytestmark = pytest.mark.unit

BASE = "http://llm.test"
ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)


@pytest.fixture(autouse=True)
def _router():
    with ROUTER:
        yield
    ROUTER.reset()


@pytest.fixture
def client(monkeypatch):
    for name in ("TLS_CERT_PATH", "LLM_VERIFY_SSL", "LLM_API_KEY", "OCR_VISION_MODEL_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(lc, "require_service", lambda key: None)
    c = lc.LLMClient(base_url=BASE + "/")
    c.stt_url, c.tts_url, c.diarization_url = "http://stt.test", "http://tts.test", "http://diar.test"
    return c


@pytest.fixture
def no_registry(monkeypatch):
    """Принудительно уводит клиент на прежний HTTP-путь (реестр провайдеров недоступен)."""

    async def broken():
        raise RuntimeError("registry down")

    monkeypatch.setattr("backend.llm_providers.get_registry", broken)


# ---------------------------------------------------------------- pure helpers


class TestModelIdResolution:
    @pytest.mark.parametrize(
        "ref,expected",
        [
            (None, "fb"),
            ("", "fb"),
            ("   ", "fb"),
            ("llm-svc://qwen-30b", "qwen-30b"),
            ("1lm-svc://qwen-30b", "qwen-30b"),
            ("LLM-SVC://Model", "Model"),
            (" llm-svc:// a b ", "ab"),
            ("models/qwen3.gguf", "qwen3"),
            ("/opt/models/Qwen3-30B-Q4.gguf", "Qwen3-30B-Q4"),
            ("C:\\models\\foo.GGUF", "foo"),
            ("some/provider/model", "model"),
            ("plain-model-name", "fb"),
        ],
    )
    def test_resolve_model_id(self, ref, expected):
        assert lc.resolve_llm_svc_model_id_for_request(ref, "fb") == expected

    @pytest.mark.parametrize(
        "loaded,requested,expected",
        [
            ("qwen", "qwen", True),
            ("QWEN", " qwen ", True),
            ("qwen", "qwen-30b-q4", True),
            ("qwen-30b-q4", "qwen", True),
            ("qwen", "qwen3", False),
            ("", "qwen", False),
            ("qwen", "", False),
        ],
    )
    def test_same_model(self, loaded, requested, expected):
        assert lc.same_llm_svc_model_id(loaded, requested) is expected

    def test_pool_contains_model(self):
        health = {"loaded_models": ["qwen-30b", None, "llama"]}
        assert lc.pool_contains_model(health, "qwen-30b-q4") is True
        assert lc.pool_contains_model(health, "mistral") is False
        assert lc.pool_contains_model({"loaded_models": []}, "x") is False
        assert lc.pool_contains_model({"model_loaded": True, "model_name": "qwen"}, "qwen-30b") is True
        assert lc.pool_contains_model({"model_loaded": False, "model_name": "qwen"}, "qwen") is False
        assert lc.pool_contains_model(None, "x") is False
        assert lc.pool_contains_model({"loaded_models": ["a"]}, "  ") is False
        assert lc.pool_contains_model({"loaded_models": ["a"]}, None) is False

    def test_empty_loaded_models_list_falls_back_to_model_name(self):
        health = {"loaded_models": [], "model_loaded": True, "model_name": "qwen"}
        assert lc.pool_contains_model(health, "qwen") is True

    def test_host_inference_ignores_the_host_map_known_issue(self):
        """Известная особенность: функция всегда возвращает хост по умолчанию — карта хостов не используется."""
        assert lc.infer_llm_host_for_openai_model_id("m", {"a": "u1", "b": "u2"}, "b") == ("b", "m")
        assert lc.infer_llm_host_for_openai_model_id("  ", {"a": "u"}, "a") == ("a", "")


class TestHostAndModelRouting:
    HOSTS = {"main": "http://a", "gpu": "http://b"}

    def test_without_hosts_falls_back_to_default(self):
        assert lc.resolve_llm_host_and_model_for_svc("llm-svc://m", "fb", {}, None) == ("default", "m")
        assert lc.resolve_llm_host_and_model_for_svc(None, "", {}, None) == ("default", "qwen-coder-30b")

    def test_known_host_prefix(self):
        assert lc.resolve_llm_host_and_model_for_svc("llm-svc://gpu/qwen", "fb", self.HOSTS, "main") == ("gpu", "qwen")

    def test_unknown_host_prefix_keeps_the_whole_tail_on_default_host(self):
        out = lc.resolve_llm_host_and_model_for_svc("llm-svc://zzz/qwen", "fb", self.HOSTS, "main")
        assert out == ("main", "zzz/qwen")

    def test_empty_tail_keeps_the_prefix_on_default_host(self):
        assert lc.resolve_llm_host_and_model_for_svc("llm-svc://gpu/", "fb", self.HOSTS, "main") == ("main", "gpu/")

    def test_bare_scheme_uses_fallback_model(self):
        assert lc.resolve_llm_host_and_model_for_svc("llm-svc://", "fb", self.HOSTS, "main") == ("main", "fb")
        assert lc.resolve_llm_host_and_model_for_svc("", "fb", self.HOSTS, "main") == ("main", "fb")

    def test_unknown_default_host_is_replaced_by_the_first(self):
        assert lc.resolve_llm_host_and_model_for_svc("llm-svc://m", "fb", self.HOSTS, "nope")[0] == "main"

    def test_typo_scheme_is_repaired(self):
        out = lc.resolve_llm_host_and_model_for_svc("1lm-svc://gpu/qwen", "fb", self.HOSTS, "main")
        assert out == ("gpu", "qwen")

    def test_filesystem_path_resolves_to_file_stem(self):
        assert lc.resolve_llm_host_and_model_for_svc("models/x.gguf", "fb", self.HOSTS, "main") == ("main", "x")


class TestPayloadNormalisation:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, ""),
            ("text", "text"),
            (["a", {"text": "b"}, {"content": "c"}, {"reasoning": "d"}, 5], "abcd"),
            ({"text": "t"}, "t"),
            ({"reasoning_text": "r"}, "r"),
            ({"thinking": "x"}, "x"),
            ({"thought": "y"}, "y"),
            ({"content": [{"text": "n"}]}, "n"),
            ({"deep_reasoning": "q", "other": "no"}, "q"),
            ({"other": "no"}, ""),
            (42, "42"),
        ],
    )
    def test_reasoning(self, value, expected):
        assert lc._normalize_reasoning_payload(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, ""),
            ("x", "x"),
            ([{"type": "text", "text": "a"}, {"type": "output_text", "content": "b"}, "c"], "abc"),
            ([{"type": "image_url", "content": "d"}, {"type": "x", "text": "e"}, {"type": "x"}], "de"),
            ({"text": "t"}, "t"),
            ({"content": "c"}, "c"),
            ({"content": 5}, ""),
            (3.5, "3.5"),
        ],
    )
    def test_content(self, value, expected):
        assert lc._normalize_content_payload(value) == expected

    def test_clean_response_unescapes_entities_repeatedly(self):
        assert lc._clean_llm_response("a &amp;lt; b") == "a < b"
        assert lc._clean_llm_response("") == ""
        assert lc._clean_llm_response(None) is None

    def test_clean_response_strips_real_chatml_tags(self):
        assert lc._clean_llm_response("Ответ<|im_end|>мусор") == "Ответ"
        assert lc._clean_llm_response("Ответ <|im_start|>user hi") == "Ответ"

    def test_clean_response_keeps_misspelt_variants(self):
        assert lc._clean_llm_response("Ответ<|imend|>мусор") == "Ответ<|imend|>мусор"
        assert lc._clean_llm_response("Ответ <|immmstart|>x") == "Ответ <|immmstart|>x"


class TestThinkTags:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("ответ", "ответ"),
            ("<think>мысли</think>Ответ", "Ответ"),
            ("<THINK>мысли</THINK> Ответ ", "Ответ"),
            ("Ответ<think>недописано", "Ответ"),
            ("рассуждение</think>Ответ", "Ответ"),
            ("<think>a</think>X<think>b</think>Y", "XY"),
            ("", ""),
        ],
    )
    def test_strip(self, text, expected):
        assert lc._strip_think_tags(text) == expected

    def test_markers(self):
        assert lc._has_think_markers("a <think>b") is True
        assert lc._has_think_markers("a </THINK>") is True
        assert lc._has_think_markers("plain") is False
        assert lc._has_think_markers("") is False

    def test_stream_callback_adapter(self):
        calls = []

        def three(chunk, acc, role):
            calls.append(("three", chunk, acc, role))

        def two(chunk, acc):
            calls.append(("two", chunk, acc))

        assert lc._invoke_stream_callback_safe(three, "c", "a", "reasoning") is True
        assert lc._invoke_stream_callback_safe(two, "c", "a") is True
        assert lc._invoke_stream_callback_safe(two, "", "a", "heartbeat") is True
        assert calls == [("three", "c", "a", "reasoning"), ("two", "c", "a")]
        assert lc._invoke_stream_callback_safe(lambda c, a, r: False, "c", "a") is False


class TestSslVerify:
    def test_default_verifies(self, monkeypatch):
        monkeypatch.delenv("TLS_CERT_PATH", raising=False)
        monkeypatch.delenv("LLM_VERIFY_SSL", raising=False)
        assert lc._build_ssl_verify() is True

    @pytest.mark.parametrize("raw", ["false", "0", "no", "FALSE"])
    def test_can_be_disabled(self, monkeypatch, raw):
        monkeypatch.delenv("TLS_CERT_PATH", raising=False)
        monkeypatch.setenv("LLM_VERIFY_SSL", raw)
        assert lc._build_ssl_verify() is False

    def test_cert_path_wins_when_it_exists(self, monkeypatch, tmp_path):
        bundle = tmp_path / "ca.pem"
        bundle.write_text("x")
        monkeypatch.setenv("TLS_CERT_PATH", str(bundle))
        monkeypatch.setenv("LLM_VERIFY_SSL", "false")
        assert lc._build_ssl_verify() == str(bundle)

    def test_missing_cert_file_keeps_verification_on_even_if_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TLS_CERT_PATH", str(tmp_path / "missing.pem"))
        monkeypatch.setenv("LLM_VERIFY_SSL", "false")
        assert lc._build_ssl_verify() is True


# ------------------------------------------------------------------ LLMClient


class TestInit:
    def test_url_hosts_and_defaults(self, client):
        assert client.llm_url == BASE
        assert client.default_llm_host in client.llm_hosts
        assert client._url_for_llm_host(None) == BASE
        assert client._url_for_llm_host("unknown") == BASE

    def test_typo_in_url_is_repaired(self, monkeypatch):
        monkeypatch.setattr(lc, "require_service", lambda key: None)
        c = lc.LLMClient(base_url="http://1lm-svc:8000")
        assert "1lm-svc" not in c.llm_url

    def test_api_key_goes_to_headers(self):
        c = lc.LLMClient(base_url=BASE, api_key="secret")
        headers = c._get_headers()
        assert headers["X-API-Key"] == "secret"
        assert headers["Content-Type"] == "application/json"

    def test_no_api_key_no_header(self, client):
        assert "X-API-Key" not in client._get_headers()

    def test_ocr_availability(self, client):
        client.ocr_url, client.ocr_vision_model_path = None, None
        assert client.ocr_available is False
        client.ocr_vision_model_path = "llm-svc://vision"
        assert client.ocr_available is True
        client.ocr_vision_model_path, client.ocr_url = None, "http://ocr"
        assert client.ocr_available is True

    def test_vision_model_from_env(self, monkeypatch):
        monkeypatch.setenv("OCR_VISION_MODEL_PATH", "llm-svc://vision")
        assert lc.LLMClient(base_url=BASE).ocr_vision_model_path == "llm-svc://vision"

    def test_json_parse_helper_reports_invalid_payload(self, client):
        good = httpx.Response(200, json={"a": 1})
        assert client._parse_json_or_log(good, op_name="x", base_url=BASE) == ({"a": 1}, None)
        bad = httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
        payload, diag = client._parse_json_or_log(bad, op_name="x", base_url=BASE)
        assert payload is None
        assert diag["error"] == "invalid_json_response" and diag["content_type"] == "text/html"


class TestLegacyHealth:
    async def test_first_valid_endpoint_wins_and_status_is_filled(self, client, no_registry):
        ROUTER.get(f"{BASE}/v1/health").respond(200, json={"loaded_models": ["m"]})
        out = await client.health_check()
        assert out["status"] == "healthy" and out["source"] == "/v1/health" and out["loaded_models"] == ["m"]

    async def test_status_from_service_is_kept(self, client, no_registry):
        ROUTER.get(f"{BASE}/v1/health").respond(200, json={"status": "degraded"})
        assert (await client.health_check())["status"] == "degraded"

    async def test_falls_through_failing_endpoints(self, client, no_registry):
        ROUTER.get(f"{BASE}/v1/health").respond(500)
        ROUTER.get(f"{BASE}/health").respond(200, text="not json")
        ROUTER.get(f"{BASE}/v1/models").respond(200, json={"data": [{"id": "a"}, {"id": "b"}]})
        out = await client.health_check()
        assert out == {"status": "healthy", "source": "/v1/models", "models_count": 2}

    async def test_non_dict_payload_is_wrapped(self, client, no_registry):
        ROUTER.get(f"{BASE}/v1/health").respond(200, json=["ok"])
        assert await client.health_check() == {"status": "healthy", "source": "/v1/health", "data": ["ok"]}

    async def test_nothing_valid_is_unhealthy(self, client, no_registry):
        ROUTER.get(f"{BASE}/v1/health").respond(404)
        ROUTER.get(f"{BASE}/health").respond(404)
        ROUTER.get(f"{BASE}/v1/models").respond(200, json={"data": "x"})
        assert await client.health_check() == {"status": "unhealthy", "error": "no_valid_health_endpoint"}

    async def test_network_error_is_unhealthy(self, client, no_registry):
        ROUTER.get(f"{BASE}/v1/health").mock(side_effect=httpx.ConnectError("refused"))
        out = await client.health_check()
        assert out["status"] == "unhealthy" and "refused" in out["error"]

    async def test_api_key_is_sent(self, no_registry):
        c = lc.LLMClient(base_url=BASE, api_key="k")
        route = ROUTER.get(f"{BASE}/v1/health").respond(200, json={})
        await c.health_check()
        assert route.calls.last.request.headers["x-api-key"] == "k"


class TestLegacyModels:
    async def test_returns_data_list(self, client, no_registry):
        ROUTER.get(f"{BASE}/v1/models").respond(200, json={"data": [{"id": "a"}]})
        assert await client.get_models() == [{"id": "a"}]

    @pytest.mark.parametrize(
        "response",
        [httpx.Response(500), httpx.Response(200, text="oops"), httpx.Response(200, json=[1, 2])],
    )
    async def test_bad_answers_give_an_empty_list(self, client, no_registry, response):
        ROUTER.get(f"{BASE}/v1/models").mock(return_value=response)
        assert await client.get_models() == []

    async def test_network_error_gives_an_empty_list(self, client, no_registry):
        ROUTER.get(f"{BASE}/v1/models").mock(side_effect=httpx.ConnectError("x"))
        assert await client.get_models() == []

    async def test_registry_path_maps_provider_models(self, client, monkeypatch):
        class Model:
            model_id, provider_id, path, display_name, context_size, extra = "m", "p", "p://m", None, 4096, None

        class Provider:
            id, kind = "p", "openai-compat"

            async def list_models(self):
                return [Model()]

        class Registry:
            def contains(self, h):
                return h == "p"

            def get(self, pid):
                return Provider()

        async def get_registry():
            return Registry()

        monkeypatch.setattr("backend.llm_providers.get_registry", get_registry)
        out = await client.get_models("p")
        assert out == [
            {"id": "m", "provider_id": "p", "path": "p://m", "display_name": "m", "context_size": 4096, "extra": {}}
        ]


class TestLoadModel:
    async def test_blank_name_is_refused(self, client):
        assert await client.load_model("  ") is False
        assert await client.load_model_if_needed("") is False

    async def test_success_flag_decides(self, client, no_registry):
        route = ROUTER.post(f"{BASE}/v1/models/load").respond(200, json={"success": True})
        assert await client.load_model(" qwen ") is True
        assert json.loads(route.calls.last.request.content) == {"model": "qwen"}

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200, json={"success": False}),
            httpx.Response(500, text="boom"),
            httpx.Response(200, json={}),
        ],
    )
    async def test_failures_return_false(self, client, no_registry, response):
        ROUTER.post(f"{BASE}/v1/models/load").mock(return_value=response)
        assert await client.load_model("qwen") is False

    async def test_network_error_returns_false(self, client, no_registry):
        ROUTER.post(f"{BASE}/v1/models/load").mock(side_effect=httpx.ConnectError("x"))
        assert await client.load_model("qwen") is False

    async def test_registry_result_is_used(self, client, monkeypatch):
        class Provider:
            id, kind = "p", "k"

            async def ensure_model_loaded(self, name):
                return name == "ok"

        class Registry:
            def contains(self, h):
                return False

            def get(self, pid):
                return Provider()

        async def get_registry():
            return Registry()

        monkeypatch.setattr("backend.llm_providers.get_registry", get_registry)
        assert await client.load_model("ok") is True
        assert await client.load_model("other") is False

    async def test_load_if_needed_skips_when_already_in_pool(self, client, monkeypatch):
        async def health(host_id=None):
            return {"loaded_models": ["qwen-30b"]}

        async def load(name, host_id=None):
            raise AssertionError("не должен вызываться")

        monkeypatch.setattr(client, "health_check", health)
        monkeypatch.setattr(client, "load_model", load)
        assert await client.load_model_if_needed("qwen") is True

    async def test_load_if_needed_loads_when_missing(self, client, monkeypatch):
        async def health(host_id=None):
            return {"loaded_models": []}

        async def load(name, host_id=None):
            return name == "qwen"

        monkeypatch.setattr(client, "health_check", health)
        monkeypatch.setattr(client, "load_model", load)
        assert await client.load_model_if_needed("qwen") is True


class TestUnloadExcess:
    async def test_all_hosts_must_succeed(self, client):
        client.llm_hosts = {"a": "http://a.test", "b": "http://b.test"}
        ROUTER.post("http://a.test/v1/models/unload-excess").respond(200, json={"success": True})
        ROUTER.post("http://b.test/v1/models/unload-excess").respond(200, json={"success": False})
        assert await client.unload_excess_llm_models() is False

    async def test_single_host_success(self, client):
        client.llm_hosts = {"a": "http://a.test", "b": "http://b.test"}
        ROUTER.post("http://a.test/v1/models/unload-excess").respond(200, json={})
        assert await client.unload_excess_llm_models("a") is True

    async def test_http_and_network_errors_are_failures(self, client):
        client.llm_hosts = {"a": "http://a.test", "b": "http://b.test"}
        ROUTER.post("http://a.test/v1/models/unload-excess").respond(500, text="x")
        ROUTER.post("http://b.test/v1/models/unload-excess").mock(side_effect=httpx.ConnectError("x"))
        assert await client.unload_excess_llm_models() is False


class TestChatCompletion:
    MSGS = [{"role": "user", "content": "hi"}]

    async def test_payload_and_result(self, client):
        route = ROUTER.post(f"{BASE}/v1/chat/completions").respond(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )
        out = await client.chat_completion(
            self.MSGS, model="m", temperature=0.2, max_tokens=64, request_extra={"top_p": 0.9, "skip": None}
        )
        assert out["choices"][0]["message"]["content"] == "ok"
        body = json.loads(route.calls.last.request.content)
        assert body == {
            "model": "m",
            "messages": self.MSGS,
            "temperature": 0.2,
            "max_tokens": 64,
            "stream": False,
            "top_p": 0.9,
        }

    async def test_routes_to_the_requested_host(self, client):
        client.llm_hosts["gpu"] = "http://gpu.test"
        route = ROUTER.post("http://gpu.test/v1/chat/completions").respond(200, json={})
        await client.chat_completion(self.MSGS, host_id="gpu")
        assert route.called

    @pytest.mark.parametrize("status", [400, 429, 500])
    async def test_http_errors_are_raised(self, client, status):
        ROUTER.post(f"{BASE}/v1/chat/completions").respond(status, text="e")
        with pytest.raises(httpx.HTTPStatusError):
            await client.chat_completion(self.MSGS)

    async def test_network_errors_are_raised(self, client):
        ROUTER.post(f"{BASE}/v1/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(httpx.ReadTimeout):
            await client.chat_completion(self.MSGS)


class TestAudioServices:
    async def test_health_checks(self, client):
        ROUTER.get("http://stt.test/v1/whisperx/health").respond(200, json={"status": "ok"})
        ROUTER.get("http://tts.test/v1/health").respond(503)
        assert await client.get_transcription_health() == {"status": "ok"}
        assert await client.get_tts_health() == {"status": "unhealthy", "code": 503}

    async def test_health_check_network_error(self, client):
        ROUTER.get("http://stt.test/v1/whisperx/health").mock(side_effect=httpx.ConnectError("x"))
        out = await client.get_transcription_health()
        assert out["status"] == "unhealthy" and "x" in out["error"]

    async def test_transcribe_sends_multipart_with_content_type_by_extension(self, client):
        route = ROUTER.post("http://stt.test/v1/whisperx/transcribe").respond(200, json={"text": "привет"})
        out = await client.transcribe_audio_whisperx(b"RIFF", filename="voice.MP3", language="ru", batch_size=4)
        assert out == {"text": "привет"}
        body = route.calls.last.request.content
        assert b'filename="voice.MP3"' in body and b"audio/mpeg" in body and b"RIFF" in body
        assert b'name="language"' in body and b"ru" in body

    @pytest.mark.parametrize(
        "name,mime",
        [("a.webm", b"audio/webm"), ("a.ogg", b"audio/ogg"), ("a.m4a", b"audio/mp4"), ("a.unknown", b"audio/wav")],
    )
    async def test_content_type_table(self, client, name, mime):
        route = ROUTER.post("http://stt.test/v1/whisperx/transcribe").respond(200, json={})
        await client.transcribe_audio_whisperx(b"x", filename=name)
        assert mime in route.calls.last.request.content

    async def test_transcribe_400_becomes_a_soft_error(self, client):
        ROUTER.post("http://stt.test/v1/whisperx/transcribe").respond(400, json={"error": "bad audio"})
        assert await client.transcribe_audio_whisperx(b"x") == {"success": False, "text": "", "error": "bad audio"}

    async def test_transcribe_400_with_plain_text(self, client):
        ROUTER.post("http://stt.test/v1/whisperx/transcribe").respond(400, text="x" * 500)
        out = await client.transcribe_audio_whisperx(b"x")
        assert out["success"] is False and len(out["error"]) == 300

    async def test_transcribe_server_error_is_raised(self, client):
        ROUTER.post("http://stt.test/v1/whisperx/transcribe").respond(500)
        with pytest.raises(httpx.HTTPStatusError):
            await client.transcribe_audio_whisperx(b"x")

    async def test_reload_models(self, client):
        ROUTER.post("http://stt.test/v1/whisperx/reload").respond(200, json={"reloaded": True})
        assert await client.reload_whisperx_models() == {"reloaded": True}

    async def test_synthesize_returns_bytes_and_sends_form(self, client):
        route = ROUTER.post("http://tts.test/v1/synthesize").respond(200, content=b"WAVDATA")
        assert await client.synthesize_speech("Привет", speaker="xenia", speech_rate=1.2) == b"WAVDATA"
        form = route.calls.last.request.content.decode()
        assert "speaker=xenia" in form and "speech_rate=1.2" in form
        assert route.calls.last.request.headers["accept"] == "audio/wav"

    async def test_synthesize_error_is_raised(self, client):
        ROUTER.post("http://tts.test/v1/synthesize").respond(500)
        with pytest.raises(httpx.HTTPStatusError):
            await client.synthesize_speech("x")

    async def test_diarize(self, client):
        route = ROUTER.post("http://diar.test/v1/diarize").respond(200, json={"segments": []})
        assert await client.diarize_audio(b"x", min_speakers=2, max_speakers=3) == {"segments": []}
        assert b'name="min_speakers"' in route.calls.last.request.content

    async def test_disabled_services_are_refused_before_any_request(self):
        c = lc.LLMClient(base_url=BASE)
        c.stt_url = c.tts_url = c.diarization_url = "http://x.test"
        for call in (
            lambda: c.transcribe_audio_whisperx(b"x"),
            lambda: c.synthesize_speech("x"),
            lambda: c.diarize_audio(b"x"),
            lambda: c.transcribe_with_diarization(b"x"),
        ):
            with pytest.raises(ServiceDisabledError):
                await call()
        assert ROUTER.calls.call_count == 0


class TestTranscribeWithDiarization:
    def patch(self, client, monkeypatch, diar, stt):
        async def diarize(*a, **k):
            return diar

        async def transcribe(*a, **k):
            return stt

        monkeypatch.setattr(client, "diarize_audio", diarize)
        monkeypatch.setattr(client, "transcribe_audio_whisperx", transcribe)

    async def test_no_speech_segments_gives_empty_result_without_stt(self, client, monkeypatch):
        calls = []

        async def transcribe(*a, **k):
            calls.append(1)

        async def diarize(*a, **k):
            return {"segments": []}

        monkeypatch.setattr(client, "diarize_audio", diarize)
        monkeypatch.setattr(client, "transcribe_audio_whisperx", transcribe)
        assert await client.transcribe_with_diarization(b"x") == {
            "success": True,
            "text": "",
            "segments": [],
            "speakers_count": 0,
        }
        assert calls == []

    async def test_word_level_matching_merges_same_speaker_runs(self, client, monkeypatch):
        diar = {
            "speakers_count": 2,
            "segments": [{"start": 0, "end": 2, "speaker": "A"}, {"start": 2, "end": 4, "speaker": "B"}],
        }
        stt = {
            "text": "привет как дела отлично",
            "segments": [
                {
                    "start": 0,
                    "end": 4,
                    "words": [
                        {"word": "привет", "start": 0.0, "end": 0.9},
                        {"word": "как", "start": 1.0, "end": 1.5},
                        {"word": "дела", "start": 2.1, "end": 2.8},
                        {"word": "отлично", "start": 3.0, "end": 3.9},
                    ],
                }
            ],
        }
        self.patch(client, monkeypatch, diar, stt)
        out = await client.transcribe_with_diarization(b"x")
        assert out["speakers_count"] == 2 and out["text"] == "привет как дела отлично"
        assert out["segments"] == [
            {"start": 0.0, "end": 1.5, "speaker": "A", "text": "привет как"},
            {"start": 2.1, "end": 3.9, "speaker": "B", "text": "дела отлично"},
        ]

    async def test_word_without_overlap_goes_to_the_default_speaker(self, client, monkeypatch):
        diar = {"segments": [{"start": 10, "end": 12, "speaker": "A"}]}
        stt = {"text": "х", "segments": [{"start": 0, "end": 1, "words": [{"word": "х", "start": 0, "end": 1}]}]}
        self.patch(client, monkeypatch, diar, stt)
        out = await client.transcribe_with_diarization(b"x")
        assert out["segments"][0]["speaker"] == "SPEAKER_0"

    async def test_segment_level_matching_single_overlap(self, client, monkeypatch):
        diar = {"segments": [{"start": 0, "end": 5, "speaker": "A"}, {"start": 5, "end": 10, "speaker": "B"}]}
        stt = {
            "text": "один два",
            "segments": [{"start": 1, "end": 2, "text": "один"}, {"start": 6, "end": 7, "text": "два"}],
        }
        self.patch(client, monkeypatch, diar, stt)
        out = await client.transcribe_with_diarization(b"x")
        assert [(s["speaker"], s["text"]) for s in out["segments"]] == [("A", "один"), ("B", "два")]

    async def test_segment_spanning_two_speakers_is_split_by_overlap(self, client, monkeypatch):
        diar = {"segments": [{"start": 0, "end": 3, "speaker": "A"}, {"start": 3, "end": 6, "speaker": "B"}]}
        stt = {"text": "а б в г", "segments": [{"start": 0, "end": 6, "text": "а б в г"}]}
        self.patch(client, monkeypatch, diar, stt)
        out = await client.transcribe_with_diarization(b"x")
        assert [(s["speaker"], s["text"]) for s in out["segments"]] == [("A", "а б"), ("B", "в г")]

    async def test_segment_in_a_gap_uses_the_nearest_speaker(self, client, monkeypatch):
        diar = {"segments": [{"start": 0, "end": 1, "speaker": "A"}, {"start": 20, "end": 21, "speaker": "B"}]}
        stt = {"text": "т", "segments": [{"start": 18, "end": 19, "text": "т"}]}
        self.patch(client, monkeypatch, diar, stt)
        assert (await client.transcribe_with_diarization(b"x"))["segments"][0]["speaker"] == "B"

    async def test_without_timestamps_words_are_spread_by_duration(self, client, monkeypatch):
        diar = {"segments": [{"start": 0, "end": 2, "speaker": "A"}, {"start": 2, "end": 6, "speaker": "B"}]}
        self.patch(client, monkeypatch, diar, {"text": "раз два три четыре пять шесть"})
        out = await client.transcribe_with_diarization(b"x")
        assert [s["speaker"] for s in out["segments"]] == ["A", "B"]
        assert " ".join(s["text"] for s in out["segments"]) == "раз два три четыре пять шесть"
        assert out["segments"][0]["text"] == "раз два"

    async def test_non_dict_transcription_is_stringified(self, client, monkeypatch):
        self.patch(client, monkeypatch, {"segments": [{"start": 0, "end": 1, "speaker": "A"}]}, "просто текст")
        out = await client.transcribe_with_diarization(b"x")
        assert out["text"] == "просто текст" and out["segments"][0]["text"] == "просто текст"

    async def test_upstream_failures_propagate(self, client, monkeypatch):
        async def diarize(*a, **k):
            raise httpx.ConnectError("diar down")

        monkeypatch.setattr(client, "diarize_audio", diarize)
        with pytest.raises(httpx.ConnectError):
            await client.transcribe_with_diarization(b"x")


class TestOcr:
    def png(self, size=(10, 10), mode="RGBA"):
        buf = io.BytesIO()
        Image.new(mode, size, (255, 0, 0, 255) if mode == "RGBA" else 128).save(buf, format="PNG")
        return buf.getvalue()

    def test_resize_for_vision_keeps_small_images_and_converts_to_jpeg(self):
        out = lc._resize_for_vision(self.png((100, 50)))
        img = Image.open(io.BytesIO(out))
        assert img.format == "JPEG" and img.size == (100, 50)

    def test_resize_for_vision_shrinks_large_images(self):
        out = lc._resize_for_vision(self.png((4096, 1024)), max_side=1024)
        assert Image.open(io.BytesIO(out)).size == (1024, 256)

    def test_resize_rejects_non_images(self):
        with pytest.raises(Exception):
            lc._resize_for_vision(b"not an image")

    async def test_recognize_without_configuration_is_an_error(self, client):
        client.ocr_vision_model_path = None
        with pytest.raises(RuntimeError, match="OCR не настроен"):
            await client.recognize_text_from_image(b"x")
        with pytest.raises(RuntimeError, match="не настроен"):
            await client.recognize_text_from_image_via_llm(b"x")

    async def test_vision_fallback_sends_the_image_and_strips_text(self, client, monkeypatch):
        client.ocr_vision_model_path = "llm-svc://vision"
        seen = {}

        class Provider:
            id, kind = "p", "k"

            async def chat(self, messages, model, temperature, max_tokens):
                seen.update(messages=messages, model=model, temperature=temperature, max_tokens=max_tokens)
                return "  Распознанный текст \n"

        class Registry:
            def resolve(self, path):
                return Provider(), "vision-model"

        async def get_registry():
            return Registry()

        monkeypatch.setattr("backend.llm_providers.get_registry", get_registry)

        out = await client.recognize_text_from_image(self.png(), "scan.png")

        assert out == {"text": "Распознанный текст"}
        assert seen["model"] == "vision-model" and seen["temperature"] == 0.0 and seen["max_tokens"] == 4096
        parts = seen["messages"][0]["content"]
        assert parts[0]["type"] == "text"
        assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    async def test_non_string_answer_is_stringified(self, client, monkeypatch):
        client.ocr_vision_model_path = "llm-svc://vision"

        class Provider:
            id, kind = "p", "k"

            async def chat(self, **kw):
                return None

        class Registry:
            def resolve(self, path):
                return Provider(), None

        async def get_registry():
            return Registry()

        monkeypatch.setattr("backend.llm_providers.get_registry", get_registry)
        assert await client.recognize_text_from_image_via_llm(self.png()) == {"text": ""}

    async def test_provider_errors_propagate(self, client, monkeypatch):
        client.ocr_vision_model_path = "llm-svc://vision"

        async def get_registry():
            raise RuntimeError("registry down")

        monkeypatch.setattr("backend.llm_providers.get_registry", get_registry)
        with pytest.raises(RuntimeError, match="registry down"):
            await client.recognize_text_from_image_via_llm(self.png())


# ------------------------------------------------------------------ LLMService


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(lc, "require_service", lambda key: None)
    return lc.LLMService(base_url=BASE)


class TestPrepareMessages:
    def test_plain_prompt(self, service):
        assert service.prepare_messages("привет") == [{"role": "user", "content": "привет"}]

    def test_explicit_system_prompt_wins(self, service):
        out = service.prepare_messages("q", history=[{"role": "user", "content": "h"}], system_prompt="Будь краток")
        assert out[0] == {"role": "system", "content": "Будь краток"}
        assert [m["role"] for m in out] == ["system", "user", "user"]

    def test_history_without_system_prompt_adds_the_default_one(self, service):
        out = service.prepare_messages("q", history=[{"role": "assistant", "content": "a"}])
        assert out[0]["role"] == "system" and "истори" in out[0]["content"].lower()

    def test_history_order_is_preserved_and_bad_roles_dropped(self, service):
        history = [
            {"role": "user", "content": "1"},
            {"role": "tool", "content": "x"},
            {"role": "assistant", "content": "2"},
            {"content": "3"},
        ]
        out = service.prepare_messages("q", history=history, system_prompt="s")
        assert [(m["role"], m["content"]) for m in out[1:-1]] == [("user", "1"), ("assistant", "2"), ("user", "3")]
        assert out[-1] == {"role": "user", "content": "q"}

    def test_long_messages_are_truncated(self, service):
        out = service.prepare_messages("q", history=[{"role": "user", "content": "я" * 9000}], system_prompt="s")
        assert out[1]["content"].endswith("... [обрезано]") and len(out[1]["content"]) == 8000 + len("... [обрезано]")

    def test_attached_documents_get_a_much_larger_limit(self, service):
        body = "[Прикреплённый документ]" + "я" * 50_000
        out = service.prepare_messages("q", history=[{"role": "user", "content": body}], system_prompt="s")
        assert out[1]["content"] == body

    def test_history_is_cut_from_the_oldest_end_when_over_budget(self, service):
        marker = "[Прикреплённый документ]"
        history = [{"role": "user", "content": marker + f"{i}" * 90_000} for i in range(10)]
        out = service.prepare_messages("q", history=history, system_prompt="s")
        kept = [m["content"][len(marker)] for m in out[1:-1]]
        assert len(kept) < 10
        assert kept == [str(i) for i in range(10 - len(kept), 10)]  # остаются самые свежие

    def test_empty_history_list_adds_no_system_message(self, service):
        assert [m["role"] for m in service.prepare_messages("q", history=[])] == ["user"]


class TestThinkingExtra:
    def test_none_means_no_extra(self, service):
        assert service._thinking_request_extra(None) is None

    def test_flag_is_forwarded(self, service):
        assert service._thinking_request_extra(True) != service._thinking_request_extra(False)
        assert service._thinking_request_extra(False)


class TestServiceInitialize:
    async def test_first_healthy_host_with_loaded_model(self, service, monkeypatch):
        async def health(host_id=None):
            return {"status": "healthy", "model_loaded": True, "model_name": "qwen"}

        monkeypatch.setattr(service.client, "health_check", health)
        assert await service.initialize() is True
        assert service.model_name == "qwen"

    async def test_falls_back_to_the_first_listed_model(self, service, monkeypatch):
        async def health(host_id=None):
            return {"status": "healthy"}

        async def models(host_id=None):
            return [{"id": "first"}, {"id": "second"}]

        monkeypatch.setattr(service.client, "health_check", health)
        monkeypatch.setattr(service.client, "get_models", models)
        assert await service.initialize() is True
        assert service.model_name == "first"

    async def test_unhealthy_hosts_are_skipped(self, service, monkeypatch):
        service.client.llm_hosts = {"a": "http://a", "b": "http://b"}
        seen = []

        async def health(host_id=None):
            seen.append(host_id)
            return {"status": "healthy" if host_id == "b" else "unhealthy"}

        async def models(host_id=None):
            return []

        monkeypatch.setattr(service.client, "health_check", health)
        monkeypatch.setattr(service.client, "get_models", models)
        assert await service.initialize() is True
        assert seen == ["a", "b"]

    async def test_nothing_available_returns_false(self, service, monkeypatch):
        async def health(host_id=None):
            return {"status": "unhealthy"}

        monkeypatch.setattr(service.client, "health_check", health)
        assert await service.initialize() is False

    async def test_errors_are_absorbed(self, service, monkeypatch):
        async def health(host_id=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(service.client, "health_check", health)
        assert await service.initialize() is False

    async def test_sync_loaded_model_name(self, service, monkeypatch):
        async def health(host_id=None):
            return {"status": "healthy", "model_loaded": True, "model_name": "actual"}

        monkeypatch.setattr(service.client, "health_check", health)
        ok, payload = await service._sync_loaded_model_name_from_health()
        assert ok is True and service.model_name == "actual" and payload["model_name"] == "actual"

    async def test_sync_without_loaded_model_reports_false(self, service, monkeypatch):
        service.model_name = "keep"

        async def health(host_id=None):
            return {"status": "healthy", "model_loaded": False}

        monkeypatch.setattr(service.client, "health_check", health)
        assert (await service._sync_loaded_model_name_from_health())[0] is False
        assert service.model_name == "keep"

    async def test_sync_on_unhealthy_or_error(self, service, monkeypatch):
        async def unhealthy(host_id=None):
            return {"status": "unhealthy"}

        monkeypatch.setattr(service.client, "health_check", unhealthy)
        assert (await service._sync_loaded_model_name_from_health())[0] is False

        async def boom(host_id=None):
            raise RuntimeError("x")

        monkeypatch.setattr(service.client, "health_check", boom)
        assert await service._sync_loaded_model_name_from_health() == (False, {})

    async def test_delegating_helpers(self, service, monkeypatch):
        async def synth(*a, **k):
            return b"audio"

        monkeypatch.setattr(service.client, "synthesize_speech", synth)
        assert await service.synthesize_speech("x") == b"audio"


# ------------------------------------------------------------ generate_response


class FakeProvider:
    def __init__(self, kind="openai-compat", hot_swap=True, models=None, load_result=True, load_error=None):
        self.id, self.kind = "gw", kind
        self.capabilities = type("Caps", (), {"hot_swap": hot_swap})()
        self.models = models or []
        self.load_result, self.load_error = load_result, load_error
        self.chat_calls, self.stream_calls, self.loaded = [], [], []
        self.chat_result = "ответ провайдера"

    async def list_models(self):
        return [type("M", (), {"model_id": m})() for m in self.models]

    async def ensure_model_loaded(self, model):
        self.loaded.append(model)
        if self.load_error:
            raise self.load_error
        return self.load_result

    async def chat(self, **kw):
        self.chat_calls.append(kw)
        return self.chat_result

    async def stream_chat(self, **kw):
        self.stream_calls.append(kw)
        return "потоковый ответ"


def install_registry(monkeypatch, provider, resolved=None, error=None):
    class Registry:
        def resolve(self, path):
            if error:
                raise error
            return provider, resolved

    async def get_registry():
        return Registry()

    monkeypatch.setattr("backend.llm_providers.get_registry", get_registry)


@pytest.fixture
def svc(service, monkeypatch):
    async def sync(host_id=None):
        return (False, {})

    monkeypatch.setattr(service, "_sync_loaded_model_name_from_health", sync)
    return service


class TestGenerateViaProvider:
    async def test_delegates_chat_to_a_non_llm_svc_provider(self, svc, monkeypatch):
        provider = FakeProvider()
        install_registry(monkeypatch, provider, resolved="gpt-x")

        out = await svc.generate_response(
            "вопрос", history=[{"role": "user", "content": "h"}], system_prompt="sys", temperature=0.3, max_tokens=77,
            model_path="gw://gpt-x",
        )

        assert out == "ответ провайдера"
        call = provider.chat_calls[0]
        assert call["model"] == "gpt-x" and call["temperature"] == 0.3 and call["max_tokens"] == 77
        assert call["messages"][0] == {"role": "system", "content": "sys"}
        assert call["messages"][-1] == {"role": "user", "content": "вопрос"}
        assert provider.loaded == ["gpt-x"]

    async def test_streaming_uses_stream_chat_when_callback_given(self, svc, monkeypatch):
        provider = FakeProvider()
        install_registry(monkeypatch, provider, resolved="m")
        cb = lambda chunk, acc, role="content": True  # noqa: E731
        out = await svc.generate_response("q", streaming=True, stream_callback=cb, model_path="gw://m")
        assert out == "потоковый ответ" and provider.stream_calls[0]["callback"] is cb and not provider.chat_calls

    async def test_streaming_without_callback_is_a_plain_chat(self, svc, monkeypatch):
        provider = FakeProvider()
        install_registry(monkeypatch, provider, resolved="m")
        await svc.generate_response("q", streaming=True, model_path="gw://m")
        assert provider.chat_calls and not provider.stream_calls

    async def test_no_model_path_uses_the_configured_default_model(self, svc, monkeypatch):
        provider = FakeProvider(models=["first"])
        install_registry(monkeypatch, provider, resolved="")
        await svc.generate_response("q", model_path=None)
        assert provider.chat_calls[0]["model"] == svc.model_name

    async def test_provider_prefix_without_model_becomes_a_bogus_model_name_known_issue(self, svc, monkeypatch):
        """Известная проблема: ``gw://`` без модели разбирается как путь файла и даёт имя ``gw:``,
        а автовыбор первой модели провайдера не срабатывает (имя уже «не пустое»)."""
        provider = FakeProvider(models=["first"])
        install_registry(monkeypatch, provider, resolved="")
        await svc.generate_response("q", model_path="gw://")
        assert provider.chat_calls[0]["model"] == "gw:"

    @pytest.mark.parametrize("junk", ["llm-svc", "llm-svc://", "local", "default", "LOCAL"])
    async def test_placeholder_models_are_treated_as_empty(self, svc, monkeypatch, junk):
        provider = FakeProvider(models=["real"])
        install_registry(monkeypatch, provider, resolved=junk)
        await svc.generate_response("q", model_path="gw://x")
        assert provider.chat_calls[0]["model"] == "real"

    async def test_ensure_loaded_failure_does_not_block_the_request(self, svc, monkeypatch):
        provider = FakeProvider(load_error=RuntimeError("no pool"))
        install_registry(monkeypatch, provider, resolved="m")
        assert await svc.generate_response("q", model_path="gw://m") == "ответ провайдера"
        provider2 = FakeProvider(load_result=False)
        install_registry(monkeypatch, provider2, resolved="m")
        assert await svc.generate_response("q", model_path="gw://m") == "ответ провайдера"

    async def test_thinking_flag_reaches_the_provider(self, svc, monkeypatch):
        provider = FakeProvider()
        install_registry(monkeypatch, provider, resolved="m")
        await svc.generate_response("q", model_path="gw://m", enable_thinking=True)
        await svc.generate_response("q", model_path="gw://m", enable_thinking=False)
        on, off = provider.chat_calls[0]["request_extra"], provider.chat_calls[1]["request_extra"]
        assert on != off

    async def test_images_are_attached_to_the_last_user_message(self, svc, monkeypatch, tmp_path):
        provider = FakeProvider()
        install_registry(monkeypatch, provider, resolved="m")
        png = tmp_path / "a.png"
        png.write_bytes(b"\x89PNG")
        jpg = tmp_path / "b.JPG"
        jpg.write_bytes(b"jpg")
        await svc.generate_response(
            "опиши", images=[str(png), str(jpg), "data:image/png;base64,AAA", "", str(tmp_path / "missing.png")],
            model_path="gw://m",
        )
        content = provider.chat_calls[0]["messages"][-1]["content"]
        assert content[0] == {"type": "text", "text": "опиши"}
        urls = [p["image_url"]["url"] for p in content[1:]]
        assert urls[0].startswith("data:image/png;base64,") and urls[1].startswith("data:image/jpeg;base64,")
        assert urls[2] == "data:image/png;base64,AAA" and len(urls) == 3

    async def test_non_hot_swap_provider_updates_the_cached_model_name(self, svc, monkeypatch):
        provider = FakeProvider(kind="llm-svc", hot_swap=False)
        install_registry(monkeypatch, provider, resolved="fixed-model")

        async def health(host_id=None):
            return {"status": "healthy", "loaded_models": ["fixed-model"]}

        async def chat(**kw):
            return {"choices": [{"message": {"content": "готово"}}]}

        monkeypatch.setattr(svc.client, "health_check", health)
        monkeypatch.setattr(svc.client, "chat_completion", chat)
        assert await svc.generate_response("q", model_path="llm-svc://fixed-model") == "готово"
        assert svc.model_name == "fixed-model"


class TestGenerateViaLlmSvc:
    @pytest.fixture(autouse=True)
    def _no_provider(self, monkeypatch):
        install_registry(monkeypatch, None, error=RuntimeError("registry unavailable"))

    def wire(self, svc, monkeypatch, health, completion, load=True):
        calls = {"load": [], "chat": []}

        async def health_check(host_id=None):
            return health() if callable(health) else health

        async def load_model_if_needed(name, host_id=None):
            calls["load"].append(name)
            return load

        async def chat_completion(**kw):
            calls["chat"].append(kw)
            if isinstance(completion, Exception):
                raise completion
            return completion

        monkeypatch.setattr(svc.client, "health_check", health_check)
        monkeypatch.setattr(svc.client, "load_model_if_needed", load_model_if_needed)
        monkeypatch.setattr(svc.client, "chat_completion", chat_completion)
        return calls

    async def test_plain_answer_is_cleaned(self, svc, monkeypatch):
        calls = self.wire(
            svc, monkeypatch, {"status": "healthy", "loaded_models": ["qwen"]},
            {"choices": [{"message": {"content": "Привет &amp; пока"}}]},
        )
        assert await svc.generate_response("q", model_path="llm-svc://qwen") == "Привет & пока"
        assert calls["chat"][0]["model"] == "qwen" and calls["load"] == []

    async def test_reasoning_is_wrapped_only_when_thinking_was_requested(self, svc, monkeypatch):
        reply = {"choices": [{"message": {"content": "Итог", "reasoning_content": "думаю"}}]}
        self.wire(svc, monkeypatch, {"status": "healthy", "loaded_models": ["qwen"]}, reply)
        with_thinking = await svc.generate_response("q", model_path="llm-svc://qwen", enable_thinking=True)
        assert with_thinking == "<think>думаю</think>\n\nИтог"
        without = await svc.generate_response("q", model_path="llm-svc://qwen", enable_thinking=False)
        assert without == "Итог"

    async def test_think_tags_are_stripped_when_thinking_was_not_requested(self, svc, monkeypatch):
        reply = {"choices": [{"message": {"content": "<think>x</think>Итог"}}]}
        self.wire(svc, monkeypatch, {"status": "healthy", "loaded_models": ["qwen"]}, reply)
        assert await svc.generate_response("q", model_path="llm-svc://qwen", enable_thinking=False) == "Итог"

    async def test_existing_think_block_is_not_wrapped_twice(self, svc, monkeypatch):
        reply = {"choices": [{"message": {"content": "<think>a</think>Итог", "reasoning": "b"}}]}
        self.wire(svc, monkeypatch, {"status": "healthy", "loaded_models": ["qwen"]}, reply)
        out = await svc.generate_response("q", model_path="llm-svc://qwen", enable_thinking=True)
        assert out.count("<think>") == 1

    async def test_missing_model_is_loaded_before_chatting(self, svc, monkeypatch):
        calls = self.wire(
            svc, monkeypatch, {"status": "healthy", "loaded_models": []}, {"choices": [{"message": {"content": "ok"}}]}
        )
        assert await svc.generate_response("q", model_path="llm-svc://qwen") == "ok"
        assert calls["load"] == ["qwen"] and svc.model_name == "qwen"

    async def test_failed_load_is_reported_as_a_generic_apology_without_exception_text(self, svc, monkeypatch):
        """RuntimeError «модель не загружена» превращается в обычный текст ответа, но без
        текста исключения (CWE-209) — подробности остаются только в логе."""
        calls = self.wire(svc, monkeypatch, {"status": "healthy", "loaded_models": []}, {}, load=False)
        out = await svc.generate_response("q", model_path="llm-svc://qwen")
        assert out == lc.LLM_GENERIC_ERROR_TEXT and "не загружена" not in out
        assert calls["chat"] == []

    async def test_empty_choices_give_the_generic_failure_text(self, svc, monkeypatch):
        self.wire(svc, monkeypatch, {"status": "healthy", "loaded_models": ["qwen"]}, {"choices": []})
        assert await svc.generate_response("q", model_path="llm-svc://qwen") == "Ошибка генерации ответа"

    async def test_http_errors_are_re_raised(self, svc, monkeypatch):
        error = httpx.HTTPStatusError("boom", request=httpx.Request("POST", BASE), response=httpx.Response(503, text="down"))
        self.wire(svc, monkeypatch, {"status": "healthy", "loaded_models": ["qwen"]}, error)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.generate_response("q", model_path="llm-svc://qwen")

    async def test_other_errors_become_an_apology(self, svc, monkeypatch):
        self.wire(svc, monkeypatch, {"status": "healthy", "loaded_models": ["qwen"]}, ConnectionError("reset"))
        out = await svc.generate_response("q", model_path="llm-svc://qwen")
        assert out == lc.LLM_GENERIC_ERROR_TEXT and "reset" not in out

    async def test_streaming_goes_through_stream_generation(self, svc, monkeypatch):
        self.wire(svc, monkeypatch, {"status": "healthy", "loaded_models": ["qwen"]}, {})
        seen = {}

        async def stream(messages, temperature, max_tokens, cb, model, **kw):
            seen.update(model=model, host=kw["host_id"], messages=messages)
            return "стрим"

        monkeypatch.setattr(svc, "_stream_generation", stream)
        out = await svc.generate_response("q", streaming=True, stream_callback=lambda *a: True, model_path="llm-svc://qwen")
        assert out == "стрим" and seen["model"] == "qwen"


# ------------------------------------------------------------- _stream_generation


def sse_body(*events, done=True):
    lines = []
    for e in events:
        lines.append("data: " + (e if isinstance(e, str) else json.dumps(e)))
        lines.append("")
    if done:
        lines += ["data: [DONE]", ""]
    return "\n".join(lines)


def delta(**fields):
    return {"choices": [{"delta": fields}]}


class Collector:
    def __init__(self, stop_after=None):
        self.chunks, self.stop_after = [], stop_after

    def __call__(self, chunk, acc, role="content"):
        self.chunks.append((role, chunk, acc))
        return not (self.stop_after and len(self.chunks) >= self.stop_after)


class TestStreamGeneration:
    URL = f"{BASE}/v1/chat/completions"
    MSGS = [{"role": "user", "content": "q"}]

    def mock(self, *events, **kw):
        return ROUTER.post(self.URL).respond(200, content=sse_body(*events, **kw).encode(), headers={"content-type": "text/event-stream"})

    async def run(self, svc, cb=None, **kw):
        cb = cb or Collector()
        out = await svc._stream_generation(self.MSGS, 0.5, 100, cb, "m", host_id=None, **kw)
        return out, cb

    async def test_accumulates_chunks_and_reports_each_one(self, svc):
        route = self.mock(delta(content="При"), delta(content="вет"))
        out, cb = await self.run(svc)
        assert out == "Привет"
        assert cb.chunks == [("content", "При", "При"), ("content", "вет", "Привет")]
        body = json.loads(route.calls.last.request.content)
        assert body["stream"] is True and body["model"] == "m" and body["temperature"] == 0.5
        assert route.calls.last.request.headers["accept"] == "text/event-stream"

    async def test_alternative_content_field_names(self, svc):
        self.mock(delta(text="a"), delta(output_text="b"), delta(message="c"))
        out, _ = await self.run(svc)
        assert out == "abc"

    async def test_extra_request_fields_are_merged(self, svc):
        route = self.mock(delta(content="x"))
        await self.run(svc, request_extra={"top_p": 0.8, "drop": None})
        body = json.loads(route.calls.last.request.content)
        assert body["top_p"] == 0.8 and "drop" not in body

    async def test_ignores_garbage_lines_and_bad_json(self, svc):
        ROUTER.post(self.URL).respond(
            200,
            content=("\n: keepalive\ndata: {not json}\n\n" + sse_body(delta(content="ok"))).encode(),
        )
        out, _ = await self.run(svc)
        assert out == "ok"

    async def test_stops_at_finish_reason_without_waiting_for_done(self, svc):
        self.mock(delta(content="a"), {"choices": [{"delta": {"content": "b"}, "finish_reason": "stop"}]}, delta(content="never"), done=False)
        out, _ = await self.run(svc)
        assert out == "ab"

    async def test_length_finish_is_not_an_error(self, svc):
        self.mock({"choices": [{"delta": {"content": "обрыв"}, "finish_reason": "length"}]}, delta(content="x"))
        out, _ = await self.run(svc)
        assert out == "обрыв"

    async def test_callback_can_abort_the_stream(self, svc):
        self.mock(delta(content="a"), delta(content="b"), delta(content="c"))
        out, cb = await self.run(svc, Collector(stop_after=2))
        assert out == "ab" and len(cb.chunks) == 2

    async def test_two_argument_callbacks_are_supported(self, svc):
        self.mock(delta(content="x"))
        seen = []

        def old_style(chunk, acc):
            seen.append((chunk, acc))

        out, _ = await self.run(svc, old_style)
        assert out == "x" and seen == [("x", "x")]

    async def test_reasoning_is_streamed_separately_only_when_thinking_requested(self, svc):
        self.mock(delta(reasoning_content="дум"), delta(reasoning="аю"), delta(content="Итог"))
        thinking_extra = svc._thinking_request_extra(True)
        out, cb = await self.run(svc, request_extra=thinking_extra)
        roles = [r for r, _, _ in cb.chunks]
        assert roles == ["reasoning", "reasoning", "content"]
        assert cb.chunks[1][2] == "думаю" or cb.chunks[1][2].endswith("аю")
        assert out == "Итог"  # финальный текст не содержит блока <think> (в отличие от режима без стрима)

    async def test_reasoning_is_dropped_when_thinking_was_not_requested(self, svc):
        self.mock(delta(reasoning_content="скрыто"), delta(content="Итог"))
        out, cb = await self.run(svc, request_extra=svc._thinking_request_extra(False))
        assert out == "Итог" and [r for r, _, _ in cb.chunks] == ["content"]

    async def test_think_tags_are_hidden_from_the_callback_in_fast_mode(self, svc):
        self.mock(delta(content="<think>мысли"), delta(content="</think>Ответ"))
        out, cb = await self.run(svc, request_extra=svc._thinking_request_extra(False))
        assert out == "Ответ"
        assert cb.chunks[-1][2] == "Ответ"

    async def test_think_tags_are_kept_when_thinking_was_requested(self, svc):
        self.mock(delta(content="<think>м</think>Ответ"))
        out, _ = await self.run(svc, request_extra=svc._thinking_request_extra(True))
        assert out == "<think>м</think>Ответ"

    async def test_chatml_tag_ends_the_stream_and_is_removed_from_the_result(self, svc):
        """Поток обрывается на ``<|im_end|>``, а очистка вырезает метку из финального текста."""
        self.mock(delta(content="Ответ"), delta(content="<|im_end|>"), delta(content="лишнее"))
        out, _ = await self.run(svc)
        assert out == "Ответ"

    async def test_http_errors_are_raised(self, svc):
        ROUTER.post(self.URL).respond(503, text="down")
        with pytest.raises(httpx.HTTPStatusError):
            await svc._stream_generation(self.MSGS, 0.5, 10, Collector(), "m", cef_correlation_id="rid")

    async def test_transport_errors_become_a_text_result(self, svc):
        ROUTER.post(self.URL).mock(side_effect=httpx.ReadError("reset"))
        out, _ = await self.run(svc)
        assert out == lc.LLM_STREAM_ERROR_TEXT and "reset" not in out

    async def test_empty_stream_gives_an_empty_string(self, svc):
        self.mock()
        out, cb = await self.run(svc)
        assert out == "" and cb.chunks == []

    async def test_routes_to_the_requested_host(self, svc):
        svc.client.llm_hosts["gpu"] = "http://gpu.test"
        route = ROUTER.post("http://gpu.test/v1/chat/completions").respond(
            200, content=sse_body(delta(content="g")).encode()
        )
        out = await svc._stream_generation(self.MSGS, 0.1, 10, Collector(), "m", host_id="gpu")
        assert out == "g" and route.called


class TestAgentWrappers:
    async def test_get_llm_service_is_a_singleton(self, monkeypatch):
        monkeypatch.setattr(lc, "llm_service", None)
        created = []

        class Fake:
            def __init__(self, *a, **k):
                created.append(self)

            async def initialize(self):
                return True

        monkeypatch.setattr(lc, "LLMService", Fake)
        first = await lc.get_llm_service()
        second = await lc.get_llm_service()
        assert first is second and len(created) == 1


class FakeService:
    def __init__(self, result="ответ", error=None):
        self.result, self.error, self.calls = result, error, []

    async def generate_response(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.result

    async def synthesize_speech(self, text, **kw):
        return b"wav:" + text.encode()

    async def transcribe_audio_whisperx(self, audio, **kw):
        return {"text": "расшифровка"}

    async def diarize_audio(self, audio, **kw):
        return {"segments": []}

    async def transcribe_with_diarization(self, audio, **kw):
        return {"text": "t"}

    async def recognize_text_from_image(self, image, **kw):
        return {"text": "ocr"}


def use_service(monkeypatch, service):
    async def get_service():
        return service

    monkeypatch.setattr(lc, "get_llm_service", get_service)


def status_error(code):
    return httpx.HTTPStatusError("x", request=httpx.Request("POST", BASE), response=httpx.Response(code, text="e"))


class TestAskAgentSync:
    def test_without_a_running_loop(self, monkeypatch):
        service = FakeService()
        use_service(monkeypatch, service)
        out = lc.ask_agent_llm_svc("вопрос", history=[{"role": "user", "content": "h"}], max_tokens=None, temperature=None, model_path="llm-svc://m")
        assert out == "ответ"
        call = service.calls[0]
        assert call["max_tokens"] == 1024 and call["temperature"] == 0.7 and call["model_path"] == "llm-svc://m"
        assert call["prompt"] == "вопрос" and call["history"] == [{"role": "user", "content": "h"}]

    def test_explicit_zero_temperature_is_kept(self, monkeypatch):
        service = FakeService()
        use_service(monkeypatch, service)
        lc.ask_agent_llm_svc("q", temperature=0.0, max_tokens=5)
        assert service.calls[0]["temperature"] == 0.0 and service.calls[0]["max_tokens"] == 5

    def test_busy_service_gives_a_friendly_message(self, monkeypatch):
        use_service(monkeypatch, FakeService(error=status_error(503)))
        assert "занят или перезагружается" in lc.ask_agent_llm_svc("q")

    def test_other_http_errors_are_raised(self, monkeypatch):
        use_service(monkeypatch, FakeService(error=status_error(400)))
        with pytest.raises(httpx.HTTPStatusError):
            lc.ask_agent_llm_svc("q")

    def test_unexpected_errors_are_raised_without_a_loop(self, monkeypatch):
        use_service(monkeypatch, FakeService(error=ValueError("bad")))
        with pytest.raises(ValueError):
            lc.ask_agent_llm_svc("q")

    async def test_inside_a_running_loop_it_runs_in_a_worker_thread(self, monkeypatch):
        service = FakeService()
        use_service(monkeypatch, service)
        assert lc.ask_agent_llm_svc("q") == "ответ"

    async def test_inside_a_loop_errors_become_a_generic_message(self, monkeypatch):
        use_service(monkeypatch, FakeService(error=ValueError("bad")))
        assert lc.ask_agent_llm_svc("q") == "Ошибка при обращении к модели."

    async def test_inside_a_loop_busy_service_message(self, monkeypatch):
        use_service(monkeypatch, FakeService(error=status_error(503)))
        assert "занят" in lc.ask_agent_llm_svc("q")

    async def test_inside_a_loop_other_http_errors_are_raised_too(self, monkeypatch):
        use_service(monkeypatch, FakeService(error=status_error(500)))
        with pytest.raises(httpx.HTTPStatusError):
            lc.ask_agent_llm_svc("q")

    async def test_context_variables_reach_the_worker_thread(self, monkeypatch):
        import contextvars

        var = contextvars.ContextVar("probe", default="empty")
        seen = []

        class Service(FakeService):
            async def generate_response(self, **kw):
                seen.append(var.get())
                return "ok"

        use_service(monkeypatch, Service())
        var.set("from-caller")
        lc.ask_agent_llm_svc("q")
        assert seen == ["from-caller"]


class TestSyncMediaWrappers:
    def test_wrappers_delegate_to_the_service(self, monkeypatch):
        use_service(monkeypatch, FakeService())
        assert lc.synthesize_speech_llm_svc("привет") == "wav:привет".encode()
        assert lc.transcribe_audio_whisperx_llm_svc(b"a") == "расшифровка"
        assert lc.diarize_audio_llm_svc(b"a") == {"segments": []}
        assert lc.transcribe_with_diarization_llm_svc(b"a") == {"text": "t"}
        assert lc.recognize_text_from_image_llm_svc(b"a") == {"text": "ocr"}

    async def test_wrappers_work_from_inside_a_running_loop(self, monkeypatch):
        use_service(monkeypatch, FakeService())
        assert lc.synthesize_speech_llm_svc("x") == b"wav:x"

    async def test_audio_health_and_error_path(self, monkeypatch):
        service = lc.LLMService(base_url=BASE)

        async def stt():
            return {"status": "ok"}

        async def tts():
            raise RuntimeError("down")

        monkeypatch.setattr(service.client, "get_transcription_health", stt)
        monkeypatch.setattr(service.client, "get_tts_health", tts)
        out = await service.get_audio_services_health()
        assert out == {"overall": "unhealthy"}

    async def test_audio_health_overall_status(self, monkeypatch):
        service = lc.LLMService(base_url=BASE)

        async def ok():
            return {"status": "healthy"}

        async def bad():
            return {"status": "unhealthy"}

        monkeypatch.setattr(service.client, "get_transcription_health", ok)
        monkeypatch.setattr(service.client, "get_tts_health", ok)
        assert (await service.get_audio_services_health())["overall"] == "healthy"
        monkeypatch.setattr(service.client, "get_tts_health", bad)
        assert (await service.get_audio_services_health())["overall"] == "unhealthy"

    async def test_non_dict_transcription_result_becomes_empty_text(self, monkeypatch):
        class Service(FakeService):
            async def transcribe_audio_whisperx(self, audio, **kw):
                return "строка вместо словаря"

        use_service(monkeypatch, Service())
        assert lc.transcribe_audio_whisperx_llm_svc(b"a") == ""
