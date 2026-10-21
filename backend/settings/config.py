"""
Основной модуль конфигурации для astrachat Backend
Загружает настройки из YAML и переменных окружения
"""
import yaml
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator
# Попытка загрузить переменные окружения из .env файла
try:
    from dotenv import load_dotenv
    # Ищем .env в разных местах
    # Ищем .env файл в корне проекта
    root_dir = Path(__file__).parent.parent.parent
    current_workdir = Path.cwd()
    env_paths = [
        current_workdir / ".env",
        root_dir / ".env",        
        Path(__file__).parent.parent / ".env",
        Path("/app") / ".env", # в docer у меня путь прописан как app/.env
    ]
    # Пробуем загрузить из стандартных путей
    loaded = False
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path, override=False)  # override=False - env переменные имеют приоритет
            loaded = True
            break
    # Если .env не найден, пробуем найти любой файл, начинающийся с .env в корне
    if not loaded:
        for search_dir in [current_workdir, root_dir, Path("/app")]:
            if search_dir.exists():
                for env_file in search_dir.glob(".env*"):
                    if env_file.is_file() and not env_file.name.endswith('.example'):
                        load_dotenv(env_file, override=False)
                        print(f"[config] Загружен .env файл: {env_file}")
                        loaded = True
                        break
                if loaded:
                    break
    if not loaded:
        print(f"[config] .env файл не найден, используются только системные переменные окружения")
except ImportError:
    print(f"[config] python-dotenv не установлен, используются только системные переменные окружения")
    pass
from .connections import (
    MongoDBConnectionConfig,
    PostgreSQLConnectionConfig,
    MinIOConnectionConfig,
    LLMServiceConnectionConfig,
    LLMHostEntry,
    _get_env_value,
    _get_env_value_int,
)
# Глобальный экземпляр настроек
_settings: Optional['Settings'] = None
class ServerConfig(BaseModel):
    """Конфигурация сервера"""
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    workers: int = 1
class CorsConfig(BaseModel):
    """Конфигурация CORS"""
    allowed_origins: List[str] = []
    allow_credentials: bool = True
    allow_methods: List[str] = ["*"]
    allow_headers: List[str] = ["*"]
class AppConfig(BaseModel):
    """Конфигурация приложения"""
    name: str = "astrachat Backend"
    version: str = "1.0.0"
    description: str = "Backend service for astrachat with microservice architecture"
    debug: bool = False
class MemoryConfig(BaseModel):
    """Конфигурация памяти"""
    enabled: bool = True
    storage_type: str = "file"  # file, redis, database
    file_path: str = "/app/memory"
    max_history_length: int = 100
    auto_save: bool = True
    save_interval: int = 30  # секунды


class ImageGenerationNodeMapEntry(BaseModel):
    node: str
    input: str


class ImageGenerationPreset(BaseModel):
    id: str = ""
    label: str = ""
    description: str = ""
    workflow_path: str = ""
    checkpoint_name: str = ""
    default_width: int = 1024
    default_height: int = 1024
    default_steps: int = 4
    node_map: Dict[str, ImageGenerationNodeMapEntry] = Field(default_factory=dict)


class ImageGenerationConfig(BaseModel):
    """ComfyUI — генерация изображений из чата и API."""
    enabled: bool = False
    comfyui_base_url: str = "http://127.0.0.1:8188"
    # URL ComfyUI UI для браузера (если бэкенд ходит на docker-сервис comfyui:8188)
    comfyui_public_url: str = "http://localhost:8188"
    workflow_path: str = "config/comfy_workflows/sd15_txt2img_api.json"
    request_timeout_sec: float = 900
    poll_interval_sec: float = 1.0
    node_map: Dict[str, ImageGenerationNodeMapEntry] = Field(default_factory=dict)
    chat_triggers_enabled: bool = True
    default_width: int = 512
    default_height: int = 512
    default_steps: int = 20
    # Имя файла в ComfyUI models/checkpoints/; пусто — из workflow или первый доступный
    checkpoint_name: str = ""
    default_preset_id: str = ""
    presets: Dict[str, ImageGenerationPreset] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def load_from_yaml_or_env(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            data = {}
        result = dict(data)

        env_enabled = _truthy_env_optional("IMAGE_GEN_ENABLED")
        if env_enabled is not None:
            result["enabled"] = env_enabled

        env_url = _get_env_value("IMAGE_GEN_COMFYUI_URL")
        if env_url:
            result["comfyui_base_url"] = env_url

        env_pub = _get_env_value("IMAGE_GEN_COMFYUI_PUBLIC_URL")
        if env_pub:
            result["comfyui_public_url"] = env_pub

        env_wf = _get_env_value("IMAGE_GEN_WORKFLOW_PATH")
        if env_wf:
            result["workflow_path"] = env_wf

        env_ckpt = _get_env_value("IMAGE_GEN_CHECKPOINT_NAME")
        if env_ckpt:
            result["checkpoint_name"] = env_ckpt

        env_chat = _truthy_env_optional("IMAGE_GEN_CHAT_TRIGGERS")
        if env_chat is not None:
            result["chat_triggers_enabled"] = env_chat

        for key, env_name, cast in (
            ("default_width", "IMAGE_GEN_DEFAULT_WIDTH", int),
            ("default_height", "IMAGE_GEN_DEFAULT_HEIGHT", int),
            ("default_steps", "IMAGE_GEN_DEFAULT_STEPS", int),
            ("request_timeout_sec", "IMAGE_GEN_TIMEOUT_SEC", float),
        ):
            raw = _get_env_value(env_name)
            if raw is not None and str(raw).strip() != "":
                try:
                    result[key] = cast(raw)
                except (TypeError, ValueError):
                    pass

        nm = result.get("node_map")
        if isinstance(nm, dict):
            parsed: Dict[str, Any] = {}
            for k, v in nm.items():
                if isinstance(v, dict) and v.get("node") and v.get("input"):
                    parsed[str(k)] = v
            result["node_map"] = parsed

        raw_presets = result.get("presets")
        if isinstance(raw_presets, dict):
            parsed_presets: Dict[str, Any] = {}
            for pid, val in raw_presets.items():
                if not isinstance(val, dict):
                    continue
                entry = dict(val)
                entry["id"] = str(pid)
                nm = entry.get("node_map")
                if isinstance(nm, dict):
                    parsed_nm: Dict[str, Any] = {}
                    for k, v in nm.items():
                        if isinstance(v, dict) and v.get("node") and v.get("input"):
                            parsed_nm[str(k)] = v
                    entry["node_map"] = parsed_nm
                parsed_presets[str(pid)] = entry
            result["presets"] = parsed_presets

        return result


class VideoGenerationPreset(BaseModel):
    id: str = ""
    label: str = ""
    description: str = ""
    workflow_path: str = ""
    checkpoint_name: str = ""
    default_width: int = 512
    default_height: int = 512
    default_steps: int = 20
    default_frames: int = 16
    node_map: Dict[str, ImageGenerationNodeMapEntry] = Field(default_factory=dict)


class VideoGenerationConfig(BaseModel):
    """ComfyUI — генерация видео из чата."""
    enabled: bool = False
    comfyui_base_url: str = "http://127.0.0.1:8188"
    comfyui_public_url: str = "http://localhost:8188"
    workflow_path: str = ""
    request_timeout_sec: float = 1800
    poll_interval_sec: float = 1.0
    node_map: Dict[str, ImageGenerationNodeMapEntry] = Field(default_factory=dict)
    chat_triggers_enabled: bool = False
    default_width: int = 512
    default_height: int = 512
    default_steps: int = 20
    default_frames: int = 16
    checkpoint_name: str = ""
    default_preset_id: str = ""
    presets: Dict[str, VideoGenerationPreset] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def load_from_yaml_or_env(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            data = {}
        result = dict(data)

        env_enabled = _truthy_env_optional("VIDEO_GEN_ENABLED")
        if env_enabled is not None:
            result["enabled"] = env_enabled

        env_url = _get_env_value("VIDEO_GEN_COMFYUI_URL")
        if env_url:
            result["comfyui_base_url"] = env_url

        env_pub = _get_env_value("VIDEO_GEN_COMFYUI_PUBLIC_URL")
        if env_pub:
            result["comfyui_public_url"] = env_pub

        env_wf = _get_env_value("VIDEO_GEN_WORKFLOW_PATH")
        if env_wf:
            result["workflow_path"] = env_wf

        env_ckpt = _get_env_value("VIDEO_GEN_CHECKPOINT_NAME")
        if env_ckpt:
            result["checkpoint_name"] = env_ckpt

        env_chat = _truthy_env_optional("VIDEO_GEN_CHAT_TRIGGERS")
        if env_chat is not None:
            result["chat_triggers_enabled"] = env_chat

        for key, env_name, cast in (
            ("default_width", "VIDEO_GEN_DEFAULT_WIDTH", int),
            ("default_height", "VIDEO_GEN_DEFAULT_HEIGHT", int),
            ("default_steps", "VIDEO_GEN_DEFAULT_STEPS", int),
            ("default_frames", "VIDEO_GEN_DEFAULT_FRAMES", int),
            ("request_timeout_sec", "VIDEO_GEN_TIMEOUT_SEC", float),
        ):
            raw = _get_env_value(env_name)
            if raw is not None and str(raw).strip() != "":
                try:
                    result[key] = cast(raw)
                except (TypeError, ValueError):
                    pass

        nm = result.get("node_map")
        if isinstance(nm, dict):
            parsed: Dict[str, Any] = {}
            for k, v in nm.items():
                if isinstance(v, dict) and v.get("node") and v.get("input"):
                    parsed[str(k)] = v
            result["node_map"] = parsed

        raw_presets = result.get("presets")
        if isinstance(raw_presets, dict):
            parsed_presets: Dict[str, Any] = {}
            for pid, val in raw_presets.items():
                if not isinstance(val, dict):
                    continue
                entry = dict(val)
                entry["id"] = str(pid)
                nm = entry.get("node_map")
                if isinstance(nm, dict):
                    parsed_nm: Dict[str, Any] = {}
                    for k, v in nm.items():
                        if isinstance(v, dict) and v.get("node") and v.get("input"):
                            parsed_nm[str(k)] = v
                    entry["node_map"] = parsed_nm
                parsed_presets[str(pid)] = entry
            result["presets"] = parsed_presets

        return result


def _truthy_env_optional(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


class LoggingConfig(BaseModel):
    """Конфигурация логирования"""
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Optional[str] = "/app/logs/backend.log"
    max_size: int = 10485760  # 10MB
    backup_count: int = 5
    console: bool = True
class SecurityConfig(BaseModel):
    """Конфигурация безопасности"""
    enabled: bool = False
    api_key: Optional[str] = None
    api_key_header: str = "X-API-Key"
    rate_limiting_enabled: bool = False
    rate_limiting_requests_per_minute: int = 60


class LoginLockoutConfig(BaseModel):
    """Блокировка входа после серии неверных паролей.

    Источники (приоритет): переменные окружения пода > auth.login_lockout в config.yml.
    ENV: AUTH_LOGIN_LOCKOUT_ENABLED, AUTH_LOGIN_LOCKOUT_MAX_FAILED_ATTEMPTS,
         AUTH_LOGIN_LOCKOUT_DURATION_SECONDS.
    """
    enabled: bool = True
    max_failed_attempts: int = 5
    lockout_duration_seconds: int = 900

    @model_validator(mode="before")
    @classmethod
    def load_from_yaml_or_env(cls, data: dict) -> dict:
        if not isinstance(data, dict):
            data = {}
        result = dict(data)

        # Легаси YAML: lockout_duration_minutes → секунды
        if "lockout_duration_seconds" not in result and result.get("lockout_duration_minutes") is not None:
            try:
                result["lockout_duration_seconds"] = int(result["lockout_duration_minutes"]) * 60
            except (TypeError, ValueError):
                pass
        result.pop("lockout_duration_minutes", None)

        env_enabled = _get_env_value("AUTH_LOGIN_LOCKOUT_ENABLED")
        if env_enabled is not None and str(env_enabled).strip():
            result["enabled"] = str(env_enabled).strip().lower() in {"1", "true", "yes", "on"}

        env_max = _get_env_value_int("AUTH_LOGIN_LOCKOUT_MAX_FAILED_ATTEMPTS")
        if env_max is not None:
            result["max_failed_attempts"] = env_max

        env_seconds = _get_env_value_int("AUTH_LOGIN_LOCKOUT_DURATION_SECONDS")
        if env_seconds is not None:
            result["lockout_duration_seconds"] = env_seconds

        return result


class SessionTimeoutConfig(BaseModel):
    """Абсолютный таймаут сессии с момента входа (таймер на frontend).

    Источник: только AUTH_SESSION_TIMEOUT_SECONDS (ConfigMap / ENV пода).
    0 = отключено. Значения из config.yml не используются.
    """
    timeout_seconds: int

    @model_validator(mode="before")
    @classmethod
    def load_from_env_only(cls, data: dict) -> dict:
        env_seconds = _get_env_value_int("AUTH_SESSION_TIMEOUT_SECONDS")
        if env_seconds is None:
            raise ValueError(
                "AUTH_SESSION_TIMEOUT_SECONDS не задан. "
                "Укажите переменную в ConfigMap/Deployment контейнера astra-chat-backend."
            )
        return {"timeout_seconds": env_seconds}


class ModelHealthConfig(BaseModel):
    """Фильтрация списка LLM-моделей по health-check при list_models / /api/models/available.

    Источник (приоритет): переменные окружения ConfigMap/Deployment контейнера astra-chat-backend.
    ENV: MODEL_HEALTH_FILTER_ENABLED, MODEL_HEALTH_PROBE_MAX, MODELS_AVAILABLE_CACHE_SECONDS.
    """

    filter_enabled: bool = True
    probe_max: int = 64
    models_available_cache_seconds: int = 60

    @model_validator(mode="before")
    @classmethod
    def load_from_env(cls, data: Any) -> dict:
        result = dict(data) if isinstance(data, dict) else {}

        env_filter = _get_env_value("MODEL_HEALTH_FILTER_ENABLED")
        if env_filter is not None and str(env_filter).strip():
            result["filter_enabled"] = str(env_filter).strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
        elif "filter_enabled" not in result:
            result["filter_enabled"] = True

        env_probe = _get_env_value_int("MODEL_HEALTH_PROBE_MAX")
        if env_probe is not None:
            result["probe_max"] = max(1, env_probe)
        elif "probe_max" not in result:
            result["probe_max"] = 64

        env_cache = _get_env_value_int("MODELS_AVAILABLE_CACHE_SECONDS")
        if env_cache is not None:
            result["models_available_cache_seconds"] = max(1, env_cache)
        elif "models_available_cache_seconds" not in result:
            result["models_available_cache_seconds"] = 60

        return result


class MicroservicesEnabledConfig(BaseModel):
    """ENV перекрывает в service_toggles; без явных YAML-значений — всегда False."""

    stt: bool = False
    tts: bool = False
    diarization: bool = False
    rag: bool = False
    rag_models: bool = False
    agents: bool = False
    minio: bool = False
    postgresql: bool = False
    model_config = {"extra": "allow"}


class AuthConfig(BaseModel):
    """Настройки аутентификации"""
    login_lockout: LoginLockoutConfig = Field(default_factory=LoginLockoutConfig)
    session_timeout: SessionTimeoutConfig = Field(default_factory=SessionTimeoutConfig)
class WebSocketConfig(BaseModel):
    """Конфигурация WebSocket"""
    enabled: bool = True
    ping_interval: int = 30  # секунды
    ping_timeout: int = 10  # секунды
    max_connections: int = 100
class FilesConfig(BaseModel):
    """Конфигурация файлов"""
    upload_dir: str = "/app/uploads"
    max_file_size: int = 104857600  # 100MB
    allowed_extensions: List[str] = [".txt", ".md", ".pdf", ".docx", ".wav", ".mp3", ".mp4", ".m4a", ".flac", ".ogg"]
    temp_dir: str = "/tmp/astrachat"
def _docker_runtime() -> bool:
    de = os.getenv("DOCKER_ENV")
    if de is not None:
        return str(de).lower() == "true"
    return os.path.exists("/.dockerenv")
def _preprocess_urls_and_microservices(config_data: Dict[str, Any]) -> None:
    """Легаси-ключ diarization_docker; base_url для llm host local из urls; убрать дублирующие microservices.url."""
    urls = config_data.get("urls")
    if isinstance(urls, dict):
        if urls.get("diarization_docker") and not urls.get("diarization_service_docker"):
            urls["diarization_service_docker"] = urls["diarization_docker"]
    if not isinstance(urls, dict):
        urls = {}
    docker = _docker_runtime()
    llm_u = (
        (urls.get("llm_service_docker") or "").strip()
        if docker
        else (urls.get("llm_service_port") or urls.get("llm_service_docker") or "").strip()
    ).rstrip("/")
    # Новая схема: llm_providers[].base_url может быть пустым в YAML.
    # Для локальных OpenAI-compat/vLLM/Ollama/LiteLLM провайдеров подставляем
    # llm_service** из urls, как это давно делалось для legacy microservices.llm.hosts.
    providers = config_data.get("llm_providers")
    if isinstance(providers, list):
        local_kinds = {"llm-svc", "openai-compat", "vllm", "ollama", "litellm"}
        for p in providers:
            if not isinstance(p, dict):
                continue
            # legacy alias: llm_key -> api_key_env
            if p.get("llm_key") and not p.get("api_key_env"):
                p["api_key_env"] = p["llm_key"]
            kind = str(p.get("kind") or "").strip().lower()
            if kind in local_kinds and not str(p.get("base_url") or "").strip() and llm_u:
                p["base_url"] = llm_u
    ms = config_data.get("microservices")
    if not isinstance(ms, dict):
        return
    llm = ms.get("llm")
    if isinstance(llm, dict):
        llm.pop("url", None)
        for h in llm.get("hosts") or []:
            if isinstance(h, dict) and not (str(h.get("base_url") or "").strip()) and llm_u:
                h["base_url"] = llm_u
    for key in ("stt", "tts", "ocr", "diarization"):
        svc = ms.get(key)
        if isinstance(svc, dict):
            svc.pop("url", None)


class McpPoolConfig(BaseModel):
    """Параметры warm session pool для streamable-http MCP."""
    max_size: int = 4
    idle_ttl_seconds: int = 120
    acquire_timeout_seconds: float = 5.0


class McpForwardHeadersUserMapping(BaseModel):
    username: Optional[str] = "X-Ldap-User"
    user_id: Optional[str] = "X-User-Id"
    email: Optional[str] = "X-User-Email"


class McpForwardHeadersConfig(BaseModel):
    enabled: bool = True
    user: McpForwardHeadersUserMapping = Field(default_factory=McpForwardHeadersUserMapping)
    chat_id_header: str = "X-OpenWebUI-Chat-Id"
    message_id_header: str = "X-OpenWebUI-Message-Id"


class McpServerConfig(BaseModel):
    """Конфигурация одного MCP-сервера."""
    id: str
    display_name: str = ""
    enabled: bool = True
    transport: str = "streamable-http"
    base_url: str = ""
    base_path: str = "/mcp"
    health_path: str = "/healthz"
    stateless: bool = True
    timeout_seconds: int = 120
    credential_provider: Optional[str] = None
    tool_name_prefix: str = ""
    enabled_tools: Optional[List[str]] = None
    function_name_filter_list: Optional[List[str]] = None
    access_grants: Optional[List[str]] = None
    auth_mode: str = "service_account"
    auth_type: str = "none"
    auth_token: Optional[str] = None
    auth_header_name: Optional[str] = None
    auth_header_value: Optional[str] = None
    verify_ssl: bool = True
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    cwd: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    custom_headers: Dict[str, str] = Field(default_factory=dict)


class WorkspacePreset(BaseModel):
    """Преднастроенная папка для coding agent (задаётся в config, выбирается в UI)."""

    id: str
    label: str
    path: str
    host_hint: Optional[str] = None


class CodingAgentConfig(BaseModel):
    """Локальный coding agent (workspace tools, паттерн Odysseus)."""

    enabled: bool = True
    auto_enable_on_file_intent: bool = False
    max_rounds: int = 50
    max_tool_calls: int = 40
    bash_timeout_sec: int = 60
    python_timeout_sec: int = 60
    allowed_roots: List[str] = Field(default_factory=list)
    # host-путь (Windows) → путь внутри контейнера Docker
    path_aliases: Dict[str, str] = Field(default_factory=dict)
    # Путь по умолчанию, если в UI/проекте не задан workspace
    default_workspace: Optional[str] = None
    # Список папок для выбора в UI (без ручного ввода Docker-путей)
    workspace_presets: List[WorkspacePreset] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def load_from_env(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            data = {}
        env_enabled = os.getenv("CODING_AGENT_ENABLED")
        if env_enabled is not None:
            data["enabled"] = env_enabled.strip().lower() in ("1", "true", "yes", "on")
        for env_key, field in (
            ("CODING_AGENT_MAX_ROUNDS", "max_rounds"),
            ("CODING_AGENT_MAX_TOOL_CALLS", "max_tool_calls"),
            ("CODING_AGENT_BASH_TIMEOUT_SEC", "bash_timeout_sec"),
            ("CODING_AGENT_PYTHON_TIMEOUT_SEC", "python_timeout_sec"),
        ):
            val = os.getenv(env_key)
            if val is not None:
                try:
                    data[field] = int(val)
                except ValueError:
                    pass
        roots = os.getenv("CODING_AGENT_ALLOWED_ROOTS")
        if roots is not None:
            data["allowed_roots"] = [p.strip() for p in roots.split(os.pathsep) if p.strip()]
        return data


class McpPlatformConfig(BaseModel):
    """Глобальные настройки MCP-платформы."""
    enabled: bool = False
    servers: List[McpServerConfig] = Field(default_factory=list)
    tools_cache_ttl_seconds: int = 60
    pool: McpPoolConfig = Field(default_factory=McpPoolConfig)
    fc_task_model: str = ""
    forward_headers: McpForwardHeadersConfig = Field(default_factory=McpForwardHeadersConfig)
    session_pool_enabled: bool = True
    tool_calling_mode_default: str = "auto"
    chat_default: str = "none"
    llm_provider_allowlist: Optional[List[str]] = None

    @model_validator(mode="before")
    @classmethod
    def load_from_env(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            data = {}
        env_enabled = os.getenv("MCP_ENABLED")
        if env_enabled is not None:
            data["enabled"] = env_enabled.strip().lower() in ("1", "true", "yes", "on")
        ttl = os.getenv("MCP_TOOLS_CACHE_TTL_SECONDS")
        if ttl is not None:
            try:
                data["tools_cache_ttl_seconds"] = int(ttl)
            except ValueError:
                pass
        fc_model = os.getenv("MCP_FC_TASK_MODEL")
        if fc_model is not None:
            data["fc_task_model"] = fc_model
        mode_default = os.getenv("MCP_TOOL_CALLING_MODE_DEFAULT")
        if mode_default is not None:
            data["tool_calling_mode_default"] = mode_default.strip()
        chat_default = os.getenv("MCP_CHAT_DEFAULT")
        if chat_default is not None:
            data["chat_default"] = chat_default.strip()
        allowlist = os.getenv("MCP_LLM_PROVIDER_ALLOWLIST")
        if allowlist is not None:
            data["llm_provider_allowlist"] = [p.strip() for p in allowlist.split(",") if p.strip()]
        pool_enabled = os.getenv("MCP_SESSION_POOL_ENABLED")
        if pool_enabled is not None:
            data["session_pool_enabled"] = pool_enabled.strip().lower() in ("1", "true", "yes", "on")
        pool = data.get("pool")
        if not isinstance(pool, dict):
            pool = {}
        for env_key, field in (
            ("MCP_SESSION_POOL_SIZE", "max_size"),
            ("MCP_POOL_MAX_SIZE", "max_size"),
            ("MCP_POOL_IDLE_TTL_SECONDS", "idle_ttl_seconds"),
            ("MCP_POOL_ACQUIRE_TIMEOUT_SECONDS", "acquire_timeout_seconds"),
        ):
            val = os.getenv(env_key)
            if val is not None:
                try:
                    pool[field] = float(val) if field == "acquire_timeout_seconds" else int(val)
                except ValueError:
                    pass
        data["pool"] = pool
        fwd = os.getenv("MCP_FORWARD_USER_INFO_HEADERS")
        if fwd is not None:
            fh = data.get("forward_headers")
            if not isinstance(fh, dict):
                fh = {}
            fh["enabled"] = fwd.strip().lower() in ("1", "true", "yes", "on")
            data["forward_headers"] = fh
        chat_hdr = os.getenv("MCP_FORWARD_HEADER_CHAT_ID")
        msg_hdr = os.getenv("MCP_FORWARD_HEADER_MESSAGE_ID")
        if chat_hdr or msg_hdr:
            fh = data.get("forward_headers")
            if not isinstance(fh, dict):
                fh = {}
            if chat_hdr:
                fh["chat_id_header"] = chat_hdr
            if msg_hdr:
                fh["message_id_header"] = msg_hdr
            data["forward_headers"] = fh
        servers = data.get("servers")
        if isinstance(servers, list):
            patched = []
            for srv in servers:
                if not isinstance(srv, dict):
                    patched.append(srv)
                    continue
                sid = str(srv.get("id") or "").strip()
                if sid:
                    env_prefix = f"MCP_SERVER_{sid.upper().replace('-', '_')}_"
                    for field, env_suffix in (
                        ("transport", "TRANSPORT"),
                        ("base_url", "BASE_URL"),
                        ("base_path", "BASE_PATH"),
                        ("health_path", "HEALTH_PATH"),
                        ("credential_provider", "CREDENTIAL_PROVIDER"),
                        ("tool_name_prefix", "TOOL_NAME_PREFIX"),
                        ("auth_mode", "AUTH_MODE"),
                        ("auth_type", "AUTH_TYPE"),
                        ("auth_token", "AUTH_TOKEN"),
                        ("auth_header_name", "AUTH_HEADER_NAME"),
                        ("auth_header_value", "AUTH_HEADER_VALUE"),
                        ("command", "COMMAND"),
                        ("cwd", "CWD"),
                    ):
                        env_val = os.getenv(f"{env_prefix}{env_suffix}")
                        if env_val is not None:
                            srv[field] = env_val
                    enabled_env = os.getenv(f"{env_prefix}ENABLED")
                    if enabled_env is not None:
                        srv["enabled"] = enabled_env.strip().lower() in ("1", "true", "yes", "on")
                    timeout_env = os.getenv(f"{env_prefix}TIMEOUT_SECONDS")
                    if timeout_env is not None:
                        try:
                            srv["timeout_seconds"] = int(timeout_env)
                        except ValueError:
                            pass
                    # В Docker нет npx/node — websearch подключается по HTTP к контейнеру mcp-websearch
                    if sid == "websearch" and _docker_runtime():
                        if os.getenv(f"{env_prefix}TRANSPORT") is None:
                            if str(srv.get("transport") or "").strip() == "stdio":
                                srv["transport"] = "streamable-http"
                                srv.setdefault("base_path", "/mcp")
                                srv.setdefault("health_path", "/mcp")
                patched.append(srv)
            data["servers"] = patched
        return data


class PluginConfig(BaseModel):
    """Один HTTP-плагин из каталога (галерея плагинов)."""

    id: str
    display_name: str = ""
    description: str = ""
    enabled: bool = True
    kind: str = "http"
    category: str = ""
    tags: List[str] = Field(default_factory=list)
    base_url: str = ""
    health_path: str = "/health"
    invoke_path: str = "/audit"
    timeout_seconds: int = 600
    invoke_file_field: str = "model_file"
    invoke_optional_file_field: str = "quality_file"
    invoke_prompt_field: str = "prompt"


class PluginsPlatformConfig(BaseModel):
    """Каталог HTTP-плагинов (cash-flow и др.)."""

    enabled: bool = True
    catalog: List[PluginConfig] = Field(default_factory=list)

    @staticmethod
    def _normalize_catalog(raw: Any) -> list[dict]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [dict(item) for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            items: list[dict] = []
            for pid, cfg in raw.items():
                if isinstance(cfg, dict):
                    item = dict(cfg)
                elif cfg is None:
                    item = {}
                else:
                    item = {"base_url": str(cfg)}
                item.setdefault("id", str(pid))
                items.append(item)
            return items
        return []

    @staticmethod
    def _env_timeout_seconds(plugin_id: str) -> Optional[int]:
        """PLUGIN_<ID>_TIMEOUT_SECONDS, иначе PLUGINS_DEFAULT_TIMEOUT_SECONDS."""
        keys = []
        slug = re.sub(r"[^A-Za-z0-9]+", "_", plugin_id).strip("_").upper()
        if slug:
            keys.append(f"PLUGIN_{slug}_TIMEOUT_SECONDS")
        keys.append("PLUGINS_DEFAULT_TIMEOUT_SECONDS")
        for key in keys:
            raw = os.getenv(key)
            if raw is None:
                continue
            try:
                value = int(str(raw).strip())
            except ValueError:
                continue
            if value > 0:
                return value
        return None

    @model_validator(mode="before")
    @classmethod
    def load_from_env(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            data = {}
        env_enabled = os.getenv("PLUGINS_ENABLED")
        if env_enabled is not None:
            data["enabled"] = env_enabled.strip().lower() in ("1", "true", "yes", "on")
        raw_catalog = data.get("catalog")
        if raw_catalog is None:
            raw_catalog = data.get("plugins")
        if raw_catalog is None:
            raw_catalog = data.get("items")
        normalized = cls._normalize_catalog(raw_catalog)
        for item in normalized:
            sid = str(item.get("id") or "").strip()
            item["id"] = sid
            if not str(item.get("display_name") or "").strip():
                item["display_name"] = sid.replace("-", " ").replace("_", " ").title()
            tags = item.get("tags")
            if isinstance(tags, str):
                item["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            elif not isinstance(tags, list):
                item["tags"] = []
            env_timeout = cls._env_timeout_seconds(sid)
            if env_timeout is not None:
                item["timeout_seconds"] = env_timeout
        data["catalog"] = normalized
        return data


class UrlsConfig(BaseModel):
    """Конфигурация URL адресов
    Все значения должны быть заданы в YAML или ENV
    """
    # Адреса
    frontend_port: str
    backend_port: str
    ingress_port: str
    llm_service_port: str
    mcp_atlassian_service_port: Optional[str] = None
    mcp_atlassian_service_docker: Optional[str] = None
    cash_flow_service_port: Optional[str] = None
    cash_flow_service_docker: Optional[str] = None
    model_config = {"extra": "allow"}
    # =============== Когда будем подрубать остальные микросервисы, прочекать эти моменты ===============
    # llm_service_docker: Optional[str] = None
    # stt_service_docker: Optional[str] = None
    # stt_service_port: Optional[str] = None
    # tts_service_docker: Optional[str] = None
    # tts_service_port: Optional[str] = None
    # ocr_service_docker: Optional[str] = None
    # ocr_service_port: Optional[str] = None
    # diarization_service_docker: Optional[str] = None
    # diarization_service_port: Optional[str] = None
    # rag_service_docker: Optional[str] = None
    # rag_service_port: Optional[str] = None
    # rag_models_service_docker: Optional[str] = None
    # rag_models_service_port: Optional[str] = None
    @model_validator(mode="before")
    @classmethod
    def load_from_yaml_or_env(cls, data: dict) -> dict:
        """Загружает значения из YAML или ENV (приоритет: YAML > ENV)"""
        if not isinstance(data, dict):
            data = {}
        result = dict(data)
        required_keys = [
            "ingress_port", "frontend_port",
            "backend_port", "llm_service_port",
        ]
        for key in required_keys:
            if key in data:
                result[key] = data[key]
            else:
                # Пробуем получить из ENV (например, FRONTEND_PORT, BACKEND_PORT и т.д.)
                env_key = key.upper()
                env_value = os.getenv(env_key)
                if env_value is None:
                    raise ValueError(f"{key} не задан в YAML (urls.{key}) или ENV ({env_key})")
                result[key] = env_value
        return result
class Settings(BaseModel):
    """Основной класс настроек приложения"""
    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    cors: CorsConfig = Field(default_factory=CorsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)
    files: FilesConfig = Field(default_factory=FilesConfig)
    urls: UrlsConfig = Field(default_factory=UrlsConfig)
    # Подключения к внешним сервисам
    mongodb: MongoDBConnectionConfig = Field(default_factory=MongoDBConnectionConfig)
    postgresql: PostgreSQLConnectionConfig = Field(default_factory=PostgreSQLConnectionConfig)
    minio: MinIOConnectionConfig = Field(default_factory=MinIOConnectionConfig)
    llm_service: LLMServiceConnectionConfig = Field(default_factory=LLMServiceConnectionConfig)
    llm_providers: List[Dict[str, Any]] = Field(default_factory=list)
    default_llm_provider: Optional[str] = None
    model_health: ModelHealthConfig = Field(default_factory=ModelHealthConfig)
    mcp: McpPlatformConfig = Field(default_factory=McpPlatformConfig)
    plugins: PluginsPlatformConfig = Field(default_factory=PluginsPlatformConfig)
    microservices_enabled: MicroservicesEnabledConfig = Field(
        default_factory=MicroservicesEnabledConfig
    )  # FEATURE-FLAG
    coding_agent: CodingAgentConfig = Field(default_factory=CodingAgentConfig)
    image_generation: ImageGenerationConfig = Field(default_factory=ImageGenerationConfig)
    video_generation: VideoGenerationConfig = Field(default_factory=VideoGenerationConfig)
    class Config:
        """Настройки Pydantic"""
        extra = "allow"  # Разрешаем дополнительные поля из YAML
    def microservice_http_base(self, docker_field: str, port_field: str) -> str:
        """Базовый URL микросервиса: в Docker — *_docker, с хоста — *_port (или fallback _docker)."""
        u = self.urls
        if _docker_runtime():
            v = getattr(u, docker_field, None) or getattr(u, port_field, None)
        else:
            v = getattr(u, port_field, None) or getattr(u, docker_field, None)
        if not v or not str(v).strip():
            raise ValueError(
                f"Задайте urls.{docker_field} и urls.{port_field} в backend/config/config.yml"
            )
        return str(v).strip().rstrip("/")

    def resolve_mcp_server_base_url(self, server_id: str) -> str:
        """Базовый URL MCP-сервера: base_url из конфига или urls.mcp_{id}_service_*."""
        sid = str(server_id or "").strip()
        if not sid:
            raise ValueError("server_id обязателен")
        for srv in self.mcp.servers:
            if srv.id == sid and str(srv.base_url or "").strip():
                return str(srv.base_url).strip().rstrip("/")
        key = f"mcp_{sid.replace('-', '_')}_service"
        return self.microservice_http_base(f"{key}_docker", f"{key}_port")

    def get_mcp_server_config(self, server_id: str) -> Optional[McpServerConfig]:
        sid = str(server_id or "").strip()
        for srv in self.mcp.servers:
            if srv.id == sid:
                return srv
        return None

    def resolve_plugin_base_url(self, plugin_id: str) -> str:
        """Базовый URL плагина: base_url из конфига или urls.{id}_service_*."""
        pid = str(plugin_id or "").strip()
        if not pid:
            raise ValueError("plugin_id обязателен")
        for plugin in self.plugins.catalog:
            if plugin.id == pid and str(plugin.base_url or "").strip():
                return str(plugin.base_url).strip().rstrip("/")
        key = pid.replace("-", "_")
        if key == "cash_flow":
            return self.microservice_http_base("cash_flow_service_docker", "cash_flow_service_port")
        return self.microservice_http_base(f"{key}_service_docker", f"{key}_service_port")

    def get_plugin_config(self, plugin_id: str) -> Optional[PluginConfig]:
        pid = str(plugin_id or "").strip()
        for plugin in self.plugins.catalog:
            if plugin.id == pid:
                return plugin
        return None

    @classmethod
    def from_yaml(cls, config_path: Optional[str] = None) -> 'Settings':
        """
        Загрузка конфигурации из YAML файла
        Args:
            config_path: Путь к файлу конфигурации. Если None, ищет в стандартных местах.
        Returns:
            Экземпляр Settings с загруженной конфигурацией
        """
        if config_path is None:
            # Поиск config.yml в различных возможных местах
            possible_paths = [
                Path(__file__).parent.parent / "config" / "config.yml",  # backend/config/config.yml
                Path(__file__).parent.parent.parent / "backend" / "config" / "config.yml",  # из корня проекта
                "config/config.yml",
                "../config/config.yml",
                "./config.yml",
            ]
            for path in possible_paths:
                path_obj = Path(path) if isinstance(path, str) else path
                if path_obj.exists():
                    config_path = str(path_obj.absolute())
                    break
            else:
                # Если файл не найден, используем значения по умолчанию
                return cls()
        else:
            # Если CONFIG_PATH задан, проверяем существование файла
            if not os.path.exists(config_path):
                # Если файл не найден по указанному пути, пробуем найти в стандартных местах
                possible_paths = [
                    Path(__file__).parent.parent / "config" / "config.yml",
                    Path(__file__).parent.parent.parent / "backend" / "config" / "config.yml",
                ]
                for path in possible_paths:
                    if path.exists():
                        config_path = str(path.absolute())
                        break
                else:
                    # Если файл не найден, используем значения по умолчанию
                    return cls()
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f) or {}
            _preprocess_urls_and_microservices(config_data)
            # Подготавливаем данные для создания Settings
            # Классы подключений сами загрузят значения из env через model_validator,
            # если они не заданы в YAML
            settings_data = {}
            # Копируем все секции из YAML
            for key, value in config_data.items():
                if key in ["mongodb", "postgresql", "minio", "llm_service"]:
                    # Для секций подключений передаем данные как есть (может быть пустым dict)
                    # model_validator в классах подключений сам загрузит из env, если секция пустая
                    settings_data[key] = value if value is not None else {}
                else:
                    settings_data[key] = value
            # Если секций подключений нет в YAML, создаем пустые dict для загрузки из ENV
            if "mongodb" not in settings_data:
                settings_data["mongodb"] = {}
            if "postgresql" not in settings_data:
                settings_data["postgresql"] = {}
            if "minio" not in settings_data:
                settings_data["minio"] = {}
            if "llm_service" not in settings_data:
                settings_data["llm_service"] = {}
            has_new_llm_providers = bool(
                isinstance(config_data.get("llm_providers"), list)
                and len(config_data.get("llm_providers") or []) > 0
            )
            # Обработка LLM Service - может быть в секции microservices.llm_svc
            if "microservices" in config_data and "llm_svc" in config_data["microservices"]:
                llm_svc_data = config_data["microservices"]["llm_svc"]
                # Объединяем данные из microservices.llm_svc с данными из llm_service
                if isinstance(llm_svc_data, dict):
                    # Копируем настройки из microservices.llm_svc в llm_service
                    for key, value in llm_svc_data.items():
                        if key not in settings_data["llm_service"]:
                            settings_data["llm_service"][key] = value
            # microservices.llm (config.yml) — алиас к llm_service
            if "microservices" in config_data and "llm" in config_data.get("microservices", {}):
                llm_ms = config_data["microservices"]["llm"]
                if isinstance(llm_ms, dict):
                    if "timeout" in llm_ms and "timeout" not in settings_data["llm_service"]:
                        settings_data["llm_service"]["timeout"] = llm_ms["timeout"]
                    models_block = llm_ms.get("models")
                    if isinstance(models_block, dict):
                        if models_block.get("default") and "default_model" not in settings_data["llm_service"]:
                            settings_data["llm_service"]["default_model"] = models_block["default"]
                        if models_block.get("fallback") is not None and "fallback_model" not in settings_data["llm_service"]:
                            settings_data["llm_service"]["fallback_model"] = models_block["fallback"]
                    hosts_raw = llm_ms.get("hosts")
                    if isinstance(hosts_raw, list) and hosts_raw and "hosts" not in settings_data["llm_service"]:
                        parsed = []
                        for h in hosts_raw:
                            if isinstance(h, dict) and h.get("id") and h.get("base_url"):
                                parsed.append(LLMHostEntry(id=str(h["id"]), base_url=str(h["base_url"]).rstrip("/")))
                        if parsed:
                            settings_data["llm_service"]["hosts"] = [e.model_dump() for e in parsed]
                    if llm_ms.get("default_host_id") and "default_host_id" not in settings_data["llm_service"]:
                        settings_data["llm_service"]["default_host_id"] = str(llm_ms["default_host_id"])
            if has_new_llm_providers and not settings_data["llm_service"]:
                # Новая схема ProviderRegistry не обязана тащить legacy llm-svc поля.
                # Передаём внутренний флаг, чтобы LLMServiceConnectionConfig применил
                # безопасные дефолты вместо обязательной валидации legacy-конфига.
                settings_data["llm_service"]["_allow_empty_legacy"] = True
            # Создаем экземпляр Settings
            settings = cls(**settings_data)
            # Обновляем URL для LLM Service из секции urls (Docker → *_docker, хост → *_port)
            if config_data.get("urls"):
                try:
                    llm_url = settings.microservice_http_base("llm_service_docker", "llm_service_port")
                    settings.llm_service.base_url = llm_url
                    ext = (config_data["urls"].get("llm_service_port") or "").strip().rstrip("/")
                    if ext:
                        settings.llm_service.external_url = ext
                except ValueError:
                    pass
            # Если в config.yml allowed_origins пустой массив, заполняем автоматически из urls
            cors_config = config_data.get("cors", {})
            cors_allowed_origins_from_config = cors_config.get("allowed_origins", [])
            # Если allowed_origins не указан или пустой, заполняем из urls
            if config_data.get("urls") and (not cors_allowed_origins_from_config or len(cors_allowed_origins_from_config) == 0):
                urls = config_data["urls"]
                cors_origins = [
                    urls.get("ingress_port", ""),
                    urls.get("frontend_port", ""),
                ]
                # Фильтруем пустые значения
                cors_origins = [origin for origin in cors_origins if origin]
                if cors_origins:
                    settings.cors.allowed_origins = cors_origins
            # Если allowed_origins явно указан, то применяем его
            elif cors_allowed_origins_from_config:
                settings.cors.allowed_origins = cors_allowed_origins_from_config
            return settings
        except Exception as e:
            raise ValueError(f"Ошибка загрузки конфигурации из {config_path}: {str(e)}")
    def get_llm_service_url(self) -> str:
        """URL LLM для текущего окружения (см. urls.llm_service в config.yml)."""
        return self.microservice_http_base("llm_service_docker", "llm_service_port")
def get_settings() -> Settings:
    """
    Получение глобального экземпляра настроек (singleton)
    Returns:
        Экземпляр Settings
    """
    global _settings
    if _settings is None:
        config_path = os.environ.get("CONFIG_PATH")
        _settings = Settings.from_yaml(config_path)
    return _settings
def reset_settings() -> Settings:
    """
    Сброс и принудительная перезагрузка настроек
    Returns:
        Новый экземпляр Settings
    """
    global _settings
    config_path = os.environ.get("CONFIG_PATH")
    _settings = Settings.from_yaml(config_path)
    _clear_dependent_caches()
    return _settings


def _clear_dependent_caches() -> None:
    """Сбрасывает кэши модулей, завязанных на ENV/конфигурацию (роли платформы)."""
    try:
        from backend.auth.agent_platform_roles import clear_role_cache

        clear_role_cache()
    except Exception:
        # Сброс кэша не должен ломать перезагрузку конфига.
        pass


# Инициализация настроек при импорте модуля (ленивая)
# Не инициализируем сразу, чтобы избежать ошибок при импорте
settings = None
def _init_settings():
    """Ленивая инициализация настроек"""
    global settings
    if settings is None:
        settings = get_settings()
    return settings