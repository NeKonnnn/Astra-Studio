"""Prometheus-метрики AstraStudio (HTTP / LLM / RAG / очереди / квоты)."""

from __future__ import annotations

import time
from typing import Optional

from backend.settings.logging import get_logger

logger = get_logger(__name__)

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _PROM_AVAILABLE = True
except ImportError:  # pragma: no cover
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    CollectorRegistry = None  # type: ignore
    Counter = Gauge = Histogram = None  # type: ignore
    generate_latest = None  # type: ignore
    _PROM_AVAILABLE = False
    logger.warning(
        "prometheus_client не установлен — /metrics будет отдавать заглушку. "
        "pip install prometheus-client"
    )

REGISTRY = CollectorRegistry() if _PROM_AVAILABLE else None

if _PROM_AVAILABLE:
    HTTP_REQUESTS = Counter(
        "astra_http_requests_total",
        "HTTP-запросы backend",
        ["method", "route", "status"],
        registry=REGISTRY,
    )
    HTTP_LATENCY = Histogram(
        "astra_http_request_duration_seconds",
        "Латентность HTTP backend",
        ["method", "route"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
        registry=REGISTRY,
    )
    LLM_REQUESTS = Counter(
        "astra_llm_requests_total",
        "Вызовы LLM (chat/generate)",
        ["status"],
        registry=REGISTRY,
    )
    LLM_LATENCY = Histogram(
        "astra_llm_request_duration_seconds",
        "Латентность LLM",
        buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
        registry=REGISTRY,
    )
    RAG_SEARCHES = Counter(
        "astra_rag_searches_total",
        "Поиски RAG",
        ["scope", "status"],
        registry=REGISTRY,
    )
    RAG_LATENCY = Histogram(
        "astra_rag_search_duration_seconds",
        "Латентность RAG search",
        ["scope"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
        registry=REGISTRY,
    )
    ACTIVE_SESSIONS = Gauge(
        "astra_active_sessions",
        "Активные JWT-сессии в памяти процесса",
        registry=REGISTRY,
    )
    QUEUE_DEPTH = Gauge(
        "astra_queue_depth",
        "Глубина внутренних очередей",
        ["queue"],
        registry=REGISTRY,
    )
    RATE_LIMIT_HITS = Counter(
        "astra_rate_limit_hits_total",
        "Срабатывания rate limit",
        registry=REGISTRY,
    )
    QUOTA_EXCEEDED = Counter(
        "astra_quota_exceeded_total",
        "Превышения дневных квот",
        ["quota"],
        registry=REGISTRY,
    )
else:  # pragma: no cover
    HTTP_REQUESTS = HTTP_LATENCY = None
    LLM_REQUESTS = LLM_LATENCY = None
    RAG_SEARCHES = RAG_LATENCY = None
    ACTIVE_SESSIONS = QUEUE_DEPTH = None
    RATE_LIMIT_HITS = QUOTA_EXCEEDED = None


def _route_label(path: str) -> str:
    if not path:
        return "/"
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    # /api/admin/users/foo → /api/admin/users
    if parts[0] == "api" and len(parts) >= 2:
        return "/" + "/".join(parts[:3] if len(parts) >= 3 else parts[:2])
    return "/" + parts[0]


def observe_http(method: str, path: str, status: int, duration_s: float) -> None:
    if not _PROM_AVAILABLE:
        return
    route = _route_label(path)
    HTTP_REQUESTS.labels(method=method.upper(), route=route, status=str(status)).inc()
    HTTP_LATENCY.labels(method=method.upper(), route=route).observe(duration_s)


def observe_llm(status: str, duration_s: float) -> None:
    if not _PROM_AVAILABLE:
        return
    LLM_REQUESTS.labels(status=status).inc()
    LLM_LATENCY.observe(duration_s)


def observe_rag(scope: str, status: str, duration_s: float) -> None:
    if not _PROM_AVAILABLE:
        return
    RAG_SEARCHES.labels(scope=scope or "default", status=status).inc()
    RAG_LATENCY.labels(scope=scope or "default").observe(duration_s)


def set_active_sessions(count: int) -> None:
    if not _PROM_AVAILABLE:
        return
    ACTIVE_SESSIONS.set(max(0, int(count)))


def set_queue_depth(queue: str, depth: int) -> None:
    if not _PROM_AVAILABLE:
        return
    QUEUE_DEPTH.labels(queue=queue).set(max(0, int(depth)))


def record_rate_limit_hit() -> None:
    if not _PROM_AVAILABLE:
        return
    RATE_LIMIT_HITS.inc()


def record_quota_exceeded(quota: str) -> None:
    if not _PROM_AVAILABLE:
        return
    QUOTA_EXCEEDED.labels(quota=quota).inc()


def render_metrics() -> tuple[bytes, str]:
    if not _PROM_AVAILABLE or generate_latest is None or REGISTRY is None:
        body = (
            b"# prometheus_client not installed\n"
            b"# HELP astra_metrics_up 0\n"
            b"# TYPE astra_metrics_up gauge\n"
            b"astra_metrics_up 0\n"
        )
        return body, "text/plain; charset=utf-8"
    try:
        # Подтянуть gauge сессий перед scrape
        from backend.auth import jwt_handler

        total = 0
        with jwt_handler._active_sessions_lock:  # noqa: SLF001
            for sessions in jwt_handler._active_sessions_by_user.values():  # noqa: SLF001
                total += len(sessions or [])
        set_active_sessions(total)
    except Exception:
        pass
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


class timed_llm:
    """Контекст/декоратор-helper для LLM latency."""

    def __init__(self) -> None:
        self._t0 = 0.0

    def __enter__(self) -> "timed_llm":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        dt = time.perf_counter() - self._t0
        observe_llm("error" if exc_type else "ok", dt)


class timed_rag:
    def __init__(self, scope: str = "default") -> None:
        self.scope = scope
        self._t0 = 0.0

    def __enter__(self) -> "timed_rag":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        dt = time.perf_counter() - self._t0
        observe_rag(self.scope, "error" if exc_type else "ok", dt)
