"""Reminder settings: the `ReminderSettings` resource of §10.

The service owns three rules the router must not be trusted with, because each
of them is a privacy decision rather than a validation:

* **the resource is replaced, never patched.** `next_fire_at` is recomputed
  from the local calendar date on every write (§10 forbids advancing a schedule
  by adding a `timedelta`, and a partial write would reintroduce the same drift
  from the other side);
* **no delivery ever leaves this module.** `GET` answers with two aggregate
  booleans and nothing else — a delivery history is a time series of a person's
  interaction with a medical application, and §10 says it must not exist in the
  API at all. There is no method here that could return one;
* **turning reminders back on after a block is two deliberate steps.** See
  `update`.

Import-linter keeps this module away from the vault (`app.services.sync`,
`app.domain.vault`, the vault repository), which is why the §11 window
arithmetic is imported from `app.domain.rate_limits` rather than from the sync
service that also uses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from uuid import UUID

from app.domain.identity import QuietHoursViolation
from app.domain.rate_limits import window_start
from app.domain.records import RateVerdict
from app.domain.records import ReminderSchedule as ReminderScheduleRecord
from app.domain.reminders import (
    BOT_BLOCKED,
    SETTINGS_BUCKET,
    SETTINGS_REQUESTS_PER_MINUTE,
    SETTINGS_WINDOW,
    BotBlocked,
    NoSchedule,
    in_quiet_hours,
    next_fire_at,
    zone_for,
)
from app.services.ports import Clock, UnitOfWork


@dataclass(frozen=True, slots=True)
class ReminderSettingsView:
    """The canonical resource plus the two flags §10 allows beside it."""

    enabled: bool
    local_time: time
    timezone_name: str
    #: The stored time now falls inside quiet hours. Only reachable when the
    #: policy moved after the schedule was made: §10 keeps existing schedules
    #: rather than switching them off, and the worker skips the occurrence.
    quiet_blocked: bool
    bot_blocked: bool


class ReminderService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def now(self) -> datetime:
        return self._clock.now()

    def read(self, unit: UnitOfWork, *, account_id: UUID) -> ReminderSettingsView:
        return _view(_require_schedule(unit, account_id))

    def update(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        enabled: bool,
        local_time: time,
        timezone_name: str,
        now: datetime,
    ) -> ReminderSettingsView:
        """Replace the resource, recomputing everything derived from it.

        The order of the three refusals is the order of their cost to the user:
        an unknown zone and a night-time slot are mistakes she can correct in
        the form she is looking at, so they answer first; the block is a fact
        about the world outside the form.

        **Why turning reminders on after a block is refused.** The server holds
        direct evidence that Telegram will not deliver, and 200 here would be a
        claim it cannot keep — the schedule would say «on» while every send
        returns 403. The way out is not a withdrawal of consent (the user
        withdrew nothing, and for an account whose only consent this is, a
        withdrawal would erase the account): it is a `PUT` that leaves `enabled`
        false, which acknowledges the block, turns it into a plain pause and —
        by design — **breaks the streak** of §10, and a second `PUT` that turns
        reminders on. Two steps, both meaningful, and no state the user cannot
        get back out of.
        """
        schedule = _require_schedule(unit, account_id)

        # Validated before the block is consulted: a 409 that hides a 422 would
        # send the user off to Telegram to fix a typo in her own form.
        zone_for(timezone_name)
        if in_quiet_hours(local_time):
            raise QuietHoursViolation()

        if schedule.disabled_reason == BOT_BLOCKED and enabled:
            raise BotBlocked()

        unit.schedules.update(
            account_id,
            timezone_name=timezone_name,
            local_time=local_time,
            enabled=enabled,
            # Acknowledged: from here on this is a pause. If the bot is still
            # blocked when the next send is attempted, the 403 starts a fresh
            # streak — which is what «14 діб поспіль» means.
            disabled_reason=None,
            disabled_at=None,
            next_fire_at=next_fire_at(timezone_name, local_time, now),
            now=now,
        )
        return _view(_require_schedule(unit, account_id))

    def consume_budget(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        now: datetime,
    ) -> RateVerdict:
        """§11: `reminders-settings 20/хв`, per account, in its own transaction.

        Same reasoning as the sync budgets it sits beside: consuming it inside
        the write would refund every refusal, and a client looping on a 409
        would then spend nothing exactly while it misbehaves.
        """
        seconds = int(SETTINGS_WINDOW.total_seconds())
        return unit.rate_windows.consume(
            account_id,
            bucket=SETTINGS_BUCKET,
            cost=1,
            limit=SETTINGS_REQUESTS_PER_MINUTE,
            window_start=window_start(now, seconds),
            window_seconds=seconds,
            now=now,
        )


def _require_schedule(unit: UnitOfWork, account_id: UUID) -> ReminderScheduleRecord:
    schedule = unit.schedules.read(account_id)
    if schedule is None:
        raise NoSchedule()
    return schedule


def _view(schedule: ReminderScheduleRecord) -> ReminderSettingsView:
    return ReminderSettingsView(
        enabled=schedule.enabled,
        local_time=schedule.local_time,
        timezone_name=schedule.timezone_name,
        quiet_blocked=in_quiet_hours(schedule.local_time),
        bot_blocked=schedule.disabled_reason == BOT_BLOCKED,
    )
