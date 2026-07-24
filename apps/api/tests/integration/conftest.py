"""Shared PostgreSQL 16 fixtures for the integration suite.

Tests connect as `api_rw`, never as the admin role: a repository that only
passes under a superuser would hide a missing GRANT until production.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import Engine, create_engine, text
from testcontainers.postgres import PostgresContainer

from app.infra.db.engine import SqlUnitOfWorkFactory

API_ROOT = Path(__file__).resolve().parents[2]

DIARY_TABLES = (
    "consent",
    "session_token",
    "auth_replay",
    "erasure_job",
    "outbox",
    "telegram_identity",
    "account",
)
# `message_cleanup` is absent on purpose: §6.3 gives api_rw INSERT only, and
# phase 1 never writes there. Cleaning it would need a privilege the production
# role must not have.
REMINDER_TABLES = ("reminder_delivery", "reminder_schedule")


@dataclass(frozen=True)
class Database:
    admin_url: str
    api_url: str
    api_password: str = field(repr=False)


class FrozenClock:
    """Deterministic clock; services take it through the `Clock` port."""

    def __init__(self, moment: datetime | None = None) -> None:
        self._moment = moment or datetime(2026, 7, 24, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._moment

    def advance(self, **delta: float) -> None:
        from datetime import timedelta

        self._moment += timedelta(**delta)

    def set(self, moment: datetime) -> None:
        self._moment = moment


@pytest.fixture(scope="session")
def identity_database() -> Iterator[Database]:
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        config = Config(str(API_ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", admin_url.replace("%", "%%"))
        command.upgrade(config, "head")

        password = secrets.token_urlsafe(24)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("ALTER ROLE api_rw PASSWORD {}").format(sql.Literal(password))
            )

        scheme, _, rest = admin_url.partition("://")
        _, _, host_part = rest.partition("@")
        yield Database(
            admin_url=admin_url,
            api_url=f"{scheme}://api_rw:{password}@{host_part}",
            api_password=password,
        )


@pytest.fixture(scope="session")
def api_engine(identity_database: Database) -> Iterator[Engine]:
    created = create_engine(
        identity_database.api_url.replace("postgresql://", "postgresql+psycopg://", 1)
    )
    yield created
    created.dispose()


@pytest.fixture
def engine(api_engine: Engine) -> Iterator[Engine]:
    """Per-test handle that truncates afterwards.

    Not autouse: the migration suites bring their own containers, and an
    autouse cleanup would attach to them too.
    """
    yield api_engine
    with api_engine.begin() as connection:
        for table in REMINDER_TABLES:
            connection.execute(text(f"DELETE FROM reminders.{table}"))
        for table in DIARY_TABLES:
            connection.execute(text(f"DELETE FROM diary.{table}"))


@pytest.fixture
def unit_of_work(engine: Engine) -> SqlUnitOfWorkFactory:
    return SqlUnitOfWorkFactory(engine)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()
