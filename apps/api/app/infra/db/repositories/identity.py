"""Account, Telegram identity, session, and replay repositories."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domain.identity import SESSION_MAX_LIFETIME, SESSION_TTL
from app.domain.records import SessionRecord, SessionSummary
from app.infra.db.models import Account, AuthReplay, SessionToken, TelegramIdentity


class AccountRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, account_id: UUID, *, created_at: datetime) -> None:
        self._session.execute(
            insert(Account).values(
                id=account_id,
                created_at=created_at,
                status="active",
            )
        )

    def status(self, account_id: UUID) -> str | None:
        return self._session.execute(
            select(Account.status).where(Account.id == account_id)
        ).scalar_one_or_none()

    def lock(self, account_id: UUID) -> bool:
        """Take the §9.1 barrier lock before any account-scoped write."""
        row = self._session.execute(
            select(Account.id)
            .where(Account.id == account_id)
            .with_for_update(key_share=True)
        ).scalar_one_or_none()
        return row is not None

    def delete(self, account_id: UUID) -> None:
        """Hard delete; foreign keys cascade to consents, sessions, and vault."""
        self._session.execute(delete(Account).where(Account.id == account_id))

    def identifiers(self) -> list[UUID]:
        """Every live account, for the restore reconciliation of §6.4.

        The journal holds `HMAC(k_erasure, account_id)` and nothing else, and an
        HMAC does not run backwards — so reconciliation recomputes the reference
        for each account that is present and matches. That is only possible
        *after* a restore has put those accounts back, which is exactly when it
        is needed.

        Nothing on a request path may call this: enumerating the whole table is
        a reconciliation operation, not an account-scoped one.
        """
        return list(self._session.execute(select(Account.id)).scalars().all())


class TelegramIdentityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        telegram_user_id: int,
        account_id: UUID,
        *,
        now: datetime,
    ) -> None:
        self._session.execute(
            insert(TelegramIdentity).values(
                telegram_user_id=telegram_user_id,
                account_id=account_id,
                first_seen_at=now,
                last_auth_at=now,
            )
        )

    def account_id_for(self, telegram_user_id: int) -> UUID | None:
        return self._session.execute(
            select(TelegramIdentity.account_id).where(
                TelegramIdentity.telegram_user_id == telegram_user_id
            )
        ).scalar_one_or_none()

    def telegram_user_id_for(self, account_id: UUID) -> int | None:
        return self._session.execute(
            select(TelegramIdentity.telegram_user_id).where(
                TelegramIdentity.account_id == account_id
            )
        ).scalar_one_or_none()

    def touch(self, telegram_user_id: int, *, now: datetime) -> None:
        self._session.execute(
            update(TelegramIdentity)
            .where(TelegramIdentity.telegram_user_id == telegram_user_id)
            .values(last_auth_at=now)
        )

    def last_auth_at(self, telegram_user_id: int) -> datetime | None:
        return self._session.execute(
            select(TelegramIdentity.last_auth_at).where(
                TelegramIdentity.telegram_user_id == telegram_user_id
            )
        ).scalar_one_or_none()


class SessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        account_id: UUID,
        *,
        token_hash: bytes,
        now: datetime,
    ) -> SessionRecord:
        session_id = uuid4()
        expires_at = now + SESSION_TTL
        self._session.execute(
            insert(SessionToken).values(
                id=session_id,
                account_id=account_id,
                token_hash=token_hash,
                created_at=now,
                expires_at=expires_at,
                last_used_at=now,
                revoked_at=None,
            )
        )
        return SessionRecord(
            id=session_id,
            account_id=account_id,
            created_at=now,
            expires_at=expires_at,
            last_used_at=now,
        )

    def find_active(self, token_hash: bytes, *, now: datetime) -> SessionRecord | None:
        row = self._session.execute(
            select(
                SessionToken.id,
                SessionToken.account_id,
                SessionToken.created_at,
                SessionToken.expires_at,
                SessionToken.last_used_at,
            ).where(
                SessionToken.token_hash == token_hash,
                SessionToken.revoked_at.is_(None),
                SessionToken.expires_at > now,
                SessionToken.created_at > now - SESSION_MAX_LIFETIME,
            )
        ).one_or_none()
        if row is None:
            return None
        return SessionRecord(
            id=row.id,
            account_id=row.account_id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            last_used_at=row.last_used_at,
        )

    def slide(self, record: SessionRecord, *, now: datetime) -> datetime:
        """Extend the window without crossing the absolute lifetime ceiling."""
        expires_at = min(record.created_at + SESSION_MAX_LIFETIME, now + SESSION_TTL)
        self._session.execute(
            update(SessionToken)
            .where(SessionToken.id == record.id)
            .values(last_used_at=now, expires_at=expires_at)
        )
        return expires_at

    def list_for_account(
        self,
        account_id: UUID,
        *,
        now: datetime,
        current_id: UUID,
    ) -> list[SessionSummary]:
        rows = self._session.execute(
            select(
                SessionToken.id,
                SessionToken.created_at,
                SessionToken.last_used_at,
                SessionToken.expires_at,
            )
            .where(
                SessionToken.account_id == account_id,
                SessionToken.revoked_at.is_(None),
                SessionToken.expires_at > now,
            )
            .order_by(SessionToken.created_at)
        ).all()
        return [
            SessionSummary(
                id=row.id,
                created_at=row.created_at,
                last_used_at=row.last_used_at,
                expires_at=row.expires_at,
                current=row.id == current_id,
            )
            for row in rows
        ]

    def revoke_others(self, account_id: UUID, *, keep: UUID, now: datetime) -> int:
        rows = self._session.execute(
            update(SessionToken)
            .where(
                SessionToken.account_id == account_id,
                SessionToken.id != keep,
                SessionToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .returning(SessionToken.id)
        ).all()
        return len(rows)

    def delete_settled_before(self, moment: datetime) -> int:
        rows = self._session.execute(
            delete(SessionToken)
            .where(SessionToken.expires_at < moment)
            .returning(SessionToken.id)
        ).all()
        return len(rows)


class AuthReplayRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def remember(self, digest: bytes, *, now: datetime) -> bool:
        """Return False when this initData has already bought a session.

        RETURNING rather than rowcount: a conflicting insert and a suppressed
        one are not reliably distinguishable by affected-row count.
        """
        inserted = self._session.execute(
            pg_insert(AuthReplay)
            .values(initdata_hash=digest, seen_at=now)
            .on_conflict_do_nothing(index_elements=["initdata_hash"])
            .returning(AuthReplay.initdata_hash)
        ).scalar_one_or_none()
        return inserted is not None

    def delete_seen_before(self, moment: datetime) -> int:
        rows = self._session.execute(
            delete(AuthReplay)
            .where(AuthReplay.seen_at < moment)
            .returning(AuthReplay.initdata_hash)
        ).all()
        return len(rows)
