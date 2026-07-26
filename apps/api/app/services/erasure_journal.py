"""Erasure journals: the database row, and the composition with the external one.

§6.4 requires the journal entry to be written *before* deletion starts, and a
failure to write it must stop the erasure. The entry is therefore committed in
its own transaction: a record without a completed erasure is harmless (it only
yields an idempotent re-erasure), while the reverse order gives a single point
of failure where the account is gone and no evidence of it survives a restore.

`DatabaseErasureJournal` is **not** the journal of §6.5 and never was: it lives
inside the database it would have to outlive. `TeeErasureJournal` is how the two
fit together — the external store answers for restores, the row keeps the two
operational fields the four-field line format has no room for
(`deletion_copy_version` and `completed_at`).

Both live in `services` rather than `infra` because neither performs I/O of its
own: one orchestrates repository ports, the other delegates to two journals.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.services.ports import ConsentCopyPort, ErasureJournalPort, UnitOfWorkFactory


class DatabaseErasureJournal:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        consent_copy: ConsentCopyPort,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._consent_copy = consent_copy

    def record_intent(self, *, account_id: UUID, code: str, at: datetime) -> UUID:
        with self._unit_of_work() as unit:
            reference = unit.erasure.record(
                account_id=account_id,
                scope=code,
                deletion_copy_version=self._consent_copy.deletion_copy_version(),
                requested_at=at,
            )
            unit.commit()
        return reference

    def confirm(self, reference: UUID, *, at: datetime) -> None:
        with self._unit_of_work() as unit:
            unit.erasure.complete(reference, at=at)
            unit.commit()


class TeeErasureJournal:
    """Write the external journal first, then the row; confirm only the row.

    **The order is the contract.** If the external store refuses, nothing else
    happens and the erasure does not start — which is the whole promise of
    §6.4, now made against the store that actually survives a restore. If the
    external write succeeds and the row does not, the erasure still does not
    start and the journal holds an entry for a deletion that never happened.

    §6.4 calls such an entry harmless, and it is worth being exact about what
    that means here, because this phase turned the journal into something a
    reconciliation acts on. The external line carries no completion state by
    design, so a restore cannot tell "never started" from "started and did not
    finish" — and it does not need to: it replays both, and replaying an
    erasure that already happened changes no data. What it does move is the
    revision counters and the journal itself for `sync_off` and
    `security_reset`. So: never a wrong deletion, never a lost one, and not
    quite free.

    Reversing the two would put the guarantee on the weaker store: the row
    would be written, the erasure would proceed, and the only record of it
    would be sitting in the database the incident is about to take.

    **`confirm` goes to the row alone.** The external line format has four
    fields and no completion, and appending a second `done` line would change a
    normative format. Nothing is lost: reconciliation reads the external
    journal, and re-running a finished erasure is a no-op.

    The two journals hold no cross-reference — the line format has no field for
    one. They are joined by `erasure_ref = HMAC(k_erasure, account_id)` and by
    time, which is exactly what the runbook works with.
    """

    def __init__(
        self,
        external: ErasureJournalPort,
        database: ErasureJournalPort,
    ) -> None:
        self._external = external
        self._database = database

    def record_intent(self, *, account_id: UUID, code: str, at: datetime) -> UUID:
        self._external.record_intent(account_id=account_id, code=code, at=at)
        return self._database.record_intent(account_id=account_id, code=code, at=at)

    def confirm(self, reference: UUID, *, at: datetime) -> None:
        self._database.confirm(reference, at=at)
