"""backend.database.init_db: инициализация подключений, геттеры, фолбэки импортов.

Подключения к Mongo/PostgreSQL/MinIO подменены фейками — сети нет. Глобальные
переменные модуля возвращаются на место через monkeypatch.
"""

from __future__ import annotations

import builtins
import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.database import init_db


@pytest.fixture(autouse=True)
def _restore_globals(monkeypatch):
    """Регистрируем все глобальные «синглтоны» в monkeypatch, чтобы тест их не протащил дальше."""
    for name in (
        "mongodb_connection",
        "postgresql_connection",
        "conversation_repo",
        "document_repo",
        "vector_repo",
        "agent_repo",
        "skill_repo",
        "user_settings_repo",
        "project_repo",
        "entity_settings_repo",
    ):
        monkeypatch.setattr(init_db, name, None)


# --------------------------------------------------------------- фолбэки импортов


def _load_copy(*blocked: str):
    """Выполняет init_db.py как отдельный модуль, пока указанные импорты бросают ImportError."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        for prefix in blocked:
            if name == prefix or name.startswith(prefix + "."):
                raise ImportError(f"заблокирован для теста: {name}")
        return real_import(name, *args, **kwargs)

    spec = importlib.util.spec_from_file_location(
        "init_db_import_fallback_copy", init_db.__file__
    )
    module = importlib.util.module_from_spec(spec)
    builtins.__import__ = fake_import
    try:
        spec.loader.exec_module(module)
    finally:
        builtins.__import__ = real_import
    return module


def test_all_optional_modules_available_in_real_module():
    assert init_db.mongodb_available is True
    assert init_db.postgresql_available is True
    assert init_db.minio_available is True


def test_missing_motor_disables_mongodb_only():
    copy = _load_copy("motor")
    assert copy.mongodb_available is False
    assert copy.MongoDBConnection is None and copy.ConversationRepository is None
    assert copy.postgresql_available is True and copy.minio_available is True


def test_missing_mongo_modules_disable_mongodb():
    copy = _load_copy("backend.database.mongodb")
    assert copy.mongodb_available is False
    assert copy.MongoDBConnection is None


def test_missing_postgresql_modules_nullify_every_class():
    copy = _load_copy("backend.database.postgresql.agent_repository")
    assert copy.postgresql_available is False
    for name in (
        "PostgreSQLConnection",
        "DocumentRepository",
        "VectorRepository",
        "AgentRepository",
        "SkillRepository",
        "UserSettingsRepository",
        "EntitySettingsRepository",
        "ProjectRepository",
    ):
        assert getattr(copy, name) is None, name
    assert copy.mongodb_available is True


def test_missing_minio_module_disables_minio():
    copy = _load_copy("backend.database.minio")
    assert copy.minio_available is False
    assert copy.mongodb_available is True and copy.postgresql_available is True


# ------------------------------------------------------------ настройки (фикстуры)


def _settings(user="", password="", host="mongo.test", port=27017, database="db"):
    return SimpleNamespace(
        mongodb=SimpleNamespace(
            user=user, password=password, host=host, port=port, database=database
        ),
        postgresql=SimpleNamespace(
            host="pg.test",
            port=5432,
            database="astra",
            user="pg",
            password="secret",
            embedding_dim=384,
        ),
    )


# ------------------------------------------------------------------ init_mongodb


class FakeMongoConnection:
    result = True
    instances = []

    def __init__(self, url, database):
        self.url = url
        self.database = database
        self.connect = AsyncMock(return_value=type(self).result)
        self.disconnect = AsyncMock()
        type(self).instances.append(self)


class FakeConversationRepo:
    def __init__(self, connection):
        self.connection = connection
        self.create_indexes = AsyncMock()


@pytest.fixture
def mongo_fakes(monkeypatch):
    FakeMongoConnection.result = True
    FakeMongoConnection.instances = []
    monkeypatch.setattr(init_db, "MongoDBConnection", FakeMongoConnection)
    monkeypatch.setattr(init_db, "ConversationRepository", FakeConversationRepo)
    monkeypatch.setattr(init_db, "get_settings", lambda: _settings())


async def test_init_mongodb_skips_when_modules_missing(monkeypatch):
    monkeypatch.setattr(init_db, "mongodb_available", False)
    assert await init_db.init_mongodb() is False
    assert init_db.mongodb_connection is None


async def test_init_mongodb_skips_when_settings_missing(monkeypatch, mongo_fakes):
    monkeypatch.setattr(init_db, "SETTINGS_AVAILABLE", False)
    assert await init_db.init_mongodb() is False
    assert FakeMongoConnection.instances == []


async def test_init_mongodb_success_creates_repo_and_indexes(mongo_fakes):
    assert await init_db.init_mongodb() is True
    connection = FakeMongoConnection.instances[0]
    assert connection.url == "mongodb://mongo.test:27017/"
    assert connection.database == "db"
    assert init_db.mongodb_connection is connection
    assert init_db.conversation_repo.connection is connection
    init_db.conversation_repo.create_indexes.assert_awaited_once()


async def test_init_mongodb_uses_credentials_in_connection_url(monkeypatch, mongo_fakes):
    """Заданные логин и пароль попадают в строку подключения (с экранированием)."""
    monkeypatch.setattr(init_db, "get_settings", lambda: _settings("us er", "p@ss", host="h", port=27017))
    assert await init_db.init_mongodb() is True
    assert FakeMongoConnection.instances[0].url == (
        "mongodb://us+er:p%40ss@h:27017/?authSource=admin"
    )


async def test_init_mongodb_without_credentials_has_no_auth(monkeypatch, mongo_fakes):
    monkeypatch.setattr(init_db, "get_settings", lambda: _settings(host="127.0.0.1", port=27117))
    assert await init_db.init_mongodb() is True
    assert FakeMongoConnection.instances[0].url == "mongodb://127.0.0.1:27117/"


@pytest.mark.parametrize("user,password", [("user", ""), ("", "pass")])
async def test_init_mongodb_partial_credentials_connects_without_auth(monkeypatch, mongo_fakes, user, password):
    monkeypatch.setattr(init_db, "get_settings", lambda: _settings(user, password))
    assert await init_db.init_mongodb() is True
    assert "@" not in FakeMongoConnection.instances[0].url


async def test_init_mongodb_connect_failure_returns_false(mongo_fakes):
    FakeMongoConnection.result = False
    assert await init_db.init_mongodb() is False
    assert init_db.conversation_repo is None


async def test_init_mongodb_unexpected_error_returns_false(monkeypatch, mongo_fakes):
    def boom():
        raise RuntimeError("settings broken")

    monkeypatch.setattr(init_db, "get_settings", boom)
    assert await init_db.init_mongodb() is False


# --------------------------------------------------------------- init_postgresql


class FakePgConnection:
    result = True
    instances = []

    def __init__(self, host, port, database, user, password):
        self.params = (host, port, database, user, password)
        self.connect = AsyncMock(return_value=type(self).result)
        self.disconnect = AsyncMock()
        type(self).instances.append(self)

    async def acquire(self):
        """Совместимость с PostgreSQLConnection.acquire() для реальных репозиториев."""
        conn = MagicMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)
        return conn


CALLS: list = []


def _fake_repo(label, extra=None):
    class _Repo:
        def __init__(self, connection, *args):
            self.connection = connection
            self.args = args
            self.label = label

        async def create_tables(self):
            CALLS.append(f"{label}.create_tables")
            if extra:
                raise extra

        async def cleanup_orphans(self):
            CALLS.append(f"{label}.cleanup_orphans")

    _Repo.__name__ = label
    return _Repo


@pytest.fixture
def pg_fakes(monkeypatch):
    CALLS.clear()
    FakePgConnection.result = True
    FakePgConnection.instances = []
    monkeypatch.setattr(init_db, "PostgreSQLConnection", FakePgConnection)
    for attr, label in (
        ("DocumentRepository", "documents"),
        ("VectorRepository", "vectors"),
        ("AgentRepository", "agents"),
        ("SkillRepository", "skills"),
        ("KnowledgeBaseRepository", "knowledge_bases"),
        ("UserSettingsRepository", "user_settings"),
        ("ProjectRepository", "projects"),
        ("EntitySettingsRepository", "entity_settings"),
    ):
        if getattr(init_db, attr, None) is not None:
            monkeypatch.setattr(init_db, attr, _fake_repo(label))

    async def agent_migration(repo):
        CALLS.append("migrate_agents")

    async def entity_migration(repo):
        CALLS.append("migrate_entities")

    monkeypatch.setattr(init_db, "_migrate_agent_model_settings", agent_migration)
    monkeypatch.setattr(init_db, "_migrate_entity_rag_settings", entity_migration)
    monkeypatch.setattr(init_db, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        "backend.settings.service_toggles.is_service_enabled", lambda key: True
    )


async def test_init_postgresql_disabled_by_feature_flag(monkeypatch, pg_fakes):
    seen = []
    monkeypatch.setattr(
        "backend.settings.service_toggles.is_service_enabled",
        lambda key: seen.append(key) or False,
    )
    assert await init_db.init_postgresql() is False
    assert seen == ["postgresql"]
    assert FakePgConnection.instances == []


async def test_init_postgresql_skips_when_modules_missing(monkeypatch, pg_fakes):
    monkeypatch.setattr(init_db, "postgresql_available", False)
    assert await init_db.init_postgresql() is False
    assert FakePgConnection.instances == []


async def test_init_postgresql_skips_when_settings_missing(monkeypatch, pg_fakes):
    monkeypatch.setattr(init_db, "SETTINGS_AVAILABLE", False)
    assert await init_db.init_postgresql() is False
    assert FakePgConnection.instances == []


async def test_init_postgresql_success_order_and_globals(pg_fakes):
    assert await init_db.init_postgresql() is True
    connection = FakePgConnection.instances[0]
    assert connection.params == ("pg.test", 5432, "astra", "pg", "secret")
    # порядок важен: агенты и проекты создаются до таблицы настроек сущностей
    assert CALLS == [
        "documents.create_tables",
        "vectors.create_tables",
        "agents.create_tables",
        "migrate_agents",
        "skills.create_tables",
        "knowledge_bases.create_tables",
        "user_settings.create_tables",
        "projects.create_tables",
        "entity_settings.create_tables",
        "migrate_entities",
        "entity_settings.cleanup_orphans",
    ]
    assert init_db.postgresql_connection is connection
    assert init_db.vector_repo.args == (384,)
    for repo in (
        init_db.document_repo,
        init_db.agent_repo,
        init_db.skill_repo,
        init_db.user_settings_repo,
        init_db.project_repo,
        init_db.entity_settings_repo,
    ):
        assert repo.connection is connection


async def test_init_postgresql_connect_failure(pg_fakes):
    FakePgConnection.result = False
    # без retry: иначе 15 попыток × sleep вешают юнит-тесты
    assert await init_db.init_postgresql(max_attempts=1, base_delay_sec=0) is False
    assert CALLS == [] and init_db.agent_repo is None


async def test_init_postgresql_table_error_returns_false(monkeypatch, pg_fakes):
    monkeypatch.setattr(
        init_db, "AgentRepository", _fake_repo("agents", RuntimeError("DDL failed"))
    )
    assert await init_db.init_postgresql(max_attempts=1, base_delay_sec=0) is False
    assert "skills.create_tables" not in CALLS


# -------------------------------------------------------------------- init_minio


@pytest.fixture
def minio_enabled(monkeypatch):
    monkeypatch.setattr(
        "backend.settings.service_toggles.is_service_enabled", lambda key: True
    )


def test_init_minio_disabled_by_feature_flag(monkeypatch):
    monkeypatch.setattr(
        "backend.settings.service_toggles.is_service_enabled", lambda key: False
    )
    monkeypatch.setattr(
        init_db, "get_minio_client", MagicMock(side_effect=AssertionError("не вызывать")), raising=False
    )
    assert init_db.init_minio() is False


def test_init_minio_skips_when_module_missing(monkeypatch, minio_enabled):
    monkeypatch.setattr(init_db, "minio_available", False)
    assert init_db.init_minio() is False


def test_init_minio_success(monkeypatch, minio_enabled):
    client = SimpleNamespace(endpoint="minio:9000", bucket_name="temp")
    monkeypatch.setattr(init_db, "get_minio_client", lambda: client, raising=False)
    assert init_db.init_minio() is True


def test_init_minio_without_client_falls_back_to_local_storage(monkeypatch, minio_enabled):
    monkeypatch.setattr(init_db, "get_minio_client", lambda: None, raising=False)
    assert init_db.init_minio() is False


def test_init_minio_error_returns_false(monkeypatch, minio_enabled):
    def boom():
        raise RuntimeError("bad endpoint")

    monkeypatch.setattr(init_db, "get_minio_client", boom, raising=False)
    assert init_db.init_minio() is False


# ---------------------------------------------------------------- init_databases


@pytest.mark.parametrize(
    "mongo, pg, minio, expected",
    [
        (True, True, True, True),
        (True, False, False, True),  # возвращается только состояние Mongo
        (False, True, True, False),
        (False, False, False, False),
    ],
)
async def test_init_databases_result_follows_mongodb(monkeypatch, mongo, pg, minio, expected):
    monkeypatch.setattr(init_db, "init_mongodb", AsyncMock(return_value=mongo))
    monkeypatch.setattr(init_db, "init_postgresql", AsyncMock(return_value=pg))
    monkeypatch.setattr(init_db, "init_minio", MagicMock(return_value=minio))
    assert await init_db.init_databases() is expected
    init_db.init_mongodb.assert_awaited_once()
    init_db.init_postgresql.assert_awaited_once()
    init_db.init_minio.assert_called_once()


# ---------------------------------------------------- close / reset / геттеры


async def test_close_databases_disconnects_both(monkeypatch):
    mongo, pg = MagicMock(disconnect=AsyncMock()), MagicMock(disconnect=AsyncMock())
    monkeypatch.setattr(init_db, "mongodb_connection", mongo)
    monkeypatch.setattr(init_db, "postgresql_connection", pg)
    await init_db.close_databases()
    mongo.disconnect.assert_awaited_once()
    pg.disconnect.assert_awaited_once()


async def test_close_databases_without_connections_is_noop():
    await init_db.close_databases()  # не бросает


def test_reset_mongodb_globals(monkeypatch):
    monkeypatch.setattr(init_db, "mongodb_connection", object())
    monkeypatch.setattr(init_db, "conversation_repo", object())
    init_db.reset_mongodb_globals()
    assert init_db.mongodb_connection is None and init_db.conversation_repo is None


MONGO_GETTERS = [
    ("get_mongodb_connection", "mongodb_connection"),
    ("get_conversation_repository", "conversation_repo"),
]
PG_GETTERS = [
    ("get_document_repository", "document_repo"),
    ("get_vector_repository", "vector_repo"),
    ("get_agent_repository", "agent_repo"),
    ("get_skill_repository", "skill_repo"),
    ("get_user_settings_repository", "user_settings_repo"),
    ("get_project_repository", "project_repo"),
    ("get_entity_settings_repository", "entity_settings_repo"),
]


@pytest.mark.parametrize("getter, attr", MONGO_GETTERS)
def test_mongo_getters(monkeypatch, getter, attr):
    func = getattr(init_db, getter)
    with pytest.raises(RuntimeError, match="не инициализирован"):
        func()
    sentinel = object()
    monkeypatch.setattr(init_db, attr, sentinel)
    assert func() is sentinel
    monkeypatch.setattr(init_db, "mongodb_available", False)
    with pytest.raises(RuntimeError, match="MongoDB модули недоступны"):
        func()


@pytest.mark.parametrize("getter, attr", PG_GETTERS)
def test_postgresql_getters(monkeypatch, getter, attr):
    func = getattr(init_db, getter)
    with pytest.raises(RuntimeError, match="PostgreSQL не инициализирован"):
        func()
    sentinel = object()
    monkeypatch.setattr(init_db, attr, sentinel)
    assert func() is sentinel
    monkeypatch.setattr(init_db, "postgresql_available", False)
    with pytest.raises(RuntimeError, match="PostgreSQL модули недоступны"):
        func()


# -------------------------------------------------------------------- миграции


async def test_migrate_agent_model_settings_delegates(monkeypatch):
    cleanup = AsyncMock()
    monkeypatch.setattr(
        "backend.services.agent_model_settings_migration.cleanup_default_agent_model_settings",
        cleanup,
    )
    repo = object()
    await init_db._migrate_agent_model_settings(repo)
    cleanup.assert_awaited_once_with(repo)


async def test_migrate_agent_model_settings_swallows_errors(monkeypatch):
    cleanup = AsyncMock(side_effect=RuntimeError("migration failed"))
    monkeypatch.setattr(
        "backend.services.agent_model_settings_migration.cleanup_default_agent_model_settings",
        cleanup,
    )
    await init_db._migrate_agent_model_settings(object())  # старт не падает
    cleanup.assert_awaited_once()


async def test_migrate_entity_rag_settings_delegates(monkeypatch):
    migrate = AsyncMock()
    monkeypatch.setattr(
        "backend.services.entity_rag_migration.migrate_user_settings_to_entities", migrate
    )
    repo = object()
    await init_db._migrate_entity_rag_settings(repo)
    migrate.assert_awaited_once_with(repo)


async def test_migrate_entity_rag_settings_swallows_errors(monkeypatch):
    migrate = AsyncMock(side_effect=RuntimeError("migration failed"))
    monkeypatch.setattr(
        "backend.services.entity_rag_migration.migrate_user_settings_to_entities", migrate
    )
    await init_db._migrate_entity_rag_settings(object())
    migrate.assert_awaited_once()
