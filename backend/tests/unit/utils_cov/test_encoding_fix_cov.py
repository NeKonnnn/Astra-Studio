"""utils/encoding_fix: настройка кодировки Windows, safe_print, safe_log."""

import logging
from types import SimpleNamespace

import pytest

from backend.utils import encoding_fix

pytestmark = pytest.mark.unit


class FakeStream:
    def __init__(self):
        self.encoding = None

    def reconfigure(self, encoding):
        self.encoding = encoding


class TestFixWindowsEncoding:
    def test_non_windows_is_noop(self, monkeypatch):
        monkeypatch.setattr(encoding_fix.sys, "platform", "linux")
        monkeypatch.setattr(
            encoding_fix.subprocess, "run", lambda *a, **k: pytest.fail("не должен вызываться")
        )

        assert encoding_fix.fix_windows_encoding() is True

    def test_windows_runs_chcp_and_reconfigures_streams(self, monkeypatch):
        calls = []
        out, err = FakeStream(), FakeStream()
        monkeypatch.setattr(encoding_fix.sys, "platform", "win32")
        monkeypatch.setattr(encoding_fix.sys, "stdout", out)
        monkeypatch.setattr(encoding_fix.sys, "stderr", err)
        monkeypatch.setattr("shutil.which", lambda name: "C:/chcp.exe")
        monkeypatch.setattr(encoding_fix.subprocess, "run", lambda *a, **k: calls.append(a))
        handlers = []
        monkeypatch.setattr(
            encoding_fix, "reconfigure_backend_handler_streams_utf8", lambda: handlers.append(1)
        )

        assert encoding_fix.fix_windows_encoding() is True
        assert calls == [(["C:/chcp.exe", "65001"],)]
        assert out.encoding == "utf-8" and err.encoding == "utf-8"
        assert handlers == [1]

    def test_chcp_path_falls_back_to_system_root(self, monkeypatch):
        calls = []
        monkeypatch.setattr(encoding_fix.sys, "platform", "win32")
        monkeypatch.setattr(encoding_fix.sys, "stdout", SimpleNamespace())  # без reconfigure
        monkeypatch.setattr(encoding_fix.sys, "stderr", SimpleNamespace())
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setenv("SystemRoot", "D:/Win")
        monkeypatch.setattr(encoding_fix.subprocess, "run", lambda *a, **k: calls.append(a[0]))
        monkeypatch.setattr(encoding_fix, "reconfigure_backend_handler_streams_utf8", lambda: None)

        assert encoding_fix.fix_windows_encoding() is True
        assert calls[0][0].replace("\\", "/") == "D:/Win/System32/chcp.exe"

    def test_failure_is_logged_and_returns_false(self, monkeypatch):
        monkeypatch.setattr(encoding_fix.sys, "platform", "win32")

        def boom(*a, **k):
            raise OSError("нет chcp")

        monkeypatch.setattr(encoding_fix.subprocess, "run", boom)

        assert encoding_fix.fix_windows_encoding() is False


class TestSafePrint:
    def test_logs_text(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            encoding_fix, "logger", SimpleNamespace(info=lambda fmt, *a: seen.append(fmt % a))
        )

        encoding_fix.safe_print("привет")

        assert seen == ["привет"]

    def test_unicode_error_falls_back_to_ascii(self, monkeypatch):
        seen = []

        def info(fmt, *a):
            text = fmt % a
            if any(ord(c) > 127 for c in text):
                raise UnicodeEncodeError("ascii", text, 0, 1, "bad")
            seen.append(text)

        monkeypatch.setattr(encoding_fix, "logger", SimpleNamespace(info=info))

        encoding_fix.safe_print("abc привет")

        assert seen == ["abc "]


class TestSafeLog:
    def test_uses_requested_level(self):
        log = SimpleNamespace(warning=lambda m: log.seen.append(("w", m)), info=lambda m: None, seen=[])

        encoding_fix.safe_log(log, "WARNING", "msg")

        assert log.seen == [("w", "msg")]

    def test_unknown_level_falls_back_to_info(self):
        seen = []
        log = SimpleNamespace(info=seen.append)

        encoding_fix.safe_log(log, "nolevel", "msg")

        assert seen == ["msg"]

    def test_unicode_error_logs_ascii_version(self):
        seen = []

        def info(m):
            if any(ord(c) > 127 for c in m):
                raise UnicodeEncodeError("ascii", m, 0, 1, "bad")
            seen.append(m)

        encoding_fix.safe_log(SimpleNamespace(info=info), "info", "x привет")

        assert seen == ["x "]
