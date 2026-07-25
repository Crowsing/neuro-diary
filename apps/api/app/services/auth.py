"""Atomic first sign-in, sessions, and step-up (§8).

The three branches of §8 are one transaction each. The branch with neither an
account nor a grant must leave zero rows, so the replay digest is recorded only
once a session is actually issued — a rejected attempt is not "used up", which
is what lets the consent dialog be shown and the call retried with a grant.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from app.domain.identity import (
    STEP_UP_FRESHNESS,
    AuthReplayed,
    ConsentKind,
    NoAccount,
    ProtectedOperation,
    StepUpRequired,
    requires_step_up,
)
from app.domain.records import ConsentRecord, SessionRecord, SessionSummary
from app.services.consent import ConsentService, GrantRequest
from app.services.ports import (
    Clock,
    InitDataValidatorPort,
    UnitOfWork,
    UnitOfWorkFactory,
)

_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    token: str
    session: SessionRecord
    consents: list[ConsentRecord]


def hash_token(token: str) -> bytes:
    """The database holds only the digest; the token itself is never stored.

    Encoded as UTF-8 rather than ASCII: Starlette decodes headers as latin-1, so
    a single high byte in `Authorization` would otherwise raise before any
    lookup and turn an unauthenticated request into a 500.
    """
    return hashlib.sha256(token.encode("utf-8")).digest()


class AuthService:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        init_data: InitDataValidatorPort,
        consents: ConsentService,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._init_data = init_data
        self._consents = consents
        self._clock = clock

    def now(self) -> datetime:
        """Single source of "now" for routes that span several services."""
        return self._clock.now()

    def authenticate(
        self,
        init_data: str,
        *,
        grant: GrantRequest | None = None,
    ) -> AuthenticatedSession:
        now = self._clock.now()
        validated = self._init_data.validate(init_data, now=now)

        with self._unit_of_work() as unit:
            account_id = unit.identities.account_id_for(validated.telegram_user_id)

            if account_id is None and grant is None:
                # Branch 2 of §8: the server keeps knowing nothing. Leaving the
                # block without committing is what makes "zero rows" structural.
                raise NoAccount()

            if not unit.auth_replay.remember(validated.replay_digest, now=now):
                raise AuthReplayed()

            if account_id is None:
                assert grant is not None  # narrowed by the branch above
                account_id = self._create_account(
                    unit,
                    telegram_user_id=validated.telegram_user_id,
                    grant=grant,
                    now=now,
                )
            else:
                unit.identities.touch(validated.telegram_user_id, now=now)
                if grant is not None:
                    self._consents.grant(
                        unit,
                        account_id=account_id,
                        telegram_user_id=validated.telegram_user_id,
                        request=grant,
                        now=now,
                    )

            token = secrets.token_urlsafe(_TOKEN_BYTES)
            session = unit.sessions.create(
                account_id,
                token_hash=hash_token(token),
                now=now,
            )
            consents = unit.consents.active(account_id)
            unit.commit()

        return AuthenticatedSession(token=token, session=session, consents=consents)

    def _create_account(
        self,
        unit: UnitOfWork,
        *,
        telegram_user_id: int,
        grant: GrantRequest,
        now: datetime,
    ) -> UUID:
        account_id = uuid4()
        unit.accounts.create(account_id, created_at=now)
        unit.identities.create(telegram_user_id, account_id, now=now)
        self._consents.grant(
            unit,
            account_id=account_id,
            telegram_user_id=telegram_user_id,
            request=grant,
            now=now,
        )
        return account_id

    def resolve_session(self, unit: UnitOfWork, token: str) -> SessionRecord | None:
        now = self._clock.now()
        record = unit.sessions.find_active(hash_token(token), now=now)
        if record is None:
            return None
        expires_at = unit.sessions.slide(record, now=now)
        return SessionRecord(
            id=record.id,
            account_id=record.account_id,
            created_at=record.created_at,
            expires_at=expires_at,
            last_used_at=now,
        )

    def list_sessions(
        self,
        unit: UnitOfWork,
        session: SessionRecord,
    ) -> list[SessionSummary]:
        return unit.sessions.list_for_account(
            session.account_id,
            now=self._clock.now(),
            current_id=session.id,
        )

    def revoke_other_sessions(self, unit: UnitOfWork, session: SessionRecord) -> int:
        return unit.sessions.revoke_others(
            session.account_id,
            keep=session.id,
            now=self._clock.now(),
        )

    def require_step_up(
        self,
        session: SessionRecord,
        operation: ProtectedOperation,
    ) -> None:
        """A fresh session is one that revalidated initData within ten minutes.

        Sessions are issued at the moment of validation, so `created_at` is that
        moment; no extra column is needed to express §8's definition.
        """
        if not requires_step_up(operation):
            return
        if not self.is_stepped_up(session):
            raise StepUpRequired()

    def is_stepped_up(self, session: SessionRecord) -> bool:
        """The same freshness test, asked rather than enforced.

        `GET /v1/sync/key` needs it as a question: an ordinary read succeeds
        without step-up, and only the previous envelope of §7 is withheld.
        """
        return self._clock.now() - session.created_at <= STEP_UP_FRESHNESS


__all__ = [
    "AuthService",
    "AuthenticatedSession",
    "ConsentKind",
    "hash_token",
]
