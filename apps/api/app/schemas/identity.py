"""Wire contracts for identity, consent, and session endpoints.

Strict with `extra="forbid"`, which is why `settings` has to be one optional
field on one model rather than two competing grant shapes.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_RECORD_KEY_HEX = re.compile(r"^[0-9a-f]{64}$")

ConsentKindLiteral = Literal["health_sync", "telegram_reminders", "cycle_sync"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReminderSettingsInput(ContractModel):
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
        if not value.strip():
            raise ValueError("timezone is required")
        return value


class GrantInput(ContractModel):
    """One contract for all three consents (§9.2)."""

    kind: ConsentKindLiteral
    text_version: str
    text_sha256: str
    settings: ReminderSettingsInput | None = None
    #: §9.7: HMAC(k_index,'cycle'), яке клієнт називає лише при grant
    #: `cycle_sync`. Сервер його не обчислює і перевірити не може.
    record_key_cycle: str | None = None

    @field_validator("text_sha256")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _SHA256_HEX.match(value):
            raise ValueError("text_sha256 must be lowercase hex")
        return value

    @field_validator("record_key_cycle")
    @classmethod
    def _validate_record_key(cls, value: str | None) -> str | None:
        if value is not None and not _RECORD_KEY_HEX.match(value):
            raise ValueError("record_key_cycle must be 32 lowercase hex bytes")
        return value

    @model_validator(mode="after")
    def _settings_belong_to_reminders_only(self) -> GrantInput:
        needs_settings = self.kind == "telegram_reminders"
        if needs_settings and self.settings is None:
            raise ValueError("telegram_reminders requires settings")
        if not needs_settings and self.settings is not None:
            raise ValueError("settings are only valid for telegram_reminders")
        if self.record_key_cycle is not None and self.kind != "cycle_sync":
            raise ValueError("record_key_cycle is only valid for cycle_sync")
        return self


class AuthRequest(ContractModel):
    init_data: str = Field(min_length=1)
    grant: GrantInput | None = None


class RevokeRequest(ContractModel):
    """The kind travels in the body; a path would leak it to the proxy log.

    `last_acked_revision` and `acknowledge_incomplete` implement the
    compensating control of §8. The predicate §8 states —
    `max(last_acked_revision) < current_revision` — reads a value that lives on
    the client (`SyncMeta`), so the client has to state it; the deviation and
    the reasons for not deriving it server-side are recorded in the plan.

    Omitting `last_acked_revision` is read as zero, which is the fail-closed
    side: a device that says nothing gets the question rather than a silent
    deletion.
    """

    kind: ConsentKindLiteral
    last_acked_revision: int = Field(default=0, ge=0)
    acknowledge_incomplete: bool = False


class ConsentOutput(ContractModel):
    kind: ConsentKindLiteral
    granted_at: datetime
    text_version: str


class ConsentListOutput(ContractModel):
    consents: list[ConsentOutput]


class AuthResponse(ContractModel):
    session_token: str
    expires_at: datetime
    consents: list[ConsentOutput]


class SessionOutput(ContractModel):
    id: UUID
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    current: bool


class SessionListOutput(ContractModel):
    sessions: list[SessionOutput]


class RevokedSessionsOutput(ContractModel):
    revoked: int


class RevokeResponse(ContractModel):
    account_erased: bool


class AccountDeletedResponse(ContractModel):
    status: Literal["erased"]
