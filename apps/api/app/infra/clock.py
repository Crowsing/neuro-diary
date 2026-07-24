"""Clock adapter. Services take the `Clock` port so tests can freeze time."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
