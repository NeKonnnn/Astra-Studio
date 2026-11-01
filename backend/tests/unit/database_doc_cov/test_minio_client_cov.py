"""MinIO-клиент: загрузка, скачивание, удаление, presigned URL, бакеты и ошибки S3Error — с фейковым Minio."""

import importlib.util
import os
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest
from minio.error import S3Error

from backend.database.minio import minio_client as mc
from backend.settings.cef_logger import storage_audit


def s3err(code: str, message: str = "boom") -> S3Error:
    # minio>=7.2: первый аргумент — response, не code
    return S3Error(None, code, message, "res", "req", "host")


class FakeResponse:
    def __init__(self, data: bytes):
        self.data = data
        self.closed = False
        self.released = False

    def read(self):
        return self.data

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class FakeMinio:
    """Фейк класса minio.Minio: хранит объекты в словаре и запоминает вызовы."""

    instances: list = []
    exists_result = True
    exists_error: Exception | None = None
    make_bucket_error: Exception | None = None

    def __init__(self, endpoint, **kwargs):
        self.endpoint = endpoint
        self.kwargs = kwargs
        self.objects: dict = {}
        self.made: list = []
        self.error: Exception | None = None
        FakeMinio.instances.append(self)

    def bucket_exists(self, bucket):
        if FakeMinio.exists_error:
            raise FakeMinio.exists_error
        return FakeMinio.exists_result

    def make_bucket(self, bucket):
        if FakeMinio.make_bucket_error:
            raise FakeMinio.make_bucket_error
        self.made.append(bucket)

    def put_object(self, bucket, name, stream, size, content_type=None):
        if self.error:
            raise self.error
        self.objects[(bucket, name)] = (stream.read(), size, content_type)

    def get_object(self, bucket, name):
        if self.error:
            raise self.error
        return FakeResponse(self.objects[(bucket, name)][0])

    def remove_object(self, bucket, name):
        if self.error:
            raise self.error
        self.objects.pop((bucket, name), None)

    def stat_object(self, bucket, name):
        if self.error:
            raise self.error
        if (bucket, name) not in self.objects:
            raise s3err("NoSuchKey")
        return object()

    def presigned_get_object(self, bucket, name, expires=None):
        if self.error:
            raise self.error
        return f"https://minio/{bucket}/{name}?exp={int(expires.total_seconds())}"


def make_settings(**overrides):
    cfg = dict(
        endpoint="minio",
        port=9000,
        access_key="ACCESSKEY",
        secret_key="secret",
        use_ssl=False,
        bucket_name="main",
        ssl_verify=True,
        ca_cert_path=None,
        documents_bucket_name="docs",
    )
    cfg.update(overrides)
    return SimpleNamespace(minio=SimpleNamespace(**cfg))


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    FakeMinio.instances = []
    FakeMinio.exists_result = True
    FakeMinio.exists_error = None
    FakeMinio.make_bucket_error = None
    monkeypatch.setattr(mc, "Minio", FakeMinio)
    monkeypatch.setattr(mc, "get_settings", lambda: make_settings())
    monkeypatch.delenv("MINIO_AUTO_CREATE_BUCKETS", raising=False)
    monkeypatch.setattr(mc, "_minio_client", None)


@pytest.fixture
def audit(monkeypatch):
    """Подмена CEF-аудита хранилища: фиксируем, какие события отправлены."""
    calls: list = []
    for name in (
        "log_minio_write_success",
        "log_minio_write_failure",
        "log_minio_read_success",
        "log_minio_read_failure",
        "log_minio_remove_success",
        "log_minio_remove_failure",
    ):
        monkeypatch.setattr(
            storage_audit, name, lambda *a, _n=name, **k: calls.append((_n, a, k))
        )
    return calls


@pytest.fixture
def client():
    return mc.MinIOClient()


class TestHttpClientBuilder:
    def test_plain_http_has_no_custom_client(self):
        assert mc._build_minio_http_client(secure=False, ssl_verify=True, ca_cert_path=None) is None

    def test_verify_disabled_gives_pool_without_cert_check(self):
        pool = mc._build_minio_http_client(secure=True, ssl_verify=False, ca_cert_path=None)
        assert pool.connection_pool_kw["cert_reqs"] == "CERT_NONE"
        assert pool.connection_pool_kw["maxsize"] == 10

    def test_existing_ca_bundle_is_used(self, tmp_path):
        ca = tmp_path / "ca.pem"
        ca.write_text("x")
        pool = mc._build_minio_http_client(secure=True, ssl_verify=True, ca_cert_path=str(ca))
        assert pool.connection_pool_kw["cert_reqs"] == "CERT_REQUIRED"
        assert pool.connection_pool_kw["ca_certs"] == str(ca)

    def test_missing_ca_bundle_falls_back_to_system_store(self, tmp_path):
        assert mc._build_minio_http_client(
            secure=True, ssl_verify=True, ca_cert_path=str(tmp_path / "none.pem")
        ) is None

    def test_secure_without_ca_uses_system_store(self):
        assert mc._build_minio_http_client(secure=True, ssl_verify=True, ca_cert_path=None) is None


class TestInit:
    def test_defaults_come_from_settings(self, client):
        assert client.endpoint == "minio:9000"
        assert (client.bucket_name, client.access_key, client.secure) == ("main", "ACCESSKEY", False)
        fake = FakeMinio.instances[0]
        assert fake.endpoint == "minio:9000"
        assert fake.kwargs == {"access_key": "ACCESSKEY", "secret_key": "secret", "secure": False}

    def test_endpoint_with_port_is_kept_and_explicit_args_win(self):
        c = mc.MinIOClient(
            endpoint="host:1234", port=1, access_key="K", secret_key="S", secure=False, bucket_name="b"
        )
        assert (c.endpoint, c.bucket_name, c.access_key, c.secret_key) == ("host:1234", "b", "K", "S")

    def test_explicit_port_zero_is_respected(self):
        assert mc.MinIOClient(port=0).endpoint == "minio:0"

    def test_secure_with_ca_passes_http_client_and_cert_check(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"
        ca.write_text("x")
        monkeypatch.setattr(
            mc, "get_settings", lambda: make_settings(use_ssl=True, ca_cert_path=str(ca), bucket_name="docs")
        )
        c = mc.MinIOClient()
        kwargs = FakeMinio.instances[0].kwargs
        assert c.secure is True
        assert kwargs["cert_check"] is True and kwargs["secure"] is True
        assert kwargs["http_client"].connection_pool_kw["ca_certs"] == str(ca)

    def test_secure_without_verification(self, monkeypatch):
        monkeypatch.setattr(mc, "get_settings", lambda: make_settings(use_ssl=True, ssl_verify=False))
        mc.MinIOClient()
        kwargs = FakeMinio.instances[0].kwargs
        assert kwargs["cert_check"] is False
        assert kwargs["http_client"].connection_pool_kw["cert_reqs"] == "CERT_NONE"

    def test_settings_without_optional_fields(self, monkeypatch):
        cfg = SimpleNamespace(
            endpoint="m", port=9000, access_key="AKEY1", secret_key="s", use_ssl=False, bucket_name="b"
        )
        monkeypatch.setattr(mc, "get_settings", lambda: SimpleNamespace(minio=cfg))
        c = mc.MinIOClient()
        assert c.ssl_verify is True and c.ca_cert_path is None

    def test_init_failure_is_reraised(self, monkeypatch):
        FakeMinio.exists_error = s3err("InternalError")
        with pytest.raises(S3Error):
            mc.MinIOClient()

    def test_library_missing_is_reported(self, monkeypatch):
        monkeypatch.setattr(mc, "MINIO_AVAILABLE", False)
        with pytest.raises(ImportError, match="MinIO библиотека не установлена"):
            mc.MinIOClient()

    def test_settings_module_missing_is_reported(self, monkeypatch):
        monkeypatch.setattr(mc, "SETTINGS_AVAILABLE", False)
        with pytest.raises(RuntimeError, match="settings недоступен"):
            mc.MinIOClient()


class TestBuckets:
    def test_existing_bucket_is_left_alone(self, client):
        client.ensure_bucket("other")
        assert client.client.made == []

    def test_missing_bucket_is_not_created_by_default(self, client):
        FakeMinio.exists_result = False
        client.ensure_bucket("other")
        assert client.client.made == []

    @pytest.mark.parametrize("flag", ["true", "1", "YES", " True "])
    def test_missing_bucket_is_created_when_enabled(self, client, monkeypatch, flag):
        monkeypatch.setenv("MINIO_AUTO_CREATE_BUCKETS", flag)
        FakeMinio.exists_result = False
        client.ensure_bucket("other")
        assert client.client.made == ["other"]

    def test_default_bucket_used_when_name_omitted(self, client, monkeypatch):
        monkeypatch.setenv("MINIO_AUTO_CREATE_BUCKETS", "true")
        FakeMinio.exists_result = False
        client._ensure_bucket_exists()
        assert client.client.made == ["main"]

    @pytest.mark.parametrize("code", ["AccessDenied", "AccessDeniedException"])
    def test_head_access_denied_is_tolerated(self, client, code):
        FakeMinio.exists_error = s3err(code)
        client.ensure_bucket("b")

    def test_head_other_error_is_raised(self, client):
        FakeMinio.exists_error = s3err("InternalError")
        with pytest.raises(S3Error):
            client.ensure_bucket("b")

    @pytest.mark.parametrize("code", ["AccessDenied", "AccessDeniedException", "BucketAlreadyOwnedByYou"])
    def test_create_race_or_denied_is_tolerated(self, client, monkeypatch, code):
        monkeypatch.setenv("MINIO_AUTO_CREATE_BUCKETS", "true")
        FakeMinio.exists_result = False
        FakeMinio.make_bucket_error = s3err(code)
        client.ensure_bucket("b")

    def test_create_other_error_is_raised(self, client, monkeypatch):
        monkeypatch.setenv("MINIO_AUTO_CREATE_BUCKETS", "true")
        FakeMinio.exists_result = False
        FakeMinio.make_bucket_error = s3err("InternalError")
        with pytest.raises(S3Error):
            client.ensure_bucket("b")


class TestUpload:
    def test_upload_stores_bytes_and_audits(self, client, audit):
        name = client.upload_file(b"abc", "dir/a.bin", "text/plain", cef_display_name="a.bin")
        assert name == "dir/a.bin"
        assert client.client.objects[("main", "dir/a.bin")] == (b"abc", 3, "text/plain")
        assert audit == [("log_minio_write_success", ("dir/a.bin", "main"), {"display_name": "a.bin"})]

    def test_upload_to_other_bucket_default_content_type(self, client, audit):
        client.upload_file(b"", "x", bucket_name="docs")
        assert client.client.objects[("docs", "x")] == (b"", 0, "application/octet-stream")

    def test_upload_without_audit(self, client, audit):
        client.upload_file(b"1", "x", cef_audit=False)
        assert audit == []

    def test_upload_error_is_audited_and_raised(self, client, audit):
        client.client.error = s3err("InternalError", "disk full")
        with pytest.raises(S3Error):
            client.upload_file(b"1", "x", cef_display_name="shown")
        assert audit[0][0] == "log_minio_write_failure"
        assert audit[0][1][:2] == ("x", "main") and "disk full" in audit[0][1][2]
        assert audit[0][2] == {"display_name": "shown"}

    def test_upload_error_without_audit(self, client, audit):
        client.client.error = s3err("InternalError")
        with pytest.raises(S3Error):
            client.upload_file(b"1", "x", cef_audit=False)
        assert audit == []


class TestDownload:
    def test_download_returns_data_and_releases_connection(self, client, audit, monkeypatch):
        client.upload_file(b"payload", "f", cef_audit=False)
        responses = []
        original = client.client.get_object

        def spy(bucket, name):
            r = original(bucket, name)
            responses.append(r)
            return r

        monkeypatch.setattr(client.client, "get_object", spy)
        assert client.download_file("f") == b"payload"
        assert responses[0].closed and responses[0].released
        assert audit == [("log_minio_read_success", ("f", "main"), {})]

    def test_download_without_audit(self, client, audit):
        client.upload_file(b"p", "f", bucket_name="docs", cef_audit=False)
        assert client.download_file("f", "docs", cef_audit=False) == b"p"
        assert audit == []

    def test_download_error_is_audited_and_raised(self, client, audit):
        client.client.error = s3err("NoSuchKey", "gone")
        with pytest.raises(S3Error):
            client.download_file("f")
        assert audit[0][0] == "log_minio_read_failure" and audit[0][1][:2] == ("f", "main")

    def test_download_error_without_audit(self, client, audit):
        client.client.error = s3err("NoSuchKey")
        with pytest.raises(S3Error):
            client.download_file("f", cef_audit=False)
        assert audit == []


class TestGetFilePath:
    def test_file_is_saved_to_temp_dir(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(mc.tempfile, "gettempdir", lambda: str(tmp_path))
        client.upload_file(b"data", "a/b/report.txt", cef_audit=False)
        path = client.get_file_path("a/b/report.txt")
        assert path == os.path.join(str(tmp_path), "report.txt")
        with open(path, "rb") as f:
            assert f.read() == b"data"

    def test_object_name_ending_with_slash_gets_generated_name(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(mc.tempfile, "gettempdir", lambda: str(tmp_path))
        client.upload_file(b"data", "dir/", cef_audit=False)
        path = client.get_file_path("dir/")
        assert os.path.basename(path).startswith("temp_") and os.path.dirname(path) == str(tmp_path)

    def test_download_error_is_propagated(self, client):
        with pytest.raises(KeyError):
            client.get_file_path("missing")


class TestDelete:
    def test_delete_existing(self, client, audit):
        client.upload_file(b"1", "f", cef_audit=False)
        assert client.delete_file("f") is True
        assert ("main", "f") not in client.client.objects
        assert audit == [("log_minio_remove_success", ("f", "main"), {})]

    def test_delete_without_audit(self, client, audit):
        assert client.delete_file("f", "docs", cef_audit=False) is True
        assert audit == []

    def test_no_such_key_gives_false_without_failure_event(self, client, audit):
        client.client.error = s3err("NoSuchKey")
        assert client.delete_file("f") is False
        assert audit == []

    def test_other_error_is_audited_and_raised(self, client, audit):
        client.client.error = s3err("AccessDenied", "nope")
        with pytest.raises(S3Error):
            client.delete_file("f")
        assert audit[0][0] == "log_minio_remove_failure" and audit[0][1][:2] == ("f", "main")

    def test_other_error_without_audit(self, client, audit):
        client.client.error = s3err("AccessDenied")
        with pytest.raises(S3Error):
            client.delete_file("f", cef_audit=False)
        assert audit == []


class TestExistsAndUrl:
    def test_file_exists(self, client):
        client.upload_file(b"1", "f", cef_audit=False)
        assert client.file_exists("f") is True
        assert client.file_exists("zzz") is False

    def test_file_exists_in_other_bucket(self, client):
        client.upload_file(b"1", "f", bucket_name="docs", cef_audit=False)
        assert client.file_exists("f") is False
        assert client.file_exists("f", "docs") is True

    def test_exists_other_error_is_raised(self, client):
        client.client.error = s3err("InternalError")
        with pytest.raises(S3Error):
            client.file_exists("f")

    def test_presigned_url_default_and_custom_expiry(self, client):
        assert client.get_presigned_url("f") == "https://minio/main/f?exp=3600"
        assert client.get_presigned_url("f", timedelta(minutes=5)).endswith("exp=300")

    def test_presigned_url_error_is_raised(self, client):
        client.client.error = s3err("InternalError")
        with pytest.raises(S3Error):
            client.get_presigned_url("f")

    def test_generated_object_name(self, client):
        name = client.generate_object_name("voice_", ".wav")
        assert name.startswith("voice_") and name.endswith(".wav")
        float(name[len("voice_"):-len(".wav")])
        assert client.generate_object_name() != "" and "." in client.generate_object_name()


class TestSingleton:
    def test_client_is_created_once(self):
        first = mc.get_minio_client()
        assert first is mc.get_minio_client()
        assert len(FakeMinio.instances) == 1

    def test_initialisation_failure_gives_none_and_retries_next_time(self):
        FakeMinio.exists_error = s3err("InternalError")
        assert mc.get_minio_client() is None
        FakeMinio.exists_error = None
        assert mc.get_minio_client() is not None

    def test_library_missing_gives_none(self, monkeypatch):
        monkeypatch.setattr(mc, "MINIO_AVAILABLE", False)
        assert mc.get_minio_client() is None


class TestLibraryAbsent:
    def test_module_without_minio_package_still_imports(self, monkeypatch):
        """Пакет `minio` не установлен: модуль загружается, флаг сбрасывается, клиент недоступен."""
        monkeypatch.setitem(sys.modules, "minio", None)
        monkeypatch.setitem(sys.modules, "minio.error", None)
        spec = importlib.util.spec_from_file_location("minio_client_nolib", mc.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.MINIO_AVAILABLE is False
        assert module.get_minio_client() is None
        with pytest.raises(ImportError):
            module.MinIOClient()
