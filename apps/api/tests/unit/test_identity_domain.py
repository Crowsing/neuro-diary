"""Identity domain constants: the step-up table, error codes, and versions.

The step-up table of §8 is transcribed here so that neither strengthening nor
weakening it can happen quietly: both directions break a test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.domain.consent_copy import TextVersion
from app.domain.identity import (
    ACCOUNT_ERASURE_DELAY,
    AUTH_REPLAY_TTL,
    CONSENT_ROW_RETENTION,
    INITDATA_MAX_AGE,
    SESSION_MAX_LIFETIME,
    SESSION_TTL,
    STEP_UP_FRESHNESS,
    STEP_UP_REQUIRED,
    AuthInvalid,
    AuthReplayed,
    AuthStale,
    ConsentAlreadyActive,
    ConsentCopyNotFrozen,
    ConsentKind,
    ConsentPrecondition,
    ConsentTextMismatch,
    DomainError,
    NoAccount,
    ProtectedOperation,
    QuietHoursViolation,
    RevokeReason,
    StepUpRequired,
    UnknownTimezone,
    requires_step_up,
)


def test_exactly_three_consent_kinds() -> None:
    assert {kind.value for kind in ConsentKind} == {
        "health_sync",
        "telegram_reminders",
        "cycle_sync",
    }


def test_exactly_three_revoke_reasons() -> None:
    assert {reason.value for reason in RevokeReason} == {
        "user",
        "bot_blocked_timeout",
        "stale_text_timeout",
    }


def test_protected_operations_cover_the_whole_step_up_table() -> None:
    assert {operation.value for operation in ProtectedOperation} == {
        "consent_revoke",
        "account_delete",
        "vault_reset",
        "rekey",
        "sync_key_write",
    }


def test_revoking_a_consent_never_requires_step_up() -> None:
    """Art. 7(3): withdrawing must be as easy as granting."""
    assert ProtectedOperation.CONSENT_REVOKE not in STEP_UP_REQUIRED
    assert requires_step_up(ProtectedOperation.CONSENT_REVOKE) is False


@pytest.mark.parametrize(
    "operation",
    [
        ProtectedOperation.ACCOUNT_DELETE,
        ProtectedOperation.VAULT_RESET,
        ProtectedOperation.REKEY,
        ProtectedOperation.SYNC_KEY_WRITE,
    ],
)
def test_irreversible_operations_require_step_up(
    operation: ProtectedOperation,
) -> None:
    assert operation in STEP_UP_REQUIRED
    assert requires_step_up(operation) is True


def test_step_up_table_is_exhaustive() -> None:
    assert STEP_UP_REQUIRED == frozenset(ProtectedOperation) - {
        ProtectedOperation.CONSENT_REVOKE
    }


def test_retention_constants_match_the_plan() -> None:
    assert SESSION_TTL == timedelta(days=7)
    assert SESSION_MAX_LIFETIME == timedelta(days=30)
    assert STEP_UP_FRESHNESS == timedelta(minutes=10)
    assert INITDATA_MAX_AGE == timedelta(hours=24)
    assert AUTH_REPLAY_TTL == timedelta(hours=48)
    assert ACCOUNT_ERASURE_DELAY == timedelta(days=30)
    assert CONSENT_ROW_RETENTION == timedelta(days=730)


DOMAIN_ERRORS = [
    (AuthInvalid, "auth_invalid"),
    (AuthStale, "auth_stale"),
    (AuthReplayed, "auth_replay"),
    (NoAccount, "no_account"),
    (StepUpRequired, "step_up_required"),
    (ConsentPrecondition, "consent_precondition"),
    (ConsentAlreadyActive, "consent_already_active"),
    (ConsentTextMismatch, "consent_text_mismatch"),
    (ConsentCopyNotFrozen, "consent_copy_not_frozen"),
    (QuietHoursViolation, "quiet_hours_violation"),
    (UnknownTimezone, "unknown_timezone"),
]


@pytest.mark.parametrize(("error_type", "code"), DOMAIN_ERRORS)
def test_domain_errors_carry_a_stable_ascii_code(
    error_type: type[DomainError],
    code: str,
) -> None:
    error = error_type()

    assert error.code == code
    assert code.isascii()
    assert str(error) == code


@pytest.mark.parametrize(("error_type", "code"), DOMAIN_ERRORS)
def test_domain_errors_cannot_echo_a_value(
    error_type: type[DomainError],
    code: str,
) -> None:
    """The constructor takes no message, so no input can ride along into a log."""
    del code
    with pytest.raises(TypeError):
        error_type("user=%7B%22id%22%3A42%7D")  # type: ignore[call-arg]


def test_text_version_round_trips() -> None:
    version = TextVersion.parse("health_sync@0.9")

    assert version.kind is ConsentKind.HEALTH_SYNC
    assert version.major == 0
    assert version.minor == 9
    assert str(version) == "health_sync@0.9"


def test_only_a_major_of_at_least_one_counts_as_frozen() -> None:
    assert TextVersion.parse("cycle_sync@0.9").is_frozen is False
    assert TextVersion.parse("cycle_sync@1.0").is_frozen is True
    assert TextVersion.parse("cycle_sync@2.13").is_frozen is True


@pytest.mark.parametrize(
    "value",
    [
        "health_sync",
        "health_sync@1",
        "health_sync@1.0.0",
        "unknown_kind@1.0",
        "health_sync@a.b",
        "@1.0",
        "health_sync@-1.0",
        " health_sync@1.0",
        "",
    ],
)
def test_malformed_text_versions_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        TextVersion.parse(value)
