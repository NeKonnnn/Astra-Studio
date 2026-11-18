"""Удаление установленных моделей с диска (веса + sidecar + хвосты загрузки)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Set

from backend.services.model_downloads.catalog import get_catalog_entry
from backend.services.model_downloads.paths import (
    MODEL_TYPES,
    model_roots,
    resolve_target_dir,
    resolve_target_file,
)
from backend.services.model_downloads.avatar import (
    avatar_sidecar_path_for_dir,
    avatar_sidecar_path_for_file,
)
from backend.services.model_downloads.provenance import (
    sidecar_path_for_dir,
    sidecar_path_for_file,
)
from backend.settings.logging import get_logger

logger = get_logger(__name__)


def _all_roots() -> List[Path]:
    return [p.resolve() for p in model_roots().values()]


def _is_under_any_root(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in _all_roots():
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _unlink(path: Path, deleted: List[str]) -> None:
    if not path.exists():
        return
    if not _is_under_any_root(path):
        raise ValueError(f"Отказ удалять вне model roots: {path}")
    if path.is_dir():
        # не удаляем сам корень models/llm и т.п.
        roots = {r.resolve() for r in _all_roots()}
        if path.resolve() in roots:
            raise ValueError(f"Нельзя удалить корневой каталог моделей: {path}")
        shutil.rmtree(path)
        deleted.append(str(path))
        return
    path.unlink()
    deleted.append(str(path))


def _delete_file_and_siblings(path: Path, deleted: List[str]) -> None:
    """Файл модели + sidecar + типичные хвосты (.partial / .tmp / .download)."""
    path = path.resolve()
    if not _is_under_any_root(path):
        raise ValueError(f"Отказ удалять вне model roots: {path}")

    siblings: Set[Path] = {
        path,
        sidecar_path_for_file(path),
        avatar_sidecar_path_for_file(path),
        path.with_suffix(path.suffix + ".partial"),
        path.with_name(path.name + ".partial"),
        path.with_name(path.name + ".tmp"),
        path.with_name(path.name + ".download"),
        path.with_name(path.name + ".aria2"),
    }
    for p in sorted(siblings):
        if p.exists() and p.is_file():
            _unlink(p, deleted)


def _delete_dir_model(path: Path, deleted: List[str]) -> None:
    path = path.resolve()
    if not _is_under_any_root(path):
        raise ValueError(f"Отказ удалять вне model roots: {path}")
    roots = {r.resolve() for r in _all_roots()}
    if path in roots:
        raise ValueError(f"Нельзя удалить корневой каталог моделей: {path}")
    side = sidecar_path_for_dir(path)
    if side.is_file():
        _unlink(side, deleted)
    av = avatar_sidecar_path_for_dir(path)
    if av.is_file():
        _unlink(av, deleted)
    if path.is_dir():
        _unlink(path, deleted)


def resolve_delete_targets(entry: Dict[str, Any]) -> List[Path]:
    """Список основных путей (файл или каталог), которые нужно убрать."""
    model_type = str(entry.get("type") or "")
    if model_type not in MODEL_TYPES:
        raise ValueError(f"Неизвестный type: {model_type}")
    local_name = entry.get("local_name")
    src = entry.get("source") or {}
    filename = src.get("filename") or local_name
    target_subdir = entry.get("target_subdir")

    if model_type == "llm_gguf":
        return [
            resolve_target_file(
                model_type,
                filename or local_name or "",
                local_name=local_name,
            )
        ]

    if model_type == "comfyui":
        return [
            resolve_target_file(
                model_type,
                filename or local_name or "",
                local_name=local_name,
                target_subdir=target_subdir or "checkpoints",
            )
        ]

    if model_type == "tts":
        return [
            resolve_target_file(model_type, "model.pt", local_name=local_name or "ru")
        ]

    if model_type in {"llm_hf", "rag_embed", "rag_rerank"}:
        if not local_name:
            raise ValueError("local_name обязателен для удаления этой модели")
        return [resolve_target_dir(model_type, local_name=local_name)]

    if model_type == "omnivoice":
        # весь каталог omnivoice — одна модель по дизайну
        return [model_roots()["omnivoice"]]

    if model_type == "ocr":
        return [model_roots()["ocr"]]

    if model_type == "diarization":
        return [model_roots()["diarization"]]

    if model_type == "stt":
        root = model_roots()["stt"]
        needle = (local_name or entry.get("stt_size") or "").replace(".", "-").lower()
        matches: List[Path] = []
        if root.is_dir() and needle:
            for p in root.iterdir():
                if needle in p.name.lower() and ("whisper" in p.name.lower() or "systran" in p.name.lower() or p.is_dir()):
                    matches.append(p)
        if not matches and root.is_dir() and needle:
            for p in root.iterdir():
                if needle in p.name.lower():
                    matches.append(p)
        if not matches:
            raise ValueError(
                "Не удалось однозначно найти STT-кэш на диске. "
                "Укажите stt_size/local_name или удалите вручную из whisperx_models."
            )
        return matches

    raise ValueError(f"Удаление для type={model_type} не поддержано")


def delete_installed_model(catalog_id: str) -> Dict[str, Any]:
    """Удаляет артефакты модели по id каталога / local-* записи."""
    entry = get_catalog_entry(catalog_id)
    if not entry:
        raise ValueError(f"Модель не найдена: {catalog_id}")
    if not entry.get("installed") and not entry.get("from_disk"):
        # всё равно пытаемся по путям — мог быть stub
        pass

    model_type = str(entry["type"])
    deleted: List[str] = []
    targets = resolve_delete_targets(entry)

    for target in targets:
        target = Path(target)
        if model_type in {"llm_gguf", "comfyui", "tts"}:
            _delete_file_and_siblings(target, deleted)
            # если TTS — почистить пустой lang dir не трогаем корень silero
            if model_type == "tts" and target.parent.is_dir():
                side = sidecar_path_for_dir(target.parent)
                if side.is_file():
                    _unlink(side, deleted)
                try:
                    if target.parent.is_dir() and not any(target.parent.iterdir()):
                        if _is_under_any_root(target.parent) and target.parent.resolve() not in {
                            r.resolve() for r in _all_roots()
                        }:
                            _unlink(target.parent, deleted)
                except OSError:
                    pass
        elif model_type in {"stt"}:
            # кэш-папки whisper — целиком
            if target.resolve() in {r.resolve() for r in _all_roots()}:
                # содержимое корня, не сам корень
                if target.is_dir():
                    for child in list(target.iterdir()):
                        _unlink(child, deleted)
            else:
                _delete_dir_model(target, deleted)
        elif model_type in {"ocr", "diarization", "omnivoice"}:
            root = target.resolve()
            if root not in {r.resolve() for r in _all_roots()}:
                _delete_dir_model(target, deleted)
            else:
                if not root.is_dir():
                    continue
                for child in list(root.iterdir()):
                    _unlink(child, deleted)
                side = sidecar_path_for_dir(root)
                if side.is_file():
                    _unlink(side, deleted)
        else:
            _delete_dir_model(target, deleted)

    if not deleted:
        raise ValueError("На диске нечего удалять (файлы уже отсутствуют)")

    logger.info("deleted model %s paths=%s", catalog_id, deleted)
    return {
        "ok": True,
        "catalog_id": catalog_id,
        "display_name": entry.get("display_name"),
        "type": model_type,
        "deleted": deleted,
        "message": f"Удалено: {entry.get('display_name') or catalog_id}",
    }
