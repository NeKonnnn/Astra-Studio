"""settings/config: Settings (URL-резолверы, from_yaml, singleton) и загрузка .env при импорте."""

import copy
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from backend.settings import config as cfg

pytestmark = pytest.mark.unit

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "config.test.yml"


def base_cfg() -> dict:
    """Копия тестового конфига (все сервисы выключены)."""
    return copy.deepcopy(yaml.safe_load(_FIXTURE.read_text(encoding="utf-8")))


@pytest.fixture
def load_yaml(monkeypatch, tmp_path):
    """Записывает dict в yml и грузит через Settings.from_yaml (путь разрешаем в обход песочницы)."""

    def _load(data: dict, settings_cls=None):
        path = tmp_path / "cfg.yml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr(cfg, "resolve_config_file_path", lambda p: Path(p))
        return (settings_cls or cfg.Settings).from_yaml(str(path))

    return _load


@pytest.fixture
def loaded(load_yaml):
    return load_yaml(base_cfg())


class TestMicroserviceHttpBase:
    def test_host_prefers_port_field(self, loaded, monkeypatch):
        monkeypatch.setenv("DOCKER_ENV", "false")
        loaded.urls.rag_service_docker = "http://docker-rag/"

        assert loaded.microservice_http_base("rag_service_docker", "rag_service_port") == "http://rag.test:8011"

    def test_docker_prefers_docker_field(self, loaded, monkeypatch):
        monkeypatch.setenv("DOCKER_ENV", "true")
        loaded.urls.rag_service_docker = " http://docker-rag/ "

        assert loaded.microservice_http_base("rag_service_docker", "rag_service_port") == "http://docker-rag"

    def test_docker_falls_back_to_port_field(self, loaded, monkeypatch):
        monkeypatch.setenv("DOCKER_ENV", "true")

        assert loaded.microservice_http_base("rag_service_docker", "rag_service_port") == "http://rag.test:8011"

    def test_host_falls_back_to_docker_field(self, loaded, monkeypatch):
        loaded.urls.only_docker_service_docker = "http://d/"

        assert loaded.microservice_http_base("only_docker_service_docker", "only_docker_service_port") == "http://d"

    def test_missing_both_raises(self, loaded):
        with pytest.raises(ValueError, match="Задайте urls.nope_docker и urls.nope_port"):
            loaded.microservice_http_base("nope_docker", "nope_port")

    def test_blank_value_raises(self, loaded):
        loaded.urls.blank_port = "   "

        with pytest.raises(ValueError):
            loaded.microservice_http_base("blank_docker", "blank_port")

    def test_get_llm_service_url(self, loaded):
        assert loaded.get_llm_service_url() == "http://llm.test:8001"


class TestMcpAndPluginResolvers:
    def test_mcp_base_url_from_server_config(self, loaded):
        loaded.mcp.servers.append(cfg.McpServerConfig(id="srv", base_url=" http://mcp/ "))

        assert loaded.resolve_mcp_server_base_url("srv") == "http://mcp"

    def test_mcp_base_url_falls_back_to_urls(self, loaded):
        loaded.urls.mcp_my_srv_service_port = "http://urls-mcp/"

        assert loaded.resolve_mcp_server_base_url("my-srv") == "http://urls-mcp"

    def test_mcp_server_with_blank_url_uses_urls(self, loaded):
        loaded.mcp.servers.append(cfg.McpServerConfig(id="blank", base_url=""))
        loaded.urls.mcp_blank_service_port = "http://fallback"

        assert loaded.resolve_mcp_server_base_url("blank") == "http://fallback"

    @pytest.mark.parametrize("sid", ["", "  ", None])
    def test_mcp_requires_server_id(self, loaded, sid):
        with pytest.raises(ValueError, match="server_id обязателен"):
            loaded.resolve_mcp_server_base_url(sid)

    def test_get_mcp_server_config(self, loaded):
        srv = cfg.McpServerConfig(id="srv")
        loaded.mcp.servers.append(srv)

        assert loaded.get_mcp_server_config(" srv ") is srv
        assert loaded.get_mcp_server_config("other") is None

    def test_plugin_base_url_from_catalog(self, loaded):
        loaded.plugins.catalog.append(cfg.PluginConfig(id="p1", base_url="http://p1/"))

        assert loaded.resolve_plugin_base_url("p1") == "http://p1"

    @pytest.mark.parametrize("pid", ["", " ", None])
    def test_plugin_requires_id(self, loaded, pid):
        with pytest.raises(ValueError, match="plugin_id обязателен"):
            loaded.resolve_plugin_base_url(pid)

    def test_cash_flow_uses_dedicated_keys(self, loaded):
        loaded.urls.cash_flow_service_port = "http://cf/"

        assert loaded.resolve_plugin_base_url("cash-flow") == "http://cf"

    def test_generic_plugin_uses_derived_keys(self, loaded):
        loaded.urls.my_plug_service_port = "http://mp"

        assert loaded.resolve_plugin_base_url("my-plug") == "http://mp"

    def test_get_plugin_config(self, loaded):
        plugin = cfg.PluginConfig(id="p1")
        loaded.plugins.catalog.append(plugin)

        assert loaded.get_plugin_config("p1") is plugin
        assert loaded.get_plugin_config("zzz") is None


class TestPreprocessUrls:
    def test_legacy_diarization_key_is_copied(self):
        data = {"urls": {"diarization_docker": "http://d"}}

        cfg._preprocess_urls_and_microservices(data)

        assert data["urls"]["diarization_service_docker"] == "http://d"

    def test_existing_new_diarization_key_is_kept(self):
        data = {"urls": {"diarization_docker": "old", "diarization_service_docker": "new"}}

        cfg._preprocess_urls_and_microservices(data)

        assert data["urls"]["diarization_service_docker"] == "new"

    def test_provider_defaults_for_local_kinds(self, monkeypatch):
        monkeypatch.setenv("DOCKER_ENV", "false")
        data = {
            "urls": {"llm_service_port": " http://host-llm/ ", "llm_service_docker": "http://docker-llm"},
            "llm_providers": [
                {"id": "a", "kind": "VLLM", "llm_key": "KEY_A"},
                {"id": "b", "kind": "openai-compat", "base_url": "http://own"},
                {"id": "c", "kind": "anthropic"},
                {"id": "d", "kind": "ollama", "api_key_env": "OWN", "llm_key": "IGNORED"},
                "not-a-dict",
            ],
        }

        cfg._preprocess_urls_and_microservices(data)

        a, b, c, d, _ = data["llm_providers"]
        assert a["base_url"] == "http://host-llm"
        assert a["api_key_env"] == "KEY_A"
        assert b["base_url"] == "http://own"
        assert "base_url" not in c
        assert d["api_key_env"] == "OWN"
        assert d["base_url"] == "http://host-llm"

    def test_docker_uses_docker_url(self, monkeypatch):
        monkeypatch.setenv("DOCKER_ENV", "true")
        data = {
            "urls": {"llm_service_port": "http://host", "llm_service_docker": "http://dock/"},
            "llm_providers": [{"id": "a", "kind": "llm-svc"}],
        }

        cfg._preprocess_urls_and_microservices(data)

        assert data["llm_providers"][0]["base_url"] == "http://dock"

    def test_no_urls_means_no_base_url(self):
        data = {"llm_providers": [{"id": "a", "kind": "vllm"}]}

        cfg._preprocess_urls_and_microservices(data)

        assert "base_url" not in data["llm_providers"][0]

    def test_microservices_cleanup(self):
        data = {
            "urls": {"llm_service_port": "http://llm"},
            "microservices": {
                "llm": {
                    "url": "x",
                    "hosts": [
                        {"id": "h1"},
                        {"id": "h2", "base_url": "http://own"},
                        "junk",
                    ],
                },
                "stt": {"url": "x", "keep": 1},
                "tts": "not-a-dict",
                "ocr": {"url": "y"},
            },
        }

        cfg._preprocess_urls_and_microservices(data)

        ms = data["microservices"]
        assert "url" not in ms["llm"]
        assert ms["llm"]["hosts"][0]["base_url"] == "http://llm"
        assert ms["llm"]["hosts"][1]["base_url"] == "http://own"
        assert ms["stt"] == {"keep": 1}
        assert ms["tts"] == "not-a-dict"
        assert ms["ocr"] == {}

    def test_microservices_not_a_dict_is_ignored(self):
        data = {"microservices": ["x"]}

        cfg._preprocess_urls_and_microservices(data)

        assert data == {"microservices": ["x"]}


class TestFromYaml:
    def test_loads_fixture_values(self, loaded):
        assert loaded.app.name == "astrachat-test"
        assert loaded.cors.allowed_origins == ["http://localhost:3000"]
        assert loaded.llm_service.base_url == "http://llm.test:8001"
        assert loaded.llm_service.external_url == "http://llm.test:8001"
        assert loaded.microservices_enabled.stt is False

    def test_cors_defaults_to_ingress_and_frontend_urls(self, load_yaml):
        data = base_cfg()
        data["cors"]["allowed_origins"] = []

        s = load_yaml(data)

        assert s.cors.allowed_origins == ["http://localhost:8080", "http://localhost:3000"]

    def test_cors_stays_empty_without_urls(self, load_yaml, monkeypatch):
        data = base_cfg()
        data["cors"]["allowed_origins"] = []
        data["urls"] = {k: "" for k in ("frontend_port", "backend_port", "ingress_port", "llm_service_port")}

        s = load_yaml(data)

        assert s.cors.allowed_origins == []

    def test_cors_from_config_without_urls(self, load_yaml, monkeypatch):
        for k in ("FRONTEND_PORT", "BACKEND_PORT", "INGRESS_PORT", "LLM_SERVICE_PORT"):
            monkeypatch.setenv(k, "http://envurl")
        data = base_cfg()
        del data["urls"]

        s = load_yaml(data)

        assert s.cors.allowed_origins == ["http://localhost:3000"]

    def test_docker_env_detected_from_dockerenv_file(self, load_yaml, monkeypatch):
        monkeypatch.delenv("DOCKER_ENV")
        monkeypatch.setattr(cfg.os.path, "exists", lambda p: p == "/.dockerenv")

        s = load_yaml(base_cfg())

        assert s.llm_service.base_url == "http://llm.test:8001"

    def test_empty_file_is_rejected_with_clear_error(self, tmp_path, monkeypatch):
        path = tmp_path / "empty.yml"
        path.write_text("", encoding="utf-8")
        monkeypatch.setattr(cfg, "resolve_config_file_path", lambda p: Path(p))
        monkeypatch.setenv("FRONTEND_PORT", "f")
        monkeypatch.setenv("BACKEND_PORT", "b")
        monkeypatch.setenv("INGRESS_PORT", "i")
        monkeypatch.setenv("LLM_SERVICE_PORT", "l")

        # пустой YAML -> нет секции postgresql/minio, обязательные поля берутся из ENV
        with pytest.raises(ValueError, match="Ошибка загрузки конфигурации"):
            cfg.Settings.from_yaml(str(path))

    def test_broken_yaml_raises_value_error_with_path(self, tmp_path, monkeypatch):
        path = tmp_path / "broken.yml"
        path.write_text("a: [unclosed\n", encoding="utf-8")
        monkeypatch.setattr(cfg, "resolve_config_file_path", lambda p: Path(p))

        with pytest.raises(ValueError, match="Ошибка загрузки конфигурации из .*broken.yml"):
            cfg.Settings.from_yaml(str(path))

    def test_invalid_section_raises_value_error(self, load_yaml):
        data = base_cfg()
        data["server"] = {"port": "not-a-number"}

        with pytest.raises(ValueError, match="Ошибка загрузки конфигурации"):
            load_yaml(data)

    def test_none_sections_become_empty_dicts(self, load_yaml, monkeypatch):
        data = base_cfg()
        data["llm_service"] = None
        data["llm_providers"] = [{"id": "p", "kind": "vllm"}]

        s = load_yaml(data)

        assert s.llm_providers[0]["id"] == "p"

    def test_legacy_llm_svc_block_fills_llm_service(self, load_yaml):
        data = base_cfg()
        data["llm_service"] = {}
        data["microservices"] = {
            "llm_svc": {
                "enabled": False,
                "timeout": 3,
                "retry_attempts": 2,
                "retry_delay": 0,
                "default_model": "legacy-m",
                "fallback_model": "legacy-fb",
            }
        }

        s = load_yaml(data)

        assert s.llm_service.default_model == "legacy-m"
        assert s.llm_service.timeout == 3

    def test_llm_svc_does_not_override_explicit_llm_service(self, load_yaml):
        data = base_cfg()
        data["microservices"] = {"llm_svc": {"default_model": "from-ms"}, "llm": {}}

        s = load_yaml(data)

        assert s.llm_service.default_model == "test-model"

    def test_llm_svc_not_a_dict_is_ignored(self, load_yaml):
        data = base_cfg()
        data["microservices"] = {"llm_svc": "junk"}

        assert load_yaml(data).llm_service.default_model == "test-model"

    def test_microservices_llm_block_fills_missing_fields(self, load_yaml):
        data = base_cfg()
        data["llm_service"] = {"enabled": False, "retry_attempts": 1, "retry_delay": 0}
        data["microservices"] = {
            "llm": {
                "timeout": 17,
                "models": {"default": "m-def", "fallback": "m-fb"},
                "hosts": [
                    {"id": "h1", "base_url": "http://h1/"},
                    {"id": "h2"},
                    "junk",
                ],
                "default_host_id": "h1",
            }
        }

        s = load_yaml(data)

        ls = s.llm_service
        assert ls.timeout == 17
        assert ls.default_model == "m-def"
        assert ls.fallback_model == "m-fb"

    def test_microservices_llm_hosts_reach_the_model(self, load_yaml):
        """BUG-BE-21: hosts/default_host_id передаются в llm_service, но валидатор их теряет."""
        seen = {}

        class Spy(cfg.Settings):
            def __init__(self, **kw):
                seen.update(kw)
                super().__init__(**kw)

        data = base_cfg()
        data["microservices"] = {
            "llm": {
                "hosts": [{"id": "h1", "base_url": "http://h1/"}, {"id": "h2"}, "junk"],
                "default_host_id": "h1",
            }
        }

        ls = load_yaml(data, Spy).llm_service

        # from_yaml корректно собрал hosts в данных для секции...
        # у h2 base_url подставлен из urls.llm_service_port (preprocess), "junk" отброшен
        assert seen["llm_service"]["hosts"] == [
            {"id": "h1", "base_url": "http://h1"},
            {"id": "h2", "base_url": "http://llm.test:8001"},
        ]
        assert seen["llm_service"]["default_host_id"] == "h1"
        # баг «hosts теряются» исправлен: хосты и default_host_id попадают в итоговую модель
        assert [(h.id, h.base_url) for h in ls.hosts] == [("h1", "http://h1"), ("h2", "http://llm.test:8001")]
        assert ls.default_host_id == "h1"

    def test_microservices_llm_does_not_override_explicit_values(self, load_yaml):
        data = base_cfg()
        data["llm_service"].update(
            {"hosts": [{"id": "own", "base_url": "http://own"}], "default_host_id": "own"}
        )
        data["microservices"] = {
            "llm": {
                "timeout": 99,
                "models": {"default": "x", "fallback": "y"},
                "hosts": [{"id": "h1", "base_url": "http://h1"}],
                "default_host_id": "h1",
            }
        }

        ls = load_yaml(data).llm_service

        assert ls.timeout == 5
        assert ls.default_model == "test-model"
        assert ls.fallback_model == "test-fallback"

    def test_microservices_llm_hosts_without_valid_entries(self, load_yaml):
        data = base_cfg()
        data["microservices"] = {"llm": {"hosts": [{"id": "only-id"}], "models": "junk"}}

        hosts = load_yaml(data).llm_service.hosts
        # запись без base_url получает адрес из urls.llm_service_port (как и в тесте выше)
        assert [(h.id, h.base_url) for h in hosts] == [("only-id", "http://llm.test:8001")]

    def test_microservices_llm_not_a_dict(self, load_yaml):
        data = base_cfg()
        data["microservices"] = {"llm": "junk"}

        assert load_yaml(data).llm_service.default_model == "test-model"

    def test_new_providers_allow_empty_legacy_llm_service(self, load_yaml, monkeypatch):
        data = base_cfg()
        data["llm_service"] = {}
        data["llm_providers"] = [{"id": "p", "kind": "openai-compat", "base_url": "http://x"}]

        s = load_yaml(data)

        assert s.llm_providers[0]["id"] == "p"

    def test_rejected_path_falls_back_to_search(self, tmp_path, monkeypatch, caplog):
        """Путь вне разрешённой директории отклоняется, поиск идёт по стандартным местам."""
        (tmp_path / "config.yml").write_text(yaml.safe_dump(base_cfg()), encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        # в банковском репо есть свой backend/config/config.yml - он нашёлся бы раньше ./config.yml;
        # уводим «стандартные места» (отсчёт от __file__ модуля) в пустую песочницу
        monkeypatch.setattr(cfg, "__file__", str(tmp_path / "x" / "y" / "config.py"))
        outside = tmp_path / "evil.yml"
        outside.write_text("x: 1", encoding="utf-8")

        s = cfg.Settings.from_yaml(str(outside))

        assert s.app.name == "astrachat-test"

    @staticmethod
    def _full_env(monkeypatch):
        env = {
            "FRONTEND_PORT": "f",
            "BACKEND_PORT": "b",
            "INGRESS_PORT": "i",
            "LLM_SERVICE_PORT": "l",
            "POSTGRES_HOST": "pg",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "db",
            "POSTGRES_USER": "u",
            "POSTGRES_PASSWORD": "p",
            "EMBEDDING_DIM": "8",
            "MINIO_ENDPOINT": "m",
            "MINIO_PORT": "9000",
            "MINIO_ACCESS_KEY": "ak",
            "MINIO_SECRET_KEY": "sk",
            "MINIO_USE_SSL": "false",
            "MINIO_BUCKET_NAME": "bk",
            "MINIO_DOCUMENTS_BUCKET_NAME": "docs",
            "MINIO_MEMORY_RAG_BUCKET_NAME": "mem",
            "MINIO_PROJECT_RAG_BUCKET_NAME": "proj",
            "USE_LLM_SVC": "false",
            "LLM_SVC_TIMEOUT": "5",
            "LLM_SVC_RETRY_ATTEMPTS": "1",
            "LLM_SVC_RETRY_DELAY": "0",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(cfg.Path, "exists", lambda self: False)

    def test_search_without_any_file_cannot_build_defaults_known_issue(self, tmp_path, monkeypatch):
        """Без config.yml Settings() не собирается даже при полном ENV: default_model/fallback_model
        для llm_service можно задать только в YAML (connections.py), поэтому ветка cls() в from_yaml
        всегда кончается ValidationError."""
        monkeypatch.chdir(tmp_path)
        self._full_env(monkeypatch)

        with pytest.raises(Exception, match="default_model не задан"):
            cfg.Settings.from_yaml(None)

    def test_search_without_any_file_uses_default_settings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._full_env(monkeypatch)
        logged = []
        monkeypatch.setattr(cfg, "_log_model_health_config", lambda s: logged.append(s))
        llm = cfg.LLMServiceConnectionConfig(**base_cfg()["llm_service"])

        class WithLlm(cfg.Settings):
            llm_service: cfg.LLMServiceConnectionConfig = cfg.Field(default_factory=lambda: llm)

        s = WithLlm.from_yaml(None)

        assert s.urls.frontend_port == "f"
        assert s.minio.endpoint == "m"
        assert logged == [s]


class TestSingleton:
    def test_get_settings_loads_once_via_config_path(self, monkeypatch):
        calls = []
        sentinel = object()
        monkeypatch.setattr(cfg, "_settings", None)
        monkeypatch.setenv("CONFIG_PATH", "/some/cfg.yml")
        monkeypatch.setattr(cfg.Settings, "from_yaml", classmethod(lambda c, p=None: calls.append(p) or sentinel))

        assert cfg.get_settings() is sentinel
        assert cfg.get_settings() is sentinel
        assert calls == ["/some/cfg.yml"]

    def test_reset_settings_reloads(self, monkeypatch):
        values = iter(["first", "second"])
        monkeypatch.setattr(cfg, "_settings", None)
        monkeypatch.setattr(cfg.Settings, "from_yaml", classmethod(lambda c, p=None: next(values)))

        assert cfg.reset_settings() == "first"
        assert cfg.get_settings() == "first"
        assert cfg.reset_settings() == "second"

    def test_init_settings_is_lazy_and_cached(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(cfg, "settings", None)
        monkeypatch.setattr(cfg, "get_settings", lambda: sentinel)

        assert cfg._init_settings() is sentinel
        monkeypatch.setattr(cfg, "get_settings", lambda: object())
        assert cfg._init_settings() is sentinel

    def test_log_model_health_config(self, loaded, monkeypatch):
        messages = []
        monkeypatch.setattr(cfg.logger, "info", lambda fmt, *a, **k: messages.append(fmt % a))

        cfg._log_model_health_config(loaded)

        assert "MODEL_HEALTH_FILTER_ENABLED=True" in messages[0]
        assert "MODEL_HEALTH_PROBE_MAX=64" in messages[0]


def _exec_config_copy(monkeypatch):
    """Выполняет исходник config.py в отдельном модуле (с покрытием), не трогая sys.modules['backend.settings.config']."""
    name = "backend.settings._config_isolated_copy"
    spec = importlib.util.spec_from_file_location(name, cfg.__file__)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class TestDotenvLoadingOnImport:
    @pytest.fixture
    def fake_dotenv(self, monkeypatch):
        import types

        loaded_files = []
        mod = types.ModuleType("dotenv")
        mod.load_dotenv = lambda path, override=False: loaded_files.append((str(path), override))
        monkeypatch.setitem(sys.modules, "dotenv", mod)
        return loaded_files

    def _isolate_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

    def test_cwd_env_file_is_loaded_without_override(self, monkeypatch, tmp_path, fake_dotenv):
        (tmp_path / ".env").write_text("A=1", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        _exec_config_copy(monkeypatch)

        assert fake_dotenv[0] == (str(tmp_path / ".env"), False)
        assert len(fake_dotenv) == 1

    def test_fallback_glob_loads_first_non_example_env_file(self, monkeypatch, tmp_path, fake_dotenv):
        (tmp_path / ".env.example").write_text("A=1", encoding="utf-8")
        (tmp_path / ".env.local").write_text("A=2", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        real_exists = Path.exists
        # ".env" в известных местах отсутствует: скрываем его, даже если лежит в корне проекта
        monkeypatch.setattr(
            Path, "exists", lambda self: False if self.name == ".env" else real_exists(self)
        )

        _exec_config_copy(monkeypatch)

        loaded = [Path(p).name for p, _ in fake_dotenv]
        assert ".env.local" in loaded
        assert ".env.example" not in loaded

    def test_no_env_files_anywhere_loads_nothing(self, monkeypatch, tmp_path, fake_dotenv):
        monkeypatch.chdir(tmp_path)
        real_exists = Path.exists
        monkeypatch.setattr(
            Path, "exists", lambda self: False if self.name == ".env" else real_exists(self)
        )
        monkeypatch.setattr(Path, "glob", lambda self, pattern: iter(()))

        _exec_config_copy(monkeypatch)

        assert fake_dotenv == []

    def test_only_example_files_are_skipped(self, monkeypatch, tmp_path, fake_dotenv):
        (tmp_path / ".env.example").write_text("A=1", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        real_exists = Path.exists
        monkeypatch.setattr(
            Path, "exists", lambda self: False if self.name == ".env" else real_exists(self)
        )
        real_glob = Path.glob
        monkeypatch.setattr(
            Path, "glob", lambda self, pattern: real_glob(self, pattern) if self == tmp_path else iter(())
        )

        _exec_config_copy(monkeypatch)

        assert fake_dotenv == []

    def test_missing_python_dotenv_is_tolerated(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "dotenv", None)  # import dotenv -> ImportError

        module = _exec_config_copy(monkeypatch)

        assert module.Settings is not cfg.Settings
        assert hasattr(module, "get_settings")
