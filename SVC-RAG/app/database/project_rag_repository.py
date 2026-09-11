# Репозитории для RAG-файлов проектов: project_rag_documents + project_rag_vectors.
# Каждый документ привязан к project_id; при удалении проекта данные удаляются каскадом.
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.database.connection import PostgreSQLConnection
from app.database.fts import (
    build_fts_or_query,
    ensure_fts_columns,
    fts_where_and_rank,
    query_has_searchable_content,
    substring_where_and_rank,
)
from app.database.models import Document, DocumentVector
from app.text_sanitize import strip_null_bytes
from app.database.search_filters import DocumentVectorSearchFilters

logger = logging.getLogger(__name__)

class ProjectRagDocumentRepository:
    def __init__(self, db: PostgreSQLConnection):
        self.db = db

    async def create_tables(self):
        async with await self.db.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_rag_documents (
                    id SERIAL PRIMARY KEY,
                    project_id VARCHAR(128) NOT NULL,
                    filename VARCHAR(512) NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_proj_rag_docs_project_id ON project_rag_documents(project_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_proj_rag_docs_created ON project_rag_documents(created_at DESC)"
            )
        logger.info("Таблица project_rag_documents готова")

    async def create_document(
        self, project_id: str, document: Document
    ) -> Optional[int]:
        meta = json.dumps(document.metadata) if document.metadata else "{}"
        pid = strip_null_bytes(project_id)
        fn = strip_null_bytes(document.filename)
        body = strip_null_bytes(document.content)
        async with await self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO project_rag_documents
                    (project_id, filename, content, metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                RETURNING id
                """,
                pid,
                fn,
                body,
                meta,
                document.created_at,
                document.updated_at,
            )
        return row["id"] if row else None

    async def get_document(self, document_id: int) -> Optional[dict]:
        async with await self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, project_id, filename, content, metadata, created_at, updated_at "
                "FROM project_rag_documents WHERE id = $1",
                document_id,
            )
        if not row:
            return None
        return self._row_to_dict(row)

    async def get_all_project_ids(self) -> List[str]:
        """Все project_id, у которых есть документы (для массовой переиндексации)."""
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT project_id FROM project_rag_documents"
            )
        return [r["project_id"] for r in rows if r["project_id"]]

    async def get_documents_by_owner(self, owner_user_id: str) -> List[dict]:
        """Документы, у которых metadata.owner_user_id совпадает с пользователем."""
        oid = (owner_user_id or "").strip().lower()
        if not oid:
            return []
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, project_id, filename, content, metadata, created_at, updated_at
                FROM project_rag_documents
                WHERE LOWER(TRIM(COALESCE(metadata->>'owner_user_id', ''))) = $1
                ORDER BY created_at DESC
                """,
                oid,
            )
        return [self._row_to_dict(r) for r in rows]

    async def get_documents_by_project(self, project_id: str) -> List[dict]:
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, project_id, filename, content, metadata, created_at, updated_at "
                "FROM project_rag_documents WHERE project_id = $1 ORDER BY created_at DESC",
                project_id,
            )
        return [self._row_to_dict(r) for r in rows]

    async def find_document_ids_by_filename(
        self, name_or_stem: str, project_id: Optional[str] = None, limit: int = 10
    ) -> List[int]:
        """Найти document_id по подстроке имени файла (ILIKE).

        Используется, когда пользователь в запросе упоминает конкретный файл
        («сделай саммари по Воронин_Михаил.docx»). Поиск case-insensitive,
        допускает имя без расширения или только часть имени.
        """
        needle = (name_or_stem or "").strip()
        if not needle:
            return []
        like = f"%{needle}%"
        sql = "SELECT id FROM project_rag_documents WHERE filename ILIKE $1"
        params: List[Any] = [like]
        if project_id is not None:
            sql += " AND project_id = $2"
            params.append(project_id)
            sql += " ORDER BY updated_at DESC LIMIT $3"
            params.append(limit)
        else:
            sql += " ORDER BY updated_at DESC LIMIT $2"
            params.append(limit)
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [int(r["id"]) for r in rows]

    async def delete_document(self, document_id: int) -> bool:
        async with await self.db.acquire() as conn:
            await conn.execute(
                "DELETE FROM project_rag_documents WHERE id = $1", document_id
            )
        return True

    async def delete_documents_by_project(self, project_id: str) -> int:
        """Удаляет все документы проекта. Возвращает количество удалённых."""
        async with await self.db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM project_rag_documents WHERE project_id = $1", project_id
            )
        # asyncpg возвращает "DELETE N"
        try:
            return int(result.split()[-1])
        except Exception:
            return 0

    def _row_to_dict(self, row) -> dict:
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta) if meta else {}
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "filename": row["filename"],
            "content": row["content"],
            "metadata": meta or {},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

class ProjectRagVectorRepository:
    def __init__(self, db: PostgreSQLConnection, embedding_dim: int = 384):
        self.db = db
        self.embedding_dim = embedding_dim
        self.base_table = "project_rag_vectors"
        self.parent_table = "project_rag_documents"

    async def _table(self, conn) -> str:
        """Таблица МОЕЙ размерности (историческая без суффикса, прочие с _<dim>)."""
        from app.database.embedding_schema import resolve_vector_table

        return await resolve_vector_table(conn, self.base_table, self.embedding_dim)

    async def _all_tables(self, conn) -> list:
        """Все таблицы стора: у документа бывают вектора сразу в нескольких."""
        from app.database.embedding_schema import list_vector_tables

        return await list_vector_tables(conn, self.base_table)

    def _cast(self) -> str:
        """vector или halfvec — по размерности (halfvec с 2001, см. B2a)."""
        from app.database.embedding_schema import vector_cast

        return vector_cast(self.embedding_dim)

    async def create_tables(self):
        """Гарантировать таблицу СВОЕЙ размерности. Ничего не удаляет и не чистит."""
        from app.database.embedding_schema import ensure_vector_table

        async with await self.db.acquire() as conn:
            table = await ensure_vector_table(
                conn, self.base_table, self.embedding_dim, self.parent_table
            )
            await ensure_fts_columns(conn, table)
        logger.info("Таблица %s готова (dim=%s)", table, self.embedding_dim)

    async def create_vectors_batch(self, vectors: List[DocumentVector]) -> int:
        if not vectors:
            return 0
        values = []
        for v in vectors:
            meta = json.dumps(v.metadata) if v.metadata else "{}"
            chunk = strip_null_bytes(v.content)
            values.append((v.document_id, v.chunk_index, str(v.embedding), chunk, meta))
        placeholders = []
        flat = []
        cast = self._cast()
        for i, (doc_id, idx, emb, content, meta) in enumerate(values):
            base = i * 5
            placeholders.append(
                f"(${base+1}, ${base+2}, ${base+3}::{cast}, ${base+4}, ${base+5}::jsonb)"
            )
            flat.extend([doc_id, idx, emb, content, meta])
        async with await self.db.acquire() as conn:
            table = await self._table(conn)
            await conn.execute(
                f"""
                INSERT INTO {table}
                    (document_id, chunk_index, embedding, content, metadata)
                VALUES {", ".join(placeholders)}
                ON CONFLICT (document_id, chunk_index) DO NOTHING
                """,
                *flat,
            )
        return len(vectors)

    async def similarity_search(
        self,
        query_embedding: List[float],
        limit: int = 10,
        project_id: Optional[str] = None,
        document_id: Optional[int] = None,
        filters: Optional[DocumentVectorSearchFilters] = None,
    ) -> List[Tuple[DocumentVector, float]]:
        emb_str = str(query_embedding)
        use_meta = filters is not None and filters.active()
        join_sql = ""
        if document_id is not None or project_id is not None or use_meta:
            join_sql = "JOIN project_rag_documents d ON d.id = v.document_id"
        clauses: List[str] = []
        params: List[Any] = [emb_str]
        pi = 2
        if document_id is not None:
            clauses.append(f"v.document_id = ${pi}")
            params.append(document_id)
            pi += 1
        elif project_id is not None:
            clauses.append(f"d.project_id = ${pi}")
            params.append(project_id)
            pi += 1
        if use_meta and filters is not None:
            if filters.date_from is not None:
                clauses.append(f"d.created_at >= ${pi}")
                params.append(filters.date_from)
                pi += 1
            if filters.date_to is not None:
                clauses.append(f"d.created_at <= ${pi}")
                params.append(filters.date_to)
                pi += 1
            fn = (filters.filename_contains or "").strip()
            if fn:
                clauses.append(f"d.filename ILIKE ${pi}")
                params.append(f"%{fn}%")
                pi += 1
        where_sql = " AND ".join(clauses) if clauses else "TRUE"
        params.append(limit)
        cast = self._cast()
        async with await self.db.acquire() as conn:
            table = await self._table(conn)
            from app.database.embedding_schema import apply_ann_search_settings

            await apply_ann_search_settings(conn)
            from_sql = f"{table} v {join_sql}".strip()
            q = f"""
                SELECT v.id, v.document_id, v.chunk_index, v.embedding::text, v.content,
                       v.metadata,
                       1 - (v.embedding <=> $1::{cast}) as similarity 
                FROM {from_sql}
                WHERE {where_sql}
                ORDER BY v.embedding <=> $1::{cast}
                LIMIT ${pi}
            """
            rows = await conn.fetch(q, *params)
        return [self._row_to_dv(row) for row in rows]

    async def keyword_search(
        self,
        query_text: str,
        limit: int = 20,
        project_id: Optional[str] = None,
        document_id: Optional[int] = None,
        filters: Optional[DocumentVectorSearchFilters] = None,
    ) -> List[Tuple[DocumentVector, float]]:
        """
        FTS-поиск по двум tsvector-колонкам (russian + simple) + GIN-индексы.

        Используется OR-семантика (```to_tsquery``` на очищенной OR-строке из
        ```build_fts_or_query```) вместо жадного AND с ```websearch_to_tsquery```:
        recall важнее на RAG-запросах вида «в каких документах упоминается X»,
        а релевантность всё равно вытянет ```ts_rank_cd```.
        """
        q_text = (query_text or "").strip()
        if not query_has_searchable_content(q_text):
            return []
        q_or = build_fts_or_query(q_text)
        if q_or is None:
            return []

        where_fts, rank_fts, _used = fts_where_and_rank(
            vectors_alias="v", first_placeholder_idx=1
        )
        params: List[Any] = [q_or, q_or]
        pi = 3

        use_meta = filters is not None and filters.active()
        need_join = document_id is not None or project_id is not None or use_meta
        join_sql = (
            "JOIN project_rag_documents d ON d.id = v.document_id" if need_join else ""
        )

        clauses: List[str] = [where_fts]
        if document_id is not None:
            clauses.append(f"v.document_id = ${pi}")
            params.append(document_id)
            pi += 1
        elif project_id is not None:
            clauses.append(f"d.project_id = ${pi}")
            params.append(project_id)
            pi += 1
        if use_meta and filters is not None:
            if filters.date_from is not None:
                clauses.append(f"d.created_at >= ${pi}")
                params.append(filters.date_from)
                pi += 1
            if filters.date_to is not None:
                clauses.append(f"d.created_at <= ${pi}")
                params.append(filters.date_to)
                pi += 1
            fn = (filters.filename_contains or "").strip()
            if fn:
                clauses.append(f"d.filename ILIKE ${pi}")
                params.append(f"%{fn}%")
                pi += 1

        where_sql = " AND ".join(clauses)
        params.append(limit)
        async with await self.db.acquire() as conn:
            table = await self._table(conn)
            from_sql = f"{table} v {join_sql}".strip()
            q = f"""
                SELECT v.id, v.document_id, v.chunk_index, v.embedding::text, v.content,
                       v.metadata,
                       {rank_fts} AS lexical_score
                FROM {from_sql}
                WHERE {where_sql}
                ORDER BY lexical_score DESC, v.chunk_index ASC
                LIMIT ${pi}
            """
            rows = await conn.fetch(q, *params)
        out: List[Tuple[DocumentVector, float]] = []
        for row in rows:
            emb = [float(x.strip()) for x in row["embedding"].strip("[]").split(",")]
            meta = row["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta) if meta else {}
            out.append(
                (
                    DocumentVector(
                        id=row["id"],
                        document_id=row["document_id"],
                        chunk_index=row["chunk_index"],
                        embedding=emb,
                        content=row["content"],
                        metadata=meta or {},
                    ),
                    float(row["lexical_score"] or 0.0),
                )
            )
        return out

    async def substring_search(
        self,
        tokens: List[str],
        limit: int = 32,
        project_id: Optional[str] = None,
        document_id: Optional[int] = None,
    ) -> List[Tuple[DocumentVector, float]]:
        """ILIKE-fallback: без токенизаторов, без tsvector, прямое подстрочное совпадение.

        Используется в entity-lane как страховка: если ```keyword_search``` (FTS) по
        именам/кодам вернул 0, этот метод всё равно найдёт чанки, где токен
        встречается буквально (в т.ч. после OCR с нестандартной токенизацией).
        """
        tokens = [t for t in (tokens or []) if t and isinstance(t, str)]
        if not tokens:
            return []

        where_sub, rank_sub, used, ilike_params = substring_where_and_rank(
            vectors_alias="v", tokens=tokens, first_placeholder_idx=1
        )
        params: List[Any] = list(ilike_params)
        pi = used + 1

        need_join = document_id is not None or project_id is not None
        join_sql = (
            "JOIN project_rag_documents d ON d.id = v.document_id" if need_join else ""
        )
        clauses: List[str] = [where_sub]
        if document_id is not None:
            clauses.append(f"v.document_id = ${pi}")
            params.append(document_id)
            pi += 1
        elif project_id is not None:
            clauses.append(f"d.project_id = ${pi}")
            params.append(project_id)
            pi += 1
        where_sql = " AND ".join(clauses)
        params.append(limit)
        async with await self.db.acquire() as conn:
            table = await self._table(conn)
            from_sql = f"{table} v {join_sql}".strip()
            q = f"""
                SELECT v.id, v.document_id, v.chunk_index, v.embedding::text, v.content,
                       v.metadata,
                       {rank_sub} AS lexical_score
                FROM {from_sql}
                WHERE {where_sql}
                ORDER BY lexical_score DESC, v.chunk_index ASC
                LIMIT ${pi}
            """
            rows = await conn.fetch(q, *params)
        out: List[Tuple[DocumentVector, float]] = []
        for row in rows:
            emb = [float(x.strip()) for x in row["embedding"].strip("[]").split(",")]
            meta = row["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta) if meta else {}
            out.append(
                (
                    DocumentVector(
                        id=row["id"],
                        document_id=row["document_id"],
                        chunk_index=row["chunk_index"],
                        embedding=emb,
                        content=row["content"],
                        metadata=meta or {},
                    ),
                    float(row["lexical_score"] or 0.0),
                )
            )
        return out

    def _row_to_dv(self, row) -> Tuple[DocumentVector, float]:
        emb = [float(x.strip()) for x in row["embedding"].strip("[]").split(",")]
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta) if meta else {}
        return (
            DocumentVector(
                id=row["id"],
                document_id=row["document_id"],
                chunk_index=row["chunk_index"],
                embedding=emb,
                content=row["content"],
                metadata=meta or {},
            ),
            float(row["similarity"]),
        )

    async def get_chunk_contents_by_indices(
        self, document_id: int, chunk_indices: List[int]
    ) -> Dict[int, str]:
        if not chunk_indices:
            return {}
        uniq = sorted({int(i) for i in chunk_indices if i is not None and int(i) >= 0})
        if not uniq:
            return {}
        out: Dict[int, str] = {}
        async with await self.db.acquire() as conn:
            for t in await self._all_tables(conn):
                rows = await conn.fetch(
                    f"""
                    SELECT chunk_index, content FROM {t}
                    WHERE document_id = $1 AND chunk_index = ANY($2::int[])
                    """,
                    document_id,
                    uniq,
                )
                if rows:
                    out = {int(r["chunk_index"]): r["content"] or "" for r in rows}
                    break
        return out

    async def bm25_store_load(self, cache_key: str, project_id: Optional[str] = None):
        """Отпечаток корпуса проекта и готовый индекс, если он свежий.

        Скоуп у проекта - сам проект, поэтому в подзапрос отпечатка входит
        соединение с таблицей документов: в таблице векторов project_id нет.
        """
        from app.database import bm25_store

        async with await self.db.acquire() as conn:
            table = await self._table(conn)
            if project_id:
                where = (
                    "JOIN project_rag_documents d ON d.id = v.document_id "
                    "WHERE d.project_id = $1"
                )
                params = [str(project_id)]
            else:
                where, params = "", []
            return await bm25_store.load(
                conn,
                cache_key=cache_key,
                vectors_table=table,
                scope_where=where,
                scope_params=params,
            )

    async def bm25_store_save(
        self, cache_key: str, fingerprint: str, payload: bytes, chunk_count: int
    ) -> None:
        from app.database import bm25_store

        async with await self.db.acquire() as conn:
            await bm25_store.save(
                conn,
                cache_key=cache_key,
                fingerprint=fingerprint,
                payload=payload,
                chunk_count=chunk_count,
            )

    async def bm25_store_drop(self, project_id: str) -> int:
        """Убрать записи проекта во всех размерностях - при его удалении."""
        from app.database import bm25_store

        async with await self.db.acquire() as conn:
            return await bm25_store.delete_by_prefix(conn, f"project:{project_id}:")

    async def get_vectors_by_document(self, document_id: int) -> List[DocumentVector]:
        """Все чанки документа по chunk_index. Нужен для parent-document expansion."""
        rows = []
        async with await self.db.acquire() as conn:
            for t in await self._all_tables(conn):
                rows = await conn.fetch(
                    "SELECT id, document_id, chunk_index, embedding::text, content, "
                    f"metadata FROM {t} WHERE document_id = $1 ORDER BY chunk_index",
                    document_id,
                )
                if rows:
                    break
        out: List[DocumentVector] = []
        for row in rows:
            emb = [float(x.strip()) for x in row["embedding"].strip("[]").split(",")]
            meta = row["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta) if meta else {}
            out.append(
                DocumentVector(
                    id=row["id"],
                    document_id=row["document_id"],
                    chunk_index=row["chunk_index"],
                    embedding=emb,
                    content=row["content"],
                    metadata=meta or {},
                )
            )
        return out

    async def delete_vectors_by_document(self, document_id: int) -> bool:
        """Чистит вектора документа во ВСЕХ таблицах (см. пояснение в kb_repository)."""
        async with await self.db.acquire() as conn:
            for t in await self._all_tables(conn):
                await conn.execute(
                    f"DELETE FROM {t} WHERE document_id = $1", document_id
                )
        return True

    async def get_all_document_ids(self, project_id: Optional[str] = None) -> List[int]:
        """Уникальные document_id в project RAG."""
        async with await self.db.acquire() as conn:
            tables = await self._all_tables(conn)
            if not tables:
                return []
            if project_id is not None:
                union = " UNION ".join(
                    f"SELECT v.document_id FROM {t} v "
                    "JOIN project_rag_documents d ON d.id = v.document_id "
                    "WHERE d.project_id = $1"
                    for t in tables
                )
                rows = await conn.fetch(
                    f"SELECT DISTINCT document_id FROM ({union}) u ORDER BY document_id",
                    project_id,
                )
            else:
                union = " UNION ".join(
                    f"SELECT document_id FROM {t}" for t in tables
                )
                rows = await conn.fetch(
                    f"SELECT DISTINCT document_id FROM ({union}) u ORDER BY document_id"
                )
        return [r["document_id"] for r in rows]

    async def get_all_contents_for_bm25(
        self, project_id: Optional[str] = None
    ) -> List[Tuple[int, int, str]]:
        """Возвращает (document_id, chunk_index, content) для BM25 (опционально в рамках project_id)."""
        async with await self.db.acquire() as conn:
            table = await self._table(conn)
            if project_id is not None:
                rows = await conn.fetch(
                    f"""
                    SELECT v.document_id, v.chunk_index, v.content
                    FROM {table} v
                    JOIN project_rag_documents d ON d.id = v.document_id
                    WHERE d.project_id = $1
                    ORDER BY v.document_id, v.chunk_index
                    """,
                    project_id,
                )
            else:
                rows = await conn.fetch(
                    f"SELECT document_id, chunk_index, content FROM {table} "
                    "ORDER BY document_id, chunk_index"
                )
        return [(r["document_id"], r["chunk_index"], r["content"]) for r in rows]

    async def get_vector_by_document_and_chunk(
        self, document_id: int, chunk_index: int
    ) -> Optional[Tuple["DocumentVector", float]]:
        """Точечный запрос одного вектора по (document_id, chunk_index)."""
        row = None
        async with await self.db.acquire() as conn:
            for t in await self._all_tables(conn):
                row = await conn.fetchrow(
                    "SELECT id, document_id, chunk_index, embedding::text, content, "
                    f"metadata FROM {t} WHERE document_id = $1 AND chunk_index = $2",
                    document_id,
                    chunk_index,
                )
                if row:
                    break
        if not row:
            return None
        emb = [float(x.strip()) for x in row["embedding"].strip("[]").split(",")]
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta) if meta else {}
        from app.database.models import DocumentVector

        return DocumentVector(
            id=row["id"],
            document_id=row["document_id"],
            chunk_index=row["chunk_index"],
            embedding=emb,
            content=row["content"],
            metadata=meta or {},
        )
