"""settings/connections: сборка конфигов подключений из YAML и окружения."""

import pytest
from pydantic import ValidationError

from backend.settings import connections as c

ENV_NAMES = [
    "MONGODB_HOST", "MONGODB_PORT", "MONGODB_DATABASE", "MONGODB_USER", "MONGODB_PASSWORD",
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "EMBEDDING_DIM",
    "MINIO_ENDPOINT", "MINIO_PORT", "MINIO_ACCESS_KEY", "MINIO_ROOT_USER", "MINIO_SECRET_KEY",
    "MINIO_ROOT_PASSWORD", "MINIO_USE_SSL", "MINIO_BUCKET_NAME", "MINIO_DOCUMENTS_BUCKET_NAME",
    "MINIO_MEMORY_RAG_BUCKET_NAME", "MINIO_PROJECT_RAG_BUCKET_NAME", "MINIO_CA_CERT", "TLS_CERT_PATH",
    "SSL_CERT_FILE", "MINIO_SSL_VERIFY",
    "USE_LLM_SVC", "LLM_SVC_TIMEOUT", "LLM_SVC_RETRY_ATTEMPTS", "LLM_SVC_RETRY_DELAY", "LLM_API_KEY",
    "LLM_USE_AUTH", "LLM_VERIFY_SSL", "LLM_SKIP_HEALTH_PROBE",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


class TestEnvHelpers:
    def test_keys_are_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("MONGODB_HOST", "h")
        assert c._get_env_value("mongodb_host") == "h"
        assert c._get_env_value("NO_SUCH_VAR_X") is None

    def test_llm_api_key_branches(self, monkeypatch):
        assert c._get_env_value("LLM_API_KEY") is None
        monkeypatch.setenv("LLM_API_KEY", "k")
        assert c._get_env_value("LLM_API_KEY") == "k"

    @pytest.mark.parametrize("raw,expected", [("42", 42), (" 7 ", 7), ("", None), ("  ", None), ("abc", None)])
    def test_int_values(self, monkeypatch, raw, expected):
        monkeypatch.setenv("MONGODB_PORT", raw)
        assert c._get_env_value_int("MONGODB_PORT") == expected

    def test_int_missing(self):
        assert c._get_env_value_int("MONGODB_PORT") is None


class TestMongo:
    def test_defaults(self):
        m = c.MongoDBConnectionConfig()
        assert (m.host, m.port, m.database, m.user, m.password) == ("mongodb", 27017, "astrachat", None, None)
        assert m.connection_string == "mongodb://mongodb:27017/astrachat"

    def test_yaml_values(self):
        m = c.MongoDBConnectionConfig(host="y", port=1, database="d", user="u", password="p")
        assert m.connection_string == "mongodb://u:p@y:1/d"

    def test_env_overrides_yaml(self, monkeypatch):
        monkeypatch.setenv("MONGODB_HOST", "envhost")
        monkeypatch.setenv("MONGODB_PORT", "999")
        monkeypatch.setenv("MONGODB_DATABASE", "envdb")
        m = c.MongoDBConnectionConfig(host="y", port=1, database="d")
        assert (m.host, m.port, m.database) == ("envhost", 999, "envdb")

    def test_credentials_are_escaped(self):
        m = c.MongoDBConnectionConfig(user="us er", password="p@ss#%")
        assert m.connection_string == "mongodb://us+er:p%40ss%23%25@mongodb:27017/astrachat"

    def test_commented_out_env_credentials_fall_back_to_yaml(self, monkeypatch):
        monkeypatch.setenv("MONGODB_USER", "# закомментировано")
        monkeypatch.setenv("MONGODB_PASSWORD", "  #x ")
        m = c.MongoDBConnectionConfig(user="yu", password="yp")
        assert (m.user, m.password) == ("yu", "yp")

    def test_env_credentials_win(self, monkeypatch):
        monkeypatch.setenv("MONGODB_USER", " eu ")
        monkeypatch.setenv("MONGODB_PASSWORD", " ep ")
        m = c.MongoDBConnectionConfig(user="yu", password="yp")
        assert (m.user, m.password) == ("eu", "ep")

    def test_only_user_has_no_auth_in_string(self):
        assert "@" not in c.MongoDBConnectionConfig(user="u").connection_string

    def test_non_dict_input_gives_defaults(self):
        assert c.MongoDBConnectionConfig.model_validate(None).host == "mongodb"

    def test_extra_fields_allowed(self):
        assert c.MongoDBConnectionConfig(replica_set="rs").replica_set == "rs"


PG_YAML = dict(host="h", port=5432, database="db", user="u", password="p", embedding_dim=768)


class TestPostgres:
    def test_all_from_yaml(self):
        p = c.PostgreSQLConnectionConfig(**PG_YAML)
        assert p.connection_string == "postgresql://u:p@h:5432/db" and p.embedding_dim == 768

    def test_all_from_env(self, monkeypatch):
        for k, v in dict(POSTGRES_HOST="eh", POSTGRES_PORT="5433", POSTGRES_DB="edb", POSTGRES_USER="eu",
                         POSTGRES_PASSWORD="ep", EMBEDDING_DIM="1024").items():
            monkeypatch.setenv(k, v)
        p = c.PostgreSQLConnectionConfig()
        assert p.connection_string == "postgresql://eu:ep@eh:5433/edb" and p.embedding_dim == 1024

    def test_yaml_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_HOST", "env")
        assert c.PostgreSQLConnectionConfig(**PG_YAML).host == "h"

    @pytest.mark.parametrize("missing,name", [
        ("host", "POSTGRES_HOST"), ("port", "POSTGRES_PORT"), ("database", "POSTGRES_DB"),
        ("user", "POSTGRES_USER"), ("password", "POSTGRES_PASSWORD"), ("embedding_dim", "EMBEDDING_DIM"),
    ])
    def test_missing_value_names_the_variable(self, missing, name):
        data = {k: v for k, v in PG_YAML.items() if k != missing}
        with pytest.raises(ValidationError, match=name):
            c.PostgreSQLConnectionConfig(**data)


MINIO_YAML = dict(endpoint="minio", port=9000, access_key="ak", secret_key="sk", use_ssl=False, bucket_name="astrachat-temp")


class TestMinio:
    def test_derived_bucket_names_and_defaults(self):
        m = c.MinIOConnectionConfig(**MINIO_YAML)
        assert m.documents_bucket_name == "astrachat-documents"
        assert m.memory_rag_bucket_name == "astrachat-memory-rag"
        assert m.project_rag_bucket_name == "astrachat-project-rag"
        assert m.ssl_verify is True and m.ca_cert_path is None
        assert m.full_endpoint == "minio:9000"

    def test_endpoint_with_port_is_kept(self):
        assert c.MinIOConnectionConfig(**{**MINIO_YAML, "endpoint": "minio:9100"}).full_endpoint == "minio:9100"

    def test_explicit_buckets(self):
        m = c.MinIOConnectionConfig(**MINIO_YAML, documents_bucket_name="d", memory_rag_bucket_name="m", project_rag_bucket_name="p")
        assert (m.documents_bucket_name, m.memory_rag_bucket_name, m.project_rag_bucket_name) == ("d", "m", "p")

    def test_all_from_env_with_root_credentials_fallback(self, monkeypatch):
        for k, v in dict(MINIO_ENDPOINT="e", MINIO_PORT="9001", MINIO_ROOT_USER="ru", MINIO_ROOT_PASSWORD="rp",
                         MINIO_USE_SSL="TRUE", MINIO_BUCKET_NAME="x-temp", MINIO_DOCUMENTS_BUCKET_NAME="docs",
                         MINIO_MEMORY_RAG_BUCKET_NAME="mem", MINIO_PROJECT_RAG_BUCKET_NAME="proj").items():
            monkeypatch.setenv(k, v)
        m = c.MinIOConnectionConfig()
        assert (m.access_key, m.secret_key, m.use_ssl) == ("ru", "rp", True)
        assert (m.documents_bucket_name, m.memory_rag_bucket_name, m.project_rag_bucket_name) == ("docs", "mem", "proj")

    def test_access_key_has_priority_over_root_user(self, monkeypatch):
        monkeypatch.setenv("MINIO_ACCESS_KEY", "ak2")
        monkeypatch.setenv("MINIO_ROOT_USER", "ru")
        monkeypatch.setenv("MINIO_SECRET_KEY", "sk2")
        data = {k: v for k, v in MINIO_YAML.items() if k not in ("access_key", "secret_key")}
        m = c.MinIOConnectionConfig(**data)
        assert (m.access_key, m.secret_key) == ("ak2", "sk2")

    @pytest.mark.parametrize("missing,name", [
        ("endpoint", "MINIO_ENDPOINT"), ("port", "MINIO_PORT"), ("access_key", "MINIO_ACCESS_KEY"),
        ("secret_key", "MINIO_SECRET_KEY"), ("use_ssl", "MINIO_USE_SSL"), ("bucket_name", "MINIO_BUCKET_NAME"),
    ])
    def test_missing_value_names_the_variable(self, missing, name):
        data = {k: v for k, v in MINIO_YAML.items() if k != missing}
        with pytest.raises(ValidationError, match=name):
            c.MinIOConnectionConfig(**data)

    def test_ca_bundle_from_yaml_then_env_in_priority_order(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"
        ca.write_text("x")
        assert c.MinIOConnectionConfig(**MINIO_YAML, ca_cert_path=str(ca)).ca_cert_path == str(ca)
        monkeypatch.setenv("SSL_CERT_FILE", "/last")
        monkeypatch.setenv("TLS_CERT_PATH", "/middle")
        assert c.MinIOConnectionConfig(**MINIO_YAML).ca_cert_path == "/middle"
        monkeypatch.setenv("MINIO_CA_CERT", "/first")
        assert c.MinIOConnectionConfig(**MINIO_YAML).ca_cert_path == "/first"

    @pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("YES", True), ("false", False), ("0", False), ("nope", False)])
    def test_ssl_verify_from_env(self, monkeypatch, raw, expected):
        monkeypatch.setenv("MINIO_SSL_VERIFY", raw)
        assert c.MinIOConnectionConfig(**MINIO_YAML).ssl_verify is expected

    def test_ssl_verify_yaml_wins(self, monkeypatch):
        monkeypatch.setenv("MINIO_SSL_VERIFY", "true")
        assert c.MinIOConnectionConfig(**MINIO_YAML, ssl_verify=0).ssl_verify is False


LLM_YAML = dict(enabled=True, timeout=30, retry_attempts=2, retry_delay=1, default_model="m", fallback_model="f")


class TestLlmService:
    def test_yaml_values_and_defaults(self):
        l = c.LLMServiceConnectionConfig(**LLM_YAML)
        assert l.auto_select is True and l.verify_ssl is True and l.skip_health_probe is False
        assert l.api_key is None and l.use_auth is False

    def test_models_section_supplies_models_and_auto_select(self):
        data = {k: v for k, v in LLM_YAML.items() if k not in ("default_model", "fallback_model")}
        l = c.LLMServiceConnectionConfig(**data, models={"default": "d", "fallback": "fb", "auto_select": False})
        assert (l.default_model, l.fallback_model, l.auto_select) == ("d", "fb", False)

    def test_values_from_env(self, monkeypatch):
        for k, v in dict(USE_LLM_SVC="true", LLM_SVC_TIMEOUT="60", LLM_SVC_RETRY_ATTEMPTS="5", LLM_SVC_RETRY_DELAY="2").items():
            monkeypatch.setenv(k, v)
        l = c.LLMServiceConnectionConfig(default_model="m", fallback_model="f")
        assert (l.enabled, l.timeout, l.retry_attempts, l.retry_delay) == (True, 60, 5, 2)

    @pytest.mark.parametrize("missing,name", [
        ("enabled", "USE_LLM_SVC"), ("timeout", "LLM_SVC_TIMEOUT"),
        ("retry_attempts", "LLM_SVC_RETRY_ATTEMPTS"), ("retry_delay", "LLM_SVC_RETRY_DELAY"),
        ("default_model", "default_model"), ("fallback_model", "fallback_model"),
    ])
    def test_missing_value_is_an_error(self, missing, name):
        data = {k: v for k, v in LLM_YAML.items() if k != missing}
        with pytest.raises(ValidationError, match=name):
            c.LLMServiceConnectionConfig(**data)

    def test_empty_legacy_mode_fills_defaults(self):
        l = c.LLMServiceConnectionConfig(_allow_empty_legacy=True)
        assert (l.enabled, l.timeout, l.retry_attempts, l.retry_delay, l.default_model, l.fallback_model) == (False, 300, 3, 1, "", "")

    def test_api_key_env_beats_yaml(self, monkeypatch):
        assert c.LLMServiceConnectionConfig(**LLM_YAML, api_key="yaml-key-long-enough-1234").api_key == "yaml-key-long-enough-1234"
        assert c.LLMServiceConnectionConfig(**LLM_YAML, api_key="short").api_key == "short"
        monkeypatch.setenv("LLM_API_KEY", "env-key-long-enough-5678")
        assert c.LLMServiceConnectionConfig(**LLM_YAML, api_key="yaml").api_key == "env-key-long-enough-5678"
        monkeypatch.setenv("LLM_API_KEY", "tiny")
        assert c.LLMServiceConnectionConfig(**LLM_YAML).api_key == "tiny"

    def test_use_auth(self, monkeypatch):
        assert c.LLMServiceConnectionConfig(**LLM_YAML, use_auth=True).use_auth is True
        monkeypatch.setenv("LLM_USE_AUTH", "True")
        assert c.LLMServiceConnectionConfig(**LLM_YAML).use_auth is True
        # YAML приоритетнее окружения
        assert c.LLMServiceConnectionConfig(**LLM_YAML, use_auth=False).use_auth is False

    def test_verify_ssl_sources(self, monkeypatch):
        assert c.LLMServiceConnectionConfig(**LLM_YAML, verify_ssl=0).verify_ssl is False
        monkeypatch.setenv("LLM_VERIFY_SSL", "false")
        assert c.LLMServiceConnectionConfig(**LLM_YAML, verify_ssl=True).verify_ssl is False  # окружение сильнее
        monkeypatch.setenv("LLM_VERIFY_SSL", "yes")
        assert c.LLMServiceConnectionConfig(**LLM_YAML).verify_ssl is True
        monkeypatch.setenv("TLS_CERT_PATH", "/etc/ca.pem")
        assert c.LLMServiceConnectionConfig(**LLM_YAML).verify_ssl == "/etc/ca.pem"

    def test_skip_health_probe(self, monkeypatch):
        monkeypatch.setenv("LLM_SKIP_HEALTH_PROBE", " TRUE ")
        assert c.LLMServiceConnectionConfig(**LLM_YAML).skip_health_probe is True
        assert c.LLMServiceConnectionConfig(**LLM_YAML, skip_health_probe=False).skip_health_probe is False

    def test_hosts(self):
        l = c.LLMServiceConnectionConfig(**LLM_YAML, hosts=[{"id": "a", "base_url": "http://a"}], default_host_id="a")
        assert l.hosts[0].base_url == "http://a" and l.default_host_id == "a"

    def test_alias(self):
        assert c.LLMConnectionConfig is c.LLMServiceConnectionConfig
