"""
main.py - точка входа
Здесь только:
  - настройка sys.path (для python main.py и Docker)
  - экспорт app для uvicorn / import backend.main
Вся логика приложения - в backend/application.py
"""

import importlib
import os
import subprocess
import sys

_current_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(_current_dir)
if _current_dir == "/app" and (not os.path.exists("/app/backend")):
    import shutil

    _ln = shutil.which("ln") or "/bin/ln"
    subprocess.run([_ln, "-sf", "/app", "/app/backend"], check=False)  # noqa: S603
sys.path.insert(0, _current_dir)
sys.path.insert(0, _root_dir)

_application = importlib.import_module("backend.application")
app = _application.app

if __name__ == "__main__":
    _application.run_server()
