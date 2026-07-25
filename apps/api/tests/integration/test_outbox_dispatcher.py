"""The transactional outbox and its dispatcher (§4.4, §6.2, §6.4).

The dispatcher is a safety net, so most of these tests build the state a
successful path never produces: an erasure that did not happen, a journal entry
nobody closed. That is the point — the paths that work are covered elsewhere.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import Engine, text

from app.domain.events import ACCOUNT_ERASURE_REQUESTED, CONSENT_REVOKED
from app.infra.db.engine import SqlUnitOfWorkFactory
from app.services.erasure import ErasureService
from app.services.erasure_journal import DatabaseErasureJournal
from app.workers.outbox_dispatcher import OUTBOX_TTL, OutboxDispatcher
from conftest import Database, FrozenClock, REPO_ROOT, Caller, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()
REMINDERS_SHA = hashlib.sha256(
    (REGISTRY / "uk/telegram_reminders/0.9.md").read_bytes()
).hexdigest()

CONSENT_KINDS = ("health_sync", "telegram_reminders", "cycle_sync")


@pytest.fixture
def dispatcher(
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
    consent_copy: object,
) -> OutboxDispatcher:
    journal = DatabaseErasureJournal(unit_of_work, consent_copy)  # type: ignore[arg-type]
    return OutboxDispatcher(unit_of_work, ErasureService(journal), clock)


@pytest.fixture
def session(caller: Caller) -> Caller:
    response = caller.post(
        "/v1/auth/telegram",
        {
            "init_data": sign_init_data(),
            "grant": {
                "kind": "health_sync",
                "text_version": "health_sync@0.9",
                "text_sha256": HEALTH_SYNC_SHA,
            },
        },
    )
    assert response.status_code == 200, response.text
    caller.token = str(response.json()["session_token"])
    return caller


def _grant_reminders(caller: Caller) -> None:
    assert (
        caller.post(
            "/v1/consents",
            {
                "kind": "telegram_reminders",
                "text_version": "telegram_reminders@0.9",
                "text_sha256": REMINDERS_SHA,
                "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
            },
        ).status_code
        == 201
    )


def _events(engine: Engine) -> list[tuple[str, dict[str, object], datetime | None]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT event_type, payload, processed_at FROM diary.outbox ORDER BY id"
            )
        ).fetchall()
    return [(row.event_type, row.payload, row.processed_at) for row in rows]


def _account_id(engine: Engine) -> UUID:
    with engine.connect() as connection:
        return UUID(
            str(connection.execute(text("SELECT id FROM diary.account")).scalar_one())
        )


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        )


# ------------------------------------------------------------------ producer


def test_revoking_publishes_an_event_that_never_names_the_consent(
    session: Caller,
    engine: Engine,
) -> None:
    """§11 and §13.12: the kind stays on the domain event, not in the row."""
    _grant_reminders(session)

    response = session.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    assert response.status_code == 200, response.text
    events = _events(engine)
    assert [event[0] for event in events] == [CONSENT_REVOKED]
    assert events[0][1] == {"account_id": str(_account_id(engine))}
    assert events[0][2] is None
    serialized = json.dumps(events[0][1])
    for kind in CONSENT_KINDS:
        assert kind not in serialized


def test_a_full_erasure_leaves_exactly_one_event_and_it_is_its_own(
    session: Caller,
    engine: Engine,
) -> None:
    """§6.4 matrix: zero rows for the erased account in every table.

    The event announcing the erasure is published after the sweep, so it is the
    only survivor — the revocation events that preceded it are gone with
    everything else.
    """
    session.post("/v1/consents/revoke", {"kind": "health_sync"})

    assert _count(engine, "diary.account") == 0
    events = _events(engine)
    assert [event[0] for event in events] == [ACCOUNT_ERASURE_REQUESTED]


def test_an_erasure_removes_the_events_of_the_account_it_erases(
    session: Caller,
    engine: Engine,
) -> None:
    """The same matrix entry, reached the other way round.

    A reminders consent is revoked first — that publishes an event and leaves
    the account alive — and only then does the account go.
    """
    _grant_reminders(session)
    session.post("/v1/consents/revoke", {"kind": "telegram_reminders"})
    assert len(_events(engine)) == 1

    session.post("/v1/consents/revoke", {"kind": "health_sync"})

    assert [event[0] for event in _events(engine)] == [ACCOUNT_ERASURE_REQUESTED]


# ------------------------------------------------------------------ consumer


def test_the_dispatcher_marks_what_it_handled_and_nothing_else(
    session: Caller,
    engine: Engine,
    dispatcher: OutboxDispatcher,
    clock: FrozenClock,
) -> None:
    _grant_reminders(session)
    session.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    result = dispatcher.run_once()

    assert result.events_processed == 1
    # Still holding `health_sync`, so nothing was orphaned and nothing erased.
    assert result.accounts_erased == 0
    assert _count(engine, "diary.account") == 1
    assert _events(engine)[0][2] == clock.now()


def test_a_second_run_does_not_claim_an_event_it_already_settled(
    session: Caller,
    engine: Engine,
    dispatcher: OutboxDispatcher,
    clock: FrozenClock,
) -> None:
    """The first line of defence: `claim` never returns a settled event.

    This does not by itself prove idempotence — see the test below, which
    exercises the write directly, because this one passes even with the guard
    in `mark_processed` removed.
    """
    _grant_reminders(session)
    session.post("/v1/consents/revoke", {"kind": "telegram_reminders"})
    dispatcher.run_once()
    first = _events(engine)[0][2]

    clock.advance(hours=1)
    again = dispatcher.run_once()

    assert again.events_processed == 0
    assert _events(engine)[0][2] == first


def test_marking_an_event_processed_twice_keeps_the_first_timestamp(
    session: Caller,
    engine: Engine,
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
) -> None:
    """Redelivery is normal, so the write itself has to be idempotent.

    Two dispatchers, or one that retried after a partial failure, can both
    reach this write. A moved timestamp would postpone the TTL of §6.2 for as
    long as redelivery keeps happening, and the second caller would count work
    that was already done.
    """
    _grant_reminders(session)
    session.post("/v1/consents/revoke", {"kind": "telegram_reminders"})
    with unit_of_work() as unit:
        event = unit.outbox.claim(limit=1)[0]
        assert unit.outbox.mark_processed(event.id, at=clock.now())
        unit.commit()

    later = clock.now() + timedelta(hours=1)
    with unit_of_work() as unit:
        again = unit.outbox.mark_processed(event.id, at=later)
        unit.commit()

    assert again is False
    assert _events(engine)[0][2] == clock.now()


def test_an_unknown_event_type_stays_in_the_queue(
    session: Caller,
    engine: Engine,
    dispatcher: OutboxDispatcher,
    identity_database: Database,
) -> None:
    """Phase 4 adds the 403 reconciler; until then its events must survive.

    A dispatcher that marks what it does not understand as done is a dispatcher
    that silently drops the first event of every future feature.
    """
    account_id = _account_id(engine)
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO diary.outbox (event_type, payload) VALUES (%s, %s::jsonb)",
            ("reminder_delivery_failed", json.dumps({"account_id": str(account_id)})),
        )

    result = dispatcher.run_once()

    assert result.events_processed == 0
    assert _events(engine)[0][2] is None


def test_the_dispatcher_erases_an_account_whose_erasure_never_happened(
    session: Caller,
    engine: Engine,
    dispatcher: OutboxDispatcher,
    identity_database: Database,
) -> None:
    """§4.3 safety net, for the state the happy path never produces.

    Revocation erases an orphaned account inside its own transaction. If that
    transaction was lost — a crash, an unreachable journal on a retry — nobody
    picked the account up: `Housekeeper` only handles the two timeout reasons,
    and only after 30 days.
    """
    account_id = _account_id(engine)
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "UPDATE diary.consent SET revoked_at = now(), revoke_reason = 'user'"
        )
        connection.execute(
            "INSERT INTO diary.outbox (event_type, payload) VALUES (%s, %s::jsonb)",
            (CONSENT_REVOKED, json.dumps({"account_id": str(account_id)})),
        )

    result = dispatcher.run_once()

    assert result.accounts_erased == 1
    assert _count(engine, "diary.account") == 0
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT scope, completed_at FROM diary.erasure_job")
        ).one()
    assert row.scope == "full"
    assert row.completed_at is not None


def test_confirming_a_journal_entry_twice_keeps_the_first_time(
    session: Caller,
    engine: Engine,
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
) -> None:
    """Two confirmations of one erasure are normal, so the write is idempotent.

    The fast path confirms right after its commit; the dispatcher confirms
    again from the redelivered event. `completed_at` must keep saying when the
    erasure actually finished, not when someone last mentioned it.
    """
    session.post("/v1/consents/revoke", {"kind": "health_sync"})
    with engine.connect() as connection:
        reference = connection.execute(
            text("SELECT id FROM diary.erasure_job")
        ).scalar_one()
    first = _completed_at(engine)
    assert first == clock.now()

    with unit_of_work() as unit:
        unit.erasure.complete(reference, at=clock.now() + timedelta(hours=1))
        unit.commit()

    assert _completed_at(engine) == first


def _completed_at(engine: Engine) -> datetime | None:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT completed_at FROM diary.erasure_job")
        ).scalar_one()


def test_the_dispatcher_leaves_the_thirty_day_window_to_the_housekeeper(
    session: Caller,
    engine: Engine,
    dispatcher: OutboxDispatcher,
    clock: FrozenClock,
    identity_database: Database,
) -> None:
    """§4.3: a consent lost for a reason that was not the user's decision keeps
    the account for 30 days, so she can come back and restore it."""
    account_id = _account_id(engine)
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "UPDATE diary.consent SET revoked_at = now(),"
            " revoke_reason = 'bot_blocked_timeout'"
        )
        # `created_at` comes from the frozen clock, not from the server: the
        # table checks `processed_at >= created_at`, and a row stamped with the
        # wall clock would be unprocessable by a test that lives in the past.
        connection.execute(
            "INSERT INTO diary.outbox (event_type, payload, created_at)"
            " VALUES (%s, %s::jsonb, %s)",
            (
                CONSENT_REVOKED,
                json.dumps({"account_id": str(account_id)}),
                clock.now(),
            ),
        )

    result = dispatcher.run_once()

    assert result.accounts_erased == 0
    assert _count(engine, "diary.account") == 1


def test_the_dispatcher_closes_a_journal_entry_nobody_confirmed(
    session: Caller,
    engine: Engine,
    dispatcher: OutboxDispatcher,
    clock: FrozenClock,
    identity_database: Database,
) -> None:
    """§6.4 opens the entry before the deletion and closes it after.

    A process that dies in between leaves `completed_at` NULL for good — and
    the promise "we re-run your deletion within 24 hours" is built on exactly
    that column being truthful.
    """
    account_id = _account_id(engine)
    reference = uuid4()
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO diary.erasure_job"
            " (id, account_id, scope, requested_at, deletion_copy_version)"
            " VALUES (%s, %s, 'full', %s, 'deletion@1.0')",
            (reference, account_id, clock.now()),
        )
        connection.execute(
            "INSERT INTO diary.outbox (event_type, payload, created_at)"
            " VALUES (%s, %s::jsonb, %s)",
            (
                ACCOUNT_ERASURE_REQUESTED,
                json.dumps(
                    {
                        "account_id": str(account_id),
                        "erasure_reference": str(reference),
                    }
                ),
                clock.now(),
            ),
        )

    result = dispatcher.run_once()

    assert result.journals_confirmed == 1
    with engine.connect() as connection:
        completed = connection.execute(
            text("SELECT completed_at FROM diary.erasure_job WHERE id = :id"),
            {"id": reference},
        ).scalar_one()
    assert completed == clock.now()


# ------------------------------------------------------------------- §6.2 TTL


def test_settled_events_leave_after_thirty_days_and_pending_ones_never_do(
    session: Caller,
    engine: Engine,
    dispatcher: OutboxDispatcher,
    clock: FrozenClock,
    identity_database: Database,
) -> None:
    account_id = _account_id(engine)
    settled = clock.now() - OUTBOX_TTL - timedelta(days=1)
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO diary.outbox (event_type, payload, created_at, processed_at)"
            " VALUES (%s, %s::jsonb, %s, %s)",
            (
                CONSENT_REVOKED,
                json.dumps({"account_id": str(account_id)}),
                settled,
                settled,
            ),
        )
        # Same age, never processed: the TTL is measured from `processed_at`,
        # so an event still waiting for its consumer must survive any sweep.
        connection.execute(
            "INSERT INTO diary.outbox (event_type, payload, created_at)"
            " VALUES (%s, %s::jsonb, %s)",
            (
                "reminder_delivery_failed",
                json.dumps({"account_id": str(account_id)}),
                settled,
            ),
        )

    result = dispatcher.run_once()

    assert result.events_removed == 1
    assert [event[0] for event in _events(engine)] == ["reminder_delivery_failed"]


def test_deliveries_older_than_fourteen_days_are_swept_by_api_rw(
    session: Caller,
    engine: Engine,
    dispatcher: OutboxDispatcher,
    clock: FrozenClock,
    identity_database: Database,
) -> None:
    """§6.2 and §6.3 together: this sweep cannot live in the reminder worker,
    which is deliberately denied `DELETE` on this table."""
    account_id = _account_id(engine)
    old = clock.now() - timedelta(days=15)
    recent = clock.now() - timedelta(days=13)
    with psycopg.connect(identity_database.admin_url, autocommit=True) as connection:
        for index, moment in enumerate((old, recent)):
            connection.execute(
                "INSERT INTO reminders.reminder_delivery"
                " (account_id, local_date, status, created_at)"
                " VALUES (%s, %s, 'sent', %s)",
                (account_id, datetime(2026, 7, 1 + index, tzinfo=UTC).date(), moment),
            )

    result = dispatcher.run_once()

    assert result.deliveries_removed == 1
    assert _count(engine, "reminders.reminder_delivery") == 1
