"""Индексация чанков волнами: эмбеддинги и вставка идут пачками

Настройки
---------
RAG_INDEX_CHUNKS_PER_BATCH=256  размер пачки; 0 или меньше - как
                                    раньше, весь документ одной пачкой.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.core.logging import get_logger
from app.database.models import DocumentVector

logger = get_logger(__name__)


def chunks_per_batch() -> int:
    """Размер пачки. 0 и меньше - весь документ одной пачкой, как раньше."""
    try:
        return int(os.getenv("RAG_INDEX_CHUNKS_PER_BATCH", "256"))
    except ValueError:
        return 256


async def embed_and_store(
    chunks_with_meta: Sequence[Tuple[str, Dict[str, Any]]],
    *,
    document_id: int,
    repo: Any,
    embed: Callable[[List[str]], Any],
    make_metadata: Callable[[int, Dict[str, Any]], Dict[str, Any]],
    timer: Optional[Any] = None,
) -> Tuple[int, List[DocumentVector]]:
    """Посчитать эмбеддинги и вставить вектора пачками.

    Возвращает (сколько вставлено, список чанков без эмбеддингов).

    Второй элемент - те же DocumentVector, но с embedding=None: он нужен
    графу, а графу от чанка требуются только chunk_index и content.
    Тексты и так живут в chunks_with_meta, так что список стоит несколько
    сот килобайт на тысячи чанков - в отличие от векторов, ради которых всё
    и затевалось.

    embed получает список текстов пачки и возвращает столько же
    векторов. make_metadata(idx, chunk_meta) собирает metadata вектора -
    у каждого хранилища она своя.

    timer необязателен: если передан, стадии embed и insert
    накапливаются в нём как раньше, и строка RAG-TIMING не меняется -
    StageTimer суммирует одноимённые стадии.

    При ошибке вставленное этим вызовом удаляется, и ошибка летит дальше.
    Без отката документ остался бы проиндексированным наполовину, и поиск
    молча отдавал бы неполную выдачу: пусто и явно лучше, чем наполовину и
    тихо. Так же вело себя и прежнее поведение - там при сбое не вставлялось
    вообще ничего.
    """
    total = len(chunks_with_meta)
    if not total:
        return 0, []

    step = chunks_per_batch()
    if step <= 0 or step > total:
        step = total

    created = 0
    stubs: List[DocumentVector] = []
    try:
        for start in range(0, total, step):
            part = chunks_with_meta[start : start + step]
            texts = [c for c, _m in part]

            if timer is not None:
                with timer.stage("embed"):
                    embeddings = await embed(texts)
            else:
                embeddings = await embed(texts)

            if len(embeddings) != len(texts):
                raise ValueError(
                    "эмбеддингов %s, а чанков %s (пачка %s-%s из %s)"
                    % (
                        len(embeddings),
                        len(texts),
                        start + 1,
                        start + len(texts),
                        total,
                    )
                )

            vectors: List[DocumentVector] = []
            for offset, ((chunk, cmeta), embedding) in enumerate(zip(part, embeddings)):
                idx = start + offset
                vectors.append(
                    DocumentVector(
                        document_id=document_id,
                        chunk_index=idx,
                        embedding=embedding,
                        content=chunk,
                        metadata=make_metadata(idx, cmeta),
                    )
                )
                stubs.append(
                    DocumentVector(
                        document_id=document_id,
                        chunk_index=idx,
                        embedding=None,
                        content=chunk,
                        metadata={},
                    )
                )

            if timer is not None:
                with timer.stage("insert"):
                    created += await repo.create_vectors_batch(vectors)
            else:
                created += await repo.create_vectors_batch(vectors)

            # Пачка записана - вектора больше не нужны. Явно, а не молча по
            # выходу из области видимости: имена живут до следующей итерации,
            # и тогда две пачки лежали бы в памяти одновременно.
            del vectors
            del embeddings

        if total > step:
            logger.debug(
                "Индексация волнами: документ %s, чанков %s, пачками по %s, вставлено %s",
                document_id,
                total,
                step,
                created,
            )
        return created, stubs
    except Exception:
        # Откат: без него упавшая индексация оставила бы часть векторов.
        try:
            await repo.delete_vectors_by_document(document_id)
        except Exception as e:
            logger.warning(
                "Индексация волнами: откат векторов документа %s не удался: %s",
                document_id,
                e,
            )
        raise
