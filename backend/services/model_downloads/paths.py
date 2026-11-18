"""Резолв целевых каталогов моделей + защита от path traversal."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Optional

MODEL_TYPES = frozenset(
    {
        "llm_gguf",
        "llm_hf",
        "rag_embed",
        "rag_rerank",
        "stt",
        "tts",
        "diarization",
        "comfyui",
        "ocr",
        "omnivoice",
    }
)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,200}$")


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _repo_root() -> Path:
    backend = _backend_dir()
    # Docker: ./backend:/app → models volume рядом /app/models
    if os.getenv("DOCKER_ENV", "").lower() == "true" or Path("/app/models").exists():
        return Path("/app")
    return backend.parent


def model_roots() -> Dict[str, Path]:
    """Корневые каталоги по логическим ключам."""
    root = _repo_root()
    docker = os.getenv("DOCKER_ENV", "").lower() == "true" or (root / "models").exists() and str(root) == "/app"
    if docker or str(root) == "/app":
        return {
            "llm": root / "models" / "llm",
            "rag": root / "models" / "rag",
            "comfyui": root / "models" / "comfyui",
            "omnivoice": root / "models" / "omnivoice",
            "stt": Path("/app/whisperx_models") if Path("/app/whisperx_models").exists() or docker else root / "whisperx_models",
            "tts": Path("/app/silero_models") if Path("/app/silero_models").exists() or docker else root / "silero_models",
            "diarization": Path("/app/diarize_models") if Path("/app/diarize_models").exists() or docker else root / "diarize_models",
            "ocr": Path("/app/surya_models") if Path("/app/surya_models").exists() or docker else root / "surya_models",
        }
    return {
        "llm": root / "models" / "llm",
        "rag": root / "models" / "rag",
        "comfyui": root / "models" / "comfyui",
        "omnivoice": root / "models" / "omnivoice",
        "stt": root / "whisperx_models",
        "tts": root / "silero_models",
        "diarization": root / "diarize_models",
        "ocr": root / "surya_models",
    }


def safe_local_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw or ".." in raw.replace("\\", "/"):
        raise ValueError(f"Некорректное имя модели: {name!r}")
    # basename только для "dir/file.gguf" из HF listing — не для traversal
    n = raw.replace("\\", "/").split("/")[-1]
    if not n or n in {".", ".."} or not _SAFE_NAME.match(n):
        raise ValueError(f"Некорректное имя модели: {name!r}")
    return n


def _ensure_under(root: Path, target: Path) -> Path:
    root_r = root.resolve()
    target_r = target.resolve()
    try:
        target_r.relative_to(root_r)
    except ValueError as exc:
        raise ValueError(f"Путь вне разрешённого каталога: {target}") from exc
    return target_r


def resolve_target_dir(
    model_type: str,
    local_name: Optional[str] = None,
    target_subdir: Optional[str] = None,
) -> Path:
    """Целевая директория (или файл-родитель) для типа модели."""
    if model_type not in MODEL_TYPES:
        raise ValueError(f"Неизвестный тип модели: {model_type}")

    roots = model_roots()
    name = safe_local_name(local_name) if local_name else None

    if model_type == "llm_gguf":
        root = roots["llm"]
        root.mkdir(parents=True, exist_ok=True)
        return root

    if model_type == "llm_hf":
        root = roots["llm"]
        if not name:
            raise ValueError("local_name обязателен для llm_hf")
        target = root / name
        target.mkdir(parents=True, exist_ok=True)
        return _ensure_under(root, target)

    if model_type in {"rag_embed", "rag_rerank"}:
        root = roots["rag"]
        if not name:
            raise ValueError("local_name обязателен для RAG")
        target = root / name
        target.mkdir(parents=True, exist_ok=True)
        return _ensure_under(root, target)

    if model_type == "stt":
        root = roots["stt"]
        root.mkdir(parents=True, exist_ok=True)
        return root

    if model_type == "tts":
        root = roots["tts"]
        lang = name or "ru"
        if lang not in {"ru", "en"}:
            raise ValueError("TTS local_name должен быть ru или en")
        target = root / lang
        target.mkdir(parents=True, exist_ok=True)
        return _ensure_under(root, target)

    if model_type == "diarization":
        root = roots["diarization"]
        root.mkdir(parents=True, exist_ok=True)
        return root

    if model_type == "comfyui":
        root = roots["comfyui"]
        sub = (target_subdir or "checkpoints").strip().replace("\\", "/")
        if ".." in sub.split("/") or sub.startswith("/"):
            raise ValueError("Некорректный target_subdir")
        target = root / sub
        target.mkdir(parents=True, exist_ok=True)
        return _ensure_under(root, target)

    if model_type == "ocr":
        root = roots["ocr"]
        root.mkdir(parents=True, exist_ok=True)
        return root

    if model_type == "omnivoice":
        root = roots["omnivoice"]
        root.mkdir(parents=True, exist_ok=True)
        return root

    raise ValueError(f"Неизвестный тип модели: {model_type}")


def resolve_target_file(
    model_type: str,
    filename: str,
    local_name: Optional[str] = None,
    target_subdir: Optional[str] = None,
) -> Path:
    """Полный путь к файлу назначения."""
    fname = safe_local_name(filename)
    directory = resolve_target_dir(model_type, local_name=local_name, target_subdir=target_subdir)
    if model_type == "llm_gguf":
        # local_name может быть именем файла
        out_name = safe_local_name(local_name) if local_name and local_name.endswith(".gguf") else fname
        return _ensure_under(directory, directory / out_name)
    if model_type == "tts":
        return _ensure_under(directory, directory / fname)
    if model_type == "comfyui":
        out_name = safe_local_name(local_name) if local_name and "." in local_name else fname
        return _ensure_under(directory, directory / out_name)
    return _ensure_under(directory, directory / fname)


def is_installed(model_type: str, local_name: Optional[str], filename: Optional[str] = None) -> bool:
    """Грубая проверка наличия артефакта на диске."""
    try:
        if model_type == "llm_gguf":
            path = resolve_target_file(model_type, filename or local_name or "", local_name=local_name)
            # stub/LFS (~128 КБ) не считаем установленной моделью
            return path.is_file() and path.stat().st_size >= 1_000_000
        if model_type in {"rag_embed", "rag_rerank", "llm_hf", "omnivoice", "ocr", "diarization"}:
            d = resolve_target_dir(model_type, local_name=local_name or "x")
            if model_type == "diarization":
                d = model_roots()["diarization"]
                # любой вес / конфиг
                if not d.exists():
                    return False
                return any(d.iterdir())
            if model_type == "ocr":
                d = model_roots()["ocr"]
                return d.exists() and any(d.iterdir())
            if model_type == "omnivoice":
                d = model_roots()["omnivoice"]
                return (d / "config.json").is_file() or any(d.glob("*.safetensors"))
            return d.exists() and any(d.iterdir())
        if model_type == "tts":
            path = resolve_target_file(model_type, "model.pt", local_name=local_name or "ru")
            return path.is_file() and path.stat().st_size > 0
        if model_type == "stt":
            root = model_roots()["stt"]
            if not root.exists():
                return False
            needle = (local_name or "").replace(".", "-")
            # HF hub layout: models--Systran--faster-whisper-medium
            for p in root.iterdir():
                name = p.name.lower()
                if needle and needle.lower() in name:
                    return True
                if "faster-whisper" in name or "whisper" in name:
                    if not local_name:
                        return True
            return False
        if model_type == "comfyui":
            path = resolve_target_file(
                model_type,
                filename or local_name or "",
                local_name=local_name,
                target_subdir="checkpoints",
            )
            return path.is_file() and path.stat().st_size > 0
    except Exception:
        return False
    return False
