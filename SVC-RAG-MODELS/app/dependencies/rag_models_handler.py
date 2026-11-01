# Загрузка моделей для RAG: эмбеддинги и реранкер — только локальные веса из models_dir.
# МУЛЬТИМОДЕЛЬНЫЙ режим: несколько моделей живут одновременно.
# Ленивая загрузка при первом обращении + замок на ключ + LRU-вытеснение по лимиту.
import asyncio
import gc
import os
import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

# Кэш: (kind, folder) -> {"model": obj, "dim": Optional[int], "path": str}
# Порядок в OrderedDict = LRU (свежие в конце).
_MODEL_CACHE: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()
_KEY_LOCKS: Dict[Tuple[str, str], asyncio.Lock] = {}
_REGISTRY_LOCK = asyncio.Lock()
_last_rag_models_error: Optional[str] = None


def _free_model_memory() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _rss_mb() -> Optional[int]:
    """Резидентная память процесса в МБ (для контроля лимита пода)."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _loaded_keys() -> List[str]:
    return [f"{k}:{n}" for (k, n) in _MODEL_CACHE]


def _log_models(action: str, kind: str, folder: str, dim: Any = None) -> None:
    # print, а не logger: гарантированно виден в docker-логах даже при кривом logging-конфиге
    print(
        f"[MODELS] {action} {kind}={folder} dim={dim} rss={_rss_mb()}MB loaded={_loaded_keys()}",
        flush=True,
    )


def _folder_of(name_or_path: Optional[str]) -> str:
    """'local/FRIDA' | 'FRIDA' | '/abs/path/FRIDA' -> 'FRIDA'."""
    raw = (name_or_path or "").strip().rstrip("/")
    if not raw:
        return ""
    low = raw.lower()
    for prefix in ("local/", "native/"):
        if low.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    if os.path.isabs(raw):
        return os.path.basename(raw)
    return raw.split("/")[-1]


def _default_folder(kind: str) -> str:
    rm = settings.rag_models
    if kind == "embedding":
        return _folder_of(rm.embedding_model or rm.embedding_model_default)
    return _folder_of(rm.reranker_model or rm.reranker_model_default)


def configured_folders(kind: str) -> List[str]:
    """Разрешённые модели: config.yml + ENV RAG_*_MODEL2..20 + дефолт. Первая — кластерная."""
    rm = settings.rag_models
    if kind == "embedding":
        raw: List[Any] = [rm.embedding_model, rm.embedding_model2]
        raw += list(rm.embedding_models or [])
        env_prefix = "RAG_EMBEDDING_MODEL"
        default = rm.embedding_model_default
    else:
        raw = [rm.reranker_model, rm.reranker_model2]
        raw += list(rm.reranker_models or [])
        env_prefix = "RAG_RERANKER_MODEL"
        default = rm.reranker_model_default
    for i in range(2, 21):
        raw.append(os.environ.get(f"{env_prefix}{i}", ""))
    raw.append(default)

    out: List[str] = []
    seen = set()
    for item in raw:
        folder = _folder_of(item if isinstance(item, str) else None)
        if folder and folder.lower() not in seen:
            seen.add(folder.lower())
            out.append(folder)
    return out


def _is_giga_embed_path(model_path: str) -> bool:
    n = (model_path or "").replace("\\", "/").lower()
    if "giga" in n and "embed" in n:
        return True
    cfg_path = os.path.join(model_path, "config.json") if model_path else ""
    if not cfg_path or not os.path.isfile(cfg_path):
        return False
    try:
        import json

        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return str(data.get("model_type") or "").lower() == "gigarembed"
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def _register_giga_remote_code(model_path: str) -> None:
    """Импорт modeling_gigarembed.py — регистрирует LatentAttentionConfig в AutoModel."""
    try:
        from transformers import AutoModel
        from transformers.dynamic_module_utils import get_class_from_dynamic_module

        latent_cfg = get_class_from_dynamic_module(
            "configuration_gigarembed.LatentAttentionConfig",
            model_path,
            trust_remote_code=True,
        )
        latent_model = get_class_from_dynamic_module(
            "modeling_gigarembed.LatentAttentionModel",
            model_path,
            trust_remote_code=True,
        )
        gigar_cfg = get_class_from_dynamic_module(
            "configuration_gigarembed.GigarEmbedConfig",
            model_path,
            trust_remote_code=True,
        )
        gigar_model = get_class_from_dynamic_module(
            "modeling_gigarembed.GigarEmbedModel",
            model_path,
            trust_remote_code=True,
        )
        for cfg, mdl in ((latent_cfg, latent_model), (gigar_cfg, gigar_model)):
            try:
                AutoModel.register(cfg, mdl, exist_ok=True)
            except TypeError:
                try:
                    AutoModel.register(cfg, mdl)
                except ValueError:
                    pass  # уже зарегистрировано
        logger.info("Giga: LatentAttention/GigarEmbed зарегистрированы в AutoModel")
    except Exception as e:
        logger.warning("Giga: предрегистрация remote-code не удалась: %s", e)


def _load_sentence_transformer(model_path: str, device: str):
    """SentenceTransformer с корректным trust_remote_code для Giga и др."""
    import torch
    from sentence_transformers import SentenceTransformer

    if _is_giga_embed_path(model_path):
        _register_giga_remote_code(model_path)

    model_kwargs: dict = {"trust_remote_code": True}
    config_kwargs: dict = {"trust_remote_code": True}
    if device.startswith("cuda"):
        model_kwargs["torch_dtype"] = (
            torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        )
    else:
        model_kwargs["torch_dtype"] = torch.float32

    return SentenceTransformer(
        model_path,
        device=device,
        trust_remote_code=True,
        local_files_only=True,
        model_kwargs=model_kwargs,
        config_kwargs=config_kwargs,
    )


def _resolve_model_path(
    models_dir: str, name_or_path: Optional[str], default_local: str
) -> str:
    # Только локальные папки в models_dir (имя из ConfigMap / settings).
    if not name_or_path:
        name_or_path = default_local
    name_or_path = name_or_path.strip()
    if "/" in name_or_path and not os.path.isabs(name_or_path):
        name_or_path = name_or_path.split("/")[-1]
    if os.path.isabs(name_or_path) and os.path.isdir(name_or_path):
        return name_or_path
    full = os.path.join(models_dir, name_or_path)
    if os.path.isdir(full):
        return full
    try:
        for entry in os.listdir(models_dir):
            if not os.path.isdir(os.path.join(models_dir, entry)):
                continue
            if name_or_path.startswith("ms-marco") and entry.startswith("ms-marco"):
                return os.path.join(models_dir, entry)
            if name_or_path.startswith("paraphrase-multilingual") and entry.startswith(
                "paraphrase-multilingual"
            ):
                return os.path.join(models_dir, entry)
            if name_or_path.startswith("bge-reranker") and entry.startswith(
                "bge-reranker"
            ):
                if (
                    name_or_path.lower() in entry.lower()
                    or entry.lower() in name_or_path.lower()
                ):
                    return os.path.join(models_dir, entry)
    except OSError:
        pass
    folder = name_or_path
    try:
        for entry in os.listdir(models_dir):
            entry_l = entry.lower()
            if folder.lower() not in entry_l and entry_l not in folder.lower():
                continue
            candidate = os.path.join(models_dir, entry)
            if not os.path.isdir(candidate):
                continue
            snap_dir = os.path.join(candidate, "snapshots")
            if os.path.isdir(snap_dir):
                for h in os.listdir(snap_dir):
                    snap_path = os.path.join(snap_dir, h)
                    if os.path.isdir(snap_path) and os.path.isfile(
                        os.path.join(snap_path, "config.json")
                    ):
                        return snap_path
            if os.path.isfile(os.path.join(candidate, "config.json")):
                return candidate
    except OSError:
        pass
    return full


def _prepare_env_once() -> str:
    """models_dir + кэш transformers + offline. Возвращает абсолютный models_dir."""
    models_dir = os.path.abspath(settings.rag_models.models_dir)
    try:
        os.makedirs(models_dir, exist_ok=True)
    except OSError:
        pass  # часто смонтирован :ro — веса уже на месте

    cache_root = (
        os.environ.get("TRANSFORMERS_CACHE")
        or os.environ.get("HF_HOME")
        or "/tmp/rag-models-cache"
    )
    cache_paths = {
        "HF_HOME": cache_root,
        "HF_HUB_CACHE": os.path.join(cache_root, "hub"),
        "TRANSFORMERS_CACHE": os.path.join(cache_root, "transformers"),
        "SENTENCE_TRANSFORMERS_HOME": os.path.join(cache_root, "sentence-transformers"),
    }
    try:
        for cache_path in cache_paths.values():
            os.makedirs(cache_path, exist_ok=True)
    except OSError as cache_err:
        logger.warning(
            "Кэш transformers '%s' недоступен на запись (%s) - оставляю models_dir.",
            cache_root,
            cache_err,
        )
        cache_paths = {
            "HF_HOME": models_dir,
            "HF_HUB_CACHE": models_dir,
            "TRANSFORMERS_CACHE": models_dir,
        }
    os.environ.update(cache_paths)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return models_dir


def _resolve_device() -> str:
    device = settings.rag_models.device
    if device == "auto":
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    return device


def _max_loaded(kind: str) -> int:
    rm = settings.rag_models
    cap = rm.max_loaded_embedding if kind == "embedding" else rm.max_loaded_reranker
    try:
        return max(1, int(cap))
    except (TypeError, ValueError):
        return 1


def _evict_lru(kind: str) -> None:
    """Вытеснить самые старые модели этого типа сверх лимита."""
    cap = _max_loaded(kind)
    while True:
        keys = [k for k in _MODEL_CACHE if k[0] == kind]
        if len(keys) <= cap:
            break
        oldest = keys[0]
        _MODEL_CACHE.pop(oldest, None)
        _log_models("evict", oldest[0], oldest[1])
        _free_model_memory()


def _load_one(kind: str, folder: str, models_dir: str, device: str) -> Dict[str, Any]:
    """Синхронная загрузка одной модели. Вызывается под замком ключа."""
    rm = settings.rag_models
    default_local = (
        rm.embedding_model_default if kind == "embedding" else rm.reranker_model_default
    )
    path = _resolve_model_path(models_dir, folder, default_local)

    if rm.offline and not os.path.isdir(path):
        raise FileNotFoundError(
            f"Модель '{folder}' не найдена по пути: {path}. "
            f"Проверьте, что в {models_dir} есть такая папка."
        )

    if kind == "embedding":
        logger.info("Гружу эмбеддинг-модель: %s", path)
        obj = _load_sentence_transformer(path, device)
        try:
            dim = int(obj.get_sentence_embedding_dimension())
        except Exception:
            dim = int(rm.embedding_dim or 384)
        logger.info("Эмбеддинг-модель '%s' загружена, размерность: %s", folder, dim)
        return {"model": obj, "dim": dim, "path": path}

    from app.services.llm_reranker import is_llm_reranker_path, load_llm_reranker

    logger.info("Гружу реранкер: %s", path)
    if is_llm_reranker_path(path):
        obj = load_llm_reranker(path, device=device)
    else:
        from sentence_transformers import CrossEncoder

        try:
            obj = CrossEncoder(
                path,
                device=device,
                trust_remote_code=True,
                automodel_args={"local_files_only": True},
                tokenizer_args={"local_files_only": True},
            )
        except TypeError:
            obj = CrossEncoder(
                path,
                device=device,
                trust_remote_code=True,
                model_kwargs={"local_files_only": True, "trust_remote_code": True},
                tokenizer_kwargs={"local_files_only": True, "trust_remote_code": True},
            )
    logger.info("Реранкер '%s' загружен", folder)
    return {"model": obj, "dim": None, "path": path}


async def _get_model_entry(kind: str, name: Optional[str]) -> Dict[str, Any]:
    """Достать модель из кэша или загрузить. Ленивая загрузка + замок на ключ + LRU."""
    global _last_rag_models_error

    if not settings.rag_models.enabled:
        raise RuntimeError("RAG-модели выключены в конфиге")

    folder = _folder_of(name) or _default_folder(kind)
    if not folder:
        raise RuntimeError(f"Не задана модель типа {kind}")

    allowed = {f.lower(): f for f in configured_folders(kind)}
    if folder.lower() not in allowed:
        raise ValueError(
            f"Модель '{folder}' не разрешена для {kind}. "
            f"Доступные: {sorted(allowed)}. Добавьте её в config.yml или ENV RAG_*_MODEL2..20."
        )
    # Каноническое имя из конфига: 'FRIDA' и 'frida' - одна запись кэша
    folder = allowed[folder.lower()]

    key = (kind, folder)
    entry = _MODEL_CACHE.get(key)
    if entry is not None:
        _MODEL_CACHE.move_to_end(key)
        return entry

    async with _REGISTRY_LOCK:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _KEY_LOCKS[key] = lock

    async with lock:
        entry = _MODEL_CACHE.get(key)
        if entry is not None:  # успели загрузить, пока ждали замок
            _MODEL_CACHE.move_to_end(key)
            return entry
        models_dir = _prepare_env_once()
        device = _resolve_device()
        try:
            entry = await asyncio.to_thread(_load_one, kind, folder, models_dir, device)
        except Exception as e:
            _last_rag_models_error = f"{kind}/{folder}: {e}"
            logger.error(
                "Не удалось загрузить %s '%s': %s", kind, folder, e, exc_info=True
            )
            _free_model_memory()
            raise
        entry["device"] = device
        _MODEL_CACHE[key] = entry
        _MODEL_CACHE.move_to_end(key)
        _log_models("loaded", kind, folder, entry.get("dim"))
        _evict_lru(kind)
        # Кластерный дефолт держим в settings для health/legacy-эндпоинтов
        if (
            kind == "embedding"
            and folder.lower() == _default_folder("embedding").lower()
        ):
            if entry.get("dim"):
                settings.rag_models.embedding_dim = int(entry["dim"])
        _last_rag_models_error = None
        return entry


async def get_embedding_model(name: Optional[str] = None) -> Dict[str, Any]:
    """{'model', 'dim', 'path', 'device'} для эмбеддера (по имени или кластерный дефолт)."""
    return await _get_model_entry("embedding", name)


async def get_reranker_model(name: Optional[str] = None) -> Dict[str, Any]:
    """{'model', 'dim', 'path', 'device'} для реранкера (по имени или кластерный дефолт)."""
    return await _get_model_entry("reranker", name)


async def get_rag_models_handler() -> Optional[dict]:
    """СОВМЕСТИМОСТЬ со старым кодом: дефолтный эмбеддер + дефолтный реранкер.

    Возвращает тот же словарь, что и раньше, чтобы health/models-эндпоинты не сломались.
    """
    global _last_rag_models_error
    if not settings.rag_models.enabled:
        logger.info("RAG-модели выключены в конфиге")
        return None
    try:
        emb = await get_embedding_model(None)
        rer = await get_reranker_model(None)
        return {
            "embedding_model": emb["model"],
            "reranker_model": rer["model"],
            "device": emb.get("device") or _resolve_device(),
            "embedding_dim": emb.get("dim"),
        }

    except Exception as e:
        _last_rag_models_error = str(e)
        logger.error("Не удалось загрузить RAG-модели: %s", e, exc_info=True)
        return None


def get_last_rag_models_error() -> Optional[str]:
    """Текст последней ошибки загрузки RAG-моделей (для логов при старте)."""
    return _last_rag_models_error


def loaded_models_info() -> Dict[str, Any]:
    """Что сейчас в памяти + RSS — для диагностики и /models/current."""
    return {
        "loaded": [
            {"kind": k, "name": n, "dim": v.get("dim")}
            for (k, n), v in _MODEL_CACHE.items()
        ],
        "rss_mb": _rss_mb(),
        "max_loaded_embedding": _max_loaded("embedding"),
        "max_loaded_reranker": _max_loaded("reranker"),
    }


async def cleanup_rag_models_handler() -> None:
    """Выгрузить всё (используется при shutdown и при админском /models/select)."""
    if _MODEL_CACHE:
        logger.info("Выгружаю RAG-модели: %s", _loaded_keys())
    _MODEL_CACHE.clear()
    _free_model_memory()
    _log_models("cleanup", "all", "-")
