"""backend/config/__init__.py: загрузка, слияние, сброс конфигурации и пути проекта."""

from pathlib import Path

import pytest

import backend.config as cfg


@pytest.fixture(autouse=True)
def _restore_config():
    """Тесты меняют глобальный синглтон — возвращаем его на место."""
    saved = cfg._config
    yield
    cfg._config = saved


def test_load_config_returns_cached_value():
    cfg._config = {"cached": True}
    assert cfg.load_config("whatever.yml") == {"cached": True}


def test_load_config_without_path_and_files_gives_defaults(monkeypatch):
    cfg._config = None
    monkeypatch.setattr(cfg.os.path, "exists", lambda p: False)
    result = cfg.load_config()
    assert result == cfg.get_default_config()


def test_load_config_without_path_picks_first_existing_candidate(monkeypatch, tmp_path):
    cfg._config = None
    yml = tmp_path / "config.yml"
    yml.write_text("app:\n  name: from-candidate\n", encoding="utf-8")
    monkeypatch.setattr(cfg.os.path, "exists", lambda p: str(p) == "./config.yml")
    monkeypatch.setattr(cfg, "Path", lambda p: yml if p == "./config.yml" else Path(p))
    # Path(__file__).parent в списке кандидатов вычисляется до подмены — это не мешает.
    result = cfg.load_config()
    assert result["app"]["name"] == "from-candidate"
    # остальные секции дополняются значениями по умолчанию
    assert result["server"]["port"] == 8000


def test_load_config_rejected_explicit_path_gives_defaults(monkeypatch):
    cfg._config = None
    monkeypatch.setattr(cfg, "resolve_config_file_path", lambda p: None)
    assert cfg.load_config("/etc/passwd") == cfg.get_default_config()


def test_load_config_explicit_path_merges_with_defaults(monkeypatch, tmp_path):
    cfg._config = None
    yml = tmp_path / "c.yml"
    yml.write_text("app:\n  name: custom\ncors:\n  allow_credentials: false\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "resolve_config_file_path", lambda p: yml)
    result = cfg.load_config(str(yml))
    assert result["app"]["name"] == "custom"
    assert result["app"]["version"] == "1.0.0"  # из дефолтов
    assert result["cors"]["allow_credentials"] is False
    assert result["cors"]["allow_methods"] == ["*"]


def test_load_config_empty_yaml_is_defaults(monkeypatch, tmp_path):
    cfg._config = None
    yml = tmp_path / "empty.yml"
    yml.write_text("", encoding="utf-8")
    monkeypatch.setattr(cfg, "resolve_config_file_path", lambda p: yml)
    assert cfg.load_config(str(yml)) == cfg.get_default_config()


def test_load_config_fixes_llm_svc_url_typos(monkeypatch, tmp_path):
    cfg._config = None
    yml = tmp_path / "typo.yml"
    yml.write_text(
        "microservices:\n  llm_svc:\n"
        "    base_url: http://1lm-svc:8001\n"
        "    external_url: http://11m-svc:8001\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "resolve_config_file_path", lambda p: yml)
    result = cfg.load_config(str(yml))
    assert result["microservices"]["llm_svc"]["base_url"] == "http://llm-svc:8001"
    assert result["microservices"]["llm_svc"]["external_url"] == "http://llm-svc:8001"


def test_load_config_keeps_correct_llm_svc_urls(monkeypatch, tmp_path):
    cfg._config = None
    yml = tmp_path / "ok.yml"
    yml.write_text(
        "microservices:\n  llm_svc:\n    base_url: http://llm-svc:1\n    external_url: http://x:2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "resolve_config_file_path", lambda p: yml)
    result = cfg.load_config(str(yml))
    assert result["microservices"]["llm_svc"]["base_url"] == "http://llm-svc:1"
    assert result["microservices"]["llm_svc"]["external_url"] == "http://x:2"


def test_load_config_broken_yaml_falls_back_to_defaults(monkeypatch, tmp_path):
    cfg._config = None
    yml = tmp_path / "broken.yml"
    yml.write_text("a: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "resolve_config_file_path", lambda p: yml)
    assert cfg.load_config(str(yml)) == cfg.get_default_config()


def test_merge_configs_is_recursive_and_does_not_touch_inputs():
    default = {"a": {"x": 1, "y": 2}, "b": 1}
    user = {"a": {"y": 3}, "c": 5}
    assert cfg.merge_configs(default, user) == {"a": {"x": 1, "y": 3}, "b": 1, "c": 5}
    assert default["a"] == {"x": 1, "y": 2}


def test_merge_configs_scalar_overrides_dict():
    assert cfg.merge_configs({"a": {"x": 1}}, {"a": 5}) == {"a": 5}


def test_get_config_loads_from_env_when_empty(monkeypatch, test_config_path):
    cfg._config = None
    monkeypatch.setenv("CONFIG_PATH", str(test_config_path))
    result = cfg.get_config()
    assert result["app"]["name"] == "astrachat-test"
    assert cfg.get_config() is result


def test_reset_config_reloads(monkeypatch, tmp_path):
    cfg._config = {"stale": 1}
    yml = tmp_path / "r.yml"
    yml.write_text("app:\n  name: reloaded\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(yml))
    monkeypatch.setattr(cfg, "resolve_config_file_path", lambda p: yml)
    assert cfg.reset_config()["app"]["name"] == "reloaded"


def test_project_root_outside_container_is_three_levels_up():
    assert cfg.get_project_root() == Path(cfg.__file__).resolve().parent.parent.parent.absolute()


@pytest.mark.parametrize("fake_file", ["/app/backend/config/__init__.py", "/app/cfg/__init__.py"])
def test_project_root_in_container_is_app(monkeypatch, fake_file):
    """Внутри контейнера (файл под /app) корень проекта всегда /app."""

    class FakePath:
        def __init__(self, p):
            self._p = p

        def resolve(self):
            return FakePath(fake_file) if self._p == cfg.__file__ else self

        def __str__(self):
            return self._p

    monkeypatch.setattr(cfg, "Path", FakePath)
    assert str(cfg.get_project_root()) == "/app"


def test_get_path_known_and_unknown_keys(monkeypatch):
    monkeypatch.setattr(cfg, "get_config", lambda: {"paths": {"data": "var/data"}})
    monkeypatch.setattr(cfg, "get_project_root", lambda: Path("/proj"))
    assert Path(cfg.get_path("data")) == (Path("/proj") / "var/data").absolute()
    assert cfg.get_path("nope") == ""
