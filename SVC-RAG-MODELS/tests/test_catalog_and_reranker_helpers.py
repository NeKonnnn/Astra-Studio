# -*- coding: utf-8 -*-
"""Юнит-тесты SVC-RAG-MODELS: каталог локальных моделей и вспомогательные функции LLM-реранкера."""

from __future__ import annotations

import json
import os
import shutil

import pytest

from app.core.config import settings
from app.services import llm_reranker as lr
from app.services import models_catalog as mc


def _clear_env(monkeypatch, prefix):
    for key in list(os.environ):
        if key.startswith(prefix):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """Изолированный models_dir и конфигурация без влияния окружения/ConfigMap."""
    _clear_env(monkeypatch, "RAG_EMBEDDING_MODEL")
    _clear_env(monkeypatch, "RAG_RERANKER_MODEL")
    cfg = settings.rag_models
    monkeypatch.setattr(cfg, "models_dir", str(tmp_path))
    monkeypatch.setattr(cfg, "embedding_model", None)
    monkeypatch.setattr(cfg, "embedding_model_default", None)
    monkeypatch.setattr(cfg, "reranker_model", None)
    monkeypatch.setattr(cfg, "reranker_model_default", None)
    return tmp_path


def make_model(root, name, *files):
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    for fname in files or ("config.json",):
        (path / fname).write_text("{}", encoding="utf-8")
    return path


class TestFolderName:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("", ""),
            (None, ""),
            ("  ", ""),
            ("model", "model"),
            ("org/model", "model"),
            ("/a/b/model/", "model"),
            ("local/FRIDA", "FRIDA"),
        ],
    )
    def test_folder_name(self, raw, expected):
        assert mc._folder_name(raw) == expected


class TestEnvModelFolders:
    def test_collects_numbered_variables_in_order(self, monkeypatch):
        _clear_env(monkeypatch, "RAG_EMBEDDING_MODEL")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL", "org/first")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL2", "second")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL20", "twentieth")
        assert mc._env_model_folders("RAG_EMBEDDING_MODEL") == ["first", "second", "twentieth"]

    def test_deduplicates_case_insensitively(self, monkeypatch):
        _clear_env(monkeypatch, "RAG_EMBEDDING_MODEL")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL", "Model")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL2", "org/MODEL")
        assert mc._env_model_folders("RAG_EMBEDDING_MODEL") == ["Model"]

    def test_ignores_variables_above_20(self, monkeypatch):
        _clear_env(monkeypatch, "RAG_EMBEDDING_MODEL")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL21", "too-far")
        assert mc._env_model_folders("RAG_EMBEDDING_MODEL") == []


class TestConfiguredFolders:
    def test_settings_values_are_appended_without_duplicates(self, catalog, monkeypatch):
        monkeypatch.setenv("RAG_EMBEDDING_MODEL", "from-env")
        monkeypatch.setattr(settings.rag_models, "embedding_model", "FROM-ENV")
        monkeypatch.setattr(settings.rag_models, "embedding_model_default", "default-one")
        assert mc.configured_embedding_folders() == ["from-env", "default-one"]

    def test_reranker_folders(self, catalog, monkeypatch):
        monkeypatch.setenv("RAG_RERANKER_MODEL2", "rr2")
        monkeypatch.setattr(settings.rag_models, "reranker_model", "rr-active")
        assert mc.configured_reranker_folders() == ["rr2", "rr-active"]


class TestGuessKind:
    @pytest.mark.parametrize(
        "name, kind",
        [
            ("bge-reranker-v2-minicpm-layerwise", "reranker"),
            ("ms-marco-MiniLM-L-6-v2", "reranker"),
            ("cross-encoder-x", "reranker"),
            ("paraphrase-multilingual-MiniLM-L12-v2", "embedding"),
            ("bge-m3", "embedding"),
            ("FRIDA", "embedding"),
            ("Giga-Embeddings-instruct", "embedding"),
        ],
    )
    def test_by_name(self, tmp_path, name, kind):
        assert mc._guess_kind(name, str(tmp_path)) == kind

    def test_reranker_hint_wins_over_embedding_hint(self, tmp_path):
        assert mc._guess_kind("bge-reranker-base", str(tmp_path)) == "reranker"

    def test_sentence_transformers_layout(self, tmp_path):
        make_model(tmp_path, "mystery", "modules.json")
        assert mc._guess_kind("mystery", str(tmp_path / "mystery")) == "embedding"
        make_model(tmp_path, "mystery2", "config_sentence_transformers.json")
        assert mc._guess_kind("mystery2", str(tmp_path / "mystery2")) == "embedding"

    def test_unknown(self, tmp_path):
        make_model(tmp_path, "unknown")
        assert mc._guess_kind("unknown", str(tmp_path / "unknown")) is None


class TestIsModelDir:
    @pytest.mark.parametrize("marker", ["config.json", "modules.json", "pytorch_model.bin", "model.safetensors"])
    def test_markers(self, tmp_path, marker):
        make_model(tmp_path, "m", marker)
        assert mc._is_model_dir(str(tmp_path / "m")) is True

    def test_hf_cache_snapshot_layout(self, tmp_path):
        snap = tmp_path / "m" / "snapshots" / "abc123"
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}")
        assert mc._is_model_dir(str(tmp_path / "m")) is True

    def test_empty_or_missing(self, tmp_path):
        (tmp_path / "empty").mkdir()
        assert mc._is_model_dir(str(tmp_path / "empty")) is False
        assert mc._is_model_dir(str(tmp_path / "missing")) is False

    def test_file_is_not_a_model(self, tmp_path):
        (tmp_path / "f").write_text("x")
        assert mc._is_model_dir(str(tmp_path / "f")) is False


class TestListModels:
    def test_disk_scan_classifies_models(self, catalog):
        make_model(catalog, "bge-m3")
        make_model(catalog, "ms-marco-MiniLM-L-6-v2")
        (catalog / "not-a-model").mkdir()
        make_model(catalog, "weird-unknown")
        result = mc.list_models()
        assert [r["name"] for r in result["embedding"]] == ["bge-m3"]
        assert [r["name"] for r in result["reranker"]] == ["ms-marco-MiniLM-L-6-v2"]
        assert result["embedding"][0]["path"] == "local/bge-m3"
        assert result["embedding"][0]["available"] is True

    def test_configmap_models_listed_as_unavailable(self, catalog, monkeypatch):
        monkeypatch.setenv("RAG_EMBEDDING_MODEL", "absent-emb")
        row = mc.list_models()["embedding"][0]
        assert row["name"] == "absent-emb"
        assert row["available"] is False

    def test_disk_row_not_duplicated_by_configmap(self, catalog, monkeypatch):
        make_model(catalog, "bge-m3")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL", "bge-m3")
        rows = mc.list_models()["embedding"]
        assert len(rows) == 1
        assert rows[0]["available"] is True

    def test_filter_by_kind(self, catalog):
        make_model(catalog, "bge-m3")
        assert list(mc.list_models("embedding")) == ["embedding"]
        assert list(mc.list_models("reranker")) == ["reranker"]

    def test_missing_models_dir(self, catalog, monkeypatch):
        monkeypatch.setattr(settings.rag_models, "models_dir", str(catalog / "absent"))
        assert mc.list_models() == {"embedding": [], "reranker": []}


class TestParseModelPath:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("local/FRIDA", ("local", "FRIDA")),
            ("FRIDA", ("local", "FRIDA")),
            ("  local/FRIDA  ", ("local", "FRIDA")),
            ("phoenix/model-x", ("phoenix", "model-x")),
        ],
    )
    def test_sources(self, raw, expected):
        assert mc.parse_model_path(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_empty_rejected(self, raw):
        with pytest.raises(ValueError, match="пуст"):
            mc.parse_model_path(raw)

    @pytest.mark.parametrize("raw", ["huggingface/BAAI/bge", "HuggingFace/x"])
    def test_external_catalog_rejected(self, raw):
        with pytest.raises(ValueError, match="Внешний каталог"):
            mc.parse_model_path(raw)

    def test_config_value_for_local(self):
        assert mc.config_value_for_path("local/org/FRIDA") == "FRIDA"
        assert mc.config_value_for_path("FRIDA") == "FRIDA"

    def test_config_value_rejects_phoenix(self):
        with pytest.raises(ValueError, match="Phoenix"):
            mc.config_value_for_path("phoenix/x")


class TestModelPaths:
    def test_current_model_paths_use_active_settings(self, catalog, monkeypatch):
        make_model(catalog, "FRIDA")
        monkeypatch.setattr(settings.rag_models, "embedding_model", "FRIDA")
        monkeypatch.setattr(settings.rag_models, "reranker_model", None)
        monkeypatch.setattr(settings.rag_models, "reranker_model_default", "rr-default")
        paths = mc.current_model_paths()
        assert paths["embedding"]["path"] == "local/FRIDA"
        assert paths["embedding"]["available"] is True
        assert paths["reranker"]["name"] == "rr-default"
        assert paths["reranker"]["available"] is False

    def test_active_beats_default(self, catalog, monkeypatch):
        monkeypatch.setattr(settings.rag_models, "embedding_model", "active")
        monkeypatch.setattr(settings.rag_models, "embedding_model_default", "default")
        monkeypatch.setattr(settings.rag_models, "reranker_model", "rr")
        assert mc.current_model_paths()["embedding"]["name"] == "active"

    def test_nothing_configured_gives_empty_rows_instead_of_crashing(self, catalog):
        paths = mc.current_model_paths()
        for kind in ("embedding", "reranker"):
            assert paths[kind]["name"] == ""
            assert paths[kind]["kind"] == kind
            assert paths[kind]["available"] is False

    def test_empty_name_is_not_reported_available_even_if_models_dir_is_a_model(self, catalog):
        # Пустое имя даёт путь к самому models_dir: он не должен считаться моделью.
        (catalog / "config.json").write_text("{}")
        assert mc.current_model_paths()["embedding"]["available"] is False

    def test_cluster_default_is_a_boot_snapshot(self, catalog, monkeypatch):
        before = mc.cluster_default_model_paths()
        monkeypatch.setattr(settings.rag_models, "embedding_model", "changed-at-runtime")
        assert mc.cluster_default_model_paths() == before


class TestIsLlmRerankerPath:
    @pytest.mark.parametrize(
        "path",
        [
            "/models/bge-reranker-v2-minicpm-layerwise",
            "local\\BGE-RERANKER-V2-GEMMA",
            "/models/reranker-v2-gemma",
            "/m/minicpm_layerwise",
        ],
    )
    def test_by_name(self, path):
        assert lr.is_llm_reranker_path(path) is True

    @pytest.mark.parametrize("path", ["", None, "/models/ms-marco-MiniLM", "/models/bge-m3"])
    def test_plain_names(self, path):
        assert lr.is_llm_reranker_path(path) is False

    def _model_with_config(self, tmp_path, cfg):
        d = tmp_path / "plain-name"
        d.mkdir()
        (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        return str(d)

    def test_by_architecture(self, tmp_path):
        path = self._model_with_config(tmp_path, {"architectures": ["LayerWiseMiniCPMForCausalLM"]})
        assert lr.is_llm_reranker_path(path) is True

    def test_by_minicpm_with_auto_map(self, tmp_path):
        path = self._model_with_config(tmp_path, {"model_type": "minicpm", "auto_map": {"AutoConfig": "x.Y"}})
        assert lr.is_llm_reranker_path(path) is True

    def test_minicpm_without_auto_map_is_not_llm_reranker(self, tmp_path):
        assert lr.is_llm_reranker_path(self._model_with_config(tmp_path, {"model_type": "minicpm"})) is False

    def test_cross_encoder_config(self, tmp_path):
        path = self._model_with_config(tmp_path, {"architectures": ["BertForSequenceClassification"]})
        assert lr.is_llm_reranker_path(path) is False

    def test_broken_config_json(self, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        (d / "config.json").write_text("{not json", encoding="utf-8")
        assert lr.is_llm_reranker_path(str(d)) is False


class TestAutoMapHelpers:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("BAAI/model--modeling_x.Class", "modeling_x.Class"),
            ("modeling_x.Class", "modeling_x.Class"),
            ("a--b--c.D", "b--c.D"),
            (None, None),
            (5, 5),
        ],
    )
    def test_local_automap_ref(self, value, expected):
        assert lr._local_automap_ref(value) == expected

    def test_fix_automap_dict_reports_changes(self):
        fixed, changed = lr._fix_automap_dict(
            {"AutoConfig": "org/m--configuration_x.Cfg", "AutoModel": "modeling_x.Model", "Other": ["x"]}
        )
        assert changed is True
        assert fixed == {"AutoConfig": "configuration_x.Cfg", "AutoModel": "modeling_x.Model", "Other": ["x"]}

    def test_fix_automap_dict_no_change(self):
        fixed, changed = lr._fix_automap_dict({"AutoModel": "modeling_x.Model"})
        assert changed is False
        assert fixed == {"AutoModel": "modeling_x.Model"}

    @pytest.mark.parametrize("value", [None, [], "str", 5])
    def test_fix_automap_dict_invalid_input(self, value):
        assert lr._fix_automap_dict(value) == ({}, False)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestPrepareOfflineModelDir:
    def _write(self, d, cfg, extra_files=()):
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        for name in extra_files:
            (d / name).write_text("x", encoding="utf-8")
        return str(d)

    @pytest.fixture
    def tmp_dirs(self):
        """Удаляет временные каталоги, созданные _prepare_offline_model_dir."""
        before = list(lr._TMP_MODEL_DIRS)
        yield
        for d in lr._TMP_MODEL_DIRS[len(before):]:
            shutil.rmtree(d, ignore_errors=True)
        del lr._TMP_MODEL_DIRS[len(before):]

    def test_missing_config(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="config.json"):
            lr._prepare_offline_model_dir(str(tmp_path))

    def test_clean_config_returns_original_path(self, tmp_path):
        path = self._write(tmp_path / "m", {"model_type": "bert"})
        assert lr._prepare_offline_model_dir(path) == path

    def test_missing_remote_code_file(self, tmp_path):
        path = self._write(tmp_path / "m", {"auto_map": {"AutoModel": "modeling_x.Model"}})
        with pytest.raises(FileNotFoundError, match="modeling_x.py"):
            lr._prepare_offline_model_dir(path)

    def test_repo_prefixed_auto_map_is_rewritten_in_tmp_dir(self, tmp_path, tmp_dirs):
        path = self._write(
            tmp_path / "m",
            {"auto_map": {"AutoModel": "BAAI/m--modeling_x.Model"}, "_name_or_path": "BAAI/m"},
            extra_files=("modeling_x.py", "model.safetensors"),
        )
        out = lr._prepare_offline_model_dir(path)
        assert out != path
        assert lr._TMP_MODEL_DIRS[-1] == out
        fixed = _read_json(os.path.join(out, "config.json"))
        assert fixed["auto_map"]["AutoModel"] == "modeling_x.Model"
        assert fixed["_name_or_path"] == path
        # Веса и код доступны из временного каталога, исходный config не тронут.
        assert os.path.exists(os.path.join(out, "model.safetensors"))
        assert os.path.exists(os.path.join(out, "modeling_x.py"))
        assert _read_json(os.path.join(path, "config.json"))["auto_map"]["AutoModel"] == "BAAI/m--modeling_x.Model"

    def test_remote_name_or_path_is_rewritten(self, tmp_path, tmp_dirs):
        path = self._write(tmp_path / "m", {"_name_or_path": "org/repo"})
        out = lr._prepare_offline_model_dir(path)
        assert out != path
        assert _read_json(os.path.join(out, "config.json"))["_name_or_path"] == path


def test_force_offline_env(monkeypatch):
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        monkeypatch.delenv(key, raising=False)
    lr._force_offline_env()
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["HF_DATASETS_OFFLINE"] == "1"
