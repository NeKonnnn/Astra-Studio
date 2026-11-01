"""repository.py на фейковом соединении: metadata, уже разобранная в dict (кодек JSONB в пуле)."""

from __future__ import annotations

from datetime import datetime

from backend.database.postgresql.repository import DocumentRepository, VectorRepository
from backend.tests.unit.database_pg_cov.fakes import FakeConn, FakeDb

NOW = datetime(2024, 1, 1)


def _doc_row(metadata):
    return {
        "id": 1,
        "filename": "a.txt",
        "content": "x",
        "metadata": metadata,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _vec_row(metadata):
    return {
        "id": 1,
        "document_id": 2,
        "chunk_index": 0,
        "embedding": "[0.5, 1.5]",
        "content": "c",
        "metadata": metadata,
        "similarity": 0.75,
    }


async def test_documents_accept_metadata_already_decoded():
    conn = FakeConn()
    conn.fetchrow.return_value = _doc_row({"k": 1})
    conn.fetch.return_value = [_doc_row({"k": 2})]
    repo = DocumentRepository(FakeDb(conn))
    assert (await repo.get_document(1)).metadata == {"k": 1}
    assert (await repo.get_all_documents())[0].metadata == {"k": 2}


async def test_vectors_accept_metadata_already_decoded():
    conn = FakeConn()
    conn.fetch.return_value = [_vec_row({"s": 1})]
    repo = VectorRepository(FakeDb(conn))
    ((vector, score),) = await repo.similarity_search([0.1, 0.2], limit=3)
    assert vector.metadata == {"s": 1} and vector.embedding == [0.5, 1.5]
    assert score == 0.75
    (stored,) = await repo.get_vectors_by_document(2)
    assert stored.metadata == {"s": 1} and stored.document_id == 2


async def test_similarity_search_query_depends_on_document_filter():
    conn = FakeConn()
    repo = VectorRepository(FakeDb(conn))
    await repo.similarity_search([1.0], limit=4)
    assert "WHERE document_id" not in conn.fetch.await_args.args[0]
    assert conn.fetch.await_args.args[1:] == ("[1.0]", 4)
    await repo.similarity_search([1.0], limit=4, document_id=9)
    assert "WHERE document_id = $2" in conn.fetch.await_args.args[0]
    assert conn.fetch.await_args.args[1:] == ("[1.0]", 9, 4)
