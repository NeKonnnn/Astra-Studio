# -*- coding: utf-8 -*-
"""Тесты backend.mcp.result_parser: разбор результатов MCP и ужатие для LLM/UI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.mcp import result_parser as rp  # noqa: E402
from backend.mcp.types import ParsedMcpResult  # noqa: E402


class TestParseToStruct:
    def test_none(self):
        assert rp.parse_mcp_result_to_struct(None).text == ""

    def test_plain_string(self):
        parsed = rp.parse_mcp_result_to_struct("hello")
        assert parsed.text == "hello"
        assert parsed.raw == "hello"

    def test_dict_with_text(self):
        assert rp.parse_mcp_result_to_struct({"text": "t"}).text == "t"

    def test_dict_unknown_shape_stringified(self):
        parsed = rp.parse_mcp_result_to_struct({"a": 1})
        assert parsed.text == str({"a": 1})

    def test_dict_wrapping_content_list(self):
        parsed = rp.parse_mcp_result_to_struct({"content": [{"type": "text", "text": "inner"}]})
        assert parsed.text == "inner"

    def test_dict_wrapping_contents_list(self):
        parsed = rp.parse_mcp_result_to_struct({"contents": [{"type": "text", "text": "a"}]})
        assert parsed.text == "a"

    def test_list_joins_text_parts(self):
        parsed = rp.parse_mcp_result_to_struct(
            [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}, "three"]
        )
        assert parsed.text == "one\ntwo\nthree"

    def test_list_collects_attachments(self):
        parsed = rp.parse_mcp_result_to_struct(
            [
                {"type": "image", "data": "AAA", "mimeType": "image/jpeg"},
                {"type": "audio", "data": "BBB"},
                {"type": "resource", "resource": {"uri": "file:///a", "text": "body"}},
            ]
        )
        assert [i["mimeType"] for i in parsed.images] == ["image/jpeg"]
        assert [a["mimeType"] for a in parsed.audio] == ["audio/wav"]
        assert parsed.resources[0]["uri"] == "file:///a"
        assert parsed.resources[0]["text"] == "body"

    def test_scalar_stringified(self):
        assert rp.parse_mcp_result_to_struct(42).text == "42"

    def test_parse_mcp_result_returns_llm_string(self):
        assert rp.parse_mcp_result([{"type": "text", "text": "ok"}]) == "ok"


class TestParseContentItem:
    def test_text_bytes_decoded(self):
        parsed = rp._parse_content_item({"type": "text", "text": "привет".encode()})
        assert parsed.text == "привет"

    def test_text_uses_data_fallback(self):
        assert rp._parse_content_item({"type": "text", "data": "d"}).text == "d"

    def test_image_defaults(self):
        parsed = rp._parse_content_item({"type": "image", "blob": "XYZ"})
        assert parsed.text == "[image]"
        assert parsed.images[0] == {"type": "image", "mimeType": "image/png", "data": "XYZ", "uri": None}

    def test_type_is_case_insensitive(self):
        assert rp._parse_content_item({"type": "TEXT", "text": "x"}).text == "x"

    def test_unknown_type_with_text(self):
        assert rp._parse_content_item({"type": "weird", "text": "t"}).text == "t"

    def test_unknown_type_without_text(self):
        item = {"type": "weird", "x": 1}
        assert rp._parse_content_item(item).text == str(item)

    def test_resource_nested_uri(self):
        parsed = rp._parse_content_item({"type": "resource", "resource": {"uri": "u://1"}})
        assert parsed.resources[0]["uri"] == "u://1"
        assert parsed.text == "u://1"

    def test_resource_without_anything(self):
        parsed = rp._parse_content_item({"type": "resource"})
        assert parsed.text == "[resource]"

    def test_resource_flat_uri(self):
        parsed = rp._parse_content_item({"type": "resource", "uri": "u://flat"})
        assert parsed.resources[0]["uri"] == "u://flat"
        assert parsed.text == "u://flat"

    def test_flat_uri_wins_over_nested(self):
        parsed = rp._parse_content_item(
            {"type": "resource", "uri": "u://flat", "resource": {"uri": "u://nested"}}
        )
        assert parsed.resources[0]["uri"] == "u://flat"

    def test_resource_not_a_dict_is_ignored(self):
        parsed = rp._parse_content_item({"type": "resource", "resource": "строка"})
        assert parsed.resources[0]["uri"] is None


class TestCompactToolResult:
    def test_empty(self):
        assert rp.compact_tool_result_for_llm("") == ""
        assert rp.compact_tool_result_for_llm(None) == ""

    def test_plain_text_truncated(self):
        out = rp.compact_tool_result_for_llm("x" * 100, max_chars=10)
        assert len(out) == 10
        assert out.endswith("…")

    def test_short_text_untouched(self):
        assert rp.compact_tool_result_for_llm("  short  ") == "short"

    def test_web_search_payload_compacted(self):
        data = {
            "query": "q",
            "engines": ["e"],
            "totalResults": 99,
            "results": [
                {"title": f"t{i}", "url": f"http://x/{i}", "description": f"<b>d{i}</b>", "source": "s", "junk": "J"}
                for i in range(9)
            ],
            "partialFailures": ["engine2"],
            "readableHtml": "<html>huge</html>",
        }
        out = json.loads(rp.compact_tool_result_for_llm(json.dumps(data)))
        assert len(out["results"]) == 5
        assert out["results"][0]["description"] == "d0"
        assert "junk" not in out["results"][0]
        assert "readableHtml" not in out
        assert out["partialFailures"] == ["engine2"]

    def test_fetch_payload_compacted(self):
        data = {"url": "http://x", "title": "T", "content": "<p>" + "a" * 5000 + "</p>", "readableHtml": "<h1/>"}
        out = json.loads(rp.compact_tool_result_for_llm(json.dumps(data), max_chars=100000))
        assert set(out) == {"url", "title", "content", "excerpt"}
        assert len(out["content"]) <= 3500

    def test_generic_dict_drops_heavy_keys(self):
        data = {"keep": 1, "html": "<x>", "links": [1], "readableHtml": "y"}
        assert json.loads(rp.compact_tool_result_for_llm(json.dumps(data))) == {"keep": 1}

    def test_json_list(self):
        assert rp.compact_tool_result_for_llm("[1, 2, 3]") == "[1, 2, 3]"

    def test_json_scalar(self):
        assert rp.compact_tool_result_for_llm("123") == "123"

    def test_non_ascii_not_escaped(self):
        assert "привет" in rp.compact_tool_result_for_llm(json.dumps({"a": "привет"}, ensure_ascii=True))

    def test_result_never_exceeds_limit(self):
        big = json.dumps({"a": "я" * 10000})
        assert len(rp.compact_tool_result_for_llm(big, max_chars=200)) <= 200


class TestFormatParsedForLlm:
    def test_text_only(self):
        assert rp.format_parsed_for_llm(ParsedMcpResult(text="hi")) == "hi"

    def test_attachments_described(self):
        parsed = ParsedMcpResult(
            text="t",
            images=[{"mimeType": "image/png"}],
            audio=[{}],
            resources=[{"uri": "u", "text": "body"}, {"uri": "v"}, {}],
        )
        out = rp.format_parsed_for_llm(parsed)
        assert "[MCP image attachment: image/png]" in out
        assert "[MCP audio attachment: audio]" in out
        assert "[MCP resource u]\nbody" in out
        assert "[MCP resource: v]" in out
        assert "[MCP resource: resource]" in out

    def test_resource_text_limited(self):
        parsed = ParsedMcpResult(text="", resources=[{"uri": "u", "text": "z" * 100}])
        out = rp.format_parsed_for_llm(parsed, max_resource_chars=10)
        assert out == "[MCP resource u]\n" + "z" * 10

    def test_empty(self):
        assert rp.format_parsed_for_llm(ParsedMcpResult(text="")) == ""


class TestPreviewParsedForUi:
    def test_text_preview_truncated(self):
        out = rp.preview_parsed_for_ui(ParsedMcpResult(text="a" * 500), max_len=20)
        assert len(out) == 20 and out.endswith("…")

    def test_short_text(self):
        assert rp.preview_parsed_for_ui(ParsedMcpResult(text=" hi ")) == "hi"

    def test_image_placeholder_replaced(self):
        parsed = ParsedMcpResult(text="[image]", images=[{"mimeType": "image/gif"}])
        assert rp.preview_parsed_for_ui(parsed) == "[Изображение: image/gif]"

    def test_audio_placeholder_replaced(self):
        parsed = ParsedMcpResult(text="[audio]", audio=[{"mimeType": "audio/mp3"}])
        assert rp.preview_parsed_for_ui(parsed) == "[Аудио: audio/mp3]"

    def test_resource_without_text(self):
        parsed = ParsedMcpResult(text="", resources=[{"uri": "file:///x"}])
        assert rp.preview_parsed_for_ui(parsed) == "[Resource: file:///x]"

    def test_resource_with_text(self):
        parsed = ParsedMcpResult(text="", resources=[{"uri": "u", "text": "  body  "}])
        assert rp.preview_parsed_for_ui(parsed) == "body"

    def test_empty(self):
        assert rp.preview_parsed_for_ui(ParsedMcpResult(text="")) == ""


class TestParsedToApiDict:
    def test_binary_omitted_by_default(self):
        parsed = ParsedMcpResult(
            text="t",
            images=[{"data": "BASE64", "mimeType": "image/png"}, {"data": None}],
            audio=[{"data": "WAV"}],
        )
        out = rp.parsed_to_api_dict(parsed)
        assert out["images"][0]["data"] == "[binary omitted]"
        assert out["images"][1]["data"] is None
        assert out["audio"][0]["data"] == "[binary omitted]"
        assert out["has_attachments"] is True

    def test_include_binary(self):
        parsed = ParsedMcpResult(text="", images=[{"data": "BASE64"}])
        assert rp.parsed_to_api_dict(parsed, include_binary=True)["images"][0]["data"] == "BASE64"

    def test_original_not_mutated(self):
        img = {"data": "BASE64"}
        rp.parsed_to_api_dict(ParsedMcpResult(text="", images=[img]))
        assert img["data"] == "BASE64"

    def test_no_attachments(self):
        assert rp.parsed_to_api_dict(ParsedMcpResult(text="x"))["has_attachments"] is False
