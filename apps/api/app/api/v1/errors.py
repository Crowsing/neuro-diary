"""Sanitized HTTP exception handlers.

Responses carry a stable ASCII code and nothing else. §11 forbids echoing the
submitted value, and the consent name never appears in an error either — the
set of consents an account holds is itself a health inference.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.infra.logging import get_logger
from app.domain.identity import (
    AuthInvalid,
    AuthReplayed,
    AuthStale,
    ConfirmRequired,
    ConsentAlreadyActive,
    ConsentCopyNotFrozen,
    ConsentPrecondition,
    ConsentRequired,
    ConsentTextMismatch,
    DomainError,
    NoAccount,
    QuietHoursViolation,
    StepUpRequired,
    UnknownTimezone,
)
from app.domain.reminders import NoSchedule
from app.domain.vault import (
    PayloadTooLarge,
    VaultGone,
    VaultReset,
)

STATUS_BY_ERROR: dict[type[DomainError], int] = {
    AuthInvalid: 401,
    AuthStale: 401,
    AuthReplayed: 401,
    NoAccount: 403,
    StepUpRequired: 403,
    #: Also `app.domain.vault.VaultForbidden`, which is the same class under the
    #: name the vault paths raise it by.
    ConsentRequired: 403,
    NoSchedule: 404,
    ConfirmRequired: 409,
    ConsentPrecondition: 409,
    ConsentAlreadyActive: 409,
    ConsentTextMismatch: 409,
    VaultReset: 409,
    VaultGone: 410,
    PayloadTooLarge: 413,
    QuietHoursViolation: 422,
    UnknownTimezone: 422,
    ConsentCopyNotFrozen: 503,
}


class RateLimitRefusal(Exception):
    """A §11 window is spent. Deliberately **not** a `DomainError`.

    A domain error cannot carry a value — `test_no_domain_error_accepts_a_
    constructor_argument` enforces that mechanically, because a field on the
    exception is the shortest path back to echoing an input into a response
    (§11). `Retry-After` is not an echo of an input: it is a server-computed
    integer already on the §11 log allowlist. But the guard is worth more than
    the convenience of reusing the base class, and a refusal that exists to
    carry an HTTP **header** belongs in the HTTP layer regardless.

    This is what closes the hole Phase 4's review named and left open: a 403
    from `require_consent` cost nothing, because the budget was spent inside the
    handler and the dependency refused before it. The dependency spends it now,
    and the refusal it raises on an exhausted window has somewhere to put the
    header — which is exactly what the review said a domain error could not do.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("rate_limited")
        self.retry_after_seconds = retry_after_seconds


async def rate_limit_refusal_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    retry_after = (
        error.retry_after_seconds if isinstance(error, RateLimitRefusal) else 1
    )
    request.state.error_code = "rate_limited"
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limited"},
        headers={"Retry-After": str(retry_after)},
    )


async def request_validation_error_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    del request, error
    return JSONResponse(
        status_code=422,
        content={"detail": "Request validation failed"},
    )


async def domain_error_handler(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, DomainError):  # pragma: no cover - registered by type
        return await unhandled_error_handler(request, error)
    status = STATUS_BY_ERROR.get(type(error), 500)
    request.state.error_code = error.code
    return JSONResponse(status_code=status, content={"error": error.code})


async def unhandled_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Swallow the exception here so its traceback never reaches the log.

    A database integrity error carries the failing statement and its bound
    parameters — account id, consent kind, telegram user id. Letting it escape
    to `uvicorn.error` would put all three on disk, past the §11 allowlist.
    """
    del error
    request.state.error_code = "internal"
    get_logger().error(
        "request_failed",
        request_id=getattr(request.state, "request_id", None),
        error_code="internal",
    )
    return JSONResponse(status_code=500, content={"error": "internal"})
