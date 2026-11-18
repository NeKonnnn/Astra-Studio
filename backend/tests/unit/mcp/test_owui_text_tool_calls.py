from backend.mcp.text_tool_calls import (
    content_has_text_tool_calls,
    extract_tool_calls_from_content,
)


def test_owui_json_tool_calls_parsed():
    raw = (
        '{\n  "tool_calls": [\n'
        '    {"name": "subagent", "parameters": '
        '{"subagent_type": "high", "prompt": "Определите файлы"}}\n'
        "  ]\n}"
    )
    assert content_has_text_tool_calls(raw)
    cleaned, calls = extract_tool_calls_from_content(raw)
    assert len(calls) == 1
    assert calls[0].name == "subagent"
    assert calls[0].arguments["subagent_type"] == "high"
    assert "tool_calls" not in cleaned


def test_owui_json_in_fence_parsed():
    raw = (
        '```json\n{"tool_calls":[{"name":"weather","parameters":{"city":"Abu Dhabi"}}]}\n```'
    )
    cleaned, calls = extract_tool_calls_from_content(raw)
    assert len(calls) == 1
    assert calls[0].name == "weather"
    assert calls[0].arguments.get("city") == "Abu Dhabi"
    assert "tool_calls" not in cleaned


def test_truncated_owui_json_repaired():
    """finish_reason=length: JSON обрывается mid-string."""
    raw = (
        '```json\n{\n  "tool_calls": [\n    {\n'
        '      "name": "subagent",\n'
        '      "parameters": {\n'
        '        "subagent_type": "high",\n'
        '        "prompt": "Определите и перечислите'
    )
    cleaned, calls = extract_tool_calls_from_content(raw)
    assert len(calls) == 1
    assert calls[0].name == "subagent"
    assert calls[0].arguments.get("subagent_type") == "high"
    assert "Определите" in str(calls[0].arguments.get("prompt") or "")
    assert "tool_calls" not in cleaned
