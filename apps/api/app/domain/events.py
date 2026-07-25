"""Cross-context domain events (§4.4).

Two events, both with a named consumer in this phase. `ConsentGranted` is absent
on purpose: §4.4 removed it once schedule provision became a single transaction,
and this plan forbids events nobody consumes.

**What these events are for.** Every effect §4.4 attributes to `ConsentRevoked`
— erasure of the vault, the counter reset, the cascade to `cycle_sync`, the
DELETE of the schedule, the server-side DELETE of §9.7 — is required by §9.8,
§4.3 and §9.7 to happen *inside the same transaction* as `revoked_at`. So the
outbox here is not a transport that carries work to someone else; it is a
trigger that says an account changed, and the consumer re-reads the state from
the database it already has open under the same role.

That is what makes the constraint of §6.2 satisfiable rather than
contradictory: `outbox.payload` may not name the revoked consent, and it does
not need to, because nothing downstream decides anything from the payload. The
account identifier is the whole payload — §6.4 already accounts for it living
there when it names `outbox` as one of the two tables whose erasure entry is
unusual.

The `kind` stays on the domain event, which lives in process memory and never
reaches storage or a log; `to_payload` is the boundary where it is dropped, and
migration 0004 makes that boundary a CHECK rather than a promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.domain.identity import ConsentKind

CONSENT_REVOKED = "consent_revoked"
ACCOUNT_ERASURE_REQUESTED = "account_erasure_requested"


@dataclass(frozen=True, slots=True)
class ConsentRevoked:
    """One consent stopped being active.

    Consumer: the dispatcher's safety net for §4.3. An account that lost its
    last consent by the user's own decision must be erased in that same
    transaction, with a hard ceiling of 15 minutes; `Housekeeper` only picks up
    the two *timeout* reasons, and only after their 30-day window. Nobody at all
    picked up an account whose immediate erasure did not happen.
    """

    account_id: UUID
    kind: ConsentKind

    @property
    def event_type(self) -> str:
        return CONSENT_REVOKED

    def to_payload(self) -> dict[str, Any]:
        """The kind does not cross this line (§11, §13.12)."""
        return {"account_id": str(self.account_id)}


@dataclass(frozen=True, slots=True)
class AccountErasureRequested:
    """A full erasure was journalled and the deletion committed.

    Consumer: the dispatcher confirms the journal entry. Confirmation runs
    outside the erasure transaction by construction — the journal is written
    before the deletion and closed after it — so a process that dies in between
    leaves `completed_at` NULL for good. That signal has to stay truthful: §6.4
    builds the promise "we re-run your deletion within 24 hours" on top of it.
    """

    account_id: UUID
    erasure_reference: UUID

    @property
    def event_type(self) -> str:
        return ACCOUNT_ERASURE_REQUESTED

    def to_payload(self) -> dict[str, Any]:
        return {
            "account_id": str(self.account_id),
            "erasure_reference": str(self.erasure_reference),
        }


DomainEvent = ConsentRevoked | AccountErasureRequested

__all__ = [
    "ACCOUNT_ERASURE_REQUESTED",
    "CONSENT_REVOKED",
    "AccountErasureRequested",
    "ConsentRevoked",
    "DomainEvent",
]
