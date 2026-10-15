# -*- coding: utf-8 -*-
"""Тесты ссылок на файлы в backend.mcp.result_parser: поиск, S3-ключи, proxy-URL, вывод в UI."""

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

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def parsed(text="", resources=None, raw=None):
    return ParsedMcpResult(text=text, resources=resources or [], raw=raw)


class TestExtractDownloadLinks:
    def test_no_links(self):
        assert rp.extract_download_links(parsed("просто текст")) == []
        assert rp.extract_download_links(parsed("")) == []

    def test_url_in_plain_text(self):
        links = rp.extract_download_links(parsed("Готово: https://files.test/a/report.pdf"))
        assert links == [{"url": "https://files.test/a/report.pdf", "label": "report.pdf", "mime": ""}]

    def test_trailing_punctuation_is_stripped(self):
        [link] = rp.extract_download_links(parsed("см. (https://x.test/f.pdf)."))
        assert link["url"] == "https://x.test/f.pdf"

    def test_duplicates_collapsed(self):
        text = "https://x.test/f.pdf и снова https://x.test/f.pdf"
        assert len(rp.extract_download_links(parsed(text))) == 1

    def test_json_url_key_with_name_label(self):
        text = json.dumps({"download_url": "https://x.test/d/1", "filename": "deck.pptx"})
        [link] = rp.extract_download_links(parsed(text))
        assert link["url"] == "https://x.test/d/1"
        assert link["label"] == "deck.pptx"

    def test_json_label_priority_follows_name_keys(self):
        text = json.dumps({"url": "https://x.test/1", "name": "N", "filename": "F"})
        assert rp.extract_download_links(parsed(text))[0]["label"] == "F"

    def test_json_default_label(self):
        [link] = rp.extract_download_links(parsed(json.dumps({"url": "https://x.test/1"})))
        assert link["label"] == "Скачать файл"

    def test_json_file_path_is_a_link_only_for_known_extensions(self):
        with_ext = rp.extract_download_links(parsed(json.dumps({"path": "out/deck.pptx"})))
        without = rp.extract_download_links(parsed(json.dumps({"path": "out/readme.txt"})))
        assert [l["url"] for l in with_ext] == ["out/deck.pptx"]
        assert without == []

    def test_invalid_json_still_scanned_as_text(self):
        links = rp.extract_download_links(parsed('{broken "url": "https://x.test/a.pdf"'))
        assert [l["url"] for l in links] == ["https://x.test/a.pdf"]

    def test_resources_are_scanned(self):
        res = [{"uri": "https://x.test/r.pdf"}, {"text": "см. https://x.test/body.docx"}, {"uri": 5}]
        urls = [l["url"] for l in rp.extract_download_links(parsed("", resources=res))]
        assert urls == ["https://x.test/r.pdf", "https://x.test/body.docx"]

    def test_non_string_json_values_ignored(self):
        assert rp.extract_download_links(parsed(json.dumps({"url": 5, "href": None, "link": ""}))) == []


class TestExtractSavedFilenames:
    def test_saved_phrases(self):
        names = rp._extract_saved_filenames(parsed("Файл сохранён: report.pptx"))
        assert names == ["report.pptx"]

    @pytest.mark.parametrize(
        "text",
        ["saved as deck.pdf", "File saved: deck.pdf", "downloaded as 'deck.pdf'"],
    )
    def test_english_phrases(self, text):
        assert rp._extract_saved_filenames(parsed(text)) == ["deck.pdf"]

    def test_local_path_reduced_to_basename(self):
        assert rp._extract_saved_filenames(parsed("записано в /tmp/out/final.xlsx")) == ["final.xlsx"]
        assert rp._extract_saved_filenames(parsed("записано в C:\\out\\final.xlsx")) == ["final.xlsx"]

    def test_http_urls_excluded(self):
        assert rp._extract_saved_filenames(parsed("https://x.test/a.pdf")) == []

    def test_deduplicated_and_resource_text_used(self):
        names = rp._extract_saved_filenames(
            parsed("saved as a.pdf", resources=[{"text": "saved as a.pdf; saved as b.docx"}])
        )
        assert names == ["a.pdf", "b.docx"]

    def test_unknown_extension_ignored(self):
        assert rp._extract_saved_filenames(parsed("saved as notes.txt")) == []


class TestExtractStorageKey:
    def test_direct_key_field(self):
        raw = {"s3_key": "/bucket/dir/deck.pptx"}
        assert rp._extract_storage_key(parsed(raw=raw)) == "bucket/dir/deck.pptx"

    def test_s3_uri_in_key_field(self):
        raw = {"object_key": "s3://my-bucket/dir/deck.pptx"}
        assert rp._extract_storage_key(parsed(raw=raw)) == "dir/deck.pptx"

    def test_json_in_text(self):
        text = json.dumps({"storageKey": "u1/deck.pptx"})
        assert rp._extract_storage_key(parsed(text)) == "u1/deck.pptx"

    def test_raw_list_of_dicts(self):
        assert rp._extract_storage_key(parsed(raw=["x", {"minio_key": "k/f.pdf"}])) == "k/f.pdf"

    def test_path_field_with_slash(self):
        assert rp._extract_storage_key(parsed(raw={"path": "\\dir\\f.pdf"})) == "dir/f.pdf"

    def test_path_field_bare_filename_with_known_extension(self):
        assert rp._extract_storage_key(parsed(raw={"file_path": "deck.pptx"})) == "deck.pptx"

    def test_path_field_bare_unknown_name_ignored(self):
        assert rp._extract_storage_key(parsed(raw={"path": "notes"})) == ""

    def test_http_path_ignored(self):
        assert rp._extract_storage_key(parsed(raw={"path": "https://x.test/a/b.pdf"})) == ""

    def test_s3_uri_in_plain_text(self):
        assert rp._extract_storage_key(parsed("загружено: s3://bkt/folder/file.docx готово")) == "folder/file.docx"

    def test_explicit_key_beats_path(self):
        raw = {"path": "other/x.pdf", "s3_key": "win/y.pdf"}
        assert rp._extract_storage_key(parsed(raw=raw)) == "win/y.pdf"

    def test_nothing(self):
        assert rp._extract_storage_key(parsed("ничего")) == ""
        assert rp._extract_storage_key(parsed(raw="строка")) == ""


class TestEnrichDownloadLinks:
    def test_direct_links_win(self):
        links = rp.enrich_download_links(parsed("https://x.test/a.pdf"), "srv")
        assert links[0]["url"] == "https://x.test/a.pdf"

    def test_proxy_url_for_saved_file(self):
        [link] = rp.enrich_download_links(parsed("Файл сохранён: deck.pptx"), "office")
        assert link["url"] == "/api/mcp/servers/office/download?filename=deck.pptx"
        assert link["label"] == "deck.pptx"
        assert link["mime"] == PPTX_MIME
        assert "storage_key" not in link

    def test_proxy_url_with_storage_key_and_quoting(self):
        parsed_result = parsed(
            json.dumps({"s3_key": "u 1/мой файл.pdf", "message": "saved as мой файл.pdf"}),
            raw={"s3_key": "u 1/мой файл.pdf"},
        )
        links = rp.enrich_download_links(parsed_result, "srv/1")
        [link] = [l for l in links if "download?" in l["url"]] or links
        if "download?" in link["url"]:
            assert "/api/mcp/servers/srv%2F1/download?" in link["url"]
            assert link["mime"] == "application/octet-stream"
            assert link["storage_key"] == "u 1/мой файл.pdf"
            assert " " not in link["url"]

    def test_no_files_no_links(self):
        assert rp.enrich_download_links(parsed("ничего"), "srv") == []


class TestFormatToolResultForUi:
    def test_raw_is_pretty_printed(self):
        out = rp.format_tool_result_for_ui(parsed("t"), raw={"a": "я"})
        assert json.loads(out) == {"a": "я"}
        assert "я" in out and "\n" in out

    def test_falls_back_to_parsed_raw(self):
        out = rp.format_tool_result_for_ui(parsed("t", raw=[1, 2]))
        assert json.loads(out) == [1, 2]

    def test_unserializable_values_stringified(self):
        out = rp.format_tool_result_for_ui(parsed(""), raw={"x": {1, 2}})
        assert "x" in out

    def test_json_text_is_reformatted_when_no_raw(self):
        out = rp.format_tool_result_for_ui(parsed('{"a":1}'))
        assert out == '{\n  "a": 1\n}'

    def test_plain_text(self):
        assert rp.format_tool_result_for_ui(parsed("hello")) == "hello"

    def test_broken_json_text_returned_as_is(self):
        assert rp.format_tool_result_for_ui(parsed("{broken")) == "{broken"

    def test_empty(self):
        assert rp.format_tool_result_for_ui(parsed("")) == ""


class TestAppendDownloadLinks:
    LINKS = [{"url": "https://x.test/a.pdf", "label": "A"}, {"url": "https://x.test/b.pdf"}]

    def test_no_links_returns_content(self):
        assert rp.append_download_links_to_content("текст", []) == "текст"

    def test_block_appended(self):
        out = rp.append_download_links_to_content("текст", self.LINKS)
        assert out.startswith("текст")
        assert "**Файлы:**" in out
        assert "- [A](https://x.test/a.pdf)" in out
        assert "- [Скачать](https://x.test/b.pdf)" in out

    def test_not_duplicated_when_url_already_in_content(self):
        content = "Вот файл: https://x.test/a.pdf"
        assert rp.append_download_links_to_content(content, self.LINKS) == content

    def test_empty_content(self):
        out = rp.append_download_links_to_content("", self.LINKS[:1])
        assert out.startswith("**Файлы:**")

    def test_links_without_url_leave_content_untouched(self):
        assert rp.append_download_links_to_content("t", [{"label": "x"}, {"url": ""}]) == "t"

    def test_link_without_url_does_not_block_valid_links(self):
        # Регрессия: пустой url «входил» в любой текст и отключал добавление блока файлов.
        out = rp.append_download_links_to_content("t", [{"label": "x"}, {"url": "u", "label": "L"}])
        assert "- [L](u)" in out
        assert "[x]" not in out
