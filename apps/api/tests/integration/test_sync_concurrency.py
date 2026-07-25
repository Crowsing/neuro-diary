"""Concurrency of §9.1 and §7 on a real PostgreSQL.

These are the tests the whole lock order exists for. They run two requests in
parallel over two connections; anything that passes them by accident would
still fail under `-p no:randomly` repetition, because the assertions are about
the *pair* of answers, not about either one.
"""

from __future__ import annotations

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import Engine, text

from conftest import REPO_ROOT, Caller, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()

KEY_A = "aa" * 32
KEY_B = "bb" * 32
CLIENT_TS = 1_768_435_200_000
ENVELOPE = base64.b64encode(b"envelope").decode()
KDF_PARAMS: dict[str, Any] = {"iterations": 1_000_000, "salt_hex": "00" * 16}


def _change(record_key: str, body: str) -> dict[str, object]:
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


def _in_parallel(*calls: tuple[str, dict[str, object]], caller: Caller) -> list[Any]:
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(caller.post, path, body) for path, body in calls]
        return [future.result() for future in futures]


def test_two_concurrent_pushes_of_one_key_give_exactly_one_200_and_one_409(
    session: Caller,
    engine: Engine,
) -> None:
    body_a = {"base_revision": 0, "changes": [_change(KEY_A, "from-a")]}
    body_b = {"base_revision": 0, "changes": [_change(KEY_A, "from-b")]}

    responses = _in_parallel(
        ("/v1/sync/push", body_a),
        ("/v1/sync/push", body_b),
        caller=session,
    )

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 409]
    conflict = next(r for r in responses if r.status_code == 409).json()
    assert conflict == {"reason": "conflict", "conflict_keys": [KEY_A]}

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT payload, revision FROM diary.vault_record")
        ).fetchall()
    assert len(rows) == 1
    assert bytes(rows[0].payload) in (b"from-a", b"from-b")
    assert rows[0].revision == 1


def test_two_concurrent_pushes_of_different_keys_lose_no_revision(
    session: Caller,
    engine: Engine,
) -> None:
    """The conflict check is per key, so both pushes succeed.

    They still serialize on the counter lock, which is what gives them two
    distinct revisions instead of one lost update.
    """
    responses = _in_parallel(
        ("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A, "a")]}),
        ("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_B, "b")]}),
        caller=session,
    )
    assert sorted(r.status_code for r in responses) == [200, 200]

    with engine.connect() as connection:
        revisions = sorted(
            row[0]
            for row in connection.execute(
                text("SELECT revision FROM diary.vault_record")
            ).fetchall()
        )
    assert revisions == [1, 2]


def test_a_second_push_after_a_conflict_succeeds_with_the_fresh_revision(
    session: Caller,
) -> None:
    _in_parallel(
        ("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A, "a")]}),
        ("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A, "b")]}),
        caller=session,
    )
    retried = session.post(
        "/v1/sync/push",
        {"base_revision": 1, "changes": [_change(KEY_A, "merged")]},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json() == {"new_revision": 2}


def test_two_concurrent_rewraps_give_exactly_one_200_and_one_409_on_wrap_version(
    session: Caller,
    engine: Engine,
) -> None:
    """§7: the CAS runs on wrap_version, not key_version.

    A CAS on key_version would let both re-wraps through, and one of the two
    new passphrases would stop opening the vault without a word to anyone.
    """
    first = session.post(
        "/v1/sync/key",
        {
            "mode": "rewrap",
            "expected_wrap_version": 0,
            "wrapped_dek": ENVELOPE,
            "kdf": "pbkdf2-sha256",
            "kdf_params": KDF_PARAMS,
        },
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"key_version": 1, "wrap_version": 1}

    body = {
        "mode": "rewrap",
        "expected_wrap_version": 1,
        "wrapped_dek": base64.b64encode(b"second-envelope").decode(),
        "kdf": "pbkdf2-sha256",
        "kdf_params": KDF_PARAMS,
    }
    responses = _in_parallel(
        ("/v1/sync/key", body),
        ("/v1/sync/key", dict(body, wrapped_dek=ENVELOPE)),
        caller=session,
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    conflict = next(r for r in responses if r.status_code == 409).json()
    assert conflict == {"current_wrap_version": 2}

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT key_version, wrap_version FROM diary.vault_key")
        ).one()
    assert row.key_version == 1
    assert row.wrap_version == 2
