"""The §11 registry: every bucket, its limit, its window, and the arithmetic.

The three budget numbers asserted here used to live in
`tests/unit/test_vault_domain.py`, which pinned the limits and nothing else. This
pins the windows too — a limit of 60 over an hour is not `sync 60/хв`, and the
old assertion could not tell the two apart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.rate_limits import BUDGETS, RateBucket, window_start


def test_the_buckets_are_the_four_of_section_eleven_plus_the_one_phase_five_adds() -> (
    None
):
    assert {bucket.value for bucket in RateBucket} == {
        "sync",
        "push_bytes",
        "key_read",
        "reminders_settings",
        "account_ops",
    }


def test_the_budgets_match_section_eleven() -> None:
    assert BUDGETS[RateBucket.SYNC].limit == 60
    assert BUDGETS[RateBucket.SYNC].window == timedelta(minutes=1)
    assert BUDGETS[RateBucket.PUSH_BYTES].limit == 5 * 1024 * 1024
    assert BUDGETS[RateBucket.PUSH_BYTES].window == timedelta(minutes=1)
    assert BUDGETS[RateBucket.KEY_READ].limit == 10
    assert BUDGETS[RateBucket.KEY_READ].window == timedelta(hours=1)
    assert BUDGETS[RateBucket.REMINDERS_SETTINGS].limit == 20
    assert BUDGETS[RateBucket.REMINDERS_SETTINGS].window == timedelta(minutes=1)


def test_the_new_budget_is_the_shape_of_a_settings_window() -> None:
    """§11 names no number here, so the one chosen is stated rather than implied."""
    assert BUDGETS[RateBucket.ACCOUNT_OPS].limit == 20
    assert BUDGETS[RateBucket.ACCOUNT_OPS].window == timedelta(minutes=1)


def test_every_bucket_has_a_budget() -> None:
    """A bucket without one raises `KeyError` on the first request that uses it."""
    assert set(BUDGETS) == set(RateBucket)


def test_every_budget_is_positive() -> None:
    for bucket, budget in BUDGETS.items():
        assert budget.limit > 0, bucket
        assert budget.window > timedelta(0), bucket


def test_the_registry_cannot_be_edited_at_runtime() -> None:
    """A limit that a caller can widen in place is not a limit.

    `BUDGETS` is a read-only view for exactly this reason: a service that could
    raise its own ceiling would make every assertion in this module a statement
    about import time only.
    """
    with pytest.raises(TypeError):
        BUDGETS[RateBucket.SYNC] = BUDGETS[RateBucket.KEY_READ]  # type: ignore[index]


def test_the_window_is_fixed_and_not_sliding() -> None:
    """Every request of one period sees the same start instant."""
    first = datetime(2026, 7, 26, 12, 0, 1, tzinfo=UTC)
    last = datetime(2026, 7, 26, 12, 0, 59, tzinfo=UTC)
    next_period = datetime(2026, 7, 26, 12, 1, 0, tzinfo=UTC)

    assert window_start(first, 60) == window_start(last, 60)
    assert window_start(next_period, 60) != window_start(last, 60)
    assert window_start(first, 60) == datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def test_the_hour_window_rounds_to_the_hour() -> None:
    moment = datetime(2026, 7, 26, 12, 34, 56, tzinfo=UTC)

    assert window_start(moment, 3600) == datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
