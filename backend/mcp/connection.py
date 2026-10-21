"""Runtime wrapper: transport + ClientSession для одного MCP-сервера."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.mcp.client import McpClient
from backend.mcp.types import McpCallContext
from backend.settings.config import McpServerConfig


@dataclass
class McpServerSession:
    server_id: str
    config: McpServerConfig
    client: McpClient
    context: McpCallContext
    created_at: float = field(default_factory=time.monotonic)
    from_pool: bool = False
    ephemeral: bool = False
    headers_fingerprint: str = ""

    async def list_tools(self) -> List[Dict[str, Any]]:
        return await self.client.list_tool_specs()

    async def call_tool(self, name: str, arguments: dict) -> Any:
        return await self.client.call_tool(name, arguments)

    async def list_resources(
        self, cursor: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return await self.client.list_resources(cursor=cursor)

    async def read_resource(self, uri: str) -> Dict[str, Any]:
        return await self.client.read_resource(uri)

    async def close(self) -> None:
        await self.client.disconnect()
