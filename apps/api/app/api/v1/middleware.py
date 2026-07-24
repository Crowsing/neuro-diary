"""Request context logging and the per-IP authentication limiter.

§11 puts the reverse-proxy limit at 10 auth attempts per minute and requires the
counter to live in process memory only. The counter here keys on an HMAC of the
address, so neither the log nor a memory dump yields the address itself.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.infra.logging import client_ref, get_logger

UNMATCHED_ROUTE = "unmatched"
_MILLISECONDS = 1_000

Handler = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Log one allowlisted line per request."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(app)
        self._monotonic = monotonic

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        request_id = uuid4().hex
        request.state.request_id = request_id
        started = self._monotonic()

        response = await call_next(request)

        duration_ms = int((self._monotonic() - started) * _MILLISECONDS)
        fields: dict[str, object] = {
            "request_id": request_id,
            "route_template": _route_template(request),
            "method": request.method,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        }
        error_code = getattr(request.state, "error_code", None)
        if error_code:
            fields["error_code"] = error_code

        get_logger().info("request_completed", **fields)
        response.headers["X-Request-Id"] = request_id
        return response


def _route_template(request: Request) -> str:
    """Return the matched route template, never the requested URL.

    A raw path can carry a date or a consent name, both of which §11 forbids.
    Unmatched requests are reported as a constant for the same reason.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    declared = getattr(request.state, "route_template", None)
    return declared if isinstance(declared, str) else UNMATCHED_ROUTE


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-address limiter held entirely in process memory."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        limits: Mapping[str, int],
        window_seconds: int,
        secret: bytes,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(app)
        self._limits = dict(limits)
        self._window = window_seconds
        self._secret = secret
        self._monotonic = monotonic
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        path = request.url.path
        limit = self._limits.get(path)
        if limit is None:
            return await call_next(request)

        # The template is a constant from the configured map, so recording it is
        # not the same as recording the requested URL.
        request.state.route_template = path

        now = self._monotonic()
        hits = self._hits[(path, self._caller(request))]
        while hits and now - hits[0] >= self._window:
            hits.popleft()

        if len(hits) >= limit:
            retry_after = max(1, int(self._window - (now - hits[0])))
            request.state.error_code = "rate_limited"
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited"},
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)
        return await call_next(request)

    def _caller(self, request: Request) -> str:
        client = request.client
        address = client.host if client else "unknown"
        return client_ref(self._secret, address, datetime.now(UTC))
