"""Reconciliation of a restored database against the erasure journal (§6.4).

A restore puts back rows the service had already erased. The journal is the only
record of *which* ones, because it lives outside the database that was restored
— and this is the code that turns that record back into deletions, so the
runbook is a procedure that runs rather than a page somebody follows by hand.

**The boundary is `at >= t_b`, and the non-strict comparison is load-bearing.**
A restore point is almost always chosen round (`10:00:00`), the journal rounds
`at` up to the hour, and `10:00 > 10:00` would silently drop exactly the entries
most likely to exist.

**Every action goes through `ErasureService`**, not through a second set of
deletes written here. A private copy of "erase the vault" is a copy that drifts
from the live path, and the drift would show up only during an incident.

**Re-running is journalled again**, because it is a deletion that happens now.
§6.4 already calls an entry without a completed erasure harmless: each of the
four actions is idempotent, so an extra entry costs a no-op and never a wrong
deletion.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.erasure_journal import JournalLine, erasure_ref
from app.services.erasure import (
    ERASURE_FULL,
    ERASURE_REMINDERS_OFF,
    ERASURE_SECURITY_RESET,
    ERASURE_SYNC_OFF,
    ErasureService,
)
from app.services.ports import Clock, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    considered: int
    applied: int
    already_absent: int
    unknown_code: tuple[str, ...]

    @property
    def outstanding(self) -> bool:
        return bool(self.unknown_code)


class RestoreReconciler:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        erasure: ErasureService,
        clock: Clock,
        *,
        erasure_key: bytes,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._erasure = erasure
        self._clock = clock
        self._erasure_key = erasure_key

    def reconcile(
        self,
        entries: Iterable[JournalLine],
        *,
        taken_at: datetime,
    ) -> ReconciliationResult:
        now = self._clock.now()
        due = [line for line in entries if line.at >= taken_at]
        by_reference = self._live_accounts()

        applied = 0
        absent = 0
        unknown: list[str] = []
        references: list[UUID] = []

        for line in due:
            account_id = by_reference.get(line.erasure_ref)
            if account_id is None:
                # The account this entry names is not in the restored database:
                # either the restore predates it, or a previous pass of this
                # reconciliation already erased it. Both are done, not skipped.
                absent += 1
                continue
            if line.code not in _ACTIONS:
                # A code this build has not been taught about is left for the
                # controller rather than guessed at. Guessing is how a
                # reconciliation deletes the wrong thing.
                unknown.append(line.code)
                continue
            reference = self._apply(line.code, account_id=account_id, now=now)
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
