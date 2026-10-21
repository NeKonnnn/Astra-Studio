"""KbDocumentRepository / KbVectorRepository на реальном PostgreSQL с pgvector."""

from datetime import datetime, timedelta

import pytest

from app.database.kb_repository import KbDocumentRepository, KbVectorRepository
from app.database.models import Document, DocumentVector
from app.database.search_filters import DocumentVectorSearchFilters

pytestmark = pytest.mark.integration

DIM = 4


@pytest.fixture
async def docs(pg_connection):
    repo = KbDocumentRepository(pg_connection)
    await repo.create_tables()
    return repo


@pytest.fixture
async def vecs(pg_connection, docs):
    repo = KbVectorRepository(pg_connection, embedding_dim=DIM)
    await repo.create_tables()
    return repo


async def _doc(docs, filename="a.txt", metadata=None, **kw):
    return await docs.create_document(
        Document(filename=filename, content=kw.pop("content", "тело"), metadata=metadata or {}, **kw)
    )


def _vec(doc_id, idx, emb, content, meta=None):
    return DocumentVector(
        document_id=doc_id, chunk_index=idx, embedding=emb, content=content, metadata=meta or {}
    )


# -------------------------------------------------------------------- documents


async def test_document_roundtrip(docs):
    doc_id = await _doc(docs, "отчёт.docx", {"size": 10, "k": [1, 2]}, content="Привет")
    got = await docs.get_document(doc_id)
    assert (got.filename, got.content) == ("отчёт.docx", "Привет")
    assert got.metadata == {"size": 10, "k": [1, 2]}
    assert (await docs.get_document_by_filename("отчёт.docx")).id == doc_id
    assert await docs.get_document(999999) is None
    assert await docs.get_document_by_filename("нет") is None


async def test_null_bytes_are_stripped_on_write(docs):
    doc_id = await _doc(docs, "a\x00.txt", content="te\x00xt")
    got = await docs.get_document(doc_id)
    assert (got.filename, got.content) == ("a.txt", "text")


async def test_listing_methods_do_not_return_content(docs):
    await _doc(docs, "a.txt", {"size": 5, "file_type": "txt", "agent_id": 7})
    await _doc(docs, "b.txt", {"agent_id": "x"})
    allx = await docs.get_all_documents()
    assert len(allx) == 2 and all(d.content is None for d in allx)
    brief = {d["filename"]: d for d in await docs.list_documents_brief()}
    assert brief["a.txt"]["metadata"] == {"agent_id": 7}
    assert brief["a.txt"]["size"] == "5" and brief["a.txt"]["file_type"] == "txt"
    assert "metadata" not in brief["b.txt"]  # нечисловой agent_id отбрасывается
    assert brief["b.txt"]["size"] == "0"


async def test_get_documents_by_ids_and_owner(docs):
    a = await _doc(docs, "a.txt", {"owner_user_id": " Alice "})
    b = await _doc(docs, "b.txt", {"owner_user_id": "bob"})
    assert {d.id for d in await docs.get_documents_by_ids([a, b, None, 999])} == {a, b}
    assert await docs.get_documents_by_ids([]) == []
    assert [d.id for d in await docs.get_documents_by_owner("ALICE")] == [a]
    assert await docs.get_documents_by_owner("  ") == []


async def test_find_duplicate_by_hash_and_by_name_size(docs):
    a = await _doc(docs, "a.txt", {"content_hash": "ABC123", "agent_id": 5})
    legacy = await _doc(docs, "Old.TXT", {"size": 42})
    shared = await _doc(docs, "shared.txt", {"content_hash": "feed"})
    assert (await docs.find_duplicate(content_hash="abc123", agent_id=5))["id"] == a
    assert await docs.find_duplicate(content_hash="abc123", agent_id=6) is None
    assert await docs.find_duplicate(content_hash="abc123") is None  # у a есть agent_id
    assert (await docs.find_duplicate(content_hash="FEED"))["id"] == shared
    assert (await docs.find_duplicate(filename="old.txt", size=42))["id"] == legacy
    assert await docs.find_duplicate(filename="old.txt", size=43) is None
    assert await docs.find_duplicate(filename="old.txt") is None
    assert await docs.find_duplicate() is None


async def test_find_duplicate_with_hash_ignores_name_and_size_fallback(docs):
    await _doc(docs, "a.txt", {"content_hash": "h1", "size": 10})
    # у документа есть hash — запасной путь «имя+размер» к нему не применяется
    assert await docs.find_duplicate(filename="a.txt", size=10) is None


async def test_clone_document_row_copies_content_and_metadata(docs):
    src = await _doc(docs, "a.txt", {"x": 1}, content="тело")
    clone_id = await docs.clone_document_row(src, metadata={"agent_id": 9})
    assert clone_id is not None and clone_id != src
    docs_list = await docs.get_all_documents()
    assert len(docs_list) == 2
    clone = await docs.get_document(clone_id)
    assert clone is not None
    assert clone.content == "тело" and clone.filename == "a.txt"
    assert clone.metadata.get("agent_id") == 9
    assert await docs.clone_document_row(999999, metadata={}) is None


async def test_find_document_ids_by_filename(docs):
    a = await _doc(docs, "Годовой отчёт 2024.pdf")
    await _doc(docs, "other.txt")
    assert await docs.find_document_ids_by_filename("отчёт") == [a]
    assert await docs.find_document_ids_by_filename("  ") == []
    assert len(await docs.find_document_ids_by_filename(".", limit=1)) == 1


async def test_delete_document_cascades_vectors(docs, vecs):
    doc_id = await _doc(docs)
    await vecs.create_vectors_batch([_vec(doc_id, 0, [1, 0, 0, 0], "x")])
    assert await docs.delete_document(doc_id) is True
    assert await docs.get_document(doc_id) is None
    assert await vecs.get_vectors_by_document(doc_id) == []
    assert await docs.delete_document(doc_id) is True  # повтор безвреден


# ---------------------------------------------------------------------- vectors


async def test_vectors_roundtrip_and_ordering(docs, vecs):
    doc_id = await _doc(docs)
    assert await vecs.create_vectors_batch([]) == 0
    n = await vecs.create_vectors_batch(
        [
            _vec(doc_id, 2, [0, 0, 1, 0], "третий", {"p": 3}),
            _vec(doc_id, 0, [1, 0, 0, 0], "нулевой"),
            _vec(doc_id, 1, [0, 1, 0, 0], "первый"),
        ]
    )
    assert n == 3
    got = await vecs.get_vectors_by_document(doc_id)
    assert [v.chunk_index for v in got] == [0, 1, 2]
    assert got[2].metadata == {"p": 3} and got[0].embedding is None
    one = await vecs.get_vector_by_document_and_chunk(doc_id, 1)
    assert one.content == "первый"
    assert await vecs.get_vector_by_document_and_chunk(doc_id, 99) is None
    assert await vecs.get_chunk_contents_by_indices(doc_id, [0, 2, 2, -1, 50]) == {
        0: "нулевой",
        2: "третий",
    }
    assert await vecs.get_chunk_contents_by_indices(doc_id, []) == {}


async def test_null_bytes_stripped_from_chunk(docs, vecs):
    doc_id = await _doc(docs)
    await vecs.create_vectors_batch([_vec(doc_id, 0, [1, 0, 0, 0], "a\x00b")])
    assert (await vecs.get_vectors_by_document(doc_id))[0].content == "ab"


async def test_chunk_for_missing_document_violates_fk(docs, vecs):
    with pytest.raises(Exception):
        await vecs.create_vectors_batch([_vec(999999, 0, [1, 0, 0, 0], "x")])


async def test_wrong_embedding_dimension_rejected(docs, vecs):
    doc_id = await _doc(docs)
    with pytest.raises(Exception):
        await vecs.create_vectors_batch([_vec(doc_id, 0, [1, 0, 0], "short")])


async def test_similarity_search_orders_by_cosine(docs, vecs):
    doc_id = await _doc(docs)
    await vecs.create_vectors_batch(
        [
            _vec(doc_id, 0, [1, 0, 0, 0], "ось x"),
            _vec(doc_id, 1, [0, 1, 0, 0], "ось y"),
            _vec(doc_id, 2, [0.9, 0.1, 0, 0], "почти x"),
        ]
    )
    res = await vecs.similarity_search([1, 0, 0, 0], limit=3)
    assert [r[0].content for r in res] == ["ось x", "почти x", "ось y"]
    assert res[0][1] == pytest.approx(1.0)
    assert res[2][1] == pytest.approx(0.0, abs=1e-6)
    assert len(await vecs.similarity_search([1, 0, 0, 0], limit=1)) == 1


async def test_similarity_search_scopes(docs, vecs):
    a = await _doc(docs, "a.txt")
    b = await _doc(docs, "b.txt")
    await vecs.create_vectors_batch([_vec(a, 0, [1, 0, 0, 0], "A")])
    await vecs.create_vectors_batch([_vec(b, 0, [1, 0, 0, 0], "B")])
    assert [r[0].content for r in await vecs.similarity_search([1, 0, 0, 0], document_id=a)] == ["A"]
    got = await vecs.similarity_search([1, 0, 0, 0], document_ids=[a, b])
    assert {r[0].content for r in got} == {"A", "B"}
    # document_ids приоритетнее document_id
    got = await vecs.similarity_search([1, 0, 0, 0], document_id=a, document_ids=[b])
    assert [r[0].content for r in got] == ["B"]


async def test_similarity_search_metadata_filters(docs, vecs):
    old = await _doc(
        docs,
        "старый.txt",
        created_at=datetime.utcnow() - timedelta(days=30),
        updated_at=datetime.utcnow() - timedelta(days=30),
    )
    new = await _doc(docs, "новый_отчёт.txt")
    for d in (old, new):
        await vecs.create_vectors_batch([_vec(d, 0, [1, 0, 0, 0], f"doc{d}")])
    week_ago = datetime.utcnow() - timedelta(days=7)
    f = DocumentVectorSearchFilters(date_from=week_ago)
    assert [r[0].document_id for r in await vecs.similarity_search([1, 0, 0, 0], filters=f)] == [new]
    f = DocumentVectorSearchFilters(date_to=week_ago)
    assert [r[0].document_id for r in await vecs.similarity_search([1, 0, 0, 0], filters=f)] == [old]
    f = DocumentVectorSearchFilters(filename_contains="ОТЧЁТ")
    assert [r[0].document_id for r in await vecs.similarity_search([1, 0, 0, 0], filters=f)] == [new]
    empty = DocumentVectorSearchFilters(filename_contains="   ")
    assert len(await vecs.similarity_search([1, 0, 0, 0], filters=empty)) == 2


async def test_keyword_search_russian_morphology_and_scoping(docs, vecs):
    a = await _doc(docs, "a.txt")
    b = await _doc(docs, "b.txt")
    await vecs.create_vectors_batch(
        [
            _vec(a, 0, [1, 0, 0, 0], "Кошки сидят на окнах и смотрят во двор"),
            _vec(a, 1, [0, 1, 0, 0], "Собака бежит по двору"),
            _vec(b, 0, [0, 0, 1, 0], "Кошка спит"),
        ]
    )
    found = await vecs.keyword_search("кошка")
    assert {r[0].content for r in found} == {"Кошки сидят на окнах и смотрят во двор", "Кошка спит"}
    only_a = await vecs.keyword_search("кошка", document_ids=[a])
    assert [r[0].document_id for r in only_a] == [a]
    # OR-семантика: хотя бы одно слово из запроса
    both = await vecs.keyword_search("собака рыба", document_id=a)
    assert [r[0].chunk_index for r in both] == [1]
    assert all(score > 0 for _, score in found)


async def test_keyword_search_degenerate_queries(docs, vecs):
    doc_id = await _doc(docs)
    await vecs.create_vectors_batch([_vec(doc_id, 0, [1, 0, 0, 0], "текст")])
    assert await vecs.keyword_search("") == []
    assert await vecs.keyword_search("   ") == []
    assert await vecs.keyword_search("!!! ???") == []
    assert await vecs.keyword_search("'; DROP TABLE kb_vectors; --") == []
    assert len(await vecs.get_vectors_by_document(doc_id)) == 1


async def test_substring_search(docs, vecs):
    doc_id = await _doc(docs)
    await vecs.create_vectors_batch(
        [
            _vec(doc_id, 0, [1, 0, 0, 0], "Артикул ABC-123 на складе"),
            _vec(doc_id, 1, [0, 1, 0, 0], "ничего"),
        ]
    )
    found = await vecs.substring_search(["abc-123"])
    assert [r[0].chunk_index for r in found] == [0]
    assert await vecs.substring_search([]) == []
    assert await vecs.substring_search(["", None]) == []
    assert await vecs.substring_search(["abc-123"], document_id=999) == []


async def test_substring_search_wildcards_known_issue(docs, vecs):
    """Известная проблема: токен с ``%`` или ``_`` работает как маска ILIKE."""
    doc_id = await _doc(docs)
    await vecs.create_vectors_batch([_vec(doc_id, 0, [1, 0, 0, 0], "обычный текст")])
    assert len(await vecs.substring_search(["%"])) == 1
    assert len(await vecs.substring_search(["обыч_ый"])) == 1


async def test_bm25_corpus_and_document_ids(docs, vecs):
    a = await _doc(docs, "a.txt")
    b = await _doc(docs, "b.txt")
    await vecs.create_vectors_batch(
        [_vec(a, 1, [1, 0, 0, 0], "a1"), _vec(a, 0, [1, 0, 0, 0], "a0"), _vec(b, 0, [1, 0, 0, 0], "b0")]
    )
    assert await vecs.get_all_contents_for_bm25() == [(a, 0, "a0"), (a, 1, "a1"), (b, 0, "b0")]
    assert await vecs.get_all_contents_for_bm25([b, None]) == [(b, 0, "b0")]
    assert await vecs.get_all_document_ids() == [a, b]


async def test_clone_vectors_and_independent_deletion(docs, vecs):
    src = await _doc(docs, "src.txt")
    dst = await _doc(docs, "dst.txt")
    await vecs.create_vectors_batch(
        [_vec(src, 0, [1, 0, 0, 0], "x", {"m": 1}), _vec(src, 1, [0, 1, 0, 0], "y")]
    )
    assert await vecs.clone_vectors_to_document(src, dst) == 2
    assert [v.content for v in await vecs.get_vectors_by_document(dst)] == ["x", "y"]
    assert await vecs.delete_vectors_by_document(src) is True
    assert await vecs.get_vectors_by_document(src) == []
    assert len(await vecs.get_vectors_by_document(dst)) == 2
    res = await vecs.similarity_search([1, 0, 0, 0], document_id=dst)
    assert res[0][0].content == "x"


async def test_two_dimensions_coexist_and_delete_cleans_all(pg_connection, docs, vecs):
    doc_id = await _doc(docs)
    other = KbVectorRepository(pg_connection, embedding_dim=6)
    await other.create_tables()
    await vecs.create_vectors_batch([_vec(doc_id, 0, [1, 0, 0, 0], "четыре")])
    await other.create_vectors_batch([_vec(doc_id, 0, [1, 0, 0, 0, 0, 0], "шесть")])
    assert (await vecs.similarity_search([1, 0, 0, 0]))[0][0].content == "четыре"
    assert (await other.similarity_search([1, 0, 0, 0, 0, 0]))[0][0].content == "шесть"
    # удаление чистит вектора во всех таблицах размерностей
    assert await vecs.delete_vectors_by_document(doc_id) is True
    assert await other.similarity_search([1, 0, 0, 0, 0, 0]) == []
    assert await vecs.get_all_document_ids() == []


async def test_search_in_empty_table(vecs):
    assert await vecs.similarity_search([1, 0, 0, 0]) == []
    assert await vecs.keyword_search("текст") == []
    assert await vecs.get_all_document_ids() == []


async def test_create_tables_is_idempotent_and_keeps_data(docs, vecs):
    doc_id = await _doc(docs)
    await vecs.create_vectors_batch([_vec(doc_id, 0, [1, 0, 0, 0], "keep")])
    await vecs.create_tables()
    await docs.create_tables()
    assert (await vecs.get_vectors_by_document(doc_id))[0].content == "keep"
