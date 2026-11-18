"""Текстовый echo-detect для dual-track: mic повторяет system audio."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence


_WORD_RE = re.compile(r"[\wа-яёА-ЯЁ]+", re.UNICODE)


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "") if len(t) > 1]


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    if not inter:
        return 0.0
    return inter / float(len(sa | sb))


def coverage(a: Sequence[str], b: Sequence[str]) -> float:
    """Доля токенов a, покрытых b (для коротких mic-фрагментов)."""
    if not a or not b:
        return 0.0
    sb = set(b)
    hit = sum(1 for t in a if t in sb)
    return hit / float(len(a))


def is_echo_text(
    local_text: str,
    remote_texts: Iterable[str],
    *,
    min_tokens: int = 3,
    jaccard_threshold: float = 0.55,
    coverage_threshold: float = 0.75,
) -> bool:
    local = _tokens(local_text)
    if len(local) < min_tokens:
        return False
    remote_all: List[str] = []
    for t in remote_texts:
        remote_all.extend(_tokens(t))
    if len(remote_all) < min_tokens:
        return False
    if jaccard(local, remote_all) >= jaccard_threshold:
        return True
    if coverage(local, remote_all) >= coverage_threshold and len(local) >= 4:
        return True
    return False


def mark_echo_segments(
    segments: List[Dict[str, Any]],
    *,
    window_ms: int = 12_000,
) -> List[Dict[str, Any]]:
    """Помечает local-сегменты, похожие на соседний remote, флагом is_echo."""
    out = [dict(s) for s in segments]
    for i, seg in enumerate(out):
        if seg.get("track") != "local":
            continue
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = int(seg.get("start_ms") or 0)
        remotes = [
            (r.get("text") or "")
            for r in out
            if r.get("track") == "remote"
            and not r.get("is_echo")
            and abs(int(r.get("start_ms") or 0) - start) <= window_ms
        ]
        if is_echo_text(text, remotes):
            out[i]["is_echo"] = True
            conf = out[i].get("confidence")
            out[i]["confidence"] = max(float(conf or 0), 0.8)
    return out
