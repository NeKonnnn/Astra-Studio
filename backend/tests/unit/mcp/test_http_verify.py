"""mcp/http_verify: выбор TLS verify для httpx MCP-клиента."""

import pytest

from backend.mcp import http_verify

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in http_verify._CA_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)


def test_default_uses_system_cas():
    assert http_verify.resolve_httpx_verify() is True


def test_server_can_disable_verification_entirely(tmp_path, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    monkeypatch.setenv("TLS_CERT_PATH", str(ca))

    assert http_verify.resolve_httpx_verify(server_verify_ssl=False) is False


def test_env_ca_bundle_is_used_when_file_exists(tmp_path, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))

    assert http_verify.resolve_httpx_verify() == str(ca)


def test_missing_ca_file_falls_back_to_system_cas(tmp_path, monkeypatch):
    monkeypatch.setenv("TLS_CERT_PATH", str(tmp_path / "missing.pem"))

    assert http_verify.resolve_httpx_verify() is True


def test_priority_follows_env_key_order(tmp_path, monkeypatch):
    first = tmp_path / "first.pem"
    second = tmp_path / "second.pem"
    first.write_text("x")
    second.write_text("x")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(second))
    monkeypatch.setenv("TLS_CERT_PATH", str(first))

    assert http_verify.resolve_httpx_verify() == str(first)


def test_missing_first_candidate_does_not_block_next(tmp_path, monkeypatch):
    real = tmp_path / "real.pem"
    real.write_text("x")
    monkeypatch.setenv("TLS_CERT_PATH", str(tmp_path / "gone.pem"))
    monkeypatch.setenv("LDAP_CA_CERT_PATH", str(real))

    assert http_verify.resolve_httpx_verify() == str(real)


def test_blank_env_value_is_ignored(monkeypatch):
    monkeypatch.setenv("TLS_CERT_PATH", "   ")

    assert http_verify.resolve_httpx_verify() is True


@pytest.mark.parametrize(
    ("verify", "label"),
    [
        (False, "False"),
        (True, "True(system CAs)"),
        ("/etc/ssl/ca.pem", "/etc/ssl/ca.pem"),
    ],
)
def test_label(verify, label):
    assert http_verify.verify_setting_label(verify) == label
