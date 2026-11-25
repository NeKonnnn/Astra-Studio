"""In-memory rate limiting + дневные квоты.

Раньше в SecurityConfig были только флаги `rate_limiting_*`, но middleware
не существовал — лимиты никогда не применялись. Здесь они реально работают.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.settings.logging import get_logger

logger = get_logger(__name__)

_SKIP_PREFIXES = (
    "/metrics",
    "/health",
    "/api/system/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/socket.io",
)


class _SlidingWindowCounter:
    def __init__(self) -> None:
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, window_seconds: float, limit: int) -> Tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            q = self._events[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                return False, len(q)
            q.append(now)
            return True, len(q)


class _DailyQuotaCounter:
    def __init__(self) -> None:
        self._counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._day: Optional[str] = None
        self._lock = Lock()

    def _roll(self) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if self._day != day:
            self._counts.clear()
            self._day = day

    def consume(self, user_key: str, metric: str, limit: int) -> Tuple[bool, int]:
        with self._lock:
            self._roll()
            cur = self._counts[user_key][metric]
            if cur >= limit:
                return False, cur
            cur += 1
            self._counts[user_key][metric] = cur
            return True, cur

    def peek(self, user_key: str, metric: str) -> int:
        with self._lock:
            self._roll()
            return int(self._counts[user_key][metric])


_rpm_counter = _SlidingWindowCounter()
_quota_counter = _DailyQuotaCounter()


def consume_llm_quota(user_id: Optional[str]) -> None:
    """Списать дневную LLM-квоту; бросает PermissionError при превышении."""
    from backend.settings.config import get_settings

    settings = get_settings()
    quotas = getattr(settings, "quotas", None)
    if not quotas or not getattr(quotas, "enabled", False):
        return
    key = (user_id or "anonymous").strip() or "anonymous"
    ok, used = _quota_counter.consume(
        key, "llm", int(quotas.llm_requests_per_day)
    )
    if not ok:
        try:
            from backend.observability.metrics import record_quota_exceeded

            record_quota_exceeded("llm")
        except Exception:
            pass
        raise PermissionError(
            f"Дневная квота LLM исчерпана ({used}/{quotas.llm_requests_per_day})"
        )


def consume_rag_quota(user_id: Optional[str]) -> None:
    """Списать дневную RAG-квоту; бросает PermissionError при превышении."""
    from backend.settings.config import get_settings

    settings = get_settings()
    quotas = getattr(settings, "quotas", None)
    if not quotas or not getattr(quotas, "enabled", False):
        return
    key = (user_id or "anonymous").strip() or "anonymous"
    ok, used = _quota_counter.consume(
        key, "rag", int(quotas.rag_searches_per_day)
    )
    if not ok:
        try:
            from backend.observability.metrics import record_quota_exceeded

            record_quota_exceeded("rag")
        except Exception:
            pass
        raise PermissionError(
            f"Дневная квота RAG исчерпана ({used}/{quotas.rag_searches_per_day})"
        )


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Глобальный RPM-лимит по IP (когда security.rate_limiting_enabled)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path or "/"
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        try:
            from backend.settings.config import get_settings

            security = get_settings().security
        except Exception:
            return await call_next(request)

        if not getattr(security, "rate_limiting_enabled", False):
            return await call_next(request)

        limit = max(1, int(getattr(security, "rate_limiting_requests_per_minute", 60) or 60))
        key = f"ip:{_client_key(request)}"
        allowed, used = _rpm_counter.hit(key, 60.0, limit)
        if not allowed:
            try:
                from backend.observability.metrics import record_rate_limit_hit

                record_rate_limit_hit()
            except Exception:
                pass
            logger.warning("rate limit exceeded key=%s used=%s limit=%s path=%s", key, used, limit, path)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Слишком много запросов. Лимит: {limit}/мин",
                },
                headers={"Retry-After": "60"},
            )
        return await call_next(request)
