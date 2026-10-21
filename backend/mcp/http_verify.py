"""TLS verify для httpx MCP-клиента (как LLM / llm_client)."""

from __future__ import annotations

import os
from typing import Union

from backend.settings.logging import get_logger

log = get_logger(__name__)

VerifySetting = Union[bool, str]

_CA_ENV_KEYS = (
    "TLS_CERT_PATH",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "LDAP_CA_CERT_PATH",
)


def resolve_httpx_verify(server_verify_ssl: bool = True) -> VerifySetting:
    """CA для httpx: TLS_CERT_PATH (как LLM) > server.verify_ssl=false > True."""
    if not server_verify_ssl:
        return False
    for env_name in _CA_ENV_KEYS:
        ca_path = (os.getenv(env_name) or "").strip()
        if ca_path and os.path.isfile(ca_path):
            return ca_path
        if ca_path and not os.path.isfile(ca_path):
            log.debug("%s=%s задан, но файл CA не найден", env_name, ca_path)
    return True


def verify_setting_label(verify: VerifySetting) -> str:
    if verify is False:
        return "False"
    if isinstance(verify, str):
        return verify
    return "True(system CAs)"
