import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.dependencies.rag_models_handler import get_reranker_model
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


class RerankRequest(BaseModel):
    """Принимает и native (passages/top_k), и OpenAI/Cohere (documents/top_n)."""

    query: str
    passages: Optional[List[str]] = None
    documents: Optional[List[str]] = None
    top_k: Optional[int] = None
    top_n: Optional[int] = None
    # Каким реранкером считать: "ms-marco-MiniLM-L6-v2" или "local/...". None -> дефолт.
    model: Optional[str] = None

    @model_validator(mode="after")
    def _normalize(self):
        docs = self.passages if self.passages is not None else self.documents
        if docs is None:
            docs = []
        self.passages = list(docs)
        n = self.top_k if self.top_k is not None else self.top_n
        if n is not None and int(n) < 0:
            raise ValueError("top_k/top_n не может быть отрицательным")
        self.top_k = int(n) if n is not None else 20
        return self


class RerankResponse(BaseModel):
    indices: List[int]
    scores: List[float]
    model: Optional[str] = None
    # OpenAI/Cohere-совместимое поле для клиентов SVC-RAG.
    results: List[dict] = Field(default_factory=list)


def _predict(model, pairs: List[List[str]]):
    return model.predict(pairs)


@router.post("/rerank", response_model=RerankResponse)
async def rerank_passages(request: RerankRequest):
    # Переранжируем по релевантности к запросу, возвращаем top_k индексов и скоры
    if not settings.rag_models.enabled:
        raise HTTPException(status_code=503, detail="Сервис RAG-моделей выключен")
    passages = request.passages or []
    if not passages:
        return RerankResponse(indices=[], scores=[], results=[])

    try:
        entry = await get_reranker_model(request.model)
    except ValueError as e:  # модель не разрешена конфигом
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        # Подробности (в т.ч. серверные пути) только в лог, клиенту - общий текст (CWE-209)
        logger.exception("Не удалось загрузить реранкер")
        raise HTTPException(status_code=503, detail="Реранкер не загружен") from e

    model = entry["model"]
    model_name = entry.get("path", "").rstrip("/").split("/")[-1] or "?"
    # top_k уже нормализован валидатором (None -> 20, 0 -> пустой ответ, <0 -> 422)
    top_k = int(request.top_k)
    print(
        f"[RERANK] model={model_name} passages={len(passages)} top_k={top_k}",
        flush=True,
    )

    pairs = [[request.query, p] for p in passages]
    # В отдельном потоке: predict у CrossEncoder/LLM-реранкера блокирующий
    scores = await asyncio.to_thread(_predict, model, pairs)

    if hasattr(scores, "__len__") and len(scores) != len(passages):
        scores = list(scores)
    else:
        scores = scores.tolist() if hasattr(scores, "tolist") else list(scores)
    top_k = min(top_k, len(scores))
    indexed = list(enumerate(scores))
    indexed.sort(key=lambda x: x[1], reverse=True)
    top = indexed[:top_k]
    indices = [i for i, _ in top]
    score_vals = [float(s) for _, s in top]
    results = [{"index": i, "relevance_score": s} for i, s in zip(indices, score_vals)]
    return RerankResponse(
        indices=indices,
        scores=score_vals,
        model=model_name,
        results=results,
    )
