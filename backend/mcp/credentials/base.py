"""Credential providers для MCP-серверов."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from backend.mcp.types import McpCallContext
from backend.settings.config import McpServerConfig
from backend.settings.logging import get_logger

logger = get_logger(__name__)


class AbstractCredentialProvider(ABC):
    provider_id: str

    @abstractmethod
    async def build_headers(
        self,
        server: McpServerConfig,
        context: McpCallContext,
    ) -> Dict[str, str]: ...

    async def health_metadata(
        self,
        server: McpServerConfig,
        context: McpCallContext,
    ) -> Dict[str, Any]:
        return {}


class NullCredentialProvider(AbstractCredentialProvider):
    provider_id = "null"

    async def build_headers(
        self, server: McpServerConfig, context: McpCallContext
    ) -> Dict[str, str]:
        return {}


def get_credential_provider(provider_id: Optional[str]) -> AbstractCredentialProvider:
    """
    Plug-in registry для per-server credentials.

    Новый MCP-сервер: добавьте ``backend/mcp/credentials/{name}.py`` и ветку ниже.
    Если per-user auth не нужен — ``credential_provider: null`` в config.
    """
    if not provider_id:
        return NullCredentialProvider()
    pid = str(provider_id).strip().lower()
    if pid == "atlassian":
        from backend.mcp.credentials.atlassian import AtlassianCredentialProvider

        return AtlassianCredentialProvider()
    logger.warning(
        "Unknown MCP credential_provider=%r — using NullCredentialProvider", provider_id
    )
    return NullCredentialProvider()
