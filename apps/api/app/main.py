"""FastAPI composition root."""

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.v1.errors import request_validation_error_handler
from app.api.v1.health import router as health_router


def create_app() -> FastAPI:
    # Uvicorn's default access formatter includes the raw query string.
    logging.getLogger("uvicorn.access").disabled = True

    application = FastAPI(title="Neuro Diary API")
    application.add_exception_handler(
        RequestValidationError,
        request_validation_error_handler,
    )
    application.include_router(health_router)
    return application


app = create_app()
