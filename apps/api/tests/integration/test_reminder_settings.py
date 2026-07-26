"""`GET`/`PUT /v1/reminders/settings` — the resource of §10.

Every refusal here is checked for two things: the code on the wire, and that
nothing moved in the database. A 422 that still wrote the schedule would be
the same defect as no validation at all, one release later.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from sqlalchemy import Engine, text

from conftest import Caller, Database, FrozenClock, REPO_ROOT, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"


def _sha256_of(relative: str) -> str:
    return hashlib.sha256((REGISTRY / relative).read_bytes()).hexdigest()


HEALTH_SYNC_SHA = _sha256_of("uk/health_sync/0.9.md")
REMINDERS_SHA = _sha256_of("uk/telegram_reminders/0.9.md")

SETTINGS = "/v1/reminders/settings"
GRANT = {
    "kind": "telegram_reminders",
    "text_version": "telegram_reminders@0.9",
    "text_sha256": REMINDERS_SHA,
    "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
}


def _authenticate(caller: Caller, **grant: object) -> None:
    body: dict[str, object] = {"init_data": sign_init_data()}
    if grant:
        body["grant"] = grant
    response = caller.post("/v1/auth/telegram", body)
    assert response.status_code == 200, response.text
    caller.token = str(response.json()["session_token"])


@pytest.fixture
def session(caller: Caller, engine: Engine) -> Caller:
    del engine  # the cleanup fixture; the schedule it removes is this one's
    _authenticate(caller, **GRANT)
    return caller


@pytest.fixture
def without_reminders(caller: Caller, engine: Engine) -> Caller:
    del engine
    _authenticate(
        caller,
        kind="health_sync",
        text_version="health_sync@0.9",
        text_sha256=HEALTH_SYNC_SHA,
    )
    return caller


def _schedule(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT tz, local_time, enabled, disabled_reason, disabled_at,"
                " next_fire_at FROM reminders.reminder_schedule"
            )
        ).one()
    return {
        "tz": row.tz,
        "local_time": row.local_time.strftime("%H:%M"),
        "enabled": row.enabled,
        "disabled_reason": row.disabled_reason,
        "disabled_at": row.disabled_at,
        "next_fire_at": row.next_fire_at,
    }


def _block(identity_database: Database, *, at: datetime) -> None:
    """Put the schedule in the state a 403 from Telegram leaves it in."""
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "UPDATE reminders.reminder_schedule SET enabled = false,"
            " disabled_reason = 'bot_blocked', disabled_at = %s",
            (at,),
        )


# ------------------------------------------------------------------------ GET


def test_the_resource_is_the_schedule_the_grant_provisioned(session: Caller) -> None:
    response = session.get(SETTINGS)

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "time": "20:00",
        "timezone": "Europe/Kyiv",
        "quiet_blocked": False,
        "bot_blocked": False,
    }


def test_the_response_carries_no_delivery_of_any_kind(
    session: Caller,
    identity_database: Database,
) -> None:
    """§10: the delivery history must not exist in the API, in any shape.

    Written against a database that *has* a delivery, so it fails if a future
    field starts reporting one — a test on an empty table would pass on an
    endpoint that returns every delivery it can find.
    """
    with psycopg.connect(identity_database.admin_url, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO reminders.reminder_delivery"
            " (account_id, local_date, status, telegram_message_id)"
            " SELECT account_id, DATE '2026-07-25', 'sent', 777"
            " FROM reminders.reminder_schedule"
        )

    body = session.get(SETTINGS).json()

    assert set(body) == {
        "enabled",
        "time",
        "timezone",
        "quiet_blocked",
        "bot_blocked",
    }
    assert "777" not in session.get(SETTINGS).text


def test_a_consent_without_a_schedule_answers_no_schedule(
    session: Caller,
    identity_database: Database,
) -> None:
    """404, and only reachable with the consent in hand — the 403 comes first."""
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute("DELETE FROM reminders.reminder_schedule")

    response = session.get(SETTINGS)

    assert response.status_code == 404
    assert response.json() == {"error": "no_schedule"}


def test_without_the_consent_the_resource_is_forbidden(
    without_reminders: Caller,
) -> None:
    """§11 `require_consent`: 403, and never a 404 that would tell them apart."""
    response = without_reminders.get(SETTINGS)

    assert response.status_code == 403
    assert response.json() == {"error": "consent_required"}


def test_without_a_session_the_resource_is_unauthenticated(caller: Caller) -> None:
    response = caller.get(SETTINGS, token="not-a-session")

    assert response.status_code == 401


def test_a_time_inside_quiet_hours_is_reported_but_not_repaired(
    session: Caller,
    identity_database: Database,
    engine: Engine,
) -> None:
    """§10: an existing schedule survives a policy change; the worker skips it.

    The only way into this state is from the outside, which is exactly what the
    flag is for — `PUT` cannot produce it.
    """
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "UPDATE reminders.reminder_schedule SET local_time = TIME '23:30'"
        )

    body = session.get(SETTINGS).json()

    assert body["quiet_blocked"] is True
    assert body["enabled"] is True
    assert _schedule(engine)["local_time"] == "23:30"


# ------------------------------------------------------------------------ PUT


def test_a_write_replaces_the_resource_and_recomputes_the_fire_time(
    session: Caller,
    engine: Engine,
) -> None:
    response = session.put(
        SETTINGS,
        {"enabled": True, "time": "09:15", "timezone": "Europe/Lisbon"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "time": "09:15",
        "timezone": "Europe/Lisbon",
        "quiet_blocked": False,
        "bot_blocked": False,
    }
    stored = _schedule(engine)
    assert stored["tz"] == "Europe/Lisbon"
    assert stored["local_time"] == "09:15"
    # 09:15 in Lisbon is 08:15Z in July; the clock is frozen at 2026-07-24T12:00Z,
    # so the next occurrence is the following morning rather than today's.
    assert stored["next_fire_at"] == datetime(2026, 7, 25, 8, 15, tzinfo=UTC)


def test_a_pause_keeps_the_consent_and_names_no_reason(
    session: Caller,
    engine: Engine,
) -> None:
    """§10: `enabled = false` is a state of its own, not a withdrawal.

    The absent `disabled_reason` is the load-bearing half — it is what keeps
    the reconciler away from a schedule the user simply switched off.
    """
    response = session.put(
        SETTINGS,
        {"enabled": False, "time": "20:00", "timezone": "Europe/Kyiv"},
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    stored = _schedule(engine)
    assert stored["enabled"] is False
    assert stored["disabled_reason"] is None
    assert stored["disabled_at"] is None
    assert session.get("/v1/consents").json()["consents"][0]["kind"] == (
        "telegram_reminders"
    )


@pytest.mark.parametrize("moment", ["07:59", "22:00", "00:00", "03:30", "23:59"])
def test_quiet_hours_are_refused_at_every_boundary(
    session: Caller,
    engine: Engine,
    moment: str,
) -> None:
    response = session.put(
        SETTINGS,
        {"enabled": True, "time": moment, "timezone": "Europe/Kyiv"},
    )

    assert response.status_code == 422
    assert response.json() == {"error": "quiet_hours_violation"}
    assert _schedule(engine)["local_time"] == "20:00"


@pytest.mark.parametrize("moment", ["08:00", "21:59"])
def test_the_edges_just_outside_quiet_hours_are_accepted(
    session: Caller,
    moment: str,
) -> None:
    """The other half of the boundary: without it the rule could reject everything."""
    response = session.put(
        SETTINGS,
        {"enabled": True, "time": moment, "timezone": "Europe/Kyiv"},
    )

    assert response.status_code == 200
    assert response.json()["time"] == moment


def test_an_unknown_timezone_is_refused_and_writes_nothing(
    session: Caller,
    engine: Engine,
) -> None:
    response = session.put(
        SETTINGS,
        {"enabled": True, "time": "09:00", "timezone": "Mars/Olympus"},
    )

    assert response.status_code == 422
    assert response.json() == {"error": "unknown_timezone"}
    assert _schedule(engine)["tz"] == "Europe/Kyiv"


def test_a_malformed_time_never_reaches_the_domain(
    session: Caller,
    engine: Engine,
) -> None:
    """The schema answers first, and §11 forbids echoing the submitted value."""
    response = session.put(
        SETTINGS,
        {"enabled": True, "time": "9:00", "timezone": "Europe/Kyiv"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Request validation failed"}
    assert "9:00" not in response.text
    assert _schedule(engine)["local_time"] == "20:00"


def test_a_partial_body_is_refused(session: Caller) -> None:
    """§10 replaces the resource; a patch would have to merge `next_fire_at`."""
    response = session.put(SETTINGS, {"enabled": False})

    assert response.status_code == 422


def test_an_unknown_field_is_refused(session: Caller) -> None:
    """`extra="forbid"`: a field the server ignores is a field a client trusts."""
    response = session.put(
        SETTINGS,
        {
            "enabled": True,
            "time": "20:00",
            "timezone": "Europe/Kyiv",
            "confirm_quiet_hours": True,
        },
    )

    assert response.status_code == 422


def test_without_the_consent_a_write_is_forbidden(without_reminders: Caller) -> None:
    response = without_reminders.put(
        SETTINGS,
        {"enabled": True, "time": "20:00", "timezone": "Europe/Kyiv"},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "consent_required"}


# --------------------------------------------------------------- bot blocked


def test_the_block_is_visible_while_it_stands(
    session: Caller,
    identity_database: Database,
) -> None:
    _block(identity_database, at=datetime(2026, 7, 20, 17, 0, tzinfo=UTC))

    body = session.get(SETTINGS).json()

    assert body["bot_blocked"] is True
    assert body["enabled"] is False


def test_changing_the_hour_while_blocked_does_not_discharge_the_deadline(
    session: Caller,
    identity_database: Database,
    engine: Engine,
) -> None:
    """The defect this whole design exists to avoid, as a test.

    §10 revokes a consent after fourteen **consecutive** days of a block, and
    migration 0005 added `disabled_at` precisely so an ordinary edit could not
    move that deadline. If a write also *cleared* the block, the edit would do
    something worse than move it: the schedule stays off, so no further send is
    attempted, no fresh 403 is ever seen, and the revocation never fires at all
    — for anybody who happened to touch the form.
    """
    blocked_at = datetime(2026, 7, 20, 17, 0, tzinfo=UTC)
    _block(identity_database, at=blocked_at)

    response = session.put(
        SETTINGS,
        {"enabled": False, "time": "21:00", "timezone": "Europe/Kyiv"},
    )

    assert response.status_code == 200
    assert response.json()["time"] == "21:00"
    assert response.json()["bot_blocked"] is True
    stored = _schedule(engine)
    assert stored["local_time"] == "21:00"
    assert stored["disabled_reason"] == "bot_blocked"
    assert stored["disabled_at"] == blocked_at


def test_turning_reminders_on_is_what_discharges_a_block(
    session: Caller,
    identity_database: Database,
    engine: Engine,
) -> None:
    """The single act that means «I have unblocked the bot» (§10).

    Accepted rather than refused: the server cannot verify the claim, and every
    alternative is worse — refusing leaves a state she cannot get out of, and
    withdrawing the consent would erase the account of anyone whose only
    consent this is. If she is wrong, the next attempt takes a 403 and a new
    streak starts from that moment, which is what «поспіль» means.
    """
    _block(identity_database, at=datetime(2026, 7, 20, 17, 0, tzinfo=UTC))

    response = session.put(
        SETTINGS,
        {"enabled": True, "time": "20:00", "timezone": "Europe/Kyiv"},
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["bot_blocked"] is False
    stored = _schedule(engine)
    assert stored["enabled"] is True
    assert stored["disabled_reason"] is None
    assert stored["disabled_at"] is None


def test_a_pause_is_not_a_block_and_carries_no_deadline(
    session: Caller,
    engine: Engine,
) -> None:
    """Switching reminders off and on again never touches the block columns."""
    assert (
        session.put(
            SETTINGS,
            {"enabled": False, "time": "20:00", "timezone": "Europe/Kyiv"},
        ).status_code
        == 200
    )
    paused = _schedule(engine)

    assert paused["enabled"] is False
    assert paused["disabled_reason"] is None
    assert paused["disabled_at"] is None


def test_a_quiet_hour_is_refused_even_while_blocked(
    session: Caller,
    identity_database: Database,
    engine: Engine,
) -> None:
    """The validations answer first and write nothing, block or no block."""
    blocked_at = datetime(2026, 7, 20, 17, 0, tzinfo=UTC)
    _block(identity_database, at=blocked_at)

    response = session.put(
        SETTINGS,
        {"enabled": True, "time": "23:00", "timezone": "Europe/Kyiv"},
    )

    assert response.status_code == 422
    assert response.json() == {"error": "quiet_hours_violation"}
    assert _schedule(engine)["disabled_at"] == blocked_at


# --------------------------------------------------------------- rate limits


def test_the_settings_budget_is_twenty_requests_a_minute(
    session: Caller,
    clock: FrozenClock,
) -> None:
    """§11, per account and in PostgreSQL — the same family as the sync budgets."""
    for _ in range(20):
        assert session.get(SETTINGS).status_code == 200

    refused = session.get(SETTINGS)

    assert refused.status_code == 429
    assert refused.json() == {"error": "rate_limited"}
    assert int(refused.headers["Retry-After"]) >= 1

    clock.advance(seconds=60)
    assert session.get(SETTINGS).status_code == 200


def test_reads_and_writes_share_one_budget(session: Caller) -> None:
    """One bucket for the resource: §11 names the endpoint, not the verb."""
    for _ in range(20):
        assert session.get(SETTINGS).status_code == 200

    refused = session.put(
        SETTINGS,
        {"enabled": True, "time": "20:00", "timezone": "Europe/Kyiv"},
    )

    assert refused.status_code == 429


def test_a_refused_write_still_costs_its_budget(
    session: Caller,
    clock: FrozenClock,
) -> None:
    """Otherwise a client looping on a 422 spends nothing exactly while it loops."""
    for _ in range(20):
        assert (
            session.put(
                SETTINGS,
                {"enabled": True, "time": "23:00", "timezone": "Europe/Kyiv"},
            ).status_code
            == 422
        )

    assert session.get(SETTINGS).status_code == 429
    clock.advance(seconds=60)
    assert session.get(SETTINGS).status_code == 200
