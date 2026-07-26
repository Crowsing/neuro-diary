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

**Every account is on its own.** One unresolvable timezone, one Telegram
refusal, one row that will not update — each is caught around a single account,
because the alternative is that the whole installation loses a day of reminders
over one row (§10).

The process runs under `reminder_worker`, whose grants reach the `reminders`
schema and nothing else. The type of `ReminderUnitOfWork` is that boundary made
mechanical: there is no `unit.consents` here to reach for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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
        now = self._clock.now()
        swept = self._sweep(now)
        tally = _Tally()

        for due in self._due(now, batch=batch):
            try:
                self._deliver(due, now=now, tally=tally)
            except Exception:  # noqa: BLE001 - §10 isolation, see module docstring
                tally.accounts_in_error += 1
                # No identifier, no zone, no chat: §11 allows none of them, and
                # the exception text can carry all three.
                get_logger().warning("reminder_account_failed")

        removed = self._drain_cleanup(now)
        return DeliveryResult(
            sent=tally.sent,
            skipped_quiet=tally.skipped_quiet,
            skipped_stale=tally.skipped_stale,
            failed=tally.failed,
            blocked=tally.blocked,
            swept=swept,
            messages_removed=removed,
            accounts_in_error=tally.accounts_in_error,
        )

    # ------------------------------------------------------------- the sweeper

    def _sweep(self, now: datetime) -> int:
        """§10: a claim nobody closed becomes `failed`, and is never re-sent.

        It runs *before* the due scan on purpose. A row left `pending` by a
        crashed attempt already holds that day's claim, so closing it first
        means the day is settled before anything looks at the schedule again —
        and the settled state is `failed`, not another attempt.
        """
        with self._unit_of_work() as unit:
            stale = unit.reminders.stale_pending(older_than=now - SWEEPER_THRESHOLD)
            for occurrence in stale:
                unit.reminders.finish_occurrence(
                    occurrence.account_id,
                    local_date=occurrence.local_date,
                    status=DeliveryStatus.FAILED.value,
                )
            unit.commit()
        return len(stale)

    # --------------------------------------------------------------- delivery

    def _due(self, now: datetime, *, batch: int) -> list[DueReminder]:
        """A candidate list, not a lease.

        The lock taken here is released at this commit, which is what keeps a
        slow Telegram call from pinning rows. Two workers can therefore see one
        account — and both will try to claim its occurrence, where exactly one
        of them wins on the primary key.
        """
        with self._unit_of_work() as unit:
            claimed = unit.reminders.claim_due(moment=now, limit=batch)
            unit.commit()
        return claimed

    def _deliver(self, due: DueReminder, *, now: datetime, tally: _Tally) -> None:
        decision = decide(
            scheduled_for=due.next_fire_at,
            now=now,
            timezone_name=due.timezone_name,
        )
        local_date = local_date_of(due.next_fire_at, due.timezone_name)
        following = next_fire_at(due.timezone_name, due.local_time, now)

        with self._unit_of_work() as unit:
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
            chat_id=due.telegram_chat_id,
            deadline=attempt_deadline(
                started_at=now,
                timezone_name=due.timezone_name,
            ),
        )

        with self._unit_of_work() as unit:
            if receipt.outcome is SendOutcome.SENT:
                unit.reminders.finish_occurrence(
                    due.account_id,
                    local_date=local_date,
                    status=DeliveryStatus.SENT.value,
                    telegram_message_id=receipt.message_id,
                )
                tally.sent += 1
            else:
                unit.reminders.finish_occurrence(
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

    # ---------------------------------------------------------------- cleanup

    def _drain_cleanup(self, now: datetime) -> int:
        """§6.4: take back what was sent, and forget it either way.

        Two rules that look like one. Telegram may refuse the deletion — the
        window a bot has to delete its own message is short — and the row goes
        regardless once its TTL passes, because the promise is that the chat id
        stops living here, not that the message was removed.
        """
        removed = 0
        with self._unit_of_work() as unit:
            pending = unit.reminders.due_cleanups(moment=now, limit=CLEANUP_BATCH)
            unit.commit()

        for entry in pending:
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
    accounts_in_error: int = 0


#: Re-exported so a caller composing this worker does not have to reach into the
#: domain for the one constant it must agree with (§6.4).
CLEANUP_TTL = MESSAGE_CLEANUP_TTL
