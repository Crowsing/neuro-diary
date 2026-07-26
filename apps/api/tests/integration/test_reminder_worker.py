"""Delivery under the real `reminder_worker` role (§10 DoD).

Everything here runs through `worker_engine`, which authenticates as
`reminder_worker` rather than as `api_rw`. That is the point of the module: a
positive test under a wider role would pass on privileges the production
process does not have, and the isolation of §5.3 would be asserted nowhere.

Telegram is a fake implementing the port. The adapter's own behaviour — the
allowlist, 403, 429, `retry_after` — is asserted against the real request
builder in `tests/unit/test_telegram_bot_api.py`; here the fake exists so the
*worker's* ordering can be observed, including from inside the send.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import psycopg
import pytest
from sqlalchemy import Engine, text

from app.domain.reminders import (
    ATTEMPT_BUDGET,
    SendOutcome,
    SendReceipt,
    attempt_deadline,
)
from app.infra.db.engine import SqlReminderUnitOfWorkFactory
from app.workers.reminder_worker import ReminderWorker

from conftest import Caller, Database, FrozenClock, REPO_ROOT, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
REMINDERS_SHA = hashlib.sha256(
    (REGISTRY / "uk" / "telegram_reminders" / "0.9.md").read_bytes()
).hexdigest()

KYIV = "Europe/Kyiv"
CHAT_ID = 4242424242


class FakeTelegram:
    """A `TelegramDeliveryPort` that answers on demand and remembers everything.

    `before_send` runs *inside* the send, which is how insert-before-send is
    observed rather than inferred: the hook looks at the row while the message
    is notionally in flight.
    """

    def __init__(
        self,
        *,
        outcome: SendOutcome = SendOutcome.SENT,
        before_send: Callable[[], None] | None = None,
    ) -> None:
        self.outcome = outcome
        self.sent: list[int] = []
        #: Recorded rather than discarded: the deadline is the only channel
        #: through which §10's attempt budget and its nightfall bound reach
        #: Telegram, and a fake that threw it away left that wiring unasserted.
        self.deadlines: list[datetime] = []
        self.deleted: list[tuple[int, int]] = []
        self.deletion_succeeds = True
        self._before_send = before_send
        self._next_message_id = 1000

    def send_reminder(self, *, chat_id: int, deadline: datetime) -> SendReceipt:
        self.deadlines.append(deadline)
        if self._before_send is not None:
            self._before_send()
        self.sent.append(chat_id)
        if self.outcome is not SendOutcome.SENT:
            return SendReceipt(outcome=self.outcome)
        self._next_message_id += 1
        return SendReceipt(
            outcome=SendOutcome.SENT,
            message_id=self._next_message_id,
        )

    def delete_message(self, *, chat_id: int, message_id: int) -> bool:
        self.deleted.append((chat_id, message_id))
        return self.deletion_succeeds


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def worker(
    worker_engine: Engine,
    telegram: FakeTelegram,
    clock: FrozenClock,
) -> ReminderWorker:
    return ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine),
        telegram,
        clock,
    )


def _grant(caller: Caller) -> None:
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


def _arrange(
    caller: Caller,
    identity_database: Database,
    *,
    fire_at: datetime,
    local_time: str = "20:00",
    timezone_name: str = KYIV,
    enabled: bool = True,
) -> None:
    """Provision through the real grant path, then place the occurrence in time."""
    _grant(caller)
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "UPDATE reminders.reminder_schedule"
            " SET next_fire_at = %s, local_time = %s, tz = %s, enabled = %s",
            (fire_at, local_time, timezone_name, enabled),
        )


def _schedule(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT enabled, disabled_reason, disabled_at, next_fire_at"
                " FROM reminders.reminder_schedule"
            )
        ).one()
    return {
        "enabled": row.enabled,
        "disabled_reason": row.disabled_reason,
        "disabled_at": row.disabled_at,
        "next_fire_at": row.next_fire_at,
    }


def _account_id(engine: Engine) -> UUID:
    with engine.connect() as connection:
        return UUID(
            str(
                connection.execute(
                    text("SELECT account_id FROM reminders.reminder_schedule")
                ).scalar_one()
            )
        )


def _deliveries(engine: Engine) -> list[tuple[date, str, int | None]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT local_date, status, telegram_message_id"
                " FROM reminders.reminder_delivery ORDER BY local_date"
            )
        ).all()
    return [(row.local_date, row.status, row.telegram_message_id) for row in rows]


# ------------------------------------------------------- insert-before-send


def test_the_day_is_claimed_before_the_message_leaves(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """§10: the claim exists while the send is in flight, not after it.

    Observed from inside the send rather than inferred from the end state — an
    end-state assertion passes just as happily on send-then-insert, which is
    the ordering that duplicates a message after a crash.
    """
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _arrange(caller, identity_database, fire_at=fire_at)

    seen: list[tuple[date, str, int | None]] = []
    telegram = FakeTelegram(before_send=lambda: seen.extend(_deliveries(engine)))
    ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine), telegram, clock
    ).run_once()

    assert seen == [(date(2026, 7, 24), "pending", None)]
    assert _deliveries(engine) == [(date(2026, 7, 24), "sent", 1001)]


def test_a_crash_between_send_and_confirm_never_produces_a_second_message(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """The whole reason at-most-once is the policy (§10).

    The attempt dies after the message is out. The row stays `pending`, so the
    day is spoken for; the next pass must not send again, and the sweeper must
    close the row as `failed` rather than retry it.
    """
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _arrange(caller, identity_database, fire_at=fire_at)

    crashing = FakeTelegram()
    sent_messages: list[int] = []

    def explode(*, chat_id: int, deadline: datetime) -> SendReceipt:
        del deadline
        sent_messages.append(chat_id)
        raise RuntimeError("the process died holding the receipt")

    crashing.send_reminder = explode  # type: ignore[method-assign]
    factory = SqlReminderUnitOfWorkFactory(worker_engine)

    result = ReminderWorker(factory, crashing, clock).run_once()

    assert result.accounts_in_error == 1
    assert _deliveries(engine) == [(date(2026, 7, 24), "pending", None)]

    # A second pass, one minute later: the day is claimed, so nothing goes out.
    clock.advance(minutes=1)
    healthy = FakeTelegram()
    ReminderWorker(factory, healthy, clock).run_once()

    assert healthy.sent == []
    assert _deliveries(engine) == [(date(2026, 7, 24), "pending", None)]

    # Past the sweeper threshold the claim is settled — as `failed`, never as a
    # second attempt.
    clock.advance(minutes=16)
    swept = FakeTelegram()
    result = ReminderWorker(factory, swept, clock).run_once()

    assert result.swept == 1
    assert swept.sent == []
    assert _deliveries(engine) == [(date(2026, 7, 24), "failed", None)]
    assert len(sent_messages) == 1


def test_a_second_worker_cannot_claim_the_same_day(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """The primary key is the lease; two passes over one occurrence send once."""
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _arrange(caller, identity_database, fire_at=fire_at)
    factory = SqlReminderUnitOfWorkFactory(worker_engine)

    first = FakeTelegram()
    ReminderWorker(factory, first, clock).run_once()

    # Put the schedule back on the clock, as a stale replica or a bad restore
    # would, and run again.
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "UPDATE reminders.reminder_schedule SET next_fire_at = %s", (fire_at,)
        )
    second = FakeTelegram()
    ReminderWorker(factory, second, clock).run_once()

    assert len(first.sent) == 1
    assert second.sent == []
    assert _deliveries(engine) == [(date(2026, 7, 24), "sent", 1001)]


# ------------------------------------------------------------- quiet and stale


def test_a_schedule_delayed_past_ten_is_skipped_quiet(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
    telegram: FakeTelegram,
) -> None:
    """§10 DoD verbatim: 21:30 delayed by forty minutes sends nothing."""
    fire_at = datetime(2026, 7, 24, 18, 30, tzinfo=UTC)  # 21:30 Kyiv
    _arrange(caller, identity_database, fire_at=fire_at, local_time="21:30")
    clock.set(fire_at + timedelta(minutes=40))  # 22:10 Kyiv

    result = worker.run_once()

    assert result.skipped_quiet == 1
    assert telegram.sent == []
    assert _deliveries(engine) == [(date(2026, 7, 24), "skipped_quiet", None)]
    # The schedule moved on rather than staying due for ever.
    assert _schedule(engine)["next_fire_at"] == datetime(
        2026, 7, 25, 18, 30, tzinfo=UTC
    )


def test_catch_up_on_the_sixty_first_minute_is_stale(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
    telegram: FakeTelegram,
) -> None:
    fire_at = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)  # 17:00 Kyiv
    _arrange(caller, identity_database, fire_at=fire_at, local_time="17:00")
    clock.set(fire_at + timedelta(minutes=61))

    result = worker.run_once()

    assert result.skipped_stale == 1
    assert telegram.sent == []
    assert _deliveries(engine) == [(date(2026, 7, 24), "skipped_stale", None)]


def test_catch_up_on_the_sixtieth_minute_still_sends(
    caller: Caller,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
    telegram: FakeTelegram,
) -> None:
    """The other side of the boundary: without it the rule could refuse everything."""
    fire_at = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)
    _arrange(caller, identity_database, fire_at=fire_at, local_time="17:00")
    clock.set(fire_at + timedelta(minutes=60))

    result = worker.run_once()

    assert result.sent == 1
    assert telegram.sent == [CHAT_ID]


def test_a_paused_schedule_is_never_claimed(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
    telegram: FakeTelegram,
) -> None:
    """`enabled = false` is the pause of §10; nothing is sent and nothing claimed."""
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    _arrange(caller, identity_database, fire_at=fire_at, enabled=False)
    clock.set(fire_at)

    result = worker.run_once()

    assert result.sent == 0
    assert telegram.sent == []
    assert _deliveries(engine) == []


# ------------------------------------------------------------------------ DST


@pytest.mark.parametrize(
    ("fire_at", "expected", "interval"),
    [
        # §10 fixtures, on the worker's own path rather than in the domain.
        (
            datetime(2026, 3, 28, 18, 0, tzinfo=UTC),
            datetime(2026, 3, 29, 17, 0, tzinfo=UTC),
            timedelta(hours=23),
        ),
        (
            datetime(2026, 10, 24, 17, 0, tzinfo=UTC),
            datetime(2026, 10, 25, 18, 0, tzinfo=UTC),
            timedelta(hours=25),
        ),
    ],
)
def test_the_worker_reschedules_across_both_kyiv_transitions(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
    fire_at: datetime,
    expected: datetime,
    interval: timedelta,
) -> None:
    """The wall clock stays at 20:00 while the UTC interval is 23 or 25 hours."""
    _arrange(caller, identity_database, fire_at=fire_at)
    clock.set(fire_at)

    worker.run_once()

    stored = _schedule(engine)
    assert stored["next_fire_at"] == expected
    assert expected - fire_at == interval


def test_a_schedule_at_a_nonexistent_local_time_still_moves(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
) -> None:
    """Kyiv skips 03:00–04:00 on 2026-03-29; the worker must not stall on it.

    03:30 is inside quiet hours, so nothing is sent — the assertion that
    matters is that the schedule advances to the first valid instant instead of
    staying due for ever, which is what an added `timedelta` would produce.
    """
    fire_at = datetime(2026, 3, 28, 1, 30, tzinfo=UTC)  # 03:30 Kyiv
    _arrange(caller, identity_database, fire_at=fire_at, local_time="03:30")
    clock.set(fire_at)

    worker.run_once()

    assert _schedule(engine)["next_fire_at"] == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)


def test_an_unresolvable_zone_costs_only_its_own_account(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
    telegram: FakeTelegram,
) -> None:
    """§10: per-account isolation, or one row costs the whole installation a day.

    The second account is planted directly because the grant path validates the
    zone — which is the point: the bad row can only arrive from outside the API,
    and that is exactly when the worker must survive it. Surviving is not
    enough on its own; `test_an_unresolvable_zone_leaves_the_due_index_instead_of_starving_it`
    covers the other half, that the row also stops coming back.
    """
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    _arrange(caller, identity_database, fire_at=fire_at)
    clock.set(fire_at)
    with psycopg.connect(identity_database.admin_url, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO diary.account (id, status)"
            " VALUES ('11111111-1111-1111-1111-111111111111', 'active')"
        )
        connection.execute(
            "INSERT INTO reminders.reminder_schedule"
            " (account_id, telegram_chat_id, tz, local_time, enabled, next_fire_at)"
            " VALUES ('11111111-1111-1111-1111-111111111111', 555,"
            " 'Mars/Olympus', TIME '20:00', true, %s)",
            (fire_at,),
        )

    result = worker.run_once()

    assert result.quarantined == 1
    assert result.sent == 1
    assert telegram.sent == [CHAT_ID]
    assert (date(2026, 7, 24), "sent", 1001) in _deliveries(engine)


# ------------------------------------------------------------- Telegram refusals


def test_a_block_switches_the_schedule_off_and_starts_the_streak(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """§10: 403 → `enabled = false`, `disabled_reason = 'bot_blocked'`.

    And nothing else: the consent is still active, because the reconciler is
    the only thing allowed to touch it and only after fourteen days.
    """
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _arrange(caller, identity_database, fire_at=fire_at)

    telegram = FakeTelegram(outcome=SendOutcome.BLOCKED)
    result = ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine), telegram, clock
    ).run_once()

    assert result.blocked == 1
    stored = _schedule(engine)
    assert stored["enabled"] is False
    assert stored["disabled_reason"] == "bot_blocked"
    assert stored["disabled_at"] == fire_at
    assert _deliveries(engine) == [(date(2026, 7, 24), "failed", None)]
    assert caller.get("/v1/consents").json()["consents"][0]["kind"] == (
        "telegram_reminders"
    )


def test_recording_a_block_twice_does_not_restart_the_streak(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """Idempotence of the primitive, asserted where it is actually reachable.

    Through the worker a second 403 cannot happen: the first one sets
    `enabled = false`, and `ck_reminder_schedule_disabled_state` forbids putting
    that back to true while the reason stands — so the schedule is not claimed
    again until a `PUT` acknowledges the block, which clears the reason and
    *should* start a fresh streak.

    What remains reachable is a retried transaction, and the guard is what makes
    that harmless. Asserting it here rather than staging an end-to-end scenario
    the schema forbids keeps the test honest about which path it covers.
    """
    blocked_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(blocked_at)
    _arrange(caller, identity_database, fire_at=blocked_at)
    factory = SqlReminderUnitOfWorkFactory(worker_engine)
    ReminderWorker(factory, FakeTelegram(outcome=SendOutcome.BLOCKED), clock).run_once()

    account_id = _account_id(engine)
    with factory() as unit:
        unit.reminders.record_block(account_id, now=blocked_at + timedelta(days=3))
        unit.commit()

    assert _schedule(engine)["disabled_at"] == blocked_at


def test_acknowledging_a_block_starts_the_streak_again(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """«Поспіль» means what it says: an acknowledgement is a break in the run.

    The user answers the block by switching reminders back on, the bot turns
    out to be blocked still, and the fourteen days start from that second 403 —
    not from the first one, which she has already responded to.
    """
    first_block = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(first_block)
    _arrange(caller, identity_database, fire_at=first_block)
    factory = SqlReminderUnitOfWorkFactory(worker_engine)
    ReminderWorker(factory, FakeTelegram(outcome=SendOutcome.BLOCKED), clock).run_once()

    clock.advance(days=3)
    body = {"time": "20:00", "timezone": KYIV}
    assert (
        caller.put("/v1/reminders/settings", {"enabled": False, **body}).status_code
        == 200
    )
    assert (
        caller.put("/v1/reminders/settings", {"enabled": True, **body}).status_code
        == 200
    )

    second_block = clock.now()
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "UPDATE reminders.reminder_schedule SET next_fire_at = %s", (second_block,)
        )
    ReminderWorker(factory, FakeTelegram(outcome=SendOutcome.BLOCKED), clock).run_once()

    assert _schedule(engine)["disabled_at"] == second_block


def test_a_refusal_that_is_not_a_block_changes_neither_schedule_nor_consent(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """§10 DoD: 400 and 5xx move nothing but the delivery row."""
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _arrange(caller, identity_database, fire_at=fire_at)

    result = ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine),
        FakeTelegram(outcome=SendOutcome.FAILED),
        clock,
    ).run_once()

    assert result.failed == 1
    assert result.blocked == 0
    stored = _schedule(engine)
    assert stored["enabled"] is True
    assert stored["disabled_reason"] is None
    assert stored["disabled_at"] is None
    assert _deliveries(engine) == [(date(2026, 7, 24), "failed", None)]
    assert len(caller.get("/v1/consents").json()["consents"]) == 1


# ----------------------------------------------------------------- the grants


def test_the_worker_role_is_denied_every_table_in_diary(
    identity_database: Database,
) -> None:
    """§6.3 and §5.3: the process holding `BOT_TOKEN` cannot read the diary."""
    denied = (
        "SELECT 1 FROM diary.account LIMIT 1",
        "SELECT 1 FROM diary.consent LIMIT 1",
        "SELECT 1 FROM diary.vault_record LIMIT 1",
        "SELECT 1 FROM diary.telegram_identity LIMIT 1",
        "SELECT 1 FROM diary.outbox LIMIT 1",
    )
    with psycopg.connect(identity_database.worker_url, autocommit=True) as worker:
        for statement in denied:
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
                worker.execute(statement)
            assert error.value.sqlstate == "42501"


def test_the_worker_role_cannot_delete_a_delivery(
    identity_database: Database,
) -> None:
    """§6.3 keeps the 14-day sweep in `outbox_dispatcher`, under `api_rw`.

    The negative test exists so the first person who needs housekeeping in the
    worker cannot quietly repair the GRANT and dissolve the isolation with it.
    """
    with psycopg.connect(identity_database.worker_url, autocommit=True) as worker:
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
            worker.execute("DELETE FROM reminders.reminder_delivery")
        assert error.value.sqlstate == "42501"


def test_the_worker_role_cannot_fill_the_cleanup_queue(
    identity_database: Database,
) -> None:
    """§5.3: the worker drains the queue; only `api_rw` may fill it."""
    with psycopg.connect(identity_database.worker_url, autocommit=True) as worker:
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
            worker.execute(
                "INSERT INTO reminders.message_cleanup"
                " (account_id, chat_id, message_id, expires_at)"
                " VALUES ('11111111-1111-1111-1111-111111111111', 1, 1, now())"
            )
        assert error.value.sqlstate == "42501"


def test_the_whole_cycle_is_reachable_under_the_worker_role(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
    telegram: FakeTelegram,
) -> None:
    """The positive half: claim, send, confirm, reschedule — all as the worker.

    Without it the negative tests above would pass on a role with no privileges
    at all, and the isolation would be indistinguishable from a broken worker.
    """
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    _arrange(caller, identity_database, fire_at=fire_at)
    clock.set(fire_at)

    result = worker.run_once()

    assert result == type(result)(
        sent=1,
        skipped_quiet=0,
        skipped_stale=0,
        failed=0,
        blocked=0,
        quarantined=0,
        retracted=0,
        swept=0,
        messages_removed=0,
        accounts_in_error=0,
    )
    assert telegram.sent == [CHAT_ID]
    assert _deliveries(engine) == [(date(2026, 7, 24), "sent", 1001)]
    assert _schedule(engine)["next_fire_at"] == datetime(2026, 7, 25, 17, 0, tzinfo=UTC)


# ------------------------------------------- what the review found missing


def _plant_second_account(
    identity_database: Database,
    *,
    fire_at: datetime,
    chat_id: int = 555,
    timezone_name: str = KYIV,
    local_time: str = "20:00",
    account_id: str = "22222222-2222-2222-2222-222222222222",
) -> None:
    """A second real account, so a batch is a batch rather than a special case.

    Planted directly because the grant path binds one Telegram identity per
    signed initData, and what these tests need is two rows in one scan.
    """
    with psycopg.connect(identity_database.admin_url, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO diary.account (id, status) VALUES (%s, 'active')",
            (account_id,),
        )
        connection.execute(
            "INSERT INTO reminders.reminder_schedule"
            " (account_id, telegram_chat_id, tz, local_time, enabled, next_fire_at)"
            " VALUES (%s, %s, %s, %s, true, %s)",
            (account_id, chat_id, timezone_name, local_time, fire_at),
        )


def test_the_attempt_deadline_is_what_reaches_telegram(
    caller: Caller,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
    telegram: FakeTelegram,
) -> None:
    """§10's budget and its nightfall bound travel in one value, or in none.

    The worker computes `attempt_deadline`; the adapter is the only thing that
    reads it. Nothing else in the suite observes that hand-off, so replacing it
    with a bare `now` used to leave every test green while killing the single
    retry §10 permits.
    """
    fire_at = datetime(2026, 7, 24, 15, 0, tzinfo=UTC)  # 18:00 Kyiv
    _arrange(caller, identity_database, fire_at=fire_at, local_time="18:00")
    clock.set(fire_at)

    worker.run_once()

    assert telegram.deadlines == [
        attempt_deadline(started_at=fire_at, timezone_name=KYIV)
    ]
    assert telegram.deadlines[0] == fire_at + ATTEMPT_BUDGET


def test_the_deadline_is_cut_short_by_nightfall(
    caller: Caller,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
    telegram: FakeTelegram,
) -> None:
    """A `retry_after` may not carry a message into the quiet hours (§10)."""
    fire_at = datetime(2026, 7, 24, 18, 55, tzinfo=UTC)  # 21:55 Kyiv
    _arrange(caller, identity_database, fire_at=fire_at, local_time="21:55")
    clock.set(fire_at)

    worker.run_once()

    assert telegram.deadlines == [datetime(2026, 7, 24, 19, 0, tzinfo=UTC)]
    assert telegram.deadlines[0] < fire_at + ATTEMPT_BUDGET


def test_a_slow_batch_still_respects_the_quiet_hours_of_the_later_accounts(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """§10: the guard is on the **actual** send time, not on the batch start.

    Two accounts due at 21:30 Kyiv; the first send takes forty minutes. With one
    `now` for the whole pass the second message goes out at 22:10 and is
    recorded `sent` — the scenario §10's DoD names verbatim, invisible to any
    single-account test because there batch start *is* send time.
    """
    fire_at = datetime(2026, 7, 24, 18, 30, tzinfo=UTC)  # 21:30 Kyiv
    _arrange(caller, identity_database, fire_at=fire_at, local_time="21:30")
    _plant_second_account(identity_database, fire_at=fire_at, local_time="21:30")
    clock.set(fire_at)

    slow = FakeTelegram(before_send=lambda: clock.advance(minutes=40))
    result = ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine), slow, clock
    ).run_once()

    assert result.sent == 1
    assert result.skipped_quiet == 1
    assert len(slow.sent) == 1
    statuses = {status for _, status, _ in _deliveries(engine)}
    assert statuses == {"sent", "skipped_quiet"}


def test_the_claim_is_named_by_the_scheduled_day_not_by_today(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
) -> None:
    """The idempotency key is the occurrence's own day (§10).

    Deriving it from `now` instead looks harmless and eats the *next* day: a
    23:50 occurrence picked up at 00:20 would claim tomorrow's date, and
    tomorrow's real reminder would then lose the primary key race in silence.
    """
    fire_at = datetime(2026, 7, 24, 20, 50, tzinfo=UTC)  # 23:50 Kyiv
    _arrange(caller, identity_database, fire_at=fire_at, local_time="23:50")
    clock.set(fire_at + timedelta(minutes=30))  # 00:20 Kyiv, the next day

    worker.run_once()

    assert _deliveries(engine) == [(date(2026, 7, 24), "skipped_stale", None)]


def test_the_schedule_moves_on_even_when_the_day_was_already_claimed(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """Otherwise the row stays due for ever and is re-read on every pass.

    The losing worker of a two-worker race must still advance `next_fire_at`;
    the comment in the code says so, and until this test nothing checked it.
    """
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _arrange(caller, identity_database, fire_at=fire_at)
    factory = SqlReminderUnitOfWorkFactory(worker_engine)
    ReminderWorker(factory, FakeTelegram(), clock).run_once()

    # Re-arm the row as a stale replica or a restore would, then run again: the
    # day is already claimed, so nothing is sent — but the clock must still move.
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "UPDATE reminders.reminder_schedule SET next_fire_at = %s", (fire_at,)
        )
    loser = FakeTelegram()
    ReminderWorker(factory, loser, clock).run_once()

    assert loser.sent == []
    assert _schedule(engine)["next_fire_at"] == datetime(2026, 7, 25, 17, 0, tzinfo=UTC)


def test_a_consent_withdrawn_after_the_scan_stops_the_message(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """The window the batch scan opens, and the reason the schedule is re-read.

    Two accounts in one batch. While the first is talking to Telegram the user
    of the second withdraws her consent — which is minutes, not microseconds,
    once a batch is a hundred accounts deep. Without the second read the worker
    claims a day for an erased account and sends her the message she just asked
    to stop.
    """
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _plant_second_account(identity_database, fire_at=fire_at)
    _arrange(caller, identity_database, fire_at=fire_at)

    def withdraw() -> None:
        response = caller.post("/v1/consents/revoke", {"kind": "telegram_reminders"})
        assert response.status_code == 200, response.text

    telegram = FakeTelegram(before_send=withdraw)
    result = ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine), telegram, clock
    ).run_once()

    # The planted account is scanned first (lower `next_fire_at` tie broken by
    # insertion); its send withdraws the other one's consent.
    assert telegram.sent == [555]
    assert result.sent == 1
    assert _deliveries(engine) == [(date(2026, 7, 24), "sent", 1001)]
    # Load-bearing: the withdrawn account has to be *skipped*, not to blow up
    # inside the isolation handler. Without this the guard could be deleted and
    # the assertions above would still hold — the `AttributeError` on a missing
    # row would be swallowed as an account error and nothing would notice.
    assert result.accounts_in_error == 0


def test_a_message_sent_into_an_erased_account_is_taken_straight_back(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """The one window a re-read cannot close, and what closes it instead.

    The erasure commits while the message is in flight: the claim disappears,
    `finish_occurrence` moves nothing, and the only trace of that message is in
    the worker's own memory. `message_cleanup` is unreachable from here (§6.3
    gives it `INSERT` to `api_rw` alone), so the retraction happens on the spot.
    """
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _arrange(caller, identity_database, fire_at=fire_at)

    def withdraw() -> None:
        assert (
            caller.post(
                "/v1/consents/revoke", {"kind": "telegram_reminders"}
            ).status_code
            == 200
        )

    telegram = FakeTelegram(before_send=withdraw)
    result = ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine), telegram, clock
    ).run_once()

    assert telegram.sent == [CHAT_ID]
    assert result.retracted == 1
    assert telegram.deleted == [(CHAT_ID, 1001)]
    # Nothing survives the erasure: no orphan delivery row for an account that
    # no longer has a consent (§4.4).
    assert _deliveries(engine) == []


def test_a_late_attempt_never_overwrites_what_the_sweeper_settled(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """The `status = 'pending'` guard, staged rather than described.

    A batch can outlive the sweeper threshold, so the sweeper genuinely meets a
    live attempt. Without the guard the returning attempt would write `sent`
    over a row the sweeper had already given up on — and §6.4 would then hold a
    message id for a delivery the system had declared failed.
    """
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _arrange(caller, identity_database, fire_at=fire_at)
    factory = SqlReminderUnitOfWorkFactory(worker_engine)

    crashing = FakeTelegram()

    def explode(*, chat_id: int, deadline: datetime) -> SendReceipt:
        del chat_id, deadline
        raise RuntimeError("the attempt is still out there")

    crashing.send_reminder = explode  # type: ignore[method-assign]
    ReminderWorker(factory, crashing, clock).run_once()

    clock.advance(minutes=16)
    ReminderWorker(factory, FakeTelegram(), clock).run_once()
    assert _deliveries(engine) == [(date(2026, 7, 24), "failed", None)]

    # The attempt finally returns and tries to report success.
    with factory() as unit:
        moved = unit.reminders.finish_occurrence(
            _account_id(engine),
            local_date=date(2026, 7, 24),
            status="sent",
            telegram_message_id=4242,
        )
        unit.commit()

    assert moved == 0
    assert _deliveries(engine) == [(date(2026, 7, 24), "failed", None)]


def test_an_unresolvable_zone_leaves_the_due_index_instead_of_starving_it(
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker: ReminderWorker,
) -> None:
    """Isolation that kept the row in the scan would prevent the crash only.

    A hundred such rows fill `CLAIM_BATCH` on every pass and starve every real
    account. The row becomes a plain pause — no `disabled_reason`, so the
    reconciler of §10 can never turn corrupt data into a withdrawn consent.
    """
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _plant_second_account(
        identity_database, fire_at=fire_at, timezone_name="Mars/Olympus"
    )

    result = worker.run_once()

    assert result.quarantined == 1
    assert result.accounts_in_error == 0
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT enabled, disabled_reason FROM reminders.reminder_schedule")
        ).one()
    assert row.enabled is False
    assert row.disabled_reason is None

    assert worker.run_once().quarantined == 0


def test_a_broken_sweep_does_not_cost_the_batch_its_deliveries(
    caller: Caller,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """The sweeper runs first, so an exception there is the widest outage."""
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _arrange(caller, identity_database, fire_at=fire_at)

    telegram = FakeTelegram()
    worker = ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine), telegram, clock
    )
    broken = SqlReminderUnitOfWorkFactory(worker_engine)
    original = broken.__call__

    class Exploding:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> object:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("the sweep transaction will not start")
            return original()

    worker._unit_of_work = Exploding()  # type: ignore[assignment]

    result = worker.run_once()

    assert result.swept == 0
    assert result.accounts_in_error == 1
    assert telegram.sent == [CHAT_ID]


def test_a_schedule_paused_after_the_scan_sends_nothing(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
    worker_engine: Engine,
) -> None:
    """The other half of the second read: `enabled` can change inside the window.

    Withdrawing a consent removes the row; pausing leaves it there with
    `enabled = false`. A guard that only checked for a missing row would send
    to somebody who had just switched reminders off — a state the scan cannot
    see because it happened after it.
    """
    fire_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    clock.set(fire_at)
    _plant_second_account(identity_database, fire_at=fire_at)
    _arrange(caller, identity_database, fire_at=fire_at)

    def pause() -> None:
        response = caller.put(
            "/v1/reminders/settings",
            {"enabled": False, "time": "20:00", "timezone": KYIV},
        )
        assert response.status_code == 200, response.text

    telegram = FakeTelegram(before_send=pause)
    result = ReminderWorker(
        SqlReminderUnitOfWorkFactory(worker_engine), telegram, clock
    ).run_once()

    assert telegram.sent == [555]
    assert result.sent == 1
    assert result.accounts_in_error == 0
    assert _deliveries(engine) == [(date(2026, 7, 24), "sent", 1001)]
