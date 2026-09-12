"""
Модуль инициализации подключений к базам данных
"""

import asyncio
import os
import traceback
from typing import Optional
from urllib.parse import quote_plus
from backend.settings.logging import get_logger

# Импортируем настройки
try:
    from settings import get_settings
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False
    logger = get_logger(__name__)
    logger.warning("Модуль settings недоступен. Используются переменные окружения напрямую.")

logger = get_logger(__name__)

# Попытка импорта MongoDB модулей
try:
    # Проверяем наличие motor и pymongo
    try:
        import motor
        import pymongo
        # Получаем версии (не все модули имеют __version__)
        motor_version = getattr(motor, '__version__', getattr(motor, '_version', 'unknown'))
        pymongo_version = getattr(pymongo, '__version__', 'unknown')
        logger.info(f"motor ({motor_version}) и pymongo ({pymongo_version}) установлены")
    except ImportError as e:
        logger.error(f"motor или pymongo не установлены: {e}")
        logger.error("Установите: pip install motor pymongo")
        raise

    from .mongodb.connection import MongoDBConnection
    from .mongodb.repository import ConversationRepository
    mongodb_available = True
    logger.info("MongoDB модули импортированы успешно")
except ImportError as e:
    logger.error(f"MongoDB модули недоступны: {e}")
    logger.error(f"Traceback: {traceback.format_exc()}")
    mongodb_available = False
    MongoDBConnection = None
    ConversationRepository = None

# Попытка импорта PostgreSQL модулей
try:
    from .postgresql.connection import PostgreSQLConnection
    from .postgresql.repository import DocumentRepository, VectorRepository
    from .postgresql.agent_repository import AgentRepository
    from .postgresql.user_settings_repository import UserSettingsRepository
    from .postgresql.skill_repository import SkillRepository
    from .postgresql.project_repository import ProjectRepository
    from .postgresql.entity_settings_repository import EntitySettingsRepository
    postgresql_available = True
    logger.debug("PostgreSQL модули импортированы успешно")
except ImportError as e:
    logger.warning(f"PostgreSQL модули недоступны: {e}")
    postgresql_available = False
    PostgreSQLConnection = None
    DocumentRepository = None
    VectorRepository = None
    AgentRepository = None
    UserSettingsRepository = None
    SkillRepository = None
    ProjectRepository = None
    EntitySettingsRepository = None

# Попытка импорта MinIO модулей
try:
    from .minio import get_minio_client
    minio_available = True
    logger.debug("MinIO модули импортированы успешно")
except ImportError as e:
    logger.warning(f"MinIO модули недоступны: {e}")
    minio_available = False

# Глобальные подключения
mongodb_connection: Optional[MongoDBConnection] = None
postgresql_connection: Optional[PostgreSQLConnection] = None

# Глобальные репозитории
conversation_repo: Optional[ConversationRepository] = None
document_repo: Optional[DocumentRepository] = None
vector_repo: Optional[VectorRepository] = None
agent_repo: Optional[AgentRepository] = None
skill_repo: Optional["SkillRepository"] = None
user_settings_repo: Optional["UserSettingsRepository"] = None
project_repo: Optional["ProjectRepository"] = None
entity_settings_repo: Optional["EntitySettingsRepository"] = None


def get_mongodb_connection_string() -> str:
    """Получение строки подключения к MongoDB из настроек"""
    if not SETTINGS_AVAILABLE:
        raise RuntimeError("Модуль settings недоступен. Убедитесь, что настройки правильно настроены.")

    settings = get_settings()
    m = settings.mongodb
    user = str(m.user or "").strip()
    password = str(m.password or "").strip()
    if user and password:
        auth_source = (os.environ.get("MONGODB_AUTH_SOURCE") or "admin").strip() or "admin"
        return (
            f"mongodb://{quote_plus(user)}:{quote_plus(password)}@{m.host}:{m.port}/"
            f"?authSource={quote_plus(auth_source)}"
        )
    return f"mongodb://{m.host}:{m.port}/"


async def init_mongodb() -> bool:
    """Инициализация подключения к MongoDB"""
    global mongodb_connection, conversation_repo

    if not mongodb_available:
        logger.warning("MongoDB модули недоступны. Пропускаем инициализацию.")
        return False

    if not SETTINGS_AVAILABLE:
        logger.error("Модуль settings недоступен. MongoDB не может быть инициализирован.")
        return False

    try:
        settings = get_settings()
        m = settings.mongodb
        database_name = m.database
        # Строка подключения с экранированием по RFC 3986
        user = str(m.user or "").strip()
        password = str(m.password or "").strip()
        auth_source = (os.environ.get("MONGODB_AUTH_SOURCE") or "admin").strip() or "admin"
        if user and password:
            connection_string = (
                f"mongodb://{quote_plus(user)}:{quote_plus(password)}@{m.host}:{m.port}/"
                f"?authSource={quote_plus(auth_source)}"
            )
        else:
            connection_string = f"mongodb://{m.host}:{m.port}/"
        logger.info("Инициализация MongoDB...")
        _masked = connection_string.split("@")[-1] if "@" in connection_string else "*"
        logger.info(f"  Строка подключения: mongodb://*@{_masked}")
        logger.info(f"  База данных: {database_name}")

        mongodb_connection = MongoDBConnection(connection_string, database_name)

        logger.info("Попытка подключения к MongoDB...")
        if await mongodb_connection.connect():
            # Создаем репозиторий
            logger.info("Создание репозитория диалогов...")
            conversation_repo = ConversationRepository(mongodb_connection)

            # Создаем индексы
            logger.info("Создание индексов...")
            await conversation_repo.create_indexes()

            logger.info("MongoDB успешно инициализирован")
            return True
        else:
            logger.error("Не удалось подключиться к MongoDB")
            logger.error("  Проверьте, что MongoDB запущен и доступен по указанному адресу")
            return False

    except Exception as e:
        logger.error(f"Ошибка при инициализации MongoDB: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False


async def init_postgresql(
    *,
    max_attempts: int = 15,
    base_delay_sec: float = 1.0,
    max_delay_sec: float = 5.0,
) -> bool:
    """Инициализация подключения к PostgreSQL.

    После рестарта Postgres часто ещё в recovery mode, хотя healthcheck
    ``pg_isready`` уже green — поэтому здесь несколько попыток с backoff.
    """
    global postgresql_connection, document_repo, vector_repo, prompt_repo, tag_repo, agent_repo, skill_repo, user_settings_repo, project_repo, entity_settings_repo

    if not postgresql_available:
        logger.warning("PostgreSQL модули недоступны. Пропускаем инициализацию.")
        return False

    if not SETTINGS_AVAILABLE:
        logger.error("Модуль settings недоступен. PostgreSQL не может быть инициализирован.")
        return False

    settings = get_settings()
    pg_config = settings.postgresql
    embedding_dim = pg_config.embedding_dim
    last_error: Optional[BaseException] = None

    for attempt in range(1, max_attempts + 1):
        try:
            postgresql_connection = PostgreSQLConnection(
                host=pg_config.host,
                port=pg_config.port,
                database=pg_config.database,
                user=pg_config.user,
                password=pg_config.password,
            )

            if not await postgresql_connection.connect():
                last_error = RuntimeError("connect() вернул False")
                raise last_error

            # Создаем репозитории
            document_repo = DocumentRepository(postgresql_connection)
            vector_repo = VectorRepository(postgresql_connection, embedding_dim)
            agent_repo = AgentRepository(postgresql_connection)
            skill_repo = SkillRepository(postgresql_connection)
            user_settings_repo = UserSettingsRepository(postgresql_connection)
            project_repo = ProjectRepository(postgresql_connection)
            entity_settings_repo = EntitySettingsRepository(postgresql_connection)

            # Создаем таблицы
            await document_repo.create_tables()
            await vector_repo.create_tables()
            await agent_repo.create_tables()
            await _migrate_agent_model_settings(agent_repo)
            await skill_repo.create_tables()
            await user_settings_repo.create_tables()
            await project_repo.create_tables()
            # После agents и user_projects: перенос читает их владельцев, а чистка
            # сирот на них ссылается.
            await entity_settings_repo.create_tables()
            await _migrate_entity_rag_settings(entity_settings_repo)
            await entity_settings_repo.cleanup_orphans()

            if attempt > 1:
                logger.info("PostgreSQL успешно инициализирован с попытки %s/%s", attempt, max_attempts)
            else:
                logger.info("PostgreSQL успешно инициализирован")
            return True

        except Exception as e:
            last_error = e
            postgresql_connection = None
            document_repo = vector_repo = agent_repo = skill_repo = None
            user_settings_repo = project_repo = entity_settings_repo = None
            if attempt >= max_attempts:
                break
            delay = min(max_delay_sec, base_delay_sec * attempt)
            logger.warning(
                "PostgreSQL ещё не готов (попытка %s/%s): %s. Повтор через %.1fs",
                attempt,
                max_attempts,
                e,
                delay,
            )
            await asyncio.sleep(delay)

    logger.error("Не удалось инициализировать PostgreSQL после %s попыток: %s", max_attempts, last_error)
    return False


async def _migrate_agent_model_settings(repo) -> None:
    """Разовая уборка копий дефолтов в config.model_settings у агентов.

    Вынесена в отдельный модуль вместе со снимком того, что писал конструктор, —
    чтобы удалить обе части одним куском, когда старые карточки перестанут
    встречаться. Ошибка уборки не должна ронять старт.
    """
    try:
        from backend.services.agent_model_settings_migration import (
            cleanup_default_agent_model_settings,
        )

        await cleanup_default_agent_model_settings(repo)
    except Exception:
        logger.exception("[LLM-CFG-MIGRATE] уборка настроек агентов не выполнена")


def init_minio() -> bool:
    """Инициализация подключения к MinIO"""
    if not minio_available:
        logger.warning("MinIO модули недоступны. Пропускаем инициализацию.")
        return False

    try:
        logger.info("Инициализация MinIO...")
        minio_client = get_minio_client()
        if minio_client:
            endpoint = minio_client.endpoint
            bucket_temp = minio_client.bucket_name
            logger.info(f"Endpoint: {endpoint}")
            logger.info(f"Bucket (temp): {bucket_temp}")
            logger.info("MinIO успешно инициализирован")
            return True
        else:
            logger.warning("MinIO клиент недоступен")
            logger.warning("  Будет использоваться локальное хранение файлов")
            return False
    except Exception as e:
        logger.error(f"Ошибка при инициализации MinIO: {e}")
        logger.warning("  Будет использоваться локальное хранение файлов")
        return False


async def init_databases() -> bool:
    """Инициализация всех подключений к базам данных"""
    logger.info("=" * 60)
    logger.info("ИНИЦИАЛИЗАЦИЯ БАЗ ДАННЫХ")
    logger.info("=" * 60)

    mongodb_ok = await init_mongodb()
    postgresql_ok = await init_postgresql()
    minio_ok = init_minio()  # MinIO инициализация синхронная

    logger.info("=" * 60)
    if mongodb_ok and postgresql_ok and minio_ok:
        logger.info("Все базы данных успешно инициализированы")
        logger.info("=" * 60)
        return True
    else:
        logger.warning("Некоторые базы данных не инициализированы")
        if mongodb_ok:
            logger.info("MongoDB: готов")
        else:
            logger.warning("MongoDB: не инициализирован")
        if postgresql_ok:
            logger.info("PostgreSQL: готов")
        else:
            logger.warning("PostgreSQL: не инициализирован")
        if minio_ok:
            logger.info("MinIO: готов")
        else:
            logger.warning("MinIO: не инициализирован")
        logger.info("=" * 60)
        # Возвращаем True если хотя бы MongoDB инициализирован
        return mongodb_ok


async def close_databases():
    """Закрытие всех подключений к базам данных"""
    global mongodb_connection, postgresql_connection

    if mongodb_connection:
        await mongodb_connection.disconnect()

    if postgresql_connection:
        await postgresql_connection.disconnect()

    logger.info("Все подключения к базам данных закрыты")


def get_mongodb_connection():
    """Получение подключения к MongoDB"""
    global mongodb_connection
    if not mongodb_available:
        raise RuntimeError("MongoDB модули недоступны. Установите motor и pymongo.")
    if mongodb_connection is None:
        raise RuntimeError("MongoDB не инициализирован. Вызовите init_mongodb() сначала.")
    return mongodb_connection


def get_conversation_repository():
    """Получение репозитория диалогов"""
    if not mongodb_available:
        raise RuntimeError("MongoDB модули недоступны. Установите motor и pymongo.")
    if conversation_repo is None:
        raise RuntimeError("MongoDB не инициализирован. Вызовите init_mongodb() сначала.")
    return conversation_repo


def get_document_repository():
    """Получение репозитория документов"""
    if not postgresql_available:
        raise RuntimeError("PostgreSQL модули недоступны. Установите psycopg2.")
    if document_repo is None:
        raise RuntimeError("PostgreSQL не инициализирован. Вызовите init_postgresql() сначала.")
    return document_repo


def get_vector_repository():
    """Получение репозитория векторов"""
    if not postgresql_available:
        raise RuntimeError("PostgreSQL модули недоступны. Установите psycopg2.")
    if vector_repo is None:
        raise RuntimeError("PostgreSQL не инициализирован. Вызовите init_postgresql() сначала.")
    return vector_repo


def reset_mongodb_globals():
    """Сброс глобальных подключений MongoDB (для переинициализации в текущем event loop при 'Event loop is closed')."""
    global mongodb_connection, conversation_repo
    mongodb_connection = None
    conversation_repo = None


def get_agent_repository():
    """Получение репозитория агентов"""
    if not postgresql_available:
        raise RuntimeError("PostgreSQL модули недоступны. Установите psycopg2.")
    if agent_repo is None:
        raise RuntimeError("PostgreSQL не инициализирован. Вызовите init_postgresql() сначала.")
    return agent_repo


def get_skill_repository():
    """Получение репозитория skills"""
    if not postgresql_available:
        raise RuntimeError("PostgreSQL модули недоступны. Установите psycopg2.")
    if skill_repo is None:
        raise RuntimeError("PostgreSQL не инициализирован. Вызовите init_postgresql() сначала.")
    return skill_repo


def get_user_settings_repository():
    """Получение репозитория персональных LLM-настроек"""
    if not postgresql_available:
        raise RuntimeError("PostgreSQL модули недоступны. Установите psycopg2.")
    if user_settings_repo is None:
        raise RuntimeError("PostgreSQL не инициализирован. Вызовите init_postgresql() сначала.")
    return user_settings_repo


def get_project_repository():
    """Получение репозитория пользовательских проектов"""
    if not postgresql_available:
        raise RuntimeError("PostgreSQL модули недоступны. Установите psycopg2.")
    if project_repo is None:
        raise RuntimeError("PostgreSQL не инициализирован. Вызовите init_postgresql() сначала.")
    return project_repo


def get_entity_settings_repository():
    """Получение репозитория RAG-настроек агентов и проектов"""
    if not postgresql_available:
        raise RuntimeError("PostgreSQL модули недоступны. Установите psycopg2.")
    if entity_settings_repo is None:
        raise RuntimeError("PostgreSQL не инициализирован. Вызовите init_postgresql() сначала.")
    return entity_settings_repo


async def _migrate_entity_rag_settings(repo) -> None:
    """Разовый перенос настроек из user_llm_settings к сущностям.

    Ошибка переноса не должна ронять старт: настройки просто останутся
    дефолтными, а в логе будет причина.
    """
    try:
        from backend.services.entity_rag_migration import migrate_user_settings_to_entities

        await migrate_user_settings_to_entities(repo)
    except Exception:
        logger.exception("[RAG-CFG-MIGRATE] перенос настроек не выполнен")