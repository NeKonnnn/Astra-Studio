"""Лимиты цепочки агентов и шагов графа, разбор config.agent_ids."""

from __future__ import annotations

import os
from typing import Any, List, Mapping, Optional


MAX_CHAIN_AGENTS = 10
DEFAULT_GRAPH_STEPS = 50
MAX_CHAIN_AGENTS_CAP = 50
GRAPH_STEPS_CAP = 500


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return max(lo, min(default, hi))
    try:
        return max(lo, min(int(raw), hi))
    except ValueError:
        return max(lo, min(default, hi))


def get_max_chain_agents() -> int:
    """Максимум следующих агентов в цепочке: AGENT_CHAIN_MAX_AGENTS (ConfigMap)."""
    return _env_int(
        "AGENT_CHAIN_MAX_AGENTS", MAX_CHAIN_AGENTS, lo=1, hi=MAX_CHAIN_AGENTS_CAP
    )


def get_agent_graph_steps() -> int:
    """Лимит шагов графа (LLM + инструменты): AGENT_GRAPH_STEPS (ConfigMap)."""
    return _env_int("AGENT_GRAPH_STEPS", DEFAULT_GRAPH_STEPS, lo=1, hi=GRAPH_STEPS_CAP)


def _parse_positive_int(raw: Any) -> Optional[int]:
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        parsed = int(raw.strip())
        if parsed > 0:
            return parsed
    return None


def resolve_max_chain_agents(agent_profile: Optional[Mapping[str, Any]] = None) -> int:
    """Эффективный лимит цепочки: per-agent → платформенный (AGENT_CHAIN_MAX_AGENTS)."""
    platform = get_max_chain_agents()
    if not isinstance(agent_profile, Mapping):
        return platform
    parsed = _parse_positive_int(agent_profile.get("max_chain_agents"))
    if parsed is None:
        return platform
    return max(1, min(parsed, platform))


def parse_agent_ids(
    raw: Any,
    *,
    exclude_id: Optional[int] = None,
    max_agents: Optional[int] = None,
    agent_profile: Optional[Mapping[str, Any]] = None,
) -> List[int]:
    """Нормализовать `config.agent_ids`: уникальные int, без текущего, с per-agent лимитом."""
    if not isinstance(raw, list):
        return []
    if max_agents is not None:
        limit = max(1, int(max_agents))
    elif agent_profile is not None:
        limit = resolve_max_chain_agents(agent_profile)
    else:
        limit = get_max_chain_agents()
    out: List[int] = []
    seen = set()
    if exclude_id is not None:
        try:
            seen.add(int(exclude_id))
        except (TypeError, ValueError):
            pass
    for item in raw:
        try:
            aid = int(item)
        except (TypeError, ValueError):
            continue
        if aid <= 0 or aid in seen:
            continue
        seen.add(aid)
        out.append(aid)
        if len(out) >= limit:
            break
    return out
