from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

API_ROOT = Path(__file__).resolve().parents[2]

CONSENT_COLUMNS = {
    "text_sha256": ("bytea", "NO"),
    "text_locale": ("text", "NO"),
    "revoke_reason": ("text", "YES"),
}
VAULT_KEY_COLUMNS = {
    "wrap_version": ("integer", "NO"),
    "wrapped_dek_prev": ("bytea", "YES"),
    "wrap_version_prev": ("integer", "YES"),
    "prev_written_at": ("timestamp with time zone", "YES"),
}
NEW_CONSTRAINTS = {
    "ck_consent_text_sha256_size",
    "ck_consent_revoke_reason",
    "ck_consent_revoke_reason_requires_revocation",
    "ck_erasure_job_scope",
    "ck_erasure_job_deletion_copy_version_nonempty",
    "ck_vault_key_wrap_version_positive",
    "ck_vault_key_previous_envelope_complete",
    "ck_vault_record_client_ts_ms_bounds",
    "ck_message_cleanup_ids_positive",
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
        command.upgrade(_alembic_config(admin_url), "head")
        yield admin_url


def test_head_is_the_identity_consent_revision(database: str) -> None:
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone() == ("0002",)


def test_consent_records_hashed_text_locale_and_revoke_reason(
    database: str,
) -> None:
    with psycopg.connect(database) as connection:
        columns = _columns(connection, "diary", "consent")
        for name, expected in CONSENT_COLUMNS.items():
            assert columns[name] == expected

        default = connection.execute(
            """
            SELECT column_default
            FROM information_schema.columns
            WHERE table_schema = 'diary'
              AND table_name = 'consent'
              AND column_name = 'text_locale'
            """
        ).fetchone()
        assert default is not None
        assert default[0] == "'uk'::text"

        assert NEW_CONSTRAINTS <= _constraints(connection)


def test_revoke_reason_accepts_only_the_three_gate_d_values(
    database: str,
) -> None:
    with psycopg.connect(database, autocommit=True) as connection:
        definition = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'ck_consent_revoke_reason'
            """
        ).fetchone()
        assert definition is not None
        for value in ("user", "bot_blocked_timeout", "stale_text_timeout"):
            assert f"'{value}'" in definition[0]


def test_erasure_job_records_the_deletion_copy_version(database: str) -> None:
    with psycopg.connect(database) as connection:
        columns = _columns(connection, "diary", "erasure_job")
        assert columns["deletion_copy_version"] == ("text", "NO")


def test_vault_key_gains_wrap_version_and_previous_envelope(
    database: str,
) -> None:
    with psycopg.connect(database) as connection:
        columns = _columns(connection, "diary", "vault_key")
        for name, expected in VAULT_KEY_COLUMNS.items():
            assert columns[name] == expected


def test_vault_record_client_timestamp_is_milliseconds(database: str) -> None:
    with psycopg.connect(database) as connection:
        columns = _columns(connection, "diary", "vault_record")
        assert columns["client_ts_ms"] == ("bigint", "NO")
        assert "client_ts" not in columns


def test_message_cleanup_queue_exists_with_expiry_index(database: str) -> None:
    with psycopg.connect(database) as connection:
        columns = _columns(connection, "reminders", "message_cleanup")
        assert columns["account_id"] == ("uuid", "NO")
        assert columns["chat_id"] == ("bigint", "NO")
        assert columns["message_id"] == ("bigint", "NO")
        assert columns["expires_at"] == ("timestamp with time zone", "NO")

        indexes = {
            row[0]
            for row in connection.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'reminders'
                  AND tablename = 'message_cleanup'
                """
            ).fetchall()
        }
        assert {"pk_message_cleanup", "ix_message_cleanup_expiry"} <= indexes


def test_message_cleanup_grants_keep_the_bot_token_away_from_diary(
    database: str,
) -> None:
    expected = {
        "api_rw": {"INSERT"},
        "reminder_worker": {"SELECT", "DELETE"},
    }
    with psycopg.connect(database) as connection:
        for role, granted in expected.items():
            for privilege in TABLE_PRIVILEGES:
                assert _has_table_privilege(
                    connection,
                    role,
                    "reminders.message_cleanup",
                    privilege,
                ) == (privilege in granted)

        assert connection.execute(
            """
            SELECT has_schema_privilege(
                'reminder_worker',
                'diary',
                'USAGE'
            )
            """
        ).fetchone() == (False,)


def test_downgrade_restores_the_foundation_and_upgrade_replays() -> None:
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        config = _alembic_config(admin_url)

        command.upgrade(config, "head")
        command.downgrade(config, "0001")
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone() == ("0001",)
            consent_columns = _columns(connection, "diary", "consent")
            assert "text_sha256" not in consent_columns
            assert "text_locale" not in consent_columns
            assert "revoke_reason" not in consent_columns

            vault_record_columns = _columns(connection, "diary", "vault_record")
            assert "client_ts_ms" not in vault_record_columns
            assert vault_record_columns["client_ts"] == (
                "timestamp with time zone",
                "NO",
            )

            assert _columns(connection, "reminders", "message_cleanup") == {}

        command.upgrade(config, "head")
        command.downgrade(config, "base")
        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                """
                SELECT count(*)
                FROM pg_namespace
                WHERE nspname IN ('diary', 'reminders')
                """
            ).fetchone() == (0,)

        command.upgrade(config, "head")
