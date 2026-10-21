"""Интеграционные фикстуры svc-rag: реальный Postgres с pgvector из docker.

Запуск (по умолчанию тесты пропускаются):

    docker run -d --rm --name astra-itest-pg -e POSTGRES_PASSWORD=itest -e POSTGRES_DB=itest \
        -p 127.0.0.1:55443:5432 pgvector/pgvector:pg17
    ASTRA_TEST_PG_DSN=postgresql://postgres:itest@127.0.0.1:55443/itest \
        pytest -m integration tests/integration
"""

import os
import uuid
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.integration


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} не задан — интеграционный стенд не поднят")
    return value


@pytest.fixture
async def pg_connection():
    """Отдельная база на тест; настоящий PostgreSQLConnection svc-rag."""
    import asyncpg

    from app.database.connection import PostgreSQLConnection

    dsn = _require("ASTRA_TEST_PG_DSN")
    parsed = urlparse(dsn)
    name = f"itest_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    connection = PostgreSQLConnection(
        parsed.hostname or "127.0.0.1",
        parsed.port or 5432,
        name,
        parsed.username or "postgres",
        parsed.password or "",
    )
    assert await connection.connect(min_size=1, max_size=4) is True
    try:
        yield connection
    finally:
        await connection.disconnect()
        admin = await asyncpg.connect(dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
