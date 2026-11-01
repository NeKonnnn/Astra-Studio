"""LLM-реранкеры (MiniCPM layerwise / Gemma), несовместимые с CrossEncoder.

bge-reranker-v2-minicpm-layerwise — LayerWiseMiniCPMForCausalLM (trust_remote_code),
а не AutoModelForSequenceClassification.

Интерфейс predict(pairs) совместим с app/api/endpoints/rerank.py.
Только локальные веса: без запросов к huggingface.co.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from typing import Any, List, Optional, Sequence, Union

import numpy as np

logger = logging.getLogger(__name__)

_LLM_RERANKER_NAME_HINTS = (
    "minicpm-layerwise",
    "minicpm_layerwise",
    "reranker-v2-minicpm",
    "bge-reranker-v2-gemma",
    "reranker-v2-gemma",
)

_DEFAULT_PROMPT = (
    "Given a query A and a passage B, determine whether the passage contains an "
    "answer to the query by providing a prediction of either 'Yes' or 'No'."
)

# Временные каталоги с починенным auto_map (чтобы не писать в :ro PVC)
_TMP_MODEL_DIRS: List[str] = []


def is_llm_reranker_path(model_path: str) -> bool:
    """Эвристика: LLM-реранкер по config.json или имени папки."""
    name = (model_path or "").replace("\\", "/").lower()
    folder = name.rsplit("/", 1)[-1]
    if any(h in folder or h in name for h in _LLM_RERANKER_NAME_HINTS):
        return True
    cfg_path = os.path.join(model_path, "config.json") if model_path else ""
    if not cfg_path or not os.path.isfile(cfg_path):
        return False
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    arches = cfg.get("architectures") or []
    if any(
        isinstance(a, str) and ("LayerWiseMiniCPM" in a or "MiniCPMForCausalLM" in a)
        for a in arches
    ):
        return True
    model_type = str(cfg.get("model_type") or "").lower()
    if model_type == "minicpm" and cfg.get("auto_map"):
        return True
    return False


def _force_offline_env() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"


def _local_automap_ref(value: str) -> str:
    """'BAAI/model--modeling_x.Class' → 'modeling_x.Class'."""
    if not isinstance(value, str):
        return value
    if "--" in value:
        return value.split("--", 1)[-1]
    return value


def _fix_automap_dict(auto_map: Any) -> tuple[dict, bool]:
    if not isinstance(auto_map, dict):
        return {}, False
    changed = False
    out = {}
    for key, val in auto_map.items():
        if isinstance(val, str):
            fixed = _local_automap_ref(val)
            out[key] = fixed
            if fixed != val:
                changed = True
        else:
            out[key] = val
    return out, changed


def _prepare_offline_model_dir(model_path: str) -> str:
    """Путь к модели с локальным auto_map (без org/repo--...).

    Оригинальный config MiniCPM часто содержит:
      AutoModelForCausalLM: BAAI/bge-reranker-v2-minicpm-layerwise--modeling_....Class
    В офлайне transformers пытается сходить на huggingface.co.
    Пишем исправленный config во временный каталог, веса — через symlink.
    """
    cfg_path = os.path.join(model_path, "config.json")
    if not os.path.isfile(cfg_path):
        raise FileNotFoundError(f"Нет config.json в {model_path}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    auto_map, map_changed = _fix_automap_dict(cfg.get("auto_map"))
    name_or_path = str(cfg.get("_name_or_path") or "")
    path_changed = bool(name_or_path) and (
        "/" in name_or_path or name_or_path.startswith("http")
    )

    # Проверим обязательные local py для trust_remote_code
    needed_py = set()
    for ref in auto_map.values():
        if isinstance(ref, str) and "." in ref:
            needed_py.add(ref.split(".", 1)[0] + ".py")
    for py_name in needed_py:
        if not os.path.isfile(os.path.join(model_path, py_name)):
            raise FileNotFoundError(
                f"В {model_path} нет {py_name} (нужен для trust_remote_code офлайн). "
                f"Скопируйте modeling/configuration *.py вместе с весами."
            )

    if not map_changed and not path_changed:
        return model_path

    tmp = tempfile.mkdtemp(prefix="rag-llm-reranker-")
    _TMP_MODEL_DIRS.append(tmp)
    for name in os.listdir(model_path):
        if name == "config.json":
            continue
        src = os.path.join(model_path, name)
        dst = os.path.join(tmp, name)
        try:
            os.symlink(src, dst)
        except OSError:
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

    fixed = dict(cfg)
    if map_changed:
        fixed["auto_map"] = auto_map
    if path_changed:
        fixed["_name_or_path"] = model_path
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
        json.dump(fixed, f, ensure_ascii=False, indent=2)
    logger.info(
        "LLM-реранкер: локальный auto_map → %s (исходник %s)",
        tmp,
        model_path,
    )
    return tmp


def _register_local_remote_code(model_path: str) -> None:
    """Предзагрузка configuration_/modeling_ из папки модели."""
    try:
        from transformers import AutoConfig, AutoModelForCausalLM
        from transformers.dynamic_module_utils import get_class_from_dynamic_module
    except ImportError:
        return

    cfg_path = os.path.join(model_path, "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return

    auto_map, _ = _fix_automap_dict(cfg.get("auto_map"))
    for key in ("AutoConfig", "AutoModelForCausalLM", "AutoModel"):
        ref = auto_map.get(key)
        if not isinstance(ref, str) or "." not in ref:
            continue
        try:
            cls = get_class_from_dynamic_module(ref, model_path, trust_remote_code=True)
            if key == "AutoConfig":
                try:
                    AutoConfig.register(
                        cfg.get("model_type") or "minicpm", cls, exist_ok=True
                    )
                except Exception:
                    pass
            elif key == "AutoModelForCausalLM":
                try:
                    AutoModelForCausalLM.register(cls.config_class, cls, exist_ok=True)
                except Exception:
                    pass
            logger.info("LLM-реранкер: загружен remote-code %s", ref)
        except Exception as e:
            logger.warning("LLM-реранкер: не удалось предзагрузить %s: %s", ref, e)


def _build_inputs(pairs: Sequence[Sequence[str]], tokenizer, max_length: int = 1024):
    prompt = _DEFAULT_PROMPT
    sep = "\n"
    prompt_inputs = tokenizer(prompt, return_tensors=None, add_special_tokens=False)[
        "input_ids"
    ]
    sep_inputs = tokenizer(sep, return_tensors=None, add_special_tokens=False)[
        "input_ids"
    ]
    inputs = []
    bos = tokenizer.bos_token_id
    for query, passage in pairs:
        query_inputs = tokenizer(
            f"A: {query}",
            return_tensors=None,
            add_special_tokens=False,
            max_length=max_length * 3 // 4,
            truncation=True,
        )
        passage_inputs = tokenizer(
            f"B: {passage}",
            return_tensors=None,
            add_special_tokens=False,
            max_length=max_length,
            truncation=True,
        )
        q_ids = query_inputs["input_ids"]
        if bos is not None:
            q_ids = [bos] + q_ids
        item = tokenizer.prepare_for_model(
            q_ids,
            sep_inputs + passage_inputs["input_ids"],
            truncation="only_second",
            max_length=max_length,
            padding=False,
            return_attention_mask=False,
            return_token_type_ids=False,
            add_special_tokens=False,
        )
        item["input_ids"] = item["input_ids"] + sep_inputs + prompt_inputs
        item["attention_mask"] = [1] * len(item["input_ids"])
        inputs.append(item)
    return tokenizer.pad(
        inputs,
        padding=True,
        max_length=max_length + len(sep_inputs) + len(prompt_inputs),
        pad_to_multiple_of=8,
        return_tensors="pt",
    )


class LlmReranker:
    """Обёртка с predict(pairs), как у CrossEncoder."""

    def __init__(
        self,
        model_path: str,
        device: str = "cpu",
        cutoff_layers: Optional[List[int]] = None,
        max_length: int = 1024,
        batch_size: int = 2,
    ):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        _force_offline_env()

        self.device = device
        self.max_length = max_length
        self.batch_size = max(1, batch_size)
        self.cutoff_layers = cutoff_layers or [28]
        # Режим определяем по имени папки модели, а не по всему пути (родительские каталоги не в счёт)
        folder_name = model_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
        self._layerwise = is_llm_reranker_path(model_path) and (
            "minicpm" in folder_name or "layerwise" in folder_name
        )
        self._tmp_dir: Optional[str] = None

        load_path = _prepare_offline_model_dir(model_path)
        if load_path != model_path:
            self._tmp_dir = load_path

        _register_local_remote_code(load_path)

        logger.info("Гружу LLM-реранкер: %s (device=%s, offline)", load_path, device)
        self.tokenizer = AutoTokenizer.from_pretrained(
            load_path,
            trust_remote_code=True,
            local_files_only=True,
        )
        dtype = torch.float32
        if device.startswith("cuda"):
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        self.model = AutoModelForCausalLM.from_pretrained(
            load_path,
            trust_remote_code=True,
            local_files_only=True,
            torch_dtype=dtype,
            low_cpu_mem_usage=True,
        )
        self.model.to(device)
        self.model.eval()
        self._yes_loc = self.tokenizer("Yes", add_special_tokens=False)["input_ids"][0]
        self._torch = torch
        logger.info(
            "LLM-реранкер загружен (layerwise=%s, cutoff=%s)",
            self._layerwise,
            self.cutoff_layers,
        )

    def predict(self, pairs: Union[List[List[str]], List[str]]) -> np.ndarray:
        if not pairs:
            return np.asarray([], dtype=np.float32)
        # один pair [q, p] vs список pairs
        if isinstance(pairs[0], str):
            pair_list: List[List[str]] = [list(pairs)]  # type: ignore[arg-type]
        else:
            pair_list = [list(p) for p in pairs]  # type: ignore[arg-type]

        torch = self._torch
        scores: List[float] = []
        with torch.no_grad():
            for i in range(0, len(pair_list), self.batch_size):
                batch = pair_list[i : i + self.batch_size]
                inputs = _build_inputs(batch, self.tokenizer, self.max_length)
                inputs = {k: v.to(self.device) for k, v in inputs.items()}
                if self._layerwise:
                    out = self.model(
                        **inputs,
                        return_dict=True,
                        cutoff_layers=self.cutoff_layers,
                    )
                    # all_scores[0] — список тензоров по слоям; берём последний cutoff
                    layer_scores = out[0]
                    chosen = layer_scores[-1][:, -1].view(-1).float()
                    scores.extend(float(x) for x in chosen.cpu().tolist())
                else:
                    logits = self.model(**inputs, return_dict=True).logits
                    chosen = logits[:, -1, self._yes_loc].view(-1).float()
                    scores.extend(float(x) for x in chosen.cpu().tolist())
        return np.asarray(scores, dtype=np.float32)


def load_llm_reranker(model_path: str, device: str) -> Any:
    cutoff_raw = os.environ.get("RAG_RERANKER_CUTOFF_LAYERS", "28").strip()
    cutoff_layers: List[int] = []
    for part in cutoff_raw.split(","):
        part = part.strip()
        if part.isdigit():
            cutoff_layers.append(int(part))
    if not cutoff_layers:
        cutoff_layers = [28]
    batch_size = int(os.environ.get("RAG_RERANKER_BATCH_SIZE", "2"))
    max_length = int(os.environ.get("RAG_RERANKER_MAX_LENGTH", "1024"))
    return LlmReranker(
        model_path,
        device=device,
        cutoff_layers=cutoff_layers,
        max_length=max_length,
        batch_size=batch_size,
    )
