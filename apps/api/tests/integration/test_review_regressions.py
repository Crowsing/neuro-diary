"""Regressions found by the independent review, each pinned by a test.

Every case here failed before the fix. They live together so a later refactor
cannot quietly undo one of them.
"""

from __future__ import annotations

import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import Engine, text

from app.domain.identity import ConsentKind, RevokeReason
from app.infra.db.engine import SqlUnitOfWorkFactory
from app.infra.logging import LOG_ALLOWLIST, configure_logging
from conftest import REPO_ROOT, Caller, FrozenClock, sign_init_data

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
CYCLE_GRANT = {
    "kind": "cycle_sync",
    "text_version": "cycle_sync@0.9",
    "text_sha256": CYCLE_SHA,
}


def _sign_in(caller: Caller) -> str:
    response = caller.post(
        "/v1/auth/telegram",
        {"init_data": sign_init_data(), "grant": GRANT},
    )
    assert response.status_code == 200, response.text
    token = str(response.json()["session_token"])
    caller.token = token
    return token


def _count(engine: Engine, statement: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement)).scalar_one())


def test_an_appended_unsigned_field_cannot_buy_a_second_session(
    caller: Caller,
    engine: Engine,
) -> None:
    """`hash` is excluded from the check string, so it must not change the key."""
    raw = sign_init_data()
    assert (
        caller.post("/v1/auth/telegram", {"init_data": raw, "grant": GRANT}).status_code
        == 200
    )

    replayed = caller.post(
        "/v1/auth/telegram",
        {"init_data": f"{raw}&hash={'0' * 64}"},
    )

    assert replayed.status_code == 401
    assert replayed.json() == {"error": "auth_replay"}
    assert _count(engine, "SELECT count(*) FROM diary.session_token") == 1


def test_reordering_the_fields_cannot_buy_a_second_session(
    caller: Caller,
    engine: Engine,
) -> None:
    raw = sign_init_data()
    caller.post("/v1/auth/telegram", {"init_data": raw, "grant": GRANT})
    reordered = "&".join(reversed(raw.split("&")))

    replayed = caller.post("/v1/auth/telegram", {"init_data": reordered})

    assert replayed.status_code == 401
    assert _count(engine, "SELECT count(*) FROM diary.session_token") == 1


def test_concurrent_grants_of_one_kind_give_one_201_and_one_409(
    caller: Caller,
    engine: Engine,
) -> None:
    """The partial unique index must never surface as a 500 with SQL in the log."""
    _sign_in(caller)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [
            future.result()
            for future in [
                pool.submit(caller.post, "/v1/consents", CYCLE_GRANT),
                pool.submit(caller.post, "/v1/consents", CYCLE_GRANT),
            ]
        ]

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201, 409]
    assert {"error": "consent_already_active"} in [
        response.json() for response in responses if response.status_code == 409
    ]
    assert (
        _count(engine, "SELECT count(*) FROM diary.consent WHERE kind='cycle_sync'")
        == 1
    )


def test_an_internal_failure_never_puts_sql_or_identifiers_in_the_log(
    caller: Caller,
    engine: Engine,
) -> None:
    """A 500 must be an allowlisted line, not a traceback with bound parameters."""
    _sign_in(caller)
    with engine.begin() as connection:
        account_id = connection.execute(
            text("SELECT id FROM diary.account")
        ).scalar_one()
        # Break an invariant the service cannot see: the identity row is gone,
        # so provisioning a schedule would violate a NOT NULL constraint.
        connection.execute(text("DELETE FROM diary.telegram_identity"))

    buffer = io.StringIO()
    configure_logging(stream=buffer)
    response = caller.post("/v1/consents", CYCLE_GRANT)

    assert response.status_code in {401, 500}
    captured = buffer.getvalue()
    assert "INSERT INTO" not in captured
    assert "diary.consent" not in captured
    assert str(account_id) not in captured
    assert "cycle_sync" not in captured
    for line in captured.splitlines():
        assert set(json.loads(line)) <= LOG_ALLOWLIST


def test_an_authenticated_request_logs_the_rotating_account_reference(
    caller: Caller,
    engine: Engine,
) -> None:
    _sign_in(caller)
    with engine.connect() as connection:
        account_id = connection.execute(
            text("SELECT id FROM diary.account")
        ).scalar_one()

    buffer = io.StringIO()
    configure_logging(stream=buffer)
    caller.get("/v1/consents")

    (record,) = [json.loads(line) for line in buffer.getvalue().splitlines()]
    assert len(str(record["account_ref"])) == 16
    assert str(account_id) not in buffer.getvalue()


def test_revoking_the_last_consent_confirms_its_erasure_job(
    caller: Caller,
    engine: Engine,
) -> None:
    """An unconfirmed job means "erasure never finished" — it has to stay true."""
    _sign_in(caller)

    caller.post("/v1/consents/revoke", {"kind": "health_sync"})

    with engine.connect() as connection:
        completed_at = connection.execute(
            text("SELECT completed_at FROM diary.erasure_job")
        ).scalar_one()
    assert completed_at is not None


def test_a_non_ascii_bearer_token_is_unauthenticated_not_a_crash(
    api: object,
) -> None:
    """Starlette decodes headers as latin-1, so a high byte reaches the service.

    httpx refuses to encode such a header from a string, so the raw bytes go in
    the way a socket would send them.
    """
    import asyncio

    import httpx

    async def _send() -> httpx.Response:
        transport = httpx.ASGITransport(app=api, client=("203.0.113.7", 1))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as session:
            return await session.get(
                "/v1/consents",
                headers={b"authorization": "Bearer ключ".encode("latin-1", "replace")},
            )

    response = asyncio.run(_send())

    assert response.status_code == 401
    assert response.json() == {"error": "auth_invalid"}


def test_a_cascade_never_erases_the_same_account_twice(
    unit_of_work: SqlUnitOfWorkFactory,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    """Two consents revoked at the identical instant are one account, not two."""
    from datetime import timedelta
    from uuid import uuid4

    from app.services.erasure import ErasureService
    from app.services.erasure_journal import DatabaseErasureJournal
    from app.infra.consent_copy import FileConsentCopyRegistry
    from app.workers.erasure_worker import Housekeeper

    account_id = uuid4()
    revoked_at = clock.now() - timedelta(days=40)
    with unit_of_work() as unit:
        unit.accounts.create(account_id, created_at=revoked_at - timedelta(days=1))
        for kind in (ConsentKind.HEALTH_SYNC, ConsentKind.CYCLE_SYNC):
            unit.consents.grant(
                account_id,
                kind=kind,
                text_version=f"{kind.value}@0.9",
                text_sha256=bytes(range(32)),
                text_locale="uk",
                record_key_cycle=None,
                now=revoked_at - timedelta(days=1),
            )
        unit.consents.revoke(
            account_id,
            kinds=[ConsentKind.HEALTH_SYNC, ConsentKind.CYCLE_SYNC],
            reason=RevokeReason.BOT_BLOCKED_TIMEOUT,
            now=revoked_at,
        )
        unit.commit()

    registry = FileConsentCopyRegistry(REPO_ROOT / "consent-copy")
    housekeeper = Housekeeper(
        unit_of_work,
        ErasureService(DatabaseErasureJournal(unit_of_work, registry)),
        clock,
    )
    result = housekeeper.run_once()

    assert result.accounts_erased == 1
    assert _count(engine, "SELECT count(*) FROM diary.erasure_job") == 1
    assert _count(engine, "SELECT count(*) FROM diary.account") == 0
