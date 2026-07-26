"""§6.4 code `security_reset`: the two moments a re-key has to be journalled.

§7 orders a re-key as `vault-reset → upload → envelope`, so the operation is
split in time and a backup can be taken in the middle of it. That is why this
code has two writers rather than one:

* a backup taken **before** the vault-reset holds the old envelope *and* the
  old ciphertext, so after a restore the old passphrase opens the live service
  again. The entry written by the vault-reset has `at >= t_b` and reconciles it;
* a backup taken **between** the vault-reset and the envelope write still holds
  the old envelope — `vault_reset` leaves `vault_key` alone on purpose — while
  the records are already gone. The vault-reset's own entry is now *older* than
  the restore point and would never be reconciled, so only the entry written by
  `POST /v1/sync/key` closes that window.

The server also does not enforce §7's order: a client may re-key without a
preceding vault-reset, and then the second writer is the only one there is.

The price is named rather than hidden: one re-key leaves two entries. Both
reconcile to the same idempotent action, so the duplicate costs one no-op.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from conftest import REPO_ROOT, Caller, FrozenClock, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()

FIRST = base64.b64encode(b"first-envelope").decode()
SECOND = base64.b64encode(b"second-envelope").decode()
KDF_PARAMS: dict[str, Any] = {"iterations": 1_000_000, "salt_hex": "00" * 16}
KEY_A = "aa" * 32
CLIENT_TS = 1_768_435_200_000


def _write(mode: str, *, expected: int, envelope: str) -> dict[str, object]:
    return {
        "mode": mode,
        "expected_wrap_version": expected,
        "wrapped_dek": envelope,
        "kdf": "pbkdf2-sha256",
        "kdf_params": KDF_PARAMS,
    }


def _sign_in(caller: Caller) -> None:
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


@pytest.fixture
def session(caller: Caller) -> Caller:
    _sign_in(caller)
    return caller


def _push(caller: Caller) -> None:
    response = caller.post(
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
    assert response.status_code == 200, response.text


def _scopes(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return list(
            connection.execute(text("SELECT scope FROM diary.erasure_job ORDER BY id"))
            .scalars()
            .all()
        )


def _envelope(engine: Engine) -> tuple[int, int]:
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT key_version, wrap_version FROM diary.vault_key")
        ).one()
    return (row.key_version, row.wrap_version)


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
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


# ------------------------------------------------------------- what is written


def test_a_vault_reset_is_journalled_before_it_happens(
    session: Caller,
    engine: Engine,
    clock: FrozenClock,
) -> None:
    session.post("/v1/sync/key", _write("rewrap", expected=0, envelope=FIRST))
    _push(session)

    response = session.post("/v1/account/vault-reset")

    assert response.status_code == 200, response.text
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT scope, requested_at, completed_at, deletion_copy_version"
                " FROM diary.erasure_job"
            )
        ).one()
    assert row.scope == "security_reset"
    assert row.deletion_copy_version == "deletion@1.0"
    assert row.requested_at == clock.now()
    assert row.completed_at == clock.now()


def test_a_rekey_is_journalled_too(
    session: Caller,
    engine: Engine,
    clock: FrozenClock,
) -> None:
    session.post("/v1/sync/key", _write("rewrap", expected=0, envelope=FIRST))

    response = session.post(
        "/v1/sync/key", _write("rekey", expected=1, envelope=SECOND)
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"key_version": 2, "wrap_version": 2}
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT scope, requested_at, completed_at FROM diary.erasure_job")
        ).one()
    assert row.scope == "security_reset"
    assert row.requested_at == clock.now()
    assert row.completed_at == clock.now()


def test_the_ordered_rekey_of_section_seven_leaves_two_entries(
    session: Caller,
    engine: Engine,
) -> None:
    """The whole reason this code has two writers.

    A backup taken between these two calls is only reconcilable through the
    second entry: the first one is older than the restore point, and the old
    envelope is still sitting in `vault_key` at that moment.
    """
    session.post("/v1/sync/key", _write("rewrap", expected=0, envelope=FIRST))
    _push(session)

    session.post("/v1/account/vault-reset")
    session.post("/v1/sync/key", _write("rekey", expected=1, envelope=SECOND))

    assert _scopes(engine) == ["security_reset", "security_reset"]


# ---------------------------------------------------------- what is not written


def test_the_first_envelope_records_nothing(
    session: Caller,
    engine: Engine,
) -> None:
    """There is no old envelope to invalidate, in either mode.

    A vault whose `vault_key` row does not exist has never had a passphrase, so
    no restore can bring an old one back.
    """
    for mode in ("rewrap", "rekey"):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM diary.vault_key"))
        response = session.post(
            "/v1/sync/key", _write(mode, expected=0, envelope=FIRST)
        )
        assert response.status_code == 200, response.text

    assert _scopes(engine) == []


def test_a_rewrap_records_nothing(session: Caller, engine: Engine) -> None:
    """§7: changing the passphrase re-wraps the same `R`. Nothing is invalidated
    — that is exactly why re-key is a separate, explicit action."""
    session.post("/v1/sync/key", _write("rewrap", expected=0, envelope=FIRST))

    response = session.post(
        "/v1/sync/key", _write("rewrap", expected=1, envelope=SECOND)
    )

    assert response.status_code == 200, response.text
    assert _scopes(engine) == []


def test_a_wrap_conflict_records_nothing(session: Caller, engine: Engine) -> None:
    """The journal entry goes after the CAS check and before the write.

    A 409 means the envelope was not touched, and an entry claiming otherwise
    would send the runbook chasing a re-key that never happened.
    """
    session.post("/v1/sync/key", _write("rewrap", expected=0, envelope=FIRST))

    response = session.post(
        "/v1/sync/key", _write("rekey", expected=99, envelope=SECOND)
    )

    assert response.status_code == 409, response.text
    assert _scopes(engine) == []
    assert _envelope(engine) == (1, 1)


# --------------------------------------------------------- journal comes first


def test_an_unwritable_journal_stops_the_vault_reset(
    refusing_journal_api: FastAPI,
    engine: Engine,
) -> None:
    caller = Caller(refusing_journal_api)
    _sign_in(caller)
    caller.post("/v1/sync/key", _write("rewrap", expected=0, envelope=FIRST))
    _push(caller)
    before = _counters(engine)

    with pytest.raises(RuntimeError):
        caller.post("/v1/account/vault-reset")

    assert _count(engine, "diary.vault_record") == 1
    assert _counters(engine) == before


def test_an_unwritable_journal_stops_the_rekey(
    refusing_journal_api: FastAPI,
    engine: Engine,
) -> None:
    caller = Caller(refusing_journal_api)
    _sign_in(caller)
    caller.post("/v1/sync/key", _write("rewrap", expected=0, envelope=FIRST))

    with pytest.raises(RuntimeError):
        caller.post("/v1/sync/key", _write("rekey", expected=1, envelope=SECOND))

    # The envelope is untouched: neither version moved, and the bytes are the
    # first ones. A re-key that half-happened would be unrecoverable, because
    # `R` is not persisted anywhere.
    assert _envelope(engine) == (1, 1)
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT wrapped_dek FROM diary.vault_key")
        ).scalar_one()
    assert bytes(stored) == base64.b64decode(FIRST)
