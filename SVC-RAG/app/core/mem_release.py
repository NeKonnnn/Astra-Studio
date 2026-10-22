"""Освобождение памяти после тяжёлой работы.

---------
RAG_MEM_RELEASE=false            выключить целиком.
RAG_MEM_RELEASE_MB=16            прирост в МБ, начиная с которого
                                     освобождаем по месту.
RAG_MEM_RELEASE_EVERY_SECONDS=1800  период возврата по расписанию;
                                     0 - расписание не запускать.

Возврат по расписанию пишет итог на INFO: раз в полчаса это ряд точек, по
которому через неделю видно, стоит RSS полкой или ползёт. Остальные вызовы
пишут на DEBUG.

Меряет себя сама: в DEBUG видно, сколько было, сколько стало, сколько
циклов собралось и сколько это заняло. Тихая - освобождение памяти
необязательно и не должно уметь ломать запрос.
"""

from __future__ import annotations

import asyncio
import gc
import os
import time
from typing import Optional

from app.core import heap_trim
from app.core.logging import get_logger

# rss_mb читает /proc/self/statm и ничего не меняет. Берём готовую из
# memprobe, чтобы не заводить третью копию тех же шести строк: такая же
# есть внутри heap_trim, но она приватная.
from app.core.memprobe import rss_mb

logger = get_logger(__name__)

# Нижняя точка RSS, от которой считаем прирост. None - ещё не мерили.
_floor_mb: Optional[float] = None

# Задача возврата по расписанию. Живёт от старта до выключения пода.
_task: Optional[asyncio.Task] = None


def _enabled() -> bool:
    """Выключатель на живом поде, без пересборки образа."""
    return (os.getenv("RAG_MEM_RELEASE", "true") or "true").strip().lower() == "true"


def _threshold_mb() -> float:
    """Прирост RSS, ниже которого не тревожим сборщик и аллокатор."""
    try:
        return float(os.getenv("RAG_MEM_RELEASE_MB", "16"))
    except ValueError:
        return 16.0


def _period_seconds() -> float:
    """Период возврата по расписанию. Ноль и меньше - не запускать."""
    try:
        return float(os.getenv("RAG_MEM_RELEASE_EVERY_SECONDS", "1800"))
    except ValueError:
        return 1800.0


def release(reason: str, force: bool = False, loud: bool = False) -> None:
    """Вернуть память, если с прошлого раза её заметно прибавилось.

    reason уходит в лог - по нему видно, какая именно работа набрала
    память. force=True освобождает мимо порога: так работает возврат по
    расписанию и ручка диагностики.

    loud=True пишет итог на INFO, а не DEBUG. Так сделан возврат по
    расписанию: раз в полчаса это ряд точек, по которому через неделю
    видно, стоит RSS полкой или ползёт вверх. Спорить о накоплении по
    памяти бессмысленно, нужен ряд - и он должен быть виден без DEBUG.
    """
    global _floor_mb
    try:
        if not _enabled():
            return

        before = rss_mb()
        if before is None:
            # Вне Linux мерить нечем. Звать сборщик вслепую на каждый запрос
            # дороже, чем польза от этого, - работаем только по force.
            if not force:
                return
        elif not force:
            if _floor_mb is None:
                _floor_mb = before
                return
            if before - _floor_mb < _threshold_mb():
                # Заодно опускаем точку отсчёта: память могли вернуть и без нас.
                if before < _floor_mb:
                    _floor_mb = before
                return

        t0 = time.perf_counter()
        collected = gc.collect()
        # Сборщик отдал память аллокатору. Ядру её вернёт malloc_trim - он
        # пишет свою строку в лог, с точной цифрой возврата.
        heap_trim.trim(reason)
        after = rss_mb()
        ms = (time.perf_counter() - t0) * 1000.0

        say = logger.info if loud else logger.debug

        if before is None or after is None:
            say(
                "[MEM] Память после «%s»: циклов собрано %s, за %.1f мс",
                reason,
                collected,
                ms,
            )
            return

        _floor_mb = after
        say(
            "[MEM] Память после «%s»: RSS %.1f -> %.1f МБ (вернули %.1f), "
            "циклов собрано %s, за %.1f мс",
            reason,
            before,
            after,
            before - after,
            collected,
            ms,
        )
    except Exception as e:
        # Освобождение памяти необязательно: оно не должно уметь ломать работу.
        logger.debug("Освободить память после «%s» не вышло (%s)", reason, e)


async def _periodic_loop(period: float) -> None:
    """Тело задачи расписания. Живёт, пока её не отменят."""
    while True:
        try:
            await asyncio.sleep(period)
            release("расписание", force=True, loud=True)
        except asyncio.CancelledError:
            # Выключение пода. Молча уходим, иначе shutdown повиснет.
            raise
        except Exception as e:
            # Заснуть и попробовать снова: одна неудача не должна убивать
            # задачу на весь остаток жизни процесса.
            logger.debug("Возврат памяти по расписанию: %s", e)


def start_periodic() -> None:
    """Запустить возврат по расписанию. Зовётся из lifespan на старте."""
    global _task
    try:
        if _task is not None and not _task.done():
            return
        if not _enabled():
            logger.info("[MEM] возврат памяти выключен (RAG_MEM_RELEASE=false)")
            return
        period = _period_seconds()
        if period <= 0:
            logger.info(
                "[MEM] возврат памяти по расписанию выключен "
                "(RAG_MEM_RELEASE_EVERY_SECONDS=%s)",
                period,
            )
            return
        _task = asyncio.create_task(_periodic_loop(period))
        logger.info("[MEM] возврат памяти по расписанию: раз в %s с", int(period))
    except Exception as e:
        logger.warning("[MEM] не удалось запустить возврат по расписанию: %s", e)


def stop_periodic() -> None:
    """Остановить задачу. Зовётся из lifespan на выключении."""
    global _task
    try:
        if _task is not None:
            _task.cancel()
            _task = None
    except Exception as e:
        logger.debug("[MEM] не удалось остановить возврат по расписанию: %s", e)
