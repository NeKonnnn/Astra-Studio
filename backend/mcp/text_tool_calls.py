"""Парсинг tool calls из текста ответа модели (Qwen / Hermes XML / JSON)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from backend.llm_providers.base import ToolCall

_TOOL_CALL_BLOCK = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE
)
_FUNCTION_TAG = re.compile(
    r"<function=([^>\s]+)>(.*?)</function>", re.DOTALL | re.IGNORECASE
)
_FUNCTION_SELF = re.compile(r"<function=([^>/\s]+)\s*/?\s*>", re.IGNORECASE)
_JSON_FENCE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE
)


def _looks_like_owui_tool_calls_json(text: str) -> bool:
    if not text or "tool_calls" not in text:
        return False
    lowered = text.lower()
    # Не путать с обычным ответом, где слово встречается в прозе.
    return '"tool_calls"' in text or "'tool_calls'" in text or "tool_calls" in lowered


def content_has_text_tool_calls(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if "<tool_call>" in lowered or "<function=" in lowered:
        return True
    return _looks_like_owui_tool_calls_json(text)


def _extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    # Сначала fenced ```json ... ```
    for match in _JSON_FENCE.finditer(text):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _repair_truncated_json(text: str) -> Optional[str]:
    """Закрывает обрезанный JSON (finish_reason=length): кавычки/скобки."""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    chunk = text[start:].strip()
    # Убрать незакрытый fence в конце.
    if chunk.endswith("```"):
        chunk = chunk[: -3].rstrip()
    # Если обрыв внутри строки — закрываем кавычку.
    in_string = False
    escape = False
    for ch in chunk:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
    if in_string:
        chunk += '"'
    # Хвост вида `"key":` / `,` — убрать незавершённую пару.
    chunk = re.sub(r",\s*$", "", chunk)
    chunk = re.sub(r":\s*$", ": null", chunk)
    opens = chunk.count("{") - chunk.count("}")
    opens_arr = chunk.count("[") - chunk.count("]")
    if opens < 0 or opens_arr < 0:
        return None
    chunk += "]" * max(0, opens_arr) + "}" * max(0, opens)
    return chunk


def _tool_calls_from_payload(payload: dict) -> List[ToolCall]:
    raw_calls = payload.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        return []
    tool_calls: List[ToolCall] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_calls:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("function") or "").strip()
        if not name:
            continue
        args: Any = (
            item.get("parameters") if "parameters" in item else item.get("arguments")
        )
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"raw": args}
        if not isinstance(args, dict):
            args = {}
        # Обрезанный prompt всё ещё полезен для субагента.
        if "prompt" in args and isinstance(args["prompt"], str):
            args["prompt"] = args["prompt"].rstrip()
        key = (name, json.dumps(args, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        tool_calls.append(
            ToolCall(
                id=f"call_{uuid.uuid4().hex[:12]}",
                name=name,
                arguments=args,
            )
        )
    return tool_calls


def _extract_partial_tool_calls_regex(text: str) -> List[ToolCall]:
    """Когда JSON не чинится: вытащить name + parameters по кускам."""
    if not text or '"tool_calls"' not in text:
        return []
    calls: List[ToolCall] = []
    # Первый объект в tool_calls с name.
    name_m = re.search(
        r'"name"\s*:\s*"([^"]+)"',
        text,
    )
    if not name_m:
        return []
    name = name_m.group(1).strip()
    args: Dict[str, Any] = {}
    for key in ("subagent_type", "prompt", "task", "query", "city"):
        km = re.search(
            rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)(?:"|$)',
            text,
            re.DOTALL,
        )
        if km:
            raw_val = km.group(1)
            try:
                args[key] = json.loads(f'"{raw_val}"')
            except json.JSONDecodeError:
                args[key] = raw_val.encode("utf-8").decode("unicode_escape", errors="ignore")
    # Числовые/простые без кавычек
    for key in ("agent_id",):
        km = re.search(rf'"{key}"\s*:\s*(\d+)', text)
        if km:
            args[key] = int(km.group(1))
    if not name:
        return []
    # Для subagent нужен хотя бы prompt/task или subagent_type.
    if name == "subagent" and not (
        args.get("prompt") or args.get("task") or args.get("subagent_type")
    ):
        return []
    calls.append(
        ToolCall(
            id=f"call_{uuid.uuid4().hex[:12]}",
            name=name,
            arguments=args,
        )
    )
    return calls


def extract_owui_tool_calls(text: str) -> Tuple[str, List[ToolCall]]:
    """Разбирает формат {"tool_calls":[{"name","parameters"|"arguments"}]}.

    Возвращает (очищенный текст без JSON FC, список ToolCall).
    Умеет чинить обрезанный JSON (finish_reason=length).
    """
    if not text or not _looks_like_owui_tool_calls_json(text):
        return text, []
    payload = _extract_json_object(text)
    tool_calls: List[ToolCall] = []
    if isinstance(payload, dict):
        tool_calls = _tool_calls_from_payload(payload)

    if not tool_calls:
        repaired = _repair_truncated_json(text)
        if repaired:
            try:
                data = json.loads(repaired)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                tool_calls = _tool_calls_from_payload(data)

    if not tool_calls:
        tool_calls = _extract_partial_tool_calls_regex(text)

    if not tool_calls:
        return text, []

    # Убираем JSON FC из видимого текста (весь объект / fence / обрубок).
    cleaned = _JSON_FENCE.sub("", text)
    start = cleaned.find("{")
    if start >= 0:
        cleaned = cleaned[:start].strip()
    else:
        cleaned = cleaned.strip()
    return cleaned, tool_calls


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

    # JSON FC: {"tool_calls":[{"name","parameters"}]} в content.
    if not tool_calls:
        cleaned, owui_calls = extract_owui_tool_calls(text)
        if owui_calls:
            return cleaned, owui_calls

    return cleaned, tool_calls
