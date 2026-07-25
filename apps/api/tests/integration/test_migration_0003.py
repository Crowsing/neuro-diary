from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

API_ROOT = Path(__file__).resolve().parents[2]

NEW_CONSTRAINTS = {
    "ck_consent_record_key_cycle_size",
    "ck_consent_record_key_cycle_only_on_cycle_sync",
    "ck_vault_revision_consent_epoch_nonnegative",
    "ck_rate_window_bucket",
    "ck_rate_window_used_nonnegative",
}
TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)


def _alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _columns(
    connection: psycopg.Connection,
    schema: str,
    table: str,
) -> dict[str, tuple[str, str]]:
    return {
        row[0]: (row[1], row[2])
        for row in connection.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, table),
        ).fetchall()
    }


def _constraints(connection: psycopg.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            """
            SELECT constraint_row.conname
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS relation
              ON relation.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname IN ('diary', 'reminders')
            """
        ).fetchall()
    }


def _has_table_privilege(
    connection: psycopg.Connection,
    role: str,
    relation: str,
    privilege: str,
) -> bool:
    row = connection.execute(
        "SELECT has_table_privilege(%s, %s, %s)",
        (role, relation, privilege),
    ).fetchone()
    assert row is not None
    return bool(row[0])


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        # Саме "0003", а не "head": цей модуль стверджує стан після своєї
        # міграції, і наступна фаза не має його червонити.
        command.upgrade(_alembic_config(admin_url), "0003")
        yield admin_url


def test_head_is_the_vault_sync_revision(database: str) -> None:
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone() == ("0003",)
        assert NEW_CONSTRAINTS <= _constraints(connection)


def test_consent_carries_the_named_cycle_record_key(database: str) -> None:
    with psycopg.connect(database) as connection:
        columns = _columns(connection, "diary", "consent")
        assert columns["record_key_cycle"] == ("bytea", "YES")


def test_the_named_cycle_key_must_be_thirty_two_bytes(database: str) -> None:
    with psycopg.connect(database, autocommit=True) as connection:
        definition = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'ck_consent_record_key_cycle_size'
            """
        ).fetchone()
        assert definition is not None
        assert "32" in definition[0]


def test_the_named_cycle_key_belongs_to_cycle_sync_only(database: str) -> None:
    with psycopg.connect(database, autocommit=True) as connection:
        definition = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'ck_consent_record_key_cycle_only_on_cycle_sync'
            """
        ).fetchone()
        assert definition is not None
        assert "cycle_sync" in definition[0]


def test_vault_revision_carries_a_consent_epoch(database: str) -> None:
    with psycopg.connect(database) as connection:
        columns = _columns(connection, "diary", "vault_revision")
        assert columns["consent_epoch"] == ("bigint", "NO")

        default = connection.execute(
            """
            SELECT column_default
            FROM information_schema.columns
            WHERE table_schema = 'diary'
              AND table_name = 'vault_revision'
              AND column_name = 'consent_epoch'
            """
        ).fetchone()
        assert default is not None
        assert default[0] == "0"


def test_rate_window_holds_one_row_per_account_and_bucket(database: str) -> None:
    with psycopg.connect(database) as connection:
        columns = _columns(connection, "diary", "rate_window")
        assert columns["account_id"] == ("uuid", "NO")
        assert columns["bucket"] == ("text", "NO")
        assert columns["window_start"] == ("timestamp with time zone", "NO")
        assert columns["used"] == ("bigint", "NO")

        indexes = {
            row[0]
            for row in connection.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'diary' AND tablename = 'rate_window'
                """
            ).fetchall()
        }
        assert "pk_rate_window" in indexes


def test_rate_window_accepts_only_the_three_buckets_of_section_eleven(
    database: str,
) -> None:
    with psycopg.connect(database, autocommit=True) as connection:
        definition = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'ck_rate_window_bucket'
            """
        ).fetchone()
        assert definition is not None
        for bucket in ("sync", "push_bytes", "key_read"):
            assert f"'{bucket}'" in definition[0]


def test_rate_window_rows_leave_with_their_account(database: str) -> None:
    with psycopg.connect(database, autocommit=True) as connection:
        definition = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'fk_rate_window_account'
            """
        ).fetchone()
        assert definition is not None
        assert "ON DELETE CASCADE" in definition[0]


def test_rate_window_is_invisible_to_the_reminder_worker(database: str) -> None:
    expected = {
        "api_rw": {"SELECT", "INSERT", "UPDATE", "DELETE"},
        "reminder_worker": set[str](),
    }
    with psycopg.connect(database) as connection:
        for role, granted in expected.items():
            for privilege in TABLE_PRIVILEGES:
                assert _has_table_privilege(
                    connection,
                    role,
                    "diary.rate_window",
                    privilege,
                ) == (privilege in granted)


def test_downgrade_restores_0002_and_upgrade_replays() -> None:
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        config = _alembic_config(admin_url)

        command.upgrade(config, "0003")
        command.downgrade(config, "0002")
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone() == ("0002",)
            assert "record_key_cycle" not in _columns(connection, "diary", "consent")
            assert "consent_epoch" not in _columns(
                connection, "diary", "vault_revision"
            )
            assert _columns(connection, "diary", "rate_window") == {}
            assert not (NEW_CONSTRAINTS & _constraints(connection))

        command.upgrade(config, "0003")
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone() == ("0003",)
            assert NEW_CONSTRAINTS <= _constraints(connection)
