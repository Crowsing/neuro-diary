"""The one place a §11 per-account window is spent.

Before Phase 5 this arithmetic existed twice — `SyncService.consume_budget` and
`ReminderService.consume_budget` — because the import-linter contract «Reminder
worker never touches vault» forbids the second from importing the first. Two
copies that agree today is exactly the shape the domain module warns about, and
Phase 5 needs a third caller anyway: the entry dependency of §11, which spends
the budget *before* it knows whether the consent allows the request through.

The service holds no state beyond the clock. It is not merged into the unit of
work because the transaction boundary matters and belongs to the caller: a
budget consumed inside the transaction it guards is refunded by every refusal,
and a client looping on a 409 would then push for free exactly while it
misbehaves.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.domain.rate_limits import BUDGETS, RateBucket, window_start
from app.services.ports import Clock, RateVerdict, UnitOfWork


class RateLimitService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def now(self) -> datetime:
        return self._clock.now()

    def consume(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        bucket: RateBucket,
        now: datetime,
        cost: int = 1,
    ) -> RateVerdict:
        """Charge `cost` to one fixed window and say whether it still fits."""
        budget = BUDGETS[bucket]
        seconds = int(budget.window.total_seconds())
        return unit.rate_windows.consume(
            account_id,
            bucket=bucket.value,
            cost=cost,
            limit=budget.limit,
            window_start=window_start(now, seconds),
            window_seconds=seconds,
            now=now,
        )
