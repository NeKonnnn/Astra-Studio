# Модели документов и векторов для RAG
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class Document(BaseModel):
    id: Optional[int] = None
    filename: str
    # При записи и при чтении одного документа текст на месте.
    # А get_all_documents его не тянет: списку документов он не нужен
    content: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DocumentVector(BaseModel):
    id: Optional[int] = None
    document_id: int
    chunk_index: int
    # При ЗАПИСИ вектор обязателен - его только что посчитала модель.
    # При ЧТЕНИИ он не заполняется вовсе: косинус считает Postgres и
    # возвращает готовым числом, а сам вектор в Python не читают.
    embedding: Optional[List[float]] = None
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
