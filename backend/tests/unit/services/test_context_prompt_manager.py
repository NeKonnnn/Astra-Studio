"""ContextPromptManager: загрузка/сохранение файла, приоритеты промптов, импорт/экспорт."""

import json

import pytest

from backend import context_prompts as cp


@pytest.fixture
def paths(monkeypatch, tmp_path):
    """Все пути - в tmp_path; реальные файлы приложения не трогаем."""
    primary = tmp_path / "data" / "context_prompts.json"
    state = {"primary": primary, "seeds": [], "fallbacks": None, "unsafe": set()}

    monkeypatch.setattr(cp, "resolve_context_prompts_file_path_primary", lambda: state["primary"])
    monkeypatch.setattr(
        cp, "resolve_context_prompts_file_path",
        lambda p: None if str(p) in state["unsafe"] else __import__("pathlib").Path(p),
    )
    monkeypatch.setattr(cp, "context_prompts_seed_paths", lambda: list(state["seeds"]))
    monkeypatch.setattr(
        cp, "context_prompts_file_fallbacks",
        lambda p: state["fallbacks"] if state["fallbacks"] is not None else [__import__("pathlib").Path(p)],
    )
    return state


def make(paths):
    return cp.ContextPromptManager()


class TestLoad:
    def test_missing_file_is_created_with_defaults(self, paths):
        m = make(paths)
        assert m.context_prompts == {"global_prompt": "", "model_prompts": {}, "custom_prompts": {}}
        assert json.loads(paths["primary"].read_text(encoding="utf-8")) == m.context_prompts

    def test_primary_file_wins_over_seed(self, paths, tmp_path):
        seed = tmp_path / "seed.json"
        seed.write_text(json.dumps({"global_prompt": "seed"}), encoding="utf-8")
        paths["seeds"] = [seed]
        paths["primary"].parent.mkdir(parents=True)
        paths["primary"].write_text(json.dumps({"global_prompt": "user"}), encoding="utf-8")
        assert make(paths).get_global_prompt() == "user"

    def test_seed_is_used_when_primary_missing(self, paths, tmp_path):
        seed = tmp_path / "seed.json"
        seed.write_text(json.dumps({"global_prompt": "seed"}), encoding="utf-8")
        paths["seeds"] = [seed]
        assert make(paths).get_global_prompt() == "seed"

    def test_broken_or_non_dict_files_fall_back_to_defaults(self, paths, tmp_path):
        paths["primary"].parent.mkdir(parents=True)
        paths["primary"].write_text("{broken", encoding="utf-8")
        assert make(paths).get_global_prompt() == ""
        paths["primary"].write_text("[1, 2]", encoding="utf-8")
        assert make(paths).context_prompts["model_prompts"] == {}

    def test_unwritable_location_keeps_defaults_in_memory(self, paths):
        paths["fallbacks"] = []
        m = make(paths)
        assert m.get_global_prompt() == "" and not paths["primary"].exists()

    def test_candidates_are_deduplicated(self, paths):
        m = make(paths)
        paths["seeds"] = [paths["primary"], paths["primary"]]
        assert m._prompts_load_candidates().count(str(paths["primary"])) == 1

    def test_validated_path_is_resolved_lazily(self, paths):
        m = make(paths)
        m._prompts_path = None
        assert m._validated_prompts_path() == paths["primary"]


class TestSave:
    def test_replaces_content(self, paths):
        m = make(paths)
        assert m.save_context_prompts({"global_prompt": "новый"}) is True
        assert json.loads(paths["primary"].read_text(encoding="utf-8"))["global_prompt"] == "новый"

    def test_save_without_state_sets_defaults(self, paths):
        m = cp.ContextPromptManager.__new__(cp.ContextPromptManager)
        m.prompts_file = str(paths["primary"])
        m._prompts_path = None
        assert m.save_context_prompts() is True
        assert m.context_prompts["custom_prompts"] == {}

    def test_switches_to_fallback_when_primary_unwritable(self, paths, tmp_path):
        m = make(paths)
        blocked = tmp_path / "file_not_dir"
        blocked.write_text("x", encoding="utf-8")
        good = tmp_path / "fb" / "context_prompts.json"
        paths["fallbacks"] = [blocked / "p.json", good]
        assert m.save_context_prompts({"global_prompt": "fb"}) is True
        assert m.prompts_file == str(good) and m._prompts_path == good
        assert json.loads(good.read_text(encoding="utf-8"))["global_prompt"] == "fb"

    def test_unsafe_targets_are_skipped_and_all_failing_returns_false(self, paths, tmp_path):
        m = make(paths)
        bad = tmp_path / "bad.json"
        paths["unsafe"].add(str(bad))
        paths["fallbacks"] = [bad]
        assert m.save_context_prompts({"global_prompt": "x"}) is False

    def test_unexpected_error_does_not_raise(self, paths, monkeypatch):
        m = make(paths)
        monkeypatch.setattr(cp.json, "dump", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
        assert m.save_context_prompts({"global_prompt": "x"}) is False


class TestPrompts:
    def test_global_and_model_prompts(self, paths):
        m = make(paths)
        assert m.set_global_prompt("общий") is True
        assert m.get_model_prompt("m/a") == "общий"  # нет своего - общий
        m.set_model_prompt("m/a", "свой")
        assert m.get_model_prompt("m/a") == "свой"
        m.set_model_prompt("m/a", "   ")  # пусто - удалить
        assert m.get_model_prompt("m/a") == "общий"
        m.set_model_prompt("m/none", "")  # удаление несуществующего безопасно
        assert m.context_prompts["model_prompts"] == {}

    def test_model_prompts_section_is_created(self, paths):
        m = make(paths)
        del m.context_prompts["model_prompts"]
        m.set_model_prompt("m", "p")
        assert m.context_prompts["model_prompts"] == {"m": "p"}

    def test_custom_prompts_crud(self, paths):
        m = make(paths)
        assert m.get_custom_prompt("x") is None
        assert m.delete_custom_prompt("x") is False
        m.set_custom_prompt("x", "текст", "описание")
        row = m.get_custom_prompt("x")
        assert row["prompt"] == "текст" and row["description"] == "описание" and row["created_at"]
        assert list(m.get_all_custom_prompts()) == ["x"]
        assert m.delete_custom_prompt("x") is True and m.get_all_custom_prompts() == {}

    def test_custom_section_is_created(self, paths):
        m = make(paths)
        del m.context_prompts["custom_prompts"]
        assert m.get_all_custom_prompts() == {}
        m.set_custom_prompt("a", "p")
        assert "a" in m.context_prompts["custom_prompts"]
        del m.context_prompts["custom_prompts"]
        assert m.delete_custom_prompt("a") is False

    def test_effective_prompt_priority(self, paths):
        m = make(paths)
        m.set_global_prompt("общий")
        m.set_model_prompt("m", "модельный")
        m.set_custom_prompt("c", "кастомный")
        assert m.get_effective_prompt("m", "c") == "кастомный"
        assert m.get_effective_prompt("m", "нет-такого") == "модельный"
        assert m.get_effective_prompt("other") == "общий"
        m.context_prompts["custom_prompts"]["broken"] = {"description": "без prompt"}
        assert m.get_effective_prompt("other", "broken") == "общий"


class TestEnrichAndResolve:
    def test_enrich_models(self, paths):
        m = make(paths)
        m.set_global_prompt("общий")
        m.set_model_prompt("m/a", "свой")
        rows = m.enrich_models_with_prompts([{"path": "m/a"}, {"path": " "}, {"id": 1}, {"path": "m/b"}])
        assert [(r["context_prompt"], r["has_custom_prompt"]) for r in rows] == [
            ("свой", True), ("общий", False), ("общий", False), ("общий", False),
        ]

    def test_resolve_uses_context_when_no_entity_prompt(self, paths):
        m = make(paths)
        m.set_global_prompt("общий")
        m.set_model_prompt("m", "модельный")
        assert m.resolve_chat_system_prompt("m") == "модельный"
        assert m.resolve_chat_system_prompt(None) == "общий"

    def test_entity_prompts_override_context_and_keep_order(self, paths):
        m = make(paths)
        m.set_global_prompt("общий")
        assert m.resolve_chat_system_prompt("m", agent_system_prompt=" агент ") == "агент"
        out = m.resolve_chat_system_prompt("m", agent_system_prompt="агент", project_instructions="проект")
        assert out == "проект\n\nагент"

    def test_nothing_gives_none(self, paths):
        m = make(paths)
        assert m.resolve_chat_system_prompt("m") is None
        assert m.resolve_chat_system_prompt("m", agent_system_prompt="  ", project_instructions=None) is None


class TestImportExport:
    def test_roundtrip(self, paths, tmp_path):
        m = make(paths)
        m.set_global_prompt("общий")
        target = tmp_path / "export.json"
        assert m.export_prompts(str(target)) is True
        other = make(paths)
        other.set_global_prompt("другой")
        assert other.import_prompts(str(target)) is True
        assert other.get_global_prompt() == "общий"

    def test_export_error(self, paths, tmp_path):
        assert make(paths).export_prompts(str(tmp_path / "no_dir" / "x.json")) is False

    def test_import_rejects_garbage(self, paths, tmp_path):
        m = make(paths)
        assert m.import_prompts(str(tmp_path / "missing.json")) is False
        arr = tmp_path / "arr.json"
        arr.write_text("[1]", encoding="utf-8")
        assert m.import_prompts(str(arr)) is False
        bad = tmp_path / "bad.json"
        bad.write_text("{", encoding="utf-8")
        assert m.import_prompts(str(bad)) is False

    def test_timestamp_is_iso(self, paths):
        from datetime import datetime

        datetime.fromisoformat(make(paths)._get_current_timestamp())
