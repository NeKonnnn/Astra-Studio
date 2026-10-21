"""Ручки диагностики памяти: посмотреть, кто её держит, не заходя в под.

Что здесь есть
--------------
```GET  /v1/memory-diag/snapshot```   срез: RSS, блоки, кэши, топ по типам
```POST /v1/memory-diag/mode```       переключить режим замеров на живом поде
```POST /v1/memory-diag/gc```         принудительная сборка мусора с отчётом
"""

from __future__ import annotations

import gc
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from app.core import memprobe
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/snapshot")
async def memory_snapshot(
    deep: bool = Query(
        False,
        description=(
            "Обойти все живые объекты и разложить по типам. Секунда-две на "
            "куче в сотню мегабайт - поэтому по явному запросу"
        ),
    ),
    limit: int = Query(25, ge=1, le=200, description="Сколько строк в топах"),
) -> Dict[str, Any]:
    """Срез потребления памяти на текущий момент.

    Как пользоваться: снять срез до запроса к агенту и сразу после. Что
    выросло между ними, то и держит память.

    Ошибку внутри отдаём данными, а не пятисоткой: диагностика нужна как раз
    тогда, когда что-то не в порядке, и падать в этот момент ей нельзя.
    """
    try:
        return memprobe.snapshot(deep=deep, limit=limit)
    except Exception as e:
        logger.warning("[MEM] срез не собрался (%s)", e)
        return {"ошибка": str(e), "тип_ошибки": type(e).__name__}


@router.post("/mode")
async def memory_mode(
    value: Optional[str] = Query(
        None,
        description=(
            "off - выключить замеры; on - RSS, блоки и время; "
            "full - плюс точные байты через tracemalloc. "
            "Не указывать - вернуться к тому, что записано в настройках"
        ),
    ),
) -> Dict[str, Any]:
    """Переключить подробность замеров, не перезапуская под.

    ```full``` включает tracemalloc. Он честнее всех, но записывает
    происхождение каждой аллокации и сам ест память и такты, поэтому
    включать под задачу, а не оставлять навсегда.
    """
    before = memprobe.mode()
    after = memprobe.set_mode(value)
    if after == "full":
        memprobe.ensure_trace_started()
    logger.info("[MEM] режим замеров: %s -> %s (через ручку)", before, after)
    return {"было": before, "стало": after, "tracemalloc_идёт": memprobe.tracing()}


@router.post("/gc")
async def memory_gc() -> Dict[str, Any]:
    """Собрать мусор и рассказать, что это дало.

    Отвечает на вопрос, который по графику RSS не разрешить: память держат
    циклические ссылки (тогда сборка её вернёт) или это раздробленность
    кучи (тогда не вернёт ничего).
    """
    rss_before = memprobe.rss_mb()
    blocks_before = memprobe.blocks()
    collected = gc.collect()
    rss_after = memprobe.rss_mb()
    blocks_after = memprobe.blocks()

    out: Dict[str, Any] = {
        "собрано_объектов": collected,
        "блоков_освобождено": blocks_before - blocks_after,
        "мусор_неубираемый": len(gc.garbage),
    }
    if rss_before is not None and rss_after is not None:
        out["rss_мб_до"] = round(rss_before, 2)
        out["rss_мб_после"] = round(rss_after, 2)
        out["rss_вернулось_мб"] = round(rss_before - rss_after, 2)
    out["как_читать"] = (
        "Много собранных объектов - память держали циклические ссылки. "
        "Собрано мало, а RSS не изменился - память уже была свободна, "
        "но осталась у аллокатора: это раздробленность кучи, не утечка."
    )
    logger.info("[MEM] принудительная сборка мусора: %s", out)
    return out
