"""Give the reminder schedule a clock of its own and the settings a budget.

Phase 4 turns `reminders.reminder_schedule` from a row nobody reads into the
state machine of §10, and two of its promises have nowhere to live yet:

* **«403 відкликає згоду лише після 14 діб поспіль»** needs a moment to count
  from. `updated_at` is not it: every edit of the schedule moves that column, so
  a user who adjusts her time while the bot is blocked would push the deadline
  away for ever without meaning to — and «поспіль» would silently become «14
  days during which nothing else happened». `disabled_at` is set exactly when
  the bot block is recorded and cleared exactly when it ends, which is what
  makes the streak a streak;
* **`reminders-settings 20/хв` of §11** is a per-account window, and the bucket
  domain of `diary.rate_window` is a CHECK. A new bucket is a migration, not a
  string a service may invent.

`ck_reminder_schedule_disabled_at_matches_reason` is the pairing rule: the
timestamp exists if and only if the reason does. Without it the two columns
could disagree, and the reconciler would be reading a deadline that belongs to
no block.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _upgrade_schedule() -> None:
    op.execute(
        """
        ALTER TABLE reminders.reminder_schedule
            ADD COLUMN disabled_at timestamptz
        """
    )
    # The reason and its clock are one fact in two columns, so they are
    # constrained as one fact. `disabled_reason` already admits only
    # `'bot_blocked'`, which makes this the pause/block discriminator too: a
    # plain pause (`enabled = false`, no reason) carries no deadline, and that
    # is precisely why a pause can never reach the reconciler.
    op.execute(
        """
        ALTER TABLE reminders.reminder_schedule
            ADD CONSTRAINT ck_reminder_schedule_disabled_at_matches_reason
            CHECK ((disabled_reason IS NULL) = (disabled_at IS NULL))
        """
    )
    # The 403 reconciler scans for blocks older than the streak, and it runs on
    # every dispatcher pass. Without this partial index that scan reads every
    # schedule in the table to find the handful that are blocked.
    op.execute(
        """
        CREATE INDEX ix_reminder_schedule_blocked
            ON reminders.reminder_schedule (disabled_at)
            WHERE disabled_reason IS NOT NULL
        """
    )


def _upgrade_rate_window() -> None:
    # §11 lists `reminders-settings 20/хв` beside the sync budgets, so it is a
    # per-account window in PostgreSQL like the other three — not the in-memory
    # per-IP limiter, which §11 reserves for auth and keeps address-shaped.
    op.execute(
        """
        ALTER TABLE diary.rate_window
            DROP CONSTRAINT ck_rate_window_bucket
        """
    )
    op.execute(
        """
        ALTER TABLE diary.rate_window
            ADD CONSTRAINT ck_rate_window_bucket
            CHECK (
                bucket IN ('sync', 'push_bytes', 'key_read', 'reminders_settings')
            )
        """
    )


def upgrade() -> None:
    op.execute("SET LOCAL ROLE migrator")
    _upgrade_schedule()
    _upgrade_rate_window()
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE migrator")
    # Rows in the new bucket would violate the narrowed CHECK, so they go first.
    # A downgrade that fails halfway is worse than one that drops a counter: the
    # window is a rate limit, and losing it costs one minute of budget.
    op.execute("DELETE FROM diary.rate_window WHERE bucket = 'reminders_settings'")
    op.execute(
        """
        ALTER TABLE diary.rate_window
            DROP CONSTRAINT ck_rate_window_bucket
        """
    )
    op.execute(
        """
        ALTER TABLE diary.rate_window
            ADD CONSTRAINT ck_rate_window_bucket
            CHECK (bucket IN ('sync', 'push_bytes', 'key_read'))
        """
    )
    op.execute("DROP INDEX reminders.ix_reminder_schedule_blocked")
    op.execute(
        """
        ALTER TABLE reminders.reminder_schedule
            DROP CONSTRAINT ck_reminder_schedule_disabled_at_matches_reason
        """
    )
    op.execute(
        """
        ALTER TABLE reminders.reminder_schedule
            DROP COLUMN disabled_at
        """
    )
    op.execute("RESET ROLE")
