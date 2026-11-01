"""settings/config: pydantic-модели секций конфигурации и их валидаторы (ENV/YAML)."""

import pytest
from pydantic import ValidationError

from backend.settings import config as cfg

pytestmark = pytest.mark.unit


class TestServerConfig:
    def test_host_default_comes_from_env(self, monkeypatch):
        monkeypatch.setenv("SERVER_HOST", "10.1.2.3")

        assert cfg.ServerConfig().host == "10.1.2.3"

    def test_host_default_without_env(self, monkeypatch):
        monkeypatch.delenv("SERVER_HOST", raising=False)

        assert cfg.ServerConfig().host == "127.0.0.1"


class TestLoginLockoutConfig:
    def test_defaults(self):
        c = cfg.LoginLockoutConfig()

        assert (c.enabled, c.max_failed_attempts, c.lockout_duration_seconds) == (True, 5, 900)

    def test_non_dict_input_falls_back_to_defaults(self):
        assert cfg.LoginLockoutConfig.load_from_yaml_or_env("garbage") == {}

    def test_minutes_are_converted_to_seconds(self):
        c = cfg.LoginLockoutConfig(lockout_duration_minutes=2)

        assert c.lockout_duration_seconds == 120

    def test_seconds_win_over_minutes(self):
        c = cfg.LoginLockoutConfig(lockout_duration_seconds=7, lockout_duration_minutes=2)

        assert c.lockout_duration_seconds == 7

    def test_bad_minutes_are_ignored(self):
        c = cfg.LoginLockoutConfig(lockout_duration_minutes="abc")

        assert c.lockout_duration_seconds == 900

    def test_env_overrides_yaml(self, monkeypatch):
        monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_ENABLED", "off")
        monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_MAX_FAILED_ATTEMPTS", "9")
        monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_DURATION_SECONDS", "33")

        c = cfg.LoginLockoutConfig(enabled=True, max_failed_attempts=1)

        assert (c.enabled, c.max_failed_attempts, c.lockout_duration_seconds) == (False, 9, 33)

    def test_env_enabled_truthy(self, monkeypatch):
        monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_ENABLED", " Yes ")

        assert cfg.LoginLockoutConfig(enabled=False).enabled is True

    def test_blank_env_enabled_is_ignored(self, monkeypatch):
        monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_ENABLED", "  ")

        assert cfg.LoginLockoutConfig(enabled=False).enabled is False

    def test_garbage_env_int_is_ignored(self, monkeypatch):
        monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_MAX_FAILED_ATTEMPTS", "many")

        assert cfg.LoginLockoutConfig().max_failed_attempts == 5


class TestSessionTimeoutConfig:
    def test_value_comes_only_from_env(self, monkeypatch):
        monkeypatch.setenv("AUTH_SESSION_TIMEOUT_SECONDS", "42")

        assert cfg.SessionTimeoutConfig(timeout_seconds=1).timeout_seconds == 42

    def test_missing_env_raises_clear_error(self, monkeypatch):
        monkeypatch.delenv("AUTH_SESSION_TIMEOUT_SECONDS", raising=False)

        with pytest.raises(ValidationError, match="AUTH_SESSION_TIMEOUT_SECONDS не задан"):
            cfg.SessionTimeoutConfig()

    def test_garbage_env_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv("AUTH_SESSION_TIMEOUT_SECONDS", "soon")

        with pytest.raises(ValidationError, match="не задан"):
            cfg.SessionTimeoutConfig()


class TestModelHealthConfig:
    def test_defaults_without_env(self):
        c = cfg.ModelHealthConfig()

        assert (c.filter_enabled, c.probe_max, c.models_available_cache_seconds) == (True, 64, 60)

    def test_yaml_values_kept_without_env(self):
        c = cfg.ModelHealthConfig(filter_enabled=False, probe_max=5, models_available_cache_seconds=9)

        assert (c.filter_enabled, c.probe_max, c.models_available_cache_seconds) == (False, 5, 9)

    @pytest.mark.parametrize(("raw", "expected"), [("0", False), ("False", False), ("no", False), ("OFF", False), ("1", True), ("anything", True)])
    def test_env_filter_flag(self, monkeypatch, raw, expected):
        monkeypatch.setenv("MODEL_HEALTH_FILTER_ENABLED", raw)

        assert cfg.ModelHealthConfig(filter_enabled=not expected).filter_enabled is expected

    def test_env_numbers_are_clamped_to_one(self, monkeypatch):
        monkeypatch.setenv("MODEL_HEALTH_PROBE_MAX", "-4")
        monkeypatch.setenv("MODELS_AVAILABLE_CACHE_SECONDS", "0")

        c = cfg.ModelHealthConfig()

        assert (c.probe_max, c.models_available_cache_seconds) == (1, 1)

    def test_env_numbers_applied(self, monkeypatch):
        monkeypatch.setenv("MODEL_HEALTH_PROBE_MAX", "10")
        monkeypatch.setenv("MODELS_AVAILABLE_CACHE_SECONDS", "300")

        c = cfg.ModelHealthConfig(probe_max=1)

        assert (c.probe_max, c.models_available_cache_seconds) == (10, 300)

    def test_non_dict_input(self):
        assert cfg.ModelHealthConfig.load_from_env(None)["probe_max"] == 64


class TestMiscModels:
    def test_files_temp_dir_default_is_under_system_tmp(self):
        import os
        import tempfile

        assert cfg.FilesConfig().temp_dir == os.path.join(tempfile.gettempdir(), "astrachat")

    def test_audit_defaults(self):
        a = cfg.AuditConfig()

        assert a.cef_polling is True
        assert a.cef_polling_interval_seconds == 120

    def test_docker_runtime_from_env(self, monkeypatch):
        monkeypatch.setenv("DOCKER_ENV", "TRUE")
        assert cfg._docker_runtime() is True
        monkeypatch.setenv("DOCKER_ENV", "no")
        assert cfg._docker_runtime() is False

    def test_docker_runtime_without_env_checks_dockerenv_file(self, monkeypatch):
        monkeypatch.delenv("DOCKER_ENV")
        monkeypatch.setattr(cfg.os.path, "exists", lambda p: p == "/.dockerenv")

        assert cfg._docker_runtime() is True

    def test_microservices_enabled_defaults_to_false(self):
        ms = cfg.MicroservicesEnabledConfig(custom=True)

        assert ms.stt is False and ms.postgresql is False
        assert ms.custom is True


class TestUrlsConfig:
    def test_yaml_values_win_over_env(self, monkeypatch):
        monkeypatch.setenv("FRONTEND_PORT", "http://env")

        u = cfg.UrlsConfig(
            frontend_port="http://yaml",
            backend_port="b",
            ingress_port="i",
            llm_service_port="l",
            extra_key="kept",
        )

        assert u.frontend_port == "http://yaml"
        assert u.extra_key == "kept"

    def test_missing_keys_are_taken_from_env(self, monkeypatch):
        for key in ("FRONTEND_PORT", "BACKEND_PORT", "INGRESS_PORT", "LLM_SERVICE_PORT"):
            monkeypatch.setenv(key, key.lower() + "-v")

        u = cfg.UrlsConfig()

        assert u.llm_service_port == "llm_service_port-v"
        assert u.ingress_port == "ingress_port-v"

    def test_missing_key_without_env_raises(self, monkeypatch):
        monkeypatch.delenv("INGRESS_PORT", raising=False)

        with pytest.raises(ValidationError, match="ingress_port не задан в YAML"):
            cfg.UrlsConfig(frontend_port="f", backend_port="b", llm_service_port="l")

    def test_non_dict_input_is_treated_as_empty(self, monkeypatch):
        for key in ("FRONTEND_PORT", "BACKEND_PORT", "INGRESS_PORT", "LLM_SERVICE_PORT"):
            monkeypatch.setenv(key, "v")

        assert cfg.UrlsConfig.load_from_yaml_or_env(None)["frontend_port"] == "v"
