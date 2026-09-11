# Репозитории для постоянной базы знаний.
# Таблицы kb_documents и kb_vectors хранятся постоянно и не зависят от чата.
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


class KbDocumentRepository:
    def __init__(self, db: PostgreSQLConnection):
        self.db = db

    async def create_tables(self):
        async with await self.db.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS kb_documents (
                    id SERIAL PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_documents_filename ON kb_documents(filename)")
        logger.info("Таблица kb_documents готова")

    async def create_document(self, document: Document) -> Optional[int]:
        meta = json.dumps(document.metadata) if document.metadata else "{}"
        fn = strip_null_bytes(document.filename)
        body = strip_null_bytes(document.content)
        try:
            async with await self.db.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO kb_documents (filename, content, metadata, created_at, updated_at)
                    VALUES ($1, $2, $3::jsonb, $4, $5)
                    RETURNING id
                    """,
                    fn,
                    body,
                    meta,
                    document.created_at,
                    document.updated_at,
                )
            doc_id = row["id"] if row else None
            if doc_id is not None:
                from app.core.cef_logger.storage_audit import log_postgres_success

                log_postgres_success(
                    "PostgreSQL.insert(kb_documents)",
                    db=self.db,
                    request_uuid=str(doc_id),
                )
            return doc_id
        except Exception as e:
            from app.core.cef_logger.storage_audit import log_postgres_failure

            log_postgres_failure(
                "PostgreSQL.insert(kb_documents)",
                str(e),
                db=self.db,
                request_uuid=fn,
            )
            raise

    async def get_document(self, document_id: int) -> Optional[Document]:
        async with await self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, filename, content, metadata, created_at, updated_at FROM kb_documents WHERE id = $1",
                document_id,
            )
        if not row:
            return None
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta) if meta else {}
        return Document(
            id=row["id"],
            filename=row["filename"],
            content=row["content"],
            metadata=meta or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_document_by_filename(self, filename: str) -> Optional[Document]:
        async with await self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, filename, content, metadata, created_at, updated_at FROM kb_documents WHERE filename = $1",
                filename,
            )
        if not row:
            return None
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta) if meta else {}
        return Document(
            id=row["id"],
            filename=row["filename"],
            content=row["content"],
            metadata=meta or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_all_documents(self) -> List[Document]:
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, filename, content, metadata, created_at, updated_at FROM kb_documents ORDER BY created_at DESC"
            )
        out = []
        for row in rows:
            meta = row["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta) if meta else {}
            out.append(
                Document(
                    id=row["id"],
                    filename=row["filename"],
                    content=row["content"],
                    metadata=meta or {},
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return out

    async def get_documents_by_owner(self, owner_user_id: str) -> List[Document]:
        """KB-документы владельца агента (metadata.owner_user_id)."""
        oid = (owner_user_id or "").strip().lower()
        if not oid:
            return []
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, filename, content, metadata, created_at, updated_at
                FROM kb_documents
                WHERE LOWER(TRIM(COALESCE(metadata->>'owner_user_id', ''))) = $1
                ORDER BY created_at DESC
                """,
                oid,
            )
        out = []
        for row in rows:
            meta = row["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta) if meta else {}
            out.append(
                Document(
                    id=row["id"],
                    filename=row["filename"],
                    content=row["content"],
                    metadata=meta or {},
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return out

    async def get_documents_by_ids(self, document_ids: List[int]) -> List[Document]:
        ids = [int(x) for x in (document_ids or []) if x is not None]
        if not ids:
            return []
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, filename, content, metadata, created_at, updated_at
                FROM kb_documents
                WHERE id = ANY($1::int[])
                ORDER BY created_at DESC
                """,
                ids,
            )
        out = []
        for row in rows:
            meta = row["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta) if meta else {}
            out.append(
                Document(
                    id=row["id"],
                    filename=row["filename"],
                    content=row["content"],
                    metadata=meta or {},
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return out
        return out

    async def delete_document(self, document_id: int) -> bool:
        try:
            async with await self.db.acquire() as conn:
                await conn.execute("DELETE FROM kb_documents WHERE id = $1", document_id)
            from app.core.cef_logger.storage_audit import log_postgres_success

            log_postgres_success(
                "PostgreSQL.delete(kb_documents)",
                db=self.db,
                request_uuid=str(document_id),
            )
            return True
        except Exception as e:
            from app.core.cef_logger.storage_audit import log_postgres_failure

            log_postgres_failure(
                "PostgreSQL.delete(kb_documents)",
                str(e),
                db=self.db,
                request_uuid=str(document_id),
            )
            raise

    async def find_document_ids_by_filename(self, name_or_stem: str, limit: int = 10) -> List[int]:
        """Поиск document_id по имени файла через ILIKE. См. project_rag_repository."""
        needle = (name_or_stem or "").strip()
        if not needle:
            return []
        async with await self.db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM kb_documents WHERE filename ILIKE $1 ORDER BY updated_at DESC LIMIT $2",
                f"%{needle}%",
                limit,
            )
        return [int(r["id"]) for r in rows]


class KbVectorRepository:
    def __init__(self, db: PostgreSQLConnection, embedding_dim: int = 384):
        self.db = db
        self.embedding_dim = embedding_dim
        self.base_table = "kb_vectors"
        self.parent_table = "kb_documents"

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
        for i, (doc_id, idx, emb, content, meta) in enumerate(values):
            base = i * 5
            placeholders.append(f"(${base+1}, ${base+2}, ${base+3}, ${base+4}, ${base+5}::jsonb)")
            flat.extend([doc_id, idx, emb, content, meta])
        doc_id0 = vectors[0].document_id
        try:
            async with await self.db.acquire() as conn:
                table = await self._table(conn)
                await conn.execute(
                    f"""
                    INSERT INTO {table} (document_id, chunk_index, embedding, content, metadata)
                    VALUES {", ".join(placeholders)}
                    """,
                    *flat,
                )
            from app.core.cef_logger.storage_audit import log_postgres_success

            from app.clients.openai_models_compat_client import get_embed_log_context

            ctx = get_embed_log_context() or {}
            op = ctx.get("operation")
            filename = ctx.get("filename")
            op_part = f" op={op}" if op else ""
            file_part = f" file={str(filename)[:120]}" if filename else ""

            log_postgres_success(
                f"PostgreSQL.insert(kb_vectors) document_id={doc_id0} n={len(vectors)}{op_part}{file_part}",
                db=self.db,
                request_uuid=str(doc_id0),
            )
            return len(vectors)
        except Exception as e:
            from app.core.cef_logger.storage_audit import log_postgres_failure
            from app.clients.openai_models_compat_client import get_embed_log_context

            ctx = get_embed_log_context() or {}
            op = ctx.get("operation")
            filename = ctx.get("filename")
            op_part = f" op={op}" if op else ""
            file_part = f" file={str(filename)[:120]}" if filename else ""

            log_postgres_failure(
                f"PostgreSQL.insert(kb_vectors) document_id={doc_id0} n={len(vectors)}{op_part}{file_part}",
                str(e),
                db=self.db,
                request_uuid=str(doc_id0),
            )
            raise

    async def similarity_search(
        self,
        query_embedding: List[float],
        limit: int = 10,
        document_id: Optional[int] = None,
        document_ids: Optional[List[int]] = None,
        filters: Optional[DocumentVectorSearchFilters] = None,
    ) -> List[Tuple[DocumentVector, float]]:
        emb_str = str(query_embedding)
        use_join = filters is not None and filters.active()
        join_sql = "JOIN kb_documents d ON d.id = v.document_id" if use_join else ""
        clauses: List[str] = []
        params: List[Any] = [emb_str]
        pi = 2
        doc_ids = [int(x) for x in (document_ids or []) if x is not None]
        if doc_ids:
            clauses.append(f"v.document_id = ANY(${pi}::int[])")
            params.append(doc_ids)
            pi += 1
        elif document_id is not None:
            clauses.append(f"v.document_id = ${pi}")
            params.append(document_id)
            pi += 1
        if use_join and filters is not None:
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
        result = []
        for row in rows:
            emb = [float(x.strip()) for x in row["embedding"].strip("[]").split(",")]
            meta = row["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta) if meta else {}
            result.append(
                (
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
            )
        return result

    async def keyword_search(
        self,
        query_text: str,
        limit: int = 20,
        document_id: Optional[int] = None,
        document_ids: Optional[List[int]] = None,
        filters: Optional[DocumentVectorSearchFilters] = None,
    ) -> List[Tuple[DocumentVector, float]]:
        """FTS-поиск через OR-``to_tsquery`` (russian + simple). См. ``app.database.fts``.

        Важно: используется OR-семантика вместо AND. Это повышает recall
        для перечислительных/meta-запросов («в каких документах упоминается X»),
        где AND часто давал 0 результатов и ухудшал retrieval.
        """
        q_text = (query_text or "").strip()
        if not query_has_searchable_content(q_text):
            return []
        q_or = build_fts_or_query(q_text)
        if q_or is None:
            return []

        where_fts, rank_fts, _used = fts_where_and_rank(vectors_alias="v", first_placeholder_idx=1)
        params: List[Any] = [q_or, q_or]
        pi = 3

        use_join = filters is not None and filters.active()
        join_sql = "JOIN kb_documents d ON d.id = v.document_id" if use_join else ""
        clauses: List[str] = [where_fts]
        doc_ids = [int(x) for x in (document_ids or []) if x is not None]
        if doc_ids:
            clauses.append(f"v.document_id = ANY(${pi}::int[])")
            params.append(doc_ids)
            pi += 1
        elif document_id is not None:
            clauses.append(f"v.document_id = ${pi}")
            params.append(document_id)
            pi += 1
        if use_join and filters is not None:
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
        document_id: Optional[int] = None,
        document_ids: Optional[List[int]] = None,
    ) -> List[Tuple[DocumentVector, float]]:
        """ILIKE-fallback на случай, когда FTS не сработал."""
        tokens = [t for t in (tokens or []) if t and isinstance(t, str)]
        if not tokens:
            return []
        where_sub, rank_sub, used, ilike_params = substring_where_and_rank(
            vectors_alias="v", tokens=tokens, first_placeholder_idx=1
        )
        params: List[Any] = list(ilike_params)
        pi = used + 1
        clauses: List[str] = [where_sub]
        doc_ids = [int(x) for x in (document_ids or []) if x is not None]
        if doc_ids:
            clauses.append(f"v.document_id = ANY(${pi}::int[])")
            params.append(doc_ids)
            pi += 1
        elif document_id is not None:
            clauses.append(f"v.document_id = ${pi}")
            params.append(document_id)
            pi += 1
        where_sql = " AND ".join(clauses)
        params.append(limit)
        async with await self.db.acquire() as conn:
            table = await self._table(conn)
            q = f"""
                SELECT v.id, v.document_id, v.chunk_index, v.embedding::text, v.content,
                       v.metadata,
                       {rank_sub} AS lexical_score
                FROM {table} v
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

    async def get_chunk_contents_by_indices(self, document_id: int, chunk_indices: List[int]) -> Dict[int, str]:
        if not chunk_indices:
            return {}
        uniq = sorted({int(i) for i in chunk_indices if i is not None and int(i) >= 0})
        if not uniq:
            return {}
        out: Dict[int, str] = {}
        async with await self.db.acquire() as conn:
            # Документ мог индексироваться разными моделями — ищем, где он есть.
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

    async def get_vectors_by_document(self, document_id: int) -> List[DocumentVector]:
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
        out = []
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
        """Чистит вектора документа во ВСЕХ таблицах.
        Иначе при смене модели старые вектора остались бы в таблице прежней
        размерности навсегда — и всплывали бы в поиске у тех, кто на той модели.
        """
        try:
            n_deleted = 0
            async with await self.db.acquire() as conn:
                for t in await self._all_tables(conn):
                    row = await conn.fetchrow(
                        f"SELECT COUNT(*) AS c FROM {t} WHERE document_id = $1",
                        document_id,
                    )
                    n_deleted += int(row["c"] if row and row.get("c") is not None else 0)
                    await conn.execute(
                        f"DELETE FROM {t} WHERE document_id = $1",
                        document_id,
                    )

            from app.core.cef_logger.storage_audit import log_postgres_success

            from app.clients.openai_models_compat_client import get_embed_log_context

            ctx = get_embed_log_context() or {}
            op_part = f" op={ctx.get('operation')}" if ctx.get("operation") else ""
            file_part = f" file={str(ctx.get('filename'))[:120]}" if ctx.get("filename") else ""

            log_postgres_success(
                f"PostgreSQL.delete(kb_vectors) document_id={document_id} n_deleted={n_deleted}{op_part}{file_part}",
                db=self.db,
                request_uuid=str(document_id),
            )
            return True
        except Exception as e:
            from app.core.cef_logger.storage_audit import log_postgres_failure
            from app.clients.openai_models_compat_client import get_embed_log_context

            ctx = get_embed_log_context() or {}
            op_part = f" op={ctx.get('operation')}" if ctx.get("operation") else ""
            file_part = f" file={str(ctx.get('filename'))[:120]}" if ctx.get("filename") else ""

            log_postgres_failure(
                f"PostgreSQL.delete(kb_vectors) document_id={document_id}{op_part}{file_part}",
                str(e),
                db=self.db,
                request_uuid=str(document_id),
            )
            raise

    async def get_all_document_ids(self) -> List[int]:
        """Уникальные document_id в KB."""
        async with await self.db.acquire() as conn:
            tables = await self._all_tables(conn)
            if not tables:
                return []
            union = " UNION ".join(f"SELECT document_id FROM {t}" for t in tables)
            rows = await conn.fetch(f"SELECT DISTINCT document_id FROM ({union}) ORDER BY document_id")
        return [r["document_id"] for r in rows]

    async def get_all_contents_for_bm25(
        self, document_ids: Optional[List[int]] = None
    ) -> List[Tuple[int, int, str]]:
        """Возвращает (document_id, chunk_index, content) для BM25

        document_ids сужает корпус до перечисленных документов. Без него
        берётся вся таблица - это нужно, только когда белого списка нет
        """
        async with await self.db.acquire() as conn:
            table = await self._table(conn)
            if document_ids:
                rows = await conn.fetch(
                    f"SELECT document_id, chunk_index, content FROM {table} "
                    "WHERE document_id = ANY($1::int[]) "
                    "ORDER BY document_id, chunk_index",
                    [int(d) for d in document_ids if d is not None],
                )
            else:
                rows = await conn.fetch(
                    f"SELECT document_id, chunk_index, content FROM {table} "
                    "ORDER BY document_id, chunk_index"
                )
        return [(r["document_id"], r["chunk_index"], r["content"]) for r in rows]

    async def bm25_store_load(
        self, cache_key: str, document_ids: Optional[List[int]] = None
    ):
        """Отпечаток корпуса и готовый индекс из хранилища, если он свежий.

        Одним запросом: отпечаток и блоб должны браться атомарно, иначе
        документ, изменённый ровно между ними, дал бы устаревший индекс,
        принятый за свежий.
        """
        from app.database import bm25_store

        async with await self.db.acquire() as conn:
            table = await self._table(conn)
            logger.debug("BM25 store: table=%r repr_bytes=%r", table, table.encode("utf-8"))
            if document_ids:
                where = "WHERE v.document_id = ANY($1::int[])"
                params = [[int(d) for d in document_ids if d is not None]]
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

    async def get_vector_by_document_and_chunk(self, document_id: int, chunk_index: int) -> Optional[DocumentVector]:
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
        return DocumentVector(
            id=row["id"],
            document_id=row["document_id"],
            chunk_index=row["chunk_index"],
            embedding=emb,
            content=row["content"],
            metadata=meta or {},
        )
