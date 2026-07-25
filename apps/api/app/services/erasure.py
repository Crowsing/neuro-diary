"""Erasure of an account, journal first.

The order is the whole point (§6.4): journal, then delete. If the journal write
raises, the caller's transaction is discarded and nothing is erased.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.services.ports import ErasureJournalPort, UnitOfWork

ERASURE_FULL = "full"


class ErasureService:
    def __init__(self, journal: ErasureJournalPort) -> None:
        self._journal = journal

    def erase(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        now: datetime,
        code: str = ERASURE_FULL,
    ) -> UUID:
        """Record the intent, then delete inside the caller's transaction.

        The caller commits. Rows in the `reminders` schema carry no foreign key
        to `diary.account` (there are no cross-schema keys), so they are removed
        explicitly; everything inside `diary` cascades from the account row.
        """
        reference = self._journal.record_intent(
            account_id=account_id,
            code=code,
            at=now,
        )
        unit.schedules.delete(account_id)
        unit.accounts.delete(account_id)
        return reference

    def confirm(self, reference: UUID, *, now: datetime) -> None:
        self._journal.confirm(reference, at=now)
