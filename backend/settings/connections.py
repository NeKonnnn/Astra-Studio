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
        """Загружает значения из YAML или ENV (приоритет: YAML > ENV)"""
        if not isinstance(data, dict):
            data = {}
        result = _extra_fields(
            data, ("host", "port", "database", "user", "password", "embedding_dim")
        )
        # Приоритет: YAML > ENV
        # Host
        if "host" in data:
            result["host"] = data["host"]
        else:
            host = _get_env_value("POSTGRES_HOST")
            if host is None:
                msg = "POSTGRES_HOST не задан в YAML или ENV"
                raise ValueError(msg)
            result["host"] = host
        # Port
        if "port" in data:
            result["port"] = data["port"]
        else:
            port = _get_env_value_int("POSTGRES_PORT")
            if port is None:
                msg = "POSTGRES_PORT не задан в YAML или ENV"
                raise ValueError(msg)
            result["port"] = port
        # Database
        if "database" in data:
            result["database"] = data["database"]
        else:
            database = _get_env_value("POSTGRES_DB")
            if database is None:
                msg = "POSTGRES_DB не задан в YAML или ENV"
                raise ValueError(msg)
            result["database"] = database
        # User
        if "user" in data:
            result["user"] = data["user"]
        else:
            user = _get_env_value("POSTGRES_USER")
            if user is None:
                msg = "POSTGRES_USER не задан в YAML или ENV"
                raise ValueError(msg)
            result["user"] = user
        # Password
        if "password" in data:
            result["password"] = data["password"]
        else:
            password = _get_env_value("POSTGRES_PASSWORD")
            if password is None:
                msg = "POSTGRES_PASSWORD не задан в YAML или ENV"
                raise ValueError(msg)
            result["password"] = password
        # Embedding dim
        if "embedding_dim" in data:
            result["embedding_dim"] = data["embedding_dim"]
        else:
            embedding_dim = _get_env_value_int("EMBEDDING_DIM")
            if embedding_dim is None:
                msg = "EMBEDDING_DIM не задан в YAML или ENV"
                raise ValueError(msg)
            result["embedding_dim"] = embedding_dim
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
        """Загружает значения из YAML или ENV (приоритет: YAML > ENV)"""
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
        # Приоритет: YAML > ENV
        # Endpoint
        if "endpoint" in data:
            result["endpoint"] = data["endpoint"]
        else:
            endpoint = _get_env_value("MINIO_ENDPOINT")
            if endpoint is None:
                msg = "MINIO_ENDPOINT не задан в YAML или ENV"
                raise ValueError(msg)
            result["endpoint"] = endpoint
        # Port
        if "port" in data:
            result["port"] = data["port"]
        else:
            port = _get_env_value_int("MINIO_PORT")
            if port is None:
                msg = "MINIO_PORT не задан в YAML или ENV"
                raise ValueError(msg)
            result["port"] = port
        # Access key
        if "access_key" in data:
            result["access_key"] = data["access_key"]
        else:
            access_key = _get_env_value("MINIO_ACCESS_KEY") or _get_env_value(
                "MINIO_ROOT_USER"
            )
            if access_key is None:
                msg = "MINIO_ACCESS_KEY или MINIO_ROOT_USER не задан в YAML или ENV"
                raise ValueError(msg)
            result["access_key"] = access_key
        # Secret key
        if "secret_key" in data:
            result["secret_key"] = data["secret_key"]
        else:
            secret_key = _get_env_value("MINIO_SECRET_KEY") or _get_env_value(
                "MINIO_ROOT_PASSWORD"
            )
            if secret_key is None:
                msg = "MINIO_SECRET_KEY или MINIO_ROOT_PASSWORD не задан в YAML или ENV"
                raise ValueError(msg)
            result["secret_key"] = secret_key
        # Use SSL
        if "use_ssl" in data:
            use_ssl = data["use_ssl"]
        else:
            use_ssl_str = _get_env_value("MINIO_USE_SSL")
            if use_ssl_str is None:
                msg = "MINIO_USE_SSL не задан в YAML или ENV"
                raise ValueError(msg)
            use_ssl = use_ssl_str.lower() == "true"
        result["use_ssl"] = use_ssl if isinstance(use_ssl, bool) else use_ssl
        # Bucket name
        if "bucket_name" in data:
            result["bucket_name"] = data["bucket_name"]
        else:
            bucket_name = _get_env_value("MINIO_BUCKET_NAME")
            if bucket_name is None:
                msg = "MINIO_BUCKET_NAME не задан в YAML или ENV"
                raise ValueError(msg)
            result["bucket_name"] = bucket_name
        # Documents bucket name (может быть не задан, используем значение по умолчанию из bucket_name)
        if "documents_bucket_name" in data:
            result["documents_bucket_name"] = data["documents_bucket_name"]
        else:
            documents_bucket_name = _get_env_value("MINIO_DOCUMENTS_BUCKET_NAME")
            if documents_bucket_name is None:
                # Если не задан, используем bucket_name с суффиксом -documents
                result["documents_bucket_name"] = result.get(
                    "bucket_name", "astrachat-temp"
                ).replace("-temp", "-documents")
            else:
                result["documents_bucket_name"] = documents_bucket_name
        # RAG buckets (память / проекты): YAML > ENV > производное от bucket_name
        for field, env_name, suffix in (
            ("memory_rag_bucket_name", "MINIO_MEMORY_RAG_BUCKET_NAME", "-memory-rag"),
            ("project_rag_bucket_name", "MINIO_PROJECT_RAG_BUCKET_NAME", "-project-rag"),
        ):
            if data.get(field):
                result[field] = data[field]
                continue
            env_value = _get_env_value(env_name)
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