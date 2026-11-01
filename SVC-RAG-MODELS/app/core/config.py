import yaml
from pydantic import BaseModel
from typing import List, Optional, Tuple
import os
from pathlib import Path

_settings = None

_URLS_CORS_KEYS: Tuple[str, ...] = (
    "frontend_port",
    "frontend_port_ipv4",
    "frontend_port_2",
    "frontend_port_2_ipv4",
    "frontend_port_3",
    "frontend_port_3_ipv4",
    "backend_port",
    "backend_port_ipv4",
    "backend_port_2",
    "backend_port_2_ipv4",
)


_ENV_OVERRIDES = {
    "models_dir": ("RAG_MODELS_DIR", "str"),
    "embedding_model": ("RAG_EMBEDDING_MODEL", "str"),
    "reranker_model": ("RAG_RERANKER_MODEL", "str"),
    "device": ("RAG_MODELS_DEVICE", "str"),
    "offline": ("RAG_MODELS_OFFLINE", "bool"),
    "embed_batch_size": ("RAG_MODELS_EMBED_BATCH_SIZE", "int"),
}


def _apply_env(config_data: dict) -> dict:
    """env главнее config.yml: pydantic сам по себе даёт приоритет yml."""
    out = dict(config_data)
    rm = dict(out.get("rag_models") or {})
    for key, (env_name, kind) in _ENV_OVERRIDES.items():
        raw = (os.environ.get(env_name) or "").strip()
        if not raw:
            continue
        try:
            if kind == "bool":
                rm[key] = raw.lower() in ("1", "true", "yes", "on")
            elif kind == "int":
                rm[key] = int(raw)
            else:
                rm[key] = raw
            print(f"[CFG] rag_models.{key} <- env {env_name}={raw!r}", flush=True)
        except (TypeError, ValueError):
            print(f"[CFG] env {env_name}={raw!r} невалиден - игнорирую", flush=True)
    out["rag_models"] = rm
    return out


def _apply_urls_cors(config_data: dict) -> dict:
    urls = config_data.get("urls")
    if not isinstance(urls, dict):
        return config_data
    out = dict(config_data)
    cors = dict(out.get("cors") or {})
    ao = cors.get("allowed_origins")
    if not ao or ao == ["*"]:
        merged = [
            str(urls[k]).strip()
            for k in _URLS_CORS_KEYS
            if urls.get(k) and str(urls[k]).strip()
        ]
        if merged:
            cors["allowed_origins"] = merged
    out["cors"] = cors
    out.pop("urls", None)
    return out


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    docs_url: str = "/docs"
    redoc_url: str = "/redoc"


class CorsConfig(BaseModel):
    allowed_origins: List[str] = []
    allow_credentials: bool = True
    allow_methods: List[str] = ["*"]
    allow_headers: List[str] = ["*"]


class AppConfig(BaseModel):
    title: str = "RAG Models Service"
    description: str = "Embedding and Reranker models for RAG (MiniLM-L12, MiniLM-L6)"
    version: str = "1.0.0"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class RagModelsConfig(BaseModel):
    """Настройки эмбеддингов и реранкера — только локальные папки в models_dir."""

    enabled: bool = True
    # Папка с локальными весами (+ рабочий кэш transformers, если writable)
    models_dir: str = os.environ.get("RAG_MODELS_DIR", "/app/models/rag")
    # Активный эмбеддинг: имя подпапки в models_dir (из RAG_EMBEDDING_MODEL)
    embedding_model: Optional[str] = os.environ.get("RAG_EMBEDDING_MODEL")
    # Активный реранкер: имя подпапки (из RAG_RERANKER_MODEL)
    reranker_model: Optional[str] = os.environ.get("RAG_RERANKER_MODEL")
    # Только локальные веса, без сетевых загрузок
    offline: bool = os.environ.get("RAG_MODELS_OFFLINE", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    device: str = os.environ.get("RAG_MODELS_DEVICE", "cpu")  # cpu, cuda или auto
    # Дефолтные имена папок, если ENV не задан
    embedding_model_default: str = "paraphrase-multilingual-MiniLM-L12-v2"
    reranker_model_default: str = "ms-marco-MiniLM-L-6-v2"
    embedding_dim: int = 384
    # Размер батча encode(); на CPU держите 8–16, на GPU можно 32–64
    embed_batch_size: int = int(os.environ.get("RAG_MODELS_EMBED_BATCH_SIZE", "16"))
    # Дополнительные модели (кроме основной). embedding_model2 уже есть в config.yml.
    embedding_model2: Optional[str] = None
    reranker_model2: Optional[str] = None
    # Списком, если моделей больше двух
    embedding_models: List[str] = []
    reranker_models: List[str] = []
    # Сколько моделей держать в памяти одновременно (LRU-вытеснение сверх лимита)
    max_loaded_embedding: int = 2
    max_loaded_reranker: int = 2
    # Префиксы асимметричных моделей (FRIDA). Применяются, только если модель их знает.
    query_prompt_name: str = "search_query"
    document_prompt_name: str = "search_document"


class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    cors: CorsConfig = CorsConfig()
    app: AppConfig = AppConfig()
    logging: LoggingConfig = LoggingConfig()
    rag_models: RagModelsConfig = RagModelsConfig()

    @classmethod
    def from_yaml(cls, config_path: Optional[str] = None):
        # Ищем config по типичным путям, если путь не передали
        if config_path is None or config_path == "":
            possible_paths = [
                "config/config.yml",
                "../config/config.yml",
                Path(__file__).resolve().parent.parent.parent / "config" / "config.yml",
            ]
            for path in possible_paths:
                p = str(path) if hasattr(path, "resolve") else path
                if os.path.exists(p):
                    config_path = p
                    break
            else:
                return cls()
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls(**_apply_env(_apply_urls_cors(data)))
        except Exception as e:
            raise ValueError(f"Error loading config from {config_path}: {e}")


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        config_path = os.environ.get("CONFIG_PATH", "")
        _settings = Settings.from_yaml(config_path or None)
    return _settings


settings = get_settings()
