"""Erasure worker: the deadlines nobody triggers interactively.

Two jobs live here. The first is the §4.3 window for accounts that lost their
last consent for a reason that was not the user's decision — blocking a bot is
a common, easily reversed gesture, so those accounts wait 30 days rather than
disappearing within the minute. The second is the service-table TTLs of §6.2,
each of which otherwise accumulates traces indefinitely.

`run_once` takes no time of its own: the clock arrives through the port, which
is what makes the deadlines testable without waiting for them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.identity import (
    ACCOUNT_ERASURE_DELAY,
    AUTH_REPLAY_TTL,
    CONSENT_ROW_RETENTION,
    SESSION_MAX_LIFETIME,
    RevokeReason,
)
from app.services.erasure import ErasureService
from app.services.ports import Clock, UnitOfWorkFactory

DELAYED_REASONS = [
    RevokeReason.BOT_BLOCKED_TIMEOUT,
    RevokeReason.STALE_TEXT_TIMEOUT,
]


@dataclass(frozen=True, slots=True)
class HousekeepingResult:
    accounts_erased: int
    replay_digests_removed: int
    sessions_removed: int
    consent_rows_removed: int


class Housekeeper:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        erasure: ErasureService,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._erasure = erasure
        self._clock = clock

    def run_once(self) -> HousekeepingResult:
        now = self._clock.now()
        erased = self._erase_expired_accounts()
        with self._unit_of_work() as unit:
            replay = unit.auth_replay.delete_seen_before(now - AUTH_REPLAY_TTL)
            sessions = unit.sessions.delete_settled_before(now - SESSION_MAX_LIFETIME)
            consents = unit.consents.delete_revoked_before(now - CONSENT_ROW_RETENTION)
            unit.commit()
        return HousekeepingResult(
            accounts_erased=erased,
            replay_digests_removed=replay,
            sessions_removed=sessions,
            consent_rows_removed=consents,
        )

    def _erase_expired_accounts(self) -> int:
        now = self._clock.now()
        with self._unit_of_work() as unit:
            pending = unit.consents.accounts_awaiting_erasure(
                reasons=DELAYED_REASONS,
                revoked_before=now - ACCOUNT_ERASURE_DELAY,
            )

        erased = 0
        for candidate in pending:
            # One transaction per account: a single unreachable journal must not
            # cost every other account its deadline.
            with self._unit_of_work() as unit:
                unit.accounts.lock(candidate.account_id)
                if unit.consents.active_kinds(candidate.account_id):
                    continue
                reference = self._erasure.erase(
                    unit,
                    account_id=candidate.account_id,
                    now=now,
                )
                unit.commit()
            self._erasure.confirm(reference, now=now)
            erased += 1
        return erased
