"""`POST /v1/account/vault-reset` (§9.4): the server copy, dropped on purpose."""

from __future__ import annotations

import base64
import hashlib

import pytest
from sqlalchemy import Engine, text

from conftest import REPO_ROOT, Caller, FrozenClock, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()

KEY_A = "aa" * 32
KEY_B = "bb" * 32
CLIENT_TS = 1_768_435_200_000


def _change(record_key: str) -> dict[str, object]:
    return {
        "record_key": record_key,
        "client_ts_ms": CLIENT_TS,
        "payload_b64": base64.b64encode(b"ciphertext").decode(),
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


def test_a_reset_without_step_up_is_refused(
    session: Caller,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    clock.advance(minutes=11)

    refused = session.post("/v1/account/vault-reset")

    assert refused.status_code == 403
    assert refused.json() == {"error": "step_up_required"}
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM diary.vault_record")
            ).scalar_one()
            == 1
        )


def test_a_reset_drops_every_record_and_moves_all_three_counters(
    session: Caller,
    engine: Engine,
) -> None:
    session.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [_change(KEY_A), _change(KEY_B)]},
    )

    response = session.post("/v1/account/vault-reset")

    assert response.status_code == 200, response.text
    assert response.json() == {"new_revision": 3}
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM diary.vault_record")
            ).scalar_one()
            == 0
        )
        row = connection.execute(
            text(
                "SELECT current_revision, compacted_up_to, reset_revision "
                "FROM diary.vault_revision"
            )
        ).one()
    assert (row.current_revision, row.compacted_up_to, row.reset_revision) == (3, 3, 3)


def test_a_push_from_before_the_reset_is_refused_with_vault_reset(
    session: Caller,
) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    session.post("/v1/account/vault-reset")

    stale = session.post(
        "/v1/sync/push",
        {"base_revision": 1, "changes": [_change(KEY_A)]},
    )
    assert stale.status_code == 409
    assert stale.json() == {"error": "vault_reset"}


def test_the_reset_state_is_reported_before_anything_else(session: Caller) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    session.post("/v1/account/vault-reset")

    # Below the horizon the answer is 410 — a full resync, which returns the
    # reset state first because there is nothing else left to return.
    assert session.get("/v1/sync/pull?since=1").status_code == 410
    page = session.get("/v1/sync/pull?since=2").json()
    assert page["records"] == []
    assert page["current_revision"] == 2
    assert page["reset"] is False


def test_a_fresh_push_after_the_reset_starts_from_the_new_revision(
    session: Caller,
) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    reset = session.post("/v1/account/vault-reset").json()

    response = session.post(
        "/v1/sync/push",
        {"base_revision": reset["new_revision"], "changes": [_change(KEY_A)]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"new_revision": 3}


def test_the_reset_leaves_the_envelope_alone(
    session: Caller,
    engine: Engine,
) -> None:
    """Crypto-erasure belongs to phase 3; the new envelope arrives by re-key."""
    session.post(
        "/v1/sync/key",
        {
            "mode": "rewrap",
            "expected_wrap_version": 0,
            "wrapped_dek": base64.b64encode(b"envelope").decode(),
            "kdf": "pbkdf2-sha256",
            "kdf_params": {"iterations": 1_000_000, "salt_hex": "00" * 16},
        },
    )

    session.post("/v1/account/vault-reset")

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM diary.vault_key")
            ).scalar_one()
            == 1
        )
