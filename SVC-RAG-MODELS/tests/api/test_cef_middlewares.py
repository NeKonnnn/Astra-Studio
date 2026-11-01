"""CefAuditMiddleware (OBJ001/OBJ002) и CefAuthMiddleware (API-ключ, SEC001/SEC003)."""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute

from app.core.cef_logger import cef_audit_context as ctx
from app.core.cef_logger import cef_audit_middleware as audit_mw
from app.core.cef_logger import cef_auth_middleware as auth_mw
from app.core.cef_logger import CefAuditMiddleware
from app.main import create_application
from tests.cef_helpers import make_request, parse_cef

pytestmark = pytest.mark.unit

SKIPPED = ["/health", "/v1/health", "/v1/health/", "/docs", "/redoc", "/openapi.json", "/x/docs"]
NOT_SKIPPED = ["/v1/embeddings", "/v1/rerank", "/v1/models", "/v1/models/select", "/healthy", "/v1/healthz", "/docs/x"]


def _events(lines):
    return [parse_cef(line)[0]["event_id"] for line in lines]


# --------------------------------------------------------------------------- skip rules
class TestShouldSkip:
    @pytest.mark.parametrize("module", [audit_mw, auth_mw])
    @pytest.mark.parametrize("path", SKIPPED)
    def test_skipped(self, module, path):
        assert module._should_skip(path) is True

    @pytest.mark.parametrize("module", [audit_mw, auth_mw])
    @pytest.mark.parametrize("path", NOT_SKIPPED)
    def test_not_skipped(self, module, path):
        assert module._should_skip(path) is False

    @pytest.mark.parametrize("module", [audit_mw, auth_mw])
    def test_none_path_is_skipped_like_empty(self, module):
        assert module._should_skip(None) is False

    def test_only_probe_and_doc_routes_bypass_auth(self):
        """Регрессия безопасности: ни один рабочий маршрут не оканчивается на пропускаемый суффикс."""
        app = create_application()
        bypass = {r.path for r in app.routes if auth_mw._should_skip(r.path)}
        assert bypass == {"/v1/health", "/docs", "/redoc", "/openapi.json"}
        assert all(
            not auth_mw._should_skip(r.path) for r in app.routes if isinstance(r, APIRoute) and not r.path.endswith("/health")
        )


# --------------------------------------------------------------------------- audit middleware
def _audit_app():
    app = FastAPI()

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/redir")
    async def redir():
        return RedirectResponse("/ok", status_code=302)

    @app.get("/health")
    async def health():
        return {"h": 1}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("сбой " + "x" * 600)

    @app.get("/health/boom-not-skipped")
    async def not_skipped():
        raise RuntimeError("x")

    @app.get("/docs-like/health")
    async def docs_boom():
        raise RuntimeError("skipped path failure")

    app.add_middleware(CefAuditMiddleware)
    return app


@pytest.fixture
async def audit_client():
    transport = httpx.ASGITransport(app=_audit_app(), raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://svc") as c:
        yield c


class TestAuditMiddleware:
    async def test_success_logged_as_obj001(self, audit_client, cef_lines):
        r = await audit_client.get("/ok")
        assert r.status_code == 200
        (line,) = cef_lines
        header, ext = parse_cef(line)
        assert header["event_id"] == "OBJ001"
        assert ext["methodName"] == "GET /ok" and ext["serviceName"] == "SVC-RAG-MODELS"
        assert ext["outcome"] == "success" and "cs2" not in ext
        assert len(ext["requestUuid"]) == 32

    async def test_identity_and_caller_taken_from_headers(self, audit_client, cef_lines):
        await audit_client.get(
            "/ok",
            headers={"X-CEF-Suser": "ivanov", "X-CEF-Suid": "17", "X-CEF-Sntdom": "CORP",
                     "X-CEF-Caller-Service": "SVC-RAG"},
        )
        _, ext = parse_cef(cef_lines[0])
        assert (ext["suser"], ext["suid"], ext["sntdom"]) == ("ivanov", "17", "CORP")
        assert (ext["cs2"], ext["cs2Label"]) == ("SVC-RAG", "CallerService")

    async def test_redirect_is_still_success(self, audit_client, cef_lines):
        r = await audit_client.get("/redir")
        assert r.status_code == 302
        assert _events(cef_lines) == ["OBJ001"]

    async def test_client_error_logged_as_obj002_with_code(self, audit_client, cef_lines):
        r = await audit_client.get("/nope", headers={"X-CEF-Caller-Service": "caller"})
        assert r.status_code == 404
        header, ext = parse_cef(cef_lines[0])
        assert header["event_id"] == "OBJ002"
        assert ext["codeStatus"] == "404" and ext["textStatus"] == "-"
        assert ext["outcome"] == "failure" and ext["cs2"] == "caller"
        assert "Ошибка '404 -'" in ext["msg"]

    async def test_unhandled_exception_logged_and_reraised(self, audit_client, cef_lines):
        with pytest.raises(RuntimeError):
            await audit_client.get("/boom")
        header, ext = parse_cef(cef_lines[0])
        assert header["event_id"] == "OBJ002"
        assert ext["codeStatus"] == "500" and ext["outcome"] == "error"
        assert ext["textStatus"] == ("сбой " + "x" * 600)[:512]

    async def test_exception_on_skipped_path_is_not_audited_but_reraised(self, audit_client, cef_lines):
        with pytest.raises(RuntimeError, match="skipped path failure"):
            await audit_client.get("/docs-like/health")
        assert cef_lines == []

    async def test_skipped_path_success_not_audited(self, audit_client, cef_lines):
        assert (await audit_client.get("/health")).status_code == 200
        assert cef_lines == []

    async def test_context_is_reset_after_request_and_after_failure(self, audit_client):
        await audit_client.get("/ok")
        assert ctx.cef_audit_peek() == (None, None)
        with pytest.raises(RuntimeError):
            await audit_client.get("/boom")
        assert ctx.cef_audit_peek() == (None, None)

    async def test_caller_extra_helper(self):
        assert audit_mw._caller_extra(make_request()) == {}
        assert audit_mw._caller_extra(make_request({"x-cef-caller-service": "a"})) == {
            "cs2": "a", "cs2Label": "CallerService"}


# --------------------------------------------------------------------------- auth middleware
@pytest.fixture
async def secured(monkeypatch):
    monkeypatch.setenv("RAG_MODELS_API_KEY", "s3cret")
    app = create_application()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://svc") as c:
        yield c


class TestApiKeyResolution:
    def test_direct_key_stripped(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_API_KEY", "  k  ")
        assert auth_mw._configured_api_key() == "k"

    def test_indirect_key_via_env_name(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_API_KEY_ENV", "MY_SECRET_VAR")
        monkeypatch.setenv("MY_SECRET_VAR", " from-indirect ")
        assert auth_mw._configured_api_key() == "from-indirect"

    def test_indirect_to_unset_variable_gives_empty(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_API_KEY_ENV", "NOT_DEFINED_ANYWHERE")
        monkeypatch.delenv("NOT_DEFINED_ANYWHERE", raising=False)
        assert auth_mw._configured_api_key() == ""

    def test_direct_beats_indirect(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_API_KEY", "direct")
        monkeypatch.setenv("RAG_MODELS_API_KEY_ENV", "MY_SECRET_VAR")
        monkeypatch.setenv("MY_SECRET_VAR", "indirect")
        assert auth_mw._configured_api_key() == "direct"

    def test_nothing_configured(self):
        assert auth_mw._configured_api_key() == ""


class TestExtractApiKey:
    @pytest.mark.parametrize(
        "headers,expected",
        [
            ({"Authorization": "Bearer abc"}, "abc"),
            ({"Authorization": "bearer   abc  "}, "abc"),
            ({"Authorization": "BEARER abc"}, "abc"),
            ({"X-API-Key": " k1 "}, "k1"),
            ({"Authorization": "Basic dXNlcjpwYXNz"}, ""),
            ({"Authorization": "Bearer"}, ""),
            ({"Authorization": "Bearer wrong", "X-API-Key": "right"}, "wrong"),
            ({"Authorization": "Token abc", "X-API-Key": "right"}, "right"),
            ({}, ""),
        ],
    )
    def test_extract(self, headers, expected):
        assert auth_mw._extract_api_key(make_request(headers)) == expected


class TestAuthMiddleware:
    async def test_missing_key_gives_401_and_sec003(self, secured, cef_lines):
        r = await secured.get("/v1/models")
        assert r.status_code == 401
        assert r.json() == {"detail": "Unauthorized"}
        header, ext = parse_cef(cef_lines[0])
        assert header["event_id"] == "SEC003"
        assert ext["reason"] == "missing API key"
        assert ext["outcome"] == "failure" and ext["suser"] == "unknown"
        assert (ext["cs1"], ext["cs2"]) == ("unknown", "api_key")
        assert "Неудачная попытка аутентификации УЗ unknown" in ext["msg"]

    async def test_wrong_key_gives_401_invalid(self, secured, cef_lines):
        r = await secured.post("/v1/embeddings", json={"input": "x"}, headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401
        assert parse_cef(cef_lines[0])[1]["reason"] == "invalid API key"

    async def test_rejected_request_never_reaches_handler(self, secured, fake_ml):
        r = await secured.post("/v1/embeddings", json={"input": "x"})
        assert r.status_code == 401
        assert fake_ml.st_calls == []

    async def test_wrong_key_not_written_to_log(self, secured, cef_lines):
        await secured.get("/v1/models", headers={"X-API-Key": "guess-12345"})
        assert "guess-12345" not in "\n".join(cef_lines)

    async def test_failed_attempt_ignores_spoofed_headers_and_logs_peer(self, secured, cef_lines, monkeypatch):
        """BUG-RM-06: SEC003 - заголовкам не верим, даже при RAG_MODELS_TRUSTED_PROXIES=*."""
        monkeypatch.setenv("RAG_MODELS_TRUSTED_PROXIES", "*")
        await secured.get(
            "/v1/models",
            headers={"X-CEF-Suser": "ivanov", "X-CEF-Caller-Service": "SVC-RAG", "X-Real-IP": "8.8.8.8"},
        )
        _, ext = parse_cef(cef_lines[0])
        assert ext["suser"] == "unknown" and ext["cs1"] == "unknown"
        assert ext["src"] == "127.0.0.1"

    async def test_valid_key_trusts_audit_headers_without_proxy_setting(self, secured, cef_lines, monkeypatch):
        """Валидный ключ - заголовки X-CEF-* / x-real-ip принимаются (нет insecure и нет списка прокси)."""
        monkeypatch.delenv("RAG_MODELS_ALLOW_INSECURE_NO_AUTH", raising=False)
        monkeypatch.delenv("RAG_MODELS_TRUSTED_PROXIES", raising=False)
        await secured.get(
            "/v1/models",
            headers={"X-API-Key": "s3cret", "X-CEF-Suser": "petrov", "X-CEF-Caller-Service": "SVC-RAG", "X-Real-IP": "8.8.8.8"},
        )
        assert _events(cef_lines) == ["SEC001", "OBJ001"]
        for line in cef_lines:
            _, ext = parse_cef(line)
            assert ext["suser"] == "petrov" and ext["src"] == "8.8.8.8"

    @pytest.mark.parametrize(
        "headers",
        [{"Authorization": "Bearer s3cret"}, {"X-API-Key": "s3cret"}, {"Authorization": "bearer s3cret"}],
    )
    async def test_valid_key_passes_and_sec001_then_obj001(self, secured, cef_lines, headers):
        r = await secured.get("/v1/models", headers={**headers, "X-CEF-Caller-Service": "SVC-RAG"})
        assert r.status_code == 200
        assert _events(cef_lines) == ["SEC001", "OBJ001"]
        _, ext = parse_cef(cef_lines[0])
        assert ext["msg"].startswith("Сервис SVC-RAG аутентифицирован с адреса")
        assert ext["cs2"] == "api_key" and ext["outcome"] == "success"
        assert "s3cret" not in "\n".join(cef_lines)

    async def test_valid_key_with_user_headers_audits_that_user(self, secured, cef_lines):
        await secured.get("/v1/models", headers={"X-API-Key": "s3cret", "X-CEF-Suser": "petrov", "X-CEF-Suid": "3"})
        _, ext = parse_cef(cef_lines[0])
        assert (ext["suser"], ext["suid"]) == ("petrov", "3")

    async def test_valid_key_without_caller_headers(self, secured, cef_lines):
        await secured.get("/v1/models", headers={"X-API-Key": "s3cret"})
        _, ext = parse_cef(cef_lines[0])
        assert ext["suser"] == "unknown"

    async def test_authorization_header_takes_precedence_over_api_key_header(self, secured):
        r = await secured.get("/v1/models", headers={"Authorization": "Bearer wrong", "X-API-Key": "s3cret"})
        assert r.status_code == 401

    async def test_key_prefix_and_extension_rejected(self, secured):
        for bad in ("s3cre", "s3cret ", "s3cretX", "S3CRET", ""):
            r = await secured.get("/v1/models", headers={"X-API-Key": bad})
            expected = 200 if bad == "s3cret " else 401  # пробелы по краям срезаются, остальное - нет
            assert r.status_code == expected, bad

    @pytest.mark.parametrize("path", ["/v1/health", "/docs", "/redoc", "/openapi.json"])
    async def test_probe_and_doc_paths_need_no_key(self, secured, path, fake_ml):
        r = await secured.get(path)
        assert r.status_code in (200, 503)

    @pytest.mark.parametrize(
        "path", ["/v1/embeddings/health", "/v1/models/../health", "/v1/rerank/docs", "/v1/models/select/openapi.json"]
    )
    async def test_suffix_trick_skips_auth_but_exposes_no_handler(self, secured, path, fake_ml):
        """Суффикс '/health' снимает проверку ключа, но реального маршрута за ним нет: 404, данных нет."""
        r = await secured.post(path, json={"input": "x"})
        assert r.status_code in (404, 405)
        assert fake_ml.st_calls == []

    async def test_every_business_route_requires_key(self, secured):
        app = create_application()
        for route in app.routes:
            if not isinstance(route, APIRoute) or auth_mw._should_skip(route.path):
                continue
            method = sorted(route.methods)[0]
            r = await secured.request(method, route.path, json={})
            assert r.status_code == 401, (method, route.path)

    async def test_indirect_key_env_enables_auth(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_API_KEY_ENV", "RM_KEY_HOLDER")
        monkeypatch.setenv("RM_KEY_HOLDER", "indirect-key")
        transport = httpx.ASGITransport(app=create_application())
        async with httpx.AsyncClient(transport=transport, base_url="http://svc") as c:
            assert (await c.get("/v1/models")).status_code == 401
            assert (await c.get("/v1/models", headers={"X-API-Key": "indirect-key"})).status_code == 200

    async def test_without_configured_key_is_fail_closed(self, monkeypatch, cef_lines, disk_models, caplog):
        """Fail-closed (BUG-RM-01): ключ не задан и нет явного разрешения - 503, обработчик не вызван."""
        monkeypatch.delenv("RAG_MODELS_ALLOW_INSECURE_NO_AUTH", raising=False)
        transport = httpx.ASGITransport(app=create_application(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://svc") as c:
            assert (await c.get("/v1/models")).status_code == 503
            assert (await c.post("/v1/embeddings", json={"input": "x"})).status_code == 503
            r = await c.post("/v1/models/select", json={"model_type": "embedding", "model_path": "local/FRIDA"})
            assert r.status_code == 503 and "RAG_MODELS_API_KEY" in r.json()["detail"]
            assert (await c.get("/v1/health")).status_code in (200, 503)
        assert "RAG_MODELS_ALLOW_INSECURE_NO_AUTH=1" in caplog.text

    @pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
    async def test_explicit_insecure_flag_allows_no_key(self, monkeypatch, disk_models, value):
        monkeypatch.setenv("RAG_MODELS_ALLOW_INSECURE_NO_AUTH", value)
        transport = httpx.ASGITransport(app=create_application(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://svc") as c:
            assert (await c.get("/v1/models")).status_code == 200

    @pytest.mark.parametrize("value", ["", "0", "no", "false"])
    async def test_falsy_insecure_flag_stays_closed(self, monkeypatch, value):
        monkeypatch.setenv("RAG_MODELS_ALLOW_INSECURE_NO_AUTH", value)
        transport = httpx.ASGITransport(app=create_application(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://svc") as c:
            assert (await c.get("/v1/models")).status_code == 503

    async def test_configured_key_ignores_insecure_flag(self, secured):
        assert (await secured.get("/v1/models")).status_code == 401
