"""Rule-first + LLM fallback разбор голосовой команды."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from backend.settings.logging import get_logger
from backend.voice_assistant.catalog import (
    CONFIRM_PHRASES,
    INTENT_DESCRIPTIONS,
    llm_system_prompt,
    match_rules,
)

logger = get_logger(__name__)

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        m = _JSON_RE.search(text)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except Exception:
            return None


def _normalize_result(data: Dict[str, Any]) -> Dict[str, Any]:
    intent = str(data.get("intent") or "unknown").strip()
    if intent not in INTENT_DESCRIPTIONS:
        intent = "unknown"
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confirm = data.get("confirm_phrase") or CONFIRM_PHRASES.get(intent, "Готово")
    return {
        "intent": intent,
        "params": params,
        "confidence": max(0.0, min(1.0, confidence)),
        "confirm_phrase": str(confirm),
    }


def parse_voice_intent(text: str, *, ask_agent=None, model_path: Optional[str] = None) -> Dict[str, Any]:
    """Разобрать текст команды в intent.

    Сначала локальные правила (быстро), затем короткий LLM-вызов при необходимости.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return {
            "intent": "unknown",
            "params": {},
            "confidence": 0.0,
            "confirm_phrase": CONFIRM_PHRASES["unknown"],
        }

    ruled = match_rules(cleaned)
    if ruled:
        return ruled

    if not ask_agent:
        return {
            "intent": "unknown",
            "params": {},
            "confidence": 0.0,
            "confirm_phrase": CONFIRM_PHRASES["unknown"],
        }

    try:
        raw = ask_agent(
            cleaned,
            history=[],
            streaming=False,
            model_path=model_path,
            system_prompt=llm_system_prompt(),
        )
        parsed = _extract_json(raw if isinstance(raw, str) else str(raw))
        if parsed:
            return _normalize_result(parsed)
    except Exception:
        logger.exception("voice_assistant LLM parse failed")

    return {
        "intent": "unknown",
        "params": {},
        "confidence": 0.0,
        "confirm_phrase": CONFIRM_PHRASES["unknown"],
    }
