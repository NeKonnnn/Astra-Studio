"""app/services/llm_reranker.py: эвристика типа реранкера, правка auto_map, LlmReranker.predict.

Весов нет: transformers/torch - фейки (tests/fakes.py), «модель» - каталог с config.json.
"""

import json
import logging
import os
import sys
import tempfile

import numpy as np
import pytest

from app.services import llm_reranker as llm
from app.services.llm_reranker import LlmReranker

pytestmark = pytest.mark.unit

MINICPM_CFG = {
    "model_type": "minicpm",
    "architectures": ["LayerWiseMiniCPMForCausalLM"],
    "auto_map": {
        "AutoConfig": "BAAI/bge-reranker-v2-minicpm-layerwise--configuration_minicpm_reranker.LayerWiseMiniCPMConfig",
        "AutoModelForCausalLM": "BAAI/bge-reranker-v2-minicpm-layerwise--modeling_minicpm_reranker.LayerWiseMiniCPMForCausalLM",
    },
    "_name_or_path": "BAAI/bge-reranker-v2-minicpm-layerwise",
}
LOCAL_AUTOMAP_CFG = {
    "model_type": "minicpm",
    "auto_map": {"AutoModelForCausalLM": "modeling_x.Model"},
}


@pytest.fixture(autouse=True)
def _llm_env(monkeypatch, tmp_path):
    """Временные каталоги и offline-ENV - только в tmp_path / с откатом."""
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    monkeypatch.setattr(llm, "_TMP_MODEL_DIRS", [])
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        monkeypatch.setenv(name, "0")
    return tmp


@pytest.fixture
def minicpm_dir(make_model):
    d = make_model(
        "bge-reranker-v2-minicpm-layerwise",
        config=MINICPM_CFG,
        files=["configuration_minicpm_reranker.py", "modeling_minicpm_reranker.py", "model.safetensors"],
    )
    return d


class TestIsLlmRerankerPath:
    @pytest.mark.parametrize(
        "path",
        [
            "/m/bge-reranker-v2-minicpm-layerwise",
            "C:\\models\\BGE-RERANKER-V2-MINICPM_LAYERWISE",
            "/m/reranker-v2-minicpm",
            "/m/bge-reranker-v2-gemma",
            "/x/reranker-v2-gemma-lora/",
        ],
    )
    def test_name_hints(self, path):
        assert llm.is_llm_reranker_path(path) is True

    def test_empty_path_is_false(self):
        assert llm.is_llm_reranker_path("") is False

    def test_dir_without_config_is_false(self, make_model):
        assert llm.is_llm_reranker_path(str(make_model("plain-ce"))) is False

    def test_broken_config_is_false(self, make_model):
        d = make_model("plain-ce")
        (d / "config.json").write_text("{oops", encoding="utf-8")
        assert llm.is_llm_reranker_path(str(d)) is False

    @pytest.mark.parametrize("arch", ["LayerWiseMiniCPMForCausalLM", "MiniCPMForCausalLM"])
    def test_architecture_match(self, make_model, arch):
        d = make_model("anything", config={"architectures": [arch]})
        assert llm.is_llm_reranker_path(str(d)) is True

    def test_non_string_architectures_ignored(self, make_model):
        d = make_model("anything", config={"architectures": [1, None]})
        assert llm.is_llm_reranker_path(str(d)) is False

    def test_minicpm_model_type_needs_auto_map(self, make_model):
        with_map = make_model("a1", config={"model_type": "MiniCPM", "auto_map": {"x": "y.Z"}})
        without = make_model("a2", config={"model_type": "minicpm"})
        assert llm.is_llm_reranker_path(str(with_map)) is True
        assert llm.is_llm_reranker_path(str(without)) is False

    def test_bert_cross_encoder_is_false(self, make_model):
        d = make_model("ms-marco-x", config={"architectures": ["BertForSequenceClassification"], "model_type": "bert"})
        assert llm.is_llm_reranker_path(str(d)) is False


class TestAutomapHelpers:
    def test_force_offline_env(self):
        llm._force_offline_env()
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        assert os.environ["HF_DATASETS_OFFLINE"] == "1"

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("org/repo--modeling_x.Class", "modeling_x.Class"),
            ("modeling_x.Class", "modeling_x.Class"),
            ("a--b--c", "b--c"),
            (None, None),
            (5, 5),
        ],
    )
    def test_local_automap_ref(self, value, expected):
        assert llm._local_automap_ref(value) == expected

    def test_fix_automap_dict_reports_change(self):
        out, changed = llm._fix_automap_dict({"A": "org--m.C", "B": "m.D", "C": ["list"]})
        assert out == {"A": "m.C", "B": "m.D", "C": ["list"]}
        assert changed is True

    def test_fix_automap_dict_unchanged(self):
        out, changed = llm._fix_automap_dict({"A": "m.C"})
        assert (out, changed) == ({"A": "m.C"}, False)

    @pytest.mark.parametrize("bad", [None, "str", ["x"], 3])
    def test_fix_automap_dict_non_dict(self, bad):
        assert llm._fix_automap_dict(bad) == ({}, False)


class TestPrepareOfflineModelDir:
    def test_missing_config_json(self, make_model):
        d = make_model("nocfg")
        with pytest.raises(FileNotFoundError, match="Нет config.json"):
            llm._prepare_offline_model_dir(str(d))

    def test_missing_remote_code_file_is_reported(self, make_model):
        d = make_model("m", config=LOCAL_AUTOMAP_CFG)  # modeling_x.py нет
        with pytest.raises(FileNotFoundError, match="modeling_x.py"):
            llm._prepare_offline_model_dir(str(d))

    def test_clean_config_returns_same_path(self, make_model):
        d = make_model("m", config=LOCAL_AUTOMAP_CFG, files=["modeling_x.py"])
        assert llm._prepare_offline_model_dir(str(d)) == str(d)
        assert llm._TMP_MODEL_DIRS == []

    def test_non_dotted_refs_do_not_require_files(self, make_model):
        d = make_model("m", config={"auto_map": {"A": "nodots", "B": 5}})
        assert llm._prepare_offline_model_dir(str(d)) == str(d)

    def test_org_prefixed_automap_gets_fixed_copy(self, minicpm_dir, _llm_env):
        got = llm._prepare_offline_model_dir(str(minicpm_dir))
        assert got != str(minicpm_dir)
        assert os.path.dirname(got) == str(_llm_env)
        assert os.path.basename(got).startswith("rag-llm-reranker-")
        assert llm._TMP_MODEL_DIRS == [got]
        fixed = json.loads(open(os.path.join(got, "config.json"), encoding="utf-8").read())
        assert fixed["auto_map"]["AutoModelForCausalLM"] == "modeling_minicpm_reranker.LayerWiseMiniCPMForCausalLM"
        assert fixed["auto_map"]["AutoConfig"] == "configuration_minicpm_reranker.LayerWiseMiniCPMConfig"
        assert fixed["_name_or_path"] == str(minicpm_dir)
        # веса и код доступны из временного каталога
        for name in ("modeling_minicpm_reranker.py", "model.safetensors"):
            assert os.path.isfile(os.path.join(got, name))
        # исходный config не тронут
        orig = json.loads((minicpm_dir / "config.json").read_text(encoding="utf-8"))
        assert orig["auto_map"]["AutoConfig"].startswith("BAAI/")

    def test_only_name_or_path_changed(self, make_model):
        d = make_model("m", config={"_name_or_path": "org/model"}, files=["w.bin"])
        got = llm._prepare_offline_model_dir(str(d))
        fixed = json.loads(open(os.path.join(got, "config.json"), encoding="utf-8").read())
        assert fixed["_name_or_path"] == str(d)
        assert "auto_map" not in fixed

    def test_http_name_or_path_counts_as_changed(self, make_model):
        d = make_model("m", config={"_name_or_path": "hub-mirror"}.copy())
        # без "/" и без префикса http - не меняется
        assert llm._prepare_offline_model_dir(str(d)) == str(d)
        d2 = make_model("m2", config={"_name_or_path": "http:host"})
        assert llm._prepare_offline_model_dir(str(d2)) != str(d2)

    def test_only_automap_changed_keeps_name_or_path(self, make_model):
        d = make_model("m", config={"auto_map": {"A": "org--m.Cls"}}, files=["m.py"])
        got = llm._prepare_offline_model_dir(str(d))
        fixed = json.loads(open(os.path.join(got, "config.json"), encoding="utf-8").read())
        assert fixed["auto_map"] == {"A": "m.Cls"}
        assert "_name_or_path" not in fixed

    def test_symlinks_used_when_available(self, make_model, monkeypatch):
        d = make_model("m", config={"_name_or_path": "o/m"}, files=["w.bin"])
        links = []
        monkeypatch.setattr(os, "symlink", lambda src, dst: links.append((src, dst)))
        llm._prepare_offline_model_dir(str(d))
        assert [os.path.basename(s) for s, _ in links] == ["w.bin"]
        assert all(os.path.dirname(dst) == llm._TMP_MODEL_DIRS[0] for _, dst in links)

    def test_copy_fallback_for_files_and_dirs_when_symlink_denied(self, make_model, monkeypatch):
        d = make_model("m", config={"_name_or_path": "o/m"}, files=["w.bin"])
        (d / "sub").mkdir()
        (d / "sub" / "inner.txt").write_text("deep", encoding="utf-8")

        def denied(src, dst):
            raise OSError("symlink запрещён")

        monkeypatch.setattr(os, "symlink", denied)
        got = llm._prepare_offline_model_dir(str(d))
        assert open(os.path.join(got, "w.bin")).read() == "x"
        assert open(os.path.join(got, "sub", "inner.txt")).read() == "deep"


class TestRegisterLocalRemoteCode:
    def test_without_transformers_nothing_happens(self, monkeypatch, make_model):
        monkeypatch.setitem(sys.modules, "transformers", None)
        d = make_model("m", config=LOCAL_AUTOMAP_CFG)
        llm._register_local_remote_code(str(d))  # не падает

    def test_missing_or_broken_config_is_ignored(self, fake_ml, make_model):
        llm._register_local_remote_code("/no/such/dir")
        d = make_model("broken")
        (d / "config.json").write_text("{", encoding="utf-8")
        llm._register_local_remote_code(str(d))
        assert fake_ml.dyn_calls == []

    def test_registers_config_and_causal_model(self, fake_ml, make_model):
        cfg = {
            "model_type": "minicpm",
            "auto_map": {
                "AutoConfig": "org--configuration_m.Cfg",
                "AutoModelForCausalLM": "org--modeling_m.Model",
                "AutoModel": "modeling_m.Base",
                "AutoTokenizer": "tok.Tok",
                "Weird": 5,
            },
        }
        d = make_model("m", config=cfg)
        llm._register_local_remote_code(str(d))
        assert fake_ml.dyn_calls == ["configuration_m.Cfg", "modeling_m.Model", "modeling_m.Base"]
        (model_type, cfg_cls, kw), = fake_ml.autoconfig_register_calls
        assert model_type == "minicpm" and cfg_cls.__name__ == "Cfg" and kw == {"exist_ok": True}
        (conf_class, model_cls, kw2), = fake_ml.causal_register_calls
        assert model_cls.__name__ == "Model" and conf_class is model_cls.config_class
        assert kw2 == {"exist_ok": True}

    def test_default_model_type_is_minicpm(self, fake_ml, make_model):
        d = make_model("m", config={"auto_map": {"AutoConfig": "c.Cfg"}})
        llm._register_local_remote_code(str(d))
        assert fake_ml.autoconfig_register_calls[0][0] == "minicpm"

    def test_register_errors_are_swallowed(self, fake_ml, make_model, caplog):
        fake_ml.config_register_error = ValueError("dup")
        fake_ml.causal_register_error = ValueError("dup")
        d = make_model("m", config={"auto_map": {"AutoConfig": "c.Cfg", "AutoModelForCausalLM": "m.Mod"}})
        with caplog.at_level(logging.INFO, logger=llm.logger.name):
            llm._register_local_remote_code(str(d))
        assert "загружен remote-code c.Cfg" in caplog.text
        assert "загружен remote-code m.Mod" in caplog.text

    def test_dynamic_module_failure_warns_and_continues(self, fake_ml, make_model, caplog):
        fake_ml.dyn_error = RuntimeError("нет файла")
        d = make_model("m", config={"auto_map": {"AutoConfig": "c.Cfg", "AutoModelForCausalLM": "m.Mod"}})
        with caplog.at_level(logging.WARNING, logger=llm.logger.name):
            llm._register_local_remote_code(str(d))
        assert caplog.text.count("не удалось предзагрузить") == 2
        assert fake_ml.autoconfig_register_calls == []


class TestBuildInputs:
    def test_structure_and_padding(self, fake_ml):
        tok = fake_ml.FakeTokenizer()
        out = llm._build_inputs([["q", "p1"], ["qq", "a longer passage"]], tok, max_length=64)
        ids, mask = out["input_ids"].a, out["attention_mask"].a
        assert ids.shape == mask.shape and ids.shape[0] == 2
        assert ids.shape[1] % 8 == 0  # pad_to_multiple_of=8
        # bos в начале каждой пары, маска единиц до конца содержимого
        assert ids[0, 0] == tok.bos_token_id and ids[1, 0] == tok.bos_token_id
        base = 1 + len("A: q") + 1 + len("B: p1") + 1 + len(llm._DEFAULT_PROMPT)
        assert int(mask[0].sum()) == base
        assert int(mask[1].sum()) > int(mask[0].sum())

    def test_without_bos_token(self, fake_ml):
        tok = fake_ml.FakeTokenizer()
        tok.bos_token_id = None
        out = llm._build_inputs([["q", "p"]], tok, max_length=64)
        assert int(out["attention_mask"].a.sum()) == len("A: q") + 1 + len("B: p") + 1 + len(llm._DEFAULT_PROMPT)

    def test_truncates_long_passage_to_max_length(self, fake_ml):
        tok = fake_ml.FakeTokenizer()
        out = llm._build_inputs([["q", "x" * 500]], tok, max_length=40)
        # prepare_for_model режет до max_length, затем дописывается sep + prompt
        assert int(out["attention_mask"].a.sum()) == 40 + 1 + len(llm._DEFAULT_PROMPT)


class TestLlmRerankerInit:
    def test_layerwise_model_loaded_offline_on_cpu(self, fake_ml, minicpm_dir):
        r = LlmReranker(str(minicpm_dir), device="cpu")
        assert r._layerwise is True
        assert r.cutoff_layers == [28]
        assert r.batch_size == 2 and r.max_length == 1024
        assert r._tmp_dir is not None and r._tmp_dir != str(minicpm_dir)
        tok_call = fake_ml.tokenizer_calls[0]
        assert tok_call["path"] == r._tmp_dir
        assert tok_call["local_files_only"] is True and tok_call["trust_remote_code"] is True
        model_call = fake_ml.causal_load_calls[0]
        assert model_call["torch_dtype"] == "float32"
        assert model_call["low_cpu_mem_usage"] is True
        assert r.model.device == "cpu" and r.model.eval_called is True
        assert r._yes_loc == 2 + (ord("Y") % 50)
        assert os.environ["HF_HUB_OFFLINE"] == "1"

    def test_gemma_keeps_original_path(self, fake_ml, make_model):
        d = make_model("bge-reranker-v2-gemma", config={"architectures": ["GemmaForCausalLM"]})
        r = LlmReranker(str(d), device="cpu", cutoff_layers=[5, 6], batch_size=0)
        assert r._layerwise is False
        assert r._tmp_dir is None
        assert r.cutoff_layers == [5, 6]
        assert r.batch_size == 1  # max(1, ...)
        assert fake_ml.tokenizer_calls[0]["path"] == str(d)

    @pytest.mark.parametrize("bf16,dtype", [(True, "bfloat16"), (False, "float16")])
    def test_cuda_dtype(self, fake_ml, make_model, bf16, dtype):
        fake_ml.bf16 = bf16
        d = make_model("bge-reranker-v2-gemma", config={})
        r = LlmReranker(str(d), device="cuda:0")
        assert fake_ml.causal_load_calls[0]["torch_dtype"] == dtype
        assert r.model.device == "cuda:0"


class TestLayerwiseDetection:
    def test_gemma_under_minicpm_parent_is_not_layerwise(self, fake_ml, models_dir):
        d = models_dir / "minicpm-store" / "bge-reranker-v2-gemma"
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        assert LlmReranker(str(d), device="cpu")._layerwise is False


class TestPredict:
    @pytest.fixture
    def layerwise(self, fake_ml, minicpm_dir):
        return LlmReranker(str(minicpm_dir), device="cpu", batch_size=2, cutoff_layers=[7])

    @pytest.fixture
    def plain(self, fake_ml, make_model):
        d = make_model("bge-reranker-v2-gemma", config={})
        return LlmReranker(str(d), device="cpu", batch_size=2)

    @staticmethod
    def _expected(q, p):
        return 1 + len(f"A: {q}") + 1 + len(f"B: {p}") + 1 + len(llm._DEFAULT_PROMPT)

    def test_empty_pairs_gives_empty_float32(self, plain):
        out = plain.predict([])
        assert isinstance(out, np.ndarray) and out.shape == (0,) and out.dtype == np.float32

    def test_layerwise_scores_use_last_layer_last_column(self, layerwise, fake_ml):
        pairs = [["q", "a"], ["q", "longer passage"], ["q", "xy"]]
        out = layerwise.predict(pairs)
        assert out.dtype == np.float32
        assert out.tolist() == [float(self._expected("q", p)) for _, p in pairs]
        # 3 пары при batch_size=2 -> два прогона модели с нужным cutoff
        assert [c["cutoff_layers"] for c in fake_ml.model_calls] == [[7], [7]]

    def test_gemma_uses_yes_logit(self, plain, fake_ml):
        out = plain.predict([["q", "abc"], ["q", "abcdef"]])
        assert out.tolist() == [float(self._expected("q", "abc")), float(self._expected("q", "abcdef"))]
        assert [c["cutoff_layers"] for c in fake_ml.model_calls] == [None]
        assert out[1] > out[0]

    def test_single_flat_pair_is_wrapped(self, plain):
        out = plain.predict(["q", "passage"])
        assert out.shape == (1,)
        assert out[0] == self._expected("q", "passage")

    def test_batches_cover_all_pairs_in_order(self, plain, fake_ml):
        pairs = [["q", "p" * n] for n in range(1, 6)]
        out = plain.predict(pairs)
        assert len(fake_ml.model_calls) == 3  # 2 + 2 + 1
        assert out.tolist() == sorted(out.tolist())  # длиннее пассаж - больше токенов


class TestLoadLlmReranker:
    @pytest.fixture
    def captured(self, monkeypatch):
        seen = {}

        class Fake:
            def __init__(self, path, **kwargs):
                seen["path"] = path
                seen.update(kwargs)

        monkeypatch.setattr(llm, "LlmReranker", Fake)
        return seen

    def test_defaults(self, captured):
        llm.load_llm_reranker("/m/x", "cpu")
        assert captured == {
            "path": "/m/x",
            "device": "cpu",
            "cutoff_layers": [28],
            "max_length": 1024,
            "batch_size": 2,
        }

    def test_env_overrides_and_garbage_cutoff_parts_skipped(self, captured, monkeypatch):
        monkeypatch.setenv("RAG_RERANKER_CUTOFF_LAYERS", "12, x ,30,")
        monkeypatch.setenv("RAG_RERANKER_BATCH_SIZE", "4")
        monkeypatch.setenv("RAG_RERANKER_MAX_LENGTH", "256")
        llm.load_llm_reranker("/m/x", "cuda")
        assert captured["cutoff_layers"] == [12, 30]
        assert captured["batch_size"] == 4 and captured["max_length"] == 256
        assert captured["device"] == "cuda"

    def test_unusable_cutoff_falls_back_to_default(self, captured, monkeypatch):
        monkeypatch.setenv("RAG_RERANKER_CUTOFF_LAYERS", "a,b,-3")
        llm.load_llm_reranker("/m/x", "cpu")
        assert captured["cutoff_layers"] == [28]
