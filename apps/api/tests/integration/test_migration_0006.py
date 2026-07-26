"""Migration 0006: the endpoints §11 does not name get a bucket."""

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

BUCKET_CHECK = "ck_rate_window_bucket"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _bucket_check(connection: psycopg.Connection) -> str:
    definition = connection.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s",
        (BUCKET_CHECK,),
    ).fetchone()
    assert definition is not None
    return str(definition[0])


def _account(connection: psycopg.Connection) -> uuid.UUID:
    account_id = uuid.uuid4()
    connection.execute(
        "INSERT INTO diary.account (id, status) VALUES (%s, 'active')",
        (account_id,),
    )
    return account_id


def _window(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    bucket: str,
) -> None:
    connection.execute(
        "INSERT INTO diary.rate_window (account_id, bucket, window_start, used)"
        " VALUES (%s, %s, now(), 1)",
        (account_id, bucket),
    )


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        # Саме "0006", а не "head": наступна ревізія не має червонити цей модуль.
        command.upgrade(_alembic_config(admin_url), "0006")
        yield admin_url


def test_head_is_the_account_ops_revision(database: str) -> None:
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone() == ("0006",)


def test_the_account_operations_budget_has_a_bucket(database: str) -> None:
    """The window §11 does not name, and the reason a bucket is a migration."""
    with psycopg.connect(database, autocommit=True) as connection:
        account_id = _account(connection)
        _window(connection, account_id, "account_ops")

        with pytest.raises(psycopg.errors.CheckViolation):
            _window(connection, account_id, "invented")

        connection.execute("DELETE FROM diary.account WHERE id = %s", (account_id,))


def test_the_four_earlier_buckets_are_still_admitted(database: str) -> None:
    """A widened CHECK that dropped an old value would be a silent regression."""
    with psycopg.connect(database, autocommit=True) as connection:
        account_id = _account(connection)
        for bucket in ("sync", "push_bytes", "key_read", "reminders_settings"):
            _window(connection, account_id, bucket)

        stored = {
            row[0]
            for row in connection.execute(
                "SELECT bucket FROM diary.rate_window WHERE account_id = %s",
                (account_id,),
            ).fetchall()
        }
        assert stored == {"sync", "push_bytes", "key_read", "reminders_settings"}
        connection.execute("DELETE FROM diary.account WHERE id = %s", (account_id,))


def test_downgrade_restores_0005_and_upgrade_replays() -> None:
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        config = _alembic_config(admin_url)

        command.upgrade(config, "0006")
        # A row in the new bucket is what makes the downgrade interesting: a
        # narrowing CHECK cannot be added over data that violates it, so the
        # downgrade has to clear the bucket first or it fails halfway.
        with psycopg.connect(admin_url, autocommit=True) as connection:
            _window(connection, _account(connection), "account_ops")

        command.downgrade(config, "0005")
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone() == ("0005",)
            assert "account_ops" not in _bucket_check(connection)
            assert "reminders_settings" in _bucket_check(connection)

        command.upgrade(config, "0006")
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone() == ("0006",)
            assert "account_ops" in _bucket_check(connection)


def test_the_narrowed_check_refuses_the_new_bucket_after_a_downgrade() -> None:
    """The downgrade has to leave a database that cannot hold the new bucket.

    Reading the constraint definition would pass on a constraint that names the
    bucket in a comment, so the assertion is an insert.
    """
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        config = _alembic_config(admin_url)
        command.upgrade(config, "0006")
        command.downgrade(config, "0005")

        with psycopg.connect(admin_url, autocommit=True) as connection:
            account_id = _account(connection)
            with pytest.raises(psycopg.errors.CheckViolation):
                _window(connection, account_id, "account_ops")
