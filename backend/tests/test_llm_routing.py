# -*- coding: utf-8 -*-
"""Тесты backend.llm_providers.routing: выбор маршрута, флаги env, ошибки LLM."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.llm_providers import routing  # noqa: E402


def _http_error(status: int, *, json_body=None, text: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    if json_body is not None:
        response = httpx.Response(status, json=json_body, request=request)
    else:
        response = httpx.Response(status, text=text or "", request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


class TestShouldUseLlmSvcDirect:
    @pytest.mark.parametrize(
        "model_path, images, expected",
        [
            (None, None, False),
            ("openai://gpt", None, False),
            ("llm-svc://qwen", None, True),
            ("  LLM-SVC://Qwen  ", None, True),
            ("openai://gpt", ["img.png"], True),
            (None, ["img.png"], True),
            ("openai://gpt", [], False),
        ],
    )
    def test_routes(self, model_path, images, expected):
        assert routing.should_use_llm_svc_direct(model_path=model_path, images=images) is expected


class TestRegistryResponseUsable:
    @pytest.mark.parametrize(
        "value, expected",
        [(None, False), ("", False), ("   \n", False), ("ответ", True), (" ok ", True)],
    )
    def test_usable(self, value, expected):
        assert routing.registry_response_usable(value) is expected


class TestIsLlmErrorText:
    @pytest.mark.parametrize(
        "text",
        [
            "Ошибка LLM (HTTP 422): bad",
            "Не удалось получить ответ от модели",
            "Сервис LLM недоступен (503). Повторите запрос",
            "Модель не загружена на стороне провайдера (503).",
            "Запрос не помещается в контекстное окно модели: ...",
        ],
    )
    def test_error_markers(self, text):
        assert routing.is_llm_error_text(text) is True

    @pytest.mark.parametrize("text", [None, "", "  ", "Обычный ответ модели", "Привет!"])
    def test_normal_text(self, text):
        assert routing.is_llm_error_text(text) is False

    def test_marker_only_checked_in_head(self):
        # Маркер глубже 200 символов — это уже содержимое ответа, а не ошибка.
        text = "x" * 250 + "Ошибка LLM (HTTP 500)"
        assert routing.is_llm_error_text(text) is False


class TestEnvFlags:
    @pytest.mark.parametrize("raw", ["0", "false", "No", "OFF"])
    def test_flag_disabled_values(self, monkeypatch, raw):
        monkeypatch.setenv("LLM_SERVICE_DISABLE_THINKING", raw)
        assert routing.service_thinking_disabled() is False

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "anything"])
    def test_flag_enabled_values(self, monkeypatch, raw):
        monkeypatch.setenv("LLM_SERVICE_DISABLE_THINKING", raw)
        assert routing.service_thinking_disabled() is True

    def test_flag_defaults(self, monkeypatch):
        for name in (
            "LLM_SERVICE_DISABLE_THINKING",
            "LLM_PASSTHROUGH_EXTRA_PARAMS",
            "LLM_SAMPLING_ENABLED",
            "LLM_SAMPLING_SEND_ALL",
            "LLM_SERVICE_NO_THINK_TAG",
        ):
            monkeypatch.delenv(name, raising=False)
        assert routing.service_thinking_disabled() is True
        assert routing.passthrough_extra_params_enabled() is True
        assert routing.sampling_enabled() is True
        assert routing.sampling_send_all() is False
        assert routing.service_no_think_tag_enabled() is True

    @pytest.mark.parametrize(
        "raw, expected",
        [(None, 1024), ("", 1024), ("abc", 1024), ("2048", 2048), ("-5", 0), ("99999", 32000)],
    )
    def test_service_min_max_tokens(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("LLM_SERVICE_MIN_MAX_TOKENS", raising=False)
        else:
            monkeypatch.setenv("LLM_SERVICE_MIN_MAX_TOKENS", raw)
        assert routing.service_min_max_tokens() == expected


class TestExtraParamsHeaders:
    def test_empty_payload(self):
        assert routing.extra_params_headers(None) == {}
        assert routing.extra_params_headers({}) == {}

    def test_payload_without_extra_keys(self):
        assert routing.extra_params_headers({"model": "m", "messages": []}) == {}

    @pytest.mark.parametrize("key", ["chat_template_kwargs", "top_k", "min_p", "repetition_penalty"])
    def test_header_for_extra_keys(self, monkeypatch, key):
        monkeypatch.delenv("LLM_PASSTHROUGH_EXTRA_PARAMS", raising=False)
        assert routing.extra_params_headers({key: 1}) == {"x-bf-passthrough-extra-params": "true"}

    def test_header_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("LLM_PASSTHROUGH_EXTRA_PARAMS", "0")
        assert routing.extra_params_headers({"top_k": 40}) == {}


class TestThinking:
    @pytest.mark.parametrize("flag", [True, False])
    def test_thinking_request_extra_shape(self, flag):
        extra = routing.thinking_request_extra(flag)
        assert extra == {
            "enable_thinking": flag,
            "chat_template_kwargs": {"enable_thinking": flag},
        }

    @pytest.mark.parametrize(
        "extra, expected",
        [
            (None, False),
            ({}, False),
            ({"enable_thinking": True}, True),
            ({"enable_thinking": False}, False),
            ({"chat_template_kwargs": {"enable_thinking": True}}, True),
            ({"chat_template_kwargs": {"enable_thinking": False}}, False),
            ({"chat_template_kwargs": "not-a-dict"}, False),
            # Верхнеуровневый флаг приоритетнее вложенного.
            ({"enable_thinking": False, "chat_template_kwargs": {"enable_thinking": True}}, False),
        ],
    )
    def test_is_thinking_requested(self, extra, expected):
        assert routing.is_thinking_requested(extra) is expected

    def test_service_call_request_extra_disables_thinking(self, monkeypatch):
        monkeypatch.delenv("LLM_SERVICE_DISABLE_THINKING", raising=False)
        assert routing.service_call_request_extra() == routing.thinking_request_extra(False)

    def test_service_call_request_extra_off(self, monkeypatch):
        monkeypatch.setenv("LLM_SERVICE_DISABLE_THINKING", "0")
        assert routing.service_call_request_extra() == {}


class TestApplyNoThinkTag:
    def test_empty(self):
        assert routing.apply_no_think_tag([]) == []

    def test_appends_to_last_user_message(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "last"},
        ]
        out = routing.apply_no_think_tag(messages)
        assert out[-1]["content"] == "last\n\n/no_think"
        assert out[1]["content"] == "first"

    def test_does_not_mutate_input(self):
        messages = [{"role": "user", "content": "q"}]
        out = routing.apply_no_think_tag(messages)
        assert messages[0]["content"] == "q"
        assert out is not messages

    def test_idempotent(self):
        once = routing.apply_no_think_tag([{"role": "user", "content": "q"}])
        twice = routing.apply_no_think_tag(once)
        assert twice[0]["content"].count("/no_think") == 1

    def test_multimodal_content_untouched(self):
        parts = [{"type": "text", "text": "hi"}]
        out = routing.apply_no_think_tag([{"role": "user", "content": parts}])
        assert out[0]["content"] == parts

    def test_no_user_message(self):
        messages = [{"role": "system", "content": "s"}]
        assert routing.apply_no_think_tag(messages) == messages


class TestSamplingMerge:
    def test_merge_without_sampling_returns_original(self, monkeypatch):
        monkeypatch.setattr(routing, "sampling_request_extra", lambda: {})
        extra = {"a": 1}
        assert routing.merge_sampling_request_extra(extra) is extra
        assert routing.merge_sampling_request_extra(None) is None

    def test_merge_does_not_override_existing(self, monkeypatch):
        monkeypatch.setattr(routing, "sampling_request_extra", lambda: {"top_p": 0.5, "min_p": 0.1})
        out = routing.merge_sampling_request_extra({"top_p": 0.9})
        assert out == {"top_p": 0.9, "min_p": 0.1}

    def test_merge_into_none(self, monkeypatch):
        monkeypatch.setattr(routing, "sampling_request_extra", lambda: {"top_k": 20})
        assert routing.merge_sampling_request_extra(None) == {"top_k": 20}

    def test_sampling_disabled(self, monkeypatch):
        monkeypatch.setenv("LLM_SAMPLING_ENABLED", "0")
        assert routing.sampling_request_extra() == {}

    @pytest.mark.parametrize(
        "left, right, expected",
        [
            (0.95, 0.95, True),
            (1, 1.0, True),
            (0.1, 0.2, False),
            (True, 1, False),
            (True, True, True),
            ("a", "a", True),
            (None, 0, False),
        ],
    )
    def test_same_number(self, left, right, expected):
        assert routing._same_number(left, right) is expected


class TestFormatLlmHttpError:
    def test_context_overflow(self):
        msg = routing.format_llm_http_error(
            _http_error(400, json_body={"detail": "maximum context length exceeded"})
        )
        assert "контекстное окно" in msg
        assert routing.is_llm_error_text(msg)

    def test_503_not_loaded(self):
        msg = routing.format_llm_http_error(_http_error(503, json_body={"detail": "model not loaded"}))
        assert "не загружена" in msg

    def test_503_generic(self):
        msg = routing.format_llm_http_error(_http_error(503, text="down"))
        assert "недоступен" in msg

    def test_detail_from_json(self):
        msg = routing.format_llm_http_error(_http_error(422, json_body={"message": "bad field"}))
        assert msg == "Ошибка LLM (HTTP 422): bad field"

    def test_json_without_known_keys(self):
        msg = routing.format_llm_http_error(_http_error(500, json_body={"x": 1}))
        assert msg.startswith("Ошибка LLM (HTTP 500):")
        assert '"x": 1' in msg

    def test_non_json_body(self):
        msg = routing.format_llm_http_error(_http_error(502, text="Bad gateway"))
        assert msg.startswith("Ошибка LLM (HTTP 502):")
        assert "Bad gateway" in msg

    def test_400_without_context_markers_is_plain_error(self):
        msg = routing.format_llm_http_error(_http_error(400, json_body={"detail": "invalid role"}))
        assert msg == "Ошибка LLM (HTTP 400): invalid role"


class TestBuildChatMessages:
    def test_prompt_only(self):
        assert routing.build_chat_messages("hi") == [{"role": "user", "content": "hi"}]

    def test_system_and_history(self):
        out = routing.build_chat_messages(
            "q",
            history=[
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "tool", "content": "ignored"},
                {"role": "user", "content": None},
                {"content": "no role"},
            ],
            system_prompt="  be brief  ",
        )
        assert out == [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q"},
        ]

    def test_blank_system_prompt_skipped(self):
        assert routing.build_chat_messages("q", system_prompt="   ") == [
            {"role": "user", "content": "q"}
        ]
