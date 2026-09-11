"""Regression tests for per-provider request_extra filtering.

``thinking_request_extra()`` always emits ``enable_thinking`` **and**
``chat_template_kwargs`` so that llm-svc / llama.cpp and vLLM-style gateways both
pick the flag up. api.openai.com, however, validates the request body strictly
and answers

  400 Unrecognized request arguments supplied: chat_template_kwargs,
  enable_thinking

rejecting the whole call, so every chat request through a ``kind: openai``
provider failed.

The point of these tests is the pair: the strict OpenAI endpoint must drop those
fields, and every OpenAI-compatible gateway must keep receiving them. The second
half is what protects the intended behaviour of the thinking toggle.
"""

import unittest

import pytest

try:
    from backend.llm_providers.base import LLMProviderConfig
    from backend.llm_providers.llm_svc import LlmSvcProvider
    from backend.llm_providers.ollama import OllamaProvider
    from backend.llm_providers.openai_compat import OpenAICompatProvider
    from backend.llm_providers.openai_native import OpenAIProvider, OpenRouterProvider
    from backend.llm_providers.routing import thinking_request_extra
except Exception as e:  # noqa: BLE001
    # Импорт пакета backend тянет рантайм-зависимости (httpx, asyncpg и пр.).
    # В окружении без них тест не запускаем, а не падаем на сборе.
    pytest.skip(f"backend runtime deps unavailable: {e}", allow_module_level=True)


_THINKING_KEYS = ("enable_thinking", "chat_template_kwargs")


def _provider(cls, kind: str):
    return cls(LLMProviderConfig(id="t", kind=kind, base_url="http://example.invalid"))


def _payload(provider, request_extra):
    """Payload, как его собирают chat/chat_completion/stream_chat."""
    payload = {"model": "m", "messages": [], "temperature": 0.7, "max_tokens": 16, "stream": False}
    return provider._apply_request_extra(payload, request_extra)


class TestStrictOpenAIDropsThinkingFields(unittest.TestCase):
    """kind: openai → api.openai.com, строгая валидация тела."""

    def setUp(self):
        self.provider = _provider(OpenAIProvider, "openai")

    def test_thinking_fields_dropped_when_disabled(self):
        payload = _payload(self.provider, thinking_request_extra(False))
        for key in _THINKING_KEYS:
            self.assertNotIn(key, payload)

    def test_thinking_fields_dropped_when_enabled(self):
        """Даже при включённом мышлении: OpenAI отклоняет запрос целиком."""
        payload = _payload(self.provider, thinking_request_extra(True))
        for key in _THINKING_KEYS:
            self.assertNotIn(key, payload)

    def test_unrelated_extra_fields_pass_through(self):
        """Фильтруются только перечисленные поля, а не request_extra целиком."""
        payload = _payload(self.provider, {"seed": 42, "enable_thinking": True})
        self.assertEqual(payload["seed"], 42)
        self.assertNotIn("enable_thinking", payload)


class TestCompatibleGatewaysKeepThinkingFields(unittest.TestCase):
    """Шлюзы читают эти поля — поведение обязано остаться прежним."""

    def test_openai_compat_keeps_fields(self):
        payload = _payload(_provider(OpenAICompatProvider, "openai-compat"), thinking_request_extra(True))
        self.assertIs(payload["enable_thinking"], True)
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": True})

    def test_llm_svc_keeps_fields(self):
        payload = _payload(_provider(LlmSvcProvider, "llm-svc"), thinking_request_extra(False))
        self.assertIs(payload["enable_thinking"], False)
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})

    def test_ollama_keeps_fields(self):
        payload = _payload(_provider(OllamaProvider, "ollama"), thinking_request_extra(True))
        self.assertIn("enable_thinking", payload)
        self.assertIn("chat_template_kwargs", payload)

    def test_openrouter_keeps_fields(self):
        """OpenRouter наследуется от OpenAIProvider, но отказ на нём не воспроизводился."""
        payload = _payload(_provider(OpenRouterProvider, "openrouter"), thinking_request_extra(True))
        self.assertIn("enable_thinking", payload)
        self.assertIn("chat_template_kwargs", payload)


class TestApplyRequestExtraBasics(unittest.TestCase):
    def test_none_values_are_skipped(self):
        payload = _payload(_provider(OpenAICompatProvider, "openai-compat"), {"seed": None, "top_p": 0.9})
        self.assertNotIn("seed", payload)
        self.assertEqual(payload["top_p"], 0.9)

    def test_empty_request_extra_leaves_payload_intact(self):
        provider = _provider(OpenAICompatProvider, "openai-compat")
        for extra in (None, {}):
            payload = _payload(provider, extra)
            self.assertEqual(
                sorted(payload), ["max_tokens", "messages", "model", "stream", "temperature"]
            )


if __name__ == "__main__":
    unittest.main()
