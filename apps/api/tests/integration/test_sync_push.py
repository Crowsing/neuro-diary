"""The push transaction of §9.1, end to end on a real PostgreSQL.

Every refusal asserts the stored state as well as the status code: §9.1
promises zero rows written, and a status code alone would not notice a partial
write.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from sqlalchemy import Engine, text

from conftest import REPO_ROOT, Caller, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()
CYCLE_SHA = hashlib.sha256((REGISTRY / "uk/cycle_sync/0.9.md").read_bytes()).hexdigest()
REMINDERS_SHA = hashlib.sha256(
    (REGISTRY / "uk/telegram_reminders/0.9.md").read_bytes()
).hexdigest()

KEY_A = "aa" * 32
KEY_B = "bb" * 32
KEY_C = "cc" * 32
CLIENT_TS = 1_768_435_200_000


def payload(text_body: str = "ciphertext") -> str:
    return base64.b64encode(text_body.encode()).decode()


def change(
    record_key: str,
    *,
    body: str | None = "ciphertext",
    tombstone: bool = False,
    client_ts_ms: int = CLIENT_TS,
) -> dict[str, object]:
    item: dict[str, object] = {
        "record_key": record_key,
        "client_ts_ms": client_ts_ms,
        "tombstone": tombstone,
    }
    if not tombstone:
        item["payload_b64"] = payload(body or "ciphertext")
    return item


def authenticate(caller: Caller, **grant: object) -> str:
    body: dict[str, object] = {"init_data": sign_init_data()}
    if grant:
        body["grant"] = grant
    response = caller.post("/v1/auth/telegram", body)
    assert response.status_code == 200, response.text
    token = str(response.json()["session_token"])
    caller.token = token
    return token


@pytest.fixture
def session(caller: Caller) -> Caller:
    authenticate(
        caller,
        kind="health_sync",
        text_version="health_sync@0.9",
        text_sha256=HEALTH_SYNC_SHA,
    )
    return caller


def _rows(engine: Engine, statement: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement)).scalar_one())


def test_the_first_push_creates_the_counters_and_returns_the_new_revision(
    session: Caller,
    engine: Engine,
) -> None:
    response = session.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [change(KEY_A)]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"new_revision": 1}
    assert _rows(engine, "SELECT count(*) FROM diary.vault_revision") == 1
    assert _rows(engine, "SELECT count(*) FROM diary.vault_record") == 1


def test_every_record_in_one_push_gets_its_own_revision(
    session: Caller,
    engine: Engine,
) -> None:
    # ux_vault_rev is UNIQUE on (account_id, revision): one revision per batch
    # would fail on the second record, so this is a schema requirement rather
    # than a nicety.
    response = session.post(
        "/v1/sync/push",
        {
            "base_revision": 0,
            "changes": [change(KEY_A), change(KEY_B), change(KEY_C)],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"new_revision": 3}
    with engine.connect() as connection:
        revisions = [
            row[0]
            for row in connection.execute(
                text("SELECT revision FROM diary.vault_record ORDER BY revision")
            ).fetchall()
        ]
    assert revisions == [1, 2, 3]


def test_revisions_are_strictly_increasing_across_many_pushes(
    session: Caller,
    engine: Engine,
) -> None:
    base = 0
    for index in range(25):
        response = session.post(
            "/v1/sync/push",
            {
                "base_revision": base,
                "changes": [change(f"{index:02x}" * 32)],
            },
        )
        assert response.status_code == 200, response.text
        new_revision = int(response.json()["new_revision"])
        assert new_revision == base + 1
        base = new_revision
    with engine.connect() as connection:
        revisions = [
            row[0]
            for row in connection.execute(
                text("SELECT revision FROM diary.vault_record ORDER BY revision")
            ).fetchall()
        ]
    assert revisions == list(range(1, 26))


def test_a_push_without_health_sync_is_refused_and_writes_nothing(
    caller: Caller,
    engine: Engine,
) -> None:
    authenticate(
        caller,
        kind="health_sync",
        text_version="health_sync@0.9",
        text_sha256=HEALTH_SYNC_SHA,
    )
    # A second consent keeps the account alive: revoking the last one erases it
    # in the same transaction (§4.3), and the answer would be 401, not 403.
    reminders = caller.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
            "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
        },
    )
    assert reminders.status_code == 201, reminders.text
    assert (
        caller.post("/v1/consents/revoke", {"kind": "health_sync"}).status_code == 200
    )

    response = caller.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [change(KEY_A)]},
    )
    assert response.status_code == 403
    assert response.json() == {"error": "consent_required"}
    assert _rows(engine, "SELECT count(*) FROM diary.vault_record") == 0


def test_a_push_without_a_session_is_unauthenticated(caller: Caller) -> None:
    response = caller.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [change(KEY_A)]},
        token="not-a-session",
    )
    assert response.status_code == 401


def test_a_stale_base_revision_reports_the_conflicting_keys_and_writes_nothing(
    session: Caller,
    engine: Engine,
) -> None:
    assert (
        session.post(
            "/v1/sync/push",
            {"base_revision": 0, "changes": [change(KEY_A, body="first")]},
        ).status_code
        == 200
    )

    response = session.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [change(KEY_A, body="second")]},
    )
    assert response.status_code == 409
    assert response.json() == {"reason": "conflict", "conflict_keys": [KEY_A]}

    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT payload FROM diary.vault_record")
        ).scalar_one()
    assert bytes(stored) == b"first"


def test_a_conflict_names_only_the_keys_that_actually_moved(
    session: Caller,
) -> None:
    assert (
        session.post(
            "/v1/sync/push",
            {"base_revision": 0, "changes": [change(KEY_A)]},
        ).status_code
        == 200
    )
    response = session.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [change(KEY_A), change(KEY_B)]},
    )
    assert response.status_code == 409
    assert response.json()["conflict_keys"] == [KEY_A]


def test_a_tombstone_clears_the_payload_in_the_same_statement(
    session: Caller,
    engine: Engine,
) -> None:
    assert (
        session.post(
            "/v1/sync/push",
            {"base_revision": 0, "changes": [change(KEY_A)]},
        ).status_code
        == 200
    )
    assert (
        session.post(
            "/v1/sync/push",
            {"base_revision": 1, "changes": [change(KEY_A, tombstone=True)]},
        ).status_code
        == 200
    )
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT payload, payload_size, deleted FROM diary.vault_record")
        ).one()
    assert row.payload is None
    assert row.payload_size == 0
    assert row.deleted is True


def test_a_tombstone_is_not_silently_overwritten_by_an_older_update(
    session: Caller,
    engine: Engine,
) -> None:
    """The optimistic protocol, not a special case, is what protects a delete.

    A device that deleted a record moves the revision; another device that
    still holds the old base_revision cannot resurrect it without seeing a 409
    first.
    """
    assert (
        session.post(
            "/v1/sync/push",
            {"base_revision": 0, "changes": [change(KEY_A)]},
        ).status_code
        == 200
    )
    assert (
        session.post(
            "/v1/sync/push",
            {"base_revision": 1, "changes": [change(KEY_A, tombstone=True)]},
        ).status_code
        == 200
    )

    response = session.post(
        "/v1/sync/push",
        {"base_revision": 1, "changes": [change(KEY_A, body="resurrected")]},
    )
    assert response.status_code == 409
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT payload, deleted FROM diary.vault_record")
        ).one()
    assert row.payload is None
    assert row.deleted is True


def test_a_payload_over_sixty_four_kibibytes_is_refused(
    session: Caller,
    engine: Engine,
) -> None:
    oversized = base64.b64encode(b"x" * 65_537).decode()
    response = session.post(
        "/v1/sync/push",
        {
            "base_revision": 0,
            "changes": [
                {
                    "record_key": KEY_A,
                    "client_ts_ms": CLIENT_TS,
                    "payload_b64": oversized,
                    "tombstone": False,
                }
            ],
        },
    )
    assert response.status_code == 413
    assert response.json() == {"error": "payload_too_large"}
    assert _rows(engine, "SELECT count(*) FROM diary.vault_record") == 0


def test_a_batch_over_two_hundred_records_is_refused_by_the_contract(
    session: Caller,
    engine: Engine,
) -> None:
    changes = [change(f"{index:064x}") for index in range(201)]
    response = session.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": changes},
    )
    assert response.status_code == 422
    assert _rows(engine, "SELECT count(*) FROM diary.vault_record") == 0


def test_a_corrupted_payload_is_refused_before_the_database(
    session: Caller,
    engine: Engine,
) -> None:
    response = session.post(
        "/v1/sync/push",
        {
            "base_revision": 0,
            "changes": [
                {
                    "record_key": KEY_A,
                    "client_ts_ms": CLIENT_TS,
                    "payload_b64": "not base64!!",
                    "tombstone": False,
                }
            ],
        },
    )
    assert response.status_code == 413
    assert _rows(engine, "SELECT count(*) FROM diary.vault_record") == 0


def test_a_named_cycle_key_without_cycle_sync_is_refused(
    caller: Caller,
    engine: Engine,
) -> None:
    """§9.7: the server gates on the one key the client named at grant time."""
    authenticate(
        caller,
        kind="health_sync",
        text_version="health_sync@0.9",
        text_sha256=HEALTH_SYNC_SHA,
    )
    granted = caller.post(
        "/v1/consents",
        {
            "kind": "cycle_sync",
            "text_version": "cycle_sync@0.9",
            "text_sha256": CYCLE_SHA,
            "record_key_cycle": KEY_C,
        },
    )
    assert granted.status_code == 201, granted.text
    assert caller.post("/v1/consents/revoke", {"kind": "cycle_sync"}).status_code == 200

    refused = caller.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [change(KEY_C)]},
    )
    assert refused.status_code == 409
    assert refused.json() == {"error": "consent_precondition"}
    assert _rows(engine, "SELECT count(*) FROM diary.vault_record") == 0

    # Any other record still syncs: only the named domain is gated.
    allowed = caller.post(
        "/v1/sync/push",
        {"base_revision": 0, "changes": [change(KEY_A)]},
    )
    assert allowed.status_code == 200, allowed.text


def test_the_error_body_never_echoes_a_payload(session: Caller) -> None:
    response = session.post(
        "/v1/sync/push",
        {
            "base_revision": 0,
            "changes": [
                {
                    "record_key": KEY_A,
                    "client_ts_ms": CLIENT_TS,
                    "payload_b64": payload("2026-01-15 headache"),
                    "tombstone": True,
                }
            ],
        },
    )
    assert response.status_code == 422
    assert "headache" not in response.text
    assert "2026-01-15" not in response.text
