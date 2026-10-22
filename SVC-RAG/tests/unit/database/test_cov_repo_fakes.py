"""Репозитории KB / Project RAG / Memory RAG: ветки ошибок и разбора ответов на фейковом соединении.

Настоящий Postgres сюда не нужен: проверяем то, что на живой базе не воспроизвести —
обрыв соединения посреди операции, нестроковый ``metadata`` (jsonb-кодек), «мусорный»
статус команды, отсутствие таблиц векторов.
"""

from datetime import datetime

import pytest

from app.clients.openai_models_compat_client import set_embed_log_context
from app.core.cef_logger import storage_audit
from app.database import embedding_schema
from app.database.kb_repository import KbDocumentRepository, KbVectorRepository
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
from tests.unit.database.fakes import FakeConn, FakeDb, broken_conn

pytestmark = pytest.mark.unit

NOW = datetime(2025, 1, 2, 3, 4, 5)


@pytest.fixture
def audit(monkeypatch):
    """Записывает вызовы CEF-аудита хранилища вместо отправки."""
    rec = {"ok": [], "fail": []}
    monkeypatch.setattr(
        storage_audit,
        "log_postgres_success",
        lambda method, *, db, request_uuid=None: rec["ok"].append((method, db, request_uuid)),
    )
    monkeypatch.setattr(
        storage_audit,
        "log_postgres_failure",
        lambda method, error, *, db, request_uuid=None: rec["fail"].append(
            (method, error, db, request_uuid)
        ),
    )
    return rec


@pytest.fixture(autouse=True)
def _tables(monkeypatch):
    """Имена таблиц векторов подставляем сами: без запросов в pg_class."""
    state = {"all": ["t_main"]}

    async def resolve(conn, base, dim):
        return "t_main"

    async def list_tables(conn, base):
        return list(state["all"])

    monkeypatch.setattr(embedding_schema, "resolve_vector_table", resolve)
    monkeypatch.setattr(embedding_schema, "list_vector_tables", list_tables)
    set_embed_log_context()  # чистый контекст операции
    yield state
    set_embed_log_context()


def _vec(doc_id=5):
    return DocumentVector(document_id=doc_id, chunk_index=0, embedding=[1.0, 0.0], content="x")


def _kb_docs(db):
    return KbDocumentRepository(db)


def _kb_vecs(db):
    return KbVectorRepository(db, embedding_dim=2)


def _pr_docs(db):
    return ProjectRagDocumentRepository(db)


def _pr_vecs(db):
    return ProjectRagVectorRepository(db, embedding_dim=2)


def _mem_docs(db):
    return MemoryRagDocumentRepository(db)


def _mem_vecs(db):
    return MemoryRagVectorRepository(db, embedding_dim=2)


def _new_doc():
    return Document(filename="a\x00.txt", content="x")


# --------------------------------------------------------------- сбой соединения

FAILURE_CASES = [
    pytest.param(
        lambda db: _kb_docs(db).create_document(_new_doc()),
        "PostgreSQL.insert(kb_documents)", "a.txt", id="kb-create-document",
    ),
    pytest.param(
        lambda db: _kb_docs(db).delete_document(7),
        "PostgreSQL.delete(kb_documents)", "7", id="kb-delete-document",
    ),
    pytest.param(
        lambda db: _kb_vecs(db).create_vectors_batch([_vec()]),
        "PostgreSQL.insert(kb_vectors) document_id=5 n=1", "5", id="kb-create-vectors",
    ),
    pytest.param(
        lambda db: _kb_vecs(db).delete_vectors_by_document(5),
        "PostgreSQL.delete(kb_vectors) document_id=5", "5", id="kb-delete-vectors",
    ),
    pytest.param(
        lambda db: _pr_docs(db).create_document("p1", _new_doc()),
        "PostgreSQL.insert(project_rag_documents)", "a.txt", id="project-create-document",
    ),
    pytest.param(
        lambda db: _pr_docs(db).delete_document(7),
        "PostgreSQL.delete(project_rag_documents)", "7", id="project-delete-document",
    ),
    pytest.param(
        lambda db: _pr_docs(db).delete_documents_by_project("p1"),
        "PostgreSQL.delete(project_rag_documents by project)", "p1", id="project-delete-by-project",
    ),
    pytest.param(
        lambda db: _pr_vecs(db).create_vectors_batch([_vec()]),
        "PostgreSQL.insert(project_rag_vectors) document_id=5 n=1", "5", id="project-create-vectors",
    ),
    pytest.param(
        lambda db: _pr_vecs(db).delete_vectors_by_document(5),
        "PostgreSQL.delete(project_rag_vectors) document_id=5", "5", id="project-delete-vectors",
    ),
    pytest.param(
        lambda db: _mem_docs(db).create_document(_new_doc()),
        "PostgreSQL.insert(memory_rag_documents)", "a.txt", id="memory-create-document",
    ),
    pytest.param(
        lambda db: _mem_docs(db).delete_document(7),
        "PostgreSQL.delete(memory_rag_documents)", "7", id="memory-delete-document",
    ),
    pytest.param(
        lambda db: _mem_vecs(db).create_vectors_batch([_vec()]),
        "PostgreSQL.insert(memory_rag_vectors) document_id=5 n=1", "5", id="memory-create-vectors",
    ),
    pytest.param(
        lambda db: _mem_vecs(db).delete_vectors_by_document(5),
        "PostgreSQL.delete(memory_rag_vectors) document_id=5", "5", id="memory-delete-vectors",
    ),
]


@pytest.mark.parametrize("call,method,request_uuid", FAILURE_CASES)
async def test_connection_failure_is_audited_and_reraised(call, method, request_uuid, audit):
    """Обрыв соединения: исключение не глотается, в аудит уходит INT006-запись, успеха нет."""
    db = FakeDb(broken_conn(ConnectionError("boom")))
    with pytest.raises(ConnectionError, match="boom"):
        await call(db)
    assert audit["ok"] == []
    ((m, error, got_db, uuid_),) = audit["fail"]
    assert m == method
    assert error == "boom"
    assert got_db is db
    assert uuid_ == request_uuid


@pytest.mark.parametrize(
    "call,method,request_uuid",
    [c for c in FAILURE_CASES if "vectors" in c.id],
)
async def test_vector_failure_log_carries_embed_context(call, method, request_uuid, audit):
    """В метку сбоя попадают операция и имя файла из контекста индексации (имя режется до 120)."""
    set_embed_log_context(operation="reindex", filename="f" * 200)
    with pytest.raises(ConnectionError):
        await call(FakeDb(broken_conn(ConnectionError("boom"))))
    ((m, *_),) = audit["fail"]
    assert m.startswith(method)
    assert m.endswith(" op=reindex file=" + "f" * 120)


async def test_create_document_without_returned_row_gives_none_and_no_audit(audit):
    """INSERT ... RETURNING не вернул строку: id нет, успешная запись в аудит не пишется."""
    db = FakeDb(FakeConn(fetchrow=None))
    assert await _kb_docs(db).create_document(Document(filename="a", content="x")) is None
    assert await _pr_docs(db).create_document("p", Document(filename="a", content="x")) is None
    assert await _mem_docs(db).create_document(Document(filename="a", content="x")) is None
    assert audit == {"ok": [], "fail": []}


async def test_memory_create_vectors_batch_of_nothing_is_a_noop():
    conn = FakeConn()
    assert await _mem_vecs(FakeDb(conn)).create_vectors_batch([]) == 0
    assert conn.calls == []


# ---------------------------------------------------- «мусорный» статус команды


async def test_delete_documents_by_project_parses_asyncpg_status(audit):
    assert await _pr_docs(FakeDb(FakeConn(execute="DELETE 3"))).delete_documents_by_project("p1") == 3
    # статус не в формате «DELETE N» — отдаём 0, а не падаем
    assert await _pr_docs(FakeDb(FakeConn(execute=""))).delete_documents_by_project("p1") == 0
    assert await _pr_docs(FakeDb(FakeConn(execute="DELETE many"))).delete_documents_by_project("p1") == 0


async def test_clone_vectors_sums_row_counts_over_all_tables(_tables):
    _tables["all"] = ["t_a", "t_b"]
    conn = FakeConn(execute="INSERT 0 3")
    assert await _kb_vecs(FakeDb(conn)).clone_vectors_to_document(1, 2) == 6
    assert len(conn.sql_of("execute")) == 2


@pytest.mark.parametrize("status", ["garbage", None, ""])
async def test_clone_vectors_ignores_unparsable_status(status):
    """Статус не «INSERT 0 N»: счётчик остаётся нулём, исключение не уходит наружу."""
    conn = FakeConn(execute=status)
    assert await _kb_vecs(FakeDb(conn)).clone_vectors_to_document(1, 2) == 0
    assert len(conn.sql_of("execute")) == 1


# ------------------------------------------------------- таблиц векторов нет


async def test_no_vector_tables_means_empty_results(_tables):
    _tables["all"] = []
    conn = FakeConn()
    kb, pr, mem = _kb_vecs(FakeDb(conn)), _pr_vecs(FakeDb(conn)), _mem_vecs(FakeDb(conn))
    assert await kb.get_all_document_ids() == []
    assert await pr.get_all_document_ids() == []
    assert await pr.get_all_document_ids("p1") == []
    assert await mem.get_all_document_ids() == []
    assert await pr.get_chunk_contents_by_indices(1, [0]) == {}
    assert await kb.get_chunk_contents_by_indices(1, [0]) == {}
    assert await kb.get_vectors_by_document(1) == []
    assert await pr.get_vectors_by_document(1) == []
    assert await kb.get_vector_by_document_and_chunk(1, 0) is None
    assert await pr.get_vector_by_document_and_chunk(1, 0) is None
    # BUG-RAG-05: memory ведёт себя так же, как kb/project
    assert await mem.get_chunk_contents_by_indices(1, [0]) == {}
    assert await mem.get_vectors_by_document(1) == []
    assert await mem.get_vector_by_document_and_chunk(1, 0) is None
    # ни одного запроса к данным: UNION по пустому списку таблиц не собирается
    assert conn.calls == []


async def test_delete_vectors_without_tables_succeeds(audit, _tables):
    _tables["all"] = []
    conn = FakeConn()
    assert await _kb_vecs(FakeDb(conn)).delete_vectors_by_document(1) is True
    assert conn.calls == []
    assert audit["ok"][0][0].endswith("n_deleted=0")


async def test_delete_vectors_counts_rows_before_delete(audit, _tables):
    _tables["all"] = ["t_a", "t_b"]
    conn = FakeConn(fetchrow={"c": 2})
    assert await _mem_vecs(FakeDb(conn)).delete_vectors_by_document(9) is True
    assert audit["ok"][0][0] == "PostgreSQL.delete(memory_rag_vectors) document_id=9 n_deleted=4"
    assert conn.sql_of("execute") == [
        "DELETE FROM t_a WHERE document_id = $1",
        "DELETE FROM t_b WHERE document_id = $1",
    ]


# --------------------------------------------- нестроковый metadata в строках

ROW = {
    "id": 1,
    "document_id": 2,
    "chunk_index": 3,
    "content": "текст",
    "metadata": {"k": 1},
    "similarity": 0.5,
    "lexical_score": 0.7,
    "filename": "f.txt",
    "project_id": "p1",
    "created_at": NOW,
    "updated_at": NOW,
}

DOC_FILTERS = DocumentVectorSearchFilters(filename_contains="f")


def _meta_of(x):
    """Достаёт metadata из Document / DocumentVector / dict / пары (вектор, оценка)."""
    if isinstance(x, tuple):
        x = x[0]
    return x["metadata"] if isinstance(x, dict) else x.metadata


def _first(res):
    return res[0] if isinstance(res, list) else res


META_CASES = [
    pytest.param(lambda db: _kb_docs(db).get_document(1), id="kb-get-document"),
    pytest.param(lambda db: _kb_docs(db).get_document_by_filename("f"), id="kb-get-by-filename"),
    pytest.param(lambda db: _kb_docs(db).get_all_documents(), id="kb-get-all"),
    pytest.param(lambda db: _kb_docs(db).get_documents_by_owner("u"), id="kb-by-owner"),
    pytest.param(lambda db: _kb_docs(db).get_documents_by_ids([1]), id="kb-by-ids"),
    pytest.param(lambda db: _kb_vecs(db).similarity_search([1.0, 0.0], filters=DOC_FILTERS), id="kb-similarity"),
    pytest.param(lambda db: _kb_vecs(db).keyword_search("договор", filters=DOC_FILTERS), id="kb-keyword"),
    pytest.param(lambda db: _kb_vecs(db).substring_search(["x"]), id="kb-substring"),
    pytest.param(lambda db: _kb_vecs(db).get_vectors_by_document(2), id="kb-vectors-by-doc"),
    pytest.param(lambda db: _kb_vecs(db).get_vector_by_document_and_chunk(2, 3), id="kb-vector-one"),
    pytest.param(lambda db: _pr_docs(db).get_document(1), id="project-get-document"),
    pytest.param(lambda db: _pr_docs(db).get_documents_by_project("p1"), id="project-by-project"),
    pytest.param(lambda db: _pr_docs(db).list_documents_brief("p1"), id="project-brief"),
    pytest.param(lambda db: _pr_vecs(db).similarity_search([1.0, 0.0], filters=DOC_FILTERS), id="project-similarity"),
    pytest.param(lambda db: _pr_vecs(db).keyword_search("договор", filters=DOC_FILTERS), id="project-keyword"),
    pytest.param(lambda db: _pr_vecs(db).substring_search(["x"]), id="project-substring"),
    pytest.param(lambda db: _pr_vecs(db).get_vectors_by_document(2), id="project-vectors-by-doc"),
    pytest.param(lambda db: _pr_vecs(db).get_vector_by_document_and_chunk(2, 3), id="project-vector-one"),
    pytest.param(lambda db: _mem_docs(db).get_document(1), id="memory-get-document"),
    pytest.param(lambda db: _mem_docs(db).get_all_documents(), id="memory-get-all"),
    pytest.param(lambda db: _mem_vecs(db).similarity_search([1.0, 0.0], filters=DOC_FILTERS), id="memory-similarity"),
    pytest.param(lambda db: _mem_vecs(db).keyword_search("договор", filters=DOC_FILTERS), id="memory-keyword"),
    pytest.param(lambda db: _mem_vecs(db).substring_search(["x"]), id="memory-substring"),
    pytest.param(lambda db: _mem_vecs(db).get_vectors_by_document(2), id="memory-vectors-by-doc"),
    pytest.param(lambda db: _mem_vecs(db).get_vector_by_document_and_chunk(2, 3), id="memory-vector-one"),
]


@pytest.mark.parametrize("call", META_CASES)
async def test_already_decoded_metadata_is_passed_through(call):
    """Если пул отдаёт jsonb уже словарём (подключён кодек), он не перекодируется."""
    db = FakeDb(FakeConn(fetch=[ROW], fetchrow=dict(ROW), fetchval=0))
    assert _meta_of(_first(await call(db))) == {"k": 1}


@pytest.mark.parametrize("call", META_CASES)
async def test_null_metadata_becomes_empty_dict(call):
    row = dict(ROW, metadata=None)
    db = FakeDb(FakeConn(fetch=[row], fetchrow=dict(row), fetchval=0))
    assert _meta_of(_first(await call(db))) == {}


@pytest.mark.parametrize("call", META_CASES)
async def test_json_string_metadata_is_decoded(call):
    row = dict(ROW, metadata='{"k": 2}')
    db = FakeDb(FakeConn(fetch=[row], fetchrow=dict(row), fetchval=0))
    assert _meta_of(_first(await call(db))) == {"k": 2}


async def test_project_row_without_content_column_gives_none_content():
    """Список документов не тянет ``content``: на KeyError отдаётся None, а не падение."""
    row = {k: v for k, v in ROW.items() if k != "content"}
    out = await _pr_docs(FakeDb(FakeConn(fetch=[row]))).list_documents_brief("p1")
    assert out[0]["content"] is None and out[0]["filename"] == "f.txt"


# ------------------------------------------------------- вырожденные аргументы


@pytest.mark.parametrize("factory", [_kb_vecs, _pr_vecs, _mem_vecs])
async def test_chunk_contents_with_only_negative_indices_is_empty_without_query(factory):
    conn = FakeConn()
    repo = factory(FakeDb(conn))
    assert await repo.get_chunk_contents_by_indices(1, [-1, -7]) == {}
    assert await repo.get_chunk_contents_by_indices(1, []) == {}
    assert conn.calls == []


@pytest.mark.parametrize("factory", [_kb_vecs, _pr_vecs, _mem_vecs])
async def test_keyword_search_without_searchable_tokens_does_not_query(factory):
    """Запрос из одних стоп-слов даёт пустой tsquery — в БД не ходим."""
    conn = FakeConn()
    assert await factory(FakeDb(conn)).keyword_search("и в на") == []
    assert conn.calls == []


@pytest.mark.parametrize("factory", [_kb_vecs, _pr_vecs, _mem_vecs])
async def test_substring_search_without_usable_tokens_does_not_query(factory):
    conn = FakeConn()
    repo = factory(FakeDb(conn))
    assert await repo.substring_search([]) == []
    assert await repo.substring_search(["", None, 5]) == []
    assert conn.calls == []
