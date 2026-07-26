"""The line format, the integrity chain and the restore interlock (§6.4).

All of it is pure, so all of it is testable without a bucket. The keys are
generated inside the run and never written to a file: gitleaks scans the whole
history, and a 64-character hex string would keep CI red long after it left
HEAD.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.domain.erasure_journal import (
    GENESIS,
    JOURNAL_RETENTION,
    ChainBreak,
    ChainVerdict,
    JournalLine,
    StoredLine,
    ceil_to_hour,
    erasure_ref,
    journal_key,
    line_hash,
    restore_interlock,
    sign_head,
    verify_chain,
    verify_head,
)

PREFIX = "erasure"
ACCOUNT = UUID("11111111-2222-3333-4444-555555555555")
MOMENT = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def erasure_key() -> bytes:
    return secrets.token_bytes(32)


@pytest.fixture
def head_key() -> bytes:
    return secrets.token_bytes(32)


def _chain(erasure_key: bytes, *codes: str) -> list[StoredLine]:
    """Link `codes` into a chain that starts at GENESIS."""
    stored: list[StoredLine] = []
    previous = GENESIS
    for index, code in enumerate(codes):
        line = JournalLine(
            erasure_ref=erasure_ref(erasure_key, uuid4()),
            at=MOMENT + timedelta(hours=index),
            code=code,
            prev_hash=previous,
        )
        body = line.to_json()
        previous = line_hash(body)
        stored.append(
            StoredLine(key=journal_key(PREFIX, line=line, body=body), body=body)
        )
    return stored


# ------------------------------------------------------------------- rounding


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 7, 24, 12, 0, 1, tzinfo=UTC),
            datetime(2026, 7, 24, 13, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 7, 24, 12, 59, 59, tzinfo=UTC),
            datetime(2026, 7, 24, 13, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 7, 24, 12, 0, 0, 1, tzinfo=UTC),
            datetime(2026, 7, 24, 13, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 7, 24, 23, 30, tzinfo=UTC),
            datetime(2026, 7, 25, 0, 0, tzinfo=UTC),
        ),
    ],
)
def test_at_is_rounded_up_to_the_hour(raw: datetime, expected: datetime) -> None:
    """§6.4: up, never down, and never to the nearest.

    Up is what keeps the entry from being the exact moment the user acted. It is
    also the only direction that is safe for the runbook: rounding can move an
    entry from `at < t_b` to `at >= t_b`, which costs one idempotent
    reconciliation, but it can never move one the other way and lose it.
    """
    assert ceil_to_hour(raw) == expected


def test_a_local_time_is_converted_before_it_is_rounded() -> None:
    """12:30 UTC, not 15:30 — the hour that is rounded is always the UTC one."""
    kyiv = datetime(2026, 7, 24, 15, 30, tzinfo=timezone(timedelta(hours=3)))

    assert ceil_to_hour(kyiv) == datetime(2026, 7, 24, 13, 0, tzinfo=UTC)


def test_rounding_never_moves_an_instant_backwards() -> None:
    for minute in range(0, 60, 7):
        for second in (0, 1, 30, 59):
            raw = datetime(2026, 7, 24, 12, minute, second, tzinfo=UTC)
            assert ceil_to_hour(raw) >= raw


# ------------------------------------------------------------- what is written


def test_the_line_carries_nothing_but_ref_time_code_and_prev_hash(
    erasure_key: bytes,
) -> None:
    """§6.4 fixes four fields, and privacy rule 3 fixes what may not be there."""
    line = JournalLine(
        erasure_ref=erasure_ref(erasure_key, ACCOUNT),
        at=MOMENT,
        code="sync_off",
        prev_hash=GENESIS,
    )

    body = line.to_json()

    assert set(json.loads(body)) == {"erasure_ref", "at", "code", "prev_hash"}
    text = body.decode()
    assert str(ACCOUNT) not in text
    assert ACCOUNT.hex not in text
    for kind in ("health_sync", "telegram_reminders", "cycle_sync"):
        assert kind not in text
    # The exact second of the request is not there either.
    assert "12:00:00Z" in text


def test_the_reference_is_a_pseudonym_and_not_the_account_id(
    erasure_key: bytes,
    head_key: bytes,
) -> None:
    reference = erasure_ref(erasure_key, ACCOUNT)

    assert reference != str(ACCOUNT)
    assert reference != ACCOUNT.hex
    # Deterministic under one key — the runbook maps refs back by recomputing
    # them for every live account, and that only works if it is a function.
    assert reference == erasure_ref(erasure_key, ACCOUNT)
    assert reference != erasure_ref(head_key, ACCOUNT)
    assert reference != erasure_ref(erasure_key, uuid4())


def test_the_canonical_form_is_byte_stable(erasure_key: bytes) -> None:
    """Two equal lines hash equally, or content addressing is a lie."""
    fields = {
        "erasure_ref": erasure_ref(erasure_key, ACCOUNT),
        "at": MOMENT,
        "code": "full",
        "prev_hash": GENESIS,
    }

    first = JournalLine(**fields).to_json()  # type: ignore[arg-type]
    second = JournalLine(**fields).to_json()  # type: ignore[arg-type]

    assert first == second
    assert line_hash(first) == line_hash(second)
    assert JournalLine.from_json(first) == JournalLine(**fields)  # type: ignore[arg-type]


def test_the_key_carries_the_hash_of_the_body(erasure_key: bytes) -> None:
    stored = _chain(erasure_key, "full")[0]

    assert stored.key.endswith(f"/{line_hash(stored.body)}.json")
    assert stored.key.startswith(f"{PREFIX}/2026-07-24/")


# ------------------------------------------------------------ integrity chain


def test_a_linked_chain_verifies(erasure_key: bytes) -> None:
    verdict = verify_chain(_chain(erasure_key, "full", "sync_off", "reminders_off"))

    assert verdict == ChainVerdict(lines=3, breaks=(), forks=())
    assert verdict.intact


def test_a_removed_line_breaks_the_chain_at_its_successor(erasure_key: bytes) -> None:
    """The whole point of `prev_hash`: a line that is gone leaves a hole."""
    chain = _chain(erasure_key, "full", "sync_off", "reminders_off")
    without_the_middle = [chain[0], chain[2]]

    verdict = verify_chain(without_the_middle)

    assert not verdict.intact
    assert len(verdict.breaks) == 1
    assert verdict.breaks[0].key == chain[2].key
    assert verdict.breaks[0].reason == "missing_predecessor"
    assert verdict.breaks[0].prev_hash == line_hash(chain[1].body)


def test_a_substituted_line_is_named_and_orphans_its_successor(
    erasure_key: bytes,
) -> None:
    chain = _chain(erasure_key, "full", "sync_off", "reminders_off")
    forged = JournalLine.from_json(chain[1].body)
    tampered = StoredLine(
        key=chain[1].key,
        body=JournalLine(
            erasure_ref=forged.erasure_ref,
            at=forged.at,
            code="reminders_off",
            prev_hash=forged.prev_hash,
        ).to_json(),
    )

    verdict = verify_chain([chain[0], tampered, chain[2]])

    assert not verdict.intact
    reasons = {(break_.key, break_.reason) for break_ in verdict.breaks}
    # Caught twice over: the key no longer addresses its content, and the line
    # that pointed at the original is now pointing at nothing.
    assert (chain[1].key, "key_does_not_address_body") in reasons
    assert (chain[2].key, "missing_predecessor") in reasons


def test_a_malformed_object_is_a_break_rather_than_a_crash() -> None:
    """An object that addresses its own body and still will not parse.

    The key is built from the garbage so the content-addressing check passes and
    the parser is the one that has to answer — otherwise this test would only
    ever exercise the check above it.
    """
    body = b"{not json"
    key = f"{PREFIX}/2026-07-24/{line_hash(body)}.json"

    verdict = verify_chain([StoredLine(key=key, body=body)])

    assert not verdict.intact
    assert verdict.breaks == (ChainBreak(key=key, reason="malformed"),)


def test_two_writers_on_one_tail_fork_and_lose_nothing(erasure_key: bytes) -> None:
    """The named outcome of concurrency, and it is not a break.

    `OutboxDispatcher` and the synchronous revoke path can append at the same
    moment. Both lines are durable and both reconcile; the chain is a tree at
    that node. Refusing the second write instead would turn a race into a
    refusal to journal — that is, into a refusal to erase.
    """
    chain = _chain(erasure_key, "full")
    sibling_line = JournalLine(
        erasure_ref=erasure_ref(erasure_key, uuid4()),
        at=MOMENT,
        code="sync_off",
        prev_hash=GENESIS,
    )
    sibling_body = sibling_line.to_json()
    sibling = StoredLine(
        key=journal_key(PREFIX, line=sibling_line, body=sibling_body),
        body=sibling_body,
    )

    verdict = verify_chain([*chain, sibling])

    assert verdict.intact
    assert verdict.breaks == ()
    assert len(verdict.forks) == 1
    assert verdict.forks[0].prev_hash == GENESIS
    assert set(verdict.forks[0].keys) == {chain[0].key, sibling.key}


def test_a_known_root_is_not_a_break(erasure_key: bytes) -> None:
    """Verifying one day at a time must not accuse yesterday's tail."""
    chain = _chain(erasure_key, "full", "sync_off")
    yesterday = line_hash(chain[0].body)

    verdict = verify_chain([chain[1]], roots=frozenset({yesterday}))

    assert verdict.intact


# ------------------------------------------------------------------ daily head


def test_the_daily_head_signature_verifies(
    erasure_key: bytes,
    head_key: bytes,
) -> None:
    chain = _chain(erasure_key, "full", "sync_off")
    last = line_hash(chain[1].body)

    signature = sign_head(head_key, day="2026-07-24", last_hash=last)

    assert verify_head(head_key, day="2026-07-24", last_hash=last, signature=signature)


@pytest.mark.parametrize("field", ["day", "last_hash", "key", "signature"])
def test_a_forged_head_is_refused(
    erasure_key: bytes,
    head_key: bytes,
    field: str,
) -> None:
    chain = _chain(erasure_key, "full")
    last = line_hash(chain[0].body)
    signature = sign_head(head_key, day="2026-07-24", last_hash=last)

    claim = {
        "key": head_key,
        "day": "2026-07-24",
        "last_hash": last,
        "signature": signature,
    }
    claim[field] = {
        "day": "2026-07-25",
        "last_hash": "ff" * 32,
        "key": secrets.token_bytes(32),
        "signature": "00" * 32,
    }[field]

    assert not verify_head(
        claim["key"],  # type: ignore[arg-type]
        day=claim["day"],  # type: ignore[arg-type]
        last_hash=claim["last_hash"],  # type: ignore[arg-type]
        signature=claim["signature"],  # type: ignore[arg-type]
    )


# ------------------------------------------------------------ restore interlock


def test_an_empty_journal_on_a_healthy_service_does_not_block_a_restore() -> None:
    """§6.4 states the formula and the reason for it in the same breath.

    Coverage is `min(now − service_start_at, J)` and *not* `now − min(at)`:
    a service that has simply never erased anything has an empty journal, and
    the second formula would read that as zero coverage and block restores
    forever.
    """
    verdict = restore_interlock(
        now=MOMENT,
        service_start_at=MOMENT - timedelta(days=200),
        backup_taken_at=MOMENT - timedelta(days=10),
    )

    assert verdict.coverage == JOURNAL_RETENTION
    assert verdict.covered


def test_a_young_service_cannot_cover_an_older_backup() -> None:
    verdict = restore_interlock(
        now=MOMENT,
        service_start_at=MOMENT - timedelta(days=2),
        backup_taken_at=MOMENT - timedelta(days=10),
    )

    assert verdict.coverage == timedelta(days=2)
    assert not verdict.covered


def test_coverage_stops_at_the_retention() -> None:
    verdict = restore_interlock(
        now=MOMENT,
        service_start_at=MOMENT - timedelta(days=4000),
        backup_taken_at=MOMENT - timedelta(days=91),
    )

    assert verdict.coverage == JOURNAL_RETENTION
    assert not verdict.covered


def test_a_backup_exactly_at_the_coverage_edge_is_covered() -> None:
    verdict = restore_interlock(
        now=MOMENT,
        service_start_at=MOMENT - timedelta(days=30),
        backup_taken_at=MOMENT - timedelta(days=30),
    )

    assert verdict.covered


def test_a_restore_point_in_the_future_is_refused() -> None:
    """Nonsense input must stop the runbook, not sail through it.

    Clamping the age to zero would call this covered, and it is the worst place
    to be permissive: `at >= t_b` would match nothing, so the operator would
    read "covered, zero entries to replay" as "safe" while every erasure the
    journal holds went unreplayed.
    """
    verdict = restore_interlock(
        now=MOMENT,
        service_start_at=MOMENT - timedelta(days=200),
        backup_taken_at=MOMENT + timedelta(hours=1),
    )

    assert not verdict.covered


def test_a_service_start_in_the_future_yields_no_coverage() -> None:
    """A misconfigured `SERVICE_START_AT` blocks restores rather than waving
    them through — the only safe direction for a value nobody can re-derive."""
    verdict = restore_interlock(
        now=MOMENT,
        service_start_at=MOMENT + timedelta(days=1),
        backup_taken_at=MOMENT - timedelta(hours=1),
    )

    assert verdict.coverage == timedelta(0)
    assert not verdict.covered
