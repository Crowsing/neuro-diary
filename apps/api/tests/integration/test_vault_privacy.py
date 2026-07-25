"""What a synchronized vault leaves in the database and in the log.

The assertions are negative on purpose. The DoD asks for zero plaintext in a
dump of real traffic — not for a review of the code that was supposed to
prevent it. The second test is the one that checks the removal of the plaintext
path header (§7): no byte string stored for an account may parse as an ISO date
or as the name of a singleton, because the server is not supposed to know
either.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.infra.logging import configure_logging

from conftest import REPO_ROOT, Caller, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()
CYCLE_SHA = hashlib.sha256((REGISTRY / "uk/cycle_sync/0.9.md").read_bytes()).hexdigest()

CONTROL_NOTE = "запаморочення після кави"
CONTROL_DATE = "2026-01-15"
SINGLETONS = ("cycle", "catalog", "groups", "settings", "manifest")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

KEY_A = "aa" * 32
KEY_CYCLE = "cc" * 32
CLIENT_TS = 1_768_435_200_000


def _ciphertext(plaintext: str) -> str:
    """A stand-in for the envelope: opaque bytes the server cannot read.

    The point of the test is not the strength of this transform — the real
    envelope is tested in `apps/web` — but that whatever the client sends is
    the only thing the server stores.
    """
    raw = plaintext.encode()
    masked = bytes(byte ^ 0x5A for byte in raw)
    return base64.b64encode(b"\x01" + masked).decode()


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


def _sync_a_diary(session: Caller) -> None:
    session.post(
        "/v1/sync/push",
        {
            "base_revision": 0,
            "changes": [
                {
                    "record_key": KEY_A,
                    "client_ts_ms": CLIENT_TS,
                    "payload_b64": _ciphertext(
                        json.dumps(
                            {"date": CONTROL_DATE, "note": CONTROL_NOTE},
                            ensure_ascii=False,
                        )
                    ),
                    "tombstone": False,
                },
                {
                    "record_key": KEY_CYCLE,
                    "client_ts_ms": CLIENT_TS,
                    "payload_b64": _ciphertext(json.dumps({"starts": [CONTROL_DATE]})),
                    "tombstone": False,
                },
            ],
        },
    )


def _dump(engine: Engine) -> str:
    """Every text and bytea column of the diary schema, as one string."""
    chunks: list[str] = []
    with engine.connect() as connection:
        tables = [
            row[0]
            for row in connection.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'diary' AND table_type = 'BASE TABLE'
                    """
                )
            ).fetchall()
        ]
        for table in tables:
            rows = connection.execute(text(f"SELECT * FROM diary.{table}")).fetchall()
            for row in rows:
                for value in row:
                    if isinstance(value, memoryview | bytes | bytearray):
                        raw = bytes(value)
                        chunks.append(raw.decode("utf-8", errors="replace"))
                        chunks.append(raw.hex())
                    else:
                        chunks.append(str(value))
    return "\n".join(chunks)


def test_the_dump_holds_no_plaintext_control_note(
    session: Caller,
    engine: Engine,
) -> None:
    _sync_a_diary(session)

    dump = _dump(engine)

    assert CONTROL_NOTE not in dump
    assert "запаморочення" not in dump
    assert "starts" not in dump


def test_no_vault_record_byte_string_parses_as_an_iso_date_or_a_singleton(
    session: Caller,
    engine: Engine,
) -> None:
    """§7: the plaintext path header is gone, so nothing names the record."""
    _sync_a_diary(session)

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT record_key, payload FROM diary.vault_record")
        ).fetchall()

    assert rows
    for row in rows:
        for column in (row.record_key, row.payload):
            if column is None:
                continue
            decoded = bytes(column).decode("utf-8", errors="replace")
            assert not ISO_DATE.search(decoded)
            for name in SINGLETONS:
                assert name not in decoded


def test_the_named_cycle_key_is_the_only_thing_the_server_learns(
    session: Caller,
    engine: Engine,
) -> None:
    """§9.7 and §13.5: the residual leak is exactly one bit, and it is named."""
    granted = session.post(
        "/v1/consents",
        {
            "kind": "cycle_sync",
            "text_version": "cycle_sync@0.9",
            "text_sha256": CYCLE_SHA,
            "record_key_cycle": KEY_CYCLE,
        },
    )
    assert granted.status_code == 201, granted.text

    with engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT kind, record_key_cycle FROM diary.consent "
                "WHERE record_key_cycle IS NOT NULL"
            )
        ).fetchall()
    assert len(stored) == 1
    assert stored[0].kind == "cycle_sync"
    assert bytes(stored[0].record_key_cycle).hex() == KEY_CYCLE


def test_a_named_cycle_key_on_another_consent_is_refused(session: Caller) -> None:
    response = session.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": hashlib.sha256(
                (REGISTRY / "uk/telegram_reminders/0.9.md").read_bytes()
            ).hexdigest(),
            "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
            "record_key_cycle": KEY_CYCLE,
        },
    )
    assert response.status_code == 422


def test_a_full_sync_flow_leaks_no_record_key_or_payload_into_the_log(
    session: Caller,
) -> None:
    capture = io.StringIO()
    configure_logging(stream=capture)
    try:
        _sync_a_diary(session)
        session.get("/v1/sync/pull")
    finally:
        configure_logging()

    lines = [json.loads(line) for line in capture.getvalue().splitlines() if line]
    assert lines
    text_of_log = capture.getvalue()
    assert KEY_A not in text_of_log
    assert KEY_CYCLE not in text_of_log
    assert CONTROL_NOTE not in text_of_log
    assert CONTROL_DATE not in text_of_log
    for entry in lines:
        assert "payload_b64" not in entry
        assert "record_key" not in entry


def test_the_log_still_reports_the_neutral_counters(session: Caller) -> None:
    """§11 allows `record_count` and `revision`; they are what makes a sync
    debuggable without naming anything."""
    capture = io.StringIO()
    configure_logging(stream=capture)
    try:
        _sync_a_diary(session)
    finally:
        configure_logging()

    entries: list[dict[str, Any]] = [
        json.loads(line) for line in capture.getvalue().splitlines() if line
    ]
    pushes = [
        entry for entry in entries if entry.get("route_template") == "/v1/sync/push"
    ]
    assert pushes
    assert pushes[-1]["record_count"] == 2
    assert pushes[-1]["revision"] == 2
