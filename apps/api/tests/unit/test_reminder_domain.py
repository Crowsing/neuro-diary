"""Quiet hours and DST-correct scheduling.

The quiet-hours policy lives here as a single constant (§10) and is exported to
web through a shared fixture; bot and web never restate it.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from app.domain.identity import UnknownTimezone
from app.domain.reminders import (
    QUIET_HOURS_END,
    QUIET_HOURS_START,
    in_quiet_hours,
    next_fire_at,
)

KYIV = "Europe/Kyiv"


def test_quiet_window_matches_the_gate_d_decision() -> None:
    assert QUIET_HOURS_START == time(22, 0)
    assert QUIET_HOURS_END == time(8, 0)


@pytest.mark.parametrize(
    ("value", "quiet"),
    [
        (time(7, 59), True),
        (time(8, 0), False),
        (time(21, 59), False),
        (time(22, 0), True),
        (time(0, 0), True),
        (time(3, 30), True),
        (time(20, 0), False),
        (time(23, 59), True),
    ],
)
def test_quiet_hours_boundaries(value: time, quiet: bool) -> None:
    assert in_quiet_hours(value) is quiet


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(UnknownTimezone):
        next_fire_at("Mars/Olympus", time(20, 0), datetime.now(UTC))


def test_next_fire_is_today_when_the_local_time_is_still_ahead() -> None:
    after = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)  # 15:00 in Kyiv

    assert next_fire_at(KYIV, time(20, 0), after) == datetime(
        2026, 7, 24, 17, 0, tzinfo=UTC
    )


def test_next_fire_rolls_over_when_the_local_time_has_passed() -> None:
    after = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)  # exactly 20:00 in Kyiv

    assert next_fire_at(KYIV, time(20, 0), after) == datetime(
        2026, 7, 25, 17, 0, tzinfo=UTC
    )


def test_spring_forward_keeps_a_twenty_three_hour_interval() -> None:
    """§10 fixture: 2026-03-28T18:00Z → 2026-03-29T17:00Z, exactly 23 hours."""
    before = datetime(2026, 3, 28, 17, 0, tzinfo=UTC)
    first = next_fire_at(KYIV, time(20, 0), before)
    second = next_fire_at(KYIV, time(20, 0), first)

    assert first == datetime(2026, 3, 28, 18, 0, tzinfo=UTC)
    assert second == datetime(2026, 3, 29, 17, 0, tzinfo=UTC)
    assert second - first == (second - first).__class__(hours=23)


def test_fall_back_keeps_a_twenty_five_hour_interval() -> None:
    before = datetime(2026, 10, 24, 16, 0, tzinfo=UTC)
    first = next_fire_at(KYIV, time(20, 0), before)
    second = next_fire_at(KYIV, time(20, 0), first)

    assert first == datetime(2026, 10, 24, 17, 0, tzinfo=UTC)
    assert second == datetime(2026, 10, 25, 18, 0, tzinfo=UTC)
    assert second - first == (second - first).__class__(hours=25)


def test_a_nonexistent_local_time_fires_at_the_first_valid_instant() -> None:
    """Kyiv skips 03:00–04:00 on 2026-03-29; 03:30 never happens."""
    before = datetime(2026, 3, 28, 22, 0, tzinfo=UTC)

    assert next_fire_at(KYIV, time(3, 30), before) == datetime(
        2026, 3, 29, 1, 0, tzinfo=UTC
    )


def test_an_ambiguous_local_time_fires_at_its_first_occurrence() -> None:
    """Kyiv repeats 03:00–04:00 on 2026-10-25; fold=0 wins."""
    before = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)

    assert next_fire_at(KYIV, time(3, 30), before) == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )


def test_schedules_are_never_advanced_by_adding_a_day() -> None:
    """Twelve consecutive fires across the spring transition stay at 20:00 local."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(KYIV)
    instant = datetime(2026, 3, 25, 12, 0, tzinfo=UTC)
    for _ in range(12):
        instant = next_fire_at(KYIV, time(20, 0), instant)
        assert instant.astimezone(zone).time() == time(20, 0)
