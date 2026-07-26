"""Identity and consent domain: pure values, no I/O.

Domain errors deliberately take no message. §11 forbids echoing an input value
into an error, and a constructor that cannot accept one enforces that
mechanically rather than by review.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import ClassVar


class ConsentKind(StrEnum):
    HEALTH_SYNC = "health_sync"
    TELEGRAM_REMINDERS = "telegram_reminders"
    CYCLE_SYNC = "cycle_sync"


class RevokeReason(StrEnum):
    """Why a consent stopped being active — it sets the erasure deadline (§4.3)."""

    USER = "user"
    BOT_BLOCKED_TIMEOUT = "bot_blocked_timeout"
    STALE_TEXT_TIMEOUT = "stale_text_timeout"


class ProtectedOperation(StrEnum):
    """The full §8 table, including operations later phases implement.

    Declared in full on purpose: a partial enum invites someone to add the
    missing rows with their own idea of which side of the table they belong on.
    """

    CONSENT_REVOKE = "consent_revoke"
    ACCOUNT_DELETE = "account_delete"
    VAULT_RESET = "vault_reset"
    REKEY = "rekey"
    SYNC_KEY_WRITE = "sync_key_write"


# Everything except withdrawal. Art. 7(3) requires withdrawing to be as easy as
# granting; the rest are irreversible losses of the server-side copy.
STEP_UP_REQUIRED: frozenset[ProtectedOperation] = frozenset(ProtectedOperation) - {
    ProtectedOperation.CONSENT_REVOKE
}


def requires_step_up(operation: ProtectedOperation) -> bool:
    return operation in STEP_UP_REQUIRED


SESSION_TTL = timedelta(days=7)
SESSION_MAX_LIFETIME = timedelta(days=30)
STEP_UP_FRESHNESS = timedelta(minutes=10)
INITDATA_MAX_AGE = timedelta(hours=24)
AUTH_REPLAY_TTL = timedelta(hours=48)
ACCOUNT_ERASURE_DELAY = timedelta(days=30)
CONSENT_ROW_RETENTION = timedelta(days=730)


class DomainError(Exception):
    """Base for failures that map to a stable ASCII code on the wire."""

    code: ClassVar[str] = "internal"

    def __init__(self) -> None:
        super().__init__(self.code)


class AuthInvalid(DomainError):
    code = "auth_invalid"


class AuthStale(DomainError):
    code = "auth_stale"


class AuthReplayed(DomainError):
    code = "auth_replay"


class NoAccount(DomainError):
    code = "no_account"


class StepUpRequired(DomainError):
    code = "step_up_required"


class ConsentPrecondition(DomainError):
    code = "consent_precondition"


class ConsentAlreadyActive(DomainError):
    code = "consent_already_active"


class ConfirmRequired(DomainError):
    """§8: the server holds data this device never acknowledged.

    The compensating control that stands in for a step-up, which Art. 7(3)
    forbids on withdrawal. The name of the consent is not in the code — the
    caller already knows which one it asked to revoke, and an error code is one
    of the three places §13.12 keeps the consent set out of.
    """

    code = "confirm_required"


class ConsentTextMismatch(DomainError):
    code = "consent_text_mismatch"


class ConsentCopyNotFrozen(DomainError):
    code = "consent_copy_not_frozen"


class ConsentRequired(DomainError):
    """§11: an endpoint carrying medical data was reached without its consent.

    Distinct from `ConsentPrecondition`, which answers a *request to change* a
    consent that cannot be changed. This one answers a request to *use* a
    feature whose consent is not active, and §10 gives it 403 rather than 404 on
    purpose: a 404 would let a caller tell «no consent» from «no data», and the
    set of consents an account holds is itself a health inference.

    It carries the same wire code as `VaultForbidden`, which is deliberate —
    §10 and §9.2 name one code — and it is a separate class because it has to
    be: the import-linter contract «Reminder worker never touches vault» forbids
    `app.services.reminder` from importing `app.domain.vault`, so reusing that
    class would either break the contract or move a vault error out of the vault.
    """

    code = "consent_required"


class QuietHoursViolation(DomainError):
    code = "quiet_hours_violation"


class UnknownTimezone(DomainError):
    code = "unknown_timezone"
