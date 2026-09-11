"""Хранилище готовых BM25-индексов в Postgres.

Зачем это нужно
---------------
BM25-индекс живёт в оперативной памяти процесса: библиотека ```rank_bm25```
иначе не умеет. Держать его между запросами дорого - при цепочке из десяти
агентов или полусотне сущностей память кончается. Держать нельзя, а собирать
заново на каждый запрос - лишняя работа.

Компромисс: собранный индекс складываем сюда, а из памяти отпускаем. При
следующем обращении не пересобираем из текста, а достаём готовый.

Три правила, на которых всё держится
------------------------------------
1. **Хранилище - ускоритель, а не источник истины.** Что бы с ним ни
   случилось - таблицы нет, блоб битый, база не отвечает - поиск обязан
   работать. Просто медленнее: соберём из текста, как раньше.

2. **Никакого pickle.** Читать pickle из базы значит исполнять то, что там
   лежит. Храним голые числа и списки (json + zlib), объект собираем сами.
   Заодно переживаем обновление ```rank_bm25```: у нас нет ссылки на класс.

3. **Свои таймауты.** В пуле соединений сервиса таймаутов нет вообще
   (```connection.py```): зависший запрос висит навсегда. Мы добавляем
   обращения к базе на КАЖДЫЙ поиск, поэтому ставим предел сами - иначе
   тормозящий Postgres превратится в висящий поиск.
"""

from __future__ import annotations

import hashlib
import json
import string
import zlib
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from rank_bm25 import BM25Okapi

from app.core.logging import get_logger

logger = get_logger(__name__)

STORE_TABLE = "bm25_index_store"

# Версия формата упаковки. Меняется при любой правке encode/decode - старые
# записи после этого просто не читаются и пересобираются.
FORMAT_VERSION = 1

# Метка формата и длина распакованного впереди сжатых данных.
#
# Без длины zlib.decompress растит выходной буфер удвоением, и на каждой
# пересадке в памяти живут сразу старый и новый: на блобе 595 КБ пик выходит
# 22 МБ при полезных 8.6. С известной длиной буфер выделяется один раз.
#
# Метка нужна, чтобы отличить новую запись от прежней. Прежние начинаются с
# сигнатуры zlib (0x78), так что спутать нельзя. Старые записи просто не
# читаются и пересобираются - ровно так и задумано для этого хранилища.
_HEADER = b"B1"
_HEADER_LEN = len(_HEADER) + 4

# Предел на любой наш запрос к базе, секунды. Лучше собрать индекс заново,
# чем держать пользователя в ожидании неотвечающего Postgres.
QUERY_TIMEOUT = 5.0

# Через сколько дней простоя запись считается ненужной и удаляется.
#
# Записи протухают, но не удаляются сами: у базы знаний ключ включает набор
# документов, и после добавления файла старый ключ уже никто не спросит.
# Чистим один раз при старте пода - это дёшево и не требует ни фоновой
# задачи, ни платы на каждом запросе.
STALE_DAYS = 7

# Имена таблиц подставляются в SQL строкой (параметром идентификатор не
# передать), поэтому проверяем их сами.
#
# Набором символов, а не регуляркой с диапазонами.
_ALLOWED_IDENT_CHARS = frozenset(string.ascii_letters + string.digits + "_")
_ALLOWED_FIRST_CHARS = frozenset(string.ascii_letters)

def _safe_table(name: str) -> str:
    if not name:
        raise ValueError("Пустое имя таблицы")
    if name[0] not in _ALLOWED_FIRST_CHARS:
        raise ValueError(
            f"Имя таблицы начинается недопустимым символом {name[0]!r}: {name!r}"
        )
    bad = sorted({c for c in name if c not in _ALLOWED_IDENT_CHARS})
    if bad:
        # Показываем коды: невидимые подмены иначе не увидеть глазами.
        codes = ", ".join(f"{c!r} (U+{ord(c):04X})" for c in bad)
        raise ValueError(f"Недопустимые символы в имени таблицы {name!r}: {codes}")
    return name

# ─── Упаковка ────────────────────────────────────────────────────────────────

def encode(index: BM25Okapi, metadatas: List[Dict[str, Any]]) -> bytes:
    """Индекс в байты: только данные, без ссылок на классы.

    zlib с уровнем 1 - осознанно: он сжимает в четыре раза за считанные
    миллисекунды, а более высокие уровни выигрывают проценты за куда большее
    время. Мы тут не архив собираем.
    """
    payload = {
        "v": FORMAT_VERSION,
        "doc_freqs": index.doc_freqs,
        "idf": index.idf,
        "doc_len": index.doc_len,
        "avgdl": index.avgdl,
        "corpus_size": index.corpus_size,
        "k1": index.k1,
        "b": index.b,
        "epsilon": index.epsilon,
        "metadatas": metadatas,
    }
    raw = json.dumps(payload).encode("utf-8")
    return _HEADER + len(raw).to_bytes(4, "big") + zlib.compress(raw, 1)

def decode(blob: bytes) -> Optional[Tuple[BM25Okapi, List[Dict[str, Any]], int]]:
    """Байты обратно в индекс. ```None```, если запись негодная.

    Негодная - это любая: битая, от другой версии формата, с потерянными
    полями. Разбираться, что именно сломалось, смысла нет: пересобрать из
    текста дешевле, чем чинить.
    """
    if not blob.startswith(_HEADER) or len(blob) < _HEADER_LEN:
        # Запись прежнего формата, без длины впереди. Не чиним - пересоберём.
        logger.info("BM25-хранилище: запись прежнего формата - пересоберём")
        return None

    try:
        size = int.from_bytes(blob[len(_HEADER) : _HEADER_LEN], "big")
        # Одним выражением, без промежуточной переменной. Это не про красоту:
        # распакованные байты, положенные в локальную переменную, живут до
        # конца выражения-присваивания и складываются с уже разобранной
        # структурой
        data = json.loads(zlib.decompress(blob[_HEADER_LEN:], 15, size + 1))
    except Exception as e:
        logger.warning("BM25-хранилище: запись не распаковалась (%s)", e)
        return None

    if not isinstance(data, dict) or data.get("v") != FORMAT_VERSION:
        logger.info(
            "BM25-хранилище: версия формата %r вместо %r - пересоберём",
            (data or {}).get("v") if isinstance(data, dict) else None,
            FORMAT_VERSION,
        )
        return None

    try:
        # BM25Okapi.__init__ требует корпус и строит всё заново - нам это
        # не нужно, у нас уже посчитано. Поэтому создаём пустой объект и
        # проставляем поля, которые использует get_scores.
        index = object.__new__(BM25Okapi)
        # Без защитных копий. json.loads только что создал эти словари и
        # списки, ссылок на них больше ни у кого нет, а типы уже те, что
        # нужны. Копирование было бы вторым экземпляром всей структуры: на
        # корпусе в 5000 чанков это 9.8 МБ лишнего пика на ровном месте.
        index.doc_freqs = data["doc_freqs"]
        index.idf = data["idf"]
        index.doc_len = data["doc_len"]
        index.avgdl = float(data["avgdl"])
        index.corpus_size = int(data["corpus_size"])
        index.k1 = float(data["k1"])
        index.b = float(data["b"])
        index.epsilon = float(data["epsilon"])
        index.tokenizer = None
        metadatas = data["metadatas"]
    except Exception as e:
        logger.warning("BM25-хранилище: запись неполная (%s)", e)
        return None

    if len(metadatas) != index.corpus_size:
        # Порядок metadatas обязан совпадать с порядком чанков в индексе:
        # search() берёт метаданные по номеру. Разошлось - выдача была бы
        # неверной, а это хуже, чем медленной.
        logger.warning(
            "BM25-хранилище: metadatas=%s против corpus_size=%s - не совпало",
            len(metadatas),
            index.corpus_size,
        )
        return None

    return index, metadatas, index.corpus_size

# ─── Схема ───────────────────────────────────────────────────────────────────

# Создавали ли уже таблицу. None - ещё не пробовали, False - не вышло и
# больше не пробуем: иначе каждый поиск платил бы за обращение к тому, чего
# нет. Сбрасывается только рестартом пода, и это правильно: если DDL не
# прошёл, само оно не починится.
_table_ready: Optional[bool] = None

async def ensure_table_once(conn) -> bool:
    global _table_ready
    if _table_ready is None:
        _table_ready = await ensure_table(conn)
        if _table_ready:
            logger.debug("BM25 хранилище: таблица %s готова", STORE_TABLE)
            await _drop_stale(conn)
    return _table_ready

async def _drop_stale(conn) -> None:
    """Убрать записи, которые давно никто не спрашивал."""
    try:
        # timedelta, а не строка: asyncpg сопоставляет interval именно с ним.
        # Со строкой падало с "'str' object has no attribute 'days'".
        result = await conn.execute(
            f"DELETE FROM {STORE_TABLE} WHERE updated_at < NOW() - $1::interval",
            timedelta(days=STALE_DAYS),
            timeout=QUERY_TIMEOUT,
        )
        removed = int(str(result).rsplit(" ", 1)[-1] or 0)
        if removed:
            logger.debug("BM25-хранилище: убрано %s устаревших записей", removed)
    except Exception as e:
        logger.warning("BM25-хранилище: чистка не удалась (%s)", e)

async def ensure_table(conn) -> bool:
    """Создать таблицу, если её нет. ```False``` - создать не удалось.

    Вызывающий по ```False``` обязан один раз пометить хранилище недоступным и
    больше в него не ходить: иначе каждый поиск будет платить за обращение к
    тому, чего нет.
    """
    try:
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {STORE_TABLE} (
                cache_key   TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                payload     BYTEA NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            timeout=QUERY_TIMEOUT,
        )
        return True
    except Exception as e:
        logger.warning("BM25-хранилище: таблица не создана (%s) - работаем без него", e)
        return False

# ─── Ключи ───────────────────────────────────────────────────────────────────

def cache_key(store: str, entity: str, dim: int) -> str:
    """Ключ записи: стор, сущность, размерность.

    Порядок именно такой, чтобы записи одной сущности убирались одним
    запросом по префиксу ```store:entity:``` - размерностей у неё может быть
    несколько, и все они становятся ненужными одновременно.
    """
    return f"{store}:{entity}:{int(dim)}"

def documents_entity(document_ids: Optional[List[int]]) -> str:
    """Имя сущности по набору документов - для базы знаний.

    Самих идентификаторов агента SVC-RAG не знает: ему приезжает белый список
    документов. Поэтому сущность - это отпечаток списка. Сортируем, чтобы
    порядок в запросе не плодил разные ключи для одного и того же набора.
    """
    if not document_ids:
        return "all"
    joined = ",".join(str(int(d)) for d in sorted(set(document_ids)))
    return "d" + hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]

# ─── Отпечаток ───────────────────────────────────────────────────────────────

def fingerprint_expr(vectors_alias: str) -> str:
    """Выражение отпечатка: по нему видно, что корпус изменился.

    ```count(*)``` ловит удаление документа, ```max(id)``` - добавление и
    переиндексацию (при ней старые чанки удаляются, новые получают большие
    id). Обе величины считаются по индексированным столбцам и стоят копейки.

    Отдельная колонка ```updated_at``` в таблицах векторов не нужна - её там и
    нет, есть только ```created_at```, а он не меняется при удалении.
    """
    alias = _safe_table(vectors_alias)
    return (
        f"(count(*)::text || ':' || coalesce(max({alias}.id), 0)::text)"
    )

# ─── Чтение и запись ─────────────────────────────────────────────────────────

async def load(
    conn,
    *,
    cache_key: str,
    vectors_table: str,
    scope_where: str = "",
    scope_params: Optional[List[Any]] = None,
) -> Tuple[Optional[str], Optional[bytes]]:
    """Отпечаток корпуса и готовый блоб, если он этому отпечатку соответствует.

    Одним запросом, а не двумя, по двум причинам. Меньше обращений к пулу - в
    сервисе он на десять соединений и без таймаутов. И, что важнее, нет
    промежутка между «посчитали отпечаток» и «взяли блоб»: иначе документ,
    изменённый ровно между ними, дал бы устаревший индекс, принятый за свежий.

    Возвращает ```(отпечаток, блоб | None)```. Отпечаток нужен и при промахе -
    с ним потом сохраняем пересобранное.
    """
    if not await ensure_table_once(conn):
        logger.debug("BM25 хранилище: недоступно, работаем без него")
        return None, None

    table = _safe_table(vectors_table)
    params = list(scope_params or [])
    key_placeholder = f"${len(params) + 1}"
    params.append(cache_key)

    sql = f"""
        WITH fp AS (
            SELECT {fingerprint_expr('v')} AS value
            FROM {table} v
            {scope_where}
        )
        SELECT fp.value AS fingerprint, s.payload
        FROM fp
        LEFT JOIN {STORE_TABLE} s
               ON s.cache_key = {key_placeholder}
              AND s.fingerprint = fp.value
    """
    logger.debug(
        "BM25 хранилище: читаю ключ=%s таблица=%s скоуп=%s",
        cache_key,
        table,
        scope_where or "вся таблица",
    )
    try:
        row = await conn.fetchrow(sql, *params, timeout=QUERY_TIMEOUT)
    except Exception as e:
        # Не поднимаем: без хранилища поиск обязан работать.
        logger.warning("BM25-хранилище: чтение не удалось (%s)", e)
        return None, None

    if not row:
        return None, None
    return row["fingerprint"], row["payload"]

async def save(
    conn,
    *,
    cache_key: str,
    fingerprint: str,
    payload: bytes,
    chunk_count: int,
) -> None:
    """Положить собранный индекс. Ошибки глушим: это ускоритель, не данные."""
    try:
        await conn.execute(
            f"""
            INSERT INTO {STORE_TABLE} (cache_key, fingerprint, payload, chunk_count, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (cache_key) DO UPDATE
               SET fingerprint = EXCLUDED.fingerprint,
                   payload     = EXCLUDED.payload,
                   chunk_count = EXCLUDED.chunk_count,
                   updated_at  = NOW()
            """,
            cache_key,
            fingerprint,
            payload,
            int(chunk_count),
            timeout=QUERY_TIMEOUT,
        )
        logger.debug(
            "BM25 хранилище: записан ключ=%s (%s чанков, %.1f КБ)",
            cache_key,
            chunk_count,
            len(payload) / 1024,
        )
    except Exception as e:
        logger.warning("BM25-хранилище: запись не удалась (%s)", e)

async def delete_by_prefix(conn, prefix: str) -> int:
    """Убрать записи сущности. Зовётся при удалении агента или проекта."""
    try:
        result = await conn.execute(
            f"DELETE FROM {STORE_TABLE} WHERE cache_key LIKE $1",
            f"{prefix}%",
            timeout=QUERY_TIMEOUT,
        )
        # asyncpg возвращает строку вида "DELETE 3"
        return int(str(result).rsplit(" ", 1)[-1] or 0)
    except Exception as e:
        logger.warning("BM25-хранилище: удаление по префиксу не удалось (%s)", e)
        return 0