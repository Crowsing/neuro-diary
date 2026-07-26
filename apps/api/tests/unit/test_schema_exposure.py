"""`/openapi.json`, `/docs` and `/redoc` behave differently in and out of development.

The two sides of this are in tension and the resolution has to serve both:
schemathesis needs the schema over HTTP from a live process, and production does
not need an interactive client for every endpoint sitting on the host.

Both branches are asserted, and the second assertion is the one that matters:
a test that only checked development would pass on an app that publishes the
schema everywhere.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.infra.clock import SystemClock
from app.infra.config import Settings
from app.infra.consent_copy import FileConsentCopyRegistry
from app.infra.telegram.initdata import InitDataValidator
from app.main import (
    DOCS_URL,
    REDOC_URL,
    SCHEMA_URL,
    AppDependencies,
    create_app,
    CONSENT_COPY_ROOT,
)
from app.services.erasure_journal import DatabaseErasureJournal

VIEWERS = (SCHEMA_URL, DOCS_URL, REDOC_URL)


#: §6.5 makes the external erasure journal mandatory outside development, so a
#: production-shaped `Settings` cannot be built without these. The keys are
#: throwaway test bytes; the journal object itself is the in-database one below,
#: because this module is about routes and nothing here erases anything.
PRODUCTION_JOURNAL = {
    "ERASURE_JOURNAL_ENABLED": "true",
    "ERASURE_JOURNAL_KEY": "11" * 32,
    "ERASURE_JOURNAL_HEAD_KEY": "22" * 32,
    "SERVICE_START_AT": "2026-01-01T00:00:00+00:00",
}


def _app(app_env: str) -> FastAPI:
    environment = {
        "API_DATABASE_URL": "postgresql://api_rw@localhost:5432/diary",
        "TELEGRAM_BOT_ID": "1234567890",
        "WEBAPP_URL": "https://example.invalid",
        "APP_ENV": app_env,
    }
    if app_env != "development":
        environment.update(PRODUCTION_JOURNAL)
    settings = Settings.from_env(environment)
    consent_copy = FileConsentCopyRegistry(CONSENT_COPY_ROOT)

    def _no_database() -> object:
        raise AssertionError("describing the API must not need a database")

    return create_app(
        AppDependencies(
            settings=settings,
            unit_of_work=_no_database,  # type: ignore[arg-type]
            clock=SystemClock(),
            init_data=InitDataValidator(
                bot_id=settings.telegram_bot_id,
                public_key=settings.telegram_ed25519_public_key,
            ),
            consent_copy=consent_copy,
            erasure_journal=DatabaseErasureJournal(
                _no_database,  # type: ignore[arg-type]
                consent_copy,
            ),
        )
    )


@pytest.mark.parametrize("path", VIEWERS)
def test_development_serves_the_schema_and_both_viewers(path: str) -> None:
    """The schemathesis job of Phase 5 fetches the document from a live api."""
    with TestClient(_app("development")) as client:
        assert client.get(path).status_code == 200, path


@pytest.mark.parametrize("path", VIEWERS)
def test_outside_development_all_three_are_absent(path: str) -> None:
    with TestClient(_app("production")) as client:
        assert client.get(path).status_code == 404, path


def test_the_oauth_redirect_page_goes_with_the_viewers() -> None:
    """`/docs/oauth2-redirect` is a fourth route FastAPI adds beside `/docs`.

    Harmless on its own, and named here because «the viewers are gone» has to
    mean every route they brought, not the two anyone thinks of.
    """
    with TestClient(_app("production")) as client:
        assert client.get("/docs/oauth2-redirect").status_code == 404
    with TestClient(_app("development")) as client:
        assert client.get("/docs/oauth2-redirect").status_code == 200


def test_closing_the_route_does_not_hide_the_document_from_the_suite() -> None:
    """`test_openapi_surface.py` must keep working outside development too.

    It builds the schema by calling `app.openapi()`, which goes through no route
    at all. That is the assumption the gating rests on, so it is asserted rather
    than assumed: the eight privacy assertions on the published surface must not
    become unreachable because production stopped serving the document.
    """
    application = _app("production")

    assert application.openapi_url is None
    assert "/v1/sync/push" in application.openapi()["paths"]


def test_the_health_probe_survives_the_gating() -> None:
    """The one route a deployment needs without a session stays public."""
    with TestClient(_app("production")) as client:
        assert client.get("/health").status_code == 200
