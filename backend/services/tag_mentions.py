"""Mentions агентов по тегам в чате: <#tagId|Name> — зеркало <$slug|Name> для skills.

Пользователь пишет #тег → вызываются все доступные агенты с этим тегом
(как обязательные субагенты по required_tag_ids карточки).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Set

TAG_MENTION_RE = re.compile(r"<#([^|>]+)\|?[^>]*>")
STRIP_TAG_MENTION_RE = re.compile(r"<#[^>]+>")


def extract_tag_ids_from_text(text: str) -> List[int]:
    if not text:
        return []
    out: List[int] = []
    seen: Set[int] = set()
    for m in TAG_MENTION_RE.finditer(text):
        raw = (m.group(1) or "").strip()
        try:
            tid = int(raw)
        except (TypeError, ValueError):
            continue
        if tid <= 0 or tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
    return out


def extract_tag_ids_from_messages(messages: Iterable[Any]) -> List[int]:
    out: List[int] = []
    seen: Set[int] = set()
    for message in messages or []:
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = message
        chunk = ""
        if isinstance(content, str):
            chunk = content
        elif isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
            chunk = "\n".join(parts)
        for tid in extract_tag_ids_from_text(chunk):
            if tid not in seen:
                seen.add(tid)
                out.append(tid)
    return out


def strip_tag_mentions(text: str) -> str:
    if not text:
        return text
    return STRIP_TAG_MENTION_RE.sub("", text).strip()


def normalize_tag_id_list(raw: Any) -> List[int]:
    if not raw:
        return []
    if isinstance(raw, (str, int)):
        raw = [raw]
    out: List[int] = []
    seen: Set[int] = set()
    for item in raw:
        try:
            tid = int(item)
        except (TypeError, ValueError):
            continue
        if tid <= 0 or tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
    return out


def collect_mention_tag_ids(
    *,
    user_message: str = "",
    data: Any = None,
) -> List[int]:
    """id тегов из payload.tag_ids и mentions в текущем сообщении."""
    from_payload: List[int] = []
    if isinstance(data, dict):
        from_payload = normalize_tag_id_list(data.get("tag_ids"))
    from_text = extract_tag_ids_from_text(user_message or "")
    out: List[int] = []
    seen: Set[int] = set()
    for tid in [*from_payload, *from_text]:
        if tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
    return out
