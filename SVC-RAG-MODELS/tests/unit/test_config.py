"""app/core/config.py: чтение config.yml, env-переопределения, CORS из urls, дефолты."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import config
from app.core.config import Settings, _apply_env, _apply_urls_cors

pytestmark = pytest.mark.unit

_ENV_NAMES = (
    "RAG_MODELS_DIR",
    "RAG_EMBEDDING_MODEL",
    "RAG_RERANKER_MODEL",
    "RAG_MODELS_DEVICE",
    "RAG_MODELS_OFFLINE",
    "RAG_MODELS_EMBED_BATCH_SIZE",
)


@pytest.fixture(autouse=True)
def _no_config_env(monkeypatch):
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _write(tmp_path, text, name="c.yml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


class TestFromYaml:
    def test_reads_values_from_yaml(self, tmp_path):
        path = _write(
            tmp_path,
            "server:\n  port: 9999\napp:\n  title: T\nrag_models:\n"
            "  models_dir: /m\n  embedding_model: E\n  max_loaded_embedding: 3\n",
        )
        s = Settings.from_yaml(path)
        assert s.server.port == 9999
        assert s.app.title == "T"
        assert s.rag_models.models_dir == "/m"
        assert s.rag_models.embedding_model == "E"
        assert s.rag_models.max_loaded_embedding == 3

    def test_empty_yaml_gives_defaults(self, tmp_path):
        s = Settings.from_yaml(_write(tmp_path, ""))
        assert s.server.port == 8000
        assert s.rag_models.enabled is True
        assert s.rag_models.query_prompt_name == "search_query"
        assert s.rag_models.document_prompt_name == "search_document"
        assert s.cors.allow_methods == ["*"]
        assert s.logging.level == "INFO"

    def test_invalid_value_wrapped_in_value_error_with_path(self, tmp_path):
        path = _write(tmp_path, "server:\n  port: not-a-number\n")
        with pytest.raises(ValueError) as exc:
            Settings.from_yaml(path)
        assert "Error loading config from" in str(exc.value)
        assert path in str(exc.value)

    def test_missing_file_is_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="Error loading config"):
            Settings.from_yaml(str(tmp_path / "nope.yml"))

    def test_malformed_yaml_is_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="Error loading config"):
            Settings.from_yaml(_write(tmp_path, "a: [unclosed"))

    def test_default_rag_models_class_values(self):
        s = Settings()
        assert s.rag_models.embedding_dim == 384
        assert s.rag_models.embedding_models == []
        assert s.rag_models.max_loaded_reranker == 2
        assert s.rag_models.embedding_model_default == "paraphrase-multilingual-MiniLM-L12-v2"


class TestConfigPathSearch:
    """Поиск config.yml, когда путь не передан."""

    def test_cwd_relative_path_first(self, tmp_path, monkeypatch):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "config.yml").write_text(
            "app:\n  title: from-cwd\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        assert Settings.from_yaml(None).app.title == "from-cwd"

    def test_parent_relative_path_second(self, tmp_path, monkeypatch):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "config.yml").write_text(
            "app:\n  title: from-parent\n", encoding="utf-8"
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.chdir(sub)
        assert Settings.from_yaml("").app.title == "from-parent"

    def test_falls_back_to_repo_config_next_to_package(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # ни "config/", ни "../config/" здесь нет
        s = Settings.from_yaml(None)
        # Подхватился реальный config/config.yml сервиса
        assert s.app.title == "astrachat RAG Models Service"
        assert s.rag_models.models_dir == "/app/models/rag"

    def test_no_config_anywhere_returns_defaults(self, monkeypatch):
        fake_os = SimpleNamespace(
            path=SimpleNamespace(exists=lambda p: False), environ=os.environ
        )
        monkeypatch.setattr(config, "os", fake_os)
        s = Settings.from_yaml(None)
        assert isinstance(s, Settings)
        assert s.server.port == 8000


class TestGetSettings:
    def test_reads_config_path_env_and_caches(self, tmp_path, monkeypatch):
        path = _write(tmp_path, "app:\n  title: via-env\n")
        monkeypatch.setattr(config, "_settings", None)
        monkeypatch.setenv("CONFIG_PATH", path)
        first = config.get_settings()
        assert first.app.title == "via-env"
        # Второй вызов - из кэша, файл повторно не читается
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "missing.yml"))
        assert config.get_settings() is first

    def test_empty_config_path_uses_search(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "_settings", None)
        monkeypatch.setenv("CONFIG_PATH", "")
        monkeypatch.chdir(tmp_path)
        assert config.get_settings().app.title == "astrachat RAG Models Service"

    def test_module_level_settings_come_from_test_fixture(self):
        # conftest выставил CONFIG_PATH до импорта приложения
        assert Path(os.environ["CONFIG_PATH"]).name == "config.test.yml"
        assert config.settings.cors.allowed_origins == ["http://front.test"]


class TestApplyEnv:
    def test_env_overrides_yaml_values(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_DIR", "/env/dir")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL", "EnvEmb")
        monkeypatch.setenv("RAG_RERANKER_MODEL", "EnvRer")
        monkeypatch.setenv("RAG_MODELS_DEVICE", "cuda")
        monkeypatch.setenv("RAG_MODELS_EMBED_BATCH_SIZE", "64")
        out = _apply_env({"rag_models": {"models_dir": "/yaml", "embedding_model": "Y"}})
        rm = out["rag_models"]
        assert rm["models_dir"] == "/env/dir"
        assert rm["embedding_model"] == "EnvEmb"
        assert rm["reranker_model"] == "EnvRer"
        assert rm["device"] == "cuda"
        assert rm["embed_batch_size"] == 64

    @pytest.mark.parametrize(
        "raw,expected",
        [("1", True), ("true", True), ("YES", True), ("on", True),
         ("0", False), ("false", False), ("no", False), ("off", False), ("абв", False)],
    )
    def test_bool_parsing(self, monkeypatch, raw, expected):
        monkeypatch.setenv("RAG_MODELS_OFFLINE", raw)
        assert _apply_env({})["rag_models"]["offline"] is expected

    def test_invalid_int_is_ignored_with_message(self, monkeypatch, capsys):
        monkeypatch.setenv("RAG_MODELS_EMBED_BATCH_SIZE", "много")
        out = _apply_env({"rag_models": {"embed_batch_size": 5}})
        assert out["rag_models"]["embed_batch_size"] == 5
        assert "невалиден" in capsys.readouterr().out

    def test_blank_env_is_ignored(self, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_DEVICE", "   ")
        out = _apply_env({"rag_models": {"device": "cpu"}})
        assert out["rag_models"]["device"] == "cpu"

    def test_does_not_mutate_input_and_handles_missing_section(self):
        src = {"server": {"port": 1}}
        out = _apply_env(src)
        assert out["rag_models"] == {}
        assert "rag_models" not in src

    def test_env_applied_through_from_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RAG_MODELS_DEVICE", "cuda:1")
        monkeypatch.setenv("RAG_MODELS_OFFLINE", "false")
        path = _write(tmp_path, "rag_models:\n  device: cpu\n  offline: true\n")
        s = Settings.from_yaml(path)
        assert s.rag_models.device == "cuda:1"
        assert s.rag_models.offline is False

    def test_log_message_does_not_break_on_value(self, monkeypatch, capsys):
        monkeypatch.setenv("RAG_MODELS_DIR", "/x")
        _apply_env({})
        assert "RAG_MODELS_DIR" in capsys.readouterr().out


class TestApplyUrlsCors:
    def test_no_urls_section_returns_same_dict(self):
        data = {"cors": {"allowed_origins": ["a"]}}
        assert _apply_urls_cors(data) is data

    def test_urls_not_dict_returns_same_dict(self):
        data = {"urls": "oops"}
        assert _apply_urls_cors(data) is data

    def test_empty_allowed_origins_filled_from_urls_in_key_order(self):
        data = {
            "urls": {
                "backend_port": " http://b:8000 ",
                "frontend_port": "http://f:3000",
                "frontend_port_2": "",
                "unknown_key": "http://ignored",
                "backend_port_2_ipv4": "http://b2",
            },
            "cors": {},
        }
        out = _apply_urls_cors(data)
        assert out["cors"]["allowed_origins"] == [
            "http://f:3000",
            "http://b:8000",
            "http://b2",
        ]
        assert "urls" not in out
        assert "urls" in data  # исходник не тронут

    def test_wildcard_allowed_origins_replaced(self):
        out = _apply_urls_cors(
            {"urls": {"frontend_port": "http://f"}, "cors": {"allowed_origins": ["*"]}}
        )
        assert out["cors"]["allowed_origins"] == ["http://f"]

    def test_explicit_origins_are_kept(self):
        out = _apply_urls_cors(
            {"urls": {"frontend_port": "http://f"}, "cors": {"allowed_origins": ["http://x"]}}
        )
        assert out["cors"]["allowed_origins"] == ["http://x"]

    def test_urls_without_usable_values_leave_cors_empty(self):
        out = _apply_urls_cors({"urls": {"frontend_port": "  "}})
        assert out["cors"] == {}
        assert "urls" not in out

    def test_urls_cors_applied_through_from_yaml(self, tmp_path):
        path = _write(tmp_path, "urls:\n  frontend_port: http://front\ncors:\n  allowed_origins: []\n")
        assert Settings.from_yaml(path).cors.allowed_origins == ["http://front"]
