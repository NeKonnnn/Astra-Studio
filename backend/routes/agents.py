"""
routes/agents.py — совместимость MCP status (оркестратор удалён).
Пользовательские агенты: /api/agents (api_agents.py).
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from backend.utils.http_errors import public_error_detail

from backend.auth.jwt_handler import get_current_user
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agents"])


@router.get("/mcp/status")
async def get_mcp_status(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Статус MCP-платформы (используется фронтом)."""
    try:
        from backend.mcp.platform import get_mcp_platform
        from backend.mcp.types import McpCallContext

        platform = get_mcp_platform()
        if not platform.initialized:
            return {
                "mcp_status": {
                    "initialized": False,
                    "servers_connected": 0,
                    "total_servers": 0,
                    "tools": 0,
                    "message": "MCP platform not initialized",
                },
                "success": True,
                "timestamp": datetime.now().isoformat(),
            }
        ctx = McpCallContext(user_id="system", username="system", is_admin=True)
        health = await platform.health(ctx)
        return {
            "mcp_status": {
                "initialized": health.get("initialized", False),
                "enabled": health.get("enabled", False),
                "servers_connected": health.get("servers_connected", 0),
                "total_servers": health.get("servers_total", 0),
                "tools": health.get("tools_total", 0),
                "servers": health.get("servers", []),
                "pool": health.get("pool", {}),
                "message": health.get("message"),
            },
            "success": True,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
