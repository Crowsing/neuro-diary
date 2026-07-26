"""Engine, session factory, and the unit of work that owns both.

Sessions never leave this package (§5.1). Services see a `UnitOfWork` protocol
whose only lifecycle verbs are commit and rollback; leaving the context without
committing discards the work, which is what makes "403 and zero rows" a
structural property rather than a discipline.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.infra.db.migrations.database_url import with_psycopg_driver
from app.infra.db.repositories.consent import (
    ConsentRepository,
    ErasureRepository,
    ReminderScheduleRepository,
)
from app.infra.db.repositories.outbox import OutboxRepository
from app.infra.db.repositories.reminders import ReminderWorkerRepository
from app.infra.db.repositories.identity import (
    AccountRepository,
    AuthReplayRepository,
    SessionRepository,
    TelegramIdentityRepository,
)
from app.infra.db.repositories.vault import (
    RateWindowRepository,
    VaultKeyRepository,
    VaultRepository,
)


def build_engine(database_url: str) -> Engine:
    return create_engine(with_psycopg_driver(database_url), pool_pre_ping=True)


class SqlUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.accounts = AccountRepository(session)
        self.identities = TelegramIdentityRepository(session)
        self.sessions = SessionRepository(session)
        self.auth_replay = AuthReplayRepository(session)
        self.consents = ConsentRepository(session)
        self.schedules = ReminderScheduleRepository(session)
        self.erasure = ErasureRepository(session)
        self.outbox = OutboxRepository(session)
        self.vault = VaultRepository(session)
        self.vault_keys = VaultKeyRepository(session)
        self.rate_windows = RateWindowRepository(session)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


class SqlUnitOfWorkScope:
    """Context manager written out by hand so its type stays precise."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._unit = SqlUnitOfWork(session)

    def __enter__(self) -> SqlUnitOfWork:
        return self._unit

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # An uncommitted block is a discarded block, never a silent commit.
        try:
            self._unit.rollback()
        finally:
            self._session.close()


class SqlUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._sessionmaker = sessionmaker(engine, expire_on_commit=False)

    def __call__(self) -> SqlUnitOfWorkScope:
        return SqlUnitOfWorkScope(self._sessionmaker())


class SqlReminderUnitOfWork:
    """One repository, because the role behind it can reach one schema.

    Not a narrowed `SqlUnitOfWork`: constructing the others would open no
    connection and break nothing, and that is exactly the problem — the worker
    would then be one autocomplete away from a statement its role refuses, and
    the refusal would arrive in production rather than in the type checker.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self.reminders = ReminderWorkerRepository(session)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


class SqlReminderUnitOfWorkScope:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._unit = SqlReminderUnitOfWork(session)

    def __enter__(self) -> SqlReminderUnitOfWork:
        return self._unit

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self._unit.rollback()
        finally:
            self._session.close()


class SqlReminderUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._sessionmaker = sessionmaker(engine, expire_on_commit=False)

    def __call__(self) -> SqlReminderUnitOfWorkScope:
        return SqlReminderUnitOfWorkScope(self._sessionmaker())
