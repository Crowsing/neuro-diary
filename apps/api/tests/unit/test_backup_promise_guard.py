"""CI guard: the backup promise has exactly one source in code (§6.4).

**Why this is a test and not a shell grep.** It runs inside the `api` job's
existing `pytest` step, needs no new CI wiring, and — the part that matters — it
is itself under test. A grep rule nobody can run against a counter-example is a
rule nobody can tell is working; the `_rejects` / `_accepts` cases below prove
this one can still fail.

**Why the boundaries are where they are.** A rule that goes red on the consent
copy or on §4.3's erasure window gets switched off by the first developer who
hits it, and a rule that lets a new literal through in a new file is not worth
its line in CI. So there are two checks with different shapes:

* the **literal** `days=30` may appear only in the three named durations that
  already exist. This is what a new unnamed thirty-day quantity looks like. The
  bare integer `30` is deliberately *not* checked: `Literal[7, 30, 90]` in the
  trends contract would make the rule false-red on its first run;
* the **prose** «30 днів» / «30 days» may appear only on lines an allowlist
  names, each with the paragraph it belongs to. Rewording one of them costs one
  allowlist edit — which is exactly the moment somebody should confirm the
  number is still not the backup promise.

Out of scope on purpose: `consent-copy/**` (the number is legitimate there and
the file is frozen), `docs/**`, the tests themselves (their `timedelta(days=30)`
assertions pin §4.3 and §6.2), and `apps/web/**` — the backup promise is a
server-side quantity, and the client renders the text straight from
`consent-copy`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.domain.retention import (
    BACKUP_CONFIG_MAX_AGE_DAYS,
    BACKUP_PROMISE_DAYS,
    JOURNAL_RETENTION_FLOOR_DAYS,
    NONCURRENT_VERSION_EXPIRATION_DAYS,
    OBJECT_LOCK_GOVERNANCE_DAYS,
)

API_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = API_ROOT / "app"
REPO_ROOT = API_ROOT.parents[1]

LITERAL = re.compile(r"days=30\b")
NAMED_DURATION = re.compile(
    r"^\s*(?P<name>[A-Z][A-Z0-9_]*)\s*(?::[^=]+)?=\s*timedelta\(days=30\)\s*$"
)
PROSE = re.compile(r"\b30[ \-]?(днів|денн|days|day)", re.IGNORECASE)

#: The only durations allowed to spell the number out. None of them is the
#: backup promise, and each already has a test pinning its value.
NAMED_THIRTY_DAY_DURATIONS = frozenset(
    {"SESSION_MAX_LIFETIME", "ACCOUNT_ERASURE_DELAY", "OUTBOX_TTL"}
)

#: Prose that may say "30 days", keyed by file and the exact line, each with the
#: paragraph that makes it legitimate. Nothing here is about backups.
ALLOWED_PROSE: dict[tuple[str, str], str] = {
    (
        "app/domain/events.py",
        "the two *timeout* reasons, and only after their 30-day window. Nobody at all",
    ): "§4.3 erasure window",
    (
        "app/infra/db/repositories/consent.py",
        "user's own decision means now, anything else means 30 days. Ordered by",
    ): "§4.3 erasure window",
    (
        "app/infra/db/repositories/outbox.py",
        '"""§6.2: 30 days after `processed_at`, never before it is set."""',
    ): "§6.2 outbox TTL",
    (
        "app/services/consent.py",
        "# 30-day window of §4.3; the sweeper owns that deadline.",
    ): "§4.3 erasure window",
    (
        "app/workers/erasure_worker.py",
        "a common, easily reversed gesture, so those accounts wait 30 days rather than",
    ): "§4.3 erasure window",
    (
        "app/workers/outbox_dispatcher.py",
        "only picks up the two *timeout* reasons, and only after 30 days — nobody at",
    ): "§4.3 erasure window",
    (
        "app/workers/outbox_dispatcher.py",
        "that is not the user's decision — and that one gets the 30-day window of",
    ): "§4.3 erasure window",
}


def _normalise(line: str) -> str:
    return " ".join(line.split())


def offences(path: str, source: str) -> list[str]:
    """Every line of `source` that spells the number without earning it."""
    found: list[str] = []
    for number, raw in enumerate(source.splitlines(), 1):
        line = _normalise(raw)
        if LITERAL.search(line):
            match = NAMED_DURATION.match(raw)
            if match is None or match.group("name") not in NAMED_THIRTY_DAY_DURATIONS:
                found.append(f"{path}:{number}: unnamed 30-day duration: {line}")
        if PROSE.search(line) and (path, line) not in ALLOWED_PROSE:
            found.append(f"{path}:{number}: undeclared «30 days»: {line}")
    return found


def _scan() -> list[str]:
    return [
        offence
        for path in sorted(APP_ROOT.rglob("*.py"))
        for offence in offences(
            str(path.relative_to(API_ROOT)),
            path.read_text(encoding="utf-8"),
        )
    ]


# --------------------------------------------------------------- the rule runs


def test_the_backup_promise_has_one_source_in_code() -> None:
    assert _scan() == []


def test_the_allowlist_has_no_dead_entries() -> None:
    """An entry nobody needs is a hole waiting for a coincidence to fill it."""
    present = {
        (str(path.relative_to(API_ROOT)), _normalise(raw))
        for path in APP_ROOT.rglob("*.py")
        for raw in path.read_text(encoding="utf-8").splitlines()
    }

    assert set(ALLOWED_PROSE) <= present


# ------------------------------------------------------- the rule can still fail


@pytest.mark.parametrize(
    "source",
    [
        "NEW_TTL = timedelta(days=30)",
        "SOMETHING = timedelta(days=30)  # backups",
        "delta = timedelta(days=30)",
        "unit.sweep(before=now - timedelta(days=30))",
    ],
)
def test_it_rejects_a_new_unnamed_duration(source: str) -> None:
    assert offences("app/whatever.py", source)


@pytest.mark.parametrize(
    "source",
    [
        "# copies disappear after 30 days",
        '"""Резервні копії зникають через 30 днів."""',
        "# the 30-day backup promise",
        "# 30-денна обіцянка",
    ],
)
def test_it_rejects_a_new_prose_mention(source: str) -> None:
    assert offences("app/whatever.py", source)


@pytest.mark.parametrize(
    "source",
    [
        "SESSION_MAX_LIFETIME = timedelta(days=30)",
        "ACCOUNT_ERASURE_DELAY = timedelta(days=30)",
        "OUTBOX_TTL = timedelta(days=30)",
        "PROMISE = timedelta(days=BACKUP_PROMISE_DAYS)",
        "period: Literal[7, 30, 90] = 30",
        'local_time = time.fromisoformat("21:30")',
        "# 30 характеристик",
        "MAX_RECORDS_PER_PUSH = 30",
    ],
)
def test_it_accepts_what_is_not_the_promise(source: str) -> None:
    """The four unrelated thirty-day values and the near misses around them.

    A rule that fired on any of these would be switched off within a week.
    """
    assert offences("app/whatever.py", source) == []


def test_a_declared_line_passes_only_in_its_own_file() -> None:
    """The allowlist is keyed by file, so copying a line elsewhere still fails."""
    path, line = next(iter(ALLOWED_PROSE))

    assert offences(path, line) == []
    assert offences("app/somewhere_else.py", line)


# ------------------------------------------------------------------- constants


def test_the_promise_is_the_number_the_user_is_given() -> None:
    """§6.4: the text is the source of truth and the constant tracks it.

    The file is frozen at `deletion@1.0`, so this asserts in one direction only
    — that the constant still says what the user was told.
    """
    promise = (REPO_ROOT / "consent-copy/uk/deletion/1.0.md").read_text(
        encoding="utf-8"
    )

    assert f"через {BACKUP_PROMISE_DAYS} днів" in promise


def test_the_retention_invariants_hold() -> None:
    """Pinned, so a future edit to one of them has to face the arithmetic."""
    assert BACKUP_PROMISE_DAYS == 30
    assert BACKUP_CONFIG_MAX_AGE_DAYS == 28
    assert OBJECT_LOCK_GOVERNANCE_DAYS == 21
    assert NONCURRENT_VERSION_EXPIRATION_DAYS == 28
    assert JOURNAL_RETENTION_FLOOR_DAYS == 36
