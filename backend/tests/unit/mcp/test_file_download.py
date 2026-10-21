"""mcp/file_download: проверка office-файлов, настройки S3, кандидаты ключей и скачивание (S3 → HTTP proxy)."""

from types import SimpleNamespace

import httpx
import pytest
import respx

from backend.mcp import file_download as fd

pytestmark = pytest.mark.unit

PPTX = b"PK\x03\x04rest-of-zip"
BASE = "https://mcp.example.com"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith(("MCP_SERVER_", "MINIO_")) or name in {
            "TLS_CERT_PATH", "LDAP_CA_CERT_PATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "S3_REGION",
        }:
            monkeypatch.delenv(name)


def server(sid="slides-srv", fs=None, verify_ssl=True):
    return SimpleNamespace(id=sid, files_storage=fs, verify_ssl=verify_ssl)


def fs_cfg(**kw):
    base = dict(enabled=True, bucket="b", endpoint="s3.local", prefix="/pres/", access_key="ak", secret_key="sk", use_ssl=True, port=9000)
    base.update(kw)
    return SimpleNamespace(**base)


class TestBlobValidation:
    @pytest.mark.parametrize(
        "data,name,ok",
        [
            (PPTX, "a.pptx", True), (PPTX, "A.DOCX", True), (PPTX, "a.xlsx", True),
            (b"{\"error\":1}", "a.pptx", False), (b"<html>", "a.docx", False),
            (b"\xd0\xcf\x11\xe0xx", "a.ppt", True), (PPTX, "a.ppt", False),
            (b"%PDF-1.7", "a.pdf", True), (b"PK\x03\x04", "a.pdf", False),
            (b"plain bytes", "a.bin", True), (b'  {"json": 1}', "a.bin", False), (b"[1]", "a.bin", False),
            (b"abc", "a.pptx", False), (b"", "a.pptx", False), (None, "a.pptx", False),
        ],
    )
    def test_is_valid_office_blob(self, data, name, ok):
        assert fd.is_valid_office_blob(data, name) is ok

    @pytest.mark.parametrize(
        "name,mime",
        [
            ("a.PPTX", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
            ("a.ppt", "application/vnd.ms-powerpoint"),
            ("a.pdf", "application/pdf"),
            ("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("a.zip", "application/octet-stream"),
        ],
    )
    def test_guess_content_type(self, name, mime):
        assert fd.guess_content_type(name) == mime


class TestEnvHelpers:
    def test_prefix_is_normalised(self):
        assert fd._storage_env_prefix("my-srv") == "MCP_SERVER_MY_SRV_FILES_S3_"

    @pytest.mark.parametrize("raw,expected", [("1", True), (" Yes ", True), ("on", True), ("0", False), ("no", False), ("", False)])
    def test_env_bool(self, monkeypatch, raw, expected):
        monkeypatch.setenv("X_FLAG", raw)
        assert fd._env_bool("X_FLAG", not expected) is expected

    def test_env_bool_default(self):
        assert fd._env_bool("UNSET_FLAG_X", True) is True

    def test_ca_path_prefers_the_server_specific_file(self, monkeypatch, tmp_path):
        specific, generic = tmp_path / "a.pem", tmp_path / "b.pem"
        specific.write_text("x")
        generic.write_text("x")
        monkeypatch.setenv("TLS_CERT_PATH", str(generic))
        assert fd._resolve_ca_cert_path("srv") == str(generic)
        monkeypatch.setenv("MCP_SERVER_SRV_FILES_S3_CA_PATH", str(specific))
        assert fd._resolve_ca_cert_path("srv") == str(specific)

    def test_missing_ca_files_are_ignored(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TLS_CERT_PATH", str(tmp_path / "nope.pem"))
        assert fd._resolve_ca_cert_path("srv") is None

    def test_verify_tls_switch(self, monkeypatch):
        assert fd._resolve_verify_tls("srv", True) is True
        monkeypatch.setenv("MCP_SERVER_SRV_FILES_S3_VERIFY_TLS", "false")
        assert fd._resolve_verify_tls("srv", True) is False

    def test_minio_fallbacks(self, monkeypatch):
        assert fd._minio_fallback("bucket", "own") == "own"
        assert fd._minio_fallback("bucket", "") == ""
        assert fd._minio_fallback("unknown", "") == ""
        monkeypatch.setenv("MINIO_DOCUMENTS_BUCKET_NAME", " docs ")
        assert fd._minio_fallback("bucket", "") == "docs"
        monkeypatch.setenv("MINIO_ROOT_USER", "root")
        assert fd._minio_fallback("access_key", "") == "root"


class TestResolveStorage:
    def test_yaml_config_is_used(self):
        s = fd.resolve_files_storage(server(fs=fs_cfg()))
        assert (s["bucket"], s["endpoint"], s["prefix"], s["port"], s["use_ssl"]) == ("b", "s3.local", "pres", 9000, True)
        assert s["access_key"] == "ak" and s["server_id"] == "slides-srv" and s["verify_tls"] is True

    def test_environment_overrides_yaml(self, monkeypatch):
        p = "MCP_SERVER_SLIDES_SRV_FILES_S3_"
        for k, v in {"BUCKET": "env-b", "ENDPOINT": "env.s3", "PREFIX": "/x/", "ACCESS_KEY": "ea", "SECRET_KEY": "es", "USE_SSL": "false", "PORT": "8080"}.items():
            monkeypatch.setenv(p + k, v)
        s = fd.resolve_files_storage(server(fs=fs_cfg()))
        assert (s["bucket"], s["endpoint"], s["prefix"], s["access_key"], s["use_ssl"], s["port"]) == ("env-b", "env.s3", "x", "ea", False, 8080)
        assert s["verify_tls"] is False

    def test_disabled_storage_is_none(self):
        assert fd.resolve_files_storage(server(fs=fs_cfg(enabled=False))) is None

    def test_minio_env_is_a_fallback_without_yaml(self, monkeypatch):
        for k, v in {"MINIO_BUCKET_NAME": "mb", "MINIO_ENDPOINT": "minio", "MINIO_ACCESS_KEY": "a", "MINIO_SECRET_KEY": "s", "MINIO_USE_SSL": "false"}.items():
            monkeypatch.setenv(k, v)
        s = fd.resolve_files_storage(server())
        assert (s["bucket"], s["prefix"], s["port"], s["use_ssl"]) == ("mb", "presentations", 80, False)

    def test_default_ports_follow_ssl(self, monkeypatch):
        for k, v in {"MINIO_BUCKET_NAME": "mb", "MINIO_ENDPOINT": "minio", "MINIO_ACCESS_KEY": "a", "MINIO_SECRET_KEY": "s"}.items():
            monkeypatch.setenv(k, v)
        assert fd.resolve_files_storage(server())["port"] == 443

    def test_bad_port_falls_back_to_yaml_or_default(self, monkeypatch):
        monkeypatch.setenv("MINIO_PORT", "abc")
        assert fd.resolve_files_storage(server(fs=fs_cfg()))["port"] == 9000

    @pytest.mark.parametrize("missing", ["bucket", "endpoint", "access_key", "secret_key"])
    def test_incomplete_configuration_is_none(self, missing):
        assert fd.resolve_files_storage(server(fs=fs_cfg(**{missing: ""}))) is None


class TestKeyCandidates:
    def test_order_and_deduplication(self):
        keys = fd._s3_object_key_candidates("a.pptx", {"prefix": "pres"}, "/custom/a.pptx")
        assert keys == ["custom/a.pptx", "pres/a.pptx", "a.pptx", "outputs/a.pptx", "files/a.pptx"]

    def test_without_prefix(self):
        assert fd._s3_object_key_candidates("a.pptx", {}) == ["a.pptx", "outputs/a.pptx", "files/a.pptx"]

    def test_duplicates_are_removed(self):
        assert fd._s3_object_key_candidates("a", {"prefix": "p"}, "p/a") == ["p/a", "a", "outputs/a", "files/a"]

    def test_storage_key_cannot_escape_the_bucket_root(self):
        assert fd._s3_object_key_candidates("a", {}, "///etc/x")[0] == "etc/x"


class FakeResponse:
    def __init__(self, data):
        self.data, self.closed, self.released = data, False, False

    def read(self):
        return self.data

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class FakeMinio:
    def __init__(self, objects):
        self.objects, self.requested = objects, []

    def get_object(self, bucket, key):
        self.requested.append((bucket, key))
        if key not in self.objects:
            raise OSError("NoSuchKey")
        return FakeResponse(self.objects[key])


STORAGE = {"bucket": "b", "prefix": "pres", "endpoint": "s3", "access_key": "a", "secret_key": "s"}


class TestFetchFromS3:
    def test_returns_the_first_valid_object(self, monkeypatch):
        client = FakeMinio({"outputs/a.pptx": PPTX})
        monkeypatch.setattr(fd, "_build_s3_client", lambda s: client)
        assert fd.fetch_from_s3("a.pptx", STORAGE) == (PPTX, "")
        assert client.requested[0] == ("b", "pres/a.pptx") and client.requested[-1] == ("b", "outputs/a.pptx")

    def test_invalid_payload_is_skipped_and_reported(self, monkeypatch):
        client = FakeMinio({"pres/a.pptx": b'{"error":"not found"}'})
        monkeypatch.setattr(fd, "_build_s3_client", lambda s: client)
        data, error = fd.fetch_from_s3("a.pptx", STORAGE)
        # Известная особенность: причина «объект не office-файл» затирается ошибкой последнего
        # ключа-кандидата (NoSuchKey), поэтому пользователь её не видит.
        assert data is None and error == "NoSuchKey"

    def test_invalid_payload_reason_is_kept_when_it_is_the_last_candidate(self, monkeypatch):
        client = FakeMinio({"files/a.pptx": b"junk"})
        monkeypatch.setattr(fd, "_build_s3_client", lambda s: client)
        data, error = fd.fetch_from_s3("a.pptx", STORAGE)
        assert data is None and "files/a.pptx is not a valid office file" in error.replace("object at key=", "")

    def test_nothing_found(self, monkeypatch):
        monkeypatch.setattr(fd, "_build_s3_client", lambda s: FakeMinio({}))
        data, error = fd.fetch_from_s3("a.pptx", STORAGE)
        assert data is None and error == "NoSuchKey"

    def test_storage_key_is_tried_first(self, monkeypatch):
        client = FakeMinio({"custom/x.pptx": PPTX})
        monkeypatch.setattr(fd, "_build_s3_client", lambda s: client)
        assert fd.fetch_from_s3("a.pptx", STORAGE, storage_key="custom/x.pptx")[0] == PPTX
        assert client.requested == [("b", "custom/x.pptx")]


class TestBuildClient:
    @pytest.fixture
    def captured(self, monkeypatch):
        import minio
        import urllib3

        seen = {"pools": []}
        monkeypatch.setattr(urllib3, "PoolManager", lambda **kw: seen["pools"].append(kw) or "pool")
        monkeypatch.setattr(urllib3, "disable_warnings", lambda *a: seen.setdefault("warned", True))
        monkeypatch.setattr(minio, "Minio", lambda endpoint, **kw: seen.update(endpoint=endpoint, **kw) or "client")
        return seen

    def test_port_is_appended_and_system_trust_used(self, captured):
        assert fd._build_s3_client({"endpoint": "s3", "port": 9000, "access_key": "a", "secret_key": "s", "use_ssl": True}) == "client"
        assert captured["endpoint"] == "s3:9000" and captured["secure"] is True and captured["pools"] == [{}]
        assert captured["region"] == "us-east-1"

    def test_explicit_port_in_endpoint_is_kept(self, captured):
        fd._build_s3_client({"endpoint": "s3:1234", "port": 9000, "access_key": "a", "secret_key": "s"})
        assert captured["endpoint"] == "s3:1234" and captured["secure"] is False

    def test_ca_bundle_requires_certificates(self, captured):
        fd._build_s3_client({"endpoint": "s3", "access_key": "a", "secret_key": "s", "use_ssl": True, "ca_path": "/ca.pem"})
        assert captured["pools"] == [{"cert_reqs": "CERT_REQUIRED", "ca_certs": "/ca.pem"}]

    def test_disabled_verification_is_explicit(self, captured):
        fd._build_s3_client({"endpoint": "s3", "access_key": "a", "secret_key": "s", "use_ssl": True, "verify_tls": False})
        assert captured["pools"] == [{"cert_reqs": "CERT_NONE"}] and captured["warned"]

    def test_region_comes_from_environment(self, captured, monkeypatch):
        monkeypatch.setenv("S3_REGION", "eu-1")
        fd._build_s3_client({"endpoint": "s3", "access_key": "a", "secret_key": "s"})
        assert captured["region"] == "eu-1"


class TestHttpHelpers:
    def test_candidate_urls_are_quoted(self):
        urls = fd._http_candidate_urls(BASE + "/", "мой файл.pptx")
        assert urls[0] == f"{BASE}/files/%D0%BC%D0%BE%D0%B9%20%D1%84%D0%B0%D0%B9%D0%BB.pptx" and len(urls) == 7
        assert fd._http_candidate_urls(BASE, "../a")[0] == f"{BASE}/files/..%2Fa"

    def test_verify_setting_priority(self, monkeypatch, tmp_path):
        assert fd._http_verify_setting("srv", True) is True
        assert fd._http_verify_setting("srv", False) is False
        monkeypatch.setenv("MCP_SERVER_SRV_FILES_S3_VERIFY_TLS", "0")
        assert fd._http_verify_setting("srv", True) is False
        ca = tmp_path / "ca.pem"
        ca.write_text("x")
        monkeypatch.setenv("SSL_CERT_FILE", str(ca))
        assert fd._http_verify_setting("srv", True) == str(ca)


class TestHttpProxy:
    async def test_first_valid_response_wins(self):
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{BASE}/files/a.pptx").respond(404)
            router.get(f"{BASE}/download/a.pptx").respond(200, content=b"<html>login</html>")
            router.get(f"{BASE}/api/files/a.pptx").mock(side_effect=httpx.ConnectError("down"))
            ok = router.get(f"{BASE}/api/download/a.pptx").respond(200, content=PPTX)
            assert await fd.fetch_from_http_proxy("a.pptx", BASE, {"Authorization": "Bearer t"}) == PPTX
            assert ok.calls[0].request.headers["authorization"] == "Bearer t"

    async def test_redirects_are_followed(self):
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{BASE}/files/a.pptx").respond(302, headers={"location": f"{BASE}/cdn/a.pptx"})
            router.get(f"{BASE}/cdn/a.pptx").respond(200, content=PPTX)
            assert await fd.fetch_from_http_proxy("a.pptx", BASE, {}) == PPTX

    async def test_nothing_found(self):
        with respx.mock(assert_all_called=False) as router:
            router.route().respond(404)
            assert await fd.fetch_from_http_proxy("a.pptx", BASE, {}) is None


class TestFetchMcpServerFile:
    @pytest.mark.parametrize("name", ["", "   ", None])
    async def test_empty_name_is_rejected(self, name):
        with pytest.raises(ValueError):
            await fd.fetch_mcp_server_file(server=server(), filename=name, base_url=BASE, http_headers={})

    async def test_s3_hit_skips_http(self, monkeypatch):
        monkeypatch.setattr(fd, "resolve_files_storage", lambda s: STORAGE)
        monkeypatch.setattr(fd, "fetch_from_s3", lambda name, st, storage_key=None: (PPTX, ""))
        with respx.mock(assert_all_called=False, assert_all_mocked=True):
            data, mime = await fd.fetch_mcp_server_file(server=server(), filename=" a.pptx ", base_url=BASE, http_headers={})
        assert data == PPTX and mime.endswith("presentationml.presentation")

    async def test_http_is_the_fallback(self, monkeypatch):
        monkeypatch.setattr(fd, "resolve_files_storage", lambda s: None)
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{BASE}/files/a.pdf").respond(200, content=b"%PDF-1.4 x")
            data, mime = await fd.fetch_mcp_server_file(server=server(), filename="a.pdf", base_url=BASE, http_headers={})
        assert data.startswith(b"%PDF") and mime == "application/pdf"

    async def test_not_found_explains_both_sources(self, monkeypatch):
        monkeypatch.setattr(fd, "resolve_files_storage", lambda s: STORAGE)
        monkeypatch.setattr(fd, "fetch_from_s3", lambda *a, **k: (None, "NoSuchKey"))
        with respx.mock(assert_all_called=False) as router:
            router.route().respond(404)
            with pytest.raises(LookupError) as err:
                await fd.fetch_mcp_server_file(server=server(), filename="a.pptx", base_url=BASE, http_headers={}, storage_key="k/a.pptx")
        message = str(err.value)
        assert "a.pptx" in message and "storage_key=k/a.pptx" in message and "S3: NoSuchKey" in message

    async def test_unconfigured_storage_is_mentioned(self, monkeypatch):
        monkeypatch.setattr(fd, "resolve_files_storage", lambda s: None)
        with respx.mock(assert_all_called=False) as router:
            router.route().respond(404)
            with pytest.raises(LookupError, match="not configured"):
                await fd.fetch_mcp_server_file(server=server(), filename="a.pptx", base_url=BASE, http_headers={})
