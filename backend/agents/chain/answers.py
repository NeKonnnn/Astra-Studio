"""Метки [[ОТВЕТ N]] в ответе последнего шага цепочки -> ответы шагов как есть.

Последний шаг видит ответы шагов выше с метками (prompts.CHAIN_MARKS_NOTE) и
ставит метку вместо пересказа - код подставляет ответ шага целиком. Та же
подстановка, что у субагентов (subagents/answers.py): неизвестные метки
убираются, пустой итог - ответы шагов подряд.

Метки получает последний шаг всегда. Ответ шага, вставленный в итог меткой,
уже есть в итоге дословно - по завершении цепочки его отдельный блок из
сообщения убирается, иначе на экране он был бы дважды. Шаги, которых в итоге
нет, остаются видны. Итог вставил все шаги - промежуточные сворачиваются
целиком, как «скрывать промежуточные» в карточке первого агента. Во время
генерации шаги видны как обычно.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Sequence, Tuple

from backend.agents.subagents.answers import (
    _ANSWER_MARK_RE,
    expand_subagent_answers,
    orphan_answer_marks,
)
from backend.settings.logging import get_logger

log = get_logger(__name__)

def chain_marks_enabled(*, is_first: bool, is_last: bool) -> bool:
    """Метки [[ОТВЕТ N]] - последнему шагу цепочки (не единственному)."""
    return is_last and not is_first

def _numbered_answers(steps: Sequence[Dict[str, Any]]) -> Dict[int, Dict[str, str]]:
    out: Dict[int, Dict[str, str]] = {}
    for n, step in enumerate(steps, 1):
        text = str(step.get("content") or "").strip()
        if text:
            name = str(step.get("agent_name") or "Агент").strip() or "Агент"
            out[n] = {"name": name, "text": text}
    return out

def chain_marks_used(text: str, steps: Sequence[Dict[str, Any]]) -> List[int]:
    """Номера шагов, чьи ответы последний шаг вставил метками (по порядку, без повторов)."""
    answers = _numbered_answers(steps)
    used: List[int] = []
    for m in _ANSWER_MARK_RE.finditer(str(text or "")):
        n = int(m.group(1))
        if n in answers and n not in used:
            used.append(n)
    return used

def fold_inserted_steps(
    steps: Sequence[Dict[str, Any]],
    marks_used: Sequence[int],
    *,
    hide_sequential: bool,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Шаги для сообщения цепочки -> (шаги, скрывать ли промежуточные).

    Шаги, чьи ответы итог (последний шаг) вставил метками, из сообщения
    убираются - они в итоге дословно. Вставлены все - все шаги остаются, но
    промежуточные скрыты. Скрыты по карточке или меток нет - как было.
    """
    if hide_sequential or not marks_used or not steps:
        return list(steps), hide_sequential
    inserted = set(marks_used)
    rest = [s for n, s in enumerate(steps[:-1], 1) if n not in inserted]
    if not rest:
        log.debug(
            "[agent-chain] итог вставил метками ответы всех шагов %s - "
            "промежуточные ответы в сообщении свёрнуты",
            list(marks_used),
        )
        return list(steps), True
    log.debug(
        "[agent-chain] итог вставил метками ответы шагов %s - их блоки убраны из "
        "сообщения, остальные шаги видны: %s",
        list(marks_used),
        [s.get("agent_name") for s in rest],
    )
    return rest + [steps[-1]], False

def expand_chain_answers(text: str, steps: Sequence[Dict[str, Any]]) -> str:
    """Метки [[ОТВЕТ N]] в ответе последнего шага -> ответы шагов 1..N как есть."""
    answers = SimpleNamespace(answers=_numbered_answers(steps))
    if not answers.answers:
        return str(text or "")
    unknown: List[str] = orphan_answer_marks(text, answers)
    out = expand_subagent_answers(text, answers)
    if out != str(text or "").strip() or unknown:
        log.debug(
            "[agent-chain] итог: метки заменены ответами шагов (%s -> %s симв.), "
            "неизвестные метки убраны: %s",
            len(str(text or "")),
            len(out),
            unknown,
        )
    return out