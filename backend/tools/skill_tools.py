"""Builtin tool view_skill — lazy-load skill content (Open WebUI parity)."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.tools import tool

from backend.settings.logging import get_logger
from backend.tools.tool_context import get_tool_context

logger = get_logger(__name__)


@tool
def view_skill(id: str) -> str:
    """
    Load the full instructions of a skill by its id from the available skills manifest.
    Use this when you need detailed instructions for a skill listed in <available_skills>.

    :param id: The id (slug) of the skill to load
    :return: JSON with name and content, or error
    """
    ctx = get_tool_context() or {}
    allowed: List[str] = list(ctx.get("__skill_ids__") or [])
    user: Dict[str, Any] = ctx.get("current_user") or {}
    user_id = user.get("user_id")
    is_admin = bool(user.get("is_admin"))

    skill_id = (id or "").strip().lower()
    if not skill_id:
        return json.dumps({"error": "Skill id is required"}, ensure_ascii=False)

    if allowed and skill_id not in {str(a).strip().lower() for a in allowed}:
        # навык не подключён к агенту — не отдаём, даже если он доступен пользователю
        return json.dumps({"error": f"Skill '{id}' not found"}, ensure_ascii=False)

    if not user_id:
        return json.dumps({"error": "User context not available"}, ensure_ascii=False)

    try:
        from backend.database.init_db import get_skill_repository
        import asyncio

        repo = get_skill_repository()

        async def _load():
            return await repo.resolve_skills_for_user(
                [skill_id], user_id, is_admin=is_admin
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                skills = pool.submit(asyncio.run, _load()).result()
        else:
            skills = asyncio.run(_load())

        if not skills:
            return json.dumps({"error": f"Skill '{id}' not found"}, ensure_ascii=False)
        skill = skills[0]
        if not skill.is_active:
            return json.dumps({"error": f"Skill '{id}' not found"}, ensure_ascii=False)
        return json.dumps(
            {"name": skill.name, "content": skill.content},
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("view_skill error")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def get_skill_tools(skill_ids: List[str] | None = None):
    """Return view_skill tool when there are agent-attached skills."""
    if skill_ids:
        return [view_skill]
    return []
