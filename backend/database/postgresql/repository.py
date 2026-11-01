"""
Репозиторий для работы с документами и векторами в PostgreSQL
"""

import json
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
import numpy as np

from .models import Document, DocumentVector
from .connection import PostgreSQLConnection
from backend.settings.logging import get_logger

logger = get_logger(__name__)


class DocumentRepository:
    """Репозиторий для работы с документами"""
    
    def __init__(self, db_connection: PostgreSQLConnection):
        """
        Инициализация репозитория
        
        Args:
            db_connection: Подключение к PostgreSQL
        """
        self.db_connection = db_connection
    
    async def create_tables(self):
        """Создание таблиц для документов и векторов"""
        try:
            # Убеждаемся, что расширение pgvector установлено
            async with await self.db_connection.acquire() as conn:
                try:
                    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    logger.info("Расширение pgvector проверено/установлено")
                except Exception as e:
                    logger.warning(f"Не удалось создать расширение pgvector (возможно, уже установлено): {e}")
                    # Продолжаем, так как расширение может быть уже установлено
            async with await self.db_connection.acquire() as conn:
                # Таблица документов
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        id SERIAL PRIMARY KEY,
                        filename VARCHAR(255) NOT NULL,
                        content TEXT NOT NULL,
                        metadata JSONB DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # Индекс для поиска по имени файла
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_documents_filename 
                    ON documents(filename)
                """)
                
                # Индекс для метаданных (GIN индекс для JSONB)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_documents_metadata 
                    ON documents USING GIN(metadata)
                """)
                
                logger.info("Таблицы для документов созданы")
                
        except Exception:
            logger.exception("Ошибка при создании таблиц документов")
            raise
    
    async def create_document(self, document: Document) -> Optional[int]:
        """
        Создание нового документа
        
        Args:
            document: Объект документа
            
        Returns:
            ID созданного документа или None в случае ошибки
        """
        try:
            async with await self.db_connection.acquire() as conn:
                # Сериализуем metadata в JSON строку
                metadata_json = json.dumps(document.metadata) if document.metadata else "{}"
                
                result = await conn.fetchrow("""
                    INSERT INTO documents (filename, content, metadata, created_at, updated_at)
                    VALUES ($1, $2, $3::jsonb, $4, $5)
                    RETURNING id
                """, 
                    document.filename,
                    document.content,
                    metadata_json,
                    document.created_at,
                    document.updated_at
                )
                
                doc_id = result['id']
                logger.info(f"Создан документ: {document.filename} (ID: {doc_id})")
                return doc_id
                
        except Exception as e:
            logger.error(f"Ошибка при создании документа: {e}")
            return None
    
    async def get_document(self, document_id: int) -> Optional[Document]:
        """
        Получение документа по ID
        
        Args:
            document_id: ID документа
            
        Returns:
            Объект документа или None
        """
        try:
            async with await self.db_connection.acquire() as conn:
                result = await conn.fetchrow("""
                    SELECT id, filename, content, metadata, created_at, updated_at
                    FROM documents
                    WHERE id = $1
                """, document_id)
                
                if result:
                    # Десериализуем metadata из JSON
                    metadata = result['metadata']
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata)
                    elif metadata is None:
                        metadata = {}
                    
                    return Document(
                        id=result['id'],
                        filename=result['filename'],
                        content=result['content'],
                        metadata=metadata,
                        created_at=result['created_at'],
                        updated_at=result['updated_at']
                    )
                return None
                
        except Exception as e:
            logger.error(f"Ошибка при получении документа: {e}")
            return None
    
    async def get_all_documents(self, limit: int = 100) -> List[Document]:
        """
        Получение всех документов
        
        Args:
            limit: Максимальное количество документов
            
        Returns:
            Список документов
        """
        try:
            async with await self.db_connection.acquire() as conn:
                results = await conn.fetch("""
                    SELECT id, filename, content, metadata, created_at, updated_at
                    FROM documents
                    ORDER BY created_at DESC
                    LIMIT $1
                """, limit)
                
                documents = []
                for result in results:
                    # Десериализуем metadata из JSON
                    metadata = result['metadata']
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata)
                    elif metadata is None:
                        metadata = {}
                    
                    documents.append(Document(
                        id=result['id'],
                        filename=result['filename'],
                        content=result['content'],
                        metadata=metadata,
                        created_at=result['created_at'],
                        updated_at=result['updated_at']
                    ))
                
                return documents
                
        except Exception as e:
            logger.error(f"Ошибка при получении документов: {e}")
            return []
    
    async def delete_document(self, document_id: int) -> bool:
        """
        Удаление документа
        
        Args:
            document_id: ID документа
            
        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute("DELETE FROM documents WHERE id = $1", document_id)
                logger.info(f"Удален документ: {document_id}")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка при удалении документа: {e}")
            return False


class VectorRepository:
    """Репозиторий для работы с векторами"""
    
    def __init__(self, db_connection: PostgreSQLConnection, embedding_dim: int = 384):
        """
        Инициализация репозитория
        
        Args:
            db_connection: Подключение к PostgreSQL
            embedding_dim: Размерность векторов
        """
        self.db_connection = db_connection
        self.embedding_dim = embedding_dim
    
    async def create_tables(self):
        """Создание таблицы для векторов с pgvector"""
        try:
            # Убеждаемся, что расширение pgvector установлено
            async with await self.db_connection.acquire() as conn:
                try:
                    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    logger.info("Расширение pgvector проверено/установлено для векторов")
                except Exception as e:
                    logger.warning(f"Не удалось создать расширение pgvector (возможно, уже установлено): {e}")
                    # Продолжаем, так как расширение может быть уже установлено
            async with await self.db_connection.acquire() as conn:
                # Таблица векторов
                await conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS document_vectors (
                        id SERIAL PRIMARY KEY,
                        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                        chunk_index INTEGER NOT NULL,
                        embedding vector({self.embedding_dim}) NOT NULL,
                        content TEXT NOT NULL,
                        metadata JSONB DEFAULT '{{}}'::jsonb,
                        created_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(document_id, chunk_index)
                    )
                """)
                
                # HNSW индекс для векторного поиска (используем cosine distance)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_document_vectors_embedding_hnsw 
                    ON document_vectors 
                    USING hnsw (embedding vector_cosine_ops)
                """)
                
                # Индекс для поиска по document_id
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_document_vectors_document_id 
                    ON document_vectors(document_id)
                """)
                
                logger.info(f"Таблицы для векторов созданы (размерность: {self.embedding_dim})")
                
        except Exception:
            logger.exception("Ошибка при создании таблиц векторов")
            raise
    
    async def create_vector(self, vector: DocumentVector) -> Optional[int]:
        """
        Создание нового вектора
        
        Args:
            vector: Объект вектора
            
        Returns:
            ID созданного вектора или None в случае ошибки
        """
        try:
            # Сериализуем metadata в JSON строку
            metadata_json = json.dumps(vector.metadata) if vector.metadata else "{}"
            
            async with await self.db_connection.acquire() as conn:
                result = await conn.fetchrow("""
                    INSERT INTO document_vectors (document_id, chunk_index, embedding, content, metadata)
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                    RETURNING id
                """,
                    vector.document_id,
                    vector.chunk_index,
                    str(vector.embedding),  # pgvector требует строку формата '[0.1, 0.2, ...]'
                    vector.content,
                    metadata_json
                )
                
                vector_id = result['id']
                logger.debug(f"Создан вектор: document_id={vector.document_id}, chunk_index={vector.chunk_index}")
                return vector_id
                
        except Exception as e:
            logger.error(f"Ошибка при создании вектора: {e}")
            return None
    
    async def create_vectors_batch(self, vectors: List[DocumentVector]) -> int:
        """
        Batch создание векторов (в 5-10 раз быстрее чем по одному)
        
        Args:
            vectors: Список объектов векторов
            
        Returns:
            Количество успешно созданных векторов
        """
        if not vectors:
            return 0
        
        try:
            # Подготавливаем данные для batch insert
            values = []
            for vector in vectors:
                metadata_json = json.dumps(vector.metadata) if vector.metadata else "{}"
                values.append((
                    vector.document_id,
                    vector.chunk_index,
                    str(vector.embedding),  # pgvector требует строку формата '[0.1, 0.2, ...]'
                    vector.content,
                    metadata_json
                ))
            
            # Выполняем batch insert с использованием executemany
            async with await self.db_connection.acquire() as conn:
                # asyncpg не поддерживает executemany напрямую для RETURNING
                # Поэтому используем COPY или множественный VALUES
                
                # Вариант 1: Множественный INSERT с VALUES
                # Это быстрее чем по одному, но медленнее чем COPY
                placeholders = []
                flat_values = []
                for i, (doc_id, chunk_idx, emb, content, meta) in enumerate(values):
                    base = i * 5
                    placeholders.append(f"(${base+1}, ${base+2}, ${base+3}, ${base+4}, ${base+5}::jsonb)")
                    flat_values.extend([doc_id, chunk_idx, emb, content, meta])
                
                query = f"""
                    INSERT INTO document_vectors (document_id, chunk_index, embedding, content, metadata)
                    VALUES {', '.join(placeholders)}
                """
                
                await conn.execute(query, *flat_values)
                
                logger.info(f"Batch insert: создано {len(vectors)} векторов")
                return len(vectors)
                
        except Exception as e:
            logger.error(f"Ошибка при batch создании векторов: {e}")
            logger.debug(f"Попытка создать {len(vectors)} векторов")
            
            # Fallback: создаем по одному
            logger.warning("Переключаемся на последовательное создание...")
            created = 0
            for vector in vectors:
                vector_id = await self.create_vector(vector)
                if vector_id:
                    created += 1
            return created
    
    async def similarity_search(
        self, 
        query_embedding: List[float], 
        limit: int = 10,
        document_id: Optional[int] = None
    ) -> List[Tuple[DocumentVector, float]]:
        """
        Поиск похожих документов по вектору (cosine similarity)
        
        Args:
            query_embedding: Вектор запроса
            limit: Максимальное количество результатов
            document_id: Опциональный фильтр по документу
            
        Returns:
            Список кортежей (вектор, similarity_score)
        """
        try:
            async with await self.db_connection.acquire() as conn:
                embedding_str = str(query_embedding)
                
                if document_id:
                    query = """
                        SELECT id, document_id, chunk_index, embedding::text, content, metadata,
                               1 - (embedding <=> $1::vector) as similarity
                        FROM document_vectors
                        WHERE document_id = $2
                        ORDER BY embedding <=> $1::vector
                        LIMIT $3
                    """
                    results = await conn.fetch(query, embedding_str, document_id, limit)
                else:
                    query = """
                        SELECT id, document_id, chunk_index, embedding::text, content, metadata,
                               1 - (embedding <=> $1::vector) as similarity
                        FROM document_vectors
                        ORDER BY embedding <=> $1::vector
                        LIMIT $2
                    """
                    results = await conn.fetch(query, embedding_str, limit)
                
                vectors = []
                for result in results:
                    # Парсим embedding из строки (формат: '[0.1, 0.2, ...]')
                    embedding_str = result['embedding'].strip('[]')
                    embedding = [float(x.strip()) for x in embedding_str.split(',')]
                    
                    # Десериализуем metadata из JSON
                    metadata = result['metadata']
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata)
                    elif metadata is None:
                        metadata = {}
                    
                    vector = DocumentVector(
                        id=result['id'],
                        document_id=result['document_id'],
                        chunk_index=result['chunk_index'],
                        embedding=embedding,
                        content=result['content'],
                        metadata=metadata
                    )
                    similarity = float(result['similarity'])
                    vectors.append((vector, similarity))
                
                return vectors
                
        except Exception as e:
            logger.error(f"Ошибка при векторном поиске: {e}")
            return []
    
    async def get_vectors_by_document(self, document_id: int) -> List[DocumentVector]:
        """
        Получить все векторы документа
        
        Args:
            document_id: ID документа
            
        Returns:
            Список векторов документа
        """
        try:
            async with await self.db_connection.acquire() as conn:
                results = await conn.fetch("""
                    SELECT id, document_id, chunk_index, embedding::text, content, metadata
                    FROM document_vectors
                    WHERE document_id = $1
                    ORDER BY chunk_index
                """, document_id)
                
                vectors = []
                for result in results:
                    # Парсим embedding из строки (формат: '[0.1, 0.2, ...]')
                    embedding_str = result['embedding'].strip('[]')
                    embedding = [float(x.strip()) for x in embedding_str.split(',')]
                    
                    # Десериализуем metadata из JSON
                    metadata = result['metadata']
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata)
                    elif metadata is None:
                        metadata = {}
                    
                    vector = DocumentVector(
                        id=result['id'],
                        document_id=result['document_id'],
                        chunk_index=result['chunk_index'],
                        embedding=embedding,
                        content=result['content'],
                        metadata=metadata
                    )
                    vectors.append(vector)
                
                return vectors
                
        except Exception as e:
            logger.error(f"Ошибка при получении векторов документа: {e}")
            return []
    
    async def delete_vectors_by_document(self, document_id: int) -> bool:
        """
        Удаление всех векторов документа
        
        Args:
            document_id: ID документа
            
        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute("DELETE FROM document_vectors WHERE document_id = $1", document_id)
                logger.info(f"Удалены векторы документа: {document_id}")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка при удалении векторов: {e}")
            return False