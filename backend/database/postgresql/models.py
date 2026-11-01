"""
Модели данных для PostgreSQL
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
from pydantic import BaseModel, Field


class Document(BaseModel):
    """Модель документа для RAG системы"""

    id: Optional[int] = Field(None, description="ID документа (автоинкремент)")
    filename: str = Field(..., description="Имя файла")
    content: str = Field(..., description="Полное содержимое документа")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Метаданные документа"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="Дата создания"
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, description="Дата обновления"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "filename": "example.pdf",
                "content": "Содержимое документа...",
                "metadata": {"type": "pdf", "pages": 10, "size": 1024000},
            }
        }


class DocumentVector(BaseModel):
    """Модель вектора документа"""

    id: Optional[int] = Field(None, description="ID вектора (автоинкремент)")
    document_id: int = Field(..., description="ID документа")
    chunk_index: int = Field(..., description="Индекс чанка в документе")
    embedding: List[float] = Field(..., description="Векторное представление")
    content: str = Field(..., description="Содержимое чанка")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Метаданные чанка"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "document_id": 1,
                "chunk_index": 0,
                "embedding": [
                    0.1,
                    0.2,
                    0.3,
                    ...,
                ],  # 384 или другое количество измерений
                "content": "Фрагмент текста из документа",
                "metadata": {"start_char": 0, "end_char": 500},
            }
        }

    def to_numpy(self) -> np.ndarray:
        """Преобразование в numpy array"""
        return np.array(self.embedding, dtype=np.float32)

    @classmethod
    def from_numpy(cls, embedding: np.ndarray, **kwargs):
        """Создание из numpy array"""
        return cls(embedding=embedding.tolist(), **kwargs)
