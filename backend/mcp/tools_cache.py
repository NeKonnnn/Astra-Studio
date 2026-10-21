"""TTL cache для list_tools (B-44)."""

from __future__ import annotations

import hashlib
import time
from typing import Dict, List, Optional, Set, Tuple

from backend.mcp.types import McpToolInfo


class McpToolsCache:
    def __init__(self, ttl_seconds: int = 60):
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._store: Dict[str, Tuple[float, List[McpToolInfo]]] = {}
        self._index: Dict[str, Set[str]] = {}

    @staticmethod
    def _cache_key(server_id: str, headers_fingerprint: str) -> str:
        raw = f"{server_id}:{headers_fingerprint}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def get_tools_indexed(
        self, server_id: str, headers_fingerprint: str = ""
    ) -> Optional[List[McpToolInfo]]:
        key = self._cache_key(server_id, headers_fingerprint)
        entry = self._store.get(key)
        if not entry:
            return None
        ts, tools = entry
        if time.monotonic() - ts > self.ttl_seconds:
            self._store.pop(key, None)
            self._index.get(server_id, set()).discard(key)
            return None
        return list(tools)

    async def set_tools_indexed(
        self,
        server_id: str,
        tools: List[McpToolInfo],
        headers_fingerprint: str = "",
    ) -> None:
        key = self._cache_key(server_id, headers_fingerprint)
        self._store[key] = (time.monotonic(), list(tools))
        self._index.setdefault(server_id, set()).add(key)

    def invalidate(self, server_id: str) -> None:
        for key in list(self._index.pop(server_id, set())):
            self._store.pop(key, None)

    def count_cached_tools(self, server_id: str) -> int:
        """Число tools из свежего кэша (без MCP-подключения)."""
        keys = self._index.get(server_id, set())
        now = time.monotonic()
        best = 0
        for key in keys:
            entry = self._store.get(key)
            if not entry:
                continue
            ts, tools = entry
            if now - ts > self.ttl_seconds:
                continue
            best = max(best, len(tools))
        return best
