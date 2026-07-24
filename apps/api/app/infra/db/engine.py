"""Engine, session factory, and the unit of work that owns both.

Sessions never leave this package (§5.1). Services see a `UnitOfWork` protocol
whose only lifecycle verbs are commit and rollback; leaving the context without
committing discards the work, which is what makes "403 and zero rows" a
structural property rather than a discipline.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.infra.db.migrations.database_url import with_psycopg_driver
from app.infra.db.repositories.consent import (
    ConsentRepository,
    ErasureRepository,
    ReminderScheduleRepository,
)
from app.infra.db.repositories.identity import (
    AccountRepository,
    AuthReplayRepository,
    SessionRepository,
    TelegramIdentityRepository,
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

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


class SqlUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._sessionmaker = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def __call__(self) -> Iterator[SqlUnitOfWork]:
        session = self._sessionmaker()
        unit = SqlUnitOfWork(session)
        try:
            yield unit
        except BaseException:
            unit.rollback()
            raise
        else:
            # An uncommitted block is a discarded block, never a silent commit.
            unit.rollback()
        finally:
            session.close()
