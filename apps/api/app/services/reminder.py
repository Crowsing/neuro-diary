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
from app.domain.records import ReminderSchedule as ReminderScheduleRecord
from app.domain.reminders import (
    BOT_BLOCKED,
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

        **Only `enabled: true` discharges a Telegram block.** This is the whole
        of «14 діб поспіль» (§10) on the write side, and getting it wrong is
        subtle: if any successful `PUT` cleared `disabled_reason`, then a user
        who merely moved her reminder from eight to nine in the evening while
        the bot was blocked would silently delete her own deadline — the
        schedule would stay off, no further send would ever be attempted, no
        fresh 403 would ever be observed, and the fourteen-day revocation would
        never fire for anybody who touched the form.

        So a write that leaves `enabled` false carries the block through
        untouched: the streak keeps running while she edits whatever she likes.
        A write that turns reminders **on** is the one act that means «I have
        unblocked the bot» — it clears the reason, re-arms the schedule, and if
        she is wrong the next attempt takes a 403 and starts a *new* streak from
        that moment. Which is what consecutive means.

        The server does not refuse that write. It cannot verify the claim either
        way, refusing would leave no way back that is not a withdrawal of
        consent — and for an account whose only consent this is, a withdrawal
        erases the account.
        """
        schedule = _require_schedule(unit, account_id)

        zone_for(timezone_name)
        if in_quiet_hours(local_time):
            raise QuietHoursViolation()

        unit.schedules.update(
            account_id,
            timezone_name=timezone_name,
            local_time=local_time,
            enabled=enabled,
            disabled_reason=None if enabled else schedule.disabled_reason,
            disabled_at=None if enabled else schedule.disabled_at,
            next_fire_at=next_fire_at(timezone_name, local_time, now),
            now=now,
        )
        return _view(_require_schedule(unit, account_id))


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
