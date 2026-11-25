"""HTTP-метрики Prometheus для всех запросов backend."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if (request.url.path or "").startswith("/metrics"):
            return await call_next(request)
        t0 = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = int(getattr(response, "status_code", 500) or 500)
            return response
        except Exception:
            status = 500
            raise
        finally:
            try:
                from backend.observability.metrics import observe_http

                observe_http(
                    request.method or "GET",
                    request.url.path or "/",
                    status,
                    time.perf_counter() - t0,
                )
            except Exception:
                pass
