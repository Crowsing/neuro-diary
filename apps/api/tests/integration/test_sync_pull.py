"""Pull, the compaction horizon, and the neutral consent signal (§9.2, §9.4)."""

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
CYCLE_SHA = hashlib.sha256((REGISTRY / "uk/cycle_sync/0.9.md").read_bytes()).hexdigest()

KEY_A = "aa" * 32
KEY_B = "bb" * 32
KEY_C = "cc" * 32
CLIENT_TS = 1_768_435_200_000


def _change(record_key: str, body: str = "ciphertext") -> dict[str, object]:
    return {
        "record_key": record_key,
        "client_ts_ms": CLIENT_TS,
        "payload_b64": base64.b64encode(body.encode()).decode(),
        "tombstone": False,
    }


def _tombstone(record_key: str) -> dict[str, object]:
    return {
        "record_key": record_key,
        "client_ts_ms": CLIENT_TS,
        "tombstone": True,
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


def _pull(caller: Caller, query: str = "") -> Any:
    return caller.get(f"/v1/sync/pull{query}")


def test_pull_returns_an_empty_page_for_a_fresh_vault(session: Caller) -> None:
    response = _pull(session)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "records": [],
        "next_since": 0,
        "current_revision": 0,
        "reset": False,
        "consent_state_changed": False,
    }


def test_pull_pages_by_revision_cursor(session: Caller) -> None:
    assert (
        session.post(
            "/v1/sync/push",
            {
                "base_revision": 0,
                "changes": [_change(KEY_A), _change(KEY_B), _change(KEY_C)],
            },
        ).status_code
        == 200
    )

    first = _pull(session, "?since=0&limit=2").json()
    assert [record["revision"] for record in first["records"]] == [1, 2]
    assert first["next_since"] == 2
    assert first["current_revision"] == 3

    second = _pull(session, f"?since={first['next_since']}&limit=2").json()
    assert [record["revision"] for record in second["records"]] == [3]
    assert second["next_since"] == 3


def test_pull_returns_the_payload_verbatim(session: Caller) -> None:
    session.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [_change(KEY_A, "opaque-bytes")]},
    )
    record = _pull(session).json()["records"][0]
    assert base64.b64decode(record["payload_b64"]) == b"opaque-bytes"
    assert record["record_key"] == KEY_A
    assert record["tombstone"] is False
    assert record["client_ts_ms"] == CLIENT_TS


def test_pull_reports_a_tombstone_without_a_payload(session: Caller) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    session.post("/v1/sync/push", {"base_revision": 1, "changes": [_tombstone(KEY_A)]})

    record = _pull(session).json()["records"][0]
    assert record["tombstone"] is True
    assert record["payload_b64"] is None


def test_pull_below_the_compaction_horizon_is_gone(
    session: Caller,
    engine: Engine,
) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    session.post("/v1/sync/push", {"base_revision": 1, "changes": [_change(KEY_B)]})
    with engine.begin() as connection:
        connection.execute(text("UPDATE diary.vault_revision SET compacted_up_to = 1"))

    assert _pull(session, "?since=0").status_code == 410
    assert _pull(session, "?since=1").status_code == 200


def test_pull_below_the_reset_revision_reports_the_reset(
    session: Caller,
    engine: Engine,
) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    with engine.begin() as connection:
        connection.execute(text("UPDATE diary.vault_revision SET reset_revision = 1"))

    assert _pull(session, "?since=0").json()["reset"] is True
    assert _pull(session, "?since=1").json()["reset"] is False


def test_pull_signals_a_consent_change_without_naming_the_kind(
    session: Caller,
    engine: Engine,
) -> None:
    session.post("/v1/sync/push", {"base_revision": 0, "changes": [_change(KEY_A)]})
    with engine.begin() as connection:
        connection.execute(text("UPDATE diary.vault_revision SET consent_epoch = 3"))

    stale = _pull(session, "?since=0&consent_epoch=2")
    assert stale.json()["consent_state_changed"] is True
    assert "cycle" not in stale.text
    assert "health" not in stale.text

    caught_up = _pull(session, "?since=0&consent_epoch=3")
    assert caught_up.json()["consent_state_changed"] is False


def test_pull_without_health_sync_is_refused(
    caller: Caller,
    clock: FrozenClock,
) -> None:
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
    reminders = caller.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": hashlib.sha256(
                (REGISTRY / "uk/telegram_reminders/0.9.md").read_bytes()
            ).hexdigest(),
            "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
        },
    )
    assert reminders.status_code == 201, reminders.text
    caller.post("/v1/consents/revoke", {"kind": "health_sync"})

    refused = _pull(caller)
    assert refused.status_code == 403
    assert refused.json() == {"error": "consent_required"}


def test_a_cursor_beyond_the_page_limit_is_refused_by_the_contract(
    session: Caller,
) -> None:
    assert _pull(session, "?limit=501").status_code == 422
    assert _pull(session, "?since=-1").status_code == 422


def test_a_full_resync_does_not_resurrect_a_deleted_key(
    session: Caller,
    engine: Engine,
) -> None:
    """After compaction the deleted key is simply absent, not present again."""
    session.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [_change(KEY_A), _change(KEY_B)]},
    )
    session.post("/v1/sync/push", {"base_revision": 2, "changes": [_tombstone(KEY_A)]})
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM diary.vault_record WHERE deleted;
                UPDATE diary.vault_revision SET compacted_up_to = 3;
                """
            )
        )

    stale = _pull(session, "?since=1")
    assert stale.status_code == 410

    resync = _pull(session, "?since=3").json()
    assert [record["record_key"] for record in resync["records"]] == []

    full = _pull(session, "?since=3&limit=500")
    assert full.status_code == 200
    with engine.connect() as connection:
        keys = {
            bytes(row[0]).hex()
            for row in connection.execute(
                text("SELECT record_key FROM diary.vault_record")
            ).fetchall()
        }
    assert keys == {KEY_B}
