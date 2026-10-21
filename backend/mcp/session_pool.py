"""Warm HTTP session pool для streamable-http MCP (B-41)."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Optional

from backend.mcp.connection import McpServerSession
from backend.settings.config import McpPoolConfig
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress

log = get_logger(__name__)


@dataclass
class McpPoolMetrics:
    acquire_total: int = 0
    hit_total: int = 0
    miss_total: int = 0
    discard_total: int = 0
    acquire_ms_total: float = 0.0
    idle_sessions: int = 0

    def to_dict(self) -> dict:
        hit_rate = self.hit_total / self.acquire_total if self.acquire_total else 0.0
        avg_acquire_ms = (
            self.acquire_ms_total / self.acquire_total if self.acquire_total else 0.0
        )
        return {
            "acquire_total": self.acquire_total,
            "hit_total": self.hit_total,
            "miss_total": self.miss_total,
            "discard_total": self.discard_total,
            "hit_rate": round(hit_rate, 4),
            "avg_acquire_ms": round(avg_acquire_ms, 2),
            "idle_sessions": self.idle_sessions,
        }


@dataclass
class _PooledEntry:
    session: McpServerSession
    idle_since: float


class McpSessionPool:

    def __init__(
        self, pool_config: McpPoolConfig, connect_factory: Callable[..., asyncio.Future]
    ):
        self._config = pool_config
        self._connect_factory = connect_factory
        self._pools: Dict[str, Deque[_PooledEntry]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._evict_task: Optional[asyncio.Task] = None
        self._closed = False
        self.metrics = McpPoolMetrics()

    def start(self) -> None:
        if self._evict_task is None:
            self._evict_task = asyncio.create_task(self._evict_loop())

    async def shutdown(self) -> None:
        self._closed = True
        if self._evict_task:
            self._evict_task.cancel()
            try:
                await self._evict_task
            except asyncio.CancelledError:
                pass
            self._evict_task = None
        for entries in list(self._pools.values()):
            while entries:
                entry = entries.popleft()
                try:
                    await entry.session.close()
                except Exception:
                    log.exception("pool shutdown close error")
        self._pools.clear()
        self._update_idle_metric()

    def get_metrics(self) -> dict:
        self._update_idle_metric()
        per_server = {sid: len(entries) for sid, entries in self._pools.items()}
        base = self.metrics.to_dict()
        base["per_server_idle"] = per_server
        base["max_size"] = self._config.max_size
        base["idle_ttl_seconds"] = self._config.idle_ttl_seconds
        return base

    def _update_idle_metric(self) -> None:
        self.metrics.idle_sessions = sum((len(q) for q in self._pools.values()))

    def _lock_for(self, server_id: str) -> asyncio.Lock:
        if server_id not in self._locks:
            self._locks[server_id] = asyncio.Lock()
        return self._locks[server_id]

    async def acquire(self, server_id: str, **connect_kwargs) -> McpServerSession:
        started = time.perf_counter()
        self.metrics.acquire_total += 1
        if self._closed:
            session = await self._connect_factory(server_id, **connect_kwargs)
            self.metrics.miss_total += 1
            self.metrics.acquire_ms_total += (time.perf_counter() - started) * 1000.0
            return session
        lock = self._lock_for(server_id)
        from_pool = False
        async with lock:
            pool = self._pools.setdefault(server_id, deque())
            while pool:
                entry = pool.popleft()
                if time.monotonic() - entry.idle_since <= self._config.idle_ttl_seconds:
                    entry.session.from_pool = True
                    self.metrics.hit_total += 1
                    self.metrics.acquire_ms_total += (
                        time.perf_counter() - started
                    ) * 1000.0
                    self._update_idle_metric()
                    from_pool = True
                    log.debug(
                        "MCP pool acquire ok server=%s from_pool=%s elapsed_ms=%.1f",
                        server_id,
                        True,
                        (time.perf_counter() - started) * 1000.0,
                    )
                    return entry.session
                with logged_suppress(log):
                    await entry.session.close()
        try:
            session = await asyncio.wait_for(
                self._connect_factory(server_id, **connect_kwargs),
                timeout=self._config.acquire_timeout_seconds,
            )
        except asyncio.TimeoutError:
            log.debug(
                "MCP pool acquire timeout server=%s acquire_timeout=%ss",
                server_id,
                float(self._config.acquire_timeout_seconds),
            )
            session = await self._connect_factory(server_id, **connect_kwargs)
        self.metrics.miss_total += 1
        self.metrics.acquire_ms_total += (time.perf_counter() - started) * 1000.0
        self._update_idle_metric()
        if not from_pool:
            log.debug(
                "MCP pool acquire ok server=%s from_pool=%s elapsed_ms=%.1f",
                server_id,
                False,
                (time.perf_counter() - started) * 1000.0,
            )
        return session

    async def release(self, session: McpServerSession) -> None:
        if self._closed:
            await session.close()
            return
        lock = self._lock_for(session.server_id)
        async with lock:
            pool = self._pools.setdefault(session.server_id, deque())
            if len(pool) >= self._config.max_size:
                await session.close()
                self._update_idle_metric()
                return
            session.from_pool = True
            pool.append(_PooledEntry(session=session, idle_since=time.monotonic()))
            self._update_idle_metric()

    async def discard(self, session: McpServerSession) -> None:
        self.metrics.discard_total += 1
        try:
            await session.close()
        except Exception:
            log.exception("pool discard error")
        self._update_idle_metric()

    async def _evict_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(max(5, self._config.idle_ttl_seconds // 2))
                await self._evict_idle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.exception("Ошибка операции")
                log.debug("pool evict loop error: %s", exc)

    async def _evict_idle(self) -> None:
        now = time.monotonic()
        for server_id, entries in list(self._pools.items()):
            lock = self._lock_for(server_id)
            async with lock:
                kept: Deque[_PooledEntry] = deque()
                while entries:
                    entry = entries.popleft()
                    if now - entry.idle_since <= self._config.idle_ttl_seconds:
                        kept.append(entry)
                    else:
                        with logged_suppress(log):
                            await entry.session.close()
                if kept:
                    self._pools[server_id] = kept
                else:
                    self._pools.pop(server_id, None)
        self._update_idle_metric()
