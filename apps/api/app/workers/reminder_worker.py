"""Reminder delivery: the at-most-once worker of §10.

**The order of the three writes is the whole design.** An occurrence is claimed
in `reminder_delivery` *before* anything is sent, because its primary key
`(account_id, local_date)` is the idempotency key: two workers, or one worker
twice, cannot both claim a day. Only then does the message go out, and only
then is the row closed. A crash anywhere in between leaves `pending`, and a
`pending` row older than the sweeper threshold becomes `failed` — never a
second send. §10 states the trade plainly and this module keeps it: one missed
neutral reminder is better than a duplicate.

**Nothing here holds a row lock across a network call.** `claim_due` takes
`FOR UPDATE SKIP LOCKED` inside its own transaction and releases it at the
commit; the lease that actually protects the send is the claimed row, which
survives a crash rather than evaporating with the connection.

**That released lock is why the schedule is read twice.** The scan hands back a
list, and this account's turn may come minutes later — after every earlier
account has talked to Telegram. In that window the user may have withdrawn her
consent, and `ErasureService` will have taken both reminder tables with it. So
the claim transaction re-reads the row under `FOR UPDATE` and stops if it is
gone: without that, the worker inserts a delivery row for an account that has
no consent and no schedule, and then sends her a message she asked to stop.

One window cannot be closed by a read — the send itself. If the erasure commits
while the message is in flight, the claim row disappears and `finish_occurrence`
moves nothing. That is the signal, and the answer to it is to **retract the
message immediately**: the worker still holds the chat id and the message id,
and `message_cleanup` cannot be reached from here (§6.3 gives it `INSERT` to
`api_rw` only). Retracting is the one action that leaves nothing behind.

**Every account is on its own, and so is every swept row and every retraction.**
One unresolvable timezone, one Telegram refusal, one row that will not update —
each is caught alone, because the alternative is that the whole installation
loses a day of reminders over one row (§10). An unresolvable zone additionally
leaves the due index rather than being re-read for ever; isolation that
prevented the crash and kept the starvation would be isolation in name only.

**Time is read per account, never per batch.** The quiet-hours guard of §10 is
about the *actual* moment of sending, and a batch of a hundred accounts can
span more than an hour of Telegram round-trips. One `now` for the whole pass
would mean the hundredth message is judged by when the first one started —
which is exactly how a reminder arrives at ten past ten at night.

The process runs under `reminder_worker`, whose grants reach the `reminders`
schema and nothing else. The type of `ReminderUnitOfWork` is that boundary made
mechanical: there is no `unit.consents` here to reach for.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.identity import UnknownTimezone
from app.domain.records import DueReminder
from app.domain.reminders import (
    MESSAGE_CLEANUP_TTL,
    SWEEPER_THRESHOLD,
    Decision,
    DeliveryStatus,
    SendOutcome,
    attempt_deadline,
    decide,
    local_date_of,
    next_fire_at,
)
from app.infra.logging import get_logger
from app.services.ports import (
    Clock,
    ReminderUnitOfWorkFactory,
    TelegramDeliveryPort,
)

CLAIM_BATCH = 100
CLEANUP_BATCH = 100

_STATUS_FOR_SKIP = {
    Decision.SKIP_QUIET: DeliveryStatus.SKIPPED_QUIET,
    Decision.SKIP_STALE: DeliveryStatus.SKIPPED_STALE,
}


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    sent: int
    skipped_quiet: int
    skipped_stale: int
    failed: int
    blocked: int
    #: Schedules taken out of the due index because their zone will not resolve.
    quarantined: int
    #: Messages sent into a chat whose account was erased mid-flight, and taken
    #: straight back out again.
    retracted: int
    swept: int
    messages_removed: int
    accounts_in_error: int


class ReminderWorker:
    def __init__(
        self,
        unit_of_work: ReminderUnitOfWorkFactory,
        telegram: TelegramDeliveryPort,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._telegram = telegram
        self._clock = clock

    def run_once(self, *, batch: int = CLAIM_BATCH) -> DeliveryResult:
        tally = _Tally()
        swept = self._sweep(tally)

        for due in self._due(batch=batch):
            try:
                self._deliver(due, tally=tally)
            except Exception:  # noqa: BLE001 - §10 isolation, see module docstring
                tally.accounts_in_error += 1
                # No identifier, no zone, no chat: §11 allows none of them, and
                # the exception text can carry all three.
                get_logger().warning("reminder_account_failed")

        removed = self._drain_cleanup(tally)
        return DeliveryResult(
            sent=tally.sent,
            skipped_quiet=tally.skipped_quiet,
            skipped_stale=tally.skipped_stale,
            failed=tally.failed,
            blocked=tally.blocked,
            quarantined=tally.quarantined,
            retracted=tally.retracted,
            swept=swept,
            messages_removed=removed,
            accounts_in_error=tally.accounts_in_error,
        )

    # ------------------------------------------------------------- the sweeper

    def _sweep(self, tally: _Tally) -> int:
        """§10: a claim nobody closed becomes `failed`, and is never re-sent.

        It runs *before* the due scan on purpose. A row left `pending` by a
        crashed attempt already holds that day's claim, so closing it first
        means the day is settled before anything looks at the schedule again —
        and the settled state is `failed`, not another attempt.

        Its own `try` for the same reason every account has one: this runs
        first, so an exception here would end the pass before a single delivery
        was attempted — the widest version of the outage §10 forbids.
        """
        now = self._clock.now()
        try:
            with self._unit_of_work() as unit:
                stale = unit.reminders.stale_pending(older_than=now - SWEEPER_THRESHOLD)
                for occurrence in stale:
                    unit.reminders.finish_occurrence(
                        occurrence.account_id,
                        local_date=occurrence.local_date,
                        status=DeliveryStatus.FAILED.value,
                    )
                unit.commit()
        except Exception:  # noqa: BLE001 - a stuck sweep must not cost the batch
            tally.accounts_in_error += 1
            get_logger().warning("reminder_sweep_failed")
            return 0
        return len(stale)

    # --------------------------------------------------------------- delivery

    def _due(self, *, batch: int) -> list[DueReminder]:
        """A candidate list, not a lease.

        The lock taken here is released at this commit, which is what keeps a
        slow Telegram call from pinning rows. Two workers can therefore see one
        account — and both will try to claim its occurrence, where exactly one
        of them wins on the primary key. What the released lock costs is a
        second read per account; `_deliver` pays it.
        """
        with self._unit_of_work() as unit:
            claimed = unit.reminders.claim_due(moment=self._clock.now(), limit=batch)
            unit.commit()
        return claimed

    def _deliver(self, due: DueReminder, *, tally: _Tally) -> None:
        # Read per account rather than per batch: by the time this account's
        # turn comes, every earlier one has been to Telegram and back.
        now = self._clock.now()

        with self._unit_of_work() as unit:
            current = unit.reminders.lock_schedule(due.account_id)
            if current is None or not current.enabled:
                # Revoked, erased or paused since the scan. Not an error, and
                # emphatically not something to send.
                return
            try:
                decision = decide(
                    scheduled_for=current.next_fire_at,
                    now=now,
                    timezone_name=current.timezone_name,
                )
                local_date = local_date_of(current.next_fire_at, current.timezone_name)
                following = next_fire_at(current.timezone_name, current.local_time, now)
            except UnknownTimezone:
                unit.reminders.quarantine(due.account_id, now=now)
                unit.commit()
                tally.quarantined += 1
                get_logger().warning("reminder_schedule_quarantined")
                return

            claimed = unit.reminders.claim_occurrence(
                due.account_id,
                local_date=local_date,
                now=now,
            )
            if claimed and decision is not Decision.SEND:
                unit.reminders.finish_occurrence(
                    due.account_id,
                    local_date=local_date,
                    status=_STATUS_FOR_SKIP[decision].value,
                )
            # The schedule moves on whether or not this pass claimed the day.
            # Without that, an occurrence already settled by another worker
            # would keep its `next_fire_at` in the past and be re-read on every
            # single pass, for ever.
            unit.reminders.reschedule(
                due.account_id,
                next_fire_at=following,
                now=now,
            )
            unit.commit()

        if not claimed:
            return
        if decision is Decision.SKIP_QUIET:
            tally.skipped_quiet += 1
            return
        if decision is Decision.SKIP_STALE:
            tally.skipped_stale += 1
            return

        # Outside every transaction: this is the call that can take seconds, and
        # a connection held across it is a connection held for nothing.
        receipt = self._telegram.send_reminder(
            chat_id=current.telegram_chat_id,
            deadline=attempt_deadline(
                started_at=now,
                timezone_name=current.timezone_name,
            ),
        )

        with self._unit_of_work() as unit:
            if receipt.outcome is SendOutcome.SENT:
                closed = unit.reminders.finish_occurrence(
                    due.account_id,
                    local_date=local_date,
                    status=DeliveryStatus.SENT.value,
                    telegram_message_id=receipt.message_id,
                )
                tally.sent += 1
            else:
                closed = unit.reminders.finish_occurrence(
                    due.account_id,
                    local_date=local_date,
                    status=DeliveryStatus.FAILED.value,
                )
                tally.failed += 1
                if receipt.outcome is SendOutcome.BLOCKED:
                    # §10: the schedule goes off and the streak starts here. The
                    # consent is untouched — it is the reconciler that decides,
                    # and only after the block has stood for fourteen days.
                    unit.reminders.record_block(due.account_id, now=now)
                    tally.blocked += 1
            unit.commit()

        if closed == 0 and receipt.outcome is SendOutcome.SENT:
            # The claim vanished while the message was in flight, which on this
            # path means an erasure committed between the claim and the send.
            # Nothing in the database points at that message any more, and
            # `message_cleanup` is out of reach (§6.3 gives it `INSERT` to
            # `api_rw` alone), so the retraction happens here and now.
            self._retract(receipt.message_id, chat_id=current.telegram_chat_id)
            tally.retracted += 1

    def _retract(self, message_id: int | None, *, chat_id: int) -> None:
        if message_id is None:  # pragma: no cover - SENT always carries one
            return
        self._telegram.delete_message(chat_id=chat_id, message_id=message_id)

    # ---------------------------------------------------------------- cleanup

    def _drain_cleanup(self, tally: _Tally) -> int:
        """§6.4: take back what was sent, and forget it either way.

        Two rules that look like one. Telegram may refuse the deletion — the
        window a bot has to delete its own message is short — and the row goes
        regardless once its TTL passes, because the promise is that the chat id
        stops living here, not that the message was removed.

        Each entry is on its own, and the unconditional expiry sweep runs even
        if every one of them failed: it is the half of the promise that does not
        depend on Telegram agreeing to anything.
        """
        now = self._clock.now()
        removed = 0
        try:
            with self._unit_of_work() as unit:
                pending = unit.reminders.due_cleanups(limit=CLEANUP_BATCH)
                unit.commit()
        except Exception:  # noqa: BLE001 - the expiry sweep below still owes its run
            get_logger().warning("reminder_cleanup_scan_failed")
            pending = []

        for entry in pending:
            try:
                deleted = self._telegram.delete_message(
                    chat_id=entry.chat_id,
                    message_id=entry.message_id,
                )
                if not deleted:
                    continue
                with self._unit_of_work() as unit:
                    removed += unit.reminders.forget_cleanup(
                        entry.account_id,
                        message_id=entry.message_id,
                    )
                    unit.commit()
            except Exception:  # noqa: BLE001 - one chat must not hold the queue
                tally.accounts_in_error += 1
                get_logger().warning("reminder_cleanup_failed")

        with self._unit_of_work() as unit:
            unit.reminders.drop_expired_cleanups(moment=now)
            unit.commit()
        return removed


@dataclass
class _Tally:
    sent: int = 0
    skipped_quiet: int = 0
    skipped_stale: int = 0
    failed: int = 0
    blocked: int = 0
    quarantined: int = 0
    retracted: int = 0
    accounts_in_error: int = 0


#: Re-exported so a caller composing this worker does not have to reach into the
#: domain for the one constant it must agree with (§6.4).
CLEANUP_TTL = MESSAGE_CLEANUP_TTL
