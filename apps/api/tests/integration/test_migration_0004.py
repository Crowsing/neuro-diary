"""Migration 0004: the outbox becomes a queue, and §11 becomes a constraint."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

API_ROOT = Path(__file__).resolve().parents[2]

NEW_CONSTRAINTS = {
    "ck_outbox_payload_has_account",
    "ck_outbox_payload_names_no_consent",
}
NEW_INDEXES = {
    "ix_outbox_unprocessed",
    "ix_outbox_processed_at",
    "ix_outbox_account",
}


def _alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _constraints(connection: psycopg.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            """
            SELECT constraint_row.conname
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS relation ON relation.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'diary' AND relation.relname = 'outbox'
            """
        ).fetchall()
    }


def _indexes(connection: psycopg.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname = 'diary' AND tablename = 'outbox'
            """
        ).fetchall()
    }


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        # Саме "0004", а не "head": фаза 4 не має червонити цей модуль.
        command.upgrade(_alembic_config(admin_url), "0004")
        yield admin_url


def test_head_is_the_outbox_revision(database: str) -> None:
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone() == ("0004",)
        assert NEW_CONSTRAINTS <= _constraints(connection)
        assert NEW_INDEXES <= _indexes(connection)


def test_an_event_without_an_account_cannot_be_written(database: str) -> None:
    """§6.4 promises zero rows per erased account in all 12 tables.

    An event whose account cannot be named is an event erasure cannot find.
    """
    with psycopg.connect(database, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "INSERT INTO diary.outbox (event_type, payload)"
                " VALUES ('consent_revoked', '{\"note\": \"nothing\"}'::jsonb)"
            )


@pytest.mark.parametrize(
    "payload",
    [
        '{"account_id": "%s", "kind": "health_sync"}',
        '{"account_id": "%s", "kind": "telegram_reminders"}',
        '{"account_id": "%s", "kind": "cycle_sync"}',
        '{"account_id": "%s", "health_sync": true}',
    ],
)
def test_a_payload_that_names_a_consent_cannot_be_written(
    database: str,
    payload: str,
) -> None:
    """§11 and §13.12 as a constraint, not as a convention.

    The check is on the serialized payload, so it catches the kind in a value
    and in a key alike — including a producer written after this phase.
    """
    with psycopg.connect(database, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "INSERT INTO diary.outbox (event_type, payload) VALUES (%s, %s::jsonb)",
                ("consent_revoked", payload % uuid.uuid4()),
            )


def test_a_neutral_payload_is_accepted(database: str) -> None:
    """The counterpart: without this the two checks above could pass on a table
    that rejects everything."""
    account_id = uuid.uuid4()
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO diary.outbox (event_type, payload) VALUES (%s, %s::jsonb)",
            ("consent_revoked", '{"account_id": "%s"}' % account_id),
        )
        stored = connection.execute(
            "SELECT payload ->> 'account_id' FROM diary.outbox WHERE"
            " payload ->> 'account_id' = %s",
            (str(account_id),),
        ).fetchone()
        assert stored == (str(account_id),)
        connection.execute("DELETE FROM diary.outbox")


def test_downgrade_restores_0003_and_upgrade_replays() -> None:
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        config = _alembic_config(admin_url)

        command.upgrade(config, "0004")
        command.downgrade(config, "0003")
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone() == ("0003",)
            assert not (NEW_CONSTRAINTS & _constraints(connection))
            assert not (NEW_INDEXES & _indexes(connection))

        command.upgrade(config, "0004")
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone() == ("0004",)
            assert NEW_CONSTRAINTS <= _constraints(connection)
            assert NEW_INDEXES <= _indexes(connection)
