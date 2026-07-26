"""Wire contracts for the vault synchronization endpoints (§9.2).

Standalone by contract: this module imports pydantic and nothing of the app,
so the literals and the record-key pattern are restated here rather than
borrowed from the domain.

The server is zero-knowledge, which shows up in what is *not* validated:
`payload_b64` is opaque bytes, and `kdf_params` is an arbitrary object. The
only things worth checking are shapes the storage itself requires.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 32 байти в нижньому регістрі. Патерн живе в схемі, а не лише у валідаторі:
#: `field_validator` не потрапляє в OpenAPI, тож документ дозволяв `""` — і
#: schemathesis Фази 5 справедливо називав 422 на схемно-валідних даних.
RECORD_KEY_PATTERN = r"^[0-9a-f]{64}$"

KdfLiteral = Literal["argon2id", "pbkdf2-sha256"]
KeyWriteModeLiteral = Literal["rewrap", "rekey"]

#: Кількість записів у чанку — контрактна межа клієнта (§9.5), тож її порушення
#: є помилкою валідації. Розміри в байтах перевіряє сервіс і відповідає 413.
MAX_CHANGES_PER_REQUEST = 200

#: Стеля кожного лічильника, що переходить дріт — найбільше ціле, яке JSON несе
#: точно (2^53 − 1), а не стеля `bigint`. Причина в самому документі OpenAPI:
#: FastAPI типізує `maximum` як float, тож 2^63 − 1 виходить із документа як
#: 2^63 — на одиницю БІЛЬШЕ за те, що приймає код.
#:
#: Дослівно повторює `app.domain.vault.MAX_COUNTER` — контракт «Schemas are
#: standalone» забороняє імпорт домену, а `tests/test_schemas.py` пінує обидва
#: написання разом.
MAX_COUNTER = 2**53 - 1


def _base64_chars(byte_count: int) -> int:
    """Скільки символів base64 дає рівно `byte_count` байтів."""
    return 4 * ((byte_count + 2) // 3)


#: Найдовший base64, який може дати найбільший дозволений запис (§9.5, 64 КіБ).
#:
#: Це вимога до **схеми**, а не нова межа: сервіс і далі відповідає 413 на
#: завеликий payload, і ця гілка лишається досяжною, бо base64 доповнює до
#: чотирьох — 65 536, 65 537 і 65 538 байтів дають однакову довжину рядка.
#: Причина, з якої межа потрібна саме в схемі: без неї coverage-фаза
#: schemathesis будувала масив на 200 записів із рядками довільної довжини, і
#: прогін одного ендпоінта займав дев'ять хвилин замість секунди.
MAX_PAYLOAD_B64_CHARS = _base64_chars(65_536)

#: Те саме для конверта (§7): `R` — 32 байти, кілобайт — запас на три порядки.
#: Дослівно повторює `app.domain.vault.MAX_ENVELOPE_BYTES`.
MAX_ENVELOPE_B64_CHARS = _base64_chars(1_024)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ChangeInput(ContractModel):
    record_key: str = Field(pattern=RECORD_KEY_PATTERN)
    client_ts_ms: int = Field(gt=0, le=MAX_COUNTER)
    payload_b64: str | None = Field(default=None, max_length=MAX_PAYLOAD_B64_CHARS)
    tombstone: bool = False

    @model_validator(mode="after")
    def _payload_belongs_to_updates_only(self) -> ChangeInput:
        if self.tombstone and self.payload_b64 is not None:
            raise ValueError("a tombstone carries no payload")
        if not self.tombstone and self.payload_b64 is None:
            raise ValueError("an update carries a payload")
        return self


class PushRequest(ContractModel):
    base_revision: int = Field(ge=0, le=MAX_COUNTER)
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
    expected_wrap_version: int = Field(ge=0, le=MAX_COUNTER)
    wrapped_dek: str = Field(min_length=1, max_length=MAX_ENVELOPE_B64_CHARS)
    kdf: KdfLiteral
    kdf_params: dict[str, object]


class KeyWriteAccepted(ContractModel):
    key_version: int
    wrap_version: int


class KeyWriteConflictOutput(ContractModel):
    current_wrap_version: int


class VaultResetResponse(ContractModel):
    new_revision: int
