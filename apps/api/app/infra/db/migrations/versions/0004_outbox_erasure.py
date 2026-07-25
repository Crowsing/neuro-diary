"""Give `diary.outbox` a working queue shape and enforce §11 in the database.

The table has existed since 0001 and nobody wrote to it. Phase 3 gives it its
first producer and its first consumer, and that turns three properties from
intentions into requirements:

* **the queue has to be claimable** without scanning processed rows, and the
  TTL sweep has to find settled rows without scanning the queue;
* **erasure has to be able to find an account's rows**, whose identifier lives
  inside `payload` rather than in a column (§6.4 names this table as one of the
  two that need special attention);
* **the payload must never name a consent.** §11 forbids carrying the kind of a
  revoked consent out of the database, and the set of active consents is itself
  a health inference (§13.12). A CHECK makes that mechanical: a payload that
  names a kind cannot be inserted at all, whatever a future service decides.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _upgrade_outbox() -> None:
    # The claim path reads only unprocessed rows, and there are never many of
    # them; a partial index keeps it that size regardless of how long settled
    # rows wait for their TTL.
    op.execute(
        """
        CREATE INDEX ix_outbox_unprocessed
            ON diary.outbox (id)
            WHERE processed_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ix_outbox_processed_at
            ON diary.outbox (processed_at)
            WHERE processed_at IS NOT NULL
        """
    )
    # Erasure deletes by the identifier inside the payload (§6.4). Without this
    # expression index the matrix entry for `outbox` is a sequential scan of the
    # whole queue on every erasure.
    op.execute(
        """
        CREATE INDEX ix_outbox_account
            ON diary.outbox ((payload ->> 'account_id'))
        """
    )
    # An event whose account cannot be named is an event erasure cannot remove,
    # and §6.4 promises zero rows for an erased account in every one of the 12
    # tables.
    #
    # `COALESCE` is load-bearing: `jsonb_typeof` of an absent key is NULL, and a
    # CHECK that evaluates to NULL passes. Without it the constraint would admit
    # exactly the rows it exists to reject.
    op.execute(
        """
        ALTER TABLE diary.outbox
            ADD CONSTRAINT ck_outbox_payload_has_account
            CHECK (COALESCE(jsonb_typeof(payload -> 'account_id'), 'absent') = 'string')
        """
    )
    # §11 and §13.12, as a constraint rather than as a convention. The three
    # literals are the full `kind` domain of `diary.consent`; the check is on
    # the serialized payload, so it holds for keys and values alike and for
    # every producer that will ever exist.
    op.execute(
        """
        ALTER TABLE diary.outbox
            ADD CONSTRAINT ck_outbox_payload_names_no_consent
            CHECK (
                payload::text NOT LIKE '%health_sync%'
                AND payload::text NOT LIKE '%telegram_reminders%'
                AND payload::text NOT LIKE '%cycle_sync%'
            )
        """
    )


def upgrade() -> None:
    op.execute("SET LOCAL ROLE migrator")
    _upgrade_outbox()
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE migrator")
    op.execute(
        """
        ALTER TABLE diary.outbox
            DROP CONSTRAINT ck_outbox_payload_names_no_consent
        """
    )
    op.execute(
        """
        ALTER TABLE diary.outbox
            DROP CONSTRAINT ck_outbox_payload_has_account
        """
    )
    op.execute("DROP INDEX diary.ix_outbox_account")
    op.execute("DROP INDEX diary.ix_outbox_processed_at")
    op.execute("DROP INDEX diary.ix_outbox_unprocessed")
    op.execute("RESET ROLE")
