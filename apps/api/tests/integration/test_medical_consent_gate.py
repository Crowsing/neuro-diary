"""§11 «двічі перевірена згода», on every endpoint that carries medical data.

§11 asks for two checks: a dependency on the way in, and a re-check inside the
write transaction. Before Phase 5 the sync path had only the second, and two of
the five endpoints had **neither**:

* `GET /v1/sync/key` read nothing about the consent at all;
* `POST /v1/sync/key` checked that the account was active and stopped there, so
  an account holding `telegram_reminders` alone could store a `wrapped_dek` —
  the key to a server copy it had never consented to keeping;
* `POST /v1/account/vault-reset` did the same, and wrote a `security_reset` line
  into the erasure journal on every call while doing it.

Step-up is not a substitute for any of that. It proves the person is at the
keyboard; it says nothing about what she agreed to.

The two halves are tested separately, and on purpose. Phase 4's review found
that «both halves of §11 could be removed one at a time with nothing turning
red», so each half here has a test that fails only when *it* is removed:

* the **door** is observed through the budget. A refusal at the door costs a
  request of the endpoint's §11 window, because the window is charged before the
  consent is read. Remove the dependency and the refusal becomes free;
* the **barrier** is observed by calling the service directly, with no HTTP and
  no dependency in the picture at all. Remove the in-transaction check and the
  service performs the operation.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from app.domain.identity import ConsentRequired
from app.domain.rate_limits import BUDGETS, RateBucket
from app.domain.records import RecordWrite
from app.domain.vault import KeyWriteMode
from app.services.erasure import ErasureService
from app.services.sync import SyncService

from conftest import REPO_ROOT, Caller, FrozenClock, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"


def _sha256_of(relative: str) -> str:
    return hashlib.sha256((REGISTRY / relative).read_bytes()).hexdigest()


REMINDERS_GRANT: dict[str, object] = {
    "kind": "telegram_reminders",
    "text_version": "telegram_reminders@0.9",
    "text_sha256": _sha256_of("uk/telegram_reminders/0.9.md"),
    "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
}
HEALTH_SYNC_GRANT: dict[str, object] = {
    "kind": "health_sync",
    "text_version": "health_sync@0.9",
    "text_sha256": _sha256_of("uk/health_sync/0.9.md"),
}

ENVELOPE = base64.b64encode(b"envelope-without-consent").decode()
KDF_PARAMS: dict[str, Any] = {"iterations": 1_000_000, "salt_hex": "00" * 16}
KEY_WRITE: dict[str, object] = {
    "mode": "rewrap",
    "expected_wrap_version": 0,
    "wrapped_dek": ENVELOPE,
    "kdf": "pbkdf2-sha256",
    "kdf_params": KDF_PARAMS,
}
PUSH: dict[str, object] = {
    "base_revision": 0,
    "changes": [
        {
            "record_key": "aa" * 32,
            "client_ts_ms": 1_768_435_200_000,
            "payload_b64": base64.b64encode(b"x" * 16).decode(),
            "tombstone": False,
        }
    ],
}


@pytest.fixture
def reminded(caller: Caller, engine: Engine) -> Caller:
    """A real account whose only consent is `telegram_reminders`.

    Not a hand-built row: the account, the identity and the consent all arrive
    through the atomic first login of §8, so nothing here depends on a shape the
    production path would never produce.
    """
    del engine
    response = caller.post(
        "/v1/auth/telegram",
        {"init_data": sign_init_data(), "grant": REMINDERS_GRANT},
    )
    assert response.status_code == 200, response.text
    caller.token = str(response.json()["session_token"])
    return caller


@pytest.fixture
def synced(caller: Caller, engine: Engine) -> Caller:
    """The mirror of `reminded`: `health_sync` and nothing else."""
    del engine
    response = caller.post(
        "/v1/auth/telegram",
        {"init_data": sign_init_data(), "grant": HEALTH_SYNC_GRANT},
    )
    assert response.status_code == 200, response.text
    caller.token = str(response.json()["session_token"])
    return caller


def _account_id(engine: Engine) -> UUID:
    with engine.connect() as connection:
        return UUID(
            str(connection.execute(text("SELECT id FROM diary.account")).scalar_one())
        )


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        )


# -------------------------------------------------------------------- the door


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/v1/sync/push", PUSH),
        ("GET", "/v1/sync/pull", None),
        ("GET", "/v1/sync/key", None),
        ("POST", "/v1/sync/key", KEY_WRITE),
        ("POST", "/v1/account/vault-reset", None),
    ],
)
def test_no_health_sync_no_medical_endpoint(
    reminded: Caller,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    """All five refuse with the one code §9.2 and §10 give this refusal.

    `GET /v1/sync/key` used to answer 404 here — truthfully, since revocation
    deletes the envelope — and that truth hid the fact that nothing was checking
    the consent. An account that never granted `health_sync` got the same answer
    as one whose vault was empty.
    """
    response = reminded.request(method, path, json_body=body)

    assert response.status_code == 403, response.text
    assert response.json() == {"error": "consent_required"}


def test_the_envelope_is_not_written_without_the_consent_for_it(
    reminded: Caller,
    clock: FrozenClock,
    engine: Engine,
) -> None:
    """Step-up and all: the wrapped key is the key to a server copy.

    The session is fresh, so §8's step-up is satisfied — which is exactly the
    combination that used to succeed. A `vault_key` row for an account without
    `health_sync` is a server copy of the diary key stored under no consent.
    """
    del clock  # the session was minted a moment ago; step-up holds
    refused = reminded.post("/v1/sync/key", KEY_WRITE)

    assert refused.status_code == 403
    assert _count(engine, "diary.vault_key") == 0


def test_a_refused_reset_leaves_no_line_in_the_erasure_journal(
    reminded: Caller,
    engine: Engine,
) -> None:
    """§6.4 keeps the journal a record of deletions that happened.

    `vault_reset` journals unconditionally and before it deletes anything, which
    is right — and is why the consent has to be read before the journal write
    rather than after it. A refused reset that still wrote a `security_reset`
    would hand the restore runbook a deletion to repeat that never occurred.
    """
    refused = reminded.post("/v1/account/vault-reset")

    assert refused.status_code == 403
    assert _count(engine, "diary.erasure_job") == 0


@pytest.mark.parametrize(
    ("path", "bucket"),
    [
        ("/v1/sync/pull", RateBucket.SYNC),
        ("/v1/sync/key", RateBucket.KEY_READ),
    ],
)
def test_the_door_charges_the_window_before_it_reads_the_consent(
    reminded: Caller,
    engine: Engine,
    path: str,
    bucket: RateBucket,
) -> None:
    """The observable difference between «there is a door» and «there is not».

    Both of these refuse inside the service too, so the status code alone cannot
    tell the two designs apart. The budget can: only a dependency charges it
    before the refusal.
    """
    assert reminded.get(path).status_code == 403

    with engine.connect() as connection:
        used = connection.execute(
            text("SELECT used FROM diary.rate_window WHERE bucket = :bucket"),
            {"bucket": bucket.value},
        ).scalar_one()
    assert used == 1
    assert used < BUDGETS[bucket].limit


@pytest.mark.parametrize("path", ["/v1/sync/key", "/v1/account/vault-reset"])
def test_the_consent_is_read_before_the_handler_asks_for_a_step_up(
    reminded: Caller,
    clock: FrozenClock,
    path: str,
) -> None:
    """The door's consent check, observed rather than declared.

    Both of these ask for a step-up **inside the handler**, so a stale session
    without the consent separates the two designs: §11's entry check answers
    `consent_required`, while a design with only the in-transaction half lets the
    request reach the handler and answers `step_up_required` first.

    It is not only an ordering nicety. `step_up_required` tells a caller who has
    no consent at all which operations are protected and that her token is
    otherwise good — a probe §11 has no reason to hand out.
    """
    clock.advance(minutes=30)  # past STEP_UP_FRESHNESS
    body = KEY_WRITE if path == "/v1/sync/key" else None

    refused = reminded.post(path, body)

    assert refused.status_code == 403
    assert refused.json() == {"error": "consent_required"}


def test_the_consent_is_read_before_the_payload_is_measured(
    reminded: Caller,
) -> None:
    """The same discriminator on push, where the handler answers 413 first.

    `record_writes` runs in the handler and refuses an oversized record with
    `payload_too_large`. A consentless caller must never learn that her push was
    measured at all: the answer is the refusal at the door.
    """
    oversized: dict[str, object] = {
        "base_revision": 0,
        "changes": [
            {
                "record_key": "bb" * 32,
                "client_ts_ms": 1_768_435_200_000,
                "payload_b64": base64.b64encode(b"x" * 70_000).decode(),
                "tombstone": False,
            }
        ],
    }

    refused = reminded.post("/v1/sync/push", oversized)

    assert refused.status_code == 403
    assert refused.json() == {"error": "consent_required"}


# ----------------------------------------------------------------- the barrier


@pytest.fixture
def sync_service(dependencies: Any) -> SyncService:
    """The service composed exactly as `create_app` composes it — without FastAPI.

    The point of the four tests below is that nothing from the HTTP layer is in
    the picture: no dependency, no router, no exception handler. What refuses
    them is the check inside the transaction, and nothing else can.
    """
    return SyncService(dependencies.clock, ErasureService(dependencies.erasure_journal))


def test_the_barrier_refuses_a_pull_with_no_dependency_in_the_picture(
    reminded: Caller,
    engine: Engine,
    dependencies: Any,
    sync_service: SyncService,
) -> None:
    del reminded
    account_id = _account_id(engine)

    with dependencies.unit_of_work() as unit:
        with pytest.raises(ConsentRequired):
            sync_service.pull(
                unit,
                account_id=account_id,
                since=0,
                limit=500,
                consent_epoch_seen=0,
            )


def test_the_barrier_refuses_an_envelope_read(
    reminded: Caller,
    engine: Engine,
    dependencies: Any,
    sync_service: SyncService,
) -> None:
    del reminded
    account_id = _account_id(engine)

    with dependencies.unit_of_work() as unit:
        with pytest.raises(ConsentRequired):
            sync_service.read_key(
                unit,
                account_id=account_id,
                stepped_up=True,
                now=dependencies.clock.now(),
            )


def test_the_barrier_refuses_an_envelope_write(
    reminded: Caller,
    engine: Engine,
    dependencies: Any,
    sync_service: SyncService,
) -> None:
    del reminded
    account_id = _account_id(engine)

    with dependencies.unit_of_work() as unit:
        with pytest.raises(ConsentRequired):
            sync_service.write_key(
                unit,
                account_id=account_id,
                mode=KeyWriteMode.REWRAP,
                expected_wrap_version=0,
                wrapped_dek=b"envelope",
                kdf="pbkdf2-sha256",
                kdf_params=KDF_PARAMS,
                now=dependencies.clock.now(),
            )
    assert _count(engine, "diary.vault_key") == 0


def test_the_barrier_refuses_a_vault_reset(
    reminded: Caller,
    engine: Engine,
    dependencies: Any,
    sync_service: SyncService,
) -> None:
    del reminded
    account_id = _account_id(engine)

    with dependencies.unit_of_work() as unit:
        with pytest.raises(ConsentRequired):
            sync_service.vault_reset(
                unit,
                account_id=account_id,
                now=dependencies.clock.now(),
            )
    assert _count(engine, "diary.erasure_job") == 0


def _one_write() -> list[RecordWrite]:
    return [
        RecordWrite(
            record_key=bytes.fromhex("aa" * 32),
            payload=b"x" * 16,
            tombstone=False,
            client_ts_ms=1_768_435_200_000,
        )
    ]


def test_the_barrier_refuses_a_push_with_no_dependency_in_the_picture(
    reminded: Caller,
    engine: Engine,
    dependencies: Any,
    sync_service: SyncService,
) -> None:
    """Крок 4 порядку §9.1 — половина, якої не спостерігав жоден тест.

    Знайдено незалежним review Фази 5, і це рівно те твердження, яке докстрінг
    цього модуля робив про **усі** медичні ендпоінти: «кожна половина має тест,
    що падає лише від її зняття». Для push воно було хибним. Двері відмовляють
    цьому акаунту 403 незалежно від того, що робить сервіс, тож жоден HTTP-тест
    двох конструкцій не розрізняв — а це саме той ендпоінт, який записує
    щоденник, і саме та половина, що стоїть під `accounts.lock`, тим самим, який
    бере транзакція стирання §9.8.
    """
    del reminded
    account_id = _account_id(engine)

    with dependencies.unit_of_work() as unit:
        with pytest.raises(ConsentRequired):
            sync_service.push(
                unit,
                account_id=account_id,
                base_revision=0,
                changes=_one_write(),
                now=dependencies.clock.now(),
            )
    assert _count(engine, "diary.vault_record") == 0


def test_the_barrier_refuses_a_push_for_an_account_that_is_no_longer_active(
    synced: Caller,
    engine: Engine,
    dependencies: Any,
    sync_service: SyncService,
) -> None:
    """Друга половина того самого кроку 4, і теж не спостережена нічим.

    Через HTTP вона майже недосяжна — стирання прибирає і сесії, і згоди, — але
    з-під `accounts.lock` вона єдина, що серіалізується проти §9.8, і зникнути
    могла безслідно.
    """
    del synced
    account_id = _account_id(engine)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE diary.account SET status = 'erasing' WHERE id = :id"),
            {"id": account_id},
        )

    with dependencies.unit_of_work() as unit:
        with pytest.raises(ConsentRequired):
            sync_service.push(
                unit,
                account_id=account_id,
                base_revision=0,
                changes=_one_write(),
                now=dependencies.clock.now(),
            )
    assert _count(engine, "diary.vault_record") == 0
