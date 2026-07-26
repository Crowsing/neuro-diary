"""The three restore drills of §6.4, on a real PostgreSQL.

**On fixtures, and that is said plainly.** No backup was taken and no database
was restored: each drill *builds* the state a restore would leave behind — rows
that are present again and a journal that says they should not be — and then
runs the reconciliation against it. That proves the reconciliation, not the
backup tooling. Proving the backup tooling needs buckets nobody had in this
session, and it is recorded as outstanding rather than implied.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.domain.erasure_journal import JournalLine
from app.infra.config import Settings
from app.infra.db.engine import SqlUnitOfWorkFactory
from app.infra.erasure_journal.journal import ObjectStoreErasureJournal
from app.infra.erasure_journal.store import InMemoryObjectStore
from app.main import AppDependencies, create_app
from app.services.erasure import ErasureService
from app.services.erasure_journal import DatabaseErasureJournal, TeeErasureJournal
from app.workers.restore_reconciler import RestoreReconciler
from conftest import REPO_ROOT, Caller, FrozenClock, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()
REMINDERS_SHA = hashlib.sha256(
    (REGISTRY / "uk/telegram_reminders/0.9.md").read_bytes()
).hexdigest()

ENVELOPE = base64.b64encode(b"the-old-envelope").decode()
KDF_PARAMS: dict[str, Any] = {"iterations": 1_000_000, "salt_hex": "00" * 16}
KEY_A = "aa" * 32
CLIENT_TS = 1_768_435_200_000

#: A round restore point, because that is what an operator picks.
RESTORE_POINT = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def erasure_key() -> bytes:
    # Minted inside the run: gitleaks scans the whole history.
    return secrets.token_bytes(32)


@pytest.fixture
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
def external(
    store: InMemoryObjectStore,
    erasure_key: bytes,
) -> ObjectStoreErasureJournal:
    return ObjectStoreErasureJournal(
        store,
        erasure_key=erasure_key,
        head_key=secrets.token_bytes(32),
    )


@pytest.fixture
def journal(
    external: ObjectStoreErasureJournal,
    unit_of_work: SqlUnitOfWorkFactory,
    consent_copy: object,
) -> TeeErasureJournal:
    return TeeErasureJournal(
        external,
        DatabaseErasureJournal(unit_of_work, consent_copy),  # type: ignore[arg-type]
    )


@pytest.fixture
def teed_api(
    dependencies: AppDependencies,
    settings: Settings,
    journal: TeeErasureJournal,
) -> FastAPI:
    return create_app(
        AppDependencies(
            settings=settings,
            unit_of_work=dependencies.unit_of_work,
            clock=dependencies.clock,
            init_data=dependencies.init_data,
            consent_copy=dependencies.consent_copy,
            erasure_journal=journal,
        )
    )


@pytest.fixture
def reconciler(
    unit_of_work: SqlUnitOfWorkFactory,
    journal: TeeErasureJournal,
    clock: FrozenClock,
    erasure_key: bytes,
) -> RestoreReconciler:
    return RestoreReconciler(
        unit_of_work,
        ErasureService(journal),
        clock,
        erasure_key=erasure_key,
    )


@pytest.fixture
def session(teed_api: FastAPI) -> Caller:
    caller = Caller(teed_api)
    response = caller.post(
        "/v1/auth/telegram",
        {
            "init_data": sign_init_data(),
            "grant": {
                "kind": "health_sync",
                "text_version": "health_sync@0.9",
                "text_sha256": HEALTH_SYNC_SHA,
            },
        },
    )
    assert response.status_code == 200, response.text
    caller.token = str(response.json()["session_token"])
    return caller


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        )


def _account_id(engine: Engine) -> UUID:
    with engine.connect() as connection:
        return UUID(
            str(connection.execute(text("SELECT id FROM diary.account")).scalar_one())
        )


def _counters(engine: Engine) -> tuple[int, int, int]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT current_revision, compacted_up_to, reset_revision"
                " FROM diary.vault_revision"
            )
        ).one()
    return (row.current_revision, row.compacted_up_to, row.reset_revision)


def _stock_the_vault(session: Caller) -> None:
    response = session.post(
        "/v1/sync/key",
        {
            "mode": "rewrap",
            "expected_wrap_version": 0,
            "wrapped_dek": ENVELOPE,
            "kdf": "pbkdf2-sha256",
            "kdf_params": KDF_PARAMS,
        },
    )
    assert response.status_code == 200, response.text
    pushed = session.post(
        "/v1/sync/push",
        {
            "base_revision": 0,
            "changes": [
                {
                    "record_key": KEY_A,
                    "client_ts_ms": CLIENT_TS,
                    "payload_b64": base64.b64encode(b"ciphertext").decode(),
                    "tombstone": False,
                }
            ],
        },
    )
    assert pushed.status_code == 200, pushed.text


def _grant_reminders(caller: Caller) -> None:
    response = caller.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
            "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
        },
    )
    assert response.status_code == 201, response.text


def _entries(external: ObjectStoreErasureJournal) -> list[JournalLine]:
    return [JournalLine.from_json(item.body) for item in external.read()]


# ------------------------------------------------------------------- drill one


def test_a_backup_with_an_in_flight_job_erases_again(
    session: Caller,
    engine: Engine,
    external: ObjectStoreErasureJournal,
    journal: TeeErasureJournal,
    reconciler: RestoreReconciler,
) -> None:
    """The journal opened, the deletion did not commit, the backup caught that.

    This is the state §6.4 calls harmless on its own — an entry with no
    completed erasure — and it is harmless precisely because reconciliation
    turns it back into a deletion.
    """
    _stock_the_vault(session)
    account_id = _account_id(engine)
    journal.record_intent(account_id=account_id, code="full", at=RESTORE_POINT)
    assert _count(engine, "diary.erasure_job WHERE completed_at IS NULL") == 1

    result = reconciler.reconcile(_entries(external), taken_at=RESTORE_POINT)

    assert result.applied == 1
    assert not result.outstanding
    for table in ("account", "vault_record", "vault_key", "consent"):
        assert _count(engine, f"diary.{table}") == 0, table


# ------------------------------------------------------------------ drill two


def test_a_backup_taken_before_the_request_is_reconciled_from_the_journal(
    session: Caller,
    engine: Engine,
    external: ObjectStoreErasureJournal,
    reconciler: RestoreReconciler,
) -> None:
    """The database holds no trace of the erasure — only the journal does.

    `diary.erasure_job` went back with the restore, so the entry that survives
    is the one in the other country. That is the whole argument of §6.5,
    rehearsed.
    """
    _stock_the_vault(session)
    _grant_reminders(session)
    account_id = _account_id(engine)
    external.record_intent(account_id=account_id, code="full", at=RESTORE_POINT)
    assert _count(engine, "diary.erasure_job") == 0

    result = reconciler.reconcile(_entries(external), taken_at=RESTORE_POINT)

    assert result.applied == 1
    assert _count(engine, "diary.account") == 0
    assert _count(engine, "reminders.reminder_schedule") == 0


# ---------------------------------------------------------------- drill three


def test_a_backup_taken_before_a_rekey_is_reconciled_as_a_security_reset(
    session: Caller,
    engine: Engine,
    external: ObjectStoreErasureJournal,
    reconciler: RestoreReconciler,
) -> None:
    """The case no single section of the plan saw on its own (§6.4 ↔ §7).

    A restore from before a re-key hands the old passphrase a *live* service:
    the old envelope and the old ciphertext are both back. The reconciliation
    removes both.

    "The old passphrase no longer works" is asserted as "there is no envelope
    left to open" — the server is zero-knowledge and could not test a
    passphrase even if it wanted to. Saying it any other way would be a claim
    the code cannot make.
    """
    _stock_the_vault(session)
    account_id = _account_id(engine)
    before = _counters(engine)
    external.record_intent(
        account_id=account_id,
        code="security_reset",
        at=RESTORE_POINT,
    )

    result = reconciler.reconcile(_entries(external), taken_at=RESTORE_POINT)

    assert result.applied == 1
    assert _count(engine, "diary.vault_key") == 0
    assert _count(engine, "diary.vault_record") == 0
    # The account outlives it — this is not an erasure the user asked for.
    assert _count(engine, "diary.account") == 1
    # A device that was offline through all of this cannot push its copy back.
    assert _counters(engine)[2] == before[0] + 1
    # And the replay is journalled under its own code, not under `sync_off`.
    assert [line.code for line in _entries(external)] == [
        "security_reset",
        "security_reset",
    ]


# -------------------------------------------------------------- the boundary


def test_an_entry_exactly_at_a_round_restore_point_is_included(
    session: Caller,
    engine: Engine,
    external: ObjectStoreErasureJournal,
    reconciler: RestoreReconciler,
) -> None:
    """§6.4 spells the comparison `at >= t_b`, and the round case is why.

    Operators pick round restore points and the journal rounds `at` up to the
    hour, so `10:00 > 10:00` would drop precisely the entries most likely to
    exist.
    """
    _stock_the_vault(session)
    account_id = _account_id(engine)
    external.record_intent(account_id=account_id, code="full", at=RESTORE_POINT)

    result = reconciler.reconcile(_entries(external), taken_at=RESTORE_POINT)

    assert result.considered == 1
    assert result.applied == 1
    assert _count(engine, "diary.account") == 0


def test_an_entry_from_before_the_restore_point_is_left_alone(
    session: Caller,
    engine: Engine,
    external: ObjectStoreErasureJournal,
    reconciler: RestoreReconciler,
) -> None:
    """The other side of the boundary, without which the test above passes on
    a reconciler that simply erases everything it is handed."""
    _stock_the_vault(session)
    account_id = _account_id(engine)
    external.record_intent(
        account_id=account_id,
        code="full",
        at=RESTORE_POINT - timedelta(hours=1),
    )

    result = reconciler.reconcile(_entries(external), taken_at=RESTORE_POINT)

    assert result.considered == 0
    assert result.applied == 0
    assert _count(engine, "diary.account") == 1


# --------------------------------------------------------------- other paths


def test_an_entry_for_an_account_the_restore_did_not_bring_back_is_done(
    session: Caller,
    engine: Engine,
    external: ObjectStoreErasureJournal,
    reconciler: RestoreReconciler,
) -> None:
    _stock_the_vault(session)
    external.record_intent(
        account_id=UUID("00000000-0000-4000-8000-000000000000"),
        code="full",
        at=RESTORE_POINT,
    )

    result = reconciler.reconcile(_entries(external), taken_at=RESTORE_POINT)

    assert result.already_absent == 1
    assert result.applied == 0
    assert _count(engine, "diary.account") == 1


def test_a_reminders_entry_removes_both_reminder_tables(
    session: Caller,
    engine: Engine,
    identity_database: Any,
    external: ObjectStoreErasureJournal,
    reconciler: RestoreReconciler,
) -> None:
    import psycopg

    _grant_reminders(session)
    account_id = _account_id(engine)
    with psycopg.connect(identity_database.admin_url, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO reminders.reminder_delivery"
            " (account_id, local_date, status, created_at)"
            " VALUES (%s, DATE '2026-07-24', 'sent', now())",
            (str(account_id),),
        )
    external.record_intent(
        account_id=account_id,
        code="reminders_off",
        at=RESTORE_POINT,
    )

    reconciler.reconcile(_entries(external), taken_at=RESTORE_POINT)

    assert _count(engine, "reminders.reminder_schedule") == 0
    assert _count(engine, "reminders.reminder_delivery") == 0
    # The consent row is untouched: a reconciliation restores the *data* state
    # the user asked for, and the revocation itself came back with the restore.
    assert _count(engine, "diary.account") == 1


def test_a_second_entry_for_an_account_the_first_one_erased_does_nothing(
    session: Caller,
    engine: Engine,
    external: ObjectStoreErasureJournal,
    reconciler: RestoreReconciler,
) -> None:
    """The accounts are enumerated once; entries are applied one by one.

    Two `full` entries for the same account is the ordinary shape of that — a
    restore can easily carry more than one request for an account whose first
    replay already erased it. The row lock is what notices, and without it the
    second pass would journal an erasure of rows that are not there.
    """
    _stock_the_vault(session)
    account_id = _account_id(engine)
    external.record_intent(account_id=account_id, code="full", at=RESTORE_POINT)
    external.record_intent(
        account_id=account_id,
        code="full",
        at=RESTORE_POINT + timedelta(hours=1),
    )

    result = reconciler.reconcile(_entries(external), taken_at=RESTORE_POINT)

    assert result.considered == 2
    assert result.applied == 1
    assert _count(engine, "diary.account") == 0
    # Two entries in, one replay out: the journal did not grow by a second
    # erasure of an account that was already gone.
    assert [line.code for line in _entries(external)] == ["full", "full", "full"]


def test_an_unknown_code_is_reported_rather_than_guessed(
    session: Caller,
    engine: Engine,
    external: ObjectStoreErasureJournal,
    erasure_key: bytes,
    reconciler: RestoreReconciler,
) -> None:
    """A future code must stop the runbook, not fall through to a branch.

    `ck_erasure_job_scope` keeps the database honest about the four; the
    journal is a different store with a different lifetime, so this build has
    to answer for a line it does not understand.
    """
    _stock_the_vault(session)
    account_id = _account_id(engine)
    from app.domain.erasure_journal import erasure_ref

    line = JournalLine(
        erasure_ref=erasure_ref(erasure_key, account_id),
        at=RESTORE_POINT,
        code="something_phase_seven_invented",
        prev_hash="0" * 64,
    )

    result = reconciler.reconcile([line], taken_at=RESTORE_POINT)

    assert result.outstanding
    assert result.unknown_code == ("something_phase_seven_invented",)
    assert result.applied == 0
    assert _count(engine, "diary.account") == 1
