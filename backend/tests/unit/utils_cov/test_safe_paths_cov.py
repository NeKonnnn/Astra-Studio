"""utils/safe_paths: редкие ветки (каталог /app, ошибки ФС, выход за корень логов)."""

from pathlib import Path

import pytest

from backend.utils import safe_paths

pytestmark = pytest.mark.unit


class TestDefaultAppRoot:
    @staticmethod
    def _fake_dirs(monkeypatch, existing):
        real_is_dir = Path.is_dir
        monkeypatch.setattr(
            Path, "is_dir", lambda self: str(self).replace("\\", "/") in existing or real_is_dir(self)
        )

    def test_uses_slash_app_when_directory_exists(self, monkeypatch):
        # нужен и /app, и /app/backend: одного /app мало (чужой F:pp на Windows-хосте)
        self._fake_dirs(monkeypatch, {"/app", "/app/backend"})

        assert safe_paths.default_app_root() == Path("/app")

    def test_slash_app_without_backend_is_ignored(self, monkeypatch):
        real_is_dir = Path.is_dir
        monkeypatch.setattr(
            Path,
            "is_dir",
            lambda self: True if str(self).replace("\\", "/") == "/app" else (
                False if str(self).replace("\\", "/") == "/app/backend" else real_is_dir(self)
            ),
        )

        assert safe_paths.default_app_root() == Path(safe_paths.__file__).resolve().parent.parent.parent

    def test_falls_back_to_repository_root(self, monkeypatch):
        real_is_dir = Path.is_dir
        monkeypatch.setattr(
            Path, "is_dir", lambda self: False if str(self).replace("\\", "/") == "/app" else real_is_dir(self)
        )

        root = safe_paths.default_app_root()

        assert root == Path(safe_paths.__file__).resolve().parent.parent.parent


class TestResolvePathUnderBaseErrors:
    def test_resolve_failure_returns_none(self, tmp_path, monkeypatch):
        real_resolve = Path.resolve

        def fake_resolve(self, strict=False):
            if "boom" in str(self):
                raise OSError("плохой путь")
            return real_resolve(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", fake_resolve)

        assert safe_paths.resolve_path_under_base("boom/x.log", tmp_path) is None

    def test_parent_creation_failure_returns_none(self, tmp_path):
        (tmp_path / "occupied").write_text("файл вместо каталога")

        result = safe_paths.resolve_path_under_base(
            "occupied/child.log", tmp_path, create_parent=True
        )

        assert result is None


class TestResolveLogFilePath:
    def test_path_escaping_unresolved_logs_parent_is_rejected(self, tmp_path, monkeypatch):
        # logs_dir.parent содержит «..»: после resolve путь лежит под базой,
        # но не под нормализованным родителем - вторая проверка отклоняет его.
        (tmp_path / "x").mkdir()
        weird_logs = tmp_path / "x" / ".." / "logs"
        monkeypatch.setattr(safe_paths, "default_logs_dir", lambda: weird_logs)

        assert safe_paths.resolve_log_file_path("logs/app.log") is None

    def test_not_writable_path_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(safe_paths, "default_logs_dir", lambda: tmp_path / "logs")
        monkeypatch.setattr(safe_paths, "is_writable_path", lambda p: False)

        assert safe_paths.resolve_log_file_path("logs/app.log") is None

    def test_writable_path_inside_root_is_returned(self, tmp_path, monkeypatch):
        monkeypatch.setattr(safe_paths, "default_logs_dir", lambda: tmp_path / "logs")

        result = safe_paths.resolve_log_file_path("logs/app.log")

        assert result == (tmp_path / "logs" / "app.log").resolve()
