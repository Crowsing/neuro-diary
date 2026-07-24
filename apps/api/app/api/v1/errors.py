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
    ConsentAlreadyActive,
    ConsentCopyNotFrozen,
    ConsentPrecondition,
    ConsentTextMismatch,
    DomainError,
    NoAccount,
    QuietHoursViolation,
    StepUpRequired,
    UnknownTimezone,
)

STATUS_BY_ERROR: dict[type[DomainError], int] = {
    AuthInvalid: 401,
    AuthStale: 401,
    AuthReplayed: 401,
    NoAccount: 403,
    StepUpRequired: 403,
    ConsentPrecondition: 409,
    ConsentAlreadyActive: 409,
    ConsentTextMismatch: 409,
    QuietHoursViolation: 422,
    UnknownTimezone: 422,
    ConsentCopyNotFrozen: 503,
}


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
