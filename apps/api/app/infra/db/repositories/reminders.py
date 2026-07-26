"""The `reminder_worker` half of the `reminders` schema (§6.3).

Every statement here is reachable under a role with `SELECT/UPDATE` on
`reminder_schedule`, `SELECT/INSERT/UPDATE` on `reminder_delivery`,
`SELECT/DELETE` on `message_cleanup` and **no privilege at all on `diary`**.
That is not a coincidence to be preserved by care: the GRANT tests run this
class under the real role, so a statement that needs more fails there rather
than in production.

`message_cleanup` is reached through `text()` rather than an ORM class. §6.4
records that the table has no model, and the reason it gives — no foreign key,
no erasure path, `INSERT` only for `api_rw` — is the reason it is worth keeping
true rather than convenient.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.records import DueReminder, PendingCleanup, StalePending
from app.domain.reminders import BOT_BLOCKED, DeliveryStatus
from app.infra.db.models import ReminderDelivery, ReminderSchedule


class ReminderWorkerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim_due(self, *, moment: datetime, limit: int) -> list[DueReminder]:
        """Lock the schedules that are due, skipping whatever another pass holds.

        `FOR UPDATE SKIP LOCKED` over `ix_reminder_schedule_due` is what lets a
        second worker exist at all: it takes the next rows instead of waiting
        for the first, and a crash releases the lock rather than stranding the
        account until someone notices.

        The lock is a lease for the caller's transaction, so the caller must not
        hold it across a network call to Telegram — the occurrence claim in
        `reminder_delivery` is what survives that, and it is a row of its own.
        """
        rows = self._session.execute(
            select(
                ReminderSchedule.account_id,
                ReminderSchedule.telegram_chat_id,
                ReminderSchedule.tz,
                ReminderSchedule.local_time,
                ReminderSchedule.next_fire_at,
            )
            .where(
                ReminderSchedule.enabled.is_(True),
                ReminderSchedule.next_fire_at <= moment,
            )
            .order_by(ReminderSchedule.next_fire_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        return [
            DueReminder(
                account_id=row.account_id,
                telegram_chat_id=row.telegram_chat_id,
                timezone_name=row.tz,
                local_time=row.local_time,
                next_fire_at=row.next_fire_at,
            )
            for row in rows
        ]

    def claim_occurrence(
        self, account_id: UUID, *, local_date: date, now: datetime
    ) -> bool:
        """Insert-before-send: the primary key is the idempotency key (§10).

        A conflict is the answer «this day is already spoken for», and it is
        deliberately not distinguished from «already sent»: at-most-once means
        that a day which has been claimed once is never sent again, whatever
        became of that attempt.

        A SAVEPOINT wraps the insert because a duplicate key aborts the whole
        transaction in PostgreSQL otherwise, and the caller still has a
        rescheduling to commit.
        """
        try:
            with self._session.begin_nested():
                self._session.execute(
                    insert(ReminderDelivery).values(
                        account_id=account_id,
                        local_date=local_date,
                        status=DeliveryStatus.PENDING.value,
                        telegram_message_id=None,
                        created_at=now,
                    )
                )
        except IntegrityError:
            return False
        return True

    def finish_occurrence(
        self,
        account_id: UUID,
        *,
        local_date: date,
        status: str,
        telegram_message_id: int | None = None,
    ) -> None:
        """Close a claimed occurrence.

        Guarded on `status = 'pending'` so a sweeper that already wrote `failed`
        is never overwritten by a late-returning attempt. Without the guard the
        two writers race and the row can end up claiming a send that the sweeper
        had already given up on.
        """
        self._session.execute(
            update(ReminderDelivery)
            .where(
                ReminderDelivery.account_id == account_id,
                ReminderDelivery.local_date == local_date,
                ReminderDelivery.status == DeliveryStatus.PENDING.value,
            )
            .values(status=status, telegram_message_id=telegram_message_id)
        )

    def reschedule(
        self, account_id: UUID, *, next_fire_at: datetime, now: datetime
    ) -> None:
        self._session.execute(
            update(ReminderSchedule)
            .where(ReminderSchedule.account_id == account_id)
            .values(next_fire_at=next_fire_at, updated_at=now)
        )

    def record_block(self, account_id: UUID, *, now: datetime) -> None:
        """§10: a 403 switches the schedule off and starts the streak.

        Guarded on `disabled_reason IS NULL`, which makes it idempotent in the
        way that matters: a second 403 must not move `disabled_at` forward, or
        a bot that stays blocked would push its own deadline away for ever and
        the reconciler would never fire.
        """
        self._session.execute(
            update(ReminderSchedule)
            .where(
                ReminderSchedule.account_id == account_id,
                ReminderSchedule.disabled_reason.is_(None),
            )
            .values(
                enabled=False,
                disabled_reason=BOT_BLOCKED,
                disabled_at=now,
                updated_at=now,
            )
        )

    def stale_pending(self, *, older_than: datetime) -> list[StalePending]:
        rows = self._session.execute(
            select(ReminderDelivery.account_id, ReminderDelivery.local_date).where(
                ReminderDelivery.status == DeliveryStatus.PENDING.value,
                ReminderDelivery.created_at < older_than,
            )
        ).all()
        return [
            StalePending(account_id=row.account_id, local_date=row.local_date)
            for row in rows
        ]

    def due_cleanups(self, *, moment: datetime, limit: int) -> list[PendingCleanup]:
        rows = self._session.execute(
            text(
                """
                SELECT account_id, chat_id, message_id, expires_at
                FROM reminders.message_cleanup
                ORDER BY expires_at
                LIMIT :limit
                """
            ),
            {"limit": limit, "moment": moment},
        ).all()
        return [
            PendingCleanup(
                account_id=row.account_id,
                chat_id=row.chat_id,
                message_id=row.message_id,
                expires_at=row.expires_at,
            )
            for row in rows
        ]

    def forget_cleanup(self, account_id: UUID, *, message_id: int) -> int:
        rows = self._session.execute(
            text(
                """
                DELETE FROM reminders.message_cleanup
                WHERE account_id = :account_id AND message_id = :message_id
                RETURNING message_id
                """
            ),
            {"account_id": account_id, "message_id": message_id},
        ).all()
        return len(rows)

    def drop_expired_cleanups(self, *, moment: datetime) -> int:
        """§6.4: past the TTL the row goes, whatever Telegram said.

        The promise is that the chat id stops existing here after forty-eight
        hours, not that the message was removed. Keeping a row because its
        deletion kept failing would turn a bounded exception into an unbounded
        one — the opposite of what the queue is for.
        """
        rows = self._session.execute(
            text(
                """
                DELETE FROM reminders.message_cleanup
                WHERE expires_at <= :moment
                RETURNING message_id
                """
            ),
            {"moment": moment},
        ).all()
        return len(rows)
