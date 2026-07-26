"""Reminder scheduling domain: quiet hours and DST-correct next-fire times.

The quiet-hours policy is a single constant here (§10). Advancing a schedule by
adding a timedelta is forbidden — that is precisely how DST bugs appear — so
every fire time is resolved from a local calendar date.

The delivery timings of §10 live here too, and the consistency condition
between them is checked **at import with `raise`**, exactly as
`app.domain.retention` checks its own. The reasoning is the same and so is the
requirement behind it: `python -O` strips `assert`, so a static check written
that way disappears in production and nowhere else. That argument only holds if
a running process actually imports this module — `app.workers.reminder_worker`
imports it directly, `app.worker_main` imports that, and a test starts a fresh
interpreter to confirm the chain rather than asserting it in prose.

A comment beside the three numbers would not be a check. The failure it guards
is silent and expensive: if one attempt could outlive the sweeper threshold,
the sweeper would declare a live attempt dead, and at-most-once would turn into
the duplicate it exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.identity import DomainError, UnknownTimezone

# Gate D: a hard ban, with no field to confirm a night-time slot.
QUIET_HOURS_START = time(22, 0)
QUIET_HOURS_END = time(8, 0)

_LOOKAHEAD_DAYS = 4
_TRANSITION_PRECISION = timedelta(seconds=1)

#: The only value `ck_reminder_schedule_disabled_reason` admits. A schedule
#: switched off without it is the pause of §10, and the reconciler must never
#: confuse the two.
BOT_BLOCKED = "bot_blocked"


class DeliveryStatus(StrEnum):
    """The five values `ck_reminder_delivery_status` admits, and no sixth."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED_STALE = "skipped_stale"
    SKIPPED_QUIET = "skipped_quiet"


class Decision(StrEnum):
    """What the worker should do with an occurrence it has claimed."""

    SEND = "send"
    SKIP_QUIET = "skip_quiet"
    SKIP_STALE = "skip_stale"


class SendOutcome(StrEnum):
    """What Telegram did with one attempt, in the three shapes §10 acts on.

    `FAILED` deliberately covers 400, every 5xx, a timeout and an exhausted
    budget as one outcome, because the worker's response to all four is
    identical: write `failed`, change neither the schedule nor the consent, and
    never try that day again. Splitting them would create a branch whose only
    possible use is to retry something at-most-once forbids retrying.
    """

    SENT = "sent"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SendReceipt:
    outcome: SendOutcome
    #: Present exactly when `outcome is SENT`. It is what §6.4 later needs to
    #: delete the message, and the only value Telegram returns that is kept.
    message_id: int | None = None


class NoSchedule(DomainError):
    """§10: there is a consent but no schedule row to read or change."""

    code = "no_schedule"


# §10 lists a sixth code, `bot_blocked` (409), and this implementation never
# emits it — recorded as a deviation rather than left as a silent gap.
#
# It would answer a `PUT` that turns reminders on while the schedule carries a
# Telegram block. Refusing there looks protective and is not: the server cannot
# verify the claim either way, and every escape route from the refusal is worse
# than accepting it. Clearing the block on *any* successful write would let an
# ordinary change of hour delete the fourteen-day deadline in silence; requiring
# a withdrawal of consent would erase the account of anyone whose only consent
# this is; and refusing without an escape route is a state the user cannot leave.
#
# So `enabled: true` is accepted and re-arms the schedule; if the bot is still
# blocked the next attempt takes a 403 and a **new** streak begins — which is
# precisely what «поспіль» asks for. The state remains visible: `GET` still
# reports `bot_blocked`.


# ------------------------------------------------------------ outbound content

#: §10 exact allowlist, confirmed verbatim by Gate D. No interpolation, ever.
#: The absent full stop is part of the string, not an oversight.
MESSAGE_TEXT = "Час зробити короткий запис"
BUTTON_LABEL = "Відкрити щоденник"

#: §10: the metadata a stranger can read off a lock screen or a CT log. The
#: check that uses these is real rather than decorative, so the list lives in
#: the domain and both the surface test and any future guard read this one copy.
FORBIDDEN_SURFACE_TOKENS = (
    "невро",
    "neuro",
    "симптом",
    "symptom",
    "мігрен",
    "migrain",
    "епілепс",
    "epilep",
    "склероз",
    "sclero",
    "здоров",
    "health",
    "діагн",
    "diagn",
)


def forbidden_tokens_in(value: str) -> list[str]:
    """Every §10 token present in `value`, case-insensitively."""
    lowered = value.lower()
    return [token for token in FORBIDDEN_SURFACE_TOKENS if token in lowered]


# ------------------------------------------------------------- delivery timing

#: §11: `reminders-settings 20/хв`, a per-account window in PostgreSQL like the
#: other three application budgets — not the in-memory per-IP limiter, which
#: §11 keeps for auth because its key is an address.
SETTINGS_BUCKET = "reminders_settings"
SETTINGS_REQUESTS_PER_MINUTE = 20
SETTINGS_WINDOW = timedelta(minutes=1)

#: §10: everything one attempt may spend, including waiting out a `retry_after`.
#: The worker writes `failed` itself when it runs out rather than leaving the
#: row for the sweeper.
ATTEMPT_BUDGET = timedelta(minutes=10)

#: §10: a `pending` row older than this is `failed`, and never re-sent.
SWEEPER_THRESHOLD = timedelta(minutes=15)

#: §10: the longest the sweeper may go between passes. It is part of the
#: arithmetic below, not decoration: the sweeper can be a whole period late.
SWEEPER_PERIOD = timedelta(minutes=5)

#: §10, reduced from four hours by Gate D: a reminder to make a short note is
#: no longer doing its job an hour later, but it does arrive at a moment the
#: user did not choose.
CATCH_UP_WINDOW = timedelta(minutes=60)

#: §10: how long a block has to stand, without interruption, before it reaches
#: the consent. Fourteen consecutive days, counted from `disabled_at`.
BOT_BLOCKED_STREAK = timedelta(days=14)

#: §6.4: how long the server may keep a chat id and a message id after the rows
#: they belong to are gone — for the single purpose of deleting those messages.
MESSAGE_CLEANUP_TTL = timedelta(hours=48)

#: §10 pacing. Telegram's documented global ceiling, spent by a token bucket.
SEND_RATE_PER_SECOND = 25


if ATTEMPT_BUDGET >= SWEEPER_THRESHOLD:
    raise ValueError(
        "§10: the budget of one attempt must stay below the sweeper threshold,"
        " or the sweeper marks an attempt failed while it is still running —"
        " and at-most-once becomes the duplicate it exists to prevent"
    )

if ATTEMPT_BUDGET + SWEEPER_PERIOD > SWEEPER_THRESHOLD:
    raise ValueError(
        "§10: a sweeper pass can be a whole period late, so the threshold has"
        " to cover the budget plus that lateness — otherwise the strict"
        " inequality above holds on paper and fails on a busy host"
    )

if CATCH_UP_WINDOW >= timedelta(days=1):
    raise ValueError(
        "§10: catch-up may never reach a previous day — a reminder claimed for"
        " one local date must not be sent on another"
    )


def in_quiet_hours(value: time) -> bool:
    """`t >= 22:00 OR t < 08:00` — 08:00 allowed, 07:59 not; 21:59 yes, 22:00 no."""
    return value >= QUIET_HOURS_START or value < QUIET_HOURS_END


def zone_for(timezone_name: str) -> ZoneInfo:
    """Validate through the **pinned** timezone database (§10).

    Not `ZoneInfo(name)`. That constructor searches `TZPATH` first, and on every
    machine this code has ever run on — macOS, the CI image, a Debian container
    — `/usr/share/zoneinfo` exists and wins. The pinned `tzdata` dependency
    would then be an unused package, every DST fixture in this suite would
    silently assert a property of the host image, and §10's «а не tzdata
    хоста» would be a sentence rather than a fact.

    Loading the file out of the package sidesteps that without mutating global
    state: `zoneinfo.reset_tzpath` would work too, but it does not invalidate
    the `ZoneInfo` cache, so any zone another module had already constructed
    would keep the host's rules and nothing would say so.

    The name is stored verbatim by the caller; no alias is canonicalised here,
    which is why the membership test is against the package's own list rather
    than against a set of canonical zones.
    """
    try:
        return _packaged_zone(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, OSError, ModuleNotFoundError) as error:
        raise UnknownTimezone() from error


@lru_cache(maxsize=None)
def _zone_names() -> frozenset[str]:
    """Every name the pinned database ships, canonical zones and links alike."""
    listing = resources.files("tzdata").joinpath("zones").read_text(encoding="utf-8")
    return frozenset(listing.split())


@lru_cache(maxsize=None)
def _packaged_zone(timezone_name: str) -> ZoneInfo:
    if timezone_name not in _zone_names():
        # Also the traversal guard: only names the package itself lists ever
        # reach the resource lookup below.
        raise UnknownTimezone()
    package, _, leaf = f"tzdata.zoneinfo.{timezone_name}".replace("/", ".").rpartition(
        "."
    )
    with resources.files(package).joinpath(leaf).open("rb") as handle:
        return ZoneInfo.from_file(handle, key=timezone_name)


def next_fire_at(
    timezone_name: str,
    local_time: time,
    after: datetime,
) -> datetime:
    """Return the first UTC instant strictly after `after` at `local_time`."""
    zone = zone_for(timezone_name)
    candidate_date = after.astimezone(zone).date()

    for _ in range(_LOOKAHEAD_DAYS):
        instant = resolve_local(candidate_date, local_time, zone)
        if instant > after:
            return instant
        candidate_date += timedelta(days=1)

    raise UnknownTimezone()


def resolve_local(day: date, local_time: time, zone: ZoneInfo) -> datetime:
    """Map a wall-clock time to UTC, resolving both DST irregularities.

    Fall back (the time happens twice) takes the first occurrence, `fold=0`.
    Spring forward (the time never happens) takes the first valid instant after
    the gap — the transition itself, not the same wall clock an hour later.
    """
    naive = datetime.combine(day, local_time)
    early = naive.replace(tzinfo=zone, fold=0).astimezone(UTC)
    late = naive.replace(tzinfo=zone, fold=1).astimezone(UTC)

    if early <= late:
        # Unambiguous, or ambiguous with the first occurrence earlier.
        return early
    return _transition_between(late, early, zone)


def local_date_of(instant: datetime, timezone_name: str) -> date:
    """The calendar day an occurrence belongs to, in the schedule's own zone.

    This is the second half of the `reminder_delivery` primary key, so it is the
    identity of the occurrence rather than a display value: two instants that
    map to one local date are the same reminder, and one of them is never sent.
    """
    return instant.astimezone(zone_for(timezone_name)).date()


def decide(
    *,
    scheduled_for: datetime,
    now: datetime,
    timezone_name: str,
) -> Decision:
    """§10, in the order §10 states it: catch-up, same day, quiet hours.

    The quiet check is on the **actual** moment of sending, not on the planned
    one. That is the whole difference between the two skip statuses: a schedule
    at 21:30 delayed by forty minutes is inside the catch-up window and on the
    right day, and it is still refused — at 22:10 the message would arrive in
    the hours Gate D closed, and a reminder that wakes someone is precisely the
    harm the feature exists to avoid.
    """
    zone = zone_for(timezone_name)
    if now - scheduled_for > CATCH_UP_WINDOW:
        return Decision.SKIP_STALE
    if now.astimezone(zone).date() != scheduled_for.astimezone(zone).date():
        return Decision.SKIP_STALE
    if in_quiet_hours(now.astimezone(zone).time()):
        return Decision.SKIP_QUIET
    return Decision.SEND


def attempt_deadline(*, started_at: datetime, timezone_name: str) -> datetime:
    """When this attempt must be over: the budget, or nightfall, whichever first.

    §10 allows `retry_after` to be honoured «в межах вікна валідності тієї ж
    доби», and quiet hours are what ends that window: an occurrence claimed for
    one local date must not be delivered after 22:00 local, whatever budget is
    left. Taking the minimum keeps both rules in one value, so no caller can
    satisfy one of them and forget the other.
    """
    zone = zone_for(timezone_name)
    nightfall = resolve_local(
        started_at.astimezone(zone).date(),
        QUIET_HOURS_START,
        zone,
    )
    return min(started_at + ATTEMPT_BUDGET, nightfall)


def _transition_between(lower: datetime, upper: datetime, zone: ZoneInfo) -> datetime:
    """Binary-search the instant the offset changes inside a gap."""
    baseline = lower.astimezone(zone).utcoffset()
    while upper - lower > _TRANSITION_PRECISION:
        middle = lower + (upper - lower) / 2
        if middle.astimezone(zone).utcoffset() == baseline:
            lower = middle
        else:
            upper = middle
    return upper.replace(microsecond=0)
