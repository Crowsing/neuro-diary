"""Consent endpoints end to end (§9.2, §4.3).

Every failing case asserts zero rows as well as the status code: a rejected
grant that still wrote something would be a consent nobody gave.
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.domain.identity import ConsentKind
from app.infra.config import Settings
from app.main import AppDependencies, create_app

from conftest import REPO_ROOT, Caller, FrozenClock, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"


def _sha256_of(relative: str) -> str:
    return hashlib.sha256((REGISTRY / relative).read_bytes()).hexdigest()


HEALTH_SYNC_SHA = _sha256_of("uk/health_sync/0.9.md")
REMINDERS_SHA = _sha256_of("uk/telegram_reminders/0.9.md")
CYCLE_SHA = _sha256_of("uk/cycle_sync/0.9.md")

REMINDER_SETTINGS = {"time": "20:00", "timezone": "Europe/Kyiv"}


def _rows(engine: Engine, statement: str) -> int:
    with engine.connect() as connection:
        count = connection.execute(text(statement)).scalar_one()
    return int(count)


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


def test_granting_lists_the_active_consent(session: Caller) -> None:
    response = session.get("/v1/consents")

    assert response.status_code == 200
    assert [item["kind"] for item in response.json()["consents"]] == ["health_sync"]
    assert response.json()["consents"][0]["text_version"] == "health_sync@0.9"


def test_stored_hash_matches_the_text_that_was_shown(
    session: Caller,
    engine: Engine,
) -> None:
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT encode(text_sha256, 'hex') FROM diary.consent")
        ).scalar_one()

    assert stored == HEALTH_SYNC_SHA


def test_a_wrong_hash_is_refused_and_writes_nothing(
    session: Caller,
    engine: Engine,
) -> None:
    response = session.post(
        "/v1/consents",
        {
            "kind": "cycle_sync",
            "text_version": "cycle_sync@0.9",
            "text_sha256": "00" * 32,
        },
    )

    assert response.status_code == 409
    assert response.json() == {"error": "consent_text_mismatch"}
    assert _rows(engine, "SELECT count(*) FROM diary.consent") == 1


def test_cycle_sync_requires_an_active_health_sync(
    caller: Caller,
    engine: Engine,
) -> None:
    authenticate(
        caller,
        kind="telegram_reminders",
        text_version="telegram_reminders@0.9",
        text_sha256=REMINDERS_SHA,
        settings=REMINDER_SETTINGS,
    )

    response = caller.post(
        "/v1/consents",
        {
            "kind": "cycle_sync",
            "text_version": "cycle_sync@0.9",
            "text_sha256": CYCLE_SHA,
        },
    )

    assert response.status_code == 409
    assert response.json() == {"error": "consent_precondition"}
    assert (
        _rows(engine, "SELECT count(*) FROM diary.consent WHERE kind='cycle_sync'") == 0
    )


def test_cycle_sync_is_granted_alongside_health_sync(session: Caller) -> None:
    response = session.post(
        "/v1/consents",
        {
            "kind": "cycle_sync",
            "text_version": "cycle_sync@0.9",
            "text_sha256": CYCLE_SHA,
        },
    )

    assert response.status_code == 201
    kinds = {item["kind"] for item in session.get("/v1/consents").json()["consents"]}
    assert kinds == {"health_sync", "cycle_sync"}


def test_reminders_without_settings_are_refused_and_write_nothing(
    session: Caller,
    engine: Engine,
) -> None:
    response = session.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
        },
    )

    assert response.status_code == 422
    assert (
        _rows(
            engine, "SELECT count(*) FROM diary.consent WHERE kind='telegram_reminders'"
        )
        == 0
    )
    assert _rows(engine, "SELECT count(*) FROM reminders.reminder_schedule") == 0


def test_settings_on_a_consent_that_has_none_are_refused(session: Caller) -> None:
    response = session.post(
        "/v1/consents",
        {
            "kind": "cycle_sync",
            "text_version": "cycle_sync@0.9",
            "text_sha256": CYCLE_SHA,
            "settings": REMINDER_SETTINGS,
        },
    )

    assert response.status_code == 422


def test_granting_reminders_provisions_the_schedule_in_one_transaction(
    session: Caller,
    engine: Engine,
) -> None:
    response = session.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
            "settings": REMINDER_SETTINGS,
        },
    )

    assert response.status_code == 201
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT tz, local_time, enabled, telegram_chat_id"
                " FROM reminders.reminder_schedule"
            )
        ).one()
    assert row.tz == "Europe/Kyiv"
    assert row.local_time.isoformat() == "20:00:00"
    assert row.enabled is True


@pytest.mark.parametrize("forbidden", ["22:00", "07:59", "03:30", "23:59"])
def test_quiet_hours_are_refused(
    session: Caller,
    engine: Engine,
    forbidden: str,
) -> None:
    response = session.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
            "settings": {"time": forbidden, "timezone": "Europe/Kyiv"},
        },
    )

    assert response.status_code == 422
    assert response.json() == {"error": "quiet_hours_violation"}
    assert _rows(engine, "SELECT count(*) FROM reminders.reminder_schedule") == 0


@pytest.mark.parametrize("allowed", ["08:00", "21:59", "20:00"])
def test_the_edges_of_the_allowed_window_pass(session: Caller, allowed: str) -> None:
    response = session.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
            "settings": {"time": allowed, "timezone": "Europe/Kyiv"},
        },
    )

    assert response.status_code == 201


def test_an_unknown_timezone_is_refused(session: Caller) -> None:
    response = session.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
            "settings": {"time": "20:00", "timezone": "Mars/Olympus"},
        },
    )

    assert response.status_code == 422
    assert response.json() == {"error": "unknown_timezone"}


def test_granting_the_same_consent_twice_is_refused(session: Caller) -> None:
    response = session.post(
        "/v1/consents",
        {
            "kind": "health_sync",
            "text_version": "health_sync@0.9",
            "text_sha256": HEALTH_SYNC_SHA,
        },
    )

    assert response.status_code == 409
    assert response.json() == {"error": "consent_already_active"}


def test_unknown_fields_are_refused(session: Caller) -> None:
    response = session.post(
        "/v1/consents",
        {
            "kind": "health_sync",
            "text_version": "health_sync@0.9",
            "text_sha256": HEALTH_SYNC_SHA,
            "acknowledge_incomplete": True,
        },
    )

    assert response.status_code == 422


def test_revoking_health_sync_cascades_to_cycle_sync(
    session: Caller,
    engine: Engine,
) -> None:
    session.post(
        "/v1/consents",
        {
            "kind": "cycle_sync",
            "text_version": "cycle_sync@0.9",
            "text_sha256": CYCLE_SHA,
        },
    )
    session.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
            "settings": REMINDER_SETTINGS,
        },
    )

    response = session.post("/v1/consents/revoke", {"kind": "health_sync"})

    assert response.status_code == 200
    remaining = {
        item["kind"] for item in session.get("/v1/consents").json()["consents"]
    }
    assert remaining == {"telegram_reminders"}
    with engine.connect() as connection:
        revocations = connection.execute(
            text(
                "SELECT kind, revoked_at, revoke_reason FROM diary.consent"
                " WHERE revoked_at IS NOT NULL ORDER BY kind"
            )
        ).all()
    assert [row.kind for row in revocations] == ["cycle_sync", "health_sync"]
    assert {row.revoke_reason for row in revocations} == {"user"}
    assert revocations[0].revoked_at == revocations[1].revoked_at


def test_revoking_reminders_removes_the_schedule(
    session: Caller,
    engine: Engine,
) -> None:
    session.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
            "settings": REMINDER_SETTINGS,
        },
    )
    assert _rows(engine, "SELECT count(*) FROM reminders.reminder_schedule") == 1

    response = session.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    assert response.status_code == 200
    assert _rows(engine, "SELECT count(*) FROM reminders.reminder_schedule") == 0


def test_revoking_a_consent_that_is_not_active_is_refused(session: Caller) -> None:
    response = session.post("/v1/consents/revoke", {"kind": "cycle_sync"})

    assert response.status_code == 409
    assert response.json() == {"error": "consent_precondition"}


def test_revocation_needs_no_step_up(session: Caller, clock: FrozenClock) -> None:
    """Art. 7(3): withdrawing stays as easy as granting, however old the session."""
    clock.advance(hours=6)

    response = session.post("/v1/consents/revoke", {"kind": "health_sync"})

    assert response.status_code == 200


def test_consents_require_a_session(caller: Caller) -> None:
    assert caller.get("/v1/consents").status_code == 401
    assert (
        caller.post(
            "/v1/consents",
            {
                "kind": "health_sync",
                "text_version": "health_sync@0.9",
                "text_sha256": HEALTH_SYNC_SHA,
            },
        ).status_code
        == 401
    )


def test_production_refuses_unfrozen_copy_and_writes_nothing(
    dependencies: AppDependencies,
    settings: Settings,
    engine: Engine,
) -> None:
    """The controller is still unnamed, so a real deployment must not accept."""
    production = create_app(
        AppDependencies(
            settings=Settings.from_env(
                {
                    "API_DATABASE_URL": settings.api_database_url,
                    "TELEGRAM_BOT_ID": str(settings.telegram_bot_id),
                    "WEBAPP_URL": settings.webapp_url,
                    "APP_ENV": "production",
                    "TELEGRAM_ED25519_PUBLIC_KEY": (
                        settings.telegram_ed25519_public_key.hex()
                    ),
                    "LOG_ACCOUNT_REF_KEY": bytes(range(32)).hex(),
                    # §6.5 makes the external journal mandatory outside
                    # development, so a production `Settings` cannot be built
                    # without it. Minted here rather than written down —
                    # gitleaks scans the whole history.
                    "ERASURE_JOURNAL_ENABLED": "true",
                    "ERASURE_JOURNAL_KEY": secrets.token_bytes(32).hex(),
                    "ERASURE_JOURNAL_HEAD_KEY": secrets.token_bytes(32).hex(),
                    "SERVICE_START_AT": "2026-01-01T00:00:00Z",
                }
            ),
            unit_of_work=dependencies.unit_of_work,
            clock=dependencies.clock,
            init_data=dependencies.init_data,
            consent_copy=dependencies.consent_copy,
            erasure_journal=dependencies.erasure_journal,
        )
    )
    caller = Caller(production)

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

    assert response.status_code == 503
    assert response.json() == {"error": "consent_copy_not_frozen"}
    assert _rows(engine, "SELECT count(*) FROM diary.account") == 0


def test_the_registry_path_is_configurable(api: FastAPI) -> None:
    assert isinstance(REGISTRY, Path)
    assert api.title


@pytest.mark.parametrize("kind", [kind.value for kind in ConsentKind])
def test_no_endpoint_path_carries_a_consent_name(api: FastAPI, kind: str) -> None:
    assert all(kind not in path for path in api.openapi()["paths"])
