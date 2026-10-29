"""mcp/text_tool_calls и mcp/result_parser: разбор tool calls из текста и результатов MCP-инструментов."""

import json

import pytest

from backend.mcp import result_parser as rp
from backend.mcp.text_tool_calls import content_has_text_tool_calls, extract_tool_calls_from_content
from backend.mcp.types import ParsedMcpResult

pytestmark = pytest.mark.unit


class TestTextToolCalls:
    @pytest.mark.parametrize(("text", "expected"), [("", False), (None, False), ("просто текст", False), ("<tool_call>x", True), ("<TOOL_CALL>", True), ("a <function=f> b", True)])
    def test_detection(self, text, expected):
        assert content_has_text_tool_calls(text) is expected

    def test_json_tool_call(self):
        text = 'Сейчас поищу.\n<tool_call>\n{"name": "search", "arguments": {"q": "курс"}}\n</tool_call>'

        cleaned, calls = extract_tool_calls_from_content(text)

        assert cleaned == "Сейчас поищу."
        assert [(c.name, c.arguments) for c in calls] == [("search", {"q": "курс"})] and calls[0].id.startswith("call_")

    def test_json_variants(self):
        text = '<tool_call>{"function": "f1", "parameters": {"a": 1}}</tool_call><tool_call>{"name": "f2"}</tool_call>'

        _, calls = extract_tool_calls_from_content(text)

        assert [(c.name, c.arguments) for c in calls] == [("f1", {"a": 1}), ("f2", {})]

    def test_non_dict_arguments_become_empty(self):
        _, calls = extract_tool_calls_from_content('<tool_call>{"name": "f", "arguments": "oops"}</tool_call>')

        assert calls[0].arguments == {}

    def test_hermes_function_tags_inside_a_block(self):
        text = '<tool_call><function=lookup>{"id": 7}</function></tool_call>'

        cleaned, calls = extract_tool_calls_from_content(text)

        assert cleaned == "" and [(c.name, c.arguments) for c in calls] == [("lookup", {"id": 7})]

    def test_self_closing_function_tag(self):
        _, calls = extract_tool_calls_from_content("<tool_call><function=ping/></tool_call>")

        assert [(c.name, c.arguments) for c in calls] == [("ping", {})]

    def test_function_tags_without_a_wrapper(self):
        cleaned, calls = extract_tool_calls_from_content('Ответ <function=lookup>{"id": 1}</function> конец')

        assert [c.name for c in calls] == ["lookup"] and "<function" not in cleaned and cleaned.startswith("Ответ")

    def test_duplicates_are_collapsed_by_name(self):
        text = '<tool_call>{"name": "f", "arguments": {"a": 1}}</tool_call><tool_call>{"name": "f", "arguments": {"a": 2}}</tool_call>'

        _, calls = extract_tool_calls_from_content(text)

        assert [c.arguments for c in calls] == [{"a": 1}]  # второй вызов той же функции теряется

    @pytest.mark.parametrize("block", ["{broken json", "[1, 2]", '{"arguments": {}}', "", "plain words"])
    def test_garbage_blocks_give_no_calls(self, block):
        _, calls = extract_tool_calls_from_content(f"<tool_call>{block}</tool_call>")

        assert calls == []

    def test_broken_function_arguments_become_empty(self):
        _, calls = extract_tool_calls_from_content("<function=f>{not json}</function>")

        assert [(c.name, c.arguments) for c in calls] == [("f", {})]

    def test_text_without_calls_is_returned_unchanged(self):
        assert extract_tool_calls_from_content("обычный ответ") == ("обычный ответ", [])
        assert extract_tool_calls_from_content("") == ("", [])

    def test_unique_ids(self):
        _, calls = extract_tool_calls_from_content('<tool_call>{"name":"a"}</tool_call><tool_call>{"name":"b"}</tool_call>')

        assert len({c.id for c in calls}) == 2


def parsed(text="", **kw):
    return ParsedMcpResult(text=text, **kw)


class TestParseStruct:
    def test_plain_values(self):
        assert rp.parse_mcp_result_to_struct(None).text == ""
        assert rp.parse_mcp_result_to_struct("текст").text == "текст"
        assert rp.parse_mcp_result_to_struct(42).text == "42"

    def test_text_blocks_are_joined(self):
        result = rp.parse_mcp_result_to_struct([{"type": "text", "text": "a"}, {"type": "text", "text": ""}, "строка", {"type": "text", "text": "b"}])

        assert result.text == "a\nстрока\nb"

    def test_content_wrappers_are_unwrapped(self):
        assert rp.parse_mcp_result_to_struct({"content": [{"type": "text", "text": "x"}]}).text == "x"
        assert rp.parse_mcp_result_to_struct({"contents": [{"type": "text", "text": "y"}]}).text == "y"
        assert rp.parse_mcp_result_to_struct({"text": "z"}).text == "z"
        assert rp.parse_mcp_result_to_struct({"unknown": 1}).text == "{'unknown': 1}"

    def test_image_and_audio(self):
        result = rp.parse_mcp_result_to_struct([{"type": "image", "data": "AAA", "mimeType": "image/jpeg"}, {"type": "audio", "blob": "BBB"}])

        assert result.images[0]["mimeType"] == "image/jpeg" and result.images[0]["data"] == "AAA"
        assert result.audio[0]["mimeType"] == "audio/wav" and result.audio[0]["data"] == "BBB"
        assert result.text == "[image]\n[audio]"

    def test_nested_resource(self):
        result = rp.parse_mcp_result_to_struct([{"type": "resource", "resource": {"uri": "file:///a.txt", "text": "тело"}}])

        assert result.resources[0]["uri"] == "file:///a.txt" and result.resources[0]["text"] == "тело" and result.text == "тело"

    @pytest.mark.xfail(strict=True, reason="BUG: приоритет `or`/`if` — uri верхнего уровня теряется, если нет вложенного resource")
    def test_resource_with_a_top_level_uri(self):
        result = rp.parse_mcp_result_to_struct([{"type": "resource", "uri": "file:///a.txt", "text": "тело"}])

        assert result.resources[0]["uri"] == "file:///a.txt"

    def test_bytes_text_is_decoded(self):
        assert rp.parse_mcp_result_to_struct([{"type": "text", "text": "привет".encode()}]).text == "привет"

    def test_untyped_items(self):
        assert rp.parse_mcp_result_to_struct([{"text": "без типа"}]).text == "без типа"
        assert rp.parse_mcp_result_to_struct([{"a": 1}]).text == "{'a': 1}"

    def test_parse_to_string(self):
        assert rp.parse_mcp_result([{"type": "text", "text": "a"}, {"type": "image", "data": "x"}]) == "a\n[image]\n[MCP image attachment: image/png]"


class TestFormatting:
    def test_llm_format_describes_attachments(self):
        result = parsed("текст", images=[{"mimeType": "image/gif"}], audio=[{}], resources=[{"uri": "u1", "text": "тело" * 2000}, {"uri": "u2"}])

        text = rp.format_parsed_for_llm(result, max_resource_chars=10)

        assert "[MCP image attachment: image/gif]" in text and "[MCP audio attachment: audio]" in text
        assert "[MCP resource u1]\n" + ("тело" * 2000)[:10] in text and "[MCP resource: u2]" in text

    def test_preview_prefers_text_and_truncates(self):
        assert rp.preview_parsed_for_ui(parsed("коротко")) == "коротко"
        long = rp.preview_parsed_for_ui(parsed("x" * 500), max_len=10)
        assert long == "x" * 9 + "…"

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            (parsed("[image]", images=[{"mimeType": "image/png"}]), "[Изображение: image/png]"),
            (parsed("[audio]", audio=[{}]), "[Аудио: audio]"),
            (parsed("", resources=[{"uri": "u"}]), "[Resource: u]"),
            (parsed("", resources=[{"uri": "u", "text": "  снимок  "}]), "снимок"),
            (parsed(""), ""),
        ],
    )
    def test_preview_placeholders(self, result, expected):
        assert rp.preview_parsed_for_ui(result) == expected

    def test_api_dict_hides_binary_data(self):
        result = parsed("t", images=[{"data": "AAAA", "mimeType": "image/png"}], audio=[{"data": ""}])

        hidden = rp.parsed_to_api_dict(result)
        assert hidden["images"][0]["data"] == "[binary omitted]" and hidden["audio"][0]["data"] is None and hidden["has_attachments"] is True
        assert rp.parsed_to_api_dict(result, include_binary=True)["images"][0]["data"] == "AAAA"
        assert result.images[0]["data"] == "AAAA"  # исходный объект не меняется
        assert rp.parsed_to_api_dict(parsed("t"))["has_attachments"] is False

    def test_ui_format_prefers_raw_json(self):
        assert json.loads(rp.format_tool_result_for_ui(parsed("x"), raw={"a": "б"})) == {"a": "б"}
        assert rp.format_tool_result_for_ui(parsed("x", raw={"k": 1})) == json.dumps({"k": 1}, indent=2)

    def test_ui_format_pretty_prints_json_text(self):
        assert rp.format_tool_result_for_ui(parsed('{"a":1}')) == '{\n  "a": 1\n}'
        assert rp.format_tool_result_for_ui(parsed("{broken")) == "{broken"
        assert rp.format_tool_result_for_ui(parsed("")) == ""

    def test_unserializable_raw_falls_back_to_default_str(self):
        assert "obj" in rp.format_tool_result_for_ui(parsed("x"), raw={"o": object})


class TestDownloadLinks:
    def test_urls_in_text(self):
        links = rp.extract_download_links(parsed("Готово: https://files.corp/deck.pptx, и ещё (https://x.org/a.pdf)."))

        assert [l["url"] for l in links] == ["https://files.corp/deck.pptx", "https://x.org/a.pdf"]
        assert links[0]["label"] == "deck.pptx,"  # known issue: в подпись попадает хвостовая запятая (в url она срезана)

    def test_json_link_keys_and_label(self):
        links = rp.extract_download_links(parsed(json.dumps({"filename": "Отчёт.pptx", "download_url": "https://s/a?sig=1", "url": "https://s/b"})))

        assert {l["url"] for l in links} >= {"https://s/a?sig=1", "https://s/b"} and links[0]["label"] == "Отчёт.pptx"

    def test_links_from_resources_and_dedup(self):
        result = parsed("https://a.org/f.pdf", resources=[{"uri": "https://a.org/f.pdf", "text": "см. https://b.org/g.docx"}])

        assert [l["url"] for l in rp.extract_download_links(result)] == ["https://a.org/f.pdf", "https://b.org/g.docx"]

    def test_no_links(self):
        assert rp.extract_download_links(parsed("нет ссылок")) == []

    def test_javascript_style_urls_are_not_extracted(self):
        assert rp.extract_download_links(parsed("javascript:alert(1) data:text/html,x")) == []

    def test_webpage_search_urls_are_not_downloads(self):
        """websearch отдаёт обычные страницы — это не файлы для «Скачать»."""
        text = json.dumps(
            {
                "query": "погода в москве",
                "results": [
                    {
                        "title": "Погода в Москве",
                        "url": "https://yandex.ru/pogoda/ru/moscow",
                        "description": "сейчас +5",
                    },
                    {"title": "Gismeteo", "url": "https://www.gismeteo.ru/weather-moscow-4368/"},
                ],
            },
            ensure_ascii=False,
        )
        assert rp.extract_download_links(parsed(text)) == []
        assert rp.enrich_download_links(parsed(text), "websearch") == []

    def test_saved_file_gets_a_proxy_link(self):
        links = rp.enrich_download_links(parsed("Файл сохранён: deck_v2.pptx"), "office")

        assert links[0]["url"].startswith("/api/mcp/servers/office/download?filename=") and "mime" in links[0]
        assert links[0]["mime"].endswith("presentationml.presentation")

    def test_proxy_link_carries_the_storage_key(self):
        text = json.dumps({"message": "saved as report.pdf", "s3_key": "/bucket/dir/report.pdf"})

        link = rp.enrich_download_links(parsed(text), "office")[0]

        assert link["storage_key"] == "bucket/dir/report.pdf" and "storage_key=bucket/dir/report.pdf" in link["url"]
        assert link["mime"] == "application/octet-stream"

    def test_server_id_is_url_encoded(self):
        link = rp.enrich_download_links(parsed("saved as a.pdf"), "evil/../id")[0]

        assert link["url"].startswith("/api/mcp/servers/evil%2F..%2Fid/download")

    def test_real_links_win_over_proxy(self):
        links = rp.enrich_download_links(parsed("https://x.org/a.pdf и saved as b.pdf"), "s")

        assert [l["url"] for l in links] == ["https://x.org/a.pdf"]

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"s3_key": "dir/a.pptx"}, "dir/a.pptx"),
            ({"objectKey": "s3://bucket/path/a.pptx"}, "path/a.pptx"),
            ({"output_path": "C:\\out\\a.pptx"}, "C:/out/a.pptx"),
            ({"path": "https://x/a.pptx"}, ""),
            ({"path": "a.pptx"}, "a.pptx"),
            ({"path": "readme"}, ""),
        ],
    )
    def test_storage_key_extraction(self, payload, expected):
        assert rp._extract_storage_key(parsed(json.dumps(payload))) == expected

    def test_storage_key_from_s3_uri_in_text_and_raw(self):
        assert rp._extract_storage_key(parsed("загружено в s3://bkt/folder/file.pdf")) == "folder/file.pdf"
        assert rp._extract_storage_key(parsed("", raw=[{"minio_key": "k/a.pdf"}, "x"])) == "k/a.pdf"

    def test_links_are_appended_as_markdown(self):
        content = rp.append_download_links_to_content("Готово", [{"label": "Deck", "url": "https://x/d.pptx"}])

        assert content == "Готово\n**Файлы:**\n- [Deck](https://x/d.pptx)"

    @pytest.mark.xfail(strict=True, reason="BUG: пустой url в списке даёт `'' in body` → ссылки не дописываются вообще")
    def test_an_empty_url_does_not_suppress_the_other_links(self):
        content = rp.append_download_links_to_content("Готово", [{"label": "Deck", "url": "https://x/d.pptx"}, {"label": "", "url": ""}])

        assert "https://x/d.pptx" in content

    def test_append_is_idempotent(self):
        body = "уже есть https://x/d.pptx"

        assert rp.append_download_links_to_content(body, [{"label": "Deck", "url": "https://x/d.pptx"}]) == body
        assert rp.append_download_links_to_content("a", []) == "a"
