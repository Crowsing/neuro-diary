"""Backup retention: two different named quantities, not one (§6.4).

`BACKUP_PROMISE_DAYS` is the number the user is told. It is the single source of
that number in code, and a CI guard keeps any other literal spelling of it out —
which matters because the system holds three *unrelated* thirty-day durations
(§4.3's erasure window, §6.2's session and outbox TTLs) and confusing any of
them with the promise would make a text for the user false.

Nothing renders the promise from this constant, and that is not an omission:
the sentence the user reads lives in `consent-copy/uk/deletion/1.0.md`, frozen
and hashed, and the server never renders it. What the constant is *for* is the
arithmetic below and the guard's allowlist — a test ties it to the frozen text
so the two cannot drift apart in silence.

`BACKUP_CONFIG_MAX_AGE_DAYS` is the infrastructure invariant behind it: the
configured expiry has to be shorter than the promise, or the promise is a hope.

The checks below run at import and raise rather than `assert`, because `python
-O` strips `assert` — and a static check that disappears exactly in production
is worse than none. That argument is only worth making if the module is
actually imported by the running process, so `JOURNAL_RETENTION` lives here
rather than beside the journal: `app.domain.erasure_journal` imports it, the
adapter imports that, and `app.main` imports the adapter. A retention module
nobody but pytest loads would dodge the letter of the `-O` problem and keep its
substance.
"""

from __future__ import annotations

from datetime import timedelta

#: The promise in `consent-copy/uk/deletion/1.0.md`. Every appearance of this
#: number elsewhere refers here rather than restating it.
BACKUP_PROMISE_DAYS = 30

#: The configured expiry of the backup repositories. Deliberately shorter than
#: the promise: the gap is the room the promise needs to stay true.
BACKUP_CONFIG_MAX_AGE_DAYS = 28

#: Object Lock, governance mode. Below the expiry on purpose, so the lock never
#: blocks the routine clean-up that keeps the promise.
OBJECT_LOCK_GOVERNANCE_DAYS = 21

#: Object Lock turns versioning on, so delete markers would otherwise leave
#: noncurrent versions forever and the promise would quietly become false.
#: Proof of this one is `ListObjectVersions`, never a backup tool's report.
NONCURRENT_VERSION_EXPIRATION_DAYS = 28

#: `D_rec` of §6.4: the time to notice a restore is needed and decide on it.
RECOVERY_DETECTION_DAYS = 1

#: `M` of §6.4: margin for running the reconciliation itself.
RECONCILIATION_MARGIN_DAYS = 7

#: §6.4: `J_min = BACKUP_CONFIG_MAX_AGE + D_rec + M`. The journal has to outlive
#: the oldest backup that could still be restored, plus the time to notice and
#: to act.
JOURNAL_RETENTION_FLOOR_DAYS = (
    BACKUP_CONFIG_MAX_AGE_DAYS + RECOVERY_DETECTION_DAYS + RECONCILIATION_MARGIN_DAYS
)

#: §6.4 retention `J`, chosen with room above the floor below. Enforced by the
#: bucket lifecycle rule and never by this codebase: the writer holds `PUT` and
#: there is no principal with `DELETE`. The number lives here because the
#: restore interlock needs it, not because anything here trims anything.
JOURNAL_RETENTION = timedelta(days=90)


if BACKUP_CONFIG_MAX_AGE_DAYS >= BACKUP_PROMISE_DAYS:
    raise ValueError(
        "§6.4: BACKUP_CONFIG_MAX_AGE_DAYS must stay below BACKUP_PROMISE_DAYS,"
        " or the promise made to the user is longer than the configuration"
        " that keeps it"
    )

if OBJECT_LOCK_GOVERNANCE_DAYS >= NONCURRENT_VERSION_EXPIRATION_DAYS:
    raise ValueError(
        "§6.4: the Object Lock has to expire before the version does, or the"
        " lock blocks the expiry it exists alongside"
    )

if JOURNAL_RETENTION < timedelta(days=JOURNAL_RETENTION_FLOOR_DAYS):
    raise ValueError(
        "§6.4: the erasure journal would expire before the oldest restorable"
        " backup could be reconciled against it"
    )
