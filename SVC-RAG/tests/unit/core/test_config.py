"""core.config: слияние urls/env/providers, нормализация адресов, загрузка Settings из yml, get_settings и диагностика."""

from __future__ import annotations

import pytest
import yaml

from app.core import config as cfg

pytestmark = pytest.mark.unit

ENV_KEYS = (
    "DOCKER_ENV", "RAG_MODELS_CLIENT_BASE_URL", "RAG_MODELS_SERVICE_URL", "RAG_API_KEY",
    "RAG_MODELS_CLIENT_EMBED_BATCH_SIZE", "RAG_MODELS_CLIENT_EMBED_CONCURRENCY", "RAG_MODELS_PROVIDER",
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "RAG_EMBEDDING_DIM",
    "backend_service_port", "BACKEND_SERVICE_PORT", "RAG_MEMPROBE", "RAG_HYBRID_MAX_CHUNKS_PER_DOCUMENT",
    "RAG_VECTOR_MAX_CHUNKS_PER_DOCUMENT", "RAG_BM25_RETAIN_SECONDS", "RAG_BM25_STORE_ENABLED",
    "RAG_BM25_MALLOC_TRIM",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DOCKER_ENV", "false")


# ───────────────────────────── env-хелперы ─────────────────────────────


class TestEnvHelpers:
    def test_env_str(self, monkeypatch):
        assert cfg._env_str("NOPE_X") is None
        monkeypatch.setenv("X_VAL", "  значение ")
        assert cfg._env_str("X_VAL") == "значение"
        monkeypatch.setenv("X_VAL", "   ")
        assert cfg._env_str("X_VAL") is None

    def test_env_int(self, monkeypatch):
        assert cfg._env_int("NOPE_X") is None
        monkeypatch.setenv("X_NUM", " 42 ")
        assert cfg._env_int("X_NUM") == 42
        monkeypatch.setenv("X_NUM", "abc")
        with pytest.raises(ValueError):
            cfg._env_int("X_NUM")

    def test_docker_runtime_env_overrides_filesystem(self, monkeypatch):
        monkeypatch.setenv("DOCKER_ENV", "TRUE")
        assert cfg._docker_runtime() is True
        monkeypatch.setenv("DOCKER_ENV", "no")
        assert cfg._docker_runtime() is False

    def test_docker_runtime_checks_dockerenv_file(self, monkeypatch):
        monkeypatch.delenv("DOCKER_ENV")
        monkeypatch.setattr(cfg.os.path, "exists", lambda p: p == "/.dockerenv")
        assert cfg._docker_runtime() is True
        monkeypatch.setattr(cfg.os.path, "exists", lambda p: False)
        assert cfg._docker_runtime() is False

    def test_pick_service_url(self, monkeypatch):
        urls = {"svc_docker": " http://docker:1/ ", "svc_port": "http://host:2/"}
        assert cfg._pick_service_url(urls, "svc_docker", "svc_port") == "http://host:2"
        assert cfg._pick_service_url({"svc_docker": "http://docker:1/"}, "svc_docker", "svc_port") == "http://docker:1"
        monkeypatch.setenv("DOCKER_ENV", "true")
        assert cfg._pick_service_url(urls, "svc_docker", "svc_port") == "http://docker:1"
        assert cfg._pick_service_url({}, "svc_docker", "svc_port") == ""
        assert cfg._pick_service_url(None, "a", "b") == ""


# ───────────────────────────── urls ─────────────────────────────


class TestApplyUrls:
    def test_data_without_urls_is_returned_untouched(self):
        data = {"a": 1}

        assert cfg._apply_urls_section(data) is data

    def test_fills_missing_sections_and_drops_urls(self):
        data = {"urls": {
            "rag_models_service_port": "http://rm:1", "ocr_service_port": "http://ocr:2",
            "llm_service_port": "http://llm:3", "backend_service_port": "http://be:4",
            "frontend_port": "http://fe:5", "frontend_port_ipv4": " ", "backend_port": "http://be:6",
        }}

        out = cfg._apply_urls_section(data)

        assert "urls" not in out and "urls" in data
        assert out["rag_models_client"]["base_url"] == "http://rm:1" and out["ocr"]["url"] == "http://ocr:2"
        assert out["llm_service"]["base_url"] == "http://llm:3" and out["backend"]["base_url"] == "http://be:4"
        assert out["cors"]["allowed_origins"] == ["http://fe:5", "http://be:6"]

    def test_explicit_values_are_not_overridden(self):
        data = {
            "urls": {"rag_models_service_port": "http://rm:1", "ocr_service_port": "http://ocr:2",
                     "llm_service_port": "http://llm:3", "backend_service_port": "http://be:4", "frontend_port": "http://fe"},
            "rag_models_client": {"base_url": "http://own-rm", "timeout": 3},
            "ocr": {"url": "http://own-ocr"}, "llm_service": {"base_url": "http://own-llm"},
            "backend": {"base_url": "http://own-be"}, "cors": {"allowed_origins": ["http://x"]},
        }

        out = cfg._apply_urls_section(data)

        assert out["rag_models_client"] == {"base_url": "http://own-rm", "timeout": 3}
        assert out["ocr"]["url"] == "http://own-ocr" and out["llm_service"]["base_url"] == "http://own-llm"
        assert out["backend"]["base_url"] == "http://own-be" and out["cors"]["allowed_origins"] == ["http://x"]

    def test_wildcard_cors_is_replaced_but_only_when_urls_exist(self):
        out = cfg._apply_urls_section({"urls": {"frontend_port": "http://fe"}, "cors": {"allowed_origins": ["*"]}})
        assert out["cors"]["allowed_origins"] == ["http://fe"]

        out = cfg._apply_urls_section({"urls": {"other": "x"}, "cors": {"allowed_origins": ["*"]}})
        assert out["cors"]["allowed_origins"] == ["*"]

    def test_no_urls_for_service_keeps_section_empty(self):
        out = cfg._apply_urls_section({"urls": {"other": "x"}})

        assert out["rag_models_client"] == {} and out["ocr"] == {} and out["llm_service"] == {} and out["backend"] == {}


# ───────────────────────────── провайдеры ─────────────────────────────

PROVIDERS = {"rag_models": {"provider": "native", "providers": [
    {"id": "CORSUR", "base_url": "https://llm.corsur/", "api_key_env": "CORSUR_KEY", "chat_only": True},
    {"id": "PHOENIX", "base_url": "http://phoenix:8080/", "api_key_env": "PHOENIX_KEY"},
    {"id": "other", "base_url": "http://other/", "api_key_env": "OTHER_KEY"},
]}}


class TestEmbeddingGateway:
    def test_preferred_from_yaml_provider(self):
        data = {"rag_models": {"provider": "other", "providers": PROVIDERS["rag_models"]["providers"]}}

        assert cfg._embedding_gateway_from_providers(data) == ("http://other", "OTHER_KEY")

    def test_env_provider_beats_yaml_and_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_PROVIDER", "phoenix")

        assert cfg._embedding_gateway_from_providers(PROVIDERS) == ("http://phoenix:8080", "PHOENIX_KEY")

    def test_falls_back_to_corsur_then_phoenix_then_any(self):
        no_base = {"rag_models": {"provider": "missing", "providers": [
            {"id": "CORSUR", "base_url": ""}, {"id": "PHOENIX", "base_url": "http://p/"},
        ]}}
        assert cfg._embedding_gateway_from_providers(no_base) == ("http://p", "")

        only_other = {"rag_models": {"providers": [{"id": "x", "base_url": ""}, {"id": "y", "base_url": "http://y/", "api_key_env": "K"}]}}
        assert cfg._embedding_gateway_from_providers(only_other) == ("http://y", "K")

    def test_nothing_configured(self):
        assert cfg._embedding_gateway_from_providers({}) == ("", "")
        assert cfg._embedding_gateway_from_providers({"rag_models": {"providers": [{"id": "x"}]}}) == ("", "")

    def test_duplicate_preferred_ids_are_visited_once(self):
        data = {"rag_models": {"provider": "corsur", "providers": [{"id": "CORSUR", "base_url": "http://c/", "api_key_env": "K"}]}}

        assert cfg._embedding_gateway_from_providers(data) == ("http://c", "K")


class TestBaseUrlHelpers:
    @pytest.mark.parametrize("raw,expected", [
        ("HTTPS://Host:443/", "https://host"), ("http://host:80", "http://host"), ("http://host:8080/", "http://host:8080"),
        ("https://host:80", "https://host:80"), (None, ""), ("  ", ""),
    ])
    def test_normalize(self, raw, expected):
        assert cfg._normalize_base_url(raw) == expected

    def test_provider_entry_by_base_url(self):
        assert cfg._provider_entry_by_base_url(PROVIDERS, "HTTPS://llm.corsur:443")["id"] == "CORSUR"
        assert cfg._provider_entry_by_base_url(PROVIDERS, "http://unknown") == {}
        assert cfg._provider_entry_by_base_url(PROVIDERS, "") == {}
        assert cfg._provider_entry_by_base_url({}, "http://x") == {}

    def test_chat_only_flag_comes_from_config_not_hostname(self):
        assert cfg._is_chat_only_llm_gateway(PROVIDERS, "https://llm.corsur") is True
        assert cfg._is_chat_only_llm_gateway(PROVIDERS, "http://phoenix:8080") is False
        assert cfg._is_chat_only_llm_gateway(PROVIDERS, "https://new.llm.corsur") is False


class TestRagModelsClientEnv:
    def test_env_base_url_and_key_override_yaml(self, monkeypatch, capsys):
        monkeypatch.setenv("RAG_MODELS_SERVICE_URL", "http://svc/")
        monkeypatch.setenv("RAG_API_KEY", "KEYNAME")

        out = cfg._apply_rag_models_client_env({"rag_models_client": {"base_url": "http://yaml", "timeout": 1}})

        assert out["rag_models_client"] == {"base_url": "http://svc", "timeout": 1, "api_key_env": "KEYNAME"}
        assert "rag_models_client.base_url <- env" in capsys.readouterr().out

    def test_client_base_url_env_has_priority_over_service_url(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_CLIENT_BASE_URL", "http://client")
        monkeypatch.setenv("RAG_MODELS_SERVICE_URL", "http://service")

        assert cfg._apply_rag_models_client_env({})["rag_models_client"]["base_url"] == "http://client"

    def test_api_key_value_is_not_printed_to_stdout(self, monkeypatch, capsys):
        # BUG-RAG-23: значение RAG_API_KEY не должно попадать в stdout / логи пода.
        monkeypatch.setenv("RAG_API_KEY", "sk-very-secret")

        out = cfg._apply_rag_models_client_env({})

        assert "sk-very-secret" not in capsys.readouterr().out
        assert out["rag_models_client"]["api_key_env"] == "sk-very-secret"

    def test_batch_and_concurrency_from_env_are_clamped(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_CLIENT_EMBED_BATCH_SIZE", "0")
        monkeypatch.setenv("RAG_MODELS_CLIENT_EMBED_CONCURRENCY", "8")

        rmc = cfg._apply_rag_models_client_env({})["rag_models_client"]

        assert rmc["embed_batch_size"] == 1 and rmc["embed_concurrency"] == 8

    def test_garbage_numbers_are_ignored(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_CLIENT_EMBED_BATCH_SIZE", "много")
        monkeypatch.setenv("RAG_MODELS_CLIENT_EMBED_CONCURRENCY", "x")

        assert cfg._apply_rag_models_client_env({})["rag_models_client"] == {}

    def test_no_env_keeps_data(self):
        assert cfg._apply_rag_models_client_env({"rag_models_client": {"base_url": "u"}})["rag_models_client"] == {"base_url": "u"}


class TestSanitizeClientUrl:
    def test_chat_only_gateway_is_replaced_with_embedding_gateway(self, capsys):
        providers = {"rag_models": {"provider": "PHOENIX", "providers": PROVIDERS["rag_models"]["providers"]}}
        data = dict(providers, rag_models_client={"base_url": "https://llm.corsur/"})

        out = cfg._sanitize_rag_models_client_url(data)["rag_models_client"]

        assert out["base_url"] == "http://phoenix:8080"
        assert "chat-шлюз не обслуживает" in capsys.readouterr().out

    def test_chat_only_gateway_that_is_also_preferred_provider_is_left_alone(self):
        data = dict(PROVIDERS, rag_models_client={"base_url": "https://llm.corsur/"})

        out = cfg._sanitize_rag_models_client_url(data)["rag_models_client"]

        assert out["base_url"] == "https://llm.corsur/"

    def test_chat_only_gateway_keeps_explicit_key(self):
        providers = {"rag_models": {"provider": "PHOENIX", "providers": PROVIDERS["rag_models"]["providers"]}}
        data = dict(providers, rag_models_client={"base_url": "https://llm.corsur", "api_key_env": "MINE"})

        out = cfg._sanitize_rag_models_client_url(data)["rag_models_client"]

        assert out["base_url"] == "http://phoenix:8080" and out["api_key_env"] == "MINE"

    def test_chat_only_gateway_takes_provider_key_when_empty(self):
        providers = {"rag_models": {"provider": "PHOENIX", "providers": PROVIDERS["rag_models"]["providers"]}}
        data = dict(providers, rag_models_client={"base_url": "https://llm.corsur"})

        out = cfg._sanitize_rag_models_client_url(data)["rag_models_client"]

        assert out["base_url"] == "http://phoenix:8080" and out["api_key_env"] == "PHOENIX_KEY"

    def test_chat_only_gateway_without_alternative_stays(self):
        data = {"rag_models": {"providers": [{"id": "CORSUR", "base_url": "https://only/", "chat_only": True}]},
                "rag_models_client": {"base_url": "https://only"}}

        assert cfg._sanitize_rag_models_client_url(data)["rag_models_client"]["base_url"] == "https://only"

    def test_regular_url_gets_provider_key_if_missing(self):
        providers = {"rag_models": {"provider": "PHOENIX", "providers": PROVIDERS["rag_models"]["providers"]}}

        out = cfg._sanitize_rag_models_client_url(dict(providers, rag_models_client={"base_url": "http://rm"}))["rag_models_client"]
        assert out == {"base_url": "http://rm", "api_key_env": "PHOENIX_KEY"}

        kept = cfg._sanitize_rag_models_client_url(dict(providers, rag_models_client={"base_url": "http://rm", "api_key_env": "K"}))
        assert kept["rag_models_client"]["api_key_env"] == "K"

    def test_no_providers_no_key(self):
        assert cfg._sanitize_rag_models_client_url({"rag_models_client": {"base_url": "http://rm"}})["rag_models_client"] == {"base_url": "http://rm"}


# ───────────────────────────── прочие env-оверрайды ─────────────────────────────


class TestOverrides:
    def test_postgres_env_overrides_yaml(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_HOST", "pg")
        monkeypatch.setenv("POSTGRES_PORT", "6432")
        monkeypatch.setenv("POSTGRES_DB", "db")
        monkeypatch.setenv("POSTGRES_USER", "u")
        monkeypatch.setenv("POSTGRES_PASSWORD", "p")
        monkeypatch.setenv("RAG_EMBEDDING_DIM", "1024")

        out = cfg._apply_postgres_env_overrides({"postgresql": {"host": "yaml", "extra": 1}})

        assert out["postgresql"] == {"host": "pg", "extra": 1, "port": 6432, "database": "db", "user": "u",
                                     "password": "p", "embedding_dim": 1024}

    def test_postgres_without_env_keeps_yaml(self):
        assert cfg._apply_postgres_env_overrides({"postgresql": {"host": "yaml"}})["postgresql"] == {"host": "yaml"}
        assert cfg._apply_postgres_env_overrides({})["postgresql"] == {}

    def test_backend_service_env(self, monkeypatch):
        assert cfg._apply_backend_service_env({"backend": {"base_url": "yaml"}})["backend"]["base_url"] == "yaml"
        monkeypatch.setenv("BACKEND_SERVICE_PORT", " http://upper/ ")
        assert cfg._apply_backend_service_env({})["backend"]["base_url"] == "http://upper"
        monkeypatch.setenv("backend_service_port", "http://lower/")
        assert cfg._apply_backend_service_env({"backend": {"timeout": 1}})["backend"] == {"timeout": 1, "base_url": "http://lower"}

    def test_rag_env_overrides_cast_values(self, monkeypatch, capsys):
        monkeypatch.setenv("RAG_MEMPROBE", " FULL ")
        monkeypatch.setenv("RAG_HYBRID_MAX_CHUNKS_PER_DOCUMENT", "3")
        monkeypatch.setenv("RAG_VECTOR_MAX_CHUNKS_PER_DOCUMENT", "4")
        monkeypatch.setenv("RAG_BM25_RETAIN_SECONDS", "2.5")
        monkeypatch.setenv("RAG_BM25_STORE_ENABLED", "False")
        monkeypatch.setenv("RAG_BM25_MALLOC_TRIM", "TRUE")

        out = cfg._apply_rag_env_overrides({"rag": {"chunk_size": 10, "memprobe": "on"}})

        assert out["rag"] == {"chunk_size": 10, "memprobe": "full", "hybrid_max_chunks_per_document": 3,
                              "vector_max_chunks_per_document": 4, "bm25_retain_seconds": 2.5,
                              "bm25_store_enabled": False, "bm25_malloc_trim": True}
        assert "rag.memprobe <- env RAG_MEMPROBE" in capsys.readouterr().out

    def test_rag_env_garbage_is_ignored(self, monkeypatch, capsys):
        monkeypatch.setenv("RAG_BM25_RETAIN_SECONDS", "быстро")

        data = {"rag": {"bm25_retain_seconds": 2.0}}
        out = cfg._apply_rag_env_overrides(data)

        assert out["rag"] == {"bm25_retain_seconds": 2.0}
        assert "невалиден" in capsys.readouterr().out

    def test_rag_env_nothing_set_returns_copy_without_rag_key(self):
        assert cfg._apply_rag_env_overrides({"x": 1}) == {"x": 1}

    def test_provider_env_beats_yaml(self, monkeypatch, capsys):
        assert cfg._apply_rag_models_provider_env({"rag_models": {"provider": "yaml"}})["rag_models"]["provider"] == "yaml"
        monkeypatch.setenv("RAG_MODELS_PROVIDER", " PHOENIX ")

        out = cfg._apply_rag_models_provider_env({"rag_models": {"provider": "yaml", "providers": []}})

        assert out["rag_models"] == {"provider": "PHOENIX", "providers": []}
        assert "RAG_MODELS_PROVIDER" in capsys.readouterr().out


# ───────────────────────────── модели и загрузка ─────────────────────────────

MINIMAL = {
    "rag_models_client": {"base_url": "http://rm:1", "timeout": 5},
    "postgresql": {"host": "h", "port": 5432, "database": "d", "user": "u", "password": "p", "embedding_dim": 8},
    "minio": {"endpoint": "m", "port": 9000, "access_key": "a", "secret_key": "s", "use_ssl": False,
              "bucket_name": "b", "documents_bucket_name": "d"},
    "ocr": {"url": "http://ocr", "timeout": 5},
    "llm_service": {"base_url": "http://llm", "timeout": 5, "default_model": "m"},
}


def write_yaml(tmp_path, data, name="config.yml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(path)


class TestDefaults:
    def test_model_defaults(self):
        assert cfg.ServerConfig().port == 8000 and cfg.ServerConfig().docs_url == "/docs"
        assert cfg.CorsConfig().allowed_origins == [] and cfg.AppConfig().title == "RAG Service"
        assert cfg.LoggingConfig().noisy_level == "WARNING"
        assert cfg.AuditConfig().cef_polling is True and cfg.AuditConfig().cef_polling_interval_seconds == 120
        entry = cfg.RagModelsProviderEntry(id="x")
        assert entry.chat_only is False and entry.embed_path == "/v1/embeddings" and entry.extra_headers == {}
        assert cfg.RagModelsProvidersConfig().provider == "native"
        assert cfg.BackendServiceConfig().timeout == 120.0

    def test_chat_only_flag_survives_pydantic_validation(self):
        entry = cfg.RagModelsProviderEntry(**{"id": "CORSUR", "chat_only": True})

        assert entry.chat_only is True


class TestFromYaml:
    def test_loads_minimal_yaml_with_defaults(self, tmp_path):
        s = cfg.Settings.from_yaml(write_yaml(tmp_path, MINIMAL))

        assert s.postgresql.embedding_dim == 8 and s.rag_models_client.base_url == "http://rm:1"
        assert s.rag.enabled is True and s.server.port == 8000 and s.backend.base_url == ""

    def test_applies_urls_and_env_overrides(self, tmp_path, monkeypatch):
        data = dict(MINIMAL)
        data["ocr"] = {"timeout": 5}
        data["urls"] = {"ocr_service_port": "http://ocr-from-urls:1", "backend_service_port": "http://be:2"}
        monkeypatch.setenv("POSTGRES_HOST", "env-host")
        monkeypatch.setenv("RAG_EMBEDDING_DIM", "16")
        monkeypatch.setenv("RAG_MODELS_PROVIDER", "PHOENIX")
        monkeypatch.setenv("RAG_BM25_RETAIN_SECONDS", "7")

        s = cfg.Settings.from_yaml(write_yaml(tmp_path, data))

        assert s.ocr.url == "http://ocr-from-urls:1" and s.backend.base_url == "http://be:2"
        assert s.postgresql.host == "env-host" and s.postgresql.embedding_dim == 16
        assert s.rag_models.provider == "PHOENIX" and s.rag.bm25_retain_seconds == 7.0

    def test_invalid_config_is_wrapped_with_path(self, tmp_path):
        path = write_yaml(tmp_path, {"postgresql": {"host": "h"}})

        with pytest.raises(ValueError) as exc:
            cfg.Settings.from_yaml(path)

        assert "Ошибка загрузки конфига" in str(exc.value) and path in str(exc.value)

    def test_missing_file_is_wrapped(self, tmp_path):
        with pytest.raises(ValueError, match="Ошибка загрузки конфига"):
            cfg.Settings.from_yaml(str(tmp_path / "nope.yml"))

    def test_empty_yaml_fails_validation(self, tmp_path):
        path = tmp_path / "empty.yml"
        path.write_text("", encoding="utf-8")

        with pytest.raises(ValueError, match="Ошибка загрузки конфига"):
            cfg.Settings.from_yaml(str(path))

    def test_non_numeric_postgres_port_env_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_PORT", "abc")

        with pytest.raises(ValueError, match="Ошибка загрузки конфига"):
            cfg.Settings.from_yaml(write_yaml(tmp_path, MINIMAL))

    def test_autodiscovery_uses_first_existing_candidate(self, tmp_path, monkeypatch):
        path = write_yaml(tmp_path, MINIMAL)
        monkeypatch.setattr(cfg.os.path, "exists", lambda p: p == "config/config.yml")
        opened = []
        real_open = open

        def fake_open(p, *a, **k):
            opened.append(p)
            return real_open(path, *a, **k)

        monkeypatch.setattr("builtins.open", fake_open)

        s = cfg.Settings.from_yaml("")

        assert opened == ["config/config.yml"] and s.postgresql.database == "d"

    def test_autodiscovery_fails_when_nothing_found(self, monkeypatch):
        monkeypatch.setattr(cfg.os.path, "exists", lambda p: False)

        with pytest.raises(ValueError, match="config.yml не найден"):
            cfg.Settings.from_yaml(None)


class TestGetSettings:
    def test_loads_once_from_config_path_and_caches(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "_settings", None)
        monkeypatch.setenv("CONFIG_PATH", write_yaml(tmp_path, MINIMAL))

        first = cfg.get_settings()
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "other.yml"))

        assert first.postgresql.host == "h" and cfg.get_settings() is first


class TestDiagnostics:
    def test_summary_has_no_secrets(self, tmp_path, monkeypatch):
        data = dict(MINIMAL)
        data["rag_models"] = {"provider": "PHOENIX", "providers": [{"id": "PHOENIX", "base_url": "http://p"}]}
        data["rag_models_client"] = {"base_url": "http://rm", "timeout": 5, "api_key_env": "KEY_NAME"}
        settings = cfg.Settings.from_yaml(write_yaml(tmp_path, data))
        monkeypatch.setenv("CONFIG_PATH", " /etc/rag.yml ")

        diag = cfg.get_settings_diagnostics(settings)

        assert diag["config_source"] == "/etc/rag.yml" and diag["docker_runtime"] is False
        assert diag["rag_models"]["providers_configured"] == ["PHOENIX"] and diag["rag_models"]["api_key_env"] == "KEY_NAME"
        assert diag["postgresql"] == {"host": "h", "port": 5432, "database": "d", "user": "u", "embedding_dim": 8}
        assert diag["llm_service"]["default_model"] == "m" and diag["rag"]["vector_index_method"] == "hnsw"
        flat = repr(diag)
        assert "password" not in flat and "secret_key" not in flat and "access_key" not in flat

    def test_defaults_to_global_settings_and_autodiscovered_source(self, tmp_path, monkeypatch):
        settings = cfg.Settings.from_yaml(write_yaml(tmp_path, MINIMAL))
        monkeypatch.setattr(cfg, "_settings", settings)
        monkeypatch.delenv("CONFIG_PATH", raising=False)

        diag = cfg.get_settings_diagnostics()

        assert diag["config_source"] == "auto-discovered config.yml" and diag["rag_models"]["api_key_env"] is None
        assert diag["server"] == {"host": "0.0.0.0", "port": 8000}
