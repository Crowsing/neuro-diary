"""The log allowlist of §11 and the rotating account reference.

The allowlist drops unknown keys instead of masking them: masking preserves the
shape of what was logged, and the shape of a medical value is itself a signal.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import structlog

from app.infra.logging import LOG_ALLOWLIST, account_ref, configure_logging, get_logger

ACCOUNT = UUID("11111111-2222-3333-4444-555555555555")
OTHER_ACCOUNT = UUID("99999999-8888-7777-6666-555555555555")
KEY = bytes(range(32))
NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
EPOCH_SECONDS = 7 * 24 * 60 * 60
# Epochs are fixed seven-day windows, so "less than seven days apart" is not the
# same as "inside one epoch". These anchors make the distinction explicit.
EPOCH_START = datetime.fromtimestamp(
    int(NOW.timestamp()) // EPOCH_SECONDS * EPOCH_SECONDS, UTC
)


@pytest.fixture
def captured() -> io.StringIO:
    buffer = io.StringIO()
    configure_logging(stream=buffer)
    return buffer


def _records(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line]


def test_allowlist_matches_the_plan_verbatim() -> None:
    assert LOG_ALLOWLIST == frozenset(
        {
            "timestamp",
            "level",
            "event",
            "request_id",
            "route_template",
            "method",
            "status_code",
            "duration_ms",
            "account_ref",
            "record_count",
            "revision",
            "error_code",
            "retry_after",
        }
    )


def test_fields_outside_the_allowlist_are_dropped(captured: io.StringIO) -> None:
    get_logger().info(
        "auth_completed",
        account_ref="0123456789abcdef",
        status_code=200,
        init_data="user=%7B%22id%22%3A42%7D&auth_date=1&signature=abc",
        telegram_user_id=42,
        account_id=str(ACCOUNT),
        kind="health_sync",
        ip="203.0.113.7",
        query="?note=migraine",
    )

    (record,) = _records(captured)
    assert record == {
        "event": "auth_completed",
        "level": "info",
        "timestamp": record["timestamp"],
        "account_ref": "0123456789abcdef",
        "status_code": 200,
    }
    raw = captured.getvalue()
    for leaked in ("signature", "telegram_user_id", str(ACCOUNT), "health_sync"):
        assert leaked not in raw
    assert "203.0.113.7" not in raw
    assert "migraine" not in raw


def test_exception_tracebacks_cannot_smuggle_values(captured: io.StringIO) -> None:
    try:
        raise ValueError("PRIVATE_SENTINEL")
    except ValueError:
        get_logger().exception("unhandled", error_code="internal")

    assert "PRIVATE_SENTINEL" not in captured.getvalue()
    (record,) = _records(captured)
    assert record["error_code"] == "internal"


def test_account_ref_is_sixteen_hex_characters() -> None:
    reference = account_ref(KEY, ACCOUNT, NOW)

    assert len(reference) == 16
    assert int(reference, 16) >= 0
    assert str(ACCOUNT) not in reference
    assert ACCOUNT.hex[:16] != reference


def test_account_ref_separates_accounts() -> None:
    assert account_ref(KEY, ACCOUNT, NOW) != account_ref(KEY, OTHER_ACCOUNT, NOW)


def test_account_ref_is_stable_inside_an_epoch() -> None:
    start = EPOCH_START + timedelta(seconds=1)
    end = EPOCH_START + timedelta(days=6, hours=23, minutes=59)

    assert account_ref(KEY, ACCOUNT, start) == account_ref(KEY, ACCOUNT, end)


def test_account_ref_rotates_between_epochs() -> None:
    last = EPOCH_START + timedelta(days=7) - timedelta(seconds=1)
    first = EPOCH_START + timedelta(days=7)

    assert account_ref(KEY, ACCOUNT, last) != account_ref(KEY, ACCOUNT, first)


def test_account_ref_depends_on_the_secret() -> None:
    other_key = bytes(range(1, 33))

    assert account_ref(KEY, ACCOUNT, NOW) != account_ref(other_key, ACCOUNT, NOW)


def test_configure_logging_is_idempotent(captured: io.StringIO) -> None:
    configure_logging(stream=captured)
    get_logger().info("health", status_code=200)

    assert len(_records(captured)) == 1


def test_structlog_is_the_configured_pipeline(captured: io.StringIO) -> None:
    del captured
    assert structlog.is_configured()
