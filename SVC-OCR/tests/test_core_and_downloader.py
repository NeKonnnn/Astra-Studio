# -*- coding: utf-8 -*-
"""Юнит-тесты общих модулей model-сервисов: core.security и utils.downloader.

Файл одинаков во всех SVC-OCR / SVC-SPEECH-* (модули там побайтно совпадают).
Каждый сервис запускается отдельно из своей директории: у каждого свой пакет ``app``.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.core import security
from app.utils import downloader


@pytest.fixture
def sec(monkeypatch):
    """Управляемые настройки безопасности вместо реального config.yml."""
    cfg = SimpleNamespace(security=SimpleNamespace(enabled=True, api_key="secret"))
    monkeypatch.setattr(security, "settings", cfg)
    return cfg.security


class TestVerifyApiKey:
    async def test_disabled_allows_everything(self, sec):
        sec.enabled = False
        assert await security.verify_api_key(None) is True

    async def test_valid_key(self, sec):
        assert await security.verify_api_key("secret") is True

    @pytest.mark.parametrize(
        "key, detail",
        [(None, "API key is missing"), ("", "API key is missing"), ("wrong", "Invalid API key")],
    )
    async def test_rejected(self, sec, key, detail):
        with pytest.raises(HTTPException) as exc:
            await security.verify_api_key(key)
        assert exc.value.status_code == 401
        assert exc.value.detail == detail
        assert exc.value.headers == {"WWW-Authenticate": "API-Key"}

    @pytest.mark.parametrize("configured", [None, ""])
    async def test_unconfigured_server_key(self, sec, configured):
        sec.api_key = configured
        with pytest.raises(HTTPException) as exc:
            await security.verify_api_key("anything")
        assert exc.value.detail == "API key is not configured on server"

    async def test_comparison_is_exact(self, sec):
        with pytest.raises(HTTPException):
            await security.verify_api_key("secret ")
        with pytest.raises(HTTPException):
            await security.verify_api_key("SECRET")


class TestPathKinds:
    @pytest.mark.parametrize(
        "path, expected",
        [
            ("s3://b/k", True),
            ("gs://b/k", True),
            ("https://x/y", True),
            ("http://x/y", True),
            ("/local/file", False),
            ("relative/file.bin", False),
            ("ftp://x/y", False),
        ],
    )
    def test_is_cloud_path(self, path, expected):
        assert downloader.is_cloud_path(path) is expected

    def test_is_local_path(self, tmp_path):
        existing = tmp_path / "f.bin"
        existing.write_bytes(b"1")
        assert downloader.is_local_path(str(existing)) is True
        assert downloader.is_local_path("relative/missing.bin") is True
        assert downloader.is_local_path("https://x/y") is False


class FakeResponse:
    def __init__(self, chunks=(b"abc", b"def"), headers=None, error=None):
        self._chunks = chunks
        self.headers = headers or {}
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def iter_content(self, chunk_size=8192):
        yield from self._chunks


class TestDownloadFromHttp:
    def test_success_writes_file_and_creates_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(downloader.requests, "get", lambda *a, **k: FakeResponse())
        dest = tmp_path / "nested" / "dir" / "model.bin"
        assert downloader.download_from_http("http://x/m.bin", str(dest)) is True
        assert dest.read_bytes() == b"abcdef"

    def test_empty_chunks_are_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            downloader.requests, "get", lambda *a, **k: FakeResponse(chunks=(b"a", b"", b"b"))
        )
        dest = tmp_path / "m.bin"
        assert downloader.download_from_http("http://x", str(dest)) is True
        assert dest.read_bytes() == b"ab"

    def test_http_error_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            downloader.requests, "get", lambda *a, **k: FakeResponse(error=RuntimeError("404"))
        )
        assert downloader.download_from_http("http://x", str(tmp_path / "m.bin")) is False

    def test_network_error_returns_false(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise ConnectionError("down")

        monkeypatch.setattr(downloader.requests, "get", boom)
        assert downloader.download_from_http("http://x", str(tmp_path / "m.bin")) is False

    def test_request_uses_streaming_and_timeout(self, tmp_path, monkeypatch):
        seen = {}

        def fake_get(url, **kwargs):
            seen.update(url=url, **kwargs)
            return FakeResponse()

        monkeypatch.setattr(downloader.requests, "get", fake_get)
        downloader.download_from_http("http://x/m", str(tmp_path / "m"))
        assert seen["url"] == "http://x/m"
        assert seen["stream"] is True
        assert seen["timeout"] > 0


def _install_fake_boto3(monkeypatch, client):
    boto3 = ModuleType("boto3")
    boto3.client = MagicMock(return_value=client)
    botocore = ModuleType("botocore")
    botocore_exc = ModuleType("botocore.exceptions")
    botocore_exc.ClientError = Exception
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", botocore_exc)


class TestDownloadFromS3:
    def test_missing_boto3_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "boto3", None)  # import boto3 -> ImportError
        assert downloader.download_from_s3("s3://b/k", str(tmp_path / "m")) is False

    def test_success_parses_bucket_and_key(self, tmp_path, monkeypatch):
        client = MagicMock()
        _install_fake_boto3(monkeypatch, client)
        dest = tmp_path / "sub" / "m.bin"
        assert downloader.download_from_s3("s3://my-bucket/path/to/m.bin", str(dest)) is True
        client.download_file.assert_called_once_with("my-bucket", "path/to/m.bin", str(dest))
        assert dest.parent.is_dir()

    def test_client_failure_returns_false(self, tmp_path, monkeypatch):
        client = MagicMock()
        client.download_file.side_effect = RuntimeError("denied")
        _install_fake_boto3(monkeypatch, client)
        assert downloader.download_from_s3("s3://b/k", str(tmp_path / "m")) is False


class TestDownloadFromGcs:
    def test_missing_library_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "google.cloud", None)
        assert downloader.download_from_gcs("gs://b/k", str(tmp_path / "m")) is False

    def test_success_parses_bucket_and_blob(self, tmp_path, monkeypatch):
        blob = MagicMock()
        bucket = MagicMock()
        bucket.blob.return_value = blob
        gclient = MagicMock()
        gclient.bucket.return_value = bucket
        storage = ModuleType("google.cloud.storage")
        storage.Client = MagicMock(return_value=gclient)
        cloud = ModuleType("google.cloud")
        cloud.storage = storage
        google = ModuleType("google")
        google.cloud = cloud
        monkeypatch.setitem(sys.modules, "google", google)
        monkeypatch.setitem(sys.modules, "google.cloud", cloud)
        monkeypatch.setitem(sys.modules, "google.cloud.storage", storage)

        dest = tmp_path / "m.bin"
        assert downloader.download_from_gcs("gs://bkt/dir/m.bin", str(dest)) is True
        gclient.bucket.assert_called_once_with("bkt")
        bucket.blob.assert_called_once_with("dir/m.bin")
        blob.download_to_filename.assert_called_once_with(str(dest))


class TestDownloadModel:
    def test_existing_destination_short_circuits(self, tmp_path, monkeypatch):
        dest = tmp_path / "m.bin"
        dest.write_bytes(b"x")
        monkeypatch.setattr(downloader, "download_from_http", MagicMock(side_effect=AssertionError))
        assert downloader.download_model("http://x/m", str(dest)) is True

    @pytest.mark.parametrize(
        "source, target",
        [
            ("s3://b/k", "download_from_s3"),
            ("gs://b/k", "download_from_gcs"),
            ("http://x/m", "download_from_http"),
            ("https://x/m", "download_from_http"),
        ],
    )
    def test_dispatch_by_scheme(self, tmp_path, monkeypatch, source, target):
        calls = {}

        def make(name):
            def fake(src, dst):
                calls[name] = (src, dst)
                return True

            return fake

        for name in ("download_from_s3", "download_from_gcs", "download_from_http"):
            monkeypatch.setattr(downloader, name, make(name))
        dest = str(tmp_path / "m.bin")
        assert downloader.download_model(source, dest) is True
        assert list(calls) == [target]
        assert calls[target] == (source, dest)

    def test_dispatch_propagates_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(downloader, "download_from_http", lambda *a: False)
        assert downloader.download_model("http://x", str(tmp_path / "m")) is False

    def test_local_source_is_copied(self, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"payload")
        dest = tmp_path / "out" / "dst.bin"
        assert downloader.download_model(str(src), str(dest)) is True
        assert dest.read_bytes() == b"payload"
        assert src.exists()

    def test_missing_local_source(self, tmp_path):
        assert downloader.download_model(str(tmp_path / "none"), str(tmp_path / "dst")) is False


class TestEnsureModelExists:
    def test_existing_destination(self, tmp_path):
        dest = tmp_path / "m.bin"
        dest.write_bytes(b"x")
        assert downloader.ensure_model_exists("s3://b/k", str(dest)) is True

    def test_cloud_source_triggers_download(self, tmp_path, monkeypatch):
        called = MagicMock(return_value=True)
        monkeypatch.setattr(downloader, "download_model", called)
        dest = str(tmp_path / "m.bin")
        assert downloader.ensure_model_exists("https://x/m", dest) is True
        called.assert_called_once_with("https://x/m", dest)

    def test_local_source_is_copied(self, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"1")
        dest = tmp_path / "dst.bin"
        assert downloader.ensure_model_exists(str(src), str(dest)) is True
        assert dest.read_bytes() == b"1"

    def test_nothing_available(self, tmp_path):
        assert downloader.ensure_model_exists(str(tmp_path / "none"), str(tmp_path / "dst")) is False
