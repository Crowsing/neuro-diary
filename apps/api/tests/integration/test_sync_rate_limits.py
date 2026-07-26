"""Per-account windows of §11, in PostgreSQL and without Redis."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.domain.rate_limits import BUDGETS, RateBucket

from conftest import REPO_ROOT, Caller, FrozenClock, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()

KEY_READS_PER_HOUR = BUDGETS[RateBucket.KEY_READ].limit
SYNC_REQUESTS_PER_MINUTE = BUDGETS[RateBucket.SYNC].limit

KEY_A = "aa" * 32
CLIENT_TS = 1_768_435_200_000
KDF_PARAMS: dict[str, Any] = {"iterations": 1_000_000, "salt_hex": "00" * 16}


def _change(record_key: str = KEY_A, size: int = 16) -> dict[str, object]:
    return {
        "record_key": record_key,
        "client_ts_ms": CLIENT_TS,
        "payload_b64": base64.b64encode(b"x" * size).decode(),
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


def test_the_sixty_first_sync_request_in_a_minute_is_rate_limited(
    session: Caller,
) -> None:
    for _ in range(SYNC_REQUESTS_PER_MINUTE):
        assert session.get("/v1/sync/pull").status_code == 200

    refused = session.get("/v1/sync/pull")
    assert refused.status_code == 429
    assert refused.json() == {"error": "rate_limited"}
    assert int(refused.headers["Retry-After"]) > 0


def test_the_window_rolls_and_the_budget_returns(
    session: Caller,
    clock: FrozenClock,
) -> None:
    for _ in range(SYNC_REQUESTS_PER_MINUTE):
        session.get("/v1/sync/pull")
    assert session.get("/v1/sync/pull").status_code == 429

    clock.advance(minutes=1)
    assert session.get("/v1/sync/pull").status_code == 200


def test_the_budget_is_consumed_even_when_the_push_is_refused(
    session: Caller,
    engine: Engine,
) -> None:
    """A 409 must not refund the window.

    Otherwise a client stuck in a conflict loop pushes for free exactly when it
    is misbehaving, and the limit protects nothing.
    """
    assert (
        session.post(
            "/v1/sync/push", {"base_revision": 0, "changes": [_change()]}
        ).status_code
        == 200
    )
    for _ in range(5):
        assert (
            session.post(
                "/v1/sync/push",
                {"base_revision": 0, "changes": [_change()]},
            ).status_code
            == 409
        )

    with engine.connect() as connection:
        used = connection.execute(
            text("SELECT used FROM diary.rate_window WHERE bucket = 'sync'")
        ).scalar_one()
    assert used == 6


def test_push_volume_over_five_mebibytes_a_minute_is_rate_limited(
    session: Caller,
) -> None:
    # 5 MiB budget, 64 KiB per record, 80 records per push → the fifth push
    # crosses the line.
    base = 0
    statuses = []
    for round_index in range(6):
        changes = [
            _change(f"{round_index:02x}{index:062x}", size=65_536)
            for index in range(20)
        ]
        response = session.post(
            "/v1/sync/push",
            {"base_revision": base, "changes": changes},
        )
        statuses.append(response.status_code)
        if response.status_code == 200:
            base = int(response.json()["new_revision"])
    assert 429 in statuses
    assert statuses.index(429) >= 4


def test_the_eleventh_key_read_in_an_hour_is_rate_limited(
    session: Caller,
    clock: FrozenClock,
) -> None:
    """§11: this endpoint hands out the envelope, the salt and the parameters,
    so an unlimited read turns a stolen session into an offline attack."""
    session.post(
        "/v1/sync/key",
        {
            "mode": "rewrap",
            "expected_wrap_version": 0,
            "wrapped_dek": base64.b64encode(b"envelope").decode(),
            "kdf": "pbkdf2-sha256",
            "kdf_params": KDF_PARAMS,
        },
    )

    for _ in range(KEY_READS_PER_HOUR):
        assert session.get("/v1/sync/key").status_code == 200

    refused = session.get("/v1/sync/key")
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0

    clock.advance(hours=1)
    assert session.get("/v1/sync/key").status_code == 200


def test_consents_do_not_share_the_sync_bucket(session: Caller) -> None:
    """§9.4 forbids pruning without a fresh consent answer, so exhausting the
    sync budget must not also block the answer that unblocks pruning."""
    for _ in range(SYNC_REQUESTS_PER_MINUTE + 1):
        session.get("/v1/sync/pull")

    assert session.get("/v1/sync/pull").status_code == 429
    assert session.get("/v1/consents").status_code == 200


def test_an_account_never_holds_more_than_three_rate_window_rows(
    session: Caller,
    engine: Engine,
) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change()]})
    session.get("/v1/sync/pull")
    session.get("/v1/sync/key")

    with engine.connect() as connection:
        buckets = {
            row[0]
            for row in connection.execute(
                text("SELECT bucket FROM diary.rate_window")
            ).fetchall()
        }
    assert buckets <= {"sync", "push_bytes", "key_read"}
    assert len(buckets) <= 3


def test_rate_window_rows_leave_with_the_account(
    session: Caller,
    engine: Engine,
    clock: FrozenClock,
) -> None:
    session.get("/v1/sync/pull")
    deleted = session.post("/v1/account/delete")
    assert deleted.status_code == 200, deleted.text

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM diary.rate_window")
            ).scalar_one()
            == 0
        )
