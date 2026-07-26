"""The §11 windows Phase 5 adds, and the three refusals it deliberately does not.

`tests/unit/test_rate_limit_surface.py` holds the map: which operation charges
which bucket, and why the three without one have none. This module is the
territory — every claim there that is about behaviour is exercised here against
a real PostgreSQL.

Two of these tests exist because of a hole Phase 4's review named and left open:
a 403 from `require_consent` cost nothing, because the budget was spent inside
the handler and the dependency refused before it ever ran. A token holder
without the consent could collect refusals for free.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.domain.rate_limits import BUDGETS, RateBucket

from conftest import REPO_ROOT, Caller, FrozenClock, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"


def _sha256_of(relative: str) -> str:
    return hashlib.sha256((REGISTRY / relative).read_bytes()).hexdigest()


HEALTH_SYNC_SHA = _sha256_of("uk/health_sync/0.9.md")
REMINDERS_SHA = _sha256_of("uk/telegram_reminders/0.9.md")

ACCOUNT_OPS_LIMIT = BUDGETS[RateBucket.ACCOUNT_OPS].limit
SYNC_LIMIT = BUDGETS[RateBucket.SYNC].limit
SETTINGS_LIMIT = BUDGETS[RateBucket.REMINDERS_SETTINGS].limit

HEALTH_SYNC_GRANT: dict[str, object] = {
    "kind": "health_sync",
    "text_version": "health_sync@0.9",
    "text_sha256": HEALTH_SYNC_SHA,
}
REMINDERS_GRANT: dict[str, object] = {
    "kind": "telegram_reminders",
    "text_version": "telegram_reminders@0.9",
    "text_sha256": REMINDERS_SHA,
    "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
}


def _authenticate(caller: Caller, grant: dict[str, object]) -> Caller:
    response = caller.post(
        "/v1/auth/telegram",
        {"init_data": sign_init_data(), "grant": grant},
    )
    assert response.status_code == 200, response.text
    caller.token = str(response.json()["session_token"])
    return caller


@pytest.fixture
def synced(caller: Caller, engine: Engine) -> Caller:
    del engine  # the truncating fixture
    return _authenticate(caller, HEALTH_SYNC_GRANT)


@pytest.fixture
def reminded(caller: Caller, engine: Engine) -> Caller:
    del engine
    return _authenticate(caller, REMINDERS_GRANT)


def _used(engine: Engine, bucket: RateBucket) -> int:
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT used FROM diary.rate_window WHERE bucket = :bucket"),
            {"bucket": bucket.value},
        ).fetchone()
    return 0 if row is None else int(row[0])


# ---------------------------------------------------------------- account_ops


def test_the_twenty_first_session_listing_in_a_minute_is_rate_limited(
    synced: Caller,
) -> None:
    for _ in range(ACCOUNT_OPS_LIMIT):
        assert synced.get("/v1/sessions").status_code == 200

    refused = synced.get("/v1/sessions")
    assert refused.status_code == 429
    assert refused.json() == {"error": "rate_limited"}
    assert int(refused.headers["Retry-After"]) > 0


def test_the_account_operations_window_rolls(
    synced: Caller,
    clock: FrozenClock,
) -> None:
    for _ in range(ACCOUNT_OPS_LIMIT + 1):
        synced.get("/v1/sessions")
    assert synced.get("/v1/sessions").status_code == 429

    clock.advance(minutes=1)
    assert synced.get("/v1/sessions").status_code == 200


def test_the_account_operations_share_one_window(synced: Caller) -> None:
    """One bucket for the four, and that is the recorded decision.

    Granting is the endpoint the window exists for — it writes rows and reads
    the copy registry off disk — and it must not be reachable for free by a
    caller that has just spent the budget elsewhere.
    """
    for _ in range(ACCOUNT_OPS_LIMIT):
        assert synced.get("/v1/sessions").status_code == 200

    refused = synced.post("/v1/consents", REMINDERS_GRANT)
    assert refused.status_code == 429


def test_revocation_stays_reachable_with_the_window_spent(synced: Caller) -> None:
    """Art. 7(3): withdrawing must be as easy as granting.

    Behind a shared budget the grant had already spent, it would be strictly
    harder — which is the whole reason this endpoint has no window.
    """
    for _ in range(ACCOUNT_OPS_LIMIT + 1):
        synced.get("/v1/sessions")

    revoked = synced.post("/v1/consents/revoke", {"kind": "health_sync"})
    assert revoked.status_code == 200, revoked.text


def test_reading_the_consents_stays_reachable_with_the_window_spent(
    synced: Caller,
) -> None:
    """§9.4 forbids pruning without a fresh consent answer.

    A window here would let an exhausted budget block the answer that unblocks
    pruning — the same property `test_consents_do_not_share_the_sync_bucket`
    holds from the sync side.
    """
    for _ in range(ACCOUNT_OPS_LIMIT + 1):
        synced.get("/v1/sessions")

    assert synced.get("/v1/consents").status_code == 200


def test_erasure_stays_reachable_with_the_window_spent(synced: Caller) -> None:
    """Art. 17 with Art. 12(3): a window here could only delay an erasure."""
    for _ in range(ACCOUNT_OPS_LIMIT + 1):
        synced.get("/v1/sessions")

    erased = synced.post("/v1/account/delete")
    assert erased.status_code == 200, erased.text


# ------------------------------------------------- a refusal costs its budget


def test_a_consent_refusal_on_the_settings_spends_the_settings_window(
    synced: Caller,
    engine: Engine,
) -> None:
    """The hole Phase 4 left: 403 from `require_consent` cost nothing.

    This account holds `health_sync` and never granted `telegram_reminders`, so
    every call is a 403. The budget still runs out, and the refusal that follows
    is a 429 — which is only possible because the window is charged at the door,
    before the consent is read.
    """
    for _ in range(SETTINGS_LIMIT):
        refused = synced.get("/v1/reminders/settings")
        assert refused.status_code == 403
        assert refused.json() == {"error": "consent_required"}

    assert _used(engine, RateBucket.REMINDERS_SETTINGS) == SETTINGS_LIMIT

    exhausted = synced.get("/v1/reminders/settings")
    assert exhausted.status_code == 429
    assert int(exhausted.headers["Retry-After"]) > 0


def test_a_consent_refusal_on_the_sync_path_spends_the_sync_window(
    reminded: Caller,
    engine: Engine,
) -> None:
    """The same, on the endpoints Phase 5 gave the dependency to.

    This account holds `telegram_reminders` alone. Before Phase 5 a pull refused
    inside the transaction, which is one of the two checks §11 asks for — and the
    budget it spent was refunded by nothing, because the router charged it before
    the service ever refused. The claim being made now is narrower and testable:
    the refusal costs a request of the window.
    """
    for _ in range(SYNC_LIMIT):
        refused = reminded.get("/v1/sync/pull")
        assert refused.status_code == 403
        assert refused.json() == {"error": "consent_required"}

    assert _used(engine, RateBucket.SYNC) == SYNC_LIMIT
    assert reminded.get("/v1/sync/pull").status_code == 429


def test_an_unauthenticated_call_spends_nothing(synced: Caller, engine: Engine) -> None:
    """A 401 has no account to charge, and inventing one would be the bug.

    The window is keyed by `account_id` with a foreign key to `diary.account`;
    charging a caller whose token resolves to nothing would either need a
    fabricated account or would answer 500.
    """
    for _ in range(ACCOUNT_OPS_LIMIT + 5):
        assert synced.get("/v1/sessions", token="not-a-token").status_code == 401

    assert _used(engine, RateBucket.ACCOUNT_OPS) == 0
    assert synced.get("/v1/sessions").status_code == 200


# --------------------------------------------------------------- bounded rows


def test_an_account_holds_at_most_one_row_per_declared_bucket(
    synced: Caller,
    engine: Engine,
) -> None:
    """§6.2 leaves `rate_window` without a TTL job because the slots are fixed.

    That argument survives only while the number of buckets is bounded and every
    row belongs to one of them, so both halves are asserted rather than the
    number three, which stopped being true in Phase 4.
    """
    change: dict[str, Any] = {
        "record_key": "aa" * 32,
        "client_ts_ms": 1_768_435_200_000,
        "payload_b64": base64.b64encode(b"x" * 16).decode(),
        "tombstone": False,
    }
    synced.post("/v1/sync/push", {"base_revision": 0, "changes": [change]})
    synced.get("/v1/sync/pull")
    synced.get("/v1/sync/key")
    synced.get("/v1/sessions")
    synced.get("/v1/reminders/settings")

    with engine.connect() as connection:
        buckets = [
            row[0]
            for row in connection.execute(
                text("SELECT bucket FROM diary.rate_window")
            ).fetchall()
        ]

    assert set(buckets) <= {bucket.value for bucket in RateBucket}
    assert len(buckets) == len(set(buckets))
    assert len(buckets) <= len(RateBucket)


# ------------------------------------------------ Retry-After: округлення вгору


def test_waiting_exactly_the_named_seconds_is_enough(
    synced: Caller,
    clock: FrozenClock,
) -> None:
    """Знайдено навантажувальним smoke §12, а не міркуванням.

    `Retry-After` рахувався через `int()`, тобто обрізався вниз: вікно, що
    закривається за 59.6 с, називало 59. Клієнт, який чекає **рівно** названу
    кількість секунд — а саме так і має робити клієнт, і саме так робить
    `apps/web/src/sync/engine.ts`, — просинався за 0.4 с до відкриття й отримував
    другий 429. Вивантаж на 5.3 МіБ зупинявся на 18-му чанку після паузи, яку
    сервер сам і назвав.

    Годинник тут зсувається на дробову частку секунди саме тому, що дефект жив у
    дробовій частині: на цілих значеннях старий код проходив.
    """
    clock.set(clock.now().replace(second=0, microsecond=400_000))
    for _ in range(ACCOUNT_OPS_LIMIT):
        assert synced.get("/v1/sessions").status_code == 200

    refused = synced.get("/v1/sessions")
    assert refused.status_code == 429
    named = int(refused.headers["Retry-After"])

    # Чекаємо рівно стільки, скільки сказав сервер — і ні мілісекунди більше.
    clock.advance(seconds=named)
    assert synced.get("/v1/sessions").status_code == 200


def test_the_named_wait_is_never_zero(synced: Caller, clock: FrozenClock) -> None:
    """Інша межа тієї самої арифметики: «почекай нуль» — це негайний ретрай."""
    clock.set(clock.now().replace(second=59, microsecond=999_000))
    for _ in range(ACCOUNT_OPS_LIMIT):
        synced.get("/v1/sessions")

    refused = synced.get("/v1/sessions")
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
