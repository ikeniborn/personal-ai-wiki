from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from paw.obs import metrics


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        metrics.HTTP_INFLIGHT.inc()
        start = time.perf_counter()
        status = 500
        try:
            response: Response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            template = getattr(route, "path", None) or "<unmatched>"
            method = request.method
            metrics.HTTP_INFLIGHT.dec()
            metrics.HTTP_DURATION.labels(method=method, route=template).observe(
                time.perf_counter() - start
            )
            metrics.HTTP_REQUESTS.labels(
                method=method, route=template, status=str(status)
            ).inc()
