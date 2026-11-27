"""Хит поиска, который несёт сырой cosine рядом со скором ранжирования.

Зачем отдельный тип. ```score``` в выдаче — это скор ТОЙ шкалы, в которой
работала стратегия: cosine у ```vector```/```raw_cosine```, RRF (~0.016) у
```hybrid``` и у auto-ветки с keyword-merge, логит cross-encoder после реранка,
BM25/max у ```lexical```. По нему нельзя показать пользователю «релевантность
84%»: RRF-скор говорит только о ранге, а не о близости к запросу.

Сырая косинусная близость чанка известна ровно в одном месте — сразу после
pgvector, до всех merge/boost/rerank. ```RagHit``` протаскивает её до ответа
API, чтобы фронт считал проценты по абсолютной шкале (
```relevance = 1 - distance```), а не растягивал RRF-скоры между min и max
конкретной выдачи.

Наследование от ```tuple``` — намеренно: весь пайплайн распаковывает хит как
```content, score, document_id, chunk_index```, и таких мест десятки. ```RagHit```
остаётся кортежем ровно из четырёх элементов, поэтому существующий код
работает без правок, а ```cosine``` доступен там, где он нужен.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence, Tuple

HitRow = Tuple[str, float, Optional[int], Optional[int]]

def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Косинусная близость двух векторов; None — считать не из чего.

    Нужна для чанков, которые пришли НЕ из pgvector: BM25, entity lane,
    filename-anchor, parent-expansion. У них нет скора от базы, но эмбеддинг
    в ```DocumentVector``` есть всегда — репозитории читают колонку ```embedding```
    во всех запросах. Дот-продукт на ~1024 числа для пары десятков чанков
    дешевле любого обращения к БД, зато шкала получается одна на всю выдачу.

    Нормировку считаем явно: полагаться на то, что эмбеддер отдаёт единичные
    векторы, нельзя — модель выбирается в UI и может смениться.
    """
    if not a or not b or len(a) != len(b):
        return None
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return None
    value = dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
    if math.isnan(value) or math.isinf(value):
        return None
    return max(-1.0, min(1.0, value))

class RagHit(tuple):
    """```(content, score, document_id, chunk_index)``` + ```cosine```.

    ```__slots__``` не используется: для подклассов кортежа он запрещён
    (tuple — тип переменной длины).
    """

    def __new__(
        cls,
        content: str,
        score: float,
        document_id: Optional[int],
        chunk_index: Optional[int],
        cosine: Optional[float] = None,
    ) -> "RagHit":
        return super().__new__(cls, (content, score, document_id, chunk_index))

    def __init__(
        self,
        content: str,
        score: float,
        document_id: Optional[int],
        chunk_index: Optional[int],
        cosine: Optional[float] = None,
    ) -> None:
        self.cosine: Optional[float] = None if cosine is None else float(cosine)

    def with_content(self, content: str) -> "RagHit":
        """Копия с другим текстом (sentence window склеивает соседние чанки)."""
        return RagHit(content, self[1], self[2], self[3], self.cosine)

def hit_cosine(hit: Any) -> Optional[float]:
    """Сырой cosine хита или None, если стратегия его не сохранила."""
    value = getattr(hit, "cosine", None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def as_rag_hit(hit: Any, cosine: Optional[float] = None) -> RagHit:
    """Кортеж из четырёх элементов → ```RagHit```. Уже готовый хит не теряет cosine."""
    if isinstance(hit, RagHit) and cosine is None:
        return hit
    content, score, document_id, chunk_index = hit[0], hit[1], hit[2], hit[3]
    return RagHit(
        content,
        float(score),
        document_id,
        chunk_index,
        hit_cosine(hit) if cosine is None else cosine,
    )