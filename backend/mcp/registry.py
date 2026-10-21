"""Конфигурационный реестр MCP-серверов (без persistent connections)."""

from __future__ import annotations

from typing import Dict, List, Optional

from backend.settings.config import McpPlatformConfig, McpServerConfig, Settings
from backend.settings.logging import get_logger

log = get_logger(__name__)


class McpRegistry:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._config: McpPlatformConfig = settings.mcp
        self._servers: Dict[str, McpServerConfig] = {}

    def initialize(self) -> None:
        self._config = self._settings.mcp
        self._servers = {}
        for srv in self._config.servers:
            if srv.id:
                self._servers[srv.id] = srv
        log.info(
            "MCP registry loaded: enabled=%s servers=%s",
            self._config.enabled,
            list(self._servers.keys()),
        )

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    @property
    def config(self) -> McpPlatformConfig:
        return self._config

    def list_servers(self) -> List[McpServerConfig]:
        return list(self._servers.values())

    def list_enabled_servers(self) -> List[McpServerConfig]:
        return [s for s in self._servers.values() if s.enabled]

    def get_server(self, server_id: str) -> Optional[McpServerConfig]:
        return self._servers.get(server_id)

    def require_server(self, server_id: str) -> McpServerConfig:
        srv = self.get_server(server_id)
        if not srv:
            from backend.mcp.exceptions import McpServerNotFoundError

            msg = f"MCP server '{server_id}' not found"
            raise McpServerNotFoundError(msg)
        return srv
