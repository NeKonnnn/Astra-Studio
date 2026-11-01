"""mcp/result_parser: ссылки на файлы, S3-ключи, превью и разбор содержимого MCP-ответов."""

import pytest

from backend.mcp import result_parser as rp
from backend.mcp.types import ParsedMcpResult

pytestmark = pytest.mark.unit


def parsed(text="", **kw):
    return ParsedMcpResult(text=text, **kw)


class TestFormatForLlm:
    def test_text_and_attachments(self):
        p = parsed("ответ", images=[{"mimeType": "image/jpeg"}, {}], audio=[{}], resources=[{"uri": "u1", "text": "тело" * 3}, {"uri": "u2"}, {}])
        out = rp.format_parsed_for_llm(p)
        assert out.splitlines()[:4] == ["ответ", "[MCP image attachment: image/jpeg]", "[MCP image attachment: image]", "[MCP audio attachment: audio]"]
        assert "[MCP resource u1]\nтелотелотело" in out and "[MCP resource: u2]" in out and out.endswith("[MCP resource: resource]")

    def test_resource_text_is_truncated(self):
        out = rp.format_parsed_for_llm(parsed(resources=[{"uri": "u", "text": "x" * 50}]), max_resource_chars=10)
        assert out == "[MCP resource u]\n" + "x" * 10


class TestPreview:
    def test_long_text_is_cut_with_ellipsis(self):
        out = rp.preview_parsed_for_ui(parsed("a" * 400), max_len=10)
        assert out == "a" * 9 + "…"

    def test_placeholder_text_falls_through_to_attachments(self):
        assert rp.preview_parsed_for_ui(parsed("[image]", images=[{"mimeType": "image/png"}])) == "[Изображение: image/png]"
        assert rp.preview_parsed_for_ui(parsed("[image]", images=[{}])) == "[Изображение: image]"
        assert rp.preview_parsed_for_ui(parsed("[audio]", audio=[{"mimeType": "audio/mp3"}])) == "[Аудио: audio/mp3]"
        assert rp.preview_parsed_for_ui(parsed("[audio]", audio=[{}])) == "[Аудио: audio]"

    def test_resource_previews(self):
        assert rp.preview_parsed_for_ui(parsed("", resources=[{"uri": "u", "text": "  тело  "}])) == "тело"
        assert rp.preview_parsed_for_ui(parsed("", resources=[{"uri": "u", "text": "z" * 30}]), max_len=5) == "zzzz…"
        assert rp.preview_parsed_for_ui(parsed("", resources=[{"uri": "file://x"}])) == "[Resource: file://x]"
        assert rp.preview_parsed_for_ui(parsed("", resources=[{}])) == "[Resource: resource]"

    def test_placeholder_without_attachments_is_returned_as_is(self):
        assert rp.preview_parsed_for_ui(parsed("[image]")) == "[image]"
        assert rp.preview_parsed_for_ui(parsed("")) == ""


class TestExtractDownloadLinks:
    def test_json_fields_and_urls(self):
        text = '{"filename": "deck.pptx", "download_url": "https://h/files/deck.pptx", "url": "  ", "path": "out/deck.pptx", "file_path": "no_ext"}'
        links = rp.extract_download_links(parsed(text))
        urls = [x["url"] for x in links]
        assert urls == ["https://h/files/deck.pptx", "out/deck.pptx"]
        assert links[0]["label"] == "deck.pptx" and links[0]["mime"] == ""

    def test_label_has_no_trailing_punctuation(self):
        links = rp.extract_download_links(parsed("Скачать: https://h/a.pdf)."))
        assert links[0]["label"] == "a.pdf"

    def test_path_label_falls_back_to_default(self):
        links = rp.extract_download_links(parsed('{"file_path": "dir/sub/file.xlsx"}'))
        assert links == [{"url": "dir/sub/file.xlsx", "label": "Скачать файл", "mime": ""}]

    def test_plain_urls_trailing_punctuation_and_dedup(self):
        links = rp.extract_download_links(parsed("Скачать: https://h/a.pdf). Ещё https://h/a.pdf, и https://h/dir/"))
        assert [x["url"] for x in links] == ["https://h/a.pdf", "https://h/dir/"]
        assert links[1]["label"] == "Скачать файл"

    def test_invalid_json_still_scans_urls(self):
        assert rp.extract_download_links(parsed('{"a": https://h/x.pdf')) == [{"url": "https://h/x.pdf", "label": "x.pdf", "mime": ""}]

    def test_resources_are_scanned(self):
        p = parsed("", resources=[{"uri": "https://h/r.docx", "text": "см. https://h/t.pdf"}, {"uri": 5, "text": None}])
        assert [x["url"] for x in rp.extract_download_links(p)] == ["https://h/r.docx", "https://h/t.pdf"]

    def test_empty(self):
        assert rp.extract_download_links(parsed("")) == []


class TestSavedFilenames:
    def test_patterns_and_resource_text(self):
        p = parsed("Файл сохранён: 'отчёт.pptx'. Saved as deck.pdf; смотри C:\\tmp\\win.docx и /var/x/nix.xlsx", resources=[{"text": "file saved: res.pptx"}, {"text": None}])
        names = rp._extract_saved_filenames(p)
        assert names == ["отчёт.pptx", "deck.pdf", "win.docx", "nix.xlsx", "res.pptx"]

    def test_bare_extension_after_directory_is_not_a_file_name(self):
        assert rp._extract_saved_filenames(parsed("лежит в dir/.pptx")) == []

    def test_urls_and_duplicates_are_ignored(self):
        names = rp._extract_saved_filenames(parsed("https://h/a.pdf saved a.pdf; saved a.pdf"))
        assert names == ["a.pdf"]


class TestStorageKey:
    def key(self, text="", raw=None, resources=()):
        return rp._extract_storage_key(parsed(text, raw=raw, resources=list(resources)))

    def test_from_json_text(self):
        assert self.key('{"s3_key": "/pres/a.pptx"}') == "pres/a.pptx"

    def test_s3_uri_in_key(self):
        assert self.key(raw={"objectKey": "s3://bucket/dir/a.pptx"}) == "dir/a.pptx"

    def test_from_raw_dict_and_list(self):
        assert self.key(raw={"minio_key": "k/a.pptx"}) == "k/a.pptx"
        assert self.key(raw=[{"x": 1}, "junk", {"storage_key": "k2"}]) == "k2"

    def test_from_resource_text(self):
        assert self.key(resources=[{"text": '{"minioObject": "m/a.pdf"}'}, {"text": 5}]) == "m/a.pdf"

    def test_broken_json_is_skipped(self):
        assert self.key("{broken") == ""

    def test_json_list_text_is_ignored(self):
        assert self.key('{"x": 1}') == ""

    def test_path_fields(self):
        assert self.key('{"path": "dir\\\\sub\\\\a.pptx"}') == "dir/sub/a.pptx"
        assert self.key('{"file_path": "a.pdf"}') == "a.pdf"
        assert self.key('{"output_path": "s3://b/out/a.pptx"}') == "out/a.pptx"
        assert self.key('{"s3_path": "x/y"}') == "x/y"

    def test_path_fields_that_are_not_keys(self):
        assert self.key('{"path": "https://h/a.pptx"}') == ""
        assert self.key('{"path": "plain"}') == ""
        assert self.key('{"path": "  "}') == ""

    def test_s3_uri_without_object_part_is_returned_as_is(self):
        assert self.key('{"s3_key": "s3://bucket"}') == "s3://bucket"
        assert self.key('{"path": "s3://bucket"}') == "s3://bucket"

    def test_s3_uri_in_plain_text(self):
        assert self.key("загружено в s3://bucket/dir/a.pptx готово") == "dir/a.pptx"

    def test_nothing(self):
        assert self.key("просто текст") == ""


class TestEnrichDownloadLinks:
    def test_real_links_win(self):
        assert rp.enrich_download_links(parsed("https://h/a.pdf"), "srv")[0]["url"] == "https://h/a.pdf"

    def test_proxy_link_with_storage_key(self):
        p = parsed("Файл сохранён: deck.pptx", raw={"s3_key": "out/deck.pptx"})
        (link,) = rp.enrich_download_links(p, "ppt srv")
        assert link["url"] == "/api/mcp/servers/ppt%20srv/download?filename=deck.pptx&storage_key=out/deck.pptx"
        assert link["storage_key"] == "out/deck.pptx" and link["mime"].endswith("presentationml.presentation")

    def test_proxy_link_for_other_types_without_key(self):
        (link,) = rp.enrich_download_links(parsed("saved as t.xlsx"), "srv")
        assert link["url"] == "/api/mcp/servers/srv/download?filename=t.xlsx" and link["mime"] == "application/octet-stream"
        assert "storage_key" not in link

    def test_no_files(self):
        assert rp.enrich_download_links(parsed("ничего"), "srv") == []


class TestFormatForUi:
    def test_raw_is_pretty_printed(self):
        assert rp.format_tool_result_for_ui(parsed("x"), {"a": "я"}) == '{\n  "a": "я"\n}'

    def test_parsed_raw_is_used(self):
        assert rp.format_tool_result_for_ui(parsed("x", raw=[1])) == "[\n  1\n]"

    def test_unserializable_raw_falls_back_to_text(self):
        assert rp.format_tool_result_for_ui(parsed("текст"), {(1, 2): 3}) == "текст"

    def test_json_text_is_reformatted(self):
        assert rp.format_tool_result_for_ui(parsed('{"a":1}')) == '{\n  "a": 1\n}'

    def test_invalid_json_text_is_kept(self):
        assert rp.format_tool_result_for_ui(parsed("{oops")) == "{oops"
        assert rp.format_tool_result_for_ui(parsed("")) == ""


class TestAppendLinks:
    def test_no_links(self):
        assert rp.append_download_links_to_content("текст", []) == "текст"

    def test_block_is_added(self):
        out = rp.append_download_links_to_content(" текст ", [{"url": "u1", "label": ""}, {"url": "u2", "label": "Файл"}])
        assert out == "текст\n**Файлы:**\n- [Скачать](u1)\n- [Файл](u2)"

    def test_link_with_empty_url_does_not_suppress_the_block(self):
        links = [{"url": "", "label": "x"}, {"url": "u2", "label": "Файл"}]
        assert rp.append_download_links_to_content("текст", links) == "текст\n**Файлы:**\n- [Файл](u2)"

    def test_empty_content(self):
        assert rp.append_download_links_to_content(None, [{"url": "u", "label": "L"}]) == "**Файлы:**\n- [L](u)"

    def test_already_mentioned_link_is_not_duplicated(self):
        assert rp.append_download_links_to_content("тут u1", [{"url": "u1", "label": "L"}]) == "тут u1"


class TestParseResult:
    def test_scalars(self):
        assert rp.parse_mcp_result(None) == ""
        assert rp.parse_mcp_result("текст") == "текст"
        assert rp.parse_mcp_result(42) == "42"

    def test_dict_variants(self):
        assert rp.parse_mcp_result_to_struct({"contents": [{"type": "text", "text": "a"}]}).text == "a"
        assert rp.parse_mcp_result_to_struct({"content": "b"}).text == "b"
        assert rp.parse_mcp_result_to_struct({"text": "c"}).text == "c"
        assert rp.parse_mcp_result_to_struct({"type": "image", "data": "AAA"}).images[0]["data"] == "AAA"
        res = rp.parse_mcp_result_to_struct({"foo": 1})
        assert res.text == "{'foo': 1}" and res.raw == {"foo": 1}

    def test_list_mixes_item_kinds(self):
        res = rp.parse_mcp_result_to_struct([
            {"type": "text", "text": "t"}, "голая строка", {"type": "audio", "blob": "b"},
            {"type": "image", "mime_type": "image/gif", "data": "d", "uri": "u"},
        ])
        assert res.text == "t\nголая строка\n[audio]\n[image]"
        assert res.images[0]["mimeType"] == "image/gif" and res.audio[0]["data"] == "b" and res.audio[0]["mimeType"] == "audio/wav"

    def test_text_item_with_bytes_and_data_key(self):
        assert rp._parse_content_item({"type": "text", "text": b"\xd0\xb0"}).text == "а"
        assert rp._parse_content_item({"type": "text", "data": "d"}).text == "d"
        assert rp._parse_content_item({"type": "text"}).text == ""

    def test_untyped_items(self):
        assert rp._parse_content_item({"text": "plain"}).text == "plain"
        assert rp._parse_content_item({"x": 1}).text == "{'x': 1}"

    def test_resource_with_nested_resource(self):
        item = {"type": "resource", "resource": {"uri": "file://a", "text": "тело"}, "mimeType": "text/plain"}
        res = rp._parse_content_item(item)
        assert res.text == "тело" and res.resources[0]["uri"] == "file://a" and res.resources[0]["mimeType"] == "text/plain"

    def test_resource_label_falls_back_to_uri(self):
        res = rp._parse_content_item({"type": "resource", "resource": {"uri": "file://a"}})
        assert res.text == "file://a"

    def test_resource_without_anything(self):
        assert rp._parse_content_item({"type": "resource"}).text == "[resource]"

    def test_flat_resource_keeps_its_uri(self):
        res = rp._parse_content_item({"type": "resource", "uri": "file://flat", "text": "t"})
        assert res.resources[0]["uri"] == "file://flat"

    def test_nested_resource_uri_is_used_without_top_level_one(self):
        res = rp._parse_content_item({"type": "resource", "resource": {"uri": "file://n", "text": "t"}})
        assert res.resources[0]["uri"] == "file://n"


class TestApiDict:
    def test_binary_is_omitted_by_default(self):
        p = parsed("t", images=[{"data": "AAA", "mimeType": "i"}, {"data": None}], audio=[{"data": "B"}], resources=[{"uri": "u"}])
        out = rp.parsed_to_api_dict(p)
        assert out["images"][0]["data"] == "[binary omitted]" and out["images"][1]["data"] is None
        assert out["audio"][0]["data"] == "[binary omitted]" and out["has_attachments"] is True
        assert p.images[0]["data"] == "AAA"  # исходный объект не изменён

    def test_binary_can_be_included(self):
        out = rp.parsed_to_api_dict(parsed("t", images=[{"data": "AAA"}]), include_binary=True)
        assert out["images"][0]["data"] == "AAA"

    def test_plain_text_has_no_attachments(self):
        assert rp.parsed_to_api_dict(parsed("t"))["has_attachments"] is False
