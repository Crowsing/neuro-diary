"""Transfer values shared by services and adapters.

They live in the domain because both layers above and below need them, and a
value object in `services` would force `infra` to import upwards — exactly the
direction the layers contract of §5.2 forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
