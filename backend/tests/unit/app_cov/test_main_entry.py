"""backend/main.py: настройка sys.path и экспорт app из backend.application."""

import os
import sys
import types
from pathlib import Path

import pytest

import backend.main as main_module

_MAIN_PATH = Path(main_module.__file__)


@pytest.fixture
def run_main(monkeypatch):
    """Выполняет main.py заново с подменённым backend.application и изолированным sys.path."""
    fake_app = object()
    calls = types.SimpleNamespace(run_server=0, subprocess=[])
    fake_application = types.SimpleNamespace(
        app=fake_app, run_server=lambda: setattr(calls, "run_server", calls.run_server + 1)
    )
    monkeypatch.setitem(sys.modules, "backend.application", fake_application)
    monkeypatch.setattr(sys, "path", list(sys.path))

    def _run(name="backend.main", file_name=None):
        source = _MAIN_PATH.read_text(encoding="utf-8")
        namespace = {"__name__": name, "__file__": file_name or str(_MAIN_PATH)}
        exec(compile(source, str(_MAIN_PATH), "exec"), namespace)  # noqa: S102
        return namespace

    return types.SimpleNamespace(run=_run, app=fake_app, calls=calls)


def test_real_module_exports_application_app():
    import backend.application as application

    assert main_module.app is application.app


def test_import_puts_package_dirs_in_sys_path_and_exports_app(run_main):
    ns = run_main.run()
    assert ns["app"] is run_main.app
    assert sys.path[0] == ns["_root_dir"]
    assert sys.path[1] == ns["_current_dir"]
    assert ns["_current_dir"] == str(_MAIN_PATH.parent)
    assert os.path.dirname(ns["_current_dir"]) == ns["_root_dir"]
    assert run_main.calls.run_server == 0  # при импорте сервер не стартует


def test_run_as_script_starts_server(run_main):
    run_main.run(name="__main__")
    assert run_main.calls.run_server == 1


def test_container_layout_creates_backend_symlink(run_main, monkeypatch):
    """Каталог /app без /app/backend: main.py создаёт симлинк через ln."""
    import subprocess

    recorded = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: recorded.append((cmd, kw)))
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("os.path.abspath", lambda p: "/app/main.py")
    monkeypatch.setattr("os.path.exists", lambda p: False)
    run_main.run()
    assert recorded == [(["/bin/ln", "-sf", "/app", "/app/backend"], {"check": False})]


def test_container_layout_uses_ln_from_path(run_main, monkeypatch):
    import subprocess

    recorded = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: recorded.append(cmd))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ln")
    monkeypatch.setattr("os.path.abspath", lambda p: "/app/main.py")
    monkeypatch.setattr("os.path.exists", lambda p: False)
    run_main.run()
    assert recorded == [["/usr/bin/ln", "-sf", "/app", "/app/backend"]]


def test_container_layout_with_existing_backend_dir_does_nothing(run_main, monkeypatch):
    import subprocess

    recorded = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: recorded.append(cmd))
    monkeypatch.setattr("os.path.abspath", lambda p: "/app/main.py")
    monkeypatch.setattr("os.path.exists", lambda p: True)
    run_main.run()
    assert recorded == []
