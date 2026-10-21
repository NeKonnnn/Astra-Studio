"""Сквозной сценарий KbService: файл → парсинг → чанки → эмбеддинги (фейк) → pgvector → поиск → удаление."""

import asyncio

import pytest

from app.database.graph_repository import GraphRepository
from app.database.kb_repository import KbDocumentRepository, KbVectorRepository
from app.services import index_failed_report
from app.services import text_duplicate
from app.services.kb_service import KbService

pytestmark = pytest.mark.integration

DIM = 8
TOPICS = ["кошк", "собак", "птиц", "налог", "космос", "музык", "футбол"]


def vector_for(text: str):
    low = (text or "").lower()
    vec = [float(low.count(t)) for t in TOPICS] + [0.01]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


class FakeRagClient:
    """Детерминированные «эмбеддинги» по тематическим корням слов."""

    def __init__(self):
        self.embed_calls = []
        self.fail = None

    async def embed(self, texts, model=None, kind=None):
        if self.fail:
            raise self.fail
        self.embed_calls.append((list(texts), kind))
        return [vector_for(t) for t in texts]

    async def embed_single(self, text, model=None, kind=None):
        return vector_for(text)

    async def rerank(self, query, documents, **kw):
        raise AssertionError("реранк в этих тестах не запрашивается")


CATS = ("Кошки любят спать. Кошка мурлычет на окне, а кошки охотятся на мышей. " * 6).encode()
DOGS = ("Собаки верные друзья. Собака охраняет дом, а собаки любят гулять во дворе. " * 6).encode()
TAXES = ("Налог на прибыль уплачивается ежеквартально. Налоговая служба проверяет налог. " * 6).encode()


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    for name in ("_SEEN", "_seen", "_REGISTRY", "_registry"):
        store = getattr(text_duplicate, name, None)
        if hasattr(store, "clear"):
            store.clear()
    reports = []

    async def report(**kw):
        reports.append(kw)

    monkeypatch.setattr(index_failed_report, "report_index_failed", report)
    yield reports


@pytest.fixture
async def svc(pg_connection):
    docs = KbDocumentRepository(pg_connection)
    vecs = KbVectorRepository(pg_connection, embedding_dim=DIM)
    graph = GraphRepository(pg_connection)
    await docs.create_tables()
    await vecs.create_tables()
    await graph.create_tables()
    service = KbService(docs, vecs, FakeRagClient(), graph_repo=graph)
    service.fake = service.rag_client
    return service


async def _count(pg_connection, table):
    async with await pg_connection.acquire() as conn:
        return await conn.fetchval(f"SELECT COUNT(*) FROM {table}")


async def test_index_stores_document_vectors_graph_and_metadata(svc, pg_connection):
    res = await svc.index_document(
        CATS, "cats.txt", owner_user_id=" Alice ", uploaded_by="Bob", agent_id=7,
        minio_object="o/cats", minio_bucket="b",
    )
    assert res["ok"] is True and res["chunks_count"] >= 1
    info = await svc.get_document_info(res["document_id"])
    meta = info["metadata"]
    assert meta["owner_user_id"] == "alice" and meta["uploaded_by"] == "bob"
    assert meta["agent_id"] == 7 and meta["size"] == len(CATS) and meta["source"] == "agent"
    assert meta["minio_object"] == "o/cats" and len(meta["content_hash"]) >= 32
    assert await _count(pg_connection, "kb_vectors") == res["chunks_count"]
    assert await _count(pg_connection, "rag_graph_nodes") == res["chunks_count"]
    assert all(kind == "document" for _, kind in svc.fake.embed_calls)


async def test_search_finds_the_relevant_document_first(svc):
    cats = (await svc.index_document(CATS, "cats.txt"))["document_id"]
    dogs = (await svc.index_document(DOGS, "dogs.txt"))["document_id"]
    taxes = (await svc.index_document(TAXES, "taxes.txt"))["document_id"]
    hits = await svc.search("расскажи про кошки", k=3, use_reranking=False)
    assert hits and hits[0][2] == cats
    hits = await svc.search("налоговая проверка", k=3, use_reranking=False)
    assert hits[0][2] == taxes
    only_dogs = await svc.search("кошки", k=5, document_ids=[dogs], use_reranking=False)
    assert {h[2] for h in only_dogs} <= {dogs}


async def test_search_result_shape_and_trace(svc):
    await svc.index_document(CATS, "cats.txt")
    hits, trace = await svc.search("кошки", k=2, use_reranking=False, return_trace=True)
    content, score, doc_id, chunk_idx = hits[0]
    assert isinstance(content, str) and isinstance(score, float)
    assert isinstance(doc_id, int) and isinstance(chunk_idx, int)
    assert trace is not None
    assert len(hits) <= 2


async def test_search_on_empty_base_returns_nothing(svc):
    assert await svc.search("что угодно", k=5, use_reranking=False) == []


async def test_byte_duplicate_is_not_indexed_twice(svc, pg_connection):
    first = await svc.index_document(CATS, "cats.txt")
    again = await svc.index_document(CATS, "other-name.txt")
    assert again["document_id"] == first["document_id"]
    assert again.get("duplicate") is True or again.get("skipped") or again.get("ok") is not None
    assert await _count(pg_connection, "kb_documents") == 1


async def test_same_text_in_other_bytes_is_detected(svc, pg_connection):
    await svc.index_document(CATS, "cats.txt")
    await svc.index_document(CATS + b"\n", "cats-copy.txt")
    assert await _count(pg_connection, "kb_documents") == 1


async def test_same_file_for_another_agent_is_allowed(svc, pg_connection):
    a = await svc.index_document(CATS, "cats.txt", agent_id=1)
    b = await svc.index_document(CATS, "cats.txt", agent_id=2)
    assert a["ok"] and b["ok"] and a["document_id"] != b["document_id"]
    assert await _count(pg_connection, "kb_documents") == 2


async def test_check_duplicate_reports_existing(svc):
    first = await svc.index_document(CATS, "cats.txt", agent_id=3)
    from app.services.document_duplicate import file_content_hash

    got = await svc.check_duplicate(content_hash=file_content_hash(CATS), agent_id=3)
    assert got == {"duplicate": True, "document_id": first["document_id"], "filename": "cats.txt"}
    assert await svc.check_duplicate(content_hash="deadbeef", agent_id=3) == {"duplicate": False}


async def test_empty_and_unsupported_files_create_nothing(svc, pg_connection):
    empty = await svc.index_document(b"   \n  ", "empty.txt")
    assert empty["ok"] is False and empty["document_id"] is None
    unsupported = await svc.index_document(b"\x00\x01\x02", "file.xyz")
    assert unsupported["ok"] is False
    assert await _count(pg_connection, "kb_documents") == 0


async def test_embedding_failure_removes_document_and_reports_to_backend(
    svc, pg_connection, _isolated_state
):
    svc.fake.fail = ConnectionError("models down")
    res = await svc.index_document(CATS, "cats.txt", minio_object="o/cats", minio_bucket="b")
    assert res["ok"] is False and "Ошибка эмбеддингов" in res["error"] and res["document_id"] is None
    assert await _count(pg_connection, "kb_documents") == 0
    assert await _count(pg_connection, "kb_vectors") == 0
    assert _isolated_state and _isolated_state[0]["minio_object"] == "o/cats"
    assert "models down" in _isolated_state[0]["reason"]


async def test_partial_batch_failure_leaves_no_vectors(svc, pg_connection, monkeypatch):
    monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", "2")
    big = ("\n\n".join(f"Абзац {i}. " + "кошки " * 80 for i in range(12))).encode()
    calls = {"n": 0}
    real = svc.fake.embed

    async def flaky(texts, model=None, kind=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise TimeoutError("second wave")
        return await real(texts, model=model, kind=kind)

    svc.fake.embed = flaky
    res = await svc.index_document(big, "big.txt", chunk_size=300, chunk_overlap=0)
    assert res["ok"] is False
    assert calls["n"] == 2
    assert await _count(pg_connection, "kb_vectors") == 0
    assert await _count(pg_connection, "kb_documents") == 0


async def test_multi_wave_indexing_stores_contiguous_chunk_indexes(svc, pg_connection, monkeypatch):
    monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", "3")
    big = ("\n\n".join(f"Абзац {i}. " + "собаки " * 80 for i in range(10))).encode()
    res = await svc.index_document(big, "big.txt", chunk_size=300, chunk_overlap=0)
    assert res["ok"] and res["chunks_count"] > 3
    async with await pg_connection.acquire() as conn:
        idx = [r["chunk_index"] for r in await conn.fetch("SELECT chunk_index FROM kb_vectors ORDER BY 1")]
    assert idx == list(range(res["chunks_count"]))
    assert len(svc.fake.embed_calls) == -(-res["chunks_count"] // 3)


async def test_delete_removes_everything_and_returns_storage_keys(svc, pg_connection):
    res = await svc.index_document(CATS, "cats.txt", minio_object="o/cats", minio_bucket="b")
    await svc.search("кошки", k=2, use_reranking=False)  # прогреть BM25-кэш
    out = await svc.delete_document(res["document_id"])
    assert out == {"ok": True, "document_id": res["document_id"], "minio_object": "o/cats", "minio_bucket": "b"}
    for table in ("kb_documents", "kb_vectors", "rag_graph_nodes"):
        assert await _count(pg_connection, table) == 0
    assert await svc.search("кошки", k=2, use_reranking=False) == []
    assert await svc.delete_document(res["document_id"]) == {"ok": False, "document_id": res["document_id"]}


async def test_delete_one_document_keeps_the_other_searchable(svc):
    cats = (await svc.index_document(CATS, "cats.txt"))["document_id"]
    dogs = (await svc.index_document(DOGS, "dogs.txt"))["document_id"]
    await svc.delete_document(cats)
    hits = await svc.search("собаки", k=3, use_reranking=False)
    assert hits and {h[2] for h in hits} == {dogs}


async def test_list_documents_brief(svc):
    await svc.index_document(CATS, "cats.txt", agent_id=4)
    items = await svc.list_documents()
    assert len(items) == 1 and items[0]["filename"] == "cats.txt"
    assert items[0]["size"] == str(len(CATS)) and items[0]["metadata"] == {"agent_id": 4}


async def test_reindex_document_replaces_vectors_with_new_chunking(svc, pg_connection):
    big = ("\n\n".join(f"Абзац {i}. " + "птицы " * 80 for i in range(8))).encode()
    res = await svc.index_document(big, "big.txt", chunk_size=2000, chunk_overlap=0)
    before = await _count(pg_connection, "kb_vectors")
    n = await svc.reindex_document(res["document_id"], chunk_size=300, chunk_overlap=0)
    after = await _count(pg_connection, "kb_vectors")
    assert n == after and after > before


async def test_reindex_missing_document_is_a_noop(svc):
    assert await svc.reindex_document(999999) == 0


async def test_hierarchical_strategy_indexes_summaries_and_chunks(svc, pg_connection):
    text = ("\n\n".join(f"Раздел {i}. " + "космос " * 90 for i in range(10))).encode()
    res = await svc.index_document(
        text, "space.txt", chunking_strategy="hierarchical", chunk_size=400, chunk_overlap=0
    )
    assert res["ok"] and res["chunks_count"] > 1
    async with await pg_connection.acquire() as conn:
        kinds = {
            r["t"]: r["c"]
            for r in await conn.fetch(
                "SELECT metadata->>'type' AS t, COUNT(*) AS c FROM kb_vectors GROUP BY 1"
            )
        }
        negatives = await conn.fetchval("SELECT COUNT(*) FROM kb_vectors WHERE chunk_index < 0")
    assert kinds["detail_chunk"] == res["chunks_count"]
    assert kinds["full_summary"] == 1 and kinds["intermediate_summary"] >= 1
    assert negatives == kinds["full_summary"] + kinds["intermediate_summary"]


async def test_clone_document_copies_indexed_document(svc):
    res = await svc.index_document(CATS, "cats.txt")
    cloned = await svc.clone_document(res["document_id"], agent_id=9)
    assert cloned["ok"] is True
    assert cloned["document_id"] != res["document_id"]
    assert cloned["source_document_id"] == res["document_id"]


async def test_clone_of_missing_document(svc):
    assert (await svc.clone_document(999999))["error"] == "not_found"


async def test_concurrent_upload_of_identical_files(svc, pg_connection):
    results = await asyncio.gather(
        *(svc.index_document(CATS, f"cats-{i}.txt") for i in range(4)), return_exceptions=True
    )
    assert not [r for r in results if isinstance(r, Exception)], results
    assert any(isinstance(r, dict) and r.get("ok") for r in results)
    docs = await _count(pg_connection, "kb_documents")
    assert docs == 1, f"осталось документов: {docs}"


async def test_filename_mention_pulls_that_document(svc):
    await svc.index_document(CATS, "Годовой отчёт.txt")
    dogs = (await svc.index_document(DOGS, "dogs.txt"))["document_id"]
    hits = await svc.search("что в файле dogs.txt?", k=5, use_reranking=False)
    assert dogs in {h[2] for h in hits}
