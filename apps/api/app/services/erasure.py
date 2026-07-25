"""Erasure of an account, journal first.

The order is the whole point (§6.4): journal, then delete. If the journal write
raises, the caller's transaction is discarded and nothing is erased.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.domain.events import AccountErasureRequested
from app.services.ports import ErasureJournalPort, UnitOfWork

ERASURE_FULL = "full"
#: §6.4 runbook: `DELETE vault_record + vault_key` plus the counter reset of
#: §4.3. A partial erasure, not an account one — the account keeps living.
ERASURE_SYNC_OFF = "sync_off"


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

        `outbox` cascades from nothing at all — its account identifier lives
        inside `payload` (§6.4) — so it is cleared explicitly, and the event
        announcing this erasure is published *after* that sweep. Publishing it
        first would delete it again a line later.
        """
        reference = self._journal.record_intent(
            account_id=account_id,
            code=code,
            at=now,
        )
        unit.outbox.delete_for_account(account_id)
        unit.schedules.delete(account_id)
        unit.accounts.delete(account_id)
        event = AccountErasureRequested(
            account_id=account_id,
            erasure_reference=reference,
        )
        unit.outbox.publish(
            event_type=event.event_type,
            payload=event.to_payload(),
            now=now,
        )
        return reference

    def erase_vault(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        now: datetime,
    ) -> UUID:
        """Erase the vault of a still-living account (§6.4 code `sync_off`).

        Same ordering rule as a full erasure and for the same reason: the
        journal entry is the only thing that survives a restore, so it goes
        first and a failure to write it stops everything.

        The caller has already taken the account row lock — that lock is the
        barrier of §9.8 against an in-flight push, and taking it here instead
        would be a second, later lock with no barrier property at all.
        """
        reference = self._journal.record_intent(
            account_id=account_id,
            code=ERASURE_SYNC_OFF,
            at=now,
        )
        unit.vault.delete_all(account_id)
        # Crypto-erasure (§6.4): removing the envelope makes the ciphertext in
        # any older backup undecryptable. It strengthens the TTL promise rather
        # than replacing it — those backups still hold the old envelope, and
        # the backup encryption key is cluster-wide, unchanged and undestroyed
        # by erasing one account.
        unit.vault_keys.delete(account_id)
        # §4.3: counters outlive the records unless they are reset here. A
        # device that went offline before the revocation would otherwise pass
        # both gates of §9.1 after a re-grant and quietly upload the whole
        # diary back — no 409, no 410, no dialogue.
        counters = unit.vault.lock_counters(account_id)
        unit.vault.reset_counters(account_id, revision=counters.current_revision + 1)
        return reference

    def confirm(self, reference: UUID, *, now: datetime) -> None:
        self._journal.confirm(reference, at=now)
