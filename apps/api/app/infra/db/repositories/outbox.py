"""Transactional outbox: the producer side and the queue side (§4.4, §6.2).

`publish` runs inside the caller's transaction — that is the whole point of the
pattern: an event exists if and only if the change it announces committed.

`claim` and `mark_processed` are separate calls rather than one, because the
work between them is the consumer's and may fail. `SKIP LOCKED` lets a second
dispatcher run without waiting on the first, and the `processed_at IS NULL`
guard in `mark_processed` is what makes a redelivered event a no-op instead of
a moved timestamp.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from app.domain.records import PendingEvent
from app.infra.db.models import Outbox


class OutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def publish(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> None:
        self._session.execute(
            insert(Outbox).values(
                event_type=event_type,
                payload=payload,
                created_at=now,
                processed_at=None,
            )
        )
        self._session.flush()

    def claim(self, *, limit: int) -> list[PendingEvent]:
        """Oldest unprocessed events, locked for this transaction only."""
        rows = self._session.execute(
            select(Outbox.id, Outbox.event_type, Outbox.payload)
            .where(Outbox.processed_at.is_(None))
            .order_by(Outbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        return [
            PendingEvent(id=row.id, event_type=row.event_type, payload=row.payload)
            for row in rows
        ]

    def mark_processed(self, event_id: int, *, at: datetime) -> bool:
        """Idempotent close: an already-processed event keeps its timestamp."""
        rows = self._session.execute(
            update(Outbox)
            .where(Outbox.id == event_id, Outbox.processed_at.is_(None))
            .values(processed_at=at)
            .returning(Outbox.id)
        ).all()
        return len(rows) == 1

    def delete_processed_before(self, moment: datetime) -> int:
        """§6.2: 30 days after `processed_at`, never before it is set."""
        rows = self._session.execute(
            delete(Outbox)
            .where(Outbox.processed_at.is_not(None), Outbox.processed_at < moment)
            .returning(Outbox.id)
        ).all()
        return len(rows)

    def delete_for_account(self, account_id: UUID) -> int:
        """The erasure-matrix entry for this table (§6.4).

        There is no `account_id` column and no foreign key, so the row would
        otherwise outlive the account it names — including the event that
        announced the account's own erasure.
        """
        rows = self._session.execute(
            delete(Outbox)
            .where(Outbox.payload["account_id"].astext == str(account_id))
            .returning(Outbox.id)
        ).all()
        return len(rows)
