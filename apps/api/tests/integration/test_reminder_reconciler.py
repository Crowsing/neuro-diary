"""The 403 reconciler and the message-cleanup queue (§10, §6.4).

Two promises that were written down before this phase and kept by nobody: that
a block reaches the consent after fourteen consecutive days, and that a deleted
schedule leaves behind exactly enough to take its messages out of the chat.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from sqlalchemy import Engine, text

from app.domain.events import CONSENT_REVOKED
from app.domain.reminders import BOT_BLOCKED_STREAK, MESSAGE_CLEANUP_TTL, SendOutcome
from app.infra.db.engine import SqlReminderUnitOfWorkFactory, SqlUnitOfWorkFactory
from app.services.consent import ConsentService
from app.services.erasure import ErasureService
from app.services.erasure_journal import DatabaseErasureJournal
from app.workers.erasure_worker import Housekeeper
from app.workers.outbox_dispatcher import OutboxDispatcher
from app.workers.reminder_worker import ReminderWorker

from conftest import Caller, Database, FrozenClock, REPO_ROOT, sign_init_data
from test_reminder_worker import FakeTelegram

REMINDERS_SHA = hashlib.sha256(
    (REPO_ROOT / "consent-copy" / "uk" / "telegram_reminders" / "0.9.md").read_bytes()
).hexdigest()
HEALTH_SHA = hashlib.sha256(
    (REPO_ROOT / "consent-copy" / "uk" / "health_sync" / "0.9.md").read_bytes()
).hexdigest()

KYIV = "Europe/Kyiv"
FIRE_AT = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)


@pytest.fixture
def dispatcher(
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
    consent_copy: object,
) -> OutboxDispatcher:
    erasure = ErasureService(
        DatabaseErasureJournal(unit_of_work, consent_copy)  # type: ignore[arg-type]
    )
    consents = ConsentService(
        consent_copy,  # type: ignore[arg-type]
        erasure,
        allow_unfrozen_copy=True,
    )
    return OutboxDispatcher(unit_of_work, erasure, consents, clock)


def _grant(caller: Caller, *, also_health: bool = False) -> None:
    response = caller.post(
        "/v1/auth/telegram",
        {
            "init_data": sign_init_data(),
            "grant": {
                "kind": "telegram_reminders",
                "text_version": "telegram_reminders@0.9",
                "text_sha256": REMINDERS_SHA,
                "settings": {"time": "20:00", "timezone": KYIV},
            },
        },
    )
    assert response.status_code == 200, response.text
    caller.token = str(response.json()["session_token"])
    if also_health:
        assert (
            caller.post(
                "/v1/consents",
                {
                    "kind": "health_sync",
                    "text_version": "health_sync@0.9",
                    "text_sha256": HEALTH_SHA,
                },
            ).status_code
            == 201
        )


def _block(
    caller: Caller,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
    *,
    also_health: bool = False,
) -> None:
    """Reach the blocked state the only way production can: a 403 on a send."""
    _grant(caller, also_health=also_health)
    clock.set(FIRE_AT)
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "UPDATE reminders.reminder_schedule SET next_fire_at = %s", (FIRE_AT,)
        )
    ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine),
        FakeTelegram(outcome=SendOutcome.BLOCKED),
        clock,
    ).run_once()


def _consent_rows(engine: Engine) -> list[tuple[str, datetime | None, str | None]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT kind, revoked_at, revoke_reason FROM diary.consent ORDER BY kind"
            )
        ).all()
    return [(row.kind, row.revoked_at, row.revoke_reason) for row in rows]


def _events(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return [
            row.event_type
            for row in connection.execute(
                text("SELECT event_type FROM diary.outbox ORDER BY id")
            ).all()
        ]


def _rows(engine: Engine, statement: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement)).scalar_one())


# ------------------------------------------------------------- the fourteen days


def test_a_block_of_thirteen_days_does_not_touch_the_consent(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
    dispatcher: OutboxDispatcher,
) -> None:
    """§10 DoD: 403 does not revoke immediately, and thirteen days is not fourteen."""
    _block(caller, identity_database, worker_engine, clock)

    clock.set(FIRE_AT + BOT_BLOCKED_STREAK - timedelta(days=1))
    result = dispatcher.run_once()

    assert result.consents_timed_out == 0
    assert _consent_rows(engine) == [("telegram_reminders", None, None)]
    assert _rows(engine, "SELECT count(*) FROM reminders.reminder_schedule") == 1


def test_a_block_of_fourteen_days_reaches_the_consent(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
    dispatcher: OutboxDispatcher,
) -> None:
    """§10: `revoked_at` + `revoke_reason='bot_blocked_timeout'` + `ConsentRevoked`.

    And §4.4: the schedule rows go with the consent rather than outliving it.
    """
    _block(caller, identity_database, worker_engine, clock, also_health=True)
    settled = FIRE_AT + BOT_BLOCKED_STREAK
    clock.set(settled)

    result = dispatcher.run_once()

    assert result.consents_timed_out == 1
    assert ("telegram_reminders", settled, "bot_blocked_timeout") in _consent_rows(
        engine
    )
    assert CONSENT_REVOKED in _events(engine)
    assert _rows(engine, "SELECT count(*) FROM reminders.reminder_schedule") == 0
    assert _rows(engine, "SELECT count(*) FROM reminders.reminder_delivery") == 0
    # The account keeps living: another consent is still active, and a timeout
    # is not the user's decision anyway.
    assert _rows(engine, "SELECT count(*) FROM diary.account") == 1


def test_a_pause_never_reaches_the_consent_however_long_it_lasts(
    caller: Caller,
    engine: Engine,
    clock: FrozenClock,
    dispatcher: OutboxDispatcher,
) -> None:
    """§10, and the test the launch parameters called non-optional.

    `enabled = false` with no reason is the user switching reminders off. The
    reconciler looks at `disabled_reason` and never at `enabled`, so a year of
    pause costs nothing.
    """
    _grant(caller)
    assert (
        caller.put(
            "/v1/reminders/settings",
            {"enabled": False, "time": "20:00", "timezone": KYIV},
        ).status_code
        == 200
    )

    clock.set(FIRE_AT + timedelta(days=365))
    result = dispatcher.run_once()

    assert result.consents_timed_out == 0
    assert _consent_rows(engine) == [("telegram_reminders", None, None)]
    assert _rows(engine, "SELECT count(*) FROM reminders.reminder_schedule") == 1


def test_a_second_pass_does_not_revoke_twice(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
    dispatcher: OutboxDispatcher,
) -> None:
    """Redelivery is normal, so the reconciler has to be idempotent."""
    _block(caller, identity_database, worker_engine, clock, also_health=True)
    clock.set(FIRE_AT + BOT_BLOCKED_STREAK)
    dispatcher.run_once()

    result = dispatcher.run_once()

    assert result.consents_timed_out == 0
    revoked = [row for row in _consent_rows(engine) if row[1] is not None]
    assert len(revoked) == 1


def test_the_housekeeper_gives_the_timeout_its_thirty_day_window(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
    dispatcher: OutboxDispatcher,
    unit_of_work: SqlUnitOfWorkFactory,
    consent_copy: object,
) -> None:
    """§4.3: a consent lost to a timeout is not the user's decision.

    Fourteen days to reach the consent, then thirty more before the account
    goes — and the account must still be recoverable inside them.
    """
    _block(caller, identity_database, worker_engine, clock)
    clock.set(FIRE_AT + BOT_BLOCKED_STREAK)
    dispatcher.run_once()
    assert _rows(engine, "SELECT count(*) FROM diary.account") == 1

    housekeeper = Housekeeper(
        unit_of_work,
        ErasureService(
            DatabaseErasureJournal(unit_of_work, consent_copy)  # type: ignore[arg-type]
        ),
        clock,
    )

    clock.set(FIRE_AT + BOT_BLOCKED_STREAK + timedelta(days=29))
    housekeeper.run_once()
    assert _rows(engine, "SELECT count(*) FROM diary.account") == 1

    clock.set(FIRE_AT + BOT_BLOCKED_STREAK + timedelta(days=31))
    housekeeper.run_once()
    assert _rows(engine, "SELECT count(*) FROM diary.account") == 0


# ------------------------------------------------------------ the cleanup queue


def _sent_one(
    caller: Caller,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
) -> FakeTelegram:
    _grant(caller)
    clock.set(FIRE_AT)
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "UPDATE reminders.reminder_schedule SET next_fire_at = %s", (FIRE_AT,)
        )
    telegram = FakeTelegram()
    ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine), telegram, clock
    ).run_once()
    return telegram


def _cleanup_rows(identity_database: Database) -> list[tuple[int, int]]:
    with psycopg.connect(identity_database.admin_url, autocommit=True) as connection:
        return [
            (row[0], row[1])
            for row in connection.execute(
                "SELECT chat_id, message_id FROM reminders.message_cleanup"
                " ORDER BY message_id"
            ).fetchall()
        ]


def test_revoking_reminders_hands_the_sent_messages_to_the_worker(
    caller: Caller,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
) -> None:
    """§6.4, the promise that had no writer before this phase.

    «До 48 годин після видалення на сервері лишаються chat_id і номери
    повідомлень — саме щоб їх прибрати.»
    """
    _sent_one(caller, identity_database, worker_engine, clock)

    response = caller.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    assert response.status_code == 200
    assert _cleanup_rows(identity_database) == [(4242424242, 1001)]


def test_deleting_the_account_hands_them_over_too(
    caller: Caller,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
) -> None:
    """A full erasure removes the same rows, so it owes the same hand-over."""
    _sent_one(caller, identity_database, worker_engine, clock)
    # §8 step-up: deleting an account needs a session younger than ten minutes,
    # and the send above moved the clock five hours forward.
    fresh = caller.post(
        "/v1/auth/telegram", {"init_data": sign_init_data(auth_date=clock.now())}
    )
    caller.token = str(fresh.json()["session_token"])

    response = caller.post("/v1/account/delete")

    assert response.status_code == 200, response.text
    assert _cleanup_rows(identity_database) == [(4242424242, 1001)]


def test_the_worker_takes_the_message_back_and_forgets_it(
    caller: Caller,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
) -> None:
    _sent_one(caller, identity_database, worker_engine, clock)
    caller.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    telegram = FakeTelegram()
    result = ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine), telegram, clock
    ).run_once()

    assert telegram.deleted == [(4242424242, 1001)]
    assert result.messages_removed == 1
    assert _cleanup_rows(identity_database) == []


def test_a_refused_deletion_keeps_the_row_until_its_ttl_and_no_longer(
    caller: Caller,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
) -> None:
    """§6.4: the promise is that the chat id stops living here, not that Telegram
    agreed. A bot may only delete its own message for a short while, so the TTL
    is unconditional — otherwise a permanent refusal would keep the identifier
    for ever, which is the opposite of what the queue is for.
    """
    _sent_one(caller, identity_database, worker_engine, clock)
    caller.post("/v1/consents/revoke", {"kind": "telegram_reminders"})
    factory = SqlReminderUnitOfWorkFactory(worker_engine)

    stubborn = FakeTelegram()
    stubborn.deletion_succeeds = False
    ReminderWorker(factory, stubborn, clock).run_once()

    assert _cleanup_rows(identity_database) == [(4242424242, 1001)]

    # The **same** refusing fake past the TTL. Handing the second pass a healthy
    # one would remove the row through `forget_cleanup` after a successful
    # `deleteMessages` and prove the opposite branch — which is exactly what the
    # first version of this test did, leaving the unconditional expiry sweep
    # deletable with the suite green.
    clock.advance(hours=49)
    result = ReminderWorker(factory, stubborn, clock).run_once()

    assert result.messages_removed == 0
    assert stubborn.deleted == [(4242424242, 1001), (4242424242, 1001)]
    assert _cleanup_rows(identity_database) == []


def test_a_revocation_without_a_delivery_queues_nothing(
    caller: Caller,
    identity_database: Database,
) -> None:
    """The counterpart: a queue that filled unconditionally would keep a chat id
    for an account that never received a message."""
    _grant(caller)

    caller.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    assert _cleanup_rows(identity_database) == []


def test_the_queue_records_the_forty_eight_hour_deadline_it_promises(
    caller: Caller,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
) -> None:
    """§6.4 fixes the window at forty-eight hours; nothing else read it back."""
    _sent_one(caller, identity_database, worker_engine, clock)
    revoked_at = clock.now()

    caller.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    with psycopg.connect(identity_database.admin_url, autocommit=True) as connection:
        expires_at = connection.execute(
            "SELECT expires_at FROM reminders.message_cleanup"
        ).fetchone()
    assert expires_at is not None
    assert expires_at[0] == revoked_at + MESSAGE_CLEANUP_TTL


def test_one_chat_that_will_not_answer_does_not_hold_the_whole_queue(
    caller: Caller,
    identity_database: Database,
    worker_engine: Engine,
    clock: FrozenClock,
) -> None:
    """§10 isolation reaches the cleanup drain too, not only the delivery loop.

    Before this the drain had no `try` of its own, so one raising deletion ended
    the pass — and `drive()` then waited a whole sweeper period before the next
    one.
    """
    _sent_one(caller, identity_database, worker_engine, clock)
    caller.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    exploding = FakeTelegram()

    def refuse(*, chat_id: int, message_id: int) -> bool:
        del chat_id, message_id
        raise RuntimeError("the chat is gone and the client says so loudly")

    exploding.delete_message = refuse  # type: ignore[method-assign]
    result = ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine), exploding, clock
    ).run_once()

    assert result.accounts_in_error == 1
    # The row survives its failure and still leaves at the TTL: the promise does
    # not depend on Telegram, or on this client, answering at all.
    assert _cleanup_rows(identity_database) == [(4242424242, 1001)]

    clock.advance(hours=49)
    ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine), exploding, clock
    ).run_once()

    assert _cleanup_rows(identity_database) == []
