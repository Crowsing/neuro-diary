"""Consent, reminder schedule, and erasure repositories."""

from __future__ import annotations

from datetime import datetime, time
from uuid import UUID, uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session

from app.domain.identity import ConsentKind, RevokeReason
from app.domain.records import ConsentRecord, PendingErasure
from app.infra.db.models import (
    Consent,
    ErasureJob,
    ReminderDelivery,
    ReminderSchedule,
)


class ConsentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def grant(
        self,
        account_id: UUID,
        *,
        kind: ConsentKind,
        text_version: str,
        text_sha256: bytes,
        text_locale: str,
        record_key_cycle: bytes | None,
        now: datetime,
    ) -> None:
        self._session.execute(
            insert(Consent).values(
                id=uuid4(),
                account_id=account_id,
                kind=kind.value,
                granted_at=now,
                revoked_at=None,
                text_version=text_version,
                text_sha256=text_sha256,
                text_locale=text_locale,
                revoke_reason=None,
                record_key_cycle=record_key_cycle,
            )
        )
        self._session.flush()

    def named_cycle_key(self, account_id: UUID) -> bytes | None:
        """The record_key the client named when granting `cycle_sync` (§9.7).

        Read regardless of `revoked_at`: the push gate needs the key of a
        revoked consent, and that is exactly when it matters.
        """
        row = self._session.execute(
            select(Consent.record_key_cycle)
            .where(
                Consent.account_id == account_id,
                Consent.kind == ConsentKind.CYCLE_SYNC.value,
                Consent.record_key_cycle.is_not(None),
            )
            .order_by(Consent.granted_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return None if row is None else bytes(row)

    def active(self, account_id: UUID) -> list[ConsentRecord]:
        rows = self._session.execute(
            select(
                Consent.kind,
                Consent.granted_at,
                Consent.text_version,
                Consent.text_sha256,
                Consent.text_locale,
            )
            .where(Consent.account_id == account_id, Consent.revoked_at.is_(None))
            .order_by(Consent.granted_at)
        ).all()
        return [
            ConsentRecord(
                kind=ConsentKind(row.kind),
                granted_at=row.granted_at,
                text_version=row.text_version,
                text_sha256=row.text_sha256,
                text_locale=row.text_locale,
            )
            for row in rows
        ]

    def active_kinds(self, account_id: UUID) -> set[ConsentKind]:
        return {record.kind for record in self.active(account_id)}

    def revoke(
        self,
        account_id: UUID,
        *,
        kinds: list[ConsentKind],
        reason: RevokeReason,
        now: datetime,
    ) -> list[ConsentKind]:
        """Revoke the given kinds in one statement; returns the ones affected."""
        rows = self._session.execute(
            update(Consent)
            .where(
                Consent.account_id == account_id,
                Consent.kind.in_([kind.value for kind in kinds]),
                Consent.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoke_reason=reason.value)
            .returning(Consent.kind)
        ).all()
        self._session.flush()
        return [ConsentKind(row.kind) for row in rows]

    def delete_revoked_before(self, moment: datetime) -> int:
        """§4.3: a revoked row is proof of consent for 24 months, then it goes."""
        rows = self._session.execute(
            delete(Consent)
            .where(
                Consent.revoked_at.is_not(None),
                Consent.revoked_at < moment,
            )
            .returning(Consent.id)
        ).all()
        return len(rows)

    def accounts_awaiting_erasure(
        self,
        *,
        reasons: list[RevokeReason],
        revoked_before: datetime,
    ) -> list[PendingErasure]:
        """Accounts whose last consent went away for a non-user reason (§4.3)."""
        latest = (
            select(
                Consent.account_id.label("account_id"),
                func.max(Consent.revoked_at).label("revoked_at"),
                func.count().filter(Consent.revoked_at.is_(None)).label("active"),
            )
            .group_by(Consent.account_id)
            .subquery()
        )
        reason_rows = (
            select(Consent.account_id, Consent.revoke_reason, Consent.revoked_at)
            .where(Consent.revoke_reason.in_([reason.value for reason in reasons]))
            .subquery()
        )
        rows = self._session.execute(
            select(latest.c.account_id, latest.c.revoked_at)
            .join(
                reason_rows,
                (reason_rows.c.account_id == latest.c.account_id)
                & (reason_rows.c.revoked_at == latest.c.revoked_at),
            )
            .where(
                latest.c.active == 0,
                latest.c.revoked_at < revoked_before,
            )
        ).all()
        return [
            PendingErasure(account_id=row.account_id, revoked_at=row.revoked_at)
            for row in rows
        ]


class ReminderScheduleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def provision(
        self,
        account_id: UUID,
        *,
        telegram_chat_id: int,
        timezone_name: str,
        local_time: time,
        next_fire_at: datetime,
        now: datetime,
    ) -> None:
        self._session.execute(
            insert(ReminderSchedule).values(
                account_id=account_id,
                telegram_chat_id=telegram_chat_id,
                tz=timezone_name,
                local_time=local_time,
                enabled=True,
                disabled_reason=None,
                next_fire_at=next_fire_at,
                updated_at=now,
            )
        )

    def exists(self, account_id: UUID) -> bool:
        return (
            self._session.execute(
                select(ReminderSchedule.account_id).where(
                    ReminderSchedule.account_id == account_id
                )
            ).scalar_one_or_none()
            is not None
        )

    def delete(self, account_id: UUID) -> None:
        """§4.4: no schedule row may outlive its consent."""
        self._session.execute(
            delete(ReminderDelivery).where(ReminderDelivery.account_id == account_id)
        )
        self._session.execute(
            delete(ReminderSchedule).where(ReminderSchedule.account_id == account_id)
        )


class ErasureRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        account_id: UUID,
        scope: str,
        deletion_copy_version: str,
        requested_at: datetime,
    ) -> UUID:
        job_id = uuid4()
        self._session.execute(
            insert(ErasureJob).values(
                id=job_id,
                account_id=account_id,
                scope=scope,
                requested_at=requested_at,
                completed_at=None,
                deletion_copy_version=deletion_copy_version,
            )
        )
        self._session.flush()
        return job_id

    def complete(self, job_id: UUID, *, at: datetime) -> None:
        self._session.execute(
            update(ErasureJob).where(ErasureJob.id == job_id).values(completed_at=at)
        )

    def jobs_for(self, account_id: UUID) -> list[tuple[str, str, datetime | None]]:
        rows = self._session.execute(
            select(
                ErasureJob.scope,
                ErasureJob.deletion_copy_version,
                ErasureJob.completed_at,
            ).where(ErasureJob.account_id == account_id)
        ).all()
        return [
            (row.scope, row.deletion_copy_version, row.completed_at) for row in rows
        ]
