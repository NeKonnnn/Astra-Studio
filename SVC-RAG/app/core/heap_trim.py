"""Возврат освобождённой памяти ядру (glibc malloc_trim).

---------------
Питон освобождает объект, но память из процесса не уходит: аллокатор
оставляет её у себя под будущие нужды. Контейнер показывает RSS, и на
графике это выглядит так, будто освобождения не произошло.

Пока процесс ничем больше не занят, аллокатор куски всё-таки отдаёт сам.
В поде - нет: рядом идут опросы статуса от всех открытых вкладок,
эмбеддинги, ответы. Объекты разных запросов ложатся вперемешку, а кусок
возвращается только целиком опустевшим - с соседями он не пустеет никогда.
Отсюда и наблюдаемое «поднялось до 113 и держится».

malloc_trim просит glibc отдать ядру свободные страницы, не дожидаясь
этого стечения обстоятельств.

----------------------
Достаёт не до всей памяти. Мелочь до 512 байт живёт в аренах pymalloc, куда
glibc не дотягивается.

Работает только с glibc. На musl (alpine) и вне Linux функции просто нет -
тогда модуль тихо ничего не делает.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import time
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Не чаще раза в столько секунд. Вызов обходит списки свободных блоков glibc
# и на это время держит event loop. При тридцати запросах в секунду звать его
# тридцать раз бессмысленно: куча у процесса одна, и первый же вызов отдаёт
# всё, что можно отдать.
MIN_INTERVAL_SECONDS = 1.0

_libc: Optional[ctypes.CDLL] = None
_looked_up = False
_last_call = 0.0


def _resolve() -> Optional[ctypes.CDLL]:
    """Найти malloc_trim один раз. None - значит его здесь нет."""
    global _libc, _looked_up
    if _looked_up:
        return _libc
    _looked_up = True
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        # Обращение к атрибуту и есть проверка наличия символа.
        lib.malloc_trim.argtypes = [ctypes.c_size_t]
        lib.malloc_trim.restype = ctypes.c_int
        _libc = lib
        logger.debug(
            "malloc_trim: доступен, освобождённая память будет возвращаться ядру"
        )
    except Exception as e:
        # Сюда попадают musl и не-Linux. Это не поломка, а «нечем работать».
        logger.debug("malloc_trim: недоступен (%s) - работаем как раньше", e)
    return _libc


def _rss_mb() -> Optional[float]:
    """RSS процесса в мегабайтах. Читаем /proc: psutil в образе нет."""
    try:
        with open("/proc/self/statm", "rb") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / 1048576.0
    except Exception:
        return None


# Номер параметра mallopt из glibc malloc.h. Они там отрицательные, и -8 -
# это именно M_ARENA_MAX.
M_ARENA_MAX = -8


def set_arena_max(count: int) -> bool:
    """Ограничить число арен кучи glibc. False - не вышло или нечем.

    glibc заводит отдельную арену на поток, до восьми на ядро. Каждая
    дырявится сама по себе и назад не сжимается, и в контейнере это
    выглядит как RSS, который растёт по чуть-чуть и не возвращается.

    Обычно это делают переменной окружения MALLOC_ARENA_MAX, но её читает
    glibc при инициализации процесса - из конфига сервиса уже поздно.
    mallopt делает то же самое из кода: ограничение действует на арены,
    которые ещё не созданы, поэтому звать надо как можно раньше, до первых
    рабочих потоков.

    Тихая: не вышло - работаем как работали.
    """
    lib = _resolve()
    if lib is None:
        return False
    try:
        n = int(count)
        if n <= 0:
            return False
        lib.mallopt.argtypes = [ctypes.c_int, ctypes.c_int]
        lib.mallopt.restype = ctypes.c_int
        ok = bool(lib.mallopt(M_ARENA_MAX, n))
        logger.info(
            "malloc: арен кучи не больше %s (mallopt=%s)",
            n,
            "ок" if ok else "отказ",
        )
        return ok
    except Exception as e:
        logger.debug("malloc: ограничить число арен не вышло (%s)", e)
        return False


def set_arena_max_from_env() -> bool:
    """То же по RAG_MALLOC_ARENA_MAX. Не задано - ничего не трогаем.

    Отдельной функцией, чтобы точка вызова в main.py была одной строкой и
    не тащила туда ни os, ни разбор значения.
    """
    raw = (os.getenv("RAG_MALLOC_ARENA_MAX", "") or "").strip()
    if not raw:
        return False
    try:
        n = int(raw)
    except ValueError:
        logger.warning("RAG_MALLOC_ARENA_MAX=%r - не число, пропускаем", raw)
        return False
    return set_arena_max(n)


def trim(reason: str) -> None:
    """Попросить glibc вернуть ядру освобождённые страницы.

    Тихая и необязательная: любая неудача - просто ничего не делаем. Ради
    подчистки графика ронять поиск нельзя.

    Пишет в DEBUG, сколько вернулось на самом деле. Это не украшение: до
    выката в контур настоящую цифру взять негде, а по логу она видна сразу.
    """
    global _last_call
    lib = _resolve()
    if lib is None:
        return

    now = time.monotonic()
    if now - _last_call < MIN_INTERVAL_SECONDS:
        return
    _last_call = now

    before = _rss_mb()
    t0 = time.perf_counter()
    try:
        gave_back = bool(lib.malloc_trim(0))
    except Exception as e:
        logger.debug("malloc_trim: вызов не прошёл (%s)", e)
        return
    ms = (time.perf_counter() - t0) * 1000.0
    after = _rss_mb()

    if before is None or after is None:
        logger.debug(
            "malloc_trim после «%s»: за %.1f мс, ядру отдано=%s", reason, ms, gave_back
        )
        return

    logger.debug(
        "malloc_trim после «%s»: RSS %.1f -> %.1f МБ (вернули %.1f) за %.1f мс, ядру отдано=%s",
        reason,
        before,
        after,
        before - after,
        ms,
        gave_back,
    )
