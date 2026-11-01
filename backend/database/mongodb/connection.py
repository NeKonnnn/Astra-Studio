import re
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ConfigurationError, ConnectionFailure

from backend.settings.logging import get_logger

logger = get_logger(__name__)

_USERINFO_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^/?#]*)@")


def mask_connection_string(connection_string: str) -> str:
    """Скрывает пароль в строке подключения (user:pass@host -> user:***@host)."""
    match = _USERINFO_RE.match(connection_string)
    if not match or ":" not in match.group(2):
        return connection_string
    user = match.group(2).split(":", 1)[0]
    return f"{match.group(1)}{user}:***@{connection_string[match.end():]}"


class MongoDBConnection:
    """Класс для управления подключением к MongoDB"""

    def __init__(self, connection_string: str, database_name: str = "astrachat"):
        """
        Инициализация подключения к MongoDB

        Args:
            connection_string: Строка подключения к MongoDB
            database_name: Имя базы данных
        """
        self.connection_string = connection_string
        self.database_name = database_name
        self.client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None

    async def connect(self):
        """Подключение к MongoDB"""
        try:
            safe_connection_string = mask_connection_string(self.connection_string)
            logger.debug(f"Создание клиента MongoDB: {safe_connection_string}")
            self.client = AsyncIOMotorClient(
                self.connection_string,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                socketTimeoutMS=10000,
            )
            logger.debug("Проверка подключения (ping)...")
            await self.client.admin.command("ping")
            logger.debug("Ping успешен")
            self.db = self.client[self.database_name]
            logger.info(
                f"Успешное подключение к MongoDB. База данных: {self.database_name}"
            )
            return True
        except ConnectionFailure:
            logger.exception("Ошибка подключения к MongoDB")
            logger.exception(
                f"Проверьте, что MongoDB запущен и доступен по адресу: {mask_connection_string(self.connection_string)}"
            )
            return False
        except ConfigurationError:
            logger.exception("Ошибка конфигурации MongoDB")
            logger.exception(f"Проверьте строку подключения: {mask_connection_string(self.connection_string)}")
            return False
        except Exception:
            logger.exception("Неожиданная ошибка при подключении к MongoDB")
            import traceback

            logger.exception(f"Traceback: {traceback.format_exc()}")
            return False

    async def disconnect(self):
        """Отключение от MongoDB"""
        if self.client:
            self.client.close()
            logger.info("Отключение от MongoDB")

    async def health_check(self) -> bool:
        """Проверка здоровья подключения"""
        try:
            if self.client:
                await self.client.admin.command("ping")
                return True
            return False
        except Exception:
            logger.exception("Ошибка при проверке здоровья MongoDB")
            return False

    def get_database(self) -> AsyncIOMotorDatabase:
        """Получение объекта базы данных"""
        if self.db is None:
            msg = "База данных не подключена. Вызовите connect() сначала."
            raise RuntimeError(msg)
        return self.db

    def get_collection(self, collection_name: str):
        """Получение коллекции"""
        return self.get_database()[collection_name]
