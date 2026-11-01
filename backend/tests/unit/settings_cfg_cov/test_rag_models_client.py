"""settings/rag_models_client: URL, таймауты и обработка ошибок HTTP-клиента SVC-RAG-MODELS."""

import json

import httpx
import pytest
import respx

from backend.settings import rag_models_client as rmc

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("RAG_MODELS_SERVICE_URL", raising=False)
    monkeypatch.delenv("RAG_MODELS_HTTP_TIMEOUT", raising=False)
    monkeypatch.delenv("RAG_MODELS_API_KEY", raising=False)
    monkeypatch.delenv("RAG_MODELS_API_KEY_ENV", raising=False)
    monkeypatch.setattr(rmc, "_rag_models_client_singleton", None)


class TestRequestUrl:
    @pytest.mark.parametrize(
        ("base", "path", "expected"),
        [
            ("http://h:1/", "/models", "http://h:1/v1/models"),
            (" http://h:1 ", "health", "http://h:1/v1/health"),
            ("", "/x", "/v1/x"),
            (None, "/x", "/v1/x"),
        ],
    )
    def test_builds_v1_url(self, base, path, expected):
        assert rmc._rag_models_request_url(base, path) == expected


class TestInit:
    def test_explicit_base_url_is_stripped(self):
        client = rmc.RagModelsClient(base_url=" http://explicit:9/ ")

        assert client.base_url == "http://explicit:9"
        assert client.timeout == 600.0

    def test_env_url_used_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_SERVICE_URL", " http://env:7/ ")

        assert rmc.RagModelsClient().base_url == "http://env:7"

    def test_falls_back_to_settings(self):
        # в тестовом конфиге задан urls.rag_models_service_port
        assert rmc.RagModelsClient().base_url == "http://rag-models.test:8010"

    def test_falls_back_to_localhost_when_settings_have_no_url(self, monkeypatch):
        class _NoUrl:
            def microservice_http_base(self, *_a):
                msg = "no url"
                raise ValueError(msg)

        monkeypatch.setattr(rmc, "get_settings", lambda: _NoUrl())

        assert rmc.RagModelsClient().base_url == "http://localhost:8010"

    def test_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_HTTP_TIMEOUT", "12.5")

        assert rmc.RagModelsClient(base_url="http://x").timeout == 12.5

    def test_timeout_from_argument(self):
        assert rmc.RagModelsClient(base_url="http://x", timeout=3).timeout == 3


@pytest.fixture
def client():
    return rmc.RagModelsClient(base_url="http://rm.test")


class TestRequests:
    @respx.mock
    async def test_list_models_without_type(self, client):
        route = respx.get("http://rm.test/v1/models").respond(json={"models": []})

        assert await client.list_models() == {"models": []}
        assert dict(route.calls.last.request.url.params) == {}

    @respx.mock
    async def test_list_models_with_type_passes_query(self, client):
        route = respx.get("http://rm.test/v1/models").respond(json={"models": ["a"]})

        assert await client.list_models("embed") == {"models": ["a"]}
        assert dict(route.calls.last.request.url.params) == {"type": "embed"}

    @respx.mock
    async def test_get_current(self, client):
        respx.get("http://rm.test/v1/models/current").respond(json={"embed": "m"})

        assert await client.get_current() == {"embed": "m"}

    @respx.mock
    async def test_select_model_sends_json_body(self, client):
        route = respx.post("http://rm.test/v1/models/select").respond(json={"ok": True})

        assert await client.select_model("embed", "/m/path") == {"ok": True}
        assert json.loads(route.calls.last.request.content) == {
            "model_type": "embed",
            "model_path": "/m/path",
        }

    @respx.mock
    async def test_health(self, client):
        respx.get("http://rm.test/v1/health").respond(json={"status": "ok"})

        assert await client.health() == {"status": "ok"}


class TestApiKey:
    """SVC-RAG-MODELS fail-closed (BUG-RM-01): backend передаёт общий ключ."""

    @respx.mock
    async def test_key_is_sent_in_both_headers(self, client, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_API_KEY", " k1 ")
        route = respx.get("http://rm.test/v1/models/current").respond(json={})

        await client.get_current()

        headers = route.calls.last.request.headers
        assert headers["authorization"] == "Bearer k1" and headers["x-api-key"] == "k1"

    @respx.mock
    async def test_key_from_indirect_env(self, client, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_API_KEY_ENV", "MY_RM_KEY")
        monkeypatch.setenv("MY_RM_KEY", "k2")
        route = respx.post("http://rm.test/v1/models/select").respond(json={})

        await client.select_model("embedding", "m")

        assert route.calls.last.request.headers["x-api-key"] == "k2"

    @respx.mock
    async def test_no_key_no_auth_headers(self, client):
        route = respx.get("http://rm.test/v1/models").respond(json={})

        await client.list_models()

        headers = route.calls.last.request.headers
        assert "authorization" not in headers and "x-api-key" not in headers


class TestErrors:
    @respx.mock
    async def test_error_detail_string(self, client):
        respx.get("http://rm.test/v1/health").respond(404, json={"detail": "not here"})

        with pytest.raises(httpx.HTTPStatusError) as exc:
            await client.health()

        assert "404" in str(exc.value)
        assert "not here" in str(exc.value)

    @respx.mock
    async def test_error_detail_list_is_joined(self, client):
        respx.get("http://rm.test/v1/health").respond(
            422, json={"detail": ["bad a", "bad b"]}
        )

        with pytest.raises(httpx.HTTPStatusError) as exc:
            await client.health()

        assert "bad a; bad b" in str(exc.value)

    @respx.mock
    async def test_error_message_field(self, client):
        respx.get("http://rm.test/v1/health").respond(500, json={"message": "boom"})

        with pytest.raises(httpx.HTTPStatusError) as exc:
            await client.health()

        assert "500" in str(exc.value) and "boom" in str(exc.value)

    @respx.mock
    async def test_non_json_error_body_is_truncated_text(self, client):
        respx.get("http://rm.test/v1/health").respond(502, text="x" * 600)

        with pytest.raises(httpx.HTTPStatusError) as exc:
            await client.health()

        assert str(exc.value).endswith(": " + "x" * 500)

    @respx.mock
    async def test_empty_error_body_has_no_detail(self, client):
        respx.get("http://rm.test/v1/health").respond(503, text="")

        with pytest.raises(httpx.HTTPStatusError) as exc:
            await client.health()

        assert str(exc.value) == "503 Service Unavailable"

    @respx.mock
    async def test_json_without_detail_keys(self, client):
        respx.get("http://rm.test/v1/health").respond(400, json={"other": 1})

        with pytest.raises(httpx.HTTPStatusError) as exc:
            await client.health()

        assert str(exc.value) == "400 Bad Request"


class TestSingleton:
    def test_returns_same_instance(self):
        first = rmc.get_rag_models_client()

        assert rmc.get_rag_models_client() is first
