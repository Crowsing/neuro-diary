"""Sanitized HTTP exception handlers."""

from fastapi import Request
from fastapi.responses import JSONResponse


async def request_validation_error_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    del request, error
    return JSONResponse(
        status_code=422,
        content={"detail": "Request validation failed"},
    )
