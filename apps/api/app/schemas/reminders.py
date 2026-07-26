"""Wire contract for the `ReminderSettings` resource (§10).

There is no output model for a delivery, and that is the point of the module
rather than an omission: §10 forbids the API from exposing delivery status as a
list or a history, so the shape that would carry one is never declared.

The `HH:mm` pattern and the three-kind literal are restated here rather than
imported from the domain — the import-linter contract «Schemas are standalone»
keeps this package free of `app.domain`, `app.services` and `app.infra`, and a
contract test pins the two spellings together.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReminderSettingsUpdate(ContractModel):
    """`PUT` carries the whole resource: §10 replaces, it does not patch.

    All three fields are required. An optional `time` would make «leave it as
    it was» expressible, and the server would then have to merge — which is the
    one operation on a schedule that cannot be done without re-deriving
    `next_fire_at` anyway.
    """

    enabled: bool
    time: str
    timezone: str

    @field_validator("time")
    @classmethod
    def _validate_time(cls, value: str) -> str:
        if not _TIME.match(value):
            raise ValueError("time must be HH:mm")
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        # Shape only. The tz database is the authority and lives in the domain,
        # so an unknown-but-well-formed zone is a 422 `unknown_timezone` from
        # there rather than a validation error here.
        if not value.strip():
            raise ValueError("timezone is required")
        return value


class ReminderSettingsOutput(ContractModel):
    """The canonical resource plus the two aggregate flags of §10.

    `quiet_blocked` and `bot_blocked` are booleans about the schedule as it
    stands now. Neither says when, how often, or how many — a count would be a
    delivery history in disguise.
    """

    enabled: bool
    time: str
    timezone: str
    quiet_blocked: bool
    bot_blocked: bool
