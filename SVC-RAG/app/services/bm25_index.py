"""In-memory BM25 индекс и гибридное объединение со скорами векторного поиска."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from rank_bm25 import BM25Okapi

from app.core.logging import get_logger
from app.core.memprobe import probe
from app.database.models import DocumentVector

logger = get_logger(__name__)

_BM25_MIN_CORPUS_FOR_ZERO_CUT = 5

FetchContentsFn = Callable[[], Awaitable[List[Tuple[int, int, str]]]]
FetchChunkFn = Callable[[int, int], Awaitable[Optional[DocumentVector]]]

# Достать из Postgres отпечаток корпуса и готовый блоб, если он этому
# отпечатку соответствует. Промах - (отпечаток, None). Хранилище недоступно -
# (None, None), и тогда работаем как раньше, через сборку из текста.
LoadFromStoreFn = Callable[[], Awaitable[Tuple[Optional[str], Optional[bytes]]]]

# Положить собранный индекс в Postgres под этот отпечаток.
SaveToStoreFn = Callable[[str, bytes, int], Awaitable[None]]

# Classic RRF constant (Cormack et al.). Не зависит от абсолютных шкал cosine/BM25.
RRF_K = 60

# Индекс «занят» с того момента, как его забрал запрос, и до того, как этот
# запрос по нему поищет:
#
#     забрали индекс -> считаем эмбеддинг запроса -> вектора -> ищем по BM25
#     ^^^ заняли                                                ^^^ отпустили
#
# В середине индекс никто не трогает, но отбирать его нельзя: тот, кто взял,
# вот-вот им воспользуется. Занятие это и защищает.
#
# Обычно снимает сам поиск. Константа - предохранитель на случай, когда
# поиска не будет вовсе: стратегия могла разрешиться в чисто векторную, и
# тогда снимать занятие некому. Через минуту считаем, что не дождались.
#
# Величина некритична: несобранный индекс памяти не занимает, а собранный в
# худшем случае подержится лишнюю минуту.
CLAIM_TTL_SECONDS = 60.0


def _trim_heap(reason: str) -> None:
    """Вернуть ядру память только что отпущенного индекса.

    Отдельной функцией, а не вызовом из heap_trim напрямую: здесь живёт
    проверка настройки, и здесь же её удобно выключить целиком. Настройку
    читаем на каждом вызове - get_settings кэширован, а выключать trim
    приходится на живом поде, без пересборки образа.
    """
    try:
        from app.core.config import get_settings

        if not get_settings().rag.bm25_malloc_trim:
            return
        from app.core import heap_trim

        heap_trim.trim(reason)
    except Exception as e:
        # Возврат памяти ядру необязателен: он не должен уметь ломать поиск.
        logger.debug("BM25: вернуть память ядру не вышло (%s)", e)


def tokenize_ru_en(text: str) -> List[str]:
    """Простая токенизация для BM25: по пробелам и пунктуации."""
    text = (text or "").lower()
    return re.findall(r"\b\w+\b", text)

def rrf_score(rank: int, rrf_k: int = RRF_K) -> float:
    """Reciprocal Rank Fusion вклад для 0-based rank."""
    return 1.0 / float(rrf_k + rank + 1)

class InMemoryBm25Index:
    """BM25Okapi над чанками хранилища; пересобирается по флагу needs_rebuild."""

    def __init__(
        self,
        fetch_contents: FetchContentsFn,
        *,
        load_from_store: Optional[LoadFromStoreFn] = None,
        save_to_store: Optional[SaveToStoreFn] = None,
    ):
        self._fetch_contents = fetch_contents
        # Хранилище необязательно: без него класс ведёт себя ровно как раньше.
        # Так же он ведёт себя, если хранилище отвалилось - см. ensure_built.
        self._load_from_store = load_from_store
        self._save_to_store = save_to_store
        # Один полёт на индекс: при одновременных запросах к одному агенту
        # грузит первый, остальные ждут его результат. Без этого питон
        # распаковал бы блоб столько раз, сколько пришло запросов, - по
        # очереди, потому что распаковка синхронная и держит event loop.
        self._lock = asyncio.Lock()
        # Сколько поисков идёт прямо сейчас. Пока не ноль, память не отдаём:
        # иначе освободили бы индекс из-под работающего запроса.
        self.in_flight: int = 0
        # До какого момента индекс занят запросом, который его забрал, но до
        # поиска ещё не дошёл. Снимается сразу после поиска.
        self.claimed_until: float = 0.0
        # Сколько держать после поиска. Ставит держатель; ноль - отпускать
        # сразу, как только поиск закончился.
        self.retain_seconds: float = 0.0
        self.last_used: float = time.monotonic()
        self.index: Optional[BM25Okapi] = None
        self.metadatas: List[Dict[str, Any]] = []
        # Раньше здесь лежал self.texts - полный текст всех чанков стора
        # Держим счётчик вместо корпуса
        self.chunk_count: int = 0
        self.needs_rebuild: bool = True
        # Отпечаток корпуса, взятый при чтении из хранилища. Под ним потом
        # сохраняем собранное - считать второй раз незачем
        self._pending_fingerprint: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self.index is not None and bool(self.metadatas)

    def _release_after_search(self) -> None:
        """Отпустить память сразу после поиска - или через паузу."""
        if self.in_flight > 0 or not self.ready:
            return

        if self.retain_seconds <= 0:
            self._release_idle("поиск закончился")
            return

        # Пауза задана - отпустим отложенно. Задача лёгкая и одноразовая;
        # если за это время придёт новый поиск, отпускать будет нечего:
        # проверка ниже это увидит по in_flight и last_used.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        deadline = self.last_used
        loop.call_later(
            self.retain_seconds,
            lambda: self._release_idle("истекла пауза", only_if_last_used=deadline),
        )

    def _release_idle(self, reason: str, only_if_last_used: Optional[float] = None) -> None:
        """Отпустить, если с индексом действительно перестали работать."""
        if self.in_flight > 0 or not self.ready:
            return
        if only_if_last_used is not None and self.last_used != only_if_last_used:
            # За время паузы был ещё поиск - решать будет уже он.
            return
        if time.monotonic() < self.claimed_until:
            return
        logger.debug(
            "BM25: отпускаю индекс (%s чанков): %s",
            self.chunk_count,
            reason,
        )
        self.release()
        self.needs_rebuild = True
        # Строго после release(): до него объекты еще живы и отдавать нечего.
        _trim_heap(reason)

    def claim(self, ttl: float = CLAIM_TTL_SECONDS) -> None:
        """Занять индекс: его забрал запрос, поиск будет позже."""
        self.claimed_until = time.monotonic() + ttl

    def release(self) -> None:
        """Отпустить построенный индекс и всё, что он держит."""
        if self.index is None and not self.metadatas:
            # Отпускать нечего - и в лог тогда писать не о чем.
            self._pending_fingerprint = None
            return
        with probe("BM25 отпускание индекса", чанков=self.chunk_count):
            self.index = None
            self.metadatas = []
            self.chunk_count = 0
            self._pending_fingerprint = None

    def mark_dirty(self) -> None:
        # Не только флаг: отпускаем индекс сразу. Раньше он оставался в памяти
        # до следующего поиска по этому стору - а если по стору больше никто не
        # искал, то навсегда
        self.needs_rebuild = True
        self.release()

    async def ensure_built(self) -> bool:
        """Убедиться, что индекс готов: из хранилища, а если не вышло - собрать."""
        if not self.needs_rebuild and self.index:
            return self.ready

        async with self._lock:
            if not self.needs_rebuild and self.index:
                return self.ready
            with probe("BM25 попытка взять готовый из хранилища"):
                loaded = await self._load_stored()
            if loaded:
                return self.ready
            with probe("BM25 сборка из текста"):
                await self.build()
        return self.ready

    async def _load_stored(self) -> bool:
        """Попробовать взять готовый индекс из Postgres.

        Любая неудача - это просто False: соберём из текста. Хранилище здесь
        ускоритель, а не источник истины, и уронить поиск оно не должно ни
        при каких обстоятельствах.
        """
        if self._load_from_store is None:
            return False
        t0 = time.perf_counter()
        try:
            fingerprint, blob = await self._load_from_store()
        except Exception as e:
            logger.warning("BM25: чтение из хранилища не удалось (%s)", e)
            return False
        logger.debug(
            "BM25 хранилище: отпечаток=%s блоб=%s",
            fingerprint,
            f"{len(blob)} байт" if blob else "нет",
        )

        # Отпечаток запоминаем даже при промахе: под ним сохраним то, что
        # соберём. Второй раз его считать не надо.
        self._pending_fingerprint = fingerprint
        if not blob:
            logger.debug(
                "BM25 хранилище: готового индекса нет - соберём и положим"
            )
            return False

        from app.database.bm25_store import decode

        with probe("BM25 распаковка блоба", байт=len(blob)):
            restored = decode(blob)
        if restored is None:
            logger.debug("BM25 хранилище: блоб не годится - соберём заново")
            return False

        self.index, self.metadatas, self.chunk_count = restored
        self.needs_rebuild = False
        logger.info(
            "BM25 индекс загружен из хранилища: %s чанков за %.3fs",
            self.chunk_count,
            time.perf_counter() - t0,
        )
        return True

    async def _store_built(self) -> None:
        """Положить собранный индекс в Postgres под ранее взятый отпечаток."""
        fingerprint = getattr(self, "_pending_fingerprint", None)
        if self._save_to_store is None or not fingerprint or not self.ready:
            logger.debug(
                "BM25 хранилище: не сохраняю (хранилище=%s отпечаток=%s готов=%s)",
                self._save_to_store is not None,
                bool(fingerprint),
                self.ready,
            )
            return
        try:
            from app.database.bm25_store import encode

            t0 = time.perf_counter()
            with probe("BM25 упаковка индекса", чанков=self.chunk_count):
                payload = encode(self.index, self.metadatas)
            with probe("BM25 запись в Postgres", байт=len(payload)):
                await self._save_to_store(fingerprint, payload, self.chunk_count)
            logger.debug(
                "BM25 хранилище: сохранено %s чанков, %.1f КБ за %.3fs (отпечаток=%s)",
                self.chunk_count,
                len(payload) / 1024,
                time.perf_counter() - t0,
                fingerprint,
            )
        except Exception as e:
            logger.warning("BM25: запись в хранилище не удалась (%s)", e)

    async def build(self) -> None:
        t0 = time.perf_counter()
        try:
            rows = await self._fetch_contents()
            if not rows:
                logger.warning("Нет текстов для построения BM25 индекса")
                self.release()
                self.needs_rebuild = False
                return

            rows = list(rows)
            metadatas: List[Dict[str, Any]] = [
                {"document_id": d, "chunk": c} for d, c, _ in rows
            ]
            total = len(rows)

            def _make_corpus(source: list):
                """Токены по одному, с немедленным освобождением строки.

                BM25Okapi._initialize перебирает корпус и ни разу не берёт от
                него len(), поэтому генератор ему подходит. Это важно: раньше
                на пике в памяти лежали одновременно весь сырой текст из БД и
                все списки токенов, и только потом - словари частот. Теперь
                каждая строка отпускается сразу после токенизации.
                """

                def _corpus():
                    for i in range(total):
                        _d, _c, content = source[i]
                        source[i] = None
                        yield tokenize_ru_en(content)

                return _corpus()

            # Корпус BM25Okapi у себя не хранит: строит doc_freqs, idf и doc_len.
            self.index = BM25Okapi(_make_corpus(rows))
            del rows

            self.metadatas = metadatas
            self.chunk_count = total
            self.needs_rebuild = False
            logger.debug(
                "BM25 индекс построен: %s чанков за %.3fs",
                total,
                time.perf_counter() - t0,
            )
            # Кладём в Postgres, чтобы следующий раз не собирать заново.
            await self._store_built()
        except Exception as e:
            logger.error("Ошибка построения BM25 индекса: %s", e)
            self.release()
            self.needs_rebuild = False

    async def search(self, query: str, k: int) -> List[Tuple[int, int, float]]:
        """Возвращает список (document_id, chunk_index, score)."""
        self.in_flight += 1
        self.last_used = time.monotonic()
        try:
            return await self._search(query, k)
        finally:
            self.in_flight -= 1
            # Поиск состоялся - занятие больше не нужно, дальше решает пауза.
            self.claimed_until = 0.0
            self.last_used = time.monotonic()
            self._release_after_search()

    async def _search(self, query: str, k: int) -> List[Tuple[int, int, float]]:
        if not await self.ensure_built() or not self.index:
            return []
        try:
            q_tokens = tokenize_ru_en(query)
            if not q_tokens:
                return []
            scores = self.index.get_scores(q_tokens)
            top_indices = sorted(
                range(len(scores)), key=lambda i: scores[i], reverse=True
            )[:k]
            # При малом корпусе IDF вырождается: BM25 штрафует термы, которые
            # есть во всех документах, а при N=1 таков ЛЮБОЙ совпавший терм.
            _drop_zero = self.chunk_count >= _BM25_MIN_CORPUS_FOR_ZERO_CUT
            if not _drop_zero:
                logger.debug(
                    "BM25: корпус мал (%s чанков) - не отсекаем по нулевому скору",
                    self.chunk_count,
                )
            results: List[Tuple[int, int, float]] = []
            for idx in top_indices:
                meta = self.metadatas[idx]
                score = float(scores[idx])
                if _drop_zero and score <= 0:
                    continue
                results.append((meta["document_id"], meta["chunk"], score))
            return results
        except Exception as e:
            logger.error("Ошибка BM25 поиска: %s", e)
            return []

class Bm25IndexCache:
    """Держатель индексов: отпускает те, с которыми перестали работать.

    Индекс живёт, пока с ним работают,
    и отпускается, когда по нему перестали искать. Готовый лежит в Postgres,
    так что возврат стоит не пересборку, а чтение.

    Почему пауза, а не «отпускать сразу после запроса». Момента «запрос
    закончился» здесь не видно: сервис получает отдельные вызовы поиска.
    А между тем, как запрос забрал индекс, и тем, как он до поиска дошёл,
    успевает пройти эмбеддинг запроса - сотни миллисекунд. Отпустив в этот
    промежуток, мы бы отобрали индекс у того, кто вот-вот им воспользуется.
    Пауза покрывает этот зазор; на память она не влияет, потому что запросы
    короче неё.

    Индекс, по которому прямо сейчас идёт поиск, не отпускается никогда,
    сколько бы ни истекло.
    """

    def __init__(self, retain_seconds: float):
        self._retain = max(0.0, float(retain_seconds))
        self._items: Dict[Any, InMemoryBm25Index] = {}

    def __len__(self) -> int:
        return len(self._items)

    def get_or_create(
        self, key: Any, factory: Callable[[], InMemoryBm25Index]
    ) -> InMemoryBm25Index:
        self._sweep(skip=key)
        idx = self._items.get(key)
        if idx is None:
            idx = factory()
            self._items[key] = idx
        idx.retain_seconds = self._retain
        idx.last_used = time.monotonic()
        # Занимаем сразу: между «забрали» и «ищем» считается эмбеддинг
        # запроса, и в этот промежуток индекс отбирать нельзя.
        idx.claim()
        return idx

    def values(self) -> List[InMemoryBm25Index]:
        return list(self._items.values())

    def items(self) -> List[Tuple[Any, InMemoryBm25Index]]:
        return list(self._items.items())

    def drop(self, predicate: Callable[[Any], bool]) -> int:
        """Убрать индексы, ключ которых подходит под условие, и отпустить память."""
        keys = [k for k in self._items if predicate(k)]
        for k in keys:
            idx = self._items.pop(k, None)
            if idx is not None:
                idx.release()
        return len(keys)

    def _sweep(self, *, skip: Any = None) -> None:
        """Отпустить то, с чем перестали работать.

        Чистим по обращению, а не по таймеру: отдельная фоновая задача - это
        ещё один способ всё сломать, а под нагрузкой обращения и так идут
        десятками в секунду. Когда обращений нет, память держать не мешает
        никому - в этот момент за неё никто и не борется.

        Сама запись остаётся: она весит байты, а индекс внутри отпущен. Так
        сохраняется отметка времени, и при возврате не надо заводить объект
        заново.
        """
        now = time.monotonic()
        for key, idx in self._items.items():
            if key == skip or idx.in_flight > 0 or not idx.ready:
                continue
            if now < idx.claimed_until:
                # Кто-то уже забрал его и вот-вот будет искать.
                continue
            if now - idx.last_used >= self._retain:
                logger.debug(
                    "BM25: отпускаю индекс %s (%s чанков, простой %.1f с)",
                    key,
                    idx.chunk_count,
                    now - idx.last_used,
                )
                idx.release()
                idx.needs_rebuild = True

async def hybrid_combine_vector_bm25(
    query: str,
    vector_pairs: List[Tuple[DocumentVector, float]],
    k: int,
    *,
    bm25_index: InMemoryBm25Index,
    bm25_weight: float,
    fetch_chunk: FetchChunkFn,
    document_id: Optional[int] = None,
    allowed_document_ids: Optional[Set[int]] = None,
) -> List[Tuple[DocumentVector, float]]:
    """Гибрид vector+BM25 через weighted RRF (не max-norm linear blend).

    Почему не ```(1-w)*norm_vec + w*norm_bm25```:
      - max-norm внутри каждого списка даёт BM25-only потолок ```w``` (часто 0.35),
        а top-1 vector — ```1-w``` (0.65). Векторный шум всегда побеждает sparse hit.
      - Cosine ∈ ~[0,1] и сырой BM25 — разные шкалы; деление на max не делает их
        сопоставимыми по смыслу.

    RRF (Cormack et al.) работает по **рангам**, поэтому шкалы не смешиваются:
      score = (1-w)/(RRF_K+rank_vec) + w/(RRF_K+rank_bm25)
    Документ только в одном списке получает вклад только от него — на равных
    рангах sparse и dense сопоставимы; документы в обоих списках получают бонус.
    """
    # Best practice (LangChain EnsembleRetriever / Qdrant hybrid):
    # параллельно top-N из dense и sparse, затем weighted RRF (k=60).
    # Веса: bm25_weight — вклад sparse; (1-w) — dense. Дефолт ~0.35/0.65.
    w = max(0.0, min(1.0, float(bm25_weight)))
    w_vec = 1.0 - w
    # Симметричный пул кандидатов: не меньше k*3 и не меньше 48 с каждой стороны.
    pool = max(int(k) * 3, 48)
    bm25_results = await bm25_index.search(query, pool)
    # Индекс общий на всю таблицу - сужаем до разрешенных документов
    if document_id is not None:
        bm25_results = [row for row in bm25_results if int(row[0]) == int(document_id)]
    elif allowed_document_ids is not None:
        bm25_results = [
            row for row in bm25_results if int(row[0]) in allowed_document_ids
        ]

    # Dense тоже ограничиваем тем же pool (входной список может быть шире).
    vector_sorted = sorted(vector_pairs, key=lambda x: float(x[1]), reverse=True)[:pool]

    if not vector_sorted and not bm25_results:
        return []

    # Ранги 0-based по исходному порядку (уже отсортированы по убыванию скора).
    vec_rank: Dict[Tuple[int, int], int] = {}
    vec_raw: Dict[Tuple[int, int], float] = {}
    vec_obj: Dict[Tuple[int, int], DocumentVector] = {}
    for rank, (v, sc) in enumerate(vector_sorted):
        key = (int(v.document_id), int(v.chunk_index))
        if key in vec_rank:
            continue
        vec_rank[key] = rank
        vec_raw[key] = float(sc)
        vec_obj[key] = v

    bm25_rank: Dict[Tuple[int, int], int] = {}
    bm25_raw: Dict[Tuple[int, int], float] = {}
    for rank, (doc_id, chunk_index, sc) in enumerate(bm25_results):
        key = (int(doc_id), int(chunk_index))
        if key in bm25_rank:
            continue
        bm25_rank[key] = rank
        bm25_raw[key] = float(sc)

    all_keys = set(vec_rank.keys()) | set(bm25_rank.keys())
    max_vec = max(vec_raw.values(), default=1.0) or 1.0
    max_bm25 = max(bm25_raw.values(), default=1.0) or 1.0

    combined: List[Tuple[DocumentVector, float]] = []
    for key in all_keys:
        score = 0.0
        if key in vec_rank:
            score += w_vec * rrf_score(vec_rank[key])
        if key in bm25_rank:
            score += w * rrf_score(bm25_rank[key])

        # Микро-тайбрейк по нормализованным сырым скорам (не влияет на порядок RRF
        # между разными рангами, только разделяет почти равные RRF).
        tie = 0.0
        if key in vec_raw:
            tie += w_vec * (vec_raw[key] / max_vec)
        if key in bm25_raw:
            tie += w * (bm25_raw[key] / max_bm25)
        score += 1e-6 * tie

        vec = vec_obj.get(key)
        if vec is None:
            vec = await fetch_chunk(key[0], key[1])
            if isinstance(vec, tuple):
                vec = vec[0]
            if not vec:
                continue
        combined.append((vec, float(score)))

    combined.sort(key=lambda x: x[1], reverse=True)
    top = combined[:k]
    # --- Гарантированные слоты sparse-канала ---
    # Weighted RRF при w<0.5 математически не пускает BM25-only хиты в top-k
    # (лучший BM25-хит проигрывает dense-кандидату вплоть до ранга ~61*(1-2w)/w).
    # Поэтому top-N хитов BM25 гарантированно получают места в выдаче,
    # N = round(k*w) (минимум 1). Хиты, уже попавшие в top по RRF, занимают слот.
    if bm25_results and top and w > 0:
        slots = max(1, int(round(k * w)))
        top_keys = {(int(v.document_id), int(v.chunk_index)) for v, _ in top}
        injected: List[Tuple[DocumentVector, float]] = []
        satisfied = 0
        for doc_id, chunk_index, _sc in bm25_results:
            if satisfied >= slots:
                break
            key = (int(doc_id), int(chunk_index))
            if key in top_keys:
                satisfied += 1
                continue
            vec = vec_obj.get(key)
            if vec is None:
                vec = await fetch_chunk(key[0], key[1])
                if isinstance(vec, tuple):
                    vec = vec[0]
            if not vec:
                continue
            injected.append((vec, w * rrf_score(bm25_rank.get(key, 0))))
            top_keys.add(key)
            satisfied += 1
        if injected:
            keep = max(0, k - len(injected))
            top = top[:keep] + injected
            top.sort(key=lambda x: x[1], reverse=True)
            logger.debug(
                "[hybrid] sparse-гарантия: добавлено %d BM25-хитов в top-%d (slots=%d)",
                len(injected),
                k,
                slots,
            )
    return top