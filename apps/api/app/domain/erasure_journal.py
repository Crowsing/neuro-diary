"""The external erasure journal: line format, integrity chain, interlock (§6.4).

Everything here is pure. There is no store, no clock and no key custody in this
module — only the shape of what gets written and the arithmetic that says
whether the journal can answer for a restore. That is deliberate: the format is
a contract between a writer that lives in `app.infra` and a reconciler that
lives in `app.workers`, and a contract nobody can test without a bucket is a
contract nobody tests.

**What may be in a line, and why so little.** `{erasure_ref, at, code,
prev_hash}` and nothing else. `erasure_ref = HMAC(k_erasure, account_id)` — the
plain identifier is never written, nor is the Telegram id, nor the name of the
consent. `at` is rounded *up* to the UTC hour so the entry is not the exact
moment the user acted.

**Why up and not down.** Rounding can only move an entry later, so the runbook's
`at >= t_b` filter can gain an entry it did not strictly need — one extra
idempotent reconciliation — and can never lose one it did. Rounding down would
invert that, and the failure would be silent.

**The journal is pseudonymised personal data for its whole life.** Not anonymous
after thirty days: for every code except `full` the account keeps living and its
`account_id` stays in `diary.account`, so the HMAC stays re-computable and the
reference stays linkable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

#: §6.4 retention `J`. It is enforced by the bucket lifecycle rule and never by
#: this process: the writer holds `PUT` and there is no principal with `DELETE`.
#: The number lives here because the restore interlock needs it, not because
#: anything in this codebase trims anything.
JOURNAL_RETENTION = timedelta(days=90)

#: `prev_hash` of the very first line of the journal. A chain has to start
#: somewhere, and a named constant is what lets the verifier tell "this is the
#: beginning" from "the predecessor is missing".
GENESIS = "0" * 64

_ZERO = timedelta(0)


def erasure_ref(key: bytes, account_id: UUID) -> str:
    """`HMAC(k_erasure, account_id)` — the only identifier the journal holds.

    One-way on purpose, and that shapes the runbook: reconciliation cannot read
    an account out of a reference, it recomputes the reference for every live
    account and matches. That works precisely because a restore has already put
    those accounts back.
    """
    return hmac.new(key, account_id.bytes, hashlib.sha256).hexdigest()


def ceil_to_hour(at: datetime) -> datetime:
    """Round up to the UTC hour; an instant already on the hour stays put."""
    moment = at.astimezone(UTC)
    truncated = moment.replace(minute=0, second=0, microsecond=0)
    if truncated == moment:
        return truncated
    return truncated + timedelta(hours=1)


def day_of(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class JournalLine:
    erasure_ref: str
    at: datetime
    code: str
    prev_hash: str

    def to_json(self) -> bytes:
        """The canonical body. Byte-stable, because the key is its hash."""
        return json.dumps(
            {
                "erasure_ref": self.erasure_ref,
                "at": self.at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "code": self.code,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_json(cls, body: bytes) -> JournalLine:
        payload = json.loads(body)
        return cls(
            erasure_ref=str(payload["erasure_ref"]),
            at=datetime.strptime(str(payload["at"]), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            ),
            code=str(payload["code"]),
            prev_hash=str(payload["prev_hash"]),
        )


def line_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def journal_key(prefix: str, *, line: JournalLine, body: bytes) -> str:
    """`<prefix>/<day>/<sha256 of the body>.json`.

    Content addressed, so no key is ever written twice with different content
    and writing the same line again is idempotent rather than an overwrite. An
    append-only bucket has no way to express "replace", and this layout never
    asks it to.
    """
    return f"{prefix}/{day_of(line.at)}/{line_hash(body)}.json"


def head_key(prefix: str, *, day: str, last_hash: str) -> str:
    """One head object per append, keyed by the line it attests.

    The day's authoritative head is the one whose `last_hash` is not the
    `prev_hash` of any other line of that day — that is, the tail. Writing a
    single `_head.json` per day and overwriting it would be the obvious design
    and the wrong one: overwriting is the one thing append-only forbids.
    """
    return f"{prefix}/{day}/head/{last_hash}.json"


def sign_head(key: bytes, *, day: str, last_hash: str) -> str:
    """HMAC of the controller's key over the day and its tail (§6.4)."""
    message = f"ndv1-erasure-head\x1f{day}\x1f{last_hash}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_head(key: bytes, *, day: str, last_hash: str, signature: str) -> bool:
    return hmac.compare_digest(
        sign_head(key, day=day, last_hash=last_hash),
        signature,
    )


@dataclass(frozen=True, slots=True)
class StoredLine:
    """Exactly what the store returned: the key it was under and its bytes."""

    key: str
    body: bytes


@dataclass(frozen=True, slots=True)
class ChainBreak:
    key: str
    reason: str
    prev_hash: str | None = None


@dataclass(frozen=True, slots=True)
class ChainFork:
    """Two lines appended against the same tail. Not a break — see below."""

    prev_hash: str
    keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChainVerdict:
    lines: int
    breaks: tuple[ChainBreak, ...]
    forks: tuple[ChainFork, ...]

    @property
    def intact(self) -> bool:
        return not self.breaks

    @property
    def forked(self) -> bool:
        return bool(self.forks)


def verify_chain(
    stored: Iterable[StoredLine],
    *,
    roots: frozenset[str] = frozenset({GENESIS}),
) -> ChainVerdict:
    """Walk the journal and say **where** it breaks, not merely whether.

    Without this the `prev_hash` field is decoration: nothing would ever read
    it, and a line removed from the bucket would look exactly like a line that
    was never written.

    Three failures are distinguished because they mean different things to the
    controller: a body that will not parse, a key that no longer addresses its
    body (the object was replaced in place), and a `prev_hash` that leads
    nowhere (the predecessor is gone).

    A **fork** is reported separately and is not a break. Two writers — the
    dispatcher and the synchronous revoke path — can read the same tail and both
    append against it. Both lines are durable, both reconcile, and the chain is
    a tree at that node. The alternative, a conditional PUT that fails the
    loser, would turn a race into a failed journal write, and a failed journal
    write stops an erasure.

    `roots` names hashes that may legitimately have no predecessor here, so a
    single day can be verified without accusing yesterday's tail.
    """
    lines = list(stored)
    breaks: list[ChainBreak] = []
    by_hash: dict[str, StoredLine] = {}
    parsed: list[tuple[StoredLine, JournalLine, str]] = []

    for item in lines:
        digest = line_hash(item.body)
        if not item.key.endswith(f"/{digest}.json"):
            breaks.append(ChainBreak(key=item.key, reason="key_does_not_address_body"))
            continue
        try:
            line = JournalLine.from_json(item.body)
        except (ValueError, KeyError, TypeError):
            breaks.append(ChainBreak(key=item.key, reason="malformed"))
            continue
        by_hash[digest] = item
        parsed.append((item, line, digest))

    successors: dict[str, list[str]] = {}
    for item, line, _digest in parsed:
        successors.setdefault(line.prev_hash, []).append(item.key)
        if line.prev_hash in roots or line.prev_hash in by_hash:
            continue
        breaks.append(
            ChainBreak(
                key=item.key,
                reason="missing_predecessor",
                prev_hash=line.prev_hash,
            )
        )

    forks = tuple(
        ChainFork(prev_hash=prev, keys=tuple(sorted(keys)))
        for prev, keys in sorted(successors.items())
        if len(keys) > 1
    )
    return ChainVerdict(lines=len(lines), breaks=tuple(breaks), forks=forks)


@dataclass(frozen=True, slots=True)
class RestoreInterlock:
    coverage: timedelta
    backup_age: timedelta

    @property
    def covered(self) -> bool:
        """Both bounds, and the lower one is not decoration.

        A `backup_taken_at` in the future is nonsense — you cannot restore to a
        point that has not happened — and clamping its age to zero would call
        it covered. It is worse than useless there: `at >= t_b` would then
        match nothing, reconciliation would do nothing, and the operator would
        read "covered, zero entries" as "the restore is safe" while every
        erasure the journal holds went unreplayed.
        """
        return _ZERO <= self.backup_age <= self.coverage


def restore_interlock(
    *,
    now: datetime,
    service_start_at: datetime,
    backup_taken_at: datetime,
    retention: timedelta = JOURNAL_RETENTION,
) -> RestoreInterlock:
    """§6.4: coverage is `min(now − service_start_at, J)`, never `now − min(at)`.

    The rejected formula reads an empty journal as zero coverage, so a healthy
    service that has simply never erased anything could never be restored. This
    one asks a different question — how far back the journal *could* answer for
    — and a service that has been running longer than `J` can answer for `J`.

    `service_start_at` is a deployment constant rather than a row: this
    interlock is evaluated during a restore, and reading it out of the database
    being restored would make the guard depend on the artefact it guards.

    A `service_start_at` in the future is a misconfiguration nobody can
    re-derive, so it yields zero coverage and blocks the restore.
    """
    elapsed = max(now - service_start_at, _ZERO)
    return RestoreInterlock(
        coverage=min(elapsed, retention),
        backup_age=now - backup_taken_at,
    )
