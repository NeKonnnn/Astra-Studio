"""utils/url_safety: защита от SSRF для пользовательских URL."""

import pytest

from backend.utils import url_safety as us

pytestmark = pytest.mark.unit

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


class TestValidatePublicHttpUrl:
    @pytest.mark.parametrize("url", ["https://youtu.be/abc", "http://example.com:8080/x?y=1"])
    def test_public_http_urls_pass(self, url):
        assert us.validate_public_http_url(url, resolver=PUBLIC) == url

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://a.com", "gopher://a.com", "javascript:alert(1)", "//a.com/x", "a.com/x"])
    def test_other_schemes_are_rejected(self, url):
        with pytest.raises(us.UnsafeUrlError):
            us.validate_public_http_url(url, resolver=PUBLIC)

    @pytest.mark.parametrize("url", ["", "   ", "http://", "http:///x", "x" * 3000])
    def test_malformed_urls_are_rejected(self, url):
        with pytest.raises(us.UnsafeUrlError):
            us.validate_public_http_url(url, resolver=PUBLIC)

    @pytest.mark.parametrize(
        "ip",
        ["127.0.0.1", "10.0.0.1", "172.16.5.4", "192.168.1.1", "169.254.169.254", "0.0.0.0", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1", "224.0.0.1", "100.64.0.1"],
    )
    def test_non_public_addresses_are_rejected(self, ip):
        with pytest.raises(us.UnsafeUrlError):
            us.validate_public_http_url("http://host.example/x", resolver=lambda host: [ip])

    def test_any_forbidden_address_among_several_rejects_the_url(self):
        with pytest.raises(us.UnsafeUrlError):
            us.validate_public_http_url("http://h.example/", resolver=lambda host: ["93.184.216.34", "10.0.0.1"])

    @pytest.mark.parametrize("host", ["localhost", "LOCALHOST.", "metadata.google.internal", "svc.internal", "a.localhost", "printer.local"])
    def test_reserved_hostnames_are_rejected_without_resolving(self, host):
        def boom(_):
            raise AssertionError("resolver must not be called")

        with pytest.raises(us.UnsafeUrlError):
            us.validate_public_http_url(f"http://{host}/x", resolver=boom)

    def test_userinfo_does_not_hide_the_real_host(self):
        with pytest.raises(us.UnsafeUrlError):
            us.validate_public_http_url("http://youtube.com@127.0.0.1/x", resolver=lambda host: [host])

    def test_resolution_failure_is_rejected(self):
        def fail(_):
            raise OSError("nxdomain")

        with pytest.raises(us.UnsafeUrlError):
            us.validate_public_http_url("http://nope.example/", resolver=fail)

    def test_empty_resolution_is_rejected(self):
        with pytest.raises(us.UnsafeUrlError):
            us.validate_public_http_url("http://nope.example/", resolver=lambda host: [])
