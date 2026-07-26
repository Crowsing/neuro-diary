"""The shape of a §11 per-account window, shared by the services that use one.

It lives in the domain rather than beside the first caller because the second
caller may not reach that one: the import-linter contract «Reminder worker never
touches vault» forbids `app.services.reminder` from importing
`app.services.sync`, where this arithmetic was written first. Copying three
lines across that boundary would leave two fixed-window definitions that agree
today, and the way they stop agreeing is silent — one rounds, the other does
not, and a limit becomes twice what §11 says on the minute boundary.
"""

from __future__ import annotations

from datetime import datetime


def window_start(now: datetime, seconds: int) -> datetime:
    """Fixed window: the same instant for every request of one period."""
    epoch = int(now.timestamp()) // seconds * seconds
    return datetime.fromtimestamp(epoch, tz=now.tzinfo)
