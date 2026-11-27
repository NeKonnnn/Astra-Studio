"""Лимиты шагов агентного графа (recursion_limit)."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from backend.agents.chain.settings import get_agent_graph_steps

DEFAULT_RECURSION_LIMIT = 50
MAX_RECURSION_LIMIT_CAP = 500


def resolve_recursion_limit(
    agent_profile: Optional[Mapping[str, Any]] = None,
    *,
    global_default: Optional[int] = None,
    max_cap: Optional[int] = None,
) -> int:
    """Эффективный лимит шагов: per-agent → global (AGENT_GRAPH_STEPS) → 50."""
    limit = global_default if global_default is not None else get_agent_graph_steps()
    if not isinstance(limit, int) or limit <= 0:
        limit = DEFAULT_RECURSION_LIMIT

    raw = None
    if isinstance(agent_profile, Mapping):
        raw = agent_profile.get("recursion_limit")
    if isinstance(raw, int) and raw > 0:
        limit = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        parsed = int(raw.strip())
        if parsed > 0:
            limit = parsed

    cap = max_cap if max_cap is not None else MAX_RECURSION_LIMIT_CAP
    if isinstance(cap, int) and cap > 0 and limit > cap:
        limit = cap
    return limit
