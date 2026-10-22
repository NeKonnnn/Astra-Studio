"""Фикстуры юнит-тестов сервисов RAG (R1): сервисы на in-memory репозиториях, без БД и сети."""

import pytest

from app.core.config import get_settings

from app.services import index_failed_report, text_duplicate
from app.services.kb_service import KbService
from app.services.memory_rag_service import MemoryRagService
from app.services.project_rag_service import ProjectRagService

from .fakes import (
    FakeGraphRepo,
    FakeKbDocRepo,
    FakeMemoryDocRepo,
    FakeProjectDocRepo,
    FakeRagClient,
    FakeVectorRepo,
)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Кэш отпечатков текста — глобальный: чистим, чтобы тесты не влияли друг на друга."""
    text_duplicate._fingerprints.clear()
    monkeypatch.setenv("RAG_MEM_RELEASE", "false")
    for name in (
        "RAG_MEMORY_CHUNK_SIZE",
        "RAG_MEMORY_CHUNK_OVERLAP",
        "RAG_MEMORY_CHUNKING_STRATEGY",
        "RAG_MEMORY_EMBEDDING_MODEL",
        "RAG_INDEX_CHUNKS_PER_BATCH",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    text_duplicate._fingerprints.clear()


@pytest.fixture
def failed_reports(monkeypatch):
    """Перехват report_index_failed: сеть к backend не нужна, вызовы видны тесту."""
    calls = []

    async def fake_report(**kw):
        calls.append(kw)

    monkeypatch.setattr(index_failed_report, "report_index_failed", fake_report)
    return calls


@pytest.fixture
def rerank_on(monkeypatch):
    """Включить реранк в конфиге; кириллический запрос не гасит его (RAG_FORCE_RERANK_CYRILLIC)."""
    monkeypatch.setattr(get_settings().rag, "use_reranking", True)
    monkeypatch.setenv("RAG_FORCE_RERANK_CYRILLIC", "1")


@pytest.fixture
def client():
    return FakeRagClient()


@pytest.fixture
def graph():
    return FakeGraphRepo()


@pytest.fixture
def kb(client, graph):
    docs = FakeKbDocRepo()
    vecs = FakeVectorRepo(doc_repo=docs)
    svc = KbService(docs, vecs, client, graph_repo=graph)
    return svc


@pytest.fixture
def kb_nograph(client):
    docs = FakeKbDocRepo()
    return KbService(docs, FakeVectorRepo(doc_repo=docs), client)


@pytest.fixture
def memory(client, graph):
    docs = FakeMemoryDocRepo()
    return MemoryRagService(docs, FakeVectorRepo(doc_repo=docs), client, graph_repo=graph)


@pytest.fixture
def memory_nograph(client):
    docs = FakeMemoryDocRepo()
    return MemoryRagService(docs, FakeVectorRepo(doc_repo=docs), client)


@pytest.fixture
def project(client, graph):
    docs = FakeProjectDocRepo()
    return ProjectRagService(docs, FakeVectorRepo(doc_repo=docs), client, graph_repo=graph)


@pytest.fixture
def project_nograph(client):
    docs = FakeProjectDocRepo()
    return ProjectRagService(docs, FakeVectorRepo(doc_repo=docs), client)
