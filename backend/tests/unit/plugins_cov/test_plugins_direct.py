"""services/plugins_direct: выбор вложения для плагина, прямой вызов сервиса и блоки для промпта."""

from types import SimpleNamespace

import pytest

from backend.services import plugins_direct as pd

pytestmark = pytest.mark.unit


def att(name="model.xlsx", obj="obj/1", bucket="b"):
    return {"name": name, "minio_object": obj, "minio_bucket": bucket}


class TestAttachmentHelpers:
    def test_debug_rows(self):
        rows = pd._attachment_debug_rows([att(), "junk", {"name": " ", "contentType": "x/y", "size": 5}, {"content_type": "t"}])
        assert rows[0] == {"name": "model.xlsx", "has_minio_object": True, "minio_object": "obj/1", "minio_bucket": "b", "contentType": None, "size": None}
        assert rows[1] == {"raw_type": "str"}
        assert rows[2]["name"] is None and rows[2]["has_minio_object"] is False and rows[2]["contentType"] == "x/y" and rows[2]["size"] == 5
        assert rows[3]["contentType"] == "t"

    def test_debug_rows_for_non_list(self):
        assert pd._attachment_debug_rows(None) == [] and pd._attachment_debug_rows("x") == []

    def test_candidates_need_name_object_and_bucket(self):
        items = [att(), "junk", att(obj=""), att(bucket=" "), att(name=""), {"name": " a.xlsx ", "minio_object": " o ", "minio_bucket": " b "}]
        assert pd._attachment_candidates(items) == [
            {"name": "model.xlsx", "minio_object": "obj/1", "minio_bucket": "b"},
            {"name": "a.xlsx", "minio_object": "o", "minio_bucket": "b"},
        ]
        assert pd._attachment_candidates(None) == []


class TestPickPluginRun:
    def test_no_plugin_ids(self):
        assert pd.pick_plugin_run(None, [att()], "q") is None
        assert pd.pick_plugin_run([" ", ""], [att()], "q") is None

    @pytest.mark.parametrize("attachments", [None, [], "x"])
    def test_no_attachments(self, attachments):
        assert pd.pick_plugin_run(["cash-flow"], attachments, "q") is None

    def test_attachments_without_storage_reference(self):
        assert pd.pick_plugin_run(["cash-flow"], [att(obj="")], "q") is None

    def test_plugin_without_extension_map(self):
        assert pd.pick_plugin_run(["unknown-plugin"], [att()], "q") is None

    def test_no_matching_file_type(self):
        assert pd.pick_plugin_run(["cash-flow"], [att(name="doc.docx")], "q") is None

    def test_picks_excel_and_trims_prompt(self):
        run = pd.pick_plugin_run(["unknown", "cash-flow"], [att("doc.docx"), att("Модель.XLSM", "o2", "b2")], "  " + "я" * 3000 + "\n")
        assert (run.plugin_id, run.label, run.file_name, run.minio_object, run.minio_bucket) == ("cash-flow", "Аудит денежного потока", "Модель.XLSM", "o2", "b2")
        assert run.prompt == "я" * 2000

    def test_non_string_message(self):
        run = pd.pick_plugin_run(["cash-flow"], [att()], None, chat_mode="agent")
        assert run.prompt == ""


RUN = pd.PluginDirectRun("cash-flow", "Аудит денежного потока", "model.xlsx", "obj/1", "b", "проверь")


class FakeRegistry:
    def __init__(self, plugin):
        self.plugin = plugin

    def require_plugin(self, pid):
        if self.plugin is None:
            raise KeyError(pid)
        return self.plugin


class FakePlatform:
    def __init__(self, plugin=None, result=None, error=None, base="http://cf:8000"):
        self.plugin = plugin if plugin is not None else SimpleNamespace(invoke_path="run", timeout_seconds=0)
        self._registry = FakeRegistry(self.plugin)
        self.result, self.error, self.base, self.calls = result, error, base, []

    def resolve_base_url(self, plugin):
        return self.base

    async def invoke_multipart(self, pid, *, files, form):
        self.calls.append((pid, files, form))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def env(monkeypatch):
    import backend.plugins.platform as pplat
    import backend.plugins.tools as ptools

    state = SimpleNamespace(platform=FakePlatform(result={"verdict_markdown": "# Вердикт", "status": "ok"}), payload=b"XLSX", load_error=None, loaded=[])

    def load(obj, bucket):
        state.loaded.append((obj, bucket))
        if state.load_error:
            raise state.load_error
        return state.payload

    monkeypatch.setattr(ptools, "load_attachment_bytes", load)
    monkeypatch.setattr(pplat, "get_plugins_platform", lambda: state.platform)
    return state


class TestRunPluginDirect:
    async def test_success(self, env):
        out = await pd.run_plugin_direct(RUN)
        assert out.ok and out.verdict_markdown == "# Вердикт" and out.artifact_markdown.startswith(":::artifact")
        assert out.bytes_sent == 4 and out.invoke_url == "http://cf:8000/run" and out.result_status == "ok" and out.error == ""
        pid, files, form = env.platform.calls[0]
        assert pid == "cash-flow" and form == {"prompt": "проверь"}
        assert files == [("model_file", ("model.xlsx", b"XLSX", pd._XLSX_MIME))]
        assert env.loaded == [("obj/1", "b")]

    async def test_status_from_nested_result_and_no_prompt(self, env):
        env.platform.result = {"result": {"status": "warn", "verdict_markdown": "v"}}
        env.platform.plugin = SimpleNamespace(invoke_path="", timeout_seconds=5)
        env.platform._registry.plugin = env.platform.plugin
        out = await pd.run_plugin_direct(pd.PluginDirectRun("cash-flow", "L", "m.xlsx", "o", "b", ""))
        assert out.result_status == "warn" and out.invoke_url == "http://cf:8000/audit"
        assert env.platform.calls[0][2] is None

    async def test_unknown_plugin_uses_default_field(self, env):
        env.platform.result = {"verdict_markdown": "v"}
        await pd.run_plugin_direct(pd.PluginDirectRun("other", "L", "m.xlsx", "o", "b", "p"))
        assert env.platform.calls[0][1][0][0] == "file"

    async def test_attachment_read_failure(self, env):
        env.load_error = OSError("нет файла")
        out = await pd.run_plugin_direct(RUN)
        assert out.ok is False and out.error == "не удалось прочитать вложение «model.xlsx»: нет файла" and env.platform.calls == []

    async def test_invoke_failure_keeps_context(self, env):
        env.platform.error = RuntimeError("сервис упал")
        out = await pd.run_plugin_direct(RUN)
        assert (out.ok, out.error, out.bytes_sent, out.invoke_url) == (False, "сервис упал", 4, "http://cf:8000/run")

    async def test_empty_exception_message_uses_class_name(self, env):
        env.platform.error = TimeoutError()
        assert (await pd.run_plugin_direct(RUN)).error == "TimeoutError"

    async def test_unknown_plugin_in_registry(self, env):
        env.platform._registry.plugin = None
        env.platform.plugin = None
        out = await pd.run_plugin_direct(RUN)
        assert out.ok is False and out.invoke_url == "" and "cash-flow" in out.error

    async def test_empty_dict_answer_is_a_failure(self, env):
        env.platform.result = {}
        out = await pd.run_plugin_direct(RUN)
        assert out.ok is False and out.error == "сервис плагина вернул пустой результат"

    async def test_non_dict_answer_gives_failure(self, env):
        env.platform.result = ["x"]
        out = await pd.run_plugin_direct(RUN)
        assert out.ok is False and out.result_status == "" and out.error == "сервис плагина вернул пустой результат"

    async def test_none_payload_counts_zero_bytes(self, env):
        env.payload = None
        env.platform.result = {"verdict_markdown": "v"}
        assert (await pd.run_plugin_direct(RUN)).bytes_sent == 0


class TestPromptBlocks:
    def test_failure_block(self):
        out = pd.prompt_block_for_outcome(RUN, pd.PluginDirectOutcome(ok=False, elapsed_sec=1.0, error="сбой"))
        assert out.startswith("[Плагин «Аудит денежного потока» не смог обработать файл «model.xlsx»: сбой.")

    def test_success_block(self):
        out = pd.prompt_block_for_outcome(RUN, pd.PluginDirectOutcome(ok=True, elapsed_sec=12.4, verdict_markdown="  вердикт  "))
        assert "(12 с)" in out and out.endswith("]\nвердикт")

    def test_long_verdict_is_truncated(self):
        long = "в" * (pd.PROMPT_VERDICT_MAX_CHARS + 10)
        out = pd.prompt_block_for_outcome(RUN, pd.PluginDirectOutcome(ok=True, elapsed_sec=0, verdict_markdown=long))
        assert "вердикт обрезан для промпта" in out and out.count("в") < len(long) + 200

    def test_system_notes(self):
        assert "model.xlsx" in pd.system_note_prerun(RUN) and "не вызывай" in pd.system_note_prerun(RUN).lower()
        assert pd.system_note_no_tools(None) == "" and pd.system_note_no_tools([" "]) == ""
        assert "cash-flow" in pd.system_note_no_tools(["cash-flow"])
