"""routes/documents: удалённый global store, защита путей inline-вложений, выдача файла превью."""

import os
from types import SimpleNamespace

import pytest

from backend.routes import documents as module

pytestmark = pytest.mark.unit

BASE = "/api/documents"
POD = module.INLINE_ATTACH_POD_TMP_BUCKET


@pytest.fixture
def attach_dir(tmp_path, monkeypatch):
    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setenv(module.INLINE_ATTACH_DIR_ENV, str(root))
    return root


def _write(root, rel, data=b"data"):
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class TestRemovedGlobalStore:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/upload"),
            ("POST", "/query"),
            ("DELETE", "/some-file.pdf"),
            ("GET", "/report/generate"),
            ("GET", "/report/download"),
            ("POST", "/extract"),
        ],
    )
    async def test_endpoint_is_gone(self, make_client, method, path):
        client = make_client(module.router, user=None)
        kwargs = {}
        if path == "/upload" or path == "/extract":
            kwargs["files"] = {"file": ("a.txt", b"x", "text/plain")}
        elif path == "/query":
            kwargs["json"] = {"query": "q"}

        response = await client.request(method, BASE + path, **kwargs)

        assert response.status_code == 410

    @pytest.mark.parametrize("path", ["", "/"])
    async def test_listing_is_always_empty_and_marked_deprecated(self, make_client, path):
        response = await make_client(module.router, user=None).get(BASE + path, follow_redirects=True)

        body = response.json()
        assert response.status_code == 200
        assert (body["documents"], body["count"], body["deprecated"]) == ([], 0, True)

    async def test_gone_endpoints_never_touch_storage(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "minio_client", SimpleNamespace(), raising=False)

        response = await make_client(module.router, user=None).delete(BASE + "/../../etc/passwd")

        assert response.status_code in (404, 410)


class TestContentTypeHelpers:
    @pytest.mark.parametrize(
        ("filename", "content_type", "expected"),
        [
            ("a.pdf", "", "application/pdf"),
            ("a.PDF", "", "application/pdf"),
            ("a.xyz", "", "application/octet-stream"),
            ("a", "", "application/octet-stream"),
            ("a.pdf", "Application/X-Custom", "application/x-custom"),
        ],
    )
    def test_resolve_content_type(self, filename, content_type, expected):
        assert module._resolve_content_type(filename, content_type) == expected

    @pytest.mark.parametrize(
        ("filename", "content_type", "kind"),
        [
            ("a.png", "", "image"),
            ("noext", "image/webp", "image"),
            ("a.docx", "", "document"),
            ("a.LOG", "", "document"),
            ("noext", "application/pdf", "document"),
            ("noext", "text/rtf", "document"),
            ("a.exe", "application/octet-stream", "unsupported"),
            ("a.zip", "", "unsupported"),
            ("", "", "unsupported"),
        ],
    )
    def test_detect_attachment_kind(self, filename, content_type, kind):
        assert module._detect_inline_attachment_kind(filename, content_type) == kind

    def test_unsupported_extension_message_lists_the_allowed_ones(self):
        message = module._unsupported_inline_attach_extension_detail("virus.EXE")

        assert message.startswith(".exe файлы не поддерживаются")
        assert ".pdf" in message and ".docx" in message

    def test_message_for_files_without_extension(self):
        assert module._unsupported_inline_attach_extension_detail("README").startswith("файлы без расширения")

    def test_supported_extension_label_is_sorted_and_complete(self):
        label = module._inline_attach_supported_extensions_label()

        assert label.split(", ") == sorted(label.split(", "))
        assert {".pdf", ".png", ".txt"} <= set(label.split(", "))

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Это нормальный русский текст, в котором достаточно букв для проверки.", True),
            ("This is a perfectly normal english text layer extracted from a PDF.", True),
            ("1234567890 " * 10, False),
            ("!!!???...,,,;;;---" * 5, False),
            ("коротко", False),
            ("", False),
        ],
    )
    def test_text_layer_heuristic(self, text, expected):
        assert module._looks_like_real_text(text) is expected

    def test_text_layer_thresholds_are_configurable(self):
        assert module._looks_like_real_text("abc", min_chars=3, min_letter_ratio=0.5) is True


class TestPodPaths:
    @pytest.mark.parametrize(
        ("login", "expected"),
        [
            ("alice", "alice"),
            ("a.b-c_d", "a.b-c_d"),
            ("../../etc", "....etc"),
            ("a/b", "ab"),
            ("иван", "anonymous"),
            ("", "anonymous"),
            (None, "anonymous"),
            ("   ", "anonymous"),
        ],
    )
    def test_user_directory_name_is_safe(self, login, expected):
        assert module._sanitize_inline_attach_user_dir(login) == expected

    def test_sanitized_name_never_contains_a_separator(self):
        for login in ["a/b", "..\\x", "a\x00b", "a b", "a;rm -rf /"]:
            name = module._sanitize_inline_attach_user_dir(login)
            assert "/" not in name and "\\" not in name and " " not in name

    def test_date_directory(self):
        from datetime import datetime

        assert module._inline_attach_date_dir(now=datetime(2026, 3, 7)) == "2026/03/07"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("alice/2026/01/01/f.png", "alice/2026/01/01/f.png"),
            ("/alice//2026/f.png", "alice/2026/f.png"),
            ("alice\\2026\\f.png", "alice/2026/f.png"),
            ("../../etc/passwd", "etc/passwd"),
            ("a/./b/../c", "a/b/c"),
            ("..", None),
            ("", None),
            (None, None),
            ("   ", None),
        ],
    )
    def test_object_key_has_no_traversal_components(self, raw, expected):
        assert module._normalize_inline_attach_object_key(raw) == expected

    def test_dir_is_required_from_the_environment(self, monkeypatch):
        monkeypatch.delenv(module.INLINE_ATTACH_DIR_ENV, raising=False)

        assert module._inline_attach_pod_dir() is None
        assert module._build_inline_attach_storage_dir("alice") is None

    def test_storage_dir_is_per_user_and_date(self, attach_dir):
        from datetime import datetime

        path = module._build_inline_attach_storage_dir("alice", now=datetime(2026, 1, 2))

        assert path == str(attach_dir / "alice" / "2026" / "01" / "02")
        assert os.path.isdir(path)


def _make_symlink(link, target, is_dir=False):
    """Создаёт симлинк; пропускает тест, если у ОС нет привилегии (Windows WinError 1314)."""
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink недоступен в этой среде: {exc}")


class TestPodFileResolution:
    def test_existing_file(self, attach_dir):
        _write(attach_dir, "alice/2026/01/01/f.png")

        assert module._inline_attach_pod_file_path("alice/2026/01/01/f.png") == str(
            (attach_dir / "alice" / "2026" / "01" / "01" / "f.png").resolve()
        )

    def test_missing_file_and_directories(self, attach_dir):
        _write(attach_dir, "alice/f.png")

        assert module._inline_attach_pod_file_path("alice/ghost.png") is None
        assert module._inline_attach_pod_file_path("alice") is None

    @pytest.mark.parametrize("key", ["../outside.txt", "../../etc/passwd", "alice/../../outside.txt", "/etc/passwd", "..\\outside.txt"])
    def test_traversal_cannot_leave_the_attachments_directory(self, attach_dir, key):
        (attach_dir.parent / "outside.txt").write_text("secret")

        assert module._inline_attach_pod_file_path(key) is None

    def test_symlink_pointing_outside_is_refused(self, attach_dir, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret")
        _make_symlink(attach_dir / "link.txt", secret)

        assert module._inline_attach_pod_file_path("link.txt") is None

    def test_directory_symlink_pointing_outside_is_refused(self, attach_dir, tmp_path):
        outside = tmp_path / "other"
        outside.mkdir()
        (outside / "f.txt").write_text("x")
        _make_symlink(attach_dir / "alice", outside, is_dir=True)

        assert module._inline_attach_pod_file_path("alice/f.txt") is None

    def test_no_directory_configured(self, monkeypatch):
        monkeypatch.delenv(module.INLINE_ATTACH_DIR_ENV, raising=False)

        assert module._inline_attach_pod_file_path("a/b.txt") is None


class TestSaveAndDelete:
    def test_saved_file_gets_a_random_name_and_a_relative_key(self, attach_dir):
        key = module._try_save_bytes_to_pod_attachments(b"%PDF", "Отчёт.PDF", "application/pdf", username="alice")

        assert key.startswith("alice/") and key.endswith(".pdf")
        assert os.path.basename(key).startswith("doc_")
        assert (attach_dir / key).read_bytes() == b"%PDF"
        assert "Отчёт" not in key

    def test_images_get_an_image_prefix_and_names_do_not_repeat(self, attach_dir):
        first = module._try_save_bytes_to_pod_attachments(b"a", "p.png", "image/png", username="alice")
        second = module._try_save_bytes_to_pod_attachments(b"a", "p.png", "image/png", username="alice")

        assert os.path.basename(first).startswith("img_") and first != second

    def test_original_filename_cannot_influence_the_location(self, attach_dir):
        key = module._try_save_bytes_to_pod_attachments(b"x", "../../evil.txt", "text/plain", username="../../bob")

        assert key.startswith("....bob/") or key.startswith("bob/") or ".." not in key.split("/")
        assert (attach_dir / key).is_file()
        assert attach_dir in (attach_dir / key).resolve().parents

    def test_returns_none_without_a_configured_directory(self, monkeypatch):
        monkeypatch.delenv(module.INLINE_ATTACH_DIR_ENV, raising=False)

        assert module._try_save_bytes_to_pod_attachments(b"x", "a.txt", "text/plain", username="alice") is None

    def test_delete_removes_the_file_only_inside_the_directory(self, attach_dir):
        path = _write(attach_dir, "alice/f.txt")
        outside = attach_dir.parent / "keep.txt"
        outside.write_text("keep")

        module._delete_pod_attach("alice/f.txt")
        module._delete_pod_attach("../keep.txt")
        module._delete_pod_attach("ghost.txt")

        assert not path.exists()
        assert outside.read_text() == "keep"


class TestReadAttachmentBytes:
    def test_pod_file_with_a_mime_from_the_extension(self, attach_dir):
        _write(attach_dir, "alice/f.png", b"PNG")

        assert module.read_inline_attachment_bytes(POD, "alice/f.png") == (b"PNG", "image/png")

    def test_unknown_extension_is_octet_stream(self, attach_dir):
        _write(attach_dir, "alice/f.weird", b"x")

        assert module.read_inline_attachment_bytes(POD, "alice/f.weird")[1] == "application/octet-stream"

    @pytest.mark.parametrize(("bucket", "obj"), [("", "x"), ("b", ""), ("  ", "  ")])
    def test_blank_arguments(self, bucket, obj):
        assert module.read_inline_attachment_bytes(bucket, obj) is None

    def test_missing_pod_file(self, attach_dir):
        assert module.read_inline_attachment_bytes(POD, "alice/ghost.png") is None

    def test_other_buckets_go_to_minio(self, monkeypatch):
        calls = []

        class Minio:
            def download_file(self, name, bucket_name=None):
                calls.append((name, bucket_name))
                return b"minio-bytes"

        monkeypatch.setattr(module, "minio_client", Minio())

        assert module.read_inline_attachment_bytes("docs", "dir/file.pdf") == (b"minio-bytes", "application/pdf")
        assert calls == [("dir/file.pdf", "docs")]

    def test_minio_failures_are_swallowed(self, monkeypatch):
        class Minio:
            def download_file(self, name, bucket_name=None):
                raise RuntimeError("minio down")

        monkeypatch.setattr(module, "minio_client", Minio())

        assert module.read_inline_attachment_bytes("docs", "a.pdf") is None

    def test_no_minio_configured(self, monkeypatch):
        monkeypatch.setattr(module, "minio_client", None)

        assert module.read_inline_attachment_bytes("docs", "a.pdf") is None


class TestInlineFileEndpoint:
    URL = BASE + "/inline-file"

    async def test_requires_login(self, make_client, attach_dir):
        _write(attach_dir, "alice/f.png")

        response = await make_client(module.router, user=None).get(self.URL, params={"bucket": POD, "object": "alice/f.png"})

        assert response.status_code in (401, 403)

    async def test_serves_a_pod_file(self, make_client, attach_dir):
        _write(attach_dir, "alice/2026/01/01/img_1.png", b"\x89PNG")

        response = await make_client(module.router).get(self.URL, params={"bucket": POD, "object": "alice/2026/01/01/img_1.png"})

        assert response.status_code == 200
        assert response.content == b"\x89PNG"
        assert response.headers["content-type"] == "image/png"

    async def test_missing_file(self, make_client, attach_dir):
        response = await make_client(module.router).get(self.URL, params={"bucket": POD, "object": "alice/ghost.png"})

        assert response.status_code == 404

    @pytest.mark.parametrize("obj", ["../outside.txt", "../../etc/passwd", "%2e%2e/%2e%2e/etc/passwd"])
    async def test_traversal_attempts_do_not_leak_files(self, make_client, attach_dir, obj):
        (attach_dir.parent / "outside.txt").write_text("secret")

        response = await make_client(module.router).get(self.URL, params={"bucket": POD, "object": obj})

        assert response.status_code in (403, 404)
        assert "secret" not in response.text

    @pytest.mark.parametrize("params", [{}, {"bucket": POD}, {"object": "x"}, {"bucket": "", "object": "x"}])
    async def test_both_parameters_are_required(self, make_client, params):
        assert (await make_client(module.router).get(self.URL, params=params)).status_code == 422

    async def test_minio_bucket_requires_minio(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "minio_client", None)

        response = await make_client(module.router).get(self.URL, params={"bucket": "docs", "object": "a.pdf"})

        assert response.status_code == 503

    async def test_other_users_cannot_read_a_pod_attachment(self, make_client, attach_dir):
        _write(attach_dir, "alice/2026/01/01/doc_secret.txt", b"alice's private data")
        bob = make_client(module.router, user={"username": "bob", "user_id": "bob"})

        response = await bob.get(self.URL, params={"bucket": POD, "object": "alice/2026/01/01/doc_secret.txt"})

        assert response.status_code == 403
        assert "private" not in response.text

    async def test_admin_can_read_any_pod_attachment(self, make_client, attach_dir):
        _write(attach_dir, "alice/2026/01/01/doc.txt", b"data")
        admin = make_client(module.router, user={"username": "root", "user_id": "root", "is_admin": True})

        response = await admin.get(self.URL, params={"bucket": POD, "object": "alice/2026/01/01/doc.txt"})

        assert response.status_code == 200 and response.content == b"data"

    async def test_user_prefix_must_match_exactly(self, make_client, attach_dir):
        _write(attach_dir, "alice2/doc.txt", b"other")

        response = await make_client(module.router).get(self.URL, params={"bucket": POD, "object": "alice2/doc.txt"})

        assert response.status_code == 403

    async def test_the_bucket_name_is_not_taken_from_the_request(self, make_client, monkeypatch):
        seen = []

        class Minio:
            def download_file(self, name, bucket_name=None):
                seen.append(bucket_name)
                return b"internal"

        monkeypatch.setattr(module, "minio_client", Minio())
        monkeypatch.setattr(module, "_documents_bucket_name", lambda: "docs")

        response = await make_client(module.router).get(self.URL, params={"bucket": "rag-private-bucket", "object": "x.txt"})

        assert response.status_code == 403
        assert seen == []

    async def test_the_configured_documents_bucket_is_allowed(self, make_client, monkeypatch):
        class Minio:
            def download_file(self, name, bucket_name=None):
                return b"pdf-bytes" if bucket_name == "docs" else None

        monkeypatch.setattr(module, "minio_client", Minio())
        monkeypatch.setattr(module, "_documents_bucket_name", lambda: "docs")

        response = await make_client(module.router).get(self.URL, params={"bucket": "docs", "object": "doc_1.pdf"})

        assert response.status_code == 200 and response.content == b"pdf-bytes"
