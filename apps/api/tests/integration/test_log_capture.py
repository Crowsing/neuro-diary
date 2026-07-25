"""What a full phase 1 flow leaves in the log (§11).

The assertions are negative on purpose: the DoD asks for zero initData and zero
raw `account_id` in a capture of real traffic, not for a code review of the
processor.
"""

from __future__ import annotations

import hashlib
import io
import json
from uuid import UUID

from sqlalchemy import Engine, text

from app.infra.logging import LOG_ALLOWLIST, configure_logging
from conftest import REPO_ROOT, TELEGRAM_USER_ID, Caller, sign_init_data

HEALTH_SYNC_SHA = hashlib.sha256(
    (REPO_ROOT / "consent-copy" / "uk" / "health_sync" / "0.9.md").read_bytes()
).hexdigest()
CYCLE_SHA = hashlib.sha256(
    (REPO_ROOT / "consent-copy" / "uk" / "cycle_sync" / "0.9.md").read_bytes()
).hexdigest()
GRANT = {
    "kind": "health_sync",
    "text_version": "health_sync@0.9",
    "text_sha256": HEALTH_SYNC_SHA,
}
CLIENT_IP = "203.0.113.7"


def _run_full_flow(caller: Caller, engine: Engine) -> tuple[str, UUID]:
    raw_init_data = sign_init_data()
    caller.post("/v1/auth/telegram", {"init_data": raw_init_data})
    response = caller.post(
        "/v1/auth/telegram",
        {"init_data": raw_init_data, "grant": GRANT},
    )
    caller.token = str(response.json()["session_token"])

    with engine.connect() as connection:
        account_id = connection.execute(
            text("SELECT id FROM diary.account")
        ).scalar_one()

    caller.get("/v1/consents")
    caller.post(
        "/v1/consents",
        {
            "kind": "cycle_sync",
            "text_version": "cycle_sync@0.9",
            "text_sha256": CYCLE_SHA,
        },
    )
    caller.post("/v1/consents/revoke", {"kind": "cycle_sync"})
    caller.get("/v1/sessions")
    caller.post("/v1/sessions/revoke-others")
    caller.get("/v1/entry/2026-03-14")
    return raw_init_data, UUID(str(account_id))


def test_a_full_flow_leaks_nothing_into_the_log(
    caller: Caller,
    engine: Engine,
) -> None:
    buffer = io.StringIO()
    configure_logging(stream=buffer)

    raw_init_data, account_id = _run_full_flow(caller, engine)

    captured = buffer.getvalue()
    assert captured, "the flow produced no log lines at all"

    for secret in (
        raw_init_data,
        "signature=",
        str(account_id),
        account_id.hex,
        str(TELEGRAM_USER_ID),
        CLIENT_IP,
        "health_sync",
        "cycle_sync",
        "telegram_reminders",
        "2026-03-14",
        HEALTH_SYNC_SHA,
    ):
        assert secret not in captured, secret

    assert str(caller.token) not in captured


def test_every_logged_field_is_on_the_allowlist(
    caller: Caller,
    engine: Engine,
) -> None:
    buffer = io.StringIO()
    configure_logging(stream=buffer)

    _run_full_flow(caller, engine)

    for line in buffer.getvalue().splitlines():
        record = json.loads(line)
        assert set(record) <= LOG_ALLOWLIST, record


def test_the_log_records_route_templates_and_outcomes(
    caller: Caller,
    engine: Engine,
) -> None:
    """The allowlist is not an excuse for logging nothing useful."""
    buffer = io.StringIO()
    configure_logging(stream=buffer)

    _run_full_flow(caller, engine)

    records = [json.loads(line) for line in buffer.getvalue().splitlines()]
    templates = {record["route_template"] for record in records}
    assert {"/v1/auth/telegram", "/v1/consents", "/v1/sessions"} <= templates
    assert "unmatched" in templates
    assert {403, 200, 201} <= {record["status_code"] for record in records}
