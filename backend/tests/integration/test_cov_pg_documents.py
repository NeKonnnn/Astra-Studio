"""Реальный PostgreSQL + pgvector: DocumentRepository и VectorRepository (repository.py)."""

from datetime import datetime, timedelta

import pytest

from backend.database.postgresql.models import Document, DocumentVector
from backend.database.postgresql.repository import DocumentRepository, VectorRepository

pytestmark = pytest.mark.integration

DIM = 3


@pytest.fixture
async def docs(pg_connection):
    repo = DocumentRepository(pg_connection)
    await repo.create_tables()
    return repo


@pytest.fixture
async def vectors(pg_connection, docs):
    repo = VectorRepository(pg_connection, embedding_dim=DIM)
    await repo.create_tables()
    return repo


def _vec(document_id, index, embedding, content="чанк", **meta):
    return DocumentVector(
        document_id=document_id,
        chunk_index=index,
        embedding=embedding,
        content=content,
        metadata=meta,
    )


async def test_create_tables_is_idempotent(pg_connection, docs, vectors):
    await docs.create_tables()
    await vectors.create_tables()
    async with await pg_connection.acquire() as conn:
        tables = {
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
    assert {"documents", "document_vectors"} <= tables


async def test_document_roundtrip_with_metadata(docs):
    doc_id = await docs.create_document(
        Document(
            filename="отчёт.pdf",
            content="Текст отчёта",
            metadata={"pages": 3, "тип": "pdf"},
        )
    )
    assert isinstance(doc_id, int)
    loaded = await docs.get_document(doc_id)
    assert loaded.id == doc_id
    assert loaded.filename == "отчёт.pdf" and loaded.content == "Текст отчёта"
    assert loaded.metadata == {"pages": 3, "тип": "pdf"}


async def test_document_without_metadata_gets_empty_dict(docs):
    doc_id = await docs.create_document(Document(filename="a.txt", content="x"))
    assert (await docs.get_document(doc_id)).metadata == {}


async def test_get_missing_document_returns_none(docs):
    assert await docs.get_document(987654) is None


async def test_get_all_documents_newest_first_and_limit(docs):
    base = datetime(2024, 1, 1)
    ids = []
    for i in range(3):
        ids.append(
            await docs.create_document(
                Document(
                    filename=f"f{i}.txt",
                    content="x",
                    created_at=base + timedelta(days=i),
                    updated_at=base + timedelta(days=i),
                )
            )
        )
    listed = await docs.get_all_documents()
    assert [d.id for d in listed] == list(reversed(ids))
    assert [d.id for d in await docs.get_all_documents(limit=2)] == ids[:0:-1]


async def test_get_all_documents_decodes_metadata(docs):
    await docs.create_document(Document(filename="a", content="x", metadata={"k": [1, 2]}))
    (doc,) = await docs.get_all_documents()
    assert doc.metadata == {"k": [1, 2]}


async def test_null_metadata_is_returned_as_empty_dict(docs, pg_connection):
    """NULL в колонке metadata (ручная правка/старые данные) превращается в {}."""
    doc_id = await docs.create_document(Document(filename="a", content="x"))
    async with await pg_connection.acquire() as conn:
        await conn.execute("UPDATE documents SET metadata = NULL WHERE id = $1", doc_id)
    assert (await docs.get_document(doc_id)).metadata == {}
    assert (await docs.get_all_documents())[0].metadata == {}


async def test_delete_document_removes_it(docs):
    doc_id = await docs.create_document(Document(filename="a", content="x"))
    assert await docs.delete_document(doc_id) is True
    assert await docs.get_document(doc_id) is None
    # повторное удаление несуществующего — тоже успех (DELETE без строк не ошибка)
    assert await docs.delete_document(doc_id) is True


async def test_create_document_too_long_filename_returns_none(docs):
    """filename — VARCHAR(255): слишком длинное имя -> ошибка БД, метод возвращает None."""
    assert await docs.create_document(Document(filename="я" * 300, content="x")) is None


async def test_create_vector_and_read_back(docs, vectors):
    doc_id = await docs.create_document(Document(filename="a", content="x"))
    vec_id = await vectors.create_vector(_vec(doc_id, 0, [0.5, 0.25, 1.0], start=0))
    assert isinstance(vec_id, int)
    (stored,) = await vectors.get_vectors_by_document(doc_id)
    assert stored.id == vec_id
    assert stored.embedding == pytest.approx([0.5, 0.25, 1.0])
    assert stored.metadata == {"start": 0}
    assert stored.content == "чанк"


async def test_create_vector_duplicate_chunk_and_wrong_dimension_return_none(docs, vectors):
    doc_id = await docs.create_document(Document(filename="a", content="x"))
    assert await vectors.create_vector(_vec(doc_id, 0, [1, 0, 0])) is not None
    assert await vectors.create_vector(_vec(doc_id, 0, [0, 1, 0])) is None  # UNIQUE
    assert await vectors.create_vector(_vec(doc_id, 1, [1, 0])) is None  # размерность
    assert await vectors.create_vector(_vec(999999, 0, [1, 0, 0])) is None  # нет документа
    assert len(await vectors.get_vectors_by_document(doc_id)) == 1


async def test_get_vectors_by_document_ordered_and_null_metadata(docs, vectors, pg_connection):
    doc_id = await docs.create_document(Document(filename="a", content="x"))
    await vectors.create_vector(_vec(doc_id, 2, [0, 0, 1]))
    await vectors.create_vector(_vec(doc_id, 0, [1, 0, 0]))
    async with await pg_connection.acquire() as conn:
        await conn.execute("UPDATE document_vectors SET metadata = NULL")
    result = await vectors.get_vectors_by_document(doc_id)
    assert [v.chunk_index for v in result] == [0, 2]
    assert all(v.metadata == {} for v in result)


async def test_create_vectors_batch_inserts_all(docs, vectors):
    doc_id = await docs.create_document(Document(filename="a", content="x"))
    batch = [_vec(doc_id, i, [i, 1, 0], f"чанк {i}") for i in range(4)]
    assert await vectors.create_vectors_batch(batch) == 4
    stored = await vectors.get_vectors_by_document(doc_id)
    assert [v.content for v in stored] == [f"чанк {i}" for i in range(4)]


async def test_create_vectors_batch_falls_back_to_single_inserts(docs, vectors):
    """Дубликат в пакете ломает одну INSERT; откат на поштучную вставку сохраняет валидные."""
    doc_id = await docs.create_document(Document(filename="a", content="x"))
    batch = [
        _vec(doc_id, 0, [1, 0, 0]),
        _vec(doc_id, 1, [0, 1, 0]),
        _vec(doc_id, 1, [0, 0, 1]),
    ]
    assert await vectors.create_vectors_batch(batch) == 2
    stored = await vectors.get_vectors_by_document(doc_id)
    assert [v.chunk_index for v in stored] == [0, 1]
    assert stored[1].embedding == pytest.approx([0, 1, 0])


async def test_similarity_search_orders_by_cosine_and_filters_by_document(docs, vectors):
    d1 = await docs.create_document(Document(filename="a", content="x"))
    d2 = await docs.create_document(Document(filename="b", content="y"))
    await vectors.create_vectors_batch(
        [
            _vec(d1, 0, [1, 0, 0], "ближайший"),
            _vec(d1, 1, [0, 1, 0], "ортогональный"),
            _vec(d2, 0, [1, 1, 0], "средний", src="b"),
        ]
    )
    found = await vectors.similarity_search([1, 0, 0], limit=10)
    assert [v.content for v, _ in found] == ["ближайший", "средний", "ортогональный"]
    scores = [s for _, s in found]
    assert scores[0] == pytest.approx(1.0)
    assert scores[1] == pytest.approx(0.7071, abs=1e-3)
    assert scores[2] == pytest.approx(0.0, abs=1e-6)
    assert found[1][0].metadata == {"src": "b"}

    only_d2 = await vectors.similarity_search([1, 0, 0], limit=10, document_id=d2)
    assert [v.document_id for v, _ in only_d2] == [d2]

    assert len(await vectors.similarity_search([1, 0, 0], limit=1)) == 1


async def test_similarity_search_decodes_null_metadata(docs, vectors, pg_connection):
    doc_id = await docs.create_document(Document(filename="a", content="x"))
    await vectors.create_vector(_vec(doc_id, 0, [1, 0, 0]))
    async with await pg_connection.acquire() as conn:
        await conn.execute("UPDATE document_vectors SET metadata = NULL")
    ((vector, _),) = await vectors.similarity_search([1, 0, 0])
    assert vector.metadata == {}


async def test_similarity_search_wrong_dimension_returns_empty(docs, vectors):
    doc_id = await docs.create_document(Document(filename="a", content="x"))
    await vectors.create_vector(_vec(doc_id, 0, [1, 0, 0]))
    assert await vectors.similarity_search([1, 0]) == []


async def test_delete_vectors_by_document_leaves_other_documents(docs, vectors):
    d1 = await docs.create_document(Document(filename="a", content="x"))
    d2 = await docs.create_document(Document(filename="b", content="y"))
    await vectors.create_vectors_batch([_vec(d1, 0, [1, 0, 0]), _vec(d2, 0, [0, 1, 0])])
    assert await vectors.delete_vectors_by_document(d1) is True
    assert await vectors.get_vectors_by_document(d1) == []
    assert len(await vectors.get_vectors_by_document(d2)) == 1


async def test_deleting_document_cascades_to_vectors(docs, vectors):
    doc_id = await docs.create_document(Document(filename="a", content="x"))
    await vectors.create_vector(_vec(doc_id, 0, [1, 0, 0]))
    assert await docs.delete_document(doc_id) is True
    assert await vectors.get_vectors_by_document(doc_id) == []
