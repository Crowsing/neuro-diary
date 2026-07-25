"""Add the identity, consent, and erasure delta required by phase 1.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _upgrade_consent() -> None:
    op.add_column(
        "consent",
        sa.Column("text_sha256", sa.LargeBinary(), nullable=False),
        schema="diary",
    )
    op.add_column(
        "consent",
        sa.Column(
            "text_locale",
            sa.Text(),
            server_default=sa.text("'uk'"),
            nullable=False,
        ),
        schema="diary",
    )
    op.add_column(
        "consent",
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        schema="diary",
    )
    op.create_check_constraint(
        "ck_consent_text_sha256_size",
        "consent",
        "octet_length(text_sha256) = 32",
        schema="diary",
    )
    op.create_check_constraint(
        "ck_consent_revoke_reason",
        "consent",
        """
        revoke_reason IS NULL
        OR revoke_reason IN (
            'user',
            'bot_blocked_timeout',
            'stale_text_timeout'
        )
        """,
        schema="diary",
    )
    # A reason without a revocation would let the erasure schedule of §4.3 read
    # a deadline off a consent that is still active.
    op.create_check_constraint(
        "ck_consent_revoke_reason_requires_revocation",
        "consent",
        "revoke_reason IS NULL OR revoked_at IS NOT NULL",
        schema="diary",
    )


def _upgrade_erasure_job() -> None:
    op.add_column(
        "erasure_job",
        sa.Column("deletion_copy_version", sa.Text(), nullable=False),
        schema="diary",
    )
    op.create_check_constraint(
        "ck_erasure_job_deletion_copy_version_nonempty",
        "erasure_job",
        "btrim(deletion_copy_version) <> ''",
        schema="diary",
    )
    op.create_check_constraint(
        "ck_erasure_job_scope",
        "erasure_job",
        """
        scope IN (
            'full',
            'sync_off',
            'reminders_off',
            'security_reset'
        )
        """,
        schema="diary",
    )


def _upgrade_vault_key() -> None:
    op.add_column(
        "vault_key",
        sa.Column(
            "wrap_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        schema="diary",
    )
    op.add_column(
        "vault_key",
        sa.Column("wrapped_dek_prev", sa.LargeBinary(), nullable=True),
        schema="diary",
    )
    op.add_column(
        "vault_key",
        sa.Column("wrap_version_prev", sa.Integer(), nullable=True),
        schema="diary",
    )
    op.add_column(
        "vault_key",
        sa.Column(
            "prev_written_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="diary",
    )
    op.create_check_constraint(
        "ck_vault_key_wrap_version_positive",
        "vault_key",
        "wrap_version > 0",
        schema="diary",
    )
    op.create_check_constraint(
        "ck_vault_key_previous_envelope_complete",
        "vault_key",
        """
        (
            wrapped_dek_prev IS NULL
            AND wrap_version_prev IS NULL
            AND prev_written_at IS NULL
        )
        OR
        (
            wrapped_dek_prev IS NOT NULL
            AND wrap_version_prev IS NOT NULL
            AND prev_written_at IS NOT NULL
            AND wrap_version_prev < wrap_version
        )
        """,
        schema="diary",
    )


def _upgrade_vault_record() -> None:
    # §6.2: the AAD binds to a decimal integer of UTC milliseconds, so a
    # timestamptz round trip would stop the envelope from opening.
    op.drop_column("vault_record", "client_ts", schema="diary")
    op.add_column(
        "vault_record",
        sa.Column("client_ts_ms", sa.BigInteger(), nullable=False),
        schema="diary",
    )
    op.create_check_constraint(
        "ck_vault_record_client_ts_ms_bounds",
        "vault_record",
        "client_ts_ms > 0",
        schema="diary",
    )


def _upgrade_message_cleanup() -> None:
    op.create_table(
        "message_cleanup",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "chat_id > 0 AND message_id > 0",
            name="ck_message_cleanup_ids_positive",
        ),
        sa.PrimaryKeyConstraint(
            "account_id",
            "message_id",
            name="pk_message_cleanup",
        ),
        schema="reminders",
    )
    op.create_index(
        "ix_message_cleanup_expiry",
        "message_cleanup",
        ["expires_at"],
        unique=False,
        schema="reminders",
    )
    # §5.3: the worker holds BOT_TOKEN, so it may drain the queue but never
    # fill it; api_rw fills it but never reads it back.
    op.execute(
        """
        REVOKE ALL ON reminders.message_cleanup
            FROM PUBLIC, api_rw, reminder_worker;
        GRANT INSERT ON reminders.message_cleanup TO api_rw;
        GRANT SELECT, DELETE ON reminders.message_cleanup TO reminder_worker;
        """
    )


def upgrade() -> None:
    op.execute("SET LOCAL ROLE migrator")
    _upgrade_consent()
    _upgrade_erasure_job()
    _upgrade_vault_key()
    _upgrade_vault_record()
    _upgrade_message_cleanup()
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE migrator")

    op.drop_index(
        "ix_message_cleanup_expiry",
        table_name="message_cleanup",
        schema="reminders",
    )
    op.drop_table("message_cleanup", schema="reminders")

    op.drop_constraint(
        "ck_vault_record_client_ts_ms_bounds",
        "vault_record",
        type_="check",
        schema="diary",
    )
    op.drop_column("vault_record", "client_ts_ms", schema="diary")
    op.add_column(
        "vault_record",
        sa.Column("client_ts", sa.DateTime(timezone=True), nullable=False),
        schema="diary",
    )

    op.drop_constraint(
        "ck_vault_key_previous_envelope_complete",
        "vault_key",
        type_="check",
        schema="diary",
    )
    op.drop_constraint(
        "ck_vault_key_wrap_version_positive",
        "vault_key",
        type_="check",
        schema="diary",
    )
    for column in (
        "prev_written_at",
        "wrap_version_prev",
        "wrapped_dek_prev",
        "wrap_version",
    ):
        op.drop_column("vault_key", column, schema="diary")

    op.drop_constraint(
        "ck_erasure_job_scope",
        "erasure_job",
        type_="check",
        schema="diary",
    )
    op.drop_constraint(
        "ck_erasure_job_deletion_copy_version_nonempty",
        "erasure_job",
        type_="check",
        schema="diary",
    )
    op.drop_column("erasure_job", "deletion_copy_version", schema="diary")

    op.drop_constraint(
        "ck_consent_revoke_reason_requires_revocation",
        "consent",
        type_="check",
        schema="diary",
    )
    op.drop_constraint(
        "ck_consent_revoke_reason",
        "consent",
        type_="check",
        schema="diary",
    )
    op.drop_constraint(
        "ck_consent_text_sha256_size",
        "consent",
        type_="check",
        schema="diary",
    )
    for column in ("revoke_reason", "text_locale", "text_sha256"):
        op.drop_column("consent", column, schema="diary")

    op.execute("RESET ROLE")
