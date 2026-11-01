"""utils/llm_defaults: заводские LLM-настройки из seed-файла, максимумы и запасные значения."""

import json

import pytest

from backend.utils import llm_defaults as ld

pytestmark = pytest.mark.unit


@pytest.fixture
def seeds(monkeypatch, tmp_path):
    """Подменяет список seed-файлов и сбрасывает кэш (после теста кэш возвращается как был)."""
    monkeypatch.setattr(ld, "_cache", None)
    paths = []
    monkeypatch.setattr(ld, "llm_settings_seed_paths", lambda: list(paths))

    def add(name, content):
        path = tmp_path / name
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        paths.append(path)
        return path

    add.paths = paths
    return add


class TestIsSettingsKey:
    @pytest.mark.parametrize(("key", "expected"), [("temperature", True), ("_max_values", False), ("_comment", False), (5, True)])
    def test_underscore_keys_are_service_keys(self, key, expected):
        assert ld.is_settings_key(key) is expected


class TestReadSeed:
    def test_no_seed_files_gives_empty_dict(self, seeds):
        assert ld._read_seed() == {}

    def test_missing_files_are_skipped(self, seeds, tmp_path):
        seeds.paths.append(tmp_path / "absent.json")

        assert ld._read_seed() == {}

    def test_non_dict_seed_is_skipped_for_the_next_one(self, seeds):
        seeds("a.json", "[1, 2]")
        seeds("b.json", {"temperature": 0.1})

        assert ld._read_seed() == {"temperature": 0.1}

    def test_broken_json_is_skipped_for_the_next_one(self, seeds):
        seeds("a.json", "{not json")
        seeds("b.json", {"top_k": 7})

        assert ld._read_seed() == {"top_k": 7}


class TestBuildDefaults:
    def test_fallback_values_without_seed(self, seeds):
        defaults = ld.llm_settings_defaults()

        assert defaults["context_size"] == 100000
        assert defaults["temperature"] == 0.7
        assert ld.llm_settings_max_values()["context_size"] == 200000

    def test_seed_overrides_known_keys_only(self, seeds):
        seeds(
            "seed.json",
            {
                "temperature": 0.2,
                "top_k": None,
                "_comment": "служебное",
                "unknown_key": 1,
                "_max_values": {"temperature": 1.5, "unknown_max": 9, "top_k": None},
            },
        )

        defaults = ld.llm_settings_defaults()
        maxima = ld.llm_settings_max_values()

        assert defaults["temperature"] == 0.2
        assert defaults["top_k"] == 40  # None в seed не затирает дефолт
        assert "unknown_key" not in defaults and "_comment" not in defaults
        assert maxima["temperature"] == 1.5
        assert "unknown_max" not in maxima and maxima["top_k"] == 200

    def test_max_below_default_is_raised_to_the_default(self, seeds):
        seeds("seed.json", {"context_size": 500000, "_max_values": {"context_size": 1000}})

        assert ld.llm_settings_max_values()["context_size"] == 500000

    def test_non_numeric_defaults_do_not_affect_maxima(self, seeds):
        seeds("seed.json", {"temperature": "hot"})

        assert ld.llm_settings_max_values()["temperature"] == 2.0

    def test_non_dict_max_values_block_is_ignored(self, seeds):
        seeds("seed.json", {"_max_values": [1, 2]})

        assert ld.llm_settings_max_values()["top_p"] == 1.0

    def test_results_are_copies(self, seeds):
        ld.llm_settings_defaults()["temperature"] = 99
        ld.llm_settings_max_values()["temperature"] = 99

        assert ld.llm_settings_defaults()["temperature"] == 0.7
        assert ld.llm_settings_max_values()["temperature"] == 2.0

    def test_reload_rereads_the_seed(self, seeds):
        path = seeds("seed.json", {"temperature": 0.2})
        assert ld.llm_settings_defaults()["temperature"] == 0.2
        path.write_text(json.dumps({"temperature": 0.9}), encoding="utf-8")

        assert ld.llm_settings_defaults()["temperature"] == 0.2  # кэш
        assert ld.reload_llm_defaults()["defaults"]["temperature"] == 0.9
        assert ld.llm_settings_defaults()["temperature"] == 0.9
