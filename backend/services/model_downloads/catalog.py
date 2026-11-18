"""Чтение курируемого каталога моделей из YAML + скан уже установленных на диске."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

from backend.services.model_downloads.paths import MODEL_TYPES, is_installed, model_roots
from backend.services.model_downloads.avatar import read_avatar_url_near
from backend.services.model_downloads.provenance import (
    read_provenance_near,
    source_from_provenance,
)

_REQUIRED = ("id", "type", "display_name", "source")
_MIN_GGUF_BYTES = 1_000_000  # отсечь stub/LFS pointer (~128 КБ)


def _catalog_path() -> Path:
    backend = Path(__file__).resolve().parents[2]
    return backend / "config" / "model_catalog.yml"


def load_catalog_raw() -> List[Dict[str, Any]]:
    path = _catalog_path()
    if not path.is_file():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    models = data.get("models") or []
    if not isinstance(models, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        if any(k not in item for k in _REQUIRED):
            continue
        if item.get("type") not in MODEL_TYPES:
            continue
        src = item.get("source") or {}
        if not isinstance(src, dict) or src.get("kind") not in {"hf", "http"}:
            continue
        out.append(item)
    return out


def _fmt_size(n: int) -> str:
    if n <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(n)
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    return f"~{v:.1f} {units[i]}" if i else f"~{int(v)} {units[i]}"


def _attach_avatar(entry: Dict[str, Any], path: Optional[Path] = None) -> Dict[str, Any]:
    """avatar_url только с диска (sidecar/provenance) — без сетевых запросов в list."""
    out = dict(entry)
    if out.get("avatar_url"):
        return out
    if path is not None:
        local = read_avatar_url_near(path)
        if local:
            out["avatar_url"] = local
            return out
        meta = read_provenance_near(path)
        if meta and meta.get("avatar_url"):
            out["avatar_url"] = meta["avatar_url"]
            return out
    return out


def _with_provenance(entry: Dict[str, Any], path: Path) -> Dict[str, Any]:
    """Если рядом sidecar от UI-pull — подставляем HF/HTTP source для Re-pull."""
    meta = read_provenance_near(path)
    if not meta:
        return _attach_avatar(entry, path)
    out = dict(entry)
    fallback = (entry.get("source") or {}).get("filename") or entry.get("local_name")
    out["source"] = source_from_provenance(meta, fallback_filename=fallback)
    out["has_provenance"] = True
    if meta.get("display_name"):
        out["display_name"] = meta["display_name"]
    if meta.get("apply_action"):
        out["apply"] = meta["apply_action"]
    if meta.get("target_subdir"):
        out["target_subdir"] = meta["target_subdir"]
    if meta.get("stt_size"):
        out["stt_size"] = meta["stt_size"]
    if meta.get("local_name"):
        out["local_name"] = meta["local_name"]
    if meta.get("avatar_url"):
        out["avatar_url"] = meta["avatar_url"]
    kind = (out["source"] or {}).get("kind")
    if kind == "hf" and out["source"].get("repo_id"):
        out["description"] = f"Скачано через UI · {out['source']['repo_id']}"
    elif kind == "http":
        out["description"] = "Скачано через UI (HTTP)"
    return _attach_avatar(out, path)


def _scan_disk_models() -> List[Dict[str, Any]]:
    """Модели, лежащие на диске, но не обязательно прописанные в YAML-каталоге."""
    roots = model_roots()
    found: List[Dict[str, Any]] = []

    llm_root = roots["llm"]
    if llm_root.is_dir():
        for p in sorted(llm_root.glob("*.gguf")):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size < _MIN_GGUF_BYTES:
                continue
            found.append(
                _with_provenance(
                    {
                        "id": f"local-llm-{p.stem}",
                        "type": "llm_gguf",
                        "display_name": p.name,
                        "description": "Установлено локально (models/llm)",
                        "size_hint": _fmt_size(size),
                        "gated": False,
                        "apply": "load_llm",
                        "local_name": p.name,
                        "installed": True,
                        "from_disk": True,
                        "source": {"kind": "local", "filename": p.name},
                    },
                    p,
                )
            )
        for d in sorted(llm_root.iterdir()):
            if not d.is_dir():
                continue
            if (d / "config.json").is_file() or any(d.glob("*.safetensors")):
                found.append(
                    _with_provenance(
                        {
                            "id": f"local-llm-hf-{d.name}",
                            "type": "llm_hf",
                            "display_name": d.name,
                            "description": "HF-папка локально (models/llm)",
                            "gated": False,
                            "apply": "load_llm",
                            "local_name": d.name,
                            "installed": True,
                            "from_disk": True,
                            "source": {"kind": "local"},
                        },
                        d,
                    )
                )

    rag_root = roots["rag"]
    if rag_root.is_dir():
        for d in sorted(rag_root.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if not any(d.iterdir()):
                continue
            # эвристика: rerank в имени
            mtype = "rag_rerank" if "rerank" in d.name.lower() else "rag_embed"
            found.append(
                _with_provenance(
                    {
                        "id": f"local-rag-{d.name}",
                        "type": mtype,
                        "display_name": d.name,
                        "description": "Установлено локально (models/rag)",
                        "gated": False,
                        "apply": "select_rag",
                        "local_name": d.name,
                        "installed": True,
                        "from_disk": True,
                        "source": {"kind": "local"},
                    },
                    d,
                )
            )

    comfy = roots["comfyui"] / "checkpoints"
    if comfy.is_dir():
        for p in sorted(list(comfy.glob("*.safetensors")) + list(comfy.glob("*.ckpt"))):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size < _MIN_GGUF_BYTES:
                continue
            found.append(
                _with_provenance(
                    {
                        "id": f"local-comfy-{p.stem}",
                        "type": "comfyui",
                        "display_name": p.name,
                        "description": "Установлено локально (models/comfyui/checkpoints)",
                        "size_hint": _fmt_size(size),
                        "gated": False,
                        "apply": "none",
                        "local_name": p.name,
                        "target_subdir": "checkpoints",
                        "installed": True,
                        "from_disk": True,
                        "source": {"kind": "local", "filename": p.name},
                    },
                    p,
                )
            )

    tts = roots["tts"]
    for lang in ("ru", "en"):
        pt = tts / lang / "model.pt"
        if pt.is_file() and pt.stat().st_size > 0:
            found.append(
                _with_provenance(
                    {
                        "id": f"local-tts-{lang}",
                        "type": "tts",
                        "display_name": f"Silero TTS ({lang})",
                        "description": f"Установлено локально (silero_models/{lang})",
                        "gated": False,
                        "apply": "reload_tts",
                        "local_name": lang,
                        "installed": True,
                        "from_disk": True,
                        "source": {"kind": "local", "filename": "model.pt"},
                    },
                    pt,
                )
            )

    return found


def get_catalog_entry(catalog_id: str) -> Optional[Dict[str, Any]]:
    cid = (catalog_id or "").strip()
    for item in list_catalog_with_installed():
        if item.get("id") == cid:
            return item
    return None


def list_catalog_with_installed() -> List[Dict[str, Any]]:
    """Каталог YAML + локальные файлы с диска (без дублей по имени)."""
    result: List[Dict[str, Any]] = []
    known_files: Set[str] = set()
    known_dirs: Set[str] = set()

    for item in load_catalog_raw():
        src = item.get("source") or {}
        filename = src.get("filename") or item.get("local_name")
        installed = is_installed(
            str(item.get("type")),
            item.get("local_name"),
            filename=filename,
        )
        entry = dict(item)
        entry["installed"] = bool(installed)
        entry["from_disk"] = False
        result.append(_attach_avatar(entry))
        if filename:
            known_files.add(str(filename).lower())
            known_files.add(Path(str(filename)).name.lower())
        if item.get("local_name"):
            known_dirs.add(str(item["local_name"]).lower())
            known_files.add(str(item["local_name"]).lower())

    for disk in _scan_disk_models():
        fname = (disk.get("source") or {}).get("filename") or disk.get("local_name") or ""
        key = str(fname).lower()
        # уже покрыто записью каталога
        if key and (key in known_files or key in known_dirs):
            continue
        stem = Path(str(fname)).stem.lower() if fname else ""
        if stem and any(stem == Path(k).stem.lower() for k in known_files):
            continue
        if disk.get("local_name") and str(disk["local_name"]).lower() in known_dirs:
            continue
        result.append(disk)

    # установленные сверху в своём типе
    result.sort(key=lambda m: (0 if m.get("installed") else 1, str(m.get("type")), str(m.get("display_name"))))
    return result
