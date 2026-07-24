"""The three branches of `POST /v1/auth/telegram` (§8).

Branch 2 — no account and no grant — is the one the whole design turns on: the
server must still know nothing afterwards, which means zero rows in every
table, including the replay ledger.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import Engine, text

from app.infra.config import Settings
from app.infra.telegram.initdata import InitDataValidator
from app.main import AppDependencies, create_app
from conftest import (
    PUBLIC_KEY,
    REPO_ROOT,
    Caller,
    FrozenClock,
    sign_init_data,
)

HEALTH_SYNC_SHA = hashlib.sha256(
    (REPO_ROOT / "consent-copy" / "uk" / "health_sync" / "0.9.md").read_bytes()
).hexdigest()
GRANT = {
    "kind": "health_sync",
    "text_version": "health_sync@0.9",
    "text_sha256": HEALTH_SYNC_SHA,
}
DIARY_TABLES = (
    "account",
    "telegram_identity",
    "consent",
    "session_token",
    "auth_replay",
    "erasure_job",
)


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        )


def test_first_sign_in_without_a_grant_leaves_the_server_knowing_nothing(
    caller: Caller,
    engine: Engine,
) -> None:
    response = caller.post("/v1/auth/telegram", {"init_data": sign_init_data()})

    assert response.status_code == 403
    assert response.json() == {"error": "no_account"}
    for table in DIARY_TABLES:
        assert _count(engine, f"diary.{table}") == 0, table


def test_first_sign_in_with_a_grant_creates_everything_at_once(
    caller: Caller,
    engine: Engine,
) -> None:
    response = caller.post(
        "/v1/auth/telegram",
        {"init_data": sign_init_data(), "grant": GRANT},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_token"]
    assert [item["kind"] for item in body["consents"]] == ["health_sync"]
    for table in ("account", "telegram_identity", "consent", "session_token"):
        assert _count(engine, f"diary.{table}") == 1, table


def test_a_known_identity_gets_a_session_and_its_consents(
    caller: Caller,
    engine: Engine,
) -> None:
    caller.post("/v1/auth/telegram", {"init_data": sign_init_data(), "grant": GRANT})

    again = caller.post(
        "/v1/auth/telegram",
        {"init_data": sign_init_data(nonce="second")},
    )

    assert again.status_code == 200
    assert [item["kind"] for item in again.json()["consents"]] == ["health_sync"]
    assert _count(engine, "diary.account") == 1
    assert _count(engine, "diary.session_token") == 2


def test_the_same_init_data_buys_only_one_session(
    caller: Caller,
    engine: Engine,
) -> None:
    raw = sign_init_data()
    caller.post("/v1/auth/telegram", {"init_data": raw, "grant": GRANT})

    replayed = caller.post("/v1/auth/telegram", {"init_data": raw})

    assert replayed.status_code == 401
    assert replayed.json() == {"error": "auth_replay"}
    assert _count(engine, "diary.session_token") == 1


def test_a_refused_attempt_does_not_consume_the_init_data(
    caller: Caller,
    engine: Engine,
) -> None:
    """403 must be retryable: the consent dialog happens between the two calls."""
    raw = sign_init_data()
    assert caller.post("/v1/auth/telegram", {"init_data": raw}).status_code == 403

    accepted = caller.post("/v1/auth/telegram", {"init_data": raw, "grant": GRANT})

    assert accepted.status_code == 200
    assert _count(engine, "diary.auth_replay") == 1


def test_a_failed_grant_creates_no_account(caller: Caller, engine: Engine) -> None:
    response = caller.post(
        "/v1/auth/telegram",
        {
            "init_data": sign_init_data(),
            "grant": {**GRANT, "text_sha256": "11" * 32},
        },
    )

    assert response.status_code == 409
    for table in DIARY_TABLES:
        assert _count(engine, f"diary.{table}") == 0, table


def test_stale_init_data_is_refused(caller: Caller, clock: FrozenClock) -> None:
    raw = sign_init_data(auth_date=clock.now() - timedelta(hours=24, seconds=1))

    response = caller.post("/v1/auth/telegram", {"init_data": raw, "grant": GRANT})

    assert response.status_code == 401
    assert response.json() == {"error": "auth_stale"}


def test_a_broken_signature_is_refused(caller: Caller) -> None:
    tampered = sign_init_data().replace("signature=", "signature=AA")

    response = caller.post("/v1/auth/telegram", {"init_data": tampered})

    assert response.status_code == 401
    assert response.json() == {"error": "auth_invalid"}


def test_min_auth_date_cuts_init_data_issued_before_a_restore(
    dependencies: AppDependencies,
    settings: Settings,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    restored_at = clock.now() - timedelta(hours=1)
    app = create_app(
        AppDependencies(
            settings=settings,
            unit_of_work=dependencies.unit_of_work,
            clock=clock,
            init_data=InitDataValidator(
                bot_id=settings.telegram_bot_id,
                public_key=PUBLIC_KEY,
                min_auth_date=restored_at,
            ),
            consent_copy=dependencies.consent_copy,
            erasure_journal=dependencies.erasure_journal,
        )
    )
    caller = Caller(app)

    old = caller.post(
        "/v1/auth/telegram",
        {
            "init_data": sign_init_data(auth_date=clock.now() - timedelta(hours=2)),
            "grant": GRANT,
        },
    )
    fresh = caller.post(
        "/v1/auth/telegram",
        {
            "init_data": sign_init_data(auth_date=clock.now() - timedelta(minutes=5)),
            "grant": GRANT,
        },
    )

    assert old.status_code == 401
    assert old.json() == {"error": "auth_stale"}
    assert fresh.status_code == 200
    assert _count(engine, "diary.account") == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"init_data": ""},
        {"init_data": "x", "unexpected": 1},
        {"init_data": "x", "grant": {"kind": "health_sync"}},
        {"init_data": "x", "grant": {**GRANT, "kind": "unknown"}},
    ],
)
def test_malformed_requests_are_refused_without_echoing_them(
    caller: Caller,
    body: dict[str, object],
) -> None:
    response = caller.post("/v1/auth/telegram", body)

    assert response.status_code == 422
    assert response.json() == {"detail": "Request validation failed"}
