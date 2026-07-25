"""The process states, at startup, that the consent copy is not frozen.

A development stand that accepts draft copy must say so. The disclosure carries
a count and nothing else: `text_version` embeds the consent name, and §11 keeps
that out of the log.
"""

from __future__ import annotations

import io
import json

import pytest

from app.infra.clock import SystemClock
from app.infra.config import Settings
from app.infra.consent_copy import FileConsentCopyRegistry
from app.infra.logging import LOG_ALLOWLIST, configure_logging
from app.infra.telegram.initdata import InitDataValidator
from app.main import CONSENT_COPY_ROOT, AppDependencies, create_app
from app.services.erasure_journal import DatabaseErasureJournal

BASE_ENV = {
    "API_DATABASE_URL": "postgresql://api_rw@localhost:5432/diary",
    "TELEGRAM_BOT_ID": "1234567890",
    "WEBAPP_URL": "https://example.invalid",
}


def _dependencies(app_env: str) -> AppDependencies:
    settings = Settings.from_env({**BASE_ENV, "APP_ENV": app_env})
    consent_copy = FileConsentCopyRegistry(CONSENT_COPY_ROOT)

    def _no_database() -> object:  # pragma: no cover - never reached at startup
        raise AssertionError("startup must not touch the database")

    return AppDependencies(
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


@pytest.fixture
def captured() -> io.StringIO:
    buffer = io.StringIO()
    configure_logging(stream=buffer)
    return buffer


def test_the_registry_reports_which_versions_are_unfrozen() -> None:
    registry = FileConsentCopyRegistry(CONSENT_COPY_ROOT)

    assert sorted(registry.unfrozen_versions()) == [
        "cycle_sync@0.9",
        "health_sync@0.9",
        "telegram_reminders@0.9",
    ]


def test_development_startup_discloses_the_draft_copy(captured: io.StringIO) -> None:
    create_app(_dependencies("development"))

    records = [json.loads(line) for line in captured.getvalue().splitlines()]
    (disclosure,) = [
        record
        for record in records
        if record["event"] == "consent_copy_unfrozen_accepted"
    ]
    assert disclosure["level"] == "warning"
    assert disclosure["record_count"] == 3


def test_production_startup_discloses_that_grants_are_refused(
    captured: io.StringIO,
) -> None:
    create_app(_dependencies("production"))

    records = [json.loads(line) for line in captured.getvalue().splitlines()]
    (disclosure,) = [
        record
        for record in records
        if record["event"] == "consent_copy_unfrozen_refused"
    ]
    assert disclosure["level"] == "warning"


def test_the_disclosure_never_names_a_consent(captured: io.StringIO) -> None:
    """`text_version` is `<kind>@<major>.<minor>` — the kind may not be logged."""
    create_app(_dependencies("development"))

    raw = captured.getvalue()
    for kind in ("health_sync", "telegram_reminders", "cycle_sync"):
        assert kind not in raw
    for line in raw.splitlines():
        assert set(json.loads(line)) <= LOG_ALLOWLIST
