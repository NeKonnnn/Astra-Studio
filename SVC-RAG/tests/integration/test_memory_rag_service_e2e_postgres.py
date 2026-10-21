"""MemoryRagService (общая Библиотека): сквозной сценарий на pgvector и настройки из окружения."""

import pytest

from app.database.graph_repository import GraphRepository
from app.database.memory_rag_repository import (
    MemoryRagDocumentRepository,
    MemoryRagVectorRepository,
)
from app.services import memory_rag_service as mrs
from app.services import text_duplicate
from app.services.memory_rag_service import MemoryRagService

pytestmark = pytest.mark.integration

DIM = 8
TOPICS = ["кошк", "собак", "птиц", "налог", "космос", "музык", "футбол"]


def vector_for(text: str):
    low = (text or "").lower()
    vec = [float(low.count(t)) for t in TOPICS] + [0.01]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


class FakeRagClient:
    def __init__(self):
        self.fail = None

    async def embed(self, texts, model=None, kind=None):
        if self.fail:
            raise self.fail
        return [vector_for(t) for t in texts]

    async def embed_single(self, text, model=None, kind=None):
        return vector_for(text)


CATS = ("Кошки любят спать. Кошка мурлычет на окне, а кошки охотятся на мышей. " * 6).encode()
DOGS = ("Собаки верные друзья. Собака охраняет дом, а собаки любят гулять во дворе. " * 6).encode()


@pytest.fixture(autouse=True)
def _clean_text_registry():
    for name in ("_SEEN", "_seen", "_REGISTRY", "_registry"):
        store = getattr(text_duplicate, name, None)
        if hasattr(store, "clear"):
            store.clear()


@pytest.fixture
async def svc(pg_connection):
    docs = MemoryRagDocumentRepository(pg_connection)
    vecs = MemoryRagVectorRepository(pg_connection, embedding_dim=DIM)
    graph = GraphRepository(pg_connection)
    await docs.create_tables()
    await vecs.create_tables()
    await graph.create_tables()
    service = MemoryRagService(docs, vecs, FakeRagClient(), graph_repo=graph)
    service.fake = service.rag_client
    return service


async def _count(pg_connection, table, where="TRUE"):
    async with await pg_connection.acquire() as conn:
        return await conn.fetchval(f"SELECT COUNT(*) FROM {table} WHERE {where}")


async def test_index_search_delete_cycle(svc, pg_connection):
    cats = await svc.index_document(CATS, "cats.txt", "o/cats", "b")
    dogs = await svc.index_document(DOGS, "dogs.txt")
    assert cats["ok"] and dogs["ok"] and cats["chunks_count"] >= 1
    hits = await svc.search("кошки", k=3, use_reranking=False)
    assert hits and hits[0][2] == cats["document_id"]
    assert (await svc.search("собаки", k=3, use_reranking=False, document_id=dogs["document_id"]))[0][2] == dogs["document_id"]
    out = await svc.delete_document(cats["document_id"])
    assert out == {"ok": True, "document_id": cats["document_id"], "minio_object": "o/cats", "minio_bucket": "b"}
    assert {h[2] for h in await svc.search("кошки собаки", k=5, use_reranking=False)} == {dogs["document_id"]}
    assert await _count(pg_connection, "rag_graph_nodes", "store_type = 'memory'") == dogs["chunks_count"]
    assert await svc.delete_document(cats["document_id"]) == {"ok": False, "error": "not_found"}


async def test_library_is_global_without_scope(svc):
    a = await svc.index_document(CATS, "cats.txt")
    b = await svc.index_document(CATS, "cats-again.txt")
    assert b["document_id"] == a["document_id"]
    # у документа есть content_hash — запасной путь «имя + размер» к нему не применяется
    assert await svc.check_duplicate(filename="cats.txt", size=len(CATS)) == {"duplicate": False}
    assert len(await svc.list_documents()) == 1


async def test_embedding_failure_cleans_everything(svc, pg_connection):
    svc.fake.fail = ConnectionError("models down")
    res = await svc.index_document(CATS, "cats.txt", "o/cats", "b")
    assert res["ok"] is False and res["document_id"] is None
    assert await _count(pg_connection, "memory_rag_documents") == 0
    assert await _count(pg_connection, "memory_rag_vectors") == 0


async def test_empty_file_creates_nothing(svc, pg_connection):
    assert (await svc.index_document(b"   ", "empty.txt"))["ok"] is False
    assert await _count(pg_connection, "memory_rag_documents") == 0


async def test_hierarchical_strategy_comes_only_from_env(svc, pg_connection, monkeypatch):
    text = ("\n\n".join(f"Раздел {i}. " + "космос " * 90 for i in range(8))).encode()
    # параметр вызова игнорируется: Библиотека читает стратегию только из env
    res = await svc.index_document(text, "space.txt", chunking_strategy="hierarchical", chunk_size=400, chunk_overlap=0)
    assert res["ok"]
    assert await _count(pg_connection, "memory_rag_vectors", "chunk_index < 0") == 0
    monkeypatch.setenv("RAG_MEMORY_CHUNKING_STRATEGY", "hierarchical")
    monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "400")
    monkeypatch.setenv("RAG_MEMORY_CHUNK_OVERLAP", "0")
    other = ("\n\n".join(f"Часть {i}. " + "музыка " * 90 for i in range(8))).encode()
    res2 = await svc.index_document(other, "music.txt")
    assert res2["ok"] and res2["chunks_count"] > 1
    assert await _count(pg_connection, "memory_rag_vectors", "chunk_index < 0") >= 2


async def test_reindex_document_and_reindex_all(svc, pg_connection, monkeypatch):
    big = ("\n\n".join(f"Абзац {i}. " + "птицы " * 80 for i in range(8))).encode()
    monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "2000")
    monkeypatch.setenv("RAG_MEMORY_CHUNK_OVERLAP", "0")
    doc = (await svc.index_document(big, "big.txt"))["document_id"]
    before = await _count(pg_connection, "memory_rag_vectors")
    monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "300")
    n = await svc.reindex_document(doc)
    assert n > before and await _count(pg_connection, "memory_rag_vectors") == n
    assert await svc.reindex_document(999999) == 0
    result = await svc.reindex_all(chunk_size=300, chunk_overlap=0)
    assert result["documents"] == 1 and result["chunks"] >= 1


async def test_reindex_all_ignores_ui_chunk_params_known_issue(svc, pg_connection, monkeypatch):
    """Библиотека нарезается только по env ``RAG_MEMORY_*`` — параметры вызова
    молча игнорируются (так задумано, но легко принять за баг: UI-настройки до Библиотеки не доходят)."""
    big = ("\n\n".join(f"Абзац {i}. " + "птицы " * 80 for i in range(8))).encode()
    await svc.index_document(big, "big.txt", chunk_size=2000, chunk_overlap=0)
    monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "2000")
    monkeypatch.setenv("RAG_MEMORY_CHUNK_OVERLAP", "0")
    first = await svc.reindex_all(chunk_size=200, chunk_overlap=0)
    monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "300")
    second = await svc.reindex_all(chunk_size=2000, chunk_overlap=0)
    assert second["chunks"] > first["chunks"]


async def test_reindex_all_stops_when_generation_is_outdated(svc):
    await svc.index_document(CATS, "cats.txt")
    await svc.index_document(DOGS, "dogs.txt")
    gen = mrs.bump_memory_reindex_generation()
    mrs.bump_memory_reindex_generation()
    result = await svc.reindex_all(generation=gen)
    assert result["documents"] == 0


# ------------------------------------------------------------- env helpers (pure)


class TestMemoryChunkParams:
    def test_defaults(self, monkeypatch):
        for n in ("RAG_MEMORY_CHUNK_SIZE", "RAG_MEMORY_CHUNK_OVERLAP", "RAG_MEMORY_CHUNKING_STRATEGY"):
            monkeypatch.delenv(n, raising=False)
        assert mrs._memory_chunk_params() == (None, None, None)

    def test_values(self, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", " 800 ")
        monkeypatch.setenv("RAG_MEMORY_CHUNK_OVERLAP", "50")
        monkeypatch.setenv("RAG_MEMORY_CHUNKING_STRATEGY", " hierarchical ")
        assert mrs._memory_chunk_params() == (800, 50, "hierarchical")

    def test_garbage_numbers_fall_back(self, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "abc")
        monkeypatch.setenv("RAG_MEMORY_CHUNK_OVERLAP", "1.5")
        assert mrs._memory_chunk_params()[:2] == (None, None)


class TestMemoryEmbeddingChoice:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, (None, None)),
            ("", (None, None)),
            ("  ", (None, None)),
            ("phoenix/emb-1", ("PHOENIX", "emb-1")),
            ("PHOENIX_Embeddings/bge", ("PHOENIX_Embeddings", "bge")),
            ("corsur/e5", ("CORSUR", "e5")),
            ("local/my-model", ("native", "my-model")),
            ("plain-model", ("native", "plain-model")),
            ("phoenix/", (None, None)),
            ("local/", (None, None)),
        ],
    )
    def test_formats(self, monkeypatch, raw, expected):
        for n in ("RAG_PHOENIX_PROVIDER_ID", "RAG_PHOENIX_EMBEDDINGS_PROVIDER_ID", "RAG_CORSUR_PROVIDER_ID"):
            monkeypatch.delenv(n, raising=False)
        if raw is None:
            monkeypatch.delenv("RAG_MEMORY_EMBEDDING_MODEL", raising=False)
        else:
            monkeypatch.setenv("RAG_MEMORY_EMBEDDING_MODEL", raw)
        assert mrs.memory_embedding_choice() == expected

    def test_provider_ids_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_EMBEDDING_MODEL", "phoenix/x")
        monkeypatch.setenv("RAG_PHOENIX_PROVIDER_ID", "MY_GW")
        assert mrs.memory_embedding_choice() == ("MY_GW", "x")


class TestDocActors:
    def test_object_and_dict(self):
        class Doc:
            filename = "a.txt"
            metadata = {"owner_user_id": " Alice ", "uploaded_by": "bob"}

        assert mrs._doc_actors(Doc()) == ("Alice", "bob", "a.txt")
        assert mrs._doc_actors({"name": "n", "metadata": '{"owner_user_id": "x"}'}) == ("x", "-", "n")

    def test_missing_everything(self):
        assert mrs._doc_actors({}) == ("-", "-", "?")
        assert mrs._doc_actors({"metadata": "not json"}) == ("-", "-", "?")

    def test_generation_counter(self):
        before = mrs.current_memory_reindex_generation()
        assert mrs.bump_memory_reindex_generation() == before + 1
        assert mrs.current_memory_reindex_generation() == before + 1
