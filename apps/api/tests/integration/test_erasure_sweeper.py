"""The housekeeping pass: the 30-day window of §4.3 and the service TTLs of §6.2."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from app.domain.identity import ConsentKind, RevokeReason
from app.infra.db.engine import SqlUnitOfWorkFactory
from app.services.erasure import ErasureService
from app.services.erasure_journal import DatabaseErasureJournal
from app.workers.erasure_worker import Housekeeper
from conftest import FrozenClock

TEXT_SHA256 = bytes(range(32))


@pytest.fixture
def housekeeper(
    unit_of_work: SqlUnitOfWorkFactory,
    consent_copy: object,
    clock: FrozenClock,
) -> Housekeeper:
    journal = DatabaseErasureJournal(unit_of_work, consent_copy)  # type: ignore[arg-type]
    return Housekeeper(unit_of_work, ErasureService(journal), clock)


def _abandoned_account(
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
    *,
    reason: RevokeReason,
    age: timedelta,
) -> UUID:
    account_id = uuid4()
    granted = clock.now() - age - timedelta(days=1)
    with unit_of_work() as unit:
        unit.accounts.create(account_id, created_at=granted)
        unit.identities.create(int(account_id.int % 10**9) + 1, account_id, now=granted)
        unit.consents.grant(
            account_id,
            kind=ConsentKind.TELEGRAM_REMINDERS,
            text_version="telegram_reminders@0.9",
            text_sha256=TEXT_SHA256,
            text_locale="uk",
            now=granted,
        )
        unit.consents.revoke(
            account_id,
            kinds=[ConsentKind.TELEGRAM_REMINDERS],
            reason=reason,
            now=clock.now() - age,
        )
        unit.commit()
    return account_id


def _count(engine: Engine, statement: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement)).scalar_one())


def test_an_account_blocked_for_29_days_is_still_recoverable(
    housekeeper: Housekeeper,
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    """§4.3: blocking a bot is a common, easily reversed gesture."""
    _abandoned_account(
        unit_of_work,
        clock,
        reason=RevokeReason.BOT_BLOCKED_TIMEOUT,
        age=timedelta(days=29),
    )

    housekeeper.run_once()

    assert _count(engine, "SELECT count(*) FROM diary.account") == 1
    assert _count(engine, "SELECT count(*) FROM diary.erasure_job") == 0


def test_an_account_blocked_past_thirty_days_is_erased(
    housekeeper: Housekeeper,
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    _abandoned_account(
        unit_of_work,
        clock,
        reason=RevokeReason.BOT_BLOCKED_TIMEOUT,
        age=timedelta(days=30, minutes=1),
    )

    erased = housekeeper.run_once()

    assert erased.accounts_erased == 1
    assert _count(engine, "SELECT count(*) FROM diary.account") == 0
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT scope, deletion_copy_version, completed_at"
                " FROM diary.erasure_job"
            )
        ).one()
    assert row.scope == "full"
    assert row.deletion_copy_version == "deletion@1.0"
    assert row.completed_at is not None


def test_stale_text_timeout_uses_the_same_window(
    housekeeper: Housekeeper,
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    _abandoned_account(
        unit_of_work,
        clock,
        reason=RevokeReason.STALE_TEXT_TIMEOUT,
        age=timedelta(days=31),
    )

    housekeeper.run_once()

    assert _count(engine, "SELECT count(*) FROM diary.account") == 0


def test_an_account_with_an_active_consent_is_untouched(
    housekeeper: Housekeeper,
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    account_id = _abandoned_account(
        unit_of_work,
        clock,
        reason=RevokeReason.BOT_BLOCKED_TIMEOUT,
        age=timedelta(days=60),
    )
    with unit_of_work() as unit:
        unit.consents.grant(
            account_id,
            kind=ConsentKind.HEALTH_SYNC,
            text_version="health_sync@0.9",
            text_sha256=TEXT_SHA256,
            text_locale="uk",
            now=clock.now(),
        )
        unit.commit()

    housekeeper.run_once()

    assert _count(engine, "SELECT count(*) FROM diary.account") == 1


def test_replay_digests_expire_after_forty_eight_hours(
    housekeeper: Housekeeper,
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    with unit_of_work() as unit:
        unit.auth_replay.remember(
            bytes(range(32)), now=clock.now() - timedelta(hours=49)
        )
        unit.auth_replay.remember(
            bytes(range(1, 33)), now=clock.now() - timedelta(hours=47)
        )
        unit.commit()

    result = housekeeper.run_once()

    assert result.replay_digests_removed == 1
    assert _count(engine, "SELECT count(*) FROM diary.auth_replay") == 1


def test_revoked_consents_are_kept_for_two_years_then_dropped(
    housekeeper: Housekeeper,
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    """§4.3: proof of consent under Art. 7(1), bounded by minimisation."""
    account_id = uuid4()
    long_ago = clock.now() - timedelta(days=800)
    with unit_of_work() as unit:
        unit.accounts.create(account_id, created_at=long_ago)
        unit.consents.grant(
            account_id,
            kind=ConsentKind.HEALTH_SYNC,
            text_version="health_sync@0.9",
            text_sha256=TEXT_SHA256,
            text_locale="uk",
            now=long_ago,
        )
        unit.consents.revoke(
            account_id,
            kinds=[ConsentKind.HEALTH_SYNC],
            reason=RevokeReason.USER,
            now=long_ago + timedelta(days=1),
        )
        unit.commit()

    result = housekeeper.run_once()

    assert result.consent_rows_removed == 1
    assert _count(engine, "SELECT count(*) FROM diary.consent") == 0
