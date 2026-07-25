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
        """Current and reset move together; the horizon stays where it was.

        Moving `compacted_up_to` here as well made `reset: true` unreachable:
        the 410 gate of pull is checked before the reset flag, so every cursor
        below the reset revision answered 410 and the confirmation screen §9.4
        asks for could never appear. The reset gate of push is the first guard
        of §9.1 anyway, so a stale device is still stopped — with the answer
        that names the reason.
        """
        self._session.execute(
            update(VaultRevision)
            .where(VaultRevision.account_id == account_id)
            .values(current_revision=revision, reset_revision=revision)
        )

    def delete_named_key(self, account_id: UUID, *, record_key: bytes) -> int:
        """§9.7: hard DELETE of one named record, without a tombstone.

        A tombstone on the singleton `cycle`, appearing exactly when the server
        has a fresh `revoked_at('cycle_sync')`, would deanonymize that
        `record_key` and retroactively its whole update history — the one thing
        the opaque-key scheme exists to prevent.
        """
        rows = self._session.execute(
            delete(VaultRecord)
            .where(
                VaultRecord.account_id == account_id,
                VaultRecord.record_key == record_key,
            )
            .returning(VaultRecord.record_key)
        ).all()
        return len(rows)

    def reset_counters(self, account_id: UUID, *, revision: int) -> None:
        """§4.3: all three counters move together when the vault is emptied.

        Not the same statement as `mark_reset`, which deliberately leaves the
        compaction horizon alone so that `reset: true` stays reachable on pull.
        Here the vault has no records left at all, so there is nothing for a
        cursor below the horizon to return, and §4.3 asks for the horizon to
        move as well: a device that reconnects after a re-grant must be sent to
        a full resync rather than handed an empty page.
        """
        self._session.execute(
            update(VaultRevision)
            .where(VaultRevision.account_id == account_id)
            .values(
                current_revision=revision,
                compacted_up_to=revision,
                reset_revision=revision,
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
        """DELETE and horizon advance in one transaction, as §6.4 requires.

        The horizon never passes a live record. §6.4 says
        `compacted_up_to = GREATEST(compacted_up_to, max(revision видалених))`,
        and taken literally that number can exceed the revision of a record
        that is still stored: revisions are per write, not per key, so a live
        record written early keeps a low revision forever. The horizon would
        then declare it unreachable — every legal cursor answers 410, and the
        full resync §9.2 sends the client to has nothing to return. A device
        that has just installed the app would never receive the diary at all.

        Capping the horizon below the lowest live revision keeps the promise
        that mattered (tombstones past their TTL are gone and cannot be
        pushed against) without making live data unreachable. The deviation is
        recorded in the plan.
        """
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
        lowest_live = self._session.execute(
            select(func.min(VaultRecord.revision)).where(
                VaultRecord.account_id == account_id
            )
        ).scalar_one_or_none()
        horizon = highest if lowest_live is None else min(highest, lowest_live - 1)
        self._session.execute(
            update(VaultRevision)
            .where(VaultRevision.account_id == account_id)
            .values(
                compacted_up_to=func.greatest(VaultRevision.compacted_up_to, horizon)
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

    def delete(self, account_id: UUID) -> int:
        """Crypto-erasure (§6.4): the envelope goes with the records.

        Old backups keep the old envelope — this strengthens the TTL promise
        rather than replacing it, and saying otherwise would make the deletion
        copy untrue.
        """
        rows = self._session.execute(
            delete(VaultKey)
            .where(VaultKey.account_id == account_id)
            .returning(VaultKey.account_id)
        ).all()
        return len(rows)

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
        now: datetime,
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
        # Скільки лишилося до кінця вікна, а не яка воно завдовжки: рахунок від
        # `window_start` давав би константу — 3600 секунд для key_read навіть
        # тоді, коли вікно закривається за секунду.
        closes_at = row.window_start + timedelta(seconds=window_seconds)
        remaining = int((closes_at - now).total_seconds())
        return RateVerdict(allowed=False, retry_after_seconds=max(remaining, 1))

    def clear(self, account_id: UUID) -> None:
        self._session.execute(
            delete(RateWindow).where(RateWindow.account_id == account_id)
        )
