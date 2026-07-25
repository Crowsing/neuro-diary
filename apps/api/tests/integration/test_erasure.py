"""Erasure ordering and the deadline that depends on `revoke_reason` (§4.3, §6.4)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

import psycopg
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.infra.config import Settings
from app.main import AppDependencies, create_app
from conftest import REPO_ROOT, Caller, Database, FrozenClock, sign_init_data

HEALTH_SYNC_SHA = hashlib.sha256(
    (REPO_ROOT / "consent-copy" / "uk" / "health_sync" / "0.9.md").read_bytes()
).hexdigest()
REMINDERS_SHA = hashlib.sha256(
    (REPO_ROOT / "consent-copy" / "uk" / "telegram_reminders" / "0.9.md").read_bytes()
).hexdigest()
GRANT = {
    "kind": "health_sync",
    "text_version": "health_sync@0.9",
    "text_sha256": HEALTH_SYNC_SHA,
}


class RefusingJournal:
    """Stands in for an unreachable append-only store (§6.4)."""

    def record_intent(self, *, account_id: UUID, code: str, at: datetime) -> UUID:
        del account_id, code, at
        raise RuntimeError("journal unavailable")

    def confirm(self, reference: UUID, *, at: datetime) -> None:  # pragma: no cover
        del reference, at


def _sign_in(caller: Caller) -> str:
    response = caller.post(
        "/v1/auth/telegram",
        {"init_data": sign_init_data(), "grant": GRANT},
    )
    assert response.status_code == 200, response.text
    token = str(response.json()["session_token"])
    caller.token = token
    return token


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        )


def test_losing_the_last_consent_by_user_action_erases_immediately(
    caller: Caller,
    engine: Engine,
) -> None:
    _sign_in(caller)

    response = caller.post("/v1/consents/revoke", {"kind": "health_sync"})

    assert response.status_code == 200
    assert response.json() == {"account_erased": True}
    for table in ("account", "telegram_identity", "consent", "session_token"):
        assert _count(engine, f"diary.{table}") == 0, table


def test_the_erasure_job_records_the_promise_in_force(
    caller: Caller,
    engine: Engine,
) -> None:
    _sign_in(caller)

    caller.post("/v1/consents/revoke", {"kind": "health_sync"})

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT scope, deletion_copy_version, requested_at"
                " FROM diary.erasure_job"
            )
        ).one()
    assert row.scope == "full"
    assert row.deletion_copy_version == "deletion@1.0"
    assert row.requested_at is not None


def test_a_remaining_consent_keeps_the_account(
    caller: Caller,
    engine: Engine,
) -> None:
    _sign_in(caller)
    caller.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
            "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
        },
    )

    response = caller.post("/v1/consents/revoke", {"kind": "health_sync"})

    assert response.json() == {"account_erased": False}
    assert _count(engine, "diary.account") == 1
    # The account survives, but its vault does not: §6.4 gives that partial
    # erasure its own journal code, and the entry is written before the
    # deletion exactly as a full erasure's is.
    with engine.connect() as connection:
        scopes = (
            connection.execute(text("SELECT scope FROM diary.erasure_job"))
            .scalars()
            .all()
        )
    assert scopes == ["sync_off"]


def test_erasure_removes_the_reminder_rows_that_have_no_foreign_key(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
) -> None:
    """§6.4 names *two* tables in this schema, so the test asserts on both.

    `reminder_delivery` is written through the admin connection because §6.3
    gives `api_rw` no INSERT there — phase 4's worker owns that write. Without
    a row present, removing the DELETE from the repository left the whole suite
    green, which is exactly how an unguarded promise survives to production.
    """
    _sign_in(caller)
    caller.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
            "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
        },
    )
    with engine.connect() as connection:
        account_id = connection.execute(
            text("SELECT id FROM diary.account")
        ).scalar_one()
    with psycopg.connect(identity_database.admin_url, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO reminders.reminder_delivery"
            " (account_id, local_date, status, created_at)"
            " VALUES (%s, DATE '2026-07-24', 'sent', now())",
            (account_id,),
        )
    caller.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    caller.post("/v1/consents/revoke", {"kind": "health_sync"})

    assert _count(engine, "diary.account") == 0
    assert _count(engine, "reminders.reminder_schedule") == 0
    assert _count(engine, "reminders.reminder_delivery") == 0


@pytest.fixture
def refusing_journal_api(
    dependencies: AppDependencies,
    settings: Settings,
) -> FastAPI:
    return create_app(
        AppDependencies(
            settings=settings,
            unit_of_work=dependencies.unit_of_work,
            clock=dependencies.clock,
            init_data=dependencies.init_data,
            consent_copy=dependencies.consent_copy,
            erasure_journal=RefusingJournal(),
        )
    )


def test_an_unwritable_journal_stops_the_erasure(
    refusing_journal_api: FastAPI,
    engine: Engine,
) -> None:
    """§6.4: journal first. No record, no deletion — never the other way round."""
    caller = Caller(refusing_journal_api)
    _sign_in(caller)

    with pytest.raises(RuntimeError):
        caller.post("/v1/consents/revoke", {"kind": "health_sync"})

    assert _count(engine, "diary.account") == 1
    assert _count(engine, "diary.erasure_job") == 0
    with engine.connect() as connection:
        revoked = connection.execute(
            text("SELECT count(*) FROM diary.consent WHERE revoked_at IS NOT NULL")
        ).scalar_one()
    assert revoked == 0


def test_account_delete_journals_before_deleting(
    caller: Caller,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    _sign_in(caller)

    response = caller.post("/v1/account/delete")

    assert response.status_code == 200
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT scope, deletion_copy_version, requested_at, completed_at"
                " FROM diary.erasure_job"
            )
        ).one()
    assert row.scope == "full"
    assert row.requested_at == clock.now()
    assert row.completed_at == clock.now()
    assert _count(engine, "diary.account") == 0


def test_an_unwritable_journal_stops_account_delete(
    refusing_journal_api: FastAPI,
    engine: Engine,
) -> None:
    caller = Caller(refusing_journal_api)
    _sign_in(caller)

    with pytest.raises(RuntimeError):
        caller.post("/v1/account/delete")

    assert _count(engine, "diary.account") == 1
