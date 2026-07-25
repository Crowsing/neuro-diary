"""Vault compactor: the tombstones nobody deletes interactively.

Separate from `Housekeeper` on purpose. The TTLs it sweeps are single DELETE
statements over service tables; compaction is a per-account transaction that
takes the §9.1 lock order and moves a horizon other requests are gated on.
Folding one into the other would make a mistake in either one look like a
mistake in both.

`run_once` takes no time of its own: the clock arrives through the port, which
is what makes the horizon testable without waiting 187 days for it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.vault import TOMBSTONE_TTL
from app.services.ports import Clock, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class CompactionResult:
    accounts_compacted: int
    tombstones_removed: int


class VaultCompactor:
    def __init__(self, unit_of_work: UnitOfWorkFactory, clock: Clock) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock

    def run_once(self, *, batch: int = 100) -> CompactionResult:
        cutoff = self._clock.now() - TOMBSTONE_TTL
        with self._unit_of_work() as unit:
            candidates = unit.vault.accounts_with_stale_tombstones(
                older_than=cutoff,
                limit=batch,
            )

        accounts = 0
        removed = 0
        for account_id in candidates:
            # One transaction per account, in the lock order of §9.1: the
            # account row first, then the counters. A compactor that took them
            # the other way round would deadlock against an ordinary push
            # instead of queueing behind it.
            with self._unit_of_work() as unit:
                if not unit.accounts.lock(account_id):
                    continue
                unit.vault.lock_counters(account_id)
                deleted = unit.vault.compact(account_id, older_than=cutoff)
                unit.commit()
            if deleted:
                accounts += 1
                removed += deleted
        return CompactionResult(accounts_compacted=accounts, tombstones_removed=removed)
