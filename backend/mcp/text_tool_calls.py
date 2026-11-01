"""Парсинг tool calls из текста ответа модели (Qwen / Hermes XML)."""

from __future__ import annotations

import json
import re
import uuid
from typing import List, Tuple

from backend.llm_providers.base import ToolCall

_TOOL_CALL_BLOCK = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE
)
_FUNCTION_TAG = re.compile(
    r"<function=([^>\s]+)>(.*?)</function>", re.DOTALL | re.IGNORECASE
)
_FUNCTION_SELF = re.compile(r"<function=([^>/\s]+)\s*/?\s*>", re.IGNORECASE)


def content_has_text_tool_calls(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return "<tool_call>" in lowered or "<function=" in lowered


def extract_tool_calls_from_content(text: str) -> Tuple[str, List[ToolCall]]:
    """Извлекает tool calls из XML/JSON в content; возвращает (очищенный текст, calls)."""
    if not text:
        return text, []

    tool_calls: List[ToolCall] = []
    seen_calls: set[tuple[str, str]] = set()

    def _append(name: str, arguments: dict) -> None:
        name = name.strip()
        if not name:
            return
        # одинаковые вызовы схлопываются; та же функция с другими аргументами — нет
        call_key = (name, json.dumps(arguments, sort_keys=True, default=str))
        if call_key in seen_calls:
            return
        seen_calls.add(call_key)
        tool_calls.append(
            ToolCall(
                id=f"call_{uuid.uuid4().hex[:12]}",
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )

    def _parse_args(body: str) -> dict:
        body = (body or "").strip()
        if not body:
            return {}
        if body.startswith("{"):
            try:
                parsed = json.loads(body)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    for block in _TOOL_CALL_BLOCK.findall(text):
        block = block.strip()
        if block.startswith("{"):
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                name = data.get("name") or data.get("function")
                args = data.get("arguments") or data.get("parameters") or {}
                if name:
                    _append(str(name), args if isinstance(args, dict) else {})
            continue
        for name, body in _FUNCTION_TAG.findall(block):
            _append(name, _parse_args(body))
        # пустой вызов — только тег, не вошедший в разобранные <function=..>..</function>
        for name in _FUNCTION_SELF.findall(_FUNCTION_TAG.sub("", block)):
            _append(name, {})

    if not tool_calls:
        for name, body in _FUNCTION_TAG.findall(text):
            _append(name, _parse_args(body))
        for name in _FUNCTION_SELF.findall(_FUNCTION_TAG.sub("", text)):
            _append(name, {})

    cleaned = _TOOL_CALL_BLOCK.sub("", text).strip()
    if tool_calls:
        for tc in tool_calls:
            cleaned = re.sub(
                rf"<function={re.escape(tc.name)}[^>]*>.*?</function>",
                "",
                cleaned,
                flags=re.DOTALL | re.IGNORECASE,
            )
            cleaned = re.sub(
                rf"<function={re.escape(tc.name)}\s*/?\s*>",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
        cleaned = cleaned.strip()
    return cleaned, tool_calls
