# -*- coding: utf-8 -*-
"""Тесты backend.settings.service_toggles: флаги микросервисов и обработчик ServiceDisabledError (503)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.settings import service_toggles as st  # noqa: E402

_ENV_NAMES = [env for env, _ in st._SERVICES.values()]


@pytest.fixture(autouse=True)
def clean_flags(monkeypatch):
    """Ни одна переменная *_SERVICE_ENABLED не влияет на тест; конфиг — пустой."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(st, "get_settings", lambda: SimpleNamespace())
    return monkeypatch


class TestEnvBool:
    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " On "])
    def test_true_values(self, monkeypatch, raw):
        monkeypatch.setenv("X_FLAG", raw)
        assert st._env_bool("X_FLAG") is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "anything-else"])
    def test_false_values(self, monkeypatch, raw):
        monkeypatch.setenv("X_FLAG", raw)
        assert st._env_bool("X_FLAG") is False

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_unset_or_blank_is_none(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv("X_FLAG", raising=False)
        else:
            monkeypatch.setenv("X_FLAG", raw)
        assert st._env_bool("X_FLAG") is None


class TestIsServiceEnabled:
    def test_default_is_disabled(self):
        assert st.is_service_enabled("stt") is False

    def test_env_enables(self, monkeypatch):
        monkeypatch.setenv("STT_SERVICE_ENABLED", "true")
        assert st.is_service_enabled("stt") is True

    def test_env_beats_yaml(self, monkeypatch):
        monkeypatch.setattr(st, "get_settings", lambda: SimpleNamespace(microservices_enabled=SimpleNamespace(stt=True)))
        monkeypatch.setenv("STT_SERVICE_ENABLED", "false")
        assert st.is_service_enabled("stt") is False

    def test_yaml_flag_used_without_env(self, monkeypatch):
        monkeypatch.setattr(st, "get_settings", lambda: SimpleNamespace(microservices_enabled=SimpleNamespace(tts=True)))
        assert st.is_service_enabled("tts") is True

    def test_microservices_dict_block(self, monkeypatch):
        cfg = SimpleNamespace(microservices={"diarization": {"enabled": True}})
        monkeypatch.setattr(st, "get_settings", lambda: cfg)
        assert st.is_service_enabled("diarization") is True

    def test_agents_alias_to_llm_block(self, monkeypatch):
        cfg = SimpleNamespace(microservices={"llm": {"enabled": True}})
        monkeypatch.setattr(st, "get_settings", lambda: cfg)
        monkeypatch.setenv("POSTGRESQL_SERVICE_ENABLED", "true")
        assert st.is_service_enabled("agents") is True

    def test_minio_enabled_when_endpoint_configured(self, monkeypatch):
        monkeypatch.setattr(st, "get_settings", lambda: SimpleNamespace(minio=SimpleNamespace(endpoint="minio")))
        assert st.is_service_enabled("minio") is True

    def test_env_flag_overrides_configured_endpoint(self, monkeypatch):
        monkeypatch.setattr(st, "get_settings", lambda: SimpleNamespace(minio=SimpleNamespace(endpoint="minio")))
        monkeypatch.setenv("MINIO_SERVICE_ENABLED", "false")
        assert st.is_service_enabled("minio") is False

    def test_postgresql_enabled_when_host_configured(self, monkeypatch):
        monkeypatch.setattr(st, "get_settings", lambda: SimpleNamespace(postgresql=SimpleNamespace(host="pg")))
        assert st.is_service_enabled("postgresql") is True

    def test_rag_requires_postgresql(self, monkeypatch):
        monkeypatch.setenv("RAG_SERVICE_ENABLED", "true")
        assert st.is_service_enabled("rag") is False
        monkeypatch.setenv("POSTGRESQL_SERVICE_ENABLED", "true")
        assert st.is_service_enabled("rag") is True

    def test_dependency_can_disable_dependent(self, monkeypatch):
        monkeypatch.setenv("AGENTS_SERVICE_ENABLED", "true")
        monkeypatch.setenv("POSTGRESQL_SERVICE_ENABLED", "false")
        assert st.is_service_enabled("agents") is False

    def test_mcp_uses_settings_not_env(self, monkeypatch):
        monkeypatch.setattr(st, "get_settings", lambda: SimpleNamespace(mcp=SimpleNamespace(enabled=True)))
        monkeypatch.setenv("MCP_ENABLED", "false")
        assert st.is_service_enabled("mcp") is True

    def test_broken_settings_mean_disabled(self, monkeypatch):
        def boom():
            raise RuntimeError("no settings")

        monkeypatch.setattr(st, "get_settings", boom)
        assert st.is_service_enabled("stt") is False

    def test_unknown_service_defaults_to_false(self):
        assert st.is_service_enabled("nope") is False


class TestServiceDisabledError:
    def test_display_name(self):
        assert st.display_name("rag") == "RAG (поиск по документам)"
        assert st.display_name("unknown") == "unknown"

    def test_message_and_key(self):
        err = st.ServiceDisabledError("rag")
        assert err.key == "rag"
        assert "RAG (поиск по документам)" in str(err)
        assert "в разработке" in str(err)

    def test_require_service(self, monkeypatch):
        with pytest.raises(st.ServiceDisabledError) as exc:
            st.require_service("stt")
        assert exc.value.key == "stt"
        monkeypatch.setenv("STT_SERVICE_ENABLED", "true")
        st.require_service("stt")  # не бросает


def build_app(*, with_handler=True):
    """Минимальное приложение: тот же порядок, что в main.py (CORS снаружи обработчика)."""
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if with_handler:
        st.register_service_disabled_handler(app)

    @app.get("/rag")
    def rag_endpoint():
        st.require_service("rag")
        return {"ok": True}

    @app.get("/broken")
    def broken_endpoint():
        raise ValueError("настоящая ошибка")

    return app


ORIGIN = {"Origin": "http://localhost:3000"}


class TestServiceDisabledHandler:
    def test_disabled_service_gives_503_json(self):
        client = TestClient(build_app(), raise_server_exceptions=False)
        resp = client.get("/rag", headers=ORIGIN)
        assert resp.status_code == 503
        body = resp.json()
        assert body["service"] == "rag"
        assert "в разработке" in body["detail"]
        assert "RAG (поиск по документам)" in body["detail"]

    def test_response_carries_cors_headers(self):
        # Регрессия: без обработчика 500 уходил без CORS и в браузере выглядел как CORS-ошибка.
        client = TestClient(build_app(), raise_server_exceptions=False)
        resp = client.get("/rag", headers=ORIGIN)
        assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"

    def test_enabled_service_is_not_affected(self, monkeypatch):
        monkeypatch.setenv("RAG_SERVICE_ENABLED", "true")
        monkeypatch.setenv("POSTGRESQL_SERVICE_ENABLED", "true")
        client = TestClient(build_app(), raise_server_exceptions=False)
        resp = client.get("/rag", headers=ORIGIN)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_other_errors_stay_500(self):
        client = TestClient(build_app(), raise_server_exceptions=False)
        assert client.get("/broken", headers=ORIGIN).status_code == 500

    def test_without_handler_it_is_a_bare_500_without_cors(self):
        """Фиксирует исходный дефект: на это опирается ценность обработчика."""
        client = TestClient(build_app(with_handler=False), raise_server_exceptions=False)
        resp = client.get("/rag", headers=ORIGIN)
        assert resp.status_code == 500
        assert "access-control-allow-origin" not in resp.headers


def test_main_registers_the_handler():
    """backend.main подключает обработчик (иначе фича-флаги снова дадут голый 500)."""
    try:
        from backend.main import app as main_app
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"backend.main не импортируется в этом окружении: {exc}")
    assert st.ServiceDisabledError in main_app.exception_handlers
