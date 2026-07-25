"""Vault synchronization: the push transaction, pull, and the envelope CAS.

The push transaction of §9.1 is a fixed order of seven steps, and the order is
the whole design: it is what turns two concurrent pushes of one account into
one 200 and one 409 instead of a lost update. It is written out linearly here
so the order stays reviewable on one screen.

Outcomes that carry data (`conflict_keys`, `current_wrap_version`) are returned
as values, never raised: `DomainError` cannot hold a field, and that is exactly
what keeps an input value out of an error response (§11).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.identity import ConsentKind, ConsentPrecondition
from app.domain.records import RateVerdict, RecordWrite, StoredRecord, StoredVaultKey
from app.domain.vault import (
    KEY_READ_WINDOW,
    KEY_READS_PER_HOUR,
    MAX_PUSH_BYTES,
    MAX_RECORD_BYTES,
    MAX_RECORDS_PER_PUSH,
    PREV_ENVELOPE_MIN_INTERVAL,
    PREV_ENVELOPE_TTL,
    PULL_PAGE_LIMIT,
    PUSH_BYTES_PER_MINUTE,
    SYNC_REQUESTS_PER_MINUTE,
    SYNC_WINDOW,
    KeyWriteMode,
    PayloadTooLarge,
    RateBucket,
    VaultForbidden,
    VaultGone,
    VaultReset,
)
from app.services.ports import Clock, UnitOfWork


@dataclass(frozen=True, slots=True)
class PushApplied:
    new_revision: int


@dataclass(frozen=True, slots=True)
class PushConflict:
    """Per-key conflict detected under the lock; nothing was written."""

    conflict_keys: list[bytes]


PushOutcome = PushApplied | PushConflict


@dataclass(frozen=True, slots=True)
class PullPage:
    records: list[StoredRecord]
    next_since: int
    current_revision: int
    reset: bool
    consent_state_changed: bool


@dataclass(frozen=True, slots=True)
class KeyView:
    stored: StoredVaultKey
    previous_offered: bool


@dataclass(frozen=True, slots=True)
class KeyWriteApplied:
    key_version: int
    wrap_version: int


@dataclass(frozen=True, slots=True)
class KeyWriteConflict:
    current_wrap_version: int


KeyWriteOutcome = KeyWriteApplied | KeyWriteConflict


def _window_start(now: datetime, seconds: int) -> datetime:
    """Fixed window: the same instant for every request of one period."""
    epoch = int(now.timestamp()) // seconds * seconds
    return datetime.fromtimestamp(epoch, tz=now.tzinfo)


class SyncService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def now(self) -> datetime:
        return self._clock.now()

    # ------------------------------------------------------------------ push

    def push(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        base_revision: int,
        changes: list[RecordWrite],
        now: datetime,
    ) -> PushOutcome:
        self._assert_within_size_limits(changes)

        # 1. The account row is always the first lock: the erasure transaction
        #    of §9.8 takes the same one, which is what serializes them.
        if not unit.accounts.lock(account_id):
            raise VaultForbidden()
        # 2. The first push of two devices must not race on the counter row.
        unit.vault.ensure_counters(account_id)
        # 3. Per-account serialization, before any read of vault_record.
        counters = unit.vault.lock_counters(account_id)
        # 4. Re-check inside the transaction: a consent revoked between the
        #    dependency check and here must still refuse the write.
        if unit.accounts.status(account_id) != "active":
            raise VaultForbidden()
        active = unit.consents.active_kinds(account_id)
        if ConsentKind.HEALTH_SYNC not in active:
            raise VaultForbidden()
        self._assert_named_domains_are_consented(
            unit,
            account_id=account_id,
            changes=changes,
            active=active,
        )
        # 5. Guards, in this order: a reset is a stronger statement than a
        #    compaction horizon and must answer first (§9.4).
        if base_revision < counters.reset_revision:
            raise VaultReset()
        if base_revision < counters.compacted_up_to:
            raise VaultGone()
        # 6. Per-key conflict check under the lock. Outside it the answer is
        #    already stale by the time anyone acts on it.
        ordered = sorted(changes, key=lambda write: write.record_key)
        stored = unit.vault.revisions_for(
            account_id, [write.record_key for write in ordered]
        )
        conflicts = sorted(
            key for key, revision in stored.items() if revision > base_revision
        )
        if conflicts:
            return PushConflict(conflict_keys=conflicts)
        # 7. Write, then move the counter. Every record takes its own revision.
        unit.vault.upsert(
            account_id,
            writes=ordered,
            first_revision=counters.current_revision + 1,
            now=now,
        )
        new_revision = counters.current_revision + len(ordered)
        unit.vault.set_current_revision(account_id, value=new_revision)
        return PushApplied(new_revision=new_revision)

    def _assert_within_size_limits(self, changes: list[RecordWrite]) -> None:
        """Answer 413 before the database answers 500.

        `ck_vault_record_payload_limit` of migration 0001 caps a payload at
        64 KiB, so an oversized record that reached the INSERT would surface as
        an integrity error rather than as the documented refusal.
        """
        if len(changes) > MAX_RECORDS_PER_PUSH:
            raise PayloadTooLarge()
        total = 0
        for write in changes:
            size = 0 if write.payload is None else len(write.payload)
            if size > MAX_RECORD_BYTES:
                raise PayloadTooLarge()
            total += size
        if total > MAX_PUSH_BYTES:
            raise PayloadTooLarge()

    def _assert_named_domains_are_consented(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        changes: list[RecordWrite],
        active: set[ConsentKind],
    ) -> None:
        """§9.7: refuse a write to a domain whose consent is inactive.

        The server cannot tell which ciphertext is the cycle — it knows only
        the one `record_key` the client named at grant time. Without a named
        key the gate cannot fire, and that limit is the honest thing to
        document rather than to work around.
        """
        if ConsentKind.CYCLE_SYNC in active:
            return
        named = unit.consents.named_cycle_key(account_id)
        if named is None:
            return
        if any(write.record_key == named for write in changes):
            raise ConsentPrecondition()

    # ------------------------------------------------------------------ pull

    def pull(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        since: int,
        limit: int,
        consent_epoch_seen: int,
    ) -> PullPage:
        if unit.accounts.status(account_id) != "active":
            raise VaultForbidden()
        if ConsentKind.HEALTH_SYNC not in unit.consents.active_kinds(account_id):
            raise VaultForbidden()

        counters = unit.vault.counters(account_id)
        if since < counters.compacted_up_to:
            raise VaultGone()

        records = unit.vault.page(
            account_id,
            since=since,
            limit=min(limit, PULL_PAGE_LIMIT),
        )
        return PullPage(
            records=records,
            next_since=records[-1].revision if records else since,
            current_revision=counters.current_revision,
            reset=since < counters.reset_revision,
            consent_state_changed=consent_epoch_seen < counters.consent_epoch,
        )

    # ------------------------------------------------------------- vault key

    def read_key(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        stepped_up: bool,
        now: datetime,
    ) -> KeyView | None:
        stored = unit.vault_keys.read(account_id)
        if stored is None:
            return None
        # The previous envelope makes a mistaken overwrite reversible, but it
        # is a second door to the same vault: only step-up opens it, and only
        # while the TTL of §7 holds.
        offer_previous = stepped_up and (
            stored.prev_written_at is not None
            and now - stored.prev_written_at < PREV_ENVELOPE_TTL
        )
        if offer_previous:
            return KeyView(stored=stored, previous_offered=True)
        return KeyView(
            stored=StoredVaultKey(
                wrapped_dek=stored.wrapped_dek,
                kdf=stored.kdf,
                kdf_params=stored.kdf_params,
                key_version=stored.key_version,
                wrap_version=stored.wrap_version,
                wrapped_dek_prev=None,
                wrap_version_prev=None,
                prev_written_at=None,
            ),
            previous_offered=False,
        )

    def write_key(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        mode: KeyWriteMode,
        expected_wrap_version: int,
        wrapped_dek: bytes,
        kdf: str,
        kdf_params: dict[str, Any],
        now: datetime,
    ) -> KeyWriteOutcome:
        if not unit.accounts.lock(account_id):
            raise VaultForbidden()
        current = unit.vault_keys.lock(account_id)

        if current is None:
            # The first envelope of a vault; `expected_wrap_version = 0` is how
            # a client states it believes there is none.
            if expected_wrap_version != 0:
                return KeyWriteConflict(current_wrap_version=0)
            unit.vault_keys.write(
                account_id,
                wrapped_dek=wrapped_dek,
                kdf=kdf,
                kdf_params=kdf_params,
                key_version=1,
                wrap_version=1,
                keep_previous=False,
                now=now,
            )
            return KeyWriteApplied(key_version=1, wrap_version=1)

        if current.wrap_version != expected_wrap_version:
            return KeyWriteConflict(current_wrap_version=current.wrap_version)

        # §7: the CAS runs on wrap_version. A re-wrap leaves key_version alone,
        # so two concurrent re-wraps would both pass a CAS on key_version and
        # one of the new passphrases would be lost without a word.
        key_version = (
            current.key_version + 1
            if mode is KeyWriteMode.REKEY
            else current.key_version
        )
        keep_previous = (
            current.prev_written_at is None
            or now - current.prev_written_at >= PREV_ENVELOPE_MIN_INTERVAL
        )
        unit.vault_keys.write(
            account_id,
            wrapped_dek=wrapped_dek,
            kdf=kdf,
            kdf_params=kdf_params,
            key_version=key_version,
            wrap_version=current.wrap_version + 1,
            keep_previous=keep_previous,
            now=now,
        )
        return KeyWriteApplied(
            key_version=key_version,
            wrap_version=current.wrap_version + 1,
        )

    # ----------------------------------------------------------- vault reset

    def vault_reset(self, unit: UnitOfWork, *, account_id: UUID) -> int:
        if not unit.accounts.lock(account_id):
            raise VaultForbidden()
        unit.vault.ensure_counters(account_id)
        counters = unit.vault.lock_counters(account_id)
        unit.vault.delete_all(account_id)
        revision = counters.current_revision + 1
        unit.vault.mark_reset(account_id, revision=revision)
        return revision

    # ----------------------------------------------------------- rate limits

    def consume_budget(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        bucket: RateBucket,
        cost: int,
        now: datetime,
    ) -> RateVerdict:
        """§11 windows, committed separately from the work they guard.

        If the budget were consumed inside the push transaction, every 409 and
        every 410 would roll the counter back — and a looping client would push
        for free exactly when it is misbehaving.
        """
        limit, window = {
            RateBucket.SYNC: (SYNC_REQUESTS_PER_MINUTE, SYNC_WINDOW),
            RateBucket.PUSH_BYTES: (PUSH_BYTES_PER_MINUTE, SYNC_WINDOW),
            RateBucket.KEY_READ: (KEY_READS_PER_HOUR, KEY_READ_WINDOW),
        }[bucket]
        seconds = int(window.total_seconds())
        return unit.rate_windows.consume(
            account_id,
            bucket=bucket.value,
            cost=cost,
            limit=limit,
            window_start=_window_start(now, seconds),
            window_seconds=seconds,
            now=now,
        )
