"""Project RAG и Memory RAG репозитории на реальном PostgreSQL с pgvector."""

from datetime import datetime, timedelta

import pytest

from app.database import bm25_store
from app.database.memory_rag_repository import (
    MemoryRagDocumentRepository,
    MemoryRagVectorRepository,
)
from app.database.models import Document, DocumentVector
from app.database.project_rag_repository import (
    ProjectRagDocumentRepository,
    ProjectRagVectorRepository,
)
from app.database.search_filters import DocumentVectorSearchFilters

pytestmark = pytest.mark.integration

DIM = 4


@pytest.fixture(autouse=True)
def _reset_bm25_state():
    bm25_store._table_ready = None
    yield
    bm25_store._table_ready = None


def _vec(doc_id, idx, emb, content, meta=None):
    return DocumentVector(
        document_id=doc_id, chunk_index=idx, embedding=emb, content=content, metadata=meta or {}
    )


# ---------------------------------------------------------------------- project


@pytest.fixture
async def pdocs(pg_connection):
    repo = ProjectRagDocumentRepository(pg_connection)
    await repo.create_tables()
    return repo


@pytest.fixture
async def pvecs(pg_connection, pdocs):
    repo = ProjectRagVectorRepository(pg_connection, embedding_dim=DIM)
    await repo.create_tables()
    return repo


async def _pdoc(pdocs, project="p1", filename="a.txt", metadata=None, content="тело", **kw):
    return await pdocs.create_document(
        project, Document(filename=filename, content=content, metadata=metadata or {}, **kw)
    )


async def test_project_documents_roundtrip_and_isolation(pdocs):
    a = await _pdoc(pdocs, "p1", "a.txt", {"k": 1}, "Привет")
    b = await _pdoc(pdocs, "p2", "b.txt")
    got = await pdocs.get_document(a)
    assert (got["project_id"], got["filename"], got["content"]) == ("p1", "a.txt", "Привет")
    assert got["metadata"] == {"k": 1}
    assert [d["id"] for d in await pdocs.get_documents_by_project("p1")] == [a]
    assert [d["id"] for d in await pdocs.list_documents_brief("p2")] == [b]
    assert (await pdocs.list_documents_brief("p2"))[0]["content"] is None
    assert await pdocs.get_documents_by_project("нет") == []
    assert await pdocs.get_document(999999) is None
    assert set(await pdocs.get_all_project_ids()) == {"p1", "p2"}


async def test_project_null_bytes_stripped(pdocs):
    doc_id = await _pdoc(pdocs, "p\x001", "a\x00.txt", content="te\x00xt")
    got = await pdocs.get_document(doc_id)
    assert (got["project_id"], got["filename"], got["content"]) == ("p1", "a.txt", "text")


async def test_project_find_duplicate_is_scoped_to_project(pdocs):
    a = await _pdoc(pdocs, "p1", "a.txt", {"content_hash": "ABC"})
    legacy = await _pdoc(pdocs, "p1", "Old.TXT", {"size": 7})
    assert (await pdocs.find_duplicate(project_id="p1", content_hash="abc"))["id"] == a
    assert await pdocs.find_duplicate(project_id="p2", content_hash="abc") is None
    assert (await pdocs.find_duplicate(project_id="p1", filename="old.txt", size=7))["id"] == legacy
    assert await pdocs.find_duplicate(project_id="p1", filename="old.txt", size=8) is None
    assert await pdocs.find_duplicate(project_id="  ", content_hash="abc") is None
    assert await pdocs.find_duplicate(project_id="p1") is None


async def test_project_documents_by_owner_and_filename(pdocs):
    a = await _pdoc(pdocs, "p1", "Годовой отчёт.pdf", {"owner_user_id": " Alice "})
    b = await _pdoc(pdocs, "p2", "отчёт квартал.pdf", {"owner_user_id": "bob"})
    assert [d["id"] for d in await pdocs.get_documents_by_owner("ALICE")] == [a]
    assert await pdocs.get_documents_by_owner("") == []
    assert set(await pdocs.find_document_ids_by_filename("ОТЧЁТ")) == {a, b}
    assert await pdocs.find_document_ids_by_filename("отчёт", project_id="p2") == [b]
    assert await pdocs.find_document_ids_by_filename("  ") == []
    assert len(await pdocs.find_document_ids_by_filename("отчёт", limit=1)) == 1


async def test_project_delete_document_and_project_cascade(pdocs, pvecs):
    a = await _pdoc(pdocs, "p1", "a.txt")
    b = await _pdoc(pdocs, "p1", "b.txt")
    c = await _pdoc(pdocs, "p2", "c.txt")
    for d in (a, b, c):
        await pvecs.create_vectors_batch([_vec(d, 0, [1, 0, 0, 0], f"d{d}")])
    assert await pdocs.delete_document(a) is True
    assert await pvecs.get_vectors_by_document(a) == []
    assert await pdocs.delete_documents_by_project("p1") == 1
    assert await pvecs.get_vectors_by_document(b) == []
    assert len(await pvecs.get_vectors_by_document(c)) == 1
    assert await pdocs.delete_documents_by_project("p1") == 0


async def test_project_vectors_roundtrip(pdocs, pvecs):
    d = await _pdoc(pdocs)
    assert await pvecs.create_vectors_batch([]) == 0
    await pvecs.create_vectors_batch(
        [_vec(d, 1, [0, 1, 0, 0], "первый", {"m": 1}), _vec(d, 0, [1, 0, 0, 0], "нулевой")]
    )
    got = await pvecs.get_vectors_by_document(d)
    assert [v.chunk_index for v in got] == [0, 1] and got[1].metadata == {"m": 1}
    assert (await pvecs.get_vector_by_document_and_chunk(d, 1)).content == "первый"
    assert await pvecs.get_vector_by_document_and_chunk(d, 9) is None
    assert await pvecs.get_chunk_contents_by_indices(d, [1, 1, -2, 7]) == {1: "первый"}
    assert await pvecs.get_chunk_contents_by_indices(d, []) == {}


async def test_project_similarity_is_scoped_by_project_and_document(pdocs, pvecs):
    a = await _pdoc(pdocs, "p1", "a.txt")
    b = await _pdoc(pdocs, "p2", "b.txt")
    await pvecs.create_vectors_batch([_vec(a, 0, [1, 0, 0, 0], "A")])
    await pvecs.create_vectors_batch([_vec(b, 0, [1, 0, 0, 0], "B")])
    q = [1, 0, 0, 0]
    assert {r[0].content for r in await pvecs.similarity_search(q)} == {"A", "B"}
    assert [r[0].content for r in await pvecs.similarity_search(q, project_id="p1")] == ["A"]
    assert [r[0].content for r in await pvecs.similarity_search(q, project_id="p2")] == ["B"]
    assert await pvecs.similarity_search(q, project_id="нет") == []
    # document_id приоритетнее project_id
    got = await pvecs.similarity_search(q, project_id="p1", document_id=b)
    assert [r[0].content for r in got] == ["B"]


async def test_project_similarity_ordering_and_filters(pdocs, pvecs):
    old = await _pdoc(
        pdocs, "p", "старый.txt",
        created_at=datetime.utcnow() - timedelta(days=30),
        updated_at=datetime.utcnow() - timedelta(days=30),
    )
    new = await _pdoc(pdocs, "p", "новый_отчёт.txt")
    await pvecs.create_vectors_batch([_vec(old, 0, [0, 1, 0, 0], "old")])
    await pvecs.create_vectors_batch([_vec(new, 0, [1, 0, 0, 0], "new")])
    res = await pvecs.similarity_search([1, 0, 0, 0], project_id="p")
    assert [r[0].content for r in res] == ["new", "old"]
    assert res[0][1] == pytest.approx(1.0)
    week = datetime.utcnow() - timedelta(days=7)
    f = DocumentVectorSearchFilters(date_from=week)
    assert [r[0].content for r in await pvecs.similarity_search([1, 0, 0, 0], filters=f)] == ["new"]
    f = DocumentVectorSearchFilters(filename_contains="ОТЧЁТ")
    assert [r[0].content for r in await pvecs.similarity_search([1, 0, 0, 0], filters=f)] == ["new"]


async def test_project_keyword_and_substring_search_scoping(pdocs, pvecs):
    a = await _pdoc(pdocs, "p1", "a.txt")
    b = await _pdoc(pdocs, "p2", "b.txt")
    await pvecs.create_vectors_batch([_vec(a, 0, [1, 0, 0, 0], "Кошки сидят на окнах")])
    await pvecs.create_vectors_batch([_vec(b, 0, [1, 0, 0, 0], "Кошка спит. Артикул XY-77")])
    assert len(await pvecs.keyword_search("кошка")) == 2
    assert [r[0].document_id for r in await pvecs.keyword_search("кошка", project_id="p2")] == [b]
    assert [r[0].document_id for r in await pvecs.keyword_search("кошка", document_id=a)] == [a]
    assert await pvecs.keyword_search("") == []
    assert await pvecs.keyword_search("'; DROP TABLE project_rag_vectors; --") == []
    assert [r[0].document_id for r in await pvecs.substring_search(["xy-77"])] == [b]
    assert await pvecs.substring_search(["xy-77"], project_id="p1") == []
    assert await pvecs.substring_search([]) == []


async def test_project_document_ids_and_bm25_corpus_by_project(pdocs, pvecs):
    a = await _pdoc(pdocs, "p1", "a.txt")
    b = await _pdoc(pdocs, "p2", "b.txt")
    await pvecs.create_vectors_batch([_vec(a, 1, [1, 0, 0, 0], "a1"), _vec(a, 0, [1, 0, 0, 0], "a0")])
    await pvecs.create_vectors_batch([_vec(b, 0, [1, 0, 0, 0], "b0")])
    assert await pvecs.get_all_document_ids() == [a, b]
    assert await pvecs.get_all_document_ids("p1") == [a]
    assert await pvecs.get_all_contents_for_bm25("p1") == [(a, 0, "a0"), (a, 1, "a1")]
    assert len(await pvecs.get_all_contents_for_bm25()) == 3


async def test_project_bm25_store_fingerprint_is_per_project(pdocs, pvecs):
    a = await _pdoc(pdocs, "p1", "a.txt")
    b = await _pdoc(pdocs, "p2", "b.txt")
    await pvecs.create_vectors_batch([_vec(a, 0, [1, 0, 0, 0], "a0")])
    await pvecs.create_vectors_batch([_vec(b, 0, [1, 0, 0, 0], "b0")])
    fp1, _ = await pvecs.bm25_store_load("project:p1:4", "p1")
    await pvecs.bm25_store_save("project:p1:4", fp1, b"blob", 1)
    assert (await pvecs.bm25_store_load("project:p1:4", "p1"))[1] is not None
    # изменение чужого проекта не инвалидирует индекс p1
    await pvecs.delete_vectors_by_document(b)
    assert (await pvecs.bm25_store_load("project:p1:4", "p1"))[1] is not None
    # изменение своего — инвалидирует
    await pvecs.create_vectors_batch([_vec(a, 1, [1, 0, 0, 0], "a1")])
    assert (await pvecs.bm25_store_load("project:p1:4", "p1"))[1] is None


async def test_project_bm25_store_drop_removes_all_dimensions(pdocs, pvecs):
    a = await _pdoc(pdocs, "p1", "a.txt")
    await pvecs.create_vectors_batch([_vec(a, 0, [1, 0, 0, 0], "x")])
    fp, _ = await pvecs.bm25_store_load("project:p1:4", "p1")
    for key in ("project:p1:4", "project:p1:768", "project:p10:4"):
        await pvecs.bm25_store_save(key, fp, b"x", 1)
    assert await pvecs.bm25_store_drop("p1") == 2


async def test_project_two_dimensions_coexist(pg_connection, pdocs, pvecs):
    d = await _pdoc(pdocs)
    other = ProjectRagVectorRepository(pg_connection, embedding_dim=6)
    await other.create_tables()
    await pvecs.create_vectors_batch([_vec(d, 0, [1, 0, 0, 0], "четыре")])
    await other.create_vectors_batch([_vec(d, 0, [1, 0, 0, 0, 0, 0], "шесть")])
    assert (await pvecs.similarity_search([1, 0, 0, 0]))[0][0].content == "четыре"
    assert (await other.similarity_search([1, 0, 0, 0, 0, 0]))[0][0].content == "шесть"
    assert await pvecs.delete_vectors_by_document(d) is True
    assert await other.similarity_search([1, 0, 0, 0, 0, 0]) == []


async def test_project_wrong_dimension_and_orphan_chunk_rejected(pdocs, pvecs):
    d = await _pdoc(pdocs)
    with pytest.raises(Exception):
        await pvecs.create_vectors_batch([_vec(d, 0, [1, 0], "short")])
    with pytest.raises(Exception):
        await pvecs.create_vectors_batch([_vec(999999, 0, [1, 0, 0, 0], "orphan")])


# ----------------------------------------------------------------------- memory


@pytest.fixture
async def mdocs(pg_connection):
    repo = MemoryRagDocumentRepository(pg_connection)
    await repo.create_tables()
    return repo


@pytest.fixture
async def mvecs(pg_connection, mdocs):
    repo = MemoryRagVectorRepository(pg_connection, embedding_dim=DIM)
    await repo.create_tables()
    return repo


async def _mdoc(mdocs, filename="a.txt", metadata=None, content="тело", **kw):
    return await mdocs.create_document(
        Document(filename=filename, content=content, metadata=metadata or {}, **kw)
    )


async def test_memory_documents_roundtrip(mdocs):
    a = await _mdoc(mdocs, "a\x00.txt", {"size": 5, "file_type": "txt"}, "Привет")
    got = await mdocs.get_document(a)
    assert (got.filename, got.content, got.metadata["size"]) == ("a.txt", "Привет", 5)
    assert await mdocs.get_document(999999) is None
    assert [d.content for d in await mdocs.get_all_documents()] == [None]
    brief = (await mdocs.list_documents_brief())[0]
    assert (brief["size"], brief["file_type"]) == ("5", "txt")
    await _mdoc(mdocs, "b.txt")
    assert {d["size"] for d in await mdocs.list_documents_brief()} == {"5", "0"}


async def test_memory_find_duplicate_and_filename(mdocs):
    a = await _mdoc(mdocs, "Отчёт.docx", {"content_hash": "HASH"})
    legacy = await _mdoc(mdocs, "Old.TXT", {"size": 9})
    assert (await mdocs.find_duplicate(content_hash="hash"))["id"] == a
    assert (await mdocs.find_duplicate(filename="old.txt", size=9))["id"] == legacy
    assert await mdocs.find_duplicate(filename="old.txt", size=1) is None
    assert await mdocs.find_duplicate() is None
    assert await mdocs.find_document_ids_by_filename("отчёт") == [a]
    assert await mdocs.find_document_ids_by_filename("") == []


async def test_memory_vectors_search_and_delete(mdocs, mvecs):
    a = await _mdoc(mdocs, "a.txt")
    b = await _mdoc(mdocs, "b.txt")
    await mvecs.create_vectors_batch(
        [_vec(a, 0, [1, 0, 0, 0], "Кошки сидят на окнах"), _vec(a, 1, [0, 1, 0, 0], "Собака бежит")]
    )
    await mvecs.create_vectors_batch([_vec(b, 0, [0.9, 0.1, 0, 0], "Кошка спит. Код ZZ-9")])
    res = await mvecs.similarity_search([1, 0, 0, 0], limit=3)
    assert [r[0].content for r in res][:2] == ["Кошки сидят на окнах", "Кошка спит. Код ZZ-9"]
    assert [r[0].content for r in await mvecs.similarity_search([1, 0, 0, 0], document_id=b)] == [
        "Кошка спит. Код ZZ-9"
    ]
    assert len(await mvecs.keyword_search("кошка")) == 2
    assert [r[0].document_id for r in await mvecs.keyword_search("кошка", document_id=b)] == [b]
    assert await mvecs.keyword_search("   ") == []
    assert [r[0].document_id for r in await mvecs.substring_search(["zz-9"])] == [b]
    assert await mvecs.get_all_document_ids() == [a, b]
    assert await mvecs.get_all_contents_for_bm25() == [
        (a, 0, "Кошки сидят на окнах"),
        (a, 1, "Собака бежит"),
        (b, 0, "Кошка спит. Код ZZ-9"),
    ]
    assert await mvecs.delete_vectors_by_document(a) is True
    assert await mvecs.get_vectors_by_document(a) == []
    assert await mvecs.get_all_document_ids() == [b]


async def test_memory_filters(mdocs, mvecs):
    old = await _mdoc(
        mdocs, "старый.txt",
        created_at=datetime.utcnow() - timedelta(days=30),
        updated_at=datetime.utcnow() - timedelta(days=30),
    )
    new = await _mdoc(mdocs, "новый.txt")
    await mvecs.create_vectors_batch([_vec(old, 0, [1, 0, 0, 0], "old")])
    await mvecs.create_vectors_batch([_vec(new, 0, [1, 0, 0, 0], "new")])
    f = DocumentVectorSearchFilters(date_to=datetime.utcnow() - timedelta(days=7))
    assert [r[0].content for r in await mvecs.similarity_search([1, 0, 0, 0], filters=f)] == ["old"]
    f = DocumentVectorSearchFilters(filename_contains="НОВЫЙ")
    assert [r[0].content for r in await mvecs.similarity_search([1, 0, 0, 0], filters=f)] == ["new"]


async def test_memory_delete_document_cascades(mdocs, mvecs):
    a = await _mdoc(mdocs)
    await mvecs.create_vectors_batch([_vec(a, 0, [1, 0, 0, 0], "x")])
    assert await mdocs.delete_document(a) is True
    assert await mvecs.get_vectors_by_document(a) == []
    assert await mdocs.delete_document(a) is True


async def test_memory_bm25_store_whole_table_fingerprint(mdocs, mvecs):
    a = await _mdoc(mdocs)
    await mvecs.create_vectors_batch([_vec(a, 0, [1, 0, 0, 0], "x")])
    fp, _ = await mvecs.bm25_store_load("memory:all:4")
    await mvecs.bm25_store_save("memory:all:4", fp, b"blob", 1)
    assert (await mvecs.bm25_store_load("memory:all:4"))[1] is not None
    await mvecs.create_vectors_batch([_vec(a, 1, [0, 1, 0, 0], "y")])
    assert (await mvecs.bm25_store_load("memory:all:4"))[1] is None


async def test_memory_lookups_ignore_other_dimension_tables_known_issue(
    pg_connection, mdocs, mvecs
):
    """Известная проблема: ``get_vector_by_document_and_chunk``,
    ``get_vectors_by_document`` и ``get_chunk_contents_by_indices`` в memory
    смотрят только таблицу своей размерности, а в kb/project — во все (там это
    сделано именно для случая смены модели). Чанки документа, проиндексированного
    прежней моделью, для parent-document expansion «пропадают»."""
    a = await _mdoc(mdocs)
    other = MemoryRagVectorRepository(pg_connection, embedding_dim=6)
    await other.create_tables()
    await other.create_vectors_batch([_vec(a, 0, [1, 0, 0, 0, 0, 0], "шесть")])
    assert await mvecs.get_vector_by_document_and_chunk(a, 0) is None
    assert await mvecs.get_vectors_by_document(a) == []
    assert await mvecs.get_chunk_contents_by_indices(a, [0]) == {}
    assert (await other.get_vector_by_document_and_chunk(a, 0)).content == "шесть"
    # удаление, наоборот, чистит все таблицы
    assert await mvecs.delete_vectors_by_document(a) is True
    assert await other.get_vectors_by_document(a) == []
