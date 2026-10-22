# Пул подключений к PostgreSQL (pgvector)
import logging
from typing import Optional
import asyncpg
from asyncpg import Pool

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class _ConnectionContextManager:
    def __init__(self, cm):
        self._cm = cm

    async def __aenter__(self):
        return await self._cm.__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self._cm.__aexit__(exc_type, exc_val, exc_tb)


class PostgreSQLConnection:
    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
    ):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.pool: Optional[Pool] = None

    async def connect(self, min_size: int = 2, max_size: int = 10) -> bool:
        try:
            logger.debug(
                "PostgreSQL (SVC-RAG): подключение host=%r port=%s database=%r user=%r",
                self.host,
                self.port,
                self.database,
                self.user,
            )
            self.pool = await asyncpg.create_pool(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                min_size=min_size,
                max_size=max_size,
            )
            async with self.pool.acquire() as conn:
                await conn.execute("SELECT 1")
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                _pgv = await conn.fetchval(
                    "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                )
                _pg = await conn.fetchval("SHOW server_version")
            logger.info(
                "PostgreSQL (SVC-RAG): подключено (postgres=%s, pgvector=%s)",
                _pg,
                _pgv,
            )
            return True
        except Exception as e:
            logger.error("PostgreSQL (SVC-RAG): ошибка подключения: %s", e)
            # Пул мог быть создан, а настройка pgvector упасть — не оставляем
            # «наполовину готовый» пул (утечка соединений, BUG-RAG-34).
            pool, self.pool = self.pool, None
            if pool is not None:
                try:
                    await pool.close()
                except Exception as close_err:
                    logger.warning(
                        "PostgreSQL (SVC-RAG): не удалось закрыть пул: %s", close_err
                    )
            return False

    async def disconnect(self):
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("PostgreSQL (SVC-RAG): отключено")

    async def acquire(self):
        if not self.pool:
            await self.connect()
        if not self.pool:
            raise RuntimeError("Пул PostgreSQL не создан")
        return _ConnectionContextManager(self.pool.acquire())

    async def health_check(self) -> bool:
        try:
            if self.pool:
                async with self.pool.acquire() as conn:
                    await conn.execute("SELECT 1")
                return True
            return False
        except Exception:
            return False


def get_postgres_connection() -> PostgreSQLConnection:
    cfg = get_settings().postgresql
    return PostgreSQLConnection(
        host=cfg.host,
        port=cfg.port,
        database=cfg.database,
        user=cfg.user,
        password=cfg.password,
    )
