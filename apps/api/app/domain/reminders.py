"""Reminder scheduling domain: quiet hours and DST-correct next-fire times.

The quiet-hours policy is a single constant here (§10). Advancing a schedule by
adding a timedelta is forbidden — that is precisely how DST bugs appear — so
every fire time is resolved from a local calendar date.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.identity import UnknownTimezone

# Gate D: a hard ban, with no field to confirm a night-time slot.
QUIET_HOURS_START = time(22, 0)
QUIET_HOURS_END = time(8, 0)

_LOOKAHEAD_DAYS = 4
_TRANSITION_PRECISION = timedelta(seconds=1)


def in_quiet_hours(value: time) -> bool:
    """`t >= 22:00 OR t < 08:00` — 08:00 allowed, 07:59 not; 21:59 yes, 22:00 no."""
    return value >= QUIET_HOURS_START or value < QUIET_HOURS_END


def zone_for(timezone_name: str) -> ZoneInfo:
    """Validate through the timezone database, storing the name verbatim (§10)."""
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise UnknownTimezone() from error


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
