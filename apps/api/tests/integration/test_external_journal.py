"""The composed journal on the real request paths (§6.4, §6.5).

The unit suite proves the format and the chain. This one proves the thing that
only the whole application can answer: that every code §6.4 names actually
reaches the external store, that the external write happens *before* anything is
deleted, and that a store which refuses stops the erasure rather than being
skipped.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.domain.erasure_journal import JournalLine
from app.infra.config import Settings
from app.infra.erasure_journal.journal import ObjectStoreErasureJournal
from app.infra.erasure_journal.store import InMemoryObjectStore, ObjectStoreError
from app.main import AppDependencies, create_app
from app.services.erasure_journal import DatabaseErasureJournal, TeeErasureJournal
from conftest import REPO_ROOT, Caller, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()
REMINDERS_SHA = hashlib.sha256(
    (REGISTRY / "uk/telegram_reminders/0.9.md").read_bytes()
).hexdigest()

FIRST = base64.b64encode(b"first-envelope").decode()
SECOND = base64.b64encode(b"second-envelope").decode()
KDF_PARAMS: dict[str, Any] = {"iterations": 1_000_000, "salt_hex": "00" * 16}
KEY_A = "aa" * 32
CLIENT_TS = 1_768_435_200_000


@pytest.fixture
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
def external(store: InMemoryObjectStore) -> ObjectStoreErasureJournal:
    # Minted inside the run: gitleaks scans the whole history, and a hex
    # literal in a file would keep CI red long after it left HEAD.
    return ObjectStoreErasureJournal(
        store,
        erasure_key=secrets.token_bytes(32),
        head_key=secrets.token_bytes(32),
    )


@pytest.fixture
def teed_api(
    dependencies: AppDependencies,
    settings: Settings,
    external: ObjectStoreErasureJournal,
) -> FastAPI:
    return create_app(
        AppDependencies(
            settings=settings,
            unit_of_work=dependencies.unit_of_work,
            clock=dependencies.clock,
            init_data=dependencies.init_data,
            consent_copy=dependencies.consent_copy,
            erasure_journal=TeeErasureJournal(
                external,
                DatabaseErasureJournal(
                    dependencies.unit_of_work,
                    dependencies.consent_copy,
                ),
            ),
        )
    )


@pytest.fixture
def session(teed_api: FastAPI) -> Caller:
    caller = Caller(teed_api)
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


def _grant_reminders(caller: Caller) -> None:
    response = caller.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": REMINDERS_SHA,
            "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
        },
    )
    assert response.status_code == 201, response.text


def _write_key(caller: Caller, mode: str, *, expected: int, envelope: str) -> Any:
    return caller.post(
        "/v1/sync/key",
        {
            "mode": mode,
            "expected_wrap_version": expected,
            "wrapped_dek": envelope,
            "kdf": "pbkdf2-sha256",
            "kdf_params": KDF_PARAMS,
        },
    )


def _push(caller: Caller) -> None:
    response = caller.post(
        "/v1/sync/push",
        {
            "base_revision": 0,
            "changes": [
                {
                    "record_key": KEY_A,
                    "client_ts_ms": CLIENT_TS,
                    "payload_b64": base64.b64encode(b"ciphertext").decode(),
                    "tombstone": False,
                }
            ],
        },
    )
    assert response.status_code == 200, response.text


def _codes(journal: ObjectStoreErasureJournal) -> list[str]:
    return sorted(JournalLine.from_json(item.body).code for item in journal.read())


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        )


def _account_id(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(text("SELECT id FROM diary.account")).scalar_one()
        )


# ------------------------------------------------------------- both journals


def test_a_revocation_writes_the_external_line_and_the_row(
    session: Caller,
    engine: Engine,
    external: ObjectStoreErasureJournal,
) -> None:
    _grant_reminders(session)

    response = session.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    assert response.status_code == 200, response.text
    assert _codes(external) == ["reminders_off"]
    assert external.audit().trustworthy
    # The row keeps the two operational fields the four-field line has no room
    # for, and the confirmation still lands on it.
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT scope, deletion_copy_version, completed_at"
                " FROM diary.erasure_job"
            )
        ).one()
    assert row.scope == "reminders_off"
    assert row.deletion_copy_version == "deletion@1.0"
    assert row.completed_at is not None


def test_the_external_line_names_neither_the_account_nor_the_consent(
    session: Caller,
    engine: Engine,
    store: InMemoryObjectStore,
) -> None:
    """Privacy rule 3, asserted against the bytes that leave this process."""
    account_id = _account_id(engine)
    _grant_reminders(session)

    session.post("/v1/consents/revoke", {"kind": "telegram_reminders"})

    written = b"".join(store.get(key) for key in store.list("erasure").keys)
    text_form = written.decode()
    assert account_id not in text_form
    assert account_id.replace("-", "") not in text_form
    assert "4242424242" not in text_form  # the Telegram id of the fixture
    for kind in ("health_sync", "telegram_reminders", "cycle_sync"):
        assert kind not in text_form
    # The hour is there; the minute the user acted is not.
    assert json.loads(
        store.get(
            next(key for key in store.list("erasure").keys if "/head/" not in key)
        )
    )["at"].endswith(":00:00Z")


def test_an_unreachable_external_journal_stops_the_erasure(
    teed_api: FastAPI,
    engine: Engine,
    store: InMemoryObjectStore,
) -> None:
    """§6.4 through the *new* adapter, not only through the database one.

    The external write comes first, so a store that refuses leaves the row
    unwritten too — which is the proof that the ordering inside
    `TeeErasureJournal` is the one it claims.
    """
    caller = Caller(teed_api)
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
    _write_key(caller, "rewrap", expected=0, envelope=FIRST)
    _push(caller)
    _grant_reminders(caller)
    store.refusing = True

    with pytest.raises(ObjectStoreError):
        caller.post(
            "/v1/consents/revoke",
            {"kind": "health_sync", "acknowledge_incomplete": True},
        )

    assert _count(engine, "diary.vault_record") == 1
    assert _count(engine, "diary.vault_key") == 1
    assert _count(engine, "diary.erasure_job") == 0
    assert _count(engine, "diary.consent WHERE revoked_at IS NOT NULL") == 0


def test_every_code_of_section_six_four_reaches_the_external_journal(
    session: Caller,
    engine: Engine,
    external: ObjectStoreErasureJournal,
) -> None:
    """All four, on the paths a user actually walks.

    Two of them had no writer at all before this block, and the third drill of
    §6.4 — restoring a backup taken before a re-key — is unreachable without
    `security_reset` in here.
    """
    _grant_reminders(session)
    _write_key(session, "rewrap", expected=0, envelope=FIRST)
    _push(session)

    assert _write_key(session, "rekey", expected=1, envelope=SECOND).status_code == 200
    assert (
        session.post("/v1/consents/revoke", {"kind": "telegram_reminders"}).status_code
        == 200
    )
    _grant_reminders(session)
    assert (
        session.post(
            "/v1/consents/revoke",
            {"kind": "health_sync", "acknowledge_incomplete": True},
        ).status_code
        == 200
    )
    assert session.post("/v1/account/delete").status_code == 200

    assert _codes(external) == ["full", "reminders_off", "security_reset", "sync_off"]
    assert external.audit().trustworthy
    assert _count(engine, "diary.account") == 0
    # The journal outlives the account it names — deliberately, and it is the
    # only table in the erasure matrix for which "zero rows" is false by design.
    assert _count(engine, "diary.erasure_job") == 4
