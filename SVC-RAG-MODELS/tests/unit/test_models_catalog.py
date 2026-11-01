"""app/services/models_catalog.py: каталог локальных моделей, разбор путей, OpenAI-список."""

import pytest

from app.services import models_catalog as mc

pytestmark = pytest.mark.unit


class TestFolderName:
    @pytest.mark.parametrize(
        "raw,expected",
        [("", ""), (None, ""), ("  ", ""), ("FRIDA", "FRIDA"), ("org/FRIDA", "FRIDA"),
         ("a/b/FRIDA/", "FRIDA"), (" local/FRIDA ", "FRIDA")],
    )
    def test_folder_name(self, raw, expected):
        assert mc._folder_name(raw) == expected


class TestEnvModelFolders:
    def test_collects_base_and_numbered_vars_deduplicated(self, monkeypatch):
        monkeypatch.setenv("RAG_EMBEDDING_MODEL", "org/FRIDA")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL2", "frida")  # дубль без учёта регистра
        monkeypatch.setenv("RAG_EMBEDDING_MODEL3", "Giga")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL20", "Last")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL21", "ignored")  # вне диапазона 2..20
        monkeypatch.setenv("RAG_EMBEDDING_MODEL4", "   ")
        assert mc._env_model_folders("RAG_EMBEDDING_MODEL") == ["FRIDA", "Giga", "Last"]

    def test_empty_environment(self):
        assert mc._env_model_folders("RAG_EMBEDDING_MODEL") == []


class TestConfiguredFolders:
    def test_embedding_appends_settings_models_without_duplicates(self, rm_settings, monkeypatch):
        monkeypatch.setenv("RAG_EMBEDDING_MODEL", "FRIDA")
        # settings.embedding_model = FRIDA уже есть, default - добавляется
        assert mc.configured_embedding_folders() == [
            "FRIDA",
            "paraphrase-multilingual-MiniLM-L12-v2",
        ]

    def test_embedding_without_env_uses_settings(self):
        assert mc.configured_embedding_folders() == [
            "FRIDA",
            "paraphrase-multilingual-MiniLM-L12-v2",
        ]

    def test_embedding_skips_empty_settings(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "embedding_model", None)
        monkeypatch.setattr(rm_settings, "embedding_model_default", "")
        assert mc.configured_embedding_folders() == []

    def test_reranker_env_and_settings(self, monkeypatch):
        monkeypatch.setenv("RAG_RERANKER_MODEL2", "second")
        assert mc.configured_reranker_folders() == [
            "second",
            "ms-marco-MiniLM-L6-v2",
            "ms-marco-MiniLM-L-6-v2",
        ]

    def test_reranker_skips_empty_settings(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "reranker_model", None)
        monkeypatch.setattr(rm_settings, "reranker_model_default", "")
        assert mc.configured_reranker_folders() == []


class TestGuessKind:
    @pytest.mark.parametrize(
        "name",
        ["ms-marco-MiniLM-L6-v2", "bge-reranker-v2-m3", "cross-encoder-x", "TinyBERT-rank",
         "bge-reranker-v2-minicpm-layerwise", "my_minicpm_layerwise"],
    )
    def test_reranker_hints(self, name, tmp_path):
        assert mc._guess_kind(name, str(tmp_path)) == "reranker"

    @pytest.mark.parametrize(
        "name",
        ["paraphrase-multilingual-MiniLM-L12-v2", "multilingual-e5-large", "bge-m3",
         "FRIDA", "Giga-Embeddings-instruct", "my-embedding"],
    )
    def test_embedding_hints(self, name, tmp_path):
        assert mc._guess_kind(name, str(tmp_path)) == "embedding"

    def test_reranker_hint_wins_over_embedding_hint(self, tmp_path):
        assert mc._guess_kind("bge-reranker-v2", str(tmp_path)) == "reranker"

    def test_modules_json_marks_embedding(self, tmp_path):
        (tmp_path / "modules.json").write_text("[]")
        assert mc._guess_kind("mystery", str(tmp_path)) == "embedding"

    def test_st_config_marks_embedding(self, tmp_path):
        (tmp_path / "config_sentence_transformers.json").write_text("{}")
        assert mc._guess_kind("mystery", str(tmp_path)) == "embedding"

    def test_unknown_is_none(self, tmp_path):
        assert mc._guess_kind("mystery", str(tmp_path)) is None


class TestIsModelDir:
    def test_not_a_directory(self, tmp_path):
        f = tmp_path / "file"
        f.write_text("x")
        assert mc._is_model_dir(str(f)) is False
        assert mc._is_model_dir(str(tmp_path / "missing")) is False

    @pytest.mark.parametrize("marker", ["config.json", "modules.json", "pytorch_model.bin", "model.safetensors"])
    def test_marker_files(self, tmp_path, marker):
        (tmp_path / marker).write_text("x")
        assert mc._is_model_dir(str(tmp_path)) is True

    def test_empty_dir_is_not_a_model(self, tmp_path):
        assert mc._is_model_dir(str(tmp_path)) is False

    def test_hf_cache_layout_with_snapshot_config(self, tmp_path):
        snap = tmp_path / "snapshots" / "abc123"
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}")
        assert mc._is_model_dir(str(tmp_path)) is True

    def test_snapshots_without_config(self, tmp_path):
        (tmp_path / "snapshots" / "abc123").mkdir(parents=True)
        assert mc._is_model_dir(str(tmp_path)) is False


class TestRowsAndScan:
    def test_row_shape(self):
        assert mc._row("FRIDA", "embedding", available=True, description="d") == {
            "path": "local/FRIDA",
            "name": "FRIDA",
            "display_name": "FRIDA",
            "source": "local",
            "kind": "embedding",
            "description": "d",
            "available": True,
        }

    def test_scan_missing_dir_is_empty(self, tmp_path):
        assert mc._scan_local_models(str(tmp_path / "nope")) == []

    def test_scan_lists_sorted_known_models_only(self, models_dir, make_model):
        make_model("ms-marco-MiniLM-L6-v2", config={})
        make_model("FRIDA", config={})
        make_model("mystery", config={})  # тип не определить - пропуск
        make_model("empty-embedding")  # нет маркеров - не модель
        (models_dir / "stray-file.txt").write_text("x")
        rows = mc._scan_local_models(str(models_dir))
        assert [(r["name"], r["kind"], r["available"]) for r in rows] == [
            ("FRIDA", "embedding", True),
            ("ms-marco-MiniLM-L6-v2", "reranker", True),
        ]
        assert str(models_dir) in rows[0]["description"]

    def test_scan_listdir_error_gives_empty(self, models_dir, monkeypatch):
        def boom(path):
            raise OSError("denied")

        monkeypatch.setattr(mc.os, "listdir", boom)
        assert mc._scan_local_models(str(models_dir)) == []

    def test_configmap_rows_availability(self, models_dir, make_model):
        make_model("FRIDA", config={})
        rows = mc._configmap_rows(str(models_dir))
        by = {(r["kind"], r["name"]): r["available"] for r in rows}
        assert by[("embedding", "FRIDA")] is True
        assert by[("embedding", "paraphrase-multilingual-MiniLM-L12-v2")] is False
        assert by[("reranker", "ms-marco-MiniLM-L6-v2")] is False
        assert all("ConfigMap" in r["description"] for r in rows)


class TestListModels:
    def test_disk_row_wins_over_configmap_duplicate(self, make_model):
        make_model("FRIDA", config={})
        out = mc.list_models()
        frida = [r for r in out["embedding"] if r["name"] == "FRIDA"]
        assert len(frida) == 1
        assert frida[0]["available"] is True
        assert "Локальная модель" in frida[0]["description"]

    def test_configmap_only_models_are_unavailable(self):
        out = mc.list_models()
        assert [r["name"] for r in out["embedding"]] == [
            "FRIDA",
            "paraphrase-multilingual-MiniLM-L12-v2",
        ]
        assert not any(r["available"] for r in out["embedding"] + out["reranker"])

    def test_kind_filter(self, make_model):
        make_model("FRIDA", config={})
        out = mc.list_models("reranker")
        assert set(out) == {"reranker"}
        assert [r["name"] for r in out["reranker"]] == [
            "ms-marco-MiniLM-L6-v2",
            "ms-marco-MiniLM-L-6-v2",
        ]

    def test_case_insensitive_dedup_on_path(self, models_dir, make_model, rm_settings, monkeypatch):
        make_model("frida", config={})
        monkeypatch.setattr(rm_settings, "embedding_model", "FRIDA")
        names = [r["name"] for r in mc.list_models("embedding")["embedding"]]
        assert [n.lower() for n in names].count("frida") == 1


@pytest.mark.skipif(
    not hasattr(mc, "openai_model_list"),
    reason="GPB-ahead: models_catalog.openai_model_list отсутствует в memo",
)
class TestOpenaiModelList:
    def test_only_available_models_embeddings_first(self):
        models = {
            "embedding": [
                {"name": "off", "path": "local/off", "available": False},
                {"name": "FRIDA", "path": "local/FRIDA", "available": True, "display_name": "Фрида"},
            ],
            "reranker": [{"name": "ms", "path": "local/ms", "available": True}],
        }
        assert mc.openai_model_list(models) == [
            {"id": "FRIDA", "object": "model", "owned_by": "svc-rag-models", "kind": "embedding",
             "path": "local/FRIDA", "display_name": "Фрида"},
            {"id": "ms", "object": "model", "owned_by": "svc-rag-models", "kind": "reranker",
             "path": "local/ms", "display_name": "ms"},
        ]

    def test_missing_kinds_tolerated(self):
        assert mc.openai_model_list({"embedding": None}) == []
        assert mc.openai_model_list({}) == []


class TestParseModelPath:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("local/FRIDA", ("local", "FRIDA")),
            ("FRIDA", ("local", "FRIDA")),
            ("  FRIDA ", ("local", "FRIDA")),
            ("phoenix/some-model", ("phoenix", "some-model")),
            ("local/org/FRIDA", ("local", "org/FRIDA")),
        ],
    )
    def test_sources(self, raw, expected):
        assert mc.parse_model_path(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_empty_rejected(self, raw):
        with pytest.raises(ValueError, match="model_path пуст"):
            mc.parse_model_path(raw)

    @pytest.mark.parametrize("raw", ["huggingface/org/m", "HuggingFace/org/m"])
    def test_external_catalog_disabled(self, raw):
        with pytest.raises(ValueError, match="Внешний каталог"):
            mc.parse_model_path(raw)


class TestConfigValueForPath:
    def test_returns_last_component(self):
        assert mc.config_value_for_path("local/FRIDA") == "FRIDA"
        assert mc.config_value_for_path("local/org/FRIDA/") == "FRIDA"

    def test_phoenix_rejected(self):
        with pytest.raises(ValueError, match="Phoenix"):
            mc.config_value_for_path("phoenix/m")

    def test_traversal_collapses_to_last_component(self):
        assert mc.config_value_for_path("local/../../etc") == "etc"

    def test_dot_dot_alone_is_returned_verbatim(self):
        # '..' - отдельная компонента; отсекать её должен вызывающий код (см. test_models_api)
        assert mc.config_value_for_path("local/..") == ".."


class TestModelPathRows:
    def test_current_paths_reflect_settings_and_disk(self, make_model, rm_settings, monkeypatch):
        make_model("FRIDA", config={})
        cur = mc.current_model_paths()
        assert cur["embedding"] == {
            "path": "local/FRIDA", "name": "FRIDA", "display_name": "FRIDA",
            "source": "local", "kind": "embedding", "available": True,
        }
        assert cur["reranker"]["available"] is False
        monkeypatch.setattr(rm_settings, "embedding_model", None)
        monkeypatch.setattr(rm_settings, "reranker_model", None)
        cur = mc.current_model_paths()
        assert cur["embedding"]["name"] == "paraphrase-multilingual-MiniLM-L12-v2"
        assert cur["reranker"]["name"] == "ms-marco-MiniLM-L-6-v2"

    def test_cluster_default_uses_boot_snapshot_not_runtime_settings(self, make_model, monkeypatch, rm_settings):
        make_model("BootEmb", config={})
        monkeypatch.setattr(mc, "_BOOT_EMBEDDING_MODEL", "BootEmb")
        monkeypatch.setattr(mc, "_BOOT_RERANKER_MODEL", "org/BootRer")
        monkeypatch.setattr(rm_settings, "embedding_model", "ChangedAtRuntime")
        cd = mc.cluster_default_model_paths()
        assert cd["embedding"]["name"] == "BootEmb" and cd["embedding"]["available"] is True
        assert cd["reranker"]["name"] == "BootRer" and cd["reranker"]["path"] == "local/BootRer"
        assert cd["reranker"]["available"] is False

    def test_boot_snapshot_taken_from_config_at_import(self):
        assert mc._BOOT_EMBEDDING_MODEL == "FRIDA"
        assert mc._BOOT_RERANKER_MODEL == "ms-marco-MiniLM-L6-v2"

    def test_model_path_row_uses_value_when_no_folder(self):
        # '/' даёт пустую компоненту -> берётся исходное значение
        row = mc._model_path_row("/", "embedding")
        assert row["name"] == "/"
        assert row["available"] is False

    def test_available_requires_directory_inside_models_dir(self, models_dir):
        (models_dir / "FRIDA").write_text("not a dir")
        assert mc._model_path_row("FRIDA", "embedding")["available"] is False
