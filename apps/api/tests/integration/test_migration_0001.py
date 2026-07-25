from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.exc import DBAPIError
from testcontainers.postgres import PostgresContainer

API_ROOT = Path(__file__).resolve().parents[2]
DIARY_TABLES = {
    "account",
    "telegram_identity",
    "consent",
    "session_token",
    "auth_replay",
    "vault_key",
    "vault_revision",
    "vault_record",
    "erasure_job",
    "outbox",
}
REMINDER_TABLES = {"reminder_schedule", "reminder_delivery"}
TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)


@dataclass(frozen=True)
class Database:
    admin_url: str
    api_password: str = field(repr=False)
    worker_password: str = field(repr=False)


def _alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _connect(
    database: Database,
    *,
    role: str | None = None,
) -> psycopg.Connection:
    if role == "api_rw":
        return psycopg.connect(
            database.admin_url,
            user=role,
            password=database.api_password,
            autocommit=True,
        )
    if role == "reminder_worker":
        return psycopg.connect(
            database.admin_url,
            user=role,
            password=database.worker_password,
            autocommit=True,
        )
    return psycopg.connect(database.admin_url, autocommit=True)


@pytest.fixture(scope="module")
def database() -> Iterator[Database]:
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        config = _alembic_config(admin_url)

        # Pinned to the foundation revision on purpose: this module asserts the
        # state 0001 leaves behind, and later revisions have their own suites.
        command.upgrade(config, "0001")
        command.downgrade(config, "base")
        with psycopg.connect(admin_url) as connection:
            count = connection.execute(
                """
                SELECT count(*)
                FROM pg_namespace
                WHERE nspname IN ('diary', 'reminders')
                """
            ).fetchone()
            assert count == (0,)
        command.upgrade(config, "0001")

        api_password = secrets.token_urlsafe(24)
        worker_password = secrets.token_urlsafe(24)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("ALTER ROLE api_rw PASSWORD {}").format(
                    sql.Literal(api_password)
                )
            )
            connection.execute(
                sql.SQL("ALTER ROLE reminder_worker PASSWORD {}").format(
                    sql.Literal(worker_password)
                )
            )

        yield Database(
            admin_url=admin_url,
            api_password=api_password,
            worker_password=worker_password,
        )


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


def test_postgresql_16_and_foundation_objects(database: Database) -> None:
    with _connect(database) as connection:
        version = connection.execute(
            "SELECT current_setting('server_version_num')::integer"
        ).fetchone()
        assert version is not None
        assert version[0] // 10000 == 16

        revision = connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone()
        assert revision == ("0001",)

        tables = set(
            connection.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema IN ('diary', 'reminders')
                  AND table_type = 'BASE TABLE'
                """
            ).fetchall()
        )
        assert tables == {
            *(("diary", table) for table in DIARY_TABLES),
            *(("reminders", table) for table in REMINDER_TABLES),
        }

        indexes = {
            row[0]
            for row in connection.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname IN ('diary', 'reminders')
                """
            ).fetchall()
        }
        assert {
            "ux_consent_active",
            "ix_session_token_token_hash",
            "ux_vault_rev",
            "ix_reminder_schedule_due",
        } <= indexes

        constraints = {
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
        assert {
            "ck_account_status",
            "ck_consent_kind",
            "ck_reminder_schedule_disabled_reason",
            "ck_reminder_delivery_status",
        } <= constraints

        cross_schema_foreign_keys = connection.execute(
            """
            SELECT count(*)
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS source
              ON source.oid = constraint_row.conrelid
            JOIN pg_namespace AS source_schema
              ON source_schema.oid = source.relnamespace
            JOIN pg_class AS target
              ON target.oid = constraint_row.confrelid
            JOIN pg_namespace AS target_schema
              ON target_schema.oid = target.relnamespace
            WHERE constraint_row.contype = 'f'
              AND source_schema.nspname = 'reminders'
              AND target_schema.nspname = 'diary'
            """
        ).fetchone()
        assert cross_schema_foreign_keys == (0,)


def test_all_migration_role_memberships_fail_closed() -> None:
    protected_roles = ("migrator", "api_rw", "reminder_worker")
    with PostgresContainer("postgres:16", driver=None) as postgres:
        admin_url = postgres.get_connection_url()
        config = _alembic_config(admin_url)

        with psycopg.connect(admin_url, autocommit=True) as connection:
            for role in (*protected_roles, "membership_probe"):
                connection.execute(
                    sql.SQL("CREATE ROLE {}").format(sql.Identifier(role))
                )

            for protected_role in protected_roles:
                for granted_role, member_role in (
                    ("membership_probe", protected_role),
                    (protected_role, "membership_probe"),
                ):
                    connection.execute(
                        sql.SQL("GRANT {} TO {}").format(
                            sql.Identifier(granted_role),
                            sql.Identifier(member_role),
                        )
                    )
                    with pytest.raises(DBAPIError, match="must not participate"):
                        command.upgrade(config, "head")
                    connection.execute(
                        sql.SQL("REVOKE {} FROM {}").format(
                            sql.Identifier(granted_role),
                            sql.Identifier(member_role),
                        )
                    )

        command.upgrade(config, "head")


def test_roles_and_ownership_are_isolated(database: Database) -> None:
    with _connect(database) as connection:
        roles = {
            row[0]: row[1:]
            for row in connection.execute(
                """
                SELECT
                    rolname,
                    rolcanlogin,
                    rolinherit,
                    rolsuper,
                    rolcreatedb,
                    rolcreaterole,
                    rolreplication,
                    rolbypassrls
                FROM pg_roles
                WHERE rolname IN ('migrator', 'api_rw', 'reminder_worker')
                """
            ).fetchall()
        }
        assert roles == {
            "migrator": (False, False, False, False, False, False, False),
            "api_rw": (True, False, False, False, False, False, False),
            "reminder_worker": (
                True,
                False,
                False,
                False,
                False,
                False,
                False,
            ),
        }

        schema_owners = set(
            connection.execute(
                """
                SELECT nspname, pg_get_userbyid(nspowner)
                FROM pg_namespace
                WHERE nspname IN ('diary', 'reminders')
                """
            ).fetchall()
        )
        assert schema_owners == {
            ("diary", "migrator"),
            ("reminders", "migrator"),
        }

        relation_owners = {
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT pg_get_userbyid(class.relowner)
                FROM pg_class AS class
                JOIN pg_namespace AS namespace
                  ON namespace.oid = class.relnamespace
                WHERE namespace.nspname IN ('diary', 'reminders')
                  AND class.relkind IN ('r', 'S', 'i')
                """
            ).fetchall()
        }
        assert relation_owners == {"migrator"}

        memberships = connection.execute(
            """
            SELECT count(*)
            FROM pg_auth_members
            WHERE roleid IN (
                      SELECT oid
                      FROM pg_roles
                      WHERE rolname IN (
                          'migrator',
                          'api_rw',
                          'reminder_worker'
                      )
                  )
               OR member IN (
                      SELECT oid
                      FROM pg_roles
                      WHERE rolname IN (
                          'migrator',
                          'api_rw',
                          'reminder_worker'
                      )
                  )
            """
        ).fetchone()
        assert memberships == (0,)
        assert connection.execute(
            """
            SELECT has_database_privilege(
                'migrator',
                current_database(),
                'CREATE'
            )
            """
        ).fetchone() == (False,)

        for role in ("api_rw", "reminder_worker"):
            owns_database = connection.execute(
                """
                SELECT pg_get_userbyid(datdba) = %s
                FROM pg_database
                WHERE datname = current_database()
                """,
                (role,),
            ).fetchone()
            assert owns_database == (False,)

            for privilege in TABLE_PRIVILEGES:
                assert not _has_table_privilege(
                    connection,
                    role,
                    "public.alembic_version",
                    privilege,
                )


def test_exact_grant_matrix_and_real_role_connections(
    database: Database,
) -> None:
    with _connect(database) as admin:
        assert admin.execute(
            "SELECT has_schema_privilege('api_rw', 'diary', 'USAGE')"
        ).fetchone() == (True,)
        assert admin.execute(
            """
            SELECT has_schema_privilege(
                'reminder_worker',
                'diary',
                'USAGE'
            )
            """
        ).fetchone() == (False,)
        for role in ("api_rw", "reminder_worker"):
            assert admin.execute(
                "SELECT has_schema_privilege(%s, 'diary', 'CREATE')",
                (role,),
            ).fetchone() == (False,)
            assert admin.execute(
                "SELECT has_schema_privilege(%s, 'reminders', 'CREATE')",
                (role,),
            ).fetchone() == (False,)

        for table in DIARY_TABLES:
            relation = f"diary.{table}"
            for privilege in TABLE_PRIVILEGES:
                assert _has_table_privilege(
                    admin,
                    "api_rw",
                    relation,
                    privilege,
                ) == (privilege in {"SELECT", "INSERT", "UPDATE", "DELETE"})
                assert not _has_table_privilege(
                    admin,
                    "reminder_worker",
                    relation,
                    privilege,
                )

        expected_reminder_privileges = {
            ("api_rw", "reminder_schedule"): {
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
            },
            ("api_rw", "reminder_delivery"): {"SELECT", "DELETE"},
            ("reminder_worker", "reminder_schedule"): {
                "SELECT",
                "UPDATE",
            },
            ("reminder_worker", "reminder_delivery"): {
                "SELECT",
                "INSERT",
                "UPDATE",
            },
        }
        for (role, table), expected in expected_reminder_privileges.items():
            for privilege in TABLE_PRIVILEGES:
                assert _has_table_privilege(
                    admin,
                    role,
                    f"reminders.{table}",
                    privilege,
                ) == (privilege in expected)

    account_id = uuid4()
    now = datetime.now(UTC)
    with _connect(database, role="api_rw") as api:
        api.execute(
            """
            INSERT INTO diary.account (id, created_at, status)
            VALUES (%s, %s, 'active')
            """,
            (account_id, now),
        )
        api.execute(
            """
            INSERT INTO reminders.reminder_schedule (
                account_id,
                telegram_chat_id,
                tz,
                local_time,
                enabled,
                next_fire_at,
                updated_at
            )
            VALUES (%s, 1001, 'Europe/Kyiv', %s, true, %s, %s)
            """,
            (account_id, time(9, 0), now, now),
        )

    with _connect(database, role="reminder_worker") as worker:
        assert worker.execute(
            """
            SELECT enabled
            FROM reminders.reminder_schedule
            WHERE account_id = %s
            """,
            (account_id,),
        ).fetchone() == (True,)
        worker.execute(
            """
            UPDATE reminders.reminder_schedule
            SET next_fire_at = %s, updated_at = %s
            WHERE account_id = %s
            """,
            (now, now, account_id),
        )
        worker.execute(
            """
            INSERT INTO reminders.reminder_delivery (
                account_id,
                local_date,
                status,
                created_at
            )
            VALUES (%s, %s, 'pending', %s)
            """,
            (account_id, date(2026, 7, 24), now),
        )
        worker.execute(
            """
            UPDATE reminders.reminder_delivery
            SET status = 'sent', telegram_message_id = 123
            WHERE account_id = %s AND local_date = %s
            """,
            (account_id, date(2026, 7, 24)),
        )

        denied_statements = (
            "SELECT 1 FROM diary.account LIMIT 1",
            "INSERT INTO reminders.reminder_schedule DEFAULT VALUES",
            "DELETE FROM reminders.reminder_delivery",
            "CREATE TABLE reminders.forbidden (id integer)",
            "SET ROLE migrator",
        )
        for statement in denied_statements:
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
                worker.execute(statement)
            assert error.value.sqlstate == "42501"

    with _connect(database, role="api_rw") as api:
        assert api.execute(
            """
            SELECT status
            FROM reminders.reminder_delivery
            WHERE account_id = %s AND local_date = %s
            """,
            (account_id, date(2026, 7, 24)),
        ).fetchone() == ("sent",)

        for statement in (
            "INSERT INTO reminders.reminder_delivery DEFAULT VALUES",
            "UPDATE reminders.reminder_delivery SET status = 'failed'",
            "SELECT version_num FROM public.alembic_version",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
                api.execute(statement)
            assert error.value.sqlstate == "42501"

        api.execute(
            "DELETE FROM reminders.reminder_delivery WHERE account_id = %s",
            (account_id,),
        )
        api.execute(
            "DELETE FROM reminders.reminder_schedule WHERE account_id = %s",
            (account_id,),
        )
        api.execute(
            "DELETE FROM diary.account WHERE id = %s",
            (account_id,),
        )


def test_default_privileges_fail_closed(database: Database) -> None:
    with _connect(database) as admin:
        try:
            admin.execute("SET ROLE migrator")
            admin.execute("CREATE TABLE diary._acl_probe (id integer)")
            admin.execute("CREATE SEQUENCE diary._acl_probe_seq")
            admin.execute("CREATE TABLE reminders._acl_probe (id integer)")
            admin.execute(
                """
                CREATE FUNCTION diary._acl_probe_fn()
                RETURNS integer
                LANGUAGE sql
                IMMUTABLE
                AS 'SELECT 1'
                """
            )
            admin.execute("RESET ROLE")

            for privilege in TABLE_PRIVILEGES:
                assert _has_table_privilege(
                    admin,
                    "api_rw",
                    "diary._acl_probe",
                    privilege,
                ) == (privilege in {"SELECT", "INSERT", "UPDATE", "DELETE"})
                assert not _has_table_privilege(
                    admin,
                    "reminder_worker",
                    "diary._acl_probe",
                    privilege,
                )
                for role in ("api_rw", "reminder_worker"):
                    assert not _has_table_privilege(
                        admin,
                        role,
                        "reminders._acl_probe",
                        privilege,
                    )

            assert admin.execute(
                """
                SELECT has_sequence_privilege(
                    'api_rw',
                    'diary._acl_probe_seq',
                    'USAGE'
                )
                """
            ).fetchone() == (True,)
            assert admin.execute(
                """
                SELECT has_sequence_privilege(
                    'api_rw',
                    'diary._acl_probe_seq',
                    'UPDATE'
                )
                """
            ).fetchone() == (False,)
            assert admin.execute(
                """
                SELECT has_sequence_privilege(
                    'reminder_worker',
                    'diary._acl_probe_seq',
                    'USAGE'
                )
                """
            ).fetchone() == (False,)

            for role in ("api_rw", "reminder_worker"):
                assert admin.execute(
                    """
                    SELECT has_function_privilege(
                        %s,
                        'diary._acl_probe_fn()',
                        'EXECUTE'
                    )
                    """,
                    (role,),
                ).fetchone() == (False,)
        finally:
            admin.execute("RESET ROLE")
            admin.execute("SET ROLE migrator")
            admin.execute("DROP FUNCTION IF EXISTS diary._acl_probe_fn()")
            admin.execute("DROP TABLE IF EXISTS reminders._acl_probe")
            admin.execute("DROP SEQUENCE IF EXISTS diary._acl_probe_seq")
            admin.execute("DROP TABLE IF EXISTS diary._acl_probe")
            admin.execute("RESET ROLE")
