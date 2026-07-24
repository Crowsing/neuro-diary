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

    @field_validator("text_sha256")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _SHA256_HEX.match(value):
            raise ValueError("text_sha256 must be lowercase hex")
        return value

    @model_validator(mode="after")
    def _settings_belong_to_reminders_only(self) -> GrantInput:
        needs_settings = self.kind == "telegram_reminders"
        if needs_settings and self.settings is None:
            raise ValueError("telegram_reminders requires settings")
        if not needs_settings and self.settings is not None:
            raise ValueError("settings are only valid for telegram_reminders")
        return self


class AuthRequest(ContractModel):
    init_data: str = Field(min_length=1)
    grant: GrantInput | None = None


class RevokeRequest(ContractModel):
    """The kind travels in the body; a path would leak it to the proxy log."""

    kind: ConsentKindLiteral


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
