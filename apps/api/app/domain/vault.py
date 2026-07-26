"""Encrypted vault domain: pure values, no I/O.

The server never parses a payload. Everything expressible here is a counter, a
size, or a refusal — which is what "zero-knowledge" means in code.

Domain errors take no message, exactly as in `identity`: §11 forbids echoing an
input value into an error, and a constructor that cannot accept one enforces
that mechanically. The 409 bodies that do carry data (`conflict_keys`,
`current_wrap_version`) are outcome values returned by the service, not
exceptions — see `app.services.sync`.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum

from app.domain.identity import ConsentRequired, DomainError

# §6.4: one product horizon, everything else derived from it. H is the only
# number the user ever sees; the slack covers the compactor period, clock skew
# and serialization lag. The invariant JOURNAL_TTL >= TOMBSTONE_TTL is what
# keeps the 410 path from becoming dead code.
HORIZON = timedelta(days=180)
COMPACTION_SLACK = timedelta(days=7)
TOMBSTONE_TTL = HORIZON + COMPACTION_SLACK
JOURNAL_TTL = TOMBSTONE_TTL

# §9.5 limits. Batch limits are checked before the database, or the CHECK
# constraint of migration 0001 would answer 500 where the contract says 413.
RECORD_KEY_BYTES = 32
MAX_RECORD_BYTES = 65_536

# §7: the envelope is the root secret `R` — 32 bytes — wrapped by a KEK, so a
# kilobyte is three orders of magnitude of slack. It is bounded because
# «unbounded» is not a size: `ck_vault_key_wrapped_dek_nonempty` of migration
# 0001 refuses an empty envelope and says nothing about a large one, and the
# server is zero-knowledge about the *contents*, not about the length.
MAX_ENVELOPE_BYTES = 1_024
MAX_RECORDS_PER_PUSH = 200
MAX_PUSH_BYTES = 1_048_576
PULL_PAGE_LIMIT = 500

# The largest integer a counter may carry across the wire.
#
# Without a bound at all, a cursor of `10**22` reached PostgreSQL as an
# out-of-range parameter, the driver raised, and the sanitized handler answered
# **500** — found by the schemathesis run of Phase 5, on
# `GET /v1/sync/pull?since=3138421778880289832960`. 422 is the right answer: the
# value is outside the contract, not a server fault.
#
# It is 2^53 − 1 rather than the `bigint` ceiling of 2^63 − 1, and the reason is
# the wire rather than the column. A JSON number is an IEEE-754 double almost
# everywhere, including in the OpenAPI document itself: FastAPI's spec model
# types `maximum` as a float, so a declared bound of 2^63 − 1 comes back out of
# the document as 2^63 — one **larger** than what the code accepts, which is a
# schema that lies in the direction that matters. At 2^53 − 1 the value survives
# every round-trip exactly.
#
# The gap to the column is unreachable, not merely unlikely: 2^53 milliseconds is
# roughly 285 000 years, and a revision is issued per record.
MAX_COUNTER = 2**53 - 1

# §11 per-account rate limits moved to `app.domain.rate_limits` in Phase 5: the
# buckets, their limits and their windows are now one registry, because
# «which endpoints are limited» had to become a property a test can assert. What
# was decided here and still holds: `GET /v1/consents` deliberately does not
# share the `sync` bucket, and gets no bucket of its own either — the post-410
# rule of §9.4 forbids pruning without a fresh consent answer, so an exhausted
# budget must never block the answer that unblocks pruning.

# §7: the previous envelope makes a mistaken or malicious overwrite reversible
# without weakening anything — it is returned only under step-up.
PREV_ENVELOPE_TTL = timedelta(days=7)
PREV_ENVELOPE_MIN_INTERVAL = timedelta(hours=24)


class KeyWriteMode(StrEnum):
    """Re-wrap keeps the vault and bumps `wrap_version`; re-key replaces both."""

    REWRAP = "rewrap"
    REKEY = "rekey"


#: No active `health_sync`, or the account is no longer active.
#:
#: The same class as `ConsentRequired`, not a sibling of it. §9.2 and §10 give
#: one wire code — `consent_required` — to «this endpoint needs a consent you do
#: not have», and two classes carrying one code is exactly what
#: `test_error_codes_are_stable_ascii_and_unique` exists to catch: a handler
#: registered for one of them silently misses the other.
#:
#: It is declared in `app.domain.identity` rather than here because the
#: import-linter contract «Reminder worker never touches vault» forbids
#: `app.services.reminder` from importing this module, and the reminder
#: endpoints need the same refusal. The name stays for the vault paths that
#: raise it, where «forbidden» reads better than «required».
VaultForbidden = ConsentRequired


class VaultReset(DomainError):
    code = "vault_reset"


class VaultGone(DomainError):
    """The cursor is below the compaction horizon; a full resync is required."""

    code = "gone"


class PayloadTooLarge(DomainError):
    code = "payload_too_large"


class TransientConflict(DomainError):
    """Serialization failure or deadlock; the router retries once."""

    code = "retry"
