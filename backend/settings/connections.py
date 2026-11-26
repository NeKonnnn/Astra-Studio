from pydantic import BaseModel, Field, model_validator
from typing import Optional, Dict, List, Union
import os
from urllib.parse import quote_plus
from backend.settings.logging import get_logger
logger = get_logger(__name__)
#os_env: dict = {}
def _extra_fields(data: dict, known: tuple) -> dict:
    """Возвращает неизвестные валидатору ключи (для ``extra="allow"``).

    Валидаторы ``mode="before"`` собирают результат заново и иначе теряли бы
    все поля, которые они не разбирают явно (например ``hosts``).
    """
    return {k: v for k, v in data.items() if k not in known}


# os_env: dict = {}

def _load_envs() -> Dict:
    """Method load system environment to local dictionary _os_env"""
    return {x.lower(): os.environ[x] for x in os.environ.keys()}
    #if os_env.__len__() == 0:
    #    logger.info("Loading system environments")
    #    os_env = {x.lower(): os.environ[x] for x in os.environ.keys()}
    #    logger.info("__os_envs= {os_env}")
def _get_env_value(key: str) -> Optional[str]:
    """Получает значение из переменной окружения или None"""
    logger.debug(f"_get_env_value: {key}")
    os_env = _load_envs()
    value = os_env.get(key.lower())  # .get() вместо [] — не падает если ключа нет
    # Дополнительное логирование для LLM_API_KEY (чтобы видеть, вытягивается ли из env/Kubernetes)
    if key == "LLM_API_KEY":
        if value:
            logger.info(f"[ENV] LLM_API_KEY вытягивается из переменных окружения: да (длина={len(value)})")
        else:
            logger.warning("[ENV] LLM_API_KEY вытягивается из переменных окружения: нет (переменная не задана или пустая)")
    return value
def _get_env_value_int(key: str) -> Optional[int]:
    """Получает int значение из переменной окружения или None"""
    logger.debug(f"_get_env_value_int: {key}")
    value = _get_env_value(key)
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        logger.warning("[ENV] %s: не удалось разобрать целое число из %r", key, value)
        return None
class MongoDBConnectionConfig(BaseModel):
    """Конфигурация подключения к MongoDB
    Значения загружаются из YAML или ENV, с дефолтами для надёжности
    """

    host: str = ""
    port: int = 0
    database: str = ""
    user: Optional[str] = None
    password: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def load_from_yaml_or_env(cls, data: dict) -> dict:
        """Загружает значения из YAML или ENV (приоритет: YAML > ENV > de,faults)"""
        if not isinstance(data, dict):
            data = {}
        result = _extra_fields(
            data, ("host", "port", "database", "user", "password", "conection_string")
        )
        env_host = _get_env_value("MONGODB_HOST")
        if env_host:
            result["host"] = env_host
        elif "host" in data and data["host"]:
            result["host"] = data["host"]
        else:
            result["host"] = "mongodb"
        env_port = _get_env_value_int("MONGODB_PORT")
        if env_port:
            result["port"] = env_port
        elif "port" in data and data["port"]:
            result["port"] = data["port"]
        else:
            result["port"] = 27017
        env_database = _get_env_value("MONGODB_DATABASE")
        if env_database:
            result["database"] = env_database
        elif "database" in data and data["database"]:
            result["database"] = data["database"]
        else:
            result["database"] = "astrachat"
        # User (приоритет: ENV > YAML > None)
        env_user = _get_env_value("MONGODB_USER")
        if env_user:
            env_user = env_user.strip()
            if env_user.startswith("#"):
                env_user = None
        if env_user:
            user = env_user
        elif "user" in data and data["user"]:
            user = data["user"]
        else:
            user = None
        if user:
            result["user"] = user
        # Password (приоритет: ENV > YAML > None)
        env_password = _get_env_value("MONGODB_PASSWORD")
        if env_password:
            env_password = env_password.strip()
            if env_password.startswith("#"):
                env_password = None
        if env_password:
            password = env_password
        elif "password" in data and data["password"]:
            password = data["password"]
        else:
            password = None
        if password:
            result["password"] = password
        result.pop("conection_string", None)
        return result

    def _build_connection_string(self) -> str:
        """Формирует строку подключения с экранированием user/password по RFC 3986 (для паролей с %, #, @ и т.д.)."""
        user = str(self.user or "").strip()
        password = str(self.password or "").strip()
        if user and password:
            safe_user = quote_plus(user)
            safe_password = quote_plus(password)
            return f"mongodb://{safe_user}:{safe_password}@{self.host}:{self.port}/{self.database}"
        return f"mongodb://{self.host}:{self.port}/{self.database}"

    @property
    def connection_string(self) -> str:
        """Строка подключения к MongoDB (через _build_connection_string будет экранирование)"""
        return self._build_connection_string()

    class Config:
        """Настройки Pydantic"""

        extra = "allow"  # Разрешаем дополнительные поля из YAML


def _env_or_yaml(
    data: dict,
    field: str,
    env_keys: Union[str, List[str]],
    *,
    required: bool = True,
    as_int: bool = False,
    as_bool: bool = False,
) -> Optional[Union[str, int, bool]]:
    """Секреты и runtime-настройки: ENV перекрывает YAML (Docker/.env > config.yml)."""
    keys = [env_keys] if isinstance(env_keys, str) else list(env_keys)
    for key in keys:
        raw = _get_env_value_int(key) if as_int else _get_env_value(key)
        if raw is None:
            continue
        if as_bool:
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
        return raw
    if field in data and data[field] is not None and data[field] != "":
        val = data[field]
        if as_bool and not isinstance(val, bool):
            return str(val).strip().lower() in {"1", "true", "yes", "on"}
        return val
    if required:
        raise ValueError(f"{keys[0]} не задан в ENV или YAML ({field})")
    return None


class PostgreSQLConnectionConfig(BaseModel):
    """Конфигурация подключения к PostgreSQL
    Все значения должны быть заданы в YAML или ENV
    """

    host: str
    port: int
    database: str
    user: str
    password: str
    embedding_dim: int

    @model_validator(mode="before")
    @classmethod
    def load_from_yaml_or_env(cls, data: dict) -> dict:
        """Загружает значения из YAML или ENV (секреты: ENV > YAML)."""
        if not isinstance(data, dict):
            data = {}
        result = _extra_fields(
            data, ("host", "port", "database", "user", "password", "embedding_dim")
        )
        result["host"] = _env_or_yaml(data, "host", "POSTGRES_HOST")
        result["port"] = _env_or_yaml(data, "port", "POSTGRES_PORT", as_int=True)
        result["database"] = _env_or_yaml(data, "database", "POSTGRES_DB")
        result["user"] = _env_or_yaml(data, "user", "POSTGRES_USER")
        # Пароль всегда ENV first — иначе случайный POSTGRES_PASSWORD из install ломается YAML "password"
        result["password"] = _env_or_yaml(data, "password", "POSTGRES_PASSWORD")
        result["embedding_dim"] = _env_or_yaml(
            data, "embedding_dim", "EMBEDDING_DIM", as_int=True
        )
        return result

    @property
    def connection_string(self) -> str:
        """Формирует строку подключения к PostgreSQL"""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"

    class Config:
        """Настройки Pydantic"""

        extra = "allow"


class MinIOConnectionConfig(BaseModel):
    """Конфигурация подключения к MinIO
    Все значения должны быть заданы в YAML или ENV
    """

    endpoint: str
    port: int
    access_key: str
    secret_key: str
    use_ssl: bool
    bucket_name: str
    documents_bucket_name: str
    memory_rag_bucket_name: str
    project_rag_bucket_name: str
    ssl_verify: bool = True
    ca_cert_path: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def load_from_yaml_or_env(cls, data: dict) -> dict:
        """Загружает значения из YAML или ENV (секреты/бакеты: ENV > YAML)."""
        if not isinstance(data, dict):
            data = {}
        result = _extra_fields(
            data,
            (
                "endpoint",
                "port",
                "access_key",
                "secret_key",
                "use_ssl",
                "bucket_name",
                "documents_bucket_name",
                "memory_rag_bucket_name",
                "project_rag_bucket_name",
                "ca_cert_path",
                "ssl_verify",
            ),
        )
        result["endpoint"] = _env_or_yaml(data, "endpoint", "MINIO_ENDPOINT")
        result["port"] = _env_or_yaml(data, "port", "MINIO_PORT", as_int=True)
        result["access_key"] = _env_or_yaml(
            data, "access_key", ["MINIO_ACCESS_KEY", "MINIO_ROOT_USER"]
        )
        result["secret_key"] = _env_or_yaml(
            data, "secret_key", ["MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"]
        )
        use_ssl = _env_or_yaml(
            data, "use_ssl", "MINIO_USE_SSL", required=False, as_bool=True
        )
        result["use_ssl"] = False if use_ssl is None else use_ssl
        result["bucket_name"] = _env_or_yaml(data, "bucket_name", "MINIO_BUCKET_NAME")
        docs = _env_or_yaml(
            data, "documents_bucket_name", "MINIO_DOCUMENTS_BUCKET_NAME", required=False
        )
        if docs:
            result["documents_bucket_name"] = docs
        else:
            result["documents_bucket_name"] = result.get(
                "bucket_name", "astrachat-temp"
            ).replace("-temp", "-documents")
        # RAG buckets: ENV > YAML > производное от bucket_name
        for field, env_name, suffix in (
            ("memory_rag_bucket_name", "MINIO_MEMORY_RAG_BUCKET_NAME", "-memory-rag"),
            ("project_rag_bucket_name", "MINIO_PROJECT_RAG_BUCKET_NAME", "-project-rag"),
        ):
            env_value = _env_or_yaml(data, field, env_name, required=False)
            if env_value:
                result[field] = env_value
            else:
                base = result.get("bucket_name", "astrachat-temp")
                if base.endswith("-temp"):
                    base = base[: -len("-temp")]
                result[field] = f"{base}{suffix}"
        if "ca_cert_path" in data and data["ca_cert_path"]:
            result["ca_cert_path"] = data["ca_cert_path"]
        else:
            result["ca_cert_path"] = None
            for env_name in ("MINIO_CA_CERT", "TLS_CERT_PATH", "SSL_CERT_FILE"):
                ca_path = _get_env_value(env_name)
                if ca_path:
                    result["ca_cert_path"] = ca_path
                    logger.debug(
                        "[MinIOConnectionConfig] CA bundle из %s: %s", env_name, ca_path
                    )
                    break
        if result.get("ca_cert_path") and not os.path.isfile(result["ca_cert_path"]):
            logger.warning(
                "[MinIOConnectionConfig] CA bundle не найден на диске: %s",
                result["ca_cert_path"],
            )
        if "ssl_verify" in data:
            result["ssl_verify"] = bool(data["ssl_verify"])
        else:
            verify_str = _get_env_value("MINIO_SSL_VERIFY")
            if verify_str is not None:
                result["ssl_verify"] = verify_str.strip().lower() in (
                    "true",
                    "1",
                    "yes",
                )
            else:
                result["ssl_verify"] = True
        if result["ssl_verify"] is False:
            logger.warning(
                "[MinIOConnectionConfig] Проверка SSL отключена (MINIO_SSL_VERIFY=false)"
            )
        return result

    @property
    def full_endpoint(self) -> str:
        """Возвращает полный endpoint с портом"""
        if ":" not in self.endpoint:
            return f"{self.endpoint}:{self.port}"
        return self.endpoint

    class Config:
        """Настройки Pydantic"""

        extra = "allow"


class LLMHostEntry(BaseModel):
    """Один инстанс llm-svc (или совместимого OpenAI API) с собственным base_url."""
    id: str = Field(..., description="Короткий идентификатор для путей llm-svc://id/model")
    base_url: str
class LLMServiceConnectionConfig(BaseModel):
    """Конфигурация подключения к LLM Service
    Все значения должны быть заданы в YAML или ENV
    """
    enabled: bool
    timeout: int
    retry_attempts: int
    retry_delay: int
    # URL будут определяться динамически из секции urls в config.yml
    # или из переменных окружения
    base_url: Optional[str] = None
    external_url: Optional[str] = None
    # SSL: проверка сертификата
    verify_ssl: Union[bool, str] = True
    # Настройки моделей
    default_model: str
    fallback_model: str
    auto_select: bool
    # мульти-хост
    hosts: Optional[List[LLMHostEntry]] = None
    default_host_id: Optional[str] = None
    @model_validator(mode='before')
    @classmethod
    def load_from_yaml_or_env(cls, data: dict) -> dict:
        """Загружает значения из YAML или ENV (приоритет: ENV > YAML для 
        секретов, YAML > ENV для остального)"""
        if not isinstance(data, dict):
            data = {}
        allow_empty_legacy = bool(data.get("_allow_empty_legacy"))
        result = _extra_fields(
            data,
            (
                "enabled", "timeout", "retry_attempts", "retry_delay",
                "base_url", "external_url", "verify_ssl",
                "default_model", "fallback_model", "auto_select",
                "hosts", "default_host_id", "api_key", "use_auth",
                "skip_health_probe", "models", "_allow_empty_legacy",
            ),
        )
        # Приоритет: YAML > ENV (для обычных настроек)
        # Enabled
        if "enabled" in data:
            enabled = data["enabled"]
        else:
            enabled_str = _get_env_value("USE_LLM_SVC")
            if enabled_str is None:
                if allow_empty_legacy:
                    enabled = False
                else:
                    raise ValueError("USE_LLM_SVC не задан в YAML или ENV")
            else:
                enabled = enabled_str.lower() == "true"
        result["enabled"] = enabled if isinstance(enabled, bool) else enabled
        # Timeout
        if "timeout" in data:
            result["timeout"] = data["timeout"]
        else:
            timeout = _get_env_value_int("LLM_SVC_TIMEOUT")
            if timeout is None:
                if allow_empty_legacy:
                    result["timeout"] = 300
                else:
                    raise ValueError("LLM_SVC_TIMEOUT не задан в YAML или ENV")
            else:
                result["timeout"] = timeout
        # Retry attempts
        if "retry_attempts" in data:
            result["retry_attempts"] = data["retry_attempts"]
        else:
            retry_attempts = _get_env_value_int("LLM_SVC_RETRY_ATTEMPTS")
            if retry_attempts is None:
                if allow_empty_legacy:
                    result["retry_attempts"] = 3
                else:
                    raise ValueError("LLM_SVC_RETRY_ATTEMPTS не задан в YAML или ENV")
            else:
                result["retry_attempts"] = retry_attempts
        # Retry delay
        if "retry_delay" in data:
            result["retry_delay"] = data["retry_delay"]
        else:
            retry_delay = _get_env_value_int("LLM_SVC_RETRY_DELAY")
            if retry_delay is None:
                if allow_empty_legacy:
                    result["retry_delay"] = 1
                else:
                    raise ValueError("LLM_SVC_RETRY_DELAY не задан в YAML или ENV")
            else:
                result["retry_delay"] = retry_delay
        # Обработка вложенной структуры models из YAML
        models_data = data.get("models", {}) if isinstance(data.get("models"), dict) else {}
        # Default model (может быть в YAML в секции microservices.llm_svc.models.default)
        if "default_model" in data:
            result["default_model"] = data["default_model"]
        elif models_data.get("default"):
            result["default_model"] = models_data["default"]
        else:
            if allow_empty_legacy:
                result["default_model"] = ""
            else:
                # Если не задан, пробуем получить из config.yml через внешний контекст
                # Это значение должно быть в config.yml в секции microservices.llm_svc.models.default
                raise ValueError("default_model не задан в YAML (llm_service.default_model или llm_service.models.default или microservices.llm_svc.models.default)")
        # Fallback model
        if "fallback_model" in data:
            result["fallback_model"] = data["fallback_model"]
        elif models_data.get("fallback"):
            result["fallback_model"] = models_data["fallback"]
        else:
            if allow_empty_legacy:
                result["fallback_model"] = ""
            else:
                raise ValueError("fallback_model не задан в YAML (llm_service.fallback_model или llm_service.models.fallback или microservices.llm_svc.models.fallback)")
        # Auto select
        if "auto_select" in data:
            result["auto_select"] = data["auto_select"]
        elif "auto_select" in models_data:
            result["auto_select"] = models_data["auto_select"]
        else:
            # По умолчанию True, если не задано
            result["auto_select"] = True
        # API Key (ПРИОРИТЕТ: ENV > YAML)
        api_key_from_env = _get_env_value("LLM_API_KEY")
        if api_key_from_env:
            result["api_key"] = api_key_from_env
            masked_key = f"{api_key_from_env[:8]}...{api_key_from_env[-4:]}" if len(api_key_from_env) > 12 else "*"
            logger.info(f"[LLMServiceConnectionConfig] LLM_API_KEY источник: переменные окружения (Kubernetes/ENV), значение: {masked_key}")
        elif "api_key" in data and data["api_key"]:
            result["api_key"] = data["api_key"]
            masked_key = f"{data['api_key'][:8]}...{data['api_key'][-4:]}" if len(data["api_key"]) > 12 else "*"
            logger.info(f"[LLMServiceConnectionConfig] LLM_API_KEY источник: config.yml, значение: {masked_key}")
        else:
            result["api_key"] = None
            logger.warning("[LLMServiceConnectionConfig] LLM_API_KEY не задан: ни в переменных окружения, ни в config.yml")
        # Use auth (дефолт: false)
        if "use_auth" in data:
            result["use_auth"] = data["use_auth"]
            logger.info(f"[LLMServiceConnectionConfig] LLM_USE_AUTH взят из config.yml: {result['use_auth']}")
        else:
            use_auth_str = _get_env_value("LLM_USE_AUTH")
            if use_auth_str:
                result["use_auth"] = use_auth_str.lower() == "true"
                logger.info(f"[LLMServiceConnectionConfig] LLM_USE_AUTH найден в переменных окружения: {result['use_auth']}")
            else:
                # По умолчанию False, если не задано
                result["use_auth"] = False
                logger.info(f"[LLMServiceConnectionConfig] LLM_USE_AUTH не найден, используется дефолт: {result['use_auth']}")
        tls_cert = _get_env_value("TLS_CERT_PATH")
        if tls_cert:
            result["verify_ssl"] = tls_cert
            logger.info(f"[LLMServiceConnectionConfig] SSL CA bundle из TLS_CERT_PATH: {tls_cert}")
        else:
            verify_str = _get_env_value("LLM_VERIFY_SSL")
            if verify_str is not None:
                result["verify_ssl"] = verify_str.strip().lower() in ("true", "1", "yes")
                logger.info(f"[LLMServiceConnectionConfig] verify_ssl из LLM_VERIFY_SSL: {result['verify_ssl']}")
            elif "verify_ssl" in data:
                result["verify_ssl"] = bool(data["verify_ssl"])
                logger.info(f"[LLMServiceConnectionConfig] verify_ssl из config.yml: {result['verify_ssl']}")
            else:
                result["verify_ssl"] = True
        if result["verify_ssl"] is False:
            logger.warning("[LLMServiceConnectionConfig] Проверка SSL отключена (verify_ssl=false)")
        if "skip_health_probe" in data:
            result["skip_health_probe"] = bool(data["skip_health_probe"])
        else:
            sk = _get_env_value("LLM_SKIP_HEALTH_PROBE")
            result["skip_health_probe"] = (sk or "").strip().lower() in ("1", "true", "yes")
        if "hosts" in data:
            result["hosts"] = data["hosts"]
        if "default_host_id" in data:
            result["default_host_id"] = data["default_host_id"]
        return result
    class Config:
        """Настройки Pydantic"""
        extra = "allow"
LLMConnectionConfig = LLMServiceConnectionConfig