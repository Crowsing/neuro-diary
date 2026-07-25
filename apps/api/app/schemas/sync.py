"""Wire contracts for the vault synchronization endpoints (§9.2).

Standalone by contract: this module imports pydantic and nothing of the app,
so the literals and the record-key pattern are restated here rather than
borrowed from the domain.

The server is zero-knowledge, which shows up in what is *not* validated:
`payload_b64` is opaque bytes, and `kdf_params` is an arbitrary object. The
only things worth checking are shapes the storage itself requires.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_RECORD_KEY_HEX = re.compile(r"^[0-9a-f]{64}$")

KdfLiteral = Literal["argon2id", "pbkdf2-sha256"]
KeyWriteModeLiteral = Literal["rewrap", "rekey"]

#: Кількість записів у чанку — контрактна межа клієнта (§9.5), тож її порушення
#: є помилкою валідації. Розміри в байтах перевіряє сервіс і відповідає 413.
MAX_CHANGES_PER_REQUEST = 200


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ChangeInput(ContractModel):
    record_key: str
    client_ts_ms: int = Field(gt=0)
    payload_b64: str | None = None
    tombstone: bool = False

    @field_validator("record_key")
    @classmethod
    def _validate_record_key(cls, value: str) -> str:
        if not _RECORD_KEY_HEX.match(value):
            raise ValueError("record_key must be 32 lowercase hex bytes")
        return value

    @model_validator(mode="after")
    def _payload_belongs_to_updates_only(self) -> ChangeInput:
        if self.tombstone and self.payload_b64 is not None:
            raise ValueError("a tombstone carries no payload")
        if not self.tombstone and self.payload_b64 is None:
            raise ValueError("an update carries a payload")
        return self


class PushRequest(ContractModel):
    base_revision: int = Field(ge=0)
    changes: list[ChangeInput] = Field(
        min_length=1,
        max_length=MAX_CHANGES_PER_REQUEST,
    )


class PushAccepted(ContractModel):
    new_revision: int


class PushConflictOutput(ContractModel):
    reason: Literal["conflict"] = "conflict"
    conflict_keys: list[str]


class PullRecordOutput(ContractModel):
    record_key: str
    payload_b64: str | None
    tombstone: bool
    revision: int
    client_ts_ms: int


class PullResponse(ContractModel):
    records: list[PullRecordOutput]
    next_since: int
    current_revision: int
    reset: bool
    #: Нейтральне: «щось зі згодами змінилося», без назви kind (§9.7).
    consent_state_changed: bool


class KeyOutput(ContractModel):
    wrapped_dek: str
    kdf: str
    kdf_params: dict[str, object]
    key_version: int
    wrap_version: int
    #: Віддається лише при step-up і лише поки живий TTL (§7).
    wrapped_dek_prev: str | None = None


class KeyWriteRequest(ContractModel):
    mode: KeyWriteModeLiteral
    expected_wrap_version: int = Field(ge=0)
    wrapped_dek: str = Field(min_length=1)
    kdf: KdfLiteral
    kdf_params: dict[str, object]


class KeyWriteAccepted(ContractModel):
    key_version: int
    wrap_version: int


class KeyWriteConflictOutput(ContractModel):
    current_wrap_version: int


class VaultResetResponse(ContractModel):
    new_revision: int
