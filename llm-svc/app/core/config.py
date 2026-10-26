import logging
import yaml
import os
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple, Type
from pydantic import BaseModel, model_validator, ConfigDict
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, SettingsConfigDict, PydanticBaseSettingsSource
logger = logging.getLogger(__name__)
_settings = None
class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    docs_url: str = "/docs"
    redoc_url: str = "/redoc"
class UrlsConfig(BaseModel):
    """Публичные URL (как у backend/frontend); CORS собирается из непустых полей."""
    model_config = ConfigDict(extra="ignore")

    frontend_port: Optional[str] = None
    frontend_port_ipv4: Optional[str] = None
    frontend_port_2: Optional[str] = None
    frontend_port_2_ipv4: Optional[str] = None
    frontend_port_3: Optional[str] = None
    frontend_port_3_ipv4: Optional[str] = None
    backend_port: Optional[str] = None
    backend_port_ipv4: Optional[str] = None
    backend_port_2: Optional[str] = None
    backend_port_2_ipv4: Optional[str] = None


class CorsConfig(BaseModel):
    allowed_origins: List[str] = []
    allow_credentials: bool = True
    allow_methods: List[str] = ["*"]
    allow_headers: List[str] = ["*"]
class ModelConfig(BaseModel):
    path: str = "/dev/null"
    name: str = "model"
    backend: str = "llama.cpp"  # llama.cpp или vllm
    chat_template_path: str = None
    chat_format: str = None
    ctx_size: int = 4096
    gpu_layers: int = 0
    verbose: bool = False
    # llama.cpp pool параметры
    pool_size: int = 1
    n_thread: int = 4
    n_threads_batch: int = 4
    n_batch: int = 512
    n_ubatch: int = 512
    # vLLM параметры
    tensor_parallel_size: int = 1
    gpu_memory_utilization: float = 0.9
    trust_remote_code: bool = False
    quantization: Optional[str] = None  # awq, gptq, fp8 или null
class GenerationConfig(BaseModel):
    default_temperature: float = 0.7
    default_max_tokens: int = 256
    stream: bool = False
class AppConfig(BaseModel):
    title: str = "Llama CPP API Service"
    description: str = "API service for Llama-based models compatible with OpenAI API"
    version: str = "1.0.0"
class MonitoringConfig(BaseModel):
    enabled: bool = False
    prometheus_port: int = 9090
    metrics_path: str = "/metrics"
class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Optional[str] = None
    max_size: int = 10485760
    backup_count: int = 5
class CachingConfig(BaseModel):
    enabled: bool = True
    ttl: int = 300
    max_size: int = 1000
class VaultConfig(BaseModel):
    role_id: Optional[str] = None
    approle_name: Optional[str] = None
    verify: Optional[bool] = True
    tuz_username: Optional[str] = None
    tuz_password: Optional[str] = None
    cert_path: Optional[str] = None
    path: Optional[str] = None
class SecurityConfig(BaseModel):
    enabled: bool = True
    api_key_header: str = "X-API-Key"
class NexusConfig(BaseModel):
    enabled: bool = False
    url: str = ""
    username: Optional[str] = None
    password: Optional[str] = None
    cert_path: Optional[str] = None
class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: Type[BaseSettings], config_path: str):
        super().__init__(settings_cls)
        self.config_path = config_path
        self._data: Dict[str, Any] | None = None
    def _load_yaml(self) -> Dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.config_path or not os.path.exists(self.config_path):
            logger.warning(f"Configuration file (.yaml) not found at {self.config_path}")
            self._data = {}
            return self._data
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                self._data = data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"Error loading YAML config from {self.config_path}: {e}")
            self._data = {}
        return self._data
    def __call__(self) -> Dict[str, Any]:
        return self._load_yaml()
    def get_field_value(self, field: "FieldInfo", field_name: str) -> tuple[Any, str, bool]:
        data = self._load_yaml()
        if field_name not in data:
            return None, "", False
        value = data[field_name]
        return value, "yaml", True
    def prepare_field_value(self, field_name: str, field: "FieldInfo", value: Any, value_is_complex: bool) -> Any:
        return value
def _cors_keys_from_urls() -> Tuple[str, ...]:
    return (
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


class Settings(BaseSettings):
    server: ServerConfig = ServerConfig()
    cors: CorsConfig = CorsConfig()
    urls: Optional[UrlsConfig] = None
    model: ModelConfig = ModelConfig()
    generation: GenerationConfig = GenerationConfig()
    app: AppConfig = AppConfig()
    monitoring: MonitoringConfig = MonitoringConfig()
    logging: LoggingConfig = LoggingConfig()
    caching: CachingConfig = CachingConfig()
    vault: VaultConfig = VaultConfig()
    security: SecurityConfig = SecurityConfig()
    nexus: NexusConfig = NexusConfig()
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        extra="ignore",
        env_file=None
    )

    @model_validator(mode="after")
    def _cors_from_urls(self) -> "Settings":
        if self.urls is None or self.cors.allowed_origins:
            return self
        origins: List[str] = []
        for k in _cors_keys_from_urls():
            v = getattr(self.urls, k, None)
            if v and str(v).strip():
                origins.append(str(v).strip())
        if origins:
            self.cors.allowed_origins = origins
        return self

    @classmethod
    def settings_customise_sources(
            cls,
            settings_cls: Type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings:PydanticBaseSettingsSource
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """
        Определение приоритета источников (от самого важного к менее важному)
        1. ENV - самый высокий приоритет
        2. YAML - основной источник конфигов
        3. Значения по умолчанию, вшитые в классы - в случае если не указано в других источниках
        """
        config_path = _get_config_path_strategy()
        return (
            env_settings,
            YamlConfigSettingsSource(settings_cls, config_path),
            init_settings
        )
    @classmethod
    def from_yaml(cls, config_path: Optional[str] = None) -> "Settings":
        """Загрузка конфигурации из YAML файла"""
        if config_path:
            global _MANUAL_CONFIG_PATH
            _MANUAL_CONFIG_PATH = config_path
        return cls()
_MANUAL_CONFIG_PATH: Optional[str] = None
def _get_config_path_strategy() -> str:
    """Поиск файла конфигурации."""
    # Если задан вручную через from_yaml
    if _MANUAL_CONFIG_PATH:
        return  _MANUAL_CONFIG_PATH
    # Если задан в переменной окружения
    env_path = os.environ.get("CONFIG_PATH")
    if env_path:
        return env_path
    app_env = os.environ.get("CURRENT_ENV", "dev").lower()
    config_filename = f"config.{app_env}.yml"
    # Стандартные пути поиска конфига
    possible_paths = [
        "config/config.yml",
        "../config/config.yml",
        "./config.yml",
        str(Path(__file__).parent.parent.parent / "config" / config_filename)
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    # Конфигурационный файл не найден
    logger.warning(f"Configuration file (.yaml) not found.")
    return ""
def get_settings() -> Settings:
    """Получение экземпляра настроек."""
    global _settings
    if _settings is None:
        _settings = Settings.from_yaml()
    return _settings
def reset_settings() -> Settings:
    """Сброс и принудительная перезагрузка настроек."""
    global _settings, _MANUAL_CONFIG_PATH
    _settings = None
    _MANUAL_CONFIG_PATH = None
    return get_settings()
# Инициализация настроек
settings = get_settings()
