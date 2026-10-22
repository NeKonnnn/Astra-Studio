"""Общий модуль Full-Text Search для всех хранилищ RAG.

Ключевые идеи:

1. **OR-семантика, а не AND**. ``websearch_to_tsquery`` / ``plainto_tsquery`` по умолчанию
   соединяют лексемы запроса через AND. Это губительно для перечислительных и
   meta-запросов (пример: «Выведи списком все документы, в которых упоминается имя
   Константин» → AND(выведи, списком, документ, упомина, имя, константин) → 0 хитов).
   Для RAG-keyword-поиска мы сами строим ``to_tsquery``-выражение из OR-ов:
   ``константин | документ | упомина`` — чанк, где есть «Константин», матчится и
   ранжируется по ``ts_rank_cd``; чанки с более широким совпадением — выше.

2. **Две сгенерированные tsvector-колонки + GIN**: ``russian`` (лемматизация и стоп-слова)
   и ``simple`` (не теряет аббревиатуры/коды/латиницу/цифры: НК, ФЗ, v3, 2025). Матчим
   обе, ранг = ``GREATEST(ru, simple)`` — одна выдача ловит и естественный русский, и
   имена собственные/коды.

3. **Токенизация — здесь, в Python**, а не в Postgres. Postgres нужен очищенный OR-тсquery,
   иначе придётся заворачивать ``to_tsquery`` в ``try/except`` в SQL, что дорого.

4. Одна точка правды для keyword-логики всех 4 хранилищ (``document_vectors``,
   ``kb_vectors``, ``memory_rag_vectors``, ``project_rag_vectors``).
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

from asyncpg import Connection, exceptions as asyncpg_exceptions

logger = logging.getLogger(__name__)

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Минимальный список «мусорных» для FTS-запроса слов. Важно:
# - Это НЕ замена postgres-словарю: ``to_tsvector('russian', ...)`` в колонке
#   ``content_tsv_ru`` уже фильтрует русские стоп-слова.
# - Этот список используется ТОЛЬКО при сборке OR-tsquery: чтобы не тащить
#   в запрос бессмысленные функциональные слова, ломающие ранжирование и
#   раздувающие pg_stat_statements.
# - Если пользователь реально пишет запрос ровно из стоп-слов — всё равно
#   вернётся пустой tsquery и будет graceful skip.
_FTS_QUERY_STOPWORDS = {
    # Русский (короткий, консервативный набор — не зарезаем ничего содержательного):
    "и",
    "в",
    "во",
    "на",
    "по",
    "из",
    "с",
    "со",
    "к",
    "ко",
    "у",
    "о",
    "об",
    "а",
    "но",
    "или",
    "либо",
    "что",
    "чтобы",
    "как",
    "это",
    "эта",
    "эти",
    "тот",
    "та",
    "те",
    "не",
    "ни",
    "же",
    "ли",
    "бы",
    "уже",
    "ещё",
    "еще",
    "для",
    "при",
    "за",
    "до",
    "без",
    "то",
    "от",
    "над",
    "под",
    "про",
    "через",
    "мне",
    "меня",
    "мы",
    "нас",
    "нам",
    "вы",
    "вас",
    "вам",
    "они",
    "их",
    "им",
    "он",
    "она",
    "его",
    "ее",
    "её",
    "ей",
    "им",
    "ими",
    "есть",
    "быть",
    "был",
    "была",
    "было",
    "были",
    # Английский:
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "to",
    "of",
    "in",
    "on",
    "at",
    "for",
    "and",
    "or",
    "if",
    "with",
    "by",
    "from",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
}


def _safe_ident(name: str, kind: str = "identifier") -> str:
    if not name or not _SAFE_IDENT_RE.match(name):
        raise ValueError(f"Небезопасное имя {kind}: {name!r}")
    return name


async def ensure_fts_columns(conn: Connection, table_name: str) -> None:
    """Создаёт ``content_tsv_ru`` / ``content_tsv_simple`` + GIN-индексы (идемпотентно).

    Безопасно вызывать на каждом старте SVC-RAG: при наличии — NOOP, при отсутствии —
    одноразовая миграция (Postgres перепишет все строки таблицы, тяжёлая операция
    для больших корпусов, но выполнится один раз).
    """
    table = _safe_ident(table_name, "таблицы")

    statements = [
        (
            "content_tsv_ru",
            f"""
            ALTER TABLE {table}
                ADD COLUMN IF NOT EXISTS content_tsv_ru tsvector
                GENERATED ALWAYS AS (to_tsvector('russian'::regconfig, coalesce(content, ''))) STORED
            """,
        ),
        (
            "content_tsv_simple",
            f"""
            ALTER TABLE {table}
                ADD COLUMN IF NOT EXISTS content_tsv_simple tsvector
                GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, coalesce(content, ''))) STORED
            """,
        ),
    ]
    for col_name, sql in statements:
        try:
            await conn.execute(sql)
        except asyncpg_exceptions.PostgresError as e:
            # Поднимаем уровень с debug → warning: эта ошибка на живом корпусе
            # приводит к «молча отключённому» keyword-поиску, один из главных
            # источников «ничего не найдено» в RAG.
            logger.warning(
                "ensure_fts_columns(%s): колонка %s не создана: %s", table, col_name, e
            )

    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table}_tsv_ru_gin ON {table} USING gin (content_tsv_ru)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table}_tsv_simple_gin ON {table} USING gin (content_tsv_simple)"
    )


def query_has_searchable_content(text: str) -> bool:
    """Есть ли в запросе хоть что-то, что может стать индексируемой лексемой."""
    if not text:
        return False
    return bool(re.search(r"[\w\u0400-\u04FF]", text, re.UNICODE))


_TOKEN_RE = re.compile(r"[\wа-яёА-ЯЁ]+", re.UNICODE)


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return [t for t in _TOKEN_RE.findall(text) if t]


def _clean_for_tsquery_lexeme(token: str) -> Optional[str]:
    """Приводит токен к виду, безопасному для ``to_tsquery`` (без префиксов / без метасимволов).

    Postgres не любит в tsquery символы ``&``, ``|``, ``!``, ``(``, ``)``, ``:``, ``*``
    и пустые строки. Мы уже токенизируем по ``\\w`` — поэтому в токене остаются
    только буквы/цифры, но на всякий случай экранируем.
    """
    t = token.strip().lower()
    if len(t) < 2:
        return None
    if t in _FTS_QUERY_STOPWORDS:
        return None
    return t


def build_fts_or_query(text: str) -> Optional[str]:
    """Строит OR-выражение для ``to_tsquery`` из свободной формы запроса.

    Возвращает строку вида ``"константин | документ | упомина"`` (без кавычек) или
    ``None``, если после чистки ничего не осталось.

    Почему OR, а не AND:
      - Для перечислительных/meta-запросов AND убивает recall (см. модульный docstring).
      - ts_rank_cd хорошо «награждает» чанки, у которых матчей больше, поэтому
        OR-запросы не превращают выдачу в хаос: более релевантное всё равно выше.
      - Идеально дополняет векторный поиск в pipeline: он даёт семантику, FTS — точные
        совпадения (имена, коды, даты).
    """
    tokens = _tokenize(text)
    seen = set()
    out: List[str] = []
    for t in tokens:
        lex = _clean_for_tsquery_lexeme(t)
        if not lex or lex in seen:
            continue
        seen.add(lex)
        out.append(lex)
    if not out:
        return None
    return " | ".join(out)


# Детектор собственных имён/кодов для «entity lane»:
# - Кириллица/латиница с заглавной буквы длиной >= 2 (Лев, Пётр, Alice, Бортяков).
#   Порог опущен до 2, чтобы не пропускать короткие имена: «Лев», «Лев-2».
# - Последовательности заглавных букв длиной 2..6 (аббревиатуры: ОФЗ, НДС, НК, KPI).
# - Числа длиной >= 4 (даты, коды: 2025, 44-ФЗ).
_PROPERNOUN_RE = re.compile(
    r"(?:(?<!\w)[А-ЯЁA-Z][а-яёa-z]{1,}\b)"
    r"|(?:(?<!\w)[А-ЯЁA-Z]{2,6}\b)"
    r"|(?:(?<!\w)\d{4,}\b)"
)

# Слова на заглавную, которые НЕ считаем именами собственными даже если стоят в начале.
_NON_ENTITY_HINTS = {
    "Выведи",
    "Покажи",
    "Перечисли",
    "Назови",
    "Расскажи",
    "Опиши",
    "Дай",
    "Составь",
    "Сделай",
    "Сформируй",
    "Найди",
    "Объясни",
    "Что",
    "Кто",
    "Где",
    "Когда",
    "Какой",
    "Какая",
    "Какие",
    "Какое",
    "Почему",
    "Зачем",
    "Сколько",
    "Как",
    "Если",
    "Можно",
    "Нужно",
    "Список",
}


# НОРМАЛИЗАЦИЯ ПАДЕЖЕЙ — делегирована PostgreSQL-у.
#
# Раньше здесь жила функция `crude_russian_stem` — пытались обрезать окончания
# имён в Python, чтобы ILIKE `%Константи%` ловил падежные формы. Это была
# эвристика с ворохом crash-кейсов:
#
#   * «Пётр» / «Петра» / «Петру» — короткие (4 буквы), стеммер их не трогал,
#     ILIKE на «Петра» не находил «Пётр» в документе.
#   * «Лев» / «Льва» — снова коротко, стеммер пропускал.
#   * «Илье» → «ил» — наоборот, агрессивно, ложно матчило «или», «ил», «иллюзия».
#   * «Константин» → «Констант» → ложно матчило «константа» в техтекстах.
#
# Правильное место для нормализации падежей — `to_tsvector('russian', ...)`,
# который использует snowball-стеммер Postgres и уже стоит на колонке
# `content_tsv_ru`. Он корректно приводит «Константина → константин»,
# «Петра → петр», «Михайлины → михайлин», «документа → документ» и т.д.
#
# ILIKE остаётся в пайплайне только как страховка под то, что snowball
# принципиально не знает:
#   * коды и идентификаторы (СК0050629, 44-ФЗ, v3.2),
#   * латиницу (когда в тексте Konstantin.nf@yandex.ru, а не «Константин»),
#   * редкие токены, для которых snowball не строит лексему (имена-омографы).
# Для этих кейсов ILIKE ищет подстроку **токена как есть**, без самодельных
# обрезаний.


def extract_proper_nouns(text: str) -> List[str]:
    """Извлекает из запроса собственные имена / аббревиатуры / числовые коды.

    Возвращает список токенов в нижнем регистре (чтобы дальше прогнать через
    ``to_tsquery`` и матчить по FTS). Дедуплицирует, отбрасывает очевидно
    не-сущности вроде "Выведи", "Какой".
    """
    if not text:
        return []
    raw = _PROPERNOUN_RE.findall(text)
    out: List[str] = []
    seen = set()
    for tok in raw:
        if tok in _NON_ENTITY_HINTS:
            continue
        lex = _clean_for_tsquery_lexeme(tok)
        if not lex or lex in seen:
            continue
        seen.add(lex)
        out.append(lex)
    return out


def build_entity_or_query(text: str) -> Optional[str]:
    """Собирает OR-tsquery ТОЛЬКО по собственным именам/кодам из запроса.

    Используется в «entity lane» retrieval-пайплайна: делаем отдельный targeted FTS
    по именам, и пинаем найденные чанки в финальный ответ даже если их векторное
    сходство низкое. Это основной фикс рецидива «в документах не упоминается
    X», хотя X явно есть в корпусе.
    """
    nouns = extract_proper_nouns(text)
    if not nouns:
        return None
    return " | ".join(nouns)


# Имена файлов в запросе: "Воронин_Михаил.docx", "report.pdf", "data.xlsx".
# Поддерживаем форматы, которые реально парсятся в document_parser.py.
# Разрешаем юникод-буквы, цифры, _ - . ( ) пробел — чтобы ловить "CV_Nekrasov (1).pdf".
# Расширение в конце — обязательный маркер.
_FILENAME_EXTS = r"(?:pdf|docx|doc|docm|xlsx|xls|xlsm|txt|jpg|jpeg|png|webp|bmp|csv|md|log|rtf|odt|pptx)"
_FILENAME_RE = re.compile(
    # Без кавычек: не допускаем пробелов в стэме (иначе захватим часть вопроса),
    # НО разрешаем одно опциональное "(n)" с пробелом — типовой суффикс от
    # скачивания ("CV_Nekrasov (1).pdf").
    r"(?:^|(?<=[\s«»\"'“”()\[\],;]))"
    r"("
    r"[\w\u00C0-\uFFFF][\w\u00C0-\uFFFF\-.]{0,100}?"
    r"(?:\s*\([^)\s]{1,10}\))?"
    r"\." + _FILENAME_EXTS + r")(?=$|[\s«»\"'“”()\[\],;.!?])",
    re.IGNORECASE | re.UNICODE,
)
# Если имя в кавычках — ''"'"" / "«»" — вытащим и с пробелами.
_QUOTED_FILENAME_RE = re.compile(
    r"[\"'«»“”]([^\"'«»“”]+?\." + _FILENAME_EXTS + r")[\"'«»“”]",
    re.IGNORECASE | re.UNICODE,
)


def extract_filenames(text: str) -> List[str]:
    """Извлекает из запроса упоминания имён файлов.

    Детектирует:
      * 'CV_Nekrasov (1).pdf', 'report.docx', 'Воронин_Михаил.docx'
      * В кавычках: «doc.pdf», "report.xlsx", 'file.txt'
    Возвращает уникальный список (в исходном регистре, без окружающих пробелов).

    Зачем: когда пользователь пишет «сделай саммари по "Воронин_Михаил.docx"»,
    семантический поиск по тексту запроса НЕ находит содержимое документа —
    потому что в документе нет слов «саммари» и «сделай», а запрос адресован
    **конкретному файлу**. Такие запросы надо обрабатывать иначе: найти
    document_id по имени файла и вытащить ВСЕ его чанки в контекст.
    """
    if not text:
        return []
    found: List[str] = []
    seen = set()
    quoted_spans: List[Tuple[int, int]] = []
    for m in _QUOTED_FILENAME_RE.finditer(text):
        quoted_spans.append(m.span())
        name = m.group(1).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            found.append(name)
    for m in _FILENAME_RE.finditer(text):
        # Хвост имени в кавычках («Годовой отчёт 2024.pdf» -> «2024.pdf») — не отдельный файл.
        if any(s <= m.start() < e for s, e in quoted_spans):
            continue
        name = m.group(1).strip().strip(".,;:!?)")
        if name and name.lower() not in seen:
            seen.add(name.lower())
            found.append(name)
    return found


def escape_like(value: str) -> str:
    """Экранирует обратный слэш, ``%`` и ``_`` для LIKE/ILIKE (escape PostgreSQL по умолчанию — обратный слэш)."""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def substring_where_and_rank(
    *,
    vectors_alias: str,
    tokens: List[str],
    first_placeholder_idx: int,
) -> Tuple[str, str, int, List[str]]:
    """Страховочный поиск через ILIKE для случаев, когда FTS не сработал.

    Зачем: tsvector-колонки могут быть ещё не заполнены (миграция в процессе),
    OCR-текст может содержать нестандартные токены (пробелы внутри слов), или
    токенизатор ``russian`` неожиданно отбросил редкое имя. ILIKE берёт текст
    как есть и страхует от всех этих случаев.

    Возвращает: ``(where_clause, rank_expr, used_placeholders, prepared_params)``.

    ``rank_expr`` — число совпавших токенов (чем больше, тем выше). Это грубо, но
    достаточно для fallback. ``used_placeholders`` = количество токенов, params
    уже с обёрткой ``%token%``.
    """
    alias = _safe_ident(vectors_alias, "алиаса")
    if not tokens:
        return "FALSE", "0", 0, []
    conds: List[str] = []
    weights: List[str] = []
    prepared: List[str] = []
    for i, tok in enumerate(tokens):
        pi = first_placeholder_idx + i
        conds.append(f"{alias}.content ILIKE ${pi}")
        weights.append(f"(CASE WHEN {alias}.content ILIKE ${pi} THEN 1 ELSE 0 END)")
        prepared.append(f"%{escape_like(tok)}%")
    where_clause = "(" + " OR ".join(conds) + ")"
    rank_expr = "(" + " + ".join(weights) + ")::float"
    return where_clause, rank_expr, len(tokens), prepared


def fts_where_and_rank(
    *,
    vectors_alias: str,
    first_placeholder_idx: int,
) -> Tuple[str, str, int]:
    """Возвращает ``(where_clause, rank_expr, used_placeholders)``.

    Контракт изменился: **теперь caller должен передать уже собранный OR-tsquery
    через ``build_fts_or_query``** (один и тот же текст в $N и $N+1). Если
    ``build_fts_or_query`` вернул ``None`` (в запросе нет ни одной индексируемой
    лексемы) — FTS пропускается совсем.

    Пример использования::

        q_or = build_fts_or_query(q_text)
        if q_or is None:
            return []
        where_fts, rank_fts, used = fts_where_and_rank(vectors_alias="v", first_placeholder_idx=1)
        params = [q_or, q_or]  # под $1 (ru) и $2 (simple)
        # следующие параметры должны использовать $3, $4, ...
    """
    alias = _safe_ident(vectors_alias, "алиаса")
    p_ru = first_placeholder_idx
    p_simple = first_placeholder_idx + 1
    where_clause = (
        f"({alias}.content_tsv_ru @@ to_tsquery('russian'::regconfig, ${p_ru})"
        f" OR {alias}.content_tsv_simple @@ to_tsquery('simple'::regconfig, ${p_simple}))"
    )
    rank_expr = (
        "GREATEST("
        f"ts_rank_cd({alias}.content_tsv_ru, to_tsquery('russian'::regconfig, ${p_ru})),"
        f"ts_rank_cd({alias}.content_tsv_simple, to_tsquery('simple'::regconfig, ${p_simple}))"
        ")"
    )
    return where_clause, rank_expr, 2
