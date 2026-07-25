"""Transfer values shared by services and adapters.

They live in the domain because both layers above and below need them, and a
value object in `services` would force `infra` to import upwards — exactly the
direction the layers contract of §5.2 forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
