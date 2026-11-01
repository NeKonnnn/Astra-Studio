import asyncio
import logging
from typing import List, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from app.dependencies.rag_models_handler import get_embedding_model
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


class EmbedRequest(BaseModel):
    """OpenAI-совместимый POST /v1/embeddings (+ legacy text/texts)."""

    input: Union[str, List[str], None] = None
    text: Union[str, None] = None
    texts: Union[List[str], None] = None
    # Какой моделью эмбеддить: "FRIDA" или "local/FRIDA". None -> кластерный дефолт.
    model: Optional[str] = None
    # Асимметричные модели (FRIDA): "query" / "document" → search_query / search_document.
    kind: Optional[str] = None

    @model_validator(mode="after")
    def _require_texts(self):
        if not self.get_texts():
            raise ValueError("Нужно передать input, text или texts")
        return self

    def get_texts(self) -> List[str]:
        if self.input is not None:
            if isinstance(self.input, list):
                return [str(x) for x in self.input]
            return [str(self.input)]
        if self.texts:
            return list(self.texts)
        if self.text is not None:
            return [self.text]
        return []


def _prompt_name_for(model, kind: Optional[str]) -> Optional[str]:
    """Имя промпта для асимметричной модели, если она его знает.

    FRIDA объявляет промпты search_query / search_document. У моделей без промптов
    (Giga, MiniLM) словарь пуст — тогда возвращаем None и encode() зовём как раньше,
    иначе sentence-transformers ругнётся на неизвестный prompt_name.
    """
    if not kind:
        return None
    key = str(kind).strip().lower()
    rm = settings.rag_models
    mapping = {
        "query": rm.query_prompt_name,
        "search_query": rm.query_prompt_name,
        "document": rm.document_prompt_name,
        "doc": rm.document_prompt_name,
        "passage": rm.document_prompt_name,
        "search_document": rm.document_prompt_name,
    }
    name = mapping.get(key)
    if not name:
        return None
    prompts = getattr(model, "prompts", None) or {}
    try:
        return name if name in prompts else None
    except TypeError:
        return None


def _encode_texts(model, texts: List[str], batch_size: int, prompt_name: Optional[str]):
    kwargs = {
        "convert_to_numpy": True,
        "batch_size": batch_size,
        "show_progress_bar": len(texts) > batch_size,
    }
    if prompt_name:
        kwargs["prompt_name"] = prompt_name
    return model.encode(texts, **kwargs)


async def _compute_embeddings(
    texts: List[str],
    model_name: Optional[str],
    kind: Optional[str],
) -> tuple[List[List[float]], str, Optional[str]]:
    try:
        entry = await get_embedding_model(model_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        # Подробности (в т.ч. серверные пути) только в лог, клиенту - общий текст (CWE-209)
        logger.exception("Не удалось загрузить эмбеддинг-модель")
        raise HTTPException(
            status_code=503, detail="Эмбеддинг-модель не загружена"
        ) from e

    model = entry["model"]
    resolved_name = entry.get("path", "").rstrip("/").split("/")[-1] or "?"
    batch_size = max(1, int(settings.rag_models.embed_batch_size))
    prompt_name = _prompt_name_for(model, kind)
    logger.info(
        "[EMBED] model=%s dim=%s kind=%s prompt=%s texts=%s batch_size=%s",
        resolved_name,
        entry.get("dim"),
        kind or "-",
        prompt_name or "-",
        len(texts),
        batch_size,
    )

    embeddings = await asyncio.to_thread(
        _encode_texts, model, texts, batch_size, prompt_name
    )
    if hasattr(embeddings, "ndim") and embeddings.ndim == 1:
        embeddings = [embeddings.tolist()]
    else:
        embeddings = embeddings.tolist()
    if embeddings and embeddings[0]:
        entry["dim"] = len(embeddings[0])
    return embeddings, resolved_name, prompt_name


@router.post("/embeddings")
async def embed_texts(request: EmbedRequest):
    """OpenAI-совместимый POST /v1/embeddings — основной контракт для SVC-RAG."""
    if not settings.rag_models.enabled:
        raise HTTPException(status_code=503, detail="Сервис RAG-моделей выключен")

    texts = request.get_texts()
    embeddings, model_name, _prompt = await _compute_embeddings(
        texts, request.model, request.kind
    )

    return {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": vec, "index": i}
            for i, vec in enumerate(embeddings)
        ],
        "model": model_name,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }
