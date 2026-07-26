"""Give the endpoints §11 does not name a window of their own.

§11 lists four application budgets by name — sync, push volume, key reads and
reminder settings. Seven endpoints had none: both session routes, all three
consent routes and both account routes. Phase 5 reads that list as a **floor**
rather than a ceiling, and the reasoning is not symmetric across the seven, so
neither is the outcome:

* `POST /v1/consents` writes consent rows, reads the copy registry off disk and
  may provision a schedule. It is the cheapest way to make this server work
  under a Bearer token, and it gets the window;
* `GET /v1/sessions`, `POST /v1/sessions/revoke-others` and
  `POST /v1/account/vault-reset` write or read rows under the same token and get
  it too — the last one through the `sync` bucket, since it is a vault write;
* `POST /v1/consents/revoke` gets **no** window: Art. 7(3) requires withdrawing
  to be as easy as granting, and «as easy» cannot mean «behind a budget the
  grant already spent»;
* `GET /v1/consents` gets none either: the post-410 rule of §9.4 forbids pruning
  without a fresh consent answer, so an exhausted budget must never block the
  answer that unblocks pruning;
* `POST /v1/account/delete` gets none: it is Art. 17, it is terminal on success,
  and it already needs a step-up. A shared window could only delay an erasure
  request.

The bucket domain of `diary.rate_window` is a CHECK, so a new bucket is a
migration and not a string a service may invent. That is the point of the
constraint: an endpoint whose window was never migrated fails loudly on the
first request instead of running unlimited.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUCKETS_0006 = "'sync', 'push_bytes', 'key_read', 'reminders_settings', 'account_ops'"
_BUCKETS_0005 = "'sync', 'push_bytes', 'key_read', 'reminders_settings'"


def _replace_bucket_check(buckets: str) -> None:
    op.execute(
        """
        ALTER TABLE diary.rate_window
            DROP CONSTRAINT ck_rate_window_bucket
        """
    )
    op.execute(
        f"""
        ALTER TABLE diary.rate_window
            ADD CONSTRAINT ck_rate_window_bucket
            CHECK (bucket IN ({buckets}))
        """
    )


def upgrade() -> None:
    op.execute("SET LOCAL ROLE migrator")
    _replace_bucket_check(_BUCKETS_0006)
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE migrator")
    # Rows in the new bucket would violate the narrowed CHECK, so they go first —
    # the same order migration 0005 needed, and for the same reason: a downgrade
    # that fails halfway is worse than one that drops a counter, because losing a
    # rate window costs one minute of budget and nothing else.
    op.execute("DELETE FROM diary.rate_window WHERE bucket = 'account_ops'")
    _replace_bucket_check(_BUCKETS_0005)
    op.execute("RESET ROLE")
