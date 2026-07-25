"""Vault, envelope, and rate-window repositories.

This module is the only place that names a lock mode or an upsert. The push
transaction of §9.1 is expressed by the *order* in which the service calls
these methods; nothing here decides policy on its own.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domain.records import (
    RateVerdict,
    RecordWrite,
    StoredRecord,
    StoredVaultKey,
    VaultCounters,
)
from app.infra.db.models import RateWindow, VaultKey, VaultRecord, VaultRevision


class VaultRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_counters(self, account_id: UUID) -> None:
        """Step 2 of §9.1: the first push of two devices must not race."""
        self._session.execute(
            pg_insert(VaultRevision)
            .values(
                account_id=account_id,
                current_revision=0,
                compacted_up_to=0,
                reset_revision=0,
                consent_epoch=0,
            )
            .on_conflict_do_nothing(index_elements=["account_id"])
        )

    def counters(self, account_id: UUID) -> VaultCounters:
        row = self._session.execute(
            select(
                VaultRevision.current_revision,
                VaultRevision.compacted_up_to,
                VaultRevision.reset_revision,
                VaultRevision.consent_epoch,
            ).where(VaultRevision.account_id == account_id)
        ).one_or_none()
        if row is None:
            return VaultCounters(
                current_revision=0,
                compacted_up_to=0,
                reset_revision=0,
                consent_epoch=0,
            )
        return VaultCounters(
            current_revision=row.current_revision,
            compacted_up_to=row.compacted_up_to,
            reset_revision=row.reset_revision,
            consent_epoch=row.consent_epoch,
        )

    def lock_counters(self, account_id: UUID) -> VaultCounters:
        """Step 3 of §9.1: per-account serialization of every write path.

        `FOR UPDATE` rather than `key_share`: this row is read and then written
        in the same transaction, and a share lock would let two pushes both
        read the same `current_revision`.
        """
        row = self._session.execute(
            select(
                VaultRevision.current_revision,
                VaultRevision.compacted_up_to,
                VaultRevision.reset_revision,
                VaultRevision.consent_epoch,
            )
            .where(VaultRevision.account_id == account_id)
            .with_for_update()
        ).one()
        return VaultCounters(
            current_revision=row.current_revision,
            compacted_up_to=row.compacted_up_to,
            reset_revision=row.reset_revision,
            consent_epoch=row.consent_epoch,
        )

    def revisions_for(self, account_id: UUID, keys: list[bytes]) -> dict[bytes, int]:
        """Step 6 of §9.1. Valid only under the lock taken in step 3."""
        if not keys:
            return {}
        rows = self._session.execute(
            select(VaultRecord.record_key, VaultRecord.revision).where(
                VaultRecord.account_id == account_id,
                VaultRecord.record_key.in_(keys),
            )
        ).all()
        return {bytes(row.record_key): row.revision for row in rows}

    def upsert(
        self,
        account_id: UUID,
        *,
        writes: list[RecordWrite],
        first_revision: int,
        now: datetime,
    ) -> None:
        """Step 7 of §9.1. Every record gets its own revision.

        `ux_vault_rev` is UNIQUE on (account_id, revision), so one revision per
        batch would fail the second record of any multi-record push.
        """
        for offset, write in enumerate(writes):
            revision = first_revision + offset
            payload = None if write.tombstone else write.payload
            statement = (
                pg_insert(VaultRecord)
                .values(
                    account_id=account_id,
                    record_key=write.record_key,
                    payload=payload,
                    payload_size=0 if payload is None else len(payload),
                    revision=revision,
                    deleted=write.tombstone,
                    client_ts_ms=write.client_ts_ms,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["account_id", "record_key"],
                    set_={
                        "payload": payload,
                        "payload_size": 0 if payload is None else len(payload),
                        "revision": revision,
                        "deleted": write.tombstone,
                        "client_ts_ms": write.client_ts_ms,
                        "updated_at": now,
                    },
                )
            )
            self._session.execute(statement)

    def set_current_revision(self, account_id: UUID, *, value: int) -> None:
        self._session.execute(
            update(VaultRevision)
            .where(VaultRevision.account_id == account_id)
            .values(current_revision=value)
        )

    def bump_consent_epoch(self, account_id: UUID) -> None:
        """Neutral signal for pull: a consent changed, without naming which."""
        self._session.execute(
            update(VaultRevision)
            .where(VaultRevision.account_id == account_id)
            .values(consent_epoch=VaultRevision.consent_epoch + 1)
        )

    def page(self, account_id: UUID, *, since: int, limit: int) -> list[StoredRecord]:
        rows = self._session.execute(
            select(
                VaultRecord.record_key,
                VaultRecord.payload,
                VaultRecord.deleted,
                VaultRecord.revision,
                VaultRecord.client_ts_ms,
            )
            .where(
                VaultRecord.account_id == account_id,
                VaultRecord.revision > since,
            )
            .order_by(VaultRecord.revision)
            .limit(limit)
        ).all()
        return [
            StoredRecord(
                record_key=bytes(row.record_key),
                payload=None if row.payload is None else bytes(row.payload),
                deleted=row.deleted,
                revision=row.revision,
                client_ts_ms=row.client_ts_ms,
            )
            for row in rows
        ]

    def delete_all(self, account_id: UUID) -> int:
        rows = self._session.execute(
            delete(VaultRecord)
            .where(VaultRecord.account_id == account_id)
            .returning(VaultRecord.record_key)
        ).all()
        return len(rows)

    def mark_reset(self, account_id: UUID, *, revision: int) -> None:
        """All three counters move together (§9.4).

        Leaving `compacted_up_to` behind would let a stale device pass the 410
        gate straight into an emptied vault.
        """
        self._session.execute(
            update(VaultRevision)
            .where(VaultRevision.account_id == account_id)
            .values(
                current_revision=revision,
                reset_revision=revision,
                compacted_up_to=revision,
            )
        )

    def accounts_with_stale_tombstones(
        self,
        *,
        older_than: datetime,
        limit: int,
    ) -> list[UUID]:
        rows = self._session.execute(
            select(VaultRecord.account_id)
            .where(VaultRecord.deleted.is_(True), VaultRecord.updated_at < older_than)
            .group_by(VaultRecord.account_id)
            .limit(limit)
        ).all()
        return [row.account_id for row in rows]

    def compact(self, account_id: UUID, *, older_than: datetime) -> int:
        """DELETE and horizon advance in one statement, as §6.4 requires."""
        removed = self._session.execute(
            delete(VaultRecord)
            .where(
                VaultRecord.account_id == account_id,
                VaultRecord.deleted.is_(True),
                VaultRecord.updated_at < older_than,
            )
            .returning(VaultRecord.revision)
        ).all()
        if not removed:
            return 0
        highest = max(row.revision for row in removed)
        self._session.execute(
            update(VaultRevision)
            .where(VaultRevision.account_id == account_id)
            .values(
                compacted_up_to=func.greatest(VaultRevision.compacted_up_to, highest)
            )
        )
        return len(removed)


class VaultKeyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _row(self, account_id: UUID, *, lock: bool) -> StoredVaultKey | None:
        statement = select(
            VaultKey.wrapped_dek,
            VaultKey.kdf,
            VaultKey.kdf_params,
            VaultKey.key_version,
            VaultKey.wrap_version,
            VaultKey.wrapped_dek_prev,
            VaultKey.wrap_version_prev,
            VaultKey.prev_written_at,
        ).where(VaultKey.account_id == account_id)
        if lock:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return StoredVaultKey(
            wrapped_dek=bytes(row.wrapped_dek),
            kdf=row.kdf,
            kdf_params=row.kdf_params,
            key_version=row.key_version,
            wrap_version=row.wrap_version,
            wrapped_dek_prev=(
                None if row.wrapped_dek_prev is None else bytes(row.wrapped_dek_prev)
            ),
            wrap_version_prev=row.wrap_version_prev,
            prev_written_at=row.prev_written_at,
        )

    def read(self, account_id: UUID) -> StoredVaultKey | None:
        return self._row(account_id, lock=False)

    def lock(self, account_id: UUID) -> StoredVaultKey | None:
        """Read under `FOR UPDATE` so the CAS on `wrap_version` is atomic."""
        return self._row(account_id, lock=True)

    def write(
        self,
        account_id: UUID,
        *,
        wrapped_dek: bytes,
        kdf: str,
        kdf_params: dict[str, Any],
        key_version: int,
        wrap_version: int,
        keep_previous: bool,
        now: datetime,
    ) -> None:
        previous = self._row(account_id, lock=False)
        columns: dict[str, Any] = {
            "wrapped_dek": wrapped_dek,
            "kdf": kdf,
            "kdf_params": kdf_params,
            "key_version": key_version,
            "wrap_version": wrap_version,
        }
        if keep_previous and previous is not None:
            columns["wrapped_dek_prev"] = previous.wrapped_dek
            columns["wrap_version_prev"] = previous.wrap_version
            columns["prev_written_at"] = now

        self._session.execute(
            pg_insert(VaultKey)
            .values(account_id=account_id, **columns)
            .on_conflict_do_update(index_elements=["account_id"], set_=columns)
        )

    def clear_expired_previous(self, account_id: UUID, *, older_than: datetime) -> None:
        self._session.execute(
            update(VaultKey)
            .where(
                VaultKey.account_id == account_id,
                VaultKey.prev_written_at.is_not(None),
                VaultKey.prev_written_at < older_than,
            )
            .values(
                wrapped_dek_prev=None,
                wrap_version_prev=None,
                prev_written_at=None,
            )
        )


class RateWindowRepository:
    """Fixed windows in PostgreSQL, no Redis (§11).

    One row per (account, bucket) — at most three per account — so the table
    needs no TTL job and leaves with the account it belongs to.
    """

    _CONSUME = text(
        """
        INSERT INTO diary.rate_window AS w (account_id, bucket, window_start, used)
        VALUES (:account_id, :bucket, :window_start, :cost)
        ON CONFLICT (account_id, bucket) DO UPDATE
           SET window_start = GREATEST(w.window_start, :window_start),
               used = CASE
                          WHEN w.window_start < :window_start THEN :cost
                          ELSE w.used + :cost
                      END
        RETURNING used, window_start
        """
    )

    def __init__(self, session: Session) -> None:
        self._session = session

    def consume(
        self,
        account_id: UUID,
        *,
        bucket: str,
        cost: int,
        limit: int,
        window_start: datetime,
        window_seconds: int,
    ) -> RateVerdict:
        row = self._session.execute(
            self._CONSUME,
            {
                "account_id": account_id,
                "bucket": bucket,
                "window_start": window_start,
                "cost": cost,
            },
        ).one()
        if row.used <= limit:
            return RateVerdict(allowed=True, retry_after_seconds=0)
        closes_at = row.window_start + timedelta(seconds=window_seconds)
        remaining = int((closes_at - window_start).total_seconds())
        return RateVerdict(allowed=False, retry_after_seconds=max(remaining, 1))

    def clear(self, account_id: UUID) -> None:
        self._session.execute(
            delete(RateWindow).where(RateWindow.account_id == account_id)
        )
