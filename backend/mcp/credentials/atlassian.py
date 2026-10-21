"""Atlassian credential provider (service_account MVP + per_user B-12)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.mcp.credentials.base import AbstractCredentialProvider
from backend.mcp.credentials.store import get_credentials
from backend.mcp.types import McpCallContext
from backend.settings.config import McpServerConfig

# Поля payload для per-user PAT (B-12.4)
ATLASSIAN_CREDENTIAL_FIELD_NAMES: tuple[str, ...] = (
    "jira_url",
    "jira_pat",
    "confluence_url",
    "confluence_pat",
)

# Backward-compatible alias (routes/mcp_atlassian.py)
ATLASSIAN_CREDENTIAL_FIELDS = ATLASSIAN_CREDENTIAL_FIELD_NAMES


def validate_atlassian_payload(payload: Dict[str, Any]) -> List[str]:
    """Возвращает список ошибок валидации (пустой = OK)."""
    errors: List[str] = []
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    has_jira = bool(payload.get("jira_url") and payload.get("jira_pat"))
    has_confluence = bool(
        payload.get("confluence_url") and payload.get("confluence_pat")
    )
    if not has_jira and not has_confluence:
        errors.append(
            "At least one of (jira_url+jira_pat) or (confluence_url+confluence_pat) required"
        )
    return errors


class AtlassianCredentialProvider(AbstractCredentialProvider):
    provider_id = "atlassian"
    credential_field_names: tuple[str, ...] = ATLASSIAN_CREDENTIAL_FIELD_NAMES

    async def build_headers(
        self, server: McpServerConfig, context: McpCallContext
    ) -> Dict[str, str]:
        mode = str(server.auth_mode or "service_account").strip().lower()
        if mode == "service_account":
            # Secrets живут в Pod mcp-atlassian; backend не передаёт PAT.
            return {}

        creds = await get_credentials(context.user_id, server.id)
        if not creds:
            return {}

        headers: Dict[str, str] = {}
        jira_pat = creds.get("jira_pat")
        jira_url = creds.get("jira_url")
        confluence_pat = creds.get("confluence_pat")
        confluence_url = creds.get("confluence_url")

        if jira_url:
            headers["X-Atlassian-Jira-Url"] = str(jira_url)
        if jira_pat:
            headers["X-Atlassian-Jira-Personal-Token"] = str(jira_pat)
        if confluence_url:
            headers["X-Atlassian-Confluence-Url"] = str(confluence_url)
        if confluence_pat:
            headers["X-Atlassian-Confluence-Personal-Token"] = str(confluence_pat)

        # Primary PAT для Authorization, если задан jira_pat
        if jira_pat:
            headers["Authorization"] = f"Token {jira_pat}"
        elif confluence_pat:
            headers["Authorization"] = f"Token {confluence_pat}"

        return headers

    async def health_metadata(
        self, server: McpServerConfig, context: McpCallContext
    ) -> Dict[str, Any]:
        mode = str(server.auth_mode or "service_account").strip().lower()
        if mode == "service_account":
            return {
                "auth_mode": mode,
                "jira_configured": True,
                "confluence_configured": True,
            }
        creds = await get_credentials(context.user_id, server.id) or {}
        return {
            "auth_mode": mode,
            "jira_configured": bool(creds.get("jira_url") and creds.get("jira_pat")),
            "confluence_configured": bool(
                creds.get("confluence_url") and creds.get("confluence_pat")
            ),
        }

    def sanitize_payload_for_response(
        self, payload: Optional[Dict[str, Any]]
    ) -> Dict[str, bool]:
        if not payload:
            return {}
        return {
            field: bool(payload.get(field)) for field in ATLASSIAN_CREDENTIAL_FIELDS
        }
