"""Shared PostgreSQL 16 fixtures for the integration suite.

Tests connect as `api_rw`, never as the admin role: a repository that only
passes under a superuser would hide a missing GRANT until production.
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from psycopg import sql
from sqlalchemy import Engine, create_engine, text
from testcontainers.postgres import PostgresContainer

from app.infra.config import Settings
from app.infra.consent_copy import FileConsentCopyRegistry
from app.infra.db.engine import SqlUnitOfWorkFactory
from app.services.erasure_journal import DatabaseErasureJournal
from app.infra.telegram.initdata import InitDataValidator
from app.main import AppDependencies, create_app

API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parents[1]

BOT_ID = 1234567890
TELEGRAM_USER_ID = 4242424242
SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC_KEY = SIGNING_KEY.public_key().public_bytes_raw()

DIARY_TABLES = (
    "consent",
    "session_token",
    "auth_replay",
    "erasure_job",
    "outbox",
    "telegram_identity",
    "account",
)
REMINDER_TABLES = ("reminder_delivery", "reminder_schedule")
# `message_cleanup` is cleaned through the admin connection rather than through
# `api_rw`, which holds `INSERT` on it and nothing else (§6.3). Phase 4 gives
# the table its first writer, so leaving it out of the reset would let one
# test's queue reach the next one's worker.
ADMIN_ONLY_TABLES = ("reminders.message_cleanup",)


@dataclass(frozen=True)
class Database:
    admin_url: str
    api_url: str
    api_password: str = field(repr=False)
    #: Phase 4. The delivery worker runs under its own role, and the DoD asks
    #: for the full insert-before-send cycle to be proven under *that* role
    #: rather than under `api_rw` — a positive test as `api_rw` would pass on
    #: privileges the production process does not have.
    worker_url: str = ""
    worker_password: str = field(default="", repr=False)


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
        worker_password = secrets.token_urlsafe(24)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("ALTER ROLE api_rw PASSWORD {}").format(sql.Literal(password))
            )
            connection.execute(
                sql.SQL("ALTER ROLE reminder_worker PASSWORD {}").format(
                    sql.Literal(worker_password)
                )
            )

        scheme, _, rest = admin_url.partition("://")
        _, _, host_part = rest.partition("@")
        yield Database(
            admin_url=admin_url,
            api_url=f"{scheme}://api_rw:{password}@{host_part}",
            api_password=password,
            worker_url=f"{scheme}://reminder_worker:{worker_password}@{host_part}",
            worker_password=worker_password,
        )


@pytest.fixture(scope="session")
def worker_engine(identity_database: Database) -> Iterator[Engine]:
    """An engine bound to `reminder_worker`, privileges and all.

    Kept separate from `api_engine` on purpose: a worker test that reached the
    database through the api engine would pass on `diary` access the real
    process is denied, and the isolation of §5.3 would be asserted nowhere.
    """
    created = create_engine(
        identity_database.worker_url.replace("postgresql://", "postgresql+psycopg://", 1)
    )
    yield created
    created.dispose()


@pytest.fixture(scope="session")
def api_engine(identity_database: Database) -> Iterator[Engine]:
    created = create_engine(
        identity_database.api_url.replace("postgresql://", "postgresql+psycopg://", 1)
    )
    yield created
    created.dispose()


@pytest.fixture
def engine(api_engine: Engine, identity_database: Database) -> Iterator[Engine]:
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
    with psycopg.connect(identity_database.admin_url, autocommit=True) as admin:
        for qualified in ADMIN_ONLY_TABLES:
            admin.execute(f"DELETE FROM {qualified}")


@pytest.fixture
def unit_of_work(engine: Engine) -> SqlUnitOfWorkFactory:
    return SqlUnitOfWorkFactory(engine)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def settings(identity_database: Database) -> Settings:
    return Settings.from_env(
        {
            "API_DATABASE_URL": identity_database.api_url,
            "TELEGRAM_BOT_ID": str(BOT_ID),
            "WEBAPP_URL": "https://example.invalid",
            "APP_ENV": "development",
            "TELEGRAM_ED25519_PUBLIC_KEY": PUBLIC_KEY.hex(),
            "LOG_ACCOUNT_REF_KEY": bytes(range(32)).hex(),
        }
    )


@pytest.fixture
def consent_copy() -> FileConsentCopyRegistry:
    return FileConsentCopyRegistry(REPO_ROOT / "consent-copy")


@pytest.fixture
def init_data_validator(settings: Settings) -> InitDataValidator:
    return InitDataValidator(
        bot_id=settings.telegram_bot_id,
        public_key=settings.telegram_ed25519_public_key,
    )


@pytest.fixture
def dependencies(
    settings: Settings,
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
    init_data_validator: InitDataValidator,
    consent_copy: FileConsentCopyRegistry,
) -> AppDependencies:
    return AppDependencies(
        settings=settings,
        unit_of_work=unit_of_work,
        clock=clock,
        init_data=init_data_validator,
        consent_copy=consent_copy,
        erasure_journal=DatabaseErasureJournal(unit_of_work, consent_copy),
    )


@pytest.fixture
def api(dependencies: AppDependencies) -> FastAPI:
    return create_app(dependencies)


class RefusingJournal:
    """An `ErasureJournalPort` that cannot record anything.

    §6.4 puts the journal write before the deletion and says a failure to write
    it must stop the erasure. Every code that erases owes a test against this,
    so it lives here rather than in the file of whichever suite needed it first.
    """

    def record_intent(self, *, account_id: UUID, code: str, at: datetime) -> UUID:
        del account_id, code, at
        raise RuntimeError("journal unavailable")

    def confirm(self, reference: UUID, *, at: datetime) -> None:  # pragma: no cover
        del reference, at


@pytest.fixture
def refusing_journal_api(
    dependencies: AppDependencies,
    settings: Settings,
) -> FastAPI:
    return create_app(
        AppDependencies(
            settings=settings,
            unit_of_work=dependencies.unit_of_work,
            clock=dependencies.clock,
            init_data=dependencies.init_data,
            consent_copy=dependencies.consent_copy,
            erasure_journal=RefusingJournal(),
        )
    )


class Caller:
    """Synchronous facade over the ASGI transport."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self.token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        token: str | None = None,
    ) -> httpx.Response:
        bearer = token if token is not None else self.token
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}

        async def _send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self._app, client=("203.0.113.7", 1))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as session:
                return await session.request(
                    method, path, json=json_body, headers=headers
                )

        return asyncio.run(_send())

    def get(self, path: str, *, token: str | None = None) -> httpx.Response:
        return self.request("GET", path, token=token)

    def post(
        self,
        path: str,
        body: dict[str, object] | None = None,
        *,
        token: str | None = None,
    ) -> httpx.Response:
        return self.request("POST", path, json_body=body, token=token)

    def put(
        self,
        path: str,
        body: dict[str, object] | None = None,
        *,
        token: str | None = None,
    ) -> httpx.Response:
        return self.request("PUT", path, json_body=body, token=token)


@pytest.fixture
def caller(api: FastAPI) -> Caller:
    return Caller(api)


def sign_init_data(
    *,
    telegram_user_id: int = TELEGRAM_USER_ID,
    auth_date: datetime | None = None,
    nonce: str | None = None,
) -> str:
    """Build initData signed with the deterministic test key."""
    moment = auth_date or FrozenClock().now()
    fields: dict[str, str] = {
        "auth_date": str(int(moment.timestamp())),
        "user": json.dumps({"id": telegram_user_id}),
    }
    if nonce is not None:
        fields["query_id"] = nonce

    pairs = sorted(fields.items())
    body = "\n".join(f"{key}={value}" for key, value in pairs)
    signature = SIGNING_KEY.sign(f"{BOT_ID}:WebAppData\n{body}".encode())
    fields["signature"] = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return "&".join(f"{key}={quote(value, safe='')}" for key, value in fields.items())
