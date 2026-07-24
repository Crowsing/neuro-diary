"""Create PostgreSQL schemas, tables, roles, and isolated grants.

Revision ID: 0001
Revises:
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_roles() -> None:
    op.execute(
        """
        DO $roles$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'migrator'
            ) THEN
                CREATE ROLE migrator
                    NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOREPLICATION NOBYPASSRLS;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'api_rw'
            ) THEN
                CREATE ROLE api_rw
                    LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOREPLICATION NOBYPASSRLS;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'reminder_worker'
            ) THEN
                CREATE ROLE reminder_worker
                    LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOREPLICATION NOBYPASSRLS;
            END IF;
        END
        $roles$;

        ALTER ROLE migrator
            NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
        ALTER ROLE api_rw
            LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
        ALTER ROLE reminder_worker
            LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;

        DO $role_isolation$
        BEGIN
            IF EXISTS (
                SELECT 1
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
            ) THEN
                RAISE EXCEPTION
                    'migration roles must not participate in role memberships';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_database
                WHERE datname = current_database()
                  AND datdba IN (
                      SELECT oid
                      FROM pg_roles
                      WHERE rolname IN ('api_rw', 'reminder_worker')
                  )
            ) THEN
                RAISE EXCEPTION
                    'application roles must not own the database';
            END IF;
        END
        $role_isolation$;
        """
    )


def _grant_schema_creation() -> None:
    op.execute(
        """
        DO $database_grant$
        BEGIN
            EXECUTE format(
                'GRANT CREATE ON DATABASE %I TO migrator',
                current_database()
            );
        END
        $database_grant$;
        """
    )


def _revoke_schema_creation() -> None:
    op.execute(
        """
        DO $database_revoke$
        BEGIN
            EXECUTE format(
                'REVOKE CREATE ON DATABASE %I FROM migrator',
                current_database()
            );
        END
        $database_revoke$;
        """
    )


def _create_diary_tables() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'erasing', 'erased')",
            name="ck_account_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account"),
        schema="diary",
    )

    op.create_table(
        "telegram_identity",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "last_auth_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "telegram_user_id > 0",
            name="ck_telegram_identity_user_id_positive",
        ),
        sa.CheckConstraint(
            "last_auth_at >= first_seen_at",
            name="ck_telegram_identity_auth_order",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["diary.account.id"],
            name="fk_telegram_identity_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "telegram_user_id",
            name="pk_telegram_identity",
        ),
        sa.UniqueConstraint(
            "account_id",
            name="uq_telegram_identity_account_id",
        ),
        schema="diary",
    )

    op.create_table(
        "consent",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("text_version", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('health_sync', 'telegram_reminders', 'cycle_sync')",
            name="ck_consent_kind",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name="ck_consent_revocation_order",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["diary.account.id"],
            name="fk_consent_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_consent"),
        schema="diary",
    )
    op.create_index(
        "ux_consent_active",
        "consent",
        ["account_id", "kind"],
        unique=True,
        schema="diary",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "session_token",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32",
            name="ck_session_token_hash_size",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_session_token_expiry_order",
        ),
        sa.CheckConstraint(
            "last_used_at >= created_at",
            name="ck_session_token_last_used_order",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_session_token_revocation_order",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["diary.account.id"],
            name="fk_session_token_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session_token"),
        schema="diary",
    )
    op.create_index(
        "ix_session_token_token_hash",
        "session_token",
        ["token_hash"],
        unique=False,
        schema="diary",
    )

    op.create_table(
        "auth_replay",
        sa.Column("initdata_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(initdata_hash) = 32",
            name="ck_auth_replay_hash_size",
        ),
        sa.PrimaryKeyConstraint("initdata_hash", name="pk_auth_replay"),
        schema="diary",
    )

    op.create_table(
        "vault_key",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("kdf", sa.Text(), nullable=False),
        sa.Column(
            "kdf_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "key_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(wrapped_dek) > 0",
            name="ck_vault_key_wrapped_dek_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(kdf) <> ''",
            name="ck_vault_key_kdf_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(kdf_params) = 'object'",
            name="ck_vault_key_params_object",
        ),
        sa.CheckConstraint(
            "key_version > 0",
            name="ck_vault_key_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["diary.account.id"],
            name="fk_vault_key_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", name="pk_vault_key"),
        schema="diary",
    )

    op.create_table(
        "vault_revision",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column(
            "current_revision",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "compacted_up_to",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "reset_revision",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "current_revision >= 0",
            name="ck_vault_revision_current_nonnegative",
        ),
        sa.CheckConstraint(
            "compacted_up_to >= 0 AND compacted_up_to <= current_revision",
            name="ck_vault_revision_compaction_range",
        ),
        sa.CheckConstraint(
            "reset_revision >= 0 AND reset_revision <= current_revision",
            name="ck_vault_revision_reset_range",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["diary.account.id"],
            name="fk_vault_revision_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", name="pk_vault_revision"),
        schema="diary",
    )

    op.create_table(
        "vault_record",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("record_key", sa.LargeBinary(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=True),
        sa.Column("payload_size", sa.Integer(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "deleted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("client_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(record_key) = 32",
            name="ck_vault_record_key_size",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_vault_record_revision_positive",
        ),
        sa.CheckConstraint(
            "payload_size BETWEEN 0 AND 65536",
            name="ck_vault_record_payload_limit",
        ),
        sa.CheckConstraint(
            """
            (
                deleted
                AND payload IS NULL
                AND payload_size = 0
            )
            OR
            (
                NOT deleted
                AND payload IS NOT NULL
                AND payload_size = octet_length(payload)
                AND payload_size > 0
            )
            """,
            name="ck_vault_record_tombstone_payload",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["diary.account.id"],
            name="fk_vault_record_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "account_id",
            "record_key",
            name="pk_vault_record",
        ),
        schema="diary",
    )
    op.create_index(
        "ux_vault_rev",
        "vault_record",
        ["account_id", "revision"],
        unique=True,
        schema="diary",
    )

    op.create_table(
        "erasure_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= requested_at",
            name="ck_erasure_job_completion_order",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_erasure_job"),
        schema="diary",
    )

    op.create_table(
        "outbox",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "btrim(event_type) <> ''",
            name="ck_outbox_event_type_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_outbox_payload_object",
        ),
        sa.CheckConstraint(
            "processed_at IS NULL OR processed_at >= created_at",
            name="ck_outbox_processing_order",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox"),
        schema="diary",
    )


def _create_reminder_tables() -> None:
    op.create_table(
        "reminder_schedule",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=False),
        sa.Column("local_time", sa.Time(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "telegram_chat_id > 0",
            name="ck_reminder_schedule_private_chat",
        ),
        sa.CheckConstraint(
            "btrim(tz) <> ''",
            name="ck_reminder_schedule_timezone_nonempty",
        ),
        sa.CheckConstraint(
            "disabled_reason IS NULL OR disabled_reason = 'bot_blocked'",
            name="ck_reminder_schedule_disabled_reason",
        ),
        sa.CheckConstraint(
            "disabled_reason IS NULL OR NOT enabled",
            name="ck_reminder_schedule_disabled_state",
        ),
        sa.PrimaryKeyConstraint(
            "account_id",
            name="pk_reminder_schedule",
        ),
        schema="reminders",
    )
    op.create_index(
        "ix_reminder_schedule_due",
        "reminder_schedule",
        ["enabled", "next_fire_at"],
        unique=False,
        schema="reminders",
    )

    op.create_table(
        "reminder_delivery",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            """
            status IN (
                'pending',
                'sent',
                'failed',
                'skipped_stale',
                'skipped_quiet'
            )
            """,
            name="ck_reminder_delivery_status",
        ),
        sa.PrimaryKeyConstraint(
            "account_id",
            "local_date",
            name="pk_reminder_delivery",
        ),
        schema="reminders",
    )


def _apply_grants() -> None:
    op.execute(
        """
        REVOKE ALL ON SCHEMA diary, reminders
            FROM PUBLIC, api_rw, reminder_worker;
        GRANT USAGE ON SCHEMA diary TO api_rw;
        GRANT USAGE ON SCHEMA reminders TO api_rw, reminder_worker;

        REVOKE ALL ON ALL TABLES IN SCHEMA diary
            FROM PUBLIC, api_rw, reminder_worker;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA diary
            FROM PUBLIC, api_rw, reminder_worker;
        REVOKE ALL ON ALL ROUTINES IN SCHEMA diary
            FROM PUBLIC, api_rw, reminder_worker;

        GRANT SELECT, INSERT, UPDATE, DELETE
            ON ALL TABLES IN SCHEMA diary TO api_rw;
        GRANT USAGE
            ON ALL SEQUENCES IN SCHEMA diary TO api_rw;

        REVOKE ALL ON ALL TABLES IN SCHEMA reminders
            FROM PUBLIC, api_rw, reminder_worker;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA reminders
            FROM PUBLIC, api_rw, reminder_worker;
        REVOKE ALL ON ALL ROUTINES IN SCHEMA reminders
            FROM PUBLIC, api_rw, reminder_worker;

        GRANT SELECT, INSERT, UPDATE, DELETE
            ON reminders.reminder_schedule TO api_rw;
        GRANT SELECT, DELETE
            ON reminders.reminder_delivery TO api_rw;
        GRANT SELECT, UPDATE
            ON reminders.reminder_schedule TO reminder_worker;
        GRANT SELECT, INSERT, UPDATE
            ON reminders.reminder_delivery TO reminder_worker;

        ALTER DEFAULT PRIVILEGES FOR ROLE migrator
            REVOKE EXECUTE ON ROUTINES FROM PUBLIC;

        ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA diary
            REVOKE ALL ON TABLES FROM api_rw, reminder_worker;
        ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA diary
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO api_rw;
        ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA diary
            REVOKE ALL ON SEQUENCES FROM api_rw, reminder_worker;
        ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA diary
            GRANT USAGE ON SEQUENCES TO api_rw;

        ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA reminders
            REVOKE ALL ON TABLES FROM api_rw, reminder_worker;
        ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA reminders
            REVOKE ALL ON SEQUENCES FROM api_rw, reminder_worker;
        """
    )


def _set_runtime_search_paths() -> None:
    op.execute(
        """
        DO $search_paths$
        BEGIN
            EXECUTE format(
                'ALTER ROLE api_rw IN DATABASE %I '
                'SET search_path = pg_catalog, diary, reminders',
                current_database()
            );
            EXECUTE format(
                'ALTER ROLE reminder_worker IN DATABASE %I '
                'SET search_path = pg_catalog, reminders',
                current_database()
            );
        END
        $search_paths$;
        """
    )


def upgrade() -> None:
    _create_roles()
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    _grant_schema_creation()
    op.execute("SET LOCAL ROLE migrator")

    op.execute("CREATE SCHEMA diary AUTHORIZATION migrator")
    op.execute("CREATE SCHEMA reminders AUTHORIZATION migrator")
    _create_diary_tables()
    _create_reminder_tables()
    _apply_grants()

    op.execute("RESET ROLE")
    _revoke_schema_creation()
    _set_runtime_search_paths()


def downgrade() -> None:
    op.execute("SET LOCAL ROLE migrator")
    op.execute("DROP SCHEMA reminders CASCADE")
    op.execute("DROP SCHEMA diary CASCADE")
    op.execute("RESET ROLE")
