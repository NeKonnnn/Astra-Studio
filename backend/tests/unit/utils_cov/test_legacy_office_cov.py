"""utils/legacy_office: .doc/.ppt через antiword/catdoc/catppt (подмена shutil.which и subprocess.run)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from backend.utils import legacy_office as lo

pytestmark = pytest.mark.unit


class Runner:
    """Подмена subprocess.run: по имени утилиты отдаёт заготовленный результат."""

    def __init__(self, results):
        self.results = results  # {утилита: (код, stdout, stderr) | Exception}
        self.calls = []
        self.seen_data = {}

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        with open(cmd[-1], "rb") as f:
            self.seen_data[cmd[0]] = f.read()
        res = self.results[cmd[0]]
        if isinstance(res, Exception):
            raise res
        code, out, err = res
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)


@pytest.fixture
def tools(monkeypatch):
    """Все утилиты «установлены»; результат задаётся через runner.results."""
    runner = Runner({})
    monkeypatch.setattr(lo.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(lo.subprocess, "run", runner)
    return runner


class TestRealOfficeExt:
    @pytest.mark.parametrize("ext,data,expected", [
        (".doc", b"PK\x03\x04rest", ".docx"),
        (".xls", b"PK\x03\x04rest", ".xlsx"),
        (".ppt", b"PK\x03\x04rest", ".pptx"),
        (".doc", b"{\\rtf1\\ansi", ".rtf"),
        (".doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".doc"),
        (".ppt", b"{\\rtf1", ".ppt"),
        (".txt", b"PK\x03\x04", ".txt"),
    ])
    def test_extension_by_content(self, ext, data, expected):
        assert lo.real_office_ext(ext, data) == expected


class TestTidy:
    def test_collapses_padding_and_blank_runs(self):
        raw = "Колонка1      Колонка2   \n\n\n\nСтрока\tс\t\tтабами  \n"

        assert lo._tidy(raw) == "Колонка1 Колонка2\n\nСтрока\tс табами"


class TestRunTool:
    def test_returns_code_stdout_and_stderr_without_path(self, monkeypatch):
        def run(cmd, **kw):
            return SimpleNamespace(
                returncode=3, stdout="вывод".encode(), stderr=f"fail {cmd[-1]} end".encode()
            )

        monkeypatch.setattr(lo.subprocess, "run", run)

        code, out, err = lo._run_tool(["antiword", "-w", "0"], "/tmp/a.doc")

        assert (code, out, err) == (3, "вывод", "fail файл end")

    def test_timeout_becomes_runtime_error(self, monkeypatch):
        def boom(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 120)

        monkeypatch.setattr(lo.subprocess, "run", boom)

        with pytest.raises(RuntimeError, match="antiword: не уложился в 120 с"):
            lo._run_tool(["antiword"], "/tmp/a.doc")


class TestExtractDoc:
    def test_first_tool_text_is_tidied_and_tool_gets_data(self, tools):
        tools.results["antiword"] = (0, "Привет     мир\n\n\n\nКонец".encode(), b"")

        text = lo.extract_doc_text(b"DOCDATA", "a.doc")

        assert text == "Привет мир\n\nКонец"
        assert tools.seen_data["antiword"] == b"DOCDATA"
        assert [c[0][0] for c in tools.calls] == ["antiword"]
        assert tools.calls[0][1]["timeout"] == lo._TIMEOUT_S

    def test_temp_file_is_removed(self, tools):
        tools.results["antiword"] = (0, b"text", b"")

        lo.extract_doc_text(b"X")

        path = tools.calls[0][0][-1]
        import os

        assert not os.path.exists(path) and path.endswith(".doc")

    def test_falls_back_to_catdoc_when_antiword_fails(self, tools):
        tools.results["antiword"] = (1, b"", b"not a word doc")
        tools.results["catdoc"] = (0, "из catdoc".encode(), b"")

        assert lo.extract_doc_text(b"D") == "из catdoc"
        assert [c[0][0] for c in tools.calls] == ["antiword", "catdoc"]

    def test_missing_tool_is_skipped(self, tools, monkeypatch):
        monkeypatch.setattr(lo.shutil, "which", lambda n: None if n == "antiword" else "/x")
        tools.results["catdoc"] = (0, b"ok", b"")

        assert lo.extract_doc_text(b"D", "n.doc") == "ok"
        assert [c[0][0] for c in tools.calls] == ["catdoc"]

    def test_tool_exception_is_collected_then_next_tool_used(self, tools):
        tools.results["antiword"] = RuntimeError("antiword: не уложился в 120 с")
        tools.results["catdoc"] = (0, b"ok", b"")

        assert lo.extract_doc_text(b"D") == "ok"

    def test_all_tools_failing_raise_with_all_reasons(self, tools):
        tools.results["antiword"] = (2, b"", b"")
        tools.results["catdoc"] = (1, b"", b"bad file")

        with pytest.raises(RuntimeError) as ei:
            lo.extract_doc_text(b"D")

        msg = str(ei.value)
        assert "antiword: код 2: без сообщения" in msg
        assert "catdoc: код 1: bad file" in msg

    def test_no_tools_installed_raises(self, tools, monkeypatch):
        monkeypatch.setattr(lo.shutil, "which", lambda n: None)

        with pytest.raises(RuntimeError, match="antiword не установлен; catdoc не установлен"):
            lo.extract_doc_text(b"D")

    def test_empty_output_returns_empty_string_not_error(self, tools):
        tools.results["antiword"] = (0, b"   \n  ", b"")
        tools.results["catdoc"] = (0, b"", b"")

        assert lo.extract_doc_text(b"D") == ""

    def test_empty_output_after_failure_still_returns_empty(self, tools):
        tools.results["antiword"] = (1, b"", b"boom")
        tools.results["catdoc"] = (0, b"", b"")

        assert lo.extract_doc_text(b"D") == ""

    def test_unlink_oserror_is_swallowed(self, tools, monkeypatch):
        tools.results["antiword"] = (0, b"ok", b"")
        def bad_unlink(p):
            raise OSError("busy")
        monkeypatch.setattr(lo.os, "unlink", bad_unlink)

        assert lo.extract_doc_text(b"D") == "ok"


class TestExtractPpt:
    def test_uses_catppt_with_cp1251(self, tools):
        tools.results["catppt"] = (0, "Слайд".encode(), b"")

        assert lo.extract_ppt_text(b"P", "a.ppt") == "Слайд"
        cmd = tools.calls[0][0]
        assert cmd[0] == "catppt" and "cp1251" in cmd and cmd[-1].endswith(".ppt")
