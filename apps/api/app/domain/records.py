"""Transfer values shared by services and adapters.

They live in the domain because both layers above and below need them, and a
value object in `services` would force `infra` to import upwards — exactly the
direction the layers contract of §5.2 forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from app.domain.identity import ConsentKind


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    kind: ConsentKind
    granted_at: datetime
    text_version: str
    text_sha256: bytes
    text_locale: str


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: UUID
    account_id: UUID
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime


@dataclass(frozen=True, slots=True)
class SessionSummary:
    id: UUID
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    current: bool


@dataclass(frozen=True, slots=True)
class PendingErasure:
    account_id: UUID
    revoked_at: datetime


@dataclass(frozen=True, slots=True)
class PendingEvent:
    """One claimed outbox row (§4.4).

    `payload` carries the account identifier and nothing that names a consent;
    the consumer reads whatever else it needs from the database (§11, §13.12).
    """

    id: int
    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConsentText:
    """A registry entry: the text shown and the digest stored alongside it."""

    kind: ConsentKind
    text_version: str
    locale: str
    sha256: bytes
    body: str


@dataclass(frozen=True, slots=True)
class VaultCounters:
    current_revision: int
    compacted_up_to: int
    reset_revision: int
    consent_epoch: int


@dataclass(frozen=True, slots=True)
class RecordWrite:
    """One change of a push. `payload` is None exactly when `tombstone`."""

    record_key: bytes
    payload: bytes | None
    tombstone: bool
    client_ts_ms: int


@dataclass(frozen=True, slots=True)
class StoredRecord:
    record_key: bytes
    payload: bytes | None
    deleted: bool
    revision: int
    client_ts_ms: int


@dataclass(frozen=True, slots=True)
class StoredVaultKey:
    wrapped_dek: bytes
    kdf: str
    kdf_params: dict[str, Any]
    key_version: int
    wrap_version: int
    wrapped_dek_prev: bytes | None
    wrap_version_prev: int | None
    prev_written_at: datetime | None


@dataclass(frozen=True, slots=True)
class RateVerdict:
    allowed: bool
    retry_after_seconds: int


@dataclass(frozen=True, slots=True)
class ReminderSchedule:
    """One row of `reminders.reminder_schedule`, as a value.

    `disabled_reason` and `disabled_at` travel together — migration 0005 makes
    that a CHECK — and together they are the only thing separating a pause the
    user asked for from a block Telegram imposed. The reconciler decides on
    those two fields alone, never on `enabled`.
    """

    account_id: UUID
    telegram_chat_id: int
    timezone_name: str
    local_time: time
    enabled: bool
    disabled_reason: str | None
    disabled_at: datetime | None
    next_fire_at: datetime


@dataclass(frozen=True, slots=True)
class DueReminder:
    """A schedule the worker has locked for this pass.

    Deliberately not the whole row: the worker needs a chat id, a zone and the
    instant the occurrence belongs to, and nothing else in the table is any of
    its business.
    """

    account_id: UUID
    telegram_chat_id: int
    timezone_name: str
    #: Carried so the next fire time is resolved from the local calendar date
    #: rather than by adding a day to `next_fire_at` — the one arithmetic §10
    #: forbids outright.
    local_time: time
    next_fire_at: datetime


@dataclass(frozen=True, slots=True)
class PendingCleanup:
    """One message §6.4 promised to remove from the chat within its TTL."""

    account_id: UUID
    chat_id: int
    message_id: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StalePending:
    """A claimed occurrence whose attempt never reported back."""

    account_id: UUID
    local_date: date
