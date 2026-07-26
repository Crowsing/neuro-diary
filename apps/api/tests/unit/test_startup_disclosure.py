"""The process states, at startup, that the consent copy is not frozen.

A development stand that accepts draft copy must say so. The disclosure carries
a count and nothing else: `text_version` embeds the consent name, and §11 keeps
that out of the log.
"""

from __future__ import annotations

import io
import json
import secrets

import pytest

from app.infra.clock import SystemClock
from app.infra.config import Settings
from app.infra.consent_copy import FileConsentCopyRegistry
from app.infra.logging import LOG_ALLOWLIST, configure_logging
from app.infra.telegram.initdata import InitDataValidator
from app.main import CONSENT_COPY_ROOT, AppDependencies, create_app
from app.services.erasure_journal import DatabaseErasureJournal, TeeErasureJournal

BASE_ENV = {
    "API_DATABASE_URL": "postgresql://api_rw@localhost:5432/diary",
    "TELEGRAM_BOT_ID": "1234567890",
    "WEBAPP_URL": "https://example.invalid",
}


def _journal_env(app_env: str) -> dict[str, str]:
    """§6.5 makes the external journal mandatory outside development.

    Minted per call: gitleaks scans the whole history, so a hex literal in a
    file would keep CI red long after it left HEAD.
    """
    if app_env == "development":
        return {}
    return {
        "ERASURE_JOURNAL_ENABLED": "true",
        "ERASURE_JOURNAL_KEY": secrets.token_bytes(32).hex(),
        "ERASURE_JOURNAL_HEAD_KEY": secrets.token_bytes(32).hex(),
        "SERVICE_START_AT": "2026-01-01T00:00:00Z",
    }


def _dependencies(app_env: str) -> AppDependencies:
    settings = Settings.from_env(
        {**BASE_ENV, "APP_ENV": app_env, **_journal_env(app_env)}
    )
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


def test_startup_discloses_which_erasure_journal_is_wired(
    captured: io.StringIO,
) -> None:
    """§6.5: running on `diary.erasure_job` alone is a weakened guarantee.

    The line reports the wired object rather than the configuration flag. The
    flag says what the environment asked for; only the object says what this
    process will actually do, and reporting the wrong one would be a claim
    nothing keeps.
    """
    create_app(_dependencies("development"))

    events = [json.loads(line)["event"] for line in captured.getvalue().splitlines()]
    assert "erasure_journal_database_only" in events
    assert "erasure_journal_external" not in events


def test_a_teed_journal_is_disclosed_as_external(captured: io.StringIO) -> None:
    plain = _dependencies("development")
    create_app(
        AppDependencies(
            settings=plain.settings,
            unit_of_work=plain.unit_of_work,
            clock=plain.clock,
            init_data=plain.init_data,
            consent_copy=plain.consent_copy,
            erasure_journal=TeeErasureJournal(
                plain.erasure_journal,
                plain.erasure_journal,
            ),
        )
    )

    events = [json.loads(line)["event"] for line in captured.getvalue().splitlines()]
    assert "erasure_journal_external" in events
    assert "erasure_journal_database_only" not in events


def test_the_disclosure_never_names_a_consent(captured: io.StringIO) -> None:
    """`text_version` is `<kind>@<major>.<minor>` — the kind may not be logged."""
    create_app(_dependencies("development"))

    raw = captured.getvalue()
    for kind in ("health_sync", "telegram_reminders", "cycle_sync"):
        assert kind not in raw
    for line in raw.splitlines():
        assert set(json.loads(line)) <= LOG_ALLOWLIST
