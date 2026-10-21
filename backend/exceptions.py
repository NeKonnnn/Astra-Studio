"""Общие исключения backend."""


class ConfigValueMissingError(ValueError):
    """Значение конфигурации не задано в YAML или ENV."""

    def __init__(self, name: str, *, alt: str | None = None) -> None:
        self.name = name
        self.alt = alt
        if alt:
            super().__init__(f"{name} или {alt} не задан в YAML или ENV")
        else:
            super().__init__(f"{name} не задан в YAML или ENV")


class ConfigFieldMissingError(ValueError):
    """Обязательное поле конфигурации не задано в YAML."""

    def __init__(self, field: str, locations: str) -> None:
        self.field = field
        self.locations = locations
        super().__init__(f"{field} не задан в YAML ({locations})")


class LLMProviderBaseUrlRequiredError(ValueError):
    """Для LLM-провайдера обязателен base_url."""

    def __init__(self, provider_id: str, kind: str) -> None:
        self.provider_id = provider_id
        self.kind = kind
        super().__init__(
            f"LLMProviderConfig(id={provider_id!r}, kind={kind!r}): base_url обязателен для этого kind."
        )


class ModuleAttributeNotFoundError(AttributeError):
    """Атрибут модуля не найден (ленивый __getattr__)."""

    def __init__(self, module: str, name: str) -> None:
        self.module = module
        self.name = name
        super().__init__(f"module {module!r} has no attribute {name!r}")


# --- JWT ---


class JwtInvalidAudienceError(Exception):
    """Неверный claim aud в JWT."""

    def __init__(self, expected: str, actual: object) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Invalid audience (expected {expected!r}, got {actual!r})")


class JwtInvalidIssuerError(Exception):
    """Неверный claim iss в JWT."""

    def __init__(self, expected: str, actual: object) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Invalid issuer (expected {expected!r}, got {actual!r})")


class JwtEnvVarMissingError(RuntimeError):
    """Обязательная JWT-переменная окружения не задана."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Missing required JWT env var: {name}")


class JwtEnvVarInvalidError(RuntimeError):
    """JWT-переменная окружения имеет неверное значение."""

    def __init__(self, name: str, raw: object) -> None:
        self.name = name
        self.raw = raw
        super().__init__(
            f"JWT env var {name} must be a positive integer (seconds), got {raw!r}"
        )


class JwtEnvVarsMissingError(RuntimeError):
    """Отсутствуют обязательные JWT-переменные окружения."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(f"Missing required JWT env vars: {', '.join(names)}")


class JwtAuthError(Exception):
    """Ошибка валидации JWT с пользовательским сообщением."""

    def __init__(
        self,
        code: str,
        *,
        reason: str,
        token_type: str = "access",  # noqa: S107
        max_sessions: int | None = None,
    ) -> None:
        self.code = code
        self.reason = reason
        self.token_type = token_type
        detail = _jwt_auth_detail(
            code, token_type=token_type, max_sessions=max_sessions
        )
        self.detail = detail
        super().__init__(detail)


def _jwt_auth_detail(
    code: str, *, token_type: str = "access", max_sessions: int | None = None
) -> str:  # noqa: S107
    if code == "missing_client_binding":
        return "Токен не привязан к сессии браузера"
    if code == "client_binding_mismatch":
        return "Токен не соответствует сессии браузера"
    if code == "missing_session_id":
        return "Сессия завершена"
    if code == "session_not_in_memory":
        return "Сессия завершена: сервер перезапущен"
    if code == "session_superseded":
        if max_sessions is not None and max_sessions > 1:
            return f"Сессия завершена: превышен лимит активных сессий ({max_sessions})"
        return "Сессия завершена: выполнен вход с другого устройства/окна"
    if code == "invalid_token_type":
        return "Неверный тип токена"
    if code == "missing_sub":
        return (
            "Неверный refresh токен" if token_type == "refresh" else "Неверный токен"
        )  # noqa: S105
    if code == "token_validation_failed":
        if token_type == "refresh":  # noqa: S105
            return "Не удалось валидировать refresh токен"
        return "Не удалось валидировать токен"
    return code


# --- Database / init ---


class SettingsModuleUnavailableError(RuntimeError):
    """Модуль settings недоступен."""

    def __init__(
        self, *, hint: str = "Проверьте, что настройки загружены корректно."
    ) -> None:
        super().__init__(f"Модуль settings недоступен. {hint}")


class MongoDBModuleUnavailableError(RuntimeError):
    """MongoDB-модуль недоступен."""

    def __init__(self) -> None:
        super().__init__("MongoDB модули недоступны. Установите motor и pymongo.")


class MongoDBNotInitializedError(RuntimeError):
    """MongoDB не инициализирован."""

    def __init__(self) -> None:
        super().__init__("MongoDB не инициализирован. Вызовите init_mongodb() сначала.")


class PostgreSQLModuleUnavailableError(RuntimeError):
    """PostgreSQL-модуль недоступен."""

    def __init__(self) -> None:
        super().__init__("PostgreSQL модуль недоступен. Установите psycopg2.")


class PostgreSQLNotInitializedError(RuntimeError):
    """PostgreSQL не инициализирован."""

    def __init__(self) -> None:
        super().__init__(
            "PostgreSQL не инициализирован. Вызовите init_postgresql() сначала."
        )


class DatabaseNotConnectedError(RuntimeError):
    """База данных не подключена."""

    def __init__(self, db_name: str = "База данных") -> None:
        super().__init__(f"{db_name} не подключена. Вызовите connect() сначала.")


class ConnectionPoolNotCreatedError(RuntimeError):
    """Пул соединений не создан."""

    def __init__(self) -> None:
        super().__init__(
            "Пул соединений не создан. Вызовите connect() или ensure_pool() сначала."
        )


class MongoDBUnavailableError(RuntimeError):
    """MongoDB недоступен для операции."""

    def __init__(self, operation: str = "сохранить сообщение") -> None:
        super().__init__(f"MongoDB недоступен. Невозможно {operation}.")


class MongoDBSaveFailedError(RuntimeError):
    """Не удалось сохранить данные в MongoDB."""

    def __init__(self) -> None:
        super().__init__("Не удалось сохранить сообщение в MongoDB")


class MongoDBSaveError(RuntimeError):
    """Ошибка при сохранении в MongoDB."""

    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(f"Ошибка при сохранении сообщения: {cause}")


# --- MinIO ---


class MinioLibraryNotInstalledError(ImportError):
    """Библиотека MinIO не установлена."""

    def __init__(self) -> None:
        super().__init__(
            "MinIO библиотека не установлена. Установите: pip install minio"
        )


class MinioSettingsUnavailableError(RuntimeError):
    """MinIO не может быть инициализирован без settings."""

    def __init__(self) -> None:
        super().__init__(
            "Модуль settings недоступен. MinIO не может быть инициализирован."
        )


class MinioRuntimeNotInstalledError(RuntimeError):
    """MinIO library not installed (runtime)."""

    def __init__(self) -> None:
        super().__init__("MinIO library not installed")


# --- LLM providers ---


class LLMProvidersJsonTypeError(TypeError):
    """LLM_PROVIDERS_JSON должен быть массивом."""

    def __init__(self) -> None:
        super().__init__("LLM_PROVIDERS_JSON должен быть JSON-массивом")


class LLMProvidersJsonEntryTypeError(ValueError):
    """Неподдерживаемый тип записи в LLM_PROVIDERS_JSON."""

    def __init__(self, item_type: type) -> None:
        self.item_type = item_type
        super().__init__(f"Неподдерживаемый тип записи: {item_type!r}")


class LLMProvidersNotConfiguredError(RuntimeError):
    """LLM-провайдеры не настроены."""

    def __init__(self) -> None:
        super().__init__(
            "Нет настроенных LLM-провайдеров. Заполните секцию llm_providers: в backend/config/config.yml или microservices.llm.hosts."
        )


class LLMProvidersInitError(RuntimeError):
    """Ни один LLM-провайдер не инициализирован."""

    def __init__(self) -> None:
        super().__init__("Ни один провайдер не инициализирован.")


# --- MCP ---


class McpClientNotConnectedError(RuntimeError):
    """MCP client не подключён."""

    def __init__(self) -> None:
        super().__init__("MCP client is not connected.")


class McpToolNoResultError(RuntimeError):
    """MCP tool call не вернул результат."""

    def __init__(self) -> None:
        super().__init__("No result returned from MCP tool call.")


class McpListResourcesNoResultError(RuntimeError):
    """MCP list_resources не вернул результат."""

    def __init__(self) -> None:
        super().__init__("No result returned from MCP list_resources call.")


class McpReadResourceNoResultError(RuntimeError):
    """MCP read_resource не вернул результат."""

    def __init__(self) -> None:
        super().__init__("No result returned from MCP read_resource call.")


class McpCredentialsKeyNotConfiguredError(RuntimeError):
    """Ключ шифрования MCP credentials не настроен."""

    def __init__(self, *, for_store: bool = False) -> None:
        if for_store:
            super().__init__(
                "MCP_CREDENTIALS_ENCRYPTION_KEY is not configured; cannot store credentials"
            )
        else:
            super().__init__("MCP_CREDENTIALS_ENCRYPTION_KEY is not configured")


class McpCredentialsDecryptError(RuntimeError):
    """Не удалось расшифровать MCP credentials."""

    def __init__(self) -> None:
        super().__init__("Failed to decrypt MCP credentials")


class McpSessionUnavailableError(RuntimeError):
    """MCP-сессия недоступна."""

    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        super().__init__(f"MCP session unavailable: {server_id}")


# --- Realtime / Socket.IO ---


class SocketNotAuthorizedError(ConnectionRefusedError):
    """Socket.IO: пользователь не авторизован."""

    def __init__(self) -> None:
        super().__init__("Не авторизован")


class SocketInvalidTokenError(ConnectionRefusedError):
    """Socket.IO: неверный или просроченный токен."""

    def __init__(self) -> None:
        super().__init__("Неверный или просроченный токен")


# --- Transcription ---


class LocalTranscribersUnavailableError(Exception):
    """Локальные модели транскрибации недоступны."""

    def __init__(self) -> None:
        super().__init__(
            "Локальные модели отсутствуют. Проверьте папки или включите USE_LLM_SVC=true"
        )


class SttUnavailableError(Exception):
    """STT недоступен."""

    def __init__(self) -> None:
        super().__init__(
            "STT недоступен: включите USE_LLM_SVC или установите локальный WhisperX"
        )


class SounddeviceUnavailableError(Exception):
    """Пакет sounddevice недоступен."""

    def __init__(self) -> None:
        super().__init__("Для записи с микрофона нужен пакет sounddevice и PortAudio")


class LocalWhisperXUnavailableError(Exception):
    """Локальный WhisperX недоступен."""

    def __init__(self) -> None:
        super().__init__(
            "Локальный WhisperX недоступен. Установите зависимости или USE_LLM_SVC=true"
        )


class WhisperXNotInstalledError(ImportError):
    """WhisperX не установлен локально."""

    def __init__(self) -> None:
        super().__init__("WhisperX не установлен локально. Установите USE_LLM_SVC=true")


# --- App state ---


class SettingsFileNotFoundError(OSError):
    """Файл settings не найден ни в одном из путей."""

    def __init__(self, paths: object) -> None:
        self.paths = paths
        super().__init__(f"Не найден файл settings ни в одном пути: {paths}")


# --- Settings / config ---


class AuthSessionTimeoutNotConfiguredError(ValueError):
    """AUTH_SESSION_TIMEOUT_SECONDS не задан."""

    def __init__(self) -> None:
        super().__init__(
            "AUTH_SESSION_TIMEOUT_SECONDS не задан. Укажите переменную в ConfigMap/Deployment контейнера astra-chat-backend."
        )


class ConfigUrlKeyMissingError(ValueError):
    """Ключ URL не задан в YAML или ENV."""

    def __init__(self, key: str, env_key: str) -> None:
        self.key = key
        self.env_key = env_key
        super().__init__(f"{key} не задан в YAML (urls.{key}) или ENV ({env_key})")


class ConfigUrlPortMissingError(ValueError):
    """Порты URL не заданы в config.yml."""

    def __init__(self, docker_field: str, port_field: str) -> None:
        self.docker_field = docker_field
        self.port_field = port_field
        super().__init__(
            f"Задайте urls.{docker_field} и urls.{port_field} в backend/config/config.yml"
        )


class McpServerIdRequiredError(ValueError):
    """server_id обязателен."""

    def __init__(self) -> None:
        super().__init__("server_id обязателен")


class ConfigLoadError(ValueError):
    """Ошибка загрузки конфигурации."""

    def __init__(self, config_path: str, cause: Exception) -> None:
        self.config_path = config_path
        self.cause = cause
        super().__init__(f"Ошибка загрузки конфигурации из {config_path}: {cause}")


# --- RAG client ---


class RagServiceRequestError(RuntimeError):
    """Ошибка HTTP-запроса к SVC-RAG."""

    def __init__(self, method: str, url: str, status_code: int, detail: str) -> None:
        self.method = method
        self.url = url
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"SVC-RAG {method} {url} failed: {status_code} {detail}")


class RagServiceError(RuntimeError):
    """Общая ошибка SVC-RAG."""

    def __init__(self, method: str, url: str, cause: Exception) -> None:
        self.method = method
        self.url = url
        self.cause = cause
        super().__init__(f"SVC-RAG {method} {url} error: {cause}")


# --- Routes / validation ---


class InvalidAudioDataError(ValueError):
    """Некорректные аудио данные."""

    def __init__(self) -> None:
        super().__init__("Некорректные аудио данные")


class InvalidFilenameError(ValueError):
    """Некорректное имя файла."""

    def __init__(self) -> None:
        super().__init__("Invalid filename")
