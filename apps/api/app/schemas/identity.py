"""Wire contracts for identity, consent, and session endpoints.

Strict with `extra="forbid"`, which is why `settings` has to be one optional
field on one model rather than two competing grant shapes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Патерни живуть у схемі, а не у валідаторах: `field_validator` не потрапляє в
#: OpenAPI, тож документ обіцяв будь-який рядок там, де сервер приймає лише
#: `HH:mm` або 32 байти hex. Фаззер Фази 5 знайшов саме цю розбіжність.
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
HEX32_PATTERN = r"^[0-9a-f]{64}$"

#: Та сама стеля, що в `app.schemas.sync`: найбільше ціле, яке JSON несе точно.
MAX_COUNTER = 2**53 - 1

ConsentKindLiteral = Literal["health_sync", "telegram_reminders", "cycle_sync"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReminderSettingsInput(ContractModel):
    time: str = Field(pattern=TIME_PATTERN)
    #: Форма, і тільки. База зон — авторитет, вона живе в домені, тож невідома
    #: але добре сформована зона це 422 `unknown_timezone` звідти, а не помилка
    #: валідації тут.
    timezone: str = Field(min_length=1)


class GrantInput(ContractModel):
    """One contract for all three consents (§9.2)."""

    kind: ConsentKindLiteral
    text_version: str = Field(min_length=1)
    text_sha256: str = Field(pattern=HEX32_PATTERN)
    settings: ReminderSettingsInput | None = None
    #: §9.7: HMAC(k_index,'cycle'), яке клієнт називає лише при grant
    #: `cycle_sync`. Сервер його не обчислює і перевірити не може.
    record_key_cycle: str | None = Field(default=None, pattern=HEX32_PATTERN)

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
    last_acked_revision: int = Field(default=0, ge=0, le=MAX_COUNTER)
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
