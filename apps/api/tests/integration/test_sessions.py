"""Opaque sessions, sliding expiry, and the step-up rule (§8)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import Engine, text

from conftest import REPO_ROOT, Caller, FrozenClock, sign_init_data

HEALTH_SYNC_SHA = hashlib.sha256(
    (REPO_ROOT / "consent-copy" / "uk" / "health_sync" / "0.9.md").read_bytes()
).hexdigest()
GRANT = {
    "kind": "health_sync",
    "text_version": "health_sync@0.9",
    "text_sha256": HEALTH_SYNC_SHA,
}


def _sign_in(caller: Caller, *, nonce: str | None = None) -> str:
    body: dict[str, object] = {"init_data": sign_init_data(nonce=nonce)}
    if nonce is None:
        body["grant"] = GRANT
    response = caller.post("/v1/auth/telegram", body)
    assert response.status_code == 200, response.text
    token = str(response.json()["session_token"])
    caller.token = token
    return token


def test_the_database_stores_only_a_digest_of_the_token(
    caller: Caller,
    engine: Engine,
) -> None:
    token = _sign_in(caller)

    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT token_hash FROM diary.session_token")
        ).scalar_one()

    assert stored == hashlib.sha256(token.encode()).digest()
    assert token.encode() != stored


def test_a_session_lists_itself_as_current(caller: Caller) -> None:
    _sign_in(caller)

    response = caller.get("/v1/sessions")

    assert response.status_code == 200
    (session,) = response.json()["sessions"]
    assert session["current"] is True
    assert set(session) == {
        "id",
        "created_at",
        "last_used_at",
        "expires_at",
        "current",
    }


def test_the_session_list_carries_no_address_or_client_hint(caller: Caller) -> None:
    _sign_in(caller)

    body = caller.get("/v1/sessions").text

    assert "203.0.113.7" not in body
    assert "user_agent" not in body
    assert "ip" not in body


def _expires_at(caller: Caller) -> datetime:
    raw = caller.get("/v1/sessions").json()["sessions"][0]["expires_at"]
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def test_expiry_slides_forward_on_use(caller: Caller, clock: FrozenClock) -> None:
    _sign_in(caller)
    first = _expires_at(caller)

    clock.advance(days=3)

    assert _expires_at(caller) > first


def test_sliding_never_passes_the_absolute_ceiling(
    caller: Caller,
    clock: FrozenClock,
) -> None:
    created = clock.now()
    _sign_in(caller)

    ceiling = created + timedelta(days=30)
    for _ in range(5):
        clock.advance(days=5)
        assert caller.get("/v1/sessions").status_code == 200
        assert _expires_at(caller) <= ceiling

    clock.set(created + timedelta(days=31))
    assert caller.get("/v1/sessions").status_code == 401


def test_an_unknown_token_is_refused(caller: Caller) -> None:
    _sign_in(caller)

    assert caller.get("/v1/sessions", token="not-a-real-token").status_code == 401


def test_a_missing_header_is_refused(caller: Caller) -> None:
    assert caller.get("/v1/sessions").status_code == 401


def test_revoke_others_keeps_the_calling_session(
    caller: Caller,
    engine: Engine,
) -> None:
    first = _sign_in(caller)
    second = _sign_in(caller, nonce="second")
    third = _sign_in(caller, nonce="third")

    response = caller.post("/v1/sessions/revoke-others", token=third)

    assert response.status_code == 200
    assert response.json() == {"revoked": 2}
    assert caller.get("/v1/sessions", token=third).status_code == 200
    assert caller.get("/v1/sessions", token=first).status_code == 401
    assert caller.get("/v1/sessions", token=second).status_code == 401
    with engine.connect() as connection:
        alive = connection.execute(
            text("SELECT count(*) FROM diary.session_token WHERE revoked_at IS NULL")
        ).scalar_one()
    assert alive == 1


def test_deleting_an_account_needs_a_fresh_session(
    caller: Caller,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    _sign_in(caller)
    clock.advance(minutes=11)

    response = caller.post("/v1/account/delete")

    assert response.status_code == 403
    assert response.json() == {"error": "step_up_required"}
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM diary.account")).scalar_one()
            == 1
        )


def test_a_fresh_session_may_delete_the_account(
    caller: Caller,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    _sign_in(caller)
    clock.advance(minutes=9)

    response = caller.post("/v1/account/delete")

    assert response.status_code == 200
    assert response.json() == {"status": "erased"}
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM diary.account")).scalar_one()
            == 0
        )
