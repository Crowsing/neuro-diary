"""Migration 0005: the block gets a clock, and the settings budget gets a bucket."""

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

PAIRING = "ck_reminder_schedule_disabled_at_matches_reason"
BLOCKED_INDEX = "ix_reminder_schedule_blocked"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _constraints(connection: psycopg.Connection, table: str) -> set[str]:
    schema, _, name = table.partition(".")
    return {
        row[0]
        for row in connection.execute(
            """
            SELECT constraint_row.conname
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS relation ON relation.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = %s AND relation.relname = %s
            """,
            (schema, name),
        ).fetchall()
    }


def _columns(connection: psycopg.Connection, schema: str, table: str) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        ).fetchall()
    }


def _indexes(connection: psycopg.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname = 'reminders' AND tablename = 'reminder_schedule'
            """
        ).fetchall()
    }


def _schedule_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "account_id": uuid.uuid4(),
        "telegram_chat_id": 4242424242,
        "tz": "Europe/Kyiv",
        "local_time": "20:00",
        "enabled": True,
        "disabled_reason": None,
        "disabled_at": None,
        "next_fire_at": "2026-07-26T17:00:00+00:00",
    }
    values.update(overrides)
    return values


def _insert_schedule(connection: psycopg.Connection, **overrides: object) -> None:
    values = _schedule_values(**overrides)
    connection.execute(
        """
        INSERT INTO reminders.reminder_schedule (
            account_id, telegram_chat_id, tz, local_time,
            enabled, disabled_reason, disabled_at, next_fire_at
        )
        VALUES (
            %(account_id)s, %(telegram_chat_id)s, %(tz)s, %(local_time)s,
            %(enabled)s, %(disabled_reason)s, %(disabled_at)s, %(next_fire_at)s
        )
        """,
        values,
    )


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        # Саме "0005", а не "head": наступна ревізія не має червонити цей модуль.
        command.upgrade(_alembic_config(admin_url), "0005")
        yield admin_url


def test_head_is_the_reminder_delivery_revision(database: str) -> None:
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone() == ("0005",)
        assert "disabled_at" in _columns(connection, "reminders", "reminder_schedule")
        assert PAIRING in _constraints(connection, "reminders.reminder_schedule")
        assert BLOCKED_INDEX in _indexes(connection)


def test_a_block_without_a_clock_cannot_be_written(database: str) -> None:
    """Without the pairing rule the reconciler reads a deadline that has no block."""
    with psycopg.connect(database, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_schedule(
                connection,
                enabled=False,
                disabled_reason="bot_blocked",
                disabled_at=None,
            )


def test_a_clock_without_a_block_cannot_be_written(database: str) -> None:
    """The other half: a timestamp that outlived its reason is a false deadline."""
    with psycopg.connect(database, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_schedule(
                connection,
                enabled=False,
                disabled_reason=None,
                disabled_at="2026-07-26T12:00:00+00:00",
            )


def test_a_pause_carries_no_deadline_and_is_accepted(database: str) -> None:
    """`enabled = false` with neither reason nor clock is the pause of §10.

    The counterpart to the two rejections above: without it they could both pass
    on a table that rejects every disabled row, and the pause — the state the
    reconciler must never act on — would be unrepresentable.
    """
    account_id = uuid.uuid4()
    with psycopg.connect(database, autocommit=True) as connection:
        _insert_schedule(
            connection,
            account_id=account_id,
            enabled=False,
            disabled_reason=None,
            disabled_at=None,
        )
        assert connection.execute(
            "SELECT disabled_reason, disabled_at FROM reminders.reminder_schedule"
            " WHERE account_id = %s",
            (account_id,),
        ).fetchone() == (None, None)
        connection.execute("DELETE FROM reminders.reminder_schedule")


def test_a_block_with_its_clock_is_accepted(database: str) -> None:
    account_id = uuid.uuid4()
    with psycopg.connect(database, autocommit=True) as connection:
        _insert_schedule(
            connection,
            account_id=account_id,
            enabled=False,
            disabled_reason="bot_blocked",
            disabled_at="2026-07-26T12:00:00+00:00",
        )
        row = connection.execute(
            "SELECT disabled_reason FROM reminders.reminder_schedule"
            " WHERE account_id = %s",
            (account_id,),
        ).fetchone()
        assert row == ("bot_blocked",)
        connection.execute("DELETE FROM reminders.reminder_schedule")


def test_the_settings_budget_has_a_bucket(database: str) -> None:
    """§11 puts `reminders-settings 20/хв` beside the sync budgets, in PostgreSQL."""
    account_id = uuid.uuid4()
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO diary.account (id, status) VALUES (%s, 'active')",
            (account_id,),
        )
        connection.execute(
            "INSERT INTO diary.rate_window (account_id, bucket, window_start, used)"
            " VALUES (%s, 'reminders_settings', now(), 1)",
            (account_id,),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "INSERT INTO diary.rate_window (account_id, bucket, window_start, used)"
                " VALUES (%s, 'invented', now(), 1)",
                (account_id,),
            )
        connection.execute("DELETE FROM diary.account WHERE id = %s", (account_id,))


def test_downgrade_restores_0004_and_upgrade_replays() -> None:
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        config = _alembic_config(admin_url)

        command.upgrade(config, "0005")
        # A row in the new bucket is what makes the downgrade interesting: a
        # narrowing CHECK cannot be added over data that violates it, so the
        # downgrade has to clear the bucket first or it fails halfway.
        account_id = uuid.uuid4()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO diary.account (id, status) VALUES (%s, 'active')",
                (account_id,),
            )
            connection.execute(
                "INSERT INTO diary.rate_window"
                " (account_id, bucket, window_start, used)"
                " VALUES (%s, 'reminders_settings', now(), 1)",
                (account_id,),
            )

        command.downgrade(config, "0004")
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone() == ("0004",)
            assert "disabled_at" not in _columns(
                connection, "reminders", "reminder_schedule"
            )
            assert PAIRING not in _constraints(
                connection, "reminders.reminder_schedule"
            )
            assert BLOCKED_INDEX not in _indexes(connection)

        command.upgrade(config, "0005")
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone() == ("0005",)
            assert "disabled_at" in _columns(
                connection, "reminders", "reminder_schedule"
            )
            assert PAIRING in _constraints(connection, "reminders.reminder_schedule")
            assert BLOCKED_INDEX in _indexes(connection)
