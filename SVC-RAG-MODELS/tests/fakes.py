"""Фейковые ML-библиотеки для тестов SVC-RAG-MODELS.

Реальные ``torch`` / ``sentence_transformers`` / ``transformers`` в тестовое окружение
не ставятся: код сервиса импортирует их лениво внутри функций, поэтому достаточно
подложить фейки в ``sys.modules``. Выходы детерминированы и считаются на numpy.
"""

from __future__ import annotations

import contextlib
import os
import sys
import types
from typing import Any, Dict, List, Optional

import numpy as np


class FakeTensor:
    """Мини-тензор поверх numpy: ровно те методы, что зовёт LlmReranker.predict."""

    def __init__(self, arr: Any):
        self.a = np.asarray(arr)

    def to(self, device: str) -> "FakeTensor":
        return self

    def cpu(self) -> "FakeTensor":
        return self

    def float(self) -> "FakeTensor":
        return FakeTensor(self.a.astype(np.float32))

    def view(self, *shape: int) -> "FakeTensor":
        return FakeTensor(self.a.reshape(*shape))

    def tolist(self) -> list:
        return self.a.tolist()

    def __getitem__(self, idx: Any) -> "FakeTensor":
        return FakeTensor(self.a[idx])


class FakeML:
    """Набор фейковых модулей + журнал вызовов + рычаги для сценариев ошибок."""

    VOCAB = 128

    def __init__(self) -> None:
        # --- рычаги ---
        self.dim = 8
        self.cuda_available = False
        self.bf16 = True
        self.st_error: Optional[BaseException] = None
        self.dim_error = False
        self.encode_1d = False
        self.ce_reject_automodel_args = False
        self.ce_error: Optional[BaseException] = None
        self.automodel_register_mode = "ok"  # ok | typeerror | typeerror_valueerror
        self.dyn_error: Optional[BaseException] = None
        self.causal_register_error: Optional[BaseException] = None
        self.config_register_error: Optional[BaseException] = None
        # --- журналы ---
        self.st_calls: List[Dict[str, Any]] = []
        self.ce_calls: List[Dict[str, Any]] = []
        self.empty_cache_calls = 0
        self.automodel_register_calls: List[tuple] = []
        self.dyn_calls: List[str] = []
        self.autoconfig_register_calls: List[tuple] = []
        self.causal_register_calls: List[tuple] = []
        self.tokenizer_calls: List[Dict[str, Any]] = []
        self.causal_load_calls: List[Dict[str, Any]] = []
        self.model_calls: List[Dict[str, Any]] = []
        self.models: List[Any] = []
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        ml = self

        # ---------------- torch ----------------
        torch = types.ModuleType("torch")
        torch.float32 = "float32"
        torch.float16 = "float16"
        torch.bfloat16 = "bfloat16"
        cuda = types.SimpleNamespace(
            is_available=lambda: ml.cuda_available,
            is_bf16_supported=lambda: ml.bf16,
            empty_cache=lambda: setattr(
                ml, "empty_cache_calls", ml.empty_cache_calls + 1
            ),
        )
        torch.cuda = cuda

        @contextlib.contextmanager
        def no_grad():
            yield

        torch.no_grad = no_grad
        self.torch = torch

        # ---------------- sentence_transformers ----------------
        class SentenceTransformer:
            def __init__(self, model_path: str, device: str = "cpu", **kwargs: Any):
                ml.st_calls.append({"path": model_path, "device": device, **kwargs})
                if ml.st_error is not None:
                    raise ml.st_error
                self.path = model_path
                self.device = device
                base = os.path.basename(model_path.rstrip("/\\")).lower()
                self.prompts = (
                    {"search_query": "query: ", "search_document": "doc: "}
                    if "frida" in base
                    else {}
                )
                self.encode_calls: List[Dict[str, Any]] = []
                ml.models.append(self)

            def get_sentence_embedding_dimension(self) -> int:
                if ml.dim_error:
                    raise RuntimeError("нет размерности")
                return ml.dim

            def encode(self, texts: List[str], **kwargs: Any) -> np.ndarray:
                self.encode_calls.append({"texts": list(texts), **kwargs})
                shift = 1.0 if kwargs.get("prompt_name") else 0.0
                rows = [
                    [
                        ((sum(map(ord, t)) + j) % 17) / 17.0 + shift
                        for j in range(ml.dim)
                    ]
                    for t in texts
                ]
                arr = np.asarray(rows, dtype=np.float32)
                return arr[0] if ml.encode_1d else arr

        class CrossEncoder:
            def __init__(self, model_path: str, device: str = "cpu", **kwargs: Any):
                ml.ce_calls.append({"path": model_path, "device": device, **kwargs})
                if ml.ce_reject_automodel_args and "automodel_args" in kwargs:
                    raise TypeError("unexpected keyword automodel_args")
                if ml.ce_error is not None:
                    raise ml.ce_error
                self.path = model_path
                ml.models.append(self)

            def predict(self, pairs: List[List[str]]) -> np.ndarray:
                # Скор = число общих слов запроса и пассажа (детерминированно)
                out = []
                for q, p in pairs:
                    out.append(float(len(set(q.lower().split()) & set(p.lower().split()))))
                return np.asarray(out, dtype=np.float32)

        st_mod = types.ModuleType("sentence_transformers")
        st_mod.SentenceTransformer = SentenceTransformer
        st_mod.CrossEncoder = CrossEncoder
        self.sentence_transformers = st_mod
        self.SentenceTransformer = SentenceTransformer
        self.CrossEncoder = CrossEncoder

        # ---------------- transformers ----------------
        class AutoModel:
            @staticmethod
            def register(cfg: Any, mdl: Any, **kwargs: Any) -> None:
                ml.automodel_register_calls.append((cfg, mdl, dict(kwargs)))
                if ml.automodel_register_mode == "typeerror" and "exist_ok" in kwargs:
                    raise TypeError("exist_ok не поддерживается")
                if ml.automodel_register_mode == "typeerror_valueerror":
                    if "exist_ok" in kwargs:
                        raise TypeError("exist_ok не поддерживается")
                    raise ValueError("уже зарегистрировано")

        class AutoConfig:
            @staticmethod
            def register(model_type: str, cls: Any, **kwargs: Any) -> None:
                ml.autoconfig_register_calls.append((model_type, cls, dict(kwargs)))
                if ml.config_register_error is not None:
                    raise ml.config_register_error

        class FakeTokenizer:
            bos_token_id: Optional[int] = 1

            def __call__(
                self,
                text: str,
                return_tensors: Any = None,
                add_special_tokens: bool = False,
                max_length: Optional[int] = None,
                truncation: Any = None,
            ) -> Dict[str, List[int]]:
                ids = [2 + (ord(c) % 50) for c in text]
                if truncation and max_length:
                    ids = ids[:max_length]
                return {"input_ids": ids}

            def prepare_for_model(
                self, first: List[int], second: List[int], **kwargs: Any
            ) -> Dict[str, List[int]]:
                ids = list(first) + list(second)
                max_length = kwargs.get("max_length")
                if max_length:
                    ids = ids[:max_length]
                return {"input_ids": ids}

            def pad(
                self,
                inputs: List[Dict[str, List[int]]],
                padding: bool = True,
                max_length: Optional[int] = None,
                pad_to_multiple_of: Optional[int] = None,
                return_tensors: Optional[str] = None,
            ) -> Dict[str, FakeTensor]:
                width = max(len(i["input_ids"]) for i in inputs)
                if pad_to_multiple_of:
                    width = -(-width // pad_to_multiple_of) * pad_to_multiple_of
                ids = np.zeros((len(inputs), width), dtype=np.int64)
                mask = np.zeros((len(inputs), width), dtype=np.int64)
                for r, item in enumerate(inputs):
                    n = len(item["input_ids"])
                    ids[r, :n] = item["input_ids"]
                    mask[r, :n] = item["attention_mask"]
                return {
                    "input_ids": FakeTensor(ids),
                    "attention_mask": FakeTensor(mask),
                }

        class AutoTokenizer:
            @staticmethod
            def from_pretrained(path: str, **kwargs: Any) -> FakeTokenizer:
                ml.tokenizer_calls.append({"path": path, **kwargs})
                return FakeTokenizer()

        class FakeCausalLM:
            def __init__(self, path: str):
                self.path = path
                self.device: Optional[str] = None
                self.eval_called = False

            def to(self, device: str) -> "FakeCausalLM":
                self.device = device
                return self

            def eval(self) -> "FakeCausalLM":
                self.eval_called = True
                return self

            def __call__(
                self,
                input_ids: FakeTensor,
                attention_mask: FakeTensor,
                return_dict: bool = False,
                cutoff_layers: Optional[List[int]] = None,
            ) -> Any:
                ml.model_calls.append(
                    {"cutoff_layers": cutoff_layers, "return_dict": return_dict}
                )
                count = attention_mask.a.sum(axis=1).astype(np.float32)  # токенов
                batch = count.shape[0]
                if cutoff_layers is not None:
                    # Список слоёв: берётся ПОСЛЕДНИЙ, колонка -1
                    first = np.stack([count * 0 - 1] * 3, axis=1)
                    last = np.stack([count * 0 - 2, count * 0 - 3, count], axis=1)
                    return ([FakeTensor(first), FakeTensor(last)],)
                logits = np.full((batch, 4, ml.VOCAB), -1.0, dtype=np.float32)
                yes_loc = 2 + (ord("Y") % 50)
                logits[:, -1, yes_loc] = count
                return types.SimpleNamespace(logits=FakeTensor(logits))

        class AutoModelForCausalLM:
            @staticmethod
            def from_pretrained(path: str, **kwargs: Any) -> FakeCausalLM:
                ml.causal_load_calls.append({"path": path, **kwargs})
                model = FakeCausalLM(path)
                ml.models.append(model)
                return model

            @staticmethod
            def register(cfg: Any, cls: Any, **kwargs: Any) -> None:
                ml.causal_register_calls.append((cfg, cls, dict(kwargs)))
                if ml.causal_register_error is not None:
                    raise ml.causal_register_error

        def get_class_from_dynamic_module(
            ref: str, path: str, trust_remote_code: bool = False
        ) -> Any:
            ml.dyn_calls.append(ref)
            if ml.dyn_error is not None:
                raise ml.dyn_error
            config_class = type("FakeConfigClass", (), {})
            return type(ref.split(".")[-1], (), {"config_class": config_class})

        tr_mod = types.ModuleType("transformers")
        tr_mod.AutoModel = AutoModel
        tr_mod.AutoConfig = AutoConfig
        tr_mod.AutoTokenizer = AutoTokenizer
        tr_mod.AutoModelForCausalLM = AutoModelForCausalLM
        dyn_mod = types.ModuleType("transformers.dynamic_module_utils")
        dyn_mod.get_class_from_dynamic_module = get_class_from_dynamic_module
        tr_mod.dynamic_module_utils = dyn_mod
        self.transformers = tr_mod
        self.dynamic_module_utils = dyn_mod
        self.FakeTokenizer = FakeTokenizer

    # ------------------------------------------------------------------
    def install(self, monkeypatch: Any, *, transformers: bool = True) -> "FakeML":
        monkeypatch.setitem(sys.modules, "torch", self.torch)
        monkeypatch.setitem(sys.modules, "sentence_transformers", self.sentence_transformers)
        if transformers:
            monkeypatch.setitem(sys.modules, "transformers", self.transformers)
            monkeypatch.setitem(
                sys.modules,
                "transformers.dynamic_module_utils",
                self.dynamic_module_utils,
            )
        return self
