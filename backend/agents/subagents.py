"""Субагенты: изолированные дочерние запуски через tool `subagent`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence
import os

from backend.agents.chain import _parse_positive_int, parse_agent_ids
from backend.agents.config import resolve_recursion_limit
from backend.mcp.types import McpToolInfo
from backend.settings.logging import get_logger

log = get_logger(__name__)

NATIVE_SERVER_ID = "__astra_native__"
SUBAGENT_TOOL_NAME = "subagent"
SELF_SUBAGENT_TYPE = "self"
MAX_SUBAGENTS = 10
MAX_SUBAGENTS_CAP = 50
MAX_SUBAGENT_DEPTH = 3


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return max(lo, min(default, hi))
    try:
        return max(lo, min(int(raw), hi))
    except ValueError:
        return max(lo, min(default, hi))


def get_max_subagents() -> int:
    """Максимум субагентов: AGENT_SUBAGENTS_MAX (ConfigMap)."""
    return _env_int("AGENT_SUBAGENTS_MAX", MAX_SUBAGENTS, lo=1, hi=MAX_SUBAGENTS_CAP)


def resolve_max_subagents(agent_profile: Optional[Mapping[str, Any]] = None) -> int:
    """Эффективный лимит субагентов: per-agent → платформенный (AGENT_SUBAGENTS_MAX)."""
    platform = get_max_subagents()
    if not isinstance(agent_profile, Mapping):
        return platform
    parsed = _parse_positive_int(agent_profile.get("max_subagents"))
    if parsed is None:
        return platform
    return max(1, min(parsed, platform))


SubagentExecutor = Callable[..., Awaitable[str]]


@dataclass
class AgentSubagentsConfig:
    enabled: bool = False
    allow_self: bool = True
    agent_ids: List[int] = field(default_factory=list)
    # Обязательные по тегам: агенты с любым из этих тегов вызываются кодом
    # до ответа родителя (required_subagents.py). required_only - родитель
    # сверх них никого не зовёт.
    required_tag_ids: List[int] = field(default_factory=list)
    required_only: bool = False


@dataclass
class SubagentRunContext:
    parent_agent_id: Optional[int]
    parent_profile: Dict[str, Any]
    user: Optional[dict]
    user_id: Optional[str]
    depth: int = 0
    remaining_steps: int = 50
    executor: Optional[SubagentExecutor] = None
    # Вложения сообщения пользователя - для
    # плагинов ребёнка. Явно, а не через tool_context: у того глобальный
    # fallback, и файл мог бы прийти из чужого запроса.
    inline_attachments: Optional[List[Any]] = None


def parse_subagents_config(
    raw: Any,
    *,
    exclude_id: Optional[int] = None,
    agent_profile: Optional[Mapping[str, Any]] = None,
) -> AgentSubagentsConfig:
    if not isinstance(raw, dict):
        return AgentSubagentsConfig()
    enabled = raw.get("enabled") is True
    allow_self = raw.get("allow_self")
    if allow_self is None:
        allow_self = raw.get("allowSelf")
    allow_self_bool = allow_self is not False
    max_sub = resolve_max_subagents(agent_profile)
    agent_ids = parse_agent_ids(
        raw.get("agent_ids"), exclude_id=exclude_id, max_agents=max_sub
    )
    required_tag_ids: List[int] = []
    for item in raw.get("required_tag_ids") or []:
        try:
            tid = int(item)
        except (TypeError, ValueError):
            continue
        if tid > 0 and tid not in required_tag_ids:
            required_tag_ids.append(tid)
    required_only = raw.get("required_only") is True
    return AgentSubagentsConfig(
        enabled=enabled,
        allow_self=allow_self_bool,
        agent_ids=agent_ids,
        required_tag_ids=required_tag_ids,
        required_only=required_only,
    )


def subagent_type_for_agent_id(agent_id: int) -> str:
    return f"agent_{int(agent_id)}"


def resolve_subagent_target(
    subagent_type: str,
    *,
    parent_agent_id: Optional[int],
    config: AgentSubagentsConfig,
) -> Optional[int]:
    st = (subagent_type or "").strip()
    if st == SELF_SUBAGENT_TYPE:
        if not config.allow_self or parent_agent_id is None:
            return None
        return int(parent_agent_id)
    if st.startswith("agent_"):
        try:
            aid = int(st[6:])
        except (TypeError, ValueError):
            return None
        if aid in config.agent_ids:
            return aid
    return None


def build_subagent_tools(
    config: AgentSubagentsConfig,
    *,
    parent_agent_id: Optional[int],
    agent_names: Mapping[int, str],
) -> List[McpToolInfo]:
    if not config.enabled:
        return []
    enum_values: List[str] = []
    descriptions: List[str] = []
    if config.allow_self and parent_agent_id is not None:
        enum_values.append(SELF_SUBAGENT_TYPE)
        name = agent_names.get(parent_agent_id) or "self"
        descriptions.append(
            f"- {SELF_SUBAGENT_TYPE}: spawn {name} in an isolated context"
        )
    for aid in config.agent_ids:
        st = subagent_type_for_agent_id(aid)
        enum_values.append(st)
        name = agent_names.get(aid) or f"Agent {aid}"
        descriptions.append(f"- {st}: delegate to {name}")
    if not enum_values:
        return []
    desc = (
        "Spawn an isolated subagent to handle a focused subtask. "
        "Verbose tool output stays in the child context; only a summary returns.\n"
        "If the result contains :::artifact blocks (diagrams, charts, presentations, "
        "HTML) or download links, reproduce them in your final answer VERBATIM, "
        "unchanged - they are rendered for the user; do not retell them as text.\n"
        + "\n".join(descriptions)
    )
    return [
        McpToolInfo(
            server_id=NATIVE_SERVER_ID,
            name=SUBAGENT_TOOL_NAME,
            qualified_name=SUBAGENT_TOOL_NAME,
            description=desc,
            parameters={
                "type": "object",
                "properties": {
                    "subagent_type": {
                        "type": "string",
                        "enum": enum_values,
                        "description": "Which subagent to spawn",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Focused task for the subagent",
                    },
                },
                "required": ["subagent_type", "prompt"],
            },
        )
    ]


async def execute_subagent_tool(
    arguments: Dict[str, Any],
    *,
    ctx: SubagentRunContext,
    config: AgentSubagentsConfig,
) -> str:
    if ctx.depth >= MAX_SUBAGENT_DEPTH:
        return f"Subagent depth limit ({MAX_SUBAGENT_DEPTH}) reached."
    if ctx.remaining_steps <= 0:
        return "Subagent step budget exhausted."
    subagent_type = str(arguments.get("subagent_type") or "").strip()
    prompt = str(arguments.get("prompt") or arguments.get("task") or "").strip()
    if not prompt:
        return "Subagent prompt is required."
    target_id = resolve_subagent_target(
        subagent_type,
        parent_agent_id=ctx.parent_agent_id,
        config=config,
    )
    if target_id is None:
        return f"Unknown or disallowed subagent_type: {subagent_type!r}"
    if ctx.executor is None:
        return "Subagent executor is not configured."
    # Через tool subagent ходят только вызовы по решению модели: обязательные
    # (по тегам) идут мимо него, в required_subagents.py.
    log.debug(
        "[subagent] ВЫЗОВ ПО ВЫБОРУ РОДИТЕЛЯ: %s → agent_id=%s глубина=%s prompt=«%s»",
        subagent_type,
        target_id,
        ctx.depth + 1,
        prompt[:120].replace("\n", " "),
    )
    try:
        return await ctx.executor(
            target_agent_id=target_id,
            prompt=prompt,
            parent_profile=ctx.parent_profile,
            user=ctx.user,
            user_id=ctx.user_id,
            depth=ctx.depth + 1,
            remaining_steps=ctx.remaining_steps,
            inline_attachments=ctx.inline_attachments,
        )
    except Exception as exc:
        # Строка «Subagent error» уезжала в UI с success=true. Исключение
        # ловит agent_loop: он проставит failure и напечатает traceback,
        # здесь - только адрес сбоя.
        log.error("Subagent execution failed target=%s: %s", target_id, exc)
        raise


async def execute_native_tool(
    tool_info: McpToolInfo,
    arguments: Dict[str, Any],
    *,
    subagent_ctx: Optional[SubagentRunContext] = None,
    subagent_config: Optional[AgentSubagentsConfig] = None,
) -> str:
    if tool_info.server_id != NATIVE_SERVER_ID:
        return f"Unknown native tool server: {tool_info.server_id}"
    if tool_info.name == SUBAGENT_TOOL_NAME:
        if subagent_ctx is None or subagent_config is None:
            return "Subagents are not enabled for this run."
        args = arguments if isinstance(arguments, dict) else {}
        return await execute_subagent_tool(
            args, ctx=subagent_ctx, config=subagent_config
        )
    return f"Unknown native tool: {tool_info.name}"


async def load_subagent_agent_names(
    agent_ids: Sequence[int],
    *,
    user_id: Optional[str],
) -> Dict[int, str]:
    if not agent_ids:
        return {}
    try:
        from backend.database.init_db import get_agent_repository

        repo = get_agent_repository()
        if repo is None:
            return {}
        out: Dict[int, str] = {}
        for aid in agent_ids:
            ag = await repo.get_agent(int(aid), user_id)
            if ag and ag.name:
                label = str(ag.name).strip()
                # Родитель выбирает ребёнка по этой подписи - и только по ней.
                # Одно имя не говорит, у кого таблица, а кто строит графики;
                # без описания модель либо угадывает, либо зовёт всех подряд.
                desc = " ".join(str(getattr(ag, "description", "") or "").split())
                if desc:
                    label = f"{label} - {desc[:200]}"
                out[int(aid)] = label
        return out
    except Exception:
        log.exception("load_subagent_agent_names")
        return {}


def subagents_from_profile(profile: Mapping[str, Any]) -> AgentSubagentsConfig:
    parent_id = profile.get("agent_id")
    exclude = int(parent_id) if parent_id is not None else None
    return parse_subagents_config(
        profile.get("subagents"),
        exclude_id=exclude,
        agent_profile=profile,
    )


def profile_recursion_limit(profile: Mapping[str, Any]) -> int:
    return resolve_recursion_limit(profile)
