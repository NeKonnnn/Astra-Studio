"""Последовательная цепочка агентов (LibreChat Mixture-of-Agents).

Текущий агент — первый узел. `config.agent_ids` — упорядоченный список
следующих агентов (без текущего). На запуске каждый следующий получает
выводы предыдущих как auxiliary-контекст, LLM следующего не выбирает.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import os

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


DEFAULT_CHAIN_PROMPT_TEMPLATE = (
    "Based on the following conversation and analysis from previous agents, "
    "please provide your insights:\n\n{convo}\n\n"
    "Please add your specific expertise and perspective to this discussion."
)


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


def format_run_buffer(
    user_message: str,
    steps: Sequence[Dict[str, Any]],
) -> str:
    """Буфер текущего хода: вопрос пользователя + ответы предыдущих агентов."""
    parts = [f"Human: {user_message.strip()}"]
    for step in steps:
        name = str(step.get("agent_name") or "Agent").strip() or "Agent"
        content = str(step.get("content") or "").strip()
        parts.append(f"AI ({name}): {content}")
    return "\n\n".join(parts)


def build_chain_user_message(
    user_message: str,
    steps: Sequence[Dict[str, Any]],
    prompt_template: str = DEFAULT_CHAIN_PROMPT_TEMPLATE,
) -> str:
    """Промпт следующего агента: Mixure-of-Agents с `{convo}`."""
    convo = format_run_buffer(user_message, steps)
    template = (
        prompt_template or DEFAULT_CHAIN_PROMPT_TEMPLATE
    ).strip() or DEFAULT_CHAIN_PROMPT_TEMPLATE
    if "{convo}" not in template:
        return f"{template}\n\n{convo}"
    return template.replace("{convo}", convo)


def format_visible_chain_content(
    steps: Sequence[Dict[str, Any]],
    *,
    hide_sequential_outputs: bool,
) -> str:
    """Текст, который видит пользователь и который уходит в историю."""
    if not steps:
        return ""
    if hide_sequential_outputs:
        return str(steps[-1].get("content") or "")
    blocks: List[str] = []
    for step in steps:
        name = str(step.get("agent_name") or "Агент").strip() or "Агент"
        content = str(step.get("content") or "").strip()
        blocks.append(f"**▸ {name}**\n\n{content}")
    return "\n\n".join(blocks).strip()


def chain_step_header(agent_name: str) -> str:
    name = (agent_name or "").strip() or "Агент"
    return f"**▸ {name}**\n\n"


async def resolve_agent_chain(
    primary_id: Any,
    primary_profile: Dict[str, Any],
    user_id: Optional[str],
    user: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """Загрузить профили цепочки: [primary, ...agent_ids]. Без транзитивного обхода чужих цепочек."""
    from backend.realtime.helpers import _resolve_agent_chat_params

    try:
        pid = int(primary_id) if primary_id is not None else None
    except (TypeError, ValueError):
        pid = None

    primary = dict(primary_profile) if isinstance(primary_profile, dict) else {}
    if pid is not None:
        primary["agent_id"] = pid
    chain: List[Dict[str, Any]] = [primary]
    if not pid:
        return chain

    next_ids = parse_agent_ids(
        primary.get("agent_ids"), exclude_id=pid, agent_profile=primary
    )
    if not next_ids:
        return chain

    for aid in next_ids:
        profile = await _resolve_agent_chat_params(aid, user_id, user=user)
        if not isinstance(profile, dict):
            continue
        if not (profile.get("name") or profile.get("system_prompt")):
            continue
        profile["agent_id"] = aid
        chain.append(profile)
    return chain


def prepare_step_socket_data(
    data: Optional[dict],
    profile: Dict[str, Any],
    *,
    is_first: bool,
) -> dict:
    """Копия payload чата под конкретного агента цепочки.

    MCP/плагины — из карточки шага. coding_mode и явный tool_ids UI — только у первого.
    """
    from backend.realtime.helpers import agent_mcp_tool_ids, agent_plugin_ids

    step_data = dict(data) if isinstance(data, dict) else {}
    if not is_first:
        step_data.pop("coding_mode", None)
        step_data.pop("plan_mode", None)
        step_data.pop("approved_plan", None)
        # #теги из чата отрабатывают только на первом шаге цепочки —
        # иначе одни и те же агенты вызвались бы на каждом hop.
        step_data.pop("tag_ids", None)
        if isinstance(step_data.get("message"), str):
            try:
                from backend.services.tag_mentions import strip_tag_mentions

                step_data["message"] = strip_tag_mentions(step_data["message"])
            except Exception:
                pass
        step_data["tool_ids"] = agent_mcp_tool_ids(profile)
        plugins = agent_plugin_ids(profile)
        if plugins:
            step_data["__plugin_ids__"] = plugins
        else:
            step_data.pop("__plugin_ids__", None)
        return step_data

    if not (step_data.get("tool_ids") or step_data.get("mcp_tool_ids")):
        mcp_ids = agent_mcp_tool_ids(profile)
        if mcp_ids:
            step_data["tool_ids"] = mcp_ids
    plugins = agent_plugin_ids(profile)
    if plugins:
        step_data["__plugin_ids__"] = plugins
    return step_data


def iter_chain_stream_prefixes(
    steps_so_far: Iterable[Dict[str, Any]],
    next_name: str,
    *,
    hide_sequential_outputs: bool,
) -> Tuple[str, str]:
    """Вернуть (stream_prefix, header) для текущего шага."""
    if hide_sequential_outputs:
        return "", ""
    header = chain_step_header(next_name)
    if not steps_so_far:
        return header, header
    visible = format_visible_chain_content(
        list(steps_so_far), hide_sequential_outputs=False
    )
    prefix = f"{visible}\n\n{header}" if visible else header
    return prefix, header
