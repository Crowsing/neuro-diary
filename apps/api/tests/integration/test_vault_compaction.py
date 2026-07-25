"""The compactor and the horizon it moves (§6.4, §9.4)."""

from __future__ import annotations

import base64
import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import Engine, text

from app.domain.vault import TOMBSTONE_TTL
from app.infra.db.engine import SqlUnitOfWorkFactory
from app.workers.vault_compactor import VaultCompactor

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


def _tombstone(record_key: str) -> dict[str, object]:
    return {"record_key": record_key, "client_ts_ms": CLIENT_TS, "tombstone": True}


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


@pytest.fixture
def compactor(
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
) -> VaultCompactor:
    return VaultCompactor(unit_of_work, clock)


def _age_tombstones(engine: Engine, *, by: timedelta) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE diary.vault_record SET updated_at = updated_at - :age "
                "WHERE deleted"
            ),
            {"age": by},
        )


def _counters(engine: Engine) -> tuple[int, int, int]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT current_revision, compacted_up_to, reset_revision "
                "FROM diary.vault_revision"
            )
        ).one()
    return (row.current_revision, row.compacted_up_to, row.reset_revision)


def test_a_fresh_tombstone_survives_the_compactor(
    session: Caller,
    engine: Engine,
    compactor: VaultCompactor,
) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    session.post("/v1/sync/push", {"base_revision": 1, "changes": [_tombstone(KEY_A)]})

    result = compactor.run_once()

    assert result.tombstones_removed == 0
    assert _counters(engine)[1] == 0


def test_tombstones_past_the_ttl_are_deleted_and_the_horizon_advances(
    session: Caller,
    engine: Engine,
    compactor: VaultCompactor,
) -> None:
    session.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [_change(KEY_A), _change(KEY_B)]},
    )
    session.post("/v1/sync/push", {"base_revision": 2, "changes": [_tombstone(KEY_A)]})
    _age_tombstones(engine, by=TOMBSTONE_TTL + timedelta(days=1))

    result = compactor.run_once()

    assert result.accounts_compacted == 1
    assert result.tombstones_removed == 1
    current, compacted, _ = _counters(engine)
    assert current == 3
    assert compacted == 3
    with engine.connect() as connection:
        keys = {
            bytes(row[0]).hex()
            for row in connection.execute(
                text("SELECT record_key FROM diary.vault_record")
            ).fetchall()
        }
    assert keys == {KEY_B}


def test_the_horizon_never_goes_backwards(
    session: Caller,
    engine: Engine,
    compactor: VaultCompactor,
) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    session.post("/v1/sync/push", {"base_revision": 1, "changes": [_tombstone(KEY_A)]})
    _age_tombstones(engine, by=TOMBSTONE_TTL + timedelta(days=1))
    with engine.begin() as connection:
        connection.execute(text("UPDATE diary.vault_revision SET compacted_up_to = 2"))

    compactor.run_once()

    assert _counters(engine)[1] == 2


def test_a_push_below_the_horizon_is_gone(
    session: Caller,
    engine: Engine,
    compactor: VaultCompactor,
) -> None:
    session.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [_change(KEY_A), _change(KEY_B)]},
    )
    session.post("/v1/sync/push", {"base_revision": 2, "changes": [_tombstone(KEY_A)]})
    _age_tombstones(engine, by=TOMBSTONE_TTL + timedelta(days=1))
    compactor.run_once()

    stale = session.post(
        "/v1/sync/push",
        {"base_revision": 1, "changes": [_change(KEY_B)]},
    )
    assert stale.status_code == 410
    assert stale.json() == {"error": "gone"}

    fresh = session.post(
        "/v1/sync/push",
        {"base_revision": 3, "changes": [_change(KEY_B)]},
    )
    assert fresh.status_code == 200, fresh.text


def test_the_compactor_leaves_live_records_alone(
    session: Caller,
    engine: Engine,
    compactor: VaultCompactor,
) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE diary.vault_record SET updated_at = updated_at - :age"),
            {"age": TOMBSTONE_TTL * 2},
        )

    result = compactor.run_once()

    assert result.tombstones_removed == 0
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM diary.vault_record")
            ).scalar_one()
            == 1
        )


def test_compaction_is_idempotent(
    session: Caller,
    engine: Engine,
    compactor: VaultCompactor,
) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    session.post("/v1/sync/push", {"base_revision": 1, "changes": [_tombstone(KEY_A)]})
    _age_tombstones(engine, by=TOMBSTONE_TTL + timedelta(days=1))

    first = compactor.run_once()
    second = compactor.run_once()

    assert first.tombstones_removed == 1
    assert second.tombstones_removed == 0
    assert _counters(engine)[1] == 2
