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
    """

    def ensure_counters(self, account_id: UUID) -> None: ...
    def counters(self, account_id: UUID) -> VaultCounters: ...
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
    def mark_reset(self, account_id: UUID, *, revision: int) -> None: ...
    def accounts_with_stale_tombstones(
        self, *, older_than: datetime, limit: int
    ) -> list[UUID]: ...
    def compact(self, account_id: UUID, *, older_than: datetime) -> int: ...


class VaultKeyRepositoryPort(Protocol):
    def read(self, account_id: UUID) -> StoredVaultKey | None: ...
    def lock(self, account_id: UUID) -> StoredVaultKey | None: ...
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

    Phase 1 writes it inside the same transaction; phase 3 replaces this with
    the external append-only store of §6.4. The contract that matters here is
    the ordering: a failure to record must stop the erasure.
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
    "PendingErasure",
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
