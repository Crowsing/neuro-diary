"""Consent, reminder schedule, and erasure repositories."""

from __future__ import annotations

from datetime import datetime, time
from uuid import UUID, uuid4

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.identity import ConsentKind, RevokeReason
from app.domain.records import ConsentRecord, PendingErasure
from app.domain.records import ReminderSchedule as ReminderScheduleRecord
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

    def latest_revoke_reason(self, account_id: UUID) -> RevokeReason | None:
        """Why this account most recently lost a consent (§4.3).

        The deadline for an account with no active consent depends on it: the
        user's own decision means now, anything else means 30 days. Ordered by
        `revoked_at` and then by `id`, because a cascade revokes two rows with
        an identical timestamp and an unordered tie would answer at random.
        """
        row = self._session.execute(
            select(Consent.revoke_reason)
            .where(
                Consent.account_id == account_id,
                Consent.revoked_at.is_not(None),
            )
            .order_by(Consent.revoked_at.desc(), Consent.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        return None if row is None else RevokeReason(row)

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

    def read(self, account_id: UUID) -> ReminderScheduleRecord | None:
        row = self._session.execute(
            select(
                ReminderSchedule.account_id,
                ReminderSchedule.telegram_chat_id,
                ReminderSchedule.tz,
                ReminderSchedule.local_time,
                ReminderSchedule.enabled,
                ReminderSchedule.disabled_reason,
                ReminderSchedule.disabled_at,
                ReminderSchedule.next_fire_at,
            ).where(ReminderSchedule.account_id == account_id)
        ).one_or_none()
        if row is None:
            return None
        return ReminderScheduleRecord(
            account_id=row.account_id,
            telegram_chat_id=row.telegram_chat_id,
            timezone_name=row.tz,
            local_time=row.local_time,
            enabled=row.enabled,
            disabled_reason=row.disabled_reason,
            disabled_at=row.disabled_at,
            next_fire_at=row.next_fire_at,
        )

    def update(
        self,
        account_id: UUID,
        *,
        timezone_name: str,
        local_time: time,
        enabled: bool,
        disabled_reason: str | None,
        disabled_at: datetime | None,
        next_fire_at: datetime,
        now: datetime,
    ) -> None:
        """Write the whole canonical resource, never a subset.

        §10 makes `PUT` a replacement of `{enabled, time, timezone}`, and the
        three service columns are recomputed from it. Updating only the changed
        fields would leave `next_fire_at` describing a time that is no longer
        the schedule's — the exact drift the "never advance by adding" rule
        exists to prevent, arriving through the other door.
        """
        self._session.execute(
            update(ReminderSchedule)
            .where(ReminderSchedule.account_id == account_id)
            .values(
                tz=timezone_name,
                local_time=local_time,
                enabled=enabled,
                disabled_reason=disabled_reason,
                disabled_at=disabled_at,
                next_fire_at=next_fire_at,
                updated_at=now,
            )
        )
        self._session.flush()

    def blocked_since(self, moment: datetime) -> list[UUID]:
        """§10: blocks that have stood, unbroken, since at or before `moment`.

        The predicate is `disabled_reason IS NOT NULL`, never `NOT enabled` —
        the two read very differently even though, under
        `ck_reminder_schedule_disabled_at_matches_reason`, they select the same
        rows here: a pause has no `disabled_at`, so the second condition is NULL
        for it either way. The schema is what keeps a pause out, and the
        predicate is written to say the same thing the schema enforces rather
        than to rely on it. That distinction is worth stating because a test
        cannot draw it: the state that would tell the two apart — disabled, with
        a timestamp and no reason — is one the CHECK forbids.
        """
        rows = self._session.execute(
            select(ReminderSchedule.account_id).where(
                ReminderSchedule.disabled_reason.is_not(None),
                ReminderSchedule.disabled_at <= moment,
            )
        ).all()
        return [row.account_id for row in rows]

    def queue_cleanup(self, account_id: UUID, *, expires_at: datetime) -> int:
        """§6.4: hand the sent messages to the worker before the rows go.

        **Why one statement per message, and not `INSERT … SELECT`.** The set
        version needs `ON CONFLICT … DO NOTHING` to be idempotent, and
        PostgreSQL requires `SELECT` on the target for that clause — a
        privilege §6.3 withholds from `api_rw` on purpose («fills it but never
        reads it back»). Widening the grant to keep the query tidy would trade
        a real isolation property for one round trip. A conflicting row also
        aborts the whole set statement, so the tidy version would silently drop
        the messages that did *not* conflict.

        A duplicate is reachable in exactly one situation: a restore puts the
        delivery rows back while an earlier queue entry is still inside its
        forty-eight hours. Skipping it keeps the **first** `expires_at`, which
        is the conservative half — re-queueing would extend a window §6.4
        promises to be at most that long.

        The identifiers do pass through this method, and they go no further:
        nothing here returns them, logs them, or hands them to a service. Only
        the count leaves.
        """
        rows = self._session.execute(
            select(
                ReminderSchedule.telegram_chat_id,
                ReminderDelivery.telegram_message_id,
            )
            .join(
                ReminderDelivery,
                ReminderDelivery.account_id == ReminderSchedule.account_id,
            )
            .where(
                ReminderSchedule.account_id == account_id,
                ReminderDelivery.telegram_message_id.is_not(None),
            )
        ).all()

        queued = 0
        for row in rows:
            try:
                with self._session.begin_nested():
                    self._session.execute(
                        text(
                            "INSERT INTO reminders.message_cleanup"
                            " (account_id, chat_id, message_id, expires_at)"
                            " VALUES (:account_id, :chat_id, :message_id,"
                            " :expires_at)"
                        ),
                        {
                            "account_id": account_id,
                            "chat_id": row.telegram_chat_id,
                            "message_id": row.telegram_message_id,
                            "expires_at": expires_at,
                        },
                    )
            except IntegrityError:
                continue
            queued += 1
        return queued

    def delete(self, account_id: UUID) -> None:
        """§4.4: no schedule row may outlive its consent."""
        self._session.execute(
            delete(ReminderDelivery).where(ReminderDelivery.account_id == account_id)
        )
        self._session.execute(
            delete(ReminderSchedule).where(ReminderSchedule.account_id == account_id)
        )

    def delete_deliveries_before(self, moment: datetime) -> int:
        """§6.2: 14-day TTL, run by `api_rw`.

        Deliberately not the reminder worker's job — §6.3 withholds `DELETE` on
        this table from that role and keeps a negative test on it, so that the
        first person to need housekeeping cannot quietly repair the GRANT and
        dissolve the isolation test of phase 4.
        """
        rows = self._session.execute(
            delete(ReminderDelivery)
            .where(ReminderDelivery.created_at < moment)
            .returning(ReminderDelivery.account_id)
        ).all()
        return len(rows)


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
        """Idempotent: the first confirmation is the true one.

        Two confirmations of one erasure are normal now that the dispatcher
        acts as the safety net for the writer that never got to close its
        entry — the fast path confirms right after the commit, and the
        redelivered event confirms again. Without the guard the second call
        would move `completed_at` forward and state that the erasure finished
        later than it did.
        """
        self._session.execute(
            update(ErasureJob)
            .where(ErasureJob.id == job_id, ErasureJob.completed_at.is_(None))
            .values(completed_at=at)
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
