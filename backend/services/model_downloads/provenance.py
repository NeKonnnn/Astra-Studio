"""Sidecar provenance: откуда скачана модель (для Re-pull)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

SIDECAR_SUFFIX = ".astra-source.json"
DIR_SIDECAR_NAME = ".astra-source.json"
_VERSION = 1


def sidecar_path_for_file(model_file: Path) -> Path:
    return model_file.with_name(model_file.name + SIDECAR_SUFFIX)


def sidecar_path_for_dir(model_dir: Path) -> Path:
    return model_dir / DIR_SIDECAR_NAME


def write_provenance(local_path: Path, meta: Dict[str, Any]) -> Optional[Path]:
    """Пишет sidecar рядом с файлом или внутри каталога модели."""
    if not local_path:
        return None
    path = Path(local_path)
    payload = {
        "version": _VERSION,
        "saved_at": time.time(),
        **{k: v for k, v in meta.items() if v is not None and v != ""},
    }
    try:
        if path.is_file():
            dest = sidecar_path_for_file(path)
        elif path.is_dir():
            dest = sidecar_path_for_dir(path)
        else:
            # ещё не существует — решаем по суффиксу
            if path.suffix:
                dest = sidecar_path_for_file(path)
            else:
                dest = sidecar_path_for_dir(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return dest
    except OSError:
        return None


def read_provenance_near(path: Path) -> Optional[Dict[str, Any]]:
    """Читает sidecar у файла или каталога."""
    candidates = []
    if path.is_file() or path.suffix:
        candidates.append(sidecar_path_for_file(path))
    if path.is_dir() or not path.suffix:
        candidates.append(sidecar_path_for_dir(path))
    # для TTS: .../ru/model.pt → также каталог ru/
    if path.is_file():
        candidates.append(sidecar_path_for_dir(path.parent))
    for c in candidates:
        data = _read_json(c)
        if data:
            return data
    return None


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if not data.get("repo_id") and not data.get("url"):
            return None
        return data
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def source_from_provenance(meta: Dict[str, Any], *, fallback_filename: Optional[str] = None) -> Dict[str, Any]:
    """Строит source для каталога из sidecar."""
    if meta.get("url") and not meta.get("repo_id"):
        src: Dict[str, Any] = {"kind": "http", "url": meta["url"]}
    else:
        src = {"kind": "hf"}
        if meta.get("repo_id"):
            src["repo_id"] = meta["repo_id"]
        if meta.get("url"):
            src["url"] = meta["url"]
    fname = meta.get("filename") or fallback_filename
    if fname:
        src["filename"] = fname
    if meta.get("revision"):
        src["revision"] = meta["revision"]
    if meta.get("allow_patterns"):
        src["allow_patterns"] = meta["allow_patterns"]
    return src
