"""llm_providers/routing: флаги окружения, параметры мышления/выборки, форматирование ошибок."""

import httpx
import pytest

from backend.llm_providers import routing

pytestmark = pytest.mark.unit

ENV_NAMES = (
    "LLM_SERVICE_DISABLE_THINKING",
    "LLM_SERVICE_MIN_MAX_TOKENS",
    "LLM_PASSTHROUGH_EXTRA_PARAMS",
    "LLM_SAMPLING_ENABLED",
    "LLM_SAMPLING_SEND_ALL",
    "LLM_SERVICE_NO_THINK_TAG",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


class TestDirectRouting:
    def test_images_force_the_legacy_path(self):
        assert routing.should_use_llm_svc_direct(images=[b"x"]) is True

    @pytest.mark.parametrize("path", ["llm-svc://host/model", "LLM-SVC://m", "  llm-svc://m"])
    def test_legacy_uris_use_the_legacy_path(self, path):
        assert routing.should_use_llm_svc_direct(model_path=path) is True

    @pytest.mark.parametrize("path", [None, "", "openai/gpt-4o", "vllm/qwen"])
    def test_registry_paths_do_not(self, path):
        assert routing.should_use_llm_svc_direct(model_path=path) is False

    def test_empty_image_list_is_not_vision(self):
        assert routing.should_use_llm_svc_direct(images=[]) is False

    @pytest.mark.parametrize(("response", "usable"), [(None, False), ("", False), ("  \n", False), ("ответ", True)])
    def test_registry_response_usable(self, response, usable):
        assert routing.registry_response_usable(response) is usable


class TestEnvFlags:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("0", False), ("false", False), ("No", False), ("OFF", False), ("1", True), ("true", True), ("anything", True)],
    )
    def test_env_flag_parsing(self, monkeypatch, raw, expected):
        monkeypatch.setenv("X_FLAG", raw)

        assert routing._env_flag("X_FLAG", not expected) is expected

    def test_env_flag_default_when_unset_or_blank(self, monkeypatch):
        assert routing._env_flag("X_FLAG", True) is True
        assert routing._env_flag("X_FLAG", False) is False
        monkeypatch.setenv("X_FLAG", "   ")
        assert routing._env_flag("X_FLAG", True) is True

    def test_thinking_disabled_by_default(self, monkeypatch):
        assert routing.service_thinking_disabled() is True
        monkeypatch.setenv("LLM_SERVICE_DISABLE_THINKING", "0")
        assert routing.service_thinking_disabled() is False

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(None, 1024), ("", 1024), ("abc", 1024), ("2048", 2048), ("-5", 0), ("999999", 32000), ("0", 0)],
    )
    def test_min_max_tokens(self, monkeypatch, raw, expected):
        if raw is not None:
            monkeypatch.setenv("LLM_SERVICE_MIN_MAX_TOKENS", raw)

        assert routing.service_min_max_tokens() == expected


class TestPassthroughHeaders:
    def test_no_header_for_empty_or_plain_payload(self):
        assert routing.extra_params_headers(None) == {}
        assert routing.extra_params_headers({}) == {}
        assert routing.extra_params_headers({"model": "m", "messages": []}) == {}

    @pytest.mark.parametrize("key", ["chat_template_kwargs", "top_k", "min_p", "repetition_penalty"])
    def test_header_for_nonstandard_fields(self, key):
        assert routing.extra_params_headers({key: 1}) == {"x-bf-passthrough-extra-params": "true"}

    def test_header_can_be_switched_off(self, monkeypatch):
        monkeypatch.setenv("LLM_PASSTHROUGH_EXTRA_PARAMS", "0")

        assert routing.extra_params_headers({"top_k": 40}) == {}

    def test_standard_openai_fields_do_not_need_it(self):
        assert routing.extra_params_headers({"top_p": 0.9, "frequency_penalty": 0.1}) == {}


class TestThinking:
    def test_request_extra_shape(self):
        assert routing.thinking_request_extra(True) == {"chat_template_kwargs": {"enable_thinking": True}}
        assert routing.thinking_request_extra(False) == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_no_top_level_enable_thinking(self):
        assert "enable_thinking" not in routing.thinking_request_extra(True)

    @pytest.mark.parametrize(
        ("extra", "expected"),
        [
            (None, False),
            ({}, False),
            ({"enable_thinking": True}, True),
            ({"enable_thinking": False}, False),
            ({"chat_template_kwargs": {"enable_thinking": True}}, True),
            ({"chat_template_kwargs": {"enable_thinking": False}}, False),
            ({"chat_template_kwargs": {}}, False),
            ({"chat_template_kwargs": "oops"}, False),
        ],
    )
    def test_is_thinking_requested(self, extra, expected):
        assert routing.is_thinking_requested(extra) is expected

    def test_top_level_flag_wins_over_template_kwargs(self):
        extra = {"enable_thinking": False, "chat_template_kwargs": {"enable_thinking": True}}

        assert routing.is_thinking_requested(extra) is False

    def test_service_calls_disable_thinking_by_default(self, monkeypatch):
        assert routing.service_call_request_extra() == {"chat_template_kwargs": {"enable_thinking": False}}
        monkeypatch.setenv("LLM_SERVICE_DISABLE_THINKING", "0")
        assert routing.service_call_request_extra() == {}


class TestNoThinkTag:
    def test_flag(self, monkeypatch):
        assert routing.service_no_think_tag_enabled() is True
        monkeypatch.setenv("LLM_SERVICE_NO_THINK_TAG", "false")
        assert routing.service_no_think_tag_enabled() is False

    def test_appends_to_the_last_user_message_only(self):
        messages = [
            {"role": "user", "content": "первый"},
            {"role": "assistant", "content": "ответ"},
            {"role": "user", "content": "второй"},
        ]

        out = routing.apply_no_think_tag(messages)

        assert out[2]["content"] == "второй\n\n/no_think"
        assert out[0]["content"] == "первый"

    def test_does_not_mutate_the_input(self):
        messages = [{"role": "user", "content": "q"}]

        routing.apply_no_think_tag(messages)

        assert messages == [{"role": "user", "content": "q"}]

    def test_is_idempotent(self):
        once = routing.apply_no_think_tag([{"role": "user", "content": "q"}])

        assert routing.apply_no_think_tag(once) == once

    def test_skips_multimodal_content(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "q"}]}]

        assert routing.apply_no_think_tag(messages) == messages

    def test_without_user_messages_nothing_changes(self):
        messages = [{"role": "system", "content": "s"}]

        assert routing.apply_no_think_tag(messages) == messages

    def test_empty_input(self):
        assert routing.apply_no_think_tag([]) == []


class TestErrorText:
    @pytest.mark.parametrize(
        "text",
        [
            "Ошибка LLM (HTTP 422): bad",
            "Не удалось получить ответ от модели",
            "Сервис LLM недоступен (503). Повторите запрос",
            "Модель не загружена на стороне провайдера (503).",
            "Запрос не помещается в контекстное окно модели",
            "  ОШИБКА LLM (HTTP 500)",
        ],
    )
    def test_known_error_texts(self, text):
        assert routing.is_llm_error_text(text) is True

    @pytest.mark.parametrize("text", [None, "", "  ", "Обычный ответ модели", "Расскажи про ошибки LLM"])
    def test_normal_answers_are_not_errors(self, text):
        assert routing.is_llm_error_text(text) is False

    def test_only_the_beginning_of_the_text_is_checked(self):
        assert routing.is_llm_error_text("а" * 300 + "Ошибка LLM (HTTP 500)") is False


def _http_error(status, *, json=None, text=None):
    request = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    response = httpx.Response(status, json=json, text=text, request=request) if json is not None else httpx.Response(
        status, text=text or "", request=request
    )
    return httpx.HTTPStatusError("boom", request=request, response=response)


class TestFormatHttpError:
    def test_context_overflow(self):
        message = routing.format_llm_http_error(_http_error(400, json={"detail": "maximum context length exceeded"}))

        assert "контекстное окно" in message
        assert "maximum context length exceeded" in message

    def test_context_hint_is_found_in_the_raw_body(self):
        message = routing.format_llm_http_error(_http_error(400, text="prompt is too long"))

        assert "контекстное окно" in message

    def test_503_model_not_loaded(self):
        message = routing.format_llm_http_error(_http_error(503, json={"detail": "Model not loaded"}))

        assert "не загружена" in message

    def test_503_unavailable(self):
        message = routing.format_llm_http_error(_http_error(503, json={"detail": "busy"}))

        assert "недоступен (503)" in message

    @pytest.mark.parametrize("field", ["detail", "message", "error"])
    def test_other_errors_use_the_detail_fields(self, field):
        message = routing.format_llm_http_error(_http_error(422, json={field: "плохой запрос"}))

        assert message == "Ошибка LLM (HTTP 422): плохой запрос"

    def test_json_without_known_fields_is_dumped(self):
        message = routing.format_llm_http_error(_http_error(500, json={"foo": "бар"}))

        assert message.startswith("Ошибка LLM (HTTP 500): ")
        assert "бар" in message

    def test_non_json_body_is_quoted(self):
        message = routing.format_llm_http_error(_http_error(502, text="Bad gateway"))

        assert message == "Ошибка LLM (HTTP 502): Bad gateway"

    def test_long_details_are_truncated(self):
        message = routing.format_llm_http_error(_http_error(500, json={"detail": "x" * 2000}))

        assert len(message) < 500

    def test_400_without_context_hint_is_a_plain_error(self):
        message = routing.format_llm_http_error(_http_error(400, json={"detail": "bad field"}))

        assert message == "Ошибка LLM (HTTP 400): bad field"

    def test_result_is_recognized_as_an_llm_error_text(self):
        message = routing.format_llm_http_error(_http_error(422, json={"detail": "x"}))

        assert routing.is_llm_error_text(message) is True


class TestBuildMessages:
    def test_prompt_only(self):
        assert routing.build_chat_messages("привет") == [{"role": "user", "content": "привет"}]

    def test_system_prompt_goes_first_and_is_trimmed(self):
        out = routing.build_chat_messages("q", system_prompt="  будь краток  ")

        assert out[0] == {"role": "system", "content": "будь краток"}
        assert out[-1] == {"role": "user", "content": "q"}

    def test_blank_system_prompt_is_ignored(self):
        assert routing.build_chat_messages("q", system_prompt="   ") == [{"role": "user", "content": "q"}]

    def test_history_keeps_only_valid_roles_and_content(self):
        history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "tool", "content": "c"},
            {"role": "user", "content": None},
            {"role": "", "content": "d"},
            {"role": "system", "content": "e"},
        ]

        out = routing.build_chat_messages("q", history=history)

        assert [m["content"] for m in out] == ["a", "b", "e", "q"]


class TestSampling:
    @pytest.fixture
    def active(self, monkeypatch):
        from backend.services import user_llm_settings
        from backend.utils import llm_defaults

        state = {"active": {}, "defaults": {}}
        monkeypatch.setattr(user_llm_settings, "get_active_model_settings", lambda: state["active"])
        monkeypatch.setattr(llm_defaults, "llm_settings_defaults", lambda: state["defaults"])
        return state

    def test_nothing_to_send_when_values_equal_cluster_defaults(self, active):
        active["active"] = {"top_p": 0.95, "top_k": 40}
        active["defaults"] = {"top_p": 0.95, "top_k": 40}

        assert routing.sampling_request_extra() == {}

    def test_sends_only_values_changed_by_the_user(self, active):
        active["active"] = {"top_p": 0.5, "top_k": 40, "min_p": 0.2}
        active["defaults"] = {"top_p": 0.95, "top_k": 40, "min_p": 0.05}

        assert routing.sampling_request_extra() == {"top_p": 0.5, "min_p": 0.2}

    def test_number_comparison_ignores_int_float_difference(self, active):
        active["active"] = {"top_k": 40.0}
        active["defaults"] = {"top_k": 40}

        assert routing.sampling_request_extra() == {}

    def test_repeat_penalty_is_renamed_for_vllm(self, active):
        active["active"] = {"repeat_penalty": 1.3}
        active["defaults"] = {"repeat_penalty": 1.05}

        assert routing.sampling_request_extra() == {"repetition_penalty": 1.3}

    def test_send_all_sends_defaults_too(self, active, monkeypatch):
        monkeypatch.setenv("LLM_SAMPLING_SEND_ALL", "1")
        active["active"] = {"top_p": 0.95}
        active["defaults"] = {"top_p": 0.95}

        assert routing.sampling_request_extra() == {"top_p": 0.95}

    def test_missing_values_are_skipped(self, active):
        active["active"] = {"top_p": None}

        assert routing.sampling_request_extra() == {}

    def test_master_switch(self, active, monkeypatch):
        monkeypatch.setenv("LLM_SAMPLING_ENABLED", "0")
        active["active"] = {"top_p": 0.1}

        assert routing.sampling_request_extra() == {}

    def test_unavailable_settings_give_nothing(self, monkeypatch):
        from backend.services import user_llm_settings

        def boom():
            raise RuntimeError("no settings")

        monkeypatch.setattr(user_llm_settings, "get_active_model_settings", boom)

        assert routing.sampling_request_extra() == {}

    def test_merge_never_overrides_caller_values(self, active):
        active["active"] = {"top_p": 0.2, "top_k": 10}
        active["defaults"] = {"top_p": 0.9, "top_k": 40}

        merged = routing.merge_sampling_request_extra({"top_p": 0.7, "chat_template_kwargs": {}})

        assert merged == {"top_p": 0.7, "chat_template_kwargs": {}, "top_k": 10}

    def test_merge_returns_the_input_when_there_is_nothing_to_add(self, active):
        extra = {"a": 1}

        assert routing.merge_sampling_request_extra(extra) is extra
        assert routing.merge_sampling_request_extra(None) is None

    def test_same_number_handles_booleans_strictly(self):
        assert routing._same_number(True, True) is True
        assert routing._same_number(True, 1) is False
        assert routing._same_number("a", "a") is True
        assert routing._same_number(0.1 + 0.2, 0.3) is True
