"""Учёт памяти: кто сколько занял и сколько вернул.

Зачем это нужно
---------------
Разбор «почему под ест память» упёрся в то, что единственный доступный
прибор - график RSS в Kubesphere. Он показывает сумму по всему поду с
запаздыванием и не отвечает на главный вопрос: какая именно операция
заняла память и вернула ли она её обратно.

Здесь три прибора вместо одного.

RSS - сколько процесс забрал у ядра
    Та же величина, что на графике. Грубая: аллокатор питона отдаёт память
    ядру не сразу, поэтому мелкие операции покажут ноль, а освобождение
    почти всегда покажет ноль. Зато это ровно то число, по которому
    случается OOM.

Блоки - сколько объектов сейчас держит питон
    sys.getallocatedblocks(). Стоит одно обращение к счётчику, работает
    всегда и без подготовки. Именно этот прибор отвечает на вопрос
    «освободилось ли»: RSS после release() не шелохнётся, а блоки уйдут в
    минус на то же число, на какое ушли в плюс при сборке.

Питон - точные байты и пик
    tracemalloc. Даёт настоящие цифры вместо косвенных, но включается
    отдельно (режим full): он записывает происхождение каждой аллокации
    и сам съедает память и такты.

Режимы
------
off   ничего не делаем, накладных расходов нет вовсе
on    RSS + блоки + время. Дёшево, годится как постоянный режим
full  добавляет точные байты и пик через tracemalloc. Для охоты

Всё пишется в DEBUG. При обычном уровне логов вывода не будет даже в
режиме full, поэтому включать не страшно.
"""

from __future__ import annotations

import contextvars
import gc
import os
import sys
import time
import tracemalloc
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Глубина вложенности замеров. ContextVar, а не обычная переменная: под
# asyncio запросы чередуются на одном потоке, и общий счётчик перемешал бы
# лесенку разных запросов.
_depth: contextvars.ContextVar[int] = contextvars.ContextVar("memprobe_depth", default=0)

# Сколько кадров стека помнит tracemalloc. Один - этого хватает, чтобы
# назвать виновника, а больше заметно дороже.
TRACE_FRAMES = 1

MODES = ("off", "on", "full")

# Разобранный режим. Настройки грузятся один раз за жизнь пода и потом не
# меняются, поэтому и разбирать их на каждом замере незачем.
_mode_cache: Optional[str] = None

# Режим, выставленный ручкой диагностики на живом поде. Перебивает настройку.
_mode_override: Optional[str] = None

def _normalize(raw: Any) -> str:
    """Привести значение настройки к одному из MODES.

    Булево на входе - не паранойя. В YAML голое on разбирается как
    true, а off как false; в config.yml значение поэтому взято в
    кавычки, но кавычки - ровно то, что теряется при ручном переносе.
    Считаем true за on, чтобы диагностика не отключилась молча.
    """
    if isinstance(raw, bool):
        return "on" if raw else "off"
    value = str(raw if raw is not None else "off").strip().lower()
    if value in ("true", "yes", "1"):
        return "on"
    if value in ("false", "no", "0", "none", ""):
        return "off"
    return value if value in MODES else "off"

def mode() -> str:
    """Текущий режим замеров.

    Настройку не читаем через get_settings(): её первый вызов грузит
    config.yml, а это около мегабайта - и случился бы он внутри первого же
    измеряемого участка, испортив ровно тот замер, ради которого всё
    затевалось. Поэтому берём уже загруженные настройки, если они есть, а
    если их ещё нет - молчим. К моменту первого запроса конфиг давно
    загружен, так что на боевом пути это ничего не меняет.
    """
    if _mode_override is not None:
        return _mode_override
    global _mode_cache
    if _mode_cache is not None:
        return _mode_cache
    try:
        from app.core import config as cfg

        settings = getattr(cfg, "_settings", None)
        if settings is None:
            return "off"
        _mode_cache = _normalize(settings.rag.memprobe)
    except Exception:
        return "off"
    return _mode_cache

def set_mode(value: Optional[str]) -> str:
    """Переключить режим на живом поде. None - вернуться к настройке."""
    global _mode_override
    if value is None:
        _mode_override = None
    else:
        _mode_override = _normalize(value)
        if _mode_override != "full":
            stop_trace()
    current = mode()
    logger.debug("[MEM] режим замеров переключён на %s", current)
    return current

# ─── Приборы ─────────────────────────────────────────────────────────────────

def rss_mb() -> Optional[float]:
    """Сколько процесс забрал у ядра, МБ. None - мерить нечем (не Linux)."""
    try:
        with open("/proc/self/statm", "rb") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / 1048576.0
    except Exception:
        return None

def _proc_status() -> Dict[str, float]:
    """VmPeak/VmSize/VmHWM из /proc/self/status, МБ. Пусто вне Linux."""
    out: Dict[str, float] = {}
    keys = {
        "VmPeak": "vm_peak_mb",
        "VmSize": "vm_size_mb",
        "VmHWM": "rss_peak_mb",
        "VmRSS": "rss_mb",
        "Threads": "threads",
    }
    try:
        with open("/proc/self/status", "r", encoding="ascii", errors="replace") as f:
            for line in f:
                name, _, rest = line.partition(":")
                if name not in keys:
                    continue
                parts = rest.split()
                if not parts:
                    continue
                value = float(parts[0])
                out[keys[name]] = value if name == "Threads" else value / 1024.0
    except Exception:
        pass
    return out

def blocks() -> int:
    """Сколько блоков сейчас держит аллокатор питона."""
    try:
        return sys.getallocatedblocks()
    except Exception:
        return 0

def traced() -> tuple:
    """(текущие, пиковые) байты по tracemalloc. Нули, если он не запущен."""
    try:
        if not tracemalloc.is_tracing():
            return (0, 0)
        return tracemalloc.get_traced_memory()
    except Exception:
        return (0, 0)

# ─── Включение tracemalloc ───────────────────────────────────────────────────

def ensure_trace_started() -> bool:
    """Запустить tracemalloc, если режим full и он ещё не идёт."""
    try:
        if tracemalloc.is_tracing():
            return True
        tracemalloc.start(TRACE_FRAMES)
        logger.debug(
            "[MEM] tracemalloc запущен (%s кадр стека). Точные байты доступны, "
            "но и сам он теперь ест память",
            TRACE_FRAMES,
        )
        return True
    except Exception as e:
        logger.debug("[MEM] tracemalloc не запустился (%s)", e)
        return False

def stop_trace() -> bool:
    """Остановить tracemalloc и освободить его собственные записи."""
    try:
        if not tracemalloc.is_tracing():
            return False
        tracemalloc.stop()
        logger.debug("[MEM] tracemalloc остановлен")
        return True
    except Exception:
        return False

# ─── Замер ───────────────────────────────────────────────────────────────────

def _fmt_num(n: int) -> str:
    """Разряды пробелами: 182350 -> 182 350. Иначе глазом не прочитать."""
    sign = "-" if n < 0 else "+"
    body = f"{abs(n):,}".replace(",", " ")
    return f"{sign}{body}"

def _fmt_mb(x: Optional[float]) -> str:
    if x is None:
        return "н/д"
    return f"{x:+.2f} МБ" if x else "0.00 МБ"

@contextmanager
def probe(label: str, **extra: Any):
    """Замерить, сколько памяти заняла (или вернула) операция.

    Обычный with, а не async with: внутри блока можно спокойно
    ждать корутины, менеджеру это безразлично.

        with probe("распаковка блоба", байт=len(blob)):
            index = decode(blob)

    Никогда не бросает: диагностика не должна уметь ломать запрос.
    """
    m = mode()
    if m == "off":
        yield
        return

    if m == "full":
        ensure_trace_started()

    # Снятие показаний тоже в try. Прибор не должен уметь сорвать запрос -
    # ни на выходе, ни на входе. Без этого достаточно было бы одной ошибки
    # в rss_mb(), чтобы измеряемый блок вообще не выполнился.
    depth = 0
    token = None
    rss0 = None
    blk0 = None
    tr0 = 0
    try:
        depth = _depth.get()
        token = _depth.set(depth + 1)
        rss0 = rss_mb()
        blk0 = blocks()
        tr0, _ = traced()
    except Exception:
        pass

    t0 = time.perf_counter()
    try:
        yield
    finally:
        try:
            ms = (time.perf_counter() - t0) * 1000.0
            rss1 = rss_mb()
            blk1 = blocks()
            tr1, peak = traced()

            parts: List[str] = []
            if rss0 is not None and rss1 is not None:
                parts.append(f"RSS {rss0:.1f}->{rss1:.1f} ({_fmt_mb(rss1 - rss0)})")
            else:
                parts.append("RSS н/д")
            if blk0 is not None:
                parts.append(f"блоки {_fmt_num(blk1 - blk0)}")
            if tr0 or tr1:
                parts.append(f"питон {_fmt_mb((tr1 - tr0) / 1048576.0)}")
                parts.append(f"пик {peak / 1048576.0:.2f} МБ")
            parts.append(f"{ms:.1f} мс")
            for k, v in extra.items():
                parts.append(f"{k}={v}")

            logger.debug("[MEM] %s%s | %s", "  " * depth, label, " | ".join(parts))
        except Exception:
            # Замер сломался - и бог с ним. Запрос важнее.
            pass
        finally:
            # Глубину возвращаем в любом случае, иначе лесенка уползёт
            # вправо на весь остаток жизни пода.
            if token is not None:
                try:
                    _depth.reset(token)
                except Exception:
                    pass

# ─── Снимок состояния ────────────────────────────────────────────────────────

def _cache_counts() -> Dict[str, Any]:
    """Что лежит в известных кэшах прямо сейчас.

    Читаем модульные синглтоны напрямую, а не через get_*_service(): те
    создают сервис, если его ещё нет, и снимок сам менял бы картину.
    """
    out: Dict[str, Any] = {}

    def bm25_of(holder, name: str) -> None:
        if holder is None:
            # Не молчим: пустая строка и отсутствующая строка читаются
            # по-разному, а перепутать их здесь легко.
            out[name] = "сервис ещё не создан"
            return
        try:
            items = holder.values()
            out[name] = {
                "индексов_всего": len(items),
                "собрано_сейчас": sum(1 for i in items if i.ready),
                "чанков_в_памяти": sum(i.chunk_count for i in items if i.ready),
                "идёт_поисков": sum(getattr(i, "in_flight", 0) for i in items),
            }
        except Exception as e:
            out[name] = {"ошибка": str(e)}

    try:
        from app import dependencies as deps

        kb = getattr(deps, "_kb_service", None)
        pr = getattr(deps, "_project_rag_service", None)
        mr = getattr(deps, "_memory_rag_service", None)
        bm25_of(getattr(kb, "_bm25_by_key", None), "bm25_база_знаний")
        bm25_of(getattr(pr, "_bm25_by_key", None), "bm25_проекты")
        bm25_of(getattr(mr, "_bm25_by_dim", None), "bm25_библиотека")
    except Exception as e:
        out["bm25"] = {"ошибка": str(e)}

    try:
        from app.services import embed_routing as er

        out["embed_routing"] = {
            name: len(getattr(er, name, {}) or {})
            for name in (
                "_clients",
                "_dims",
                "_dim_locks",
                "_repos",
                "_auto_models",
                "_auto_locks",
                "_catalogs",
                "_rerank_clients",
            )
        }
    except Exception as e:
        out["embed_routing"] = {"ошибка": str(e)}

    try:
        from app import dependencies as deps

        pool = getattr(getattr(deps, "_pg", None), "pool", None)
        if pool is not None:
            out["пул_postgres"] = {
                "соединений": pool.get_size(),
                "простаивает": pool.get_idle_size(),
                "предел": pool.get_max_size(),
            }
    except Exception as e:
        out["пул_postgres"] = {"ошибка": str(e)}

    return out

def _by_type(limit: int) -> List[Dict[str, Any]]:
    """Живые объекты по типам: сколько штук и сколько весят.

    Обходит всё, что видит сборщик мусора. На куче в сотню мегабайт это
    секунда-две, поэтому только по явному запросу.

    Размер считается через sys.getsizeof, то есть без вложенного
    содержимого: у списка это его собственная таблица указателей, а не
    элементы. Для поиска виновника этого достаточно - важно, кого много.
    """
    stats: Dict[str, List[int]] = {}
    try:
        for obj in gc.get_objects():
            try:
                name = type(obj).__name__
                size = sys.getsizeof(obj)
            except Exception:
                continue
            slot = stats.get(name)
            if slot is None:
                stats[name] = [1, size]
            else:
                slot[0] += 1
                slot[1] += size
    except Exception:
        return []

    rows = [
        {"тип": k, "штук": v[0], "размер_мб": round(v[1] / 1048576.0, 2)}
        for k, v in stats.items()
    ]
    rows.sort(key=lambda r: r["размер_мб"], reverse=True)
    return rows[:limit]

def _top_allocations(limit: int) -> List[Dict[str, Any]]:
    """Откуда пришла живая память - по данным tracemalloc."""
    try:
        if not tracemalloc.is_tracing():
            return []
        snap = tracemalloc.take_snapshot()
        rows = []
        for stat in snap.statistics("lineno")[:limit]:
            frame = stat.traceback[0] if stat.traceback else None
            rows.append(
                {
                    "где": f"{frame.filename}:{frame.lineno}" if frame else "?",
                    "размер_мб": round(stat.size / 1048576.0, 3),
                    "блоков": stat.count,
                }
            )
        return rows
    except Exception:
        return []

def snapshot(deep: bool = False, limit: int = 25) -> Dict[str, Any]:
    """Полный срез потребления памяти на текущий момент."""
    cur, peak = traced()
    data: Dict[str, Any] = {
        "режим": mode(),
        "процесс": _proc_status() or {"замечание": "не Linux - /proc недоступен"},
        "питон": {
            "блоков_занято": blocks(),
            "tracemalloc_идёт": tracemalloc.is_tracing(),
            "traced_мб": round(cur / 1048576.0, 2),
            "traced_пик_мб": round(peak / 1048576.0, 2),
            "объектов_под_gc": len(gc.get_objects()) if deep else None,
            "счётчики_gc": list(gc.get_count()),
            "мусор_неубираемый": len(gc.garbage),
        },
        "кэши": _cache_counts(),
    }
    rss = rss_mb()
    if rss is not None:
        data["rss_мб"] = round(rss, 2)
    if deep:
        data["по_типам"] = _by_type(limit)
    top = _top_allocations(limit)
    if top:
        data["откуда_память"] = top
    elif mode() != "full":
        data["замечание"] = (
            "Разбивка по местам аллокации есть только в режиме full. "
            "Включить на живом поде: POST /v1/memory-diag/mode?value=full"
        )
    return data

def tracing() -> bool:
    """Идёт ли сейчас tracemalloc."""
    try:
        return tracemalloc.is_tracing()
    except Exception:
        return False

# ─── Замер на уровне запроса ─────────────────────────────────────────────────

# Пути, которые опрашиваются постоянно и сами по себе ничего не занимают.
# Без этого лог утонет в замерах опроса статуса: его дёргает каждая открытая
# вкладка раз в несколько секунд, и по этой же причине в коммите 27 из него
# убирали обычные строки лога.
QUIET_SUFFIXES = ("/reindex/status", "/health", "/memory-diag/snapshot")

def install_request_probe(app: Any) -> None:
    """Повесить замер памяти на каждый HTTP-запрос.

    Даёт верхнюю строку лесенки: сколько запрос занял целиком. Вложенные
    замеры внутри BM25 показывают, из чего это сложилось.

    Тело ответа отправляется уже после middleware, поэтому в замер оно не
    попадает. Для наших целей неважно: интересует индекс, а не сериализация
    двух десятков чанков.
    """

    @app.middleware("http")
    async def _memprobe_request(request, call_next):
        path = request.url.path
        if mode() == "off" or path.endswith(QUIET_SUFFIXES):
            return await call_next(request)
        with probe(f"ЗАПРОС {request.method} {path}"):
            return await call_next(request)

    return None