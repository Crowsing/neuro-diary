"""ORM models for the tables phase 1 touches.

`vault_*` is deliberately absent: phase 1 has no vault code beyond the DDL, and
a mapped class would be an invitation to write some. Deleting an account still
clears those rows — the foreign keys of migration 0001 cascade.
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    LargeBinary,
    Text,
    Time,
    Uuid,
)
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


# `outbox` is mapped nowhere on purpose: §4.4 removed `ConsentGranted`, and the
# only remaining event has its consumer in phase 3.

__all__ = [
    "Account",
    "AuthReplay",
    "Base",
    "Consent",
    "ErasureJob",
    "ReminderDelivery",
    "ReminderSchedule",
    "SessionToken",
    "TelegramIdentity",
]
