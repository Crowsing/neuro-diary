"""Protocol interfaces and transfer values between services and adapters.

Services depend on these; `main.py` supplies the implementations. Nothing here
imports SQLAlchemy or FastAPI, which is what keeps the import-linter contracts
of §5.2 satisfiable.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date, datetime, time
from typing import Any, Protocol
from uuid import UUID

from app.domain.identity import ConsentKind, RevokeReason
from app.domain.reminders import SendReceipt
from app.domain.records import (
    ConsentRecord,
    ConsentText,
    DueReminder,
    PendingCleanup,
    PendingErasure,
    PendingEvent,
    RateVerdict,
    RecordWrite,
    ReminderSchedule,
    SessionRecord,
    SessionSummary,
    StalePending,
    StoredRecord,
    StoredVaultKey,
    VaultCounters,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class AccountRepositoryPort(Protocol):
    def create(self, account_id: UUID, *, created_at: datetime) -> None: ...
    def status(self, account_id: UUID) -> str | None: ...
    def lock(self, account_id: UUID) -> bool: ...
    def delete(self, account_id: UUID) -> None: ...
    #: Restore reconciliation only (§6.4): the journal names accounts by an
    #: HMAC, which only runs forwards, so the references are matched by
    #: recomputing them for every account that a restore brought back.
    def identifiers(self) -> list[UUID]: ...


class TelegramIdentityRepositoryPort(Protocol):
    def create(
        self, telegram_user_id: int, account_id: UUID, *, now: datetime
    ) -> None: ...
    def account_id_for(self, telegram_user_id: int) -> UUID | None: ...
    def telegram_user_id_for(self, account_id: UUID) -> int | None: ...
    def touch(self, telegram_user_id: int, *, now: datetime) -> None: ...
    def last_auth_at(self, telegram_user_id: int) -> datetime | None: ...


class SessionRepositoryPort(Protocol):
    def create(
        self, account_id: UUID, *, token_hash: bytes, now: datetime
    ) -> SessionRecord: ...
    def find_active(
        self, token_hash: bytes, *, now: datetime
    ) -> SessionRecord | None: ...
    def slide(self, record: SessionRecord, *, now: datetime) -> datetime: ...
    def list_for_account(
        self, account_id: UUID, *, now: datetime, current_id: UUID
    ) -> list[SessionSummary]: ...
    def revoke_others(self, account_id: UUID, *, keep: UUID, now: datetime) -> int: ...
    def delete_settled_before(self, moment: datetime) -> int: ...


class AuthReplayRepositoryPort(Protocol):
    def remember(self, digest: bytes, *, now: datetime) -> bool: ...
    def delete_seen_before(self, moment: datetime) -> int: ...


class ConsentRepositoryPort(Protocol):
    def grant(
        self,
        account_id: UUID,
        *,
        kind: ConsentKind,
        text_version: str,
        text_sha256: bytes,
        text_locale: str,
        record_key_cycle: bytes | None,
        now: datetime,
    ) -> None: ...
    def named_cycle_key(self, account_id: UUID) -> bytes | None: ...
    def active(self, account_id: UUID) -> list[ConsentRecord]: ...
    def active_kinds(self, account_id: UUID) -> set[ConsentKind]: ...
    def revoke(
        self,
        account_id: UUID,
        *,
        kinds: list[ConsentKind],
        reason: RevokeReason,
        now: datetime,
    ) -> list[ConsentKind]: ...
    def latest_revoke_reason(self, account_id: UUID) -> RevokeReason | None: ...
    def delete_revoked_before(self, moment: datetime) -> int: ...
    def accounts_awaiting_erasure(
        self, *, reasons: list[RevokeReason], revoked_before: datetime
    ) -> list[PendingErasure]: ...


class ReminderScheduleRepositoryPort(Protocol):
    """The `api_rw` half of the `reminders` schema (§6.3).

    It provisions, edits, disables and erases; it never claims an occurrence and
    never marks a delivery sent, because the role behind it holds no `INSERT` on
    `reminder_delivery`. The worker's half is `ReminderWorkerPort` below, and
    the split is the GRANT matrix expressed in types.
    """

    def provision(
        self,
        account_id: UUID,
        *,
        telegram_chat_id: int,
        timezone_name: str,
        local_time: time,
        next_fire_at: datetime,
        now: datetime,
    ) -> None: ...
    def exists(self, account_id: UUID) -> bool: ...
    def read(self, account_id: UUID) -> ReminderSchedule | None: ...
    def update(
        self,
        account_id: UUID,
        *,
        timezone_name: str,
        local_time: time,
        enabled: bool,
        disabled_reason: str | None,
        disabled_at: datetime | None,
        next_fire_at: datetime,
        now: datetime,
    ) -> None: ...
    #: §10 reconciler input: accounts whose block has stood without a break
    #: since at or before `moment`. Reads `disabled_reason` and never `enabled`,
    #: so a pause can never appear here.
    def blocked_since(self, moment: datetime) -> list[UUID]: ...
    #: §6.4: the queue is filled by `api_rw` *before* the rows that name those
    #: messages are deleted, and drained by the worker. `expires_at` is the hard
    #: 48-hour TTL, not a hint.
    #:
    #: It takes no chat id and no message ids, and returns none: the adapter
    #: reads them from the rows that are about to be deleted and copies them
    #: across, so nothing above this line ever holds one.
    def queue_cleanup(self, account_id: UUID, *, expires_at: datetime) -> int: ...
    def delete(self, account_id: UUID) -> None: ...
    def delete_deliveries_before(self, moment: datetime) -> int: ...


class ReminderWorkerPort(Protocol):
    """Everything the delivery worker may touch, and nothing else.

    The role behind it (`reminder_worker`) has zero privileges on `diary`, no
    `DELETE` on `reminder_delivery` and no `INSERT` on `message_cleanup`. This
    protocol names only operations those grants permit, so a method that would
    need a wider role cannot be added here without the GRANT test noticing.
    """

    def claim_due(self, *, moment: datetime, limit: int) -> list[DueReminder]: ...
    #: Insert-before-send (§10). `False` means the day was already claimed —
    #: by a previous run, by another worker, or by an attempt that failed. It is
    #: never a reason to send anyway.
    def claim_occurrence(
        self, account_id: UUID, *, local_date: date, now: datetime
    ) -> bool: ...
    def finish_occurrence(
        self,
        account_id: UUID,
        *,
        local_date: date,
        status: str,
        telegram_message_id: int | None = None,
    ) -> None: ...
    def reschedule(
        self, account_id: UUID, *, next_fire_at: datetime, now: datetime
    ) -> None: ...
    #: §10: 403 from Telegram switches the schedule off and starts the streak.
    #: Idempotent — a second 403 must not restart a running streak.
    def record_block(self, account_id: UUID, *, now: datetime) -> None: ...
    def stale_pending(self, *, older_than: datetime) -> list[StalePending]: ...
    def due_cleanups(self, *, moment: datetime, limit: int) -> list[PendingCleanup]: ...
    def forget_cleanup(self, account_id: UUID, *, message_id: int) -> int: ...
    #: The unconditional half of §6.4: whatever the queue still holds past its
    #: TTL goes, whether or not Telegram ever accepted the deletion.
    def drop_expired_cleanups(self, *, moment: datetime) -> int: ...


class OutboxRepositoryPort(Protocol):
    """Transactional outbox (§4.4).

    `publish` belongs to the caller's transaction; everything else belongs to
    the dispatcher's. `delete_for_account` is this table's entry in the erasure
    matrix of §6.4 — the identifier lives inside the payload, so no cascade can
    do it.
    """

    def publish(
        self, *, event_type: str, payload: dict[str, Any], now: datetime
    ) -> None: ...
    def claim(self, *, limit: int, after_id: int = 0) -> list[PendingEvent]: ...
    def mark_processed(self, event_id: int, *, at: datetime) -> bool: ...
    def delete_processed_before(self, moment: datetime) -> int: ...
    def delete_for_account(self, account_id: UUID) -> int: ...


class ErasureRepositoryPort(Protocol):
    def record(
        self,
        *,
        account_id: UUID,
        scope: str,
        deletion_copy_version: str,
        requested_at: datetime,
    ) -> UUID: ...
    def complete(self, job_id: UUID, *, at: datetime) -> None: ...


class VaultRepositoryPort(Protocol):
    """Everything the push transaction of §9.1 needs, in its own order.

    `lock_counters` is the second lock of the transaction and must never be
    taken before `accounts.lock`: a fixed lock order is what keeps two pushes
    of one account from deadlocking instead of serializing.

    `bump_consent_epoch` belongs here rather than to the consent repository:
    the counter it moves lives in the vault row that pull reads.
    """

    def ensure_counters(self, account_id: UUID) -> None: ...
    def bump_consent_epoch(self, account_id: UUID) -> None: ...
    def counters(self, account_id: UUID) -> VaultCounters: ...
    #: §8: is there anything above this revision the device could still pull?
    #: Not the same question as `current_revision > n` — a hard DELETE by named
    #: key (§9.7) moves the counter without leaving anything to fetch.
    def has_records_above(self, account_id: UUID, *, revision: int) -> bool: ...
    def lock_counters(self, account_id: UUID) -> VaultCounters: ...
    def revisions_for(
        self, account_id: UUID, keys: list[bytes]
    ) -> dict[bytes, int]: ...
    def upsert(
        self,
        account_id: UUID,
        *,
        writes: list[RecordWrite],
        first_revision: int,
        now: datetime,
    ) -> None: ...
    def set_current_revision(self, account_id: UUID, *, value: int) -> None: ...
    def page(
        self, account_id: UUID, *, since: int, limit: int
    ) -> list[StoredRecord]: ...
    def delete_all(self, account_id: UUID) -> int: ...
    def delete_named_key(self, account_id: UUID, *, record_key: bytes) -> int: ...
    def mark_reset(self, account_id: UUID, *, revision: int) -> None: ...
    def reset_counters(self, account_id: UUID, *, revision: int) -> None: ...
    def accounts_with_stale_tombstones(
        self, *, older_than: datetime, limit: int
    ) -> list[UUID]: ...
    def compact(self, account_id: UUID, *, older_than: datetime) -> int: ...


class VaultKeyRepositoryPort(Protocol):
    def read(self, account_id: UUID) -> StoredVaultKey | None: ...
    def lock(self, account_id: UUID) -> StoredVaultKey | None: ...
    #: Crypto-erasure of §6.4 — a strengthening of the TTL promise, not a
    #: replacement for it: older backups still hold the old envelope.
    def delete(self, account_id: UUID) -> int: ...
    def write(
        self,
        account_id: UUID,
        *,
        wrapped_dek: bytes,
        kdf: str,
        kdf_params: dict[str, Any],
        key_version: int,
        wrap_version: int,
        keep_previous: bool,
        now: datetime,
    ) -> None: ...


class RateWindowRepositoryPort(Protocol):
    def consume(
        self,
        account_id: UUID,
        *,
        bucket: str,
        cost: int,
        limit: int,
        window_start: datetime,
        window_seconds: int,
        now: datetime,
    ) -> RateVerdict: ...


class UnitOfWork(Protocol):
    """Read-only properties, not attributes.

    A protocol with mutable attributes is invariant in their types, which would
    reject any concrete repository that is merely a subtype of its port.
    """

    @property
    def accounts(self) -> AccountRepositoryPort: ...
    @property
    def identities(self) -> TelegramIdentityRepositoryPort: ...
    @property
    def sessions(self) -> SessionRepositoryPort: ...
    @property
    def auth_replay(self) -> AuthReplayRepositoryPort: ...
    @property
    def consents(self) -> ConsentRepositoryPort: ...
    @property
    def schedules(self) -> ReminderScheduleRepositoryPort: ...
    @property
    def erasure(self) -> ErasureRepositoryPort: ...
    @property
    def outbox(self) -> OutboxRepositoryPort: ...
    @property
    def vault(self) -> VaultRepositoryPort: ...
    @property
    def vault_keys(self) -> VaultKeyRepositoryPort: ...
    @property
    def rate_windows(self) -> RateWindowRepositoryPort: ...

    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[UnitOfWork]: ...


class ReminderUnitOfWork(Protocol):
    """The worker's transaction: one schema, one repository, nothing else.

    Not a subset of `UnitOfWork` but a different shape, and on purpose. A worker
    handed the full unit would compile against `unit.consents` and `unit.vault`
    and only discover at runtime — in production, under a role with no `USAGE`
    on `diary` — that it may not read them. Here that mistake does not type.
    """

    @property
    def reminders(self) -> ReminderWorkerPort: ...

    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class ReminderUnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[ReminderUnitOfWork]: ...


class TelegramDeliveryPort(Protocol):
    """The two Bot API methods §5.3 pt. 6 allows this system to call.

    Nothing here takes a text, a label or a URL: §10 makes all three constants,
    and a parameter would be the first place an interpolation could appear.
    """

    def send_reminder(self, *, chat_id: int, deadline: datetime) -> SendReceipt: ...
    def delete_message(self, *, chat_id: int, message_id: int) -> bool: ...


class ConsentCopyPort(Protocol):
    def grant_text(self, kind: ConsentKind, *, locale: str) -> ConsentText: ...
    def unfrozen_versions(self) -> list[str]: ...
    def deletion_copy_version(self) -> str: ...


class ErasureJournalPort(Protocol):
    """Append-only record of the fact of erasure, written *before* deleting.

    Two implementations and one composition of them: `DatabaseErasureJournal`
    writes `diary.erasure_job` in a transaction of its own,
    `ObjectStoreErasureJournal` appends to the external store of §6.5, and
    `TeeErasureJournal` puts the external one in front of the row.

    The contract that matters here is the ordering: a failure to record must
    stop the erasure, whichever of them is wired.
    """

    def record_intent(
        self,
        *,
        account_id: UUID,
        code: str,
        at: datetime,
    ) -> UUID: ...
    def confirm(self, reference: UUID, *, at: datetime) -> None: ...


class InitDataValidatorPort(Protocol):
    def validate(self, init_data: str, *, now: datetime) -> ValidatedInitDataLike: ...


class ValidatedInitDataLike(Protocol):
    @property
    def telegram_user_id(self) -> int: ...
    @property
    def auth_date(self) -> datetime: ...
    @property
    def replay_digest(self) -> bytes: ...


__all__ = [
    "AccountRepositoryPort",
    "AuthReplayRepositoryPort",
    "Clock",
    "ConsentCopyPort",
    "ConsentRecord",
    "ConsentRepositoryPort",
    "ConsentText",
    "DueReminder",
    "ErasureJournalPort",
    "ErasureRepositoryPort",
    "InitDataValidatorPort",
    "OutboxRepositoryPort",
    "PendingCleanup",
    "PendingErasure",
    "PendingEvent",
    "RateVerdict",
    "RateWindowRepositoryPort",
    "RecordWrite",
    "ReminderSchedule",
    "ReminderScheduleRepositoryPort",
    "ReminderUnitOfWork",
    "ReminderUnitOfWorkFactory",
    "ReminderWorkerPort",
    "SessionRecord",
    "SessionRepositoryPort",
    "SessionSummary",
    "SendReceipt",
    "StalePending",
    "StoredRecord",
    "StoredVaultKey",
    "TelegramDeliveryPort",
    "TelegramIdentityRepositoryPort",
    "UnitOfWork",
    "UnitOfWorkFactory",
    "ValidatedInitDataLike",
    "VaultCounters",
    "VaultKeyRepositoryPort",
    "VaultRepositoryPort",
]
