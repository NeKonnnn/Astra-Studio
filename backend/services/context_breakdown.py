"""Оценка сегментов контекста чата (системные промпты, RAG, MCP)."""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.app_state import rag_system_prompt
from backend.realtime.helpers import _resolve_agent_chat_params
from backend.rag_query.prompts import merge_strict_rag_system_prompt
from backend.services.user_llm_settings import (
    enrich_agent_profile_with_user_settings,
    get_user_model_settings,
    get_user_prompt_manager,
)

_MCP_TOKENS_CACHE: Dict[str, Tuple[int, float]] = {}
_MCP_CACHE_TTL_SEC = 120.0


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    base = math.ceil(len(text) / 4)
    special = len(re.findall(r"[^\w\sа-яё]", text, flags=re.IGNORECASE))
    newlines = text.count("\n")
    return base + math.ceil(special / 2) + math.ceil(newlines / 2)


async def _entity_context_size(
    user_id: Optional[str] = None, agent_profile: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """Размер контекста ТОЙ сущности, с которой разговаривают. None - не задан нигде

    Раньше бралась только персональная настройка пользователя, поэтому в чате с
    агентом, у которого свой 'context_size', счётчик и карточка агента
    показывали разные числа про одно и то же. Итог слияния
    (кластер -> пользователь -> агент) считает
    'enrich_agent_profile_with_user_settings', здесь он только читается
    """
    effective = (agent_profile or {}).get("effective_model_settings")
    try:
        if isinstance(effective, dict) and effective:
            return int(effective.get("context_size") or 0) or None
        user_ms = await get_user_model_settings(user_id)
        return int(user_ms.get("context_size") or 0) or None
    except (TypeError, ValueError):
        return None
    except Exception:
        return None


async def _estimate_mcp_tools_tokens(
    tool_ids: Optional[List[str]], user: Optional[dict]
) -> int:
    if not tool_ids or not user:
        return 0

    cache_key = "|".join(sorted(str(t) for t in tool_ids))
    cached = _MCP_TOKENS_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and (now - cached[1]) < _MCP_CACHE_TTL_SEC:
        return cached[0]

    try:
        from backend.mcp.chat_integration import build_mcp_context_from_user
        from backend.mcp.platform import get_mcp_platform
        from backend.mcp.prompt_fc_handler import _render_tools_prompt
        from backend.mcp.resolvers import parse_mcp_server_ids
    except ImportError:
        return 0

    platform = get_mcp_platform()
    if not platform.enabled or not platform.initialized:
        return 0

    mcp_ctx = build_mcp_context_from_user(user)
    server_ids = parse_mcp_server_ids(tool_ids)
    if not server_ids:
        return 0

    all_tools = []
    for sid in server_ids:
        try:
            specs = await platform.list_tools_for_server(sid, mcp_ctx)
            all_tools.extend(specs)
        except Exception:
            continue
    all_tools = platform.filter_tools_by_context(
        all_tools, mcp_ctx, enabled_server_ids=server_ids
    )
    if not all_tools:
        _MCP_TOKENS_CACHE[cache_key] = (0, now)
        return 0

    try:
        prompt_text = _render_tools_prompt(all_tools)
    except Exception:
        payload = json.dumps(
            [
                {
                    "name": t.qualified_name,
                    "description": t.description,
                    "parameters": t.parameters,
                }
                for t in all_tools
            ],
            ensure_ascii=False,
        )
        prompt_text = payload
    tokens = estimate_tokens(prompt_text)
    _MCP_TOKENS_CACHE[cache_key] = (tokens, now)
    return tokens


async def build_context_overhead(
    *,
    model_path: Optional[str] = None,
    project_instructions: Optional[str] = None,
    agent_id: Optional[int] = None,
    use_kb_rag: bool = False,
    tool_ids: Optional[List[str]] = None,
    user: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Сегменты «скрытого» контекста, как в realtime/handlers.py (без истории и черновика).
    """
    segments: List[Dict[str, Any]] = []
    user_id = (user or {}).get("user_id") if user else None

    agent_profile = await _resolve_agent_chat_params(agent_id, user_id, user=user)
    # Тот же итог настроек, что применяется в чате: кластер -> пользователь -> агент
    agent_profile = await enrich_agent_profile_with_user_settings(
        agent_profile, user_id
    )
    agent_prompt = (agent_profile.get("system_prompt") or "").strip()
    project_text = (project_instructions or "").strip()

    if project_text:
        segments.append(
            {
                "id": "project",
                "label": "Проект",
                "tokens": estimate_tokens(project_text),
                "active": True,
            }
        )

    if agent_prompt:
        segments.append(
            {
                "id": "agent",
                "label": "Агент",
                "tokens": estimate_tokens(agent_prompt),
                "active": True,
            }
        )

    context_eff = ""
    try:
        cpm = await get_user_prompt_manager(user_id)
        if model_path:
            context_eff = (cpm.get_effective_prompt(model_path) or "").strip()
        else:
            context_eff = (cpm.get_global_prompt() or "").strip()
    except Exception:
        context_eff = ""

    if context_eff:
        # В websocket-пути без агента context prompt может не уйти в API (registry).
        # Показываем как настроенный сегмент; active=False если перекрыт промптом
        # сущности — агента или проекта (см. resolve_chat_system_prompt).
        segments.append(
            {
                "id": "context_instructions",
                "label": "Системный промпт",
                "tokens": estimate_tokens(context_eff),
                "active": not (agent_prompt or project_text),
            }
        )

    if use_kb_rag:
        rag_block = merge_strict_rag_system_prompt("", rag_override=rag_system_prompt)
        segments.append(
            {
                "id": "rag_rules",
                "label": "RAG (правила)",
                "tokens": estimate_tokens(rag_block),
                "active": True,
            }
        )

    mcp_tokens = await _estimate_mcp_tools_tokens(tool_ids, user)
    if mcp_tokens > 0:
        segments.append(
            {
                "id": "mcp_tools",
                "label": "MCP (инструменты)",
                "tokens": mcp_tokens,
                "active": True,
            }
        )

    # Скиллы агента (тот же injection, что в handlers.apply_skills_to_chat).
    try:
        from backend.services.skills import (
            resolve_agent_skill_scope,
            resolve_and_build_skill_injection,
        )

        skills_enabled, agent_skill_ids = resolve_agent_skill_scope(agent_profile)
        if skills_enabled or agent_skill_ids:
            skill_block, _, _, _ = await resolve_and_build_skill_injection(
                user_skill_ids=[],
                agent_skill_ids=agent_skill_ids,
                user_id=user_id,
                is_admin=bool((user or {}).get("is_admin")),
                skills_enabled=skills_enabled,
                eager_agent_fallback=True,
            )
            skill_tokens = estimate_tokens(skill_block)
            if skill_tokens > 0 or skills_enabled:
                segments.append(
                    {
                        "id": "skills",
                        "label": "Скиллы",
                        "tokens": skill_tokens,
                        "active": skill_tokens > 0,
                    }
                )
    except Exception:
        pass

    # Artifacts-промпт, если включён у агента.
    try:
        from backend.prompts.artifacts import maybe_artifacts_prompt_for_agent

        artifacts_block = maybe_artifacts_prompt_for_agent(agent_profile) or ""
        art_tokens = estimate_tokens(artifacts_block)
        if art_tokens > 0 or bool(agent_profile.get("artifacts_enabled")):
            segments.append(
                {
                    "id": "artifacts",
                    "label": "Artifacts",
                    "tokens": art_tokens,
                    "active": art_tokens > 0,
                }
            )
    except Exception:
        pass

    overhead_active = sum(s["tokens"] for s in segments if s.get("active", True))
    overhead_all = sum(s["tokens"] for s in segments)

    entity_context_size = await _entity_context_size(user_id, agent_profile)

    return {
        "max_tokens": entity_context_size or 8192,
        "entity_context_size": entity_context_size,
        "segments": segments,
        "overhead_tokens_active": overhead_active,
        "overhead_tokens_configured": overhead_all,
        "skills_enabled": bool(
            (agent_profile or {}).get("skills_enabled")
            or (agent_profile or {}).get("skill_ids")
        ),
        "artifacts_enabled": bool((agent_profile or {}).get("artifacts_enabled")),
    }
