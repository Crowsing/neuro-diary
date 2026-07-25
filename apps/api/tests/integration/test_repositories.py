"""Repository behaviour against real PostgreSQL under the `api_rw` role."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.identity import ConsentKind, RevokeReason
from app.infra.db.engine import SqlUnitOfWorkFactory

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
TELEGRAM_USER_ID = 4242424242
TEXT_SHA256 = bytes(range(32))


def _seed_account(unit_of_work: SqlUnitOfWorkFactory) -> object:
    account_id = uuid4()
    with unit_of_work() as uow:
        uow.accounts.create(account_id, created_at=NOW)
        uow.identities.create(TELEGRAM_USER_ID, account_id, now=NOW)
        uow.commit()
    return account_id


def test_account_and_identity_round_trip(
    unit_of_work: SqlUnitOfWorkFactory,
) -> None:
    account_id = _seed_account(unit_of_work)

    with unit_of_work() as uow:
        assert uow.accounts.status(account_id) == "active"
        assert uow.identities.account_id_for(TELEGRAM_USER_ID) == account_id
        assert uow.identities.account_id_for(1) is None


def test_identity_touch_records_the_latest_authentication(
    unit_of_work: SqlUnitOfWorkFactory,
) -> None:
    _seed_account(unit_of_work)
    later = NOW + timedelta(days=1)

    with unit_of_work() as uow:
        uow.identities.touch(TELEGRAM_USER_ID, now=later)
        uow.commit()

    with unit_of_work() as uow:
        assert uow.identities.last_auth_at(TELEGRAM_USER_ID) == later


def test_only_one_active_consent_per_kind(
    unit_of_work: SqlUnitOfWorkFactory,
) -> None:
    account_id = _seed_account(unit_of_work)

    with unit_of_work() as uow:
        uow.consents.grant(
            account_id,
            kind=ConsentKind.HEALTH_SYNC,
            text_version="health_sync@0.9",
            text_sha256=TEXT_SHA256,
            text_locale="uk",
            record_key_cycle=None,
            now=NOW,
        )
        uow.commit()

    with pytest.raises(IntegrityError), unit_of_work() as uow:
        uow.consents.grant(
            account_id,
            kind=ConsentKind.HEALTH_SYNC,
            text_version="health_sync@0.9",
            text_sha256=TEXT_SHA256,
            text_locale="uk",
            record_key_cycle=None,
            now=NOW,
        )
        uow.commit()


def test_revoking_frees_the_kind_for_a_new_grant(
    unit_of_work: SqlUnitOfWorkFactory,
) -> None:
    account_id = _seed_account(unit_of_work)

    with unit_of_work() as uow:
        uow.consents.grant(
            account_id,
            kind=ConsentKind.HEALTH_SYNC,
            text_version="health_sync@0.9",
            text_sha256=TEXT_SHA256,
            text_locale="uk",
            record_key_cycle=None,
            now=NOW,
        )
        uow.commit()

    with unit_of_work() as uow:
        revoked = uow.consents.revoke(
            account_id,
            kinds=[ConsentKind.HEALTH_SYNC],
            reason=RevokeReason.USER,
            now=NOW + timedelta(hours=1),
        )
        uow.commit()
    assert revoked == [ConsentKind.HEALTH_SYNC]

    with unit_of_work() as uow:
        assert uow.consents.active_kinds(account_id) == set()
        uow.consents.grant(
            account_id,
            kind=ConsentKind.HEALTH_SYNC,
            text_version="health_sync@0.9",
            text_sha256=TEXT_SHA256,
            text_locale="uk",
            record_key_cycle=None,
            now=NOW + timedelta(hours=2),
        )
        uow.commit()

    with unit_of_work() as uow:
        assert uow.consents.active_kinds(account_id) == {ConsentKind.HEALTH_SYNC}


def test_active_consents_expose_the_stored_hash(
    unit_of_work: SqlUnitOfWorkFactory,
) -> None:
    account_id = _seed_account(unit_of_work)

    with unit_of_work() as uow:
        uow.consents.grant(
            account_id,
            kind=ConsentKind.CYCLE_SYNC,
            text_version="cycle_sync@0.9",
            text_sha256=TEXT_SHA256,
            text_locale="uk",
            record_key_cycle=None,
            now=NOW,
        )
        uow.commit()

    with unit_of_work() as uow:
        (record,) = uow.consents.active(account_id)

    assert record.kind is ConsentKind.CYCLE_SYNC
    assert record.text_version == "cycle_sync@0.9"
    assert record.text_sha256 == TEXT_SHA256
    assert record.granted_at == NOW


def test_replay_digest_is_accepted_once(
    unit_of_work: SqlUnitOfWorkFactory,
) -> None:
    digest = bytes(range(32))

    with unit_of_work() as uow:
        assert uow.auth_replay.remember(digest, now=NOW) is True
        uow.commit()

    with unit_of_work() as uow:
        assert uow.auth_replay.remember(digest, now=NOW) is False


def test_rollback_leaves_no_rows(unit_of_work: SqlUnitOfWorkFactory) -> None:
    account_id = uuid4()

    with unit_of_work() as uow:
        uow.accounts.create(account_id, created_at=NOW)
        # No commit: leaving the block must discard the insert.

    with unit_of_work() as uow:
        assert uow.accounts.status(account_id) is None


def test_an_exception_inside_the_unit_of_work_rolls_back(
    unit_of_work: SqlUnitOfWorkFactory,
) -> None:
    account_id = uuid4()

    with pytest.raises(RuntimeError), unit_of_work() as uow:
        uow.accounts.create(account_id, created_at=NOW)
        raise RuntimeError("boom")

    with unit_of_work() as uow:
        assert uow.accounts.status(account_id) is None
