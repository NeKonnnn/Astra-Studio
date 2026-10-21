"""Generic MCP API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from backend.utils.http_errors import public_error_detail
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.auth.jwt_handler import get_current_user
from backend.auth.agent_platform_roles import user_can_use_mcp
from backend.mcp.access import has_mcp_server_access
from backend.mcp.context_headers import build_mcp_context_headers
from backend.mcp.exceptions import McpServerNotFoundError
from backend.mcp.file_download import fetch_mcp_server_file, guess_content_type
from backend.mcp.platform import get_mcp_platform
from backend.mcp.session_manager import build_server_auth_headers
from backend.mcp.types import McpCallContext
from backend.settings.config import get_settings
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["mcp"])


class McpVerifyBody(BaseModel):
    server_id: Optional[str] = None


class McpToolInvokeBody(BaseModel):
    arguments: Dict[str, Any] = Field(default_factory=dict)


class McpCredentialsBody(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)


def _validate_credentials_payload(server_id: str, payload: Dict[str, Any]) -> None:
    if server_id == "atlassian":
        from backend.mcp.credentials.atlassian import validate_atlassian_payload

        errors = validate_atlassian_payload(payload)
        if errors:
            raise HTTPException(status_code=400, detail="; ".join(errors))
        return
    if not payload:
        raise HTTPException(status_code=400, detail="Empty credentials payload")


def _require_admin(current_user: dict) -> None:
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")


def _require_mcp_feature(current_user: dict) -> None:
    if not user_can_use_mcp(current_user):
        raise HTTPException(
            status_code=403,
            detail="MCP недоступен для вашей роли",
        )


async def get_mcp_user(
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    _require_mcp_feature(current_user)
    return current_user


def _mcp_context(current_user: dict) -> McpCallContext:
    return McpCallContext(
        user_id=str(current_user.get("user_id") or current_user.get("username") or ""),
        username=str(current_user.get("username") or ""),
        is_admin=bool(current_user.get("is_admin")),
        groups=list(
            current_user.get("groups") or current_user.get("ldap_groups") or []
        ),
        ldap_groups=list(
            current_user.get("ldap_groups") or current_user.get("groups") or []
        ),
    )


def _platform_or_503():
    platform = get_mcp_platform()
    if not platform.initialized:
        raise HTTPException(status_code=503, detail="MCP platform not initialized")
    return platform


@router.get("/api/mcp/servers")
async def list_mcp_servers(current_user: Annotated[dict, Depends(get_mcp_user)]):
    platform = _platform_or_503()
    return {
        "success": True,
        "servers": platform.list_servers_public(_mcp_context(current_user)),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/api/mcp/status")
async def get_mcp_status(
    current_user: Annotated[dict, Depends(get_mcp_user)],
    servers: Optional[str] = Query(
        None, description="Список id через запятую — probe только выбранных серверов"
    ),
    fetch_tools: bool = Query(
        False, description="Подключаться и list_tools (admin/diagnostics)"
    ),
):
    platform = _platform_or_503()
    server_ids = [s.strip() for s in (servers or "").split(",") if s.strip()] or None
    health = await platform.health(
        _mcp_context(current_user),
        server_ids=server_ids,
        fetch_tools=fetch_tools,
    )
    return {
        "success": True,
        "mcp_status": health,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/api/mcp/servers/{server_id}/status")
async def get_mcp_server_status(
    server_id: str,
    current_user: Annotated[dict, Depends(get_mcp_user)],
    fetch_tools: bool = Query(False, description="Подключаться и list_tools"),
):
    platform = _platform_or_503()
    try:
        st = await platform.get_server_status(
            server_id, _mcp_context(current_user), fetch_tools=fetch_tools
        )
    except McpServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "success": True,
        "server": {
            "id": st.server_id,
            "display_name": st.display_name,
            "enabled": st.enabled,
            "connected": st.connected,
            "transport": st.transport,
            "tools": st.tools,
            "latency_ms": st.latency_ms,
            "error": st.error,
            "metadata": st.metadata,
        },
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/api/mcp/servers/{server_id}/health")
async def get_mcp_server_health(
    server_id: str, current_user: Annotated[dict, Depends(get_mcp_user)]
):
    _require_admin(current_user)
    platform = _platform_or_503()
    try:
        health = await platform.health(_mcp_context(current_user))
        for srv in health.get("servers", []):
            if srv.get("id") == server_id:
                return {
                    "success": True,
                    "server": srv,
                    "timestamp": datetime.now().isoformat(),
                }
        raise HTTPException(
            status_code=404, detail=f"MCP server '{server_id}' not found"
        )
    except McpServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/mcp/servers/{server_id}/tools")
async def get_mcp_server_tools(
    server_id: str, current_user: Annotated[dict, Depends(get_mcp_user)]
):
    platform = _platform_or_503()
    try:
        tools = await platform.list_tools_for_server(
            server_id, _mcp_context(current_user)
        )
        return {
            "success": True,
            "server_id": server_id,
            "tools": [
                {
                    "server_id": server_id,
                    "name": t.name,
                    "qualified_name": t.qualified_name,
                    "description": t.description,
                    "parameters": t.parameters,
                }
                for t in tools
            ],
            "count": len(tools),
            "timestamp": datetime.now().isoformat(),
        }
    except McpServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("MCP list tools failed server=%s", server_id)
        raise HTTPException(status_code=502, detail=public_error_detail(exc)) from exc


@router.get("/api/mcp/tools")
async def get_all_mcp_tools(current_user: Annotated[dict, Depends(get_mcp_user)]):
    platform = _platform_or_503()
    tools = await platform.list_all_tools(_mcp_context(current_user))
    return {
        "success": True,
        "tools": [
            {
                "server_id": t.server_id,
                "name": t.name,
                "qualified_name": t.qualified_name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ],
        "count": len(tools),
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/api/mcp/servers/{server_id}/tools/{tool_name}")
async def invoke_mcp_tool_debug(
    server_id: str,
    tool_name: str,
    body: McpToolInvokeBody,
    current_user: Annotated[dict, Depends(get_mcp_user)],
):
    _require_admin(current_user)
    platform = _platform_or_503()
    try:
        result = await platform.call_tool_debug(
            server_id, tool_name, body.arguments, _mcp_context(current_user)
        )
        return {
            "success": True,
            "server_id": server_id,
            "tool_name": tool_name,
            "result": result,
            "timestamp": datetime.now().isoformat(),
        }
    except McpServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "MCP tool invoke failed server=%s tool=%s", server_id, tool_name
        )
        raise HTTPException(status_code=502, detail=public_error_detail(exc)) from exc


@router.post("/api/mcp/servers/{server_id}/verify")
async def verify_mcp_server(
    server_id: str, current_user: Annotated[dict, Depends(get_mcp_user)]
):
    _require_admin(current_user)
    platform = _platform_or_503()
    result = await platform.verify_server(server_id, _mcp_context(current_user))
    return {
        "success": result.success,
        "verify": {
            "server_id": result.server_id,
            "success": result.success,
            "tools_count": result.tools_count,
            "latency_ms": result.latency_ms,
            "error": result.error,
            "tools": result.tools[:50],
        },
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/api/mcp/servers/verify")
async def verify_all_mcp_servers(
    current_user: Annotated[dict, Depends(get_mcp_user)],
):
    _require_admin(current_user)
    platform = _platform_or_503()
    ctx = _mcp_context(current_user)
    results: List[dict] = []
    for srv in platform.list_servers_public(ctx):
        if not srv.get("enabled"):
            continue
        vr = await platform.verify_server(srv["id"], ctx)
        results.append(
            {
                "server_id": vr.server_id,
                "success": vr.success,
                "tools_count": vr.tools_count,
                "latency_ms": vr.latency_ms,
                "error": vr.error,
            }
        )
    return {
        "success": all(r["success"] for r in results) if results else True,
        "results": results,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/api/mcp/servers/{server_id}/credentials")
async def get_mcp_server_credentials(
    server_id: str, current_user: Annotated[dict, Depends(get_mcp_user)]
):
    _platform_or_503()
    server = get_settings().get_mcp_server_config(server_id)
    if not server:
        raise HTTPException(
            status_code=404, detail=f"MCP server '{server_id}' not found"
        )
    from backend.mcp.credentials.store import credentials_metadata

    user_id = str(current_user.get("user_id") or current_user.get("username") or "")
    meta = await credentials_metadata(user_id, server_id)
    return {
        "success": True,
        "server_id": server_id,
        "auth_mode": server.auth_mode,
        **meta,
        "timestamp": datetime.now().isoformat(),
    }


@router.put("/api/mcp/servers/{server_id}/credentials")
async def put_mcp_server_credentials(
    server_id: str,
    body: McpCredentialsBody,
    current_user: Annotated[dict, Depends(get_mcp_user)],
):
    _platform_or_503()
    server = get_settings().get_mcp_server_config(server_id)
    if not server:
        raise HTTPException(
            status_code=404, detail=f"MCP server '{server_id}' not found"
        )
    if str(server.auth_mode or "").lower() != "per_user":
        raise HTTPException(
            status_code=400,
            detail=f"Server '{server_id}' uses auth_mode={server.auth_mode}; per-user credentials not applicable",
        )
    _validate_credentials_payload(server_id, body.payload)
    from backend.mcp.credentials.store import save_credentials

    user_id = str(current_user.get("user_id") or current_user.get("username") or "")
    ok = await save_credentials(user_id, server_id, body.payload)
    if not ok:
        raise HTTPException(
            status_code=503,
            detail="Credentials storage unavailable (MongoDB or MCP_CREDENTIALS_ENCRYPTION_KEY)",
        )
    return {
        "success": True,
        "server_id": server_id,
        "message": "Credentials saved",
        "timestamp": datetime.now().isoformat(),
    }


@router.delete("/api/mcp/servers/{server_id}/credentials")
async def delete_mcp_server_credentials(
    server_id: str, current_user: Annotated[dict, Depends(get_mcp_user)]
):
    _platform_or_503()
    if not get_settings().get_mcp_server_config(server_id):
        raise HTTPException(
            status_code=404, detail=f"MCP server '{server_id}' not found"
        )
    from backend.mcp.credentials.store import delete_credentials

    user_id = str(current_user.get("user_id") or current_user.get("username") or "")
    deleted = await delete_credentials(user_id, server_id)
    return {
        "success": True,
        "server_id": server_id,
        "deleted": deleted,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/api/mcp/servers/{server_id}/resources")
async def list_mcp_server_resources(
    server_id: str,
    current_user: Annotated[dict, Depends(get_mcp_user)],
    cursor: Optional[str] = None,
):
    platform = _platform_or_503()
    try:
        resources = await platform.list_resources_for_server(
            server_id, _mcp_context(current_user), cursor=cursor
        )
        return {
            "success": True,
            "server_id": server_id,
            "resources": resources,
            "count": len(resources),
            "timestamp": datetime.now().isoformat(),
        }
    except McpServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("MCP list resources failed server=%s", server_id)
        raise HTTPException(status_code=502, detail=public_error_detail(exc)) from exc


@router.get("/api/mcp/servers/{server_id}/resources/read")
async def read_mcp_server_resource(
    server_id: str,
    uri: Annotated[str, Query(..., description="MCP resource URI")],
    current_user: Annotated[dict, Depends(get_mcp_user)],
):
    platform = _platform_or_503()
    try:
        payload = await platform.read_resource_for_server(
            server_id, uri, _mcp_context(current_user)
        )
        return {
            "success": True,
            "server_id": server_id,
            "resource": payload,
            "timestamp": datetime.now().isoformat(),
        }
    except McpServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("MCP read resource failed server=%s uri=%s", server_id, uri)
        raise HTTPException(status_code=502, detail=public_error_detail(exc)) from exc


@router.get("/api/mcp/servers/{server_id}/download")
async def download_mcp_server_file(
    server_id: str,
    current_user: Annotated[dict, Depends(get_mcp_user)],
    filename: Annotated[
        str, Query(..., description="Имя файла, например presentation.pptx")
    ],
    storage_key: Annotated[
        Optional[str],
        Query(description="Ключ объекта в S3 (если известен из результата MCP tool)"),
    ] = None,
):
    """Proxy скачивания файла с MCP-сервера (S3/MinIO или HTTP fallback)."""
    safe_name = (filename or "").strip()
    if not safe_name or ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    settings = get_settings()
    server = settings.get_mcp_server_config(server_id)
    if not server or not server.enabled:
        raise HTTPException(
            status_code=404, detail=f"MCP server '{server_id}' not found"
        )

    ctx = _mcp_context(current_user)
    if not has_mcp_server_access(server, ctx):
        raise HTTPException(status_code=403, detail="Access denied")

    base = str(server.base_url or "").strip() or settings.resolve_mcp_server_base_url(
        server_id
    )
    headers = {**build_server_auth_headers(server), **build_mcp_context_headers(ctx)}
    safe_storage_key = (storage_key or "").strip().lstrip("/")
    if ".." in safe_storage_key:
        raise HTTPException(status_code=400, detail="Invalid storage_key")

    try:
        data, content_type = await fetch_mcp_server_file(
            server=server,
            filename=safe_name,
            base_url=base,
            http_headers=headers,
            storage_key=safe_storage_key or None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("MCP download failed server=%s file=%s", server_id, safe_name)
        raise HTTPException(status_code=502, detail=public_error_detail(exc)) from exc

    media_type = content_type or guess_content_type(safe_name)
    return StreamingResponse(
        iter([data]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/api/mcp/pool/metrics")
async def get_mcp_pool_metrics(
    current_user: Annotated[dict, Depends(get_mcp_user)],
):
    _require_admin(current_user)
    platform = _platform_or_503()
    return {
        "success": True,
        "pool": platform.get_pool_metrics(),
        "timestamp": datetime.now().isoformat(),
    }
