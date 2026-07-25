"""ORM models for the tables phases 1 to 3 touch."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    Time,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "account"
    __table_args__ = {"schema": "diary"}

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)


class TelegramIdentity(Base):
    __tablename__ = "telegram_identity"
    __table_args__ = {"schema": "diary"}

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("diary.account.id"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_auth_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Consent(Base):
    __tablename__ = "consent"
    __table_args__ = {"schema": "diary"}

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    account_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("diary.account.id"))
    kind: Mapped[str] = mapped_column(Text)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text_version: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    text_locale: Mapped[str] = mapped_column(Text)
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    # §9.7: the client names HMAC(k_index,'cycle') when it grants `cycle_sync`.
    # Phase 2 stores it and gates pushes on it; the hard DELETE it enables
    # belongs to phase 3.
    record_key_cycle: Mapped[bytes | None] = mapped_column(LargeBinary)


class SessionToken(Base):
    __tablename__ = "session_token"
    __table_args__ = {"schema": "diary"}

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    account_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("diary.account.id"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthReplay(Base):
    __tablename__ = "auth_replay"
    __table_args__ = {"schema": "diary"}

    initdata_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ErasureJob(Base):
    __tablename__ = "erasure_job"
    __table_args__ = {"schema": "diary"}

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    account_id: Mapped[UUID] = mapped_column(Uuid)
    scope: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_copy_version: Mapped[str] = mapped_column(Text)


class Outbox(Base):
    """Transactional outbox (§4.4).

    No foreign key to `account`, and deliberately so: `AccountErasureRequested`
    is written in the very transaction that deletes the account row, and a
    cascade would take the event with it. Erasure removes an account's rows by
    the identifier inside `payload`, which is the entry §6.4 names for this
    table.
    """

    __tablename__ = "outbox"
    __table_args__ = {"schema": "diary"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReminderSchedule(Base):
    __tablename__ = "reminder_schedule"
    __table_args__ = {"schema": "reminders"}

    account_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    tz: Mapped[str] = mapped_column(Text)
    local_time: Mapped[time] = mapped_column(Time)
    enabled: Mapped[bool] = mapped_column(Boolean)
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    next_fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReminderDelivery(Base):
    __tablename__ = "reminder_delivery"
    __table_args__ = {"schema": "reminders"}

    account_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VaultKey(Base):
    __tablename__ = "vault_key"
    __table_args__ = {"schema": "diary"}

    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("diary.account.id"), primary_key=True
    )
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary)
    kdf: Mapped[str] = mapped_column(Text)
    kdf_params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    key_version: Mapped[int] = mapped_column(Integer)
    # CAS runs on `wrap_version`, not `key_version` (§7): a re-wrap leaves the
    # key version alone, so two concurrent re-wraps would both pass a CAS on it
    # and one of the new passphrases would be lost silently.
    wrap_version: Mapped[int] = mapped_column(Integer)
    wrapped_dek_prev: Mapped[bytes | None] = mapped_column(LargeBinary)
    wrap_version_prev: Mapped[int | None] = mapped_column(Integer)
    prev_written_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VaultRevision(Base):
    __tablename__ = "vault_revision"
    __table_args__ = {"schema": "diary"}

    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("diary.account.id"), primary_key=True
    )
    current_revision: Mapped[int] = mapped_column(BigInteger)
    compacted_up_to: Mapped[int] = mapped_column(BigInteger)
    reset_revision: Mapped[int] = mapped_column(BigInteger)
    consent_epoch: Mapped[int] = mapped_column(BigInteger)


class VaultRecord(Base):
    __tablename__ = "vault_record"
    __table_args__ = {"schema": "diary"}

    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("diary.account.id"), primary_key=True
    )
    record_key: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    payload: Mapped[bytes | None] = mapped_column(LargeBinary)
    payload_size: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(BigInteger)
    deleted: Mapped[bool] = mapped_column(Boolean)
    # bigint, not timestamptz: the AAD of §7 binds to a decimal integer of UTC
    # milliseconds, and a round trip through timestamptz is not byte-stable.
    client_ts_ms: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RateWindow(Base):
    __tablename__ = "rate_window"
    __table_args__ = {"schema": "diary"}

    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("diary.account.id"), primary_key=True
    )
    bucket: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[int] = mapped_column(BigInteger)


__all__ = [
    "Account",
    "AuthReplay",
    "Base",
    "Consent",
    "ErasureJob",
    "Outbox",
    "RateWindow",
    "ReminderDelivery",
    "ReminderSchedule",
    "SessionToken",
    "TelegramIdentity",
    "VaultKey",
    "VaultRecord",
    "VaultRevision",
]
