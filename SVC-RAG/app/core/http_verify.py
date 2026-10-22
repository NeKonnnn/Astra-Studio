"""TLS verify для httpx — тот же контракт, что у backend (llm_client / openai_compat).

Приоритет: TLS_CERT_PATH -> SSL_CERT_FILE -> REQUESTS_CA_BUNDLE -> True.
Достаточно одной переменной TLS_CERT_PATH с путём к CA bundle.
"""

from __future__ import annotations

import logging
import os
from typing import Union

logger = logging.getLogger(__name__)

VerifySetting = Union[bool, str]

_CA_ENV_KEYS = ("TLS_CERT_PATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


def resolve_httpx_verify() -> VerifySetting:
    """Параметр verify для httpx.AsyncClient."""
    for env_name in _CA_ENV_KEYS:
        ca_path = str(os.getenv(env_name, "") or "").strip()
        if not ca_path:
            continue
        if os.path.isfile(ca_path):
            # Размер бандла - это цена одного httpx-клиента
            try:
                ca_size = os.path.getsize(ca_path)
            except OSError:
                ca_size = -1
            logger.debug(
                "[SSL] CA bundle из %s: %s (%s байт)", env_name, ca_path, ca_size
            )
            return ca_path
        logger.warning(
            "[SSL] %s задан, но файл не найден: %s — используется verify=True",
            env_name,
            ca_path,
        )
    return True
