"""services/legacy_office: .doc/.ppt через antiword/catdoc/catppt (подмена asyncio.create_subprocess_exec), определение формата по содержимому."""

from __future__ import annotations

import asyncio
import logging
import os

import pytest

from app.services import legacy_office as lo

pytestmark = pytest.mark.unit


# ───────────────────────────── real_office_ext / _tidy ─────────────────────────────


class TestRealOfficeExt:
    @pytest.mark.parametrize("ext,data,expected", [
        (".doc", b"PK\x03\x04rest", ".docx"),
        (".xls", b"PK\x03\x04rest", ".xlsx"),
        (".ppt", b"PK\x03\x04rest", ".pptx"),
        (".doc", b"{\\rtf1\\ansi", ".rtf"),
        (".doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".doc"),
        (".ppt", b"{\\rtf1", ".ppt"),
        (".txt", b"PK\x03\x04", ".txt"),
        (".docx", b"PK\x03\x04", ".docx"),
        (".doc", b"", ".doc"),
    ])
    def test_extension_by_content(self, ext, data, expected):
        assert lo.real_office_ext(ext, data) == expected


class TestTidy:
    def test_collapses_padding_and_blank_runs(self):
        raw = "Колонка1      Колонка2   \n\n\n\nСтрока\tс\t\tтабами  \n"

        assert lo._tidy(raw) == "Колонка1 Колонка2\n\nСтрока\tс табами"

    def test_empty_and_whitespace_only(self):
        assert lo._tidy("") == "" and lo._tidy("  \n \n") == ""


# ───────────────────────────── подмена процессов ─────────────────────────────


class FakeProc:
    def __init__(self, out=b"", err=b"", code=0, hang=False):
        self.out, self.err, self.returncode, self.hang = out, err, code, hang
        self.killed = False
        self.waited = False

    async def communicate(self):
        if self.hang:
            await asyncio.Event().wait()
        return self.out, self.err

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True


class Spawner:
    """Заменяет create_subprocess_exec: по имени утилиты отдаёт заданный FakeProc и пишет запуски."""

    def __init__(self):
        self.procs = {}
        self.launches = []
        self.seen_input = []

    def __call__(self, *cmd, **kwargs):
        async def run():
            path = cmd[-1]
            with open(path, "rb") as f:
                self.seen_input.append(f.read())
            self.launches.append((cmd[:-1], path, kwargs))
            result = self.procs[cmd[0]]
            if isinstance(result, Exception):
                raise result
            return result

        return run()


@pytest.fixture
def spawn(monkeypatch):
    spawner = Spawner()
    monkeypatch.setattr(lo.asyncio, "create_subprocess_exec", spawner)
    monkeypatch.setattr(lo.shutil, "which", lambda name: f"/usr/bin/{name}")
    return spawner


class TestRunTool:
    async def test_returns_code_stdout_and_stderr_without_temp_path(self, spawn, tmp_path):
        target = tmp_path / "f.doc"
        target.write_bytes(b"data")
        spawn.procs["antiword"] = FakeProc(b"\xd0\xa2\xd0\xb5\xd0\xba\xd1\x81\xd1\x82", f"ошибка в {target}".encode(), 3)

        code, out, err = await lo._run_tool(("antiword", "-w", "0"), str(target))

        assert (code, out) == (3, "Текст") and err == "ошибка в файл"
        assert spawn.launches[0][0] == ("antiword", "-w", "0") and spawn.launches[0][1] == str(target)
        # stdout и stderr перехватываются
        assert spawn.launches[0][2]["stdout"] == asyncio.subprocess.PIPE

    async def test_invalid_utf8_is_replaced_not_raised(self, spawn, tmp_path):
        target = tmp_path / "f.doc"
        target.write_bytes(b"x")
        spawn.procs["antiword"] = FakeProc(b"\xff\xfe ok", b"\xff", 0)

        _, out, err = await lo._run_tool(("antiword",), str(target))

        assert "ok" in out and "�" in out and err == "�"

    async def test_timeout_kills_process_and_raises(self, spawn, tmp_path, monkeypatch):
        target = tmp_path / "f.doc"
        target.write_bytes(b"x")
        proc = FakeProc(hang=True)
        spawn.procs["antiword"] = proc
        monkeypatch.setattr(lo, "_TIMEOUT_S", 0.01)

        with pytest.raises(RuntimeError, match="antiword: не уложился в"):
            await lo._run_tool(("antiword",), str(target))

        assert proc.killed and proc.waited

    async def test_cancellation_kills_child(self, spawn, tmp_path):
        # BUG-RAG-20 / RAG-13: при CancelledError дочерний antiword/catdoc/catppt убивается и ожидается.
        target = tmp_path / "f.doc"
        target.write_bytes(b"x")
        proc = FakeProc(hang=True)
        spawn.procs["antiword"] = proc
        task = asyncio.create_task(lo._run_tool(("antiword",), str(target)))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert proc.killed is True and proc.waited is True


class TestExtract:
    async def test_first_tool_text_is_tidied_and_returned(self, spawn):
        spawn.procs["antiword"] = FakeProc("Привет    мир\n\n\n\nконец".encode())

        text = await lo.extract_doc_text(b"\xd0\xcf-binary", "отчёт.doc")

        assert text == "Привет мир\n\nконец"
        # утилита получила именно переданные байты во временном файле с нужным суффиксом
        assert spawn.seen_input == [b"\xd0\xcf-binary"]
        assert spawn.launches[0][1].endswith(".doc") and spawn.launches[0][0][0] == "antiword"
        assert not os.path.exists(spawn.launches[0][1])

    async def test_antiword_failure_falls_back_to_catdoc(self, spawn):
        spawn.procs["antiword"] = FakeProc(b"", b"is not a Word Document", 1)
        spawn.procs["catdoc"] = FakeProc("из catdoc".encode())

        assert await lo.extract_doc_text(b"x") == "из catdoc"
        assert [l[0][0] for l in spawn.launches] == ["antiword", "catdoc"]

    async def test_empty_output_tries_next_tool(self, spawn):
        spawn.procs["antiword"] = FakeProc(b"  \n ")
        spawn.procs["catdoc"] = FakeProc("текст".encode())

        assert await lo.extract_doc_text(b"x") == "текст"

    async def test_all_tools_empty_is_empty_document_not_error(self, spawn, caplog):
        spawn.procs["antiword"] = FakeProc(b" ")
        spawn.procs["catdoc"] = FakeProc(b"")

        with caplog.at_level(logging.WARNING, logger=lo.logger.name):
            assert await lo.extract_doc_text(b"x", "пустой.doc") == ""

        assert any("текста нет" in r.getMessage() for r in caplog.records)

    async def test_one_empty_one_failed_is_still_empty_with_errors_in_log(self, spawn, caplog):
        spawn.procs["antiword"] = FakeProc(b"")
        spawn.procs["catdoc"] = FakeProc(b"", b"boom", 2)

        with caplog.at_level(logging.WARNING, logger=lo.logger.name):
            assert await lo.extract_doc_text(b"x") == ""

        assert any("catdoc: код 2: boom" in r.getMessage() for r in caplog.records)

    async def test_all_tools_failed_raises_with_every_reason(self, spawn):
        spawn.procs["antiword"] = FakeProc(b"", b"", 1)
        spawn.procs["catdoc"] = FakeProc(b"", b"x" * 400, 2)

        with pytest.raises(RuntimeError) as exc:
            await lo.extract_doc_text(b"x")

        msg = str(exc.value)
        assert "antiword: код 1: без сообщения" in msg and "catdoc: код 2: " + "x" * 300 in msg
        assert "x" * 301 not in msg

    async def test_missing_tools_are_reported(self, spawn, monkeypatch, caplog):
        monkeypatch.setattr(lo.shutil, "which", lambda name: None)

        with caplog.at_level(logging.WARNING, logger=lo.logger.name):
            with pytest.raises(RuntimeError) as exc:
                await lo.extract_doc_text(b"x", "a.doc")

        assert str(exc.value) == "antiword не установлен; catdoc не установлен"
        assert spawn.launches == []
        assert any("утилиты нет в PATH" in r.getMessage() for r in caplog.records)

    async def test_launch_errors_are_collected_and_next_tool_is_tried(self, spawn):
        spawn.procs["antiword"] = OSError("Permission denied")
        spawn.procs["catdoc"] = FakeProc("ok".encode())

        assert await lo.extract_doc_text(b"x") == "ok"

    async def test_launch_error_of_all_tools(self, spawn):
        spawn.procs["antiword"] = OSError("нет прав")
        spawn.procs["catdoc"] = RuntimeError("упал")

        with pytest.raises(RuntimeError, match="нет прав; упал"):
            await lo.extract_doc_text(b"x")

    async def test_ppt_uses_catppt_with_cp1251_source(self, spawn):
        spawn.procs["catppt"] = FakeProc("слайд".encode())

        assert await lo.extract_ppt_text(b"x", "p.ppt") == "слайд"
        cmd, path, _ = spawn.launches[0]
        assert cmd == ("catppt", "-d", "utf-8", "-s", "cp1251") and path.endswith(".ppt")

    async def test_temp_file_is_removed_even_on_failure(self, spawn):
        spawn.procs["catppt"] = FakeProc(b"", b"bad", 1)

        with pytest.raises(RuntimeError):
            await lo.extract_ppt_text(b"x")

        assert not os.path.exists(spawn.launches[0][1])

    async def test_unlink_error_is_ignored(self, spawn, monkeypatch):
        spawn.procs["catppt"] = FakeProc("t".encode())

        def boom(path):
            raise OSError("занят")

        monkeypatch.setattr(lo.os, "unlink", boom)

        assert await lo.extract_ppt_text(b"x") == "t"

    async def test_default_label_without_name(self, spawn, caplog):
        spawn.procs["catppt"] = FakeProc("t".encode())

        with caplog.at_level(logging.INFO, logger=lo.logger.name):
            await lo.extract_ppt_text(b"abc")

        assert any("«файл.ppt» (3 байт)" in r.getMessage() for r in caplog.records)
