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

from app.infra.logging import account_ref, client_ref, get_logger

UNMATCHED_ROUTE = "unmatched"
_MILLISECONDS = 1_000

#: §9.2: the pull cursor is the only thing allowed into a request line, and only
#: as integers. The path is compared literally because no path template in this
#: API carries a parameter — `test_nothing_but_an_integer_cursor_travels_in_the_url`
#: holds that from the other side.
QUERY_ALLOWLIST: Mapping[str, frozenset[str]] = {
    "/v1/sync/pull": frozenset({"since", "limit", "consent_epoch"}),
}

Handler = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Log one allowlisted line per request."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        secret: bytes = b"",
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(app)
        self._secret = secret
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
        # §11 allows both counters by name. They are what makes a sync
        # debuggable without learning anything about what was synchronized:
        # how many records moved, and to which revision.
        for counter in ("record_count", "revision"):
            value = getattr(request.state, counter, None)
            if value is not None:
                fields[counter] = value
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            fields["retry_after"] = int(retry_after)

        account_id = getattr(request.state, "account_id", None)
        if account_id is not None:
            # The rotating reference, never the identifier itself (§11).
            fields["account_ref"] = account_ref(
                self._secret, account_id, datetime.now(UTC)
            )

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


class QueryParameterAllowlistMiddleware(BaseHTTPMiddleware):
    """Refuse any query parameter this API does not read.

    Starlette ignores unknown query parameters, which is the convention and is
    also how a medical value would reach a reverse proxy's request line: §2 keeps
    symptoms, ratings, cycle dates and notes out of the URL, and until Phase 5
    that rule was enforced only by the client. Ignoring a parameter is not the
    same as refusing it — the request line is already written by then.

    Found by the schemathesis run of §12, which reported that a schema-violating
    request (`?x-schemathesis-unknown-property=42`) was accepted with 200.
    """

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        allowed = QUERY_ALLOWLIST.get(request.url.path, frozenset())
        unknown = set(request.query_params) - allowed
        if unknown:
            # The names are not echoed: §11 forbids putting a submitted value in
            # an error, and a parameter name is a submitted value.
            #
            # `route_template` is deliberately left unset, which makes the log
            # line say `unmatched`. This middleware runs before routing and knows
            # only the requested path — and a requested path is exactly what §11
            # forbids in the log. Losing the template on a malformed request is
            # the cheaper of the two mistakes.
            request.state.error_code = "unknown_query_parameter"
            return JSONResponse(
                status_code=422,
                content={"detail": "Request validation failed"},
            )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-address limiter held entirely in process memory."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        limits: Mapping[tuple[str, str], int],
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
        # Keyed by method as well as path. §11 limits *authentication attempts*,
        # and a `PATCH` to that path is not one: it is a 405 the router answers
        # without touching the database. Before Phase 5 the counter ignored the
        # method, so a 429 preempted the 405 — found by the `unsupported_method`
        # check of the schemathesis run.
        limit = self._limits.get((request.method, path))
        if limit is None:
            return await call_next(request)

        # The template is a constant from the configured map, so recording it is
        # not the same as recording the requested URL.
        request.state.route_template = path

        now = self._monotonic()
        key = (f"{request.method} {path}", self._caller(request))
        hits = self._hits[key]
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if not hits:
            # Otherwise every address ever seen keeps an empty deque for good,
            # and `client_ref` rotates weekly.
            del self._hits[key]
            hits = self._hits[key]

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
