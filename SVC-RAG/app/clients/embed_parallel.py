"""Параллельный прогон embed-батчей с сохранением порядка текстов.

concurrency=1 — как раньше (строго последовательно).
concurrency>1 — до N одновременных POST /embeddings (Semaphore).

Порядок гарантируется слотом results[i]: батчи могут завершиться в любом
порядке, но склейка всегда идёт по возрастанию i (т.е. по start в texts).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Awaitable, Callable, List, Optional, Sequence, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

EmbedBatchFn = Callable[[int, List[str]], Awaitable[List[List[float]]]]

# Подробный лог проверки порядка (по умолчанию вкл.). K8s: RAG_EMBED_ORDER_LOG=0 — выкл.
_ORDER_LOG = str(os.getenv("RAG_EMBED_ORDER_LOG", "1")).strip().lower() not in (
    "0",
    "false",
    "no",
)

def _text_fp(text: str) -> str:
    raw = (text or "").encode("utf-8", errors="replace")
    return hashlib.sha1(raw).hexdigest()[:10]

def _emb_fp(vec: Sequence[float]) -> str:
    if not vec:
        return "empty"
    # Несколько первых компонент + длина — достаточно, чтобы поймать перестановку.
    head = ",".join(f"{float(x):.5f}" for x in list(vec)[:4])
    return f"d{len(vec)}:{head}"

class EmbedBatchTimeout(RuntimeError):
    """Батч не уложился в таймаут — ни в наш, ни в таймаут шлюза.

    Отдельный тип, потому что это единственная ошибка, которую лечит НЕ повтор,
    а уменьшение батча: тот же батч по тому же таймауту упадёт снова.
    """

# Коды, которыми «не дождались» отвечают шлюзы: 408 — сам клиент, 504 —
# openresty/APISIX перед svc-rag-models, 524/598/599 — прокси семейства Cloudflare
# и nginx-сборок, встречаются в контуре на тех же маршрутах.
_TIMEOUT_STATUSES = (408, 504, 524, 598, 599)

# Дробление батча при таймауте.
#
# Зачем: после перевода svc-rag-models на CPU батч из 24 чанков считается дольше,
# чем ждёт шлюз, и приходит 504 Gateway Time-out. Повтор тем же батчем даёт ровно
# тот же 504, после чего падает индексация ВСЕГО документа и загруженный файл 
# удаляется из PVC. Половина батча считается примерно вдвое быстрее, поэтому 
# вместо повтора делим батч пополам и уходим вглубь до размера 1. 
# Документ индексируется медленнее, но индексируется —
# функциональность не отключается, убирается только заведомо бесполезная работа.
#
# K8s: RAG_EMBED_SPLIT_ON_TIMEOUT=0 — вернуть старое поведение (ошибка сразу).
_SPLIT_ON_TIMEOUT = str(os.getenv("RAG_EMBED_SPLIT_ON_TIMEOUT", "1")).strip().lower() not in (
    "0",
    "false",
    "no",
)

# Запоминать ли уроки таймаута (меньший батч, последовательные запросы) на
# следующие документы. Без этого каждый документ начинал бы с конфигурационных
# значений и снова тратил по таймауту шлюза на каждый первый батч.
# K8s: RAG_EMBED_ADAPT_ON_TIMEOUT=0 — не запоминать.
ADAPT_ON_TIMEOUT = str(os.getenv("RAG_EMBED_ADAPT_ON_TIMEOUT", "1")).strip().lower() not in (
    "0",
    "false",
    "no",
)

# Через сколько прогонов embed подряд БЕЗ таймаутов возвращать прежние батч и
# параллелизм. Считаются только прогоны, которые и вправду отправили батч
# текущего размера: запрос поиска из одного текста успевает всегда и ничего не
# доказывает. Обратный ход нужен, чтобы возврат модели на GPU не требовал ни
# правки переменных окружения, ни перезапуска пода.
# K8s: RAG_EMBED_ADAPT_RECOVER_AFTER, 0 — обратного хода нет.
ADAPT_RECOVER_AFTER = max(
    0, int(os.getenv("RAG_EMBED_ADAPT_RECOVER_AFTER", "3") or 0)
)

# До какого размера батча уменьшать, прежде чем трогать параллелизм.
#
# Cначала уменьшаем батч, и только когда уменьшать уже нечего, а таймауты
# продолжаются, делим параллелизм пополам — значит дело и вправду в числе
# одновременных запросов к одному CPU.
# K8s: RAG_EMBED_CONC_DROP_BATCH_FLOOR.
CONC_DROP_BATCH_FLOOR = max(
    1, int(os.getenv("RAG_EMBED_CONC_DROP_BATCH_FLOOR", "2") or 2)
)

def is_timeout_error(err: BaseException) -> bool:
    """Ошибка означает «не дождались ответа», а не «запрос неверный».

    Разбираем три формы, в которых таймаут доезжает сюда: исключение httpx,
    HTTP-статус шлюза и уже завёрнутый клиентом текст ответа (клиенты
    оборачивают ошибку в RuntimeError, чтобы тело шлюза дошло до пользователя).
    """
    if isinstance(err, EmbedBatchTimeout):
        return True
    try:
        import httpx
    except Exception:  # pragma: no cover — httpx есть всегда, но падать тут нечем
        httpx = None  # type: ignore[assignment]
    if httpx is not None:
        if isinstance(err, httpx.TimeoutException):
            return True
        if isinstance(err, httpx.HTTPStatusError):
            return err.response.status_code in _TIMEOUT_STATUSES
    # Последняя линия: клиент отдал RuntimeError с телом шлюза внутри.
    text = str(err).lower()
    if "gateway time-out" in text or "gateway timeout" in text:
        return True
    return any(f"http {code}" in text for code in _TIMEOUT_STATUSES)

def _with_timeout_split(
    embed_batch: EmbedBatchFn,
    log_prefix: str,
    on_batch_shrink: Optional[Callable[[int], None]],
    start_cap: int,
) -> EmbedBatchFn:
    """Обёртка: таймаут на батче → делим батч пополам и считаем половины подряд.

    Рекурсия идёт внутри слота вызывающего (и внутри его Semaphore), поэтому
    параллелизм не растёт: дробление не добавляет одновременных POST.

    Выученный размер (cap) общий на весь вызов. Каждый неудачный уровень
    стоит полного таймаута шлюза, и на разведку ушло больше половины
    времени. Теперь первый же батч, наткнувшийся на потолок, закрывает
    вопрос для всех остальных в этом вызове.
    """
    if not _SPLIT_ON_TIMEOUT:
        return embed_batch

    cap = {"size": max(1, int(start_cap or 1))}

    async def _run(start: int, batch: List[str]) -> List[List[float]]:
        if len(batch) > cap["size"]:
            # Про такой размер уже известно, что он не проходит: режем сразу,
            # не оплачивая ещё одно ожидание шлюза.
            size = cap["size"]
            logger.debug(
                "%s: батч %s–%s режу по выученному размеру %s без пробы",
                log_prefix,
                start + 1,
                start + len(batch),
                size,
            )
            out: List[List[float]] = []
            for off in range(0, len(batch), size):
                out.extend(await _run(start + off, batch[off : off + size]))
            return out
        try:
            return await embed_batch(start, batch)
        except Exception as e:
            if len(batch) <= 1 or not is_timeout_error(e):
                raise
            mid = len(batch) // 2
            if mid < cap["size"]:
                cap["size"] = mid
            logger.warning(
                "%s: батч %s–%s (%s фрагментов, %s символов) не уложился в таймаут "
                "(%s: %s) — делю на %s+%s и считаю половины по очереди",
                log_prefix,
                start + 1,
                start + len(batch),
                len(batch),
                sum(len(t or "") for t in batch),
                type(e).__name__,
                " ".join(str(e).split())[:200],
                mid,
                len(batch) - mid,
            )
            if on_batch_shrink is not None:
                try:
                    on_batch_shrink(len(batch))
                except Exception:
                    pass
            left = await _run(start, batch[:mid])
            right = await _run(start + mid, batch[mid:])
            return left + right

    return _run

async def embed_texts_in_batches(
    texts: Sequence[str],
    *,
    batch_size: int,
    concurrency: int,
    embed_batch: EmbedBatchFn,
    log_prefix: str = "embed",
    on_batch_shrink: Optional[Callable[[int], None]] = None,
) -> List[List[float]]:
    """Разбить texts на батчи и прогнать embed_batch с ограниченным параллелизмом.

    embed_batch(start_index, batch_texts) -> embeddings той же длины, что batch.
    Результат склеивается в исходном порядке texts.

    on_batch_shrink(size) вызывается, когда батч размера size пришлось дробить
    по таймауту: клиент может запомнить меньший размер и не наступать на те же
    грабли на следующих документах.
    """
    if not texts:
        return []
    bs = max(1, int(batch_size or 1))
    conc = max(1, int(concurrency or 1))
    embed_batch = _with_timeout_split(embed_batch, log_prefix, on_batch_shrink, bs)
    batches: List[tuple[int, List[str]]] = []
    for start in range(0, len(texts), bs):
        batches.append((start, list(texts[start : start + bs])))

    n_batches = len(batches)
    if n_batches == 1 or conc == 1:
        out: List[List[float]] = []
        for bi, (start, batch) in enumerate(batches):
            if n_batches > 1:
                logger.debug(
                    "%s: батч %s–%s из %s (seq slot=%s)",
                    log_prefix,
                    start + 1,
                    start + len(batch),
                    len(texts),
                    bi,
                )
            part = await embed_batch(start, batch)
            if len(part) != len(batch):
                raise ValueError(
                    f"{log_prefix}: батч slot={bi} start={start}: "
                    f"эмбеддингов {len(part)} ≠ текстов {len(batch)}"
                )
            out.extend(part)
        if _ORDER_LOG:
            logger.debug(
                "%s: порядок векторов OK (mode=sequential, texts=%s batches=%s) "
                "склейка по возрастанию start",
                log_prefix,
                len(texts),
                n_batches,
            )
        return out

    sem = asyncio.Semaphore(conc)
    # Слоты строго по индексу батча — финиш out-of-order не ломает порядок.
    results: List[Optional[List[List[float]]]] = [None] * n_batches
    finish_order: List[int] = []
    finish_lock = asyncio.Lock()
    t0 = time.perf_counter()

    async def _one(i: int, start: int, batch: List[str]) -> None:
        async with sem:
            t_first = _text_fp(batch[0])
            t_last = _text_fp(batch[-1])
            logger.debug(
                "%s: батч %s–%s из %s (parallel conc=%s slot=%s "
                "text_fp[first]=%s text_fp[last]=%s)",
                log_prefix,
                start + 1,
                start + len(batch),
                len(texts),
                conc,
                i,
                t_first,
                t_last,
            )
            part = await embed_batch(start, batch)
            if len(part) != len(batch):
                raise ValueError(
                    f"{log_prefix}: батч slot={i} start={start}: "
                    f"эмбеддингов {len(part)} ≠ текстов {len(batch)}"
                )
            results[i] = part
            async with finish_lock:
                finish_order.append(i)
            if _ORDER_LOG:
                logger.debug(
                    "%s: слот %s готов (start=%s size=%s) "
                    "emb_fp[first]=%s emb_fp[last]=%s finish_seq=%s",
                    log_prefix,
                    i,
                    start,
                    len(part),
                    _emb_fp(part[0]),
                    _emb_fp(part[-1]),
                    list(finish_order),
                )

    await asyncio.gather(
        *(_one(i, start, batch) for i, (start, batch) in enumerate(batches))
    )
    elapsed = time.perf_counter() - t0

    out: List[List[float]] = []
    order_ok = True
    for i, (start, batch) in enumerate(batches):
        part = results[i]
        if part is None:
            raise RuntimeError(
                f"{log_prefix}: внутренний сбой параллельного embed (slot={i})"
            )
        # Инвариант: часть i должна лечь ровно на texts[start:start+len]
        if len(out) != start:
            order_ok = False
            logger.error(
                "%s: ПОРЯДОК СЛОМАН перед слотом %s: len(out)=%s, ожидался start=%s",
                log_prefix,
                i,
                len(out),
                start,
            )
        out.extend(part)

    if len(out) != len(texts):
        raise ValueError(
            f"{log_prefix}: число эмбеддингов ({len(out)}) ≠ числу текстов ({len(texts)})"
        )

    # Точечная сверка границ батчей: вектор на abs_idx относится к texts[abs_idx].
    if _ORDER_LOG:
        for i, (start, batch) in enumerate(batches):
            part = results[i] or []
            for j in (0, len(batch) - 1):
                abs_idx = start + j
                same_text = texts[abs_idx] == batch[j]
                logger.debug(
                    "%s: align slot=%s idx=%s text_match=%s text_fp=%s emb_fp=%s",
                    log_prefix,
                    i,
                    abs_idx,
                    same_text,
                    _text_fp(batch[j]),
                    _emb_fp(part[j]),
                )
                if not same_text:
                    order_ok = False

    finish_sorted = sorted(finish_order) == list(range(n_batches))
    finish_was_sorted = finish_order == list(range(n_batches))
    if order_ok:
        logger.debug(
            "%s: порядок векторов OK (mode=parallel texts=%s batches=%s "
            "batch_size=%s concurrency=%s %.3fs) "
            "слоты склеены по возрастанию i; "
            "финиш батчей %s%s",
            log_prefix,
            len(texts),
            n_batches,
            bs,
            conc,
            elapsed,
            finish_order,
            (
                ""
                if finish_was_sorted
                else " (out-of-order finish, порядок в out сохранён через slots)"
            ),
        )
    else:
        logger.error(
            "%s: порядок векторов НЕ OK finish_order=%s finish_covers_all=%s",
            log_prefix,
            finish_order,
            finish_sorted,
        )
        raise RuntimeError(
            f"{log_prefix}: проверка порядка векторов провалена (см. логи align/slots)"
        )
    return out