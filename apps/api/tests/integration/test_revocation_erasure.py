"""Revocation as an erasure, on a real PostgreSQL (§4.3, §6.4, §9.7, §9.8).

Every test here asserts stored state, not only a status code. The failure mode
this phase exists to prevent is precisely one where the answer looks right and
the rows stay behind.
"""

from __future__ import annotations

import base64
import hashlib
import threading
import time
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.infra.config import Settings
from app.main import AppDependencies, create_app
from conftest import Database, REPO_ROOT, Caller, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()
CYCLE_SHA = hashlib.sha256((REGISTRY / "uk/cycle_sync/0.9.md").read_bytes()).hexdigest()
REMINDERS_SHA = hashlib.sha256(
    (REGISTRY / "uk/telegram_reminders/0.9.md").read_bytes()
).hexdigest()

KEY_ENTRY = "aa" * 32
KEY_OTHER = "bb" * 32
KEY_CYCLE = "cc" * 32
CLIENT_TS = 1_768_435_200_000
ENVELOPE = base64.b64encode(b"envelope").decode()
KDF_PARAMS: dict[str, Any] = {"iterations": 1_000_000, "salt_hex": "00" * 16}


def _change(record_key: str, body: str = "ciphertext") -> dict[str, object]:
    return {
        "record_key": record_key,
        "client_ts_ms": CLIENT_TS,
        "payload_b64": base64.b64encode(body.encode()).decode(),
        "tombstone": False,
    }


@pytest.fixture
def session(caller: Caller) -> Caller:
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


def _grant_cycle(caller: Caller, *, record_key_cycle: str | None = KEY_CYCLE) -> None:
    body: dict[str, object] = {
        "kind": "cycle_sync",
        "text_version": "cycle_sync@0.9",
        "text_sha256": CYCLE_SHA,
    }
    if record_key_cycle is not None:
        body["record_key_cycle"] = record_key_cycle
    response = caller.post("/v1/consents", body)
    assert response.status_code == 201, response.text


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


def _push(caller: Caller, base_revision: int, *keys: str) -> Any:
    return caller.post(
        "/v1/sync/push",
        {
            "base_revision": base_revision,
            "changes": [_change(key) for key in keys],
        },
    )


def _write_envelope(caller: Caller) -> None:
    response = caller.post(
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


def _scalar(engine: Engine, statement: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(text(statement)).scalar_one()


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


def _stocked_vault(session: Caller) -> int:
    """Two records and an envelope — a vault that has something to lose."""
    _write_envelope(session)
    response = _push(session, 0, KEY_ENTRY, KEY_OTHER)
    assert response.status_code == 200, response.text
    return int(response.json()["new_revision"])


# --------------------------------------------------------------- health_sync


def test_revoking_health_sync_leaves_no_vault_record_and_no_vault_key(
    session: Caller,
    engine: Engine,
) -> None:
    """DoD: zero rows in both tables. Crypto-erasure is part of the promise."""
    revision = _stocked_vault(session)
    _grant_reminders(session)

    response = session.post(
        "/v1/consents/revoke",
        {"kind": "health_sync", "last_acked_revision": revision},
    )

    assert response.status_code == 200, response.text
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_record") == 0
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_key") == 0
    # The account is still here — this was a partial erasure, not an account one.
    assert _scalar(engine, "SELECT count(*) FROM diary.account") == 1


def test_the_vault_erasure_is_journalled_before_it_happens(
    session: Caller,
    engine: Engine,
) -> None:
    revision = _stocked_vault(session)
    _grant_reminders(session)

    session.post(
        "/v1/consents/revoke",
        {"kind": "health_sync", "last_acked_revision": revision},
    )

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT scope, requested_at, completed_at, deletion_copy_version"
                " FROM diary.erasure_job"
            )
        ).one()
    assert row.scope == "sync_off"
    assert row.deletion_copy_version == "deletion@1.0"
    assert row.requested_at is not None


def test_an_unwritable_journal_stops_the_vault_erasure(
    refusing_journal_api: FastAPI,
    engine: Engine,
) -> None:
    """§6.4: journal first. Nothing is deleted and nothing is revoked."""
    caller = Caller(refusing_journal_api)
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
    caller.token = str(response.json()["session_token"])
    revision = _stocked_vault(caller)
    _grant_reminders(caller)

    with pytest.raises(RuntimeError):
        caller.post(
            "/v1/consents/revoke",
            {"kind": "health_sync", "last_acked_revision": revision},
        )

    assert _scalar(engine, "SELECT count(*) FROM diary.vault_record") == 2
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_key") == 1
    assert (
        _scalar(
            engine,
            "SELECT count(*) FROM diary.consent WHERE revoked_at IS NOT NULL",
        )
        == 0
    )


def test_the_counters_are_reset_so_a_stale_device_cannot_upload_it_all_back(
    session: Caller,
    engine: Engine,
) -> None:
    """§4.3, and the reason it is not optional.

    A device that went offline before the revocation still holds the old
    `base_revision`. Without the reset it passes both gates of §9.1 after a
    re-grant and quietly restores the diary the user just removed — no 409, no
    410, no dialogue.
    """
    revision = _stocked_vault(session)
    _grant_reminders(session)
    session.post(
        "/v1/consents/revoke",
        {"kind": "health_sync", "last_acked_revision": revision},
    )
    current, compacted, reset = _counters(engine)
    assert (current, compacted, reset) == (revision + 1, revision + 1, revision + 1)

    # The user changes her mind and turns synchronization back on.
    regrant = session.post(
        "/v1/consents",
        {
            "kind": "health_sync",
            "text_version": "health_sync@0.9",
            "text_sha256": HEALTH_SYNC_SHA,
        },
    )
    assert regrant.status_code == 201, regrant.text

    stale = _push(session, revision, KEY_ENTRY)

    assert stale.status_code == 409
    assert stale.json() == {"error": "vault_reset"}
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_record") == 0


# ------------------------------------------------------- §8 confirm_required


def test_revoking_refuses_once_when_the_server_holds_more_than_the_device_saw(
    session: Caller,
    engine: Engine,
) -> None:
    """§8: the compensating control that stands in for a forbidden step-up."""
    revision = _stocked_vault(session)

    refused = session.post(
        "/v1/consents/revoke",
        {"kind": "health_sync", "last_acked_revision": revision - 1},
    )

    assert refused.status_code == 409
    assert refused.json() == {"error": "confirm_required"}
    # Refused means refused: nothing revoked, nothing deleted.
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_record") == 2
    assert (
        _scalar(
            engine,
            "SELECT count(*) FROM diary.consent WHERE revoked_at IS NOT NULL",
        )
        == 0
    )


def test_the_second_call_with_the_acknowledgement_goes_through(
    session: Caller,
    engine: Engine,
) -> None:
    revision = _stocked_vault(session)
    _grant_reminders(session)
    assert (
        session.post(
            "/v1/consents/revoke",
            {"kind": "health_sync", "last_acked_revision": revision - 1},
        ).status_code
        == 409
    )

    confirmed = session.post(
        "/v1/consents/revoke",
        {
            "kind": "health_sync",
            "last_acked_revision": revision - 1,
            "acknowledge_incomplete": True,
        },
    )

    assert confirmed.status_code == 200, confirmed.text
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_record") == 0


def test_a_device_that_says_nothing_gets_the_question(
    session: Caller,
) -> None:
    """Fail-closed: an omitted field reads as zero, not as "everything seen"."""
    _stocked_vault(session)

    refused = session.post("/v1/consents/revoke", {"kind": "health_sync"})

    assert refused.status_code == 409
    assert refused.json() == {"error": "confirm_required"}


def test_an_empty_vault_asks_nothing(session: Caller) -> None:
    """The counterpart: the control must not stand between the user and a
    withdrawal when there is nothing on the server to lose."""
    _grant_reminders(session)

    response = session.post("/v1/consents/revoke", {"kind": "health_sync"})

    assert response.status_code == 200, response.text


def test_revoking_another_consent_is_never_gated_by_the_vault(
    session: Caller,
) -> None:
    """§8 names `health_sync` only, and Art. 7(3) is the reason to keep it that
    narrow: withdrawal must be as easy as granting."""
    _stocked_vault(session)
    _grant_reminders(session)

    response = session.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    assert response.status_code == 200, response.text


# ----------------------------------------------------------------- §9.7 cycle


def test_revoking_cycle_sync_hard_deletes_the_named_record_without_a_tombstone(
    session: Caller,
    engine: Engine,
) -> None:
    _write_envelope(session)
    _grant_cycle(session)
    pushed = _push(session, 0, KEY_ENTRY, KEY_CYCLE)
    assert pushed.status_code == 200, pushed.text
    revision = int(pushed.json()["new_revision"])

    response = session.post("/v1/consents/revoke", {"kind": "cycle_sync"})

    assert response.status_code == 200, response.text
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT encode(record_key,'hex') AS key, deleted FROM diary.vault_record"
            )
        ).fetchall()
    # Hard DELETE, not a tombstone: a tombstone appearing exactly when the
    # server has a fresh `revoked_at('cycle_sync')` would deanonymize the key.
    assert [(row.key, row.deleted) for row in rows] == [(KEY_ENTRY, False)]
    # The revision moves, so every other device learns there is something new.
    assert _counters(engine)[0] == revision + 1
    # The rest of the vault is untouched — this is not a vault erasure.
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_key") == 1


def test_a_consent_without_a_named_key_revokes_and_deletes_nothing(
    session: Caller,
    engine: Engine,
) -> None:
    """The defined outcome §9.7 owes a NULL column, rather than silence.

    The server cannot tell which ciphertext is the cycle until the client names
    the key, so it deletes nothing and says so — guessing would be worse than
    the honest gap.
    """
    _write_envelope(session)
    _grant_cycle(session, record_key_cycle=None)
    pushed = _push(session, 0, KEY_ENTRY, KEY_CYCLE)
    revision = int(pushed.json()["new_revision"])

    response = session.post("/v1/consents/revoke", {"kind": "cycle_sync"})

    assert response.status_code == 200, response.text
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_record") == 2
    # No row went away, so no revision was spent announcing that one did.
    assert _counters(engine)[0] == revision


def test_revoking_health_sync_takes_the_cycle_consent_with_it(
    session: Caller,
    engine: Engine,
) -> None:
    _write_envelope(session)
    _grant_cycle(session)
    revision = int(_push(session, 0, KEY_ENTRY, KEY_CYCLE).json()["new_revision"])
    _grant_reminders(session)

    session.post(
        "/v1/consents/revoke",
        {"kind": "health_sync", "last_acked_revision": revision},
    )

    with engine.connect() as connection:
        active = (
            connection.execute(
                text("SELECT kind FROM diary.consent WHERE revoked_at IS NULL")
            )
            .scalars()
            .all()
        )
    assert active == ["telegram_reminders"]
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_record") == 0


def test_a_push_of_the_named_key_after_revocation_is_refused_with_zero_rows(
    session: Caller,
    engine: Engine,
) -> None:
    """DoD: the §9.7 push gate still fires once the consent is gone.

    Device B does not know about the revocation yet. Without this gate it
    quietly resurrects the cycle data.
    """
    _write_envelope(session)
    _grant_cycle(session)
    revision = int(_push(session, 0, KEY_ENTRY, KEY_CYCLE).json()["new_revision"])
    session.post("/v1/consents/revoke", {"kind": "cycle_sync"})

    refused = _push(session, revision + 1, KEY_CYCLE)

    assert refused.status_code == 409
    assert refused.json() == {"error": "consent_precondition"}
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_record") == 1


# --------------------------------------------------------- §9.8 serialization


def test_the_vault_erasure_waits_for_the_account_row_lock(
    session: Caller,
    engine: Engine,
    identity_database: Database,
) -> None:
    """§9.8: the barrier is the account row lock of §9.1, step 1.

    Held deterministically by a second connection rather than raced: a race
    that happens to pass proves nothing about the lock. While the lock is held,
    the revocation must not finish; once released, it must.
    """
    revision = _stocked_vault(session)
    _grant_reminders(session)
    account_id = _account_id(engine)

    outcome: list[int] = []

    def revoke() -> None:
        response = session.post(
            "/v1/consents/revoke",
            {"kind": "health_sync", "last_acked_revision": revision},
        )
        outcome.append(response.status_code)

    with psycopg.connect(identity_database.api_url) as holder:
        holder.execute(
            "SELECT id FROM diary.account WHERE id = %s FOR NO KEY UPDATE",
            (account_id,),
        )
        worker = threading.Thread(target=revoke)
        worker.start()
        time.sleep(1.0)
        blocked = not outcome
        holder.rollback()
        worker.join(timeout=10)

    assert blocked, "revocation did not take the account row lock of §9.1"
    assert outcome == [200]
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_record") == 0


def test_a_push_racing_the_revocation_never_leaves_records_behind(
    session: Caller,
    engine: Engine,
) -> None:
    """The invariant the barrier buys, stated as an invariant.

    Either ordering is legal — the push commits first and its rows are deleted,
    or it starts after and fails the in-transaction consent check of §9.1 step
    4. What is never legal is a vault with records and no consent.
    """
    revision = _stocked_vault(session)
    _grant_reminders(session)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pushing = pool.submit(_push, session, revision, KEY_ENTRY)
        revoking = pool.submit(
            session.post,
            "/v1/consents/revoke",
            {
                "kind": "health_sync",
                "last_acked_revision": revision,
                "acknowledge_incomplete": True,
            },
        )
        push_response = pushing.result()
        revoke_response = revoking.result()

    assert revoke_response.status_code == 200, revoke_response.text
    assert push_response.status_code in (200, 403), push_response.text
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_record") == 0
    assert _scalar(engine, "SELECT count(*) FROM diary.vault_key") == 0


class RefusingJournal:
    def record_intent(self, *, account_id: UUID, code: str, at: datetime) -> UUID:
        del account_id, code, at
        raise RuntimeError("journal unavailable")

    def confirm(self, reference: UUID, *, at: datetime) -> None:  # pragma: no cover
        del reference, at


@pytest.fixture
def refusing_journal_api(
    dependencies: AppDependencies,
    settings: Settings,
) -> FastAPI:
    return create_app(
        AppDependencies(
            settings=settings,
            unit_of_work=dependencies.unit_of_work,
            clock=dependencies.clock,
            init_data=dependencies.init_data,
            consent_copy=dependencies.consent_copy,
            erasure_journal=RefusingJournal(),
        )
    )
