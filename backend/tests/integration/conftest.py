"""Интеграционные фикстуры: реальные MongoDB / PostgreSQL из docker.

Запуск (адреса задаёт окружение, по умолчанию тесты пропускаются):

    docker run -d --rm --name astra-itest-mongo -p 127.0.0.1:57028:27017 mongo:7
    docker run -d --rm --name astra-itest-pg -e POSTGRES_PASSWORD=itest -e POSTGRES_DB=itest \
        -p 127.0.0.1:55443:5432 pgvector/pgvector:pg17
    ASTRA_TESTS_ALLOW_NETWORK=1 ASTRA_TEST_MONGO_URL=mongodb://127.0.0.1:57028 \
        ASTRA_TEST_PG_DSN=postgresql://postgres:itest@127.0.0.1:55443/itest \
        pytest -m integration backend/tests/integration
"""

import os
import uuid

import pytest

pytestmark = pytest.mark.integration


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} не задан — интеграционный стенд не поднят")
    if os.environ.get("ASTRA_TESTS_ALLOW_NETWORK") != "1":
        pytest.skip("нужен ASTRA_TESTS_ALLOW_NETWORK=1: в юнит-режиме сеть заблокирована")
    return value


@pytest.fixture
async def mongo_connection():
    """Отдельная БД на тест — тесты не видят данных друг друга и не трогают чужие базы."""
    from backend.database.mongodb.connection import MongoDBConnection

    url = _require("ASTRA_TEST_MONGO_URL")
    name = f"astra_itest_{uuid.uuid4().hex[:12]}"
    connection = MongoDBConnection(url, name)
    assert await connection.connect() is True
    try:
        yield connection
    finally:
        await connection.client.drop_database(name)
        await connection.disconnect()


@pytest.fixture
async def pg_connection():
    """Отдельная база на тест; настоящий PostgreSQLConnection из продуктового кода."""
    from urllib.parse import urlparse

    import asyncpg

    from backend.database.postgresql.connection import PostgreSQLConnection

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
