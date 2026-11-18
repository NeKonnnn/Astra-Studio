"""Промпт-логика брифа AstraStudio: part summaries → финальный документ."""

from __future__ import annotations

from typing import List, Optional, Tuple

_BRIEF_SYSTEM = (
    "Ты аналитик рабочих встреч платформы AstraStudio. "
    "Пиши на языке стенограммы (смешанный → русский). "
    "Только факты из текста; ничего не выдумывай. "
    "Если данных мало — явно скажи об этом."
)

_FINAL_STRUCTURE = (
    "Собери финальный бриф в Markdown строго с разделами:\n"
    "## Суть\n"
    "2–4 предложения: зачем была встреча и главный итог.\n"
    "## Решения\n"
    "Маркированный список принятых решений (или «не зафиксированы»).\n"
    "## Задачи\n"
    "Маркированный список: действие — владелец (если есть) — срок (если есть).\n"
    "## Открытые вопросы\n"
    "Что осталось неясным.\n"
    "## Риски / блокеры\n"
    "Кратко или «не выявлены».\n"
    "Не копируй стенограмму целиком. Без вступлений вроде «Конечно»."
)

_PART_STRUCTURE = (
    "Суммируй ФРАГМЕНТ встречи в компактный Markdown:\n"
    "### Фрагмент\n"
    "- ключевые тезисы (3–6 пунктов)\n"
    "- решения (если были)\n"
    "- задачи (если были)\n"
    "Без выдумок, без полного копирования реплик."
)


def split_transcript_parts(transcript: str, max_chars: int = 9000) -> List[str]:
    text = (transcript or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    lines = text.splitlines()
    parts: List[str] = []
    buf: List[str] = []
    size = 0
    for line in lines:
        add = len(line) + 1
        if buf and size + add > max_chars:
            parts.append("\n".join(buf))
            buf = [line]
            size = add
        else:
            buf.append(line)
            size += add
    if buf:
        parts.append("\n".join(buf))
    return parts


def build_part_prompt(
    part: str,
    *,
    index: int,
    total: int,
    participants: str,
    instruction: Optional[str] = None,
) -> Tuple[str, str]:
    system = f"{_BRIEF_SYSTEM}\n{_PART_STRUCTURE}"
    user = (
        f"Фрагмент {index}/{total}. Участники: {participants or 'не указаны'}.\n"
    )
    if instruction:
        user += f"Доп. акцент: {instruction}\n"
    user += f"\nСтенограмма фрагмента:\n{part}"
    return system, user


def build_final_prompt(
    *,
    participants: str,
    source_app: str,
    instruction: Optional[str],
    part_summaries: Optional[List[str]],
    full_transcript: Optional[str],
) -> Tuple[str, str]:
    system = f"{_BRIEF_SYSTEM}\n{_FINAL_STRUCTURE}"
    user = (
        f"Участники: {participants or 'не указаны'}\n"
        f"Источник/приложение: {source_app or 'неизвестно'}\n"
    )
    if instruction:
        user += f"Доп. инструкция заказчика: {instruction}\n"
    if part_summaries and len(part_summaries) > 1:
        joined = "\n\n---\n\n".join(
            f"### Сводка части {i + 1}\n{s}" for i, s in enumerate(part_summaries)
        )
        user += (
            "\nНиже — промежуточные сводки длинной встречи. "
            "Собери из них единый бриф, устрани повторы:\n\n"
            f"{joined}"
        )
    else:
        user += f"\nСтенограмма:\n{full_transcript or ''}"
    return system, user
