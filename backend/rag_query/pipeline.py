"""Предобработка запроса: нормализация, опционально опечатки, HyDE, multi-query, фильтры."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import json
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from backend.rag_query.metadata_filters import extract_filters_from_query
from backend.rag_query.preprocess import normalize_query
from backend.settings.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ProcessedQuery:
    original: str
    normalized: str
    query_for_search: str
    vector_query: Optional[str]
    multi_variants: Optional[List[str]]
    filters: Optional[Dict[str, Any]]


def _cut(text: Optional[str], limit: int = 300) -> str:
    """Текст для лога: одной строкой и без простыней.

    Запрос и варианты влезают целиком, HyDE-абзацы обрезаются — в логе важен
    не весь сгенерированный текст, а то, что модель вообще придумала.
    """
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else f"{s[:limit]}…(+{len(s) - limit} симв.)"


def _hyde_max_chars() -> int:
    try:
        return max(200, min(8000, int(os.getenv("RAG_HYDE_MAX_CHARS", "2000"))))
    except ValueError:
        return 2000


def _typos_min_similarity() -> float:
    """Насколько «исправленный» запрос обязан остаться похожим на исходный.

    Шаг исправления опечаток ПОДМЕНЯЕТ запрос, по которому дальше идёт и
    лексический поиск, и multi-query, и HyDE. Модель охотно пересказывает
    вопрос вместо правки опечаток: 02.08 «Мы выдаем БГ новому клиенту, в
    полномочиях Бизнес…» превратилось в «Как поставить на мониторинг
    банкгарантию нового клиента…» — близость 0.31, общее слово одно из
    тринадцати. Настоящая правка опечаток даёт 0.9+, поэтому порог посередине.

    ```RAG_TYPOS_MIN_SIMILARITY=0``` — проверку выключить.
    """
    try:
        return max(0.0, min(1.0, float(os.getenv("RAG_TYPOS_MIN_SIMILARITY", "0.6"))))
    except ValueError:
        return 0.6


async def _llm_short(prompt: str, system: str, max_tokens: int = 512) -> str:
    """Короткий служебный вызов LLM. Пустая строка = не получилось.

    ```ask_agent``` при сбое возвращает НЕ исключение, а человекочитаемый текст
    ошибки. Для чата это правильно, а здесь — яд: 01.08 строка «Не удалось
    получить ответ от модели…» прошла проверку длины и заменила собой запрос
    пользователя, после чего поиск шёл по тексту ошибки. Поэтому результат
    проверяется и в таком случае обнуляется.
    """
    from backend.agent_llm_svc import ask_agent
    from backend.llm_providers.routing import is_llm_error_text

    loop = asyncio.get_running_loop()
    # Модель этого чата, см. llm_judge.py: читаем до потока.
    from backend.settings.cef_logger.cef_audit_context import chat_model_peek

    _model_path = chat_model_peek()

    def _call() -> str:
        return ask_agent(
            prompt,
            history=[],
            streaming=False,
            system_prompt=system,
            model_path=_model_path,
            max_tokens=max_tokens,
            service_call=True,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        raw = await loop.run_in_executor(ex, _call)
    if is_llm_error_text(raw):
        logger.warning(
            "[RAG-PREP] LLM вернула текст ошибки вместо ответа — шаг пропущен: %s",
            _cut(raw, 160),
        )
        return ""
    return raw or ""


# Предыдущий вопрос пользователя. Контекстом, а не аргументом: до
# process_user_query путь идёт через три метода RagClient, и протаскивать
# историю через все сигнатуры - большая правка ради одной строки.
_prev_user_question: contextvars.ContextVar = contextvars.ContextVar(
    "rag_prev_user_question", default=None
)

# Длинный «предыдущий вопрос» размоет эмбеддинг сильнее, чем поможет.
_PREV_QUESTION_MAX_CHARS = 300

# Слова, которыми вопрос ссылается на сказанное раньше: предмет остался в
# предыдущей реплике. Сравниваем ЦЕЛИКОМ со словом, а не подстрокой: «нее»
# входит в «подробнее», и подстрочная проверка ловила обычные вопросы.
_FOLLOWUP_HINT_WORDS = frozenset(
    (
        "там",
        "тут",
        "туда",
        "оттуда",
        "него",
        "нему",
        "ним",
        "неё",
        "нее",
        "ней",
        "них",
        "этом",
        "этой",
        "этого",
        "этот",
        "эту",
        "эти",
        "тогда",
        "теперь",
    )
)

# Эти из двух слов, внутрь слова не попадут - ищем подстрокой.
_FOLLOWUP_HINT_PHRASES = ("в нем", "в нём", "а еще", "а ещё", "а также")

# Короткий вопрос почти всегда опирается на предыдущий: «посмотри в файле
# smeta.xlsx» - четыре слова, и предмета в них нет.
_FOLLOWUP_MAX_WORDS = 6


def _contextualize_enabled() -> bool:
    """RAG_QUERY_CONTEXTUALIZE=0 - не дополнять вектор предыдущим вопросом."""
    raw = (os.getenv("RAG_QUERY_CONTEXTUALIZE", "1") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def set_rag_dialog_context(
    history: Optional[List[Dict[str, Any]]],
    current: Optional[str] = None,
) -> None:
    """Запомнить предыдущий вопрос пользователя для дополнения запроса.

    Вызывать на КАЖДОМ сообщении, в том числе с пустой историей: иначе
    значение от прошлого запроса утечёт в следующий.

    current - текст текущего сообщения. На момент вызова история его ещё не
    содержит, но если порядок когда-нибудь поменяют, запрос не должен
    склеиться сам с собой.
    """
    now = " ".join(str(current or "").split())
    prev = ""
    for msg in reversed(list(history or [])):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role") or "").strip().lower() != "user":
            continue
        text = " ".join(str(msg.get("content") or "").split())
        if not text or (now and text == now):
            continue
        prev = text
        break
    _prev_user_question.set(prev[:_PREV_QUESTION_MAX_CHARS] or None)
    # Реплики для переписывания запроса моделью. В отличие от склейки берём
    # и ответы ассистента: предмет часто назван именно в них («в файле
    # smeta.xlsx есть раздел с итогами»).
    turns: List[str] = []
    for msg in list(history or [])[-_REWRITE_MAX_TURNS * 2 :]:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        text = " ".join(str(msg.get("content") or "").split())
        if not text:
            continue
        who = "Пользователь" if role == "user" else "Ассистент"
        turns.append(f"{who}: {text[:_REWRITE_TURN_MAX_CHARS]}")
    _dialog_turns.set(turns or None)
    # Кэш переписывания - свой на каждое сообщение: три источника (проект,
    # KB, память) переписывают один и тот же вопрос, вызов нужен один.
    _rewritten_query.set(None)


def looks_like_followup(text: str) -> bool:
    """Вопрос выглядит продолжением разговора, а не самостоятельным."""
    t = " ".join(str(text or "").split()).lower()
    if not t:
        return False
    if len(t.split()) <= _FOLLOWUP_MAX_WORDS:
        return True
    words = re.findall(r"[^\W\d_]+", t, flags=re.UNICODE)
    if any(w in _FOLLOWUP_HINT_WORDS for w in words):
        return True
    return any(phrase in t for phrase in _FOLLOWUP_HINT_PHRASES)


# Хвост разговора для переписывания: пар «вопрос-ответ».
_REWRITE_MAX_TURNS = 4
_REWRITE_TURN_MAX_CHARS = 400

# Реплики диалога и кэш переписывания - оба на время одного сообщения.
_dialog_turns: contextvars.ContextVar = contextvars.ContextVar(
    "rag_dialog_turns", default=None
)
_rewritten_query: contextvars.ContextVar = contextvars.ContextVar(
    "rag_rewritten_query", default=None
)


def _rewrite_enabled() -> bool:
    """RAG_QUERY_REWRITE=0 - не переписывать запрос моделью.

    Отдельно от RAG_QUERY_CONTEXTUALIZE: тот выключает бесплатную склейку,
    этот - только вызовы модели.
    """
    raw = (os.getenv("RAG_QUERY_REWRITE", "1") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


async def rewrite_query_with_context(query: str) -> Optional[str]:
    """Переписать вопрос по истории - когда поиск ничего не нашёл.

    Зовётся ПОСЛЕ неудачного поиска, а не до него: вызов модели тратится
    только там, где дешёвая склейка не помогла, а не на каждом коротком
    вопросе.

    Результат кэшируется на сообщение: источников три (проект, KB, память),
    вопрос у них один, вызов нужен один. Неудача тоже кэшируется - второй
    раз модель не дёргаем.

    None - переписывать нечем, выключено или не получилось.
    """
    if not _rewrite_enabled():
        return None
    cached = _rewritten_query.get()
    if cached is not None:
        return cached or None

    turns = _dialog_turns.get()
    original = " ".join(str(query or "").split())
    if not turns or not original:
        return None

    rewritten = await _llm_short(
        "Перепиши последний вопрос так, чтобы он был понятен без переписки: "
        "подставь в него предмет из предыдущих сообщений. Не отвечай на вопрос "
        "и не добавляй ничего от себя. Сохрани язык, термины и имена файлов "
        "автора. Верни только вопрос, одной строкой.\
\
Переписка:\
" + "\
".join(turns) + f"\
\
Последний вопрос: {original}",
        "Ты переписываешь поисковые запросы. Отвечай только текстом запроса.",
        max_tokens=200,
    )

    candidate = " ".join(str(rewritten or "").split())
    # Ответ в разы длиннее вопроса - модель не переписала, а ответила.
    if candidate and len(candidate) > max(200, len(original) * 6):
        logger.debug(
            "[RAG-PREP] переписывание: ответ слишком длинный, пропускаем: %s",
            _cut(candidate, 160),
        )
        candidate = ""
    if not candidate or candidate == original:
        _rewritten_query.set("")
        return None

    _rewritten_query.set(candidate)
    logger.debug(
        "[RAG-PREP] переписали запрос по истории: %r -> %r",
        _cut(original),
        _cut(candidate),
    )
    return candidate


async def process_user_query(
    user_text: str,
    *,
    fix_typos: bool = False,
    multi_query: bool = False,
    hyde: bool = False,
) -> ProcessedQuery:
    original = user_text or ""
    normalized = normalize_query(original)
    q = normalized
    if normalized != original:
        logger.debug(
            "[RAG-PREP] нормализация: %r -> %r", _cut(original), _cut(normalized)
        )
    if fix_typos:
        try:
            fixed = await _llm_short(
                "Исправь ТОЛЬКО опечатки, ошибки раскладки и согласования. Больше ничего "
                "не меняй: не переформулируй, не сокращай, не дополняй, не раскрывай "
                "аббревиатуры, сохрани порядок слов и термины автора. Если опечаток нет — "
                "верни текст без изменений. Верни только текст запроса, одной строкой, "
                "без комментариев:\n\n" + q,
                "Ты нормализуешь пользовательский поисковый запрос.",
                max_tokens=256,
            )
            fixed = (fixed or "").strip().split("\n")[0].strip()
            min_sim = _typos_min_similarity()
            similarity = (
                SequenceMatcher(None, q.lower(), fixed.lower()).ratio()
                if fixed
                else 0.0
            )
            if not (2 < len(fixed) < len(q) * 3 and len(fixed) < 2000):
                # Модель вернула что-то неправдоподобное — берём исходный запрос.
                logger.debug(
                    "[RAG-PREP] опечатки: ответ модели отклонён (длина %s при исходной %s): %r",
                    len(fixed),
                    len(q),
                    _cut(fixed),
                )
            elif similarity < min_sim:
                # Пересказ вместо правки опечаток. Подменять им запрос нельзя:
                # по нему идёт лексический поиск, и слова пользователя теряются.
                logger.debug(
                    "[RAG-PREP] опечатки: ответ отклонён как переформулировка "
                    "(близость %.2f < %.2f): %r -> %r",
                    similarity,
                    min_sim,
                    _cut(q),
                    _cut(fixed),
                )
            else:
                if fixed != q:
                    logger.debug(
                        "[RAG-PREP] опечатки исправлены (близость %.2f): %r -> %r",
                        similarity,
                        _cut(q),
                        _cut(fixed),
                    )
                else:
                    logger.debug("[RAG-PREP] опечатки: правок нет")
                q = fixed
        except Exception:
            logger.exception("RAG_QUERY_FIX_TYPOS")
    filters = extract_filters_from_query(q)
    vector_query: Optional[str] = None
    multi_variants: Optional[List[str]] = None
    if multi_query:
        try:
            raw = await _llm_short(
                'Сгенерируй 3–5 коротких альтернативных формулировок для поиска по базе (тот же смысл и язык, что у запроса). Верни только JSON вида {"variants":["..."]} без markdown.\n\nЗапрос:\n'
                + q,
                "Отвечай только компактным JSON.",
                max_tokens=400,
            )
            m = re.search("\\{[\\s\\S]*\\}", raw or "")
            if m:
                data = json.loads(m.group())
                vars_ = data.get("variants") or []
                if isinstance(vars_, list) and vars_:
                    multi_variants = [
                        str(x).strip() for x in vars_[:6] if str(x).strip()
                    ]
            if multi_variants:
                logger.debug(
                    "[RAG-PREP] multi-query придумал %s вариантов:\n%s",
                    len(multi_variants),
                    "\n".join(
                        f"    {i}. {_cut(v)}" for i, v in enumerate(multi_variants, 1)
                    ),
                )
            else:
                logger.debug(
                    "[RAG-PREP] multi-query: вариантов не получено, сырой ответ: %r",
                    _cut(raw),
                )
        except Exception:
            logger.exception("RAG_MULTI_QUERY_ENABLED")
    if hyde:
        try:
            hyde = await _llm_short(
                "Напиши 1–3 коротких абзаца гипотетического ответа на запрос (как если бы ты знал тему). Не ссылайся на реальные документы, законы или источники по названию.\n\nЗапрос:\n"
                + q,
                "HyDE: гипотетический текст для плотного поиска.",
                max_tokens=400,
            )
            hyde = (hyde or "").strip()
            if len(hyde) > 30:
                cap = _hyde_max_chars()
                vector_query = f"{q}\n\n{hyde[:cap]}"
                logger.debug(
                    "[RAG-PREP] HyDE придумал гипотетический ответ (%s симв., "
                    "в вектор уйдёт %s): %s",
                    len(hyde),
                    min(len(hyde), cap),
                    _cut(hyde, 500),
                )
            else:
                logger.debug(
                    "[RAG-PREP] HyDE: текст слишком короткий (%s симв.), "
                    "вектор ищем по исходному запросу: %r",
                    len(hyde),
                    _cut(hyde),
                )
        except Exception:
            logger.exception("RAG_HYDE_ENABLED")
    if filters:
        logger.debug("[RAG-PREP] фильтры из запроса: %s", filters)

    # Дешёвая склейка с предыдущим вопросом для follow-up (без вызова модели).
    prev = _prev_user_question.get()
    if _contextualize_enabled() and prev and looks_like_followup(q):
        if vector_query:
            vector_query = f"{prev}\n{vector_query}"
        else:
            vector_query = f"{prev}\n{q}"
        logger.debug(
            "[RAG-PREP] follow-up: вектор дополнен предыдущим вопросом: %r",
            _cut(vector_query),
        )

    logger.debug(
        "[RAG-PREP] итог препроцесса: fix_typos=%s, multi_query=%s (вариантов=%s), "
        "HyDE(vector_query=%s), filters=%s, query_for_search=%r",
        fix_typos,
        multi_query,
        len(multi_variants or []),
        bool(vector_query),
        bool(filters),
        _cut(q),
    )
    return ProcessedQuery(
        original=original,
        normalized=normalized,
        query_for_search=q,
        vector_query=vector_query,
        multi_variants=multi_variants,
        filters=filters,
    )
