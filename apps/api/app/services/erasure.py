"""Erasure of an account, journal first.

The order is the whole point (§6.4): journal, then delete. If the journal write
raises, the caller's transaction is discarded and nothing is erased.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.domain.events import (
    AccountErasureRequested,
    ReminderErasureRequested,
    SecurityResetRequested,
    VaultErasureRequested,
)
from app.domain.reminders import MESSAGE_CLEANUP_TTL
from app.services.ports import ErasureJournalPort, UnitOfWork

ERASURE_FULL = "full"
#: §6.4 runbook: `DELETE vault_record + vault_key` plus the counter reset of
#: §4.3. A partial erasure, not an account one — the account keeps living.
ERASURE_SYNC_OFF = "sync_off"
#: §6.4 runbook: `DELETE reminder_schedule + reminder_delivery`. Also partial.
ERASURE_REMINDERS_OFF = "reminders_off"
#: §6.4 runbook: `DELETE vault_key + vault_record`, `reset_revision =
#: current_revision + 1`. Not an erasure the user asked for — it marks the
#: moment after which restoring a backup would hand an old passphrase a live
#: service (§7).
ERASURE_SECURITY_RESET = "security_reset"


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
        _hand_over_sent_messages(unit, account_id=account_id, now=now)
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
        code: str = ERASURE_SYNC_OFF,
    ) -> UUID:
        """Erase the vault of a still-living account (§6.4 code `sync_off`).

        Same ordering rule as a full erasure and for the same reason: the
        journal entry is the only thing that survives a restore, so it goes
        first and a failure to write it stops everything.

        The caller has already taken the account row lock — that lock is the
        barrier of §9.8 against an in-flight push, and taking it here instead
        would be a second, later lock with no barrier property at all.

        `code` exists for exactly one caller: the restore reconciliation of
        §6.4 replays a `security_reset` with the same three effects and has to
        journal it under its own name. Sharing this implementation is the point
        — a second copy of "delete the vault" is a second thing to drift.
        """
        reference = self._journal.record_intent(
            account_id=account_id,
            code=code,
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
        # The same safety net a full erasure gets, and for the same reason: the
        # journal entry is closed outside this transaction, so a process that
        # dies right after the commit would otherwise leave `completed_at` NULL
        # for good. Covering only the account erasure would leave the more
        # frequent half — withdrawal while another consent remains — unguarded.
        event = VaultErasureRequested(
            account_id=account_id,
            erasure_reference=reference,
        )
        unit.outbox.publish(
            event_type=event.event_type,
            payload=event.to_payload(),
            now=now,
        )
        return reference

    def erase_reminders(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        now: datetime,
    ) -> UUID:
        """Erase the reminder rows of a still-living account (§6.4 `reminders_off`).

        Same ordering rule and same lock expectation as `erase_vault`: the
        caller already holds the account row lock, and the journal entry goes
        first because it is the only thing that survives a restore.

        Both `reminders` tables go together. Neither carries a foreign key to
        `diary.account` — there are no cross-schema keys — so no cascade would
        take them, and §4.4 forbids a schedule row outliving its consent.
        """
        reference = self._journal.record_intent(
            account_id=account_id,
            code=ERASURE_REMINDERS_OFF,
            at=now,
        )
        _hand_over_sent_messages(unit, account_id=account_id, now=now)
        unit.schedules.delete(account_id)
        event = ReminderErasureRequested(
            account_id=account_id,
            erasure_reference=reference,
        )
        unit.outbox.publish(
            event_type=event.event_type,
            payload=event.to_payload(),
            now=now,
        )
        return reference

    def record_security_reset(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        now: datetime,
    ) -> UUID:
        """Journal a `security_reset` before the caller performs it (§6.4, §7).

        Unlike the three erasures above, the deletion itself belongs to the
        caller: a vault-reset and an envelope write are two different mutations
        of two different tables, and both mark the same fact — the passphrase
        that used to open the server copy no longer does.

        The event is published before that mutation rather than after it, which
        is safe only because nothing on these paths sweeps the outbox. A full
        erasure has to publish last for exactly the opposite reason.
        """
        reference = self._journal.record_intent(
            account_id=account_id,
            code=ERASURE_SECURITY_RESET,
            at=now,
        )
        event = SecurityResetRequested(
            account_id=account_id,
            erasure_reference=reference,
        )
        unit.outbox.publish(
            event_type=event.event_type,
            payload=event.to_payload(),
            now=now,
        )
        return reference

    def confirm(self, reference: UUID, *, now: datetime) -> None:
        self._journal.confirm(reference, at=now)


def _hand_over_sent_messages(
    unit: UnitOfWork,
    *,
    account_id: UUID,
    now: datetime,
) -> None:
    """§6.4: keep what is needed to take the messages back, and only that.

    Until phase 4 this was the gap between what §6.4 promised and what the code
    did. The promise is explicit — «до 48 годин після видалення на сервері
    лишаються `chat_id` і номери повідомлень — саме щоб їх прибрати» — and the
    erasure deleted both tables outright, so the promise was kept for nobody and
    every reminder already delivered stayed in the chat for good.

    The copy happens **before** the delete, in the same transaction, because
    after it the chat id no longer exists anywhere. §6.3 gives `api_rw` the
    `INSERT` on this queue and gives the worker the `SELECT`/`DELETE`, which is
    the same split read from the other side: the process that can name the
    account cannot send anything, and the process holding `BOT_TOKEN` cannot see
    `diary`.

    The window is hard and short. Nothing here shortens it further on a rollback
    and nothing lengthens it on a retry: the insert keeps the first `expires_at`
    it wrote.
    """
    unit.schedules.queue_cleanup(
        account_id,
        expires_at=now + MESSAGE_CLEANUP_TTL,
    )
