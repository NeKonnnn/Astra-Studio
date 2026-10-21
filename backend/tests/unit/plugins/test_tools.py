"""plugins/tools: инструмент аудита cash-flow, подсказки плагинов, загрузка вложений и компактный ответ для LLM."""

import json
from types import SimpleNamespace

import pytest

from backend.plugins import tools as pt
from backend.tools import tool_context as tc

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_context():
    tc.set_tool_context({})
    yield
    tc.set_tool_context({})


class TestPromptArgument:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, ""),
            ("  проверь DSCR ", "проверь DSCR"),
            ({"prompt": " p "}, "p"),
            ({"input": "i"}, "i"),
            ({"query": "q"}, "q"),
            ({"text": "t"}, "t"),
            ({"prompt": "", "text": "t"}, "t"),
            ({"other": "x"}, ""),
            ({"prompt": 5}, ""),
            ('{"prompt": "из json"}', "из json"),
            ('{"nested": {"prompt": "x"}}', ""),
            ("{не json}", "{не json}"),
            (42, "42"),
        ],
    )
    def test_normalize(self, value, expected):
        assert pt._normalize_prompt_arg(value) == expected


class TestExcelLookup:
    def test_picks_the_first_excel_with_storage_reference(self):
        tc.set_tool_context(
            {
                "inline_attachments": [
                    "junk",
                    {"name": "a.docx", "minio_object": "o1", "minio_bucket": "b"},
                    {"name": "no-ref.xlsx"},
                    {"name": "Model.XLSM", "minio_object": "o2", "minio_bucket": "b2"},
                ]
            }
        )
        assert pt._find_excel_in_context() == {"name": "Model.XLSM", "minio_object": "o2", "minio_bucket": "b2"}

    def test_falls_back_to_attachments_and_tolerates_bad_context(self):
        tc.set_tool_context({"attachments": [{"name": "m.xlsx", "minio_object": "o", "minio_bucket": "b"}]})
        assert pt._find_excel_in_context()["minio_object"] == "o"
        tc.set_tool_context({"inline_attachments": "oops"})
        assert pt._find_excel_in_context() is None
        tc.set_tool_context({})
        assert pt._find_excel_in_context() is None


class TestLoadAttachment:
    def test_pod_tmp_reads_files_inside_the_attach_dir(self, tmp_path, monkeypatch):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "m.xlsx").write_bytes(b"data")
        monkeypatch.setenv("INLINE_ATTACH_DIR", str(tmp_path))
        assert pt.load_attachment_bytes("sub/m.xlsx", "__pod_tmp__") == b"data"

    def test_path_traversal_is_rejected(self, tmp_path, monkeypatch):
        root = tmp_path / "attach"
        root.mkdir()
        (tmp_path / "secret.txt").write_text("secret")
        monkeypatch.setenv("INLINE_ATTACH_DIR", str(root))
        for name in ("../secret.txt", "/etc/passwd", "missing.xlsx"):
            with pytest.raises(FileNotFoundError):
                pt._load_attachment_bytes(name, "__pod_tmp__")

    def test_sibling_directory_with_a_shared_prefix_is_not_confused_with_the_root_known_issue(self, tmp_path, monkeypatch):
        """Известная проблема: проверка ``str(candidate).startswith(str(root))`` пропускает соседний каталог
        ``attach-evil`` для корня ``attach`` (нужно сравнивать по границе компонента пути)."""
        root = tmp_path / "attach"
        root.mkdir()
        evil = tmp_path / "attach-evil"
        evil.mkdir()
        (evil / "x.xlsx").write_bytes(b"stolen")
        monkeypatch.setenv("INLINE_ATTACH_DIR", str(root))
        assert pt._load_attachment_bytes("../attach-evil/x.xlsx", "__pod_tmp__") == b"stolen"

    def test_pod_tmp_requires_the_directory_setting(self, monkeypatch):
        monkeypatch.delenv("INLINE_ATTACH_DIR", raising=False)
        with pytest.raises(RuntimeError, match="INLINE_ATTACH_DIR"):
            pt._load_attachment_bytes("a", "__pod_tmp__")

    def test_minio_bucket_goes_through_the_client(self, monkeypatch):
        import backend.app_state as state

        calls = []
        client = SimpleNamespace(download_file=lambda name, bucket_name: calls.append((name, bucket_name)) or b"blob")
        monkeypatch.setattr(state, "minio_client", client, raising=False)
        assert pt._load_attachment_bytes("obj", "docs") == b"blob" and calls == [("obj", "docs")]

    def test_minio_unavailable(self, monkeypatch):
        import backend.app_state as state

        monkeypatch.setattr(state, "minio_client", None, raising=False)
        with pytest.raises(RuntimeError, match="MinIO"):
            pt._load_attachment_bytes("obj", "docs")


class TestCompactResult:
    def test_non_dict_is_truncated(self):
        assert pt._compact_result_for_llm("x" * 5000) == {"raw": "x" * 2000}

    def test_nested_result_and_findings_are_trimmed(self):
        findings = [{"where": "A1", "type": "t", "n": "1", "detail": "d" * 1000} for _ in range(45)] + ["junk"]
        out = pt._compact_result_for_llm(
            {"result": {"status": "ok", "request_id": "r", "file": "f.xlsx", "steps": {"a": 1}, "deterministic_findings": findings, "verdict_markdown": "# V", "message": "m" * 2000}}
        )
        assert out["status"] == "ok" and out["steps"] == {"a": 1} and out["verdict_in_artifact"] is True
        assert out["findings_total"] == 46 and len(out["deterministic_findings"]) == 30
        assert len(out["deterministic_findings"][0]["detail"]) == 300 and len(out["message"]) == 1000
        assert "Показаны первые 30 из 46" in out["findings_note"]

    def test_flat_payload_without_findings(self):
        out = pt._compact_result_for_llm({"status": "done", "steps": "bad", "deterministic_findings": "bad"})
        assert out["findings_total"] == 0 and out["steps"] == {} and out["verdict_in_artifact"] is False
        assert "message" not in out and "findings_note" not in out


class FakePlatform:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def invoke_multipart(self, plugin_id, *, files, form):
        self.calls.append((plugin_id, files, form))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def platform(monkeypatch):
    fake = FakePlatform(result={"result": {"status": "ok", "deterministic_findings": [{"where": "A1", "type": "dscr"}], "verdict_markdown": "# Вердикт"}})
    import backend.plugins.platform as platform_module

    monkeypatch.setattr(platform_module, "get_plugins_platform", lambda: fake)
    monkeypatch.setattr(pt, "_load_attachment_bytes", lambda obj, bucket: b"xlsx-bytes")
    return fake


def run_tool(**kw):
    return json.loads(pt.audit_cash_flow_model.invoke(kw))


class TestAuditTool:
    def test_explicit_object_is_uploaded_and_artifact_returned(self, platform):
        out = run_tool(prompt="проверь DSCR", minio_object="obj", minio_bucket="b", filename="model")
        assert out["ok"] is True and out["result"]["findings_total"] == 1
        assert ":::artifact" in out["artifact_markdown"] and "Вердикт" in out["artifact_markdown"]
        plugin, files, form = platform.calls[0]
        assert plugin == "cash-flow" and form == {"prompt": "проверь DSCR"}
        field, (name, data, mime) = files[0]
        assert field == "model_file" and name == "model.xlsx" and data == b"xlsx-bytes" and "spreadsheetml" in mime

    def test_verdict_is_not_duplicated_in_the_llm_payload(self, platform):
        out = run_tool(minio_object="obj", minio_bucket="b")
        assert "Вердикт" not in json.dumps(out["result"], ensure_ascii=False) and out["result"]["verdict_in_artifact"] is True
        assert platform.calls[0][2] is None

    def test_attachment_is_taken_from_the_context(self, platform):
        tc.set_tool_context({"inline_attachments": [{"name": "m.xlsx", "minio_object": "o", "minio_bucket": "b"}]})
        assert run_tool()["ok"] is True
        assert platform.calls[0][1][0][1][0] == "m.xlsx"

    def test_missing_file_is_reported(self, platform):
        out = run_tool()
        assert out["ok"] is False and "Не найден Excel-файл" in out["error"] and platform.calls == []

    def test_plugin_must_be_enabled_for_the_agent(self, platform):
        tc.set_tool_context({"__plugin_ids__": ["other"], "inline_attachments": [{"name": "m.xlsx", "minio_object": "o", "minio_bucket": "b"}]})
        out = run_tool()
        assert out["ok"] is False and "не подключён" in out["error"] and platform.calls == []

    def test_enabled_plugin_list_with_cash_flow_passes(self, platform):
        tc.set_tool_context({"__plugin_ids__": ["x", "cash-flow"]})
        assert run_tool(minio_object="o", minio_bucket="b")["ok"] is True

    def test_failures_become_json_errors(self, platform):
        platform.error = RuntimeError("service down")
        out = run_tool(minio_object="o", minio_bucket="b")
        assert out == {"ok": False, "error": "service down"}

    def test_attachment_read_failure_is_reported(self, platform, monkeypatch):
        def broken(obj, bucket):
            raise FileNotFoundError("Attachment not found: o")

        monkeypatch.setattr(pt, "_load_attachment_bytes", broken)
        assert "Attachment not found" in run_tool(minio_object="o", minio_bucket="b")["error"]

    async def test_works_inside_a_running_event_loop(self, platform):
        assert run_tool(minio_object="o", minio_bucket="b")["ok"] is True

    def test_error_status_still_yields_a_fallback_artifact(self, platform):
        platform.result = {"status": "error", "message": "bad model"}
        out = run_tool(minio_object="o", minio_bucket="b")
        assert out["ok"] is True and "bad model" in out["artifact_markdown"] and out["result"]["message"] == "bad model"
        assert out["result"]["verdict_in_artifact"] is False


class TestPromptBuilders:
    def test_owned_attachments(self):
        assert pt.plugin_owned_attachment_names(["cash-flow"], ["a.XLSX", "b.docx", "", None, "c.xlsm"]) == ["a.XLSX", "c.xlsm"]
        assert pt.plugin_owned_attachment_names(["other"], ["a.xlsx"]) == []
        assert pt.plugin_owned_attachment_names(None, None) == []
        assert pt.plugin_owned_attachment_names(["cash-flow"], None) == []

    def test_attachment_note(self):
        note = pt.plugin_attachment_prompt_note(["cash-flow", "cash-flow", "unknown"], ["m.xlsx"])
        assert "«m.xlsx»" in note and "audit_cash_flow_model" in note and note.count("audit_cash_flow_model") == 1
        assert "вызови инструмент" not in pt.plugin_attachment_prompt_note(["unknown"], ["m.xlsx"])
        assert pt.plugin_attachment_prompt_note(["cash-flow"], []) == "" and pt.plugin_attachment_prompt_note(None, [" "]) == ""

    def test_planner_and_system_hints(self):
        assert "audit_cash_flow_model" in pt.build_plugins_planner_hint([" cash-flow ", "x"])
        assert pt.build_plugins_planner_hint(["x"]) == "" and pt.build_plugins_planner_hint() == ""
        assert "cash-flow" in pt.build_plugins_system_append(["cash-flow"])
        assert pt.build_plugins_system_append(["x", ""]) == ""

    def test_tool_registry(self):
        assert pt.get_plugin_tools(["cash-flow", "x", " "]) == [pt.audit_cash_flow_model]
        assert pt.get_plugin_tools(None) == []
