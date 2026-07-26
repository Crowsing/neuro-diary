"""Every §11 per-account window, in one registry.

It lives in the domain rather than beside the first caller because the second
caller may not reach that one: the import-linter contract «Reminder worker never
touches vault» forbids `app.services.reminder` from importing
`app.services.sync`, where this arithmetic was written first. Copying three
lines across that boundary would leave two fixed-window definitions that agree
today, and the way they stop agreeing is silent — one rounds, the other does
not, and a limit becomes twice what §11 says on the minute boundary.

Phase 5 turns that one function into the whole registry, and for a related
reason. §11 names four application budgets; the endpoints it does not name had
no window at all, and «which endpoints are limited» was answerable only by
reading every router and cross-checking it against every call site. That is not
a property a test can hold. With the buckets, their limits and their windows in
one mapping, `tests/unit/test_rate_limit_surface.py` can assert that every
published operation has a *named* status — a bucket or a recorded reason for
having none — and a new endpoint that arrives without one fails CI.

`ACCOUNT_OPS` is the bucket §11 does not name. The list of §11 is read here as a
floor rather than a ceiling: the sixteenth deviation records the decision and
the two endpoints deliberately left out of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class RateBucket(StrEnum):
    """The buckets `ck_rate_window_bucket` admits, and no fifth string.

    The CHECK constraint is the authority; a service that invented a bucket name
    would get a constraint violation rather than a silent unlimited endpoint,
    which is why the bucket domain is a migration and not a constant.
    """

    SYNC = "sync"
    PUSH_BYTES = "push_bytes"
    KEY_READ = "key_read"
    REMINDERS_SETTINGS = "reminders_settings"
    #: Migration 0006. Session and account operations that write or read rows
    #: under a Bearer token and that §11 does not name.
    ACCOUNT_OPS = "account_ops"


@dataclass(frozen=True, slots=True)
class Budget:
    """How much fits in one fixed window, and how long the window is."""

    limit: int
    window: timedelta


#: §11 verbatim for the first four, plus the one Phase 5 adds.
#:
#: `PUSH_BYTES` is the only budget whose cost is not one per request: it is
#: charged the byte volume of the push, which is why it cannot be spent by the
#: entry dependency and stays in the handler.
BUDGETS: Mapping[RateBucket, Budget] = MappingProxyType(
    {
        RateBucket.SYNC: Budget(limit=60, window=timedelta(minutes=1)),
        RateBucket.PUSH_BYTES: Budget(
            limit=5 * 1024 * 1024,
            window=timedelta(minutes=1),
        ),
        RateBucket.KEY_READ: Budget(limit=10, window=timedelta(hours=1)),
        RateBucket.REMINDERS_SETTINGS: Budget(limit=20, window=timedelta(minutes=1)),
        RateBucket.ACCOUNT_OPS: Budget(limit=20, window=timedelta(minutes=1)),
    }
)


def window_start(now: datetime, seconds: int) -> datetime:
    """Fixed window: the same instant for every request of one period."""
    epoch = int(now.timestamp()) // seconds * seconds
    return datetime.fromtimestamp(epoch, tz=now.tzinfo)
