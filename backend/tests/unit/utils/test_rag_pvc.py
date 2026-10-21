"""storage/rag_pvc: файловое хранилище исходников RAG — пути, запись, удаление, снимок дерева."""

import os
from datetime import datetime

import pytest

from backend.storage import rag_pvc as pvc

pytestmark = pytest.mark.unit


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_PVC_DIR", str(tmp_path / "ragdb"))
    monkeypatch.setenv("RAG_USE_PVC", "true")
    (tmp_path / "ragdb").mkdir()
    return tmp_path / "ragdb"


@pytest.fixture(autouse=True)
def _audit(monkeypatch):
    """CEF-события не нужны — запоминаем, что они были вызваны."""
    calls = []
    for name in (
        "log_rag_pvc_write_success",
        "log_rag_pvc_write_failure",
        "log_rag_pvc_remove_success",
        "log_rag_pvc_remove_failure",
    ):
        monkeypatch.setattr(
            f"backend.settings.cef_logger.storage_audit.{name}",
            lambda *a, _n=name, **k: calls.append((_n, a, k)),
        )
    return calls


class TestSwitches:
    @pytest.mark.parametrize("raw,expected", [(None, False), ("", False), ("true", True), ("1", True), ("YES", True), ("on", True), ("false", False), ("0", False)])
    def test_use_pvc(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("RAG_USE_PVC", raising=False)
        else:
            monkeypatch.setenv("RAG_USE_PVC", raw)
        assert pvc.use_rag_pvc() is expected

    def test_availability_requires_an_existing_writable_directory(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RAG_PVC_DIR", raising=False)
        assert pvc.rag_pvc_available() is False
        monkeypatch.setenv("RAG_PVC_DIR", str(tmp_path / "missing"))
        assert pvc.rag_pvc_available() is False
        assert not (tmp_path / "missing").exists()  # проверка не создаёт каталог
        monkeypatch.setenv("RAG_PVC_DIR", str(tmp_path))
        assert pvc.rag_pvc_available() is True

    def test_read_only_directory_is_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RAG_PVC_DIR", str(tmp_path))
        os.chmod(tmp_path, 0o500)
        try:
            if os.access(tmp_path, os.W_OK):
                pytest.skip("права не ограничивают текущего пользователя")
            assert pvc.rag_pvc_available() is False
        finally:
            os.chmod(tmp_path, 0o700)

    def test_require_is_a_noop_when_pvc_is_not_used(self, monkeypatch):
        monkeypatch.setenv("RAG_USE_PVC", "false")
        monkeypatch.delenv("RAG_PVC_DIR", raising=False)
        pvc.require_rag_pvc_available()

    def test_require_raises_when_pvc_is_used_but_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RAG_USE_PVC", "true")
        monkeypatch.setenv("RAG_PVC_DIR", str(tmp_path / "gone"))
        with pytest.raises(pvc.RagPvcUnavailable, match="Удаление отменено"):
            pvc.require_rag_pvc_available()
        monkeypatch.delenv("RAG_PVC_DIR")
        with pytest.raises(pvc.RagPvcUnavailable, match="<не задан>"):
            pvc.require_rag_pvc_available()

    def test_root_creates_the_directory_and_requires_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RAG_PVC_DIR", raising=False)
        assert pvc.rag_pvc_root() is None and pvc.rag_pvc_root(required=False) is None
        target = tmp_path / "new" / "root"
        monkeypatch.setenv("RAG_PVC_DIR", str(target))
        assert pvc.rag_pvc_root() == str(target) and target.is_dir()

    def test_bucket_marker(self):
        assert pvc.is_rag_pvc_bucket(" __pvc_rag__ ") is True
        assert pvc.is_rag_pvc_bucket("minio-bucket") is False and pvc.is_rag_pvc_bucket(None) is False


class TestPathSafety:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("a/b/c.txt", "a/b/c.txt"),
            ("/a//b/./c.txt", "a/b/c.txt"),
            ("a\\b\\c.txt", "a/b/c.txt"),
            ("../../etc/passwd", "etc/passwd"),
            ("a/../b", "a/b"),
            ("", None),
            ("   ", None),
            ("../..", None),
            (None, None),
        ],
    )
    def test_normalize_object_key(self, raw, expected):
        assert pvc._normalize_object_key(raw) == expected

    def test_traversal_attempts_never_leave_the_root(self, root, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret")
        for attempt in ("../secret.txt", "../../secret.txt", "..\\secret.txt", "a/../../secret.txt", str(secret)):
            path = pvc.rag_pvc_file_path(attempt)
            assert path is None or path.startswith(str(root.resolve()))
            assert path is None or "secret" not in os.path.basename(path)

    def test_returns_existing_files_inside_the_root_only(self, root):
        (root / "memory" / "u").mkdir(parents=True)
        f = root / "memory" / "u" / "a.txt"
        f.write_text("x")
        assert pvc.rag_pvc_file_path("memory/u/a.txt") == str(f.resolve())
        assert pvc.rag_pvc_file_path("memory/u/missing.txt") is None
        assert pvc.rag_pvc_file_path("memory/u") is None  # каталог — не файл
        assert pvc.rag_pvc_file_path("") is None

    def test_symlink_pointing_outside_the_root_is_rejected(self, root, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("x")
        try:
            (root / "link.txt").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink недоступен в этой среде (Windows WinError 1314): {exc}")
        assert pvc.rag_pvc_file_path("link.txt") is None

    def test_no_root_means_no_path(self, monkeypatch):
        monkeypatch.delenv("RAG_PVC_DIR", raising=False)
        assert pvc.rag_pvc_file_path("a/b") is None

    @pytest.mark.parametrize(
        "name,expected",
        [("alice", "alice"), ("  bob  ", "bob"), ("a/../b", "a..b"), ("иван", "anonymous"), ("", "anonymous"), (None, "anonymous"), ("o'neil", "oneil")],
    )
    def test_user_directory_is_sanitised(self, name, expected):
        assert pvc._sanitize_user_dir(name) == expected

    def test_user_with_dots_keeps_them_known_issue(self):
        """Имя из одних точек (``..``) проходит очистку как есть: каталог пользователя становится ``..``."""
        assert pvc._sanitize_user_dir("..") == ".."

    def test_storage_dir_for_dotdot_user_escapes_the_scope_known_issue(self, root):
        """Следствие предыдущего: логин ``..`` даёт каталог ``{root}/{scope}/../yyyy/mm/dd`` —
        файлы окажутся рядом со scope, а не в нём (в пределах корня, но вне каталога вида)."""
        path = pvc.build_rag_pvc_storage_dir("memory", "..")
        assert path is not None
        assert os.path.realpath(path).startswith(str(root.resolve()))
        assert "/memory/" not in os.path.realpath(path).replace(str(root.resolve()), "")


class TestStorageDir:
    def test_layout_and_creation(self, root):
        now = datetime(2026, 3, 7, 12, 0)
        path = pvc.build_rag_pvc_storage_dir("project", "Alice", now=now)
        assert path == str(root / "project" / "Alice" / "2026" / "03" / "07")
        assert os.path.isdir(path)

    def test_unknown_scope_or_no_root(self, root, monkeypatch):
        assert pvc.build_rag_pvc_storage_dir("bogus", "u") is None
        monkeypatch.delenv("RAG_PVC_DIR")
        assert pvc.build_rag_pvc_storage_dir("memory", "u") is None

    def test_date_dir_format(self):
        assert pvc._date_dir(now=datetime(2025, 1, 2)) == "2025/01/02"
        assert pvc._date_dir().count("/") == 2


class TestSave:
    def test_writes_the_file_and_returns_a_relative_key(self, root, _audit):
        key = pvc.save_rag_bytes_to_pvc(b"DATA", "Отчёт.PDF", scope="memory", username="alice", prefix="rag")
        assert key and key.startswith("memory/alice/") and key.endswith(".pdf")
        stored = root / key
        assert stored.read_bytes() == b"DATA"
        assert os.path.basename(key).startswith("rag_")
        assert [c[0] for c in _audit] == ["log_rag_pvc_write_success"]
        assert pvc.rag_pvc_file_path(key) == str(stored.resolve())

    def test_unique_names_per_call(self, root):
        a = pvc.save_rag_bytes_to_pvc(b"x", "a.txt", scope="agent", username="u", prefix="p")
        b = pvc.save_rag_bytes_to_pvc(b"x", "a.txt", scope="agent", username="u", prefix="p")
        assert a != b

    def test_prefix_and_extension_are_sanitised(self, root):
        key = pvc.save_rag_bytes_to_pvc(b"x", "noext", scope="agent", username="u", prefix="../evil name")
        base = os.path.basename(key)
        assert key.endswith(".bin") and base.startswith(".._evil_name_") and " " not in base
        assert key.count("/") == 5  # scope/user/yyyy/mm/dd/<file> — префикс не создал новых уровней
        key2 = pvc.save_rag_bytes_to_pvc(b"x", "a.TXT", scope="agent", username="u", prefix="")
        assert os.path.basename(key2).startswith("rag_") and key2.endswith(".txt")

    def test_empty_content_is_rejected(self, root, _audit):
        assert pvc.save_rag_bytes_to_pvc(b"", "a.txt", scope="memory", username="u", prefix="p") is None
        assert _audit[-1][0] == "log_rag_pvc_write_failure" and "пустой" in _audit[-1][1][1]

    def test_missing_pvc_is_a_failure_not_an_exception(self, monkeypatch, _audit):
        monkeypatch.delenv("RAG_PVC_DIR", raising=False)
        assert pvc.save_rag_bytes_to_pvc(b"x", "a.txt", scope="memory", username="u", prefix="p") is None
        assert _audit[-1][0] == "log_rag_pvc_write_failure"

    def test_write_errors_are_reported(self, root, monkeypatch, _audit):
        real_open = open

        def failing_open(path, mode="r", *a, **k):
            if "wb" in mode:
                raise OSError("disk full")
            return real_open(path, mode, *a, **k)

        monkeypatch.setattr("builtins.open", failing_open)
        assert pvc.save_rag_bytes_to_pvc(b"x", "a.txt", scope="memory", username="u", prefix="p") is None
        assert _audit[-1][0] == "log_rag_pvc_write_failure" and "disk full" in _audit[-1][1][1]

    def test_unknown_scope_fails(self, root):
        assert pvc.save_rag_bytes_to_pvc(b"x", "a.txt", scope="bogus", username="u", prefix="p") is None


class TestDelete:
    def put(self, root, scope="memory"):
        key = pvc.save_rag_bytes_to_pvc(b"x", "a.txt", scope=scope, username="u", prefix="p")
        return key

    def test_removes_the_file(self, root, _audit):
        key = self.put(root)
        pvc.delete_rag_pvc_file(key, pvc.RAG_PVC_BUCKET_MARKER)
        assert not (root / key).exists()
        assert _audit[-1][0] == "log_rag_pvc_remove_success" and _audit[-1][2]["scope"] == "memory"

    def test_other_buckets_are_not_touched(self, root):
        key = self.put(root)
        pvc.delete_rag_pvc_file(key, "some-minio-bucket")
        assert (root / key).exists()

    def test_without_bucket_a_valid_key_is_deleted(self, root):
        key = self.put(root)
        pvc.delete_rag_pvc_file(key)
        assert not (root / key).exists()

    def test_missing_file_is_not_an_error(self, root):
        pvc.delete_rag_pvc_file("memory/u/2026/01/01/nope.txt", pvc.RAG_PVC_BUCKET_MARKER)
        pvc.delete_rag_pvc_file(None)
        pvc.delete_rag_pvc_file("")

    def test_unavailable_volume_refuses_to_delete(self, root, monkeypatch):
        key = self.put(root)
        monkeypatch.setenv("RAG_PVC_DIR", str(root.parent / "unmounted"))
        with pytest.raises(pvc.RagPvcUnavailable):
            pvc.delete_rag_pvc_file(key, pvc.RAG_PVC_BUCKET_MARKER)

    def test_traversal_key_cannot_delete_foreign_files(self, root, tmp_path):
        victim = tmp_path / "victim.txt"
        victim.write_text("keep me")
        pvc.delete_rag_pvc_file("../victim.txt", pvc.RAG_PVC_BUCKET_MARKER)
        assert victim.exists()

    def test_removal_error_is_reported_and_reraised(self, root, monkeypatch, _audit):
        key = self.put(root)
        monkeypatch.setattr(os, "remove", lambda p: (_ for _ in ()).throw(PermissionError("denied")))
        with pytest.raises(PermissionError):
            pvc.delete_rag_pvc_file(key, pvc.RAG_PVC_BUCKET_MARKER)
        assert _audit[-1][0] == "log_rag_pvc_remove_failure" and "denied" in _audit[-1][2]["error"]

    def test_unknown_scope_in_key_is_audited_as_unknown(self, root, _audit):
        (root / "custom").mkdir()
        (root / "custom" / "f.txt").write_text("x")
        pvc.delete_rag_pvc_file("custom/f.txt")
        assert _audit[-1][2]["scope"] == "unknown"


class TestSnapshot:
    def fill(self, root):
        k1 = pvc.save_rag_bytes_to_pvc(b"12345", "a.txt", scope="memory", username="alice", prefix="p")
        k2 = pvc.save_rag_bytes_to_pvc(b"123", "b.txt", scope="project", username="bob", prefix="p")
        return k1, k2

    def test_full_snapshot(self, root):
        k1, k2 = self.fill(root)
        snap = pvc.snapshot_rag_pvc_structure(highlight_object=k1)
        assert snap["file_count"] == 2 and snap["total_bytes"] == 8
        assert [f["name"] for f in snap["files"]] == sorted([k1, k2])
        assert {f["name"]: f["just_added"] for f in snap["files"]} == {k1: True, k2: False}
        assert "memory/" in snap["tree"] and "← new" in snap["tree"] and "(5 B)" in snap["tree"]

    def test_scoped_snapshot_hides_the_scope_prefix_in_the_tree(self, root):
        k1, _ = self.fill(root)
        snap = pvc.snapshot_rag_pvc_structure(scope="memory", highlight_object=k1)
        assert snap["file_count"] == 1 and snap["files"][0]["name"] == k1
        assert "alice/" in snap["tree"] and "project" not in snap["tree"] and "← new" in snap["tree"]

    def test_empty_storage(self, root):
        snap = pvc.snapshot_rag_pvc_structure()
        assert snap["file_count"] == 0 and "(empty)" in snap["tree"]

    def test_bad_scope_and_missing_env(self, root, monkeypatch):
        assert "неизвестный scope" in pvc.snapshot_rag_pvc_structure(scope="bogus")["error"]
        monkeypatch.delenv("RAG_PVC_DIR")
        snap = pvc.snapshot_rag_pvc_structure()
        assert snap["rag_pvc_dir"] is None and "не задан" in snap["error"]

    def test_read_error_is_reported(self, root, monkeypatch):
        def broken(*a, **k):
            raise OSError("io error")

        monkeypatch.setattr(pvc, "_collect_rag_pvc_files", broken)
        snap = pvc.snapshot_rag_pvc_structure()
        assert snap["error"] == "io error" and "ошибка чтения" in snap["tree"]

    def test_logging_helper_does_not_raise(self, root):
        self.fill(root)
        pvc.log_rag_pvc_structure(filename="a.txt", scope="memory")

    def test_nested_tree_with_file_shadowing_a_directory(self):
        files = [{"name": "a", "size": 1, "modified": None, "just_added": False}, {"name": "a/b.txt", "size": 2, "modified": None, "just_added": True}]
        lines = pvc._render_rag_pvc_tree_lines(files)
        text = "\n".join(lines)
        assert "a/" in text and "b.txt (2 B)  ← new" in text and "a (1 B)" in text

    def test_empty_tree_lines(self):
        assert pvc._render_rag_pvc_tree_lines([]) == ["  (empty)"]
