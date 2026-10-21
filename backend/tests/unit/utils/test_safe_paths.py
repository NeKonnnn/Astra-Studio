"""utils/safe_paths: защита от выхода за пределы каталога (CWE-73) и от инъекции в логи (CWE-117)."""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.utils import safe_paths

pytestmark = pytest.mark.unit


@pytest.fixture
def root(tmp_path, monkeypatch):
    """Корень приложения во временном каталоге: тесты не пишут в репозиторий."""
    monkeypatch.setattr(safe_paths, "default_app_root", lambda: tmp_path)
    for name in (
        "BACKEND_LOG_DIR",
        "CONTEXT_PROMPTS_DIR",
        "CONTEXT_PROMPTS_FILE",
        "LLM_SETTINGS_DIR",
        "LLM_SETTINGS_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


class TestSanitizeLogValue:
    def test_newlines_and_nul_are_replaced(self):
        assert safe_paths.sanitize_log_value("a\nb\r\nc\x00d") == "a b  c d"

    def test_non_strings_are_stringified(self):
        assert safe_paths.sanitize_log_value(42) == "42"
        assert safe_paths.sanitize_log_value(None) == "None"

    def test_forged_log_line_cannot_start_new_record(self):
        forged = "ok\n2026-01-01 [ERROR] fake admin login"

        assert "\n" not in safe_paths.sanitize_log_value(forged)

    @given(st.text())
    def test_never_contains_line_breaks(self, text):
        out = safe_paths.sanitize_log_value(text)

        assert "\n" not in out and "\r" not in out and "\x00" not in out


class TestResolvePathUnderBase:
    def test_relative_path_is_joined_to_base(self, tmp_path):
        result = safe_paths.resolve_path_under_base("a/b.txt", tmp_path)

        assert result == (tmp_path / "a" / "b.txt").resolve()

    def test_absolute_path_inside_base_is_accepted(self, tmp_path):
        inside = tmp_path / "x.txt"

        assert safe_paths.resolve_path_under_base(str(inside), tmp_path) == inside.resolve()

    def test_absolute_path_outside_base_is_rejected(self, tmp_path):
        assert safe_paths.resolve_path_under_base("/etc/passwd", tmp_path) is None

    @pytest.mark.parametrize(
        "raw",
        ["../x", "a/../../x", "..", "a/..", "/../etc/passwd", "a/../b"],
    )
    def test_parent_components_are_rejected(self, tmp_path, raw):
        assert safe_paths.resolve_path_under_base(raw, tmp_path) is None

    @pytest.mark.parametrize(("raw", "expected"), [("a/./b", "a/b"), ("./a", "a"), (".", "")])
    def test_single_dot_is_normalized_away_not_rejected(self, tmp_path, raw, expected):
        # См. KNOWN_BUGS.md: docstring обещает запрет ".", но pathlib убирает его
        # при разборе, поэтому проверка на "." недостижима. Выхода за base нет.
        result = safe_paths.resolve_path_under_base(raw, tmp_path)

        assert result == (tmp_path / expected).resolve()

    @pytest.mark.parametrize("raw", ["", "   ", "\t"])
    def test_blank_path_is_rejected(self, tmp_path, raw):
        assert safe_paths.resolve_path_under_base(raw, tmp_path) is None

    def test_symlink_escaping_base_is_rejected(self, tmp_path):
        import sys

        base = tmp_path / "base"
        outside = tmp_path / "outside"
        base.mkdir()
        outside.mkdir()
        try:
            (base / "link").symlink_to(outside)
        except OSError as exc:
            if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
                pytest.skip("создание symlink на Windows требует прав администратора")
            raise

        assert safe_paths.resolve_path_under_base("link/secret.txt", base) is None

    def test_sibling_directory_with_same_prefix_is_rejected(self, tmp_path):
        base = tmp_path / "app"
        sibling = tmp_path / "app-evil"
        base.mkdir()
        sibling.mkdir()

        assert safe_paths.resolve_path_under_base(str(sibling / "f"), base) is None

    def test_create_parent_creates_directories(self, tmp_path):
        result = safe_paths.resolve_path_under_base("deep/er/f.txt", tmp_path, create_parent=True)

        assert result.parent.is_dir()

    def test_create_parent_not_called_for_rejected_path(self, tmp_path):
        safe_paths.resolve_path_under_base("../evil/f.txt", tmp_path, create_parent=True)

        assert not (tmp_path.parent / "evil").exists()

    def test_defaults_to_app_root(self, root):
        assert safe_paths.resolve_path_under_base("f.txt") == (root / "f.txt").resolve()

    def test_surrounding_whitespace_is_stripped(self, tmp_path):
        result = safe_paths.resolve_path_under_base("  f.txt  ", tmp_path)

        assert result == (tmp_path / "f.txt").resolve()

    @settings(max_examples=200, deadline=None)
    @given(st.lists(st.sampled_from(["a", "b", "..", ".", "c d", "ы"]), min_size=1, max_size=6))
    def test_result_always_stays_inside_base(self, parts):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            result = safe_paths.resolve_path_under_base("/".join(parts), base)

            if ".." in parts:
                assert result is None
            else:
                assert result is not None
                result.relative_to(base)


class TestConfigFilePath:
    def test_existing_yaml_inside_root_is_accepted(self, root):
        cfg = root / "config.yml"
        cfg.write_text("a: 1")

        assert safe_paths.resolve_config_file_path(str(cfg)) == cfg.resolve()

    def test_yaml_extension_variants(self, root):
        for name in ("a.yaml", "b.YML"):
            (root / name).write_text("x: 1")
            assert safe_paths.resolve_config_file_path(name) is not None

    @pytest.mark.parametrize("name", ["config.json", "config.py", "config"])
    def test_other_extensions_are_rejected(self, root, name):
        (root / name).write_text("x")

        assert safe_paths.resolve_config_file_path(name) is None

    def test_missing_file_is_rejected(self, root):
        assert safe_paths.resolve_config_file_path("nope.yml") is None

    def test_directory_named_like_yaml_is_rejected(self, root):
        (root / "dir.yml").mkdir()

        assert safe_paths.resolve_config_file_path("dir.yml") is None

    def test_file_outside_root_is_rejected(self, root, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside") / "c.yml"
        outside.write_text("x: 1")

        assert safe_paths.resolve_config_file_path(str(outside)) is None

    @pytest.mark.parametrize("raw", [None, ""])
    def test_empty_is_rejected(self, root, raw):
        assert safe_paths.resolve_config_file_path(raw) is None


class TestDirectories:
    def test_logs_dir_default_is_under_root(self, root):
        assert safe_paths.default_logs_dir() == root / "logs"
        assert (root / "logs").is_dir()

    def test_logs_dir_env_inside_root_is_used(self, root, monkeypatch):
        monkeypatch.setenv("BACKEND_LOG_DIR", "custom-logs")

        assert safe_paths.default_logs_dir() == (root / "custom-logs").resolve()

    def test_logs_dir_env_outside_root_falls_back_to_default(self, root, monkeypatch):
        monkeypatch.setenv("BACKEND_LOG_DIR", "/etc")

        assert safe_paths.default_logs_dir() == root / "logs"

    def test_logs_dir_env_with_traversal_falls_back(self, root, monkeypatch):
        monkeypatch.setenv("BACKEND_LOG_DIR", "../escape")

        assert safe_paths.default_logs_dir() == root / "logs"

    def test_context_prompts_dir_default(self, root):
        assert safe_paths.default_context_prompts_dir() == root / "memory"

    def test_llm_settings_dir_default(self, root):
        assert safe_paths.default_llm_settings_dir() == root / "memory"

    def test_llm_settings_path_env_is_honoured_inside_root(self, root, monkeypatch):
        monkeypatch.setenv("LLM_SETTINGS_PATH", "memory/custom.json")

        assert safe_paths.resolve_llm_settings_file_path() == (
            root / "memory" / "custom.json"
        ).resolve()

    def test_llm_settings_path_env_outside_root_is_ignored(self, root, monkeypatch):
        monkeypatch.setenv("LLM_SETTINGS_PATH", "/tmp/evil.json")

        assert safe_paths.resolve_llm_settings_file_path() == (
            root / "memory" / "llm_settings.json"
        )

    def test_llm_settings_fallbacks_start_with_primary_and_are_unique(self, root):
        primary = root / "memory" / "llm_settings.json"

        fallbacks = safe_paths.llm_settings_file_fallbacks(primary)

        assert fallbacks[0] == primary.resolve()
        assert len(fallbacks) == len(set(fallbacks))

    def test_llm_settings_seed_paths_unique(self, root):
        seeds = safe_paths.llm_settings_seed_paths()

        assert len(seeds) == len(set(seeds))
        assert any(p.name == "llm_settings.json" for p in seeds)


class TestContextPromptsPaths:
    def test_primary_default(self, root):
        assert safe_paths.resolve_context_prompts_file_path_primary() == (
            root / "memory" / "context_prompts.json"
        )

    def test_primary_env_file_inside_memory_parent(self, root, monkeypatch):
        monkeypatch.setenv("CONTEXT_PROMPTS_FILE", "memory/mine.json")

        assert safe_paths.resolve_context_prompts_file_path_primary() == (
            root / "memory" / "mine.json"
        ).resolve()

    def test_primary_env_with_traversal_is_ignored(self, root, monkeypatch):
        monkeypatch.setenv("CONTEXT_PROMPTS_FILE", "../../etc/x.json")

        assert safe_paths.resolve_context_prompts_file_path_primary() == (
            root / "memory" / "context_prompts.json"
        )

    def test_resolve_rejects_traversal_and_blank(self, root):
        assert safe_paths.resolve_context_prompts_file_path("../x.json") is None
        assert safe_paths.resolve_context_prompts_file_path("") is None
        assert safe_paths.resolve_context_prompts_file_path(None) is None

    def test_seed_paths_unique_and_include_primary(self, root):
        seeds = safe_paths.context_prompts_seed_paths()

        assert len(seeds) == len(set(seeds))
        assert (root / "memory" / "context_prompts.json").resolve() in seeds

    def test_fallbacks_start_with_primary(self, root):
        primary = root / "memory" / "context_prompts.json"

        assert safe_paths.context_prompts_file_fallbacks(primary)[0] == primary.resolve()


class TestLogFilePath:
    def test_file_inside_logs_parent_is_accepted(self, root):
        result = safe_paths.resolve_log_file_path("logs/backend.log")

        assert result == (root / "logs" / "backend.log").resolve()

    def test_traversal_is_rejected(self, root):
        assert safe_paths.resolve_log_file_path("../backend.log") is None

    def test_absolute_path_elsewhere_is_rejected(self, root):
        assert safe_paths.resolve_log_file_path("/var/log/x.log") is None

    @pytest.mark.parametrize("raw", [None, ""])
    def test_empty_is_none(self, root, raw):
        assert safe_paths.resolve_log_file_path(raw) is None


class TestWritable:
    def test_writable_directory(self, tmp_path):
        assert safe_paths.is_writable_path(tmp_path / "f.txt") is True

    def test_creates_missing_parent(self, tmp_path):
        target = tmp_path / "new" / "f.txt"

        assert safe_paths.is_writable_path(target) is True
        assert target.parent.is_dir()

    def test_unwritable_directory(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            import os

            if os.access(locked, os.W_OK):
                pytest.skip("running with privileges that ignore permissions")
            assert safe_paths.is_writable_path(locked / "f.txt") is False
        finally:
            locked.chmod(0o700)

    def test_parent_is_a_file(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")

        assert safe_paths.is_writable_path(blocker / "child.txt") is False


class TestDefaultAppRoot:
    def test_points_to_backend_parent_without_docker_app_dir(self, monkeypatch):
        monkeypatch.setattr(Path, "is_dir", lambda self: False)

        root = safe_paths.default_app_root()

        assert (root / "backend" / "utils" / "safe_paths.py").is_file()
