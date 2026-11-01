"""app/dependencies/rag_models_handler.py: загрузка/LRU моделей, устройство, ошибки, cleanup.

torch / sentence_transformers / transformers - фейки (tests/fakes.py), модели - каталоги в tmp_path.
"""

import asyncio
import io
import json
import logging
import os
import sys
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.dependencies import rag_models_handler as h

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- helpers
class TestFreeModelMemory:
    def test_empties_cuda_cache_when_available(self, fake_ml):
        fake_ml.cuda_available = True
        h._free_model_memory()
        assert fake_ml.empty_cache_calls == 1

    def test_skips_empty_cache_without_cuda(self, fake_ml):
        h._free_model_memory()
        assert fake_ml.empty_cache_calls == 0

    def test_missing_torch_is_not_an_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", None)  # import -> ImportError
        h._free_model_memory()  # не падает


class TestRssMb:
    def test_parses_vmrss_in_mb(self, monkeypatch):
        monkeypatch.setattr(
            h, "open", lambda *a, **k: io.StringIO("Name:\tx\nVmRSS:\t  2048 kB\n"), raising=False
        )
        assert h._rss_mb() == 2

    def test_malformed_line_gives_none(self, monkeypatch):
        monkeypatch.setattr(
            h, "open", lambda *a, **k: io.StringIO("VmRSS: abc kB\n"), raising=False
        )
        assert h._rss_mb() is None

    def test_no_vmrss_line_gives_none(self, monkeypatch):
        monkeypatch.setattr(
            h, "open", lambda *a, **k: io.StringIO("Name:\tx\n"), raising=False
        )
        assert h._rss_mb() is None

    def test_unreadable_proc_gives_none(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no /proc")

        monkeypatch.setattr(h, "open", boom, raising=False)
        assert h._rss_mb() is None


class TestLoggingHelpers:
    def test_loaded_keys_format(self, handler_state):
        h._MODEL_CACHE[("embedding", "FRIDA")] = {}
        h._MODEL_CACHE[("reranker", "ms")] = {}
        assert h._loaded_keys() == ["embedding:FRIDA", "reranker:ms"]

    def test_log_models_prints_line(self, monkeypatch, capsys):
        monkeypatch.setattr(h, "_rss_mb", lambda: 7)
        h._log_models("loaded", "embedding", "FRIDA", 8)
        out = capsys.readouterr().out
        assert "[MODELS] loaded embedding=FRIDA dim=8 rss=7MB loaded=[]" in out


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("FRIDA", "FRIDA"),
        ("local/FRIDA", "FRIDA"),
        ("LOCAL/FRIDA", "FRIDA"),
        ("native/FRIDA", "FRIDA"),
        ("org/sub/FRIDA", "FRIDA"),
        ("FRIDA/", "FRIDA"),
        ("/abs/path/FRIDA", "FRIDA"),
        ("/abs/path/FRIDA/", "FRIDA"),
    ],
)
def test_folder_of(raw, expected):
    assert h._folder_of(raw) == expected


class TestFolderResolutionTraversal:
    """Имя модели из запроса не должно давать выход из models_dir."""

    @pytest.mark.parametrize(
        "name", ["../../etc", "..", "/etc/passwd", "local/../..", "..\\..\\windows", "FRIDA/../.."]
    )
    async def test_request_name_outside_allowed_list_is_rejected(self, fake_ml, name):
        with pytest.raises(ValueError, match="не разрешена"):
            await h.get_embedding_model(name)
        assert fake_ml.st_calls == []  # до загрузки дело не дошло


class TestDefaultFolder:
    def test_embedding_uses_active_model(self):
        assert h._default_folder("embedding") == "FRIDA"

    def test_embedding_falls_back_to_default(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "embedding_model", None)
        assert h._default_folder("embedding") == "paraphrase-multilingual-MiniLM-L12-v2"

    def test_reranker_uses_active_and_default(self, rm_settings, monkeypatch):
        assert h._default_folder("reranker") == "ms-marco-MiniLM-L6-v2"
        monkeypatch.setattr(rm_settings, "reranker_model", "")
        assert h._default_folder("reranker") == "ms-marco-MiniLM-L-6-v2"


class TestConfiguredFolders:
    def test_embedding_order_dedup_and_default_last(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "embedding_models", ["Extra", "frida", "local/Third"])
        monkeypatch.setenv("RAG_EMBEDDING_MODEL5", "FromEnv")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL7", "extra")
        assert h.configured_folders("embedding") == [
            "FRIDA",
            "Giga-Embeddings-instruct",
            "Extra",
            "Third",
            "FromEnv",
            "paraphrase-multilingual-MiniLM-L12-v2",
        ]

    def test_reranker_uses_reranker_settings_and_env(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "reranker_model2", "second-rer")
        monkeypatch.setattr(rm_settings, "reranker_models", ["third-rer"])
        monkeypatch.setenv("RAG_RERANKER_MODEL20", "env-rer")
        monkeypatch.setenv("RAG_EMBEDDING_MODEL3", "not-for-reranker")
        assert h.configured_folders("reranker") == [
            "ms-marco-MiniLM-L6-v2",
            "second-rer",
            "third-rer",
            "env-rer",
            "ms-marco-MiniLM-L-6-v2",
        ]

    def test_none_and_blank_items_skipped(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "embedding_model", None)
        monkeypatch.setattr(rm_settings, "embedding_model2", None)
        monkeypatch.setattr(rm_settings, "embedding_models", None)
        assert h.configured_folders("embedding") == ["paraphrase-multilingual-MiniLM-L12-v2"]


# --------------------------------------------------------------------------- Giga
class TestIsGigaEmbedPath:
    def test_by_folder_name(self):
        assert h._is_giga_embed_path("/m/Giga-Embeddings-instruct") is True
        assert h._is_giga_embed_path("C:\\m\\GIGA_embed") is True

    def test_by_config_model_type(self, make_model):
        d = make_model("mystery", config={"model_type": "GigarEmbed"})
        assert h._is_giga_embed_path(str(d)) is True

    def test_other_model_type_is_false(self, make_model):
        d = make_model("bert-like", config={"model_type": "bert"})
        assert h._is_giga_embed_path(str(d)) is False

    def test_no_config_file(self, make_model):
        d = make_model("plain")
        assert h._is_giga_embed_path(str(d)) is False

    def test_empty_path(self):
        assert h._is_giga_embed_path("") is False

    def test_broken_json_is_false(self, make_model):
        d = make_model("broken")
        (d / "config.json").write_text("{not json", encoding="utf-8")
        assert h._is_giga_embed_path(str(d)) is False

    def test_unreadable_config_is_false(self, make_model, monkeypatch):
        d = make_model("noaccess", config={"model_type": "gigarembed"})

        def boom(*a, **k):
            raise OSError("denied")

        monkeypatch.setattr(h, "open", boom, raising=False)
        assert h._is_giga_embed_path(str(d)) is False


class TestRegisterGigaRemoteCode:
    def test_registers_both_pairs_with_exist_ok(self, fake_ml):
        h._register_giga_remote_code("/m/giga")
        assert fake_ml.dyn_calls == [
            "configuration_gigarembed.LatentAttentionConfig",
            "modeling_gigarembed.LatentAttentionModel",
            "configuration_gigarembed.GigarEmbedConfig",
            "modeling_gigarembed.GigarEmbedModel",
        ]
        assert len(fake_ml.automodel_register_calls) == 2
        assert all(c[2] == {"exist_ok": True} for c in fake_ml.automodel_register_calls)

    def test_old_transformers_without_exist_ok_falls_back(self, fake_ml):
        fake_ml.automodel_register_mode = "typeerror"
        h._register_giga_remote_code("/m/giga")
        kwargs = [c[2] for c in fake_ml.automodel_register_calls]
        assert kwargs == [{"exist_ok": True}, {}, {"exist_ok": True}, {}]

    def test_already_registered_value_error_is_swallowed(self, fake_ml):
        fake_ml.automodel_register_mode = "typeerror_valueerror"
        h._register_giga_remote_code("/m/giga")  # не бросает
        assert len(fake_ml.automodel_register_calls) == 4

    def test_dynamic_module_failure_only_warns(self, fake_ml, caplog):
        fake_ml.dyn_error = RuntimeError("нет сети")
        with caplog.at_level(logging.WARNING, logger=h.logger.name):
            h._register_giga_remote_code("/m/giga")
        assert "предрегистрация remote-code не удалась" in caplog.text
        assert fake_ml.automodel_register_calls == []

    def test_missing_transformers_only_warns(self, monkeypatch, caplog):
        monkeypatch.setitem(sys.modules, "transformers", None)
        with caplog.at_level(logging.WARNING, logger=h.logger.name):
            h._register_giga_remote_code("/m/giga")
        assert "предрегистрация" in caplog.text


class TestLoadSentenceTransformer:
    def test_cpu_uses_float32_and_local_files(self, fake_ml):
        model = h._load_sentence_transformer("/m/plain", "cpu")
        call = fake_ml.st_calls[0]
        assert call["path"] == "/m/plain"
        assert call["device"] == "cpu"
        assert call["local_files_only"] is True
        assert call["trust_remote_code"] is True
        assert call["model_kwargs"] == {"trust_remote_code": True, "torch_dtype": "float32"}
        assert call["config_kwargs"] == {"trust_remote_code": True}
        assert model.device == "cpu"
        assert fake_ml.dyn_calls == []  # не Giga: remote-code не регистрируем

    def test_cuda_prefers_bfloat16(self, fake_ml):
        h._load_sentence_transformer("/m/plain", "cuda:0")
        assert fake_ml.st_calls[0]["model_kwargs"]["torch_dtype"] == "bfloat16"

    def test_cuda_without_bf16_uses_float16(self, fake_ml):
        fake_ml.bf16 = False
        h._load_sentence_transformer("/m/plain", "cuda")
        assert fake_ml.st_calls[0]["model_kwargs"]["torch_dtype"] == "float16"

    def test_giga_registers_remote_code_first(self, fake_ml):
        h._load_sentence_transformer("/m/Giga-Embeddings-instruct", "cpu")
        assert len(fake_ml.dyn_calls) == 4
        assert len(fake_ml.st_calls) == 1


# --------------------------------------------------------------------------- path
class TestResolveModelPath:
    def test_empty_name_uses_default_local(self, models_dir, make_model):
        d = make_model("fallback-model")
        assert h._resolve_model_path(str(models_dir), None, "fallback-model") == str(d)

    def test_exact_folder(self, models_dir, make_model):
        d = make_model("FRIDA")
        assert h._resolve_model_path(str(models_dir), "FRIDA", "x") == str(d)

    def test_org_prefix_dropped_for_relative_names(self, models_dir, make_model):
        d = make_model("FRIDA")
        assert h._resolve_model_path(str(models_dir), "ai-forever/FRIDA", "x") == str(d)

    def test_absolute_existing_dir_returned_as_is(self, models_dir, tmp_path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        assert h._resolve_model_path(str(models_dir), str(other), "x") == str(other)

    def test_ms_marco_prefix_matches_other_version(self, models_dir, make_model):
        (models_dir / "a-file").write_text("not a dir")  # файлы пропускаются
        d = make_model("ms-marco-MiniLM-L-6-v2")
        got = h._resolve_model_path(str(models_dir), "ms-marco-MiniLM-L6-v2", "x")
        assert got == os.path.join(str(models_dir), d.name)

    def test_paraphrase_multilingual_prefix(self, models_dir, make_model):
        d = make_model("paraphrase-multilingual-mpnet-base-v2")
        got = h._resolve_model_path(str(models_dir), "paraphrase-multilingual-MiniLM-L12-v2", "x")
        assert got == os.path.join(str(models_dir), d.name)

    def test_bge_reranker_matches_when_name_contained(self, models_dir, make_model):
        d = make_model("bge-reranker-v2-minicpm-layerwise")
        got = h._resolve_model_path(str(models_dir), "bge-reranker-v2", "x")
        assert got == os.path.join(str(models_dir), d.name)

    def test_bge_reranker_unrelated_variant_not_matched(self, models_dir, make_model):
        make_model("bge-reranker-base", config={"a": 1})
        got = h._resolve_model_path(str(models_dir), "bge-reranker-large", "x")
        assert got == os.path.join(str(models_dir), "bge-reranker-large")

    def test_fuzzy_match_picks_snapshot_with_config(self, models_dir, make_model):
        cand = make_model("models--org--FRIDA-hf-cache")
        snaps = cand / "snapshots"
        (snaps / "notadir-file").parent.mkdir(parents=True)
        (snaps / "notadir-file").write_text("x")
        empty_snap = snaps / "aaa-no-config"
        empty_snap.mkdir()
        good = snaps / "bbb-good"
        good.mkdir()
        (good / "config.json").write_text("{}")
        got = h._resolve_model_path(str(models_dir), "frida", "x")
        assert got == str(good)

    def test_fuzzy_match_uses_candidate_config_without_snapshots(self, models_dir, make_model):
        (models_dir / "aa-frida-file").write_text("x")  # не каталог - пропуск
        d = make_model("my-frida-copy", config={"a": 1})
        assert h._resolve_model_path(str(models_dir), "frida", "x") == str(d)

    def test_fuzzy_snapshots_without_config_fall_back_to_candidate_config(self, models_dir, make_model):
        d = make_model("frida-cache", config={"a": 1})
        (d / "snapshots" / "empty-hash").mkdir(parents=True)
        assert h._resolve_model_path(str(models_dir), "frida", "x") == str(d)

    def test_fuzzy_candidate_without_config_falls_back_to_full(self, models_dir, make_model):
        make_model("frida-empty")
        got = h._resolve_model_path(str(models_dir), "frida", "x")
        assert got == os.path.join(str(models_dir), "frida")

    def test_missing_models_dir_returns_joined_path(self, tmp_path):
        missing = str(tmp_path / "nope")
        assert h._resolve_model_path(missing, "FRIDA", "x") == os.path.join(missing, "FRIDA")


# --------------------------------------------------------------------------- env / device
class TestPrepareEnvOnce:
    def test_creates_dirs_and_sets_offline_env(self, rm_settings, models_dir, tmp_path, monkeypatch):
        cache = tmp_path / "cacheroot"
        monkeypatch.setenv("TRANSFORMERS_CACHE", str(cache))
        got = h._prepare_env_once()
        assert got == os.path.abspath(str(models_dir))
        assert os.environ["HF_HOME"] == str(cache)
        assert os.environ["HF_HUB_CACHE"] == os.path.join(str(cache), "hub")
        assert os.environ["TRANSFORMERS_CACHE"] == os.path.join(str(cache), "transformers")
        assert os.path.isdir(os.environ["SENTENCE_TRANSFORMERS_HOME"])
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"

    def test_hf_home_used_when_no_transformers_cache(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRANSFORMERS_CACHE")
        monkeypatch.setenv("HF_HOME", str(tmp_path / "hf-home"))
        h._prepare_env_once()
        assert os.environ["HF_HUB_CACHE"] == os.path.join(str(tmp_path / "hf-home"), "hub")

    def test_default_cache_root_when_no_env(self, monkeypatch):
        monkeypatch.delenv("TRANSFORMERS_CACHE")
        monkeypatch.delenv("HF_HOME", raising=False)
        made = []
        monkeypatch.setattr(h.os, "makedirs", lambda p, exist_ok=False: made.append(p))
        h._prepare_env_once()
        assert os.environ["HF_HOME"] == "/tmp/rag-models-cache"
        assert os.path.join("/tmp/rag-models-cache", "hub") in made

    def test_readonly_models_dir_is_tolerated(self, rm_settings, tmp_path, monkeypatch):
        blocker = tmp_path / "file-instead-of-dir"
        blocker.write_text("x")
        monkeypatch.setattr(rm_settings, "models_dir", str(blocker / "models"))
        got = h._prepare_env_once()  # makedirs -> OSError, но не падаем
        assert got == os.path.abspath(str(blocker / "models"))

    def test_unwritable_cache_falls_back_to_models_dir(self, models_dir, tmp_path, monkeypatch, caplog):
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        monkeypatch.setenv("TRANSFORMERS_CACHE", str(blocker / "cache"))
        with caplog.at_level(logging.WARNING, logger=h.logger.name):
            got = h._prepare_env_once()
        assert "недоступен на запись" in caplog.text
        for key in ("HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE"):
            assert os.environ[key] == got
        assert os.environ["HF_HUB_OFFLINE"] == "1"


class TestResolveDevice:
    def test_explicit_device_returned(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "device", "cuda:1")
        assert h._resolve_device() == "cuda:1"

    def test_auto_picks_cuda_when_available(self, rm_settings, monkeypatch, fake_ml):
        monkeypatch.setattr(rm_settings, "device", "auto")
        fake_ml.cuda_available = True
        assert h._resolve_device() == "cuda"

    def test_auto_picks_cpu_without_cuda(self, rm_settings, monkeypatch, fake_ml):
        monkeypatch.setattr(rm_settings, "device", "auto")
        assert h._resolve_device() == "cpu"

    def test_auto_without_torch_is_cpu(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "device", "auto")
        monkeypatch.setitem(sys.modules, "torch", None)
        assert h._resolve_device() == "cpu"


class TestMaxLoadedAndEviction:
    @pytest.mark.parametrize("cap,expected", [(3, 3), (0, 1), (-5, 1), ("4", 4), ("abc", 1), (None, 1)])
    def test_max_loaded_normalised(self, rm_settings, monkeypatch, cap, expected):
        monkeypatch.setattr(rm_settings, "max_loaded_embedding", cap)
        assert h._max_loaded("embedding") == expected

    def test_max_loaded_reranker_uses_own_setting(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "max_loaded_reranker", 5)
        assert h._max_loaded("reranker") == 5

    def test_evicts_oldest_of_same_kind_only(self, rm_settings, monkeypatch, capsys, fake_ml):
        monkeypatch.setattr(rm_settings, "max_loaded_embedding", 1)
        monkeypatch.setattr(h, "_rss_mb", lambda: None)
        h._MODEL_CACHE[("embedding", "old")] = {}
        h._MODEL_CACHE[("reranker", "rer")] = {}
        h._MODEL_CACHE[("embedding", "new")] = {}
        h._evict_lru("embedding")
        assert list(h._MODEL_CACHE) == [("reranker", "rer"), ("embedding", "new")]
        assert "evict embedding=old" in capsys.readouterr().out

    def test_no_eviction_within_limit(self, handler_state):
        h._MODEL_CACHE[("embedding", "a")] = {}
        h._MODEL_CACHE[("embedding", "b")] = {}
        h._evict_lru("embedding")
        assert len(h._MODEL_CACHE) == 2


# --------------------------------------------------------------------------- _load_one
class TestLoadOne:
    def test_offline_missing_dir_raises_file_not_found(self, models_dir):
        with pytest.raises(FileNotFoundError, match="Модель 'GHOST' не найдена"):
            h._load_one("embedding", "GHOST", str(models_dir), "cpu")

    def test_online_mode_does_not_require_dir(self, rm_settings, monkeypatch, models_dir, fake_ml):
        monkeypatch.setattr(rm_settings, "offline", False)
        entry = h._load_one("embedding", "GHOST", str(models_dir), "cpu")
        assert entry["path"] == os.path.join(str(models_dir), "GHOST")
        assert len(fake_ml.st_calls) == 1

    def test_embedding_entry_has_model_dim_path(self, models_dir, make_model, fake_ml):
        d = make_model("FRIDA")
        entry = h._load_one("embedding", "FRIDA", str(models_dir), "cpu")
        assert entry["dim"] == 8
        assert entry["path"] == str(d)
        assert entry["model"].path == str(d)

    def test_dim_falls_back_to_settings_when_model_fails(self, rm_settings, monkeypatch, models_dir, make_model, fake_ml):
        make_model("FRIDA")
        fake_ml.dim_error = True
        monkeypatch.setattr(rm_settings, "embedding_dim", 512)
        assert h._load_one("embedding", "FRIDA", str(models_dir), "cpu")["dim"] == 512

    def test_dim_falls_back_to_384_when_setting_is_zero(self, rm_settings, monkeypatch, models_dir, make_model, fake_ml):
        make_model("FRIDA")
        fake_ml.dim_error = True
        monkeypatch.setattr(rm_settings, "embedding_dim", 0)
        assert h._load_one("embedding", "FRIDA", str(models_dir), "cpu")["dim"] == 384

    def test_default_local_used_for_empty_folder(self, models_dir, make_model, fake_ml):
        d = make_model("paraphrase-multilingual-MiniLM-L12-v2")
        entry = h._load_one("embedding", "", str(models_dir), "cpu")
        assert entry["path"] == str(d)

    def test_cross_encoder_reranker(self, models_dir, make_model, fake_ml):
        d = make_model("ms-marco-MiniLM-L6-v2", config={"architectures": ["BertForSequenceClassification"]})
        entry = h._load_one("reranker", "ms-marco-MiniLM-L6-v2", str(models_dir), "cpu")
        assert entry["dim"] is None
        assert entry["path"] == str(d)
        call = fake_ml.ce_calls[0]
        assert call["automodel_args"] == {"local_files_only": True}
        assert call["tokenizer_args"] == {"local_files_only": True}
        assert entry["model"].predict([["a b", "a c"]]).tolist() == [1.0]

    def test_cross_encoder_old_signature_falls_back_to_kwargs(self, models_dir, make_model, fake_ml):
        make_model("ms-marco-MiniLM-L6-v2", config={"architectures": ["Bert"]})
        fake_ml.ce_reject_automodel_args = True
        h._load_one("reranker", "ms-marco-MiniLM-L6-v2", str(models_dir), "cpu")
        assert len(fake_ml.ce_calls) == 2
        retry = fake_ml.ce_calls[1]
        assert "automodel_args" not in retry
        assert retry["model_kwargs"] == {"local_files_only": True, "trust_remote_code": True}
        assert retry["tokenizer_kwargs"] == {"local_files_only": True, "trust_remote_code": True}

    def test_llm_reranker_routed_by_folder_name(self, models_dir, make_model, fake_ml):
        d = make_model(
            "bge-reranker-v2-minicpm-layerwise",
            config={"model_type": "minicpm", "architectures": ["LayerWiseMiniCPMForCausalLM"]},
        )
        entry = h._load_one("reranker", "bge-reranker-v2-minicpm-layerwise", str(models_dir), "cpu")
        from app.services.llm_reranker import LlmReranker

        assert isinstance(entry["model"], LlmReranker)
        assert entry["path"] == str(d)
        assert fake_ml.ce_calls == []  # CrossEncoder не использовался


# --------------------------------------------------------------------------- _get_model_entry
class TestGetModelEntry:
    async def test_disabled_raises_runtime_error(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "enabled", False)
        with pytest.raises(RuntimeError, match="выключены"):
            await h.get_embedding_model()

    async def test_no_model_configured_raises(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "embedding_model", None)
        monkeypatch.setattr(rm_settings, "embedding_model_default", "")
        with pytest.raises(RuntimeError, match="Не задана модель типа embedding"):
            await h.get_embedding_model()

    async def test_unknown_model_rejected_with_allowed_list(self, fake_ml):
        with pytest.raises(ValueError) as exc:
            await h.get_embedding_model("Stranger")
        msg = str(exc.value)
        assert "Stranger" in msg and "не разрешена" in msg and "frida" in msg

    async def test_loads_default_lazily_and_caches(self, make_model, fake_ml):
        d = make_model("FRIDA")
        first = await h.get_embedding_model()
        second = await h.get_embedding_model(None)
        assert first is second
        assert len(fake_ml.st_calls) == 1
        assert first["path"] == str(d)
        assert first["device"] == "cpu"
        assert first["dim"] == 8
        assert list(h._MODEL_CACHE) == [("embedding", "FRIDA")]

    async def test_name_with_local_prefix_and_case_insensitive_allowed(self, make_model, fake_ml):
        make_model("Giga-Embeddings-instruct")
        entry = await h.get_embedding_model("local/GIGA-EMBEDDINGS-INSTRUCT")
        assert entry["path"].lower().endswith("giga-embeddings-instruct")
        # ключ кэша - каноническое имя из конфига, а не регистр запроса (BUG-RM-08)
        assert ("embedding", "Giga-Embeddings-instruct") in h._MODEL_CACHE

    async def test_cache_hit_moves_entry_to_end(self, make_model, fake_ml):
        make_model("FRIDA")
        make_model("Giga-Embeddings-instruct")
        await h.get_embedding_model("FRIDA")
        await h.get_embedding_model("Giga-Embeddings-instruct")
        await h.get_embedding_model("FRIDA")  # FRIDA снова самая свежая
        assert list(h._MODEL_CACHE) == [
            ("embedding", "Giga-Embeddings-instruct"),
            ("embedding", "FRIDA"),
        ]

    async def test_concurrent_requests_load_model_once(self, make_model, fake_ml):
        make_model("FRIDA")
        results = await asyncio.gather(*(h.get_embedding_model("FRIDA") for _ in range(5)))
        assert len(fake_ml.st_calls) == 1
        assert all(r is results[0] for r in results)

    async def test_lru_evicts_oldest_over_limit(self, rm_settings, monkeypatch, make_model, fake_ml):
        monkeypatch.setattr(rm_settings, "max_loaded_embedding", 1)
        make_model("FRIDA")
        make_model("Giga-Embeddings-instruct")
        await h.get_embedding_model("FRIDA")
        await h.get_embedding_model("Giga-Embeddings-instruct")
        assert list(h._MODEL_CACHE) == [("embedding", "Giga-Embeddings-instruct")]
        # вытесненную модель придётся грузить заново
        await h.get_embedding_model("FRIDA")
        assert len(fake_ml.st_calls) == 3

    async def test_limits_are_per_kind(self, rm_settings, monkeypatch, make_model, fake_ml):
        monkeypatch.setattr(rm_settings, "max_loaded_embedding", 1)
        monkeypatch.setattr(rm_settings, "max_loaded_reranker", 1)
        make_model("FRIDA")
        make_model("ms-marco-MiniLM-L6-v2", config={"architectures": ["Bert"]})
        await h.get_embedding_model()
        await h.get_reranker_model()
        assert set(h._MODEL_CACHE) == {("embedding", "FRIDA"), ("reranker", "ms-marco-MiniLM-L6-v2")}

    async def test_reranker_entry_has_no_dim(self, make_model, fake_ml):
        make_model("ms-marco-MiniLM-L6-v2", config={"architectures": ["Bert"]})
        entry = await h.get_reranker_model("ms-marco-MiniLM-L6-v2")
        assert entry["dim"] is None and entry["device"] == "cpu"

    async def test_default_model_updates_settings_dim(self, rm_settings, make_model, fake_ml):
        make_model("FRIDA")
        fake_ml.dim = 1536
        await h.get_embedding_model()
        assert rm_settings.embedding_dim == 1536

    async def test_non_default_model_does_not_touch_settings_dim(self, rm_settings, make_model, fake_ml):
        make_model("Giga-Embeddings-instruct")
        fake_ml.dim = 2048
        await h.get_embedding_model("Giga-Embeddings-instruct")
        assert rm_settings.embedding_dim == 384

    async def test_zero_dim_does_not_overwrite_settings(self, rm_settings, make_model, fake_ml):
        make_model("FRIDA")
        fake_ml.dim = 0
        await h.get_embedding_model()
        assert rm_settings.embedding_dim == 384

    async def test_load_error_recorded_and_reraised(self, make_model, fake_ml, caplog):
        make_model("FRIDA")
        fake_ml.st_error = RuntimeError("веса повреждены")
        with caplog.at_level(logging.ERROR, logger=h.logger.name):
            with pytest.raises(RuntimeError, match="веса повреждены"):
                await h.get_embedding_model()
        assert h.get_last_rag_models_error() == "embedding/FRIDA: веса повреждены"
        assert h._MODEL_CACHE == OrderedDict()
        assert "Не удалось загрузить embedding 'FRIDA'" in caplog.text

    async def test_retry_after_failure_succeeds_and_clears_error(self, make_model, fake_ml):
        make_model("FRIDA")
        fake_ml.st_error = RuntimeError("временно")
        with pytest.raises(RuntimeError):
            await h.get_embedding_model()
        fake_ml.st_error = None
        entry = await h.get_embedding_model()
        assert entry["dim"] == 8
        assert h.get_last_rag_models_error() is None

    async def test_missing_weights_dir_reported(self, fake_ml):
        with pytest.raises(FileNotFoundError):
            await h.get_embedding_model("FRIDA")
        assert "embedding/FRIDA" in h.get_last_rag_models_error()

    async def test_same_model_in_different_case_is_cached_once(self, make_model, fake_ml):
        make_model("FRIDA")
        a = await h.get_embedding_model("FRIDA")
        b = await h.get_embedding_model("frida")
        assert a is b
        assert len(fake_ml.st_calls) == 1


# --------------------------------------------------------------------------- compat API
class TestHandlerFacade:
    async def test_disabled_returns_none(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "enabled", False)
        assert await h.get_rag_models_handler() is None

    async def test_returns_default_embedder_and_reranker(self, make_model, fake_ml):
        make_model("FRIDA")
        make_model("ms-marco-MiniLM-L6-v2", config={"architectures": ["Bert"]})
        handler = await h.get_rag_models_handler()
        assert set(handler) == {"embedding_model", "reranker_model", "device", "embedding_dim"}
        assert handler["device"] == "cpu"
        assert handler["embedding_dim"] == 8
        assert handler["embedding_model"] is h._MODEL_CACHE[("embedding", "FRIDA")]["model"]
        assert handler["reranker_model"] is h._MODEL_CACHE[("reranker", "ms-marco-MiniLM-L6-v2")]["model"]

    async def test_reranker_failure_returns_none_and_keeps_error(self, make_model, fake_ml):
        make_model("FRIDA")  # реранкера на диске нет
        assert await h.get_rag_models_handler() is None
        assert "ms-marco-MiniLM-L6-v2" in h.get_last_rag_models_error()

    async def test_device_falls_back_to_resolver(self, make_model, fake_ml, monkeypatch):
        make_model("FRIDA")
        make_model("ms-marco-MiniLM-L6-v2", config={"architectures": ["Bert"]})
        await h.get_embedding_model()
        h._MODEL_CACHE[("embedding", "FRIDA")]["device"] = ""
        monkeypatch.setattr(h, "_resolve_device", lambda: "cpu-fallback")
        assert (await h.get_rag_models_handler())["device"] == "cpu-fallback"

    def test_last_error_reflects_module_state(self, handler_state, monkeypatch):
        assert h.get_last_rag_models_error() is None
        monkeypatch.setattr(h, "_last_rag_models_error", "boom")
        assert h.get_last_rag_models_error() == "boom"


class TestInfoAndCleanup:
    def test_loaded_models_info(self, handler_state, monkeypatch, rm_settings):
        monkeypatch.setattr(h, "_rss_mb", lambda: 123)
        monkeypatch.setattr(rm_settings, "max_loaded_reranker", 3)
        h._MODEL_CACHE[("embedding", "FRIDA")] = {"dim": 8}
        h._MODEL_CACHE[("reranker", "ms")] = {"dim": None}
        info = h.loaded_models_info()
        assert info == {
            "loaded": [
                {"kind": "embedding", "name": "FRIDA", "dim": 8},
                {"kind": "reranker", "name": "ms", "dim": None},
            ],
            "rss_mb": 123,
            "max_loaded_embedding": 2,
            "max_loaded_reranker": 3,
        }

    async def test_cleanup_clears_cache_and_frees_memory(self, make_model, fake_ml, capsys, caplog):
        make_model("FRIDA")
        fake_ml.cuda_available = True
        await h.get_embedding_model()
        with caplog.at_level(logging.INFO, logger=h.logger.name):
            await h.cleanup_rag_models_handler()
        assert len(h._MODEL_CACHE) == 0
        assert fake_ml.empty_cache_calls >= 1
        assert "Выгружаю RAG-модели: ['embedding:FRIDA']" in caplog.text
        assert "cleanup all=-" in capsys.readouterr().out

    async def test_cleanup_on_empty_cache_is_quiet_noop(self, handler_state, caplog):
        with caplog.at_level(logging.INFO, logger=h.logger.name):
            await h.cleanup_rag_models_handler()
        assert "Выгружаю" not in caplog.text
        assert len(h._MODEL_CACHE) == 0
