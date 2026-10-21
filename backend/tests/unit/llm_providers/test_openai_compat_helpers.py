"""llm_providers/openai_compat: чистые хелперы (таймауты, повторы, очистка ответа, think-теги)."""

from types import SimpleNamespace

import pytest

from backend.llm_providers import openai_compat as oc

pytestmark = pytest.mark.unit

ENV = (
    "LLM_AUTO_CONTINUE_MAX",
    "LLM_STREAM_READ_TIMEOUT",
    "LLM_STREAM_IDLE_TIMEOUT",
    "LLM_STREAM_ERROR_RETRIES",
    "LLM_RATE_LIMIT_RETRIES",
    "LLM_ERROR_BODY_LOG_CHARS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


class TestEnvLimits:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(None, 5), ("", 5), ("abc", 5), ("0", 0), ("3", 3), ("99", 20), ("-4", 0)],
    )
    def test_auto_continue_max(self, monkeypatch, raw, expected):
        if raw is not None:
            monkeypatch.setenv("LLM_AUTO_CONTINUE_MAX", raw)

        assert oc._auto_continue_max() == expected

    def test_stream_read_timeout_uses_provider_default_with_a_floor(self):
        assert oc._stream_read_timeout_sec(3600) == 3600.0
        assert oc._stream_read_timeout_sec(10) == 60.0
        assert oc._stream_read_timeout_sec("x") == 3600.0
        assert oc._stream_read_timeout_sec(None) == 3600.0

    def test_stream_read_timeout_env_override(self, monkeypatch):
        monkeypatch.setenv("LLM_STREAM_READ_TIMEOUT", "900")
        assert oc._stream_read_timeout_sec(3600) == 900.0
        monkeypatch.setenv("LLM_STREAM_READ_TIMEOUT", "5")
        assert oc._stream_read_timeout_sec(3600) == 60.0
        monkeypatch.setenv("LLM_STREAM_READ_TIMEOUT", "junk")
        assert oc._stream_read_timeout_sec(120) == 120.0

    @pytest.mark.parametrize(("raw", "expected"), [(None, 300.0), ("600", 600.0), ("5", 30.0), ("x", 300.0)])
    def test_stream_idle_timeout(self, monkeypatch, raw, expected):
        if raw is not None:
            monkeypatch.setenv("LLM_STREAM_IDLE_TIMEOUT", raw)

        assert oc._stream_idle_timeout_sec() == expected

    @pytest.mark.parametrize(("raw", "expected"), [(None, 2), ("0", 0), ("4", 4), ("50", 5), ("x", 2)])
    def test_stream_error_retries(self, monkeypatch, raw, expected):
        if raw is not None:
            monkeypatch.setenv("LLM_STREAM_ERROR_RETRIES", raw)

        assert oc._stream_error_retries() == expected

    @pytest.mark.parametrize(("raw", "expected"), [(None, 3), ("0", 0), ("7", 7), ("99", 10), ("x", 3)])
    def test_rate_limit_retries(self, monkeypatch, raw, expected):
        if raw is not None:
            monkeypatch.setenv("LLM_RATE_LIMIT_RETRIES", raw)

        assert oc._rate_limit_retries() == expected

    @pytest.mark.parametrize(("raw", "expected"), [(None, 2000), ("50", 50), ("99999", 20000), ("-1", 0), ("x", 2000)])
    def test_error_body_limit(self, monkeypatch, raw, expected):
        if raw is not None:
            monkeypatch.setenv("LLM_ERROR_BODY_LOG_CHARS", raw)

        assert oc._llm_error_body_limit() == expected


class TestRateLimitDelay:
    @staticmethod
    def _response(headers):
        return SimpleNamespace(headers=headers)

    @pytest.mark.parametrize(("retry_after", "expected"), [("5", 5.0), ("0", 1.0), ("120", 60.0), ("2.5", 2.5)])
    def test_retry_after_is_respected_within_bounds(self, retry_after, expected):
        assert oc._rate_limit_delay(self._response({"retry-after": retry_after}), 0) == expected

    @pytest.mark.parametrize(("attempt", "expected"), [(0, 3.0), (1, 8.0), (2, 20.0), (9, 20.0)])
    def test_backoff_without_a_header(self, attempt, expected):
        assert oc._rate_limit_delay(self._response({}), attempt) == expected

    def test_garbage_header_falls_back_to_backoff(self):
        assert oc._rate_limit_delay(self._response({"retry-after": "Wed, 21 Oct"}), 1) == 8.0
        assert oc._rate_limit_delay(object(), 0) == 3.0


class TestContextFit:
    VLLM_BODY = (
        "This model's maximum context length of 262144 tokens. However, you requested "
        "200000 output tokens and your prompt contains at least 71140 tokens from the input"
    )
    OPENAI_BODY = "maximum context length is 8192 tokens. However, your messages resulted in (7000 in the messages, 2000 in the completion)"

    def test_vllm_message_gives_the_remaining_room(self):
        fit = oc._context_fit_max_tokens("p", "m", 400, self.VLLM_BODY, 200_000)

        assert fit == 262_144 - 71_140 - 512

    def test_openai_message(self):
        fit = oc._context_fit_max_tokens("p", "m", 400, self.OPENAI_BODY, 4000)

        assert fit == 8192 - 7000 - 512

    @pytest.mark.parametrize("status", [200, 413, 422, 500])
    def test_only_400_is_retried(self, status):
        assert oc._context_fit_max_tokens("p", "m", status, self.VLLM_BODY, 200_000) is None

    @pytest.mark.parametrize("body", ["", "something else", "context length exceeded"])
    def test_unrecognized_body(self, body):
        assert oc._context_fit_max_tokens("p", "m", 400, body, 1000) is None

    def test_no_retry_when_requested_tokens_already_fit(self):
        assert oc._context_fit_max_tokens("p", "m", 400, self.VLLM_BODY, 100) is None

    def test_no_retry_when_there_is_no_room_left(self):
        body = "maximum context length of 1000 tokens. ... 900 tokens from the input"

        assert oc._context_fit_max_tokens("p", "m", 400, body, 5000) is None

    def test_bad_max_tokens_value(self):
        assert oc._context_fit_max_tokens("p", "m", 400, self.VLLM_BODY, "many") is None
        assert oc._context_fit_max_tokens("p", "m", 400, self.VLLM_BODY, None) is None


class TestStreamCallback:
    def test_three_argument_callback(self):
        calls = []

        def cb(chunk, acc, role):
            calls.append((chunk, acc, role))

        assert oc._invoke_stream_callback(cb, "a", "ab", "reasoning") is True
        assert calls == [("a", "ab", "reasoning")]

    def test_two_argument_callback_is_supported(self):
        calls = []

        def cb(chunk, acc):
            calls.append((chunk, acc))

        assert oc._invoke_stream_callback(cb, "a", "ab") is True
        assert calls == [("a", "ab")]

    def test_false_stops_the_stream_but_none_does_not(self):
        assert oc._invoke_stream_callback(lambda c, a, r: False, "a", "a") is False
        assert oc._invoke_stream_callback(lambda c, a, r: None, "a", "a") is True
        assert oc._invoke_stream_callback(lambda c, a, r: 0, "a", "a") is True

    def test_heartbeat_is_ignored_by_old_two_argument_callbacks(self):
        calls = []

        def cb(chunk, acc):
            calls.append(chunk)
            return False

        assert oc._invoke_stream_callback(cb, "", "", "heartbeat") is True
        assert calls == []


class TestCleanResponse:
    def test_empty(self):
        assert oc.clean_llm_response("") == ""
        assert oc.clean_llm_response(None) is None

    def test_real_chatml_markers_are_removed(self):
        text = "Ответ<|im_end|>\n<|im_start|>user\nещё"

        assert oc.clean_llm_response(text) == "Ответ"

    def test_misspelt_markers_without_underscore_are_kept(self):
        text = "Ответ<|imend|>\n<|imstart|>user\nещё"

        assert oc.clean_llm_response(text) == text

    def test_escaped_markers_are_unescaped_first(self):
        assert oc.clean_llm_response("Ответ&lt;|im_end|&gt;хвост") == "Ответ"
        assert oc.clean_llm_response("Ответ&amp;lt;|im_end|&amp;gt;хвост") == "Ответ"
        assert oc.clean_llm_response("Ответ&amp;amp;lt;|im_start|&amp;amp;gt;хвост") == "Ответ"

    def test_unescapes_entities_and_trims_the_right_side(self):
        assert oc.clean_llm_response("a &amp; b  \n") == "a & b"

    def test_plain_text_is_kept(self):
        assert oc.clean_llm_response("  привет") == "  привет"


class TestThinkTags:
    def test_text_without_markers_is_untouched(self):
        assert oc._strip_think_tags("ответ") == "ответ"
        assert oc._strip_think_tags("") == ""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("<think>мысли</think>Ответ", "Ответ"),
            ("<THINK>мысли</THINK> Ответ ", "Ответ"),
            ("Начало<think>незакрытое", "Начало"),
            ("мысли без открытия</think>Ответ", "Ответ"),
            ("<think>a</think>X<think>b</think>Y", "XY"),
            ("<think>\nмного\nстрок\n</think>\nОтвет", "Ответ"),
        ],
    )
    def test_strip(self, raw, expected):
        assert oc._strip_think_tags(raw) == expected

    @pytest.mark.parametrize(("text", "expected"), [("", False), ("просто", False), ("<think>x", True), ("x</THINK>", True)])
    def test_markers(self, text, expected):
        assert oc._has_think_markers(text) is expected


class TestPayloadNormalization:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, ""),
            ("текст", "текст"),
            (["a", {"text": "b"}, {"content": "c"}, {"reasoning": "d"}, {"x": 1}, 5], "abcd"),
            ({"text": "t"}, "t"),
            ({"reasoning_text": "r"}, "r"),
            ({"thinking": "th"}, "th"),
            ({"content": [{"text": "n1"}, "n2"]}, "n1n2"),
            ({"my_reasoning": "x", "other": "y"}, "x"),
            (42, "42"),
        ],
    )
    def test_reasoning(self, value, expected):
        assert oc._normalize_reasoning_payload(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, ""),
            ("текст", "текст"),
            ([{"type": "text", "text": "a"}, {"type": "output_text", "text": "b"}, "c"], "abc"),
            ([{"type": "image_url", "content": "ignored-not"}], "ignored-not"),
            ({"text": "t"}, "t"),
            ({"content": "c"}, "c"),
            ({"nothing": 1}, ""),
            (7, "7"),
        ],
    )
    def test_content(self, value, expected):
        assert oc._normalize_content_payload(value) == expected


class TestContextSizeParsing:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (None, None),
            ({}, None),
            ({"context_length": 8192}, 8192),
            ({"max_model_len": 16384.0}, 16384),
            ({"context_window": "32,768"}, 32768),
            ({"n_ctx": "32 768"}, 32768),
            ({"context_length": "128k"}, 128_000),
            ({"context_length": "1.5M"}, 1_500_000),
            ({"context_length": True}, None),
            ({"context_length": 0}, None),
            ({"context_length": -5}, None),
            ({"context_length": "many"}, None),
        ],
    )
    def test_parse(self, data, expected):
        assert oc._parse_context_size_from_mapping(data) == expected


class TestGuardrailAndLogging:
    @pytest.mark.parametrize(
        "body",
        ["Request blocked by guardrail", "CONTENT_FILTER triggered", "flagged by moderation", "Запрос заблокирован политикой", "нарушает модерацию"],
    )
    def test_guardrail_markers(self, body):
        assert oc._looks_like_guardrail(body) is True

    @pytest.mark.parametrize("body", [None, "", "model not found", "internal error"])
    def test_ordinary_errors_are_not_guardrails(self, body):
        assert oc._looks_like_guardrail(body) is False

    def test_cut_for_log_collapses_whitespace_and_marks_truncation(self):
        assert oc._cut_for_log("a\n\n  b", 100) == "a b"
        assert oc._cut_for_log("x" * 30, 10) == "xxxxxxxxxx…(+20 симв.)"
        assert oc._cut_for_log(None, 10) == ""

    def test_safe_response_text(self):
        assert oc._safe_response_text(SimpleNamespace(text="тело")) == "тело"
        assert oc._safe_response_text(SimpleNamespace(text=None)) == ""

        class Streamed:
            @property
            def text(self):
                raise RuntimeError("ResponseNotRead")

        assert "поток не прочитан" in oc._safe_response_text(Streamed())

    def test_failure_log_does_not_raise_and_flags_guardrails(self, monkeypatch):
        records = []
        fake_logger = SimpleNamespace(warning=lambda msg, *args, **kw: records.append(msg % args if args else msg))
        monkeypatch.setattr(oc, "logger", fake_logger)
        oc._log_llm_http_failure(
            provider_id="gw",
            status_code=400,
            body="blocked by guardrail",
            model="m",
            messages=[{"role": "user", "content": "привет"}, None],
            max_tokens=10,
            has_tools=False,
            payload_keys=["model"],
        )

        text = "\n".join(records)
        assert "отказ шлюза HTTP 400" in text
        assert "guardrail/модерация" in text


class TestAllowlist:
    @staticmethod
    def _model(model_id, display=""):
        return SimpleNamespace(model_id=model_id, display_name=display)

    def test_matches_full_id_short_name_and_display_name(self):
        model = self._model("embed/FRIDA", "FRIDA Large")

        assert oc._model_in_allowlist(model, ["embed/FRIDA"]) is True
        assert oc._model_in_allowlist(model, ["frida"]) is True
        assert oc._model_in_allowlist(model, ["FRIDA Large"]) is True

    def test_other_models_do_not_match(self):
        assert oc._model_in_allowlist(self._model("vllm/qwen"), ["frida"]) is False
        assert oc._model_in_allowlist(self._model("vllm/qwen"), []) is False

    def test_short_name_matches_every_group_with_that_model(self):
        a = self._model("vllm/qwen")
        b = self._model("vllm_other/qwen")

        assert oc._model_in_allowlist(a, ["qwen"]) and oc._model_in_allowlist(b, ["qwen"])

    def test_blank_entries_never_match(self):
        assert oc._model_in_allowlist(self._model("m"), ["", None, "  "]) is False


class TestNextLine:
    async def test_awaits_the_iterator(self):
        async def gen():
            yield "первая"
            yield "вторая"

        it = gen()

        assert await oc._next_line(it) == "первая"
        assert await oc._next_line(it) == "вторая"
        with pytest.raises(StopAsyncIteration):
            await oc._next_line(it)
