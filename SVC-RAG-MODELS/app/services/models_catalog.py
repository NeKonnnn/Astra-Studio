"""Каталог локальных моделей эмбеддингов и реранкера для UI.

Источники:
- папки в RAG_MODELS_DIR;
- имена из ConfigMap/ENV: RAG_EMBEDDING_MODEL[N], RAG_RERANKER_MODEL[N].

Только локальные веса — без внешних каталогов моделей.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from app.core.config import settings

ModelKind = Literal["embedding", "reranker"]

_RERANKER_HINTS = (
    "marco",
    "cross-encoder",
    "rerank",
    "ms-marco",
    "tinybert",
    "minicpm-layerwise",
    "minicpm_layerwise",
)
_EMBEDDING_HINTS = (
    "paraphrase",
    "embedding",
    "e5-",
    "bge-",
    "multilingual-minilm",
    "minilm-l12",
    "frida",
    "giga-",
)


def _folder_name(value: str) -> str:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    # org/model или путь → последняя компонента
    return raw.split("/")[-1]


def _env_model_folders(prefix: str) -> List[str]:
    """Собирает RAG_*_MODEL, RAG_*_MODEL2 … RAG_*_MODEL20 из окружения."""
    out: List[str] = []
    seen: Set[str] = set()

    def add(raw: str) -> None:
        folder = _folder_name(raw)
        if not folder:
            return
        key = folder.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(folder)

    add(os.environ.get(prefix, "") or "")
    for i in range(2, 21):
        add(os.environ.get(f"{prefix}{i}", "") or "")
    return out


def configured_embedding_folders() -> List[str]:
    folders = _env_model_folders("RAG_EMBEDDING_MODEL")
    # Активная / дефолт из settings (на случай yaml без ENV)
    for extra in (
        settings.rag_models.embedding_model,
        settings.rag_models.embedding_model_default,
    ):
        name = _folder_name(extra or "")
        if name and name.lower() not in {f.lower() for f in folders}:
            folders.append(name)
    return folders


def configured_reranker_folders() -> List[str]:
    folders = _env_model_folders("RAG_RERANKER_MODEL")
    for extra in (
        settings.rag_models.reranker_model,
        settings.rag_models.reranker_model_default,
    ):
        name = _folder_name(extra or "")
        if name and name.lower() not in {f.lower() for f in folders}:
            folders.append(name)
    return folders


def _guess_kind(folder_name: str, model_dir: str) -> Optional[ModelKind]:
    n = folder_name.lower()
    if any(h in n for h in _RERANKER_HINTS):
        return "reranker"
    if any(h in n for h in _EMBEDDING_HINTS):
        return "embedding"
    # sentence-transformers layout — почти всегда embedding
    if os.path.isfile(os.path.join(model_dir, "modules.json")):
        return "embedding"
    if os.path.isfile(os.path.join(model_dir, "config_sentence_transformers.json")):
        return "embedding"
    return None


def _is_model_dir(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    for marker in (
        "config.json",
        "modules.json",
        "pytorch_model.bin",
        "model.safetensors",
    ):
        if os.path.isfile(os.path.join(path, marker)):
            return True
    snap = os.path.join(path, "snapshots")
    if os.path.isdir(snap):
        for h in os.listdir(snap):
            if os.path.isfile(os.path.join(snap, h, "config.json")):
                return True
    return False


def _row(
    folder: str,
    kind: ModelKind,
    *,
    available: bool,
    description: str,
) -> Dict[str, Any]:
    return {
        "path": f"local/{folder}",
        "name": folder,
        "display_name": folder,
        "source": "local",
        "kind": kind,
        "description": description,
        "available": available,
    }


def _scan_local_models(models_dir: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not os.path.isdir(models_dir):
        return rows
    try:
        entries = sorted(os.listdir(models_dir))
    except OSError:
        return rows
    for entry in entries:
        full = os.path.join(models_dir, entry)
        if not _is_model_dir(full):
            continue
        kind = _guess_kind(entry, full)
        if kind is None:
            continue
        rows.append(
            _row(
                entry,
                kind,
                available=True,
                description=f"Локальная модель в {models_dir}",
            )
        )
    return rows


def _configmap_rows(models_dir: str) -> List[Dict[str, Any]]:
    """Модели из ConfigMap/ENV — в UI даже если папки ещё нет (available=false)."""
    rows: List[Dict[str, Any]] = []
    for folder in configured_embedding_folders():
        full = os.path.join(models_dir, folder)
        rows.append(
            _row(
                folder,
                "embedding",
                available=_is_model_dir(full),
                description="Из ConfigMap (RAG_EMBEDDING_MODEL*)",
            )
        )
    for folder in configured_reranker_folders():
        full = os.path.join(models_dir, folder)
        rows.append(
            _row(
                folder,
                "reranker",
                available=_is_model_dir(full),
                description="Из ConfigMap (RAG_RERANKER_MODEL*)",
            )
        )
    return rows


def _path_key(path: str) -> str:
    return (path or "").strip().lower()


def list_models(kind: Optional[ModelKind] = None) -> Dict[str, List[Dict[str, Any]]]:
    models_dir = os.path.abspath(settings.rag_models.models_dir)
    by_kind: Dict[str, List[Dict[str, Any]]] = {"embedding": [], "reranker": []}
    seen: Dict[str, Set[str]] = {"embedding": set(), "reranker": set()}

    def add(row: Dict[str, Any]) -> None:
        k = row["kind"]
        key = _path_key(row["path"])
        if key in seen[k]:
            # Уже есть (скан диска) — не дублируем ConfigMap-строку
            return
        seen[k].add(key)
        by_kind[k].append(row)

    # Сначала диск (available=true), затем ConfigMap (дополнит отсутствующие)
    for row in _scan_local_models(models_dir):
        add(row)
    for row in _configmap_rows(models_dir):
        add(row)

    if kind is not None:
        return {kind: by_kind[kind]}
    return by_kind


def parse_model_path(model_path: str) -> Tuple[str, str]:
    raw = (model_path or "").strip()
    if not raw:
        raise ValueError("model_path пуст")
    if raw.lower().startswith("huggingface/"):
        raise ValueError(
            "Внешний каталог моделей отключён: используйте local/<папка> из models/rag"
        )
    if raw.startswith("local/"):
        return "local", raw[len("local/") :]
    if raw.startswith("phoenix/"):
        return "phoenix", raw[len("phoenix/") :]
    return "local", raw


def config_value_for_path(model_path: str) -> str:
    source, model_id = parse_model_path(model_path)
    if source == "phoenix":
        raise ValueError("Phoenix-модели выбираются через backend, не через svc-rag-models")
    return _folder_name(model_id) or model_id


# Снимок ConfigMap/env на старте процесса. UI-выбор не должен подменять
# «кластерный дефолт» для других пользователей (settings.rag_models.* мутабельны).
_BOOT_EMBEDDING_MODEL = (
    settings.rag_models.embedding_model
    or settings.rag_models.embedding_model_default
)
_BOOT_RERANKER_MODEL = (
    settings.rag_models.reranker_model or settings.rag_models.reranker_model_default
)


def _model_path_row(value: str, kind: ModelKind) -> Dict[str, str]:
    models_dir = os.path.abspath(settings.rag_models.models_dir)
    folder = _folder_name(value) or value
    local_path = os.path.join(models_dir, folder)
    return {
        "path": f"local/{folder}",
        "name": folder,
        "display_name": folder,
        "source": "local",
        "kind": kind,
        "available": _is_model_dir(local_path),
    }


def cluster_default_model_paths() -> Dict[str, Dict[str, str]]:
    """Модели из ConfigMap/env на старте пода (не runtime после /models/select)."""
    return {
        "embedding": _model_path_row(_BOOT_EMBEDDING_MODEL, "embedding"),
        "reranker": _model_path_row(_BOOT_RERANKER_MODEL, "reranker"),
    }


def current_model_paths() -> Dict[str, Dict[str, str]]:
    emb_cfg = (
        settings.rag_models.embedding_model
        or settings.rag_models.embedding_model_default
    )
    rer_cfg = (
        settings.rag_models.reranker_model or settings.rag_models.reranker_model_default
    )
    return {
        "embedding": _model_path_row(emb_cfg, "embedding"),
        "reranker": _model_path_row(rer_cfg, "reranker"),
    }
