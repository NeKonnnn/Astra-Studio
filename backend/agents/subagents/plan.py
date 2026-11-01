"""План главного на ход с субагентами: составить, показать, следить за выполнением.

1. Перед первым шагом модель сама составляет подробный план по своей
   инструкции: шаги по порядку, у каждого - что именно сделать, кто
   (сама или какой субагент - она видит их имена и описания), на результаты каких шагов
    он опирается и каким должен быть результат.
2. План - в её системный промпт на весь ход.
3. После каждого ответа субагента модель видит прогресс по плану: сколько
   сделано, с чем сверить результат, какой шаг следующий и какие метки
   [[ОТВЕТ N]] ему передать, какие шаги пропущены.
4. Модель заканчивает, а шаги с субагентами из плана не сделаны - один раз
   получает их список: сделать или объяснить, почему не нужны.
5. В логе - план по шангам в начале хода и итог по плану в конце
   ([subagent-plan] ПЛАН ГЛАВНОГО / ИТОГ ПО ПЛАНУ)

Только главный (глубина 0) и только если у него есть инструмент subagent.
SUBAGENT_PLAN=0 (ConfigMap) - выключить.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional

from backend.agents.subagents.settings import (
    AgentSubagentsConfig,
    resolve_subagent_target,
    subagent_type_by_name,
)
from backend.settings.logging import get_logger

log = get_logger(__name__)

PLAN_MAX_STEPS = 40

PLAN_REQUEST = """Прежде чем выполнять запрос, составь подробный план по своей инструкции (системный промпт выше) и запросу пользователя.

Твои субагенты (имя - описание):
{agents}

Верни ТОЛЬКО JSON, без пояснений:
{{"steps": [{{"do": "...", "subagent": "имя или null", "uses": [номера шагов], "check": "..."}}]}}
- Шаги - в том порядке, в котором их требует твоя инструкция. Ничего не пропускай.
- do - конкретно, что сделать в этом шаге, по пунктам твоей инструкции (1-3 предложения).
- subagent - имя субагента из списка, если шаг выполняет он. Часть работы, для которой есть субагент, поручай ему. Шаг, который делаешь сам (например, собрать итоговый ответ), - null.
- uses - номера шагов, на результаты которых опирается этот шаг (сводка, выводы, резюме, проверка согласованности). Нет таких - [].
- check - одно предложение: каким по твоей инструкции должен быть результат шага (обязательные пункты, формат, объём).
- Запрос простой (вопрос, приветствие) - один шаг."""

PLAN_MISSING_NOTE = (
    "По твоему плану не выполнены шаги:\n{steps}\n"
    "Выполни их сейчас, по порядку плана. Если какой-то шаг действительно не нужен "
    "для этого запроса - дай итоговый ответ и одной строкой объясни почему."
)

def plan_enabled() -> bool:
    return (os.getenv("SUBAGENT_PLAN", "1") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )

def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Первый JSON-объект из ответа модели (модель может обернуть его в ```json)."""
    raw = str(text or "")
    start = raw.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(raw[start : i + 1])
                    except ValueError:
                        break
                    return data if isinstance(data, dict) else None
        start = raw.find("{", start + 1)
    return None

def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]

def parse_plan(
    text: str,
    config: AgentSubagentsConfig,
    *,
    parent_agent_id: Optional[int],
    agent_names: Optional[Mapping[int, str]],
) -> List[Dict[str, Any]]:
    """Ответ модели -> [{"do", "subagent", "target_id", "uses", "check"}]. Непонятный ответ - пустой план."""
    data = _extract_json(text)
    steps = (data or {}).get("steps")
    if not isinstance(steps, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in steps[:PLAN_MAX_STEPS]:
        if not isinstance(item, dict):
            continue
        do = _clean(item.get("do"), 400)
        sub = item.get("subagent")
        sub = str(sub).strip() if sub not in (None, "", "null") else ""
        target = (
            resolve_subagent_target(
                sub,
                parent_agent_id=parent_agent_id,
                config=config,
                agent_names=agent_names,
            )
            if sub
            else None
        )
        if not do and target is None:
            continue
        uses: List[int] = []
        raw_uses = item.get("uses")
        for u in raw_uses if isinstance(raw_uses, list) else []:
            try:
                n = int(u)
            except (TypeError, ValueError):
                continue
            # Опираться можно только на шаги, которые идут раньше.
            if 1 <= n <= len(out) and n not in uses:
                uses.append(n)
        out.append(
            {
                "do": do or sub,
                "subagent": sub if target is not None else "",
                "target_id": target,
                "uses": uses,
                "check": _clean(item.get("check"), 300),
            }
        )
    return out

def _agents_list(
    config: AgentSubagentsConfig, agent_names: Optional[Mapping[int, str]]
) -> str:
    """«- имя: описание» для каждого субагента - как в списке инструментов."""
    names = {**(config.agent_names or {}), **dict(agent_names or {})}
    lines = []
    for aid, value in subagent_type_by_name(config, agent_names).items():
        label = str(names.get(aid) or "")
        desc = label.split(" - ", 1)[1].strip() if " - " in label else ""
        lines.append(f"- {value}" + (f": {desc}" if desc else ""))
    return "\n".join(lines) or "- (нет)"

async def make_subagent_plan(
    *,
    messages: List[Dict[str, Any]],
    model_path: str,
    config: AgentSubagentsConfig,
    parent_agent_id: Optional[int],
    agent_names: Optional[Mapping[int, str]],
    request_extra: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Один вызов модели главного: план по её инструкции. Сбой - пустой план (ход идёт как раньше)."""
    try:
        from backend.llm_providers import get_registry

        registry = await get_registry()
        provider, model_id = registry.resolve(model_path)
        if not model_id:
            models = await provider.list_models()
            model_id = models[0].model_id if models else ""
        text = await provider.chat(
            list(messages)
            + [
                {
                    "role": "user",
                    "content": PLAN_REQUEST.format(
                        agents=_agents_list(config, agent_names)
                    ),
                }
            ],
            model_id,
            temperature=0.1,
            max_tokens=4096,
            request_extra=request_extra,
        )
    except Exception:
        log.exception("[subagent-plan] план не составлен - ход без плана")
        return []
    plan = parse_plan(
        text, config, parent_agent_id=parent_agent_id, agent_names=agent_names
    )
    if plan:
        log.info("%s", plan_log_text(plan))
    else:
        log.warning(
            "[subagent-plan] ПЛАН ГЛАВНОГО не составлен - ход без плана. "
            "Ответ модели на запрос плана: %r",
            str(text)[:2000],
        )
    return plan

def plan_log_text(plan: List[Dict[str, Any]]) -> str:
    """План для лога: по строке на шаг, опоры и проверка - строкой ниже."""
    lines = [
        f"[subagent-plan] ПЛАН ГЛАВНОГО: шагов {len(plan)}, с субагентами "
        f"{sum(1 for s in plan if s['target_id'] is not None)}"
    ]
    for i, s in enumerate(plan, 1):
        lines.append(f"  {i:>2}. [{s['subagent'] or 'сам'}] {s['do']}")
        extra = []
        if s["uses"]:
            extra.append("опирается на шаги " + ", ".join(str(n) for n in s["uses"]))
        if s["check"]:
            extra.append(f"проверка: {s['check']}")
        if extra:
            lines.append("      " + " | ".join(extra))
    return "\n".join(lines)

def log_plan_report(ctx: Any) -> None:
    """Конец хода главного: какие шаги плана сделаны, какие нет, кто вызван вне плана."""
    plan = getattr(ctx, "plan", None) or []
    if not plan:
        return
    done = _step_answers(ctx)
    sub_steps = [i for i, s in enumerate(plan, 1) if s["target_id"] is not None]
    if not sub_steps:
        return
    lines = [
        f"[subagent-plan] ИТОГ ПО ПЛАНУ: выполнено "
        f"{sum(1 for i in sub_steps if i in done)} из {len(sub_steps)} шагов с субагентами"
    ]
    for i, s in enumerate(plan, 1):
        if s["target_id"] is None:
            lines.append(f"· {i:>2}. [сам] {s['do']}")
        elif i in done:
            lines.append(f"✓ {i:>2}. [{s['subagent']}] ответ [[ОТВЕТ {done[i]}]]")
        else:
            lines.append(f"✗ {i:>2}. [{s['subagent']}] НЕ ВЫЗВАН")
    used = set(done.values())
    names = getattr(ctx, "agent_names", None) or {}
    extra = []
    for key, answer_no in sorted(
        (getattr(ctx, "done_calls", None) or {}).items(), key=lambda kv: kv[1]
    ):
        if answer_no in used:
            continue
        aid = str(key).split("\n", 1)[0]
        try:
            label = str(names.get(int(aid)) or "").split(" - ", 1)[0].strip()
        except ValueError:
            label = ""
        extra.append(f"{label or 'agent_' + aid} [[ОТВЕТ {answer_no}]]")
    if extra:
        lines.append("   вне плана вызваны: " + ", ".join(extra))
    log.debug("%s", "\n".join(lines))

def _who(s: Dict[str, Any]) -> str:
    return f"субагент «{s['subagent']}»" if s["target_id"] is not None else "сам"

def plan_system_block(plan: List[Dict[str, Any]]) -> str:
    """План - в системный промпт главного на весь ход."""
    if not plan:
        return ""
    lines = []
    for i, s in enumerate(plan, 1):
        line = f"{i}. {s['do']} — {_who(s)}"
        if s["uses"]:
            line += " — опирается на шаги " + ", ".join(str(n) for n in s["uses"])
        if s["check"]:
            line += f"\n   Проверка: {s['check']}"
        lines.append(line)
    return (
        "ТВОЙ ПЛАН НА ЭТОТ ЗАПРОС (ты составил его по своей инструкции). "
        "Выполняй шаги по порядку, не пропуская. Шагу, который опирается на другие, "
        "передавай их ответы метками [[ОТВЕТ N]]:\n" + "\n".join(lines)
    )

def _step_answers(ctx: Any) -> Dict[int, int]:
    """Номер шага плана -> номер ответа [[ОТВЕТ N]] выполнившего его субагента.

    Вызовы одного субагента (по done_calls, в порядке ответов) раздаются его
    шагам в порядке плана.
    """
    plan = getattr(ctx, "plan", None) or []
    by_target: Dict[int, List[int]] = {}
    for key, answer_no in sorted(
        (getattr(ctx, "done_calls", None) or {}).items(), key=lambda kv: kv[1]
    ):
        try:
            by_target.setdefault(int(str(key).split("\n", 1)[0]), []).append(
                int(answer_no)
            )
        except ValueError:
            continue
    out: Dict[int, int] = {}
    for i, s in enumerate(plan, 1):
        queue = by_target.get(s["target_id"]) if s["target_id"] is not None else None
        if queue:
            out[i] = queue.pop(0)
    return out

def _step_line(i: int, s: Dict[str, Any]) -> str:
    return f"шаг {i} — {s['do']} ({_who(s)})"

def _uses_hint(s: Dict[str, Any], done: Dict[int, int]) -> str:
    """Какие метки передать шагу, который опирается на другие."""
    marks = [f"[[ОТВЕТ {done[n]}]] (шаг {n})" for n in s["uses"] if n in done]
    return ("; передай в задание " + ", ".join(marks)) if marks else ""

def plan_progress_line(ctx: Any) -> str:
    """Строка прогресса к ответу субагента - только для модели главного."""
    plan = getattr(ctx, "plan", None) or []
    sub_steps = [(i, s) for i, s in enumerate(plan, 1) if s["target_id"] is not None]
    if not sub_steps:
        return ""
    done = _step_answers(ctx)
    last_done = max(done) if done else 0
    skipped = [i for i, _ in sub_steps if i < last_done and i not in done]
    nxt = next(((i, s) for i, s in sub_steps if i not in done and i > last_done), None)
    parts = [f"[План: выполнено {len(done)} из {len(sub_steps)} шагов с субагентами."]
    # Только что выполненный шаг - тот, чей ответ последний.
    just = max(done, key=lambda i: done[i]) if done else None
    if just and plan[just - 1]["check"]:
        parts.append(
            f"Результат шага {just} сверь с планом: {plan[just - 1]['check'].rstrip('.')}."
        )
    if skipped:
        parts.append(
            "Пропущены: " + ", ".join(f"шаг {i}" for i in skipped) + " - выполни их."
        )
    if nxt:
        parts.append(
            "Следующий по плану: " + _step_line(*nxt) + _uses_hint(nxt[1], done) + "."
        )
    elif not skipped:
        parts.append("Все шаги с субагентами выполнены - заверши по плану.")
    line = " ".join(parts) + "]"
    log.info("[subagent-plan] %s", line)
    return line

def plan_missing_note(ctx: Any) -> str:
    """Шаги с субагентами из плана, которые не выполнены, - сообщение модели (или пусто)."""
    plan = getattr(ctx, "plan", None) or []
    done = _step_answers(ctx)
    missing = [
        (i, s)
        for i, s in enumerate(plan, 1)
        if s["target_id"] is not None and i not in done
    ]
    if not missing:
        return ""
    log.warning(
        "[subagent-plan] модель заканчивает, не выполнив шаги плана: %s",
        [f"{i}:{s['subagent']}" for i, s in missing],
    )
    return PLAN_MISSING_NOTE.format(
        steps="\n".join(_step_line(i, s) + _uses_hint(s, done) for i, s in missing)
    )