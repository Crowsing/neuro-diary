"""Outbox dispatcher: the consumer of §4.4 and the TTL sweeper of §6.2.

It runs as `api_rw` for a reason that is not incidental: §6.3 deliberately
withholds `DELETE` on `reminder_delivery` from `reminder_worker`, so the
housekeeping of that table has to live in a process that is not the worker.

**Why the events need a consumer at all.** Every effect §4.4 attributes to a
revocation happens inside the revoking transaction — §9.8, §4.3 and §9.7 all
require it. So the dispatcher is not doing that work; it is the safety net for
the two moments where the work provably escapes a single transaction:

* an account that lost its last consent by the user's own decision must be
  erased right then, with the hard ceiling of 15 minutes (§4.3). `Housekeeper`
  only picks up the two *timeout* reasons, and only after 30 days — nobody at
  all picked up an account whose immediate erasure did not happen;
* the journal entry of an erasure is opened before the deletion and closed
  after it (§6.4), so a process that dies in between leaves `completed_at` NULL
  for good. §6.4 builds the promise "we re-run your deletion within 24 hours"
  on that column, and a permanently false negative there is a promise nobody
  can keep.

Both handlers are idempotent because redelivery is normal, and the claim, the
work and the `processed_at` write genuinely share one transaction — the claim is
taken per event inside it, so the row lock is a lease rather than a hint and a
crash before the commit returns the event to the queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.events import (
    ACCOUNT_ERASURE_REQUESTED,
    CONSENT_REVOKED,
    REMINDER_ERASURE_REQUESTED,
    SECURITY_RESET_REQUESTED,
    VAULT_ERASURE_REQUESTED,
)
from app.domain.identity import ConsentKind, ConsentPrecondition, RevokeReason
from app.domain.records import PendingEvent
from app.domain.reminders import BOT_BLOCKED_STREAK
from app.services.consent import ConsentService
from app.services.erasure import ErasureService
from app.services.ports import Clock, UnitOfWork, UnitOfWorkFactory

#: §6.2. Both are TTLs of service tables, and neither is the backup promise of
#: §6.4 — they are named here so nothing has to reach for a literal.
REMINDER_DELIVERY_TTL = timedelta(days=14)
OUTBOX_TTL = timedelta(days=30)
CLAIM_BATCH = 100


@dataclass(frozen=True, slots=True)
class DispatchResult:
    events_processed: int
    accounts_erased: int
    journals_confirmed: int
    deliveries_removed: int
    events_removed: int
    consents_timed_out: int


class OutboxDispatcher:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        erasure: ErasureService,
        consents: ConsentService,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._erasure = erasure
        self._consents = consents
        self._clock = clock

    def run_once(self) -> DispatchResult:
        now = self._clock.now()
        processed = 0
        erased: list[UUID] = []
        confirmed: list[UUID] = []
        cursor = 0

        for _ in range(CLAIM_BATCH):
            # One transaction per event, and the claim lives inside it. That is
            # what makes the row lock an actual lease: a second dispatcher skips
            # what this one holds, and a crash before the commit returns the
            # event to the queue. Claiming a batch in its own transaction would
            # release every lock before any work started, and `SKIP LOCKED`
            # would hand the next dispatcher the identical batch.
            #
            # One transaction per event also keeps an account whose erasure
            # cannot complete from costing every other event its turn.
            with self._unit_of_work() as unit:
                claimed = unit.outbox.claim(limit=1, after_id=cursor)
                if not claimed:
                    break
                event = claimed[0]
                # The cursor steps over anything this dispatcher leaves alone,
                # so an unhandled event neither blocks the queue nor repeats.
                cursor = event.id

                if event.event_type == CONSENT_REVOKED:
                    reference = self._erase_if_orphaned(unit, event, now=now)
                    if reference is not None:
                        erased.append(reference)
                elif event.event_type in (
                    ACCOUNT_ERASURE_REQUESTED,
                    VAULT_ERASURE_REQUESTED,
                    REMINDER_ERASURE_REQUESTED,
                    SECURITY_RESET_REQUESTED,
                ):
                    reference = _reference_of(event)
                    if reference is not None:
                        confirmed.append(reference)
                else:
                    # An event type this dispatcher has not been taught about is
                    # left in the queue rather than marked done. Phase 4 adds
                    # the 403 reconciler here; until then its events, if any,
                    # must survive rather than be swallowed.
                    continue
                if unit.outbox.mark_processed(event.id, at=now):
                    processed += 1
                unit.commit()

        # Journals are closed only after the transactions that own their
        # deletions committed. Confirming earlier would state that an erasure
        # finished while it could still be rolled back.
        for reference in [*erased, *confirmed]:
            self._erasure.confirm(reference, now=now)

        timed_out = self._reconcile_blocked_bots(now=now)

        with self._unit_of_work() as unit:
            deliveries = unit.schedules.delete_deliveries_before(
                now - REMINDER_DELIVERY_TTL
            )
            events = unit.outbox.delete_processed_before(now - OUTBOX_TTL)
            unit.commit()

        return DispatchResult(
            events_processed=processed,
            accounts_erased=len(erased),
            journals_confirmed=len(confirmed),
            deliveries_removed=deliveries,
            events_removed=events,
            consents_timed_out=timed_out,
        )

    def _reconcile_blocked_bots(self, *, now: datetime) -> int:
        """§10: a block that has stood for fourteen days reaches the consent.

        **Why this is a scan and not an event handler.** §10 points at the
        dispatcher's unknown-`event_type` branch, and the dispatcher is indeed
        where this belongs — but not as a consumer, because there is nobody to
        produce the event. The only process that learns about a 403 is the
        reminder worker, and §6.3 gives it zero privileges on `diary`, so it
        cannot write `diary.outbox` at all. An event would need either a wider
        role for the process holding `BOT_TOKEN` or a second writer with no
        source of truth. The state is already durable in
        `reminder_schedule.disabled_at`; reading it is cheaper than inventing a
        message about it. The branch stays as it was, still refusing to swallow
        an event it was not taught.

        **Why `disabled_reason` and never `enabled`.** A pause the user asked
        for is `enabled = false` with no reason and no `disabled_at`, and it
        must never cost her a consent however long it lasts (§10). The index
        this scan reads is partial on `disabled_reason IS NOT NULL`, so a paused
        schedule is not merely filtered out — it is not in the index.

        Revocation goes through `ConsentService` rather than through the
        repository so the cascade of §4.3 stays in one place: the reason is not
        the user's, so the account is *not* erased now — it gets the thirty-day
        window `Housekeeper` owns — while the schedule rows go immediately,
        because §4.4 forbids one outliving its consent.
        """
        with self._unit_of_work() as unit:
            overdue = unit.schedules.blocked_since(now - BOT_BLOCKED_STREAK)

        reconciled = 0
        for account_id in overdue:
            # One transaction per account, for the reason every other worker
            # gives: one account that cannot be revoked must not cost the rest
            # of them their deadline.
            with self._unit_of_work() as unit:
                try:
                    self._consents.revoke(
                        unit,
                        account_id=account_id,
                        kind=ConsentKind.TELEGRAM_REMINDERS,
                        now=now,
                        reason=RevokeReason.BOT_BLOCKED_TIMEOUT,
                    )
                except ConsentPrecondition:
                    # Already revoked — a redelivery, a race with the user, or a
                    # schedule row that outlived its consent. Nothing to do, and
                    # nothing worth failing the batch over.
                    continue
                unit.commit()
            reconciled += 1
        return reconciled

    def _erase_if_orphaned(
        self,
        unit: UnitOfWork,
        event: PendingEvent,
        *,
        now: datetime,
    ) -> UUID | None:
        """§4.3 safety net. Idempotent: a finished erasure leaves nothing to do.

        Three conditions, and each is a reason to do nothing rather than a
        formality: the account row may already be gone (the revocation erased it
        in its own transaction), it may still hold a consent (only one of
        several was revoked), or it may have lost its last consent for a reason
        that is not the user's decision — and that one gets the 30-day window of
        §4.3, which belongs to `Housekeeper` and not here.
        """
        account_id = _account_of(event)
        if account_id is None:
            return None
        if not unit.accounts.lock(account_id):
            return None
        if unit.consents.active_kinds(account_id):
            return None
        if unit.consents.latest_revoke_reason(account_id) is not RevokeReason.USER:
            return None
        return self._erasure.erase(unit, account_id=account_id, now=now)


def _account_of(event: PendingEvent) -> UUID | None:
    """The identifier §6.4 says lives inside the payload.

    Migration 0004 constrains it to be present and to be a string, so a failure
    to parse here means a row got past the constraint — worth skipping, never
    worth failing the batch.
    """
    return _uuid_field(event, "account_id")


def _reference_of(event: PendingEvent) -> UUID | None:
    return _uuid_field(event, "erasure_reference")


def _uuid_field(event: PendingEvent, name: str) -> UUID | None:
    raw = event.payload.get(name)
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None
