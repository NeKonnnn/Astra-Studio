"""app/main.py: lifespan, create_application, тихие пути, форма тела запроса, middleware log_requests."""

import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import main
from app.core.cef_logger import CefAuditMiddleware, CefAuthMiddleware
from app.dependencies import rag_models_handler as h
from tests.api.conftest import disk_models  # noqa: F401  (фикстура API-тестов нужна и здесь)

pytestmark = pytest.mark.unit

# GPB_ASTRA опережает memo по redact/quiet helpers — классы ниже пропускаем до порта API.
_HAS_PAYLOAD_SHAPE = hasattr(main, "_payload_shape")
_HAS_IS_QUIET = hasattr(main, "_is_quiet")
_HAS_QUIET_PATHS = hasattr(main, "_quiet_paths")


# --------------------------------------------------------------------------- lifespan
class TestLifespan:
    async def test_start_loads_models_and_emits_sys001_then_sys002(self, disk_models, caplog, cef_lines):
        caplog.set_level(logging.INFO, logger="app.main")
        async with main.lifespan(main.app):
            assert ("embedding", "FRIDA") in h._MODEL_CACHE
            assert ("reranker", "ms-marco-MiniLM-L6-v2") in h._MODEL_CACHE
            assert "Сервис RAG-моделей готов" in caplog.text
            assert any("|SYS001|Application Started|" in line for line in cef_lines)
            assert not any("|SYS002|" in line for line in cef_lines)
        assert any("|SYS002|Application Stopped|" in line for line in cef_lines)
        assert len(h._MODEL_CACHE) == 0  # cleanup выгрузил модели
        assert "Сервис RAG-моделей остановлен" in caplog.text

    async def test_models_failed_logs_error_with_reason_but_service_starts(self, fake_ml, caplog, cef_lines):
        caplog.set_level(logging.INFO, logger="app.main")
        async with main.lifespan(main.app):
            pass
        assert "RAG-модели не поднялись: " in caplog.text
        assert "не найдена" in caplog.text
        assert any("|SYS001|" in line for line in cef_lines)

    async def test_models_failed_without_reason(self, monkeypatch, caplog):
        async def none_handler():
            return None

        monkeypatch.setattr(main, "get_rag_models_handler", none_handler)
        monkeypatch.setattr(main, "get_last_rag_models_error", lambda: None)
        caplog.set_level(logging.INFO, logger="app.main")
        async with main.lifespan(main.app):
            pass
        assert "RAG-модели не поднялись" in caplog.text
        assert "не поднялись:" not in caplog.text

    async def test_disabled_skips_model_loading(self, fake_ml, rm_settings, monkeypatch, cef_lines):
        monkeypatch.setattr(rm_settings, "enabled", False)
        called = []

        async def spy():
            called.append(1)

        monkeypatch.setattr(main, "get_rag_models_handler", spy)
        async with main.lifespan(main.app):
            pass
        assert called == []
        assert fake_ml.st_calls == []
        assert any("|SYS001|" in line for line in cef_lines)

    async def test_startup_error_is_logged_and_reraised(self, monkeypatch, caplog, cef_lines):
        async def boom():
            raise RuntimeError("старт упал")

        monkeypatch.setattr(main, "get_rag_models_handler", boom)
        with caplog.at_level(logging.ERROR, logger="app.main"):
            with pytest.raises(RuntimeError, match="старт упал"):
                async with main.lifespan(main.app):
                    pytest.fail("тело lifespan не должно выполниться")
        assert "Ошибка при старте: старт упал" in caplog.text
        assert not any("|SYS001|" in line for line in cef_lines)

    async def test_lifespan_wired_into_application(self, disk_models, cef_lines):
        app = main.create_application()
        async with app.router.lifespan_context(app):
            r = await httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://svc"
            ).get("/v1/health")
            assert r.status_code == 200
        assert any("|SYS002|" in line for line in cef_lines)


# --------------------------------------------------------------------------- create_application
class TestCreateApplication:
    def test_metadata_and_docs_urls_from_settings(self):
        app = main.create_application()
        assert isinstance(app, FastAPI)
        assert app.title == "RAG Models (test)"
        assert app.version == "0.0.1"
        assert app.docs_url == "/docs" and app.redoc_url == "/redoc"

    def test_routes_under_v1_prefix(self):
        paths = {r.path for r in main.create_application().routes}
        assert {
            "/v1/embeddings", "/v1/rerank", "/v1/health",
            "/v1/models", "/v1/models/current", "/v1/models/select",
        } <= paths

    def test_middleware_order_auth_outermost(self):
        classes = [m.cls for m in main.create_application().user_middleware]
        # последний добавленный - внешний: сперва Auth, потом Audit, потом CORS
        assert classes == [CefAuthMiddleware, CefAuditMiddleware, CORSMiddleware]

    async def test_cors_allows_configured_origin_only(self):
        app = main.create_application()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://svc") as c:
            ok = await c.options(
                "/v1/health",
                headers={"Origin": "http://front.test", "Access-Control-Request-Method": "GET"},
            )
            bad = await c.options(
                "/v1/health",
                headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "GET"},
            )
        assert ok.headers["access-control-allow-origin"] == "http://front.test"
        assert "access-control-allow-origin" not in bad.headers
        assert bad.status_code == 400

    def test_module_level_app_has_request_logger(self):
        assert isinstance(main.app, FastAPI)
        assert any(m.cls.__name__ == "BaseHTTPMiddleware" for m in main.app.user_middleware)


# --------------------------------------------------------------------------- quiet paths
@pytest.mark.skipif(not _HAS_QUIET_PATHS, reason="GPB-ahead: main._quiet_paths отсутствует в memo")
class TestQuietPaths:
    def test_default_when_env_not_set(self):
        assert main._quiet_paths() == main._DEFAULT_QUIET_PATHS
        assert "/health" in main._quiet_paths()

    def test_env_list_is_trimmed(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_LOG_QUIET_PATHS", " /ping , /pong,,")
        assert main._quiet_paths() == ("/ping", "/pong")

    def test_empty_env_disables_quiet_mode(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_LOG_QUIET_PATHS", "")
        assert main._quiet_paths() == ()
        assert main._is_quiet("GET", "/v1/health") is False


@pytest.mark.skipif(not _HAS_IS_QUIET, reason="GPB-ahead: main._is_quiet отсутствует в memo")
class TestIsQuiet:
    @pytest.mark.parametrize("path", ["/health", "/v1/health", "/healthz", "/ready", "/readyz",
                                      "/live", "/livez", "/metrics", "/v1/health?probe=1"])
    @pytest.mark.parametrize("method", ["GET", "get", "HEAD"])
    def test_probe_paths_are_quiet_for_reads(self, method, path):
        assert main._is_quiet(method, path) is True

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "", None])
    def test_writes_and_unknown_methods_are_logged(self, method):
        assert main._is_quiet(method, "/v1/health") is False

    def test_business_paths_are_logged(self):
        assert main._is_quiet("GET", "/v1/models") is False
        assert main._is_quiet("GET", None) is False

    def test_query_string_is_not_matched(self):
        assert main._is_quiet("GET", "/v1/models?next=/health") is False

    @pytest.mark.parametrize("status,quiet", [
        (200, True), (204, True), (299, True),
        (199, False), (300, False), (404, False), (500, False), (503, False),
        ("200", True), ("abc", False), (None, True), (object(), False),
    ])
    def test_only_successful_probe_responses_are_hidden(self, status, quiet):
        assert main._is_quiet("GET", "/v1/health", status) is quiet


# --------------------------------------------------------------------------- payload shape
@pytest.mark.skipif(not _HAS_PAYLOAD_SHAPE, reason="GPB-ahead: main._payload_shape отсутствует в memo")
class TestPayloadShape:
    def test_non_dict_shows_type_only(self):
        assert main._payload_shape(["секрет"]) == "list"
        assert main._payload_shape("секрет") == "str"
        assert main._payload_shape(None) == "NoneType"

    def test_safe_keys_show_values(self):
        shape = main._payload_shape({"model": "FRIDA", "top_n": 5, "truncate": True, "dimensions": 8.5})
        assert shape == "model=FRIDA top_n=5 truncate=True dimensions=8.5"

    def test_free_text_only_as_length(self):
        shape = main._payload_shape({"query": "очень секретный запрос", "text": "тоже"})
        assert shape == "query=<22 симв.> text=<4 симв.>"
        assert "секрет" not in shape

    def test_lists_show_count_and_total_length_only(self):
        shape = main._payload_shape({"input": ["abc", "de", 5], "passages": []})
        assert shape == "input=<3 шт., 5 симв.> passages=<0 шт., 0 симв.>"

    def test_scalars_and_none_shown(self):
        assert main._payload_shape({"top_k": 3, "flag": False, "x": None, "r": 1.5}) == "top_k=3 flag=False x=None r=1.5"

    def test_other_containers_show_type_name(self):
        assert main._payload_shape({"meta": {"k": "секрет"}, "t": (1, 2)}) == "meta=<dict> t=<tuple>"

    def test_safe_key_with_non_scalar_value_falls_back_to_shape(self):
        # model=<список> не печатается значением
        assert main._payload_shape({"model": ["секрет"]}) == "model=<1 шт., 6 симв.>"
        assert main._payload_shape({"model": {"a": "секрет"}}) == "model=<dict>"

    def test_empty_dict(self):
        assert main._payload_shape({}) == ""


# --------------------------------------------------------------------------- log_requests
class _FakeRequest:
    def __init__(self, method="POST", path="/v1/embeddings", body=b"", content_type="application/json", body_error=None):
        self.method = method
        self.url = SimpleNamespace(path=path, __str__=lambda s: f"http://svc{path}")
        self.url = _Url(path)
        self.headers = {"content-type": content_type} if content_type else {}
        self._body = body
        self._err = body_error

    async def body(self):
        if self._err:
            raise self._err
        return self._body


class _Url:
    def __init__(self, path):
        self.path = path

    def __str__(self):
        return f"http://svc{self.path}"


def _call_next(status=200):
    async def call_next(request):
        return SimpleNamespace(status_code=status)

    return call_next


class TestLogRequestsMiddleware:
    SECRET = "ПАСПОРТ-СЕРИЯ-4510-НОМЕР-123456"

    async def test_logs_request_and_status_lines(self, caplog):
        caplog.set_level(logging.INFO, logger="app.main")
        resp = await main.log_requests(_FakeRequest("GET", "/v1/models"), _call_next(200))
        assert resp.status_code == 200
        lines = [r.getMessage() for r in caplog.records if r.name == "app.main"]
        assert any(": GET http://svc/v1/models" in m for m in lines)
        assert any("status=200 time=" in m for m in lines)
        # request_id связывает обе строки
        rid = lines[0].split()[1].rstrip(":")
        assert rid in lines[1]

    @pytest.mark.skipif(not _HAS_IS_QUIET, reason="GPB-ahead: quiet probe paths")
    async def test_successful_probe_not_logged_at_all(self, caplog):
        caplog.set_level(logging.DEBUG, logger="app.main")
        await main.log_requests(_FakeRequest("GET", "/v1/health"), _call_next(200))
        assert [r for r in caplog.records if r.name == "app.main"] == []

    @pytest.mark.skipif(not _HAS_IS_QUIET, reason="GPB-ahead: quiet probe paths")
    async def test_failed_probe_response_is_logged_but_request_line_is_not(self, caplog):
        caplog.set_level(logging.INFO, logger="app.main")
        await main.log_requests(_FakeRequest("GET", "/v1/health"), _call_next(503))
        msgs = [r.getMessage() for r in caplog.records if r.name == "app.main"]
        assert len(msgs) == 1 and "status=503" in msgs[0]

    @pytest.mark.skipif(not _HAS_PAYLOAD_SHAPE, reason="GPB-ahead: body shape redaction")
    async def test_post_json_body_logged_as_shape_only(self, caplog):
        caplog.set_level(logging.DEBUG, logger="app.main")
        body = ('{"input": ["%s", "второй"], "model": "FRIDA"}' % self.SECRET).encode()
        await main.log_requests(_FakeRequest(body=body), _call_next(200))
        assert self.SECRET not in caplog.text
        assert "второй" not in caplog.text
        assert "input=<2 шт., " in caplog.text and "model=FRIDA" in caplog.text

    @pytest.mark.skipif(not _HAS_PAYLOAD_SHAPE, reason="GPB-ahead: body shape redaction")
    async def test_non_json_content_type_body_not_parsed(self, caplog):
        caplog.set_level(logging.DEBUG, logger="app.main")
        await main.log_requests(
            _FakeRequest(body=self.SECRET.encode(), content_type="text/plain"), _call_next(200)
        )
        assert self.SECRET not in caplog.text
        assert not any(r.levelno == logging.DEBUG and r.name == "app.main" for r in caplog.records)

    @pytest.mark.skipif(not _HAS_PAYLOAD_SHAPE, reason="GPB-ahead: body shape redaction")
    async def test_missing_content_type_header(self, caplog):
        caplog.set_level(logging.DEBUG, logger="app.main")
        await main.log_requests(_FakeRequest(body=b'{"a": 1}', content_type=None), _call_next(200))
        assert not any(r.levelno == logging.DEBUG and r.name == "app.main" for r in caplog.records)

    @pytest.mark.skipif(not _HAS_PAYLOAD_SHAPE, reason="GPB-ahead: body shape redaction")
    async def test_empty_body_skipped(self, caplog):
        caplog.set_level(logging.DEBUG, logger="app.main")
        await main.log_requests(_FakeRequest(body=b""), _call_next(200))
        assert not any(r.levelno == logging.DEBUG and r.name == "app.main" for r in caplog.records)

    @pytest.mark.skipif(not _HAS_PAYLOAD_SHAPE, reason="GPB-ahead: body shape redaction")
    async def test_invalid_json_is_swallowed_and_not_echoed(self, caplog):
        caplog.set_level(logging.DEBUG, logger="app.main")
        resp = await main.log_requests(
            _FakeRequest(body=("{" + self.SECRET).encode()), _call_next(422)
        )
        assert resp.status_code == 422
        assert self.SECRET not in caplog.text

    @pytest.mark.skipif(not _HAS_PAYLOAD_SHAPE, reason="GPB-ahead: body shape redaction")
    async def test_body_read_error_is_logged_and_request_continues(self, caplog):
        caplog.set_level(logging.DEBUG, logger="app.main")
        resp = await main.log_requests(
            _FakeRequest(body_error=RuntimeError("stream closed")), _call_next(200)
        )
        assert resp.status_code == 200
        assert "body read error stream closed" in caplog.text

    @pytest.mark.skipif(not _HAS_IS_QUIET, reason="GPB-ahead: quiet probe paths")
    async def test_quiet_post_to_probe_path_does_not_read_body(self, caplog, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_LOG_QUIET_PATHS", "/v1/embeddings")
        req = _FakeRequest("GET", "/v1/embeddings", body_error=AssertionError("body must not be read"))
        await main.log_requests(req, _call_next(200))

    @pytest.mark.xfail(strict=False, reason="known: query/passages ещё пишутся в INFO-логи memo")
    async def test_end_to_end_content_never_in_info_logs(self, disk_models, caplog, capsys):
        caplog.set_level(logging.DEBUG)
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://svc") as c:
            r = await c.post("/v1/rerank", json={"query": self.SECRET, "passages": [self.SECRET + "-2"]})
        assert r.status_code == 200
        out = capsys.readouterr()
        assert self.SECRET not in caplog.text + out.out + out.err
        assert "query=<" in caplog.text  # форма запроса записана
