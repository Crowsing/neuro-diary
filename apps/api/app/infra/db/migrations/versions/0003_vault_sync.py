"""Add the vault-sync delta required by phase 2.

Three changes, each with a named consumer in this phase:

* `consent.record_key_cycle` — the client names `HMAC(k_index,'cycle')` when it
  grants `cycle_sync` (§9.7). Phase 2 stores it and gates pushes on it; the
  server-side hard DELETE at revocation belongs to phase 3.
* `vault_revision.consent_epoch` — makes the neutral `consent_state_changed`
  flag of a pull a function of a revision rather than of a clock, so it
  survives a 410 resync. §6.2 does not list this column; the deviation is
  recorded in the plan by its own commit.
* `diary.rate_window` — the per-account windows of §11 in PostgreSQL, without
  Redis. The primary key is `(account_id, bucket)`, so an account can never
  hold more than three rows and no TTL job is needed.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _upgrade_consent() -> None:
    op.add_column(
        "consent",
        sa.Column("record_key_cycle", sa.LargeBinary(), nullable=True),
        schema="diary",
    )
    op.create_check_constraint(
        "ck_consent_record_key_cycle_size",
        "consent",
        "record_key_cycle IS NULL OR octet_length(record_key_cycle) = 32",
        schema="diary",
    )
    # A named key on any other consent would be a record the server can point
    # at without the client ever having named it — exactly the one bit §13.5
    # bounds the residual risk to.
    op.create_check_constraint(
        "ck_consent_record_key_cycle_only_on_cycle_sync",
        "consent",
        "record_key_cycle IS NULL OR kind = 'cycle_sync'",
        schema="diary",
    )


def _upgrade_vault_revision() -> None:
    op.add_column(
        "vault_revision",
        sa.Column(
            "consent_epoch",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        schema="diary",
    )
    op.create_check_constraint(
        "ck_vault_revision_consent_epoch_nonnegative",
        "vault_revision",
        "consent_epoch >= 0",
        schema="diary",
    )


def _upgrade_rate_window() -> None:
    op.create_table(
        "rate_window",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "used",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "bucket IN ('sync', 'push_bytes', 'key_read')",
            name="ck_rate_window_bucket",
        ),
        sa.CheckConstraint("used >= 0", name="ck_rate_window_used_nonnegative"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["diary.account.id"],
            name="fk_rate_window_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", "bucket", name="pk_rate_window"),
        schema="diary",
    )
    # `ALTER DEFAULT PRIVILEGES` of 0001 already grants DML to api_rw for tables
    # migrator creates in `diary`. The explicit statement documents the intent
    # and keeps the grant correct if the creating role ever changes.
    op.execute(
        """
        REVOKE ALL ON diary.rate_window FROM PUBLIC, reminder_worker;
        GRANT SELECT, INSERT, UPDATE, DELETE ON diary.rate_window TO api_rw;
        """
    )


def upgrade() -> None:
    op.execute("SET LOCAL ROLE migrator")
    _upgrade_consent()
    _upgrade_vault_revision()
    _upgrade_rate_window()
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE migrator")

    op.drop_table("rate_window", schema="diary")

    op.drop_constraint(
        "ck_vault_revision_consent_epoch_nonnegative",
        "vault_revision",
        type_="check",
        schema="diary",
    )
    op.drop_column("vault_revision", "consent_epoch", schema="diary")

    op.drop_constraint(
        "ck_consent_record_key_cycle_only_on_cycle_sync",
        "consent",
        type_="check",
        schema="diary",
    )
    op.drop_constraint(
        "ck_consent_record_key_cycle_size",
        "consent",
        type_="check",
        schema="diary",
    )
    op.drop_column("consent", "record_key_cycle", schema="diary")

    op.execute("RESET ROLE")
