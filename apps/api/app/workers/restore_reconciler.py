"""Reconciliation of a restored database against the erasure journal (§6.4).

A restore puts back rows the service had already erased. The journal is the only
record of *which* ones, because it lives outside the database that was restored,
and this is the code that turns that record back into deletions.

**It has no entry point yet, and that is not an oversight to read past.** There
is no CLI and no scheduler here; the only caller today is the drill suite,
because the reader it would consume — `ObjectStoreErasureJournal` over a real
bucket — cannot be constructed until the transport of §6.5 exists
(`app.main._object_store` refuses). Until then `docs/restore-runbook.md` is a
procedure a person carries out with these pieces, not one they can start.

**The boundary is `at >= t_b`, and the non-strict comparison is load-bearing.**
A restore point is almost always chosen round (`10:00:00`), the journal rounds
`at` up to the hour, and `10:00 > 10:00` would silently drop exactly the entries
most likely to exist.

**Every action goes through `ErasureService`**, not through a second set of
deletes written here. A private copy of "erase the vault" is a copy that drifts
from the live path, and the drift would show up only during an incident.

**Re-running never deletes the wrong thing, but it is not free.** Each of the
four actions leaves the *data* where a first pass left it. Two of them do move
other state on every pass: `sync_off` and `security_reset` go through
`erase_vault`, which advances all three revision counters and appends a fresh
journal line. So a second pass is safe and a tenth is still safe, but "run it
again to be sure" is not a no-op and the runbook says how to stop.

**What this module does not do is decide.** It refuses a restore the journal
cannot answer for, it refuses to guess at a code it does not know, and it
reports anything it could not finish — but every one of those is a stop for the
controller, not a branch this code takes on its own.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.erasure_journal import JournalLine, erasure_ref, restore_interlock
from app.services.erasure import (
    ERASURE_FULL,
    ERASURE_REMINDERS_OFF,
    ERASURE_SECURITY_RESET,
    ERASURE_SYNC_OFF,
    ErasureService,
)
from app.services.ports import Clock, UnitOfWorkFactory


class RestoreNotCovered(RuntimeError):
    """The journal cannot answer for the period this restore reaches back into.

    Step 2 of the runbook, made mechanical. Leaving it to a human to compute
    `min(now − service_start_at, J)` before calling this would make "the steps
    are blocking" a statement about discipline rather than about the code.
    """


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    considered: int
    applied: int
    already_absent: int
    unknown_code: tuple[str, ...]
    failed: int = 0

    @property
    def resolved(self) -> int:
        return self.considered - self.already_absent

    @property
    def outstanding(self) -> bool:
        """Anything the controller has to look at before calling this done.

        `resolved == 0` while entries were due is in here for a reason that is
        not hypothetical: `erasure_ref` is `HMAC(k_erasure, account_id)`, and a
        rotated or mistyped key makes *every* reference miss. Without this the
        run would report `considered=N, applied=0` with an empty
        `unknown_code`, and the runbook's completion criterion would read that
        as success while not one erasure was replayed — the 24-hour promise
        broken in silence.

        It does fire on a legitimate second pass whose entries were all `full`
        (the accounts are gone, so nothing resolves). That is the right way
        round: a run that changed nothing asks for one look at the key, and the
        first pass's numbers settle it.
        """
        return (
            bool(self.unknown_code)
            or self.failed > 0
            or (self.considered > 0 and self.resolved == 0)
        )


class RestoreReconciler:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        erasure: ErasureService,
        clock: Clock,
        *,
        erasure_key: bytes,
        service_start_at: datetime,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._erasure = erasure
        self._clock = clock
        self._erasure_key = erasure_key
        self._service_start_at = service_start_at

    def reconcile(
        self,
        entries: Iterable[JournalLine],
        *,
        taken_at: datetime,
        acknowledge_uncovered: bool = False,
    ) -> ReconciliationResult:
        now = self._clock.now()
        interlock = restore_interlock(
            now=now,
            service_start_at=self._service_start_at,
            backup_taken_at=taken_at,
        )
        if not interlock.covered and not acknowledge_uncovered:
            # Fail closed, and overridable only in as many words — the same
            # shape as the `acknowledge_incomplete` of §8, and for the same
            # reason: the decision belongs to the controller, but it has to be
            # a decision rather than an omission.
            raise RestoreNotCovered(
                "the journal covers"
                f" {interlock.coverage.days} days and this restore reaches"
                f" back {interlock.backup_age.days}"
            )

        due = [line for line in entries if line.at >= taken_at]
        by_reference = self._live_accounts()

        applied = 0
        absent = 0
        failed = 0
        unknown: list[str] = []
        references: list[UUID] = []

        for line in due:
            if line.code not in _ACTIONS:
                # A code this build has not been taught about is left for the
                # controller rather than guessed at, and it is checked before
                # the account is looked up: an unknown code for an account that
                # happens to be absent is still an unknown code.
                unknown.append(line.code)
                continue
            account_id = by_reference.get(line.erasure_ref)
            if account_id is None:
                # The account this entry names is not in the restored database:
                # either the restore predates it, or an earlier entry of this
                # same pass erased it. Both are done, not skipped.
                absent += 1
                continue
            try:
                reference = self._apply(line.code, account_id=account_id, now=now)
            except Exception:
                # One unreachable account must not cost every other account its
                # deadline — the same reason `OutboxDispatcher` and
                # `Housekeeper` take one transaction per item. The count goes
                # into `outstanding`, so a partial run cannot read as a clean
                # one, and the entries that did apply are still reported.
                failed += 1
                continue
            if reference is not None:
                references.append(reference)
                applied += 1

        for reference in references:
            self._erasure.confirm(reference, now=now)

        return ReconciliationResult(
            considered=len(due),
            applied=applied,
            already_absent=absent,
            unknown_code=tuple(sorted(set(unknown))),
            failed=failed,
        )

    # --------------------------------------------------------------- internals

    def _live_accounts(self) -> dict[str, UUID]:
        """`erasure_ref → account_id` for everything the restore brought back.

        The journal holds only the HMAC, so the mapping is rebuilt forwards.
        Without `k_erasure` the journal cannot be reconciled at all — which is
        why its custody is a runbook precondition and not a footnote.
        """
        with self._unit_of_work() as unit:
            identifiers = unit.accounts.identifiers()
        return {
            erasure_ref(self._erasure_key, account_id): account_id
            for account_id in identifiers
        }

    def _apply(self, code: str, *, account_id: UUID, now: datetime) -> UUID | None:
        with self._unit_of_work() as unit:
            if not unit.accounts.lock(account_id):
                # The accounts were enumerated once, at the start; by the time a
                # given entry is applied its account may be gone — most often
                # because an earlier entry in this same pass erased it. Never a
                # journal entry for a row that is not there.
                return None
            if code == ERASURE_FULL:
                reference = self._erasure.erase(unit, account_id=account_id, now=now)
            elif code in (ERASURE_SYNC_OFF, ERASURE_SECURITY_RESET):
                # `security_reset` is the same three effects as `sync_off` —
                # `DELETE vault_record + vault_key` and the counter reset — so
                # it shares the implementation and keeps its own name in the
                # journal. §6.4 writes the two rows separately for a reason:
                # what they mean to the user differs, what they do does not.
                #
                # No `ensure_counters` here on purpose: an account only exists
                # alongside a consent (§4.3), and `ConsentService.grant` creates
                # the counters row in that same transaction. A missing row would
                # mean a state this codebase cannot produce, and crashing the
                # reconciliation loudly beats inventing counters for a vault
                # whose history is unknown — the runbook's answer to a crash is
                # "stop and escalate", which is the right answer here.
                reference = self._erasure.erase_vault(
                    unit,
                    account_id=account_id,
                    now=now,
                    code=code,
                )
            else:
                reference = self._erasure.erase_reminders(
                    unit,
                    account_id=account_id,
                    now=now,
                )
            unit.commit()
        return reference


#: The four of §6.4. Kept beside the branch above so an added code fails the
#: `unknown_code` check instead of silently taking the last branch.
_ACTIONS = frozenset(
    {
        ERASURE_FULL,
        ERASURE_SYNC_OFF,
        ERASURE_SECURITY_RESET,
        ERASURE_REMINDERS_OFF,
    }
)
