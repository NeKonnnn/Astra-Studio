# -*- coding: utf-8 -*-
"""Юнит-тесты llm-svc: tool calls, gguf-пути, схемы сообщений, пул моделей, проверка API-ключа."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core import security
from app.exceptions import (
    ModelNotLoadedError,
    PoolExhaustedError,
    ServiceUnavailableError,
)
from app.models.schemas import (
    AssistantMessage,
    ChatCompletionRequest,
    ToolMessage,
    UserMessage,
)
from app.services import model_pool as model_pool_module
from app.services.generators.tool_call_processor import ToolCallProcessor
from app.services.model_pool import ModelPool
from app.utils import gguf_paths


class TestToolCallProcessor:
    extract = staticmethod(ToolCallProcessor.extract_tool_calls)

    def test_no_calls(self):
        text, calls = self.extract("  просто ответ  ")
        assert text == "просто ответ"
        assert calls == []

    def test_single_call_is_parsed_and_removed(self):
        raw = 'Ищу.<tool_call>{"name": "search", "arguments": {"q": "кот"}}</tool_call>'
        text, calls = self.extract(raw)
        assert text == "Ищу."
        assert len(calls) == 1
        assert calls[0].type == "function"
        assert calls[0].function.name == "search"
        assert json.loads(calls[0].function.arguments) == {"q": "кот"}
        assert calls[0].id.startswith("call_0_")

    def test_multiple_calls_get_distinct_indexed_ids(self):
        raw = (
            '<tool_call>{"name": "a", "arguments": {}}</tool_call>'
            '<tool_call>{"name": "b", "arguments": {"x": 1}}</tool_call>'
        )
        _, calls = self.extract(raw)
        assert [c.function.name for c in calls] == ["a", "b"]
        assert calls[0].id.startswith("call_0_") and calls[1].id.startswith("call_1_")

    def test_multiline_json_block(self):
        raw = '<tool_call>\n{\n  "name": "f",\n  "arguments": {"k": "v"}\n}\n</tool_call>'
        _, calls = self.extract(raw)
        assert json.loads(calls[0].function.arguments) == {"k": "v"}

    def test_invalid_json_is_skipped_but_still_stripped(self):
        text, calls = self.extract("до <tool_call>{oops</tool_call> после")
        assert calls == []
        assert "tool_call" not in text

    def test_valid_call_survives_broken_neighbour(self):
        raw = '<tool_call>{bad</tool_call><tool_call>{"name": "ok", "arguments": {}}</tool_call>'
        _, calls = self.extract(raw)
        assert [c.function.name for c in calls] == ["ok"]

    def test_missing_arguments_default_to_empty_object(self):
        _, calls = self.extract('<tool_call>{"name": "f"}</tool_call>')
        assert calls[0].function.arguments == "{}"

    def test_missing_name_becomes_empty_string(self):
        _, calls = self.extract('<tool_call>{"arguments": {}}</tool_call>')
        assert calls[0].function.name == ""

    def test_non_ascii_arguments_roundtrip(self):
        _, calls = self.extract('<tool_call>{"name": "f", "arguments": {"город": "Омск"}}</tool_call>')
        assert json.loads(calls[0].function.arguments) == {"город": "Омск"}

    @pytest.mark.parametrize(
        "tools, expected",
        [(None, False), ([], False), ([{"type": "function"}], True)],
    )
    def test_should_use_tools(self, tools, expected):
        assert ToolCallProcessor.should_use_tools(tools) is expected


def _touch(path, size=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return str(path)


class TestGgufPaths:
    def test_id_for_llm_folder_is_short(self, tmp_path):
        p = _touch(tmp_path / "llm" / "qwen.gguf")
        assert gguf_paths.gguf_model_id_for_path(p, str(tmp_path)) == "qwen"

    def test_id_for_other_folder_keeps_relative_path(self, tmp_path):
        p = _touch(tmp_path / "vendor" / "sub" / "m.gguf")
        assert gguf_paths.gguf_model_id_for_path(p, str(tmp_path)) == "vendor/sub/m"

    def test_id_for_root_level_file(self, tmp_path):
        p = _touch(tmp_path / "m.GGUF")
        assert gguf_paths.gguf_model_id_for_path(p, str(tmp_path)) == "m"

    def test_id_for_nested_llm_is_not_shortened(self, tmp_path):
        p = _touch(tmp_path / "llm" / "deep" / "m.gguf")
        assert gguf_paths.gguf_model_id_for_path(p, str(tmp_path)) == "llm/deep/m"

    @pytest.mark.parametrize("model_id", ["", "   ", None])
    def test_resolve_empty(self, tmp_path, model_id):
        assert gguf_paths.resolve_gguf_path(model_id, str(tmp_path)) is None

    def test_resolve_by_relative_id(self, tmp_path):
        p = _touch(tmp_path / "vendor" / "m.gguf")
        assert gguf_paths.resolve_gguf_path("vendor/m", str(tmp_path)) == p

    def test_resolve_accepts_gguf_suffix_and_backslashes(self, tmp_path):
        p = _touch(tmp_path / "vendor" / "m.gguf")
        assert gguf_paths.resolve_gguf_path("vendor\\m.gguf", str(tmp_path)) == p

    def test_resolve_short_name_in_llm(self, tmp_path):
        p = _touch(tmp_path / "llm" / "qwen.gguf")
        assert gguf_paths.resolve_gguf_path("qwen", str(tmp_path)) == p

    def test_resolve_by_stem_search(self, tmp_path):
        p = _touch(tmp_path / "a" / "b" / "unique.gguf")
        assert gguf_paths.resolve_gguf_path("unique", str(tmp_path)) == p

    def test_resolve_prefers_llm_folder_on_ambiguity(self, tmp_path):
        _touch(tmp_path / "other" / "dup.gguf")
        preferred = _touch(tmp_path / "llm" / "sub" / "dup.gguf")
        assert gguf_paths.resolve_gguf_path("dup", str(tmp_path)) == preferred

    def test_resolve_missing(self, tmp_path):
        assert gguf_paths.resolve_gguf_path("nope", str(tmp_path)) is None
        assert gguf_paths.resolve_gguf_path("nope", str(tmp_path / "absent")) is None

    def test_list_models(self, tmp_path):
        _touch(tmp_path / "llm" / "a.gguf", size=2 * 1024 * 1024)
        _touch(tmp_path / "b" / "c.gguf", size=1024)
        _touch(tmp_path / "readme.txt")
        models = {m["id"]: m for m in gguf_paths.list_gguf_models(str(tmp_path))}
        assert set(models) == {"a", "b/c"}
        assert models["a"]["size_mb"] == 2.0
        assert models["a"]["object"] == "model"
        assert models["a"]["owned_by"] == "local"

    def test_list_models_missing_root(self, tmp_path):
        assert gguf_paths.list_gguf_models(str(tmp_path / "absent")) == []


class TestMessageSchemas:
    def test_user_text_list_is_flattened(self):
        msg = UserMessage(content=[{"type": "text", "text": "при"}, {"type": "text", "text": "вет"}])
        assert msg.content == "привет"

    def test_user_list_with_image_kept_as_is(self):
        parts = [{"type": "text", "text": "что тут?"}, {"type": "image_url", "image_url": {"url": "data:..."}}]
        assert UserMessage(content=parts).content == parts

    def test_user_plain_string(self):
        assert UserMessage(content="hi").content == "hi"

    def test_tool_message_plain_text_is_wrapped(self):
        msg = ToolMessage(content="результат", tool_call_id="1", name="f")
        assert json.loads(msg.content) == [{"type": "text", "text": "результат"}]

    def test_tool_message_valid_json_string_untouched(self):
        raw = '{"a": 1}'
        assert ToolMessage(content=raw, tool_call_id="1", name="f").content == raw

    def test_tool_message_list_is_serialized(self):
        parts = [{"type": "text", "text": "x"}]
        msg = ToolMessage(content=parts, tool_call_id="1", name="f")
        assert json.loads(msg.content) == parts

    def test_tool_message_other_types_wrapped(self):
        msg = ToolMessage(content=42, tool_call_id="1", name="f")
        assert json.loads(msg.content) == [{"type": "text", "text": "42"}]

    def test_assistant_defaults(self):
        msg = AssistantMessage()
        assert msg.content is None and msg.tool_calls is None


class TestChatCompletionRequest:
    def _req(self, **over):
        return ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}], **over)

    def test_defaults(self):
        req = self._req()
        assert req.model == "abstract-model"
        assert req.temperature == 0.7
        assert req.max_tokens == 256
        assert req.stream is False
        assert req.enable_thinking is None

    @pytest.mark.parametrize(
        "field, value",
        [
            ("temperature", -0.1),
            ("temperature", 2.1),
            ("max_tokens", 0),
            ("frequency_penalty", 2.5),
            ("presence_penalty", -3),
        ],
    )
    def test_out_of_range_rejected(self, field, value):
        with pytest.raises(ValidationError):
            self._req(**{field: value})

    @pytest.mark.parametrize("role", ["system", "user", "assistant"])
    def test_roles_resolve_to_matching_message(self, role):
        req = ChatCompletionRequest(messages=[{"role": role, "content": "x"}])
        assert req.messages[0].role.value == role

    def test_unknown_role_rejected(self):
        with pytest.raises(ValidationError):
            ChatCompletionRequest(messages=[{"role": "robot", "content": "x"}])

    def test_tools_parsed(self):
        req = self._req(tools=[{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}])
        assert req.tools[0].function.name == "f"


class FakeContext:
    def __init__(self, context_id, *, fail=False, ready=True):
        self.context_id = context_id
        self.fail = fail
        self.is_ready = ready
        self.resets = 0
        self.initialized = 0
        self.cleaned = 0

    async def initialize(self):
        if self.fail:
            raise RuntimeError("no model")
        self.initialized += 1

    async def reset_cache(self):
        self.resets += 1

    async def cleanup(self):
        self.cleaned += 1


@pytest.fixture
def patch_contexts(monkeypatch):
    """ModelPool создаёт FakeContext; fail_ids задаёт, какие контексты не инициализируются."""
    created = []
    holder = SimpleNamespace(fail_ids=set())

    def factory(context_id):
        ctx = FakeContext(context_id, fail=context_id in holder.fail_ids)
        created.append(ctx)
        return ctx

    monkeypatch.setattr(model_pool_module, "ModelContext", factory)
    holder.created = created
    return holder


class TestModelPool:
    async def test_not_ready_before_initialize(self):
        pool = ModelPool(2)
        assert pool.is_ready is False
        with pytest.raises(ServiceUnavailableError):
            await pool.acquire()

    async def test_initialize_all(self, patch_contexts):
        pool = ModelPool(3)
        await pool.initialize()
        assert (pool.total_count, pool.available_count, pool.max_concurrent_requests) == (3, 3, 3)
        assert pool.is_ready is True

    async def test_partial_initialization_limits_concurrency(self, patch_contexts):
        patch_contexts.fail_ids = {1}
        pool = ModelPool(3)
        await pool.initialize()
        assert pool.available_count == 2
        assert pool.max_concurrent_requests == 2

    async def test_total_failure_gives_degraded_pool(self, patch_contexts):
        patch_contexts.fail_ids = {0, 1}
        pool = ModelPool(2)
        await pool.initialize()
        assert pool.is_ready is False
        with pytest.raises(PoolExhaustedError):
            await pool.acquire()

    async def test_acquire_release_cycle(self, patch_contexts):
        pool = ModelPool(1)
        await pool.initialize()
        ctx = await pool.acquire()
        assert pool.active_requests_count == 1
        assert pool.available_count == 0
        await pool.release(ctx)
        assert pool.active_requests_count == 0
        assert pool.available_count == 1
        assert ctx.resets == 1

    async def test_busy_pool_raises(self, patch_contexts):
        pool = ModelPool(1)
        await pool.initialize()
        await pool.acquire()
        with pytest.raises(PoolExhaustedError, match="busy"):
            await pool.acquire()

    async def test_release_unknown_context_never_goes_negative(self, patch_contexts):
        pool = ModelPool(1)
        await pool.initialize()
        await pool.release(FakeContext(99))
        assert pool.active_requests_count == 0

    async def test_release_not_ready_context_is_reinitialized(self, patch_contexts):
        pool = ModelPool(1)
        await pool.initialize()
        ctx = await pool.acquire()
        ctx.is_ready = False
        await pool.release(ctx)
        await asyncio.sleep(0.01)  # фоновая переинициализация
        assert ctx.initialized == 2
        assert pool.available_count == 1

    async def test_cleanup_resets_state(self, patch_contexts):
        pool = ModelPool(2)
        await pool.initialize()
        await pool.cleanup()
        assert pool.total_count == 0
        assert pool.is_ready is False
        assert all(c.cleaned == 1 for c in patch_contexts.created)

    def test_exception_hierarchy(self):
        assert issubclass(ModelNotLoadedError, ServiceUnavailableError)
        assert issubclass(PoolExhaustedError, ServiceUnavailableError)


@pytest.fixture
def sec(monkeypatch):
    """Подмена настроек безопасности (реальный SecurityConfig не имеет поля api_key)."""
    holder = SimpleNamespace(security=SimpleNamespace(enabled=True, api_key="secret"))
    monkeypatch.setattr(security, "settings", holder)
    return holder.security


class TestVerifyApiKey:
    async def test_disabled_allows_everything(self, sec):
        sec.enabled = False
        assert await security.verify_api_key(None) is True

    async def test_valid_key(self, sec):
        assert await security.verify_api_key("secret") is True

    @pytest.mark.parametrize(
        "key, detail",
        [(None, "API key is missing"), ("", "API key is missing"), ("wrong", "Invalid API key")],
    )
    async def test_rejected(self, sec, key, detail):
        with pytest.raises(HTTPException) as exc:
            await security.verify_api_key(key)
        assert exc.value.status_code == 401
        assert exc.value.detail == detail
        assert exc.value.headers == {"WWW-Authenticate": "API-Key"}

    @pytest.mark.parametrize("configured", [None, ""])
    async def test_unconfigured_server_key(self, sec, configured):
        sec.api_key = configured
        with pytest.raises(HTTPException) as exc:
            await security.verify_api_key("anything")
        assert exc.value.detail == "API key is not configured on server"
