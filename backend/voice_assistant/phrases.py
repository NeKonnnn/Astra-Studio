"""Шаблоны голосовых подтверждений."""

from backend.voice_assistant.catalog import CONFIRM_PHRASES


def confirm_for(intent_id: str, fallback: str = "Готово") -> str:
    return CONFIRM_PHRASES.get(intent_id, fallback)
