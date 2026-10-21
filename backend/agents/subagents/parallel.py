"""Вызовы инструментов одного шага родителя: субагенты - одновременно.

Модель главного за один шаг вызывает несколько субагентов (разделы
заключения) - раньше цикл запускал их строго по очереди
"""

from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, List, Optional, Sequence, TypeVar

from backend.settings.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")
R = TypeVar("R")

_PARALLEL_DEFAULT = 4
_PARALLEL_MAX = 16

def subagent_parallel_limit() -> int:
    """Сколько субагентов одного шага работают одновременно.

    SUBAGENT_PARALLEL (ConfigMap), по умолчанию 4; 1 - по очереди, как раньше.
    Лимит на шаг: у вложенных субагентов свой, общий на всех дал бы взаимную
    блокировку (родитель держит место и ждёт ребёнка).
    """
    raw = (os.getenv("SUBAGENT_PARALLEL") or "").strip()
    if not raw:
        return _PARALLEL_DEFAULT
    try:
        return max(1, min(int(raw), _PARALLEL_MAX))
    except ValueError:
        return _PARALLEL_DEFAULT

async def run_tool_calls(
    calls: Sequence[T],
    run_one: Callable[[T], Awaitable[R]],
    *,
    parallel: Callable[[T], bool],
    step: Optional[int] = None,
) -> List[R]:
    """Результаты run_one по вызовам - в порядке вызовов.

    parallel(call) - вызов можно запускать одновременно с другими (subagent).
    Остальные идут по очереди, одной цепочкой рядом с параллельными.
    """
    par = [i for i, c in enumerate(calls) if parallel(c)]
    limit = subagent_parallel_limit()
    if len(par) < 2 or limit < 2:
        return [await run_one(c) for c in calls]
    results: List[Optional[R]] = [None] * len(calls)
    par_set = set(par)
    seq = [i for i in range(len(calls)) if i not in par_set]
    sem = asyncio.Semaphore(limit)
    log.debug(
        "[subagent] шаг %s: субагентов одновременно - %s (не больше %s сразу)%s",
        step if step is not None else "-",
        len(par),
        limit,
        f", других инструментов по очереди - {len(seq)}" if seq else "",
    )

    async def _par(i: int) -> None:
        async with sem:
            results[i] = await run_one(calls[i])

    async def _seq() -> None:
        for i in seq:
            results[i] = await run_one(calls[i])

    tasks = [asyncio.ensure_future(_par(i)) for i in par]
    if seq:
        tasks.append(asyncio.ensure_future(_seq()))
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        # Остановка генерации или сбой одного - остальные не бросаем крутиться.
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return results  # type: ignore[return-value]