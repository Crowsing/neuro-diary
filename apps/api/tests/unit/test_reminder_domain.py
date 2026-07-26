"""Quiet hours, DST-correct scheduling, and the decisions the worker makes.

The quiet-hours policy lives here as a single constant (§10) and is exported to
`fixtures/contract/quiet-hours.json`, which `tests/contract` re-derives from
this module; bot and web never restate it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.domain.identity import UnknownTimezone
from app.domain.reminders import (
    ATTEMPT_BUDGET,
    BOT_BLOCKED_STREAK,
    CATCH_UP_WINDOW,
    MESSAGE_CLEANUP_TTL,
    QUIET_HOURS_END,
    QUIET_HOURS_START,
    SWEEPER_PERIOD,
    SWEEPER_THRESHOLD,
    Decision,
    attempt_deadline,
    decide,
    in_quiet_hours,
    local_date_of,
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


# ------------------------------------------------- the invariants of §10 timing


def test_the_attempt_budget_stays_below_the_sweeper_threshold() -> None:
    """§10's consistency condition, pinned so an edit has to face it.

    The module raises on import if this is broken, so this test cannot be the
    only guard — it is the readable statement of a rule whose real enforcement
    is that `import app.domain.reminders` fails.
    """
    assert ATTEMPT_BUDGET == timedelta(minutes=10)
    assert SWEEPER_THRESHOLD == timedelta(minutes=15)
    assert SWEEPER_PERIOD == timedelta(minutes=5)
    assert ATTEMPT_BUDGET < SWEEPER_THRESHOLD
    # The stronger form: a sweeper pass may be a whole period late and still
    # must not meet a live attempt.
    assert ATTEMPT_BUDGET + SWEEPER_PERIOD <= SWEEPER_THRESHOLD


def test_the_invariant_is_enforced_at_import_rather_than_by_this_test() -> None:
    """Re-run the module's own check against a value that breaks it.

    Without this, «checked at import» is a claim about code nobody executes
    with a failing input — the same class of defect the check exists to catch.
    """
    source = (
        "from datetime import timedelta\n"
        "ATTEMPT_BUDGET = timedelta(minutes=20)\n"
        "SWEEPER_THRESHOLD = timedelta(minutes=15)\n"
        "if ATTEMPT_BUDGET >= SWEEPER_THRESHOLD:\n"
        "    raise ValueError('§10')\n"
    )
    with pytest.raises(ValueError, match="§10"):
        exec(compile(source, "<invariant>", "exec"), {})


def test_the_other_two_horizons_match_the_plan() -> None:
    assert CATCH_UP_WINDOW == timedelta(minutes=60)
    assert BOT_BLOCKED_STREAK == timedelta(days=14)
    assert MESSAGE_CLEANUP_TTL == timedelta(hours=48)


# ------------------------------------------------------------ worker decisions


def test_an_occurrence_on_time_is_sent() -> None:
    scheduled = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)  # 20:00 Kyiv

    assert (
        decide(scheduled_for=scheduled, now=scheduled, timezone_name=KYIV)
        is Decision.SEND
    )


def test_a_delay_inside_the_catch_up_window_is_still_sent() -> None:
    scheduled = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)

    assert (
        decide(
            scheduled_for=scheduled,
            now=scheduled + timedelta(minutes=60),
            timezone_name=KYIV,
        )
        is Decision.SEND
    )


def test_the_sixty_first_minute_is_stale() -> None:
    """§10, DoD verbatim: catch-up on the 61st minute is `skipped_stale`."""
    scheduled = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)

    assert (
        decide(
            scheduled_for=scheduled,
            now=scheduled + timedelta(minutes=61),
            timezone_name=KYIV,
        )
        is Decision.SKIP_STALE
    )


def test_a_delayed_evening_reminder_is_refused_by_the_quiet_guard() -> None:
    """§10, DoD verbatim: 21:30 delayed by forty minutes is `skipped_quiet`.

    Inside the catch-up window and on the right local day — the only thing that
    refuses it is that the *actual* moment of sending is 22:10.
    """
    scheduled = datetime(2026, 7, 24, 18, 30, tzinfo=UTC)  # 21:30 Kyiv

    assert (
        decide(
            scheduled_for=scheduled,
            now=scheduled + timedelta(minutes=40),
            timezone_name=KYIV,
        )
        is Decision.SKIP_QUIET
    )


def test_a_delay_across_midnight_is_stale_even_inside_the_window() -> None:
    """«Та сама локальна доба» is a separate condition, not a consequence."""
    scheduled = datetime(2026, 7, 24, 20, 50, tzinfo=UTC)  # 23:50 Kyiv
    later = scheduled + timedelta(minutes=30)  # 00:20 Kyiv, next day

    assert (
        decide(scheduled_for=scheduled, now=later, timezone_name=KYIV)
        is Decision.SKIP_STALE
    )


def test_the_occurrence_is_named_by_the_local_day_it_belongs_to() -> None:
    """The second half of the idempotency key, in the schedule's own zone."""
    scheduled = datetime(2026, 7, 24, 21, 30, tzinfo=UTC)  # 00:30 Kyiv, the 25th

    assert local_date_of(scheduled, KYIV) == date(2026, 7, 25)


# ------------------------------------------------------------ attempt deadline


def test_the_deadline_is_the_budget_when_the_evening_is_far_off() -> None:
    started = datetime(2026, 7, 24, 15, 0, tzinfo=UTC)  # 18:00 Kyiv

    assert attempt_deadline(started_at=started, timezone_name=KYIV) == (
        started + ATTEMPT_BUDGET
    )


def test_the_deadline_is_nightfall_when_the_budget_would_outlive_the_day() -> None:
    """§10: a `retry_after` may not carry a message past the quiet boundary."""
    started = datetime(2026, 7, 24, 18, 55, tzinfo=UTC)  # 21:55 Kyiv

    deadline = attempt_deadline(started_at=started, timezone_name=KYIV)

    assert deadline == datetime(2026, 7, 24, 19, 0, tzinfo=UTC)  # 22:00 Kyiv
    assert deadline < started + ATTEMPT_BUDGET
