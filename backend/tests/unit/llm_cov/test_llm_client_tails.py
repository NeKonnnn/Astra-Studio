"""llm_client: конфигурация клиента из настроек, реестровый health, диаризация, обёртки, хвосты generate/stream."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import respx

from backend import llm_client as lc
from backend.llm_providers.base import ProviderHealth

pytestmark = pytest.mark.unit

BASE = "http://llm.test"
ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    for name in ("TLS_CERT_PATH", "LLM_VERIFY_SSL", "LLM_API_KEY", "OCR_VISION_MODEL_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(lc, "require_service", lambda key: None)
    with ROUTER:
        yield
    ROUTER.reset()


@pytest.fixture
def client():
    c = lc.LLMClient(base_url=BASE + "/")
    c.stt_url, c.tts_url, c.diarization_url = "http://stt.test", "http://tts.test", "http://diar.test"
    return c


class FakeSettings:
    """Минимальные настройки для LLMClient: все ветки конструктора управляются полями."""

    def __init__(self, llm_service=None, llm_providers=None, optional_urls=True):
        self.llm_service = llm_service if llm_service is not None else SimpleNamespace(timeout=33)
        self.llm_providers = llm_providers
        self.optional_urls = optional_urls

    def get_llm_service_url(self):
        return "http://default-llm:8001/"

    def microservice_http_base(self, docker_key, port_key):
        if not self.optional_urls:
            msg = "url не задан"
            raise ValueError(msg)
        return f"http://{port_key}"


class TestRouting:
    def test_bare_llm_svc_token_uses_fallback_model_on_default_host(self):
        hosts = {"a": "http://a", "b": "http://b"}

        assert lc.resolve_llm_host_and_model_for_svc("llm-svc", "fb-model", hosts, "b") == ("b", "fb-model")


class TestClientConfiguration:
    def test_base_url_comes_from_settings_when_not_given(self, monkeypatch):
        monkeypatch.setattr(lc, "get_settings", lambda: FakeSettings())

        c = lc.LLMClient()

        assert c.llm_url == "http://default-llm:8001"
        assert c.llm_hosts == {"default": "http://default-llm:8001"}
        assert c.default_llm_host == "default"
        assert c.timeout == 33
        assert c.stt_url == "http://stt_service_port"

    def test_missing_optional_service_urls_are_none(self, monkeypatch):
        monkeypatch.setattr(lc, "get_settings", lambda: FakeSettings(optional_urls=False))

        c = lc.LLMClient(base_url=BASE)

        assert (c.stt_url, c.tts_url, c.ocr_url, c.diarization_url) == (None, None, None, None)

    def test_hosts_from_config_in_all_supported_shapes(self, monkeypatch):
        llm = SimpleNamespace(
            timeout=10,
            api_key="cfg-key",
            hosts=[
                SimpleNamespace(id="h1", base_url="http://h1/"),
                {"id": "h2", "base_url": "http://h2"},
                {"id": "h3"},
                None,
                "junk",
            ],
            default_host_id="h2",
        )
        monkeypatch.setattr(lc, "get_settings", lambda: FakeSettings(llm))

        c = lc.LLMClient(base_url=BASE)

        assert c.llm_hosts == {"h1": "http://h1", "h2": "http://h2"}
        assert c.default_llm_host == "h2" and c.llm_url == "http://h2"
        assert c.api_key == "cfg-key"

    def test_unknown_default_host_falls_back_to_the_first_one(self, monkeypatch):
        llm = SimpleNamespace(timeout=10, hosts=[{"id": "h1", "base_url": "http://h1"}], default_host_id="zzz")
        monkeypatch.setattr(lc, "get_settings", lambda: FakeSettings(llm))

        assert lc.LLMClient(base_url=BASE).default_llm_host == "h1"

    def test_explicit_api_key_beats_config_and_env(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "env-key")
        monkeypatch.setattr(lc, "get_settings", lambda: FakeSettings(SimpleNamespace(timeout=1, api_key=None)))

        assert lc.LLMClient(base_url=BASE).api_key == "env-key"
        assert lc.LLMClient(base_url=BASE, api_key="explicit").api_key == "explicit"

    def test_broken_hosts_and_timeout_do_not_break_the_client(self, monkeypatch):
        class Broken:
            @property
            def hosts(self):
                msg = "bad hosts"
                raise RuntimeError(msg)

            @property
            def timeout(self):
                msg = "bad timeout"
                raise RuntimeError(msg)

        monkeypatch.setattr(lc, "get_settings", lambda: FakeSettings(Broken()))

        c = lc.LLMClient(base_url=BASE)

        assert c.timeout == 120.0
        assert c.llm_hosts == {"default": BASE}

    def test_enabled_openai_like_providers_become_hosts(self, monkeypatch):
        providers = [
            {"id": "gw", "base_url": "http://gw/", "kind": "OpenAI-Compat"},
            {"id": "off", "base_url": "http://off", "kind": "vllm", "enabled": False},
            {"id": "cloud", "base_url": "http://cloud", "kind": "anthropic"},
            {"id": "", "base_url": "http://x", "kind": "vllm"},
            {"id": "nourl", "base_url": "", "kind": "vllm"},
            "junk",
        ]
        llm = SimpleNamespace(timeout=5, hosts=[{"id": "gw", "base_url": "http://configured"}], default_host_id=None)
        monkeypatch.setattr(lc, "get_settings", lambda: FakeSettings(llm, providers))

        c = lc.LLMClient(base_url=BASE)

        # уже заданный host не перезаписывается (setdefault)
        assert c.llm_hosts == {"gw": "http://configured"}
        monkeypatch.setattr(lc, "get_settings", lambda: FakeSettings(SimpleNamespace(timeout=5), providers))
        assert lc.LLMClient(base_url=BASE).llm_hosts == {"default": BASE, "gw": "http://gw"}

    def test_broken_providers_section_is_ignored(self, monkeypatch):
        monkeypatch.setattr(lc, "get_settings", lambda: FakeSettings(llm_providers=42))

        assert lc.LLMClient(base_url=BASE).llm_hosts == {"default": BASE}

    def test_llm_service_without_settings_block_uses_defaults(self, monkeypatch):
        monkeypatch.setattr(lc, "LLMClient", lambda base_url=None, api_key=None: SimpleNamespace())
        monkeypatch.setattr(lc, "get_settings", lambda: SimpleNamespace())

        service = lc.LLMService()

        assert service.model_name == "qwen-coder-30b"
        assert service.fallback_model is None and service.auto_select is False


class TestRegistryHealth:
    @staticmethod
    def install(monkeypatch, provider, known=("gw",)):
        class Registry:
            def contains(self, pid):
                return pid in known

            def get(self, pid):
                self.asked = pid
                return provider

        registry = Registry()

        async def get_registry():
            return registry

        monkeypatch.setattr("backend.llm_providers.get_registry", get_registry)
        return registry

    async def test_health_is_taken_from_the_provider(self, client, monkeypatch):
        class Provider:
            id, kind = "gw", "vllm"

            async def health(self):
                return ProviderHealth(healthy=False, loaded_models=["m"], error="slow", raw={"a": 1})

        registry = self.install(monkeypatch, Provider())

        assert await client.health_check(host_id="gw") == {
            "status": "unhealthy",
            "provider_id": "gw",
            "provider_kind": "vllm",
            "loaded_models": ["m"],
            "error": "slow",
            "raw": {"a": 1},
        }
        assert registry.asked == "gw"

    async def test_unknown_host_asks_the_registry_for_the_default_provider(self, client, monkeypatch):
        class Provider:
            id, kind = "def", "llm-svc"

            async def health(self):
                return ProviderHealth(healthy=True)

        registry = self.install(monkeypatch, Provider())

        out = await client.health_check(host_id="zzz")

        assert out["status"] == "healthy" and registry.asked is None


class TestSimpleServiceHealth:
    async def test_stt_non_200_and_network_error(self, client):
        ROUTER.get("http://stt.test/v1/whisperx/health").respond(503)

        assert await client.get_transcription_health() == {"status": "unhealthy", "code": 503}

        ROUTER.get("http://stt.test/v1/whisperx/health").mock(side_effect=httpx.ConnectError("refused"))
        out = await client.get_transcription_health()
        assert out["status"] == "unhealthy" and "refused" in out["error"]

    async def test_tts_healthy_payload_is_returned_as_is(self, client):
        ROUTER.get("http://tts.test/v1/health").respond(200, json={"status": "healthy", "voices": 3})

        assert await client.get_tts_health() == {"status": "healthy", "voices": 3}

    async def test_tts_non_200_and_network_error(self, client):
        ROUTER.get("http://tts.test/v1/health").respond(500)

        assert await client.get_tts_health() == {"status": "unhealthy", "code": 500}

        ROUTER.get("http://tts.test/v1/health").mock(side_effect=httpx.ConnectError("refused"))
        out = await client.get_tts_health()
        assert out["status"] == "unhealthy" and "refused" in out["error"]

    async def test_reload_whisperx_failure_is_raised(self, client):
        ROUTER.post("http://stt.test/v1/whisperx/reload").respond(500, text="boom")

        with pytest.raises(httpx.HTTPStatusError):
            await client.reload_whisperx_models()

    async def test_diarization_failure_is_raised(self, client):
        ROUTER.post("http://diar.test/v1/diarize").respond(500, text="boom")

        with pytest.raises(httpx.HTTPStatusError):
            await client.diarize_audio(b"x")


class TestDiarizationMatchingEdges:
    @staticmethod
    def patch(client, monkeypatch, diar, stt):
        async def diarize(*a, **k):
            return diar

        async def transcribe(*a, **k):
            return stt

        monkeypatch.setattr(client, "diarize_audio", diarize)
        monkeypatch.setattr(client, "transcribe_audio_whisperx", transcribe)

    async def test_blank_words_are_skipped_in_word_level_matching(self, client, monkeypatch):
        diar = {"segments": [{"start": 0, "end": 5, "speaker": "A"}]}
        stt = {
            "text": "да нет",
            "segments": [
                {
                    "start": 0,
                    "end": 5,
                    "words": [
                        {"word": "да", "start": 0, "end": 1},
                        {"word": "   ", "start": 1, "end": 2},
                        {"word": "нет", "start": 2, "end": 3},
                    ],
                }
            ],
        }
        self.patch(client, monkeypatch, diar, stt)

        out = await client.transcribe_with_diarization(b"x")

        assert out["segments"] == [{"start": 0.0, "end": 3.0, "speaker": "A", "text": "да нет"}]

    async def test_segments_without_text_and_neighbours_of_one_speaker(self, client, monkeypatch):
        diar = {"segments": [{"start": 0, "end": 10, "speaker": "A"}]}
        stt = {
            "text": "раз два",
            "segments": [
                {"start": 1, "end": 2, "text": "раз"},
                {"start": 3, "end": 4, "text": "  "},
                {"start": 5, "end": 6, "text": "два"},
            ],
        }
        self.patch(client, monkeypatch, diar, stt)

        out = await client.transcribe_with_diarization(b"x")

        # пустой сегмент пропущен, два соседних сегмента одного спикера склеены
        assert out["segments"] == [{"start": 1.0, "end": 6.0, "speaker": "A", "text": "раз два"}]

    async def test_duration_fallback_appends_leftover_words_to_the_last_segment(self, client, monkeypatch):
        diar = {"segments": [{"start": 0, "end": 1, "speaker": "A"}, {"start": 1, "end": 2, "speaker": "B"}]}
        stt = {"text": "a b c d e", "segments": []}
        self.patch(client, monkeypatch, diar, stt)

        out = await client.transcribe_with_diarization(b"x")

        assert [s["speaker"] for s in out["segments"]] == ["A", "B"]
        assert out["segments"][0]["text"] == "a b"
        assert out["segments"][1]["text"] == "c d e"


class TestServiceWrappers:
    async def test_media_calls_are_delegated_to_the_client(self, monkeypatch):
        service = lc.LLMService(base_url=BASE)
        calls = []

        def recorder(name):
            async def call(*args, **kwargs):
                calls.append((name, args, kwargs))
                return name

            return call

        for name in (
            "transcribe_audio_whisperx",
            "diarize_audio",
            "transcribe_with_diarization",
            "recognize_text_from_image",
        ):
            monkeypatch.setattr(service.client, name, recorder(name))

        assert await service.transcribe_audio_whisperx(b"a", language="ru") == "transcribe_audio_whisperx"
        assert await service.diarize_audio(b"a") == "diarize_audio"
        assert await service.transcribe_with_diarization(b"a", engine="x") == "transcribe_with_diarization"
        assert await service.recognize_text_from_image(b"i") == "recognize_text_from_image"
        assert calls[0] == ("transcribe_audio_whisperx", (b"a",), {"language": "ru"})
        assert calls[2] == ("transcribe_with_diarization", (b"a",), {"engine": "x"})


class TestWrapSync:
    def test_runs_the_coroutine_on_a_not_running_loop(self):
        async def work():
            return 42

        previous = None
        try:
            previous = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            pass
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            assert lc._wrap_sync(work()) == 42
        finally:
            loop.close()
            asyncio.set_event_loop(previous)

    async def test_runs_the_coroutine_in_a_worker_thread_inside_a_running_loop(self):
        async def work():
            return 7

        assert lc._wrap_sync(work()) == 7


# --------------------------------------------------------------------- generate_response


class FakeProvider:
    def __init__(self, models=(), list_error=None):
        self.id, self.kind = "gw", "openai-compat"
        self.capabilities = SimpleNamespace(hot_swap=True)
        self.models, self.list_error = list(models), list_error

    async def list_models(self):
        if self.list_error:
            raise self.list_error
        return [SimpleNamespace(model_id=m) for m in self.models]

    async def ensure_model_loaded(self, model):
        return True

    async def chat(self, **kw):
        return "ответ провайдера"

    async def stream_chat(self, **kw):
        return "поток провайдера"


def install_registry(monkeypatch, provider, resolved=None):
    class Registry:
        def resolve(self, path):
            return provider, resolved

    async def get_registry():
        return Registry()

    monkeypatch.setattr("backend.llm_providers.get_registry", get_registry)


@pytest.fixture
def svc(monkeypatch):
    service = lc.LLMService(base_url=BASE)

    async def sync(host_id=None):
        return (False, {})

    monkeypatch.setattr(service, "_sync_loaded_model_name_from_health", sync)
    return service


def wire_llm_svc(svc, monkeypatch, healths, completion=None):
    """Подменяет llm-svc клиент: healths — список ответов health_check по очереди."""
    queue = list(healths)
    calls = {"chat": [], "load": []}

    async def health_check(host_id=None):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    async def load_model_if_needed(name, host_id=None):
        calls["load"].append(name)
        return True

    async def chat_completion(**kw):
        calls["chat"].append(kw)
        return completion or {"choices": [{"message": {"content": "готово"}}]}

    monkeypatch.setattr(svc.client, "health_check", health_check)
    monkeypatch.setattr(svc.client, "load_model_if_needed", load_model_if_needed)
    monkeypatch.setattr(svc.client, "chat_completion", chat_completion)
    return calls


class TestGenerateTails:
    async def test_unreadable_image_is_skipped_but_data_urls_are_kept(self, svc, monkeypatch, tmp_path):
        provider = FakeProvider()
        install_registry(monkeypatch, provider, resolved="gpt-x")
        sent = []

        async def chat(**kw):
            sent.append(kw["messages"])
            return "ok"

        provider.chat = chat

        # каталог существует, но прочитать его как файл нельзя
        out = await svc.generate_response("q", images=[str(tmp_path), "data:image/png;base64,AAA", "", "/no/such/file.png"], model_path="gw://gpt-x")

        assert out == "ok"
        content = sent[0][-1]["content"]
        assert [part["type"] for part in content] == ["text", "image_url"]
        assert content[1]["image_url"]["url"] == "data:image/png;base64,AAA"

    async def test_failed_auto_selection_falls_back_to_the_llm_svc_route(self, svc, monkeypatch):
        install_registry(monkeypatch, FakeProvider(list_error=RuntimeError("models down")), resolved="default")
        calls = wire_llm_svc(svc, monkeypatch, [{"status": "healthy", "loaded_models": []}])

        out = await svc.generate_response("q", model_path="gw://default")

        assert out == "готово"
        assert len(calls["chat"]) == 1 and calls["chat"][0]["model"] == ""

    async def test_provider_without_models_falls_back_to_the_llm_svc_route(self, svc, monkeypatch):
        install_registry(monkeypatch, FakeProvider(models=[]), resolved="local")
        calls = wire_llm_svc(svc, monkeypatch, [{"status": "healthy", "loaded_models": []}])

        assert await svc.generate_response("q") == "готово"
        assert len(calls["chat"]) == 1

    async def test_model_appearing_in_pool_while_waiting_for_the_lock_skips_loading(self, svc, monkeypatch):
        class NoRegistry:
            def resolve(self, path):
                msg = "no registry"
                raise RuntimeError(msg)

        async def get_registry():
            return NoRegistry()

        monkeypatch.setattr("backend.llm_providers.get_registry", get_registry)
        calls = wire_llm_svc(
            svc,
            monkeypatch,
            [
                {"status": "healthy", "loaded_models": []},
                {"status": "healthy", "loaded_models": ["wanted-model"]},
            ],
        )

        out = await svc.generate_response("q", model_path="llm-svc://wanted-model")

        assert out == "готово"
        assert calls["load"] == []
        assert svc.model_name == "wanted-model"


class TestStreamTails:
    URL = f"{BASE}/v1/chat/completions"
    MSGS = [{"role": "user", "content": "q"}]

    @staticmethod
    def body(*events):
        lines = []
        for e in events:
            lines.append("data: " + json.dumps(e))
            lines.append("")
        lines += ["data: [DONE]", ""]
        return chr(10).join(lines).encode()

    async def test_timeout_helper_failure_uses_client_timeout(self, svc, monkeypatch):
        import backend.llm_providers.openai_compat as oc

        def boom(_):
            msg = "helper gone"
            raise RuntimeError(msg)

        monkeypatch.setattr(oc, "_stream_read_timeout_sec", boom)
        ROUTER.post(self.URL).respond(
            200,
            content=self.body({"choices": [{"delta": {"content": "ok"}}]}),
            headers={"content-type": "text/event-stream"},
        )

        out = await svc._stream_generation(self.MSGS, 0.5, 10, lambda c, a, r="content": True, "m", host_id=None)

        assert out == "ok"

    async def test_reasoning_callback_can_abort_the_stream(self, svc):
        ROUTER.post(self.URL).respond(
            200,
            content=self.body(
                {"choices": [{"delta": {"content": "уже было"}}]},
                {"choices": [{"delta": {"reasoning_content": "думаю"}}]},
                {"choices": [{"delta": {"content": "не дойдёт"}}]},
            ),
            headers={"content-type": "text/event-stream"},
        )

        out = await svc._stream_generation(
            self.MSGS,
            0.5,
            10,
            lambda c, a, r="content": r != "reasoning",
            "m",
            host_id=None,
            request_extra={"enable_thinking": True},
        )

        assert out == "уже было"

    async def test_transport_error_with_correlation_id_is_audited(self, svc, monkeypatch):
        audited = []
        monkeypatch.setattr(lc, "log_cef_int006_llm_api_failure", lambda **kw: audited.append(kw))
        ROUTER.post(self.URL).mock(side_effect=httpx.ConnectError("refused"))

        out = await svc._stream_generation(
            self.MSGS,
            0.5,
            10,
            lambda c, a, r="content": True,
            "m",
            host_id=None,
            cef_correlation_id="rid-1",
            cef_service_name="llm-svc-main",
        )

        assert out == lc.LLM_STREAM_ERROR_TEXT
        assert "refused" not in out
        assert "refused" in str(audited[0].get("text_status"))
        assert audited and audited[0]["request_uuid"] == "rid-1"
        assert audited[0]["service_name"] == "llm-svc-main" and audited[0]["code_status"] == "STREAM"
