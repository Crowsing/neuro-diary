"""Sanitized HTTP exception handlers.

Responses carry a stable ASCII code and nothing else. §11 forbids echoing the
submitted value, and the consent name never appears in an error either — the
set of consents an account holds is itself a health inference.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

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
    assert isinstance(error, DomainError)
    status = STATUS_BY_ERROR.get(type(error), 500)
    request.state.error_code = error.code
    return JSONResponse(status_code=status, content={"error": error.code})
