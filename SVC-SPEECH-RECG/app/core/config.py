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


def _apply_urls_cors(config_data: dict) -> dict:
    urls = config_data.get("urls")
    if not isinstance(urls, dict):
        return config_data
    out = dict(config_data)
    cors = dict(out.get("cors") or {})
    ao = cors.get("allowed_origins")
    if not ao or ao == ["*"]:
        merged = [str(urls[k]).strip() for k in _URLS_CORS_KEYS if urls.get(k) and str(urls[k]).strip()]
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


class ModelConfig(BaseModel):
    path: str = os.environ.get("LLM_MODEL_PATH", "/app/models/llama-2-7b-chat.Q4_K_M.gguf")
    name: str = os.environ.get("LLM_MODEL_NAME", "llama-2-7b-chat")
    mmproj_path: Optional[str] = os.environ.get("LLM_MMPROJ_PATH", None)  # Путь к multimodal projection для VL моделей
    backend: str = os.environ.get("LLM_BACKEND", "llama.cpp")  # llama.cpp или vllm
    ctx_size: int = 4096
    gpu_layers: int = 0
    verbose: bool = False
    # Параметры для vLLM
    tensor_parallel_size: int = 1
    gpu_memory_utilization: float = 0.9
    trust_remote_code: bool = False
    quantization: Optional[str] = None  # Тип квантования: "awq", "gptq", "fp8" или None для автоматического определения


class GenerationConfig(BaseModel):
    default_temperature: float = 0.7
    default_max_tokens: int = 256
    stream: bool = False


class AppConfig(BaseModel):
    title: str = "AI Models API Service"
    description: str = "Unified API service for LLM, Speech Recognition (WhisperX) and Text-to-Speech (Silero) models"
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


class SecurityConfig(BaseModel):
    enabled: bool = True
    api_key: Optional[str] = None
    api_key_header: str = "X-API-Key"


class SileroConfig(BaseModel):
    enabled: bool = True
    models_dir: str = os.environ.get("SILERO_MODELS_DIR", "/app/models/silero")
    sample_rate: int = 48000
    max_text_length: int = 5000
    supported_languages: List[str] = ["ru", "en"]
    supported_speakers: dict = {
        "ru": ["baya", "kseniya", "xenia", "eugene", "aidar"],
        "en": ["v3_en"]
    }


class WhisperXConfig(BaseModel):
    enabled: bool = True
    models_dir: str = os.environ.get("WHISPERX_MODELS_DIR", "/app/models/whisperx")
    model_size: str = os.environ.get("WHISPERX_MODEL_SIZE", "medium")
    device: str = "cpu"  # cpu, cuda, auto
    compute_type: str = "float16"  # float16, int8, int8_float16
    language: str = "ru"
    batch_size: int = 16
    max_file_size: int = 5 * 1024 * 1024 * 1024  # 5GB
    supported_languages: List[str] = ["ru", "en", "auto"]


class DiarizationConfig(BaseModel):
    enabled: bool = True
    models_dir: str = os.environ.get("DIARIZATION_MODELS_DIR", "/app/models/diarization")
    config_path: str = os.environ.get("DIARIZATION_CONFIG_PATH", "/app/models/diarization/pyannote_diarization_config.yaml")
    device: str = "cpu"  # cpu, cuda, auto
    min_speakers: int = 1
    max_speakers: int = 10
    min_duration: float = 1.0  # секунды
    max_file_size: int = 5 * 1024 * 1024 * 1024  # 5GB


class SuryaConfig(BaseModel):
    enabled: bool = True
    models_dir: str = os.environ.get("SURYA_MODELS_DIR", "/app/models/surya")
    device: str = "cpu"  # cpu, cuda, auto
    max_file_size: int = 50 * 1024 * 1024  # 50MB
    supported_languages: List[str] = ["ru", "en", "hi", "es", "fr", "de", "it", "pt", "vi", "tr", "ar", "zh", "ja", "ko", "th", "ur", "fa", "ta", "te", "ml", "kn", "gu", "pa", "bn", "or", "as", "ne", "mr", "sa", "my", "ka", "si", "km", "lo", "bo", "dz", "ti", "am", "sw", "zu", "xh", "af", "sq", "az", "eu", "be", "bs", "bg", "ca", "hr", "cs", "da", "nl", "et", "fi", "gl", "el", "he", "hu", "is", "id", "ga", "kk", "ky", "lv", "lt", "lb", "mk", "ms", "mt", "mn", "no", "pl", "ro", "sr", "sk", "sl", "sv", "tg", "tk", "uk", "uz", "cy", "yi", "yo", "zu"]


class NexusConfig(BaseModel):
    enabled: bool = False
    url: str = ""
    repo: str = ""
    name: str = ""
    id: str = ""
    version: str = ""
    file_name: str = ""
    login: Optional[str] = None
    password: Optional[str] = None
    cert_path: Optional[str] = None

    def __init__(self, **data):
        # Инициализация параметров аутентификации из переменных окружения, если они не заданы в конфиге
        if 'login' not in data or data['login'] is None:
            data['login'] = os.environ.get('NEXUS_LOGIN')
        if 'password' not in data or data['password'] is None:
            data['password'] = os.environ.get('NEXUS_PASSWORD')
        if 'cert_path' not in data or data['cert_path'] is None:
            data['cert_path'] = os.environ.get('TLS_CERT_PATH')
        super().__init__(**data)


class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    cors: CorsConfig = CorsConfig()
    model: ModelConfig = ModelConfig()
    generation: GenerationConfig = GenerationConfig()
    app: AppConfig = AppConfig()
    monitoring: MonitoringConfig = MonitoringConfig()
    logging: LoggingConfig = LoggingConfig()
    caching: CachingConfig = CachingConfig()
    security: SecurityConfig = SecurityConfig()
    silero: SileroConfig = SileroConfig()
    whisperx: WhisperXConfig = WhisperXConfig()
    diarization: DiarizationConfig = DiarizationConfig()
    surya: SuryaConfig = SuryaConfig()
    nexus: NexusConfig = NexusConfig()

    @classmethod
    def from_yaml(cls, config_path: str = None):
        """Загрузка конфигурации из YAML файла"""
        if config_path is None:
            # Поиск config.yml в различных возможных местах
            possible_paths = [
                "config/config.yml",
                "../config/config.yml",
                "./config.yml",
                Path(__file__).parent.parent.parent / "config" / "config.yml"
            ]

            for path in possible_paths:
                if os.path.exists(path):
                    config_path = path
                    break
            else:
                # Если файл не найден, используем значения по умолчанию
                # Просто возвращаем класс, поля инициализируются автоматически
                return cls()

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
                if config_data is None:
                    config_data = {}
                return cls(**_apply_urls_cors(config_data))
        except Exception as e:
            raise ValueError(f"Error loading config from {config_path}: {str(e)}")


def get_settings() -> Settings:
    """Получение экземпляра настроек."""
    global _settings
    if _settings is None:
        config_path = os.environ.get("CONFIG_PATH")
        _settings = Settings.from_yaml(config_path)
    return _settings

def reset_settings() -> Settings:
    """Сброс и принудительная перезагрузка настроек."""
    global _settings, settings
    config_path = os.environ.get("CONFIG_PATH")
    _settings = Settings.from_yaml(config_path)
    settings = _settings  # Обновляем глобальную переменную
    return _settings

# Инициализация настроек
settings = get_settings()