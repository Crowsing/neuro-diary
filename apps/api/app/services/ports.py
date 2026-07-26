"""Protocol interfaces and transfer values between services and adapters.

Services depend on these; `main.py` supplies the implementations. Nothing here
imports SQLAlchemy or FastAPI, which is what keeps the import-linter contracts
of §5.2 satisfiable.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, time
from typing import Any, Protocol
from uuid import UUID

from app.domain.identity import ConsentKind, RevokeReason
from app.domain.records import (
    ConsentRecord,
    ConsentText,
    PendingErasure,
    PendingEvent,
    RateVerdict,
    RecordWrite,
    SessionRecord,
    SessionSummary,
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
    def delete(self, account_id: UUID) -> None: ...
    def delete_deliveries_before(self, moment: datetime) -> int: ...


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
    "ErasureJournalPort",
    "ErasureRepositoryPort",
    "InitDataValidatorPort",
    "OutboxRepositoryPort",
    "PendingErasure",
    "PendingEvent",
    "RateVerdict",
    "RateWindowRepositoryPort",
    "RecordWrite",
    "ReminderScheduleRepositoryPort",
    "SessionRecord",
    "SessionRepositoryPort",
    "SessionSummary",
    "StoredRecord",
    "StoredVaultKey",
    "TelegramIdentityRepositoryPort",
    "UnitOfWork",
    "UnitOfWorkFactory",
    "ValidatedInitDataLike",
    "VaultCounters",
    "VaultKeyRepositoryPort",
    "VaultRepositoryPort",
]
