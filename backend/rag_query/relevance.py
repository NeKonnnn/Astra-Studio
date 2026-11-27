"""Relevance для UI карточек чанков — абсолютная шкала.

'relevance = 1.0 - distance' (cosine similarity ∈ [0, 1]),
в UI: 'Math.round(relevance * 100)%'. Число не зависит ни от того, что
ещё нашлось по этому запросу, ни от того, сколько чанков вернулось — его
можно сравнивать между запросами и с либрой.

Наш 'score' для этого не годится: это скор ТОЙ шкалы, в которой работала
стратегия SVC-RAG.

* cosine ∈ [0, 1] — 'vector' / 'raw_cosine';
* weighted RRF ~0.016 — 'hybrid' и auto-ветка с keyword-merge;
* логит cross-encoder — после реранка (может быть <0 или >1);
* BM25/max ∈ [0, 1] — 'lexical'.

Поэтому SVC-RAG отдаёт сырой cosine чанка ОТДЕЛЬНЫМ полем (см.
'backend/rag_query/hit_types.py'), и проценты считаются по нему.

Что было раньше и почему это чинится. Не-cosine шкалы нормировались min-max
внутри выдачи: лучший чанк получал 100%, худший — 1%, ВСЕГДА, независимо от
качества. На RRF-скорах 0.01639 и 0.01587 (соседние ранги, разница в
третьем знаке) это давало 100% против 6%. Пользователь видел «один чанк
релевантен, остальные мусор» там, где чанки практически равны, — отсюда и
жалоба «чанк со 100% подавляет остальные». Плюс одиночная и пакетная
конверсии одного и того же скора расходились в разы.

Если cosine неизвестен (лексический поиск, чанк из BM25 / entity lane,
старый SVC-RAG без поля) — процент не выдумывается: см.
'relevance_percent_or_none'. Пакетный вход в таком случае откатывается
на прежнюю эвристику, чтобы карточки не остались вовсе без чисел.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Union

from backend.rag_query.hit_types import hit_cosine

Number = Union[int, float]

def _finite(value: Number) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f

def cosine_to_relevance_percent(cosine: Number) -> int:
    """Формула: cosine ∈ [0, 1] → 1..100%.

    Отрицательный cosine (вектора направлены в разные стороны) — это 0
    близости, а не «немного похоже»: зажимаем в минимум шкалы.
    """
    c = _finite(cosine)
    if c is None:
        return 1
    return max(1, min(100, int(round(max(0.0, c) * 100.0))))

def score_to_relevance_percent(score: Number) -> int:
    """Один score → целое 1..100.

    Оставлено для случаев, когда cosine недоступен и показать что-то надо.
    Значение в [0, 1] считается косинусом, всё остальное — логитом реранкера
    и прогоняется через сигмоиду. Между запросами такие числа несопоставимы;
    предпочитайте 'cosine_to_relevance_percent'.
    """
    s = _finite(score)
    if s is None:
        return 1
    if 0.0 <= s <= 1.0:
        pct = int(round(s * 100.0))
    else:
        pct = int(round(100.0 / (1.0 + math.exp(-s))))
    return max(1, min(100, pct if pct > 0 else 1))

def relevance_percent_or_none(hit: object) -> Optional[int]:
    """Процент по сырому cosine хита. None — cosine неизвестен.

    None означает «честно нечего показать»: чанк найден лексически, и
    косинусной близости у него нет. Вызывающий решает, скрыть процент или
    подставить оценку по 'score'.
    """
    cosine = hit_cosine(hit)
    if cosine is None:
        return None
    return cosine_to_relevance_percent(cosine)

def hits_to_relevance_percents(hits: Sequence[object]) -> List[int]:
    """Хиты → проценты для карточек чанков.

    Cosine есть — абсолютная шкала. Нет ни у одного хита (старый
    SVC-RAG, лексический поиск) — откат на 'scores_to_relevance_percents',
    чтобы карточки не остались без чисел. Смешанный случай: у кого cosine
    есть — по нему, остальным ставится оценка по score, приведённая к той же
    шкале, чтобы порядок в списке не выглядел перепутанным.
    """
    if not hits:
        return []
    cosines = [hit_cosine(h) for h in hits]
    if all(c is None for c in cosines):
        return scores_to_relevance_percents([_hit_score(h) for h in hits])
    fallback = scores_to_relevance_percents([_hit_score(h) for h in hits])
    return [
        cosine_to_relevance_percent(c) if c is not None else fallback[i]
        for i, c in enumerate(cosines)
    ]

def _hit_score(hit: object) -> float:
    try:
        return float(hit[1])  # type: ignore[index]
    except (TypeError, ValueError, IndexError, KeyError):
        return 0.0

def scores_to_relevance_percents(scores: Sequence[Number]) -> List[int]:
    """Пакетная конверсия скоров БЕЗ cosine — запасной путь.

    Абсолютная всюду, где это возможно: cosine и BM25/max ∈ [0, 1] считаются
    напрямую, логиты реранкера — через сигмоиду. Прежней min-max растяжки
    внутри выдачи здесь больше нет: она гарантировала 100% сверху и 1% снизу
    при любом качестве и делала числа несравнимыми между запросами.

    RRF-скоры (~0.01–0.03) честно дадут 1–3%: это правда, что абсолютной
    близости они не выражают. Настоящие проценты для таких стратегий берутся
    из 'cosine' (см. 'hits_to_relevance_percents').
    """
    if not scores:
        return []
    return [score_to_relevance_percent(s) for s in scores]