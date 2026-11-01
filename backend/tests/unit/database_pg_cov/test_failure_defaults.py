"""Ветки сбоя репозиториев PostgreSQL: нет пула / упавший запрос -> безопасный дефолт.

Репозитории не должны ронять вызывающий код: каждый метод, кроме create_tables
(он пробрасывает ошибку наверх, чтобы старт приложения не прошёл молча),
логирует исключение и возвращает «пустое» значение. Подключение подменено фейком.
"""

from __future__ import annotations

import pytest

from backend.database.postgresql.agent_models import (
    AgentCreate,
    AgentFilters,
    AgentUpdate,
)
from backend.database.postgresql.agent_repository import AgentRepository
from backend.database.postgresql.entity_settings_repository import (
    EntitySettingsRepository,
)
from backend.database.postgresql.models import Document, DocumentVector
from backend.database.postgresql.project_models import ProjectCreate, ProjectUpdate
from backend.database.postgresql.project_repository import ProjectRepository
from backend.database.postgresql.repository import DocumentRepository, VectorRepository
from backend.database.postgresql.skill_models import SkillCreate, SkillFilters
from backend.database.postgresql.skill_repository import SkillRepository
from backend.database.postgresql.user_settings_repository import UserSettingsRepository
from backend.tests.unit.database_pg_cov.fakes import FakeConn, FakeDb

_AGENT_CREATE = AgentCreate(name="Агент один", system_prompt="Ты полезный помощник.")
_SKILL_CREATE = SkillCreate(name="Skill", content="тело")
_DOC = Document(filename="a.txt", content="текст")
_VEC = DocumentVector(document_id=1, chunk_index=0, embedding=[0.1, 0.2], content="c")

# (класс репозитория, метод, аргументы, ожидаемый результат при сбое)
CASES = [
    # --- agent_repository ---
    (AgentRepository, "create_agent", (_AGENT_CREATE, "u", "U"), None),
    (AgentRepository, "get_agent", (1, "u"), None),
    (AgentRepository, "get_agents", (AgentFilters(),), ([], 0)),
    (AgentRepository, "update_agent", (1, AgentUpdate(name="Новое имя"), "u"), False),
    (AgentRepository, "find_orphan_kb_document_ids", ([1, 2],), []),
    (AgentRepository, "find_documents_claimed_by_other_agents", (1, [1, 2]), []),
    (AgentRepository, "delete_agent", (1, "u"), False),
    (AgentRepository, "remove_agent_from_chains", (1,), None),
    (AgentRepository, "list_tags", (), []),
    (AgentRepository, "list_agent_ids_by_tags", ([1],), []),
    (AgentRepository, "get_agent_names_map", ([1, 2],), {}),
    (AgentRepository, "remove_agent_from_subagents", (1,), None),
    (AgentRepository, "rate_agent", (1, "u", 5), False),
    (AgentRepository, "increment_views", (1,), False),
    (AgentRepository, "increment_usage", (1,), False),
    (AgentRepository, "get_agent_stats", (1,), None),
    (AgentRepository, "add_bookmark", (1, "u"), False),
    (AgentRepository, "remove_bookmark", (1, "u"), False),
    (AgentRepository, "get_user_bookmarks", ("u",), ([], 0)),
    (AgentRepository, "get_user_permission", (1, "u"), None),
    (AgentRepository, "user_can_access_agent", (1, "u"), False),
    (AgentRepository, "share_agent", (1, "o", ["a", "b"]), ([], ["a", "b"])),
    (AgentRepository, "unshare_agent", (1, "o", "t"), False),
    (AgentRepository, "list_agent_shares", (1, "o"), []),
    (AgentRepository, "get_shared_with_me", ("u",), ([], 0)),
    # --- skill_repository ---
    (SkillRepository, "get_user_permission", (1, "u"), None),
    (SkillRepository, "create_skill", (_SKILL_CREATE, "u", "U"), None),
    (SkillRepository, "get_skill_by_id", (1, "u"), None),
    (SkillRepository, "get_skill_by_slug", ("s", "u"), None),
    (SkillRepository, "list_skills", ("u", SkillFilters()), ([], 0)),
    (SkillRepository, "toggle_skill", (1, "u"), None),
    (SkillRepository, "delete_skill", (1, "u"), False),
    (SkillRepository, "share_skill", (1, "o", ["a"]), ([], ["a"])),
    (SkillRepository, "unshare_skill", (1, "o", "t"), False),
    (SkillRepository, "list_skill_shares", (1, "o"), []),
    (SkillRepository, "list_always_apply_skills", ("u",), []),
    (SkillRepository, "list_skill_files", (1,), []),
    (SkillRepository, "upsert_skill_file_text", (1, "a.md", "text"), None),
    (SkillRepository, "get_skill_file_content", (1, "a.md"), None),
    (SkillRepository, "delete_skill_file", (1, 2), False),
    (SkillRepository, "rate_skill", (1, "u", 4), False),
    (SkillRepository, "increment_views", (1,), False),
    (SkillRepository, "increment_usage", (1,), False),
    (SkillRepository, "add_bookmark", (1, "u"), False),
    (SkillRepository, "remove_bookmark", (1, "u"), False),
    (SkillRepository, "get_user_bookmarks", ("u",), ([], 0)),
    # --- entity_settings_repository ---
    (EntitySettingsRepository, "get", ("agent", "1"), None),
    (EntitySettingsRepository, "get_many", ("agent", ["1", "2"]), {}),
    (EntitySettingsRepository, "upsert", ("agent", "1", {"a": 1}), None),
    (EntitySettingsRepository, "delete", ("agent", "1"), False),
    (EntitySettingsRepository, "cleanup_orphans", (), 0),
    (EntitySettingsRepository, "migration_applied", (), True),
    (EntitySettingsRepository, "mark_migration", ({"k": 1},), None),
    (EntitySettingsRepository, "clear_migration_mark", (), None),
    (EntitySettingsRepository, "count_foreign_entity_copies", (), 0),
    (EntitySettingsRepository, "owners_snapshot", (), ([], [])),
    # --- project_repository ---
    (ProjectRepository, "list_by_user", ("u",), []),
    (ProjectRepository, "get_by_id", ("u", "p"), None),
    (ProjectRepository, "create", ("u", ProjectCreate(name="П")), None),
    (ProjectRepository, "update", ("u", "p", ProjectUpdate(name="Н")), None),
    (ProjectRepository, "delete", ("u", "p"), False),
    (ProjectRepository, "delete_by_id", ("p",), False),
    (ProjectRepository, "_delete_entity_settings", ("p",), None),
    # --- user_settings_repository ---
    (UserSettingsRepository, "get", ("u",), None),
    (UserSettingsRepository, "upsert", ("u",), None),
    (UserSettingsRepository, "delete", ("u",), False),
    # --- repository.py ---
    (DocumentRepository, "create_document", (_DOC,), None),
    (DocumentRepository, "get_document", (1,), None),
    (DocumentRepository, "get_all_documents", (), []),
    (DocumentRepository, "delete_document", (1,), False),
    (VectorRepository, "create_vector", (_VEC,), None),
    (VectorRepository, "similarity_search", ([0.1, 0.2],), []),
    (VectorRepository, "similarity_search", ([0.1, 0.2], 5, 3), []),
    (VectorRepository, "get_vectors_by_document", (1,), []),
    (VectorRepository, "delete_vectors_by_document", (1,), False),
]

_IDS = [f"{c[0].__name__}.{c[1]}-{i}" for i, c in enumerate(CASES)]


@pytest.fixture(autouse=True)
def _silence_audit(monkeypatch):
    """CEF-аудит хранилища — отдельный модуль; здесь важен только возврат репозитория."""
    from backend.settings.cef_logger import storage_audit

    monkeypatch.setattr(storage_audit, "log_postgres_success", lambda *a, **k: None)
    monkeypatch.setattr(storage_audit, "log_postgres_failure", lambda *a, **k: None)


@pytest.mark.parametrize("cls,method,args,expected", CASES, ids=_IDS)
async def test_no_pool_returns_safe_default(cls, method, args, expected):
    """Нет пула (acquire бросает) -> метод не падает и отдаёт дефолт."""
    repo = cls(FakeDb().no_pool())
    assert await getattr(repo, method)(*args) == expected


@pytest.mark.parametrize("cls,method,args,expected", CASES, ids=_IDS)
async def test_failing_query_returns_safe_default(cls, method, args, expected):
    """Запрос падает посреди работы -> тот же дефолт, исключение наружу не уходит."""
    repo = cls(FakeDb(FakeConn().fail_all()))
    assert await getattr(repo, method)(*args) == expected


@pytest.mark.parametrize(
    "cls",
    [
        AgentRepository,
        SkillRepository,
        EntitySettingsRepository,
        ProjectRepository,
        UserSettingsRepository,
    ],
)
async def test_create_tables_reraises(cls):
    """create_tables пробрасывает ошибку: миграция схемы не должна проходить молча."""
    repo = cls(FakeDb().no_pool("нет пула"))
    with pytest.raises(RuntimeError, match="нет пула"):
        await repo.create_tables()


@pytest.mark.parametrize("cls", [DocumentRepository, VectorRepository])
async def test_documents_create_tables_propagates_error(cls):
    """create_tables логирует и пробрасывает ошибку (как остальные репозитории),
    чтобы init_postgresql не рапортовал успех при сбое схемы."""
    repo = cls(FakeDb().no_pool())
    with pytest.raises(Exception):
        await repo.create_tables()


@pytest.mark.parametrize("cls", [DocumentRepository, VectorRepository])
async def test_documents_create_tables_survives_extension_failure(cls):
    """Сбой CREATE EXTENSION не мешает создать таблицы: ошибка расширения логируется отдельно."""
    conn = FakeConn()
    calls = []

    async def execute(query, *a):
        calls.append(" ".join(query.split()))
        if "CREATE EXTENSION" in query:
            raise RuntimeError("permission denied")
        return "OK"

    conn.execute.side_effect = execute
    await cls(FakeDb(conn)).create_tables()
    assert any(c.startswith("CREATE EXTENSION") for c in calls)
    assert any("CREATE TABLE IF NOT EXISTS" in c for c in calls)
    assert any("CREATE INDEX IF NOT EXISTS" in c for c in calls)


async def test_vector_create_tables_uses_embedding_dim():
    """Размерность вектора подставляется в DDL таблицы document_vectors."""
    conn = FakeConn()
    await VectorRepository(FakeDb(conn), embedding_dim=7).create_tables()
    ddl = " ".join(" ".join(c.args[0].split()) for c in conn.execute.call_args_list)
    assert "embedding vector(7) NOT NULL" in ddl


async def test_create_vectors_batch_falls_back_to_one_by_one():
    """Сбой пакетной вставки -> последовательные вставки, считаются только успешные."""
    conn = FakeConn()
    conn.execute.side_effect = RuntimeError("batch fail")
    conn.fetchrow.side_effect = [{"id": 10}, RuntimeError("one fail"), {"id": 12}]
    repo = VectorRepository(FakeDb(conn))
    vectors = [
        DocumentVector(document_id=1, chunk_index=i, embedding=[0.5], content="c")
        for i in range(3)
    ]
    assert await repo.create_vectors_batch(vectors) == 2
    assert conn.fetchrow.await_count == 3


async def test_create_vectors_batch_empty_returns_zero_without_db():
    db = FakeDb()
    assert await VectorRepository(db).create_vectors_batch([]) == 0
    db.acquire.assert_not_awaited()
