"""The envelope endpoints of §7: step-up, CAS, and the previous envelope."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest
from sqlalchemy import Engine, text

from conftest import REPO_ROOT, Caller, FrozenClock, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()

ENVELOPE = base64.b64encode(b"first-envelope").decode()
SECOND = base64.b64encode(b"second-envelope").decode()
THIRD = base64.b64encode(b"third-envelope").decode()
KDF_PARAMS: dict[str, Any] = {"iterations": 1_000_000, "salt_hex": "00" * 16}


def _write(
    mode: str = "rewrap",
    *,
    expected: int = 0,
    envelope: str = ENVELOPE,
) -> dict[str, object]:
    return {
        "mode": mode,
        "expected_wrap_version": expected,
        "wrapped_dek": envelope,
        "kdf": "pbkdf2-sha256",
        "kdf_params": KDF_PARAMS,
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


def test_a_write_without_step_up_is_refused(
    session: Caller,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    """§8: a stale session cannot touch the envelope, in either mode.

    The server is zero-knowledge, so it cannot tell an envelope from arbitrary
    bytes — a mistaken overwrite is unrecoverable, which is why this is the one
    write that always needs a fresh initData.
    """
    clock.advance(minutes=11)

    for mode in ("rewrap", "rekey"):
        refused = session.post("/v1/sync/key", _write(mode))
        assert refused.status_code == 403
        assert refused.json() == {"error": "step_up_required"}

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM diary.vault_key")
            ).scalar_one()
            == 0
        )


def test_the_first_envelope_is_written_with_version_one(session: Caller) -> None:
    response = session.post("/v1/sync/key", _write())
    assert response.status_code == 200, response.text
    assert response.json() == {"key_version": 1, "wrap_version": 1}


def test_a_first_write_that_expects_an_existing_envelope_is_refused(
    session: Caller,
) -> None:
    response = session.post("/v1/sync/key", _write(expected=1))
    assert response.status_code == 409
    assert response.json() == {"current_wrap_version": 0}


def test_a_rewrap_bumps_wrap_version_and_leaves_key_version(
    session: Caller,
) -> None:
    session.post("/v1/sync/key", _write())
    response = session.post("/v1/sync/key", _write(expected=1, envelope=SECOND))
    assert response.status_code == 200, response.text
    assert response.json() == {"key_version": 1, "wrap_version": 2}


def test_a_rekey_bumps_both_versions(session: Caller) -> None:
    session.post("/v1/sync/key", _write())
    response = session.post(
        "/v1/sync/key", _write("rekey", expected=1, envelope=SECOND)
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"key_version": 2, "wrap_version": 2}


def test_a_stale_expected_wrap_version_reports_the_current_one(
    session: Caller,
) -> None:
    session.post("/v1/sync/key", _write())
    session.post("/v1/sync/key", _write(expected=1, envelope=SECOND))

    response = session.post("/v1/sync/key", _write(expected=1, envelope=THIRD))
    assert response.status_code == 409
    assert response.json() == {"current_wrap_version": 2}


def test_reading_a_missing_envelope_is_a_plain_not_found(session: Caller) -> None:
    response = session.get("/v1/sync/key")
    assert response.status_code == 404
    assert response.json() == {"error": "no_vault_key"}


def test_reading_returns_the_envelope_and_its_parameters(session: Caller) -> None:
    session.post("/v1/sync/key", _write())
    body = session.get("/v1/sync/key").json()
    assert body["wrapped_dek"] == ENVELOPE
    assert body["kdf"] == "pbkdf2-sha256"
    assert body["kdf_params"] == KDF_PARAMS
    assert body["key_version"] == 1
    assert body["wrap_version"] == 1


def test_the_previous_envelope_is_only_returned_with_step_up(
    session: Caller,
    clock: FrozenClock,
) -> None:
    session.post("/v1/sync/key", _write())
    session.post("/v1/sync/key", _write(expected=1, envelope=SECOND))

    fresh = session.get("/v1/sync/key").json()
    assert fresh["wrapped_dek_prev"] == ENVELOPE

    clock.advance(minutes=11)
    stale = session.get("/v1/sync/key").json()
    assert stale["wrapped_dek_prev"] is None
    assert stale["wrapped_dek"] == SECOND


def test_the_previous_envelope_is_not_rewritten_more_than_once_a_day(
    session: Caller,
    engine: Engine,
) -> None:
    """§7: keeping only the newest previous envelope would let two quick
    re-wraps in a row erase the one envelope worth keeping."""
    session.post("/v1/sync/key", _write())
    session.post("/v1/sync/key", _write(expected=1, envelope=SECOND))
    session.post("/v1/sync/key", _write(expected=2, envelope=THIRD))

    body = session.get("/v1/sync/key").json()
    assert body["wrapped_dek"] == THIRD
    assert body["wrapped_dek_prev"] == ENVELOPE
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT wrap_version_prev FROM diary.vault_key")
            ).scalar_one()
            == 1
        )


def test_the_previous_envelope_expires_after_seven_days(
    session: Caller,
    clock: FrozenClock,
) -> None:
    session.post("/v1/sync/key", _write())
    session.post("/v1/sync/key", _write(expected=1, envelope=SECOND))

    # Кожен крок у часі потребує свіжо підписаного initData: після 24 годин
    # старий стає auth_stale, і сесія так і лишилася б без step-up.
    def _step_up(nonce: str) -> None:
        response = session.post(
            "/v1/auth/telegram",
            {"init_data": sign_init_data(auth_date=clock.now(), nonce=nonce)},
        )
        assert response.status_code == 200, response.text
        session.token = str(response.json()["session_token"])

    clock.advance(days=6, hours=23)
    _step_up("fresh-1")
    assert session.get("/v1/sync/key").json()["wrapped_dek_prev"] == ENVELOPE

    clock.advance(hours=2)
    _step_up("fresh-2")
    assert session.get("/v1/sync/key").json()["wrapped_dek_prev"] is None


def test_the_envelope_endpoints_need_a_session(caller: Caller) -> None:
    assert caller.get("/v1/sync/key", token="nope").status_code == 401
    assert caller.post("/v1/sync/key", _write(), token="nope").status_code == 401
