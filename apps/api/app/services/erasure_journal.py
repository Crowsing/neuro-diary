"""Erasure journal backed by `diary.erasure_job`.

§6.4 requires the journal entry to be written *before* deletion starts, and a
failure to write it must stop the erasure. The entry is therefore committed in
its own transaction: a record without a completed erasure is harmless (it only
yields an idempotent re-erasure), while the reverse order gives a single point
of failure where the account is gone and no evidence of it survives a restore.

Phase 3 replaces this with the external append-only store; the ordering
contract is what has to survive that swap.

It lives in `services` rather than `infra` because it performs no I/O of its
own: it orchestrates the repository ports, and the SQL stays in the adapter.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.services.ports import ConsentCopyPort, UnitOfWorkFactory


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
